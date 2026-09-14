"""Taiga (classic v1) connector implementing TicketProtocol.

Maps the generic ticket operations to the Taiga REST API (/api/v1). A
"ticket" can be any of the project entities — Issue, User Story or Task —
since refs are unique per project across all entity types.
"""

import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any

import httpx
from dotenv import load_dotenv

from mcp_ticketing.protocol import TicketProtocol

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mcp-ticketing-taiga")

TAIGA_URL = os.getenv("MCP_TAIGA_URL", "https://api.taiga.io/api/v1").rstrip("/")
TAIGA_USERNAME = os.getenv("MCP_TAIGA_USERNAME", "")
TAIGA_PASSWORD = os.getenv("MCP_TAIGA_PASSWORD", "")
TAIGA_PROJECT_ID = int(os.getenv("MCP_TAIGA_PROJECT_ID", "0") or 0)

ENTITY_LABELS = {
    "issues": "Issue",
    "userstories": "User Story",
    "tasks": "Task",
}

HISTORY_NAMES = {
    "issues": "issue",
    "userstories": "userstory",
    "tasks": "task",
}

URL_KINDS = {
    "issues": "issue",
    "userstories": "us",
    "tasks": "task",
}


class TaigaClient:
    """Minimal asynchronous HTTP wrapper around the Taiga v1 API."""

    def __init__(
        self,
        url: str = TAIGA_URL,
        username: str = TAIGA_USERNAME,
        password: str = TAIGA_PASSWORD,
        project_id: int = TAIGA_PROJECT_ID,
    ):
        self.base_url = url
        self.username = username
        self.password = password
        self.project_id = project_id
        self.auth_token = ""
        self.refresh_token = ""

    @property
    def site_url(self) -> str:
        return self.base_url.replace("/api/v1", "")

    @asynccontextmanager
    async def get_client(self):
        async with httpx.AsyncClient(timeout=30.0) as client:
            yield client

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.auth_token}",
        }

    async def login(self) -> None:
        payload = {
            "type": "normal",
            "username": self.username,
            "password": self.password,
        }
        async with self.get_client() as client:
            response = await client.post(
                f"{self.base_url}/auth", headers=self._headers(), json=payload
            )
            response.raise_for_status()
            data = response.json()
        self.auth_token = data.get("auth_token", "")
        self.refresh_token = data.get("refresh", "")
        if not self.auth_token:
            raise RuntimeError("Taiga login did not return an auth token.")

    async def refresh(self) -> None:
        async with self.get_client() as client:
            response = await client.post(
                f"{self.base_url}/auth/refresh",
                headers=self._headers(),
                json={"refresh": self.refresh_token},
            )
            response.raise_for_status()
            data = response.json()
        self.auth_token = data.get("auth_token", "")
        if not self.auth_token:
            raise RuntimeError("Taiga token refresh did not return a token.")

    async def _request(
        self,
        method: str,
        path: str,
        body: Any | None = None,
        params: dict | None = None,
    ) -> Any:
        if not self.auth_token:
            await self.login()
        url = f"{self.base_url}/{path}"
        for attempt in range(2):
            async with self.get_client() as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._auth_headers(),
                    json=body,
                    params=params,
                )
            if response.status_code == 401 and attempt == 0:
                await self.refresh()
                continue
            response.raise_for_status()
            return response.json()

    async def get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, data: Any, params: dict | None = None) -> Any:
        return await self._request("POST", path, body=data, params=params)

    async def patch(self, path: str, data: Any, params: dict | None = None) -> Any:
        return await self._request("PATCH", path, body=data, params=params)


def format_ticket(entity: dict, base: str, ref_data: dict) -> str:
    """Format a Taiga entity (issue/user story/task) as a JSON ticket."""
    status_id = entity.get("status")
    status_extra = (entity.get("status_extra_info") or {}).get("name")
    state = status_extra or ref_data["statuses"].get(base, {}).get(status_id, "")

    assigned_id = entity.get("assigned_to")
    assigned_extra = entity.get("assigned_to_extra_info") or {}
    if assigned_extra.get("full_name_display"):
        assigned = assigned_extra["full_name_display"]
    elif assigned_extra.get("username"):
        assigned = assigned_extra["username"]
    elif assigned_id:
        assigned = ref_data["users"].get(assigned_id, "Unassigned")
    else:
        assigned = "Unassigned"

    milestone_name = ref_data["milestones"].get(entity.get("milestone"), "")
    raw_tags = entity.get("tags") or []
    tag_names = [t[0] for t in raw_tags if isinstance(t, list) and t]

    return json.dumps(
        {
            "id": entity.get("ref"),
            "title": entity.get("subject", ""),
            "type": ENTITY_LABELS.get(base, "Issue"),
            "state": state,
            "assigned_to": assigned,
            "tags": "; ".join(tag_names),
            "area_path": "",
            "iteration_path": milestone_name,
            "priority": ref_data["priorities"].get(entity.get("priority"), ""),
            "created_date": entity.get("created_date", ""),
            "changed_date": entity.get("modified_date", ""),
        },
        indent=2,
        ensure_ascii=False,
    )


class TaigaConnector(TicketProtocol):
    """Taiga implementation of the TicketProtocol strategy."""

    def __init__(self, client: TaigaClient | None = None):
        self.client = client or TaigaClient()
        self._ref: dict[str, Any] = {
            "statuses": {},
            "milestones": {},
            "priorities": {},
            "users": {},
            "issue_types": {},
        }
        self._ref_loaded = False
        self._project_slug = ""
        self._status_id_by_name: dict[str, dict[str, int]] = {}
        self._closed_status_id: dict[str, int | None] = {}
        self._milestone_id_by_name: dict[str, int] = {}
        self._user_id_by_name: dict[str, int] = {}
        self._ticket_type_id_by_name: dict[str, int] = {}
        self._priority_ids: list[int] = []
        self._resolved: dict[int, tuple[str, dict]] = {}

    # ──────────────────────────────────────────────
    #  INTERNAL HELPERS
    # ──────────────────────────────────────────────

    async def _ensure_ref_data(self) -> None:
        """Load and cache project reference data (statuses, priorities, ...)."""
        if self._ref_loaded:
            return
        project = self.client.project_id

        status_paths = [
            ("issue-statuses", "issues"),
            ("user-story-statuses", "userstories"),
            ("task-statuses", "tasks"),
        ]
        for path, base in status_paths:
            try:
                data = await self.client.get(path, params={"project": project})
                statuses = [s for s in data if isinstance(s, dict)]
                self._ref["statuses"][base] = {
                    s.get("id"): s.get("name", "") for s in statuses
                }
                self._status_id_by_name[base] = {
                    s.get("name", "").lower(): s.get("id") for s in statuses
                }
                self._closed_status_id[base] = next(
                    (s.get("id") for s in statuses if s.get("is_closed")), None
                )
            except httpx.HTTPStatusError as e:
                logger.warning("Could not load %s: %s", path, e)

        try:
            data = await self.client.get("priorities", params={"project": project})
            priorities = sorted(
                (s for s in data if isinstance(s, dict)),
                key=lambda s: s.get("order", 0),
            )
            self._ref["priorities"] = {
                s.get("id"): s.get("name", "") for s in priorities
            }
            self._priority_ids = [s.get("id") for s in priorities]
        except httpx.HTTPStatusError as e:
            logger.warning("Could not load priorities: %s", e)

        try:
            data = await self.client.get("issue-types", params={"project": project})
            types = [t for t in data if isinstance(t, dict)]
            self._ref["issue_types"] = {t.get("id"): t.get("name", "") for t in types}
            self._ticket_type_id_by_name = {
                t.get("name", "").lower(): t.get("id") for t in types
            }
        except httpx.HTTPStatusError as e:
            logger.warning("Could not load issue-types: %s", e)

        try:
            data = await self.client.get("milestones", params={"project": project})
            milestones = [m for m in data if isinstance(m, dict)]
            self._ref["milestones"] = {
                m.get("id"): m.get("name", "") for m in milestones
            }
            self._milestone_id_by_name = {
                m.get("name"): m.get("id") for m in milestones
            }
        except httpx.HTTPStatusError as e:
            logger.warning("Could not load milestones: %s", e)

        try:
            data = await self.client.get("users", params={"project": project})
            users = [u for u in data if isinstance(u, dict)]
            self._ref["users"] = {
                u.get("id"): u.get("full_name_display", "") for u in users
            }
            self._user_id_by_name = {
                u.get("full_name_display", "").lower(): u.get("id") for u in users
            }
        except httpx.HTTPStatusError as e:
            logger.warning("Could not load users: %s", e)

        try:
            project_data = await self.client.get(f"projects/{project}")
            self._project_slug = project_data.get("slug", "")
        except httpx.HTTPStatusError as e:
            logger.warning("Could not load project detail: %s", e)

        self._ref_loaded = True

    async def _resolve_ref(self, ref: int) -> tuple[str, dict]:
        """Resolve a per-project ref to (base path, full entity)."""
        if ref in self._resolved:
            return self._resolved[ref]
        probes = [
            ("issues", "issues/by_ref"),
            ("userstories", "userstories/by_ref"),
            ("tasks", "tasks/by_ref"),
        ]
        for base, path in probes:
            try:
                data = await self.client.get(
                    path, params={"project": self.client.project_id, "ref": ref}
                )
                self._resolved[ref] = (base, data)
                return base, data
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in (400, 404):
                    raise
        raise LookupError(f"ref {ref} not found")

    async def _patch_with_version(
        self, base: str, ref: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """PATCH an entity, injecting the current version (OCC)."""
        _, entity = await self._resolve_ref(ref)
        entity_id = entity["id"]
        current = await self.client.get(f"{base}/{entity_id}")
        body = {**payload, "version": current.get("version", 0)}
        return await self.client.patch(f"{base}/{entity_id}", body)

    def _parse_ticket_type(self, ticket_type: str) -> tuple[str, int | None]:
        key = ticket_type.strip().lower()
        if key in ("issue", "issues"):
            return "issues", None
        if key in ("user story", "userstory", "userstories"):
            return "userstories", None
        if key in ("task", "tasks"):
            return "tasks", None
        type_id = self._ticket_type_id_by_name.get(key)
        if type_id:
            return "issues", type_id
        return "issues", None

    def _user_id(self, name: str) -> int | None:
        return self._user_id_by_name.get(name.strip().lower())

    def _milestone_id(self, name: str) -> int | None:
        return self._milestone_id_by_name.get(name)

    def _priority_id(self, priority: int) -> int | None:
        if 1 <= priority <= len(self._priority_ids):
            return self._priority_ids[priority - 1]
        return None

    def _status_id(self, base: str, state: str) -> int | None:
        key = state.strip().lower()
        if key in ("closed", "done", "completed", "resolved"):
            closed = self._closed_status_id.get(base)
            if closed:
                return closed
        return self._status_id_by_name.get(base, {}).get(key)

    def _user_name(self, user_id: int | None) -> str:
        return self._ref["users"].get(user_id, "") if user_id else ""

    def _ticket_url(self, base: str, ref: int) -> str:
        if not self._project_slug:
            return ""
        kind = URL_KINDS.get(base, "issue")
        return f"{self.client.site_url}/project/{self._project_slug}/{kind}/{ref}"

    def _error(self, message: str) -> str:
        return json.dumps({"error": message})

    # ──────────────────────────────────────────────
    #  TICKET TYPES
    # ──────────────────────────────────────────────

    async def get_ticket_types(self) -> str:
        """Retrieve all valid ticket types (Issue, User Story, Task + issue types)."""
        try:
            await self._ensure_ref_data()
            ticket_types = [
                {
                    "name": "Issue",
                    "description": "Issue entity (type set by project issue type)",
                },
                {"name": "User Story", "description": "User Story entity"},
                {"name": "Task", "description": "Task entity"},
            ]
            payload: dict[str, Any] = {
                "ticket_types": ticket_types,
                "total": len(ticket_types),
            }
            issue_types = list(self._ref["issue_types"].values())
            if issue_types:
                payload["issue_types"] = [
                    {"name": name, "description": "Project issue type"}
                    for name in issue_types
                ]
            return json.dumps(payload, ensure_ascii=False)
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def check(self) -> str:
        """Verify the connection to Taiga (login, current user and project)."""
        try:
            me = await self.client.get("users/me")
            project = await self.client.get(f"projects/{self.client.project_id}")
            user = me.get("full_name_display") or me.get("username") or "?"
            return json.dumps(
                {
                    "status": "ok",
                    "backend": "taiga",
                    "user": user,
                    "project": project.get("name", self.client.project_id),
                    "project_id": self.client.project_id,
                    "message": (
                        f"Authenticated to Taiga as '{user}' "
                        f"(project '{project.get('name', '')}')."
                    ),
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    # ──────────────────────────────────────────────
    #  TICKET CRUD
    # ──────────────────────────────────────────────

    async def get_ticket(self, ticket_id: int) -> str:
        """Retrieve full details of a Ticket by ref."""
        try:
            await self._ensure_ref_data()
            base, entity = await self._resolve_ref(ticket_id)
            return format_ticket(entity, base, self._ref)
        except LookupError:
            return self._error(f"Ticket #{ticket_id} not found.")
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def get_ticket_comments(self, ticket_id: int) -> str:
        """Retrieve all comments associated with a Ticket."""
        try:
            base, entity = await self._resolve_ref(ticket_id)
            hist_name = HISTORY_NAMES[base]
            history = await self.client.get(f"history/{hist_name}/{entity['id']}")
            comments = [h for h in history if isinstance(h, dict) and h.get("comment")]
            if not comments:
                return json.dumps(
                    {
                        "ticket_id": ticket_id,
                        "comments": [],
                        "message": "No comments found.",
                    }
                )

            result = []
            for c in comments:
                user = c.get("user") or {}
                result.append(
                    {
                        "id": c.get("id"),
                        "author": user.get("full_name_display")
                        or user.get("username")
                        or "Unknown",
                        "created_date": c.get("created_at", ""),
                        "text": c.get("comment", ""),
                    }
                )
            return json.dumps(
                {
                    "ticket_id": ticket_id,
                    "type": ENTITY_LABELS.get(base, "Issue"),
                    "total": len(result),
                    "comments": result,
                },
                ensure_ascii=False,
            )
        except LookupError:
            return self._error(f"Ticket #{ticket_id} not found.")
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def add_comment(self, ticket_id: int, text: str) -> str:
        """Add a comment to a Ticket."""
        try:
            base, _ = await self._resolve_ref(ticket_id)
            await self._patch_with_version(base, ticket_id, {"comment": text})
            return json.dumps(
                {
                    "success": True,
                    "ticket_id": ticket_id,
                    "message": "Comment added successfully.",
                }
            )
        except LookupError:
            return self._error(f"Ticket #{ticket_id} not found.")
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def create_ticket(
        self,
        ticket_type: str = "",
        title: str = "",
        description: str = "",
        assigned_to: str = "",
        priority: int = 0,
        tags: str = "",
        area_path: str = "",
        iteration_path: str = "",
    ) -> str:
        """Create a new Ticket (Issue, User Story or Task).

        Args:
            ticket_type: 'Issue', 'User Story' or 'Task' — or a project issue
                type name (e.g. 'Bug'). Leave empty to retrieve available types.
            title: Ticket title
            description: Ticket description
            assigned_to: Assignee display name / username
            priority: Priority (1-4), applies to Issues only
            tags: Semicolon-separated tags
            area_path: Extra tag applied to the ticket
            iteration_path: Milestone name
        """
        if not ticket_type:
            try:
                await self._ensure_ref_data()
                available_types = ["Issue", "User Story", "Task"]
                return json.dumps(
                    {
                        "error": "ticket_type is required but was not provided.",
                        "available_types": available_types,
                        "available_issue_types": list(
                            self._ref["issue_types"].values()
                        ),
                        "message": "Please specify a valid ticket_type from the list above.",
                    },
                    ensure_ascii=False,
                )
            except httpx.HTTPStatusError as e:
                return json.dumps(
                    {
                        "error": f"HTTP {e.response.status_code}: {e.response.text}",
                        "message": "Could not fetch available ticket types.",
                    }
                )

        if not title:
            return self._error("title is required.")

        try:
            await self._ensure_ref_data()
            base, issue_type_id = self._parse_ticket_type(ticket_type)

            payload: dict[str, Any] = {
                "project": self.client.project_id,
                "subject": title,
            }
            if description:
                payload["description"] = description
            if base == "issues" and issue_type_id:
                payload["type"] = issue_type_id
            if assigned_to:
                uid = self._user_id(assigned_to)
                if uid:
                    payload["assigned_to"] = uid
            if priority > 0 and base == "issues":
                priority_id = self._priority_id(priority)
                if priority_id:
                    payload["priority"] = priority_id
            tag_names = [t.strip() for t in tags.split(";") if t.strip()]
            if area_path and area_path not in tag_names:
                tag_names.append(area_path)
            if tag_names:
                payload["tags"] = tag_names
            if iteration_path:
                milestone_id = self._milestone_id(iteration_path)
                if milestone_id:
                    payload["milestone"] = milestone_id

            data = await self.client.post(base, payload)
            ref = data.get("ref")
            return json.dumps(
                {
                    "success": True,
                    "id": ref,
                    "title": data.get("subject", title),
                    "type": ENTITY_LABELS.get(base, "Issue"),
                    "url": self._ticket_url(base, ref),
                    "message": f"Ticket #{ref} created successfully.",
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def update_ticket(
        self,
        ticket_id: int,
        title: str = "",
        description: str = "",
        assigned_to: str = "",
        state: str = "",
        priority: int = 0,
        tags: str = "",
        area_path: str = "",
        iteration_path: str = "",
    ) -> str:
        """Update an existing Ticket. Only provided fields are modified.

        Args:
            ticket_id: Ref of the ticket to update
            title: New subject
            description: New description
            assigned_to: New assignee (display name / username)
            state: New status name (e.g. 'Closed')
            priority: New priority (1-4), Issues only
            tags: New semicolon-separated tags
            area_path: Extra tag applied to the ticket
            iteration_path: New milestone name
        """
        try:
            await self._ensure_ref_data()
            base, entity = await self._resolve_ref(ticket_id)
            payload: dict[str, Any] = {}

            if title:
                payload["subject"] = title
            if description:
                payload["description"] = description
            if assigned_to:
                uid = self._user_id(assigned_to)
                if not uid:
                    return self._error(f"Assigned user '{assigned_to}' not found.")
                payload["assigned_to"] = uid
            if state:
                status_id = self._status_id(base, state)
                if not status_id:
                    available = list(self._status_id_by_name.get(base, {}).keys())
                    return self._error(
                        json.dumps(
                            {
                                "error": f"State '{state}' not found.",
                                "available_states": available,
                            }
                        )
                    )
                payload["status"] = status_id
            if priority > 0 and base == "issues":
                priority_id = self._priority_id(priority)
                if priority_id:
                    payload["priority"] = priority_id
            if tags or area_path:
                existing = [
                    t[0] for t in (entity.get("tags") or []) if isinstance(t, list)
                ]
                desired = [t.strip() for t in tags.split(";") if t.strip()]
                if not desired:
                    desired = list(existing)
                if area_path and area_path not in desired:
                    desired.append(area_path)
                payload["tags"] = desired
            if iteration_path:
                milestone_id = self._milestone_id(iteration_path)
                if not milestone_id:
                    return self._error(
                        json.dumps(
                            {
                                "error": f"Iteration '{iteration_path}' not found.",
                                "available_iterations": list(
                                    self._milestone_id_by_name.keys()
                                ),
                            }
                        )
                    )
                payload["milestone"] = milestone_id

            if not payload:
                return self._error("No fields to update provided.")

            await self._patch_with_version(base, ticket_id, payload)
            return json.dumps(
                {
                    "success": True,
                    "id": ticket_id,
                    "message": f"Ticket #{ticket_id} updated successfully.",
                    "updated_fields": list(payload.keys()),
                },
                ensure_ascii=False,
            )
        except LookupError:
            return self._error(f"Ticket #{ticket_id} not found.")
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    # ──────────────────────────────────────────────
    #  SEARCH / QUERY
    # ──────────────────────────────────────────────

    async def search_tickets(
        self,
        query: str = "",
        ticket_type: str = "",
        state: str = "",
        assigned_to: str = "",
        tags: str = "",
        area_path: str = "",
        iteration_path: str = "",
        max_results: int = 20,
    ) -> str:
        """Search tickets using a text query or built-in filters.

        Args:
            query: Free-text search across all project entities. When set,
                filters other than ticket_type are ignored.
            ticket_type: Restrict to 'Issue', 'User Story', 'Task' or an issue
                type name (filters by issue type when no custom query).
            state: 'open'/'active' or 'closed'/'done' behaviour
            assigned_to: Assignee display name / username
            tags: Semicolon-separated tags to filter by
            area_path: Ignored (tags are already used for area/area paths)
            iteration_path: Milestone name to filter by
            max_results: Maximum results (default 20, max 200)
        """
        max_results = min(max(1, max_results), 200)
        try:
            await self._ensure_ref_data()
            entity_bases = self._search_entity_bases(ticket_type)
            results: list[dict[str, Any]] = []
            truncated = False

            if query:
                data = await self.client.get(
                    "search",
                    params={"project": self.client.project_id, "text": query},
                )
                for base in entity_bases:
                    for item in data.get(base) or []:
                        results.append(self._search_item(item, base))
            else:
                params: dict[str, Any] = {"project": self.client.project_id}
                if state:
                    closed = state.lower() in (
                        "closed",
                        "done",
                        "completed",
                        "resolved",
                    )
                    params["status__is_closed"] = "true" if closed else "false"
                if assigned_to:
                    uid = self._user_id(assigned_to)
                    if uid:
                        params["assigned_to"] = uid
                if tags:
                    params["tags"] = ",".join(
                        t.strip() for t in tags.split(";") if t.strip()
                    )
                if iteration_path:
                    milestone_id = self._milestone_id(iteration_path)
                    if milestone_id:
                        params["milestone"] = milestone_id

                base, issue_type_id = self._parse_ticket_type(ticket_type)
                # If ticket_type was given, restrict to that single entity.
                candidates = [base] if ticket_type else entity_bases
                if base == "issues" and issue_type_id:
                    params["type"] = issue_type_id

                total = 0
                for b in candidates:
                    items = await self.client.get(b, params=params)
                    for item in items:
                        total += 1
                        if total <= max_results:
                            results.append(
                                json.loads(format_ticket(item, b, self._ref))
                            )
                truncated = total > max_results

            return json.dumps(
                {
                    "results": results,
                    "total": len(results),
                    "truncated": truncated,
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    def _search_entity_bases(self, ticket_type: str) -> list[str]:
        base, _ = self._parse_ticket_type(ticket_type)
        if ticket_type and base:
            return [base]
        return ["issues", "userstories", "tasks"]

    def _search_item(self, item: dict, base: str) -> dict[str, Any]:
        return {
            "id": item.get("ref"),
            "title": item.get("subject", ""),
            "type": ENTITY_LABELS.get(base, "Issue"),
            "state": self._ref["statuses"].get(base, {}).get(item.get("status"), ""),
            "assigned_to": self._user_name(item.get("assigned_to")),
            "tags": "",
            "area_path": "",
            "iteration_path": "",
            "priority": "",
        }

    async def get_tickets_needing_my_reply(self, my_display_name: str) -> str:
        """Find active tickets assigned to you where the last comment was not yours."""
        try:
            await self._ensure_ref_data()
            me = await self.client.get("users/me")
            my_id = me.get("id")
            if my_display_name:
                resolved = self._user_id(my_display_name)
                if resolved:
                    my_id = resolved

            pending = []
            for base in ("issues", "userstories", "tasks"):
                items = await self.client.get(
                    base,
                    params={
                        "project": self.client.project_id,
                        "assigned_to": my_id,
                        "status__is_closed": "false",
                    },
                )
                hist_name = HISTORY_NAMES[base]
                for item in (items or [])[:20]:
                    try:
                        history = await self.client.get(
                            f"history/{hist_name}/{item['id']}"
                        )
                    except httpx.HTTPStatusError:
                        continue
                    comments = [
                        h for h in history if isinstance(h, dict) and h.get("comment")
                    ]
                    if not comments:
                        continue
                    last = comments[-1]
                    author_id = (last.get("user") or {}).get("id")
                    if author_id != my_id:
                        author = last.get("user") or {}
                        pending.append(
                            {
                                "ticket_id": item.get("ref"),
                                "title": item.get("subject", "Untitled"),
                                "type": ENTITY_LABELS.get(base, "Issue"),
                                "last_comment_author": author.get("full_name_display")
                                or author.get("username")
                                or "Unknown",
                                "last_comment_date": last.get("created_at", ""),
                                "last_comment_preview": last.get("comment", "")[:200],
                            }
                        )

            return json.dumps(
                {
                    "pending": pending,
                    "total": len(pending),
                    "message": f"Found {len(pending)} ticket(s) needing your reply."
                    if pending
                    else "No tickets needing reply.",
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    # ──────────────────────────────────────────────
    #  LINKS
    # ──────────────────────────────────────────────

    async def link_tickets(
        self,
        source_id: int,
        target_id: int,
        link_type: str = "related",
        comment: str = "",
    ) -> str:
        """Link two Tickets by adding a cross-reference comment.

        Args:
            source_id: Source ref
            target_id: Target ref
            link_type: Ignored (Taiga has no native issue links).
            comment: Optional additional comment text
        """
        link_text = f"Linked ticket: #{target_id}"
        if comment:
            link_text = f"{comment}\n\n{link_text}"
        try:
            base, _ = await self._resolve_ref(source_id)
            await self._patch_with_version(base, source_id, {"comment": link_text})
            return json.dumps(
                {
                    "success": True,
                    "source_id": source_id,
                    "target_id": target_id,
                    "link_type": "cross-reference",
                    "message": f"Link created: #{source_id} -> #{target_id} (cross-reference comment)",
                }
            )
        except LookupError:
            return self._error(f"Ticket #{source_id} not found.")
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def get_ticket_relations(self, ticket_id: int) -> str:
        """Retrieve all cross-references of a Ticket (from description and comments)."""
        try:
            base, entity = await self._resolve_ref(ticket_id)
            texts = [entity.get("description") or ""]
            hist_name = HISTORY_NAMES[base]
            try:
                history = await self.client.get(f"history/{hist_name}/{entity['id']}")
                for h in history:
                    if isinstance(h, dict) and h.get("comment"):
                        texts.append(h.get("comment"))
            except httpx.HTTPStatusError:
                pass

            linked = set()
            pattern = re.compile(r"(?:^|\s)#(\d+)(?:\s|$|[.,;:!?\)])")
            for text in texts:
                for match in pattern.finditer(text):
                    linked.add(int(match.group(1)))
            linked.discard(ticket_id)

            relations = [
                {"type": "related", "target_id": str(x), "comment": ""}
                for x in sorted(linked)
            ]
            return json.dumps(
                {
                    "ticket_id": ticket_id,
                    "relations": relations,
                    "total": len(relations),
                },
                ensure_ascii=False,
            )
        except LookupError:
            return self._error(f"Ticket #{ticket_id} not found.")
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    # ──────────────────────────────────────────────
    #  ITERATIONS / MILESTONES
    # ──────────────────────────────────────────────

    async def get_iterations(self) -> str:
        """Retrieve all milestones from the Taiga project (mapped to iterations)."""
        try:
            data = await self.client.get(
                "milestones", params={"project": self.client.project_id}
            )
            result = []
            for m in data or []:
                if not isinstance(m, dict):
                    continue
                result.append(
                    {
                        "id": m.get("id"),
                        "name": m.get("name", ""),
                        "path": m.get("name", ""),
                        "start_date": m.get("estimated_start", "")
                        or m.get("created_date", ""),
                        "finish_date": m.get("estimated_finish", "")
                        or m.get("modified_date", ""),
                        "state": m.get("state", ""),
                    }
                )
            return json.dumps(
                {"iterations": result, "total": len(result)}, ensure_ascii=False
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def move_to_iteration(self, ticket_id: int, iteration_path: str) -> str:
        """Move a Ticket to a milestone.

        Args:
            ticket_id: Ref of the ticket to move
            iteration_path: Milestone name
        """
        try:
            await self._ensure_ref_data()
            milestone_id = self._milestone_id(iteration_path)
            if not milestone_id:
                return self._error(
                    json.dumps(
                        {
                            "error": f"Milestone '{iteration_path}' not found.",
                            "available_iterations": list(
                                self._milestone_id_by_name.keys()
                            ),
                        }
                    )
                )

            base, _ = await self._resolve_ref(ticket_id)
            await self._patch_with_version(base, ticket_id, {"milestone": milestone_id})
            return json.dumps(
                {
                    "success": True,
                    "id": ticket_id,
                    "iteration_path": iteration_path,
                    "message": f"Ticket #{ticket_id} moved to '{iteration_path}'.",
                },
                ensure_ascii=False,
            )
        except LookupError:
            return self._error(f"Ticket #{ticket_id} not found.")
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def get_area_paths(self) -> str:
        """Retrieve all tags from the Taiga project (mapped to area paths)."""
        try:
            data = await self.client.get(
                f"projects/{self.client.project_id}/tags_colors"
            )
            result = [
                {"name": name, "color": color or ""}
                for name, color in (data or {}).items()
            ]
            return json.dumps(
                {"area_paths": result, "total": len(result)}, ensure_ascii=False
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")
