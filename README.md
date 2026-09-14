# mcp-ticketing

MCP server for managing tickets on **Azure DevOps**, **GitHub Issues**, **Taiga** and **Redmine**.

All backends expose the same 13 tools with identical function names, so you can switch providers without changing your workflow.

## Setup

### 1. Install

```bash
uv sync
```

### 2. Configure credentials

Copy the example file and fill in the values for the backend you want to use:

```bash
cp .env.example .env
```

#### Azure DevOps

| Variable | Description |
|----------|-------------|
| `MCP_AZURE_DEVOPS_ORG` | Organization name |
| `MCP_AZURE_DEVOPS_PROJECT` | Project name |
| `MCP_AZDO_PAT` | Personal Access Token (scope: **Work Items Read & Write**) |

To create a PAT:
1. Go to `https://dev.azure.com/{your-org}/_usersSettings/tokens`
2. Click **New Token**
3. Select scope **Work Items (Read & Write)**
4. Copy the generated token to `.env`

#### GitHub

| Variable | Description |
|----------|-------------|
| `MCP_GITHUB_OWNER` | Repository owner (user or org) |
| `MCP_GITHUB_REPO` | Repository name |
| `MCP_GITHUB_TOKEN` | Personal Access Token (scope: **repo**) |

#### Taiga

| Variable | Description |
|----------|-------------|
| `MCP_TAIGA_URL` | API base URL (default `https://api.taiga.io/api/v1`) |
| `MCP_TAIGA_USERNAME` | Taiga username |
| `MCP_TAIGA_PASSWORD` | Taiga password |
| `MCP_TAIGA_PROJECT_ID` | Taiga project ID |

#### Redmine

| Variable | Description |
|----------|-------------|
| `MCP_REDMINE_URL` | Redmine base URL (e.g. `https://redmine.example.com`) |
| `MCP_REDMINE_API_KEY` | API key (sent as `X-Redmine-API-Key`) |
| `MCP_REDMINE_PROJECT` | Project identifier or numeric ID |

### 3. Run

```bash
uv run mcp-ticketing azure    # Azure DevOps
uv run mcp-ticketing github   # GitHub
uv run mcp-ticketing taiga    # Taiga
uv run mcp-ticketing redmine  # Redmine
```

### 4. Verify credentials (optional)

```bash
uv run mcp-ticketing azure -check   # Prints {"status": "ok", ...} and exits
uv run mcp-ticketing github -check
uv run mcp-ticketing taiga -check
uv run mcp-ticketing redmine -check
```

Exit code is `0` on success, `1` on failure.

## Available Tools

Both backends expose the same 13 tools:

| Tool | Description |
|------|-------------|
| `get_ticket_types` | List valid ticket types (Task, Bug, etc.) |
| `get_ticket` | Retrieve full ticket details |
| `get_ticket_comments` | Retrieve all comments on a ticket |
| `add_comment` | Add a comment to a ticket |
| `create_ticket` | Create a new ticket |
| `update_ticket` | Update an existing ticket (partial update) |
| `search_tickets` | Search tickets with query or filters |
| `get_tickets_needing_my_reply` | Active tickets where you need to reply |
| `link_tickets` | Link two tickets together |
| `get_ticket_relations` | Retrieve links and cross-references |
| `get_iterations` | List iterations/sprints (Azure), milestones (GitHub/Taiga) or versions (Redmine) |
| `move_to_iteration` | Move a ticket to an iteration or milestone |
| `get_area_paths` | Retrieve area paths (Azure), labels (GitHub), tags (Taiga) or categories (Redmine) |

## Usage with opencode

Add to your `opencode.json`:

```json
{
  "mcp": {
    "azure-devops": {
      "type": "local",
      "command": ["uv", "run", "mcp-ticketing", "azure"],
      "cwd": "/path/to/mcp-ticketing",
      "enabled": true
    },
    "github": {
      "type": "local",
      "command": ["uv", "run", "mcp-ticketing", "github"],
      "cwd": "/path/to/mcp-ticketing",
      "enabled": true
    },
    "taiga": {
      "type": "local",
      "command": ["uv", "run", "mcp-ticketing", "taiga"],
      "cwd": "/path/to/mcp-ticketing",
      "enabled": true
    },
    "redmine": {
      "type": "local",
      "command": ["uv", "run", "mcp-ticketing", "redmine"],
      "cwd": "/path/to/mcp-ticketing",
      "enabled": true
    }
  }
}
```

## Usage Examples

### View a ticket

```
user: Show me ticket #1234

opencode: [calls get_ticket(ticket_id=1234)]

Response:
{
  "id": 1234,
  "title": "Implement OAuth authentication",
  "type": "Task",
  "state": "Active",
  "assigned_to": "Mario Rossi",
  "tags": "backend;security",
  "area_path": "Project\\Backend",
  "iteration_path": "Project\\Sprint 5",
  "priority": 1,
  "created_date": "2026-01-15T10:30:00Z",
  "changed_date": "2026-02-01T14:22:00Z"
}
```

### Search tickets

```
user: Find all bugs assigned to me in Active state

opencode: [calls search_tickets(ticket_type="Bug", state="Active", assigned_to="Mario Rossi")]

Response:
{
  "results": [
    {
      "id": 1250,
      "title": "Login fails on Safari",
      "type": "Bug",
      "state": "Active",
      "assigned_to": "Mario Rossi",
      "priority": 2
    }
  ],
  "total": 1
}
```

### Create a ticket

```
user: Create a new Task titled "Implement REST API for users" with priority 2 and assign it to Mario Rossi

opencode: [calls create_ticket(
  ticket_type="Task",
  title="Implement REST API for users",
  priority=2,
  assigned_to="Mario Rossi"
)]

Response:
{
  "success": true,
  "id": 1289,
  "title": "Implement REST API for users",
  "type": "Task",
  "message": "Ticket #1289 created successfully."
}
```

### Update a ticket

```
user: Close ticket #1234

opencode: [calls update_ticket(ticket_id=1234, state="Closed")]

Response:
{
  "success": true,
  "id": 1234,
  "message": "Ticket #1234 updated successfully.",
  "updated_fields": ["state"]
}
```

### Add a comment

```
user: Add this comment to ticket #1234: "Implementation complete, tests passed. Ready for review."

opencode: [calls add_comment(
  ticket_id=1234,
  text="Implementation complete, tests passed. Ready for review."
)]

Response:
{
  "success": true,
  "ticket_id": 1234,
  "comment_id": 456,
  "message": "Comment added successfully."
}
```

### Tickets needing reply

```
user: Are there any tickets waiting for my reply?

opencode: [calls get_tickets_needing_my_reply(my_display_name="Mario Rossi")]

Response:
{
  "pending": [
    {
      "ticket_id": 1250,
      "title": "Login fails on Safari",
      "last_comment_author": "Luigi Verdi",
      "last_comment_date": "2026-02-05T09:15:00Z",
      "last_comment_preview": "Can you verify if the issue persists with cache disabled?"
    }
  ],
  "total": 1,
  "message": "Found 1 ticket(s) needing your reply."
}
```

### Link tickets

```
user: Link ticket #1289 as child of #1200

opencode: [calls link_tickets(
  source_id=1289,
  target_id=1200,
  link_type="System.LinkTypes.Hierarchy-Reverse"
)]

Response:
{
  "success": true,
  "source_id": 1289,
  "target_id": 1200,
  "link_type": "System.LinkTypes.Hierarchy-Reverse",
  "message": "Link created: #1289 -> #1200 (System.LinkTypes.Hierarchy-Reverse)"
}
```

### Move to an iteration

```
user: Move ticket #1234 to "Sprint 6"

opencode: [calls move_to_iteration(ticket_id=1234, iteration_path="Project\\Sprint 6")]

Response:
{
  "success": true,
  "id": 1234,
  "iteration_path": "Project\\Sprint 6",
  "message": "Ticket #1234 moved to 'Project\\Sprint 6'."
}
```

## Testing

```bash
uv run pytest tests/ -v
```

Tests are integration tests that call real APIs. They skip automatically when the corresponding environment variables are not set.

## License

See [LICENSE](LICENSE).
