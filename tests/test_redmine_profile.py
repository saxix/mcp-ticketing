"""Tests for the version strategy: profile selection from the declared
``MCP_REDMINE_VERSION`` (latest profile when unset), fail-fast on an
unsupported declared version, and the doc <-> registry consistency of the
supported majors.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mcp_ticketing.redmine import VERSION_PROFILES, RedmineConnector, select_profile
from mcp_ticketing.redmine.p6x import Profile6x
from mcp_ticketing.redmine.p7x import Profile7x
from mcp_ticketing.redmine.p42 import Profile42
from mcp_ticketing.redmine.p51 import Profile51
from mcp_ticketing.redmine.profile import (
    RedmineVersionError,
    UnsupportedRedmineVersionError,
    parse_version,
)

DOCS_REDMINE = Path(__file__).resolve().parent.parent / "docs" / "redmine.md"


@pytest.fixture(autouse=True)
def _reset_redmine_version(monkeypatch):
    """Ensure ``MCP_REDMINE_VERSION`` does not leak between tests.

    Tests that need a declared version set it explicitly via
    ``monkeypatch.setenv``; this fixture guarantees the variable is
    unset for every other test so the "latest profile" path is exercised.
    """
    monkeypatch.delenv("MCP_REDMINE_VERSION", raising=False)
    yield


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


@pytest.mark.parametrize("declared", ["4.1", "3.4", "8.0", "garbage"])
def test_select_profile_unsupported(declared):
    with pytest.raises((UnsupportedRedmineVersionError, RedmineVersionError)):
        select_profile(declared)


def test_select_profile_none_returns_latest():
    """No declared version -> the latest (last) profile in the registry."""
    latest = VERSION_PROFILES[-1]
    assert type(select_profile(None)) is latest
    assert type(select_profile("")) is latest
    assert type(select_profile("   ")) is latest


def test_select_profile_latest_is_7x():
    """The registry must end with the newest major (7.x) as the default."""
    assert issubclass(VERSION_PROFILES[-1], Profile7x)


# ──────────────────────────────────────────────
#  _ensure_profile (no server round-trip)
# ──────────────────────────────────────────────


def _mock_client(body: dict | None = None):
    """A mock client: no probe method at all (the connector must not need one)."""
    client = AsyncMock()
    client.base_url = "http://redmine.test"
    client.api_key = "test-key"
    client.project = "openextras"

    async def _get(path: str, *args, **kwargs):
        return body or {}

    client.get = AsyncMock(side_effect=_get)
    return client


@pytest.mark.asyncio
async def test_ensure_profile_declared(monkeypatch):
    """A declared version selects that profile; nothing is probed."""
    monkeypatch.setenv("MCP_REDMINE_VERSION", "4.2")
    client = _mock_client()
    connector = RedmineConnector(client=client)
    profile = await connector._ensure_profile()
    assert profile.name == "4.x"
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_profile_unset_uses_latest(monkeypatch):
    """MCP_REDMINE_VERSION unset -> latest profile, no probe."""
    client = _mock_client()
    connector = RedmineConnector(client=client)
    profile = await connector._ensure_profile()
    assert profile.name == "7.x"
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_profile_unsupported_declared_raises(monkeypatch):
    """A declared version outside 4.2 – 7.x fails fast at startup."""
    monkeypatch.setenv("MCP_REDMINE_VERSION", "3.4")
    client = _mock_client()
    connector = RedmineConnector(client=client)
    with pytest.raises(UnsupportedRedmineVersionError):
        await connector._ensure_profile()


@pytest.mark.asyncio
async def test_ensure_profile_caches():
    """The profile is resolved once and then cached."""
    client = _mock_client()
    connector = RedmineConnector(client=client)
    await connector._ensure_profile()
    first = connector._profile
    await connector._ensure_profile()
    assert connector._profile is first


@pytest.mark.asyncio
async def test_profile_invalidation_on_reselect():
    """Switching profile invalidates the per-instance caches."""
    client = _mock_client()
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
async def test_check_reports_profile():
    """check() confirms connectivity via projects.json only, no probe."""
    client = _mock_client(body={"projects": []})
    connector = RedmineConnector(client=client, profile=Profile6x())
    parsed = json.loads(await connector.check())
    assert parsed["profile"] == "6.x"
    assert parsed["status"] == "ok"
    assert "user" not in parsed


# ──────────────────────────────────────────────
#  doc <-> registry consistency (supported majors)
# ──────────────────────────────────────────────


def _registry_majors() -> set[int]:
    """The set of majors covered by ``VERSION_PROFILES``.

        Derived by sampling: a profile covers major ``m`` when ``supports()``
        is true for ``m.0`` or ``m.9`` (minors never span majors here). The scan is
    bounded by :data:`SUPPORTED_MIN` / :data:`SUPPORTED_MAX`: the newest profile
    is open-ended (7.x covers 7.0+), so an unbounded scan would over-count.
    """
    from mcp_ticketing.redmine.profile import SUPPORTED_MAX, SUPPORTED_MIN

    majors: set[int] = set()
    for prof in (cls() for cls in VERSION_PROFILES):
        for major in range(SUPPORTED_MIN[0], SUPPORTED_MAX[0] + 1):
            if prof.supports((major, 0)) or prof.supports((major, 9)):
                majors.add(major)
    return majors


def test_supported_majors_in_docs_match_registry():
    """The 'Supported versions' table in docs/redmine.md must list exactly
    the majors covered by ``VERSION_PROFILES``.

    This is the documentation contract: operators read the table to pick
    ``MCP_REDMINE_VERSION``; this test keeps the table and the registry in
    sync so adding a profile without documenting it (or vice versa) fails.
    """
    text = DOCS_REDMINE.read_text(encoding="utf-8")
    # Table rows like: | 4.x | ... | -> extract the major.
    doc_majors = sorted({int(m) for m in re.findall(r"^\|\s*(\d+)\.x\s*\|", text, flags=re.MULTILINE)})
    registry = sorted(_registry_majors())
    assert doc_majors == registry, f"docs/redmine.md lists majors {doc_majors} but the registry covers {registry}"
