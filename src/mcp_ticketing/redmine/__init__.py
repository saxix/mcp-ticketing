"""Redmine backend package.

Public surface:

* :func:`select_profile` — pick a :class:`~mcp_ticketing.redmine.profile.RedmineVersionProfile`
  from a declared version string (or the latest profile when ``None``).
* :data:`VERSION_PROFILES` — the ordered list of profile classes (oldest to
  newest); the same list of majors is documented in ``docs/redmine.md``.
* :class:`RedmineConnector` — the :class:`~mcp_ticketing.protocol.TicketProtocol`
  implementation used by the ``BACKENDS`` registry.
* :class:`RedmineClient` — the httpx wrapper.
* :class:`UnsupportedRedmineVersionError` — startup error for an invalid or
  unsupported declared ``MCP_REDMINE_VERSION``.

The version strategy (dual Strategy pattern) is: the *outer* strategy is the
ticket backend (azure/github/redmine, chosen by ``TicketManager``); the
*inner* strategy is the Redmine version profile, selected **once at startup**
from ``MCP_REDMINE_VERSION`` (latest profile when unset). No server probe:
the operator declares the product version.
"""

from mcp_ticketing.redmine.client import RedmineClient
from mcp_ticketing.redmine.connector import RedmineConnector
from mcp_ticketing.redmine.profile import (
    RedmineVersionError,
    RedmineVersionProfile,
    UnsupportedRedmineVersionError,
    parse_version,
)
from mcp_ticketing.redmine.registry import VERSION_PROFILES, select_profile

__all__ = [
    "RedmineClient",
    "RedmineConnector",
    "RedmineVersionError",
    "RedmineVersionProfile",
    "UnsupportedRedmineVersionError",
    "parse_version",
    "VERSION_PROFILES",
    "select_profile",
]
