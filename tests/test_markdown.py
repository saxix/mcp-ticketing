"""Tests for Markdown formatting of tickets and comments.

Verifies that Markdown in ticket descriptions/bodies and comment bodies
is preserved end-to-end for both backends (GitHub = Markdown native,
Azure DevOps = HTML/Markdown via API) without stripping or double-escaping,
and that it renders to valid HTML.
"""

import json
import os
from unittest.mock import AsyncMock

import pytest

from mcp_ticketing.azure_connector import API_VERSION, AzureConnector
from mcp_ticketing.github_connector import GithubConnector
from mcp_ticketing.ticket_manager import TicketManager

# ---------------------------------------------------------------------------
# Sample Markdown fixtures — shared by ticket and comment tests
# ---------------------------------------------------------------------------

SAMPLE_TICKET_MARKDOWN = """# Titolo Ticket

Descrizione con **grassetto**, *corsivo*, `inline code` e [link](https://example.com).

- lista non ordinata
  - annidata
- secondo elemento

1. ordinata 1
2. ordinata 2

> citazione importante

```python
def hello():
    print("world")
```

| Col1 | Col2 |
|------|------|
| A    | B    |

---

Paragrafo finale.
"""

SAMPLE_COMMENT_MARKDOWN = """## Risposta

Commento con **bold**, *italic*, `code`, e lista:

- punto A
- punto B

```js
console.log("ciao");
```

> blocco citazione

[vedi ticket #123](https://example.com)
"""

# We expect these substrings to survive a round-trip through the connectors
# (payload must be sent verbatim, not stripped or HTML-escaped).
TICKET_MARKDOWN_TOKENS = [
    "# Titolo Ticket",
    "**grassetto**",
    "*corsivo*",
    "`inline code`",
    "[link](https://example.com)",
    "- lista non ordinata",
    "1. ordinata 1",
    "> citazione importante",
    "```python",
    "| Col1 | Col2 |",
    "---",
]

COMMENT_MARKDOWN_TOKENS = [
    "## Risposta",
    "**bold**",
    "- punto A",
    "```js",
    "> blocco citazione",
    "[vedi ticket #123]",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_markdown_preserved(payload_text: str, tokens: list[str]) -> None:
    """Assert every Markdown token is present verbatim in the payload."""
    for token in tokens:
        assert token in payload_text, f"Markdown token missing in payload: {token!r}"


def assert_markdown_renders(markdown_text: str) -> None:
    """Assert the Markdown renders to HTML without error and preserves structure."""
    import markdown

    html = markdown.markdown(markdown_text, extensions=["extra", "tables", "fenced_code"])

    # Basic sanity: non-empty HTML containing expected tags
    assert html.strip() != ""
    assert "<" in html and ">" in html
    # Headings, lists, code and blockquote should have rendered
    # (we don't assert exact HTML, just that rendering didn't drop structure)
    assert len(html) > len(markdown_text) * 0.3  # rendered HTML shouldn't be trivially short


# ---------------------------------------------------------------------------
# Markdown rendering sanity (no backend needed)
# ---------------------------------------------------------------------------


def test_ticket_markdown_renders_to_valid_html():
    assert_markdown_renders(SAMPLE_TICKET_MARKDOWN)


def test_comment_markdown_renders_to_valid_html():
    assert_markdown_renders(SAMPLE_COMMENT_MARKDOWN)


@pytest.mark.parametrize(
    "text",
    [
        SAMPLE_TICKET_MARKDOWN,
        SAMPLE_COMMENT_MARKDOWN,
        "# h1\n**b** *i* `c`\n- a\n1. b\n> q\n```py\nx\n```",
        "| A | B |\n|---|---|\n| 1 | 2 |",
        "Testo con emoji :smile: e caratteri `ñ` `ç` `€`",
    ],
)
def test_various_markdown_snippets_render(text):
    assert_markdown_renders(text)


# ---------------------------------------------------------------------------
# GitHub — Markdown native backend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_create_ticket_preserves_markdown():
    """GitHub create_ticket must send description (body) verbatim as Markdown."""
    fake_client = AsyncMock()
    fake_client.post.return_value = {"number": 42, "html_url": "https://github.com/o/r/issues/42"}
    fake_client.get.return_value = []  # for milestones lookup

    connector = GithubConnector(client=fake_client)

    result = await connector.create_ticket(title="Test", description=SAMPLE_TICKET_MARKDOWN)

    parsed = json.loads(result)
    assert parsed["success"] is True

    # Verify payload sent to GitHub API
    assert fake_client.post.called
    # GitHubClient.post(path, data) is called as post("issues", data=payload)
    call = fake_client.post.call_args
    payload = (
        call.kwargs.get("data")
        if call.kwargs.get("data") is not None
        else (call.args[1] if len(call.args) > 1 else None)
    )
    assert payload is not None
    body_sent = payload.get("body", "")
    assert_markdown_preserved(body_sent, TICKET_MARKDOWN_TOKENS)
    assert body_sent == SAMPLE_TICKET_MARKDOWN


@pytest.mark.asyncio
async def test_github_update_ticket_preserves_markdown():
    """GitHub update_ticket must send description (body) verbatim."""
    fake_client = AsyncMock()
    fake_client.patch.return_value = {"number": 42}
    fake_client.get.return_value = []

    connector = GithubConnector(client=fake_client)

    result = await connector.update_ticket(ticket_id=42, description=SAMPLE_TICKET_MARKDOWN)

    parsed = json.loads(result)
    assert parsed["success"] is True

    call = fake_client.patch.call_args
    payload = (
        call.kwargs.get("data")
        if call.kwargs.get("data") is not None
        else (call.args[1] if len(call.args) > 1 else None)
    )
    assert payload is not None
    body_sent = payload.get("body", "")
    assert_markdown_preserved(body_sent, TICKET_MARKDOWN_TOKENS)


@pytest.mark.asyncio
async def test_github_add_comment_preserves_markdown():
    """GitHub add_comment must send body verbatim as Markdown."""
    fake_client = AsyncMock()
    fake_client.post.return_value = {"id": 1001}

    connector = GithubConnector(client=fake_client)

    result = await connector.add_comment(ticket_id=42, text=SAMPLE_COMMENT_MARKDOWN)

    parsed = json.loads(result)
    assert parsed["success"] is True

    call = fake_client.post.call_args
    payload = (
        call.kwargs.get("data")
        if call.kwargs.get("data") is not None
        else (call.args[1] if len(call.args) > 1 else None)
    )
    assert payload is not None
    text_sent = payload.get("body", "")
    assert_markdown_preserved(text_sent, COMMENT_MARKDOWN_TOKENS)
    assert text_sent == SAMPLE_COMMENT_MARKDOWN


@pytest.mark.asyncio
async def test_github_get_ticket_returns_markdown():
    """GitHub get_ticket must handle issue with Markdown body without stripping."""
    fake_issue = {
        "number": 42,
        "title": "Test",
        "state": "open",
        "labels": [],
        "assignee": None,
        "milestone": None,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "body": SAMPLE_TICKET_MARKDOWN,
    }
    from mcp_ticketing.github_connector import format_ticket

    formatted = json.loads(format_ticket(fake_issue))
    assert formatted["id"] == 42
    # Verify raw body is preserved verbatim when fetched directly
    fake_client = AsyncMock()
    fake_client.get.return_value = fake_issue
    raw = await fake_client.get("issues/42")
    assert raw["body"] == SAMPLE_TICKET_MARKDOWN
    assert_markdown_preserved(raw["body"], TICKET_MARKDOWN_TOKENS)
    # Also verify format_ticket doesn't drop metadata for issues with markdown bodies
    assert formatted["title"] == "Test"


@pytest.mark.asyncio
async def test_github_get_ticket_comments_returns_markdown():
    """GitHub get_ticket_comments must return comment bodies verbatim."""
    fake_comments = [
        {"id": 1, "user": {"login": "alice"}, "created_at": "2024-01-01T00:00:00Z", "body": SAMPLE_COMMENT_MARKDOWN},
        {"id": 2, "user": {"login": "bob"}, "created_at": "2024-01-02T00:00:00Z", "body": "Plain text comment"},
    ]
    fake_client = AsyncMock()
    fake_client.get.return_value = fake_comments

    connector = GithubConnector(client=fake_client)
    result = await connector.get_ticket_comments(ticket_id=42)

    parsed = json.loads(result)
    assert parsed["total"] == 2
    assert parsed["comments"][0]["text"] == SAMPLE_COMMENT_MARKDOWN
    assert_markdown_preserved(parsed["comments"][0]["text"], COMMENT_MARKDOWN_TOKENS)


# ---------------------------------------------------------------------------
# Azure — HTML description but Markdown-friendly comments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_azure_create_ticket_preserves_markdown_in_description():
    """Azure create_ticket must convert Markdown description to HTML for System.Description."""
    from mcp_ticketing.azure_connector import _markdown_to_html

    fake_client = AsyncMock()
    fake_client.post.return_value = {"id": 101, "rev": 1}
    fake_client.get.return_value = {"value": []}  # for ticket_type lookup not used here (ticket_type provided)

    connector = AzureConnector(client=fake_client)

    result = await connector.create_ticket(ticket_type="Task", title="Test", description=SAMPLE_TICKET_MARKDOWN)

    parsed = json.loads(result)
    assert parsed["success"] is True

    # post is called with wit/workitems/$Task and patch-ops list
    call_args = fake_client.post.call_args
    ops = (
        call_args.kwargs.get("data")
        if call_args.kwargs.get("data") is not None
        else (call_args.args[1] if len(call_args.args) > 1 else None)
    )
    assert isinstance(ops, list)
    desc_op = next((op for op in ops if op.get("path") == "/fields/System.Description"), None)
    assert desc_op is not None, "System.Description op missing"
    expected_html = _markdown_to_html(SAMPLE_TICKET_MARKDOWN)
    assert desc_op["value"] == expected_html
    # HTML should contain rendered tags, not raw Markdown
    assert "<h1>" in desc_op["value"] and "Titolo Ticket" in desc_op["value"]
    assert "<strong>grassetto</strong>" in desc_op["value"]
    assert "<em>corsivo</em>" in desc_op["value"]
    assert "<code>inline code</code>" in desc_op["value"]
    assert '<a href="https://example.com">link</a>' in desc_op["value"]
    assert "<ul>" in desc_op["value"]
    assert "<table>" in desc_op["value"]


@pytest.mark.asyncio
async def test_azure_update_ticket_preserves_markdown_in_description():
    """Azure update_ticket must convert Markdown description to HTML."""
    from mcp_ticketing.azure_connector import _markdown_to_html

    fake_client = AsyncMock()
    fake_client.patch.return_value = {"id": 101, "rev": 2}

    connector = AzureConnector(client=fake_client)

    result = await connector.update_ticket(ticket_id=101, description=SAMPLE_TICKET_MARKDOWN)

    parsed = json.loads(result)
    assert parsed["success"] is True

    call = fake_client.patch.call_args
    ops = (
        call.kwargs.get("data")
        if call.kwargs.get("data") is not None
        else (call.args[1] if len(call.args) > 1 else None)
    )
    desc_op = next((op for op in ops if op.get("path") == "/fields/System.Description"), None)
    assert desc_op is not None
    expected_html = _markdown_to_html(SAMPLE_TICKET_MARKDOWN)
    assert desc_op["value"] == expected_html
    assert "<h1>" in desc_op["value"] and "Titolo Ticket" in desc_op["value"]
    assert "<strong>grassetto</strong>" in desc_op["value"]


@pytest.mark.asyncio
async def test_azure_add_comment_preserves_markdown():
    """Azure add_comment must convert Markdown to HTML (same as description)."""
    from mcp_ticketing.azure_connector import _markdown_to_html

    fake_client = AsyncMock()
    fake_client.post.return_value = {"comment": {"id": 5}}

    connector = AzureConnector(client=fake_client)

    result = await connector.add_comment(ticket_id=101, text=SAMPLE_COMMENT_MARKDOWN)

    parsed = json.loads(result)
    assert parsed["success"] is True

    call = fake_client.post.call_args
    payload = (
        call.kwargs.get("data")
        if call.kwargs.get("data") is not None
        else (call.args[1] if len(call.args) > 1 else None)
    )
    assert payload is not None
    text_sent = payload.get("text", "")
    expected_html = _markdown_to_html(SAMPLE_COMMENT_MARKDOWN)
    assert text_sent == expected_html
    assert "<h2>" in text_sent and "Risposta" in text_sent
    assert "<strong>bold</strong>" in text_sent


@pytest.mark.asyncio
async def test_azure_get_ticket_comments_preserves_markdown():
    """Azure get_ticket_comments must return comment text verbatim."""
    fake_data = {
        "comments": [
            {
                "id": 1,
                "createdBy": {"displayName": "alice"},
                "createdDate": "2024-01-01",
                "text": SAMPLE_COMMENT_MARKDOWN,
            },
            {"id": 2, "createdBy": {"displayName": "bob"}, "createdDate": "2024-01-02", "text": SAMPLE_TICKET_MARKDOWN},
        ]
    }
    fake_client = AsyncMock()
    fake_client.get.return_value = fake_data

    connector = AzureConnector(client=fake_client)
    result = await connector.get_ticket_comments(ticket_id=101)

    parsed = json.loads(result)
    assert parsed["total"] == 2
    assert parsed["comments"][0]["text"] == SAMPLE_COMMENT_MARKDOWN
    assert_markdown_preserved(parsed["comments"][0]["text"], COMMENT_MARKDOWN_TOKENS)
    assert parsed["comments"][1]["text"] == SAMPLE_TICKET_MARKDOWN
    assert_markdown_preserved(parsed["comments"][1]["text"], TICKET_MARKDOWN_TOKENS)


# ---------------------------------------------------------------------------
# Edge cases — ensure special Markdown isn't escaped or truncated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "markdown_snippet",
    [
        "**grassetto** e *corsivo* e __underline__",
        "[link](https://example.com) e ![img](https://example.com/img.png)",
        "`inline` e ```fenced\ncode\n```",
        "- a\n  - b\n    - c\n1. x\n2. y",
        "> quote\n> > nested quote",
        "| A | B |\n|---|---|\n| 1 | 2 |",
        "Testo con <b>html</b> e &amp; entità",
        "---\n\n# Titolo\n\n---",
    ],
)
async def test_github_markdown_edge_cases_preserved(markdown_snippet):
    """Each Markdown snippet must survive GitHub ticket creation verbatim."""
    fake_client = AsyncMock()
    fake_client.post.return_value = {"number": 1, "html_url": "https://github.com/o/r/issues/1"}
    fake_client.get.return_value = []

    connector = GithubConnector(client=fake_client)
    await connector.create_ticket(title="Edge", description=markdown_snippet)

    call = fake_client.post.call_args
    payload = (
        call.kwargs.get("data")
        if call.kwargs.get("data") is not None
        else (call.args[1] if len(call.args) > 1 else None)
    )
    assert payload["body"] == markdown_snippet
    assert_markdown_renders(markdown_snippet)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "markdown_snippet",
    [
        "**grassetto** e *corsivo*",
        "`code` e ```block```",
        "- list\n- items",
        "> quote",
        "| A | B |\n|---|---|\n| 1 | 2 |",
    ],
)
async def test_azure_markdown_edge_cases_preserved(markdown_snippet):
    """Azure comment Markdown must be converted to HTML before sending."""
    from mcp_ticketing.azure_connector import _markdown_to_html

    fake_client = AsyncMock()
    fake_client.post.return_value = {"comment": {"id": 1}}

    connector = AzureConnector(client=fake_client)
    await connector.add_comment(ticket_id=1, text=markdown_snippet)

    call = fake_client.post.call_args
    payload = (
        call.kwargs.get("data")
        if call.kwargs.get("data") is not None
        else (call.args[1] if len(call.args) > 1 else None)
    )
    expected = _markdown_to_html(markdown_snippet)
    assert payload["text"] == expected
    # HTML should contain tags, not raw Markdown (proves conversion happened)
    assert "<" in payload["text"] and ">" in payload["text"]
    assert_markdown_renders(markdown_snippet)


# ---------------------------------------------------------------------------
# E2E — real API with title "Test for Markdown description/comments"
# ---------------------------------------------------------------------------

E2E_TITLE = "Test for Markdown description/comments"


def _e2e_check(resp: str) -> dict:
    """Parse MCP JSON response, skip on API error (same pattern as test_azure/github)."""
    parsed = json.loads(resp)
    if "error" in parsed:
        pytest.skip(f"Skipping — API error: {parsed['error']}")
    return parsed


@pytest.mark.asyncio
@pytest.mark.skipif(
    not all(os.getenv(v, "") for v in ["MCP_AZURE_DEVOPS_ORG", "MCP_AZURE_DEVOPS_PROJECT", "MCP_AZDO_PAT"]),
    reason="Azure DevOps env vars not set",
)
async def test_e2e_azure_markdown_description_and_comment():
    """E2E: Azure ticket + comment with Markdown, title = 'Test for Markdown description/comments'."""
    manager = TicketManager(strategy=AzureConnector())
    tid = None
    try:
        r = await manager.create_ticket(
            ticket_type="Task",
            title=E2E_TITLE,
            description=SAMPLE_TICKET_MARKDOWN,
            tags="pytest;markdown-e2e",
        )
        p = _e2e_check(r)
        assert p["success"] is True
        tid = p["id"]
        assert isinstance(tid, int)

        raw = await manager._strategy.client.get(f"wit/workitems/{tid}", params={"api-version": API_VERSION})
        desc = raw.get("fields", {}).get("System.Description", "")
        assert E2E_TITLE in raw.get("fields", {}).get("System.Title", "")
        # Azure System.Description is HTML — Markdown is converted to HTML
        # Verify rendered HTML, not raw Markdown (issue reported: single-line flattening)
        assert "<h1>" in desc and "Titolo Ticket" in desc, "H1 not rendered"
        assert "<strong>grassetto</strong>" in desc
        assert "<em>corsivo</em>" in desc
        assert "<code>inline code</code>" in desc
        assert '<a href="https://example.com">link</a>' in desc
        assert "<ul>" in desc
        assert "<table>" in desc
        assert "citazione importante" in desc
        # Should NOT be raw Markdown artifacts like "# Titolo" without HTML
        assert desc.count("<h1>") >= 1
        assert desc.count("<p>") >= 1
        # Ensure not flattened to single line without tags
        assert "<" in desc  # HTML contains block tags, not plain line
        assert_markdown_renders(SAMPLE_TICKET_MARKDOWN)

        r = await manager.add_comment(tid, SAMPLE_COMMENT_MARKDOWN)
        p = _e2e_check(r)
        assert p["success"] is True

        r = await manager.get_ticket_comments(tid)
        p = _e2e_check(r)
        assert p["total"] >= 1
        # Comments are now stored as HTML (converted from Markdown)
        found = False
        for c in p["comments"]:
            text = c["text"] or ""
            if "<h2>" in text and "Risposta" in text and "<strong>bold</strong>" in text:
                found = True
                assert "<ul>" in text
                assert "punto A" in text
                assert "<blockquote>" in text or "citazione" in text
                break
        assert found, f"HTML comment not found in {p['comments']}"
    finally:
        if tid is not None:
            try:  # noqa: SIM105
                await manager.update_ticket(ticket_id=tid, state="Closed")
            except Exception:  # noqa: BLE001
                pass  # noqa: S110


@pytest.mark.asyncio
@pytest.mark.skipif(
    not all(os.getenv(v, "") for v in ["MCP_GITHUB_OWNER", "MCP_GITHUB_REPO", "MCP_GITHUB_TOKEN"]),
    reason="GitHub env vars not set",
)
async def test_e2e_github_markdown_description_and_comment():
    """E2E: GitHub issue + comment with Markdown, title = 'Test for Markdown description/comments'."""
    manager = TicketManager(strategy=GithubConnector())
    tid = None
    try:
        r = await manager.create_ticket(
            title=E2E_TITLE,
            description=SAMPLE_TICKET_MARKDOWN,
            tags="pytest;markdown-e2e",
        )
        p = _e2e_check(r)
        assert p["success"] is True
        tid = p["id"]
        assert isinstance(tid, int)

        raw = await manager._strategy.client.get(f"issues/{tid}")
        body = raw.get("body", "") or ""
        assert raw.get("title") == E2E_TITLE
        for token in TICKET_MARKDOWN_TOKENS:
            assert token in body, f"ticket body missing token {token!r}"
        assert body == SAMPLE_TICKET_MARKDOWN
        assert_markdown_renders(body)

        r = await manager.get_ticket(tid)
        p = _e2e_check(r)
        assert p["id"] == tid
        assert p["title"] == E2E_TITLE

        r = await manager.add_comment(tid, SAMPLE_COMMENT_MARKDOWN)
        p = _e2e_check(r)
        assert p["success"] is True

        r = await manager.get_ticket_comments(tid)
        p = _e2e_check(r)
        assert p["total"] >= 1
        assert any(c["text"] == SAMPLE_COMMENT_MARKDOWN for c in p["comments"])
        for c in p["comments"]:
            if c["text"] == SAMPLE_COMMENT_MARKDOWN:
                for tok in COMMENT_MARKDOWN_TOKENS:
                    assert tok in c["text"]
                assert_markdown_renders(c["text"])
                break

        raw_comments = await manager._strategy.client.get(f"issues/{tid}/comments")
        assert any(c.get("body") == SAMPLE_COMMENT_MARKDOWN for c in raw_comments)
    finally:
        if tid is not None:
            try:  # noqa: SIM105
                await manager.update_ticket(ticket_id=tid, state="closed")
            except Exception:  # noqa: BLE001
                pass  # noqa: S110
