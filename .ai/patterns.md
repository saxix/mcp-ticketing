# Patterns Guide

This document captures the recurring architectural patterns used in the mcp-ticketing project. Each pattern describes a proven solution to a recurring design problem, with concrete examples from the codebase and guidelines for implementing new backends or features.

---

## 1. Protocol + Strategy + Dependency Injection

**Intent**: Decouple the ticketing operations from any specific backend implementation by defining a common protocol (ABC), injecting concrete strategy objects, and routing all calls through a backend-agnostic facade.

### Structure

```
TicketProtocol (ABC)              ← azure_connector / github_connector
        │  implements
   ┌───┴────┐
   │        │
   │  AzureConnector  GithubConnector                         (concrete strategies)
   │        │
   └───┬────┘
   TicketManager(strategy='azure'|'github'|TicketProtocol)  (facade, backend-agnostic)
        │
   main.py (MCPServer + @mcp.tool() wrappers, backend = sys.argv[1])
```

### Participants

| Role | Responsibility | File |
|------|----------------|------|
| **TicketProtocol** (ABC) | Declares all 14 ticket operations returning JSON strings. Concrete implementations must implement every method. | `src/mcp_ticketing/protocol.py` |
| **Connector** (Strategy) | Implements the protocol using backend-specific HTTP calls. Holds backend credentials and knows how to translate MCP operations into API calls. | `src/mcp_ticketing/azure_connector.py`, `src/mcp_ticketing/github_connector.py` |
| **TicketManager** (Facade) | Accepts a strategy (name or instance) and delegates every operation to it. The rest of the application talks to TicketManager without knowing which backend serves the tickets. | `src/mcp_ticketing/ticket_manager.py` |
| **main.py** | Composition root: parses CLI args (`sys.argv[1]`), resolves the backend strategy, builds `TicketManager`, registers all 15 `@mcp.tool()` tools against an `MCPServer`. | `src/mcp_ticketing/main.py` |

### How to Add a New Backend

Follow these steps to integrate a new ticketing backend (e.g., GitLab, Jira):

1. **Add an abstract method to `TicketProtocol`** (if the new operation is not already covered).
   ```python
   from abc import ABC, abstractmethod
   import json

   class TicketProtocol(ABC):
       @abstractmethod
       async def some_new_operation(self) -> str:
           """Description of the operation."""
   ```

2. **Implement the operation in the new connector**.
   - Implement `some_new_operation` in the new `XxxConnector(TicketProtocol)`.
   - Use the backend's HTTP client to call the API.
   - Catch `httpx.HTTPStatusError` and return `json.dumps({"error": "..."})`.
   - Follow the existing error-handling pattern.

3. **Register the new connector in `BACKENDS`** (in `ticket_manager.py`).
   ```python
   BACKENDS: dict[str, type[TicketProtocol]] = {
       "azure": AzureConnector,
       "github": GithubConnector,
       "gitlab": GitlabConnector,  # add new backend
   }
   ```

4. **Add the delegation in `TicketManager`** (if the method is not already inherited from the protocol default or if you want to be explicit).
   ```python
   async def some_new_operation(self, ...) -> str:
       return await self._strategy.some_new_operation(...)
   ```

5. **Register the MCP tool** in `main.py` with `@mcp.tool()`.
   ```python
   @mcp.tool()
   async def some_new_operation(...) -> str:
       assert manager is not None
       return await manager.some_new_operation(...)
   ```

6. **Add contract tests** in `tests/test_<backend>_contract.py` using `respx` to mock HTTP interactions.

7. **(Optional) Add an integration test** in `tests/test_integration_fake.py` if stateful round-trips matter.

### Conventions & Best Practices

| Convention | Description |
|------------|-------------|
| **Return type** | All protocol methods return `str` containing a JSON string. Never return native Python objects from tool wrappers. |
| **Error handling** | Catch `httpx.HTTPStatusError` and return `json.dumps({"error": "HTTP {status}: {text}"})`. For non-HTTP errors, catch `Exception` and return `json.dumps({"error": str(exc)})`. |
| **Auth** | Azure: basic auth with `("", pat)`. GitHub: `Authorization: Bearer <token>` header. |
| **Environment variables** | Connectors read config from env vars loaded at module import via `load_dotenv()`. New backends should follow the same pattern. |
| **CLI env mapping** | Add entries to `CLI_ENV_MAP` in `main.py` so `--owner`, `--repo`, `--token` flags work for the new backend. |
| **JSON shape** | Success: `{"success": True, ...}`. Error: `{"error": "..."}`. Keep the convention consistent across all tools. |
| **Docstrings** | Write docstrings in English. Describe parameters, return values, and backend-specific notes. |
| **Test markers** | Unit/contract tests: `not live and not integration` (default tox run). Live tests: `tox -e test-live`. |

### Example: Minimal New Backend Skeleton

```python
# src/mcp_ticketing/new_backend_connector.py
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp_ticketing.protocol import TicketProtocol

load_dotenv()

logger = logging.getLogger("mcp-ticketing-newbackend")

NEWBASE_ORG = os.getenv("MCP_NEWBASE_ORG", "")
NEWBASE_PROJECT = os.getenv("MCP_NEWBASE_PROJECT", "")
NEWBASE_TOKEN = os.getenv("MCP_NEWBASE_TOKEN", "")

API_VERSION = "v1"


class NewbaseClient:
    def __init__(self):
        self.org = os.getenv("MCP_NEWBASE_ORG", NEWBASE_ORG)
        self.project = os.getenv("MCP_NEWBASE_PROJECT", NEWBASE_PROJECT)
        self.token = os.getenv("MCP_NEWBASE_TOKEN", NEWBASE_TOKEN)
        self.base_url = f"https://api.newbase.com/{self.org}/{self.project}/v1"

    @asynccontextmanager
    async def get_client(self):
        async with httpx.AsyncClient(timeout=30.0) as client:
            yield client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            if "login" in location:
                httpx.Response(
                    401,
                    request=response.request,
                    text="authentication failed (redirect to login) — check token",
                ).raise_for_status()
        response.raise_for_status()

    async def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{path}"
        async with self.get_client() as client:
            response = await client.get(url, headers=self._headers(), params=params)
            self._raise_for_status(response)
            return response.json()

    async def post(self, path: str, data: Any, **kwargs) -> dict[str, Any]:
        url = f"{self.base_url}/{path}"
        async with self.get_client() as client:
            response = await client.post(url, headers=self._headers(), json=data, **kwargs)
            self._raise_for_status(response)
            return response.json()


class NewbaseConnector(TicketProtocol):
    """NewBase ticketing implementation of the TicketProtocol strategy."""

    def __init__(self, client: NewbaseClient | None = None):
        self.client = client or NewbaseClient()

    async def get_ticket_types(self) -> str:
        data = await self.client.get("ticket-types")
        types = data.get("types", [])
        result = [{"name": t["name"], "description": t.get("description", "")} for t in types]
        return json.dumps({"ticket_types": result, "total": len(result)}, ensure_ascii=False)

    async def check(self) -> str:
        try:
            await self.client.get("health")
            return json.dumps({
                "status": "ok",
                "backend": "newbase",
                "organization": self.client.org,
                "project": self.client.project,
                "message": f"Connection to NewBase OK.",
            }, ensure_ascii=False)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}: {e.response.text}"})

    # ... implement remaining protocol methods ...
```

### Diagram

```mermaid
flowchart TB
    direction TB
    TP[TicketProtocol (ABC)]
    AZ[AzureConnector(TicketProtocol)]
    GH[GithubConnector(TicketProtocol)]
    TM[TicketManager(TicketProtocol)]
    MAIN[main.py: MCPServer + @mcp.tool()]

    TP -->|implements| AZ
    TP -->|implements| GH
    AZ -->|delegates| TM
    GH -->|delegates| TM
    TM -->|uses| MAIN
    style TP fill:#f9f,stroke:#333,stroke-width:2px
    style AZ fill:#bbf,stroke:#333,stroke-width:2px
    style GH fill:#bbf,stroke:#333,stroke-width:2px
    style TM fill:#cfc,stroke:#333,stroke-width:2px
    style MAIN fill:#ffc,stroke:#333,stroke-width:2px
```

### Related Patterns

| Pattern | Description |
|---------|-------------|
| **CLI Env Mapping** (see `main.py:CLI_ENV_MAP`) | Maps CLI flags (`--owner`, `--repo`, `--token`) to backend-specific environment variables. Enables `uv run mcp-ticketing github --check --owner saxix --repo myrepo --token <token>`. |
| **302 Redirect Handling** (see `AzdoClient._raise_for_status`, `GitHubClient._raise_for_status`) | Detects OAuth/login redirect responses and raises a clear error about checking PAT/org/project or token/owner/repo. |
| **WIQL vs Search API** | Azure uses WIQL (`POST wit/wiql` + batch fetch), GitHub uses the search API (`GET /search/issues`). New backends should pick the query language natural to their API. |
| **Mermaid Rendering** | Azure renders to PNG locally via `mermaidx` and attaches as an image. GitHub appends a ` ## Diagrams ```mermaid ``` block to Markdown. Choose the approach compatible with your target platform. |
| **Format Ticket** | Each connector has a `format_ticket()` helper that projects raw API responses into a flat JSON shape (`id, title, type, state, assigned_to, tags, area_path, iteration_path, priority, created_date, changed_date, description`). New backends should implement a similar projector. |
