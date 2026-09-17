"""Abstract protocol for ticket operations across different ticketing backends.

The protocol defines the full set of operations that any ticketing backend
(Azure DevOps, GitHub, ...) must implement. All methods return JSON strings,
following the project convention for MCP tool outputs.
"""

import json
from abc import ABC, abstractmethod


class TicketProtocol(ABC):
    """Common interface for operating with tickets in a ticketing server.

    Concrete implementations (AzureConnector, GithubConnector) translate
    these operations into backend-specific API calls. TicketManager uses a
    TicketProtocol strategy so the rest of the application never depends on
    a concrete backend.
    """

    @abstractmethod
    async def get_ticket_types(self) -> str:
        """Retrieve all valid ticket types supported by the backend."""

    @abstractmethod
    async def check(self) -> str:
        """Verify the connection and credentials to the backend (CLI-only)."""

    @abstractmethod
    async def get_ticket(self, ticket_id: int) -> str:
        """Retrieve full details of a ticket."""

    @abstractmethod
    async def get_ticket_comments(self, ticket_id: int) -> str:
        """Retrieve all comments associated with a ticket."""

    @abstractmethod
    async def add_comment(self, ticket_id: int, text: str) -> str:
        """Add a comment to a ticket."""

    @abstractmethod
    async def add_mermaid(
        self,
        ticket_id: int,
        diagram: str,
        title: str = "",
        section: str = "Diagrams",
    ) -> str:
        """Append a Mermaid diagram to a ticket.

        ``section`` is used by GitHub (Markdown heading, default 'Diagrams')
        and ignored by Azure, which renders the diagram to a PNG and
        attaches it to the work item.
        """

    async def add_ticket_image(
        self, ticket_id: int, image_path: str, comment: str = ""
    ) -> str:
        """Attach an image file to a ticket.

        Backends that do not support image attachments inherit this default
        implementation and return a "not supported" error without hitting
        the network.
        """
        return json.dumps(
            {"error": "add_ticket_image is not supported by this backend."},
            ensure_ascii=False,
        )

    @abstractmethod
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

    @abstractmethod
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

    @abstractmethod
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

    @abstractmethod
    async def get_tickets_needing_my_reply(self, my_display_name: str) -> str:
        """Find active tickets assigned to a user where the last comment was not written by them."""

    @abstractmethod
    async def link_tickets(
        self,
        source_id: int,
        target_id: int,
        link_type: str = "related",
        comment: str = "",
    ) -> str:
        """Link two tickets."""

    @abstractmethod
    async def get_ticket_relations(self, ticket_id: int) -> str:
        """Retrieve all links/relations of a ticket."""

    @abstractmethod
    async def get_iterations(self) -> str:
        """Retrieve all iterations (sprints / milestones) from the backend."""

    @abstractmethod
    async def move_to_iteration(self, ticket_id: int, iteration_path: str) -> str:
        """Move a ticket to a different iteration."""

    @abstractmethod
    async def get_area_paths(self) -> str:
        """Retrieve all area paths / labels from the backend."""
