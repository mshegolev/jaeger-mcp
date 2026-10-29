"""Jaeger v2.22+ compatibility (/api/v3 fallback), window units, env validation, critical path."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

from jaeger_mcp import _mcp, v3_compat
from jaeger_mcp.client import JaegerHTTPClient
from jaeger_mcp.errors import ConfigError
from jaeger_mcp.tools import jaeger_critical_path, jaeger_list_operations, jaeger_list_services, jaeger_search_traces

BASE = "https://jaeger.example.com"
TID = "ab" * 16

OTLP = {
    "result": {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "acme.checkout"}}]},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": TID,
                                "spanId": "0000000000000001",
                                "name": "POST /checkout",
                                "kind": 2,
                                "startTimeUnixNano": "1700000000000000000",
                                "endTimeUnixNano": "1700000000200000000",
                                "attributes": [{"key": "http.status_code", "value": {"intValue": "500"}}],
                                "status": {"code": 2},
                            }
                        ]
                    }
                ],
            },
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "acme.billing"}}]},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": TID,
                                "spanId": "0000000000000002",
                                "parentSpanId": "0000000000000001",
                                "name": "charge",
                                "startTimeUnixNano": "1700000000050000000",
                                "endTimeUnixNano": "1700000000150000000",
                            }
                        ]
                    }
                ],
            },
        ]
    }
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JAEGER_URL", BASE)
    monkeypatch.delenv("JAEGER_TOKEN", raising=False)
    _mcp._client = None
    yield
    _mcp._client = None


class TestTranslate:
    def test_services(self) -> None:
        assert v3_compat.translate_request("/services", None) == ("/v3/services", {})

    def test_operations_unquotes_service(self) -> None:
        ep, params = v3_compat.translate_request("/services/a%2Fb/operations", None)  # type: ignore[misc]
        assert ep == "/v3/operations" and params == {"service": "a/b"}

    def test_search_params(self) -> None:
        ep, q = v3_compat.translate_request(  # type: ignore[misc]
            "/traces",
            {
                "service": "s",
                "operation": "op",
                "tags": '{"a":"b"}',
                "start": 1_700_000_000_000_000,
                "end": 1_700_000_060_000_000,
                "minDuration": "100ms",
                "maxDuration": "bogus",
                "limit": 5,
            },
        )
        assert ep == "/v3/traces"
        assert q["query.service_name"] == "s"
        assert q["query.start_time_min"] == "2023-11-14T22:13:20.000000Z"
        assert q["query.start_time_max"] == "2023-11-14T22:14:20.000000Z"
        assert q["query.duration_min"] == "100ms"
        assert "query.duration_max" not in q  # malformed durations are dropped, not forwarded
        assert q["query.num_traces"] == 5 and q["query.attributes"] == '{"a":"b"}'

    def test_response_to_v1_shape(self) -> None:
        out = v3_compat.translate_response("/traces", json.dumps(OTLP))
        (trace,) = out["data"]
        assert trace["traceID"] == TID and len(trace["spans"]) == 2
        root, child = trace["spans"]
        assert root["duration"] == 200_000 and root["startTime"] == 1_700_000_000_000_000
        assert child["references"] == [{"refType": "CHILD_OF", "traceID": TID, "spanID": "0000000000000001"}]
        assert {"key": "error", "type": "bool", "value": True} in root["tags"]
        assert {"key": "http.status_code", "type": "int64", "value": 500} in root["tags"]
        assert trace["processes"][root["processID"]]["serviceName"] == "acme.checkout"
        assert trace["processes"][child["processID"]]["serviceName"] == "acme.billing"

    def test_concatenated_chunks(self) -> None:
        body = json.dumps(OTLP) + "\n" + json.dumps(OTLP)
        assert len(v3_compat.translate_response("/traces", body)["data"]) == 1  # same trace id merged


@respx.mock
async def test_services_fall_back_to_v3_on_404() -> None:
    v1 = respx.get(f"{BASE}/api/services").mock(return_value=httpx.Response(404, text="404 page not found"))
    v3 = respx.get(f"{BASE}/api/v3/services").mock(return_value=httpx.Response(200, json={"services": ["a", "b"]}))
    res = await jaeger_list_services()
    assert "`a`" in str(res) and "`b`" in str(res)
    assert v1.call_count == 1 and v3.call_count == 1


@respx.mock
async def test_prefer_v3_after_first_fallback_skips_v1() -> None:
    v1 = respx.get(f"{BASE}/api/traces").mock(return_value=httpx.Response(404, text="404 page not found"))
    v3 = respx.get(f"{BASE}/api/v3/traces").mock(return_value=httpx.Response(200, json=OTLP))
    await jaeger_search_traces(service="acme.checkout")
    await jaeger_search_traces(service="acme.checkout")
    assert v1.call_count == 1 and v3.call_count == 2


@respx.mock
async def test_search_no_traces_found_is_empty_not_error() -> None:
    respx.get(f"{BASE}/api/traces").mock(return_value=httpx.Response(404, text="404 page not found"))
    respx.get(f"{BASE}/api/v3/traces").mock(
        return_value=httpx.Response(404, json={"error": {"httpCode": 404, "message": "No traces found"}})
    )
    res = await jaeger_search_traces(service="nope")
    assert "0 returned" in str(res)


@respx.mock
async def test_trace_by_id_falls_back_via_stream() -> None:
    respx.get(f"{BASE}/api/traces/{TID}").mock(return_value=httpx.Response(404, text="404 page not found"))
    respx.get(f"{BASE}/api/v3/traces/{TID}").mock(return_value=httpx.Response(200, json=OTLP))
    res = await jaeger_critical_path(trace_id=TID)
    assert "Critical Path" in str(res)


@respx.mock
async def test_genuinely_missing_trace_still_404() -> None:
    respx.get(f"{BASE}/api/traces/{TID}").mock(return_value=httpx.Response(404, json={"errors": []}))
    respx.get(f"{BASE}/api/v3/traces/{TID}").mock(return_value=httpx.Response(404, json={"error": {}}))
    with pytest.raises(ToolError, match="not found"):
        await jaeger_critical_path(trace_id=TID)


@respx.mock
async def test_list_operations_quotes_service_and_returns_error_result() -> None:
    route = respx.get(f"{BASE}/api/services/a%2Fb/operations").mock(
        return_value=httpx.Response(200, json={"data": ["x"]})
    )
    res = await jaeger_list_operations(service="a/b")
    assert route.called and "`x`" in str(res)

    respx.get(f"{BASE}/api/services/boom/operations").mock(return_value=httpx.Response(401))
    with pytest.raises(ToolError):
        await jaeger_list_operations(service="boom")


@respx.mock
async def test_critical_path_never_exceeds_100_percent() -> None:
    respx.get(f"{BASE}/api/traces/{TID}").mock(return_value=httpx.Response(200, json=_v1_nested_trace()))
    res = await jaeger_critical_path(trace_id=TID)
    text = str(res)
    assert "Critical Path Percentage:** 100.0%" in text


def _v1_nested_trace() -> dict:
    spans = [
        ("1", None, 0, 200_000),
        ("2", "1", 50_000, 100_000),
        ("3", "2", 60_000, 20_000),
    ]
    return {
        "data": [
            {
                "traceID": TID,
                "processes": {"p1": {"serviceName": "svc", "tags": []}},
                "spans": [
                    {
                        "traceID": TID,
                        "spanID": sid,
                        "operationName": f"op{sid}",
                        "references": [{"refType": "CHILD_OF", "traceID": TID, "spanID": parent}] if parent else [],
                        "startTime": 1_700_000_000_000_000 + off,
                        "duration": dur,
                        "tags": [],
                        "processID": "p1",
                    }
                    for sid, parent, off, dur in spans
                ],
            }
        ]
    }


@respx.mock
async def test_window_params_are_microseconds() -> None:
    from jaeger_mcp.tools import jaeger_detect_anomalies

    route = respx.get(f"{BASE}/api/traces").mock(return_value=httpx.Response(200, json={"data": []}))
    await jaeger_detect_anomalies(service="svc")
    for call in route.calls:
        start = int(call.request.url.params["start"])
        assert start > 1_000_000_000_000_000, "start must be microseconds since epoch, not milliseconds"


class TestEnvValidation:
    @pytest.mark.parametrize("name", ["JAEGER_TIMEOUT", "JAEGER_RETRY_ATTEMPTS", "JAEGER_CACHE_TTL"])
    def test_garbage_raises_config_error(self, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
        monkeypatch.setenv(name, "abc")
        with pytest.raises(ConfigError, match=name):
            JaegerHTTPClient()

    def test_negative_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_TIMEOUT", "-1")
        with pytest.raises(ConfigError):
            JaegerHTTPClient()

    def test_concurrency_never_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_CONCURRENCY_LIMIT", "0")
        assert JaegerHTTPClient()._concurrency_limit == 1
        asyncio.run(asyncio.sleep(0))
