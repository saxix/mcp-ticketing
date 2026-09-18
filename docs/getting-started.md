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

See [Configuration](configuration.md) for all environment variables.

## Running the Server

```bash
uv run mcp-ticketing azure    # Azure DevOps
uv run mcp-ticketing github   # GitHub
```

Or run directly:

```bash
uv run python -m mcp_ticketing.main azure    # Azure DevOps (default)
uv run python -m mcp_ticketing.main github   # GitHub
```

## Verifying the Connection

Start the server and use the MCP inspector or opencode to call `get_ticket_types()`. If configured correctly, it returns the list of available ticket types.

Alternatively, verify the credentials from the command line without starting the server:

```bash
uv run mcp-ticketing azure --check   # Prints {"status": "ok", ...} and exits
uv run mcp-ticketing github --check
```

Exit code is `0` on success, `1` on failure.

You can also verify credentials without setting environment variables, passing them directly on the command line:

```bash
uv run mcp-ticketing github --check \
  --owner your-github-username-or-org \
  --repo your-repository-name \
  --token your-github-personal-access-token
```

See [Configuration](configuration.md) for the full flag-to-environment mapping.

## Testing

```bash
uv run pytest tests/ -v
```

Tests are integration tests that call real APIs. They skip automatically when the corresponding environment variables are not set.
