"""Redmine connector implementing TicketProtocol.

Maps the generic ticket operations to the Redmine REST API (v3, JSON).
A "ticket" is a Redmine issue. Area paths are mapped to issue categories,
iterations to project versions, and links use the native issue relations.
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from dotenv import load_dotenv

from mcp_ticketing.protocol import TicketProtocol

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mcp-ticketing-redmine")

REDMINE_URL = os.getenv("MCP_REDMINE_URL", "").rstrip("/")
REDMINE_API_KEY = os.getenv("MCP_REDMINE_API_KEY", "")
REDMINE_PROJECT = os.getenv("MCP_REDMINE_PROJECT", "")

CLOSED_KEYWORDS = ("closed", "done", "completed", "resolved")
OPEN_KEYWORDS = ("active", "open", "new")

LINK_TYPE_MAP = {
    "related": "relates",
    "relates": "relates",
    "precedes": "precedes",
    "follows": "follows",
    "blocks": "blocks",
    "blocked": "blocked",
    "blocked_by": "blocked",
    "duplicates": "duplicates",
    "duplicated": "duplicated",
    "duplicated_by": "duplicated",
    "copied_to": "copied_to",
    "copied_from": "copied_from",
    "parent": "parent",
    "child": "child",
    "hierarchy": "parent",
}


class RedmineClient:
    """Minimal asynchronous HTTP wrapper around the Redmine REST API."""

    def __init__(
        self,
        url: str = REDMINE_URL,
        api_key: str = REDMINE_API_KEY,
        project: str = REDMINE_PROJECT,
    ):
        self.base_url = url
        self.api_key = api_key
        self.project = project

    @asynccontextmanager
    async def get_client(self):
        async with httpx.AsyncClient(timeout=30.0) as client:
            yield client

    def _headers(self) -> dict[str, str]:
        return {"X-Redmine-API-Key": self.api_key}

    async def _request(
        self,
        method: str,
        path: str,
        data: Any | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path}"
        async with self.get_client() as client:
            response = await client.request(
                method, url, headers=self._headers(), json=data, params=params
            )
            response.raise_for_status()
            return response.json()

    async def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def post(
        self, path: str, data: Any, params: dict | None = None
    ) -> dict[str, Any]:
        return await self._request("POST", path, data=data, params=params)

    async def put(
        self, path: str, data: Any, params: dict | None = None
    ) -> dict[str, Any]:
        return await self._request("PUT", path, data=data, params=params)


def format_ticket(issue: dict) -> str:
    """Format a Redmine issue as a JSON ticket."""
    tracker = issue.get("tracker") or {}
    status = issue.get("status") or {}
    priority = issue.get("priority") or {}
    assigned = issue.get("assigned_to") or {}
    category = issue.get("category") or {}
    version = issue.get("fixed_version") or {}
    return json.dumps(
        {
            "id": issue.get("id"),
            "title": issue.get("subject", ""),
            "type": tracker.get("name", "Issue"),
            "state": status.get("name", ""),
            "assigned_to": assigned.get("name", "Unassigned"),
            "tags": "",
            "area_path": category.get("name", ""),
            "iteration_path": version.get("name", ""),
            "priority": priority.get("name", ""),
            "created_date": issue.get("created_on", ""),
            "changed_date": issue.get("updated_on", ""),
        },
        indent=2,
        ensure_ascii=False,
    )


class RedmineConnector(TicketProtocol):
    """Redmine implementation of the TicketProtocol strategy."""

    def __init__(self, client: RedmineClient | None = None):
        self.client = client or RedmineClient()
        self._ref_loaded = False
        self._project: dict[str, Any] = {}
        self._tracker_id_by_name: dict[str, int] = {}
        self._status_id_by_name: dict[str, int] = {}
        self._status_by_id: dict[int, str] = {}
        self._closed_status_id: int | None = None
        self._priority_ids: list[int] = []
        self._priority_by_id: dict[int, str] = {}
        self._category_id_by_name: dict[str, int] = {}
        self._category_by_id: dict[int, str] = {}
        self._version_id_by_name: dict[str, int] = {}

    # ──────────────────────────────────────────────
    #  INTERNAL HELPERS
    # ──────────────────────────────────────────────

    async def _ensure_ref_data(self) -> None:
        """Load and cache reference data (trackers, statuses, priorities, ...)."""
        if self._ref_loaded:
            return
        try:
            project_data = await self.client.get(
                f"projects/{self.client.project}.json",
                params={"include": "trackers,issue_categories"},
            )
            self._project = project_data.get("project") or project_data
            for tracker in self._project.get("trackers") or []:
                self._tracker_id_by_name[tracker.get("name", "").lower()] = tracker.get(
                    "id"
                )
            for cat in self._project.get("issue_categories") or []:
                name = cat.get("name", "")
                self._category_id_by_name[name.lower()] = cat.get("id")
                self._category_by_id[cat.get("id")] = name
        except httpx.HTTPStatusError as e:
            logger.warning("Could not load project: %s", e)

        try:
            data = await self.client.get("issue_statuses.json")
            for status in data.get("issue_statuses", []):
                self._status_by_id[status.get("id")] = status.get("name", "")
                self._status_id_by_name[status.get("name", "").lower()] = status.get(
                    "id"
                )
                if status.get("is_closed"):
                    self._closed_status_id = status.get("id")
        except httpx.HTTPStatusError as e:
            logger.warning("Could not load issue statuses: %s", e)

        try:
            data = await self.client.get("enumerations/issue_priorities.json")
            priorities = sorted(
                data.get("issue_priorities", []),
                key=lambda p: p.get("position", 0),
            )
            self._priority_ids = [p.get("id") for p in priorities]
            self._priority_by_id = {p.get("id"): p.get("name", "") for p in priorities}
        except httpx.HTTPStatusError as e:
            logger.warning("Could not load priorities: %s", e)

        try:
            data = await self.client.get(
                f"projects/{self.client.project}/versions.json"
            )
            for version in data.get("versions", []):
                self._version_id_by_name[version.get("name", "")] = version.get("id")
        except httpx.HTTPStatusError as e:
            logger.warning("Could not load versions: %s", e)

        self._ref_loaded = True

    def _error(self, message: str) -> str:
        return json.dumps({"error": message})

    def _tracker_id(self, ticket_type: str) -> int | None:
        return self._tracker_id_by_name.get(ticket_type.strip().lower())

    def _priority_id(self, priority: int) -> int | None:
        if 1 <= priority <= len(self._priority_ids):
            return self._priority_ids[priority - 1]
        return None

    def _status_id(self, state: str) -> int | None:
        key = state.strip().lower()
        if key in CLOSED_KEYWORDS and self._closed_status_id:
            return self._closed_status_id
        return self._status_id_by_name.get(key)

    def _category_id(self, name: str) -> int | None:
        return self._category_id_by_name.get(name.strip().lower())

    def _version_id(self, name: str) -> int | None:
        return self._version_id_by_name.get(name.strip())

    async def _user_id(self, name: str) -> int | None:
        try:
            data = await self.client.get(
                "users.json", params={"name": name.strip(), "limit": "5"}
            )
        except httpx.HTTPStatusError:
            return None
        users = data.get("users", [])
        if not users:
            return None
        return users[0].get("id")

    def _issue_url(self, issue_id: int) -> str:
        return f"{self.client.base_url}/issues/{issue_id}"

    # ──────────────────────────────────────────────
    #  TICKET TYPES
    # ──────────────────────────────────────────────

    async def get_ticket_types(self) -> str:
        """Retrieve all valid ticket types (project trackers)."""
        try:
            await self._ensure_ref_data()
            ticket_types = [
                {
                    "name": name,
                    "description": "Redmine tracker",
                    "id": tracker_id,
                }
                for name, tracker_id in sorted(self._tracker_id_by_name.items())
            ]
            return json.dumps(
                {"ticket_types": ticket_types, "total": len(ticket_types)},
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def check(self) -> str:
        """Verify the connection to Redmine (API key and project)."""
        try:
            me = await self.client.get("my/account.json")
            project = await self.client.get(f"projects/{self.client.project}.json")
            user = me.get("user") or {}
            login = user.get("login", "?")
            name = user.get("firstname", "") + " " + user.get("lastname", "")
            name = name.strip() or login
            return json.dumps(
                {
                    "status": "ok",
                    "backend": "redmine",
                    "url": self.client.base_url,
                    "user": name,
                    "project": project.get("name", self.client.project),
                    "project_id": project.get("identifier", self.client.project),
                    "message": (
                        f"Authenticated to Redmine '{self.client.base_url}' as '{name}' "
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
        """Retrieve full details of a Ticket (Redmine Issue)."""
        try:
            data = await self.client.get(f"issues/{ticket_id}.json")
            return format_ticket(data.get("issue", {}))
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def get_ticket_comments(self, ticket_id: int) -> str:
        """Retrieve all comments (journal notes) associated with a Ticket."""
        try:
            data = await self.client.get(
                f"issues/{ticket_id}.json", params={"include": "journals"}
            )
            issue = data.get("issue", {})
            journals = [
                j
                for j in (issue.get("journals") or [])
                if isinstance(j, dict) and j.get("notes")
            ]
            if not journals:
                return json.dumps(
                    {
                        "ticket_id": ticket_id,
                        "comments": [],
                        "message": "No comments found.",
                    }
                )

            result = []
            for j in journals:
                user = j.get("user") or {}
                result.append(
                    {
                        "id": j.get("id"),
                        "author": user.get("name", "Unknown"),
                        "created_date": j.get("created_on", ""),
                        "text": j.get("notes", ""),
                    }
                )
            return json.dumps(
                {"ticket_id": ticket_id, "total": len(result), "comments": result},
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def add_comment(self, ticket_id: int, text: str) -> str:
        """Add a comment (journal note) to a Ticket."""
        try:
            await self.client.put(
                f"issues/{ticket_id}.json", {"issue": {"notes": text}}
            )
            return json.dumps(
                {
                    "success": True,
                    "ticket_id": ticket_id,
                    "message": "Comment added successfully.",
                }
            )
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
        """Create a new Ticket (Redmine Issue).

        Args:
            ticket_type: Tracker name (e.g. 'Bug', 'Feature'). Leave empty to
                retrieve available types.
            title: Issue subject
            description: Issue description
            assigned_to: Assignee name/login
            priority: Priority (1-4), mapped to the project priorities in order
            tags: Ignored for Redmine (no tag support in core).
            area_path: Issue category name
            iteration_path: Target version name
        """
        if not ticket_type:
            try:
                await self._ensure_ref_data()
                available = list(self._tracker_id_by_name.keys())
                return json.dumps(
                    {
                        "error": "ticket_type is required but was not provided.",
                        "available_types": available,
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
            tracker_id = self._tracker_id(ticket_type)
            if not tracker_id:
                return self._error(
                    json.dumps(
                        {
                            "error": f"Ticket type '{ticket_type}' not found.",
                            "available_types": list(self._tracker_id_by_name.keys()),
                        }
                    )
                )

            payload: dict[str, Any] = {
                "project_id": self.client.project,
                "subject": title,
                "tracker_id": tracker_id,
            }
            if description:
                payload["description"] = description
            if assigned_to:
                uid = await self._user_id(assigned_to)
                if uid:
                    payload["assigned_to_id"] = uid
            if priority > 0:
                priority_id = self._priority_id(priority)
                if priority_id:
                    payload["priority_id"] = priority_id
            if area_path:
                category_id = self._category_id(area_path)
                if category_id:
                    payload["category_id"] = category_id
            if iteration_path:
                version_id = self._version_id(iteration_path)
                if version_id:
                    payload["fixed_version_id"] = version_id

            data = await self.client.post("issues.json", {"issue": payload})
            issue = data.get("issue", {})
            issue_id = issue.get("id")
            return json.dumps(
                {
                    "success": True,
                    "id": issue_id,
                    "title": issue.get("subject", title),
                    "type": ticket_type,
                    "url": self._issue_url(issue_id),
                    "message": f"Ticket #{issue_id} created successfully.",
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
        """Update an existing Ticket (Redmine Issue). Only provided fields are modified.

        Args:
            ticket_id: Id of the issue to update
            title: New subject
            description: New description
            assigned_to: New assignee (name/login)
            state: New status name (e.g. 'Closed')
            priority: New priority (1-4)
            tags: Ignored for Redmine (no tag support in core).
            area_path: New issue category name
            iteration_path: New target version name
        """
        try:
            await self._ensure_ref_data()
            payload: dict[str, Any] = {}

            if title:
                payload["subject"] = title
            if description:
                payload["description"] = description
            if assigned_to:
                uid = await self._user_id(assigned_to)
                if not uid:
                    return self._error(f"Assigned user '{assigned_to}' not found.")
                payload["assigned_to_id"] = uid
            if state:
                status_id = self._status_id(state)
                if not status_id:
                    available = sorted(self._status_id_by_name.keys())
                    return self._error(
                        json.dumps(
                            {
                                "error": f"State '{state}' not found.",
                                "available_states": available,
                            }
                        )
                    )
                payload["status_id"] = status_id
            if priority > 0:
                priority_id = self._priority_id(priority)
                if priority_id:
                    payload["priority_id"] = priority_id
            if area_path:
                category_id = self._category_id(area_path)
                if not category_id:
                    available = [c.lower() for c in self._category_id_by_name]
                    return self._error(
                        json.dumps(
                            {
                                "error": f"Area path '{area_path}' not found.",
                                "available_area_paths": available,
                            }
                        )
                    )
                payload["category_id"] = category_id
            if iteration_path:
                version_id = self._version_id(iteration_path)
                if not version_id:
                    available = list(self._version_id_by_name.keys())
                    return self._error(
                        json.dumps(
                            {
                                "error": f"Iteration '{iteration_path}' not found.",
                                "available_iterations": available,
                            }
                        )
                    )
                payload["fixed_version_id"] = version_id

            if not payload:
                return self._error("No fields to update provided.")

            await self.client.put(f"issues/{ticket_id}.json", {"issue": payload})
            return json.dumps(
                {
                    "success": True,
                    "id": ticket_id,
                    "message": f"Ticket #{ticket_id} updated successfully.",
                    "updated_fields": list(payload.keys()),
                },
                ensure_ascii=False,
            )
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

        If 'query' is provided, it is used as a subject text search and the
        other filters are appended.

        Args:
            query: Subject text search (e.g. "bug")
            ticket_type: Tracker name to filter by
            state: Ticket state ('active'/'open'/'new', 'closed'/'done', or
                a status name)
            assigned_to: Assignee name/login
            tags: Ignored for Redmine (no tag support in core).
            area_path: Issue category name to filter by
            iteration_path: Target version name to filter by
            max_results: Maximum results (default 20, max 100)
        """
        max_results = min(max(1, max_results), 100)
        try:
            await self._ensure_ref_data()
            params: dict[str, Any] = {
                "limit": max_results,
                "sort": "updated_on:desc",
            }
            if query:
                params["subject~"] = query
            if ticket_type:
                tracker_id = self._tracker_id(ticket_type)
                if tracker_id:
                    params["tracker_id"] = tracker_id
            if state:
                key = state.strip().lower()
                if key in OPEN_KEYWORDS:
                    params["status_id"] = "open"
                elif key in CLOSED_KEYWORDS:
                    params["status_id"] = "closed"
                else:
                    status_id = self._status_id_by_name.get(key)
                    if status_id:
                        params["status_id"] = status_id
            if assigned_to:
                uid = await self._user_id(assigned_to)
                if uid:
                    params["assigned_to_id"] = uid
            if area_path:
                category_id = self._category_id(area_path)
                if category_id:
                    params["category_id"] = category_id
            if iteration_path:
                version_id = self._version_id(iteration_path)
                if version_id:
                    params["fixed_version_id"] = version_id

            data = await self.client.get("issues.json", params=params)
            issues = data.get("issues", [])
            results = [json.loads(format_ticket(i)) for i in issues]
            total = data.get("total_count", len(results))
            return json.dumps(
                {
                    "results": results,
                    "total": total,
                    "truncated": total > max_results,
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def get_tickets_needing_my_reply(self, my_display_name: str) -> str:
        """Find active tickets assigned to you where the last comment was not yours."""
        try:
            me = await self.client.get("my/account.json")
            my_user = me.get("user") or {}
            my_id = my_user.get("id")

            target_id = my_id
            if my_display_name:
                resolved = await self._user_id(my_display_name)
                if resolved:
                    target_id = resolved

            data = await self.client.get(
                "issues.json",
                params={
                    "assigned_to_id": target_id,
                    "status_id": "open",
                    "limit": "20",
                    "include": "journals",
                    "sort": "updated_on:desc",
                },
            )

            pending = []
            for issue in data.get("issues", []):
                journals = [
                    j
                    for j in (issue.get("journals") or [])
                    if isinstance(j, dict) and j.get("notes")
                ]
                if not journals:
                    continue
                last = journals[-1]
                author = (last.get("user") or {}).get("id")
                if author and author != target_id:
                    pending.append(
                        {
                            "ticket_id": issue.get("id"),
                            "title": issue.get("subject", "Untitled"),
                            "last_comment_author": (last.get("user") or {}).get(
                                "name", "Unknown"
                            ),
                            "last_comment_date": last.get("created_on", ""),
                            "last_comment_preview": last.get("notes", "")[:200],
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
        """Link two Tickets using a native Redmine issue relation.

        Args:
            source_id: Source issue id
            target_id: Target issue id
            link_type: One of 'related', 'precedes', 'follows', 'blocks',
                'blocked', 'duplicates', 'duplicated', 'copied_to',
                'copied_from', 'parent', 'child'.
            comment: Optional additional comment (note) on the source ticket
        """
        relation_type = LINK_TYPE_MAP.get(link_type.strip().lower(), "relates")
        try:
            await self.client.post(
                f"issues/{source_id}/relations.json",
                {
                    "relation": {
                        "issue_to_id": target_id,
                        "relation_type": relation_type,
                    }
                },
            )
            if comment:
                await self.client.put(
                    f"issues/{source_id}.json", {"issue": {"notes": comment}}
                )
            return json.dumps(
                {
                    "success": True,
                    "source_id": source_id,
                    "target_id": target_id,
                    "link_type": relation_type,
                    "message": f"Link created: #{source_id} -> #{target_id} ({relation_type})",
                }
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def get_ticket_relations(self, ticket_id: int) -> str:
        """Retrieve all native relations of a Ticket (Redmine Issue)."""
        try:
            data = await self.client.get(f"issues/{ticket_id}/relations.json")
            relations = [
                {
                    "type": r.get("relation_type", "relates"),
                    "target_id": str(r.get("issue_to_id", "")),
                    "comment": "",
                }
                for r in data.get("relations", [])
                if isinstance(r, dict)
            ]
            return json.dumps(
                {
                    "ticket_id": ticket_id,
                    "relations": relations,
                    "total": len(relations),
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    # ──────────────────────────────────────────────
    #  ITERATIONS / VERSIONS
    # ──────────────────────────────────────────────

    async def get_iterations(self) -> str:
        """Retrieve all versions from the Redmine project (mapped to iterations)."""
        try:
            data = await self.client.get(
                f"projects/{self.client.project}/versions.json"
            )
            result = []
            for v in data.get("versions", []):
                result.append(
                    {
                        "id": v.get("id"),
                        "name": v.get("name", ""),
                        "path": v.get("name", ""),
                        "start_date": v.get("start_date", ""),
                        "finish_date": v.get("due_date", ""),
                        "state": v.get("status", ""),
                    }
                )
            return json.dumps(
                {"iterations": result, "total": len(result)}, ensure_ascii=False
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def move_to_iteration(self, ticket_id: int, iteration_path: str) -> str:
        """Move a Ticket (Redmine Issue) to a target version.

        Args:
            ticket_id: Issue id to update
            iteration_path: Version name to assign
        """
        try:
            await self._ensure_ref_data()
            version_id = self._version_id(iteration_path)
            if not version_id:
                available = list(self._version_id_by_name.keys())
                return self._error(
                    json.dumps(
                        {
                            "error": f"Iteration '{iteration_path}' not found.",
                            "available_iterations": available,
                        }
                    )
                )

            await self.client.put(
                f"issues/{ticket_id}.json", {"issue": {"fixed_version_id": version_id}}
            )
            return json.dumps(
                {
                    "success": True,
                    "id": ticket_id,
                    "iteration_path": iteration_path,
                    "message": f"Ticket #{ticket_id} moved to '{iteration_path}'.",
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")

    async def get_area_paths(self) -> str:
        """Retrieve all issue categories from the Redmine project (mapped to area paths)."""
        try:
            await self._ensure_ref_data()
            result = [
                {"name": self._category_by_id[cid], "description": "", "id": cid}
                for cid in sorted(self._category_by_id)
            ]
            return json.dumps(
                {"area_paths": result, "total": len(result)}, ensure_ascii=False
            )
        except httpx.HTTPStatusError as e:
            return self._error(f"HTTP {e.response.status_code}: {e.response.text}")
