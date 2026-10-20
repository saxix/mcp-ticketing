"""Redmine connector implementing TicketProtocol.

Supports Redmine 4.2 through 7.x. The version-specific parts (issue-type
endpoint and field names) are delegated to a
:class:`~mcp_ticketing.redmine.profile.RedmineVersionProfile` selected **once
at startup** from the declared ``MCP_REDMINE_VERSION`` — or, when that
variable is empty, from the *latest* profile in the registry. There are no
runtime try/except-404 attempts and no version probe: the profile decides
the endpoint, and an unsupported *declared* version fails fast at startup.
The operator is expected to know and declare their server version (the
supported majors are listed in ``docs/redmine.md``).

Configuration comes from the environment: ``MCP_REDMINE_URL``,
``MCP_REDMINE_API_KEY``, ``MCP_REDMINE_PROJECT`` and the optional
``MCP_REDMINE_VERSION`` / ``MCP_REDMINE_VERIFY_SSL``.
"""

import asyncio
import html
import json
import logging
import os
from typing import Any

import httpx

from mcp_ticketing._images import is_image_file, read_image_file
from mcp_ticketing.protocol import TicketProtocol
from mcp_ticketing.redmine.base import (
    OPEN_STATES,
    STATE_TO_STATUSES,
    first_field,
    markdown_to_html,
    render_mermaid_png,
    strip_wiki,
)
from mcp_ticketing.redmine.client import RedmineClient
from mcp_ticketing.redmine.discovery import discover_priorities, resolve_priority_id
from mcp_ticketing.redmine.profile import RedmineVersionProfile
from mcp_ticketing.redmine.registry import select_profile

logger = logging.getLogger("mcp-ticketing-redmine")


def format_ticket(issue: dict, include_description: bool = False) -> str:
    """Project a Redmine issue into the flat JSON shape used by the MCP tools.

    The description is opt-in: it is only included when ``include_description``
    is True, so that list endpoints (search_tickets) stay light while
    single-ticket reads (get_ticket) expose the body.

    Field names are read through :data:`~mcp_ticketing.redmine.base.FIELD_ALIASES`
    so both old Redmine (``tracker`` / ``fixed_version``) and new Redmine
    (``issue_type`` / ``version``) payloads are handled.
    """
    status = issue.get("status", {}) or {}
    assigned_to = issue.get("assigned_to", {}) or {}
    priority = issue.get("priority", {}) or {}
    issue_type = first_field(issue, "issue_type")
    version = first_field(issue, "version")
    ticket = {
        "id": issue.get("id"),
        "title": issue.get("subject", ""),
        "type": issue_type.get("name", "Issue") or "Issue",
        "state": status.get("name", ""),
        "assigned_to": assigned_to.get("name", "Unassigned") if assigned_to else "Unassigned",
        "tags": "",
        "area_path": "",
        "iteration_path": version.get("name", "") if version else "",
        "priority": priority.get("name", "") if priority else "",
        "created_date": issue.get("created_on", ""),
        "changed_date": issue.get("updated_on", ""),
    }
    if include_description:
        ticket["description"] = issue.get("description", "") or ""
    return json.dumps(ticket, indent=2, ensure_ascii=False)


class RedmineConnector(TicketProtocol):
    """Redmine implementation of the TicketProtocol strategy.

    Works with Redmine 4.2 through 7.x. A :class:`RedmineVersionProfile`
    is selected once at startup (``_ensure_profile``) and every
    version-dependent choice is delegated to it.
    """

    def __init__(self, client: RedmineClient | None = None, profile: RedmineVersionProfile | None = None):
        self.client = client or RedmineClient()
        self._profile = profile
        self._project: dict | None = None
        self._statuses: list[dict] | None = None
        self._priorities: list[dict] | None = None
        self._profile_ready = profile is not None

    # ──────────────────────────────────────────────
    #  PROFILE / STARTUP
    # ──────────────────────────────────────────────

    async def _ensure_profile(self) -> RedmineVersionProfile:
        """Select (and cache) the active version profile.

        Selection happens **once**, without any server round-trip:

        * ``MCP_REDMINE_VERSION`` set -> the profile for that version
          (``select_profile`` raises on an invalid or unsupported version,
          failing fast at startup);
        * ``MCP_REDMINE_VERSION`` empty -> the **latest** profile in the
          registry (newest API surface as default).

        The operator is expected to know and declare their server version;
        deployments that do not expose it (no ``X-Redmine-Version`` header)
        are the rule, not the exception, so no probe is attempted.
        """
        if self._profile_ready and self._profile is not None:
            return self._profile

        declared = os.getenv("MCP_REDMINE_VERSION", "").strip()
        profile = self._profile if self._profile is not None else select_profile(declared or None)
        logger.info(
            "Redmine profile: %s (declared MCP_REDMINE_VERSION=%s)",
            profile.name,
            declared or "(not set, using latest)",
        )

        self._profile = profile
        self._profile_ready = True
        # Per-instance caches are invalidated on (re)selection.
        self._project = None
        self._priorities = None
        self._statuses = None
        return profile

    # ──────────────────────────────────────────────
    #  RESOLUTION HELPERS
    # ──────────────────────────────────────────────

    async def _resolve_project(self) -> dict:
        """Return the default project dict, resolving the identifier if needed."""
        if self._project:
            return self._project
        project_id = self.client.project
        if project_id.isdigit():
            data = await self.client.get(f"projects/{project_id}.json")
            self._project = data.get("project", {})
        else:
            data = await self.client.get("projects.json")
            projects = data.get("projects", [])
            project = next((p for p in projects if p.get("identifier") == project_id), None)
            if project is None:
                raise ValueError(f"Project '{project_id}' not found.")
            self._project = project
        return self._project

    async def _safe_resolve_project(self) -> dict | None:
        """Resolve the project, returning ``None`` on HTTP/lookup errors."""
        try:
            return await self._resolve_project()
        except (httpx.HTTPStatusError, ValueError):
            return None

    async def _get_statuses(self) -> list[dict]:
        """Return the list of issue statuses from the profile's endpoint.

        Falls back to discovering statuses from existing issues when
        ``issues_statuses.json`` is unavailable (404) on older or restricted
        deployments.
        """
        if self._statuses is not None:
            return self._statuses
        profile = await self._ensure_profile()
        try:
            data = await self.client.get(profile.status_endpoint())
            self._statuses = data.get("issues_statuses", [])
            return self._statuses
        except httpx.HTTPStatusError:
            pass
        # Fallback: discover statuses from a sample of existing issues
        project = await self._safe_resolve_project()
        if project is None:
            self._statuses = []
            return self._statuses
        data = await self.client.get("issues.json", params={"project_id": project.get("id"), "limit": 100})
        seen: dict[str, dict] = {}
        for issue in data.get("issues", []):
            s = issue.get("status") or {}
            if s.get("id") is not None:
                seen[str(s.get("id"))] = {"id": s.get("id"), "name": s.get("name", "")}
        self._statuses = list(seen.values())
        return self._statuses

    async def _get_priorities(self) -> list[dict]:
        """Return the instance priorities, discovered from one page of issues."""
        if self._priorities is not None:
            return self._priorities
        self._priorities = await discover_priorities(self.client)
        return self._priorities

    async def _resolve_priority(self, priority: int) -> int | None:
        """Map the protocol's 1–4 scale to an instance priority id by name."""
        if priority <= 0:
            return None
        return resolve_priority_id(await self._get_priorities(), priority)

    async def _list_issue_types(self) -> list[dict]:
        """Return the issue types (or trackers) from the profile's endpoint."""
        profile = await self._ensure_profile()
        data = await self.client.get(profile.issue_type_endpoint())
        if profile.issue_type_endpoint() == "issue_types.json":
            return [
                {
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "description": "Default issue type" if t.get("is_default") else "",
                }
                for t in data.get("issue_types", [])
            ]
        return [
            {"id": t.get("id"), "name": t.get("name", ""), "description": t.get("description", "") or ""}
            for t in data.get("trackers", [])
        ]

    async def _resolve_issue_type(self, ticket_type: str) -> int | None:
        """Resolve an issue type (or tracker) by name (case-insensitive)."""
        if not ticket_type:
            return None
        for t in await self._list_issue_types():
            if t.get("name", "").lower() == ticket_type.lower():
                return t.get("id")
        return None

    async def _safe_resolve_issue_type(self, ticket_type: str) -> int | None:
        try:
            return await self._resolve_issue_type(ticket_type)
        except httpx.HTTPStatusError:
            return None

    async def _resolve_status(self, state: str) -> dict | None:
        """Resolve a status by name (case-insensitive); None when absent."""
        if not state:
            return None
        statuses = await self._get_statuses()
        for s in statuses:
            if s.get("name", "").lower() == state.lower():
                return s
        return None

    async def _safe_resolve_status(self, state: str) -> int | None:
        """Resolve a state to a status id, trying generic synonyms; None when absent."""
        status = None
        try:
            status = await self._resolve_status(state)
        except httpx.HTTPStatusError:
            return None
        if status is None:
            for candidate in STATE_TO_STATUSES.get(state.lower(), ()):
                try:
                    status = await self._resolve_status(candidate)
                except httpx.HTTPStatusError:
                    break
                if status:
                    break
        return status.get("id") if status else None

    async def _resolve_assignee(self, assigned_to: str) -> int | None:
        """Resolve a user by login or name; None when absent or when users.json is unavailable."""
        if not assigned_to:
            return None
        try:
            data = await self.client.get("users.json", params={"status": "active", "limit": 100})
        except httpx.HTTPStatusError:
            # users.json may return 403 on some configurations; try to find
            # the user from issue author/assigned_to fields
            project = await self._resolve_project()
            data = await self.client.get(
                "issues.json",
                params={"project_id": project.get("id"), "limit": 100},
            )
            seen: dict[str, int] = {}
            for issue in data.get("issues", []):
                for field in ("author", "assigned_to"):
                    u = issue.get(field) or {}
                    uid: int | None = u.get("id")
                    if uid is not None:
                        key = u.get("name", "").lower()
                        if key and key not in seen:
                            seen[key] = uid
                        login = u.get("login", "").lower()
                        if login and login not in seen:
                            seen[login] = uid
            for name, uid in seen.items():
                if name == assigned_to.lower():
                    return uid
            return None
        for u in data.get("users", []):
            if (
                str(u.get("login", "")).lower() == assigned_to.lower()
                or str(u.get("name", "")).lower() == assigned_to.lower()
            ):
                return u.get("id")
        return None

    async def _safe_resolve_assignee(self, assigned_to: str) -> int | None:
        """Resolve an assignee, swallowing HTTP errors as 'not found'."""
        try:
            return await self._resolve_assignee(assigned_to)
        except httpx.HTTPStatusError:
            return None

    async def _get_project_versions(self) -> list[dict]:
        """Return the versions for the default project (works on all supported versions)."""
        profile = await self._ensure_profile()
        project = await self._resolve_project()
        project_id = project.get("id")
        if project_id is None:
            return []
        data = await self.client.get(profile.list_versions_path(project_id))
        return data.get("versions", [])

    async def _safe_resolve_version_id(self, iteration_path: str) -> int | None:
        try:
            versions = await self._get_project_versions()
            version = next((v for v in versions if v.get("name") == iteration_path), None)
            return version.get("id") if version else None
        except httpx.HTTPStatusError:
            return None

    # ──────────────────────────────────────────────
    #  TICKET TYPES
    # ──────────────────────────────────────────────

    async def get_ticket_types(self) -> str:
        """Retrieve all valid ticket types (issue types or trackers per profile)."""
        try:
            result = await self._list_issue_types()
            return json.dumps({"ticket_types": result, "total": len(result)}, ensure_ascii=False)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    async def check(self) -> str:
        """Verify the connection to Redmine.

        Ensures the profile is selected (from the declared version, no
        server probe) and confirms connectivity via ``projects.json`` —
        the single endpoint that is available on every supported version,
        including 4.x deployments where ``my_account.json`` is restricted
        or absent. The authenticated user is *not* read: ``my_account.json``
        is not relied upon anywhere in this backend.
        """
        try:
            profile = await self._ensure_profile()
            await self.client.get("projects.json")
            return json.dumps(
                {
                    "status": "ok",
                    "backend": "redmine",
                    "url": self.client.base_url,
                    "project": self.client.project,
                    "profile": profile.name,
                    "message": f"Connection to Redmine {self.client.base_url} OK.",
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    # ──────────────────────────────────────────────
    #  TICKET CRUD
    # ──────────────────────────────────────────────

    async def get_ticket(self, ticket_id: int) -> str:
        """Retrieve full details of a Ticket (Redmine issue) by ID."""
        try:
            data = await self.client.get(f"issues/{ticket_id}.json")
            return format_ticket(data.get("issue", {}), include_description=True)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    async def get_ticket_comments(self, ticket_id: int) -> str:
        """Retrieve all comments (Redmine journal notes) associated with a ticket.

        Journals are not returned by default; the ``include=journals`` flag
        is required or Redmine omits the ``journals`` key entirely.
        """
        try:
            data = await self.client.get(f"issues/{ticket_id}.json", params={"include": ["journals"]})
            journals = data.get("issue", {}).get("journals", []) or []
            result = []
            for j in journals:
                note = (j.get("notes") or "").strip()
                if not note:
                    continue
                result.append(
                    {
                        "id": j.get("id"),
                        "author": (j.get("user", {}) or {}).get("name", "Unknown"),
                        "created_date": j.get("created_on", ""),
                        "text": strip_wiki(note),
                    }
                )
            if not result:
                return json.dumps(
                    {
                        "ticket_id": ticket_id,
                        "comments": [],
                        "message": "No comments found.",
                    }
                )
            return json.dumps(
                {"ticket_id": ticket_id, "total": len(result), "comments": result},
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    async def add_comment(self, ticket_id: int, text: str) -> str:
        """Add a comment to a Ticket (Redmine journal note)."""
        try:
            data = await self.client.put(
                f"issues/{ticket_id}.json",
                data={"issue": {"notes": text}},
            )
            journals = data.get("issue", {}).get("journals", []) or []
            comment_id = journals[-1].get("id") if journals else None
            return json.dumps(
                {
                    "success": True,
                    "ticket_id": ticket_id,
                    "comment_id": comment_id,
                    "message": "Comment added successfully.",
                }
            )
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    async def add_mermaid(
        self,
        ticket_id: int,
        diagram: str,
        title: str = "",
        section: str = "Diagrams",
    ) -> str:
        """Render a Mermaid diagram to a PNG and attach it to a Ticket.

        Redmine renders the description as HTML and does not render Mermaid
        natively, so the diagram is rendered locally (bundled ``mermaidx``
        engine) to a high-resolution PNG, attached to the issue through the
        ``attachments`` endpoint, and embedded at the bottom of the description
        with an ``<img>`` tag (same approach as the Azure connector).

        Args:
            ticket_id: ID of the issue
            diagram: Mermaid diagram source
            title: Optional label used as the image caption.
            section: Ignored on Redmine (kept for protocol consistency).
        """
        if not diagram or not diagram.strip():
            return json.dumps({"error": "diagram is required."})

        try:
            png_bytes = await asyncio.to_thread(render_mermaid_png, diagram)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to render Mermaid diagram: {exc}"})

        file_name = f"mermaid-{ticket_id}.png"
        try:
            async with httpx.AsyncClient(timeout=30.0, verify=self.client.verify) as client:
                response = await client.post(
                    f"{self.client.base_url}/attachments.json",
                    headers={"X-Redmine-API-Key": self.client.api_key, "Accept": "application/json"},
                    files={"file": (file_name, png_bytes, "image/png")},
                )
                response.raise_for_status()
                attachment = response.json().get("attachment", {})
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

        try:
            issue = await self.client.get(f"issues/{ticket_id}.json")
            description = (issue.get("issue", {}).get("description", "") or "").rstrip()
            caption = title or "Mermaid diagram"
            download_url = f"{self.client.base_url}/attachments/download/{attachment.get('id')}"
            image_html = f'<p><img src="{download_url}" alt="{html.escape(caption)}"></p>'
            new_description = f"{description}\n{image_html}" if description else image_html
            await self.client.put(
                f"issues/{ticket_id}.json",
                data={"issue": {"description": new_description, "attachment_ids": [attachment.get("id")]}},
            )
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

        return json.dumps(
            {
                "success": True,
                "ticket_id": ticket_id,
                "file_name": file_name,
                "attachment_id": attachment.get("id"),
                "url": f"{self.client.base_url}/attachments/download/{attachment.get('id')}",
                "renderer": "mermaidx",
                "message": f"Mermaid diagram embedded in ticket #{ticket_id}.",
            },
            ensure_ascii=False,
        )

    async def add_ticket_image(self, ticket_id: int, image_path: str, comment: str = "") -> str:
        """Attach an image file to a Ticket (Redmine issue attachment).

        Args:
            ticket_id: ID of the issue
            image_path: local path to an image file (PNG, JPEG, GIF, WebP, ...)
            comment: optional note embedded with the image
        """
        if not image_path or not os.path.isfile(image_path) or not is_image_file(image_path):
            return json.dumps({"error": f"image_path is not a readable image file: {image_path!r}"})

        file_name = os.path.basename(image_path)
        blob = await asyncio.to_thread(read_image_file, image_path)
        if blob is None:
            return json.dumps({"error": f"Could not read {image_path!r}."})

        try:
            async with httpx.AsyncClient(timeout=30.0, verify=self.client.verify) as client:
                response = await client.post(
                    f"{self.client.base_url}/attachments.json",
                    headers={"X-Redmine-API-Key": self.client.api_key, "Accept": "application/json"},
                    files={"file": (file_name, blob)},
                )
                response.raise_for_status()
                attachment = response.json().get("attachment", {})
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

        note = comment or file_name
        try:
            await self.client.put(
                f"issues/{ticket_id}.json",
                data={"issue": {"notes": note, "attachment_ids": [attachment.get("id")]}},
            )
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

        return json.dumps(
            {
                "success": True,
                "ticket_id": ticket_id,
                "file_name": file_name,
                "attachment_id": attachment.get("id"),
                "url": f"{self.client.base_url}/attachments/download/{attachment.get('id')}",
                "message": f"Image '{file_name}' attached to ticket #{ticket_id}.",
            },
            ensure_ascii=False,
        )

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
        """Create a new Ticket (Redmine issue).

        Args:
            ticket_type: Issue type name (or tracker name on 4.2 – 6.x).
                Defaults to the project's default issue type when empty.
            title: Issue subject
            description: HTML or Markdown description (Markdown is converted)
            assigned_to: User login or full name of the assignee
            priority: Priority (1-4), mapped to the instance priorities by name
            tags: Ignored for Redmine (issues have no native tags)
            area_path: Ignored for Redmine
            iteration_path: Version name to assign (e.g. 'v1.2')
        """
        if not title:
            return json.dumps({"error": "title is required."})

        try:
            project = await self._safe_resolve_project()
            if project is None:
                return json.dumps({"error": f"Could not resolve project '{self.client.project}'."})
            profile = await self._ensure_profile()
            issue: dict[str, Any] = {
                "project_id": project.get("id"),
                "subject": title,
            }
            if ticket_type:
                issue_type_id = await self._safe_resolve_issue_type(ticket_type)
                if issue_type_id is None:
                    return json.dumps({"error": f"ticket_type '{ticket_type}' not found on this Redmine instance."})
                issue[profile.issue_type_id_field()] = issue_type_id
            if description:
                issue["description"] = markdown_to_html(description)
            if assigned_to:
                assignee_id = await self._safe_resolve_assignee(assigned_to)
                if assignee_id is None:
                    return json.dumps({"error": f"assignee '{assigned_to}' not found on this Redmine instance."})
                issue["assigned_to_id"] = assignee_id
            if priority > 0:
                priority_id = await self._resolve_priority(priority)
                if priority_id is None:
                    return json.dumps({"error": f"priority {priority} not found on this Redmine instance."})
                issue["priority_id"] = priority_id
            if iteration_path:
                version_id = await self._safe_resolve_version_id(iteration_path)
                if version_id is None:
                    versions = await self._get_project_versions()
                    return json.dumps(
                        {
                            "error": f"version '{iteration_path}' not found in project "
                            f"'{project.get('name', project.get('identifier'))}'.",
                            "available_iterations": [v.get("name") for v in versions],
                        }
                    )
                issue["fixed_version_id"] = version_id

            data = await self.client.post("issues.json", data={"issue": issue})
            created = data.get("issue", {})
            created_type = first_field(created, "issue_type").get("name", "Issue")
            return json.dumps(
                {
                    "success": True,
                    "id": created.get("id"),
                    "title": title,
                    "type": ticket_type or created_type,
                    "url": f"{self.client.base_url}/issues/{created.get('id')}",
                    "message": f"Ticket #{created.get('id')} created successfully.",
                }
            )
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

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
        """Update an existing Ticket (Redmine issue). Only provided fields will be modified.

        Args:
            ticket_id: ID of the issue to update
            title: New subject
            description: New description (HTML or Markdown)
            assigned_to: New assignee login or name (leave empty to keep)
            state: New status. Accepts a Redmine status name or a generic word
                (open/active/resolved/closed/done).
            priority: New priority (1-4)
            tags: Ignored for Redmine
            area_path: Ignored for Redmine
            iteration_path: Version name (empty string to keep)
        """
        issue: dict[str, Any] = {}

        if title:
            issue["subject"] = title
        if description:
            issue["description"] = markdown_to_html(description)
        if assigned_to:
            assignee_id = await self._safe_resolve_assignee(assigned_to)
            if assignee_id is None:
                return json.dumps({"error": f"assignee '{assigned_to}' not found on this Redmine instance."})
            issue["assigned_to_id"] = assignee_id
        if state:
            status_id = await self._safe_resolve_status(state)
            if status_id is None:
                return json.dumps({"error": f"state '{state}' does not match any status on this Redmine instance."})
            issue["status_id"] = status_id
        if priority > 0:
            priority_id = await self._resolve_priority(priority)
            if priority_id is None:
                return json.dumps({"error": f"priority {priority} not found on this Redmine instance."})
            issue["priority_id"] = priority_id
        if iteration_path:
            version_id = await self._safe_resolve_version_id(iteration_path)
            if version_id is None:
                versions = await self._get_project_versions()
                return json.dumps(
                    {
                        "error": f"version '{iteration_path}' not found.",
                        "available_iterations": [v.get("name") for v in versions],
                    }
                )
            issue["fixed_version_id"] = version_id

        if not issue:
            return json.dumps({"error": "No fields to update provided."})

        try:
            data = await self.client.put(f"issues/{ticket_id}.json", data={"issue": issue})
            updated = data.get("issue", {})
            return json.dumps(
                {
                    "success": True,
                    "id": updated.get("id", ticket_id),
                    "message": f"Ticket #{ticket_id} updated successfully.",
                    "updated_fields": list(issue.keys()),
                }
            )
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

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
        """Search tickets in Redmine using built-in filters.

        Args:
            query: Free-text search on the ticket subject. When provided,
                it is combined with the other filters (AND). Applied to the
                subject field with a wildcard, which is portable across all
                supported versions.
            ticket_type: Issue type name (or tracker name on 4.2 – 6.x)
            state: Redmine status name or generic word (open/active/closed)
            assigned_to: Assignee login or name
            tags: Ignored for Redmine
            area_path: Ignored for Redmine
            iteration_path: Version name filter
            max_results: Maximum results (default 20, max 100)
        """
        max_results = min(max(1, max_results), 100)

        params: dict[str, Any] = {
            "limit": max_results,
            "sort": "updated_on:desc",
        }
        profile = await self._ensure_profile()
        project = await self._safe_resolve_project()
        if project is None:
            return json.dumps({"error": f"Could not resolve project '{self.client.project}'."})
        params["project_id"] = project.get("id")

        if ticket_type:
            issue_type_id = await self._safe_resolve_issue_type(ticket_type)
            if issue_type_id is None:
                return json.dumps({"error": f"ticket_type '{ticket_type}' not found on this Redmine instance."})
            params[profile.issue_type_id_field()] = issue_type_id
        if state:
            status_id = await self._safe_resolve_status(state)
            if status_id is None:
                return json.dumps({"error": f"state '{state}' does not match any status on this Redmine instance."})
            params["status_id"] = status_id
        if assigned_to:
            assignee_id = await self._safe_resolve_assignee(assigned_to)
            if assignee_id is None:
                return json.dumps({"error": f"assignee '{assigned_to}' not found on this Redmine instance."})
            params["assigned_to_id"] = assignee_id
        if iteration_path:
            version_id = await self._safe_resolve_version_id(iteration_path)
            if version_id is not None:
                params["version_id"] = version_id
        if query:
            key, value = profile.free_text_param(query)
            params[key] = value

        try:
            data = await self.client.get("issues.json", params=params)
            issues = data.get("issues", [])
            results = [json.loads(format_ticket(i)) for i in issues]
            return json.dumps(
                {
                    "results": results,
                    "total": data.get("total_count", len(results)),
                    "truncated": (data.get("total_count", 0) or 0) > max_results,
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    async def get_tickets_needing_my_reply(self, my_display_name: str) -> str:
        """Find active tickets assigned to a user where the last comment was not written by them."""
        try:
            assignee_id = await self._safe_resolve_assignee(my_display_name)
            if assignee_id is None:
                return json.dumps(
                    {
                        "pending": [],
                        "message": f"No user matching '{my_display_name}' found.",
                    }
                )
            project = await self._safe_resolve_project()
            if project is None:
                return json.dumps({"error": f"Could not resolve project '{self.client.project}'."})
            # include=journals is required, else Redmine omits the journals key.
            params = {
                "assigned_to_id": assignee_id,
                "sort": "updated_on:desc",
                "limit": 20,
                "project_id": project.get("id"),
                "include": ["journals"],
            }
            data = await self.client.get("issues.json", params=params)
            issues = data.get("issues", [])

            pending = []
            for issue in issues:
                state = (issue.get("status", {}) or {}).get("name", "").lower()
                if state not in OPEN_STATES:
                    continue
                journals = issue.get("journals", []) or []
                last_note = None
                for j in reversed(journals):
                    note = (j.get("notes") or "").strip()
                    if note:
                        last_note = strip_wiki(note)
                        last_author = (j.get("user", {}) or {}).get("name", "")
                        last_date = j.get("created_on", "")
                        break
                if last_note is None:
                    continue
                if last_author.lower() != my_display_name.lower():
                    pending.append(
                        {
                            "ticket_id": issue.get("id"),
                            "title": issue.get("subject", "Untitled"),
                            "last_comment_author": last_author,
                            "last_comment_date": last_date,
                            "last_comment_preview": last_note[:200],
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
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

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
        """Link two Tickets (Redmine issues) as a relation.

        Args:
            source_id: Source issue ID
            target_id: Target issue ID
            link_type: One of 'relates to' (default), 'blocks', 'is blocked by',
                'duplicates', 'is duplicated by', 'copied from', 'copied to'.
            comment: Optional note appended to the source ticket
        """
        relation_map = {
            "related": 1,  # relates to
            "relates to": 1,
            "blocks": 2,
            "is blocked by": 3,
            "duplicates": 4,
            "is duplicated by": 5,
            "copied from": 6,
            "copied to": 7,
        }
        relation_id = relation_map.get(str(link_type).strip().lower())
        if relation_id is None:
            return json.dumps(
                {
                    "error": f"unsupported link_type '{link_type}'.",
                    "available_types": [
                        "relates to",
                        "blocks",
                        "is blocked by",
                        "duplicates",
                        "is duplicated by",
                        "copied from",
                        "copied to",
                    ],
                }
            )

        payload: dict[str, Any] = {"issue": {"relations": [{"to_issue_id": target_id, "relation_type": relation_id}]}}
        if comment:
            payload["issue"]["notes"] = comment

        try:
            await self.client.put(f"issues/{source_id}.json", data=payload)
            return json.dumps(
                {
                    "success": True,
                    "source_id": source_id,
                    "target_id": target_id,
                    "link_type": link_type,
                    "message": f"Link created: #{source_id} -> #{target_id} ({link_type})",
                }
            )
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    async def get_ticket_relations(self, ticket_id: int) -> str:
        """Retrieve all relations of a Ticket (Redmine issue)."""
        try:
            data = await self.client.get(f"issues/{ticket_id}.json")
            relations = data.get("issue", {}).get("relations", []) or []
            result: list[dict] = []
            for rel in relations:
                target = rel.get("issue", {}) or {}
                direction = (
                    "incoming"
                    if rel.get("issue_id", ticket_id) == ticket_id and rel.get("to_issue_id") != ticket_id
                    else "outgoing"
                )
                result.append(
                    {
                        "type": f"relation (id {rel.get('relation_type', '')}) {direction}",
                        "target_id": target.get("id"),
                        "comment": target.get("subject", ""),
                    }
                )
            return json.dumps(
                {"ticket_id": ticket_id, "relations": result, "total": len(result)},
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    # ──────────────────────────────────────────────
    #  ITERATIONS / VERSIONS
    # ──────────────────────────────────────────────

    async def get_iterations(self) -> str:
        """Retrieve all versions of the project (mapped to iterations)."""
        try:
            versions = await self._get_project_versions()
            result = [
                {
                    "id": v.get("id"),
                    "name": v.get("name", ""),
                    "path": v.get("name", ""),
                    "start_date": v.get("due_date", ""),
                    "finish_date": v.get("due_date", ""),
                    "state": v.get("status", ""),
                }
                for v in versions
            ]
            return json.dumps({"iterations": result, "total": len(result)}, ensure_ascii=False)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    async def move_to_iteration(self, ticket_id: int, iteration_path: str) -> str:
        """Move a Ticket (Redmine issue) to a version (milestone).

        Args:
            ticket_id: ID of the issue to update
            iteration_path: Version name to assign
        """
        try:
            version_id = await self._safe_resolve_version_id(iteration_path)
            if version_id is None:
                versions = await self._get_project_versions()
                return json.dumps(
                    {
                        "error": f"version '{iteration_path}' not found.",
                        "available_iterations": [v.get("name") for v in versions],
                    }
                )
            await self.client.put(
                f"issues/{ticket_id}.json",
                data={"issue": {"fixed_version_id": version_id}},
            )
            return json.dumps(
                {
                    "success": True,
                    "id": ticket_id,
                    "iteration_path": iteration_path,
                    "message": f"Ticket #{ticket_id} moved to '{iteration_path}'.",
                }
            )
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    async def get_area_paths(self) -> str:
        """Retrieve all issue trackers/issue types of the instance (mapped to area paths).

        Redmine has no area paths; the closest structural equivalent is the
        set of issue trackers (or issue types on 7.x).
        """
        try:
            result = await self._list_issue_types()
            return json.dumps({"area_paths": result, "total": len(result)}, ensure_ascii=False)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})
