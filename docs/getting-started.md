# Getting Started

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

```bash
git clone https://github.com/saxix/mcp-ticketing.git
cd mcp-ticketing
uv sync
```

## Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env` with your credentials. You can configure one or more backends:

=== "Azure DevOps"

    ```bash
    MCP_AZURE_DEVOPS_ORG=your-organization-name
    MCP_AZURE_DEVOPS_PROJECT=your-project-name
    MCP_AZDO_PAT=your-personal-access-token
    ```

=== "GitHub"

    ```bash
    MCP_GITHUB_OWNER=your-github-username-or-org
    MCP_GITHUB_REPO=your-repository-name
    MCP_GITHUB_TOKEN=your-github-personal-access-token
    ```

=== "Taiga"

    ```bash
    MCP_TAIGA_URL=https://api.taiga.io/api/v1
    MCP_TAIGA_USERNAME=your-taiga-username
    MCP_TAIGA_PASSWORD=your-taiga-password
    MCP_TAIGA_PROJECT_ID=your-project-id
    ```

=== "Redmine"

    ```bash
    MCP_REDMINE_URL=https://your-redmine.example.com
    MCP_REDMINE_API_KEY=your-redmine-api-key
    MCP_REDMINE_PROJECT=your-project-identifier-or-id
    ```

See [Configuration](configuration.md) for all environment variables.

## Running the Server

```bash
uv run mcp-ticketing azure    # Azure DevOps
uv run mcp-ticketing github   # GitHub
uv run mcp-ticketing taiga    # Taiga
uv run mcp-ticketing redmine  # Redmine
```

Or run directly:

```bash
uv run python -m mcp_ticketing.main azure    # Azure DevOps (default)
uv run python -m mcp_ticketing.main redmine  # Redmine
```

## Verifying the Connection

Start the server and use the MCP inspector or opencode to call `get_ticket_types()`. If configured correctly, it returns the list of available ticket types.

Alternatively, verify the credentials from the command line without starting the server:

```bash
uv run mcp-ticketing azure -check   # Prints {"status": "ok", ...} and exits
uv run mcp-ticketing github -check
uv run mcp-ticketing taiga -check
uv run mcp-ticketing redmine -check
```

Exit code is `0` on success, `1` on failure.

## Testing

```bash
uv run pytest tests/ -v
```

Tests are integration tests that call real APIs. They skip automatically when the corresponding environment variables are not set.
