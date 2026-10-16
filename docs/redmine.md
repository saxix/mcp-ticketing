# Redmine

## Overview

The Redmine backend connects to the Redmine JSON REST API to manage issues.

## Credentials

Set these environment variables:

```bash
MCP_REDMINE_URL=https://your-redmine-instance.example.com
MCP_REDMINE_PROJECT=your-project-identifier
MCP_REDMINE_API_KEY=your-redmine-api-key
# Optional: TLS verification
MCP_REDMINE_VERIFY_SSL=
```

| Variable | Required | Description |
|----------|----------|-------------|
| `MCP_REDMINE_URL` | Yes | Redmine instance base URL (e.g. `https://redmine.example.com`) |
| `MCP_REDMINE_PROJECT` | Yes | Project identifier or ID used as the default scope for tickets |
| `MCP_REDMINE_API_KEY` | Yes | API key of the authenticated user |
| `MCP_REDMINE_VERIFY_SSL` | No | `false`/`0` disables TLS verification (for self-signed or internal CA certs); a path to a CA bundle file; empty (default) uses the system CA bundle |

### API key

The API key is a per-user token shown at the top of the **My account** page.
The HTTP client authenticates with the `X-Redmine-API-Key` header.

### API Base URL

```
{MCP_REDMINE_URL}/
```

All endpoints are JSON endpoints (`.json` suffix). `MCP_REDMINE_PROJECT`
accepts a project identifier (e.g. `myproject`) or a numeric project ID and is
used as the default scope for `search_tickets`, `get_iterations` and
`get_tickets_needing_my_reply`.

## Tool Mapping

| Tool | Redmine API |
|------|-------------|
| `get_ticket_types` | `GET issue_types.json` |
| `check` | `GET my_account.json` |
| `get_ticket` | `GET issues/{id}.json` |
| `get_ticket_comments` | `GET issues/{id}.json` (journal notes) |
| `add_comment` | `PUT issues/{id}.json` (`issue[notes]`) |
| `add_mermaid` | `POST attachments.json` + `PUT issues/{id}.json` (`<img>` in description) |
| `add_ticket_image` | `POST attachments.json` + `PUT issues/{id}.json` |
| `create_ticket` | `POST issues.json` |
| `update_ticket` | `PUT issues/{id}.json` |
| `search_tickets` | `GET issues.json` (filters) |
| `get_tickets_needing_my_reply` | `GET issues.json` + journal notes |
| `link_tickets` | `PUT issues/{id}.json` (`issue[relations]`) |
| `get_ticket_relations` | `GET issues/{id}.json` (relations) |
| `get_iterations` | `GET projects/{id}/versions.json` |
| `move_to_iteration` | `PUT issues/{id}.json` (`issue[fixed_version_id]`) |
| `get_area_paths` | `GET trackers.json` (issue trackers) |

## Tool Differences

Some tools behave slightly differently on Redmine:

### `get_ticket_types`

Returns the instance's issue types (e.g. Bug, Feature, Task).

### `create_ticket`

- `ticket_type` is the **issue type** name (case-insensitive); when empty,
  Redmine uses the project's default issue type.
- `assigned_to` is a user **login** or **name**.
- `priority` (1-4) is mapped to Redmine's built-in priorities:
  1 = Urgent, 2 = High, 3 = Normal, 4 = Low.
- `iteration_path` is a **version** name (milestone).
- `tags` and `area_path` are ignored (Redmine issues have no native tags or
  area paths).

### `update_ticket`

- `state` accepts either a Redmine **status name** (e.g. `Closed`, `In
  progress`) or a generic word: `open`, `active`, `new`, `reopened` map to
  open statuses; `closed`, `resolved`, `done` map to closed statuses. The
  first matching status name is used.
- `priority` uses the same 1-4 mapping as `create_ticket`.
- `iteration_path` is a version name.

### `search_tickets`

- `query` is a free-text search on subject and description; it is **combined**
  (AND) with the other filters, unlike Azure where a custom WIQL query
  replaces the filters.
- `state` accepts a status name or a generic word (same mapping as
  `update_ticket`).
- `tags` and `area_path` are ignored.

### `link_tickets`

- `link_type` is one of: `relates to` (default), `blocks`, `is blocked by`,
  `duplicates`, `is duplicated by`, `copied from`, `copied to`.
- `comment` is appended to the source ticket as a note.

### `add_ticket_image`

Supported: the image is uploaded through `POST attachments.json` and linked
to the issue; `comment` becomes a note on the issue.

### `get_area_paths`

Returns the issue **trackers** of the instance (Redmine has no area paths;
trackers are the closest structural equivalent).

### `get_iterations`

Returns the project **versions** (milestones) instead of sprints.

## State Mapping

| Input | Redmine status tried (first match wins) |
|-------|------------------------------------------|
| `open` | New, In progress, Reopened |
| `active` | In progress, New, Reopened |
| `new` | New |
| `reopened` | Reopened |
| `closed` | Closed, Done, Resolved |
| `resolved` | Resolved, Closed, Done |
| `done` | Done, Closed |

Status names that exist on the instance (any casing) are always used as-is;
the generic words above are a fallback when no status matches the input.