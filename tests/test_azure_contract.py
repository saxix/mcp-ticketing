"""Contract tests for AzureConnector — no network, no docker.

Uses respx to intercept httpx at the transport level and verify:
- validation errors (missing ticket_type / title) are returned even when
  the enriching API call fails (regression: HTTP 302 leak)
- auth failures (302 redirect to login) surface a clear error
- error/success payload shapes for the main tools
"""

import json
import re

import httpx
import pytest
import respx

from mcp_ticketing.azure_connector import AzdoClient, AzureConnector
from mcp_ticketing.ticket_manager import TicketManager

pytestmark = pytest.mark.asyncio

BASE = "http://azdo.test/mockorg/mockproj/_apis"
TYPES_URL = f"{BASE}/wit/workitemtypes"
WORKITEM_URL = f"{BASE}/wit/workitems/{{id}}"


def _load(resp: str) -> dict:
    return json.loads(resp)


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setenv("MCP_AZURE_DEVOPS_BASE_URL", BASE)
    monkeypatch.setenv("MCP_AZURE_DEVOPS_ORG", "mockorg")
    monkeypatch.setenv("MCP_AZURE_DEVOPS_PROJECT", "mockproj")
    monkeypatch.setenv("MCP_AZDO_PAT", "fake-pat")
    return TicketManager(strategy=AzureConnector(client=AzdoClient()))


# ──────────────────────────────────────────────
#  VALIDATION (regression: was network-coupled)
# ──────────────────────────────────────────────


@respx.mock
async def test_create_ticket_missing_type_types_fetch_ok(manager):
    respx.get(TYPES_URL).mock(
        return_value=httpx.Response(
            200,
            json={"workItemTypes": [{"name": "Task"}, {"name": "Bug"}, {"name": "User Story"}]},
        )
    )
    parsed = _load(await manager.create_ticket(title=""))
    assert "error" in parsed
    assert "ticket_type is required" in parsed["error"]
    assert parsed["available_types"] == ["Task", "Bug", "User Story"]


@respx.mock
async def test_create_ticket_missing_type_types_fetch_fails(manager):
    """The original bug: a 302-to-login on the types fetch leaked raw HTML."""
    respx.get(TYPES_URL).mock(
        return_value=httpx.Response(
            302,
            headers={"location": "https://login.microsoftonline.com/common/oauth2"},
            content=b"<html><head><title>Object moved</title></head></html>",
        )
    )
    parsed = _load(await manager.create_ticket(title=""))
    assert "error" in parsed
    assert "ticket_type is required" in parsed["error"]
    assert parsed["available_types"] == []


@respx.mock
async def test_create_ticket_missing_type_types_fetch_500(manager):
    respx.get(TYPES_URL).mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.create_ticket(title=""))
    assert "ticket_type is required" in parsed["error"]
    assert parsed["available_types"] == []


@respx.mock
async def test_create_ticket_missing_title(manager):
    """No network call is made when ticket_type is given but title is empty."""
    parsed = _load(await manager.create_ticket(ticket_type="Task"))
    assert parsed == {"error": "title is required."}


# ──────────────────────────────────────────────
#  AUTH FAILURE
# ──────────────────────────────────────────────


@respx.mock
async def test_check_auth_redirect_to_login(manager):
    respx.get(TYPES_URL).mock(
        return_value=httpx.Response(
            302,
            headers={"location": "https://spsprodweu2.vsstudio.com/_login"},
        )
    )
    parsed = _load(await manager.check())
    assert "error" in parsed
    assert "authentication failed" in parsed["error"]
    assert "check PAT/org/project" in parsed["error"]


@respx.mock
async def test_get_ticket_auth_redirect_to_login(manager):
    respx.get(url__regex=rf"{BASE}/wit/workitems/\d+.*").mock(
        return_value=httpx.Response(
            302,
            headers={"location": "https://login.microsoftonline.com/"},
        )
    )
    parsed = _load(await manager.get_ticket(42))
    assert "error" in parsed
    assert "authentication failed" in parsed["error"]


# ──────────────────────────────────────────────
#  CRUD SHAPES
# ──────────────────────────────────────────────


@respx.mock
async def test_create_ticket_success(manager):
    route = respx.post(f"{BASE}/wit/workitems/$Task").mock(
        return_value=httpx.Response(
            200,
            json={"id": 101, "rev": 1, "fields": {"System.Title": "Test"}},
        )
    )
    parsed = _load(await manager.create_ticket(ticket_type="Task", title="Test", priority=2))
    assert parsed["success"] is True
    assert parsed["id"] == 101
    assert parsed["url"].startswith("https://dev.azure.com/")
    sent = json.loads(route.calls[0].request.content)
    assert {"op": "add", "path": "/fields/System.Title", "value": "Test"} in sent


@respx.mock
async def test_get_ticket_includes_description(manager):
    respx.get(f"{BASE}/wit/workitems/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 7,
                "rev": 2,
                "fields": {
                    "System.Title": "Desc test",
                    "System.State": "Active",
                    "System.Description": "<p>hello body</p>",
                    "System.AssignedTo": {"displayName": "Alice"},
                    "Microsoft.VSTS.Common.Priority": 2,
                },
            },
        )
    )
    parsed = _load(await manager.get_ticket(7))
    assert parsed["id"] == 7
    assert parsed["description"] == "<p>hello body</p>"
    assert parsed["assigned_to"] == "Alice"


@respx.mock
async def test_search_tickets_builds_wiql_and_fetches_batch(manager):
    respx.post(f"{BASE}/wit/wiql").mock(return_value=httpx.Response(200, json={"workItems": [{"id": 11}, {"id": 12}]}))
    respx.get(f"{BASE}/wit/workitems").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {"id": 11, "fields": {"System.Title": "A", "System.State": "Active"}},
                    {"id": 12, "fields": {"System.Title": "B", "System.State": "Active"}},
                ]
            },
        )
    )
    parsed = _load(await manager.search_tickets(ticket_type="Task", state="Active"))
    assert parsed["total"] == 2
    wiql_body = json.loads(respx.calls[0].request.content)
    assert "[System.WorkItemType] = 'Task'" in wiql_body["query"]
    assert "[System.State] = 'Active'" in wiql_body["query"]
    assert "description" not in parsed["results"][0]


@respx.mock
async def test_update_ticket_no_fields(manager):
    parsed = _load(await manager.update_ticket(ticket_id=1))
    assert "error" in parsed
    assert "No fields to update" in parsed["error"]


@respx.mock
async def test_get_ticket_not_found(manager):
    respx.get(url__regex=rf"{BASE}/wit/workitems/\d+.*").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )
    parsed = _load(await manager.get_ticket(999999))
    assert "error" in parsed
    assert "HTTP 404" in parsed["error"]


@respx.mock
async def test_add_comment_sends_markdown_as_html(manager):
    respx.post(f"{BASE}/wit/workitems/5/comments").mock(
        return_value=httpx.Response(
            201,
            json={"comment": {"id": 1, "text": "<p>done</p>", "createdBy": {"displayName": "Bot"}}},
        )
    )
    parsed = _load(await manager.add_comment(5, "**done**"))
    assert parsed["success"] is True
    body = json.loads(respx.calls[0].request.content)
    assert "<strong>done</strong>" in body["text"]


@respx.mock
async def test_get_iterations(manager):
    respx.get(f"{BASE}/work/teamsettings/iterations").mock(
        return_value=httpx.Response(
            200,
            json={"value": [{"name": "Sprint 1", "path": "\\Proj\\Sprint 1", "attributes": {"timeFrame": "current"}}]},
        )
    )
    parsed = _load(await manager.get_iterations())
    assert parsed["iterations"][0]["name"] == "Sprint 1"


@respx.mock
async def test_get_area_paths(manager):
    respx.get(f"{BASE}/wit/classificationNodes/Areas").mock(
        return_value=httpx.Response(
            200,
            json={"name": "MockProj", "path": "\\MockProj"},
        )
    )
    parsed = _load(await manager.get_area_paths())
    assert parsed["path"] == "\\MockProj"


# ──────────────────────────────────────────────
#  TYPES / COMMENTS / CHECK ERRORS
# ──────────────────────────────────────────────


@respx.mock
async def test_get_ticket_types_success(manager):
    respx.get(TYPES_URL).mock(
        return_value=httpx.Response(
            200,
            json={"workItemTypes": [{"name": "Task", "description": "A task"}]},
        )
    )
    parsed = _load(await manager.get_ticket_types())
    assert parsed == {"ticket_types": [{"name": "Task", "description": "A task"}], "total": 1}


@respx.mock
async def test_get_ticket_types_error(manager):
    respx.get(TYPES_URL).mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.get_ticket_types())
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_check_server_error(manager):
    respx.get(TYPES_URL).mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.check())
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_get_ticket_comments_empty(manager):
    respx.get(f"{BASE}/wit/workitems/5/comments").mock(return_value=httpx.Response(200, json={"comments": []}))
    parsed = _load(await manager.get_ticket_comments(5))
    assert parsed["comments"] == []
    assert "No comments" in parsed["message"]


@respx.mock
async def test_get_ticket_comments_lists_authors(manager):
    respx.get(f"{BASE}/wit/workitems/5/comments").mock(
        return_value=httpx.Response(
            200,
            json={
                "comments": [
                    {
                        "id": 1,
                        "text": "hello",
                        "createdBy": {"displayName": "Alice"},
                        "createdDate": "2026-01-01T00:00:00Z",
                    }
                ]
            },
        )
    )
    parsed = _load(await manager.get_ticket_comments(5))
    assert parsed["total"] == 1
    assert parsed["comments"][0]["author"] == "Alice"
    assert parsed["comments"][0]["text"] == "hello"


@respx.mock
async def test_get_ticket_comments_error(manager):
    respx.get(f"{BASE}/wit/workitems/5/comments").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.get_ticket_comments(5))
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_add_comment_error(manager):
    respx.post(f"{BASE}/wit/workitems/5/comments").mock(return_value=httpx.Response(403, text="denied"))
    parsed = _load(await manager.add_comment(5, "x"))
    assert parsed["error"].startswith("HTTP 403")


# ──────────────────────────────────────────────
#  CREATE / UPDATE FIELDS AND ERRORS
# ──────────────────────────────────────────────


@respx.mock
async def test_create_ticket_sends_all_optional_fields(manager):
    route = respx.post(f"{BASE}/wit/workitems/$Bug").mock(return_value=httpx.Response(200, json={"id": 9}))
    parsed = _load(
        await manager.create_ticket(
            ticket_type="Bug",
            title="T",
            description="**bold**",
            assigned_to="Alice",
            priority=1,
            tags="a;b",
            area_path="Proj\\Area",
            iteration_path="Proj\\Sprint 1",
        )
    )
    assert parsed["success"] is True
    ops = json.loads(route.calls[0].request.content)
    by_path = {op["path"]: op["value"] for op in ops}
    assert by_path["/fields/System.Title"] == "T"
    assert "<strong>bold</strong>" in by_path["/fields/System.Description"]
    assert by_path["/fields/System.AssignedTo"] == "Alice"
    assert by_path["/fields/Microsoft.VSTS.Common.Priority"] == "1"
    assert by_path["/fields/System.Tags"] == "a;b"
    assert by_path["/fields/System.AreaPath"] == "Proj\\Area"
    assert by_path["/fields/System.IterationPath"] == "Proj\\Sprint 1"


@respx.mock
async def test_create_ticket_error(manager):
    respx.post(f"{BASE}/wit/workitems/$Task").mock(return_value=httpx.Response(400, text="bad"))
    parsed = _load(await manager.create_ticket(ticket_type="Task", title="T"))
    assert parsed["error"].startswith("HTTP 400")


@respx.mock
async def test_update_ticket_sends_all_fields(manager):
    route = respx.patch(f"{BASE}/wit/workitems/3").mock(return_value=httpx.Response(200, json={"id": 3, "rev": 2}))
    parsed = _load(
        await manager.update_ticket(
            3,
            title="T",
            description="**d**",
            assigned_to="Bob",
            state="Active",
            priority=3,
            tags="x",
            area_path="Proj\\A",
            iteration_path="Proj\\Sprint 2",
        )
    )
    assert parsed["success"] is True
    ops = json.loads(route.calls[0].request.content)
    assert {op["path"] for op in ops} == {
        "/fields/System.Title",
        "/fields/System.Description",
        "/fields/System.AssignedTo",
        "/fields/System.State",
        "/fields/Microsoft.VSTS.Common.Priority",
        "/fields/System.Tags",
        "/fields/System.AreaPath",
        "/fields/System.IterationPath",
    }


@respx.mock
async def test_update_ticket_error(manager):
    respx.patch(f"{BASE}/wit/workitems/3").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.update_ticket(3, title="T"))
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  SEARCH BRANCHES
# ──────────────────────────────────────────────


@respx.mock
async def test_search_tickets_sends_all_filters_and_handles_empty(manager):
    route = respx.post(f"{BASE}/wit/wiql").mock(return_value=httpx.Response(200, json={"workItems": []}))
    parsed = _load(
        await manager.search_tickets(
            ticket_type="Task",
            state="Active",
            assigned_to="Alice",
            tags="a;b",
            area_path="Proj\\A",
            iteration_path="Proj\\Sprint 1",
        )
    )
    assert parsed["total"] == 0
    assert "No results" in parsed["message"]
    query = json.loads(route.calls[0].request.content)["query"]
    assert "[System.AssignedTo] = 'Alice'" in query
    assert "[System.Tags] Contains 'a'" in query
    assert "[System.Tags] Contains 'b'" in query
    assert "[System.AreaPath] = 'Proj\\A'" in query
    assert "[System.IterationPath] = 'Proj\\Sprint 1'" in query


@respx.mock
async def test_search_tickets_error(manager):
    respx.post(f"{BASE}/wit/wiql").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.search_tickets(state="Active"))
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  PENDING REPLIES
# ──────────────────────────────────────────────


@respx.mock
async def test_tickets_needing_my_reply(manager):
    respx.post(f"{BASE}/wit/wiql").mock(return_value=httpx.Response(200, json={"workItems": [{"id": 1}, {"id": 2}]}))
    respx.get(f"{BASE}/wit/workitems/1").mock(
        return_value=httpx.Response(200, json={"id": 1, "fields": {"System.Title": "A"}})
    )
    respx.get(f"{BASE}/wit/workitems/2").mock(
        return_value=httpx.Response(200, json={"id": 2, "fields": {"System.Title": "B"}})
    )
    respx.get(f"{BASE}/wit/workitems/1/comments").mock(
        return_value=httpx.Response(
            200,
            json={
                "comments": [
                    {"text": "please reply", "createdBy": {"displayName": "Alice"}, "createdDate": "2026-01-01"}
                ]
            },
        )
    )
    respx.get(f"{BASE}/wit/workitems/2/comments").mock(
        return_value=httpx.Response(
            200,
            json={"comments": [{"text": "my own", "createdBy": {"displayName": "Me"}, "createdDate": "2026-01-02"}]},
        )
    )
    parsed = _load(await manager.get_tickets_needing_my_reply("Me"))
    assert parsed["total"] == 1
    assert parsed["pending"][0]["ticket_id"] == 1
    assert parsed["pending"][0]["last_comment_author"] == "Alice"


@respx.mock
async def test_tickets_needing_my_reply_empty(manager):
    respx.post(f"{BASE}/wit/wiql").mock(return_value=httpx.Response(200, json={"workItems": []}))
    parsed = _load(await manager.get_tickets_needing_my_reply("Me"))
    assert parsed["pending"] == []
    assert "No active tickets" in parsed["message"]


@respx.mock
async def test_tickets_needing_my_reply_skips_broken_ticket(manager):
    respx.post(f"{BASE}/wit/wiql").mock(return_value=httpx.Response(200, json={"workItems": [{"id": 1}, {"id": 2}]}))
    respx.get(f"{BASE}/wit/workitems/1").mock(return_value=httpx.Response(500, text="boom"))
    respx.get(f"{BASE}/wit/workitems/2").mock(return_value=httpx.Response(200, json={"id": 2, "fields": {}}))
    respx.get(f"{BASE}/wit/workitems/2/comments").mock(
        return_value=httpx.Response(
            200, json={"comments": [{"text": "hi", "createdBy": {"displayName": "Alice"}, "createdDate": ""}]}
        )
    )
    parsed = _load(await manager.get_tickets_needing_my_reply("Me"))
    assert parsed["total"] == 1
    assert parsed["pending"][0]["ticket_id"] == 2


@respx.mock
async def test_tickets_needing_my_reply_error(manager):
    respx.post(f"{BASE}/wit/wiql").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.get_tickets_needing_my_reply("Me"))
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  LINKS / RELATIONS / ITERATIONS
# ──────────────────────────────────────────────


@respx.mock
async def test_link_tickets_success(manager):
    route = respx.patch(f"{BASE}/wit/workitems/1").mock(return_value=httpx.Response(200, json={"id": 1}))
    parsed = _load(await manager.link_tickets(1, 2, link_type="System.LinkTypes.Hierarchy-Reverse", comment="parent"))
    assert parsed["success"] is True
    ops = json.loads(route.calls[0].request.content)
    assert ops[0]["value"]["rel"] == "System.LinkTypes.Hierarchy-Reverse"
    assert ops[0]["value"]["comment"] == "parent"
    assert ops[0]["value"]["url"].endswith("/wit/workItems/2")


@respx.mock
async def test_link_tickets_error(manager):
    respx.patch(f"{BASE}/wit/workitems/1").mock(return_value=httpx.Response(400, text="bad"))
    parsed = _load(await manager.link_tickets(1, 2))
    assert parsed["error"].startswith("HTTP 400")


@respx.mock
async def test_get_ticket_relations(manager):
    respx.get(f"{BASE}/wit/workitems/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 1,
                "relations": [
                    {"rel": "System.LinkTypes.Hierarchy-Reverse", "url": "https://x/wit/workItems/2", "comment": "p"}
                ],
            },
        )
    )
    parsed = _load(await manager.get_ticket_relations(1))
    assert parsed["relations"][0]["target_id"] == "2"
    assert parsed["relations"][0]["comment"] == "p"


@respx.mock
async def test_get_ticket_relations_error(manager):
    respx.get(f"{BASE}/wit/workitems/1").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.get_ticket_relations(1))
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_get_iterations_error(manager):
    respx.get(f"{BASE}/work/teamsettings/iterations").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.get_iterations())
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_move_to_iteration_success(manager):
    route = respx.patch(f"{BASE}/wit/workitems/4").mock(return_value=httpx.Response(200, json={"id": 4}))
    parsed = _load(await manager.move_to_iteration(4, "Proj\\Sprint 2"))
    assert parsed["success"] is True
    ops = json.loads(route.calls[0].request.content)
    assert ops[0]["path"] == "/fields/System.IterationPath"
    assert ops[0]["value"] == "Proj\\Sprint 2"


@respx.mock
async def test_move_to_iteration_error(manager):
    respx.patch(f"{BASE}/wit/workitems/4").mock(return_value=httpx.Response(400, text="bad"))
    parsed = _load(await manager.move_to_iteration(4, "Proj\\Sprint 2"))
    assert parsed["error"].startswith("HTTP 400")


@respx.mock
async def test_get_area_paths_error(manager):
    respx.get(f"{BASE}/wit/classificationNodes/Areas").mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.get_area_paths())
    assert parsed["error"].startswith("HTTP 500")


# ──────────────────────────────────────────────
#  ATTACHMENTS / MERMAID
# ──────────────────────────────────────────────

ATTACHMENTS_URL = re.compile(rf"{re.escape(BASE)}/wit/attachments.*")
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image"


@respx.mock
async def test_add_ticket_image_success(manager, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(PNG_BYTES)
    respx.post(url=ATTACHMENTS_URL).mock(
        return_value=httpx.Response(200, json={"id": "att-1", "url": "http://azdo.test/att/1"})
    )
    route = respx.patch(f"{BASE}/wit/workitems/5").mock(return_value=httpx.Response(200, json={"id": 5}))

    parsed = _load(await manager.add_ticket_image(5, str(image), comment="label"))

    assert parsed["success"] is True
    assert parsed["attachment_id"] == "att-1"
    ops = json.loads(route.calls[0].request.content)
    assert ops[0]["value"]["rel"] == "AttachedFile"
    assert ops[0]["value"]["attributes"]["comment"] == "label"


async def test_add_ticket_image_invalid_path(manager):
    parsed = _load(await manager.add_ticket_image(5, "/nonexistent/file.png"))
    assert "not a readable image file" in parsed["error"]


@respx.mock
async def test_add_ticket_image_read_failure(manager, tmp_path, monkeypatch):
    image = tmp_path / "shot.png"
    image.write_bytes(PNG_BYTES)
    monkeypatch.setattr("mcp_ticketing.azure_connector.read_image_file", lambda _path: None)
    parsed = _load(await manager.add_ticket_image(5, str(image)))
    assert "Could not read" in parsed["error"]


@respx.mock
async def test_add_ticket_image_upload_error(manager, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(PNG_BYTES)
    respx.post(url=ATTACHMENTS_URL).mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.add_ticket_image(5, str(image)))
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_add_ticket_image_patch_error(manager, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(PNG_BYTES)
    respx.post(url=ATTACHMENTS_URL).mock(return_value=httpx.Response(200, json={"id": "att-1", "url": "u"}))
    respx.patch(f"{BASE}/wit/workitems/5").mock(return_value=httpx.Response(400, text="bad"))
    parsed = _load(await manager.add_ticket_image(5, str(image)))
    assert parsed["error"].startswith("HTTP 400")


@respx.mock
async def test_add_mermaid_empty_diagram(manager):
    parsed = _load(await manager.add_mermaid(5, "   "))
    assert parsed["error"] == "diagram is required."


@respx.mock
async def test_add_mermaid_render_failure(manager, monkeypatch):
    def boom(_diagram):
        raise RuntimeError("render exploded")

    monkeypatch.setattr("mcp_ticketing.azure_connector._render_mermaid_png", boom)
    parsed = _load(await manager.add_mermaid(5, "flowchart TD"))
    assert "Failed to render Mermaid" in parsed["error"]


@respx.mock
async def test_add_mermaid_success(manager, monkeypatch):
    monkeypatch.setattr("mcp_ticketing.azure_connector._render_mermaid_png", lambda _diagram: b"PNG")
    respx.post(url=ATTACHMENTS_URL).mock(
        return_value=httpx.Response(200, json={"id": "att-2", "url": "http://azdo.test/att/2"})
    )
    route = respx.patch(f"{BASE}/wit/workitems/5").mock(return_value=httpx.Response(200, json={"id": 5}))
    respx.get(f"{BASE}/wit/workitems/5").mock(
        return_value=httpx.Response(200, json={"id": 5, "fields": {"System.Description": "<p>old</p>"}})
    )

    parsed = _load(await manager.add_mermaid(5, "flowchart TD\n  A-->B", title="Flow"))

    assert parsed["success"] is True
    assert parsed["renderer"] == "mermaidx"
    assert route.call_count == 2
    description_op = json.loads(route.calls[1].request.content)
    assert description_op[0]["path"] == "/fields/System.Description"
    assert "<img" in description_op[0]["value"]
    assert 'alt="Flow"' in description_op[0]["value"]


@respx.mock
async def test_add_mermaid_upload_error(manager, monkeypatch):
    monkeypatch.setattr("mcp_ticketing.azure_connector._render_mermaid_png", lambda _diagram: b"PNG")
    respx.post(url=ATTACHMENTS_URL).mock(return_value=httpx.Response(500, text="boom"))
    parsed = _load(await manager.add_mermaid(5, "flowchart TD"))
    assert parsed["error"].startswith("HTTP 500")


@respx.mock
async def test_add_mermaid_patch_error(manager, monkeypatch):
    monkeypatch.setattr("mcp_ticketing.azure_connector._render_mermaid_png", lambda _diagram: b"PNG")
    respx.post(url=ATTACHMENTS_URL).mock(return_value=httpx.Response(200, json={"id": "att-2", "url": "u"}))
    respx.patch(f"{BASE}/wit/workitems/5").mock(return_value=httpx.Response(400, text="bad"))
    parsed = _load(await manager.add_mermaid(5, "flowchart TD"))
    assert parsed["error"].startswith("HTTP 400")


@respx.mock
async def test_check_ok(manager):
    respx.get(TYPES_URL).mock(return_value=httpx.Response(200, json={"workItemTypes": []}))
    parsed = _load(await manager.check())
    assert parsed["status"] == "ok"
    assert parsed["backend"] == "azure"
    assert parsed["project"] == "mockproj"
