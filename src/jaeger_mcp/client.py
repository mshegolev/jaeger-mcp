"""HTTP client for the Jaeger Query HTTP API.

Thin wrapper around :mod:`httpx` — reads config from env vars, supports
Bearer-token auth, HTTP Basic auth, SSL-verify toggling, and exposes
both async ``aget()`` and sync ``get()`` methods.
Errors bubble up as :class:`httpx.HTTPStatusError` and are mapped to
user-facing messages by :mod:`jaeger_mcp.errors`.

**Auth priority:** JAEGER_TOKEN (Bearer) takes precedence over
JAEGER_USERNAME/JAEGER_PASSWORD (Basic). If neither is set the client
is unauthenticated — valid for many internal Jaeger deployments.

**Threading model.** The client uses ``httpx.AsyncClient`` internally.
Sync wrappers (``get``, ``close``) are provided for backward
compatibility with callers that haven't migrated to async yet.
``get()`` delegates to ``aget()`` via ``asyncio.run()`` or a thread pool
when called from within an already-running event loop.

**Naming:** ``JaegerHTTPClient`` is the low-level HTTP transport.
The public-facing ``JaegerClient`` facade lives in :mod:`jaeger_mcp.facade`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from jaeger_mcp.errors import ConfigError

logger = logging.getLogger(__name__)


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


class JaegerHTTPClient:
    """Minimal Jaeger Query HTTP API client.

    The client reads ``JAEGER_URL``, ``JAEGER_TOKEN``, ``JAEGER_USERNAME``,
    ``JAEGER_PASSWORD``, ``JAEGER_SSL_VERIFY`` from the environment.
    Instances are safe to reuse — a single :class:`httpx.AsyncClient` is
    lazily created for connection pooling.

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
        timeout: float | None = None,
        retry_attempts: int | None = None,
        cache_ttl: float | None = None,
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

        # JGR-09: Configurable timeout (seconds). Default 30s.
        if timeout is not None:
            self.timeout = timeout
        else:
            self.timeout = float(os.environ.get("JAEGER_TIMEOUT", "30"))

        # JGR-03: Retry with exponential backoff. Default 3 attempts.
        if retry_attempts is not None:
            retries = retry_attempts
        else:
            retries = int(os.environ.get("JAEGER_RETRY_ATTEMPTS", "3"))

        self._retry_total = retries
        self._retry_backoff = 1  # 1s, 2s, 4s …
        self._retry_status_codes = {429, 500, 502, 503, 504}

        # JGR-04: TTL cache for discovery endpoints. Default 120s.
        if cache_ttl is not None:
            self.cache_ttl = cache_ttl
        else:
            self.cache_ttl = float(os.environ.get("JAEGER_CACHE_TTL", "120"))
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = asyncio.Lock()

        # Build headers dict
        self._headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "jaeger-mcp",
        }

        # Auth priority: Bearer > Basic > none.
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"

        # Basic auth: httpx uses auth= parameter
        if self.username and self.password and not self.token:
            self._auth: httpx.BasicAuth | None = httpx.BasicAuth(self.username, self.password)
        else:
            self._auth = None

        # Lazy-init httpx.AsyncClient
        self._client: httpx.AsyncClient | None = None

        if not self.ssl_verify:
            logger.warning("ssl_verify=false url=%s — TLS certificate verification disabled", self.url)

        # ASYNC-02: Concurrency limit for aget_many (default 10)
        self._concurrency_limit = int(os.environ.get("JAEGER_CONCURRENCY_LIMIT", "10"))

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Lazily create the httpx.AsyncClient on first use."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self._headers,
                auth=self._auth,
                verify=self.ssl_verify,
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
                trust_env=False,
            )
        return self._client

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = f"{self.api_url}{endpoint}"
        client = await self._ensure_client()
        t0 = time.monotonic()
        try:
            response = await client.request(
                method=method,
                url=url,
                params=params,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "method=%s url=%s status=%d ms=%.1f",
                method,
                url,
                response.status_code,
                elapsed_ms,
            )
            response.raise_for_status()
            return response
        except Exception:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info("method=%s url=%s status=ERR ms=%.1f", method, url, elapsed_ms)
            raise

    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Execute an HTTP request with retry logic.

        Retries on status codes in ``_retry_status_codes`` and connection
        errors with exponential backoff.
        """
        last_exc: Exception | None = None
        for attempt in range(self._retry_total + 1):
            try:
                return await self._request(method, endpoint, params=params)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in self._retry_status_codes:
                    raise
                last_exc = exc
                if attempt < self._retry_total:
                    delay = self._retry_backoff * (2**attempt)
                    await asyncio.sleep(delay)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_exc = exc
                if attempt < self._retry_total:
                    delay = self._retry_backoff * (2**attempt)
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    # ── Cache helpers (JGR-04) ────────────────────────────────────────

    async def _cache_get(self, key: str) -> Any | None:
        """Return cached value if TTL hasn't expired, else None."""
        if self.cache_ttl <= 0:
            return None
        async with self._cache_lock:
            entry = self._cache.get(key)
            if entry is not None:
                ts, value = entry
                if time.monotonic() - ts < self.cache_ttl:
                    return value
                del self._cache[key]
        return None

    async def _cache_set(self, key: str, value: Any) -> None:
        """Store a value in the cache with current timestamp."""
        if self.cache_ttl <= 0:
            return
        async with self._cache_lock:
            self._cache[key] = (time.monotonic(), value)

    async def aget(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Async GET ``{api_url}{endpoint}`` and return parsed JSON.

        Jaeger always returns JSON for 2xx responses; returns ``None`` for
        empty bodies.

        Discovery endpoints (``/services`` and ``/services/*/operations``)
        are cached for ``cache_ttl`` seconds (JGR-04).
        """
        # Check cache for discovery endpoints.
        cache_key: str | None = None
        if endpoint == "/services" and params is None:
            cache_key = "services"
        elif endpoint.endswith("/operations") and endpoint.startswith("/services/"):
            cache_key = f"ops:{endpoint}"

        if cache_key is not None:
            cached = await self._cache_get(cache_key)
            if cached is not None:
                return cached

        response = await self._request_with_retry("GET", endpoint, params=params)
        if not response.content:
            return None
        result = response.json()

        # Store in cache if this was a discovery endpoint.
        if cache_key is not None:
            await self._cache_set(cache_key, result)

        return result

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Sync GET wrapper — delegates to :meth:`aget`.

        Handles the case where it's called from within an already-running
        event loop (e.g. when MCP tools run inside FastMCP's async loop)
        by running aget in a new thread.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running — safe to use asyncio.run()
            return asyncio.run(self.aget(endpoint, params=params))
        # Event loop is running — run in a thread pool to avoid nested loop
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self.aget(endpoint, params=params)).result()

    async def aclose(self) -> None:
        """Async close: shut down the httpx client and zero credentials."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self.token = ""
        self.username = ""
        self.password = ""

    def close(self) -> None:
        """Close the underlying HTTP client and zero credentials.

        Called from lifespan on shutdown. Credential attributes are cleared
        to reduce the window of exposure in long-running processes (JGR-12).
        """
        if self._client is not None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self._client.aclose())
            else:
                # Can't await in sync context with running loop —
                # use thread pool as escape hatch
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(asyncio.run, self._client.aclose()).result()
            self._client = None
        self.token = ""
        self.username = ""
        self.password = ""

    async def aget_many(
        self,
        endpoints: list[tuple[str, dict[str, Any] | None]],
    ) -> list[Any]:
        """Fetch multiple endpoints concurrently with bounded concurrency.

        Args:
            endpoints: List of (endpoint, params) tuples.

        Returns:
            List of JSON results in same order as input endpoints.

        Uses asyncio.Semaphore to limit concurrent requests to
        self._concurrency_limit (default 10).
        """
        sem = asyncio.Semaphore(self._concurrency_limit)

        async def _fetch_one(endpoint: str, params: dict[str, Any] | None) -> Any:
            async with sem:
                return await self.aget(endpoint, params=params)

        return await asyncio.gather(*(_fetch_one(ep, params) for ep, params in endpoints))

    def get_many(self, endpoints: list[tuple[str, dict[str, Any] | None]]) -> list[Any]:
        """Sync wrapper for aget_many."""
        return asyncio.run(self.aget_many(endpoints))

    async def aget_stream(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Fetch endpoint with streaming response and incremental JSON parsing.

        For large traces (500+ spans), this avoids buffering the entire
        response body in memory before parsing. Uses httpx streaming +
        incremental byte collection and json.loads on the complete buffer.

        For truly incremental parsing of massive payloads, consider ijson.
        For our use case (Jaeger traces up to ~10MB), collecting bytes
        incrementally and parsing once is sufficient and avoids the ijson
        dependency.

        Returns:
            Parsed JSON response.
        """
        client = await self._ensure_client()
        url = f"{self.api_url}{endpoint}"
        t0 = time.monotonic()

        chunks: list[bytes] = []
        try:
            async with client.stream("GET", url, params=params) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    chunks.append(chunk)

            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "method=GET url=%s status=%d ms=%.1f stream=true",
                url,
                response.status_code,
                elapsed_ms,
            )

            import json

            body = b"".join(chunks)
            if not body:
                return None
            return json.loads(body)
        except Exception:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info("method=GET url=%s status=ERR ms=%.1f stream=true", url, elapsed_ms)
            raise

    def get_stream(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Sync wrapper for aget_stream."""
        return asyncio.run(self.aget_stream(endpoint, params=params))
