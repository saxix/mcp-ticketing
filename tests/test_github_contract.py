"""Contract tests for GithubConnector — no network, no docker.

Uses respx to intercept httpx at the transport level and verify:
- validation errors (missing title)
- auth failures (302 redirect to login) surface a clear error
- search uses the overridable API root (MCP_GITHUB_API_URL)
- error/success payload shapes for the main tools
"""

import json

import httpx
import pytest
import respx

from mcp_ticketing.github_connector import GitHubClient, GithubConnector
from mcp_ticketing.ticket_manager import TicketManager

pytestmark = pytest.mark.asyncio

API = "http://gh.test"
REPO = f"{API}/repos/mockowner/mockrepo"
SEARCH = f"{API}/search/issues"


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setenv("MCP_GITHUB_API_URL", API)
    monkeypatch.setenv("MCP_GITHUB_OWNER", "mockowner")
    monkeypatch.setenv("MCP_GITHUB_REPO", "mockrepo")
    monkeypatch.setenv("MCP_GITHUB_TOKEN", "ghp_fake")
    return TicketManager(strategy=GithubConnector(client=GitHubClient()))


def _load(resp: str) -> dict:
    return json.loads(resp)


# ──────────────────────────────────────────────
#  VALIDATION
# ──────────────────────────────────────────────


@respx.mock
async def test_create_ticket_missing_title(manager):
    parsed = _load(await manager.create_ticket(ticket_type="Issue"))
    assert parsed == {"error": "title is required."}


@respx.mock
async def test_get_ticket_types_is_static(manager):
    parsed = _load(await manager.get_ticket_types())
    assert parsed["total"] == 1
    assert parsed["ticket_types"][0]["name"] == "Issue"


# ──────────────────────────────────────────────
#  AUTH FAILURE
# ──────────────────────────────────────────────


@respx.mock
async def test_check_auth_redirect_to_login(manager):
    respx.get(f"{REPO}/labels").mock(
        return_value=httpx.Response(
            302,
            headers={"location": "https://github.com/login"},
        )
    )
    parsed = _load(await manager.check())
    assert "error" in parsed
    assert "authentication failed" in parsed["error"]
    assert "check token/owner/repo" in parsed["error"]


@respx.mock
async def test_get_ticket_auth_redirect_to_login(manager):
    respx.get(f"{REPO}/issues/1").mock(
        return_value=httpx.Response(
            302,
            headers={"location": "https://github.com/login?return_to=%2Fissues%2F1"},
        )
    )
    parsed = _load(await manager.get_ticket(1))
    assert "error" in parsed
    assert "authentication failed" in parsed["error"]


# ──────────────────────────────────────────────
#  CRUD SHAPES
# ──────────────────────────────────────────────


@respx.mock
async def test_check_ok(manager):
    respx.get(f"{REPO}/labels").mock(return_value=httpx.Response(200, json=[{"name": "bug"}]))
    parsed = _load(await manager.check())
    assert parsed["status"] == "ok"
    assert parsed["backend"] == "github"


@respx.mock
async def test_create_ticket_posts_issue(manager):
    route = respx.post(f"{REPO}/issues").mock(
        return_value=httpx.Response(
            201,
            json={"number": 42, "title": "T", "state": "open", "labels": [], "body": "b"},
        )
    )
    parsed = _load(await manager.create_ticket(title="T", description="b"))
    assert parsed["success"] is True
    assert parsed["id"] == 42
    sent = json.loads(route.calls[0].request.content)
    assert sent["title"] == "T"
    assert sent["body"] == "b"


@respx.mock
async def test_get_ticket_includes_body(manager):
    respx.get(f"{REPO}/issues/9").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 9,
                "title": "Body test",
                "state": "open",
                "labels": [{"name": "priority: high"}],
                "assignee": {"login": "alice"},
                "milestone": {"title": "Sprint 3"},
                "body": "full markdown body",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
            },
        )
    )
    parsed = _load(await manager.get_ticket(9))
    assert parsed["id"] == 9
    assert parsed["description"] == "full markdown body"
    assert parsed["assigned_to"] == "alice"
    assert parsed["iteration_path"] == "Sprint 3"


@respx.mock
async def test_search_uses_overridden_api_root(manager):
    respx.get(SEARCH).mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "number": 5,
                        "title": "Found",
                        "state": "open",
                        "labels": [],
                        "body": "x",
                        "created_at": "",
                        "updated_at": "",
                    }
                ],
            },
        )
    )
    parsed = _load(await manager.search_tickets(query="is:open"))
    assert parsed["total"] == 1
    assert "description" not in parsed["results"][0]
    q = respx.calls[0].request.url.params["q"]
    assert "repo:mockowner/mockrepo" in q


@respx.mock
async def test_update_ticket_no_fields(manager):
    parsed = _load(await manager.update_ticket(ticket_id=1))
    assert "error" in parsed


@respx.mock
async def test_update_ticket_patches_issue(manager):
    route = respx.patch(f"{REPO}/issues/3").mock(
        return_value=httpx.Response(200, json={"number": 3, "title": "New", "state": "closed"})
    )
    parsed = _load(await manager.update_ticket(ticket_id=3, title="New", state="closed"))
    assert parsed["success"] is True
    sent = json.loads(route.calls[0].request.content)
    assert sent["title"] == "New"
    assert sent["state"] == "closed"


@respx.mock
async def test_add_comment_posts_body(manager):
    route = respx.post(f"{REPO}/issues/8/comments").mock(
        return_value=httpx.Response(201, json={"id": 1, "body": "hello", "user": {"login": "bot"}})
    )
    parsed = _load(await manager.add_comment(8, "hello"))
    assert parsed["success"] is True
    sent = json.loads(route.calls[0].request.content)
    assert sent["body"] == "hello"


@respx.mock
async def test_get_ticket_not_found(manager):
    respx.get(f"{REPO}/issues/999999").mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    parsed = _load(await manager.get_ticket(999999))
    assert "error" in parsed
    assert "HTTP 404" in parsed["error"]


# ──────────────────────────────────────────────
#  COMMENTS / CHECK ERRORS
# ──────────────────────────────────────────────


@respx.mock
async def test_check_server_error(manager):
    respx.get(f"{REPO}/labels").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.check())
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_get_ticket_comments_empty(manager):
    respx.get(f"{REPO}/issues/5/comments").mock(return_value=httpx.Response(200, json=[]))
    parsed = _load(await manager.get_ticket_comments(5))
    assert parsed["comments"] == []
    assert "No comments" in parsed["message"]


@respx.mock
async def test_get_ticket_comments_lists_authors(manager):
    respx.get(f"{REPO}/issues/5/comments").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 1, "body": "hello", "user": {"login": "alice"}, "created_at": "2026-01-01"}],
        )
    )
    parsed = _load(await manager.get_ticket_comments(5))
    assert parsed["total"] == 1
    assert parsed["comments"][0]["author"] == "alice"
    assert parsed["comments"][0]["text"] == "hello"


@respx.mock
async def test_get_ticket_comments_error(manager):
    respx.get(f"{REPO}/issues/5/comments").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.get_ticket_comments(5))
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_add_comment_error(manager):
    respx.post(f"{REPO}/issues/8/comments").mock(return_value=httpx.Response(403, text="denied"))
    parsed = _load(await manager.add_comment(8, "hi"))
    assert parsed["error"].startswith("HTTP 403")


# ──────────────────────────────────────────────
#  CREATE / UPDATE FIELDS AND ERRORS
# ──────────────────────────────────────────────


@respx.mock
async def test_create_ticket_with_labels_assignee_milestone(manager):
    respx.get(f"{REPO}/milestones").mock(return_value=httpx.Response(200, json=[{"number": 7, "title": "Sprint 2"}]))
    route = respx.post(f"{REPO}/issues").mock(
        return_value=httpx.Response(201, json={"number": 43, "title": "T", "html_url": "https://gh/43"})
    )
    parsed = _load(
        await manager.create_ticket(
            title="T",
            description="b",
            assigned_to="alice",
            priority=1,
            tags="bug;ui",
            iteration_path="Sprint 2",
        )
    )
    assert parsed["success"] is True
    assert parsed["url"] == "https://gh/43"
    sent = json.loads(route.calls[0].request.content)
    assert sent["labels"] == ["bug", "ui", "priority: 1"]
    assert sent["assignees"] == ["alice"]
    assert sent["milestone"] == 7


@respx.mock
async def test_create_ticket_ignores_milestone_lookup_failure(manager):
    respx.get(f"{REPO}/milestones").mock(return_value=httpx.Response(500, text="boom"))
    route = respx.post(f"{REPO}/issues").mock(return_value=httpx.Response(201, json={"number": 44}))
    parsed = _load(await manager.create_ticket(title="T", iteration_path="Sprint X"))
    assert parsed["success"] is True
    assert "milestone" not in json.loads(route.calls[0].request.content)


@respx.mock
async def test_create_ticket_error(manager):
    respx.post(f"{REPO}/issues").mock(return_value=httpx.Response(422, text="invalid"))
    parsed = _load(await manager.create_ticket(title="T"))
    assert parsed["error"].startswith("HTTP 422")


@respx.mock
async def test_update_ticket_sends_all_fields(manager):
    respx.get(f"{REPO}/milestones").mock(return_value=httpx.Response(200, json=[{"number": 2, "title": "Sprint 1"}]))
    route = respx.patch(f"{REPO}/issues/3").mock(return_value=httpx.Response(200, json={"number": 3}))
    parsed = _load(
        await manager.update_ticket(
            3,
            title="T",
            description="d",
            assigned_to="bob",
            state="active",
            priority=2,
            tags="bug",
            iteration_path="Sprint 1",
        )
    )
    assert parsed["success"] is True
    assert json.loads(route.calls[0].request.content) == {
        "title": "T",
        "body": "d",
        "assignees": ["bob"],
        "state": "open",
        "labels": ["bug", "priority: 2"],
        "milestone": 2,
    }


@respx.mock
async def test_update_ticket_maps_resolved_to_closed(manager):
    route = respx.patch(f"{REPO}/issues/3").mock(return_value=httpx.Response(200, json={"number": 3}))
    await manager.update_ticket(3, state="resolved")
    assert json.loads(route.calls[0].request.content)["state"] == "closed"


@respx.mock
async def test_update_ticket_ignores_milestone_lookup_failure(manager):
    respx.get(f"{REPO}/milestones").mock(return_value=httpx.Response(500, text="boom"))
    route = respx.patch(f"{REPO}/issues/3").mock(return_value=httpx.Response(200, json={"number": 3}))
    await manager.update_ticket(3, title="T", iteration_path="Sprint X")
    assert "milestone" not in json.loads(route.calls[0].request.content)


@respx.mock
async def test_update_ticket_error(manager):
    respx.patch(f"{REPO}/issues/3").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.update_ticket(3, title="T"))
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  SEARCH BRANCHES
# ──────────────────────────────────────────────


@respx.mock
async def test_search_tickets_sends_filters(manager):
    route = respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"total_count": 0, "items": []}))
    parsed = _load(
        await manager.search_tickets(
            state="closed",
            assigned_to="alice",
            tags="bug;ui",
            iteration_path="Sprint 2",
        )
    )
    assert parsed["results"] == []
    query = route.calls[0].request.url.params["q"]
    for piece in (
        "repo:mockowner/mockrepo",
        "is:closed",
        "assignee:alice",
        "label:bug",
        "label:ui",
        "milestone:Sprint 2",
    ):
        assert piece in query


@respx.mock
async def test_search_tickets_defaults_to_open(manager):
    route = respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"total_count": 0, "items": []}))
    await manager.search_tickets()
    assert "is:open" in route.calls[0].request.url.params["q"]


@respx.mock
async def test_search_tickets_error(manager):
    respx.get(SEARCH).mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.search_tickets(query="bug"))
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  PENDING REPLIES
# ──────────────────────────────────────────────


@respx.mock
async def test_tickets_needing_my_reply(manager):
    respx.get(f"{REPO}/issues").mock(
        return_value=httpx.Response(200, json=[{"number": 1, "title": "A"}, {"number": 2, "title": "B"}])
    )
    respx.get(f"{REPO}/issues/1/comments").mock(
        return_value=httpx.Response(
            200, json=[{"body": "reply please", "user": {"login": "alice"}, "created_at": "t1"}]
        )
    )
    respx.get(f"{REPO}/issues/2/comments").mock(
        return_value=httpx.Response(200, json=[{"body": "own", "user": {"login": "me"}, "created_at": "t2"}])
    )
    parsed = _load(await manager.get_tickets_needing_my_reply("me"))
    assert parsed["total"] == 1
    assert parsed["pending"][0]["ticket_id"] == 1
    assert parsed["pending"][0]["last_comment_author"] == "alice"


@respx.mock
async def test_tickets_needing_my_reply_empty(manager):
    respx.get(f"{REPO}/issues").mock(return_value=httpx.Response(200, json=[]))
    parsed = _load(await manager.get_tickets_needing_my_reply("me"))
    assert parsed["pending"] == []
    assert "No active tickets" in parsed["message"]


@respx.mock
async def test_tickets_needing_my_reply_skips_broken_issue(manager):
    respx.get(f"{REPO}/issues").mock(
        return_value=httpx.Response(200, json=[{"number": 1, "title": "A"}, {"number": 2, "title": "B"}])
    )
    respx.get(f"{REPO}/issues/1/comments").mock(return_value=httpx.Response(500, text="boom"))
    respx.get(f"{REPO}/issues/2/comments").mock(
        return_value=httpx.Response(200, json=[{"body": "hi", "user": {"login": "alice"}, "created_at": ""}])
    )
    parsed = _load(await manager.get_tickets_needing_my_reply("me"))
    assert parsed["total"] == 1
    assert parsed["pending"][0]["ticket_id"] == 2


@respx.mock
async def test_tickets_needing_my_reply_error(manager):
    respx.get(f"{REPO}/issues").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.get_tickets_needing_my_reply("me"))
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  LINKS / RELATIONS / ITERATIONS
# ──────────────────────────────────────────────


@respx.mock
async def test_link_tickets_adds_crossref_comment(manager):
    route = respx.post(f"{REPO}/issues/1/comments").mock(return_value=httpx.Response(201, json={"id": 10}))
    parsed = _load(await manager.link_tickets(1, 2, comment="see also"))
    assert parsed["success"] is True
    body = json.loads(route.calls[0].request.content)["body"]
    assert "see also" in body
    assert "#2" in body


@respx.mock
async def test_link_tickets_error(manager):
    respx.post(f"{REPO}/issues/1/comments").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.link_tickets(1, 2))
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_get_ticket_relations_crossrefs_and_prs(manager):
    respx.get(f"{REPO}/issues/1").mock(return_value=httpx.Response(200, json={"number": 1, "body": "relates to #2"}))
    respx.get(f"{REPO}/issues/1/comments").mock(return_value=httpx.Response(200, json=[{"body": "also #3"}]))
    respx.get(f"{REPO}/issues/1/timeline").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"event": "cross-referenced", "source": {"issue": {"number": 7, "pull_request": {"url": "x"}}}},
                {"event": "cross-referenced", "source": {"issue": {"number": 8}}},
                {"event": "labeled"},
            ],
        )
    )
    parsed = _load(await manager.get_ticket_relations(1))
    relations = {(r["type"], r["target_id"]) for r in parsed["relations"]}
    assert ("pull_request", "7") in relations
    assert ("related", "2") in relations
    assert ("related", "3") in relations
    assert ("related", "8") in relations
    assert ("related", "1") not in relations


@respx.mock
async def test_get_ticket_relations_tolerates_timeline_failure(manager):
    respx.get(f"{REPO}/issues/1").mock(return_value=httpx.Response(200, json={"number": 1, "body": "see #2"}))
    respx.get(f"{REPO}/issues/1/comments").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{REPO}/issues/1/timeline").mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    parsed = _load(await manager.get_ticket_relations(1))
    assert parsed["relations"] == [{"type": "related", "target_id": "2", "comment": ""}]


@respx.mock
async def test_get_ticket_relations_error(manager):
    respx.get(f"{REPO}/issues/1").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.get_ticket_relations(1))
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_get_iterations_milestones(manager):
    respx.get(f"{REPO}/milestones").mock(
        return_value=httpx.Response(
            200,
            json=[{"number": 1, "title": "Sprint 1", "state": "open", "due_on": "2026-01-01", "closed_at": ""}],
        )
    )
    parsed = _load(await manager.get_iterations())
    assert parsed["iterations"][0] == {
        "id": 1,
        "name": "Sprint 1",
        "path": "Sprint 1",
        "start_date": "2026-01-01",
        "finish_date": "",
        "state": "open",
    }


@respx.mock
async def test_get_iterations_error(manager):
    respx.get(f"{REPO}/milestones").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.get_iterations())
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_move_to_iteration_success(manager):
    respx.get(f"{REPO}/milestones").mock(return_value=httpx.Response(200, json=[{"number": 5, "title": "Sprint 5"}]))
    route = respx.patch(f"{REPO}/issues/9").mock(return_value=httpx.Response(200, json={"number": 9}))
    parsed = _load(await manager.move_to_iteration(9, "Sprint 5"))
    assert parsed["success"] is True
    assert json.loads(route.calls[0].request.content) == {"milestone": 5}


@respx.mock
async def test_move_to_iteration_not_found(manager):
    respx.get(f"{REPO}/milestones").mock(return_value=httpx.Response(200, json=[{"number": 1, "title": "Other"}]))
    parsed = _load(await manager.move_to_iteration(9, "Nope"))
    assert "not found" in parsed["error"]
    assert parsed["available_iterations"] == ["Other"]


@respx.mock
async def test_move_to_iteration_error(manager):
    respx.get(f"{REPO}/milestones").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.move_to_iteration(9, "Sprint 5"))
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_get_area_paths_labels(manager):
    respx.get(f"{REPO}/labels").mock(
        return_value=httpx.Response(200, json=[{"name": "bug", "description": "defect", "color": "f00"}])
    )
    parsed = _load(await manager.get_area_paths())
    assert parsed["area_paths"][0] == {"name": "bug", "description": "defect", "color": "f00"}


@respx.mock
async def test_get_area_paths_error(manager):
    respx.get(f"{REPO}/labels").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.get_area_paths())
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  MERMAID
# ──────────────────────────────────────────────


@respx.mock
async def test_add_mermaid_appends_section(manager):
    respx.get(f"{REPO}/issues/1").mock(return_value=httpx.Response(200, json={"number": 1, "body": "old body"}))
    route = respx.patch(f"{REPO}/issues/1").mock(return_value=httpx.Response(200, json={"number": 1}))
    parsed = _load(await manager.add_mermaid(1, "flowchart TD\n  A-->B", title="Flow", section="Diagrams"))
    assert parsed["success"] is True
    body = json.loads(route.calls[0].request.content)["body"]
    assert "old body" in body
    assert "## Diagrams" in body
    assert "### Flow" in body
    assert "```mermaid" in body


@respx.mock
async def test_add_mermaid_empty_diagram(manager):
    parsed = _load(await manager.add_mermaid(1, "  "))
    assert parsed["error"] == "diagram is required."


@respx.mock
async def test_add_mermaid_error(manager):
    respx.get(f"{REPO}/issues/1").mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    parsed = _load(await manager.add_mermaid(1, "flowchart TD"))
    assert parsed["error"].startswith("HTTP 404")
