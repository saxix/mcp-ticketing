"""Offline tests for the Redmine connector (no network).

Real-API integration tests skip automatically when the Redmine environment
variables are not set (see the session-scoped ``pytestmark`` pattern used by
``test_azure.py`` / ``test_github.py``). The tests here mock the HTTP client
to cover the projection shape, the create/update payload building, state and
assignee resolution, and the search parameter mapping.
"""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from mcp_ticketing.redmine_connector import RedmineConnector
from mcp_ticketing.redmine_connector import format_ticket as redmine_format_ticket

REDMINE_ISSUE = {
    "id": 4242,
    "subject": "Configurable Payee Type",
    "tracker": {"id": 2, "name": "Bug"},
    "status": {"id": 1, "name": "New"},
    "assigned_to": {"id": 7, "name": "Mario Rossi"},
    "priority": {"id": 3, "name": "Normal"},
    "fixed_version": {"id": 9, "name": "v1.2"},
    "created_on": "2026-10-02T00:00:00Z",
    "updated_on": "2026-10-05T00:00:00Z",
    "description": "<p>Payee type must be configurable.</p>",
    "journals": [
        {"id": 1, "notes": "First note", "user": {"name": "Mario Rossi"}, "created_on": "2026-10-02T01:00:00Z"},
        {"id": 2, "notes": "", "user": {"name": "Sistema"}, "created_on": "2026-10-02T02:00:00Z"},
        {"id": 3, "notes": "Second note", "user": {"name": "Luigi Verdi"}, "created_on": "2026-10-03T00:00:00Z"},
    ],
}

REDMINE_ISSUE_NEW = {
    "id": 4242,
    "subject": "Configurable Payee Type",
    "issue_type": {"id": 2, "name": "Bug"},
    "status": {"id": 1, "name": "New"},
    "assigned_to": {"id": 7, "name": "Mario Rossi"},
    "priority": {"id": 3, "name": "Normal"},
    "version": {"id": 9, "name": "v1.2"},
    "created_on": "2026-10-02T00:00:00Z",
    "updated_on": "2026-10-05T00:00:00Z",
    "description": "<p>Payee type must be configurable.</p>",
}

REDMINE_PROJECT = {"id": 3, "identifier": "openextras", "name": "opencode-extras"}
REDMINE_PROJECTS = {"projects": [REDMINE_PROJECT]}
REDMINE_TYPES = {"issue_types": [{"id": 1, "name": "Task"}, {"id": 2, "name": "Bug"}]}
REDMINE_TRACKERS = {"trackers": [{"id": 1, "name": "Task"}, {"id": 2, "name": "Bug"}]}
REDMINE_STATUSES = {
    "issues_statuses": [
        {"id": 1, "name": "New", "is_closed": False},
        {"id": 2, "name": "In progress", "is_closed": False},
        {"id": 5, "name": "Closed", "is_closed": True},
    ]
}
REDMINE_USERS = {"users": [{"id": 7, "login": "mario", "name": "Mario Rossi"}]}
REDMINE_VERSIONS = {"versions": [{"id": 9, "name": "v1.2"}, {"id": 10, "name": "v2.0"}]}
REDMINE_ACCOUNT = {"user": {"id": 7, "login": "mario", "name": "Mario Rossi"}}

BASE_KEYS = {
    "id",
    "title",
    "type",
    "state",
    "assigned_to",
    "tags",
    "area_path",
    "iteration_path",
    "priority",
    "created_date",
    "changed_date",
}


@pytest.fixture(autouse=True)
def _redmine_env(monkeypatch):
    """Point the Redmine connector at a fake instance for offline tests."""
    monkeypatch.setenv("MCP_REDMINE_URL", "http://redmine.test")
    monkeypatch.setenv("MCP_REDMINE_API_KEY", "test-key")
    monkeypatch.setenv("MCP_REDMINE_PROJECT", "openextras")
    yield


def _connector(responses: dict, post=None, put=None, base_url: str = "http://redmine.test") -> RedmineConnector:
    """Build a RedmineConnector whose client returns canned JSON per path.

    ``responses`` maps a path (or path prefix) to the JSON the GET should
    return; ``post``/``put`` are the return values for the write calls.
    The mock also exposes ``base_url``/``api_key``/``project`` like the real
    ``RedmineClient`` (the connector reads them from ``self.client``).
    """
    client = AsyncMock()
    client.base_url = base_url
    client.api_key = "test-key"
    client.project = "openextras"

    async def _get(path: str, *args, **kwargs):
        for prefix, payload in responses.items():
            if path.startswith(prefix):
                return payload
        # Simulate a 404 for endpoints not present in the mock, so the
        # connector's fallback logic (old Redmine) can be exercised.
        import httpx as _httpx
        resp = _httpx.Response(404, json={"status": 404, "error": "Not Found"})
        raise _httpx.HTTPStatusError(
            f"404 for {path}", request=_httpx.Request("GET", f"http://redmine.test/{path}"),
            response=resp,
        )

    client.get.side_effect = _get
    if post is not None:
        client.post = AsyncMock(return_value=post)
    if put is not None:
        client.put = AsyncMock(return_value=put)
    return RedmineConnector(client=client)


def _load(resp: str) -> dict:
    return json.loads(resp)


# ──────────────────────────────────────────────
#  format_ticket — projection shape
# ──────────────────────────────────────────────


def test_redmine_format_ticket_omits_description_by_default():
    parsed = _load(redmine_format_ticket(REDMINE_ISSUE))
    assert "description" not in parsed
    assert set(parsed) == BASE_KEYS


def test_redmine_format_ticket_includes_description_on_request():
    parsed = _load(redmine_format_ticket(REDMINE_ISSUE, include_description=True))
    assert set(parsed) == BASE_KEYS | {"description"}
    assert parsed["description"] == REDMINE_ISSUE["description"]


def test_redmine_format_ticket_fields():
    parsed = _load(redmine_format_ticket(REDMINE_ISSUE, include_description=True))
    assert parsed["id"] == 4242
    assert parsed["title"] == "Configurable Payee Type"
    assert parsed["type"] == "Bug"
    assert parsed["state"] == "New"
    assert parsed["assigned_to"] == "Mario Rossi"
    assert parsed["iteration_path"] == "v1.2"
    assert parsed["priority"] == "Normal"


def test_redmine_format_ticket_old_redmine_fields():
    """Old Redmine uses 'tracker' and 'fixed_version' instead of 'issue_type' and 'version'."""
    old_issue = {
        "id": 100,
        "subject": "Old Redmine Issue",
        "tracker": {"id": 5, "name": "Bug"},
        "status": {"id": 1, "name": "Nuova"},
        "assigned_to": {"id": 3, "name": "Mario Rossi"},
        "priority": {"id": 5, "name": "Alta"},
        "fixed_version": {"id": 42, "name": "2.1.x"},
        "created_on": "2026-01-01T00:00:00Z",
        "updated_on": "2026-01-02T00:00:00Z",
        "description": "<p>Old Redmine</p>",
    }
    parsed = _load(redmine_format_ticket(old_issue, include_description=True))
    assert parsed["type"] == "Bug"
    assert parsed["state"] == "Nuova"
    assert parsed["iteration_path"] == "2.1.x"
    assert parsed["priority"] == "Alta"


def test_redmine_format_ticket_unassigned_defaults():
    parsed = _load(redmine_format_ticket({"id": 1, "subject": "x"}, include_description=True))
    assert parsed["assigned_to"] == "Unassigned"
    assert parsed["description"] == ""


# ──────────────────────────────────────────────
#  get_ticket / comments
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_ticket_returns_description():
    connector = _connector({"issues/4242.json": {"issue": REDMINE_ISSUE}})
    parsed = _load(await connector.get_ticket(4242))
    assert parsed["id"] == 4242
    assert "Payee type must be configurable." in parsed["description"]


@pytest.mark.asyncio
async def test_get_ticket_not_found_returns_error():
    client = AsyncMock()

    def _raise(*args, **kwargs):
        request = httpx.Request("GET", "http://redmine.test/issues/999999999.json")
        raise httpx.HTTPStatusError("not found", request=request, response=httpx.Response(404, request=request))

    client.get.side_effect = _raise
    connector = RedmineConnector(client=client)
    parsed = _load(await connector.get_ticket(999999999))
    assert "error" in parsed


@pytest.mark.asyncio
async def test_get_ticket_comments_only_notes():
    """Journals without notes (pure field changes) must not appear as comments."""
    connector = _connector({"issues/4242.json": {"issue": REDMINE_ISSUE}})
    parsed = _load(await connector.get_ticket_comments(4242))
    assert parsed["ticket_id"] == 4242
    assert parsed["total"] == 2
    assert [c["text"] for c in parsed["comments"]] == ["First note", "Second note"]


# ──────────────────────────────────────────────
#  types / check / area paths
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_ticket_types():
    connector = _connector({"issue_types.json": REDMINE_TYPES})
    parsed = _load(await connector.get_ticket_types())
    assert parsed["total"] == 2
    assert [t["name"] for t in parsed["ticket_types"]] == ["Task", "Bug"]


@pytest.mark.asyncio
async def test_check_ok():
    connector = _connector({"my_account.json": REDMINE_ACCOUNT})
    parsed = _load(await connector.check())
    assert parsed["status"] == "ok"
    assert parsed["backend"] == "redmine"
    assert parsed["user"] == "Mario Rossi"


@pytest.mark.asyncio
async def test_check_fallback_to_projects():
    """Old Redmine has no my_account.json; check falls back to projects.json."""
    connector = _connector({"projects.json": REDMINE_PROJECTS})
    parsed = _load(await connector.check())
    assert parsed["status"] == "ok"
    assert parsed["backend"] == "redmine"


@pytest.mark.asyncio
async def test_get_ticket_types_fallback_to_trackers():
    """Old Redmine has no issue_types.json; falls back to trackers."""
    connector = _connector({"trackers.json": REDMINE_TRACKERS})
    parsed = _load(await connector.get_ticket_types())
    assert parsed["total"] == 2
    assert [t["name"] for t in parsed["ticket_types"]] == ["Task", "Bug"]


@pytest.mark.asyncio
async def test_get_area_paths_returns_trackers():
    connector = _connector({"trackers.json": REDMINE_TRACKERS})
    parsed = _load(await connector.get_area_paths())
    assert parsed["total"] == 2
    assert parsed["area_paths"][0]["name"] == "Task"


# ──────────────────────────────────────────────
#  create_ticket — payload building
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_ticket_payload():
    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "issue_types.json": REDMINE_TYPES,
            "users.json": REDMINE_USERS,
            "projects/3/versions.json": REDMINE_VERSIONS,
        },
        post={"issue": {"id": 500, "issue_type": {"name": "Bug"}}},
    )

    parsed = _load(
        await connector.create_ticket(
            ticket_type="bug",
            title="New ticket",
            description="## Problem\n\nBody",
            assigned_to="mario",
            priority=2,
            iteration_path="v1.2",
        )
    )
    assert parsed["success"] is True
    assert parsed["id"] == 500
    assert parsed["url"].endswith("/issues/500")

    post_call = connector.client.post.call_args
    payload = post_call.kwargs["data"]
    assert post_call.args[0].endswith("issues.json")
    issue = payload["issue"]
    assert issue["project_id"] == 3
    assert issue["subject"] == "New ticket"
    assert issue["tracker_id"] == 2  # case-insensitive
    assert issue["assigned_to_id"] == 7
    assert issue["priority_id"] == 2  # High
    assert issue["fixed_version_id"] == 9
    assert "<h2>Problem</h2>" in issue["description"]  # markdown converted to HTML


@pytest.mark.asyncio
async def test_create_ticket_requires_title():
    connector = _connector({})
    parsed = _load(await connector.create_ticket(ticket_type="Task"))
    assert "error" in parsed
    assert "title" in parsed["error"]


@pytest.mark.asyncio
async def test_create_ticket_unknown_type_errors():
    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "issue_types.json": REDMINE_TYPES,
            "trackers.json": REDMINE_TRACKERS,
        }
    )
    parsed = _load(await connector.create_ticket(ticket_type="Epic", title="x"))
    assert "error" in parsed
    assert "Epic" in parsed["error"]


@pytest.mark.asyncio
async def test_create_ticket_unknown_assignee_errors():
    connector = _connector({"projects.json": REDMINE_PROJECTS, "users.json": REDMINE_USERS})
    parsed = _load(await connector.create_ticket(title="x", assigned_to="nobody"))
    assert "error" in parsed
    assert "nobody" in parsed["error"]


# ──────────────────────────────────────────────
#  update_ticket
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_ticket_no_fields():
    connector = _connector({})
    parsed = _load(await connector.update_ticket(ticket_id=1))
    assert "error" in parsed
    assert "No fields to update" in parsed["error"]


@pytest.mark.asyncio
async def test_update_ticket_state_and_priority():
    connector = _connector({"issues_statuses.json": REDMINE_STATUSES}, put={"issue": {"id": 1}})
    parsed = _load(await connector.update_ticket(ticket_id=1, state="closed", priority=1))
    assert parsed["success"] is True
    payload = connector.client.put.call_args.kwargs["data"]
    assert payload["issue"]["status_id"] == 5  # 'closed' -> 'Closed'
    assert payload["issue"]["priority_id"] == 1  # Urgent


@pytest.mark.asyncio
async def test_update_ticket_direct_status_name():
    connector = _connector({"issues_statuses.json": REDMINE_STATUSES}, put={"issue": {"id": 1}})
    parsed = _load(await connector.update_ticket(ticket_id=1, state="In progress"))
    assert parsed["success"] is True
    payload = connector.client.put.call_args.kwargs["data"]
    assert payload["issue"]["status_id"] == 2


@pytest.mark.asyncio
async def test_update_ticket_unknown_state_errors():
    connector = _connector({"issues_statuses.json": REDMINE_STATUSES})
    parsed = _load(await connector.update_ticket(ticket_id=1, state="bogus-state"))
    assert "error" in parsed
    assert "bogus-state" in parsed["error"]


# ──────────────────────────────────────────────
#  search_tickets — parameter mapping
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_tickets_builds_filters():
    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "issue_types.json": REDMINE_TYPES,
            "issues_statuses.json": REDMINE_STATUSES,
            "users.json": REDMINE_USERS,
            "issues.json": {"issues": [], "total_count": 0},
        }
    )
    parsed = _load(
        await connector.search_tickets(
            query="payee",
            ticket_type="bug",
            state="new",
            assigned_to="mario",
            max_results=50,
        )
    )
    assert parsed["results"] == []
    (call,) = [c for c in connector.client.get.call_args_list if c.args and c.args[0] == "issues.json"]
    params = call.kwargs["params"]
    assert params["project_id"] == 3
    assert params["query"] == "payee"
    assert params["tracker_id"] == 2
    assert params["status_id"] == 1
    assert params["assigned_to_id"] == 7
    assert params["limit"] == 50


@pytest.mark.asyncio
async def test_search_tickets_omits_description():
    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "issues.json": {"issues": [REDMINE_ISSUE], "total_count": 1},
        }
    )
    parsed = _load(await connector.search_tickets(query="payee"))
    assert parsed["total"] == 1
    assert "description" not in parsed["results"][0]


# ──────────────────────────────────────────────
#  links / iterations
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_link_tickets_relation_payload():
    connector = _connector({}, put={})
    parsed = _load(await connector.link_tickets(1, 2, link_type="blocks", comment="wait"))
    assert parsed["success"] is True
    payload = connector.client.put.call_args.kwargs["data"]
    assert payload["issue"]["relations"] == [{"to_issue_id": 2, "relation_type": 2}]
    assert payload["issue"]["notes"] == "wait"


@pytest.mark.asyncio
async def test_link_tickets_unknown_type_errors():
    connector = _connector({})
    parsed = _load(await connector.link_tickets(1, 2, link_type="nope"))
    assert "error" in parsed


@pytest.mark.asyncio
async def test_get_ticket_relations():
    issue = dict(REDMINE_ISSUE)
    issue["relations"] = [
        {"relation_type": 1, "delay": 0, "issue": {"id": 10, "subject": "Other"}},
    ]
    connector = _connector({"issues/4242.json": {"issue": issue}})
    parsed = _load(await connector.get_ticket_relations(4242))
    assert parsed["total"] == 1
    assert parsed["relations"][0]["target_id"] == 10


@pytest.mark.asyncio
async def test_get_iterations_maps_versions():
    connector = _connector({"projects.json": REDMINE_PROJECTS, "projects/3/versions.json": REDMINE_VERSIONS})
    parsed = _load(await connector.get_iterations())
    assert parsed["total"] == 2
    assert parsed["iterations"][0]["name"] == "v1.2"


@pytest.mark.asyncio
async def test_move_to_iteration_payload():
    connector = _connector(
        {"projects.json": REDMINE_PROJECTS, "projects/3/versions.json": REDMINE_VERSIONS},
        put={},
    )
    parsed = _load(await connector.move_to_iteration(1, "v2.0"))
    assert parsed["success"] is True
    payload = connector.client.put.call_args.kwargs["data"]
    assert payload["issue"]["fixed_version_id"] == 10


@pytest.mark.asyncio
async def test_move_to_iteration_unknown_version_errors():
    connector = _connector(
        {"projects.json": REDMINE_PROJECTS, "projects/3/versions.json": REDMINE_VERSIONS}
    )
    parsed = _load(await connector.move_to_iteration(1, "v9.9"))
    assert "error" in parsed
    assert "v9.9" in parsed["error"]


# ──────────────────────────────────────────────
#  needing reply
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tickets_needing_my_reply():
    connector = _connector(
        {
            "users.json": REDMINE_USERS,
            "projects.json": REDMINE_PROJECTS,
            "issues.json": {"issues": [REDMINE_ISSUE], "total_count": 1},
        }
    )
    parsed = _load(await connector.get_tickets_needing_my_reply("Mario Rossi"))
    assert parsed["total"] == 1
    assert parsed["pending"][0]["ticket_id"] == 4242
    assert parsed["pending"][0]["last_comment_author"] == "Luigi Verdi"


@pytest.mark.asyncio
async def test_tickets_needing_my_reply_no_user():
    connector = _connector({"users.json": REDMINE_USERS})
    parsed = _load(await connector.get_tickets_needing_my_reply("ghost"))
    assert parsed["pending"] == []
    assert "No user matching" in parsed["message"]
