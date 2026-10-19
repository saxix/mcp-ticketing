"""Profile for Redmine 5.0 – 5.1.

Legacy naming (trackers). 5.0 is EOL and 5.1 is legacy (security-only), but
both are still supported.
"""

from mcp_ticketing.redmine.base import RedmineBaseProfile


class Profile51(RedmineBaseProfile):
    """Redmine 5.0 – 5.x (``5.0 <= version < 6``)."""

    name = "5.x"

    def __init__(self) -> None:
        super().__init__(has_issue_types=False)

    def supports(self, version: tuple[int, int]) -> bool:
        return (5, 0) <= version < (6, 0)
