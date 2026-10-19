"""MCP server entrypoint for the ticketing backends.

Registers the 15 MCP tools against a single TicketManager whose strategy
is chosen from the command line ('azure' or 'github'). The server
is launched with ``uv run mcp-ticketing <backend>`` or
``uv run python -m mcp_ticketing.main <backend>``.

Credentials can be passed either through the environment variables or
directly on the command line, which bypasses the environment::

    uv run mcp-ticketing github --check --owner saxix --repo myrepo --token <token>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_ticketing.ticket_manager import TicketManager

# The connectors read their configuration from the environment at import
# time, so ``TicketManager`` is imported lazily (see ``_resolve``) after any
# ``--owner/--repo/--token`` values have been pushed into the environment.
# The ``mcp`` server is created eagerly because every tool is registered
# against it with the ``@mcp.tool()`` decorator.
mcp = MCPServer("mcp-ticketing")

# Reassigned by ``_resolve`` once the CLI arguments have been processed.
manager: TicketManager | None = None

# CLI flag -> environment variable mapping per backend. Flags that do not
# map cleanly onto a backend's credentials are simply ignored.
CLI_ENV_MAP: dict[str, dict[str, str]] = {
    "azure": {
        "owner": "MCP_AZURE_DEVOPS_ORG",
        "repo": "MCP_AZURE_DEVOPS_PROJECT",
        "token": "MCP_AZDO_PAT",
    },
    "github": {
        "owner": "MCP_GITHUB_OWNER",
        "repo": "MCP_GITHUB_REPO",
        "token": "MCP_GITHUB_TOKEN",
    },
    "redmine": {
        "owner": "MCP_REDMINE_URL",
        "repo": "MCP_REDMINE_PROJECT",
        "token": "MCP_REDMINE_API_KEY",
    },
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse ``argv`` (excluding the program name) into CLI options."""
    # Backwards compatible alias: the legacy ``-check`` flag becomes ``--check``.
    argv = ["--check" if a == "-check" else a for a in argv]

    parser = argparse.ArgumentParser(
        prog="mcp-ticketing",
        description="MCP server for Azure DevOps, GitHub Issues and Redmine ticketing.",
    )
    parser.add_argument(
        "backend",
        nargs="?",
        default="azure",
        choices=sorted(CLI_ENV_MAP),
        help="Ticket backend to use (default: azure).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the connection and credentials, then exit without starting the server.",
    )
    parser.add_argument("--owner", help="Owner/org override (instance base URL for Redmine).")
    parser.add_argument("--repo", help="Project/repo override (project identifier for Redmine).")
    parser.add_argument("--token", help="Token override (API key for Redmine).")
    return parser.parse_args(argv)


def _apply_cli_env(args: argparse.Namespace) -> None:
    """Push ``--owner/--repo/--token`` values into the environment for the selected backend.

    Command-line values take precedence over any pre-existing environment
    variable with the same name.
    """
    backend_map = CLI_ENV_MAP.get(args.backend, {})
    for flag, value in (
        ("owner", args.owner),
        ("repo", args.repo),
        ("token", args.token),
    ):
        if value is not None and flag in backend_map:
            os.environ[backend_map[flag]] = value


async def _warmup_redmine_profile() -> None:
    """Select the Redmine version profile at startup (fail-fast).

    For the Redmine backend this probes the server once (reading
    ``X-Redmine-Version``) or validates the declared ``MCP_REDMINE_VERSION``,
    so an unsupported/mismatched version is reported *before* the MCP server
    starts, not on the first tool call.
    """
    from mcp_ticketing.redmine.connector import RedmineConnector

    connector = RedmineConnector()
    await connector._ensure_profile()


def _resolve(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse argv, apply the CLI environment overrides, and build the TicketManager."""
    global manager
    if argv is None:
        argv = sys.argv[1:]
    args = _parse_args(list(argv))
    _apply_cli_env(args)

    # Load .env from the composition root so the CLI overrides above take
    # precedence (already pushed into the environment). The Redmine connector
    # reads its configuration at construction time, not at import time.
    load_dotenv()

    from mcp_ticketing.ticket_manager import TicketManager

    manager = TicketManager(strategy=args.backend)

    if args.backend == "redmine":
        try:
            asyncio.run(_warmup_redmine_profile())
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": str(exc)}))
            sys.exit(1)

    return args


# ──────────────────────────────────────────────
#  TICKET TYPES
# ──────────────────────────────────────────────


@mcp.tool()
async def get_ticket_types() -> str:
    """Retrieve all valid ticket types configured for the project.

    E.g. Task, Bug, User Story, Issue, Change Request.
    """
    assert manager is not None
    return await manager.get_ticket_types()


# ──────────────────────────────────────────────
#  TICKET CRUD
# ──────────────────────────────────────────────


@mcp.tool()
async def get_ticket(ticket_id: int) -> str:
    """Retrieve full details of a ticket by its ID."""
    assert manager is not None
    return await manager.get_ticket(ticket_id)


@mcp.tool()
async def get_ticket_comments(ticket_id: int) -> str:
    """Retrieve all comments associated with a ticket."""
    assert manager is not None
    return await manager.get_ticket_comments(ticket_id)


@mcp.tool()
async def add_comment(ticket_id: int, text: str) -> str:
    """Add a comment to a ticket."""
    assert manager is not None
    return await manager.add_comment(ticket_id, text)


@mcp.tool()
async def add_mermaid(
    ticket_id: int,
    diagram: str,
    title: str = "",
    section: str = "Diagrams",
) -> str:
    """Append a Mermaid diagram to a ticket.

    GitHub: the Mermaid source is appended at the bottom of the issue body
    under the given ``section`` heading (default 'Diagrams').\n
    Azure: the diagram is rendered locally to a high-resolution PNG
    (via the bundled ``mermaidx`` engine) and attached to the work item
    as an AttachedFile relation. ``section`` is ignored on Azure.\n
    Redmine: the diagram is rendered locally to a high-resolution PNG
    (same engine as Azure), attached to the issue, and embedded in the
    description with an ``<img>`` tag. ``section`` is ignored on Redmine.

    Args:
        ticket_id: ID of the ticket (work item ID or issue number)
        diagram: Mermaid diagram source (e.g. "flowchart TD\\n  A[Start] --> B[End]")
        title: Optional label. GitHub: heading above the diagram;
            Azure: attachment label; Redmine: image caption.
        section: Section heading used by GitHub (default 'Diagrams')
    """
    assert manager is not None
    return await manager.add_mermaid(ticket_id, diagram, title, section)


@mcp.tool()
async def add_ticket_image(ticket_id: int, image_path: str, comment: str = "") -> str:
    """Attach a local image file to a ticket.

    Args:
        ticket_id: ID of the ticket (work item ID or issue number)
        image_path: Local path to an image file (PNG, JPEG, GIF, WebP, ...)
        comment: Optional comment shown next to the image.
            Azure: attachment label.
            GitHub: text above the image in a new issue comment
            (images are uploaded into the repo and referenced via
            Markdown).
            Redmine: note appended to the issue together with the attachment.
    """
    assert manager is not None
    return await manager.add_ticket_image(ticket_id, image_path, comment)


@mcp.tool()
async def create_ticket(
    ticket_type: str = "",
    title: str = "",
    description: str = "",
    assigned_to: str = "",
    priority: int = 0,
    tags: str = "",
    area_path: str = "",
    iteration_path: str = "",
) -> str:
    """Create a new ticket.

    If ticket_type is not provided, returns a list of available
    types — call get_ticket_types() first to see valid options.

    Args:
        ticket_type: Ticket type (e.g. 'Task', 'Bug', 'User Story',
            'Issue', 'Change Request').
            Leave empty to retrieve available types.
        title: Ticket title
        description: HTML description (Azure/Redmine) or Markdown body (GitHub) of the ticket
        assigned_to: Assignee display name (Azure), username (GitHub)
            or login/full name (Redmine)
        priority: Priority (1-4)
        tags: Semicolon-separated tags (Azure) or labels (GitHub).
            Ignored for Redmine.
        area_path: Area path (Azure; defaults to project area).
            Ignored for GitHub and Redmine.
        iteration_path: Iteration path (Azure) or milestone title (GitHub)
            or version name (Redmine)
    """
    assert manager is not None
    return await manager.create_ticket(
        ticket_type=ticket_type,
        title=title,
        description=description,
        assigned_to=assigned_to,
        priority=priority,
        tags=tags,
        area_path=area_path,
        iteration_path=iteration_path,
    )


@mcp.tool()
async def update_ticket(
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
    """Update an existing ticket. Only provided fields will be modified.

    Args:
        ticket_id: ID of the ticket to update
        title: New title
        description: New description (HTML for Azure/Redmine, Markdown for GitHub)
        assigned_to: New assignee (leave empty to remove)
        state: New state (Azure: 'Active', 'Closed', 'Resolved'; GitHub: 'open', 'closed';
            Redmine: status name or generic word open/active/resolved/closed/done)
        priority: New priority (1-4)
        tags: New semicolon-separated tags (Azure) or labels (GitHub).
            Ignored for Redmine.
        area_path: New area path (Azure). Ignored for GitHub and Redmine.
        iteration_path: New iteration path (Azure), milestone title (GitHub)
            or version name (Redmine)
    """
    assert manager is not None
    return await manager.update_ticket(
        ticket_id=ticket_id,
        title=title,
        description=description,
        assigned_to=assigned_to,
        state=state,
        priority=priority,
        tags=tags,
        area_path=area_path,
        iteration_path=iteration_path,
    )


# ──────────────────────────────────────────────
#  SEARCH / QUERY
# ──────────────────────────────────────────────


@mcp.tool()
async def search_tickets(
    query: str = "",
    ticket_type: str = "",
    state: str = "",
    assigned_to: str = "",
    tags: str = "",
    area_path: str = "",
    iteration_path: str = "",
    max_results: int = 20,
) -> str:
    """Search tickets using a custom query OR built-in filters.

    If 'query' is provided, filters are ignored (Azure) or appended (GitHub).

    Args:
        query: Custom WIQL query (Azure), search query (GitHub, e.g. "is:open bug")
            or free-text on subject/description (Redmine)
        ticket_type: Ticket type filter (ignored for GitHub)
        state: Ticket state (Azure: 'Active', 'New', 'Closed'; GitHub: 'open', 'closed';
            Redmine: status name or generic word)
        assigned_to: Assignee display name (Azure), username (GitHub)
            or login/full name (Redmine)
        tags: Semicolon-separated tags (Azure) or labels (GitHub) to filter by.
            Ignored for Redmine.
        area_path: Area path filter (Azure). Ignored for GitHub and Redmine.
        iteration_path: Iteration path filter (Azure), milestone title (GitHub)
            or version name (Redmine)
        max_results: Maximum results (Azure default 20 / max 200,
            GitHub and Redmine default 20 / max 100)
    """
    assert manager is not None
    return await manager.search_tickets(
        query=query,
        ticket_type=ticket_type,
        state=state,
        assigned_to=assigned_to,
        tags=tags,
        area_path=area_path,
        iteration_path=iteration_path,
        max_results=max_results,
    )


@mcp.tool()
async def get_tickets_needing_my_reply(my_display_name: str) -> str:
    """Find active tickets assigned to you where the last comment was not written by you (i.e. need your reply)."""
    assert manager is not None
    return await manager.get_tickets_needing_my_reply(my_display_name)


# ──────────────────────────────────────────────
#  LINKS
# ──────────────────────────────────────────────


@mcp.tool()
async def link_tickets(
    source_id: int,
    target_id: int,
    link_type: str = "System.LinkTypes.Hierarchy-Reverse",
    comment: str = "",
) -> str:
    """Link two tickets.

    Args:
        source_id: Source ticket ID
        target_id: Target ticket ID
        link_type: Link type for Azure:
            - 'System.LinkTypes.Hierarchy-Reverse' = Parent (source -> target as child of target)
            - 'System.LinkTypes.Hierarchy-Forward' = Child (source -> target as parent of target)
            - 'System.LinkTypes.Related' = Related
            Ignored for GitHub (always adds a cross-reference).
            Redmine: one of 'relates to' (default), 'blocks', 'is blocked by',
            'duplicates', 'is duplicated by', 'copied from', 'copied to'.
        comment: Optional comment on the link (Redmine: appended as a note)
    """
    assert manager is not None
    return await manager.link_tickets(
        source_id=source_id,
        target_id=target_id,
        link_type=link_type,
        comment=comment,
    )


@mcp.tool()
async def get_ticket_relations(ticket_id: int) -> str:
    """Retrieve all links/relations (Azure, Redmine) or cross-references and linked PRs (GitHub) of a ticket."""
    assert manager is not None
    return await manager.get_ticket_relations(ticket_id)


# ──────────────────────────────────────────────
#  ITERATIONS / BOARDS
# ──────────────────────────────────────────────


@mcp.tool()
async def get_iterations() -> str:
    """Retrieve all iterations/sprints (Azure) or milestones (GitHub) or versions (Redmine) from the project."""
    assert manager is not None
    return await manager.get_iterations()


@mcp.tool()
async def move_to_iteration(ticket_id: int, iteration_path: str) -> str:
    """Move a ticket to a different iteration/sprint (Azure), milestone (GitHub) or version (Redmine).

    Args:
        ticket_id: ID of the ticket to move
        iteration_path: Full iteration path (e.g. 'Project\\Sprint 1'), milestone title
            or version name (Redmine)
    """
    assert manager is not None
    return await manager.move_to_iteration(ticket_id, iteration_path)


@mcp.tool()
async def get_area_paths() -> str:
    """Retrieve all area paths (Azure), labels (GitHub) or issue trackers (Redmine) from the project."""
    assert manager is not None
    return await manager.get_area_paths()


# ──────────────────────────────────────────────
#  ENTRYPOINT
# ──────────────────────────────────────────────


def main() -> None:
    """Run the MCP server, or verify the backend connection with ``--check``."""
    args = _resolve()
    assert manager is not None

    if args.check:
        try:
            result = asyncio.run(manager.check())
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            print(json.dumps({"error": str(exc)}))
            sys.exit(1)
        print(result)
        if "error" in json.loads(result):
            sys.exit(1)
        return

    mcp.run()


if __name__ == "__main__":
    main()
