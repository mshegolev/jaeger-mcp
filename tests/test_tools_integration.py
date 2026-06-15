"""Integration tests for the five MCP tools.

We exercise each tool end-to-end via its public function, mocking the
Jaeger HTTP layer with :mod:`responses`. The goal is to cover the
happy path and key edge cases (empty results, auth errors, filter
forwarding, truncation hints).

These tests don't spin up a full MCP server — they call the decorated
tool functions directly, which is sufficient because our tools contain
the business logic; ``@mcp.tool`` only registers them with FastMCP.
"""

from __future__ import annotations

import pytest
import responses
from mcp.server.fastmcp.exceptions import ToolError

from jaeger_mcp import _mcp
from jaeger_mcp.tools import (
    jaeger_compare_traces,
    jaeger_get_dependencies,
    jaeger_get_trace,
    jaeger_list_operations,
    jaeger_list_services,
    jaeger_search_traces,
    jaeger_span_statistics,
)

BASE = "https://jaeger.example.com"


@pytest.fixture(autouse=True)
def configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set env vars + reset the module-global client cache per-test."""
    monkeypatch.setenv("JAEGER_URL", BASE)
    monkeypatch.delenv("JAEGER_TOKEN", raising=False)
    with _mcp._client_lock:
        if _mcp._client is not None:
            try:
                _mcp._client.close()
            except Exception:
                pass
        _mcp._client = None
    yield
    with _mcp._client_lock:
        if _mcp._client is not None:
            try:
                _mcp._client.close()
            except Exception:
                pass
        _mcp._client = None


# ── jaeger_list_services ───────────────────────────────────────────────────


@responses.activate
def test_list_services_happy_path() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/services",
        json={"data": ["order-service", "payment-service", "api-gateway"]},
        status=200,
    )
    result = jaeger_list_services()
    data = result.structuredContent
    assert data["services_count"] == 3
    assert data["truncated"] is False
    assert "api-gateway" in data["services"]
    # Should be sorted alphabetically
    assert data["services"] == sorted(data["services"])


@responses.activate
def test_list_services_empty() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/services",
        json={"data": []},
        status=200,
    )
    result = jaeger_list_services()
    data = result.structuredContent
    assert data["services_count"] == 0
    assert data["truncated"] is False
    assert data["services"] == []


@responses.activate
def test_list_services_truncated_at_500() -> None:
    big_list = [f"svc-{i}" for i in range(600)]
    responses.add(
        responses.GET,
        f"{BASE}/api/services",
        json={"data": big_list},
        status=200,
    )
    result = jaeger_list_services()
    data = result.structuredContent
    assert data["services_count"] == 500
    assert data["truncated"] is True


@responses.activate
def test_list_services_401_raises_tool_error() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/services",
        json={"errors": ["Unauthorized"]},
        status=401,
    )
    with pytest.raises(ToolError, match="401"):
        jaeger_list_services()


@responses.activate
def test_list_services_markdown_shows_truncation_hint() -> None:
    # 25 services → markdown cap is 20, hint should appear
    services = [f"svc-{i:02d}" for i in range(25)]
    responses.add(
        responses.GET,
        f"{BASE}/api/services",
        json={"data": services},
        status=200,
    )
    result = jaeger_list_services()
    md = result.content[0].text
    assert "Showing first 20 of 25" in md


# ── jaeger_list_operations ─────────────────────────────────────────────────


@responses.activate
def test_list_operations_happy_path() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/services/order-service/operations",
        json={"data": ["GET /orders", "POST /orders", "DELETE /orders/{id}"]},
        status=200,
    )
    result = jaeger_list_operations(service="order-service")
    data = result.structuredContent
    assert data["service"] == "order-service"
    assert data["operations_count"] == 3
    assert data["truncated"] is False
    assert "GET /orders" in data["operations"]


@responses.activate
def test_list_operations_404_raises_tool_error() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/services/nonexistent/operations",
        json={"errors": ["Service not found"]},
        status=404,
    )
    with pytest.raises(ToolError, match="404"):
        jaeger_list_operations(service="nonexistent")


@responses.activate
def test_list_operations_truncated_at_500() -> None:
    big_list = [f"op-{i}" for i in range(600)]
    responses.add(
        responses.GET,
        f"{BASE}/api/services/svc/operations",
        json={"data": big_list},
        status=200,
    )
    result = jaeger_list_operations(service="svc")
    data = result.structuredContent
    assert data["operations_count"] == 500
    assert data["truncated"] is True


# ── jaeger_search_traces ───────────────────────────────────────────────────


def _make_trace(trace_id: str = "abc123", errors: bool = False) -> dict:
    tags = [{"key": "error", "value": True}] if errors else []
    return {
        "traceID": trace_id,
        "spans": [
            {
                "spanID": "s1",
                "operationName": "GET /api",
                "processID": "p1",
                "startTime": 1_700_000_000_000_000,
                "duration": 50_000,
                "references": [],
                "tags": tags,
            }
        ],
        "processes": {"p1": {"serviceName": "api-service"}},
    }


@responses.activate
def test_search_traces_happy_path() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": [_make_trace("trace1"), _make_trace("trace2")]},
        status=200,
    )
    result = jaeger_search_traces(service="api-service")
    data = result.structuredContent
    assert data["service"] == "api-service"
    assert data["returned"] == 2
    assert data["truncated"] is False
    assert data["traces"][0]["trace_id"] == "trace1"


@responses.activate
def test_search_traces_with_operation_filter() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": []},
        status=200,
    )
    jaeger_search_traces(service="svc", operation="GET /health")
    url = responses.calls[0].request.url
    assert "operation=GET+%2Fhealth" in url or "operation=GET" in url


@responses.activate
def test_search_traces_with_tags_filter() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": []},
        status=200,
    )
    jaeger_search_traces(service="svc", tags='{"http.status_code":"500"}')
    url = responses.calls[0].request.url
    assert "tags=" in url


@responses.activate
def test_search_traces_invalid_tags_raises_tool_error() -> None:
    responses.add(responses.GET, f"{BASE}/api/traces", json={"data": []}, status=200)
    with pytest.raises(ToolError, match="valid JSON"):
        jaeger_search_traces(service="svc", tags="not-json")


@responses.activate
def test_search_traces_with_time_range() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": []},
        status=200,
    )
    jaeger_search_traces(service="svc", start=1_000_000, end=2_000_000)
    url = responses.calls[0].request.url
    assert "start=1000000" in url
    assert "end=2000000" in url


@responses.activate
def test_search_traces_with_duration_filters() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": []},
        status=200,
    )
    jaeger_search_traces(service="svc", min_duration="100ms", max_duration="2s")
    url = responses.calls[0].request.url
    assert "minDuration=100ms" in url
    assert "maxDuration=2s" in url


@responses.activate
def test_search_traces_limit_forwarded() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": []},
        status=200,
    )
    jaeger_search_traces(service="svc", limit=50)
    url = responses.calls[0].request.url
    assert "limit=50" in url


@responses.activate
def test_search_traces_errors_counted() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": [_make_trace("t1", errors=True)]},
        status=200,
    )
    result = jaeger_search_traces(service="svc")
    assert result.structuredContent["traces"][0]["errors_count"] == 1


@responses.activate
def test_search_traces_401_raises_tool_error() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={},
        status=401,
    )
    with pytest.raises(ToolError, match="401"):
        jaeger_search_traces(service="svc")


@responses.activate
def test_search_traces_markdown_truncation_hint() -> None:
    traces = [_make_trace(f"t{i}") for i in range(25)]
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": traces},
        status=200,
    )
    result = jaeger_search_traces(service="svc", limit=1500)
    md = result.content[0].text
    assert "Showing first 20 of 25" in md


# ── jaeger_get_trace ───────────────────────────────────────────────────────


def _make_full_trace() -> dict:
    return {
        "traceID": "abcdef1234567890",
        "spans": [
            {
                "spanID": "s1",
                "operationName": "GET /orders",
                "processID": "p1",
                "startTime": 1_700_000_000_000_000,
                "duration": 300_000,
                "references": [],
                "tags": [],
            },
            {
                "spanID": "s2",
                "operationName": "SELECT * FROM orders",
                "processID": "p2",
                "startTime": 1_700_000_000_100_000,
                "duration": 150_000,
                "references": [{"refType": "CHILD_OF", "spanID": "s1"}],
                "tags": [{"key": "error", "value": True}],
            },
        ],
        "processes": {
            "p1": {"serviceName": "order-service"},
            "p2": {"serviceName": "postgres"},
        },
    }


@responses.activate
def test_get_trace_happy_path() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/abcdef1234567890",
        json={"data": [_make_full_trace()]},
        status=200,
    )
    result = jaeger_get_trace(trace_id="abcdef1234567890")
    data = result.structuredContent
    assert data["trace_id"] == "abcdef1234567890"
    assert data["span_count"] == 2
    assert data["service_count"] == 2
    assert data["errors_count"] == 1
    assert data["root_operation"] == "GET /orders"
    assert data["root_service"] == "order-service"


@responses.activate
def test_get_trace_service_breakdown() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/abcdef1234567890",
        json={"data": [_make_full_trace()]},
        status=200,
    )
    result = jaeger_get_trace(trace_id="abcdef1234567890")
    services = result.structuredContent["services"]
    # Sorted by total_duration_us descending
    svc_names = [s["service"] for s in services]
    assert "order-service" in svc_names
    assert "postgres" in svc_names


@responses.activate
def test_get_trace_execution_tree_built() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/abcdef1234567890",
        json={"data": [_make_full_trace()]},
        status=200,
    )
    result = jaeger_get_trace(trace_id="abcdef1234567890")
    tree = result.structuredContent["execution_tree"]
    root_node = next(n for n in tree if n["span_id"] == "s1")
    assert "s2" in root_node["children"]


@responses.activate
def test_get_trace_empty_data_raises_tool_error() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/badtrace00000000",
        json={"data": []},
        status=200,
    )
    with pytest.raises(ToolError, match="No trace data"):
        jaeger_get_trace(trace_id="badtrace00000000")


@responses.activate
def test_get_trace_404_raises_tool_error() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/notfound0000000000000000000000000",
        json={},
        status=404,
    )
    with pytest.raises(ToolError, match="404"):
        jaeger_get_trace(trace_id="notfound0000000000000000000000000")


@responses.activate
def test_get_trace_spans_in_result() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/abcdef1234567890",
        json={"data": [_make_full_trace()]},
        status=200,
    )
    result = jaeger_get_trace(trace_id="abcdef1234567890")
    spans = result.structuredContent["spans"]
    assert len(spans) == 2
    error_spans = [s for s in spans if s["is_error"]]
    assert len(error_spans) == 1
    assert error_spans[0]["operation_name"] == "SELECT * FROM orders"


# ── jaeger_get_dependencies ────────────────────────────────────────────────


@responses.activate
def test_get_dependencies_happy_path() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/dependencies",
        json={
            "data": [
                {"parent": "api-gateway", "child": "order-service", "callCount": 1000},
                {"parent": "order-service", "child": "postgres", "callCount": 500},
                {"parent": "api-gateway", "child": "payment-service", "callCount": 200},
            ]
        },
        status=200,
    )
    result = jaeger_get_dependencies(lookback_hours=24)
    data = result.structuredContent
    assert data["edge_count"] == 3
    assert data["lookback_hours"] == 24
    # Sorted by call_count descending
    assert data["edges"][0]["call_count"] == 1000
    assert data["edges"][0]["parent"] == "api-gateway"
    assert data["edges"][0]["child"] == "order-service"


@responses.activate
def test_get_dependencies_empty() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/dependencies",
        json={"data": []},
        status=200,
    )
    result = jaeger_get_dependencies()
    data = result.structuredContent
    assert data["edge_count"] == 0
    assert data["edges"] == []


@responses.activate
def test_get_dependencies_forwards_lookback() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/dependencies",
        json={"data": []},
        status=200,
    )
    jaeger_get_dependencies(lookback_hours=168)
    url = responses.calls[0].request.url
    # 168h * 3600 * 1000 = 604800000 ms
    assert "lookback=604800000" in url


@responses.activate
def test_get_dependencies_uses_end_ts() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/dependencies",
        json={"data": []},
        status=200,
    )
    jaeger_get_dependencies(end_ts=1_700_000_000_000_000)
    url = responses.calls[0].request.url
    # endTs = end_ts // 1000 (Jaeger expects ms)
    assert "endTs=1700000000000" in url


@responses.activate
def test_get_dependencies_skips_malformed_edges() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/dependencies",
        json={
            "data": [
                {"parent": "a", "child": "b", "callCount": 10},
                {"child": "orphan"},  # no parent → skipped
                {"parent": "no-child"},  # no child → skipped
            ]
        },
        status=200,
    )
    result = jaeger_get_dependencies()
    data = result.structuredContent
    assert data["edge_count"] == 1


@responses.activate
def test_get_dependencies_401_raises_tool_error() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/dependencies",
        json={},
        status=401,
    )
    with pytest.raises(ToolError, match="401"):
        jaeger_get_dependencies()


@responses.activate
def test_get_dependencies_markdown_truncation_hint() -> None:
    edges = [{"parent": f"svc-{i}", "child": "db", "callCount": i} for i in range(25)]
    responses.add(
        responses.GET,
        f"{BASE}/api/dependencies",
        json={"data": edges},
        status=200,
    )
    result = jaeger_get_dependencies()
    md = result.content[0].text
    assert "Showing first 20 of 25" in md


# ── jaeger_compare_traces ─────────────────────────────────────────────────


TRACE_ID_A = "aaa111bbb222ccc333ddd444eee55566"
TRACE_ID_B = "fff666eee555ddd444ccc333bbb22211"


def _make_compare_trace(
    trace_id: str,
    *,
    child_duration: int = 200_000,
    child_operation: str = "db.query",
    child_service_name: str = "db-service",
    root_operation: str = "GET /orders",
    root_service_name: str = "order-service",
    child_tags: list[dict] | None = None,
) -> dict:
    """Build a Jaeger trace dict for comparison tests."""
    return {
        "traceID": trace_id,
        "spans": [
            {
                "spanID": "s1",
                "operationName": root_operation,
                "processID": "p1",
                "startTime": 1_700_000_000_000_000,
                "duration": 500_000,
                "references": [],
                "tags": [],
            },
            {
                "spanID": "s2",
                "operationName": child_operation,
                "processID": "p2",
                "startTime": 1_700_000_000_100_000,
                "duration": child_duration,
                "references": [{"refType": "CHILD_OF", "spanID": "s1"}],
                "tags": child_tags or [],
            },
        ],
        "processes": {
            "p1": {"serviceName": root_service_name},
            "p2": {"serviceName": child_service_name},
        },
    }


@responses.activate
def test_compare_traces_happy_path() -> None:
    trace_a = _make_compare_trace(TRACE_ID_A, child_duration=200_000)
    trace_b = _make_compare_trace(TRACE_ID_B, child_duration=300_000)
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/{TRACE_ID_A}",
        json={"data": [trace_a]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/{TRACE_ID_B}",
        json={"data": [trace_b]},
        status=200,
    )
    result = jaeger_compare_traces(trace_id_a=TRACE_ID_A, trace_id_b=TRACE_ID_B)
    data = result.structuredContent
    assert data["trace_id_a"] == TRACE_ID_A
    assert data["trace_id_b"] == TRACE_ID_B
    assert data["unchanged_count"] == 1  # root span unchanged
    assert len(data["changed_spans"]) == 1
    assert data["changed_spans"][0]["duration_delta_us"] == 100_000


@responses.activate
def test_compare_traces_identical() -> None:
    trace = _make_compare_trace(TRACE_ID_A)
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/{TRACE_ID_A}",
        json={"data": [trace]},
        status=200,
    )
    # Use same trace data for B (different trace ID in URL but same content)
    trace_b = _make_compare_trace(TRACE_ID_B)
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/{TRACE_ID_B}",
        json={"data": [trace_b]},
        status=200,
    )
    result = jaeger_compare_traces(trace_id_a=TRACE_ID_A, trace_id_b=TRACE_ID_B)
    data = result.structuredContent
    assert data["unchanged_count"] == 2
    assert data["added_spans"] == []
    assert data["removed_spans"] == []
    assert data["changed_spans"] == []


@responses.activate
def test_compare_traces_empty_trace_a() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/{TRACE_ID_A}",
        json={"data": []},
        status=200,
    )
    with pytest.raises(ToolError, match="trace_id_a"):
        jaeger_compare_traces(trace_id_a=TRACE_ID_A, trace_id_b=TRACE_ID_B)


@responses.activate
def test_compare_traces_fully_different() -> None:
    trace_a = _make_compare_trace(
        TRACE_ID_A,
        root_operation="GET /orders",
        root_service_name="order-service",
        child_operation="db.query",
        child_service_name="db-service",
    )
    # Completely different services and operations
    trace_b_data = {
        "traceID": TRACE_ID_B,
        "spans": [
            {
                "spanID": "s1",
                "operationName": "POST /payments",
                "processID": "p1",
                "startTime": 1_700_000_000_000_000,
                "duration": 400_000,
                "references": [],
                "tags": [],
            },
        ],
        "processes": {
            "p1": {"serviceName": "payment-service"},
        },
    }
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/{TRACE_ID_A}",
        json={"data": [trace_a]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE}/api/traces/{TRACE_ID_B}",
        json={"data": [trace_b_data]},
        status=200,
    )
    result = jaeger_compare_traces(trace_id_a=TRACE_ID_A, trace_id_b=TRACE_ID_B)
    data = result.structuredContent
    assert len(data["removed_spans"]) == 2  # A's two spans
    assert len(data["added_spans"]) == 1  # B's one span
    assert data["unchanged_count"] == 0
    assert data["changed_spans"] == []


# ── jaeger_span_statistics ────────────────────────────────────────────────


def _make_stats_traces() -> list[dict]:
    """Build two traces with overlapping operations for statistics tests."""
    return [
        {
            "traceID": "aaa111bbb222ccc333ddd444eee55566",
            "spans": [
                {
                    "spanID": "s1",
                    "operationName": "GET /orders",
                    "duration": 1000,
                    "processID": "p1",
                    "tags": [],
                    "references": [],
                },
                {
                    "spanID": "s2",
                    "operationName": "db.query",
                    "duration": 500,
                    "processID": "p1",
                    "tags": [],
                    "references": [{"refType": "CHILD_OF", "spanID": "s1"}],
                },
            ],
            "processes": {"p1": {"serviceName": "order-service"}},
        },
        {
            "traceID": "fff666eee555ddd444ccc333bbb22211",
            "spans": [
                {
                    "spanID": "s1",
                    "operationName": "GET /orders",
                    "duration": 2000,
                    "processID": "p1",
                    "tags": [],
                    "references": [],
                },
                {
                    "spanID": "s2",
                    "operationName": "db.query",
                    "duration": 1500,
                    "processID": "p1",
                    "tags": [],
                    "references": [{"refType": "CHILD_OF", "spanID": "s1"}],
                },
            ],
            "processes": {"p1": {"serviceName": "order-service"}},
        },
    ]


@responses.activate
def test_span_statistics_happy_path() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": _make_stats_traces()},
        status=200,
    )
    result = jaeger_span_statistics(service="order-service")
    data = result.structuredContent
    assert data["service"] == "order-service"
    assert data["trace_count"] == 2
    assert len(data["stats"]) == 2
    # Sorted alphabetically: "GET /orders" first, "db.query" second
    assert data["stats"][0]["operation"] == "GET /orders"
    assert data["stats"][1]["operation"] == "db.query"
    # GET /orders: count=2, durations [1000, 2000]
    assert data["stats"][0]["count"] == 2


@responses.activate
def test_span_statistics_with_operation_filter() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": _make_stats_traces()},
        status=200,
    )
    result = jaeger_span_statistics(service="order-service", operation="GET /orders")
    data = result.structuredContent
    assert data["operation"] == "GET /orders"
    url = responses.calls[0].request.url
    assert "operation=" in url


@responses.activate
def test_span_statistics_empty_traces() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": []},
        status=200,
    )
    result = jaeger_span_statistics(service="order-service")
    data = result.structuredContent
    assert data["trace_count"] == 0
    assert data["stats"] == []


@responses.activate
def test_span_statistics_with_errors() -> None:
    traces = [
        {
            "traceID": "aaa111bbb222ccc333ddd444eee55566",
            "spans": [
                {
                    "spanID": "s1",
                    "operationName": "GET /orders",
                    "duration": 1000,
                    "processID": "p1",
                    "tags": [{"key": "error", "value": "true", "type": "bool"}],
                    "references": [],
                },
                {
                    "spanID": "s2",
                    "operationName": "GET /orders",
                    "duration": 2000,
                    "processID": "p1",
                    "tags": [],
                    "references": [],
                },
            ],
            "processes": {"p1": {"serviceName": "order-service"}},
        }
    ]
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": traces},
        status=200,
    )
    result = jaeger_span_statistics(service="order-service")
    data = result.structuredContent
    stats = data["stats"]
    assert len(stats) == 1
    assert stats[0]["error_count"] == 1
    assert isinstance(stats[0]["error_rate"], float)
    assert stats[0]["error_rate"] > 0.0


@responses.activate
def test_span_statistics_limit_forwarded() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": []},
        status=200,
    )
    jaeger_span_statistics(service="order-service", limit=50)
    url = responses.calls[0].request.url
    assert "limit=50" in url


@responses.activate
def test_span_statistics_default_limit() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={"data": []},
        status=200,
    )
    jaeger_span_statistics(service="order-service")
    url = responses.calls[0].request.url
    assert "limit=20" in url


@responses.activate
def test_span_statistics_401_raises_tool_error() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/traces",
        json={},
        status=401,
    )
    with pytest.raises(ToolError, match="401"):
        jaeger_span_statistics(service="order-service")
