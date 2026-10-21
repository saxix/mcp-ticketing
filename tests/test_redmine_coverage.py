"""Gap-closure tests for ``redmine/connector.py``.

``test_redmine.py`` and ``test_redmine_profiles.py`` cover the happy paths of
every tool. This module closes the remaining coverage gaps: the error /
``httpx.HTTPStatusError`` branches of each tool, the resolution fallbacks
(project by numeric id, assignee when ``users.json`` is 403, statuses when
``issues_statuses.json`` is 404, project versions when the project has no
id), the attachment and Mermaid tools (which use a real ``httpx.AsyncClient``
for the upload, mocked at the transport level with ``respx``), and the
remaining payload branches of ``create_ticket`` / ``update_ticket`` /
``search_tickets``.

The HTTP client is mocked with ``AsyncMock`` (same convention as
``test_redmine.py``); only the attachment upload, which builds its own
``httpx.AsyncClient``, is intercepted with ``respx``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from mcp_ticketing.redmine import RedmineConnector
from mcp_ticketing.redmine.p6x import Profile6x
from mcp_ticketing.redmine.p7x import Profile7x

pytestmark = pytest.mark.asyncio

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image"

REDMINE_PROJECTS = {"projects": [{"id": 3, "identifier": "openextras", "name": "opencode-extras"}]}
REDMINE_PROJECT_BY_ID = {"project": {"id": "3", "identifier": "openextras", "name": "opencode-extras"}}
REDMINE_TRACKERS = {"trackers": [{"id": 1, "name": "Task"}, {"id": 2, "name": "Bug"}]}
REDMINE_TYPES = {"issue_types": [{"id": 1, "name": "Task"}, {"id": 2, "name": "Bug"}]}
REDMINE_STATUSES = {
    "issues_statuses": [
        {"id": 1, "name": "New", "is_closed": False},
        {"id": 2, "name": "In progress", "is_closed": False},
        {"id": 5, "name": "Closed", "is_closed": True},
    ]
}
REDMINE_USERS = {"users": [{"id": 7, "login": "mario", "name": "Mario Rossi"}]}
REDMINE_VERSIONS = {"versions": [{"id": 9, "name": "v1.2"}, {"id": 10, "name": "v2.0"}]}
REDMINE_PRIORITIES = {
    "issues": [
        {"id": 1, "priority": {"id": 1, "name": "Urgent"}},
        {"id": 2, "priority": {"id": 2, "name": "High"}},
        {"id": 3, "priority": {"id": 3, "name": "Normal"}},
        {"id": 4, "priority": {"id": 4, "name": "Low"}},
    ]
}


def _load(resp: str) -> dict:
    return json.loads(resp)


def _make_client(responses: dict, post=None, put=None, base_url: str = "http://redmine.test"):
    """Mock RedmineClient returning canned JSON per path (see test_redmine.py)."""
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


def _connector(responses: dict, profile=None, post=None, put=None, project: str = "openextras") -> RedmineConnector:
    client = _make_client(responses, post=post, put=put)
    client.project = project
    return RedmineConnector(client=client, profile=profile or Profile6x())


def _client_404() -> AsyncMock:
    """A client whose GET always 404s (for error-branch tests)."""
    client = AsyncMock()
    client.base_url = "http://redmine.test"
    client.api_key = "test-key"
    client.project = "openextras"
    client.verify = True

    async def _get(path: str, *args, **kwargs):
        resp = httpx.Response(404, json={"status": 404, "error": "Not Found"})
        raise httpx.HTTPStatusError(
            f"404 for {path}",
            request=httpx.Request("GET", f"http://redmine.test/{path}"),
            response=resp,
        )

    client.get.side_effect = _get
    return client


# ──────────────────────────────────────────────
#  Project resolution
# ──────────────────────────────────────────────


async def test_resolve_project_by_numeric_id():
    connector = _connector({"projects/3.json": REDMINE_PROJECT_BY_ID}, project="3")
    project = await connector._resolve_project()
    assert project["identifier"] == "openextras"
    # Cached on the second call (no extra request).
    assert connector._project is not None
    await connector._resolve_project()


async def test_resolve_project_not_found_by_identifier():
    connector = _connector({"projects.json": {"projects": []}})
    with pytest.raises(ValueError):
        await connector._resolve_project()


async def test_safe_resolve_project_swallows_http_error():
    connector = RedmineConnector(client=_client_404(), profile=Profile6x())
    assert await connector._safe_resolve_project() is None


# ──────────────────────────────────────────────
#  Status resolution (fallbacks)
# ──────────────────────────────────────────────


async def test_get_statuses_uses_endpoint():
    connector = _connector({"issues_statuses.json": REDMINE_STATUSES})
    statuses = await connector._get_statuses()
    assert [s["name"] for s in statuses] == ["New", "In progress", "Closed"]


async def test_get_statuses_fallback_from_issues():
    """issues_statuses.json 404 -> statuses discovered from a sample of issues."""
    client = _make_client(
        {
            "projects.json": REDMINE_PROJECTS,
            "issues.json": {
                "issues": [
                    {"status": {"id": 1, "name": "New"}},
                    {"status": {"id": 5, "name": "Closed"}},
                    {"status": {"id": 5, "name": "Closed"}},  # duplicate
                    {"status": {}},  # missing id -> ignored
                ]
            },
        }
    )
    # Make the statuses endpoint 404 so the fallback kicks in.
    connector = RedmineConnector(client=client, profile=Profile6x())
    statuses = await connector._get_statuses()
    assert {s["id"] for s in statuses} == {1, 5}


async def test_get_statuses_fallback_no_project():
    """issues_statuses.json 404 and project unresolvable -> empty list."""
    client = _client_404()
    connector = RedmineConnector(client=client, profile=Profile6x())
    assert await connector._get_statuses() == []


async def test_safe_resolve_status_synonym():
    """A generic word resolves through STATE_TO_STATUSES when the exact name is absent."""
    connector = _connector({"issues_statuses.json": REDMINE_STATUSES})
    # 'closed' is not a literal status name here, but a synonym of 'Closed'.
    assert await connector._safe_resolve_status("closed") == 5


async def test_safe_resolve_status_not_found():
    connector = _connector({"issues_statuses.json": REDMINE_STATUSES})
    assert await connector._safe_resolve_status("bogus") is None


async def test_safe_resolve_status_swallows_http_error():
    connector = RedmineConnector(client=_client_404(), profile=Profile6x())
    assert await connector._safe_resolve_status("open") is None


# ──────────────────────────────────────────────
#  Assignee resolution (fallbacks)
# ──────────────────────────────────────────────


async def test_resolve_assignee_by_name():
    connector = _connector({"users.json": REDMINE_USERS})
    assert await connector._resolve_assignee("Mario Rossi") == 7


async def test_resolve_assignee_by_login():
    connector = _connector({"users.json": REDMINE_USERS})
    assert await connector._resolve_assignee("mario") == 7


async def test_resolve_assignee_not_found():
    connector = _connector({"users.json": REDMINE_USERS})
    assert await connector._resolve_assignee("nobody") is None


async def test_resolve_assignee_fallback_from_issues():
    """users.json 403 -> the user is discovered from issue author/assigned_to."""
    client = _make_client(
        {
            "projects.json": REDMINE_PROJECTS,
            "issues.json": {
                "issues": [
                    {
                        "author": {"id": 9, "name": "Luigi Verdi", "login": "luigi"},
                        "assigned_to": {"id": 7, "name": "Mario Rossi", "login": "mario"},
                    }
                ]
            },
        }
    )
    default_get = client.get.side_effect  # capture the canned-response closure

    # The fallback path: users.json 403s, then issues.json is re-fetched to
    # discover the user from author/assigned_to.
    async def _get(path: str, *args, **kwargs):
        if path.startswith("users.json"):
            resp = httpx.Response(403, text="forbidden")
            raise httpx.HTTPStatusError("forbidden", request=httpx.Request("GET", "u"), response=resp)
        return await default_get(path, *args, **kwargs)

    client.get.side_effect = _get
    connector = RedmineConnector(client=client, profile=Profile6x())
    assert await connector._resolve_assignee("luigi") == 9
    assert await connector._resolve_assignee("Mario Rossi") == 7
    assert await connector._resolve_assignee("ghost") is None


async def test_safe_resolve_assignee_swallows_http_error():
    connector = RedmineConnector(client=_client_404(), profile=Profile6x())
    assert await connector._safe_resolve_assignee("mario") is None


# ──────────────────────────────────────────────
#  Issue-type resolution
# ──────────────────────────────────────────────


async def test_safe_resolve_issue_type_not_found():
    connector = _connector({"trackers.json": REDMINE_TRACKERS})
    assert await connector._safe_resolve_issue_type("Epic") is None


async def test_safe_resolve_issue_type_swallows_http_error():
    connector = RedmineConnector(client=_client_404(), profile=Profile6x())
    assert await connector._safe_resolve_issue_type("Bug") is None


# ──────────────────────────────────────────────
#  Version resolution
# ──────────────────────────────────────────────


async def test_safe_resolve_version_id_found():
    connector = _connector({"projects.json": REDMINE_PROJECTS, "projects/3/versions.json": REDMINE_VERSIONS})
    assert await connector._safe_resolve_version_id("v1.2") == 9


async def test_safe_resolve_version_id_not_found():
    connector = _connector({"projects.json": REDMINE_PROJECTS, "projects/3/versions.json": REDMINE_VERSIONS})
    assert await connector._safe_resolve_version_id("v9.9") is None


async def test_safe_resolve_version_id_swallows_http_error():
    connector = RedmineConnector(client=_client_404(), profile=Profile6x())
    assert await connector._safe_resolve_version_id("v1.2") is None


# ──────────────────────────────────────────────
#  Priority resolution
# ──────────────────────────────────────────────


async def test_resolve_priority_zero_or_negative():
    connector = _connector({"issues.json": REDMINE_PRIORITIES})
    assert await connector._resolve_priority(0) is None
    assert await connector._resolve_priority(-1) is None


# ──────────────────────────────────────────────
#  get_ticket_types / get_area_paths / check — error branches
# ──────────────────────────────────────────────


async def test_get_ticket_types_error():
    connector = RedmineConnector(client=_client_404(), profile=Profile6x())
    parsed = _load(await connector.get_ticket_types())
    assert parsed["error"].startswith("HTTP 404")


async def test_get_area_paths_error():
    connector = RedmineConnector(client=_client_404(), profile=Profile6x())
    parsed = _load(await connector.get_area_paths())
    assert parsed["error"].startswith("HTTP 404")


async def test_check_error():
    connector = RedmineConnector(client=_client_404(), profile=Profile6x())
    parsed = _load(await connector.check())
    assert parsed["error"].startswith("HTTP 404")


# ──────────────────────────────────────────────
#  add_comment — success with journals + error
# ──────────────────────────────────────────────


async def test_add_comment_success():
    connector = _connector({}, put={"issue": {"journals": [{"id": 99}]}})
    parsed = _load(await connector.add_comment(5, "a note"))
    assert parsed["success"] is True
    assert parsed["comment_id"] == 99


async def test_add_comment_no_journals():
    connector = _connector({}, put={"issue": {}})
    parsed = _load(await connector.add_comment(5, "a note"))
    assert parsed["success"] is True
    assert parsed["comment_id"] is None


async def test_add_comment_error():
    client = _client_404()
    client.put = AsyncMock(side_effect=_http_error(500))
    connector = RedmineConnector(client=client, profile=Profile6x())
    parsed = _load(await connector.add_comment(5, "a note"))
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  add_mermaid — empty / render failure / upload / patch / success
# ──────────────────────────────────────────────


async def test_add_mermaid_empty_diagram():
    connector = _connector({})
    parsed = _load(await connector.add_mermaid(5, "   "))
    assert parsed["error"] == "diagram is required."


async def test_add_mermaid_render_failure(monkeypatch):
    async def boom(_func, *args, **kwargs):
        raise RuntimeError("render exploded")

    monkeypatch.setattr("mcp_ticketing.redmine.connector.asyncio.to_thread", boom)
    connector = _connector({})
    parsed = _load(await connector.add_mermaid(5, "flowchart TD"))
    assert "Failed to render Mermaid" in parsed["error"]


@respx.mock
async def test_add_mermaid_upload_error(monkeypatch):
    monkeypatch.setattr("mcp_ticketing.redmine.base.render_mermaid_png", lambda _diagram, _scale=2.0: b"PNG")
    respx.post("http://redmine.test/attachments.json").mock(return_value=httpx.Response(500, text="boom"))
    connector = _connector({})
    parsed = _load(await connector.add_mermaid(5, "flowchart TD"))
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_add_mermaid_success(monkeypatch):
    monkeypatch.setattr("mcp_ticketing.redmine.base.render_mermaid_png", lambda _diagram, _scale=2.0: b"PNG")
    respx.post("http://redmine.test/attachments.json").mock(
        return_value=httpx.Response(200, json={"attachment": {"id": 77}})
    )
    connector = _connector({"issues/5.json": {"issue": {"description": "<p>old</p>"}}}, put={"issue": {}})
    parsed = _load(await connector.add_mermaid(5, "flowchart TD\n  A-->B", title="Flow"))
    assert parsed["success"] is True
    assert parsed["attachment_id"] == 77
    assert parsed["url"].endswith("/attachments/download/77")
    payload = connector.client.put.call_args.kwargs["data"]
    assert "<img" in payload["issue"]["description"]
    assert 'alt="Flow"' in payload["issue"]["description"]
    assert payload["issue"]["attachment_ids"] == [77]


@respx.mock
async def test_add_mermaid_patch_error(monkeypatch):
    monkeypatch.setattr("mcp_ticketing.redmine.base.render_mermaid_png", lambda _diagram, _scale=2.0: b"PNG")
    respx.post("http://redmine.test/attachments.json").mock(
        return_value=httpx.Response(200, json={"attachment": {"id": 77}})
    )
    client = _make_client({"issues/5.json": {"issue": {"description": ""}}})
    client.put = AsyncMock(side_effect=_http_error(400))
    connector = RedmineConnector(client=client, profile=Profile6x())
    parsed = _load(await connector.add_mermaid(5, "flowchart TD"))
    assert parsed["error"].startswith("HTTP 400")


# ──────────────────────────────────────────────
#  add_ticket_image — invalid / read failure / upload / patch / success
# ──────────────────────────────────────────────


async def test_add_ticket_image_invalid_path():
    connector = _connector({})
    parsed = _load(await connector.add_ticket_image(5, "/nonexistent/file.png"))
    assert "not a readable image file" in parsed["error"]


@respx.mock
async def test_add_ticket_image_read_failure(tmp_path, monkeypatch):
    image = tmp_path / "shot.png"
    image.write_bytes(PNG_BYTES)
    monkeypatch.setattr("mcp_ticketing.redmine.connector.read_image_file", lambda _path: None)
    connector = _connector({})
    parsed = _load(await connector.add_ticket_image(5, str(image)))
    assert "Could not read" in parsed["error"]


@respx.mock
async def test_add_ticket_image_upload_error(tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(PNG_BYTES)
    respx.post("http://redmine.test/attachments.json").mock(return_value=httpx.Response(500, text="boom"))
    connector = _connector({})
    parsed = _load(await connector.add_ticket_image(5, str(image)))
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_add_ticket_image_success(tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(PNG_BYTES)
    respx.post("http://redmine.test/attachments.json").mock(
        return_value=httpx.Response(200, json={"attachment": {"id": 88}})
    )
    connector = _connector({}, put={"issue": {}})
    parsed = _load(await connector.add_ticket_image(5, str(image), comment="label"))
    assert parsed["success"] is True
    assert parsed["attachment_id"] == 88
    payload = connector.client.put.call_args.kwargs["data"]
    assert payload["issue"]["notes"] == "label"
    assert payload["issue"]["attachment_ids"] == [88]


@respx.mock
async def test_add_ticket_image_patch_error(tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(PNG_BYTES)
    respx.post("http://redmine.test/attachments.json").mock(
        return_value=httpx.Response(200, json={"attachment": {"id": 88}})
    )
    client = _make_client({})
    client.put = AsyncMock(side_effect=_http_error(400))
    connector = RedmineConnector(client=client, profile=Profile6x())
    parsed = _load(await connector.add_ticket_image(5, str(image)))
    assert parsed["error"].startswith("HTTP 400")


# ──────────────────────────────────────────────
#  create_ticket — error branches
# ──────────────────────────────────────────────


async def test_create_ticket_project_not_resolved():
    connector = RedmineConnector(client=_client_404(), profile=Profile6x())
    parsed = _load(await connector.create_ticket(title="x"))
    assert "Could not resolve project" in parsed["error"]


async def test_create_ticket_unknown_priority():
    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "issues.json": {"issues": []},  # no discoverable priorities
        }
    )
    parsed = _load(await connector.create_ticket(title="x", priority=4))
    assert "priority 4 not found" in parsed["error"]


async def test_create_ticket_unknown_version():
    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "projects/3/versions.json": REDMINE_VERSIONS,
        }
    )
    parsed = _load(await connector.create_ticket(title="x", iteration_path="v9.9"))
    assert "v9.9" in parsed["error"]
    assert parsed["available_iterations"] == ["v1.2", "v2.0"]


async def test_create_ticket_http_error():
    client = _make_client(
        {
            "projects.json": REDMINE_PROJECTS,
        }
    )
    client.post = AsyncMock(side_effect=_http_error(500))
    connector = RedmineConnector(client=client, profile=Profile6x())
    parsed = _load(await connector.create_ticket(title="x"))
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  update_ticket — remaining branches
# ──────────────────────────────────────────────


async def test_update_ticket_unknown_assignee():
    connector = _connector({"users.json": REDMINE_USERS})
    parsed = _load(await connector.update_ticket(ticket_id=1, assigned_to="nobody"))
    assert "nobody" in parsed["error"]


async def test_update_ticket_unknown_priority():
    connector = _connector({"issues.json": {"issues": []}})
    parsed = _load(await connector.update_ticket(ticket_id=1, priority=4))
    assert "priority 4 not found" in parsed["error"]


async def test_update_ticket_unknown_version():
    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "projects/3/versions.json": REDMINE_VERSIONS,
        }
    )
    parsed = _load(await connector.update_ticket(ticket_id=1, iteration_path="v9.9"))
    assert "v9.9" in parsed["error"]
    assert parsed["available_iterations"] == ["v1.2", "v2.0"]


async def test_update_ticket_title_and_description():
    connector = _connector({}, put={"issue": {"id": 1}})
    parsed = _load(await connector.update_ticket(ticket_id=1, title="New title", description="## Body"))
    assert parsed["success"] is True
    payload = connector.client.put.call_args.kwargs["data"]
    assert payload["issue"]["subject"] == "New title"
    assert "<h2>Body</h2>" in payload["issue"]["description"]


async def test_update_ticket_http_error():
    client = _make_client({})
    client.put = AsyncMock(side_effect=_http_error(500))
    connector = RedmineConnector(client=client, profile=Profile6x())
    parsed = _load(await connector.update_ticket(ticket_id=1, title="x"))
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  search_tickets — error branches
# ──────────────────────────────────────────────


async def test_search_tickets_project_not_resolved():
    connector = RedmineConnector(client=_client_404(), profile=Profile6x())
    parsed = _load(await connector.search_tickets())
    assert "Could not resolve project" in parsed["error"]


async def test_search_tickets_unknown_type():
    connector = _connector({"projects.json": REDMINE_PROJECTS, "trackers.json": REDMINE_TRACKERS})
    parsed = _load(await connector.search_tickets(ticket_type="Epic"))
    assert "Epic" in parsed["error"]


async def test_search_tickets_unknown_state():
    connector = _connector({"projects.json": REDMINE_PROJECTS, "issues_statuses.json": REDMINE_STATUSES})
    parsed = _load(await connector.search_tickets(state="bogus"))
    assert "bogus" in parsed["error"]


async def test_search_tickets_unknown_assignee():
    connector = _connector({"projects.json": REDMINE_PROJECTS, "users.json": REDMINE_USERS})
    parsed = _load(await connector.search_tickets(assigned_to="nobody"))
    assert "nobody" in parsed["error"]


async def test_search_tickets_with_version_filter():
    connector = _connector(
        {
            "projects.json": REDMINE_PROJECTS,
            "projects/3/versions.json": REDMINE_VERSIONS,
            "issues.json": {"issues": [], "total_count": 0},
        }
    )
    parsed = _load(await connector.search_tickets(iteration_path="v1.2"))
    assert parsed["results"] == []
    (call,) = [c for c in connector.client.get.call_args_list if c.args and c.args[0] == "issues.json"]
    assert call.kwargs["params"]["version_id"] == 9


async def test_search_tickets_http_error():
    # The project (numeric id -> projects/3.json) resolves fine; only the
    # issues.json search GET fails, exercising the HTTP error branch inside
    # search_tickets (not the project-resolution branch).
    default_get = _make_client({"projects/3.json": REDMINE_PROJECT_BY_ID}).get.side_effect
    client = _make_client({"projects/3.json": REDMINE_PROJECT_BY_ID})
    client.project = "3"

    async def _get(path: str, *args, **kwargs):
        if path.startswith("issues.json"):
            raise _http_error(500)(path, *args, **kwargs)
        return await default_get(path, *args, **kwargs)

    client.get.side_effect = _get
    connector = RedmineConnector(client=client, profile=Profile6x())
    parsed = _load(await connector.search_tickets())
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  get_tickets_needing_my_reply — remaining branches
# ──────────────────────────────────────────────


async def test_tickets_needing_my_reply_project_not_resolved():
    client = _make_client({"users.json": REDMINE_USERS})
    connector = RedmineConnector(client=client, profile=Profile6x())
    # users.json resolves, projects.json 404s.
    parsed = _load(await connector.get_tickets_needing_my_reply("mario"))
    assert "Could not resolve project" in parsed["error"]


async def test_tickets_needing_my_reply_closed_and_no_notes():
    issue_closed = {
        "id": 1,
        "subject": "closed",
        "status": {"name": "Closed"},
        "journals": [{"id": 1, "notes": "done", "user": {"name": "Mario Rossi"}}],
    }
    issue_no_notes = {
        "id": 2,
        "subject": "no notes",
        "status": {"name": "New"},
        "journals": [{"id": 1, "notes": "", "user": {"name": "Mario Rossi"}}],
    }
    connector = _connector(
        {
            "users.json": REDMINE_USERS,
            "projects.json": REDMINE_PROJECTS,
            "issues.json": {"issues": [issue_closed, issue_no_notes], "total_count": 2},
        }
    )
    parsed = _load(await connector.get_tickets_needing_my_reply("Mario Rossi"))
    assert parsed["pending"] == []
    assert "No tickets needing reply" in parsed["message"]


async def test_tickets_needing_my_reply_last_author_is_me():
    issue = {
        "id": 3,
        "subject": "I replied last",
        "status": {"name": "New"},
        "journals": [{"id": 1, "notes": "my reply", "user": {"name": "Mario Rossi"}}],
    }
    connector = _connector(
        {
            "users.json": REDMINE_USERS,
            "projects.json": REDMINE_PROJECTS,
            "issues.json": {"issues": [issue], "total_count": 1},
        }
    )
    parsed = _load(await connector.get_tickets_needing_my_reply("Mario Rossi"))
    assert parsed["pending"] == []


async def test_tickets_needing_my_reply_http_error():
    client = _make_client({"users.json": REDMINE_USERS, "projects.json": REDMINE_PROJECTS})
    # Make the issues.json call (the 3rd get) fail.
    calls = {"n": 0}

    orig = client.get.side_effect

    async def _get(path: str, *args, **kwargs):
        calls["n"] += 1
        if path.startswith("issues.json"):
            resp = httpx.Response(500, text="boom")
            raise httpx.HTTPStatusError("boom", request=httpx.Request("GET", "i"), response=resp)
        return await orig(path, *args, **kwargs)

    client.get.side_effect = _get
    connector = RedmineConnector(client=client, profile=Profile6x())
    parsed = _load(await connector.get_tickets_needing_my_reply("mario"))
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  link_tickets / get_ticket_relations / move_to_iteration — error branches
# ──────────────────────────────────────────────


async def test_link_tickets_http_error():
    client = _make_client({})
    client.put = AsyncMock(side_effect=_http_error(500))
    connector = RedmineConnector(client=client, profile=Profile6x())
    parsed = _load(await connector.link_tickets(1, 2))
    assert parsed["error"].startswith("HTTP 500")


async def test_get_ticket_relations_error():
    connector = RedmineConnector(client=_client_404(), profile=Profile6x())
    parsed = _load(await connector.get_ticket_relations(1))
    assert parsed["error"].startswith("HTTP 404")


async def test_move_to_iteration_http_error():
    client = _make_client({"projects.json": REDMINE_PROJECTS, "projects/3/versions.json": REDMINE_VERSIONS})
    client.put = AsyncMock(side_effect=_http_error(500))
    connector = RedmineConnector(client=client, profile=Profile6x())
    parsed = _load(await connector.move_to_iteration(1, "v1.2"))
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  get_iterations — error branch
# ──────────────────────────────────────────────


async def test_get_iterations_error():
    connector = RedmineConnector(client=_client_404(), profile=Profile6x())
    parsed = _load(await connector.get_iterations())
    assert parsed["error"].startswith("HTTP 404")


# ──────────────────────────────────────────────
#  7.x issue_types mapping (is_default description)
# ──────────────────────────────────────────────


async def test_list_issue_types_7x_default_flag():
    issue_types = {"issue_types": [{"id": 2, "name": "Bug", "is_default": True}]}
    connector = _connector({"issue_types.json": issue_types}, profile=Profile7x())
    types = await connector._list_issue_types()
    assert types[0]["description"] == "Default issue type"


# ──────────────────────────────────────────────
#  helpers
# ──────────────────────────────────────────────


def _http_error(status: int):
    def _raise(*args, **kwargs):
        request = httpx.Request("POST", "http://redmine.test/issues.json")
        raise httpx.HTTPStatusError(f"{status}", request=request, response=httpx.Response(status, text="boom"))

    return _raise
