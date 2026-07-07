"""Tests for jaeger_find_test_traces MCP tool (mocked HTTP via respx).

We call the decorated tool function directly to exercise business logic
without spinning up a full MCP server. HTTP is mocked at the transport
layer via respx, consistent with test_tools_integration.py.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from jaeger_mcp import _mcp
from jaeger_mcp.qa_tools import jaeger_find_test_traces

BASE = "https://jaeger.example.com"

# ── Fake trace builders ────────────────────────────────────────────────────


def _make_qa_trace(
    trace_id: str,
    start_time_us: int = 1_700_000_000_000_000,
    duration_us: int = 50_000,
    service: str = "frontend",
    has_error: bool = False,
) -> dict:
    """Build a minimal Jaeger trace dict for qa_tools tests."""
    tags = [{"key": "error", "value": True}] if has_error else []
    return {
        "traceID": trace_id,
        "spans": [
            {
                "spanID": "s1",
                "operationName": "GET /api",
                "processID": "p1",
                "startTime": start_time_us,
                "duration": duration_us,
                "references": [],
                "tags": tags,
            }
        ],
        "processes": {"p1": {"serviceName": service}},
    }


# Two traces 60 s apart — verify newest-first sort.
# start_time_us differs by 60_000_000 (60 seconds in microseconds).
TRACE_OLD = _make_qa_trace("trace-old", start_time_us=1_700_000_000_000_000)
TRACE_NEW = _make_qa_trace("trace-new", start_time_us=1_700_000_060_000_000)


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
async def test_find_test_traces_single_service() -> None:
    """Two traces returned; sorted newest-first; all 6 fields present."""
    respx.get(f"{BASE}/api/traces").mock(
        return_value=httpx.Response(200, json={"data": [TRACE_OLD, TRACE_NEW]}),
    )
    result = await jaeger_find_test_traces(
        tags={"allure.id": "TC-42"},
        service="frontend",
    )
    data = result.structuredContent
    assert data["total_count"] == 2
    traces = data["traces"]
    assert len(traces) == 2
    # Newest-first sort
    assert traces[0]["trace_id"] == "trace-new"
    assert traces[1]["trace_id"] == "trace-old"
    # All 6 TestTraceMatch fields present in every trace
    required_fields = {"trace_id", "root_service", "duration_ms", "start_time", "has_error", "span_count"}
    for trace in traces:
        assert required_fields.issubset(trace.keys()), f"Missing fields: {required_fields - trace.keys()}"


@respx.mock
async def test_find_test_traces_no_match() -> None:
    """Empty data list returns total_count=0 without raising any exception."""
    respx.get(f"{BASE}/api/traces").mock(
        return_value=httpx.Response(200, json={"data": []}),
    )
    result = await jaeger_find_test_traces(
        tags={"allure.id": "TC-99"},
        service="frontend",
    )
    data = result.structuredContent
    assert data["traces"] == []
    assert data["total_count"] == 0


@respx.mock
async def test_find_test_traces_multi_service() -> None:
    """service=None: /api/services called; per-service /api/traces deduplicated."""
    services_route = respx.get(f"{BASE}/api/services").mock(
        return_value=httpx.Response(200, json={"data": ["svc-a", "svc-b"]}),
    )
    # Both concurrent calls return the same traceID → deduplication yields 1 result.
    respx.get(f"{BASE}/api/traces").mock(
        return_value=httpx.Response(
            200,
            json={"data": [_make_qa_trace("trace-shared", service="svc-a")]},
        ),
    )
    result = await jaeger_find_test_traces(tags={"test.run_id": "abc123"})
    data = result.structuredContent
    # /api/services must have been called to discover the service list
    assert services_route.called
    # Deduplication: same traceID from two parallel calls → 1 unique trace
    assert data["total_count"] == 1
    assert data["traces"][0]["trace_id"] == "trace-shared"


@respx.mock
async def test_find_test_traces_limit() -> None:
    """limit=2 caps the returned list even when Jaeger returns 5 traces."""
    traces = [_make_qa_trace(f"t{i}", start_time_us=1_700_000_000_000_000 + i * 1_000_000) for i in range(5)]
    respx.get(f"{BASE}/api/traces").mock(
        return_value=httpx.Response(200, json={"data": traces}),
    )
    result = await jaeger_find_test_traces(
        tags={"allure.id": "TC-42"},
        service="frontend",
        limit=2,
    )
    data = result.structuredContent
    assert len(data["traces"]) == 2
    assert data["total_count"] == 5  # pre-truncation count; 5 matched, 2 returned


@respx.mock
async def test_find_test_traces_tag_echo() -> None:
    """tag_query in output equals the input tags dict unchanged."""
    respx.get(f"{BASE}/api/traces").mock(
        return_value=httpx.Response(200, json={"data": []}),
    )
    input_tags = {"allure.id": "TC-42", "custom.label": "smoke"}
    result = await jaeger_find_test_traces(tags=input_tags, service="frontend")
    assert result.structuredContent["tag_query"] == input_tags


@respx.mock
async def test_find_test_traces_service_filter() -> None:
    """service_filter in output echoes the input service param (both non-None and None)."""
    respx.get(f"{BASE}/api/traces").mock(
        return_value=httpx.Response(200, json={"data": []}),
    )
    respx.get(f"{BASE}/api/services").mock(
        return_value=httpx.Response(200, json={"data": ["frontend"]}),
    )

    # Non-None service: service_filter echoes "frontend"
    result = await jaeger_find_test_traces(tags={"allure.id": "TC-1"}, service="frontend")
    assert result.structuredContent["service_filter"] == "frontend"

    # None service: service_filter must be None
    result_none = await jaeger_find_test_traces(tags={"allure.id": "TC-1"})
    assert result_none.structuredContent["service_filter"] is None
