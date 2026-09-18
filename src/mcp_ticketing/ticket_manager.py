"""TicketManager: facade over a TicketProtocol strategy.

The manager implements the same TicketProtocol interface and delegates
every operation to an injected strategy (AzureConnector, GithubConnector,
or any future backend). The rest of the application talks to TicketManager
without knowing which backend serves the tickets.
"""

from mcp_ticketing.azure_connector import AzureConnector
from mcp_ticketing.github_connector import GithubConnector
from mcp_ticketing.protocol import TicketProtocol

BACKENDS: dict[str, type[TicketProtocol]] = {
    "azure": AzureConnector,
    "github": GithubConnector,
}


class TicketManager(TicketProtocol):
    """Delegating facade that forwards all ticket operations to a strategy.

    The strategy is injected at construction time (dependency injection),
        so the manager is backend-agnostic. It accepts either a concrete
        TicketProtocol instance or a backend name (e.g. 'azure', 'github')
        that is resolved through the BACKENDS registry.
    """

    def __init__(self, strategy: str | TicketProtocol):
        if isinstance(strategy, str):
            strategy = self._resolve(strategy)
        self._strategy = strategy

    @staticmethod
    def _resolve(backend: str) -> TicketProtocol:
        try:
            connector_cls = BACKENDS[backend.lower()]
        except KeyError:
            available = ", ".join(sorted(BACKENDS))
            raise ValueError(
                f"Unknown backend '{backend}'. Available backends: {available}"
            ) from None
        return connector_cls()

    # ──────────────────────────────────────────────
    #  TICKET TYPES
    # ──────────────────────────────────────────────

    async def get_ticket_types(self) -> str:
        """Retrieve all valid ticket types supported by the backend."""
        return await self._strategy.get_ticket_types()

    async def check(self) -> str:
        """Verify the connection and credentials to the backend (CLI-only)."""
        return await self._strategy.check()

    # ──────────────────────────────────────────────
    #  TICKET CRUD
    # ──────────────────────────────────────────────

    async def get_ticket(self, ticket_id: int) -> str:
        """Retrieve full details of a ticket."""
        return await self._strategy.get_ticket(ticket_id)

    async def get_ticket_comments(self, ticket_id: int) -> str:
        """Retrieve all comments associated with a ticket."""
        return await self._strategy.get_ticket_comments(ticket_id)

    async def add_comment(self, ticket_id: int, text: str) -> str:
        """Add a comment to a ticket."""
        return await self._strategy.add_comment(ticket_id, text)

    async def add_mermaid(
        self,
        ticket_id: int,
        diagram: str,
        title: str = "",
        section: str = "Diagrams",
    ) -> str:
        """Append a Mermaid diagram to a ticket."""
        return await self._strategy.add_mermaid(ticket_id, diagram, title, section)

    async def add_ticket_image(
        self, ticket_id: int, image_path: str, comment: str = ""
    ) -> str:
        """Attach an image file to a ticket."""
        return await self._strategy.add_ticket_image(ticket_id, image_path, comment)

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
        """Create a new ticket."""
        return await self._strategy.create_ticket(
            ticket_type=ticket_type,
            title=title,
            description=description,
            assigned_to=assigned_to,
            priority=priority,
            tags=tags,
            area_path=area_path,
            iteration_path=iteration_path,
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
        """Update an existing ticket. Only provided fields are modified."""
        return await self._strategy.update_ticket(
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
        """Search tickets using a custom query or built-in filters."""
        return await self._strategy.search_tickets(
            query=query,
            ticket_type=ticket_type,
            state=state,
            assigned_to=assigned_to,
            tags=tags,
            area_path=area_path,
            iteration_path=iteration_path,
            max_results=max_results,
        )

    async def get_tickets_needing_my_reply(self, my_display_name: str) -> str:
        """Find active tickets assigned to a user where the last comment was not written by them."""
        return await self._strategy.get_tickets_needing_my_reply(my_display_name)

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
        """Link two tickets."""
        return await self._strategy.link_tickets(
            source_id=source_id,
            target_id=target_id,
            link_type=link_type,
            comment=comment,
        )

    async def get_ticket_relations(self, ticket_id: int) -> str:
        """Retrieve all links/relations of a ticket."""
        return await self._strategy.get_ticket_relations(ticket_id)

    # ──────────────────────────────────────────────
    #  ITERATIONS / BOARDS
    # ──────────────────────────────────────────────

    async def get_iterations(self) -> str:
        """Retrieve all iterations (sprints / milestones) from the backend."""
        return await self._strategy.get_iterations()

    async def move_to_iteration(self, ticket_id: int, iteration_path: str) -> str:
        """Move a ticket to a different iteration."""
        return await self._strategy.move_to_iteration(ticket_id, iteration_path)

    async def get_area_paths(self) -> str:
        """Retrieve all area paths / labels from the backend."""
        return await self._strategy.get_area_paths()
