"""Tests for Phase 2: Reliability — retry, cache, configurable timeout.

JGR-03: HTTP retry with exponential backoff
JGR-04: TTL cache for discovery endpoints
JGR-09: Configurable HTTP timeout
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from jaeger_mcp.client import JaegerHTTPClient


# ── JGR-03: Retry ────────────────────────────────────────────────────────


class TestRetry:
    @respx.mock
    async def test_retry_on_503_then_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """503 → 200: client retries and returns success."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        route = respx.get("https://jaeger.example.com/api/services").mock(
            side_effect=[
                httpx.Response(503, json={"errors": ["unavailable"]}),
                httpx.Response(200, json={"data": ["svc-a"]}),
            ]
        )
        client = JaegerHTTPClient(cache_ttl=0)
        try:
            result = await client.aget("/services")
            assert result == {"data": ["svc-a"]}
            assert route.call_count == 2  # 1 fail + 1 success
        finally:
            await client.aclose()

    @respx.mock
    async def test_retry_exhausted_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """3x 503: client raises after exhausting retries."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        respx.get("https://jaeger.example.com/api/services").mock(
            side_effect=[
                httpx.Response(503, json={"errors": ["unavailable"]}),
                httpx.Response(503, json={"errors": ["unavailable"]}),
                httpx.Response(503, json={"errors": ["unavailable"]}),
            ]
        )
        client = JaegerHTTPClient(retry_attempts=2, cache_ttl=0)
        try:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.aget("/services")
            assert exc_info.value.response.status_code == 503
        finally:
            await client.aclose()

    @respx.mock
    async def test_retry_only_on_configured_status_codes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """404 is not retried."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        route = respx.get("https://jaeger.example.com/api/services").mock(
            return_value=httpx.Response(404, json={"errors": ["not found"]})
        )
        client = JaegerHTTPClient(cache_ttl=0)
        try:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.aget("/services")
            assert exc_info.value.response.status_code == 404
            assert route.call_count == 1  # no retries
        finally:
            await client.aclose()

    @respx.mock
    async def test_retry_on_connect_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connection errors are retried."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        route = respx.get("https://jaeger.example.com/api/services").mock(
            side_effect=[
                httpx.ConnectError("DNS lookup failed"),
                httpx.Response(200, json={"data": ["svc-a"]}),
            ]
        )
        client = JaegerHTTPClient(cache_ttl=0)
        try:
            result = await client.aget("/services")
            assert result == {"data": ["svc-a"]}
            assert route.call_count == 2
        finally:
            await client.aclose()

    @respx.mock
    async def test_retry_backoff_exponential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Retries use exponential backoff (1s, 2s, 4s)."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        respx.get("https://jaeger.example.com/api/services").mock(
            side_effect=[
                httpx.Response(503, json={"errors": ["unavailable"]}),
                httpx.Response(503, json={"errors": ["unavailable"]}),
                httpx.Response(503, json={"errors": ["unavailable"]}),
                httpx.Response(200, json={"data": ["svc-a"]}),
            ]
        )
        client = JaegerHTTPClient(retry_attempts=3, cache_ttl=0)
        try:
            t0 = time.monotonic()
            result = await client.aget("/services")
            elapsed = time.monotonic() - t0
            assert result == {"data": ["svc-a"]}
            # Backoff: 1s + 2s + 4s = 7s minimum
            assert elapsed >= 7.0, f"Expected at least 7s, got {elapsed:.3f}s"
        finally:
            await client.aclose()


# ── JGR-04: Cache ─────────────────────────────────────────────────────────


class TestCache:
    @respx.mock
    async def test_cache_services_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Services endpoint is cached for cache_ttl seconds."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        route = respx.get("https://jaeger.example.com/api/services").mock(
            return_value=httpx.Response(200, json={"data": ["svc-a", "svc-b"]})
        )
        client = JaegerHTTPClient(cache_ttl=5.0)  # 5s TTL
        try:
            # First call hits network
            result1 = await client.aget("/services")
            assert route.call_count == 1
            assert result1 == {"data": ["svc-a", "svc-b"]}

            # Second call within TTL uses cache
            result2 = await client.aget("/services")
            assert route.call_count == 1  # no additional network call
            assert result2 == {"data": ["svc-a", "svc-b"]}
            assert result1 is result2  # same object from cache
        finally:
            await client.aclose()

    @respx.mock
    async def test_cache_operations_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Operations endpoint is cached for cache_ttl seconds."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        route = respx.get("https://jaeger.example.com/api/services/web/operations").mock(
            return_value=httpx.Response(200, json={"data": ["GET /", "POST /api"]})
        )
        client = JaegerHTTPClient(cache_ttl=5.0)
        try:
            # First call hits network
            result1 = await client.aget("/services/web/operations")
            assert route.call_count == 1
            assert result1 == {"data": ["GET /", "POST /api"]}

            # Second call within TTL uses cache
            result2 = await client.aget("/services/web/operations")
            assert route.call_count == 1  # no additional network call
            assert result2 == {"data": ["GET /", "POST /api"]}
            assert result1 is result2  # same object from cache
        finally:
            await client.aclose()

    @respx.mock
    async def test_cache_expires_after_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cache expires after cache_ttl seconds."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        route = respx.get("https://jaeger.example.com/api/services").mock(
            side_effect=[
                httpx.Response(200, json={"data": ["svc-a"]}),
                httpx.Response(200, json={"data": ["svc-a", "svc-b"]}),
            ]
        )
        client = JaegerHTTPClient(cache_ttl=0.1)  # 100ms TTL
        try:
            # First call
            result1 = await client.aget("/services")
            assert result1 == {"data": ["svc-a"]}

            # Wait for cache to expire
            await asyncio.sleep(0.15)

            # Second call after TTL expires hits network again
            result2 = await client.aget("/services")
            assert route.call_count == 2
            assert result2 == {"data": ["svc-a", "svc-b"]}
            assert result1 != result2
        finally:
            await client.aclose()

    @respx.mock
    async def test_cache_disabled_when_ttl_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cache is disabled when cache_ttl=0."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        route = respx.get("https://jaeger.example.com/api/services").mock(
            side_effect=[
                httpx.Response(200, json={"data": ["svc-a"]}),
                httpx.Response(200, json={"data": ["svc-a", "svc-b"]}),
            ]
        )
        client = JaegerHTTPClient(cache_ttl=0)  # disabled
        try:
            result1 = await client.aget("/services")
            result2 = await client.aget("/services")
            assert route.call_count == 2  # both hit network
            assert result1 == {"data": ["svc-a"]}
            assert result2 == {"data": ["svc-a", "svc-b"]}
        finally:
            await client.aclose()

    @respx.mock
    async def test_non_discovery_endpoints_not_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-discovery endpoints (traces, dependencies) are not cached."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        route = respx.get("https://jaeger.example.com/api/traces").mock(
            side_effect=[
                httpx.Response(200, json={"data": [{"traceID": "t1"}]}),
                httpx.Response(200, json={"data": [{"traceID": "t2"}]}),
            ]
        )
        client = JaegerHTTPClient(cache_ttl=300)  # enabled
        try:
            result1 = await client.aget("/traces")
            result2 = await client.aget("/traces")
            assert route.call_count == 2  # both hit network
            assert result1 == {"data": [{"traceID": "t1"}]}
            assert result2 == {"data": [{"traceID": "t2"}]}
        finally:
            await client.aclose()

    @patch("jaeger_mcp.client.time")
    async def test_cache_ttl_from_env(self, mock_time, monkeypatch: pytest.MonkeyPatch) -> None:
        """JAEGER_CACHE_TTL=300 results in 300s cache TTL."""
        mock_time.monotonic.side_effect = [0, 0, 299, 301]  # 0s, 0s, 299s, 301s
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.setenv("JAEGER_CACHE_TTL", "300")
        route = respx.get("https://jaeger.example.com/api/services").mock(
            side_effect=[
                httpx.Response(200, json={"data": ["svc-a"]}),
                httpx.Response(200, json={"data": ["svc-a", "svc-b"]}),
            ]
        )
        client = JaegerHTTPClient()
        assert client.cache_ttl == 300.0


# ── JGR-09: Configurable Timeout ─────────────────────────────────────────


class TestConfigurableTimeout:
    def test_default_timeout_30(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default timeout is 30s."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        client = JaegerHTTPClient()
        assert client.timeout == 30.0

    def test_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JAEGER_TIMEOUT=5 results in 5s timeout."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.setenv("JAEGER_TIMEOUT", "5")
        client = JaegerHTTPClient()
        assert client.timeout == 5.0

    def test_timeout_from_constructor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Constructor override takes precedence over env."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.setenv("JAEGER_TIMEOUT", "99")
        client = JaegerHTTPClient(timeout=7)
        assert client.timeout == 7.0

    @respx.mock
    async def test_timeout_passed_to_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Configured timeout is used in HTTP requests."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        respx.get("https://jaeger.example.com/api/services").mock(
            return_value=httpx.Response(200, json={"data": []}),
        )
        client = JaegerHTTPClient(timeout=15, retry_attempts=0, cache_ttl=0)
        try:
            await client.aget("/services")
            assert client.timeout == 15.0
        finally:
            await client.aclose()


# ── ASYNC-02: Concurrent Fetching ────────────────────────────────────────


class TestConcurrentFetch:
    @pytest.mark.asyncio
    async def test_aget_many_fetches_concurrently(self, monkeypatch):
        """Multiple endpoints fetched via asyncio.gather."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        client = JaegerHTTPClient(cache_ttl=0)

        call_times = []

        async def mock_aget(endpoint, params=None):
            call_times.append(asyncio.get_event_loop().time())
            await asyncio.sleep(0.05)  # 50ms simulated latency
            return {"data": [f"result-{endpoint}"]}

        client.aget = mock_aget

        endpoints: list[tuple[str, dict[str, Any] | None]] = [(f"/traces/trace{i}", None) for i in range(10)]

        t0 = asyncio.get_event_loop().time()
        results = await client.aget_many(endpoints)
        elapsed = asyncio.get_event_loop().time() - t0

        assert len(results) == 10
        # Sequential would take 10 * 0.05 = 0.5s. Concurrent should be ~0.05s.
        # Allow 3x margin: should be at least 3x faster than sequential.
        assert elapsed < 0.5 / 3, f"Concurrent fetch took {elapsed:.3f}s, expected < {0.5 / 3:.3f}s"

        await client.aclose()

    @pytest.mark.asyncio
    async def test_aget_many_respects_semaphore(self, monkeypatch):
        """No more than concurrency_limit requests in flight simultaneously."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        client = JaegerHTTPClient(cache_ttl=0)
        client._concurrency_limit = 3  # low limit for testing

        in_flight = 0
        max_in_flight = 0

        async def mock_aget(endpoint, params=None):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return {"data": []}

        client.aget = mock_aget

        endpoints: list[tuple[str, dict[str, Any] | None]] = [(f"/traces/trace{i}", None) for i in range(10)]
        await client.aget_many(endpoints)

        assert max_in_flight <= 3, f"Max in-flight was {max_in_flight}, expected <= 3"

        await client.aclose()

    @pytest.mark.asyncio
    async def test_aget_many_preserves_order(self, monkeypatch):
        """Results returned in same order as input endpoints."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        client = JaegerHTTPClient(cache_ttl=0)

        async def mock_aget(endpoint, params=None):
            await asyncio.sleep(0.01)
            return {"endpoint": endpoint}

        client.aget = mock_aget

        endpoints: list[tuple[str, dict[str, Any] | None]] = [("/a", None), ("/b", None), ("/c", None)]
        results = await client.aget_many(endpoints)

        assert [r["endpoint"] for r in results] == ["/a", "/b", "/c"]

        await client.aclose()

    @pytest.mark.asyncio
    async def test_aget_many_propagates_errors(self, monkeypatch):
        """If one fetch fails, the error propagates."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        client = JaegerHTTPClient(cache_ttl=0)

        async def mock_aget(endpoint, params=None):
            if "bad" in endpoint:
                raise ValueError("bad trace")
            return {"data": []}

        client.aget = mock_aget

        with pytest.raises(ValueError, match="bad trace"):
            await client.aget_many([("/good", None), ("/bad", None)])

        await client.aclose()


# ── ASYNC-03: Streaming ──────────────────────────────────────────────────


class TestStreaming:
    @pytest.mark.asyncio
    async def test_aget_stream_returns_parsed_json(self, monkeypatch):
        """Streaming fetch returns parsed JSON same as regular aget."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")

        import respx
        import httpx

        trace_data = {"data": [{"traceID": "abc123", "spans": [{"spanID": "s1"}]}]}

        with respx.mock:
            respx.get("https://jaeger.example.com/api/traces/abc123").mock(
                return_value=httpx.Response(200, json=trace_data)
            )
            client = JaegerHTTPClient(cache_ttl=0)
            result = await client.aget_stream("/traces/abc123")
            assert result == trace_data
            await client.aclose()

    @pytest.mark.asyncio
    async def test_aget_stream_empty_body_returns_none(self, monkeypatch):
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")

        import respx
        import httpx

        with respx.mock:
            respx.get("https://jaeger.example.com/api/traces/empty").mock(return_value=httpx.Response(200, content=b""))
            client = JaegerHTTPClient(cache_ttl=0)
            result = await client.aget_stream("/traces/empty")
            assert result is None
            await client.aclose()
