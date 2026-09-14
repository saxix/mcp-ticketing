# Taiga

## Overview

The Taiga backend connects to the Taiga (classic, v1) REST API to manage project entities as tickets. A "ticket" maps to any of the project entities — **Issue**, **User Story** or **Task** — since refs are unique per project across all entity types. The `ticket_id` used by the tools is the human-readable **`ref`** (e.g. `#123`).

## Credentials

Set these environment variables:

```bash
MCP_TAIGA_URL=https://api.taiga.io/api/v1
MCP_TAIGA_USERNAME=your-taiga-username
MCP_TAIGA_PASSWORD=your-taiga-password
MCP_TAIGA_PROJECT_ID=your-project-id
```

### Authentication

The connector logs in with `POST /auth` (`type: "normal"`, username + password) and caches the bearer token. It is refreshed automatically with `POST /auth/refresh` whenever the API returns HTTP 401.

### API Base URL

```
https://{host}/api/v1/
```

Point `MCP_TAIGA_URL` at your instance (cloud uses `https://api.taiga.io/api/v1`).

## Tool Mapping

| Tool | Taiga API |
|------|-----------|
| `get_ticket_types` | `GET issue-types?project=` (+ the three entity types) |
| `get_ticket` | `GET {issues\|userstories\|tasks}/by_ref?ref=&project=` |
| `get_ticket_comments` | `GET history/{issue\|userstory\|task}/{id}` |
| `add_comment` | `PATCH {entity}/{id}` with `comment` + `version` (OCC) |
| `create_ticket` | `POST issues` / `POST userstories` / `POST tasks` |
| `update_ticket` | `PATCH {entity}/{id}` with `version` (OCC) |
| `search_tickets` | `GET search?project=&text=` or filtered entity lists |
| `get_tickets_needing_my_reply` | `GET users/me` + entity lists + `GET history/...` |
| `link_tickets` | `PATCH {entity}/{id}` (cross-reference comment) |
| `get_ticket_relations` | entity description + `GET history/...` (#ref scan) |
| `get_iterations` | `GET milestones?project=` |
| `move_to_iteration` | `PATCH {entity}/{id}` with `milestone` + `version` |
| `get_area_paths` | `GET projects/{id}/tags_colors` |

Refs are resolved to their concrete entity with the `by_ref` endpoints (`issues/by_ref`, `userstories/by_ref`, `tasks/by_ref`), cached per session.

## Tool Differences

Some tools behave differently on Taiga:

### `create_ticket` / `get_ticket_types`

`ticket_type` accepts:

- `Issue`, `User Story` or `Task` — chooses the entity to create.
- A project issue type name (e.g. `Bug`) — creates an Issue with that type.

`priority` (1-4) and issue-specific state mappings only apply to Issues.

### `search_tickets`

- With a custom `query`, uses `GET search` (free-text across all project entities); other filters are ignored.
- With filters, queries issues/tasks/user stories lists with `status__is_closed`, `assigned_to`, `tags`, `milestone` and issue `type`.

### `link_tickets`

Taiga has no native issue-issue link API. Links are added as cross-reference comments (like GitHub): a comment `Linked ticket: #<target>` is added to the source ticket. The `link_type` parameter is ignored, and `get_ticket_relations` scans descriptions and comments for `#ref`.

### `get_iterations` / `move_to_iteration`

Iterations map to **milestones**: `get_iterations` lists them, `move_to_iteration` assigns a milestone by name.

### `get_area_paths`

Returns the project **tags** (`GET projects/{id}/tags_colors`) instead of area paths, since Taiga uses tags for categorization.

## State Mapping

Taiga statuses are project-specific. `update_ticket` accepts a status **name** (case-insensitive), and the keywords `closed` / `done` / `completed` / `resolved` map to the project's closed status.