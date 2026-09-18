# mcp-ticketing

MCP server for managing tickets on **Azure DevOps** and **GitHub Issues**.

All backends expose the same 15 tools with identical function names, so you can switch providers without changing your workflow.

## Setup

### 1. Install

From source (dev mode):

```bash
uv sync
```

Or from PyPI:

```bash
uv tool install mcp-ticketing
```

Or run ad-hoc with `uvx`:

```bash
uvx mcp-ticketing github --check
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

### 3. Run

```bash
uv run mcp-ticketing azure    # Azure DevOps
uv run mcp-ticketing github   # GitHub
```

### 4. Verify credentials (optional)

```bash
uv run mcp-ticketing azure --check   # Prints {"status": "ok", ...} and exits
uv run mcp-ticketing github --check
```

Exit code is `0` on success, `1` on failure.

## Available Tools

Both backends expose the same 15 tools:

| Tool | Description |
|------|-------------|
| `get_ticket_types` | List valid ticket types (Task, Bug, etc.) |
| `get_ticket` | Retrieve full ticket details |
| `get_ticket_comments` | Retrieve all comments on a ticket |
| `add_comment` | Add a comment to a ticket |
| `add_mermaid` | Add a Mermaid diagram (Markdown source on GitHub; high-res PNG embedded on Azure) |
| `add_ticket_image` | Attach a local image file (Azure only; not supported on GitHub) |
| `create_ticket` | Create a new ticket |
| `update_ticket` | Update an existing ticket (partial update) |
| `search_tickets` | Search tickets with query or filters |
| `get_tickets_needing_my_reply` | Active tickets where you need to reply |
| `link_tickets` | Link two tickets together |
| `get_ticket_relations` | Retrieve links and cross-references |
| `get_iterations` | List iterations/sprints (Azure) or milestones (GitHub) |
| `move_to_iteration` | Move a ticket to an iteration or milestone |
| `get_area_paths` | Retrieve area paths (Azure) or labels (GitHub) |

## Usage with opencode

### Install mode (from PyPI)

```bash
uv tool install mcp-ticketing    # or: pipx install mcp-ticketing
```

### Configure

Add to your `opencode.json`. From a source checkout (dev mode):

```json
{
  "mcp": {
    "github": {
      "type": "local",
      "command": ["uv", "run", "--project", "/path/to/mcp-ticketing", "mcp-ticketing", "github"],
      "enabled": true
    }
  }
}
```

From PyPI (ephemeral):

```json
{
  "mcp": {
    "github": {
      "type": "local",
      "command": ["uvx", "mcp-ticketing", "github"],
      "enabled": true
    }
  }
}
```

Globally installed:

```json
{
  "mcp": {
    "github": {
      "type": "local",
      "command": ["mcp-ticketing", "github"],
      "enabled": true
    }
  }
}
```

Credentials can be passed without a `.env` file via the server's `environment` block (use `{env:...}` substitutions for secrets). See [docs/opencode.md](docs/opencode.md) for details.

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

### Add a diagram (Mermaid)

Use Mermaid syntax whenever a diagram is requested. On GitHub the diagram source is appended at the bottom of the issue body (rendered automatically by GitHub); on Azure the diagram is rendered locally to a high-resolution PNG — via the bundled `mermaidx` engine (Mermaid v11, no browser or Node needed, works offline) — and embedded at the bottom of the work item description (also listed in Attachments). The rendered PNG uses a 2x scale so it stays crisp when zoomed.

```
user: Generate a flowchart for the login flow and append it to ticket #1234

opencode: [calls add_mermaid(
  ticket_id=1234,
  diagram="flowchart TD\n  A[Start] --> B[Login] --> C[Dashboard]",
  title="Login Flow"
)]

Response (Azure):
{
  "success": true,
  "ticket_id": 1234,
  "file_name": "mermaid-1234.png",
  "attachment_id": "a1b2c3d4-...",
  "url": "https://dev.azure.com/org/project/_apis/wit/attachments/a1b2c3d4-...",
  "renderer": "mermaidx",
  "message": "Mermaid diagram embedded in ticket #1234."
}

Response (GitHub):
{
  "success": true,
  "ticket_id": 1234,
  "section": "Diagrams",
  "position": "bottom",
  "message": "Mermaid diagram appended to ticket #1234 in section 'Diagrams'."
}
```

### Attach an image

Azure only — GitHub Issues have no native attachment API and return `{"error": "add_ticket_image is not supported by this backend."}`.

```
user: Attach /tmp/screenshot.png to ticket #1234 with note "login screen"

opencode: [calls add_ticket_image(ticket_id=1234, image_path="/tmp/screenshot.png", comment="login screen")]

Response:
{
  "success": true,
  "ticket_id": 1234,
  "file_name": "screenshot.png",
  "attachment_id": "a1b2c3d4-...",
  "url": "https://dev.azure.com/org/project/_apis/wit/attachments/a1b2c3d4-...",
  "message": "Image 'screenshot.png' attached to ticket #1234."
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
