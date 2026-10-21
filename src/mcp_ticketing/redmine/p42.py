"""Profile for Redmine 4.2 – 4.1 (the 4.x series at/after 4.2).

Legacy naming: issue types are exposed as *trackers*
(``trackers.json`` / ``tracker`` / ``tracker_id`` / ``fixed_version``).
"""

from mcp_ticketing.redmine.base import RedmineBaseProfile


class Profile42(RedmineBaseProfile):
    """Redmine 4.2 – 4.x (``4.2 <= version < 5``)."""

    name = "4.x"

    def __init__(self) -> None:
        super().__init__(has_issue_types=False)

    def supports(self, version: tuple[int, int]) -> bool:
        # Covers 4.2 through the rest of the 4.x series.
        return (4, 2) <= version < (5, 0)
