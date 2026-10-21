"""Version-profile registry and selection (no internal dependencies).

Kept in its own module so both :mod:`~mcp_ticketing.redmine.__init__` and
:mod:`~mcp_ticketing.redmine.connector` can import :func:`select_profile`
without a circular import (``__init__`` imports the connector, which must
not import back from ``__init__``).

The registry **is** the list of supported Redmine majors. The same list is
documented for operators in ``docs/redmine.md`` (the "Supported versions"
table); a test (``tests/test_redmine_profile.py``) keeps the two in sync.
"""

from mcp_ticketing.redmine.p6x import Profile6x
from mcp_ticketing.redmine.p7x import Profile7x
from mcp_ticketing.redmine.p42 import Profile42
from mcp_ticketing.redmine.p51 import Profile51
from mcp_ticketing.redmine.profile import (
    RedmineVersionProfile,
    UnsupportedRedmineVersionError,
    is_supported,
    parse_version,
)

# Ordered from oldest to newest; ``select_profile`` returns the first profile
# whose ``supports()`` matches the declared version, and the **last** one
# (the newest) when no version is declared.
VERSION_PROFILES: list[type[RedmineVersionProfile]] = [
    Profile42,
    Profile51,
    Profile6x,
    Profile7x,
]


def select_profile(declared: str | None) -> RedmineVersionProfile:
    """Select a version profile from a declared version string.

    * ``declared`` is ``None`` / empty -> returns the **latest** profile
      (the last entry of :data:`VERSION_PROFILES`). The operator is expected
      to declare the real server version (see ``docs/redmine.md``); when they
      don't, the newest API surface is the best default. No server round-trip
      is performed.
    * otherwise the first profile whose ``supports()`` covers the parsed
      version is returned.
    * raises :class:`~mcp_ticketing.redmine.profile.RedmineVersionError`
      for an invalid version string, and
      :class:`~mcp_ticketing.redmine.profile.UnsupportedRedmineVersionError`
      when the declared version is outside the supported 4.2 – 7.x range.
    """
    if not declared or not declared.strip():
        return VERSION_PROFILES[-1]()
    version = parse_version(declared)
    if not is_supported(version):
        raise UnsupportedRedmineVersionError(
            f"Redmine version {declared} is not supported. "
            f"Supported majors: 4.x, 5.x, 6.x, 7.x (see docs/redmine.md). "
            f"Set MCP_REDMINE_VERSION to your server version."
        )
    for cls in VERSION_PROFILES:
        if cls().supports(version):
            return cls()
    raise UnsupportedRedmineVersionError(
        f"Redmine version {declared} is not supported. Supported majors: 4.x, 5.x, 6.x, 7.x (see docs/redmine.md)."
    )
