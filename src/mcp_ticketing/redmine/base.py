"""Invariant rules for Redmine 4.2 – 7.x.

:class:`RedmineBaseProfile` centralises everything that does **not** change
across the supported range, so the four concrete profiles only override
:meth:`~RedmineVersionProfile.supports` and pass their ``has_issue_types``
flag. It also hosts the version-independent helpers (wiki/HTML
normalisation, Markdown→HTML, field aliases, priority synonyms) that the
connector reuses.
"""

from __future__ import annotations

import html
import re

from mcp_ticketing.redmine.profile import RedmineVersionProfile

# ──────────────────────────────────────────────
#  Field aliases (new Redmine 7.x vs 4.2 – 6.x)
# ──────────────────────────────────────────────

FIELD_ALIASES: dict[str, tuple[str, str]] = {
    "issue_type": ("issue_type", "tracker"),
    "version": ("version", "fixed_version"),
}

# ──────────────────────────────────────────────
#  Priority synonyms (per-instance, matched by name)
# ──────────────────────────────────────────────

PRIORITY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "urgent": ("urgent", "urgente", "immediat"),
    "high": ("high", "alta", "elevat"),
    "normal": ("normal", "normale", "media", "medio"),
    "low": ("low", "bassa", "basso"),
}

PRIORITY_LEVEL_NAMES: dict[int, str] = {1: "urgent", 2: "high", 3: "normal", 4: "low"}

# ──────────────────────────────────────────────
#  Generic state words -> Redmine status names to try (EN + IT)
# ──────────────────────────────────────────────

STATE_TO_STATUSES: dict[str, tuple[str, ...]] = {
    "open": ("New", "In progress", "Reopened", "Nuova", "In lavorazione", "Riaperta", "Aperta"),
    "active": ("In progress", "New", "Reopened", "In lavorazione", "Nuova", "Aperta"),
    "closed": ("Closed", "Done", "Resolved", "Chiusa", "Completata", "Risolta"),
    "resolved": ("Resolved", "Closed", "Done", "Risolta", "Chiusa", "Completata"),
    "done": ("Done", "Closed", "Completata", "Chiusa"),
    "new": ("New", "Nuova"),
    "reopened": ("Reopened", "Riaperta"),
}

OPEN_STATES: tuple[str, ...] = (
    "open",
    "active",
    "new",
    "reopened",
    "in progress",
    "nuova",
    "in lavorazione",
    "riaperta",
    "aperta",
)


# ──────────────────────────────────────────────
#  Version-independent text helpers
# ──────────────────────────────────────────────


def strip_wiki(text: str) -> str:
    """Normalise Redmine wiki markup and HTML into readable plain text.

    Redmine journal notes are stored as wiki markup (``h1.``-``h6.``
    headings, ``* ``/``** `` bullets, ``# ``/``## `` ordered lists,
    ``| `` tables, ``*text*``/``**text**`` emphasis, ``{{code}}`` blocks)
    and may also contain HTML. Both are reduced to plain text so the
    comments render correctly in a terminal TUI.
    """

    # Wiki tables (| a | b |) -> tab-separated, one row per line.
    def _table_repl(match: re.Match) -> str:
        cells = match.group(0)
        cells = re.sub(r"^\|", "", cells)
        cells = re.sub(r"\|$", "", cells)
        return "\t".join(c.strip() for c in cells.split("|"))

    text = re.sub(r"^\|.*$", _table_repl, text, flags=re.MULTILINE)
    # Wiki headings (h1. Title) -> plain line.
    text = re.sub(r"^h([1-6])\.\s*", "", text, flags=re.MULTILINE)
    # Wiki list bullets (* item) and ordered items (# item).
    text = re.sub(r"^(?:\*{1,2}|#{1,2})\s+", "", text, flags=re.MULTILINE)
    # Emphasis and formatting markers.
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_{2,}(.+?)_{2,}", r"\1", text)
    # Single-underscore italic (_text_) only at word boundaries, so that
    # identifiers like STATO_DISTINTA are left untouched.
    text = re.sub(r"(?<![\w_])_([^_]+)_(?![\w_])", r"\1", text)
    text = re.sub(r"{{(.+?)}}", r"\1", text, flags=re.DOTALL)
    # HTML tags.
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def markdown_to_html(text: str) -> str:
    """Convert Markdown to HTML for Redmine's HTML description field.

    Redmine stores the description as HTML. If the caller passes Markdown
    (headings, **bold**, lists, tables, fenced code, etc.) we render it to
    HTML so it displays correctly. Plain HTML is passed through unchanged
    (same heuristic as the Azure connector).
    """
    if not text:
        return text
    markdown_markers = ("**", "__", "```", "## ", " - ", "- ", "* ", "1. ", "> ", "[", "](", " | ", "|")
    looks_like_markdown = any(m in text for m in markdown_markers)
    looks_like_html = text.strip().startswith("<") and ">" in text
    if looks_like_html and not looks_like_markdown:
        return text
    try:
        import markdown  # local import so a missing dep doesn't break module import

        return markdown.markdown(text, extensions=["extra", "tables", "fenced_code", "codehilite"])
    except ImportError:
        return text


def render_mermaid_png(diagram: str, scale: float = 2.0) -> bytes:
    """Render a Mermaid diagram source to a high-resolution PNG (bundled ``mermaidx``)."""
    from mermaidx import render

    return render(diagram).png(scale=scale, background="#ffffff")


def first_field(issue: dict, logical: str) -> dict:
    """Return the issue-type or version sub-dict, tolerating new/legacy field names.

    ``logical`` is one of :data:`FIELD_ALIASES` keys.
    """
    new, legacy = FIELD_ALIASES[logical]
    value = issue.get(new) or issue.get(legacy) or {}
    return value if isinstance(value, dict) else {}


def match_priority_name(name: str, wanted_level: str) -> bool:
    """Return True when a priority ``name`` matches a wanted level (EN/IT)."""
    lowered = name.lower()
    return any(syn in lowered for syn in PRIORITY_SYNONYMS.get(wanted_level, ()))


# ──────────────────────────────────────────────
#  Base profile
# ──────────────────────────────────────────────


class RedmineBaseProfile(RedmineVersionProfile):
    """Invariant rules for Redmine 4.2 – 7.x.

    Subclasses set :attr:`name`, implement :meth:`supports`, and pass
    ``has_issue_types`` to ``super().__init__`` (7.x → True, 4.2 – 6.x →
    False). The endpoint/field-name methods derive from that flag.
    """

    def __init__(self, has_issue_types: bool) -> None:
        # ``has_issue_types`` distinguishes 7.x (True) from 4.2–6.x (False).
        # The capability flags are derived from the *profile range* (what this
        # profile declares), not from the probed server version — the version
        # is validated against ``supports()`` by the connector at startup.
        self._has_issue_types = has_issue_types

    # ── auth ───────────────────────────────────────────────────
    def headers(self, client: object) -> dict[str, str]:
        """Auth headers — API key across the whole supported range."""
        return {
            "X-Redmine-API-Key": client.api_key,  # type: ignore[attr-defined]
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── varying rules (derived from the has_issue_types flag) ──
    def issue_type_endpoint(self) -> str:
        return "issue_types.json" if self._has_issue_types else "trackers.json"

    def issue_type_field(self) -> str:
        return "issue_type" if self._has_issue_types else "tracker"

    def issue_type_id_field(self) -> str:
        return "issue_type_id" if self._has_issue_types else "tracker_id"

    def version_field(self) -> str:
        return "version" if self._has_issue_types else "fixed_version"
