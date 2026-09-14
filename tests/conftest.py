import os

import pytest


def pytest_configure(config):
    """Skip tests based on which provider env vars are set."""
    azdo_vars = ["MCP_AZURE_DEVOPS_ORG", "MCP_AZURE_DEVOPS_PROJECT", "MCP_AZDO_PAT"]
    github_vars = ["MCP_GITHUB_OWNER", "MCP_GITHUB_REPO", "MCP_GITHUB_TOKEN"]

    has_azdo = all(os.getenv(v, "") for v in azdo_vars)
    has_github = all(os.getenv(v, "") for v in github_vars)

    if not has_azdo and not has_github:
        missing = azdo_vars + github_vars
        pytest.skip(
            f"Missing environment variables: {', '.join(missing)}",
            allow_module_level=True,
        )


@pytest.fixture(scope="session")
def azure_available():
    return all(
        os.getenv(v, "")
        for v in ["MCP_AZURE_DEVOPS_ORG", "MCP_AZURE_DEVOPS_PROJECT", "MCP_AZDO_PAT"]
    )


@pytest.fixture(scope="session")
def github_available():
    return all(
        os.getenv(v, "")
        for v in ["MCP_GITHUB_OWNER", "MCP_GITHUB_REPO", "MCP_GITHUB_TOKEN"]
    )
