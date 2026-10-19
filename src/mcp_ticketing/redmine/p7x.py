"""Profile for Redmine 7.0 – 7.x (current stable series).

New naming: issue types are exposed as *issue types*
(``issue_types.json`` / ``issue_type`` / ``issue_type_id`` / ``version``).
"""

from mcp_ticketing.redmine.base import RedmineBaseProfile


class Profile7x(RedmineBaseProfile):
    """Redmine 7.0 – 7.x (``version >= 7.0``)."""

    name = "7.x"

    def __init__(self) -> None:
        super().__init__(has_issue_types=True)

    def supports(self, version: tuple[int, int]) -> bool:
        return version >= (7, 0)
