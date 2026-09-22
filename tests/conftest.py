import os

import pytest


@pytest.fixture(scope="session")
def azure_available():
    return all(os.getenv(v, "") for v in ["MCP_AZURE_DEVOPS_ORG", "MCP_AZURE_DEVOPS_PROJECT", "MCP_AZDO_PAT"])


@pytest.fixture(scope="session")
def github_available():
    return all(os.getenv(v, "") for v in ["MCP_GITHUB_OWNER", "MCP_GITHUB_REPO", "MCP_GITHUB_TOKEN"])
