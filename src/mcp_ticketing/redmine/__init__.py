"""Redmine backend package.

Public surface:

* :func:`select_profile` — pick a :class:`~mcp_ticketing.redmine.profile.RedmineVersionProfile`
  from a declared version string (or ``None`` for auto-detect).
* :data:`VERSION_PROFILES` — the ordered list of profile classes.
* :class:`RedmineConnector` — the :class:`~mcp_ticketing.protocol.TicketProtocol`
  implementation used by the ``BACKENDS`` registry.
* :class:`RedmineClient` — the httpx wrapper.
* :class:`ProfileMismatchError` / :class:`UnsupportedRedmineVersionError` — startup
  errors for version declaration problems.

The version strategy (dual Strategy pattern) is: the *outer* strategy is the
ticket backend (azure/github/redmine, chosen by ``TicketManager``); the
*inner* strategy is the Redmine version profile, selected **once at startup**
from ``MCP_REDMINE_VERSION`` or from a single ``X-Redmine-Version`` probe.
"""

from mcp_ticketing.redmine.client import RedmineClient
from mcp_ticketing.redmine.connector import RedmineConnector
from mcp_ticketing.redmine.profile import (
    ProfileMismatchError,
    RedmineCapabilities,
    RedmineVersionError,
    RedmineVersionProfile,
    UnsupportedRedmineVersionError,
    parse_version,
)
from mcp_ticketing.redmine.registry import VERSION_PROFILES, select_profile

__all__ = [
    "RedmineClient",
    "RedmineConnector",
    "RedmineCapabilities",
    "RedmineVersionError",
    "RedmineVersionProfile",
    "UnsupportedRedmineVersionError",
    "ProfileMismatchError",
    "parse_version",
    "VERSION_PROFILES",
    "select_profile",
]
