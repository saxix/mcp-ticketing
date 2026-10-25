# Project context

Product: MCP server for Azure DevOps and GitHub Issues ticketing.

Repo layout
- Package: `src/mcp_ticketing/` — all logic lives here
- Entrypoint: `src/mcp_ticketing/main.py` — composition root, builds the TicketManager and registers 15 MCP tools
- Backends: `TicketManager(strategy='azure'|'github'|TicketProtocol)` with DI via the `BACKENDS` registry

Branch and deploy model
- Default backend: `azure` (CLI: `uv run mcp-ticketing azure`)
- Optional backend: `github` (CLI: `uv run mcp-ticketing github`)
- Version: 0.2.0 (from `pyproject.toml`)
- Requires Python: `>=3.10` (from `pyproject.toml`)
- Package manager: `uv`
- Test runner: `pytest` (default tox env: `lint`, `typecheck`, `test`)
- CI: `tox` with `live` and `integration` markers; `addopts` excludes `live` and `integration` from the default run