"""Per-profile Redmine tests: the version strategy selects the right
endpoint and field names, and the MCP contract (JSON shape) is identical
across profiles.

Profiles 4.2 / 5.x / 6.x use the legacy naming (trackers); 7.x uses issue
types. The tests are parametrised over the four profiles.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from mcp_ticketing.redmine import RedmineConnector
from mcp_ticketing.redmine.p6x import Profile6x
from mcp_ticketing.redmine.p7x import Profile7x
from mcp_ticketing.redmine.p42 import Profile42
from mcp_ticketing.redmine.p51 import Profile51

TRACKERS = {"trackers": [{"id": 1, "name": "Task"}, {"id": 2, "name": "Bug"}]}
ISSUE_TYPES = {"issue_types": [{"id": 1, "name": "Task"}, {"id": 2, "name": "Bug"}]}
PROJECTS = {"projects": [{"id": 3, "identifier": "openextras", "name": "opencode-extras"}]}
VERSIONS = {"versions": [{"id": 9, "name": "v1.2"}, {"id": 10, "name": "v2.0"}]}
PRIORITIES = {
    "issues": [
        {"id": 1, "priority": {"id": 1, "name": "Urgent"}},
        {"id": 2, "priority": {"id": 2, "name": "High"}},
        {"id": 3, "priority": {"id": 3, "name": "Normal"}},
        {"id": 4, "priority": {"id": 4, "name": "Low"}},
    ]
}

PROFILES = [
    pytest.param(Profile42(), id="4.x"),
    pytest.param(Profile51(), id="5.x"),
    pytest.param(Profile6x(), id="6.x"),
    pytest.param(Profile7x(), id="7.x"),
]


def _make_client(responses: dict, post=None, put=None, base_url: str = "http://redmine.test"):
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
            f"404 for {path}", request=httpx.Request("GET", f"{base_url}/{path}"), response=resp
        )

    async def _get_with_headers(path: str, *args, **kwargs):
        return await _get(path, *args, **kwargs), {}

    client.get.side_effect = _get
    client.get_with_headers.side_effect = _get_with_headers
    if post is not None:
        client.post = AsyncMock(return_value=post)
    if put is not None:
        client.put = AsyncMock(return_value=put)
    return client


def connector(responses: dict, profile, post=None, put=None) -> RedmineConnector:
    return RedmineConnector(client=_make_client(responses, post=post, put=put), profile=profile)


def load(resp: str) -> dict:
    return json.loads(resp)


def _endpoint(profile) -> str:
    return profile.issue_type_endpoint()


def _payload(profile) -> dict:
    return TRACKERS if _endpoint(profile) == "trackers.json" else ISSUE_TYPES


def _type_id_field(profile) -> str:
    return "tracker_id" if _endpoint(profile) == "trackers.json" else "issue_type_id"


# ──────────────────────────────────────────────
#  get_ticket_types — endpoint per profile
# ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", PROFILES)
async def test_get_ticket_types_endpoint(profile):
    c = connector({_endpoint(profile): _payload(profile)}, profile)
    parsed = load(await c.get_ticket_types())
    assert parsed["total"] == 2
    assert [t["name"] for t in parsed["ticket_types"]] == ["Task", "Bug"]
    assert _endpoint(profile) in [call.args[0] for call in c.client.get.call_args_list]


# ──────────────────────────────────────────────
#  create_ticket — type field name per profile
# ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", PROFILES)
async def test_create_ticket_type_field(profile):
    c = connector(
        {
            "projects.json": PROJECTS,
            _endpoint(profile): _payload(profile),
            "projects/3/versions.json": VERSIONS,
            "issues.json": PRIORITIES,
        },
        profile,
        post={"issue": {"id": 500}},
    )
    parsed = load(await c.create_ticket(ticket_type="bug", title="t", priority=2, iteration_path="v1.2"))
    assert parsed["success"] is True
    issue = c.client.post.call_args.kwargs["data"]["issue"]
    assert issue[_type_id_field(profile)] == 2  # tracker_id (4.2-6.x) / issue_type_id (7.x)
    assert issue["fixed_version_id"] == 9  # write field is always fixed_version_id
    assert issue["priority_id"] == 2  # "High" -> high


# ──────────────────────────────────────────────
#  search_tickets — type filter field per profile
# ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", PROFILES)
async def test_search_tickets_type_field(profile):
    c = connector(
        {
            "projects.json": PROJECTS,
            _endpoint(profile): _payload(profile),
            "issues.json": {"issues": [], "total_count": 0},
        },
        profile,
    )
    parsed = load(await c.search_tickets(ticket_type="bug", query="payee"))
    assert parsed["results"] == []
    (call,) = [x for x in c.client.get.call_args_list if x.args and x.args[0] == "issues.json"]
    params = call.kwargs["params"]
    assert params[_type_id_field(profile)] == 2
    assert params["subject"] == "~payee"


# ──────────────────────────────────────────────
#  Contract: identical JSON shape across profiles
# ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", PROFILES)
async def test_contract_get_ticket_types_shape(profile):
    c = connector({_endpoint(profile): _payload(profile)}, profile)
    parsed = load(await c.get_ticket_types())
    assert set(parsed) == {"ticket_types", "total"}
    assert set(parsed["ticket_types"][0]) == {"id", "name", "description"}


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", PROFILES)
async def test_contract_create_shape(profile):
    c = connector(
        {"projects.json": PROJECTS, _endpoint(profile): _payload(profile)},
        profile,
        post={"issue": {"id": 500, "subject": "t"}},
    )
    parsed = load(await c.create_ticket(title="t"))
    assert set(parsed) == {"success", "id", "title", "type", "url", "message"}


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", PROFILES)
async def test_contract_search_shape(profile):
    legacy = _endpoint(profile) == "trackers.json"
    issue = {
        "id": 1,
        "subject": "s",
        "status": {"id": 1, "name": "New"},
        "priority": {"id": 3, "name": "Normale"},
    }
    issue["tracker" if legacy else "issue_type"] = {"id": 2, "name": "Bug"}
    issue["fixed_version" if legacy else "version"] = {"id": 9, "name": "v1.2"}
    c = connector(
        {
            "projects.json": PROJECTS,
            _endpoint(profile): _payload(profile),
            "issues.json": {"issues": [issue], "total_count": 1},
        },
        profile,
    )
    parsed = load(await c.search_tickets())
    assert set(parsed) == {"results", "total", "truncated"}
    assert parsed["results"][0]["type"] == "Bug"
    assert parsed["results"][0]["iteration_path"] == "v1.2"
