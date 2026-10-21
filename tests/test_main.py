"""Unit tests for the CLI entrypoint and MCP tool wrappers."""

import argparse
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_ticketing import main as m

# ──────────────────────────────────────────────
#  ARGUMENT HANDLING
# ──────────────────────────────────────────────


def test_parse_args_defaults():
    args = m._parse_args([])
    assert args.backend == "azure"
    assert args.check is False
    assert args.owner is None


def test_parse_args_alias_and_overrides():
    args = m._parse_args(["-check", "github", "--owner", "o", "--repo", "r", "--token", "t"])
    assert args.backend == "github"
    assert args.check is True
    assert (args.owner, args.repo, args.token) == ("o", "r", "t")


def test_parse_args_rejects_unknown_backend():
    with pytest.raises(SystemExit):
        m._parse_args(["jira"])


def test_apply_cli_env_sets_backend_variables(monkeypatch):
    for name in ("MCP_GITHUB_OWNER", "MCP_GITHUB_REPO", "MCP_GITHUB_TOKEN"):
        monkeypatch.setenv(name, "old")
    args = argparse.Namespace(backend="github", owner="new-owner", repo="new-repo", token="new-token")
    m._apply_cli_env(args)
    assert os.environ["MCP_GITHUB_OWNER"] == "new-owner"
    assert os.environ["MCP_GITHUB_REPO"] == "new-repo"
    assert os.environ["MCP_GITHUB_TOKEN"] == "new-token"


def test_apply_cli_env_maps_azure_flags(monkeypatch):
    for name in ("MCP_AZURE_DEVOPS_ORG", "MCP_AZURE_DEVOPS_PROJECT", "MCP_AZDO_PAT"):
        monkeypatch.setenv(name, "old")
    args = argparse.Namespace(backend="azure", owner="org", repo="proj", token="pat")
    m._apply_cli_env(args)
    assert os.environ["MCP_AZURE_DEVOPS_ORG"] == "org"
    assert os.environ["MCP_AZURE_DEVOPS_PROJECT"] == "proj"
    assert os.environ["MCP_AZDO_PAT"] == "pat"


def test_apply_cli_env_skips_none_and_unknown_backend(monkeypatch):
    monkeypatch.setenv("MCP_GITHUB_OWNER", "keep")
    m._apply_cli_env(argparse.Namespace(backend="github", owner=None, repo=None, token=None))
    assert os.environ["MCP_GITHUB_OWNER"] == "keep"

    m._apply_cli_env(argparse.Namespace(backend="unknown", owner="x", repo="y", token="z"))
    assert os.environ["MCP_GITHUB_OWNER"] == "keep"


def test_resolve_defaults_to_sys_argv(monkeypatch):
    monkeypatch.setattr(m, "manager", None)
    monkeypatch.setattr(m.sys, "argv", ["mcp-ticketing", "github"])
    monkeypatch.setenv("MCP_GITHUB_OWNER", "o")
    monkeypatch.setenv("MCP_GITHUB_REPO", "r")
    monkeypatch.setenv("MCP_GITHUB_TOKEN", "t")

    args = m._resolve()

    assert args.backend == "github"
    assert m.manager is not None


def test_resolve_builds_manager_from_argv(monkeypatch):
    monkeypatch.setattr(m, "manager", None)
    monkeypatch.setenv("MCP_GITHUB_API_URL", "http://gh.test")
    monkeypatch.setenv("MCP_GITHUB_OWNER", "old-owner")
    monkeypatch.setenv("MCP_GITHUB_REPO", "repo")
    monkeypatch.setenv("MCP_GITHUB_TOKEN", "token")

    args = m._resolve(["github", "--owner", "new-owner"])

    from mcp_ticketing.github_connector import GithubConnector

    assert args.backend == "github"
    assert isinstance(m.manager._strategy, GithubConnector)
    assert m.manager._strategy.client.owner == "new-owner"


# ──────────────────────────────────────────────
#  TOOL WRAPPERS
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_wrappers_delegate_to_manager(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(m, "manager", fake)

    await m.get_ticket_types()
    await m.get_ticket(1)
    await m.get_ticket_comments(2)
    await m.add_comment(3, "hi")
    await m.add_mermaid(4, "flowchart TD", "title", "Section")
    await m.add_ticket_image(5, "/tmp/img.png", "caption")
    await m.create_ticket(
        ticket_type="Task",
        title="T",
        description="D",
        assigned_to="alice",
        priority=1,
        tags="a;b",
        area_path="Proj\\Area",
        iteration_path="Proj\\Sprint 1",
    )
    await m.update_ticket(
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
    await m.search_tickets(
        query="q",
        ticket_type="Task",
        state="Active",
        assigned_to="alice",
        tags="a",
        area_path="Proj\\Area",
        iteration_path="Proj\\Sprint 1",
        max_results=5,
    )
    await m.get_tickets_needing_my_reply("alice")
    await m.link_tickets(1, 2, "System.LinkTypes.Related", "see also")
    await m.get_ticket_relations(3)
    await m.get_iterations()
    await m.move_to_iteration(4, "Proj\\Sprint 2")
    await m.get_area_paths()

    fake.get_ticket_types.assert_awaited_once_with()
    fake.get_ticket.assert_awaited_once_with(1)
    fake.get_ticket_comments.assert_awaited_once_with(2)
    fake.add_comment.assert_awaited_once_with(3, "hi")
    fake.add_mermaid.assert_awaited_once_with(4, "flowchart TD", "title", "Section")
    fake.add_ticket_image.assert_awaited_once_with(5, "/tmp/img.png", "caption")
    fake.create_ticket.assert_awaited_once_with(
        ticket_type="Task",
        title="T",
        description="D",
        assigned_to="alice",
        priority=1,
        tags="a;b",
        area_path="Proj\\Area",
        iteration_path="Proj\\Sprint 1",
    )
    fake.update_ticket.assert_awaited_once_with(
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
    fake.search_tickets.assert_awaited_once_with(
        query="q",
        ticket_type="Task",
        state="Active",
        assigned_to="alice",
        tags="a",
        area_path="Proj\\Area",
        iteration_path="Proj\\Sprint 1",
        max_results=5,
    )
    fake.get_tickets_needing_my_reply.assert_awaited_once_with("alice")
    fake.link_tickets.assert_awaited_once_with(
        source_id=1, target_id=2, link_type="System.LinkTypes.Related", comment="see also"
    )
    fake.get_ticket_relations.assert_awaited_once_with(3)
    fake.get_iterations.assert_awaited_once_with()
    fake.move_to_iteration.assert_awaited_once_with(4, "Proj\\Sprint 2")
    fake.get_area_paths.assert_awaited_once_with()


# ──────────────────────────────────────────────
#  ENTRYPOINT
# ──────────────────────────────────────────────


def _fake_resolve(monkeypatch, check: bool) -> None:
    monkeypatch.setattr(m, "_resolve", lambda argv=None: argparse.Namespace(backend="github", check=check))


def test_main_check_ok_prints_result(monkeypatch, capsys):
    fake = AsyncMock()
    fake.check.return_value = json.dumps({"status": "ok"})
    monkeypatch.setattr(m, "manager", fake)
    _fake_resolve(monkeypatch, check=True)

    m.main()

    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_main_check_error_exits_one(monkeypatch, capsys):
    fake = AsyncMock()
    fake.check.return_value = json.dumps({"error": "HTTP 401: bad pat"})
    monkeypatch.setattr(m, "manager", fake)
    _fake_resolve(monkeypatch, check=True)

    with pytest.raises(SystemExit) as exc:
        m.main()

    assert exc.value.code == 1
    assert "HTTP 401" in capsys.readouterr().out


def test_main_check_exception_exits_one(monkeypatch, capsys):
    fake = AsyncMock()
    fake.check.side_effect = RuntimeError("boom")
    monkeypatch.setattr(m, "manager", fake)
    _fake_resolve(monkeypatch, check=True)

    with pytest.raises(SystemExit) as exc:
        m.main()

    assert exc.value.code == 1
    assert "boom" in capsys.readouterr().out


def test_main_runs_server_without_check(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(m, "manager", fake)
    _fake_resolve(monkeypatch, check=False)
    run = MagicMock()
    monkeypatch.setattr(m.mcp, "run", run)

    m.main()

    run.assert_called_once_with()
