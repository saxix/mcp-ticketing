# mcp-ticketing

MCP server for managing tickets on **Azure DevOps** and **GitHub Issues**.

All backends expose the same 15 tools with identical function names, so you can switch providers without changing your workflow.

## Features

- **Unified API** — same 15 tools for Azure DevOps and GitHub
- **Ticket CRUD** — create, read, update, search tickets
- **Comments** — add and read comments
- **Attachments** — attach local image files to tickets
- **Diagrams** — append Mermaid diagrams to tickets
- **Links** — link tickets together
- **Iterations** — manage sprints (Azure) and milestones (GitHub)
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
```

## Available Tools

| Tool | Description |
|------|-------------|
| `get_ticket_types` | List valid ticket types |
| `get_ticket` | Retrieve ticket details |
| `get_ticket_comments` | List all comments |
| `add_comment` | Add a comment |
| `add_mermaid` | Add a Mermaid diagram (source on GitHub, PNG attachment on Azure) |
| `add_ticket_image` | Attach a local image file (Azure only) |
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
├── ticket_manager.py    # TicketManager (facade, backend-agnostic)
└── main.py              # Composition root: MCPServer + 15 @mcp.tool()
```

`main.py` builds a single `TicketManager` from the backend chosen on the command line (`uv run mcp-ticketing azure|github`) and registers the 15 MCP tools against it. The server is built with `MCPServer` from `mcp.server.mcpserver`.
