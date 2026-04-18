"""HTTP client for the Jaeger Query HTTP API.

Thin wrapper around :mod:`requests` — reads config from env vars, supports
Bearer-token auth, HTTP Basic auth, SSL-verify toggling, and exposes get().
Errors bubble up as :class:`requests.HTTPError` and are mapped to
user-facing messages by :mod:`jaeger_mcp.errors`.

**Auth priority:** JAEGER_TOKEN (Bearer) takes precedence over
JAEGER_USERNAME/JAEGER_PASSWORD (Basic). If neither is set the session
is unauthenticated — valid for many internal Jaeger deployments.

**Threading model.** The client uses ``requests`` (synchronous). FastMCP
runs synchronous ``@mcp.tool`` in a worker thread via
``anyio.to_thread.run_sync``, so blocking HTTP calls don't block the
asyncio event loop.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3

from jaeger_mcp.errors import ConfigError


def _parse_bool(value: str | bool | None, *, default: bool) -> bool:
    """Parse an env-var boolean.

    Accepts true/false/1/0/yes/no/on/off (case-insensitive). Returns
    ``default`` when ``value`` is ``None`` or empty.
    """
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "no", "off")


def _validate_url(url: str) -> str:
    """Validate that ``url`` is a well-formed HTTP/HTTPS URL.

    Returns the URL with leading/trailing whitespace and any trailing slash
    stripped. Raises :class:`ConfigError` if the URL is missing scheme/host
    or uses an unsupported scheme.
    """
    if not url:
        raise ConfigError("JAEGER_URL is not set — configure the env var (e.g. https://jaeger.example.com)")

    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ConfigError(f"JAEGER_URL must start with http:// or https:// (got: {url!r})")
    if not parsed.netloc:
        raise ConfigError(f"JAEGER_URL is missing host (got: {url!r})")
    return cleaned.rstrip("/")


class JaegerClient:
    """Minimal Jaeger Query HTTP API client.

    The client reads ``JAEGER_URL``, ``JAEGER_TOKEN``, ``JAEGER_USERNAME``,
    ``JAEGER_PASSWORD``, ``JAEGER_SSL_VERIFY`` from the environment.
    Instances are safe to reuse — a single :class:`requests.Session` is kept
    for connection pooling.

    Auth selection:
        - If ``JAEGER_TOKEN`` is set → Bearer auth (ignores username/password).
        - Else if both ``JAEGER_USERNAME`` and ``JAEGER_PASSWORD`` are set → Basic auth.
        - Else → no auth (valid for internal/unauthenticated Jaeger instances).

    Args:
        url: Override ``JAEGER_URL``. If ``None``, read from env.
        token: Override ``JAEGER_TOKEN``. If ``None``, read from env.
        username: Override ``JAEGER_USERNAME``. If ``None``, read from env.
        password: Override ``JAEGER_PASSWORD``. If ``None``, read from env.
        ssl_verify: Override ``JAEGER_SSL_VERIFY``. If ``None``, read from env.

    Raises:
        ConfigError: If JAEGER_URL is missing or malformed.
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        ssl_verify: bool | None = None,
    ) -> None:
        raw_url = url if url is not None else os.environ.get("JAEGER_URL", "")
        self.url = _validate_url(raw_url)
        self.api_url = f"{self.url}/api"

        self.token = token if token is not None else os.environ.get("JAEGER_TOKEN", "")
        self.username = username if username is not None else os.environ.get("JAEGER_USERNAME", "")
        self.password = password if password is not None else os.environ.get("JAEGER_PASSWORD", "")

        if ssl_verify is None:
            ssl_verify = _parse_bool(os.environ.get("JAEGER_SSL_VERIFY"), default=True)
        self.ssl_verify = ssl_verify

        self.session = requests.Session()
        self.session.verify = self.ssl_verify
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "jaeger-mcp",
            }
        )
        # Jaeger is typically an internal service not reachable via env proxy.
        self.session.trust_env = False

        # Auth priority: Bearer > Basic > none.
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"
        elif self.username and self.password:
            self.session.auth = (self.username, self.password)

        if not self.ssl_verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> requests.Response:
        response = self.session.request(
            method=method,
            url=f"{self.api_url}{endpoint}",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        return response

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """GET ``{api_url}{endpoint}`` and return parsed JSON.

        Jaeger always returns JSON for 2xx responses; returns ``None`` for
        empty bodies.
        """
        response = self._request("GET", endpoint, params=params)
        if not response.content:
            return None
        return response.json()

    def close(self) -> None:
        """Close the underlying HTTP session (called from lifespan on shutdown)."""
        self.session.close()
