"""Integration tests against the stateful fake servers (docker-compose).

Run with:  tox -e integration   (or: docker compose -f tests/docker/docker-compose.yml up -d)
These tests are excluded from the default run via ``-m 'not integration'``.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from mcp_ticketing.azure_connector import AzureConnector
from mcp_ticketing.github_connector import GithubConnector

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FAKE_BASE = "http://localhost:8080"


def _load(payload: str) -> dict:
    data = json.loads(payload)
    assert "error" not in data, f"unexpected error payload: {data}"
    return data


@pytest.fixture(scope="module", autouse=True)
def fake_server_up():
    try:
        r = httpx.get(f"{FAKE_BASE}/healthz", timeout=3.0)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"fake server not reachable at {FAKE_BASE} — "
            f"run `tox -e integration` or `docker compose -f tests/docker/docker-compose.yml up -d` ({exc})"
        )


@pytest.fixture
def azure(monkeypatch):
    monkeypatch.setenv("MCP_AZURE_DEVOPS_ORG", "fakeorg")
    monkeypatch.setenv("MCP_AZURE_DEVOPS_PROJECT", "fakeproj")
    monkeypatch.setenv("MCP_AZDO_PAT", "fake-pat")
    monkeypatch.setenv("MCP_AZURE_DEVOPS_BASE_URL", f"{FAKE_BASE}/fakeorg/fakeproj/_apis")
    return AzureConnector()


@pytest.fixture
def github(monkeypatch):
    monkeypatch.setenv("MCP_GITHUB_OWNER", "fakeowner")
    monkeypatch.setenv("MCP_GITHUB_REPO", "fakerepo")
    monkeypatch.setenv("MCP_GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("MCP_GITHUB_API_URL", FAKE_BASE)
    return GithubConnector()


# ──────────────────────────────────────────────
#  AZURE
# ──────────────────────────────────────────────


async def test_azure_check_and_types(azure):
    assert _load(await azure.check())["status"] == "ok"
    assert _load(await azure.get_ticket_types())["total"] >= 5


async def test_azure_create_get_update_comment(azure):
    marker = uuid.uuid4().hex[:12]
    created = _load(
        await azure.create_ticket(
            ticket_type="Task",
            title=f"IT azure CRUD {marker}",
            description=f"**body** of {marker}",
            priority=2,
            tags="it;fake",
        )
    )
    tid = created["id"]
    assert created["success"] is True

    ticket = _load(await azure.get_ticket(tid))
    assert ticket["title"] == f"IT azure CRUD {marker}"
    assert ticket["type"] == "Task"
    assert ticket["priority"] == "2"
    assert ticket["description"]  # markdown rendered to HTML

    updated = _load(await azure.update_ticket(tid, title=f"IT azure updated {marker}", state="Active"))
    assert updated["success"] is True
    assert _load(await azure.get_ticket(tid))["state"] == "Active"

    assert _load(await azure.add_comment(tid, f"hello from {marker}"))["success"] is True
    comments = _load(await azure.get_ticket_comments(tid))
    assert any(marker in c["text"] for c in comments["comments"])


async def test_azure_search_returns_created_ticket(azure):
    marker = uuid.uuid4().hex[:12]
    created = _load(await azure.create_ticket(ticket_type="Bug", title=f"IT search {marker}"))
    results = _load(
        await azure.search_tickets(query=f"SELECT [System.Id] FROM WorkItems WHERE [System.Title] Contains '{marker}'")
    )
    assert any(r["id"] == created["id"] for r in results["results"])


async def test_azure_validation_missing_type(azure):
    data = json.loads(await azure.create_ticket(ticket_type="", title="no type"))
    assert data["error"] == "ticket_type is required but was not provided."
    assert "Task" in data["available_types"]


async def test_azure_link_relations_and_iterations(azure):
    a = _load(await azure.create_ticket(ticket_type="Task", title=f"IT link A {uuid.uuid4().hex[:8]}"))
    b = _load(await azure.create_ticket(ticket_type="Task", title=f"IT link B {uuid.uuid4().hex[:8]}"))

    link = _load(await azure.link_tickets(source_id=a["id"], target_id=b["id"], comment="parent please"))
    assert link["success"] is True
    relations = _load(await azure.get_ticket_relations(a["id"]))
    assert any(r["target_id"] == str(b["id"]) for r in relations["relations"])

    iterations = _load(await azure.get_iterations())
    assert iterations["total"] >= 2

    moved = _load(await azure.move_to_iteration(a["id"], "\\fakeproj\\Sprint 2"))
    assert moved["success"] is True
    assert _load(await azure.get_ticket(a["id"]))["iteration_path"] == "\\fakeproj\\Sprint 2"

    assert _load(await azure.get_area_paths())["path"] == "\\fakeproj"


# ──────────────────────────────────────────────
#  GITHUB
# ──────────────────────────────────────────────


async def test_github_check_and_types(github):
    assert _load(await github.check())["status"] == "ok"
    assert _load(await github.get_ticket_types())["ticket_types"][0]["name"] == "Issue"


async def test_github_create_get_update_comment(github):
    marker = uuid.uuid4().hex[:12]
    created = _load(
        await github.create_ticket(
            title=f"IT github CRUD {marker}",
            description=f"body of {marker}",
            tags="bug;it",
            priority=3,
        )
    )
    iid = created["id"]
    assert created["success"] is True

    ticket = _load(await github.get_ticket(iid))
    assert ticket["title"] == f"IT github CRUD {marker}"
    assert ticket["state"] == "open"
    assert "bug" in ticket["tags"]

    assert _load(await github.update_ticket(iid, state="closed"))["success"] is True
    assert _load(await github.get_ticket(iid))["state"] == "closed"

    assert _load(await github.add_comment(iid, f"note {marker}"))["success"] is True
    comments = _load(await github.get_ticket_comments(iid))
    assert any(marker in c["text"] for c in comments["comments"])


async def test_github_search_unique_term(github):
    marker = uuid.uuid4().hex[:12]
    created = _load(await github.create_ticket(title=f"IT gh search {marker}"))
    results = _load(await github.search_tickets(query=marker))
    assert any(r["id"] == created["id"] for r in results["results"])


async def test_github_link_relations(github):
    a = _load(await github.create_ticket(title=f"IT gh link A {uuid.uuid4().hex[:8]}"))
    b = _load(await github.create_ticket(title=f"IT gh link B {uuid.uuid4().hex[:8]}"))

    link = _load(await github.link_tickets(source_id=a["id"], target_id=b["id"]))
    assert link["success"] is True
    relations = _load(await github.get_ticket_relations(a["id"]))
    assert any(r["type"] == "related" and r["target_id"] == str(b["id"]) for r in relations["relations"])


async def test_github_missing_issue_returns_error_json(github):
    data = json.loads(await github.get_ticket(999_999))
    assert data["error"].startswith("HTTP 404")


async def test_github_tickets_needing_my_reply(github):
    marker = uuid.uuid4().hex[:8]
    created = _load(await github.create_ticket(title=f"IT gh pending {marker}", assigned_to="fakeuser"))
    await github.add_comment(created["id"], "someone else replied")

    pending = _load(await github.get_tickets_needing_my_reply("fakeuser"))
    assert any(p["ticket_id"] == created["id"] for p in pending["pending"])
