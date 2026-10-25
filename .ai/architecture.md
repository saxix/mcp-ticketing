# Architecture

Boundaries
- `TicketProtocol` (ABC) defines 14 ticket operations returning JSON strings
- `AzureConnector(TicketProtocol)` — Azure DevOps connector via `AzdoClient` HTTP wrapper
- `GithubConnector(TicketProtocol)` — GitHub connector via `GitHubClient` HTTP wrapper
- `TicketManager(TicketProtocol)` — delegating facade that forwards every call to the injected strategy

Entrypoints
- `main.py` — composition root: parses CLI args (`sys.argv[1]`), resolves the backend strategy, builds `TicketManager`, registers all 15 `@mcp.tool()` tools against an `MCPServer`
- `--check` flag: runs `manager.check()` and exits instead of starting the server

Data flow
- CLI (`--backend`, `--owner`, `--repo`, `--token`) → `_resolve()` → `TicketManager(strategy)` → `_strategy` (AzureConnector or GithubConnector) → HTTP API (Azure DevOps or GitHub)
- All business logic lives in the connectors; the manager is backend-agnostic
- 15 tools registered: `get_ticket_types`, `get_ticket`, `get_ticket_comments`, `add_comment`, `add_mermaid`, `add_ticket_image`, `create_ticket`, `update_ticket`, `search_tickets`, `get_tickets_needing_my_reply`, `link_tickets`, `get_ticket_relations`, `get_iterations`, `move_to_iteration`, `get_area_paths`

Integration points
- Azure: under `/{org}/{project}/_apis`
- GitHub: under `/repos/{owner}/{repo}` and `/search/issues`
- Fake servers for integration tests live in `tests/docker/`