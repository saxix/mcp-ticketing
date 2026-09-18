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

## Command-line overrides

You can bypass the environment variables by passing the credentials directly on the command line. This works with `--check` and with the normal server run.

| Flag | Azure (env var) | GitHub (env var) |
|------|-----------------|------------------|
| `--owner` | `MCP_AZURE_DEVOPS_ORG` | `MCP_GITHUB_OWNER` |
| `--repo` | `MCP_AZURE_DEVOPS_PROJECT` | `MCP_GITHUB_REPO` |
| `--token` | `MCP_AZDO_PAT` | `MCP_GITHUB_TOKEN` |

```bash
# Check the connection using CLI flags only (no .env needed)
uv run mcp-ticketing azure --check --owner unicef --repo ICTD-HCT-MIS --token <pat>
uv run mcp-ticketing github --check --owner saxix --repo myrepo --token <gh_pat>
```

Values passed on the command line take precedence over the environment variables.

## Security

!!! warning "Never commit credentials"

    The `.env` file is gitignored by default. Never commit PATs or secrets to the repository.

The HTTP client uses basic auth with an empty username and the PAT as the password.
