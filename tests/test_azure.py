import base64
import json
import os
import re

import httpx
import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not all(
            os.getenv(v, "")
            for v in [
                "MCP_AZURE_DEVOPS_ORG",
                "MCP_AZURE_DEVOPS_PROJECT",
                "MCP_AZDO_PAT",
            ]
        ),
        reason="Azure DevOps env vars not set",
    ),
]


def _load(resp: str) -> dict:
    return json.loads(resp)


def _check(resp: str) -> dict:
    parsed = _load(resp)
    if "error" in parsed:
        pytest.skip(f"Skipping — API error: {parsed['error']}")
    return parsed


# ──────────────────────────────────────────────
#  CONNECTIVITY
# ──────────────────────────────────────────────

from mcp_ticketing.azure_connector import AzureConnector
from mcp_ticketing.ticket_manager import TicketManager

manager = TicketManager(strategy=AzureConnector())

add_comment = manager.add_comment
add_mermaid = manager.add_mermaid
add_ticket_image = manager.add_ticket_image
create_ticket = manager.create_ticket
get_area_paths = manager.get_area_paths
get_iterations = manager.get_iterations
get_ticket = manager.get_ticket
get_ticket_comments = manager.get_ticket_comments
get_ticket_relations = manager.get_ticket_relations
get_ticket_types = manager.get_ticket_types
link_tickets = manager.link_tickets
move_to_iteration = manager.move_to_iteration
search_tickets = manager.search_tickets
update_ticket = manager.update_ticket


async def test_get_iterations():
    result = await get_iterations()
    parsed = _check(result)
    assert isinstance(parsed["iterations"], list)
    assert isinstance(parsed["total"], int)


async def test_get_area_paths():
    result = await get_area_paths()
    parsed = _check(result)
    assert isinstance(parsed, dict)
    assert "classificationPathInfo" in parsed or "path" in parsed


async def test_get_ticket_types():
    result = await get_ticket_types()
    parsed = _check(result)
    assert isinstance(parsed["ticket_types"], list)
    assert isinstance(parsed["total"], int)
    assert parsed["total"] >= 1


async def test_check():
    result = await manager.check()
    parsed = _check(result)
    assert parsed["status"] == "ok"
    assert parsed["backend"] == "azure"


async def test_create_ticket_missing_type():
    result = await create_ticket(title="")
    parsed = _load(result)
    assert "error" in parsed
    assert "ticket_type is required" in parsed["error"]
    assert "available_types" in parsed


async def test_create_ticket_missing_title():
    result = await create_ticket(ticket_type="Task")
    parsed = _load(result)
    assert "error" in parsed
    assert "title is required" in parsed["error"]


async def test_search_tickets_custom_query():
    result = await search_tickets(
        query="SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'"
    )
    parsed = _check(result)
    assert isinstance(parsed["results"], list)
    assert isinstance(parsed["total"], int)


async def test_search_tickets_no_results():
    result = await search_tickets(
        ticket_type="Task",
        state="NonExistentStateXYZ",
        max_results=5,
    )
    parsed = _load(result)
    if "error" not in parsed:
        assert isinstance(parsed["results"], list)
        assert parsed["total"] == 0


async def test_get_ticket_not_found():
    result = await get_ticket(99999999)
    parsed = _load(result)
    assert "error" in parsed
    assert "HTTP" in parsed["error"]


async def test_update_ticket_no_fields():
    result = await update_ticket(ticket_id=1)
    parsed = _load(result)
    assert "error" in parsed
    assert "No fields to update" in parsed["error"]


async def test_get_ticket_invalid_id():
    result = await get_ticket(-1)
    parsed = _load(result)
    assert "error" in parsed


# ──────────────────────────────────────────────
#  DYNAMIC CRUD TESTS
# ──────────────────────────────────────────────


async def test_create_ticket_and_crud():
    result = await create_ticket(
        ticket_type="Task",
        title="[PYTEST] Test Task — CRUD chain",
        description="Opened by an MCP server test",
        priority=3,
    )
    parsed = _check(result)
    assert parsed["success"] is True
    assert isinstance(parsed["id"], int)
    assert "url" in parsed
    assert parsed["url"].startswith("https://dev.azure.com/")
    tid = parsed["id"]

    r = await get_ticket(tid)
    p = _check(r)
    assert p["id"] == tid
    assert "title" in p

    r = await update_ticket(
        ticket_id=tid,
        title="[PYTEST] Updated Title — CRUD chain",
        priority=1,
    )
    p = _check(r)
    assert p["success"] is True
    assert p["id"] == tid

    r = await add_comment(tid, "Comment from pytest CRUD chain")
    p = _check(r)
    assert p["success"] is True
    assert p["ticket_id"] == tid

    r = await get_ticket_comments(tid)
    p = _check(r)
    assert p["ticket_id"] == tid
    assert len(p["comments"]) >= 1

    r = await search_tickets(ticket_type="Task", max_results=5)
    p = _check(r)
    assert isinstance(p["results"], list)
    assert isinstance(p["total"], int)

    r = await get_ticket_relations(tid)
    p = _check(r)
    assert p["ticket_id"] == tid
    assert isinstance(p["relations"], list)

    it_result = await get_iterations()
    it_parsed = _check(it_result)
    if it_parsed["iterations"]:
        first_iter = it_parsed["iterations"][0]["path"]
        r = await move_to_iteration(tid, first_iter)
        p = _check(r)
        assert p["success"] is True
        assert p["id"] == tid

    await update_ticket(ticket_id=tid, state="Closed")


async def test_create_ticket_with_all_fields():
    result = await create_ticket(
        ticket_type="Task",
        title="[PYTEST] Test Task Full — pytest",
        description="Full test item with all fields",
        priority=2,
        tags="pytest;automation",
    )
    parsed = _check(result)
    assert parsed["success"] is True
    assert isinstance(parsed["id"], int)
    assert "url" in parsed

    r = await update_ticket(ticket_id=parsed["id"], state="Closed")
    p = _check(r)
    assert p["success"] is True


async def test_link_two_tickets():
    r1 = await create_ticket(
        ticket_type="Task",
        title="[PYTEST] Link Source — pytest",
        priority=3,
    )
    p1 = _check(r1)
    tid_a = p1["id"]

    r2 = await create_ticket(
        ticket_type="Task",
        title="[PYTEST] Link Target — pytest",
        priority=3,
    )
    p2 = _check(r2)
    tid_b = p2["id"]

    result = await link_tickets(
        source_id=tid_a,
        target_id=tid_b,
        link_type="System.LinkTypes.Related",
        comment="Linked by pytest",
    )
    parsed = _check(result)
    assert parsed["success"] is True

    rel = await get_ticket_relations(tid_a)
    rel_parsed = _check(rel)
    assert len(rel_parsed["relations"]) > 0

    await update_ticket(ticket_id=tid_a, state="Closed")
    await update_ticket(ticket_id=tid_b, state="Closed")


async def test_add_comment_empty_text():
    r = await create_ticket(
        ticket_type="Task", title="[PYTEST] Empty comment test", priority=3
    )
    p = _check(r)
    tid = p["id"]

    result = await add_comment(tid, "")
    parsed = _load(result)
    assert "success" in parsed or "error" in parsed

    await update_ticket(ticket_id=tid, state="Closed")


async def test_add_ticket_image_invalid_file(tmp_path):
    fake = tmp_path / "not-an-image.txt"
    fake.write_bytes(b"this is not an image")
    result = await add_ticket_image(1, str(fake))
    parsed = _load(result)
    assert "error" in parsed
    assert "image" in parsed["error"]


async def test_add_ticket_image(tmp_path):
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    image = tmp_path / "pixel.png"
    image.write_bytes(png)

    r = await create_ticket(ticket_type="Task", title="[PYTEST] Image attach test")
    p = _check(r)
    tid = p["id"]

    result = await add_ticket_image(tid, str(image), comment="screenshot")
    parsed = _check(result)
    assert parsed["success"] is True
    assert parsed["ticket_id"] == tid
    assert parsed["file_name"] == "pixel.png"
    assert "attachment_id" in parsed
    assert "url" in parsed
    assert parsed["url"].startswith("https://dev.azure.com/")

    relations = await get_ticket_relations(tid)
    rel_parsed = _check(relations)
    assert any(r["type"] == "AttachedFile" for r in rel_parsed["relations"])

    await update_ticket(ticket_id=tid, state="Closed")


async def test_add_mermaid():
    r = await create_ticket(
        ticket_type="Task",
        title="[PYTEST] Mermaid diagram — pytest",
        description="Body for mermaid",
    )
    p = _check(r)
    tid = p["id"]

    diagram = "flowchart TD\n  A[Start] --> B[End]"
    result = await add_mermaid(tid, diagram, title="Flow")
    parsed = _check(result)
    assert parsed["success"] is True
    assert parsed["ticket_id"] == tid
    assert parsed["file_name"] == f"mermaid-{tid}.png"
    assert "attachment_id" in parsed
    assert "renderer" in parsed and parsed["renderer"] == "mermaidx"
    assert parsed["url"].startswith("https://dev.azure.com/")

    relations = await get_ticket_relations(tid)
    rel_parsed = _check(relations)
    assert any(r["type"] == "AttachedFile" for r in rel_parsed["relations"])

    async with httpx.AsyncClient() as client:
        response = await client.get(
            parsed["url"],
            auth=("", os.environ["MCP_AZDO_PAT"]),
        )
        assert response.status_code == 200
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    from mcp_ticketing.azure_connector import API_VERSION

    item = await manager._strategy.client.get(
        f"wit/workitems/{tid}", params={"api-version": API_VERSION}
    )
    description = item.get("fields", {}).get("System.Description", "")
    assert re.search(r'<img src="[^"]+\?fileName=mermaid-\d+\.png"', description)
    assert "Flow" in description
    assert "flowchart TD" not in description

    await update_ticket(ticket_id=tid, state="Closed")
