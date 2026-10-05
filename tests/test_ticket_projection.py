"""Tests for the flat ticket projection shared by the MCP tools.

Regression coverage: ``format_ticket`` used to drop System.Description
(Azure) and ``body`` (GitHub) entirely, so no MCP tool could ever return a
ticket description. The description is now opt-in via
``include_description`` — ``get_ticket`` enables it, ``search_tickets``
does not, so list responses stay light.
"""

import json
from unittest.mock import AsyncMock

import pytest

from mcp_ticketing.azure_connector import AzureConnector
from mcp_ticketing.azure_connector import format_ticket as azure_format_ticket
from mcp_ticketing.github_connector import GithubConnector
from mcp_ticketing.github_connector import format_ticket as github_format_ticket

AZURE_ITEM = {
    "id": 345160,
    "fields": {
        "System.Title": "Configurable Payee Type",
        "System.WorkItemType": "Enhancement",
        "System.State": "Backlog",
        "System.AreaPath": "ICTD-HCT-MIS\\HOPE",
        "System.IterationPath": "ICTD-HCT-MIS\\2026\\2026 Sep 2nd",
        "System.CreatedDate": "2026-10-02T00:00:00Z",
        "System.ChangedDate": "2026-10-02T00:00:00Z",
        "System.Description": "<h1>Problem</h1><p>Payee type must be configurable.</p>",
    },
}

GITHUB_ISSUE = {
    "number": 42,
    "title": "Configurable Payee Type",
    "state": "open",
    "labels": [],
    "assignee": None,
    "milestone": None,
    "created_at": "2026-10-02T00:00:00Z",
    "updated_at": "2026-10-02T00:00:00Z",
    "body": "# Problem\n\nPayee type must be configurable.",
}

BASE_KEYS = {
    "id",
    "title",
    "type",
    "state",
    "assigned_to",
    "tags",
    "area_path",
    "iteration_path",
    "priority",
    "created_date",
    "changed_date",
}


# ──────────────────────────────────────────────
#  format_ticket — projection shape
# ──────────────────────────────────────────────


def test_azure_format_ticket_omits_description_by_default():
    parsed = json.loads(azure_format_ticket(AZURE_ITEM))
    assert "description" not in parsed
    assert set(parsed) == BASE_KEYS


def test_azure_format_ticket_includes_description_on_request():
    parsed = json.loads(azure_format_ticket(AZURE_ITEM, include_description=True))
    assert set(parsed) == BASE_KEYS | {"description"}
    assert parsed["description"] == AZURE_ITEM["fields"]["System.Description"]


def test_github_format_ticket_omits_body_by_default():
    parsed = json.loads(github_format_ticket(GITHUB_ISSUE))
    assert "description" not in parsed
    assert set(parsed) == BASE_KEYS


def test_github_format_ticket_includes_body_on_request():
    parsed = json.loads(github_format_ticket(GITHUB_ISSUE, include_description=True))
    assert set(parsed) == BASE_KEYS | {"description"}
    assert parsed["description"] == GITHUB_ISSUE["body"]


@pytest.mark.parametrize(
    ("formatter", "item"),
    [(azure_format_ticket, {"id": 1, "fields": {}}), (github_format_ticket, {"number": 1})],
)
def test_missing_description_yields_empty_string_not_error(formatter, item):
    parsed = json.loads(formatter(item, include_description=True))
    assert parsed["description"] == ""


def test_null_description_yields_empty_string():
    item = {"id": 1, "fields": {"System.Description": None}}
    parsed = json.loads(azure_format_ticket(item, include_description=True))
    assert parsed["description"] == ""


def test_azure_description_keeps_html_imgs():
    """add_mermaid embeds <img> tags in System.Description — markup must survive."""
    html = '<p>Intro</p><p><img src="https://dev.azure.com/attach.png" alt="Flow"></p>'
    parsed = json.loads(
        azure_format_ticket({"id": 1, "fields": {"System.Description": html}}, include_description=True)
    )
    assert parsed["description"] == html
    assert "<img" in parsed["description"]


# ──────────────────────────────────────────────
#  get_ticket — must expose the description
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_azure_get_ticket_returns_description():
    fake_client = AsyncMock()
    fake_client.get.return_value = AZURE_ITEM

    result = await AzureConnector(client=fake_client).get_ticket(345160)

    parsed = json.loads(result)
    assert parsed["id"] == 345160
    assert "Payee type must be configurable." in parsed["description"]


@pytest.mark.asyncio
async def test_github_get_ticket_returns_body():
    fake_client = AsyncMock()
    fake_client.get.return_value = GITHUB_ISSUE

    result = await GithubConnector(client=fake_client).get_ticket(42)

    parsed = json.loads(result)
    assert parsed["id"] == 42
    assert "Payee type must be configurable." in parsed["description"]


# ──────────────────────────────────────────────
#  search_tickets — must stay light
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_azure_search_tickets_omits_description():
    fake_client = AsyncMock()
    fake_client.post.return_value = {"workItems": [{"id": AZURE_ITEM["id"]}]}
    fake_client.get.return_value = {"value": [AZURE_ITEM]}

    result = await AzureConnector(client=fake_client).search_tickets(query="SELECT [System.Id] FROM WorkItems")

    parsed = json.loads(result)
    assert parsed["total"] == 1
    assert "description" not in parsed["results"][0]


@pytest.mark.asyncio
async def test_github_search_tickets_omits_description():
    fake_client = AsyncMock()
    fake_client.search.return_value = {"total_count": 1, "items": [GITHUB_ISSUE]}

    result = await GithubConnector(client=fake_client).search_tickets(query="is:open")

    parsed = json.loads(result)
    assert parsed["total"] == 1
    assert "description" not in parsed["results"][0]
