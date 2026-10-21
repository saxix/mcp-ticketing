"""Tests for the per-instance priority discovery (``discovery.py``).

Priorities are an instance configuration (no ``priorities.json`` endpoint in
4.2 – 7.x), so they are discovered from a single global page of issues and
matched by name (EN + IT synonyms).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mcp_ticketing.redmine.discovery import discover_priorities, resolve_priority_id


def _client_with_issues(issues: list) -> AsyncMock:
    client = AsyncMock()

    async def _get(path: str, params=None, *args, **kwargs):
        return {"issues": issues}

    client.get.side_effect = _get
    return client


@pytest.mark.asyncio
async def test_discover_priorities_single_page():
    issues = [
        {"priority": {"id": 1, "name": "Urgente"}},
        {"priority": {"id": 3, "name": "Normale"}},
        {"priority": {"id": 3, "name": "Normale"}},  # duplicate
        {"priority": None},  # missing -> ignored
    ]
    client = _client_with_issues(issues)
    result = await discover_priorities(client)
    assert [p["id"] for p in result] == [1, 3]
    # Only one request, global (no project_id), bounded to one page.
    (call,) = client.get.call_args_list
    assert call.args[0] == "issues.json"
    params = call.kwargs["params"]
    assert params["limit"] == 100
    assert "project_id" not in params


@pytest.mark.asyncio
async def test_discover_priorities_empty_when_no_issues():
    client = _client_with_issues([])
    assert await discover_priorities(client) == []


def test_resolve_priority_id_by_name_en():
    priorities = [
        {"id": 3, "name": "Urgent"},
        {"id": 4, "name": "High"},
        {"id": 5, "name": "Normal"},
        {"id": 6, "name": "Low"},
    ]
    assert resolve_priority_id(priorities, 1) == 3
    assert resolve_priority_id(priorities, 2) == 4
    assert resolve_priority_id(priorities, 3) == 5
    assert resolve_priority_id(priorities, 4) == 6


def test_resolve_priority_id_by_name_it():
    priorities = [
        {"id": 3, "name": "Urgente"},
        {"id": 4, "name": "Alta"},
        {"id": 5, "name": "Normale"},
        {"id": 6, "name": "Bassa"},
    ]
    assert resolve_priority_id(priorities, 1) == 3
    assert resolve_priority_id(priorities, 2) == 4
    assert resolve_priority_id(priorities, 3) == 5
    assert resolve_priority_id(priorities, 4) == 6


def test_resolve_priority_id_not_found():
    priorities = [{"id": 1, "name": "Foo"}, {"id": 2, "name": "Bar"}]
    assert resolve_priority_id(priorities, 1) is None


def test_resolve_priority_id_invalid_level():
    priorities = [{"id": 1, "name": "Urgent"}]
    assert resolve_priority_id(priorities, 0) is None
    assert resolve_priority_id(priorities, 5) is None
    assert resolve_priority_id(priorities, -1) is None


def test_resolve_priority_id_substring_no_wildcard():
    """Matching is a normalised substring match; no wildcard semantics."""
    priorities = [{"id": 7, "name": "Immediato"}]  # matches 'immediat' -> urgent
    assert resolve_priority_id(priorities, 1) == 7
    priorities = [{"id": 7, "name": "Elevata"}]  # matches 'elevat' -> high
    assert resolve_priority_id(priorities, 2) == 7
