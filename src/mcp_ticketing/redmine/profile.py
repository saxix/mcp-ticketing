"""Version strategy for the Redmine backend.

Redmine's JSON REST API is stable across the supported range (4.2 – 7.x)
*except* for a few endpoint/field renames. Instead of probing at runtime
with try/except-404 fallbacks, the connector selects a
:class:`RedmineVersionProfile` **once at startup** from the declared
``MCP_REDMINE_VERSION`` — or, when that variable is empty, from the
*latest* profile in the registry — and then every operation asks the
profile *which* endpoint / field name to use. No ifs, no attempts, no
server round-trip for version detection: the operator knows the product
version and declares it (see ``docs/redmine.md`` for the supported majors).

Each profile declares:

* :meth:`supports` — the version range it covers (used by selection);
* the *varying* rules (issue-type endpoint + field names);
* the *invariant* rules, inherited from
  :class:`~mcp_ticketing.redmine.base.RedmineBaseProfile` (auth, statuses,
  ``my_account``, per-project versions, free-text param, 204 handling).
"""

import re
from abc import ABC, abstractmethod

SUPPORTED_MIN = (4, 2)
SUPPORTED_MAX = (7, 99)  # 7.x is open-ended


class RedmineVersionError(Exception):
    """Base class for version-related startup errors."""


class UnsupportedRedmineVersionError(RedmineVersionError):
    """A declared ``MCP_REDMINE_VERSION`` is not in the supported list."""


def parse_version(raw: str) -> tuple[int, int]:
    """Parse a version string like ``"6.1"``, ``"7"``, ``"4.2.1"`` to ``(major, minor)``.

    Raises :class:`RedmineVersionError` when the string is not a version.
    """
    raw = raw.strip()
    match = re.match(r"^(\d+)(?:\.(\d+))?", raw)
    if not match:
        raise RedmineVersionError(f"Invalid Redmine version: {raw!r} (expected e.g. '6.1').")
    major = int(match.group(1))
    minor = int(match.group(2)) if match.group(2) is not None else 0
    return (major, minor)


def is_supported(version: tuple[int, int]) -> bool:
    """Return True when ``version`` is within the supported 4.2 – 7.x range."""
    return SUPPORTED_MIN <= version <= SUPPORTED_MAX


class RedmineVersionProfile(ABC):
    """Strategy for a Redmine major-version range.

    Concrete subclasses (``Profile42`` … ``Profile7x``) inherit the invariant
    rules from :class:`~mcp_ticketing.redmine.base.RedmineBaseProfile` and
    override only the issue-type endpoint + field names.
    """

    #: Human-readable profile label, e.g. ``"4.x"`` / ``"7.x"``.
    name: str = "base"

    # ── selection ──────────────────────────────────────────────
    @abstractmethod
    def supports(self, version: tuple[int, int]) -> bool:
        """Return True when this profile covers ``version``."""

    # ── rules that vary between profiles ───────────────────────
    @abstractmethod
    def issue_type_endpoint(self) -> str:
        """Endpoint listing issue types: ``"trackers.json"`` or ``"issue_types.json"``."""

    @abstractmethod
    def issue_type_field(self) -> str:
        """Field name for the issue type in a GET payload: ``"tracker"`` or ``"issue_type"``."""

    @abstractmethod
    def issue_type_id_field(self) -> str:
        """Field name for the issue type id in a write payload: ``"tracker_id"`` or ``"issue_type_id"``."""

    @abstractmethod
    def version_field(self) -> str:
        """Field name for the version in a GET payload: ``"fixed_version"`` or ``"version"``."""

    # ── rules invariant across the range (defaults in base.py) ─
    def status_endpoint(self) -> str:
        return "issues_statuses.json"

    def list_versions_path(self, project_id: int | str) -> str:
        return f"projects/{project_id}/versions.json"

    def free_text_param(self, term: str) -> tuple[str, str]:
        return ("subject", f"~{term}")

    def put_returns_204(self) -> bool:
        return True
