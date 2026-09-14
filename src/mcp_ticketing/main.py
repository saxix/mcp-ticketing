"""MCP server entrypoint for the ticketing backends.

Registers the 13 MCP tools against a single TicketManager whose strategy
is chosen from the command line ('azure', 'github', 'taiga' or 'redmine'). The server
is launched with ``uv run mcp-ticketing <backend>`` or
``uv run python -m mcp_ticketing.main <backend>``.

Use ``-check`` to verify credentials and exit immediately::

    uv run mcp-ticketing github -check
"""

import asyncio
import json
import sys

from mcp.server.mcpserver import MCPServer

from mcp_ticketing.ticket_manager import TicketManager

_positional = [a for a in sys.argv[1:] if not a.startswith("-")]
backend = _positional[0] if _positional else "azure"
_check_mode = any(a in ("-check", "--check") for a in sys.argv[1:])

manager = TicketManager(strategy=backend)
mcp = MCPServer(f"mcp-ticketing ({backend})")


# ──────────────────────────────────────────────
#  TICKET TYPES
# ──────────────────────────────────────────────


@mcp.tool()
async def get_ticket_types() -> str:
    """Retrieve all valid ticket types configured for the project (e.g. Task, Bug, User Story, Issue, Change Request)."""
    return await manager.get_ticket_types()


# ──────────────────────────────────────────────
#  TICKET CRUD
# ──────────────────────────────────────────────


@mcp.tool()
async def get_ticket(ticket_id: int) -> str:
    """Retrieve full details of a ticket by its ID."""
    return await manager.get_ticket(ticket_id)


@mcp.tool()
async def get_ticket_comments(ticket_id: int) -> str:
    """Retrieve all comments associated with a ticket."""
    return await manager.get_ticket_comments(ticket_id)


@mcp.tool()
async def add_comment(ticket_id: int, text: str) -> str:
    """Add a comment to a ticket."""
    return await manager.add_comment(ticket_id, text)


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

    If ticket_type is not provided, returns a list of available types — call get_ticket_types() first to see valid options.

    Args:
        ticket_type: Ticket type (e.g. 'Task', 'Bug', 'User Story', 'Issue', 'Change Request'). Leave empty to retrieve available types.
        title: Ticket title
        description: HTML description (Azure) or Markdown body (GitHub) of the ticket
        assigned_to: Assignee display name (Azure) or username (GitHub)
        priority: Priority (1-4)
        tags: Semicolon-separated tags (Azure) or labels (GitHub)
        area_path: Area path (Azure; defaults to project area). Ignored for GitHub.
        iteration_path: Iteration path (Azure) or milestone title (GitHub)
    """
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
        description: New description (HTML for Azure, Markdown for GitHub)
        assigned_to: New assignee (leave empty to remove)
        state: New state (Azure: 'Active', 'Closed', 'Resolved'; GitHub: 'open', 'closed')
        priority: New priority (1-4)
        tags: New semicolon-separated tags (Azure) or labels (GitHub)
        area_path: New area path (Azure). Ignored for GitHub.
        iteration_path: New iteration path (Azure) or milestone title (GitHub)
    """
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
        query: Custom WIQL query (Azure) or search query (GitHub, e.g. "is:open bug")
        ticket_type: Ticket type filter (ignored for GitHub)
        state: Ticket state (Azure: 'Active', 'New', 'Closed'; GitHub: 'open', 'closed')
        assigned_to: Assignee display name (Azure) or username (GitHub)
        tags: Semicolon-separated tags (Azure) or labels (GitHub) to filter by
        area_path: Area path filter (Azure). Ignored for GitHub.
        iteration_path: Iteration path filter (Azure) or milestone title (GitHub)
        max_results: Maximum results (Azure default 20 / max 200, GitHub default 20 / max 100)
    """
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
        comment: Optional comment on the link
    """
    return await manager.link_tickets(
        source_id=source_id,
        target_id=target_id,
        link_type=link_type,
        comment=comment,
    )


@mcp.tool()
async def get_ticket_relations(ticket_id: int) -> str:
    """Retrieve all links/relations (Azure) or cross-references and linked PRs (GitHub) of a ticket."""
    return await manager.get_ticket_relations(ticket_id)


# ──────────────────────────────────────────────
#  ITERATIONS / BOARDS
# ──────────────────────────────────────────────


@mcp.tool()
async def get_iterations() -> str:
    """Retrieve all iterations/sprints (Azure) or milestones (GitHub) from the project."""
    return await manager.get_iterations()


@mcp.tool()
async def move_to_iteration(ticket_id: int, iteration_path: str) -> str:
    """Move a ticket to a different iteration/sprint (Azure) or milestone (GitHub).

    Args:
        ticket_id: ID of the ticket to move
        iteration_path: Full iteration path (e.g. 'Project\\Sprint 1') or milestone title
    """
    return await manager.move_to_iteration(ticket_id, iteration_path)


@mcp.tool()
async def get_area_paths() -> str:
    """Retrieve all area paths (Azure) or labels (GitHub) from the project."""
    return await manager.get_area_paths()


# ──────────────────────────────────────────────
#  ENTRYPOINT
# ──────────────────────────────────────────────


def main() -> None:
    """Run the MCP server, or verify the backend connection with ``-check``."""
    if _check_mode:
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
