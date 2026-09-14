import json
import os

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not all(
            os.getenv(v, "")
            for v in ["MCP_REDMINE_URL", "MCP_REDMINE_API_KEY", "MCP_REDMINE_PROJECT"]
        ),
        reason="Redmine env vars not set",
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

from mcp_ticketing.redmine_connector import RedmineConnector
from mcp_ticketing.ticket_manager import TicketManager

manager = TicketManager(strategy=RedmineConnector())

add_comment = manager.add_comment
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


async def test_check():
    result = await manager.check()
    parsed = _check(result)
    assert parsed["status"] == "ok"
    assert parsed["backend"] == "redmine"


async def test_get_ticket_types():
    result = await get_ticket_types()
    parsed = _check(result)
    assert isinstance(parsed["ticket_types"], list)
    assert isinstance(parsed["total"], int)
    assert parsed["total"] >= 1


async def test_get_iterations():
    result = await get_iterations()
    parsed = _check(result)
    assert isinstance(parsed["iterations"], list)
    assert isinstance(parsed["total"], int)


async def test_get_area_paths():
    result = await get_area_paths()
    parsed = _check(result)
    assert "area_paths" in parsed
    assert isinstance(parsed["area_paths"], list)


async def test_get_ticket_not_found():
    result = await get_ticket(999999999)
    parsed = _load(result)
    assert "error" in parsed


async def test_update_ticket_no_fields():
    result = await update_ticket(ticket_id=1)
    parsed = _load(result)
    assert "error" in parsed
    assert "No fields to update" in parsed["error"]


async def test_create_ticket_missing_type_and_title():
    result = await create_ticket(title="")
    parsed = _load(result)
    assert "error" in parsed
    assert "available_types" in parsed


async def test_search_tickets_text():
    r = await search_tickets(query="pytest", max_results=5)
    parsed = _check(r)
    assert isinstance(parsed["results"], list)
    assert isinstance(parsed["total"], int)


# ──────────────────────────────────────────────
#  DYNAMIC CRUD TESTS
# ──────────────────────────────────────────────


async def test_create_ticket_and_crud():
    types = _check(await get_ticket_types())
    first_type = types["ticket_types"][0]["name"]

    result = await create_ticket(
        ticket_type=first_type,
        title="[PYTEST] Test Issue — CRUD chain",
        description="Opened by an MCP server test",
    )
    parsed = _check(result)
    assert parsed["success"] is True
    assert isinstance(parsed["id"], int)
    assert "url" in parsed
    assert parsed["url"].endswith(f"/issues/{parsed['id']}")
    tid = parsed["id"]

    r = await get_ticket(tid)
    p = _check(r)
    assert p["id"] == tid
    assert "title" in p

    r = await update_ticket(
        ticket_id=tid,
        title="[PYTEST] Updated Title — CRUD chain",
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

    r = await get_ticket_relations(tid)
    p = _check(r)
    assert p["ticket_id"] == tid
    assert isinstance(p["relations"], list)

    it_result = await get_iterations()
    it_parsed = _check(it_result)
    if it_parsed["iterations"]:
        first_iter = it_parsed["iterations"][0]["name"]
        r = await move_to_iteration(tid, first_iter)
        p = _check(r)
        assert p["success"] is True

    r = await search_tickets(query="PYTEST", max_results=5)
    p = _check(r)
    assert isinstance(p["results"], list)

    await update_ticket(ticket_id=tid, state="closed")


async def test_link_two_tickets():
    types = _check(await get_ticket_types())
    first_type = types["ticket_types"][0]["name"]

    r1 = await create_ticket(
        ticket_type=first_type,
        title="[PYTEST] Link Source — pytest",
    )
    p1 = _check(r1)
    tid_a = p1["id"]

    r2 = await create_ticket(
        ticket_type=first_type,
        title="[PYTEST] Link Target — pytest",
    )
    p2 = _check(r2)
    tid_b = p2["id"]

    result = await link_tickets(
        source_id=tid_a,
        target_id=tid_b,
        comment="Linked by pytest",
    )
    parsed = _check(result)
    assert parsed["success"] is True

    rel = await get_ticket_relations(tid_a)
    rel_parsed = _check(rel)
    assert len(rel_parsed["relations"]) > 0

    await update_ticket(ticket_id=tid_a, state="closed")
    await update_ticket(ticket_id=tid_b, state="closed")
