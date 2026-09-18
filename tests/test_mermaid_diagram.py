"""Unit tests for the Mermaid diagram helpers.

These tests verify that:
- on GitHub the Mermaid source is appended at the bottom of a ticket body,
  collected under a single 'Diagrams' section, without hitting any network;
- on Azure the Mermaid source is rendered to a valid, zoom-friendly PNG.
"""

from mcp_ticketing.azure_connector import _render_mermaid_png
from mcp_ticketing.github_connector import append_mermaid_markdown

DIAGRAM = "flowchart TD\n  A[Start] --> B[End]"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# ──────────────────────────────────────────────
#  GitHub (Markdown body)
# ──────────────────────────────────────────────


def test_markdown_diagram_appended_at_bottom():
    body = "Ticket description"
    result = append_mermaid_markdown(body, DIAGRAM)

    assert result.startswith("Ticket description")
    assert result.index("Ticket description") < result.index("## Diagrams")
    assert result.index("## Diagrams") < result.index("```mermaid")
    assert result.endswith("```\n")
    assert "```mermaid\nflowchart TD\n  A[Start] --> B[End]\n```" in result


def test_markdown_empty_body():
    result = append_mermaid_markdown("", DIAGRAM, title="Flow")

    assert result.startswith("## Diagrams")
    assert "### Flow" in result
    assert result.endswith("```\n")


def test_markdown_reuses_existing_section():
    body = "Intro\n\n## Diagrams\n\n### Old\n\n```mermaid\nfoo --> bar\n```"
    result = append_mermaid_markdown(body, DIAGRAM, title="New")

    assert result.count("## Diagrams") == 1
    assert result.index("foo --> bar") < result.index("flowchart TD")
    assert result.endswith("```\n")


def test_markdown_custom_section():
    result = append_mermaid_markdown("body", DIAGRAM, section="Architecture")

    assert "## Architecture" in result
    assert "## Diagrams" not in result


# ──────────────────────────────────────────────
#  Azure (local PNG rendering)
# ──────────────────────────────────────────────


def test_render_mermaid_png_signature():
    png = _render_mermaid_png(DIAGRAM)

    assert png[:8] == PNG_SIGNATURE
    assert len(png) > 8


def test_render_mermaid_png_zoom_quality():
    base = _render_mermaid_png(DIAGRAM, scale=1.0)
    zoomed = _render_mermaid_png(DIAGRAM, scale=2.0)

    assert base[:8] == PNG_SIGNATURE
    assert zoomed[:8] == PNG_SIGNATURE
    assert len(zoomed) > len(base)
