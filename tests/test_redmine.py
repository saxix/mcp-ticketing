"""Offline tests for the Redmine connector (no network).

Real-API integration tests skip automatically when the Redmine environment
variables are not set. The tests here mock the HTTP client to cover the
projection shape, the create/update payload building, state and assignee
resolution, and the search parameter mapping.

Version-specific behaviour (trackers vs issue_types, fixed_version vs
version) is covered by the per-profile test modules (``test_redmine_v*.py``);
the helpers that no longer depend on a version (``format_ticket`` and
``strip_wiki``) are tested here against both legacy and modern payloads.
"""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from mcp_ticketing.redmine import RedmineConnector
from mcp_ticketing.redmine.base import strip_wiki
from mcp_ticketing.redmine.connector import format_ticket

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
# Priorities on the 1-4 scale, so the protocol scale maps 1:1 to ids.
REDMINE_PRIORITIES = {
    "issues": [
        {"id": 1, "priority": {"id": 1, "name": "Urgent"}},
        {"id": 2, "priority": {"id": 2, "name": "High"}},
        {"id": 3, "priority": {"id": 3, "name": "Normal"}},
        {"id": 4, "priority": {"id": 4, "name": "Low"}},
    ]
}

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
    """Point the Redmine connector at a fake instance for offline tests.

    ``MCP_REDMINE_VERSION`` is unset (latest-profile path); the per-test
    connector is always built with an explicit profile (see
    ``_connector``), which skips the selection step.
    """
    monkeypatch.setenv("MCP_REDMINE_URL", "http://redmine.test")
    monkeypatch.setenv("MCP_REDMINE_API_KEY", "test-key")
    monkeypatch.setenv("MCP_REDMINE_PROJECT", "openextras")
    monkeypatch.delenv("MCP_REDMINE_VERSION", raising=False)
    yield


def _make_client(responses: dict, post=None, put=None, base_url: str = "http://redmine.test"):
    """Build a mock RedmineClient returning canned JSON per path.

    ``responses`` maps a path (or path prefix) to the JSON the GET should
    return. Unknown paths raise a 404 ``HTTPStatusError``. The mock also
    exposes the client attributes the connector reads.
    """
    client = AsyncMock()
    client.base_url = base_url
    client.api_key = "test-key"
    client.project = "openextras"
    client.verify = True

    async def _get(path: str, *args, **kwargs):
        for prefix, payload in responses.items():
            if path.startswith(prefix):
                return payload
        resp = httpx.Response(404, json={"status": 404, "error": "Not Found"})
        raise httpx.HTTPStatusError(
            f"404 for {path}",
            request=httpx.Request("GET", f"{base_url}/{path}"),
            response=resp,
        )

    client.get.side_effect = _get
    if post is not None:
        client.post = AsyncMock(return_value=post)
    if put is not None:
        client.put = AsyncMock(return_value=put)
    return client


def _connector(
    responses: dict, profile, post=None, put=None, base_url: str = "http://redmine.test"
) -> RedmineConnector:
    """Build a RedmineConnector bound to ``profile`` (no startup probe)."""
    client = _make_client(responses, post=post, put=put, base_url=base_url)
    return RedmineConnector(client=client, profile=profile)


def _load(resp: str) -> dict:
    return json.loads(resp)


# ──────────────────────────────────────────────
#  format_ticket — projection shape
# ──────────────────────────────────────────────


def test_redmine_format_ticket_omits_description_by_default():
    parsed = _load(format_ticket(REDMINE_ISSUE))
    assert "description" not in parsed
    assert set(parsed) == BASE_KEYS


def test_redmine_format_ticket_includes_description_on_request():
    parsed = _load(format_ticket(REDMINE_ISSUE, include_description=True))
    assert set(parsed) == BASE_KEYS | {"description"}
    assert parsed["description"] == REDMINE_ISSUE["description"]


def test_redmine_format_ticket_fields():
    parsed = _load(format_ticket(REDMINE_ISSUE, include_description=True))
    assert parsed["id"] == 4242
    assert parsed["title"] == "Configurable Payee Type"
    assert parsed["type"] == "Bug"
    assert parsed["state"] == "New"
    assert parsed["assigned_to"] == "Mario Rossi"
    assert parsed["iteration_path"] == "v1.2"
    assert parsed["priority"] == "Normal"


def test_redmine_format_ticket_modern_fields():
    """New Redmine uses 'issue_type' and 'version' instead of 'tracker'/'fixed_version'."""
    parsed = _load(format_ticket(REDMINE_ISSUE_NEW, include_description=True))
    assert parsed["type"] == "Bug"
    assert parsed["iteration_path"] == "v1.2"
    assert parsed["state"] == "New"


def test_redmine_format_ticket_unassigned_defaults():
    parsed = _load(format_ticket({"id": 1, "subject": "x"}, include_description=True))
    assert parsed["assigned_to"] == "Unassigned"
    assert parsed["description"] == ""


# ──────────────────────────────────────────────
#  strip_wiki — wiki markup to plain text
# ──────────────────────────────────────────────


def test_strip_wiki_plain_text_unchanged():
    assert strip_wiki("First note") == "First note"
    assert strip_wiki("  spaced  ") == "spaced"


def test_strip_wiki_headings():
    assert strip_wiki("h1. Title\nh2. Sub") == "Title\nSub"


def test_strip_wiki_emphasis():
    assert strip_wiki("**bold** and *italic*") == "bold and italic"
    assert strip_wiki("__bold__ and _italic_") == "bold and italic"


def test_strip_wiki_lists():
    assert strip_wiki("* a\n* b\n# 1\n# 2") == "a\nb\n1\n2"


def test_strip_wiki_table():
    assert strip_wiki("| a | b |\n| 1 | 2 |") == "a\tb\n1\t2"


def test_strip_wiki_html():
    assert strip_wiki("<p>Hello <b>world</b></p>") == "Hello world"
    assert strip_wiki("a<br>b<br/>c") == "a\nb\nc"


def test_strip_wiki_code_block():
    assert strip_wiki("{{ code }}") == "code"


def test_strip_wiki_preserves_ident_underscores():
    assert strip_wiki("STATO_DISTINTA e TIPO_DISTINTA") == "STATO_DISTINTA e TIPO_DISTINTA"


# ──────────────────────────────────────────────
#  get_ticket / comments
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_ticket_returns_description():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector({"issues/4242.json": {"issue": REDMINE_ISSUE}}, Profile6x())
    parsed = _load(await connector.get_ticket(4242))
    assert parsed["id"] == 4242
    assert "Payee type must be configurable." in parsed["description"]


@pytest.mark.asyncio
async def test_get_ticket_not_found_returns_error():
    from mcp_ticketing.redmine.p6x import Profile6x

    client = AsyncMock()

    def _raise(*args, **kwargs):
        request = httpx.Request("GET", "http://redmine.test/issues/999999999.json")
        raise httpx.HTTPStatusError("not found", request=request, response=httpx.Response(404, request=request))

    client.get.side_effect = _raise
    connector = RedmineConnector(client=client, profile=Profile6x())
    parsed = _load(await connector.get_ticket(999999999))
    assert "error" in parsed


@pytest.mark.asyncio
async def test_get_ticket_comments_only_notes():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector({"issues/4242.json": {"issue": REDMINE_ISSUE}}, Profile6x())
    parsed = _load(await connector.get_ticket_comments(4242))
    assert parsed["ticket_id"] == 4242
    assert parsed["total"] == 2
    assert [c["text"] for c in parsed["comments"]] == ["First note", "Second note"]


@pytest.mark.asyncio
async def test_get_ticket_comments_sends_include_journals():
    """The request must pass include=journals, else Redmine omits the journals."""
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector({"issues/4242.json": {"issue": REDMINE_ISSUE}}, Profile6x())
    await connector.get_ticket_comments(4242)
    (call,) = [c for c in connector.client.get.call_args_list if c.args and c.args[0].startswith("issues/")]
    assert call.kwargs["params"] == {"include": ["journals"]}


# ──────────────────────────────────────────────
#  types / check / area paths (profile-aware)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_reports_profile():
    from mcp_ticketing.redmine.p6x import Profile6x

    # check() confirms connectivity via projects.json only (never
    # my_account.json, which is restricted on some 4.x deployments).
    connector = _connector({"projects.json": {"projects": []}}, Profile6x())
    parsed = _load(await connector.check())
    assert parsed["status"] == "ok"
    assert parsed["backend"] == "redmine"
    assert parsed["profile"] == "6.x"
    assert "user" not in parsed


@pytest.mark.asyncio
async def test_get_ticket_types_uses_profile_endpoint():
    from mcp_ticketing.redmine.p6x import Profile6x
    from mcp_ticketing.redmine.p7x import Profile7x

    # 6.x -> trackers.json
    c6 = _connector({"trackers.json": REDMINE_TRACKERS}, Profile6x())
    p6 = _load(await c6.get_ticket_types())
    assert p6["total"] == 2
    # 7.x -> issue_types.json
    c7 = _connector({"issue_types.json": REDMINE_TYPES}, Profile7x())
    p7 = _load(await c7.get_ticket_types())
    assert p7["total"] == 2


@pytest.mark.asyncio
async def test_get_area_paths_uses_profile_endpoint():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector({"trackers.json": REDMINE_TRACKERS}, Profile6x())
    parsed = _load(await connector.get_area_paths())
    assert parsed["total"] == 2
    assert parsed["area_paths"][0]["name"] == "Task"


# ──────────────────────────────────────────────
#  create_ticket — payload building (6.x -> tracker_id)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_ticket_payload():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "trackers.json": REDMINE_TRACKERS,
            "users.json": REDMINE_USERS,
            "projects/3/versions.json": REDMINE_VERSIONS,
            "issues.json": REDMINE_PRIORITIES,
        },
        Profile6x(),
        post={"issue": {"id": 500, "tracker": {"name": "Bug"}}},
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
    assert issue["tracker_id"] == 2  # case-insensitive, 6.x field name
    assert issue["assigned_to_id"] == 7
    assert issue["priority_id"] == 2  # High
    assert issue["fixed_version_id"] == 9
    assert "<h2>Problem</h2>" in issue["description"]  # markdown converted to HTML


@pytest.mark.asyncio
async def test_create_ticket_requires_title():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector({}, Profile6x())
    parsed = _load(await connector.create_ticket(ticket_type="Task"))
    assert "error" in parsed
    assert "title" in parsed["error"]


@pytest.mark.asyncio
async def test_create_ticket_unknown_type_errors():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "trackers.json": REDMINE_TRACKERS,
        },
        Profile6x(),
    )
    parsed = _load(await connector.create_ticket(ticket_type="Epic", title="x"))
    assert "error" in parsed
    assert "Epic" in parsed["error"]


@pytest.mark.asyncio
async def test_create_ticket_unknown_assignee_errors():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector({"projects.json": REDMINE_PROJECTS, "users.json": REDMINE_USERS}, Profile6x())
    parsed = _load(await connector.create_ticket(title="x", assigned_to="nobody"))
    assert "error" in parsed
    assert "nobody" in parsed["error"]


# ──────────────────────────────────────────────
#  update_ticket
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_ticket_no_fields():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector({}, Profile6x())
    parsed = _load(await connector.update_ticket(ticket_id=1))
    assert "error" in parsed
    assert "No fields to update" in parsed["error"]


@pytest.mark.asyncio
async def test_update_ticket_state_and_priority():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "issues_statuses.json": REDMINE_STATUSES,
            "issues.json": REDMINE_PRIORITIES,
        },
        Profile6x(),
        put={"issue": {"id": 1}},
    )
    parsed = _load(await connector.update_ticket(ticket_id=1, state="closed", priority=1))
    assert parsed["success"] is True
    payload = connector.client.put.call_args.kwargs["data"]
    assert payload["issue"]["status_id"] == 5  # 'closed' -> 'Closed'
    assert payload["issue"]["priority_id"] == 1  # Urgent


@pytest.mark.asyncio
async def test_update_ticket_direct_status_name():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector({"issues_statuses.json": REDMINE_STATUSES}, Profile6x(), put={"issue": {"id": 1}})
    parsed = _load(await connector.update_ticket(ticket_id=1, state="In progress"))
    assert parsed["success"] is True
    payload = connector.client.put.call_args.kwargs["data"]
    assert payload["issue"]["status_id"] == 2


@pytest.mark.asyncio
async def test_update_ticket_unknown_state_errors():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector({"issues_statuses.json": REDMINE_STATUSES}, Profile6x())
    parsed = _load(await connector.update_ticket(ticket_id=1, state="bogus-state"))
    assert "error" in parsed
    assert "bogus-state" in parsed["error"]


# ──────────────────────────────────────────────
#  search_tickets — parameter mapping
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_tickets_builds_filters():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "trackers.json": REDMINE_TRACKERS,
            "issues_statuses.json": REDMINE_STATUSES,
            "users.json": REDMINE_USERS,
            "issues.json": {"issues": [], "total_count": 0},
        },
        Profile6x(),
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
    assert params["subject"] == "~payee"  # query -> subject wildcard (portable)
    assert params["tracker_id"] == 2
    assert params["status_id"] == 1
    assert params["assigned_to_id"] == 7
    assert params["limit"] == 50


@pytest.mark.asyncio
async def test_search_tickets_omits_description():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "issues.json": {"issues": [REDMINE_ISSUE], "total_count": 1},
        },
        Profile6x(),
    )
    parsed = _load(await connector.search_tickets(query="payee"))
    assert parsed["total"] == 1
    assert "description" not in parsed["results"][0]


# ──────────────────────────────────────────────
#  links / iterations
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_link_tickets_relation_payload():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector({}, Profile6x(), put={})
    parsed = _load(await connector.link_tickets(1, 2, link_type="blocks", comment="wait"))
    assert parsed["success"] is True
    payload = connector.client.put.call_args.kwargs["data"]
    assert payload["issue"]["relations"] == [{"to_issue_id": 2, "relation_type": 2}]
    assert payload["issue"]["notes"] == "wait"


@pytest.mark.asyncio
async def test_link_tickets_unknown_type_errors():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector({}, Profile6x())
    parsed = _load(await connector.link_tickets(1, 2, link_type="nope"))
    assert "error" in parsed


@pytest.mark.asyncio
async def test_get_ticket_relations():
    from mcp_ticketing.redmine.p6x import Profile6x

    issue = dict(REDMINE_ISSUE)
    issue["relations"] = [
        {"relation_type": 1, "delay": 0, "issue": {"id": 10, "subject": "Other"}},
    ]
    connector = _connector({"issues/4242.json": {"issue": issue}}, Profile6x())
    parsed = _load(await connector.get_ticket_relations(4242))
    assert parsed["total"] == 1
    assert parsed["relations"][0]["target_id"] == 10


@pytest.mark.asyncio
async def test_get_iterations_maps_versions():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector(
        {"projects.json": REDMINE_PROJECTS, "projects/3/versions.json": REDMINE_VERSIONS}, Profile6x()
    )
    parsed = _load(await connector.get_iterations())
    assert parsed["total"] == 2
    assert parsed["iterations"][0]["name"] == "v1.2"


@pytest.mark.asyncio
async def test_move_to_iteration_payload():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector(
        {"projects.json": REDMINE_PROJECTS, "projects/3/versions.json": REDMINE_VERSIONS},
        Profile6x(),
        put={},
    )
    parsed = _load(await connector.move_to_iteration(1, "v2.0"))
    assert parsed["success"] is True
    payload = connector.client.put.call_args.kwargs["data"]
    assert payload["issue"]["fixed_version_id"] == 10


@pytest.mark.asyncio
async def test_move_to_iteration_unknown_version_errors():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector(
        {"projects.json": REDMINE_PROJECTS, "projects/3/versions.json": REDMINE_VERSIONS},
        Profile6x(),
    )
    parsed = _load(await connector.move_to_iteration(1, "v9.9"))
    assert "error" in parsed
    assert "v9.9" in parsed["error"]


# ──────────────────────────────────────────────
#  needing reply
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tickets_needing_my_reply():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector(
        {
            "users.json": REDMINE_USERS,
            "projects.json": REDMINE_PROJECTS,
            "issues.json": {"issues": [REDMINE_ISSUE], "total_count": 1},
        },
        Profile6x(),
    )
    parsed = _load(await connector.get_tickets_needing_my_reply("Mario Rossi"))
    assert parsed["total"] == 1
    assert parsed["pending"][0]["ticket_id"] == 4242
    assert parsed["pending"][0]["last_comment_author"] == "Luigi Verdi"
    # Regression: the request must request journals, else they are omitted.
    (call,) = [c for c in connector.client.get.call_args_list if c.args and c.args[0] == "issues.json"]
    assert call.kwargs["params"]["include"] == ["journals"]


@pytest.mark.asyncio
async def test_tickets_needing_my_reply_no_user():
    from mcp_ticketing.redmine.p6x import Profile6x

    connector = _connector({"users.json": REDMINE_USERS}, Profile6x())
    parsed = _load(await connector.get_tickets_needing_my_reply("ghost"))
    assert parsed["pending"] == []
    assert "No user matching" in parsed["message"]
