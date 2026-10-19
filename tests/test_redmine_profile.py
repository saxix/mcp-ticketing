"""Tests for the version strategy: profile selection, the single startup
probe (``X-Redmine-Version``), auto-detect and the declared-vs-server
mismatch (fail-fast).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from mcp_ticketing.redmine import RedmineConnector, select_profile
from mcp_ticketing.redmine.p6x import Profile6x
from mcp_ticketing.redmine.p7x import Profile7x
from mcp_ticketing.redmine.p42 import Profile42
from mcp_ticketing.redmine.p51 import Profile51
from mcp_ticketing.redmine.profile import (
    ProfileMismatchError,
    RedmineVersionError,
    UnsupportedRedmineVersionError,
    parse_version,
)

# ──────────────────────────────────────────────
#  parse_version
# ──────────────────────────────────────────────


def test_parse_version_full():
    assert parse_version("6.1") == (6, 1)
    assert parse_version("4.2.1") == (4, 2)


def test_parse_version_major_only():
    assert parse_version("5") == (5, 0)


def test_parse_version_invalid():
    with pytest.raises(RedmineVersionError):
        parse_version("not-a-version")


# ──────────────────────────────────────────────
#  select_profile
# ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "declared,expected",
    [
        ("4.2", Profile42),
        ("4.9", Profile42),
        ("5.0", Profile51),
        ("5.1", Profile51),
        ("6.0", Profile6x),
        ("6.1", Profile6x),
        ("7.0", Profile7x),
        ("7.1", Profile7x),
    ],
)
def test_select_profile_by_range(declared, expected):
    profile = select_profile(declared)
    assert type(profile) is expected


@pytest.mark.parametrize("declared", ["4.1", "3.4", "8.0"])
def test_select_profile_unsupported(declared):
    with pytest.raises(UnsupportedRedmineVersionError):
        select_profile(declared)


def test_select_profile_none_returns_none():
    assert select_profile(None) is None
    assert select_profile("") is None
    assert select_profile("   ") is None


# ──────────────────────────────────────────────
#  Probe helpers
# ──────────────────────────────────────────────


def _probe_client(version_header: str | None, body: dict | None = None):
    """A mock client whose get_with_headers returns the given version header."""
    client = AsyncMock()
    client.base_url = "http://redmine.test"
    client.api_key = "test-key"
    client.project = "openextras"

    async def _get_with_headers(path: str, *args, **kwargs):
        headers = {}
        if version_header is not None:
            headers["X-Redmine-Version"] = version_header
        return (body or {}), headers

    async def _get(path: str, *args, **kwargs):
        return body or {}

    client.get_with_headers = AsyncMock(side_effect=_get_with_headers)
    client.get = AsyncMock(side_effect=_get)
    return client


@pytest.mark.asyncio
async def test_ensure_profile_declared_matches(monkeypatch):
    """Declared version matches the server -> the declared profile is used, no mismatch."""
    monkeypatch.setenv("MCP_REDMINE_VERSION", "6.1")
    client = _probe_client("6.1")
    connector = RedmineConnector(client=client)  # no explicit profile -> declared path
    profile = await connector._ensure_profile()
    assert profile.name == "6.x"
    assert connector._last_version == (6, 1)


@pytest.mark.asyncio
async def test_ensure_profile_declared_mismatch_raises(monkeypatch):
    """Declared 4.2 but server is 6.1 -> ProfileMismatchError (fail-fast)."""
    monkeypatch.setenv("MCP_REDMINE_VERSION", "4.2")
    client = _probe_client("6.1")
    connector = RedmineConnector(client=client)  # no explicit profile -> declared path
    with pytest.raises(ProfileMismatchError):
        await connector._ensure_profile()


@pytest.mark.asyncio
async def test_auto_detect_picks_matching_profile():
    """No declared version: a single probe selects the right profile."""
    client = _probe_client("7.0")
    connector = RedmineConnector(client=client)  # no profile -> auto-detect
    profile = await connector._ensure_profile()
    assert profile.name == "7.x"
    assert connector._last_version == (7, 0)
    # Exactly one probe request.
    assert client.get_with_headers.call_count == 1


@pytest.mark.asyncio
async def test_auto_detect_unsupported_version_raises():
    """Server outside 4.2 – 7.x -> UnsupportedRedmineVersionError at startup."""
    client = _probe_client("3.4")
    connector = RedmineConnector(client=client)
    with pytest.raises(UnsupportedRedmineVersionError):
        await connector._ensure_profile()


@pytest.mark.asyncio
async def test_ensure_profile_caches():
    """The profile is probed once and then cached."""
    client = _probe_client("6.1")
    connector = RedmineConnector(client=client)
    await connector._ensure_profile()
    first = connector._profile
    await connector._ensure_profile()
    assert connector._profile is first
    assert client.get_with_headers.call_count == 1  # still one probe


@pytest.mark.asyncio
async def test_profile_invalidation_on_reselect():
    """Switching profile invalidates the per-instance caches."""
    client = _probe_client("6.1")
    connector = RedmineConnector(client=client)
    await connector._ensure_profile()
    connector._project = {"id": 3}
    connector._priorities = [{"id": 1, "name": "Urgent"}]
    # Force a re-selection with a different profile.
    connector._profile = None
    connector._profile_ready = False
    await connector._ensure_profile()
    assert connector._project is None
    assert connector._priorities is None


# ──────────────────────────────────────────────
#  check() reports the selected profile
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_reports_profile_after_probe():
    from mcp_ticketing.redmine.p6x import Profile6x

    client = _probe_client("6.1", body={"user": {"id": 7, "login": "mario", "name": "Mario"}})
    connector = RedmineConnector(client=client, profile=Profile6x())
    parsed = json.loads(await connector.check())
    assert parsed["profile"] == "6.x"
    assert parsed["status"] == "ok"
    assert parsed["user"] == "Mario"
