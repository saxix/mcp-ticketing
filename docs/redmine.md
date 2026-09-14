# Redmine

## Overview

The Redmine backend connects to the Redmine REST API (v3, JSON). A "ticket" is a Redmine **issue**. The `ticket_id` used by the tools is the issue numeric id (e.g. `123`). Project-related concepts map as follows:

- **area_path** → issue **category**
- **iteration** → project **version** (`fixed_version`)
- **ticket_type** → **tracker**
- **links** → native **issue relations**

## Credentials

Set these environment variables:

```bash
MCP_REDMINE_URL=https://your-redmine.example.com
MCP_REDMINE_API_KEY=your-redmine-api-key
MCP_REDMINE_PROJECT=your-project-identifier-or-id
```

`MCP_REDMINE_PROJECT` accepts either the project **identifier** or the numeric **id**.

### Authentication

The connector authenticates with the `X-Redmine-API-Key` header. To create an API key, open your Redmine account page and enable the "REST web service" API key (visible in **My account** → **API access key**).

### API Base URL

```
https://{host}/
```

JSON endpoints are used throughout (`/issues.json`, `/trackers.json`, ...).

## Tool Mapping

| Tool | Redmine API |
|------|-------------|
| `get_ticket_types` | `GET projects/{id}.json?include=trackers` |
| `get_ticket` | `GET issues/{id}.json` |
| `get_ticket_comments` | `GET issues/{id}.json?include=journals` |
| `add_comment` | `PUT issues/{id}.json` with `notes` |
| `create_ticket` | `POST issues.json` |
| `update_ticket` | `PUT issues/{id}.json` |
| `search_tickets` | `GET issues.json` (filters + `subject~`) |
| `get_tickets_needing_my_reply` | `GET my/account.json` + `GET issues.json?include=journals` |
| `link_tickets` | `POST issues/{id}/relations.json` |
| `get_ticket_relations` | `GET issues/{id}/relations.json` |
| `get_iterations` | `GET projects/{id}/versions.json` |
| `move_to_iteration` | `PUT issues/{id}.json` with `fixed_version_id` |
| `get_area_paths` | `GET projects/{id}.json?include=issue_categories` |

## Tool Differences

Some tools behave differently on Redmine:

### `create_ticket` / `get_ticket_types`

`ticket_type` is a **tracker** name (e.g. `Bug`, `Feature`). When omitted, `create_ticket` returns the available trackers.

`priority` (1-4) maps to the instance priorities ordered by position (`GET enumerations/issue_priorities.json`). Assignees are resolved from a name/login via `GET users.json?name=`.

### `tags`

Redmine core has **no tag support**: the `tags` parameter of `create_ticket`, `update_ticket` and `search_tickets` is ignored.

### `search_tickets`

- A custom `query` performs a subject text search (`subject~`). Other filters are appended.
- `state` accepts `open` / `active` / `new` (open statuses), `closed` / `done` / `completed` / `resolved`, or any **status name**.

### `update_ticket`

`state` accepts a status **name** (case-insensitive); the keywords `closed` / `done` / `completed` / `resolved` map to the project's closed status. Unknown categories and versions return the list of available values.

### `link_tickets`

Uses **native issue relations**:

| `link_type` | Relation |
|-------------|----------|
| `related` (default) | `relates` |
| `precedes` / `follows` | `precedes` / `follows` |
| `blocks` / `blocked` | `blocks` / `blocked` |
| `duplicates` / `duplicated` | `duplicates` / `duplicated` |
| `copied_to` / `copied_from` | `copied_to` / `copied_from` |
| `parent` / `child` | `parent` / `child` |

When a `comment` is provided, it is added as a journal note on the source issue.

### `get_iterations` / `move_to_iteration`

Iterations map to **versions** (`fixed_version`); `move_to_iteration` sets the target version by name.

### `get_area_paths`

Returns the project issue **categories** instead of area paths.

## State Mapping

`update_ticket` accepts a status **name** (case-insensitive), and the keywords `closed` / `done` / `completed` / `resolved` map to the first closed status (`is_closed`).