"""Tests for the CLI argument parsing and env-var overrides in main.py (no network)."""

import os

from mcp_ticketing.main import CLI_ENV_MAP, _apply_cli_env, _parse_args


def _parse(*argv: str):
    return _parse_args(list(argv))


# ──────────────────────────────────────────────
#  ARGUMENT PARSING
# ──────────────────────────────────────────────


def test_default_backend_is_azure():
    args = _parse()
    assert args.backend == "azure"
    assert args.check is False


def test_selects_backend_positionally():
    assert _parse("github").backend == "github"
    assert _parse("azure").backend == "azure"


def test_check_flag():
    assert _parse("github", "--check").check is True


def test_legacy_check_flag_is_accepted():
    assert _parse("github", "-check").check is True


def test_owner_repo_token_flags():
    args = _parse("github", "--owner", "acme", "--repo", "tickets", "--token", "abc")
    assert args.owner == "acme"
    assert args.repo == "tickets"
    assert args.token == "abc"


# ──────────────────────────────────────────────
#  ENV VAR OVERRIDES
# ──────────────────────────────────────────────


def test_github_flags_set_env(monkeypatch):
    for var in CLI_ENV_MAP["github"].values():
        monkeypatch.delenv(var, raising=False)

    args = _parse(
        "github", "--owner", "acme", "--repo", "tickets", "--token", "tok-123"
    )
    _apply_cli_env(args)

    assert os.getenv("MCP_GITHUB_OWNER") == "acme"
    assert os.getenv("MCP_GITHUB_REPO") == "tickets"
    assert os.getenv("MCP_GITHUB_TOKEN") == "tok-123"


def test_azure_flags_set_env(monkeypatch):
    for var in CLI_ENV_MAP["azure"].values():
        monkeypatch.delenv(var, raising=False)

    args = _parse("azure", "--owner", "myorg", "--repo", "myproj", "--token", "pat-xyz")
    _apply_cli_env(args)

    assert os.getenv("MCP_AZURE_DEVOPS_ORG") == "myorg"
    assert os.getenv("MCP_AZURE_DEVOPS_PROJECT") == "myproj"
    assert os.getenv("MCP_AZDO_PAT") == "pat-xyz"


def test_cli_flags_override_existing_env(monkeypatch):
    monkeypatch.setenv("MCP_GITHUB_OWNER", "old-owner")

    args = _parse("github", "--owner", "new-owner")
    _apply_cli_env(args)

    assert os.getenv("MCP_GITHUB_OWNER") == "new-owner"


def test_empty_flags_do_not_touch_env(monkeypatch):
    for var in CLI_ENV_MAP["azure"].values():
        monkeypatch.delenv(var, raising=False)

    _apply_cli_env(_parse("azure"))

    assert not os.getenv("MCP_AZURE_DEVOPS_ORG")
    assert not os.getenv("MCP_AZURE_DEVOPS_PROJECT")
    assert not os.getenv("MCP_AZDO_PAT")


def test_github_flags_do_not_leak_into_azure_env(monkeypatch):
    all_vars = set(CLI_ENV_MAP["azure"].values()) | set(CLI_ENV_MAP["github"].values())
    for var in all_vars:
        monkeypatch.delenv(var, raising=False)

    args = _parse("github", "--owner", "acme", "--repo", "tickets", "--token", "t")
    _apply_cli_env(args)

    assert os.getenv("MCP_AZURE_DEVOPS_ORG") is None
    assert os.getenv("MCP_AZURE_DEVOPS_PROJECT") is None
    assert os.getenv("MCP_AZDO_PAT") is None
