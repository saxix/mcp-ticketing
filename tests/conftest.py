import os
import sys

# List of connector modules that cache environment variables at import time
_CONNECTOR_MODULES = [
    "mcp_ticketing.azure_connector",
    "mcp_ticketing.github_connector",
]


def _reset_connector_environment():
    """Remove real env vars and set TEST_ vars, then reset connector modules."""
    # 1. Remove real environment variables
    _real_vars = [
        "MCP_AZURE_DEVOPS_ORG",
        "MCP_AZURE_DEVOPS_PROJECT",
        "MCP_AZDO_PAT",
        "MCP_GITHUB_OWNER",
        "MCP_GITHUB_REPO",
        "MCP_GITHUB_TOKEN",
    ]
    for var in _real_vars:
        os.environ.pop(var, None)

    # 2. Set TEST_ prefixed environment variables
    # Use values from TEST_* vars if present, otherwise empty string
    for var in _real_vars:
        test_var = f"TEST_{var}"
        value = os.getenv(test_var, "")
        os.environ[var] = value

    # 3. Reset connector modules to force re-reading environment variables
    # Remove from sys.modules so they are re-imported fresh
    for mod in _CONNECTOR_MODULES:
        if mod in sys.modules:
            del sys.modules[mod]


# Execute reset immediately on conftest module load
_reset_connector_environment()
