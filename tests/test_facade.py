"""Unit tests for :mod:`jaeger_mcp.facade` — the JaegerClient facade.

These tests mock the underlying :class:`JaegerHTTPClient` so no live
Jaeger instance is required.  They verify:

- ``from jaeger_mcp import JaegerClient`` works (JGR-01).
- ``get_trace`` returns a ``Trace`` whose spans carry ``start_utc``,
  ``service_name``, ``error``, ``tags``, ``span_id``, ``trace_id`` (JGR-02).
- ``search_traces``, ``list_services``, ``get_dependencies`` work correctly.
- ``from_env()`` constructs from environment variables.
- Context manager protocol works.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from jaeger_mcp import JaegerClient, ServiceDep, Span, Trace, TraceSummary


# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_jaeger_trace_response(
    *,
    trace_id: str = "abc123def456abc123def456abc12345",
    error_span: bool = True,
) -> dict:
    """Build a mock Jaeger API response for GET /api/traces/{id}."""
    tags_ok = [
        {"key": "http.method", "value": "GET"},
        {"key": "http.status_code", "value": "200"},
    ]
    tags_err = [
        {"key": "error", "value": "true"},
        {"key": "http.method", "value": "POST"},
        {"key": "http.status_code", "value": "500"},
        {"key": "order_id", "value": "ORD-42"},
    ]
    return {
        "data": [
            {
                "traceID": trace_id,
                "spans": [
                    {
                        "spanID": "span-root",
                        "operationName": "HTTP GET /orders",
                        "processID": "p1",
                        "startTime": 1_700_000_000_000_000,  # 2023-11-14T22:13:20 UTC
                        "duration": 500_000,
                        "references": [],
                        "tags": tags_ok,
                        "logs": [],
                    },
                    {
                        "spanID": "span-child",
                        "operationName": "db.query",
                        "processID": "p2",
                        "startTime": 1_700_000_000_100_000,
                        "duration": 200_000,
                        "references": [{"refType": "CHILD_OF", "spanID": "span-root"}],
                        "tags": tags_err if error_span else tags_ok,
                        "logs": [{"timestamp": 1_700_000_000_150_000, "fields": [{"key": "event", "value": "error"}]}]
                        if error_span
                        else [],
                    },
                ],
                "processes": {
                    "p1": {"serviceName": "order-service"},
                    "p2": {"serviceName": "db-service"},
                },
            }
        ]
    }


def _make_mock_http_client() -> MagicMock:
    """Create a mock JaegerHTTPClient."""
    mock = MagicMock()
    mock.get.return_value = _make_jaeger_trace_response()
    mock.close.return_value = None
    return mock


# ── Import test (JGR-01) ─────────────────────────────────────────────────


class TestImport:
    def test_import_from_package(self) -> None:
        """JGR-01: ``from jaeger_mcp import JaegerClient`` works."""
        from jaeger_mcp import JaegerClient as JC

        assert JC is JaegerClient

    def test_all_domain_types_importable(self) -> None:
        """All facade domain types are importable from the package."""
        from jaeger_mcp import JaegerClient, ServiceDep, Span, Trace, TraceSummary

        assert all(cls is not None for cls in [JaegerClient, Span, Trace, TraceSummary, ServiceDep])


# ── from_env (JGR-01) ────────────────────────────────────────────────────


class TestFromEnv:
    def test_from_env_constructs_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JGR-01: ``JaegerClient.from_env()`` constructs from JAEGER_URL."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.delenv("JAEGER_TOKEN", raising=False)
        monkeypatch.delenv("JAEGER_USERNAME", raising=False)
        monkeypatch.delenv("JAEGER_PASSWORD", raising=False)
        client = JaegerClient.from_env()
        try:
            assert client._http.url == "https://jaeger.example.com"
        finally:
            client.close()

    def test_from_env_with_explicit_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_URL", "https://env.example.com")
        client = JaegerClient.from_env(url="https://explicit.example.com")
        try:
            assert client._http.url == "https://explicit.example.com"
        finally:
            client.close()


# ── get_trace (JGR-02) ───────────────────────────────────────────────────


class TestGetTrace:
    def test_returns_trace_with_spans(self) -> None:
        mock_http = _make_mock_http_client()
        client = JaegerClient(mock_http)
        trace = client.get_trace("abc123def456abc123def456abc12345")

        assert isinstance(trace, Trace)
        assert trace.trace_id == "abc123def456abc123def456abc12345"
        assert len(trace.spans) == 2
        assert trace.service_count == 2
        assert trace.errors_count == 1

    def test_span_has_evidence_fields(self) -> None:
        """JGR-02: Each span carries start_utc, service_name, trace_id,
        span_id, tags, and error."""
        mock_http = _make_mock_http_client()
        client = JaegerClient(mock_http)
        trace = client.get_trace("abc123def456abc123def456abc12345")

        for span in trace.spans:
            assert isinstance(span, Span)
            assert isinstance(span.span_id, str) and span.span_id
            assert isinstance(span.trace_id, str) and span.trace_id
            assert isinstance(span.service_name, str) and span.service_name
            assert isinstance(span.start_utc, datetime)
            assert span.start_utc.tzinfo is not None  # timezone-aware
            assert isinstance(span.tags, dict)
            assert isinstance(span.error, bool)

    def test_error_span_has_error_true(self) -> None:
        """JGR-02: Error span has error=True and a valid start_utc."""
        mock_http = _make_mock_http_client()
        client = JaegerClient(mock_http)
        trace = client.get_trace("abc123def456abc123def456abc12345")

        error_spans = [s for s in trace.spans if s.error]
        assert len(error_spans) == 1

        err = error_spans[0]
        assert err.error is True
        assert err.span_id == "span-child"
        assert err.service_name == "db-service"
        assert err.operation == "db.query"
        assert err.tags.get("order_id") == "ORD-42"
        assert isinstance(err.start_utc, datetime)
        assert err.start_utc.tzinfo == timezone.utc
        # Verify the timestamp conversion is correct
        expected_utc = datetime.fromtimestamp(1_700_000_000_100_000 / 1_000_000, tz=timezone.utc)
        assert err.start_utc == expected_utc

    def test_root_span_has_no_parent(self) -> None:
        mock_http = _make_mock_http_client()
        client = JaegerClient(mock_http)
        trace = client.get_trace("abc123def456abc123def456abc12345")

        root = next(s for s in trace.spans if s.span_id == "span-root")
        assert root.parent_span_id is None
        assert root.error is False
        assert trace.root_operation == "HTTP GET /orders"
        assert trace.root_service == "order-service"

    def test_child_span_has_parent(self) -> None:
        mock_http = _make_mock_http_client()
        client = JaegerClient(mock_http)
        trace = client.get_trace("abc123def456abc123def456abc12345")

        child = next(s for s in trace.spans if s.span_id == "span-child")
        assert child.parent_span_id == "span-root"

    def test_no_trace_data_raises_valueerror(self) -> None:
        mock_http = MagicMock()
        mock_http.get.return_value = {"data": []}
        client = JaegerClient(mock_http)
        with pytest.raises(ValueError, match="No trace data"):
            client.get_trace("nonexistent")

    def test_trace_with_no_errors(self) -> None:
        mock_http = MagicMock()
        mock_http.get.return_value = _make_jaeger_trace_response(error_span=False)
        client = JaegerClient(mock_http)
        trace = client.get_trace("abc123def456abc123def456abc12345")
        assert trace.errors_count == 0
        assert all(not s.error for s in trace.spans)

    def test_span_logs_preserved(self) -> None:
        mock_http = _make_mock_http_client()
        client = JaegerClient(mock_http)
        trace = client.get_trace("abc123def456abc123def456abc12345")

        err = next(s for s in trace.spans if s.error)
        assert len(err.logs) == 1
        assert err.logs[0]["fields"][0]["value"] == "error"


# ── search_traces ─────────────────────────────────────────────────────────


class TestSearchTraces:
    def test_returns_summaries(self) -> None:
        mock_http = MagicMock()
        mock_http.get.return_value = {
            "data": [
                {
                    "traceID": "t1",
                    "spans": [
                        {
                            "spanID": "s1",
                            "operationName": "op",
                            "processID": "p1",
                            "startTime": 1_700_000_000_000_000,
                            "duration": 100_000,
                            "references": [],
                            "tags": [],
                        }
                    ],
                    "processes": {"p1": {"serviceName": "svc"}},
                }
            ]
        }
        client = JaegerClient(mock_http)
        results = client.search_traces("svc")
        assert len(results) == 1
        assert isinstance(results[0], TraceSummary)
        assert results[0].trace_id == "t1"
        assert results[0].start_utc is not None
        assert results[0].start_utc.tzinfo is not None

    def test_passes_filters_to_http(self) -> None:
        mock_http = MagicMock()
        mock_http.get.return_value = {"data": []}
        client = JaegerClient(mock_http)
        client.search_traces(
            "svc",
            operation="GET /api",
            tags={"error": "true"},
            min_duration="100ms",
            max_duration="5s",
            time_from=100,
            time_to=200,
            limit=5,
        )
        call_args = mock_http.get.call_args
        params = call_args[1].get("params") or call_args[0][1] if len(call_args[0]) > 1 else call_args[1]["params"]
        assert params["service"] == "svc"
        assert params["operation"] == "GET /api"
        assert '"error": "true"' in params["tags"]
        assert params["minDuration"] == "100ms"
        assert params["maxDuration"] == "5s"
        assert params["start"] == 100
        assert params["end"] == 200
        assert params["limit"] == 5


# ── list_services ─────────────────────────────────────────────────────────


class TestListServices:
    def test_returns_sorted(self) -> None:
        mock_http = MagicMock()
        mock_http.get.return_value = {"data": ["z-service", "a-service", "m-service"]}
        client = JaegerClient(mock_http)
        result = client.list_services()
        assert result == ["a-service", "m-service", "z-service"]


# ── get_dependencies ──────────────────────────────────────────────────────


class TestGetDependencies:
    def test_returns_service_deps(self) -> None:
        mock_http = MagicMock()
        mock_http.get.return_value = {
            "data": [
                {"parent": "frontend", "child": "api", "callCount": 100},
                {"parent": "api", "child": "db", "callCount": 50},
            ]
        }
        client = JaegerClient(mock_http)
        deps = client.get_dependencies(lookback_hours=12)
        assert len(deps) == 2
        assert isinstance(deps[0], ServiceDep)
        assert deps[0].parent == "frontend"
        assert deps[0].call_count == 100
        # Sorted by call_count descending
        assert deps[0].call_count >= deps[1].call_count


# ── Context manager ───────────────────────────────────────────────────────


class TestContextManager:
    def test_closes_on_exit(self) -> None:
        mock_http = MagicMock()
        with JaegerClient(mock_http) as client:
            assert client is not None
        mock_http.close.assert_called_once()
