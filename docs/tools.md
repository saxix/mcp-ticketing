# Tools Reference

Both Azure DevOps and GitHub backends expose the same 15 tools with identical function names and parameter signatures. Minor behavioral differences are backend-specific — see [Azure DevOps](azure.md) and [GitHub](github.md) for details.

---

## `get_ticket_types`

List all valid ticket types configured for the project.

**Parameters:** None

**Returns:**

```json
{
  "ticket_types": [
    {"name": "Task", "description": "..."},
    {"name": "Bug", "description": "..."}
  ],
  "total": 2
}
```

---

## `get_ticket`

Retrieve full details of a ticket.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `ticket_id` | int | Yes | Ticket ID (work item ID or issue number) |

**Returns:** JSON with `id`, `title`, `type`, `state`, `assigned_to`, `tags`, `area_path`, `iteration_path`, `priority`, `created_date`, `changed_date`.

---

## `get_ticket_comments`

Retrieve all comments on a ticket.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `ticket_id` | int | Yes | Ticket ID |

**Returns:**

```json
{
  "ticket_id": 123,
  "total": 2,
  "comments": [
    {
      "id": 1,
      "author": "Mario Rossi",
      "created_date": "2026-01-15T10:30:00Z",
      "text": "Comment body..."
    }
  ]
}
```

---

## `add_comment`

Add a comment to a ticket.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `ticket_id` | int | Yes | Ticket ID |
| `text` | str | Yes | Comment text |

**Returns:**

```json
{
  "success": true,
  "ticket_id": 123,
  "comment_id": 456,
  "message": "Comment added successfully."
}
```

---

## `add_mermaid`

Add a Mermaid diagram to a ticket.

- **GitHub**: the Mermaid source is appended at the bottom of the issue body, under the `## Diagrams` heading (rendered automatically by GitHub). The `section` parameter controls the heading.
- **Azure**: the diagram is rendered locally to a high-resolution PNG (2x zoom-friendly, via the bundled `mermaidx` engine), attached to the work item as an `AttachedFile` relation **and** embedded at the bottom of the description with an `<img>` tag so it is visible directly in the ticket. `section` is ignored.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | int | Yes | — | Ticket ID (work item ID or issue number) |
| `diagram` | str | Yes | — | Mermaid diagram source (e.g. `"flowchart TD\n  A[Start] --> B[End]"`) |
| `title` | str | No | `""` | GitHub: heading above the diagram; Azure: attachment label |
| `section` | str | No | `"Diagrams"` | Section heading to append to (GitHub only) |

On GitHub, if a `## Diagrams` section already exists, only the new diagram block is appended (no duplicate heading).

**Returns (GitHub):**

```json
{
  "success": true,
  "ticket_id": 1234,
  "section": "Diagrams",
  "position": "bottom",
  "message": "Mermaid diagram appended to ticket #1234 in section 'Diagrams'."
}
```

**Returns (Azure):**

```json
{
  "success": true,
  "ticket_id": 1234,
  "file_name": "mermaid-1234.png",
  "attachment_id": "a1b2c3d4-...",
  "url": "https://dev.azure.com/org/project/_apis/wit/attachments/a1b2c3d4-...",
  "renderer": "mermaidx",
  "message": "Mermaid diagram embedded in ticket #1234."
}
```

---

## `add_ticket_image`

Attach a local image file to a ticket. **Azure only** — GitHub Issues have no native attachment API and this tool returns `{"error": "add_ticket_image is not supported by this backend."}` on GitHub.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | int | Yes | — | Work item ID |
| `image_path` | str | Yes | — | Local path to an image file (PNG, JPEG, GIF, WebP, ...) |
| `comment` | str | No | `""` | Attachment label |

**Returns (Azure):**

```json
{
  "success": true,
  "ticket_id": 1234,
  "file_name": "screenshot.png",
  "attachment_id": "a1b2c3d4-...",
  "url": "https://dev.azure.com/org/project/_apis/wit/attachments/a1b2c3d4-...",
  "message": "Image 'screenshot.png' attached to ticket #1234."
}
```

**Returns (GitHub):**

```json
{
  "error": "add_ticket_image is not supported by this backend."
}
```

---

## `create_ticket`

Create a new ticket.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_type` | str | Yes | `""` | Ticket type (Task, Bug, etc.) |
| `title` | str | Yes | `""` | Ticket title |
| `description` | str | No | `""` | Description (HTML for Azure, Markdown for GitHub) |
| `assigned_to` | str | No | `""` | Assignee (display name Azure, username GitHub) |
| `priority` | int | No | `0` | Priority (1-4) |
| `tags` | str | No | `""` | Semicolon-separated tags |
| `area_path` | str | No | `""` | Area path (Azure) or labels (GitHub) |
| `iteration_path` | str | No | `""` | Iteration (Azure) or milestone title (GitHub) |

!!! note

    If `ticket_type` is not provided, the tool returns available types. Call `get_ticket_types()` first to see valid options.

**Returns:**

```json
{
  "success": true,
  "id": 1289,
  "title": "Implement REST API",
  "type": "Task",
  "message": "Ticket #1289 created successfully."
}
```

---

## `update_ticket`

Update an existing ticket. Only provided fields are modified.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `ticket_id` | int | Yes | — | Ticket ID to update |
| `title` | str | No | `""` | New title |
| `description` | str | No | `""` | New description |
| `assigned_to` | str | No | `""` | New assignee |
| `state` | str | No | `""` | New state |
| `priority` | int | No | `0` | New priority (1-4) |
| `tags` | str | No | `""` | New tags (replaces all) |
| `area_path` | str | No | `""` | New area path |
| `iteration_path` | str | No | `""` | New iteration path |

**Returns:**

```json
{
  "success": true,
  "id": 1234,
  "message": "Ticket #1234 updated successfully.",
  "updated_fields": ["state"]
}
```

---

## `search_tickets`

Search tickets using a query or built-in filters.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | str | No | `""` | Custom WIQL query (Azure) or search query (GitHub) |
| `ticket_type` | str | No | `""` | Filter by type |
| `state` | str | No | `""` | Filter by state |
| `assigned_to` | str | No | `""` | Filter by assignee |
| `tags` | str | No | `""` | Filter by tags |
| `area_path` | str | No | `""` | Filter by area path |
| `iteration_path` | str | No | `""` | Filter by iteration |
| `max_results` | int | No | `20` | Max results (1-200) |

!!! tip

    If `query` is provided, all other filters are ignored.

**Example (Azure WIQL):**

```json
{
  "query": "SELECT [System.Id] FROM WorkItems WHERE [System.WorkItemType] = 'Bug' AND [System.State] = 'Active'"
}
```

**Returns:**

```json
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
  "total": 1,
  "truncated": false
}
```

---

## `get_tickets_needing_my_reply`

Find active tickets assigned to you where the last comment was not written by you.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `my_display_name` | str | Yes | Your display name (Azure) or username (GitHub) |

**Returns:**

```json
{
  "pending": [
    {
      "ticket_id": 1250,
      "title": "Login fails on Safari",
      "last_comment_author": "Luigi Verdi",
      "last_comment_date": "2026-02-05T09:15:00Z",
      "last_comment_preview": "Can you verify if the issue persists?"
    }
  ],
  "total": 1,
  "message": "Found 1 ticket(s) needing your reply."
}
```

---

## `link_tickets`

Link two tickets together.

**Parameters:**

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `source_id` | int | Yes | — | Source ticket ID |
| `target_id` | int | Yes | — | Target ticket ID |
| `link_type` | str | No | varies | Link type (see below) |
| `comment` | str | No | `""` | Optional comment |

**Link type defaults:**

- Azure: `System.LinkTypes.Hierarchy-Reverse` (parent)
- GitHub: `related` (cross-reference comment)

**Returns:**

```json
{
  "success": true,
  "source_id": 1289,
  "target_id": 1200,
  "link_type": "System.LinkTypes.Hierarchy-Reverse",
  "message": "Link created: #1289 -> #1200 (System.LinkTypes.Hierarchy-Reverse)"
}
```

---

## `get_ticket_relations`

Retrieve all links and cross-references of a ticket.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `ticket_id` | int | Yes | Ticket ID |

**Returns:**

```json
{
  "ticket_id": 1234,
  "relations": [
    {
      "type": "System.LinkTypes.Hierarchy-Reverse",
      "target_id": "1200",
      "comment": ""
    }
  ],
  "total": 1
}
```

---

## `get_iterations`

List all iterations/sprints (Azure) or milestones (GitHub).

**Parameters:** None

**Returns (Azure):**

```json
{
  "iterations": [
    {
      "id": "...",
      "name": "Sprint 5",
      "path": "Project\\Sprint 5",
      "start_date": "2026-01-15",
      "finish_date": "2026-01-29",
      "state": "current"
    }
  ],
  "total": 1
}
```

---

## `move_to_iteration`

Move a ticket to a different iteration (Azure) or milestone (GitHub).

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `ticket_id` | int | Yes | Ticket ID |
| `iteration_path` | str | Yes | Iteration path (Azure) or milestone title (GitHub) |

**Returns:**

```json
{
  "success": true,
  "id": 1234,
  "iteration_path": "Project\\Sprint 6",
  "message": "Ticket #1234 moved to 'Project\\Sprint 6'."
}
```

---

## `get_area_paths`

Retrieve all area paths (Azure) or labels (GitHub).

**Parameters:** None

**Returns (Azure):**

```json
{
  "id": "...",
  "name": "Project",
  "children": [
    {"name": "Backend", "children": []},
    {"name": "Frontend", "children": []}
  ]
}
```
