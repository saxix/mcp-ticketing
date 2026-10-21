"""Thin httpx wrapper for the Redmine JSON REST API.

The client is version-agnostic: it only handles transport (base URL, TLS,
auth headers, 204/empty-body handling). *Which* endpoint or field name to
use for a given operation is decided by the active :class:`~mcp_ticketing.redmine.profile.RedmineVersionProfile`
— the connectors ask the profile, then call the client.

Configuration is read from the environment in ``__init__`` (not at import
time), so the CLI overrides in ``main.py`` and the test ``monkeypatch``
fixtures take effect. ``load_dotenv`` is called once from the composition
root (``main.py``), not here.
"""

import os
from contextlib import asynccontextmanager
from typing import Any

import httpx


class RedmineClient:
    """Thin httpx wrapper for the Redmine JSON REST API.

    Args:
        base_url: Instance base URL (``MCP_REDMINE_URL``).
        api_key: API key from the user's profile page (``MCP_REDMINE_API_KEY``).
        project: Project identifier or ID used as the default scope
            (``MCP_REDMINE_PROJECT``).
        verify: httpx ``verify`` value, resolved from ``MCP_REDMINE_VERIFY_SSL``:
            - empty / unset  -> ``True`` (default CA bundle)
            - ``false`` / ``0`` / ``no`` / ``off`` -> ``False`` (disable TLS verification)
            - anything else  -> treated as a path to a CA bundle file
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        project: str | None = None,
        verify: bool | str | None = None,
    ):
        self.base_url = (base_url if base_url is not None else os.getenv("MCP_REDMINE_URL", "")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("MCP_REDMINE_API_KEY", "")
        self.project = project if project is not None else os.getenv("MCP_REDMINE_PROJECT", "")
        self.verify = verify if verify is not None else self._resolve_verify(os.getenv("MCP_REDMINE_VERIFY_SSL", ""))

    @staticmethod
    def _resolve_verify(value: str) -> bool | str:
        """Resolve the ``verify`` argument for httpx from ``MCP_REDMINE_VERIFY_SSL``."""
        value = value.strip()
        if not value:
            return True
        if value.lower() in ("false", "0", "no", "off"):
            return False
        return value

    @asynccontextmanager
    async def get_client(self):
        async with httpx.AsyncClient(timeout=30.0, verify=self.verify) as client:
            yield client

    def _headers(self) -> dict[str, str]:
        # API-key auth is uniform across the supported range (4.2 – 7.x).
        return {
            "X-Redmine-API-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with self.get_client() as client:
            response = await client.get(url, headers=self._headers(), params=params or {})
            response.raise_for_status()
            return response.json()

    async def post(self, path: str, data: Any, params: dict | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with self.get_client() as client:
            response = await client.post(url, headers=self._headers(), json=data, params=params or {})
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()

    async def put(self, path: str, data: Any, params: dict | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with self.get_client() as client:
            response = await client.put(url, headers=self._headers(), json=data, params=params or {})
            response.raise_for_status()
            # Old Redmine returns 204 No Content (empty body) on success.
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()
