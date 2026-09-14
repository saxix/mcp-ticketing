# opencode Integration

## Configuration

Add mcp-ticketing to your `opencode.json`:

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

!!! tip

    You can enable one, some or all of the backends depending on your needs.

## Usage Examples

### View a ticket

```
user: Show me ticket #1234

opencode: [calls get_ticket(ticket_id=1234)]
```

### Search tickets

```
user: Find all bugs assigned to me in Active state

opencode: [calls search_tickets(ticket_type="Bug", state="Active", assigned_to="Mario Rossi")]
```

### Create a ticket

```
user: Create a new Task titled "Implement REST API" with priority 2

opencode: [calls create_ticket(ticket_type="Task", title="Implement REST API", priority=2)]
```

### Update a ticket

```
user: Close ticket #1234

opencode: [calls update_ticket(ticket_id=1234, state="Closed")]
```

### Add a comment

```
user: Add comment to ticket #1234: "Ready for review"

opencode: [calls add_comment(ticket_id=1234, text="Ready for review")]
```

### Check for pending replies

```
user: Are there any tickets waiting for my reply?

opencode: [calls get_tickets_needing_my_reply(my_display_name="Mario Rossi")]
```

### Link tickets

```
user: Link ticket #1289 as child of #1200

opencode: [calls link_tickets(source_id=1289, target_id=1200)]
```

### Move to iteration

```
user: Move ticket #1234 to Sprint 6

opencode: [calls move_to_iteration(ticket_id=1234, iteration_path="Project\\Sprint 6")]
```
