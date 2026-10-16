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

## Redmine

| Variable | Required | Description |
|----------|----------|-------------|
| `MCP_REDMINE_URL` | Yes | Redmine instance base URL (e.g. `https://redmine.example.com`) |
| `MCP_REDMINE_PROJECT` | Yes | Project identifier or ID used as the default scope for tickets |
| `MCP_REDMINE_API_KEY` | Yes | API key of the authenticated user |
| `MCP_REDMINE_VERIFY_SSL` | No | `false`/`0` disables TLS verification; a path to a CA bundle; empty for default |

### Creating an API key

1. Log in to your Redmine instance
2. Open your profile page and go to **My account**
3. Copy the **API key** shown at the top of the page
4. Add it to `.env`

The HTTP client authenticates with the `X-Redmine-API-Key` header and talks to
the JSON REST API (`*.json` endpoints).

## Command-line overrides

You can bypass the environment variables by passing the credentials directly on the command line. This works with `--check` and with the normal server run.

| Flag | Azure (env var) | GitHub (env var) | Redmine (env var) |
|------|-----------------|------------------|-------------------|
| `--owner` | `MCP_AZURE_DEVOPS_ORG` | `MCP_GITHUB_OWNER` | `MCP_REDMINE_URL` |
| `--repo` | `MCP_AZURE_DEVOPS_PROJECT` | `MCP_GITHUB_REPO` | `MCP_REDMINE_PROJECT` |
| `--token` | `MCP_AZDO_PAT` | `MCP_GITHUB_TOKEN` | `MCP_REDMINE_API_KEY` |

```bash
# Check the connection using CLI flags only (no .env needed)
uv run mcp-ticketing azure --check --owner unicef --repo ICTD-HCT-MIS --token <pat>
uv run mcp-ticketing github --check --owner saxix --repo myrepo --token <gh_pat>
uv run mcp-ticketing redmine --check --owner https://redmine.example.com --repo myproject --token <api_key>
```

Values passed on the command line take precedence over the environment variables.

## Security

!!! warning "Never commit credentials"

    The `.env` file is gitignored by default. Never commit PATs or secrets to the repository.

The HTTP client uses basic auth with an empty username and the PAT as the password.
