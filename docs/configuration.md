# Configuration

All configuration is done through environment variables. Load them via `.env`, `direnv`, or your shell.

## Azure DevOps

| Variable | Required | Description |
|----------|----------|-------------|
| `MCP_AZURE_DEVOPS_ORG` | Yes | Azure DevOps organization name |
| `MCP_AZURE_DEVOPS_PROJECT` | Yes | Azure DevOps project name |
| `MCP_AZDO_PAT` | Yes | Personal Access Token |

### Creating a PAT

1. Go to `https://dev.azure.com/{your-org}/_usersSettings/tokens`
2. Click **New Token**
3. Select scope **Work Items (Read & Write)**
4. Copy the generated token to `.env`

## GitHub

| Variable | Required | Description |
|----------|----------|-------------|
| `MCP_GITHUB_OWNER` | Yes | Repository owner (user or organization) |
| `MCP_GITHUB_REPO` | Yes | Repository name |
| `MCP_GITHUB_TOKEN` | Yes | Personal Access Token |

### Creating a PAT

1. Go to `https://github.com/settings/tokens`
2. Click **Generate new token (classic)**
3. Select scope **repo** (full control of private repositories)
4. Copy the generated token to `.env`

## Taiga

| Variable | Required | Description |
|----------|----------|-------------|
| `MCP_TAIGA_URL` | No | API base URL (default `https://api.taiga.io/api/v1`) |
| `MCP_TAIGA_USERNAME` | Yes | Taiga username |
| `MCP_TAIGA_PASSWORD` | Yes | Taiga password |
| `MCP_TAIGA_PROJECT_ID` | Yes | Taiga project ID |

The connector authenticates with `POST /auth` (username + password) and refreshes the bearer token automatically on HTTP 401.

## Redmine

| Variable | Required | Description |
|----------|----------|-------------|
| `MCP_REDMINE_URL` | Yes | Redmine base URL (e.g. `https://redmine.example.com`) |
| `MCP_REDMINE_API_KEY` | Yes | API key (sent as `X-Redmine-API-Key`) |
| `MCP_REDMINE_PROJECT` | Yes | Project identifier or numeric ID |

The connector authenticates with the `X-Redmine-API-Key` header. Project-specific data (trackers, categories, versions) is loaded from the configured project.

## Security

!!! warning "Never commit credentials"

    The `.env` file is gitignored by default. Never commit PATs or secrets to the repository.

The HTTP client uses basic auth with an empty username and the PAT as the password.
