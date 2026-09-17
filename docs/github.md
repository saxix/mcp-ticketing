# GitHub

## Overview

The GitHub backend connects to the GitHub REST API to manage issues.

## Credentials

Set these environment variables:

```bash
MCP_GITHUB_OWNER=your-github-username-or-org
MCP_GITHUB_REPO=your-repository-name
MCP_GITHUB_TOKEN=your-github-personal-access-token
```

### PAT Scopes

The Personal Access Token requires **repo** scope (full control of private repositories).

### API Base URL

```
https://api.github.com/repos/{owner}/{repo}/
```

## Tool Mapping

| Tool | GitHub API |
|------|-----------|
| `get_ticket_types` | Returns `[Issue]` (GitHub only supports Issues) |
| `get_ticket` | `GET issues/{number}` |
| `get_ticket_comments` | `GET issues/{number}/comments` |
| `add_comment` | `POST issues/{number}/comments` |
| `add_mermaid` | `GET issues/{number}` + `PATCH issues/{number}` (Markdown body) |
| `create_ticket` | `POST issues` |
| `update_ticket` | `PATCH issues/{number}` |
| `search_tickets` | `GET search/issues` |
| `get_tickets_needing_my_reply` | `GET issues` + `GET issues/{number}/comments` |
| `link_tickets` | `POST issues/{number}/comments` (cross-reference) |
| `get_ticket_relations` | `GET issues/{number}` (timeline events) |
| `get_iterations` | `GET milestones` |
| `move_to_iteration` | `PATCH issues/{number}` |
| `get_area_paths` | `GET labels` |

## Tool Differences

Some tools behave slightly differently on GitHub:

### `get_ticket_types`

GitHub only supports Issues, so this always returns a single type.

### `search_tickets`

When using filters (not a custom query), the search uses GitHub's search API syntax:

- `ticket_type` is mapped to GitHub's type filter
- `state` maps to `is:open` / `is:closed`
- `assigned_to` maps to `assignee`

### `link_tickets`

GitHub Issues don't have formal link types. Links are added as cross-reference comments. The `link_type` parameter is ignored.

### `add_ticket_image`

Not supported on GitHub. GitHub Issues have no native attachment API, so the tool returns an error (`{"error": "add_ticket_image is not supported by this backend."}`). Use Azure DevOps for image attachments.

### `get_area_paths`

Returns GitHub labels instead of area paths, since GitHub uses labels for categorization.

### `get_iterations`

Returns GitHub milestones instead of Azure DevOps iterations.

## State Mapping

| Input | GitHub State |
|-------|-------------|
| `active` / `new` | `open` |
| `closed` / `resolved` | `closed` |
| `open` | `open` |
| `closed` | `closed` |
