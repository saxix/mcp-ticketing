# Agents Guide - mcp-ticketing

## Quick Start

```bash
uv run mcp-ticketing azure    # Azure DevOps backend
uv run mcp-ticketing github   # GitHub backend
uv run mcp-ticketing redmine  # Redmine backend
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

### Design pattern (Protocol + double Strategy + DI)

The backend is chosen by an **outer strategy** (Azure / GitHub / Redmine);
the Redmine backend adds an **inner strategy** — a *version profile* selected
**once at startup** — so the connector never branches on the Redmine version
at runtime (no try/except-404 fallbacks).

```
TicketProtocol (ABC)
        │  implements
   ┌────┼─────────────────┐
   │   │                 │
AzureConnector  GithubConnector  RedmineConnector
                                       │ uses (inner strategy)
                              RedmineVersionProfile  ← selected ONCE at startup
                                       │
                              ┌────────┼────────┬────────┐
                          Profile42  Profile51  Profile6x  Profile7x
                        (4.2–4.x)  (5.0–5.x)  (6.0–6.x)  (7.0+)
                          trackers   trackers   trackers   issue_types
   TicketManager(strategy='azure'|'github'|'redmine'|TicketProtocol)  (facade, backend-agnostic)
        │
   main.py (MCPServer + @mcp.tool() wrappers, backend = sys.argv[1];
            warms up the Redmine profile before registering tools)
```

- `protocol.py` — `TicketProtocol` ABC with all 14 ticket operations returning JSON strings
- `azure_connector.py` — `AzureConnector(TicketProtocol)` + `AzdoClient` HTTP wrapper
- `github_connector.py` — `GithubConnector(TicketProtocol)` + `GitHubClient` HTTP wrapper
- `redmine/` — package:
  - `client.py` — `RedmineClient` httpx wrapper (version-agnostic transport)
  - `profile.py` — `RedmineVersionProfile` ABC + `RedmineCapabilities` + version errors + `parse_version`
  - `base.py` — `RedmineBaseProfile`: the invariant rules (auth, `issues_statuses.json`, `my_account.json`, per-project `versions.json`, `subject=~term`, 204) + the shared text helpers (`strip_wiki`, `markdown_to_html`, `render_mermaid_png`, field aliases, priority synonyms)
  - `p42.py` / `p51.py` / `p6x.py` / `p7x.py` — the four profiles; each overrides only `supports()` and the issue-type endpoint/field name (legacy `trackers` for 4.2–6.x, `issue_types` for 7.x)
  - `registry.py` — `VERSION_PROFILES` + `select_profile(declared)`
  - `discovery.py` — per-instance priority discovery (single page of issues, matched by name)
  - `connector.py` — `RedmineConnector(TicketProtocol)`; delegates every version-dependent choice to the active profile
- `ticket_manager.py` — `TicketManager(TicketProtocol)`; accepts a backend name (`'azure'`/`'github'`/`'redmine'`, resolved via the `BACKENDS` registry) or a `TicketProtocol` instance; delegates every call to the strategy
- `main.py` — composition root: parses CLI args (`<backend>` positional, `--check`, `--owner/--repo/--token` overrides), loads `.env`, builds `manager = TicketManager(strategy=<backend>)`, **warms up the Redmine profile** (fail-fast on unsupported/mismatched version) before registering the 15 tools; `--check` runs a connectivity check and exits instead of starting the MCP server

**Redmine version selection.** `MCP_REDMINE_VERSION` (e.g. `4.2`, `6.1`, `7.0`) declares the server version; the connector validates it against the real server (single probe of the `X-Redmine-Version` header) and raises `ProfileMismatchError` at startup on a mismatch. When empty, the version is **auto-detected** from that same single probe and the matching profile is selected (`UnsupportedRedmineVersionError` if the server is outside 4.2 – 7.x).

To add a new backend: implement a new `XxxConnector(TicketProtocol)`, register it in `BACKENDS` in `ticket_manager.py`, and add its namespace/class to the registry. To add a new Redmine major series: add a `ProfileNN` subclass in `redmine/` and append it to `VERSION_PROFILES` in `redmine/registry.py`.

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
| `MCP_REDMINE_URL` | Redmine instance base URL (e.g. https://redmine.example.com) |
| `MCP_REDMINE_PROJECT` | Redmine project identifier or ID (default scope) |
| `MCP_REDMINE_API_KEY` | Redmine API key (from the user profile page) |
| `MCP_REDMINE_VERSION` | (optional) Redmine version (e.g. `4.2`, `6.1`, `7.0`). Supported 4.2 – 7.x. When empty it is auto-detected from the `X-Redmine-Version` header at startup |
| `MCP_REDMINE_VERIFY_SSL` | (optional) `false` to disable TLS verification; a path to a CA bundle; empty for default |

## Code Conventions

- All tool return values are **JSON strings** (`str` type), not native Python objects
- Error handling: catch `httpx.HTTPStatusError`, return `{"error": "..."}` JSON
- Language: **English** (comments, docstrings, README)
- HTTP client: `httpx.AsyncClient` with async context managers
- Auth (Azure): basic auth with empty username + PAT as password
- Auth (GitHub): `Authorization: Bearer <token>` header
- Auth (Redmine): `X-Redmine-API-Key: <key>` header
- Business logic lives in connectors, not in the MCP adapter modules
- Backend selection lives in `main.py` (`sys.argv[1]`) — the MCP tools always run against the manager

## Security

- `.envrc` contains real credentials — must stay gitignored
- `.env` is gitignored (correct)
- Never commit PATs or secrets

## Adding a New Method to the Protocol

1. Add `@abstractmethod` to `TicketProtocol` (return JSON string)
2. Implement it in `AzureConnector`, `GithubConnector` and `RedmineConnector`
3. Add the delegation in `TicketManager`
4. Register the tool (`@mcp.tool()`) in `main.py`
5. Catch `httpx.HTTPStatusError` and return error JSON
6. Add or update tests in `tests/test_azure.py` / `tests/test_github.py` / `tests/test_redmine.py`