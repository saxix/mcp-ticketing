"""Redmine connector implementing TicketProtocol.

Supports all Redmine versions (1.x through 5.x). On older versions where
``issue_types.json``, ``issues_statuses.json``, ``priorities.json``,
``my_account.json`` and global ``versions.json`` are unavailable, the
connector falls back to ``trackers.json``, discovery from existing issues,
and per-project ``versions.json`` respectively.
"""

import asyncio
import html
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any

import httpx
from dotenv import load_dotenv

from mcp_ticketing._images import is_image_file, read_image_file
from mcp_ticketing.protocol import TicketProtocol

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("mcp-ticketing-redmine")

REDMINE_URL = os.getenv("MCP_REDMINE_URL", "")
REDMINE_API_KEY = os.getenv("MCP_REDMINE_API_KEY", "")
REDMINE_PROJECT = os.getenv("MCP_REDMINE_PROJECT", "")
# "false" or "0" disables TLS verification; a path points to a CA bundle; empty keeps default.
REDMINE_VERIFY_SSL = os.getenv("MCP_REDMINE_VERIFY_SSL", "")

# The protocol's 1-4 priority scale (Urgent, High, Normal, Low) is mapped to
# instance priority IDs by name, not by numeric ID, because priority IDs are
# per-instance. See ``RedmineConnector._resolve_priority``.

# Generic state words accepted by update_ticket / search_tickets, mapped to
# Redmine status names to try (the first match wins). Includes both English
# and Italian (the most common localization) variants.
STATE_TO_STATUSES = {
    "open": ["New", "In progress", "Reopened", "Nuova", "In lavorazione", "Riaperta", "Aperta"],
    "active": ["In progress", "New", "Reopened", "In lavorazione", "Nuova", "Aperta"],
    "closed": ["Closed", "Done", "Resolved", "Chiusa", "Completata", "Risolta"],
    "resolved": ["Resolved", "Closed", "Done", "Risolta", "Chiusa", "Completata"],
    "done": ["Done", "Closed", "Completata", "Chiusa"],
    "new": ["New", "Nuova"],
    "reopened": ["Reopened", "Riaperta"],
}

OPEN_STATES = (
    "open", "active", "new", "reopened", "in progress",
    "nuova", "in lavorazione", "riaperta", "aperta",
)


class RedmineClient:
    """Thin httpx wrapper for the Redmine JSON REST API."""

    def __init__(self):
        self.base_url = REDMINE_URL.rstrip("/")
        self.api_key = REDMINE_API_KEY
        self.project = REDMINE_PROJECT
        self.verify = self._resolve_verify()

    @staticmethod
    def _resolve_verify() -> bool | str:
        """Resolve the ``verify`` argument for httpx from ``MCP_REDMINE_VERIFY_SSL``.

        - empty / unset  -> ``True`` (use the default CA bundle)
        - ``false`` / ``0`` -> ``False`` (disable TLS verification)
        - anything else  -> the value is treated as a path to a CA bundle file
        """
        value = REDMINE_VERIFY_SSL.strip()
        if not value:
            return True
        if value.lower() in ("false", "0", "no", "off"):
            return False
        return value

    @asynccontextmanager
    async def get_client(self):
        async with httpx.AsyncClient(timeout=30.0, verify=self.verify) as client:
            yield client

    def _headers(self) -> dict[str, str]:
        return {
            "X-Redmine-API-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with self.get_client() as client:
            response = await client.get(url, headers=self._headers(), params=params or {})
            response.raise_for_status()
            return response.json()

    async def post(self, path: str, data: Any, params: dict | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with self.get_client() as client:
            response = await client.post(url, headers=self._headers(), json=data, params=params or {})
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()

    async def put(self, path: str, data: Any, params: dict | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with self.get_client() as client:
            response = await client.put(url, headers=self._headers(), json=data, params=params or {})
            response.raise_for_status()
            # Old Redmine returns 204 No Content (empty body) on success.
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()


def format_ticket(issue: dict, include_description: bool = False) -> str:
    """Project a Redmine issue into the flat JSON shape used by the MCP tools.

    The description is opt-in: it is only included when ``include_description``
    is True, so that list endpoints (search_tickets) stay light while
    single-ticket reads (get_ticket) expose the body.

    Handles both old Redmine (``tracker``, ``fixed_version``) and new Redmine
    (``issue_type``, ``version``) field names.
    """
    status = issue.get("status", {}) or {}
    assigned_to = issue.get("assigned_to", {}) or {}
    priority = issue.get("priority", {}) or {}
    # New Redmine uses "issue_type"; old Redmine uses "tracker".
    issue_type = issue.get("issue_type") or issue.get("tracker") or {}
    # New Redmine uses "version"; old Redmine uses "fixed_version".
    version = issue.get("version") or issue.get("fixed_version") or {}
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


def _strip_wiki(text: str) -> str:
    """Normalise Redmine wiki markup and HTML into readable plain text.

    Redmine journal notes are stored as wiki markup (``h1.``-``h6.``
    headings, ``* ``/``** `` bullets, ``# ``/``## `` ordered lists,
    ``| `` tables, ``*text*``/``**text**`` emphasis, ``{{code}}`` blocks)
    and may also contain HTML. Both are reduced to plain text so the
    comments render correctly in a terminal TUI.
    """
    # Wiki tables (| a | b |) -> tab-separated, one row per line.
    def _table_repl(match: re.Match) -> str:
        cells = match.group(0)
        cells = re.sub(r"^\|", "", cells)
        cells = re.sub(r"\|$", "", cells)
        return "\t".join(c.strip() for c in cells.split("|"))

    text = re.sub(r"^\|.*$", _table_repl, text, flags=re.MULTILINE)
    # Wiki headings (h1. Title) -> plain line.
    text = re.sub(r"^h([1-6])\.\s*", "", text, flags=re.MULTILINE)
    # Wiki list bullets (* item) and ordered items (# item).
    text = re.sub(r"^(?:\*{1,2}|#{1,2})\s+", "", text, flags=re.MULTILINE)
    # Emphasis and formatting markers.
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_{2,}(.+?)_{2,}", r"\1", text)
    # Single-underscore italic (_text_) only at word boundaries, so that
    # identifiers like STATO_DISTINTA are left untouched.
    text = re.sub(r"(?<![\w_])_([^_]+)_(?![\w_])", r"\1", text)
    text = re.sub(r"{{(.+?)}}", r"\1", text, flags=re.DOTALL)
    # HTML tags.
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _markdown_to_html(text: str) -> str:
    """Convert Markdown to HTML for Redmine's HTML description field.

    Redmine stores the description as HTML. If the caller passes Markdown
    (headings, **bold**, lists, tables, fenced code, etc.) we render it to
    HTML so it displays correctly. Plain HTML is passed through unchanged
    (same heuristic as the Azure connector).
    """
    if not text:
        return text
    markdown_markers = ("**", "__", "```", "## ", " - ", "- ", "* ", "1. ", "> ", "[", "](", " | ", "|")
    looks_like_markdown = any(m in text for m in markdown_markers)
    looks_like_html = text.strip().startswith("<") and ">" in text
    if looks_like_html and not looks_like_markdown:
        return text
    try:
        import markdown  # local import so missing dep doesn't break module import

        return markdown.markdown(text, extensions=["extra", "tables", "fenced_code", "codehilite"])
    except ImportError:
        return text


def _render_mermaid_png(diagram: str, scale: float = 2.0) -> bytes:
    """Render a Mermaid diagram source to a high-resolution PNG.

    Same engine as the Azure connector (bundled ``mermaidx``), 2x scale for
    crisp zooming.
    """
    from mermaidx import render

    return render(diagram).png(scale=scale, background="#ffffff")


class RedmineConnector(TicketProtocol):
    """Redmine implementation of the TicketProtocol strategy.

    Configuration comes from the environment:
    ``MCP_REDMINE_URL`` (instance base URL), ``MCP_REDMINE_API_KEY``
    (API key from a user's profile page) and ``MCP_REDMINE_PROJECT``
    (project identifier or ID used as the default scope for tickets).

    Works with all Redmine versions (1.x through 5.x). On older versions
    where certain API endpoints are unavailable, the connector falls back
    to alternative endpoints or discovery from existing data.
    """

    def __init__(self, client: RedmineClient | None = None):
        self.client = client or RedmineClient()
        self._project: dict | None = None
        self._statuses: list[dict] | None = None
        self._priorities: list[dict] | None = None

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

    async def _get_statuses(self) -> list[dict]:
        """Return the list of issue statuses, discovering them if the endpoint is unavailable.

        New Redmine exposes ``issues_statuses.json``. On older versions the
        endpoint is missing, so we fall back to collecting distinct statuses
        from a sample of existing issues.
        """
        if self._statuses is not None:
            return self._statuses
        try:
            data = await self.client.get("issues_statuses.json")
            self._statuses = data.get("issues_statuses", [])
            return self._statuses
        except httpx.HTTPStatusError:
            pass
        # Fallback: discover statuses from existing issues
        project = await self._resolve_project()
        data = await self.client.get(
            "issues.json",
            params={"project_id": project.get("id"), "limit": 100},
        )
        seen: dict[str, dict] = {}
        for issue in data.get("issues", []):
            s = issue.get("status") or {}
            if s.get("id") is not None:
                seen[str(s.get("id"))] = {"id": s.get("id"), "name": s.get("name", "")}
        self._statuses = list(seen.values())
        return self._statuses

    async def _get_priorities(self) -> list[dict]:
        """Return the list of priorities, discovering them from existing issues.

        Most Redmine versions do not expose a ``priorities.json`` endpoint,
        and the numeric IDs are not portable across instances (each
        installation configures its own set). We therefore discover the
        available priorities from a global sample of issues (not scoped to
        the project, so that priorities unused in the project are still
        found).
        """
        if self._priorities is not None:
            return self._priorities
        # Discover priorities globally (not per-project) by paginating
        # through issues, so that all configured priorities are found even
        # if the current project has no issues with a particular level.
        # Redmine caps each request at 100 issues, so we page through.
        seen: dict[str, dict] = {}
        page_size = 100
        for page in range(10):  # up to 1000 issues
            data = await self.client.get(
                "issues.json",
                params={"limit": page_size, "offset": page * page_size},
            )
            issues = data.get("issues", [])
            if not issues:
                break
            for issue in issues:
                p = issue.get("priority") or {}
                if p.get("id") is not None:
                    seen[str(p.get("id"))] = {"id": p.get("id"), "name": p.get("name", "")}
            if len(issues) < page_size:
                break
        self._priorities = list(seen.values())
        return self._priorities

    async def _resolve_priority(self, priority: int) -> int | None:
        """Map the protocol's 1-4 priority scale to an instance priority ID.

        The mapping is by name (Urgent/High/Normal/Low), not by numeric ID,
        because priority IDs are per-instance. Returns None when no priority
        on this instance matches the requested level.
        """
        if priority <= 0:
            return None
        wanted = {1: "urgent", 2: "high", 3: "normal", 4: "low"}.get(priority, "")
        if not wanted:
            return None
        synonyms = {
            "urgent": ["urgent", "urgente", "immediat", "immediat*"],
            "high": ["high", "alta", "elevat"],
            "normal": ["normal", "normale", "media", "medio"],
            "low": ["low", "bassa", "basso"],
        }[wanted]
        for p in await self._get_priorities():
            name = p.get("name", "").lower()
            for syn in synonyms:
                if syn.rstrip("*") in name:
                    return p.get("id")
        return None

    async def _resolve_issue_type(self, ticket_type: str) -> int | None:
        """Resolve an issue type (or tracker on old Redmine) by name (case-insensitive)."""
        if not ticket_type:
            return None
        # Try new Redmine first
        try:
            data = await self.client.get("issue_types.json")
            for t in data.get("issue_types", []):
                if t.get("name", "").lower() == ticket_type.lower():
                    return t.get("id")
            return None
        except httpx.HTTPStatusError:
            pass
        # Fallback: use trackers (old Redmine)
        data = await self.client.get("trackers.json")
        for t in data.get("trackers", []):
            if t.get("name", "").lower() == ticket_type.lower():
                return t.get("id")
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
            if str(u.get("login", "")).lower() == assigned_to.lower() or str(
                u.get("name", "")
            ).lower() == assigned_to.lower():
                return u.get("id")
        return None

    async def _get_project_versions(self) -> list[dict]:
        """Return the versions for the default project (works on all Redmine versions)."""
        project = await self._resolve_project()
        try:
            # Try per-project endpoint first (works on all versions)
            data = await self.client.get(f"projects/{project.get('id')}/versions.json")
            return data.get("versions", [])
        except httpx.HTTPStatusError:
            pass
        # Fallback: global versions.json (new Redmine only)
        data = await self.client.get("versions.json", params={"project_id": project.get("id")})
        return data.get("versions", [])

    # ──────────────────────────────────────────────
    #  TICKET TYPES
    # ──────────────────────────────────────────────

    async def get_ticket_types(self) -> str:
        """Retrieve all valid ticket types.

        On new Redmine returns issue types; on old Redmine returns trackers
        (the closest equivalent).
        """
        try:
            # Try new Redmine first
            try:
                data = await self.client.get("issue_types.json")
                result = [
                    {
                        "name": t.get("name"),
                        "description": "Default issue type" if t.get("is_default") else "",
                    }
                    for t in data.get("issue_types", [])
                ]
                return json.dumps({"ticket_types": result, "total": len(result)}, ensure_ascii=False)
            except httpx.HTTPStatusError:
                pass
            # Fallback: trackers
            data = await self.client.get("trackers.json")
            result = [
                {"name": t.get("name", ""), "description": t.get("description", "") or ""}
                for t in data.get("trackers", [])
            ]
            return json.dumps({"ticket_types": result, "total": len(result)}, ensure_ascii=False)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    async def check(self) -> str:
        """Verify the connection to Redmine.

        Uses ``my_account.json`` on new Redmine; falls back to
        ``projects.json`` on older versions.
        """
        try:
            try:
                data = await self.client.get("my_account.json")
                user = data.get("user", {})
                return json.dumps(
                    {
                        "status": "ok",
                        "backend": "redmine",
                        "url": self.client.base_url,
                        "project": self.client.project,
                        "user": user.get("name", user.get("login", "")),
                        "message": f"Connection to Redmine {self.client.base_url} OK as {user.get('login', '')}.",
                    },
                    ensure_ascii=False,
                )
            except httpx.HTTPStatusError:
                pass
            # Fallback: verify via projects.json
            data = await self.client.get("projects.json")
            projects = data.get("projects", [])
            project = next((p for p in projects if p.get("identifier") == self.client.project), None)
            if project is None and self.client.project:
                return json.dumps(
                    {"error": f"Project '{self.client.project}' not found."},
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "status": "ok",
                    "backend": "redmine",
                    "url": self.client.base_url,
                    "project": self.client.project,
                    "user": "unknown (my_account.json not available)",
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
        """Retrieve full details of a Ticket (Redmine issue) by ID.

        Includes the HTML description in the ``description`` key.
        """
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
            data = await self.client.get(
                f"issues/{ticket_id}.json", params={"include": ["journals"]}
            )
            journals = (data.get("issue", {}).get("journals", []) or [])
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
                        "text": _strip_wiki(note),
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
            journals = (data.get("issue", {}).get("journals", []) or [])
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
            diagram: Mermaid diagram source (e.g. "flowchart TD\\n  A[Start] --> B[End]")
            title: Optional label used as the image caption.
            section: Ignored on Redmine (kept for protocol consistency).
        """
        if not diagram or not diagram.strip():
            return json.dumps({"error": "diagram is required."})

        try:
            png_bytes = await asyncio.to_thread(_render_mermaid_png, diagram)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to render Mermaid diagram: {exc}"})

        file_name = f"mermaid-{ticket_id}.png"
        async with httpx.AsyncClient(timeout=30.0, verify=self.client.verify) as client:
            response = await client.post(
                f"{self.client.base_url}/attachments.json",
                headers={"X-Redmine-API-Key": self.client.api_key, "Accept": "application/json"},
                files={"file": (file_name, png_bytes, "image/png")},
            )
            response.raise_for_status()
            attachment = response.json().get("attachment", {})

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

        async with httpx.AsyncClient(timeout=30.0, verify=self.client.verify) as client:
            response = await client.post(
                f"{self.client.base_url}/attachments.json",
                headers={"X-Redmine-API-Key": self.client.api_key, "Accept": "application/json"},
                files={"file": (file_name, blob)},
            )
            response.raise_for_status()
            attachment = response.json().get("attachment", {})

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
            ticket_type: Issue type name (new Redmine) or tracker name (old Redmine).
                Defaults to the project's default issue type when empty.
            title: Issue subject
            description: HTML or Markdown description (Markdown is converted)
            assigned_to: User login or full name of the assignee
            priority: Priority (1-4), mapped to Redmine's built-in priorities
                (Urgent, High, Normal, Low)
            tags: Ignored for Redmine (issues have no native tags)
            area_path: Ignored for Redmine
            iteration_path: Version name to assign (e.g. 'v1.2')
        """
        if not title:
            return json.dumps({"error": "title is required."})

        try:
            project = await self._resolve_project()
            issue: dict[str, Any] = {
                "project_id": project.get("id"),
                "subject": title,
            }
            if ticket_type:
                issue_type_id = await self._resolve_issue_type(ticket_type)
                if issue_type_id is None:
                    return json.dumps(
                        {"error": f"ticket_type '{ticket_type}' not found on this Redmine instance."}
                    )
                issue["tracker_id"] = issue_type_id
            if description:
                issue["description"] = _markdown_to_html(description)
            if assigned_to:
                assignee_id = await self._resolve_assignee(assigned_to)
                if assignee_id is None:
                    return json.dumps(
                        {"error": f"assignee '{assigned_to}' not found on this Redmine instance."}
                    )
                issue["assigned_to_id"] = assignee_id
            if priority > 0:
                priority_id = await self._resolve_priority(priority)
                if priority_id is None:
                    return json.dumps(
                        {"error": f"priority {priority} not found on this Redmine instance."}
                    )
                issue["priority_id"] = priority_id
            if iteration_path:
                versions = await self._get_project_versions()
                version = next((v for v in versions if v.get("name") == iteration_path), None)
                if version is None:
                    return json.dumps(
                        {
                            "error": f"version '{iteration_path}' not found in project "
                            f"'{project.get('name', project.get('identifier'))}'.",
                            "available_iterations": [v.get("name") for v in versions],
                        }
                    )
                issue["fixed_version_id"] = version.get("id")

            data = await self.client.post("issues.json", data={"issue": issue})
            created = data.get("issue", {})
            created_type = (
                created.get("issue_type", {}) or created.get("tracker", {}) or {}
            ).get("name", "Issue")
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
            state: New status. Accepts a Redmine status name (e.g. 'Closed',
                'Chiusa') or a generic word (open/active/resolved/closed/done).
            priority: New priority (1-4)
            tags: Ignored for Redmine
            area_path: Ignored for Redmine
            iteration_path: Version name (empty string to keep)
        """
        issue: dict[str, Any] = {}

        if title:
            issue["subject"] = title
        if description:
            issue["description"] = _markdown_to_html(description)
        if assigned_to:
            assignee_id = await self._safe_resolve_assignee(assigned_to)
            if assignee_id is None:
                return json.dumps({"error": f"assignee '{assigned_to}' not found on this Redmine instance."})
            issue["assigned_to_id"] = assignee_id
        if state:
            status = await self._resolve_status(state)
            if status is None:
                for candidate in STATE_TO_STATUSES.get(state.lower(), []):
                    status = await self._resolve_status(candidate)
                    if status:
                        break
            if status is None:
                return json.dumps(
                    {"error": f"state '{state}' does not match any status on this Redmine instance."}
                )
            issue["status_id"] = status.get("id")
        if priority > 0:
            priority_id = await self._resolve_priority(priority)
            if priority_id is None:
                return json.dumps(
                    {"error": f"priority {priority} not found on this Redmine instance."}
                )
            issue["priority_id"] = priority_id
        if iteration_path:
            versions = await self._get_project_versions()
            version = next((v for v in versions if v.get("name") == iteration_path), None)
            if version is None:
                return json.dumps(
                    {"error": f"version '{iteration_path}' not found.",
                     "available_iterations": [v.get("name") for v in versions]}
                )
            issue["fixed_version_id"] = version.get("id")

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

    async def _safe_resolve_assignee(self, assigned_to: str) -> int | None:
        """Resolve an assignee, swallowing HTTP errors as 'not found'."""
        try:
            return await self._resolve_assignee(assigned_to)
        except httpx.HTTPStatusError:
            return None

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
                it is combined with the other filters (AND). Note: older
                Redmine versions do not support the ``query`` REST parameter
                (it returns HTTP 500), so the search is applied to the
                subject field with a wildcard, which is the closest
                portable equivalent.
            ticket_type: Issue type name (new Redmine) or tracker name (old Redmine)
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
        try:
            project = await self._resolve_project()
            params["project_id"] = project.get("id")
        except (httpx.HTTPStatusError, ValueError) as e:
            return json.dumps({"error": f"Could not resolve project: {e}"})

        if ticket_type:
            issue_type_id = await self._safe_resolve_issue_type(ticket_type)
            if issue_type_id is None:
                return json.dumps({"error": f"ticket_type '{ticket_type}' not found on this Redmine instance."})
            params["tracker_id"] = issue_type_id
        if state:
            status_id = await self._safe_resolve_status(state)
            if status_id is None:
                return json.dumps(
                    {"error": f"state '{state}' does not match any status on this Redmine instance."}
                )
            params["status_id"] = status_id
        if assigned_to:
            assignee_id = await self._safe_resolve_assignee(assigned_to)
            if assignee_id is None:
                return json.dumps({"error": f"assignee '{assigned_to}' not found on this Redmine instance."})
            params["assigned_to_id"] = assignee_id
        if iteration_path:
            versions = await self._get_project_versions()
            version = next((v for v in versions if v.get("name") == iteration_path), None)
            if version:
                params["version_id"] = version.get("id")
        if query:
            # The 'query' REST parameter is not supported by older Redmine
            # versions (it returns HTTP 500). Use the subject field with a
            # wildcard instead, which is portable across all versions.
            params["subject"] = f"~{query}"

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
            assignee_id = await self._resolve_assignee(my_display_name)
            if assignee_id is None:
                return json.dumps(
                    {
                        "pending": [],
                        "message": f"No user matching '{my_display_name}' found.",
                    }
                )
            params = {"assigned_to_id": assignee_id, "sort": "updated_on:desc", "limit": 20}
            project = await self._resolve_project()
            params["project_id"] = project.get("id")
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
                        last_note = _strip_wiki(note)
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

        payload: dict[str, Any] = {
            "issue": {"relations": [{"to_issue_id": target_id, "relation_type": relation_id}]}
        }
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
            relations = (data.get("issue", {}).get("relations", []) or [])
            result = []
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
            result = []
            for v in versions:
                result.append(
                    {
                        "id": v.get("id"),
                        "name": v.get("name", ""),
                        "path": v.get("name", ""),
                        "start_date": v.get("due_date", ""),
                        "finish_date": v.get("due_date", ""),
                        "state": v.get("status", ""),
                    }
                )
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
            versions = await self._get_project_versions()
            version = next((v for v in versions if v.get("name") == iteration_path), None)
            if version is None:
                return json.dumps(
                    {
                        "error": f"version '{iteration_path}' not found.",
                        "available_iterations": [v.get("name") for v in versions],
                    }
                )
            await self.client.put(
                f"issues/{ticket_id}.json",
                data={"issue": {"fixed_version_id": version.get("id")}},
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
        """Retrieve all issue trackers of the instance (mapped to area paths).

        Redmine has no area paths; the closest structural equivalent is the
        set of issue trackers, which is what other backends expose as the
        project's working categories.
        """
        try:
            data = await self.client.get("trackers.json")
            trackers = data.get("trackers", [])
            result = [
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", "") or "",
                    "color": t.get("color", ""),
                }
                for t in trackers
            ]
            return json.dumps({"area_paths": result, "total": len(result)}, ensure_ascii=False)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    # ──────────────────────────────────────────────
    #  SAFE RESOLVERS (used by search, return None on HTTP errors)
    # ──────────────────────────────────────────────

    async def _safe_resolve_issue_type(self, ticket_type: str) -> int | None:
        try:
            return await self._resolve_issue_type(ticket_type)
        except httpx.HTTPStatusError:
            return None

    async def _safe_resolve_status(self, state: str) -> int | None:
        status = None
        try:
            status = await self._resolve_status(state)
        except httpx.HTTPStatusError:
            return None
        if status is None:
            for candidate in STATE_TO_STATUSES.get(state.lower(), []):
                try:
                    status = await self._resolve_status(candidate)
                except httpx.HTTPStatusError:
                    break
                if status:
                    break
        return status.get("id") if status else None

    async def _safe_resolve_version_id(self, iteration_path: str) -> int | None:
        try:
            versions = await self._get_project_versions()
            version = next((v for v in versions if v.get("name") == iteration_path), None)
            return version.get("id") if version else None
        except httpx.HTTPStatusError:
            return None
