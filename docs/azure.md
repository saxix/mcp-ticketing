# Azure DevOps

## Overview

The Azure DevOps backend connects to the Azure DevOps REST API (v7.1) to manage work items.

## Credentials

Set these environment variables:

```bash
MCP_AZURE_DEVOPS_ORG=your-organization-name
MCP_AZURE_DEVOPS_PROJECT=your-project-name
MCP_AZDO_PAT=your-personal-access-token
```

### PAT Scopes

The Personal Access Token requires **Work Items (Read & Write)** scope.

### API Base URL

```
https://dev.azure.com/{org}/{project}/_apis/
```

## Tool Mapping

| Tool | Azure DevOps API |
|------|-----------------|
| `get_ticket_types` | `GET wit/workitemtypes` |
| `get_ticket` | `GET wit/workitems/{id}` |
| `get_ticket_comments` | `GET wit/workitems/{id}/comments` |
| `add_comment` | `POST wit/workitems/{id}/comments` |
| `add_mermaid` | `POST wit/attachments` + `PATCH wit/workitems/{id}` (AttachedFile relation + `<img>` embedded in the description; PNG rendered locally via `mermaidx`) |
| `add_ticket_image` | `POST wit/attachments` + `PATCH wit/workitems/{id}` (AttachedFile relation) |
| `create_ticket` | `POST wit/workitems/${type}` |
| `update_ticket` | `PATCH wit/workitems/{id}` |
| `search_tickets` | `POST wit/wiql` + `GET wit/workitems` |
| `get_tickets_needing_my_reply` | `POST wit/wiql` + `GET wit/workitems/{id}/comments` |
| `link_tickets` | `PATCH wit/workitems/{id}` |
| `get_ticket_relations` | `GET wit/workitems/{id}?$expand=relations` |
| `get_iterations` | `GET work/teamsettings/iterations` |
| `move_to_iteration` | `PATCH wit/workitems/{id}` |
| `get_area_paths` | `GET wit/classificationNodes/Areas` |

## Link Types

The `link_tickets` tool supports these link types:

| Value | Description |
|-------|-------------|
| `System.LinkTypes.Hierarchy-Reverse` | Parent (default) |
| `System.LinkTypes.Hierarchy-Forward` | Child |
| `System.LinkTypes.Related` | Related |

## State Values

Common states for `update_ticket` and `search_tickets`:

- `New`, `Active`, `Closed`, `Resolved`, `Removed`

Exact values depend on your project's process template.
