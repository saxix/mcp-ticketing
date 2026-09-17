"""Azure DevOps connector implementing TicketProtocol."""

import asyncio
import html
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from dotenv import load_dotenv

from mcp_ticketing._images import is_image_file, read_image_file
from mcp_ticketing.protocol import TicketProtocol

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mcp-ticketing-azure")

AZDO_ORG = os.getenv("MCP_AZURE_DEVOPS_ORG", "")
AZDO_PROJECT = os.getenv("MCP_AZURE_DEVOPS_PROJECT", "")
AZDO_PAT = os.getenv("MCP_AZDO_PAT", "")

API_VERSION = "7.1"
COMMENT_API_VERSION = "7.1-preview.3"


class AzdoClient:
    def __init__(self):
        self.org = AZDO_ORG
        self.project = AZDO_PROJECT
        self.pat = AZDO_PAT
        self.base_url = f"https://dev.azure.com/{self.org}/{self.project}/_apis"

    @asynccontextmanager
    async def get_client(self):
        async with httpx.AsyncClient(timeout=30.0) as client:
            yield client

    def _auth(self) -> tuple[str, str]:
        return ("", self.pat)

    async def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{path}"
        async with self.get_client() as client:
            response = await client.get(
                url,
                auth=self._auth(),
                headers={"Accept": "application/json"},
                params=params,
            )
            response.raise_for_status()
            return response.json()

    async def post(
        self,
        path: str,
        data: Any,
        content_type: str = "application/json",
        params: dict | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path}"
        async with self.get_client() as client:
            response = await client.post(
                url,
                auth=self._auth(),
                headers={"Content-Type": content_type, "Accept": "application/json"},
                json=data,
                params=params,
            )
            response.raise_for_status()
            return response.json()

    async def patch(
        self,
        path: str,
        data: Any,
        content_type: str = "application/json-patch+json",
        params: dict | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path}"
        async with self.get_client() as client:
            response = await client.patch(
                url,
                auth=self._auth(),
                headers={"Content-Type": content_type, "Accept": "application/json"},
                json=data,
                params=params,
            )
            response.raise_for_status()
            return response.json()

    async def post_bytes(
        self,
        path: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        params: dict | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path}"
        async with self.get_client() as client:
            response = await client.post(
                url,
                auth=self._auth(),
                headers={"Content-Type": content_type, "Accept": "application/json"},
                content=data,
                params=params,
            )
            response.raise_for_status()
            return response.json()


def format_ticket(item: dict) -> str:
    fields = item.get("fields", {})
    assigned = fields.get("System.AssignedTo", {})
    assigned_name = (
        assigned.get("displayName", "Unassigned")
        if isinstance(assigned, dict)
        else str(assigned)
    )
    return json.dumps(
        {
            "id": item.get("id"),
            "title": fields.get("System.Title", ""),
            "type": fields.get("System.WorkItemType", ""),
            "state": fields.get("System.State", ""),
            "assigned_to": assigned_name,
            "tags": fields.get("System.Tags", ""),
            "area_path": fields.get("System.AreaPath", ""),
            "iteration_path": fields.get("System.IterationPath", ""),
            "priority": fields.get("Microsoft.VSTS.Common.Priority"),
            "created_date": fields.get("System.CreatedDate", ""),
            "changed_date": fields.get("System.ChangedDate", ""),
        },
        indent=2,
        ensure_ascii=False,
    )


def _render_mermaid_png(diagram: str, scale: float = 2.0) -> bytes:
    """Render a Mermaid diagram source to a high-resolution PNG.

    Uses the bundled ``mermaidx`` engine (embedded QuickJS running the real
    Mermaid v11 library, rasterized by resvg) — fully offline, no browser.
    A 2x scale factor produces a crisp image that stays readable when
    zoomed into.
    """
    from mermaidx import render

    return render(diagram).png(scale=scale, background="#ffffff")


class AzureConnector(TicketProtocol):
    """Azure DevOps implementation of the TicketProtocol strategy."""

    def __init__(self, client: AzdoClient | None = None):
        self.client = client or AzdoClient()

    # ──────────────────────────────────────────────
    #  TICKET TYPES
    # ──────────────────────────────────────────────

    async def get_ticket_types(self) -> str:
        """Retrieve all valid ticket types configured for the project (e.g. Task, Bug, User Story, Issue, Change Request)."""
        try:
            data = await self.client.get(
                "wit/workitemtypes", params={"api-version": API_VERSION}
            )
            types = data.get("workItemTypes", data.get("value", []))
            result = []
            for t in types:
                result.append(
                    {
                        "name": t.get("name"),
                        "description": t.get("description", ""),
                    }
                )
            return json.dumps(
                {"ticket_types": result, "total": len(result)},
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    async def check(self) -> str:
        """Verify the connection to Azure DevOps (reads the work item types)."""
        try:
            await self.client.get(
                "wit/workitemtypes", params={"api-version": API_VERSION}
            )
            return json.dumps(
                {
                    "status": "ok",
                    "backend": "azure",
                    "organization": AZDO_ORG,
                    "project": AZDO_PROJECT,
                    "message": f"Connection to Azure DevOps '{AZDO_ORG}/{AZDO_PROJECT}' OK.",
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    # ──────────────────────────────────────────────
    #  TICKET CRUD
    # ──────────────────────────────────────────────

    async def get_ticket(self, ticket_id: int) -> str:
        """Retrieve full details of a Ticket (Task, Bug, User Story) from Azure DevOps."""
        try:
            data = await self.client.get(
                f"wit/workitems/{ticket_id}", params={"api-version": API_VERSION}
            )
            return format_ticket(data)
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    async def get_ticket_comments(self, ticket_id: int) -> str:
        """Retrieve all comments associated with a Ticket in Azure DevOps."""
        try:
            data = await self.client.get(
                f"wit/workitems/{ticket_id}/comments",
                params={"api-version": COMMENT_API_VERSION},
            )
            comments = data.get("comments", [])
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
                result.append(
                    {
                        "id": c.get("id"),
                        "author": c.get("createdBy", {}).get("displayName", "Unknown"),
                        "created_date": c.get("createdDate", ""),
                        "text": c.get("text", ""),
                    }
                )
            return json.dumps(
                {"ticket_id": ticket_id, "total": len(result), "comments": result},
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    async def add_comment(self, ticket_id: int, text: str) -> str:
        """Add a comment to a Ticket in Azure DevOps."""
        try:
            data = await self.client.post(
                f"wit/workitems/{ticket_id}/comments",
                data={"text": text},
                content_type="application/json",
                params={"api-version": COMMENT_API_VERSION},
            )
            comment = data.get("comment", data)
            return json.dumps(
                {
                    "success": True,
                    "ticket_id": ticket_id,
                    "comment_id": comment.get("id"),
                    "message": "Comment added successfully.",
                }
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    async def add_ticket_image(
        self, ticket_id: int, image_path: str, comment: str = ""
    ) -> str:
        """Attach an image file to a Ticket (Azure DevOps work item attachment).

        Args:
            ticket_id: ID of the work item
            image_path: local path to an image file (PNG, JPEG, GIF, WebP, ...)
            comment: optional label shown next to the attachment
        """
        if (
            not image_path
            or not os.path.isfile(image_path)
            or not is_image_file(image_path)
        ):
            return json.dumps(
                {"error": f"image_path is not a readable image file: {image_path!r}"}
            )

        file_name = os.path.basename(image_path)
        blob = await asyncio.to_thread(read_image_file, image_path)
        if blob is None:
            return json.dumps({"error": f"Could not read {image_path!r}."})

        try:
            attachment = await self.client.post_bytes(
                "wit/attachments",
                data=blob,
                content_type="application/octet-stream",
                params={"api-version": API_VERSION, "fileName": file_name},
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

        attributes = {"comment": comment} if comment else {}
        operations = [
            {
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "AttachedFile",
                    "url": attachment.get("url", ""),
                    **({"attributes": attributes} if attributes else {}),
                },
            }
        ]
        try:
            await self.client.patch(
                f"wit/workitems/{ticket_id}",
                data=operations,
                params={"api-version": API_VERSION},
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

        return json.dumps(
            {
                "success": True,
                "ticket_id": ticket_id,
                "file_name": file_name,
                "attachment_id": attachment.get("id"),
                "url": attachment.get("url"),
                "message": f"Image '{file_name}' attached to ticket #{ticket_id}.",
            },
            ensure_ascii=False,
        )

    async def add_mermaid(
        self,
        ticket_id: int,
        diagram: str,
        title: str = "",
        section: str = "Diagrams",
    ) -> str:
        """Render a Mermaid diagram to a PNG and embed it in a Ticket.

        The diagram is rendered locally (via the bundled ``mermaidx``
        engine) to a high-resolution PNG, attached to the work item through
        the ``wit/attachments`` endpoint plus an ``AttachedFile`` relation,
        and embedded at the bottom of the description with an ``<img>`` tag
        so it is visible directly in the ticket and stays readable even
        when zoomed into.

        Args:
            ticket_id: ID of the work item
            diagram: Mermaid diagram source (e.g. "flowchart TD\\n  A[Start] --> B[End]")
            title: Optional label shown as the image caption/attachment
                label (defaults to 'Mermaid diagram').
            section: Ignored on Azure (kept for protocol consistency with GitHub).
        """
        if not diagram or not diagram.strip():
            return json.dumps({"error": "diagram is required."})

        try:
            png_bytes = await asyncio.to_thread(_render_mermaid_png, diagram)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to render Mermaid diagram: {exc}"})

        file_name = f"mermaid-{ticket_id}.png"
        try:
            attachment = await self.client.post_bytes(
                "wit/attachments",
                data=png_bytes,
                content_type="application/octet-stream",
                params={"api-version": API_VERSION, "fileName": file_name},
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

        attachment_id = attachment.get("id")
        url = attachment.get("url", "") or (
            f"https://dev.azure.com/{AZDO_ORG}/{AZDO_PROJECT}/_apis/"
            f"wit/attachments/{attachment_id}"
        )

        label = title or "Mermaid diagram"
        try:
            await self.client.patch(
                f"wit/workitems/{ticket_id}",
                data=[
                    {
                        "op": "add",
                        "path": "/relations/-",
                        "value": {
                            "rel": "AttachedFile",
                            "url": url,
                            "attributes": {"comment": label},
                        },
                    }
                ],
                params={"api-version": API_VERSION},
            )

            item = await self.client.get(
                f"wit/workitems/{ticket_id}", params={"api-version": API_VERSION}
            )
            description = (
                item.get("fields", {}).get("System.Description", "") or ""
            ).rstrip()
            image_html = f'<p><img src="{url}" alt="{html.escape(label)}"></p>'
            new_description = (
                f"{description}\n{image_html}" if description else image_html
            )
            await self.client.patch(
                f"wit/workitems/{ticket_id}",
                data=[
                    {
                        "op": "add",
                        "path": "/fields/System.Description",
                        "value": new_description,
                    }
                ],
                params={"api-version": API_VERSION},
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

        return json.dumps(
            {
                "success": True,
                "ticket_id": ticket_id,
                "file_name": file_name,
                "attachment_id": attachment_id,
                "url": url,
                "renderer": "mermaidx",
                "message": f"Mermaid diagram embedded in ticket #{ticket_id}.",
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
        """Create a new Ticket in Azure DevOps.

        If ticket_type is not provided, returns a list of available types — call get_ticket_types() first to see valid options.

        Args:
            ticket_type: Ticket type (e.g. 'Task', 'Bug', 'User Story', 'Issue', 'Change Request'). Leave empty to retrieve available types.
            title: Ticket title
            description: HTML description of the ticket
            assigned_to: Display name of the assignee (email or displayName)
            priority: Priority (1-4)
            tags: Semicolon-separated tags
            area_path: Area path (defaults to project area)
            iteration_path: Iteration path (defaults to project iteration)
        """
        if not ticket_type:
            try:
                types_data = await self.client.get(
                    "wit/workitemtypes", params={"api-version": API_VERSION}
                )
                types = types_data.get("workItemTypes", types_data.get("value", []))
                names = [t.get("name") for t in types if t.get("name")]
                return json.dumps(
                    {
                        "error": "ticket_type is required but was not provided.",
                        "available_types": names,
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
            return json.dumps({"error": "title is required."})

        operations = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
        ]

        if description:
            operations.append(
                {
                    "op": "add",
                    "path": "/fields/System.Description",
                    "value": description,
                }
            )
        if assigned_to:
            operations.append(
                {"op": "add", "path": "/fields/System.AssignedTo", "value": assigned_to}
            )
        if priority > 0:
            operations.append(
                {
                    "op": "add",
                    "path": "/fields/Microsoft.VSTS.Common.Priority",
                    "value": priority,
                }
            )
        if tags:
            operations.append(
                {"op": "add", "path": "/fields/System.Tags", "value": tags}
            )
        if area_path:
            operations.append(
                {"op": "add", "path": "/fields/System.AreaPath", "value": area_path}
            )
        if iteration_path:
            operations.append(
                {
                    "op": "add",
                    "path": "/fields/System.IterationPath",
                    "value": iteration_path,
                }
            )

        try:
            data = await self.client.post(
                f"wit/workitems/${ticket_type}",
                data=operations,
                content_type="application/json-patch+json",
                params={"api-version": API_VERSION},
            )
            ticket_id = data.get("id")
            return json.dumps(
                {
                    "success": True,
                    "id": ticket_id,
                    "title": title,
                    "type": ticket_type,
                    "url": f"https://dev.azure.com/{AZDO_ORG}/{AZDO_PROJECT}/_workitems/edit/{ticket_id}",
                    "message": f"Ticket #{ticket_id} created successfully.",
                }
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

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
        """Update an existing Ticket in Azure DevOps. Only provided fields will be modified.

        Args:
            ticket_id: ID of the ticket to update
            title: New title
            description: New description (HTML)
            assigned_to: New assignee (leave empty to remove)
            state: New state (e.g. 'Active', 'Closed', 'Resolved')
            priority: New priority (1-4)
            tags: New semicolon-separated tags
            area_path: New area path
            iteration_path: New iteration path
        """
        operations = []

        if title:
            operations.append(
                {"op": "add", "path": "/fields/System.Title", "value": title}
            )
        if description:
            operations.append(
                {
                    "op": "add",
                    "path": "/fields/System.Description",
                    "value": description,
                }
            )
        if assigned_to:
            operations.append(
                {
                    "op": "add",
                    "path": "/fields/System.AssignedTo",
                    "value": assigned_to,
                }
            )
        if state:
            operations.append(
                {"op": "add", "path": "/fields/System.State", "value": state}
            )
        if priority > 0:
            operations.append(
                {
                    "op": "add",
                    "path": "/fields/Microsoft.VSTS.Common.Priority",
                    "value": priority,
                }
            )
        if tags:
            operations.append(
                {"op": "add", "path": "/fields/System.Tags", "value": tags}
            )
        if area_path:
            operations.append(
                {"op": "add", "path": "/fields/System.AreaPath", "value": area_path}
            )
        if iteration_path:
            operations.append(
                {
                    "op": "add",
                    "path": "/fields/System.IterationPath",
                    "value": iteration_path,
                }
            )

        if not operations:
            return json.dumps({"error": "No fields to update provided."})

        try:
            data = await self.client.patch(
                f"wit/workitems/{ticket_id}",
                data=operations,
                params={"api-version": API_VERSION},
            )
            return json.dumps(
                {
                    "success": True,
                    "id": data.get("id"),
                    "rev": data.get("rev"),
                    "message": f"Ticket #{ticket_id} updated successfully.",
                    "updated_fields": [op["path"].split("/")[-1] for op in operations],
                }
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

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
        """Search tickets in Azure DevOps using a WIQL query.

        Can use a custom WIQL query OR built-in filters.
        If 'query' is provided, filters are ignored.

        Args:
            query: Custom WIQL query (e.g. "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'")
            ticket_type: Ticket type filter (e.g. 'Task', 'Bug')
            state: Ticket state (e.g. 'Active', 'New', 'Closed')
            assigned_to: Assignee display name
            tags: Semicolon-separated tags to filter by
            area_path: Area path filter
            iteration_path: Iteration path filter
            max_results: Maximum results (default 20, max 200)
        """
        max_results = min(max(1, max_results), 200)

        if query:
            wiql_query = {"query": query}
        else:
            conditions = []
            if ticket_type:
                conditions.append(f"[System.WorkItemType] = '{ticket_type}'")
            if state:
                conditions.append(f"[System.State] = '{state}'")
            if assigned_to:
                conditions.append(f"[System.AssignedTo] = '{assigned_to}'")
            if tags:
                for tag in tags.split(";"):
                    tag = tag.strip()
                    if tag:
                        conditions.append(f"[System.Tags] Contains '{tag}'")
            if area_path:
                conditions.append(f"[System.AreaPath] = '{area_path}'")
            if iteration_path:
                conditions.append(f"[System.IterationPath] = '{iteration_path}'")

            where_clause = " AND ".join(conditions) if conditions else "1 = 1"
            wiql_query = {
                "query": f"SELECT [System.Id] FROM WorkItems WHERE {where_clause}"
            }

        try:
            wiql_result = await self.client.post(
                "wit/wiql", data=wiql_query, params={"api-version": API_VERSION}
            )

            work_items = wiql_result.get("workItems", [])
            if not work_items:
                return json.dumps(
                    {"results": [], "total": 0, "message": "No results found."}
                )

            item_ids = [str(item["id"]) for item in work_items[:max_results]]

            ids_param = ",".join(item_ids)
            batch_result = await self.client.get(
                "wit/workitems",
                params={
                    "ids": ids_param,
                    "api-version": API_VERSION,
                    "$expand": "relations",
                },
            )

            items = batch_result.get("value", [])
            formatted = [format_ticket(item) for item in items]

            return json.dumps(
                {
                    "results": [json.loads(f) for f in formatted],
                    "total": len(items),
                    "truncated": len(work_items) > max_results,
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    async def get_tickets_needing_my_reply(self, my_display_name: str) -> str:
        """Find active tickets assigned to you where the last comment was not written by you (i.e. need your reply)."""
        try:
            wiql_result = await self.client.post(
                "wit/wiql",
                data={
                    "query": f"SELECT [System.Id] FROM WorkItems WHERE [System.State] <> 'Closed' AND [System.State] <> 'Completed' AND [System.AssignedTo] = '{my_display_name}'"
                },
                params={"api-version": API_VERSION},
            )
            work_items = wiql_result.get("workItems", [])
            if not work_items:
                return json.dumps(
                    {
                        "pending": [],
                        "message": "No active tickets assigned to you found.",
                    }
                )

            item_ids = [str(item["id"]) for item in work_items[:20]]
            pending = []

            for tid in item_ids:
                try:
                    item_data = await self.client.get(
                        f"wit/workitems/{tid}", params={"api-version": API_VERSION}
                    )
                    title = item_data.get("fields", {}).get("System.Title", "Untitled")

                    comments_data = await self.client.get(
                        f"wit/workitems/{tid}/comments",
                        params={"api-version": COMMENT_API_VERSION},
                    )
                    comments = comments_data.get("comments", [])
                    if comments:
                        last = comments[-1]
                        author = last.get("createdBy", {}).get("displayName", "")
                        if author.lower() != my_display_name.lower():
                            pending.append(
                                {
                                    "ticket_id": int(tid),
                                    "title": title,
                                    "last_comment_author": author,
                                    "last_comment_date": last.get("createdDate", ""),
                                    "last_comment_preview": last.get("text", "")[:200],
                                }
                            )
                except httpx.HTTPStatusError as e:
                    logger.warning("Error retrieving ticket %s: %s", tid, e)
                    continue

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
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    # ──────────────────────────────────────────────
    #  LINKS
    # ──────────────────────────────────────────────

    async def link_tickets(
        self,
        source_id: int,
        target_id: int,
        link_type: str = "System.LinkTypes.Hierarchy-Reverse",
        comment: str = "",
    ) -> str:
        """Link two Tickets in Azure DevOps.

        Args:
            source_id: Source ticket ID (the child)
            target_id: Target ticket ID (the parent)
            link_type: Link type:
                - 'System.LinkTypes.Hierarchy-Reverse' = Parent (source -> target as child of target)
                - 'System.LinkTypes.Hierarchy-Forward' = Child (source -> target as parent of target)
                - 'System.LinkTypes.Related' = Related
            comment: Optional comment on the link
        """
        operations = [
            {
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": link_type,
                    "url": f"https://dev.azure.com/{AZDO_ORG}/{AZDO_PROJECT}/_apis/wit/workItems/{target_id}",
                    **({"comment": comment} if comment else {}),
                },
            }
        ]

        try:
            await self.client.patch(
                f"wit/workitems/{source_id}",
                data=operations,
                params={"api-version": API_VERSION},
            )
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
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    async def get_ticket_relations(self, ticket_id: int) -> str:
        """Retrieve all links/relations of a Ticket."""
        try:
            data = await self.client.get(
                f"wit/workitems/{ticket_id}",
                params={"api-version": API_VERSION, "$expand": "relations"},
            )
            relations = data.get("relations", [])
            result = []
            for rel in relations:
                url = rel.get("url", "")
                target_id = url.split("/")[-1] if url else "unknown"
                result.append(
                    {
                        "type": rel.get("rel", ""),
                        "target_id": target_id,
                        "comment": rel.get("comment", ""),
                    }
                )
            return json.dumps(
                {"ticket_id": ticket_id, "relations": result, "total": len(result)},
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    # ──────────────────────────────────────────────
    #  ITERATIONS / BOARDS
    # ──────────────────────────────────────────────

    async def get_iterations(self) -> str:
        """Retrieve all iterations (sprints) from the Azure DevOps project."""
        try:
            data = await self.client.get(
                "work/teamsettings/iterations", params={"api-version": API_VERSION}
            )
            iterations = data.get("value", [])
            result = []
            for it in iterations:
                result.append(
                    {
                        "id": it.get("id"),
                        "name": it.get("name", ""),
                        "path": it.get("path", ""),
                        "start_date": it.get("attributes", {}).get("startDate", ""),
                        "finish_date": it.get("attributes", {}).get("finishDate", ""),
                        "state": it.get("attributes", {}).get("timeFrame", ""),
                    }
                )
            return json.dumps(
                {"iterations": result, "total": len(result)}, ensure_ascii=False
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    async def move_to_iteration(self, ticket_id: int, iteration_path: str) -> str:
        """Move a Ticket to a different iteration (sprint).

        Args:
            ticket_id: ID of the ticket to move
            iteration_path: Full iteration path (e.g. 'Project\\Sprint 1')
        """
        try:
            data = await self.client.patch(
                f"wit/workitems/{ticket_id}",
                data=[
                    {
                        "op": "add",
                        "path": "/fields/System.IterationPath",
                        "value": iteration_path,
                    }
                ],
                params={"api-version": API_VERSION},
            )
            return json.dumps(
                {
                    "success": True,
                    "id": data.get("id"),
                    "iteration_path": iteration_path,
                    "message": f"Ticket #{ticket_id} moved to '{iteration_path}'.",
                }
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    async def get_area_paths(self) -> str:
        """Retrieve all area paths from the Azure DevOps project."""
        try:
            data = await self.client.get(
                "wit/classificationNodes/Areas",
                params={"api-version": API_VERSION, "$expand": "children"},
            )
            return json.dumps(data, indent=2, ensure_ascii=False)
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )
