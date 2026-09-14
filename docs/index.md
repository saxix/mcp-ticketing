# mcp-ticketing

MCP server for managing tickets on **Azure DevOps**, **GitHub Issues**, **Taiga** and **Redmine**.

All backends expose the same 13 tools with identical function names, so you can switch providers without changing your workflow.

## Features

- **Unified API** — same 13 tools for Azure DevOps, GitHub, Taiga and Redmine
- **Ticket CRUD** — create, read, update, search tickets
- **Comments** — add and read comments
- **Links** — link tickets together
- **Iterations** — manage sprints (Azure), milestones (GitHub/Taiga) and versions (Redmine)
- **Reply detection** — find tickets where you need to reply

## Quick Start

```bash
# Install
uv sync

# Configure (copy and fill in credentials)
cp .env.example .env

# Run
uv run mcp-ticketing azure    # Azure DevOps
uv run mcp-ticketing github   # GitHub
uv run mcp-ticketing taiga    # Taiga
uv run mcp-ticketing redmine  # Redmine
```

## Available Tools

| Tool | Description |
|------|-------------|
| `get_ticket_types` | List valid ticket types |
| `get_ticket` | Retrieve ticket details |
| `get_ticket_comments` | List all comments |
| `add_comment` | Add a comment |
| `create_ticket` | Create a new ticket |
| `update_ticket` | Update an existing ticket |
| `search_tickets` | Search with query or filters |
| `get_tickets_needing_my_reply` | Find tickets awaiting your reply |
| `link_tickets` | Link two tickets |
| `get_ticket_relations` | Retrieve links and cross-references |
| `get_iterations` | List iterations or milestones |
| `move_to_iteration` | Move a ticket to an iteration |
| `get_area_paths` | Retrieve area paths or labels |

See [Tools Reference](tools.md) for full parameter documentation.

## Architecture

```
src/mcp_ticketing/
├── protocol.py          # TicketProtocol (ABC)
├── azure_connector.py   # AzureConnector strategy
├── github_connector.py  # GithubConnector strategy
├── taiga_connector.py   # TaigaConnector strategy
├── redmine_connector.py # RedmineConnector strategy
├── ticket_manager.py    # TicketManager (facade, backend-agnostic)
└── main.py              # Composition root: MCPServer + 13 @mcp.tool()
```

`main.py` builds a single `TicketManager` from the backend chosen on the command line (`uv run mcp-ticketing azure|github|taiga|redmine`) and registers the 13 MCP tools against it. The server is built with `MCPServer` from `mcp.server.mcpserver`.
