# Technical

Language and runtime
- Language: Python 3.13 (pinned in `.python-version`)
- `pyproject.toml` requires-python: `>=3.10`
- Package manager: `uv`

Package and lockfile
- Source package: `src/mcp_ticketing/`
- Build backend: `hatchling`
- Lockfile: `uv.lock`

Commands and scripts
- `uv run mcp-ticketing azure` — start Azure backend
- `uv run mcp-ticketing github` — start GitHub backend
- `uv run mcp-ticketing github --check` — verify credentials and exit
- `tox -e test` — unit + respx contract tests with coverage (95% gate)
- `tox -e lint` — ruff check + format
- `tox -e typecheck` — mypy type checking
- `tox -e integration` — docker-compose fake servers
- `pytest tests/ --cov` — run tests with coverage

Top-level layout
- `src/mcp_ticketing/` — main package
  - `__init__.py`, `protocol.py`, `ticket_manager.py`, `azure_connector.py`, `github_connector.py`, `main.py`, `_images.py`
- `tests/` — test suite
  - `test_azure_contract.py`, `test_github_contract.py`, `test_integration_fake.py`, `fakes/`
- `tests/docker/` — docker-compose for integration fake servers
- `.env`, `.envrc`, `.env.example` — environment configuration
- `AGENTS.md`, `README.md`, `pyproject.toml`, `tox.ini`
- `.ai/` — project-specific rule files (project.md, architecture.md, technical.md)

Entrypoints and modules
- Module: `mcp_ticketing.main:main` (registered as `mcp-ticketing` script in `pyproject.toml`)
- Protocol: `mcp_ticketing.protocol.TicketProtocol` (ABC with 14 operations)
- Connectors: `mcp_ticketing.azure_connector.AzureConnector`, `mcp_ticketing.github_connector.GithubConnector`
- Manager: `mcp_ticketing.ticket_manager.TicketManager` (facade with DI)

Test runner, fixtures, coverage, CI
- Default test marker: `not live and not integration`
- Coverage gate: 95%
- Contract tests: `respx` (mock HTTP), `tests/test_azure_contract.py`, `tests/test_github_contract.py`
- Integration fake: ASGI app + in-memory store in `tests/fakes/`, served by uvicorn via `tests/docker/`
- Live tests: `tox -e test-live` (requires `.envrc` with real credentials)
