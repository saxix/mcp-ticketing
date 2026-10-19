"""Profile for Redmine 6.0 – 6.1.

Legacy naming (trackers). 6.0 is legacy stable, 6.1 is a stable series.
"""

from mcp_ticketing.redmine.base import RedmineBaseProfile


class Profile6x(RedmineBaseProfile):
    """Redmine 6.0 – 6.x (``6.0 <= version < 7``)."""

    name = "6.x"

    def __init__(self) -> None:
        super().__init__(has_issue_types=False)

    def supports(self, version: tuple[int, int]) -> bool:
        return (6, 0) <= version < (7, 0)
