"""Tests for jaeger_test_profile MCP tool + JaegerClient.test_profile facade.

The tool calls ``JaegerClient._atest_profile`` which searches Jaeger for traces
matching a tag query and aggregates per-operation latency hotspots via
``aggregate_span_statistics``. HTTP is mocked at the transport layer via respx,
consistent with test_qa_tools.py / test_regression_diff.py. One sync-facade test
exercises the ``asyncio.run`` wrapper via an AsyncMock HTTP client.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from jaeger_mcp import _mcp
from jaeger_mcp.facade import JaegerClient
from jaeger_mcp.qa_tools import jaeger_test_profile

BASE = "https://jaeger.example.com"

_PROFILE_FIELDS = {
    "operation",
    "total_wall_time_ms",
    "call_count",
    "mean_ms",
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "error_count",
    "error_rate",
}


# ── Trace builder ──────────────────────────────────────────────────────────


def _span(operation: str, duration_us: int, *, has_error: bool = False) -> dict:
    tags = [{"key": "error", "value": True, "type": "bool"}] if has_error else []
    return {
        "spanID": f"{operation}-{duration_us}",
        "operationName": operation,
        "processID": "p1",
        "startTime": 1_700_000_000_000_000,
        "duration": duration_us,
        "references": [],
        "tags": tags,
    }


def _make_profile_trace(trace_id: str, spans: list[dict], service: str = "frontend") -> dict:
    """Build a minimal Jaeger trace dict from pre-built span dicts."""
    return {
        "traceID": trace_id,
        "spans": spans,
        "processes": {"p1": {"serviceName": service}},
    }


def _ok(data: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def configured_env(monkeypatch: pytest.MonkeyPatch):
    """Set env vars + reset the module-global client cache per-test."""
    monkeypatch.setenv("JAEGER_URL", BASE)
    monkeypatch.delenv("JAEGER_TOKEN", raising=False)
    if _mcp._client is not None:
        try:
            asyncio.run(_mcp._client.aclose())
        except Exception:
            pass
    _mcp._client = None
    yield
    if _mcp._client is not None:
        try:
            asyncio.run(_mcp._client.aclose())
        except Exception:
            pass
    _mcp._client = None


# ── Tests ─────────────────────────────────────────────────────────────────


@respx.mock
async def test_profile_ranks_by_wall_time() -> None:
    """Operations are ranked by total wall time descending; all fields present."""
    trace = _make_profile_trace(
        "trace-1",
        [
            # "SLOW /a": two spans, 100 ms + 100 ms = 200 ms total wall time.
            _span("SLOW /a", 100_000),
            _span("SLOW /a", 100_000),
            # "FAST /b": one span, 10 ms total.
            _span("FAST /b", 10_000),
        ],
    )
    respx.get(f"{BASE}/api/traces").mock(return_value=_ok([trace]))

    result = await jaeger_test_profile(tags={"test.run_id": "r1"}, service="frontend")
    data = result.structuredContent

    assert data["trace_count"] == 1
    ops = data["operations"]
    assert len(ops) == 2
    # Ranked by total_wall_time_ms desc: SLOW /a (200 ms) before FAST /b (10 ms).
    assert ops[0]["operation"] == "SLOW /a"
    assert ops[0]["total_wall_time_ms"] == 200
    assert ops[0]["call_count"] == 2
    assert ops[0]["mean_ms"] == 100
    assert ops[1]["operation"] == "FAST /b"
    assert ops[1]["total_wall_time_ms"] == 10
    # Every ProfileOp field is present.
    for op in ops:
        assert _PROFILE_FIELDS.issubset(op.keys()), f"Missing: {_PROFILE_FIELDS - op.keys()}"


@respx.mock
async def test_profile_no_match() -> None:
    """Empty data returns trace_count=0 and no operations, without raising."""
    respx.get(f"{BASE}/api/traces").mock(return_value=_ok([]))

    result = await jaeger_test_profile(tags={"test.run_id": "none"}, service="frontend")
    data = result.structuredContent

    assert data["trace_count"] == 0
    assert data["operations"] == []


@respx.mock
async def test_profile_multi_service_dedup() -> None:
    """service=None: /api/services is queried; duplicate traceIDs are deduped."""
    services_route = respx.get(f"{BASE}/api/services").mock(
        return_value=_ok(["svc-a", "svc-b"]),
    )
    shared = _make_profile_trace("trace-shared", [_span("GET /x", 5_000)], service="svc-a")
    respx.get(f"{BASE}/api/traces").mock(return_value=_ok([shared]))

    result = await jaeger_test_profile(tags={"allure.id": "TC-1"})
    data = result.structuredContent

    assert services_route.called
    # Same traceID from both parallel service calls → 1 unique trace.
    assert data["trace_count"] == 1
    assert data["operations"][0]["operation"] == "GET /x"


@respx.mock
async def test_profile_error_rate() -> None:
    """Error spans are reflected in error_count / error_rate."""
    trace = _make_profile_trace(
        "trace-err",
        [
            _span("POST /pay", 20_000, has_error=True),
            _span("POST /pay", 20_000, has_error=False),
        ],
    )
    respx.get(f"{BASE}/api/traces").mock(return_value=_ok([trace]))

    result = await jaeger_test_profile(tags={"test.run_id": "r1"}, service="frontend")
    op = result.structuredContent["operations"][0]

    assert op["operation"] == "POST /pay"
    assert op["call_count"] == 2
    assert op["error_count"] == 1
    assert op["error_rate"] == 0.5


@respx.mock
async def test_profile_tag_and_service_echo() -> None:
    """tag_query echoes input unchanged; service_filter echoes service (incl. None)."""
    respx.get(f"{BASE}/api/traces").mock(return_value=_ok([]))
    respx.get(f"{BASE}/api/services").mock(return_value=_ok(["frontend"]))

    input_tags = {"allure.id": "TC-42", "custom.label": "smoke"}
    result = await jaeger_test_profile(tags=input_tags, service="frontend")
    assert result.structuredContent["tag_query"] == input_tags
    assert result.structuredContent["service_filter"] == "frontend"

    result_none = await jaeger_test_profile(tags=input_tags)
    assert result_none.structuredContent["service_filter"] is None


@respx.mock
async def test_profile_limit_caps_aggregated_traces() -> None:
    """limit caps how many traces are aggregated into the profile."""
    traces = [_make_profile_trace(f"t{i}", [_span("GET /a", 1_000)]) for i in range(5)]
    respx.get(f"{BASE}/api/traces").mock(return_value=_ok(traces))

    result = await jaeger_test_profile(tags={"test.run_id": "r1"}, service="frontend", limit=2)
    data = result.structuredContent

    assert data["trace_count"] == 2
    assert data["operations"][0]["call_count"] == 2


def test_profile_sync_facade() -> None:
    """JaegerClient.test_profile (sync) drives _atest_profile via asyncio.run."""
    trace = _make_profile_trace("trace-sync", [_span("GET /sync", 30_000)])
    mock_http = MagicMock()
    mock_http.aget = AsyncMock(return_value={"data": []})
    mock_http.aget_many = AsyncMock(return_value=[{"data": [trace]}])

    client = JaegerClient(mock_http)
    result = client.test_profile(tags={"test.run_id": "r1"}, service="frontend")

    assert result["trace_count"] == 1
    assert result["operations"][0]["operation"] == "GET /sync"
    assert result["operations"][0]["total_wall_time_ms"] == 30
    assert result["service_filter"] == "frontend"
