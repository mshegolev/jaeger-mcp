"""Tests for jaeger_regression_diff MCP tool (mocked HTTP via respx).

Calls the decorated tool function directly to exercise business logic
without spinning up a full MCP server. HTTP is mocked at the transport
layer via respx, consistent with test_qa_tools.py.

The tool calls JaegerClient._aregression_diff which calls _acompare_windows
which calls client.aget_many with two GET /api/traces requests (baseline
then comparison). We use respx.mock and side_effect lists to differentiate
baseline vs comparison responses where needed.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from jaeger_mcp import _mcp
from jaeger_mcp.qa_tools import jaeger_regression_diff

BASE = "https://jaeger.example.com"

# ── Trace builder ──────────────────────────────────────────────────────────


def _make_rd_trace(
    trace_id: str,
    operation: str = "GET /api",
    duration_us: int = 10_000,
    has_error: bool = False,
    service: str = "svc",
    start_time_us: int = 1_700_000_000_000_000,
) -> dict:
    """Build a minimal Jaeger trace dict for regression_diff tests."""
    tags = [{"key": "error", "value": True, "type": "bool"}] if has_error else []
    return {
        "traceID": trace_id,
        "spans": [
            {
                "spanID": "s1",
                "operationName": operation,
                "processID": "p1",
                "startTime": start_time_us,
                "duration": duration_us,
                "references": [],
                "tags": tags,
            }
        ],
        "processes": {"p1": {"serviceName": service}},
    }


# Pre-built trace pairs for latency regression tests.
# 10 ms fast baseline vs 200 ms slow comparison (20x slower — well above 5% threshold).
TRACE_FAST = _make_rd_trace("trace-fast", operation="GET /items", duration_us=10_000)
TRACE_SLOW = _make_rd_trace("trace-slow", operation="GET /items", duration_us=200_000)

# Same operation, same latency, but comparison has errors.
TRACE_CLEAN = _make_rd_trace("trace-clean", operation="GET /order", duration_us=20_000, has_error=False)
TRACE_ERR = _make_rd_trace("trace-err", operation="GET /order", duration_us=20_000, has_error=True)

# Slow+error baseline for recovery test.
TRACE_SLOW_ERR = _make_rd_trace("trace-slow-err", operation="GET /user", duration_us=300_000, has_error=True)
TRACE_FAST_CLEAN = _make_rd_trace("trace-fast-clean", operation="GET /user", duration_us=5_000, has_error=False)

# For severity sort: two different operations with different p95 deltas.
TRACE_OP_A_FAST = _make_rd_trace("op-a-fast", operation="POST /checkout", duration_us=10_000)
TRACE_OP_A_SLOW = _make_rd_trace("op-a-slow", operation="POST /checkout", duration_us=500_000)
TRACE_OP_B_FAST = _make_rd_trace("op-b-fast", operation="GET /products", duration_us=10_000)
TRACE_OP_B_SLOW = _make_rd_trace("op-b-slow", operation="GET /products", duration_us=50_000)


def _ok(data: list[dict]) -> httpx.Response:
    """Return a 200 JSON response with data array."""
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


# ── Helpers ────────────────────────────────────────────────────────────────


def _baseline_us() -> int:
    """Fixed baseline window: 1 hour ago → 30 min ago."""
    return 1_700_000_000_000_000


def _baseline_start() -> int:
    return _baseline_us()


def _baseline_end() -> int:
    return _baseline_us() + 30 * 60 * 1_000_000


def _comp_start() -> int:
    return _baseline_us() + 45 * 60 * 1_000_000


def _comp_end() -> int:
    return _baseline_us() + 75 * 60 * 1_000_000


# ── Tests ─────────────────────────────────────────────────────────────────


@respx.mock
async def test_all_clear() -> None:
    """Both windows contain identical traces → no regressed operations."""
    # Same trace in both windows → same stats → change_type "unchanged" → skipped.
    respx.get(f"{BASE}/api/traces").mock(side_effect=[_ok([TRACE_FAST]), _ok([TRACE_FAST])])

    result = await jaeger_regression_diff(
        service="svc",
        baseline_start=_baseline_start(),
        baseline_end=_baseline_end(),
        comparison_start=_comp_start(),
        comparison_end=_comp_end(),
    )
    data = result.structuredContent
    assert data["regressed_count"] == 0
    assert data["recovered_count"] == 0
    # Operations with "unchanged" change_type are excluded from the output list.
    regressed_ops = [op for op in data["operations"] if op["classification"] == "regressed"]
    assert len(regressed_ops) == 0


@respx.mock
async def test_latency_regression() -> None:
    """Baseline fast, comparison 20x slower → classification=regressed, severity>0."""
    respx.get(f"{BASE}/api/traces").mock(side_effect=[_ok([TRACE_FAST]), _ok([TRACE_SLOW])])

    result = await jaeger_regression_diff(
        service="svc",
        baseline_start=_baseline_start(),
        baseline_end=_baseline_end(),
        comparison_start=_comp_start(),
        comparison_end=_comp_end(),
    )
    data = result.structuredContent
    assert data["regressed_count"] >= 1
    ops = data["operations"]
    assert len(ops) >= 1
    op = ops[0]
    assert op["classification"] == "regressed"
    assert op["severity_score"] > 0
    assert op["comparison_p95_ms"] > op["baseline_p95_ms"]


@respx.mock
async def test_error_rate_override() -> None:
    """Same latency in both windows but comparison has 100% error rate → regressed."""
    # TRACE_CLEAN and TRACE_ERR have the same operation and same duration (20 ms)
    # but comparison has all spans erroring → error_rate_delta > 0.05 → regressed.
    respx.get(f"{BASE}/api/traces").mock(side_effect=[_ok([TRACE_CLEAN]), _ok([TRACE_ERR])])

    result = await jaeger_regression_diff(
        service="svc",
        baseline_start=_baseline_start(),
        baseline_end=_baseline_end(),
        comparison_start=_comp_start(),
        comparison_end=_comp_end(),
    )
    data = result.structuredContent
    assert data["regressed_count"] >= 1
    op = data["operations"][0]
    assert op["classification"] == "regressed"
    assert op["comparison_error_rate"] > op["baseline_error_rate"]


@respx.mock
async def test_recovery() -> None:
    """Baseline is slow+erroring, comparison is fast+clean → classification=recovered."""
    # 300 ms → 5 ms (17x faster), error rate 1.0 → 0.0.
    respx.get(f"{BASE}/api/traces").mock(side_effect=[_ok([TRACE_SLOW_ERR]), _ok([TRACE_FAST_CLEAN])])

    result = await jaeger_regression_diff(
        service="svc",
        baseline_start=_baseline_start(),
        baseline_end=_baseline_end(),
        comparison_start=_comp_start(),
        comparison_end=_comp_end(),
    )
    data = result.structuredContent
    assert data["recovered_count"] >= 1
    recovered_ops = [op for op in data["operations"] if op["classification"] == "recovered"]
    assert len(recovered_ops) >= 1
    op = recovered_ops[0]
    assert op["severity_score"] == 0


@respx.mock
async def test_appeared() -> None:
    """Operation only in comparison window → classification=appeared, severity=0."""
    # Baseline empty, comparison has traces.
    respx.get(f"{BASE}/api/traces").mock(side_effect=[_ok([]), _ok([TRACE_FAST])])

    result = await jaeger_regression_diff(
        service="svc",
        baseline_start=_baseline_start(),
        baseline_end=_baseline_end(),
        comparison_start=_comp_start(),
        comparison_end=_comp_end(),
    )
    data = result.structuredContent
    assert data["appeared_count"] >= 1
    appeared_ops = [op for op in data["operations"] if op["classification"] == "appeared"]
    assert len(appeared_ops) >= 1
    assert appeared_ops[0]["severity_score"] == 0


@respx.mock
async def test_removed() -> None:
    """Operation only in baseline window → classification=removed, severity=0."""
    # Baseline has traces, comparison empty.
    respx.get(f"{BASE}/api/traces").mock(side_effect=[_ok([TRACE_FAST]), _ok([])])

    result = await jaeger_regression_diff(
        service="svc",
        baseline_start=_baseline_start(),
        baseline_end=_baseline_end(),
        comparison_start=_comp_start(),
        comparison_end=_comp_end(),
    )
    data = result.structuredContent
    assert data["removed_count"] >= 1
    removed_ops = [op for op in data["operations"] if op["classification"] == "removed"]
    assert len(removed_ops) >= 1
    assert removed_ops[0]["severity_score"] == 0


@respx.mock
async def test_default_window_resolution() -> None:
    """comparison_start/end=None → resolved to now-15min/now; tool does not raise."""
    # Both windows return empty data — we just verify no exception and zero counts.
    respx.get(f"{BASE}/api/traces").mock(side_effect=[_ok([]), _ok([])])

    result = await jaeger_regression_diff(
        service="svc",
        baseline_start=_baseline_start(),
        baseline_end=_baseline_end(),
        # comparison_start and comparison_end intentionally omitted (defaults to None).
    )
    data = result.structuredContent
    assert data["regressed_count"] == 0
    assert data["recovered_count"] == 0
    assert data["appeared_count"] == 0
    assert data["removed_count"] == 0
    # comparison_start/end must have been resolved (not None) and passed to facade.
    assert data["comparison_start"] > 0
    assert data["comparison_end"] > 0


@respx.mock
async def test_severity_sorted_descending() -> None:
    """Two regressed operations → sorted by severity_score descending."""
    # POST /checkout: 10 ms → 500 ms (50x slower, very high severity).
    # GET /products: 10 ms → 50 ms (5x slower, lower severity).
    baseline = [TRACE_OP_A_FAST, TRACE_OP_B_FAST]
    comparison = [TRACE_OP_A_SLOW, TRACE_OP_B_SLOW]
    respx.get(f"{BASE}/api/traces").mock(side_effect=[_ok(baseline), _ok(comparison)])

    result = await jaeger_regression_diff(
        service="svc",
        baseline_start=_baseline_start(),
        baseline_end=_baseline_end(),
        comparison_start=_comp_start(),
        comparison_end=_comp_end(),
    )
    data = result.structuredContent
    ops = data["operations"]
    assert len(ops) >= 2
    regressed = [op for op in ops if op["classification"] == "regressed"]
    assert len(regressed) >= 2
    # Verify severity is descending.
    scores = [op["severity_score"] for op in ops]
    assert scores == sorted(scores, reverse=True), f"ops not sorted by severity: {scores}"
    # The more severely regressed op should be first.
    assert ops[0]["severity_score"] >= ops[1]["severity_score"]
