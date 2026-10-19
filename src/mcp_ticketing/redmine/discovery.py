"""Instance-level discovery for Redmine metadata.

Priorities are an *instance* configuration (each Redmine installation
defines its own set and numeric IDs are not portable). No supported version
exposes a ``priorities.json`` endpoint, so the only way to learn the
available priorities is to read them from existing issues.

:func:`discover_priorities` does this in a **single** request (one page of
100 issues, global — not scoped to the project, so a priority unused in the
project is still found). The result is cached per connector instance, so the
cost is paid once, not per operation.
"""

from __future__ import annotations

from mcp_ticketing.redmine.base import PRIORITY_SYNONYMS

PAGE_SIZE = 100

_PRIORITY_LEVELS = {1: "urgent", 2: "high", 3: "normal", 4: "low"}


async def discover_priorities(client) -> list[dict]:
    """Return the distinct priorities seen on a single global page of issues.

    Returns a list of ``{"id": <int>, "name": <str>}`` dicts. An empty list
    means either the instance has no issues or the authenticated user cannot
    read any; callers should surface a clear "no priorities found" error
    rather than retrying.
    """
    data = await client.get("issues.json", params={"limit": PAGE_SIZE, "offset": 0})
    seen: dict[str, dict] = {}
    for issue in (data or {}).get("issues", []):
        p = issue.get("priority") or {}
        if p.get("id") is not None:
            seen[str(p.get("id"))] = {"id": p.get("id"), "name": p.get("name", "")}
    return list(seen.values())


def resolve_priority_id(priorities: list[dict], level: int) -> int | None:
    """Map the protocol's 1–4 scale to an instance priority id by name.

    ``level`` is 1 (urgent) … 4 (low). Returns ``None`` when no discovered
    priority matches the level's synonyms (English + Italian).
    """
    wanted = _PRIORITY_LEVELS.get(level)
    if wanted is None:
        return None
    synonyms = PRIORITY_SYNONYMS.get(wanted, (wanted,))
    for p in priorities:
        name = (p.get("name") or "").lower()
        if not name:
            continue
        for syn in synonyms:
            # Normalised substring match (no wildcards): "immediat" matches
            # "Immediato", "elevat" matches "Elevata", etc.
            if syn.lower() in name:
                return p.get("id")
    return None
