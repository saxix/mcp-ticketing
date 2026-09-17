"""GitHub connector implementing TicketProtocol."""

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
logger = logging.getLogger("mcp-ticketing-github")

GITHUB_OWNER = os.getenv("MCP_GITHUB_OWNER", "")
GITHUB_REPO = os.getenv("MCP_GITHUB_REPO", "")
GITHUB_TOKEN = os.getenv("MCP_GITHUB_TOKEN", "")


class GitHubClient:
    def __init__(self):
        self.owner = GITHUB_OWNER
        self.repo = GITHUB_REPO
        self.token = GITHUB_TOKEN
        self.base_url = f"https://api.github.com/repos/{self.owner}/{self.repo}"

    @asynccontextmanager
    async def get_client(self):
        async with httpx.AsyncClient(timeout=30.0) as client:
            yield client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{path}"
        async with self.get_client() as client:
            response = await client.get(url, headers=self._headers(), params=params)
            response.raise_for_status()
            return response.json()

    async def post(
        self,
        path: str,
        data: Any,
        params: dict | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path}"
        async with self.get_client() as client:
            response = await client.post(
                url, headers=self._headers(), json=data, params=params
            )
            response.raise_for_status()
            return response.json()

    async def patch(
        self,
        path: str,
        data: Any,
        params: dict | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path}"
        async with self.get_client() as client:
            response = await client.patch(
                url, headers=self._headers(), json=data, params=params
            )
            response.raise_for_status()
            return response.json()

    async def search(self, query: str, params: dict | None = None) -> dict[str, Any]:
        url = "https://api.github.com/search/issues"
        search_params = {
            "q": f"{query} repo:{self.owner}/{self.repo}",
            **(params or {}),
        }
        async with self.get_client() as client:
            response = await client.get(
                url, headers=self._headers(), params=search_params
            )
            response.raise_for_status()
            return response.json()


def format_ticket(issue: dict) -> str:
    labels = issue.get("labels", [])
    label_names = [l.get("name", "") for l in labels if isinstance(l, dict)]
    assignee = issue.get("assignee", {})
    assigned_to = assignee.get("login", "Unassigned") if assignee else "Unassigned"
    milestone = issue.get("milestone")
    iteration_path = milestone.get("title", "") if milestone else ""
    priority_label = next(
        (l for l in label_names if l.lower().startswith("priority:")), ""
    )
    return json.dumps(
        {
            "id": issue.get("number"),
            "title": issue.get("title", ""),
            "type": "Issue",
            "state": issue.get("state", ""),
            "assigned_to": assigned_to,
            "tags": "; ".join(label_names),
            "area_path": "",
            "iteration_path": iteration_path,
            "priority": priority_label,
            "created_date": issue.get("created_at", ""),
            "changed_date": issue.get("updated_at", ""),
        },
        indent=2,
        ensure_ascii=False,
    )


def append_mermaid_markdown(
    body: str, diagram: str, title: str = "", section: str = "Diagrams"
) -> str:
    """Append a Mermaid diagram at the bottom of a Markdown ticket body.

    Diagrams are collected under a ``## <section>`` heading (default
    'Diagrams') at the end of the description. If the section already
    exists, only the new diagram block is appended.
    """
    body = (body or "").rstrip()
    paragraphs: list[str] = []
    if body:
        paragraphs.append(body)
    if not re.search(rf"^##\s+{re.escape(section)}\s*$", body, re.MULTILINE):
        paragraphs.append(f"## {section}")
    if title:
        paragraphs.append(f"### {title}")
    paragraphs.append(f"```mermaid\n{diagram}\n```")
    return "\n\n".join(paragraphs) + "\n"


class GithubConnector(TicketProtocol):
    """GitHub implementation of the TicketProtocol strategy."""

    def __init__(self, client: GitHubClient | None = None):
        self.client = client or GitHubClient()

    # ──────────────────────────────────────────────
    #  TICKET TYPES
    # ──────────────────────────────────────────────

    async def get_ticket_types(self) -> str:
        """Retrieve all valid ticket types supported by GitHub (Issues)."""
        return json.dumps(
            {
                "ticket_types": [
                    {"name": "Issue", "description": "GitHub Issue"},
                ],
                "total": 1,
            },
            ensure_ascii=False,
        )

    async def check(self) -> str:
        """Verify the connection to GitHub (reads the repository labels)."""
        try:
            await self.client.get("labels", params={"per_page": "1"})
            return json.dumps(
                {
                    "status": "ok",
                    "backend": "github",
                    "owner": self.client.owner,
                    "repo": self.client.repo,
                    "message": f"Connection to GitHub {self.client.owner}/{self.client.repo} OK.",
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
        """Retrieve full details of a Ticket (GitHub Issue) by number."""
        try:
            data = await self.client.get(f"issues/{ticket_id}")
            return format_ticket(data)
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    async def get_ticket_comments(self, ticket_id: int) -> str:
        """Retrieve all comments associated with a Ticket (GitHub Issue)."""
        try:
            data = await self.client.get(f"issues/{ticket_id}/comments")
            if not data:
                return json.dumps(
                    {
                        "ticket_id": ticket_id,
                        "comments": [],
                        "message": "No comments found.",
                    }
                )

            result = []
            for c in data:
                result.append(
                    {
                        "id": c.get("id"),
                        "author": c.get("user", {}).get("login", "Unknown"),
                        "created_date": c.get("created_at", ""),
                        "text": c.get("body", ""),
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
        """Add a comment to a Ticket (GitHub Issue)."""
        try:
            data = await self.client.post(
                f"issues/{ticket_id}/comments",
                data={"body": text},
            )
            return json.dumps(
                {
                    "success": True,
                    "ticket_id": ticket_id,
                    "comment_id": data.get("id"),
                    "message": "Comment added successfully.",
                }
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    async def add_mermaid(
        self,
        ticket_id: int,
        diagram: str,
        title: str = "",
        section: str = "Diagrams",
    ) -> str:
        """Append a Mermaid diagram at the bottom of a Ticket (GitHub Issue body).

        The Mermaid source is stored in a fenced ``mermaid`` block under a
        ``## <section>`` heading (default 'Diagrams') at the end of the
        issue body, so it stays editable and renderable by mermaid-enabled
        viewers (github.com renders it automatically).

        Args:
            ticket_id: Issue number to update
            diagram: Mermaid diagram source (e.g. "flowchart TD\\n  A[Start] --> B[End]")
            title: Optional heading shown above the diagram
            section: Section heading to append to (default 'Diagrams')
        """
        if not diagram or not diagram.strip():
            return json.dumps({"error": "diagram is required."})

        try:
            issue = await self.client.get(f"issues/{ticket_id}")
            body = issue.get("body", "") or ""
            new_body = append_mermaid_markdown(body, diagram, title, section)
            await self.client.patch(f"issues/{ticket_id}", data={"body": new_body})
            return json.dumps(
                {
                    "success": True,
                    "ticket_id": ticket_id,
                    "section": section,
                    "position": "bottom",
                    "message": f"Mermaid diagram appended to ticket #{ticket_id} in section '{section}'.",
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
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
        """Create a new Ticket (GitHub Issue).

        Args:
            ticket_type: Ignored for GitHub (always creates an Issue).
            title: Issue title
            description: Issue body (Markdown)
            assigned_to: GitHub username to assign
            priority: Priority level (1-4), mapped to 'priority: 1'-'priority: 4' label
            tags: Semicolon-separated labels to apply
            area_path: Ignored for GitHub.
            iteration_path: Milestone title to assign
        """
        if not title:
            return json.dumps({"error": "title is required."})

        labels = []
        if tags:
            labels = [t.strip() for t in tags.split(";") if t.strip()]
        if priority > 0:
            labels.append(f"priority: {priority}")

        milestone_number = None
        if iteration_path:
            try:
                milestones = await self.client.get(
                    "milestones", params={"state": "open"}
                )
                for m in milestones:
                    if m.get("title") == iteration_path:
                        milestone_number = m.get("number")
                        break
            except httpx.HTTPStatusError:
                pass

        payload: dict[str, Any] = {
            "title": title,
            "body": description or "",
        }
        if labels:
            payload["labels"] = labels
        if assigned_to:
            payload["assignees"] = [assigned_to]
        if milestone_number:
            payload["milestone"] = milestone_number

        try:
            data = await self.client.post("issues", data=payload)
            return json.dumps(
                {
                    "success": True,
                    "id": data.get("number"),
                    "title": title,
                    "type": "Issue",
                    "url": data.get("html_url"),
                    "message": f"Ticket #{data.get('number')} created successfully.",
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
        """Update an existing Ticket (GitHub Issue). Only provided fields will be modified.

        Args:
            ticket_id: Issue number to update
            title: New title
            description: New body (Markdown)
            assigned_to: New assignee (GitHub username)
            state: New state ('open' or 'closed')
            priority: New priority (1-4)
            tags: New semicolon-separated labels (replaces all labels)
            area_path: Ignored for GitHub.
            iteration_path: Milestone title (empty string to remove)
        """
        payload: dict[str, Any] = {}

        if title:
            payload["title"] = title
        if description:
            payload["body"] = description
        if assigned_to:
            payload["assignees"] = [assigned_to]
        if state:
            state_map = {
                "active": "open",
                "closed": "closed",
                "resolved": "closed",
                "new": "open",
            }
            payload["state"] = state_map.get(state.lower(), state.lower())
        if priority > 0 or tags:
            labels = []
            if tags:
                labels = [t.strip() for t in tags.split(";") if t.strip()]
            if priority > 0:
                labels.append(f"priority: {priority}")
            if labels:
                payload["labels"] = labels
        if iteration_path:
            try:
                milestones = await self.client.get(
                    "milestones", params={"state": "open"}
                )
                for m in milestones:
                    if m.get("title") == iteration_path:
                        payload["milestone"] = m.get("number")
                        break
            except httpx.HTTPStatusError:
                pass

        if not payload:
            return json.dumps({"error": "No fields to update provided."})

        try:
            data = await self.client.patch(f"issues/{ticket_id}", data=payload)
            return json.dumps(
                {
                    "success": True,
                    "id": data.get("number"),
                    "message": f"Ticket #{ticket_id} updated successfully.",
                    "updated_fields": list(payload.keys()),
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
        """Search tickets in GitHub using the search API.

        Can use a custom search query OR built-in filters.
        If 'query' is provided, additional filters are appended to the search.

        Args:
            query: Custom search query (e.g. "is:open bug")
            ticket_type: Ignored for GitHub (always Issue).
            state: Ticket state ('open' or 'closed')
            assigned_to: GitHub username assigned
            tags: Semicolon-separated labels to filter by
            area_path: Ignored for GitHub.
            iteration_path: Milestone title to filter by
            max_results: Maximum results (default 20, max 100)
        """
        max_results = min(max(1, max_results), 100)

        search_parts = []
        if query:
            search_parts.append(query)
        if state:
            search_parts.append(f"is:{state}")
        elif not query:
            search_parts.append("is:open")
        if assigned_to:
            search_parts.append(f"assignee:{assigned_to}")
        if tags:
            for tag in tags.split(";"):
                tag = tag.strip()
                if tag:
                    search_parts.append(f"label:{tag}")
        if iteration_path:
            search_parts.append(f"milestone:{iteration_path}")

        search_query = " ".join(search_parts)

        try:
            data = await self.client.search(
                search_query, params={"per_page": str(max_results)}
            )
            items = data.get("items", [])
            results = []
            for issue in items:
                results.append(json.loads(format_ticket(issue)))

            return json.dumps(
                {
                    "results": results,
                    "total": data.get("total_count", len(results)),
                    "truncated": data.get("total_count", 0) > max_results,
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
            data = await self.client.get(
                "issues",
                params={
                    "state": "open",
                    "assignee": my_display_name,
                    "per_page": "20",
                    "sort": "updated",
                    "direction": "desc",
                },
            )
            if not data:
                return json.dumps(
                    {
                        "pending": [],
                        "message": "No active tickets assigned to you found.",
                    }
                )

            pending = []
            for issue in data:
                issue_number = issue.get("number")
                try:
                    comments = await self.client.get(f"issues/{issue_number}/comments")
                    if comments:
                        last = comments[-1]
                        author = last.get("user", {}).get("login", "")
                        if author.lower() != my_display_name.lower():
                            pending.append(
                                {
                                    "ticket_id": issue_number,
                                    "title": issue.get("title", "Untitled"),
                                    "last_comment_author": author,
                                    "last_comment_date": last.get("created_at", ""),
                                    "last_comment_preview": last.get("body", "")[:200],
                                }
                            )
                except httpx.HTTPStatusError as e:
                    logger.warning("Error retrieving issue %s: %s", issue_number, e)
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
        link_type: str = "related",
        comment: str = "",
    ) -> str:
        """Link two Tickets (GitHub Issues) by adding a cross-reference comment.

        Args:
            source_id: Source issue number
            target_id: Target issue number
            link_type: Ignored for GitHub (always adds a cross-reference).
            comment: Optional additional comment text
        """
        link_text = f"Linked ticket: #{target_id}"
        if comment:
            link_text = f"{comment}\n\nLinked ticket: #{target_id}"

        try:
            await self.client.post(
                f"issues/{source_id}/comments",
                data={"body": link_text},
            )
            return json.dumps(
                {
                    "success": True,
                    "source_id": source_id,
                    "target_id": target_id,
                    "link_type": "cross-reference",
                    "message": f"Link created: #{source_id} -> #{target_id} (cross-reference comment)",
                }
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    async def get_ticket_relations(self, ticket_id: int) -> str:
        """Retrieve all cross-references and linked PRs of a Ticket (GitHub Issue)."""
        try:
            issue_data = await self.client.get(f"issues/{ticket_id}")
            body = issue_data.get("body", "") or ""
            comments = await self.client.get(f"issues/{ticket_id}/comments")

            linked_issues = set()
            linked_prs = set()

            cross_ref_pattern = re.compile(
                r"(?:^|\s)#(\d+)(?:\s|$|[.,;:!?\)])", re.MULTILINE
            )

            for text in [body] + [c.get("body", "") or "" for c in comments]:
                for match in cross_ref_pattern.finditer(text):
                    linked_issues.add(int(match.group(1)))

            timeline_url = f"issues/{ticket_id}/timeline"
            try:
                timeline = await self.client.get(
                    timeline_url, params={"per_page": "100"}
                )
                for event in timeline:
                    if event.get("event") == "cross-referenced":
                        source = event.get("source", {}).get("issue", {})
                        if source.get("pull_request"):
                            linked_prs.add(source.get("number"))
                        elif source.get("number"):
                            linked_issues.add(source.get("number"))
            except httpx.HTTPStatusError:
                pass

            linked_issues.discard(ticket_id)

            result = []
            for pr_num in sorted(linked_prs):
                result.append(
                    {"type": "pull_request", "target_id": str(pr_num), "comment": ""}
                )
            for issue_num in sorted(linked_issues):
                result.append(
                    {"type": "related", "target_id": str(issue_num), "comment": ""}
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
    #  ITERATIONS / MILESTONES
    # ──────────────────────────────────────────────

    async def get_iterations(self) -> str:
        """Retrieve all milestones from the GitHub repository (mapped to iterations)."""
        try:
            data = await self.client.get(
                "milestones", params={"state": "all", "per_page": "100"}
            )
            result = []
            for m in data:
                result.append(
                    {
                        "id": m.get("number"),
                        "name": m.get("title", ""),
                        "path": m.get("title", ""),
                        "start_date": m.get("due_on", ""),
                        "finish_date": m.get("closed_at", ""),
                        "state": m.get("state", ""),
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
        """Move a Ticket (GitHub Issue) to a milestone.

        Args:
            ticket_id: Issue number to update
            iteration_path: Milestone title to assign
        """
        try:
            milestones = await self.client.get("milestones", params={"state": "open"})
            milestone_number = None
            for m in milestones:
                if m.get("title") == iteration_path:
                    milestone_number = m.get("number")
                    break

            if not milestone_number:
                return json.dumps(
                    {
                        "error": f"Milestone '{iteration_path}' not found.",
                        "available_iterations": [m.get("title") for m in milestones],
                    }
                )

            await self.client.patch(
                f"issues/{ticket_id}",
                data={"milestone": milestone_number},
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
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )

    async def get_area_paths(self) -> str:
        """Retrieve all labels from the GitHub repository (mapped to area paths)."""
        try:
            data = await self.client.get("labels", params={"per_page": "100"})
            result = []
            for label in data:
                result.append(
                    {
                        "name": label.get("name", ""),
                        "description": label.get("description", ""),
                        "color": label.get("color", ""),
                    }
                )
            return json.dumps(
                {"area_paths": result, "total": len(result)}, ensure_ascii=False
            )
        except httpx.HTTPStatusError as e:
            return json.dumps(
                {"error": f"HTTP {e.response.status_code}: {e.response.text}"}
            )
