"""Version-profile registry and selection (no internal dependencies).

Kept in its own module so both :mod:`~mcp_ticketing.redmine.__init__` and
:mod:`~mcp_ticketing.redmine.connector` can import :func:`select_profile`
without a circular import (``__init__`` imports the connector, which must
not import back from ``__init__``).
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
# whose ``supports()`` matches the declared version.
VERSION_PROFILES: list[type[RedmineVersionProfile]] = [
    Profile42,
    Profile51,
    Profile6x,
    Profile7x,
]


def select_profile(declared: str | None) -> RedmineVersionProfile | None:
    """Select a version profile from a declared version string.

    * ``declared`` is ``None`` / empty -> returns ``None`` (the connector
      auto-detects via a single probe).
    * otherwise the first profile whose ``supports()`` covers the parsed
      version is instantiated and returned.
    * raises :class:`~mcp_ticketing.redmine.profile.RedmineVersionError`
      for an invalid version string, and
      :class:`~mcp_ticketing.redmine.profile.UnsupportedRedmineVersionError` when
      the declared version is outside the supported 4.2 – 7.x range.
    """
    if not declared or not declared.strip():
        return None
    version = parse_version(declared)
    if not is_supported(version):
        raise UnsupportedRedmineVersionError(
            f"Declared Redmine version {declared} is outside the supported range 4.2 – 7.x."
        )
    for cls in VERSION_PROFILES:
        if cls().supports(version):
            return cls()
    raise UnsupportedRedmineVersionError(
        f"Declared Redmine version {declared} is outside the supported range 4.2 – 7.x."
    )
