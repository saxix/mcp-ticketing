# Agents Guide - mcp-ticketing

## Quick Start

```bash
uv run mcp-ticketing azure    # Azure DevOps backend
uv run mcp-ticketing github   # GitHub backend
uv run mcp-ticketing github --check   # verify credentials and exit
```

- Python 3.13 (pinned in `.python-version`)
- Package manager: `uv`
- Single entrypoint `mcp-ticketing <backend>` (backend chosen via CLI argument)

## Architecture

- **Package**: `src/mcp_ticketing/` — all logic lives here
- **Entrypoint**: `main.py` — composition root: builds the manager and registers the 15 MCP tools
- **15 MCP tools** registered via `@mcp.tool()` decorator on the single `MCPServer`
- Framework: `mcp-server` (MCPServer from `mcp.server.mcpserver`)

### Design pattern (Protocol + Strategy + DI)

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

- `protocol.py` — `TicketProtocol` ABC with all 14 ticket operations returning JSON strings
- `azure_connector.py` — `AzureConnector(TicketProtocol)` + `AzdoClient` HTTP wrapper
- `github_connector.py` — `GithubConnector(TicketProtocol)` + `GitHubClient` HTTP wrapper
- `ticket_manager.py` — `TicketManager(TicketProtocol)`; accepts a backend name (`'azure'`/`'github'`, resolved via the `BACKENDS` registry) or a `TicketProtocol` instance; delegates every call to the strategy
- `main.py` — composition root: parses CLI args (`<backend>` positional, `--check`, `--owner/--repo/--token` overrides), sets the env, then builds `manager = TicketManager(strategy=<backend>)` + all 14 `@mcp.tool()` registrations; `--check` runs a connectivity check and exits instead of starting the MCP server

To add a new backend: implement a new `XxxConnector(TicketProtocol)`, register it in `BACKENDS` in `ticket_manager.py`, and add its namespace/class to the registry.

## Environment

Required env vars (load via `.env` or direnv):

| Variable | Description |
|----------|-------------|
| `MCP_AZURE_DEVOPS_ORG` | Azure DevOps organization name |
| `MCP_AZURE_DEVOPS_PROJECT` | Azure DevOps project name |
| `MCP_AZDO_PAT` | Personal Access Token (scope: Work Items Read & Write) |
| `MCP_GITHUB_OWNER` | GitHub owner (user or org) |
| `MCP_GITHUB_REPO` | GitHub repository name |
| `MCP_GITHUB_TOKEN` | GitHub Personal Access Token (scope: repo/issues) |

## Code Conventions

- All tool return values are **JSON strings** (`str` type), not native Python objects
- Error handling: catch `httpx.HTTPStatusError`, return `{"error": "..."}` JSON
- Language: **English** (comments, docstrings, README)
- HTTP client: `httpx.AsyncClient` with async context managers
- Auth (Azure): basic auth with empty username + PAT as password
- Auth (GitHub): `Authorization: Bearer <token>` header
- Business logic lives in connectors, not in the MCP adapter modules
- Backend selection lives in `main.py` (`sys.argv[1]`) — the MCP tools always run against the manager

## Security

- `.envrc` contains real credentials — must stay gitignored
- `.env` is gitignored (correct)
- Never commit PATs or secrets

## Adding a New Method to the Protocol

1. Add `@abstractmethod` to `TicketProtocol` (return JSON string)
2. Implement it in `AzureConnector` and `GithubConnector`
3. Add the delegation in `TicketManager`
4. Register the tool (`@mcp.tool()`) in `main.py`
5. Catch `httpx.HTTPStatusError` and return error JSON
6. Add or update tests in `tests/test_azure.py` / `tests/test_github.py`