"""Unit tests for the TicketManager facade (backend resolution + delegation)."""

import json
from unittest.mock import AsyncMock

import pytest

from mcp_ticketing.azure_connector import AzureConnector
from mcp_ticketing.github_connector import GithubConnector
from mcp_ticketing.protocol import TicketProtocol
from mcp_ticketing.ticket_manager import TicketManager


def test_resolve_known_backends(monkeypatch):
    monkeypatch.setenv("MCP_AZURE_DEVOPS_ORG", "org")
    monkeypatch.setenv("MCP_AZURE_DEVOPS_PROJECT", "proj")
    monkeypatch.setenv("MCP_AZDO_PAT", "pat")
    monkeypatch.setenv("MCP_GITHUB_OWNER", "owner")
    monkeypatch.setenv("MCP_GITHUB_REPO", "repo")
    monkeypatch.setenv("MCP_GITHUB_TOKEN", "token")

    assert isinstance(TicketManager._resolve("azure"), AzureConnector)
    assert isinstance(TicketManager._resolve("github"), GithubConnector)


def test_resolve_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("MCP_GITHUB_OWNER", "owner")
    monkeypatch.setenv("MCP_GITHUB_REPO", "repo")
    monkeypatch.setenv("MCP_GITHUB_TOKEN", "token")
    assert isinstance(TicketManager._resolve("GitHub"), GithubConnector)


def test_resolve_unknown_backend():
    with pytest.raises(ValueError, match="Unknown backend 'jira'. Available backends: azure, github"):
        TicketManager._resolve("jira")


def test_manager_accepts_strategy_instance():
    strategy = AsyncMock()
    manager = TicketManager(strategy)
    assert manager._strategy is strategy


@pytest.mark.asyncio
async def test_manager_delegates_every_operation():
    strategy = AsyncMock()
    manager = TicketManager(strategy)

    await manager.get_ticket_types()
    await manager.check()
    await manager.get_ticket(1)
    await manager.get_ticket_comments(2)
    await manager.add_comment(3, "hi")
    await manager.add_mermaid(4, "flowchart TD", "title", "Section")
    await manager.add_ticket_image(5, "/tmp/img.png", "caption")
    await manager.create_ticket(
        ticket_type="Task",
        title="T",
        description="D",
        assigned_to="alice",
        priority=1,
        tags="a;b",
        area_path="Proj\\Area",
        iteration_path="Proj\\Sprint 1",
    )
    await manager.update_ticket(
        ticket_id=6,
        title="T",
        description="D",
        assigned_to="bob",
        state="Active",
        priority=2,
        tags="c",
        area_path="Proj\\Area",
        iteration_path="Proj\\Sprint 2",
    )
    await manager.search_tickets(
        query="q",
        ticket_type="Task",
        state="Active",
        assigned_to="alice",
        tags="a",
        area_path="Proj\\Area",
        iteration_path="Proj\\Sprint 1",
        max_results=5,
    )
    await manager.get_tickets_needing_my_reply("alice")
    await manager.link_tickets(1, 2, "System.LinkTypes.Related", "see also")
    await manager.get_ticket_relations(3)
    await manager.get_iterations()
    await manager.move_to_iteration(4, "Proj\\Sprint 2")
    await manager.get_area_paths()

    strategy.get_ticket_types.assert_awaited_once_with()
    strategy.check.assert_awaited_once_with()
    strategy.get_ticket.assert_awaited_once_with(1)
    strategy.get_ticket_comments.assert_awaited_once_with(2)
    strategy.add_comment.assert_awaited_once_with(3, "hi")
    strategy.add_mermaid.assert_awaited_once_with(4, "flowchart TD", "title", "Section")
    strategy.add_ticket_image.assert_awaited_once_with(5, "/tmp/img.png", "caption")
    strategy.create_ticket.assert_awaited_once_with(
        ticket_type="Task",
        title="T",
        description="D",
        assigned_to="alice",
        priority=1,
        tags="a;b",
        area_path="Proj\\Area",
        iteration_path="Proj\\Sprint 1",
    )
    strategy.update_ticket.assert_awaited_once_with(
        ticket_id=6,
        title="T",
        description="D",
        assigned_to="bob",
        state="Active",
        priority=2,
        tags="c",
        area_path="Proj\\Area",
        iteration_path="Proj\\Sprint 2",
    )
    strategy.search_tickets.assert_awaited_once_with(
        query="q",
        ticket_type="Task",
        state="Active",
        assigned_to="alice",
        tags="a",
        area_path="Proj\\Area",
        iteration_path="Proj\\Sprint 1",
        max_results=5,
    )
    strategy.get_tickets_needing_my_reply.assert_awaited_once_with("alice")
    strategy.link_tickets.assert_awaited_once_with(
        source_id=1, target_id=2, link_type="System.LinkTypes.Related", comment="see also"
    )
    strategy.get_ticket_relations.assert_awaited_once_with(3)
    strategy.get_iterations.assert_awaited_once_with()
    strategy.move_to_iteration.assert_awaited_once_with(4, "Proj\\Sprint 2")
    strategy.get_area_paths.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_protocol_default_add_ticket_image():
    parsed = json.loads(await TicketProtocol.add_ticket_image(object(), 1, "x.png"))
    assert "not supported" in parsed["error"]
