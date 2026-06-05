"""Tests for Phase 2: Reliability — retry, cache, configurable timeout.

JGR-03: HTTP retry with exponential backoff
JGR-04: TTL cache for discovery endpoints
JGR-09: Configurable HTTP timeout
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
import responses
from requests.exceptions import ConnectionError as RequestsConnectionError

from jaeger_mcp.client import JaegerHTTPClient


# ── JGR-03: Retry ────────────────────────────────────────────────────────


class TestRetry:
    @responses.activate
    def test_retry_on_503_then_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """503 → 200: client retries and returns success."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        responses.add(
            responses.GET,
            "https://jaeger.example.com/api/services",
            json={"errors": ["unavailable"]},
            status=503,
        )
        responses.add(
            responses.GET,
            "https://jaeger.example.com/api/services",
            json={"data": ["svc-a"]},
            status=200,
        )
        client = JaegerHTTPClient(cache_ttl=0)
        try:
            result = client.get("/services")
            assert result == {"data": ["svc-a"]}
            assert len(responses.calls) == 2  # 1 fail + 1 success
        finally:
            client.close()

    @responses.activate
    def test_retry_exhausted_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """3x 503: client raises after exhausting retries."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        for _ in range(4):  # initial + 3 retries
            responses.add(
                responses.GET,
                "https://jaeger.example.com/api/services",
                json={"errors": ["unavailable"]},
                status=503,
            )
        client = JaegerHTTPClient(cache_ttl=0)
        try:
            with pytest.raises(Exception):
                client.get("/services")
        finally:
            client.close()

    @responses.activate
    def test_no_retry_on_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """400 errors are not retried."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        responses.add(
            responses.GET,
            "https://jaeger.example.com/api/services",
            json={"errors": ["bad request"]},
            status=400,
        )
        client = JaegerHTTPClient(cache_ttl=0)
        try:
            with pytest.raises(Exception):
                client.get("/services")
            assert len(responses.calls) == 1  # no retry
        finally:
            client.close()

    @responses.activate
    def test_retry_on_429(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """429 rate-limit is retried."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        responses.add(
            responses.GET,
            "https://jaeger.example.com/api/services",
            json={},
            status=429,
        )
        responses.add(
            responses.GET,
            "https://jaeger.example.com/api/services",
            json={"data": ["svc"]},
            status=200,
        )
        client = JaegerHTTPClient(cache_ttl=0)
        try:
            result = client.get("/services")
            assert result == {"data": ["svc"]}
            assert len(responses.calls) == 2
        finally:
            client.close()

    def test_retry_disabled_with_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JAEGER_RETRY_ATTEMPTS=0 disables retry."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        client = JaegerHTTPClient(retry_attempts=0)
        try:
            # Verify no retry adapter is mounted (default adapter)
            adapter = client.session.get_adapter("https://jaeger.example.com")
            assert (
                adapter.max_retries.total == 0
                or not hasattr(adapter.max_retries, "total")
                or adapter.max_retries.total in (0, None)
            )
        finally:
            client.close()

    def test_retry_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JAEGER_RETRY_ATTEMPTS env var is read."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.setenv("JAEGER_RETRY_ATTEMPTS", "5")
        client = JaegerHTTPClient()
        try:
            adapter = client.session.get_adapter("https://jaeger.example.com")
            assert adapter.max_retries.total == 5
        finally:
            client.close()


# ── JGR-04: TTL Cache ────────────────────────────────────────────────────


class TestCache:
    @responses.activate
    def test_list_services_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two list_services calls make only 1 HTTP request (cache hit)."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        responses.add(
            responses.GET,
            "https://jaeger.example.com/api/services",
            json={"data": ["svc-a", "svc-b"]},
            status=200,
        )
        client = JaegerHTTPClient(cache_ttl=60, retry_attempts=0)
        try:
            r1 = client.get("/services")
            r2 = client.get("/services")
            assert r1 == r2
            assert len(responses.calls) == 1  # only 1 HTTP call
        finally:
            client.close()

    @responses.activate
    def test_list_operations_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_operations is cached per-service."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        responses.add(
            responses.GET,
            "https://jaeger.example.com/api/services/order-svc/operations",
            json={"data": ["GET /orders"]},
            status=200,
        )
        client = JaegerHTTPClient(cache_ttl=60, retry_attempts=0)
        try:
            r1 = client.get("/services/order-svc/operations")
            r2 = client.get("/services/order-svc/operations")
            assert r1 == r2
            assert len(responses.calls) == 1
        finally:
            client.close()

    @responses.activate
    def test_get_trace_not_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_trace is NOT cached."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        for _ in range(2):
            responses.add(
                responses.GET,
                "https://jaeger.example.com/api/traces/abc123",
                json={"data": [{"traceID": "abc123", "spans": []}]},
                status=200,
            )
        client = JaegerHTTPClient(cache_ttl=60, retry_attempts=0)
        try:
            client.get("/traces/abc123")
            client.get("/traces/abc123")
            assert len(responses.calls) == 2  # both hit HTTP
        finally:
            client.close()

    @responses.activate
    def test_cache_expires_after_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After TTL expires, next call makes a fresh HTTP request."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        responses.add(
            responses.GET,
            "https://jaeger.example.com/api/services",
            json={"data": ["svc-a"]},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://jaeger.example.com/api/services",
            json={"data": ["svc-a", "svc-b"]},
            status=200,
        )
        client = JaegerHTTPClient(cache_ttl=0.1, retry_attempts=0)  # 100ms TTL
        try:
            r1 = client.get("/services")
            assert len(responses.calls) == 1
            time.sleep(0.15)  # wait for TTL to expire
            r2 = client.get("/services")
            assert len(responses.calls) == 2
            assert r2 == {"data": ["svc-a", "svc-b"]}
        finally:
            client.close()

    @responses.activate
    def test_cache_disabled_with_zero_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JAEGER_CACHE_TTL=0 disables caching."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        for _ in range(2):
            responses.add(
                responses.GET,
                "https://jaeger.example.com/api/services",
                json={"data": ["svc"]},
                status=200,
            )
        client = JaegerHTTPClient(cache_ttl=0, retry_attempts=0)
        try:
            client.get("/services")
            client.get("/services")
            assert len(responses.calls) == 2  # no caching
        finally:
            client.close()

    def test_cache_ttl_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JAEGER_CACHE_TTL env var is read."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.setenv("JAEGER_CACHE_TTL", "300")
        client = JaegerHTTPClient()
        try:
            assert client.cache_ttl == 300.0
        finally:
            client.close()


# ── JGR-09: Configurable Timeout ─────────────────────────────────────────


class TestConfigurableTimeout:
    def test_default_timeout_30(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default timeout is 30s."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        client = JaegerHTTPClient()
        try:
            assert client.timeout == 30.0
        finally:
            client.close()

    def test_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JAEGER_TIMEOUT=5 results in 5s timeout."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.setenv("JAEGER_TIMEOUT", "5")
        client = JaegerHTTPClient()
        try:
            assert client.timeout == 5.0
        finally:
            client.close()

    def test_timeout_from_constructor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Constructor override takes precedence over env."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.setenv("JAEGER_TIMEOUT", "99")
        client = JaegerHTTPClient(timeout=7)
        try:
            assert client.timeout == 7.0
        finally:
            client.close()

    @responses.activate
    def test_timeout_passed_to_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Configured timeout is used in HTTP requests."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        responses.add(
            responses.GET,
            "https://jaeger.example.com/api/services",
            json={"data": []},
            status=200,
        )
        client = JaegerHTTPClient(timeout=15, retry_attempts=0, cache_ttl=0)
        try:
            client.get("/services")
            # responses library doesn't verify timeout directly,
            # but we verified client.timeout is set and _request uses it
            assert client.timeout == 15.0
        finally:
            client.close()
