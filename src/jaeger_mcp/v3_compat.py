"""Compatibility layer for Jaeger builds that no longer serve the v1 query API.

Jaeger v2.22+ answers ``/api/v3/*`` (OTLP JSON) but returns 404 for the classic
``/api/services``, ``/api/services/{svc}/operations`` and ``/api/traces`` search
endpoints. The client tries v1 first and, on 404, falls back to the helpers here:

* :func:`translate_request` maps a v1 endpoint + params to a v3 endpoint + params.
* :func:`translate_response` converts the v3 payload back to the v1 JSON shape
  (``{"data": [...]}``), so every tool downstream keeps working unchanged.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

_DEFAULT_SEARCH_WINDOW_US = 3600 * 1_000_000  # Jaeger UI default: last hour
_DURATION_RE = re.compile(r"^\s*\d+(\.\d+)?(ns|us|µs|ms|s|m|h)\s*$")

_KIND = {1: "internal", 2: "server", 3: "client", 4: "producer", 5: "consumer"}


def _rfc3339(us: int) -> str:
    return datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def is_fallback_endpoint(endpoint: str) -> bool:
    """True for the v1 endpoints that have a v3 equivalent."""
    return endpoint == "/services" or endpoint == "/traces" or endpoint.startswith(("/services/", "/traces/"))


def translate_request(endpoint: str, params: dict[str, Any] | None) -> tuple[str, dict[str, Any]] | None:
    """Return ``(v3_endpoint, v3_params)`` for a v1 request, or ``None`` if not translatable."""
    params = dict(params or {})
    if endpoint == "/services":
        return "/v3/services", {}
    if endpoint.startswith("/services/") and endpoint.endswith("/operations"):
        svc = endpoint[len("/services/") : -len("/operations")]
        return "/v3/operations", {"service": unquote(svc)}
    if endpoint.startswith("/traces/"):
        return f"/v3/traces/{endpoint[len('/traces/') :]}", {}
    if endpoint == "/traces":
        now_us = int(time.time() * 1_000_000)
        end_us = int(params.get("end") or now_us)
        start_us = int(params.get("start") or (end_us - _DEFAULT_SEARCH_WINDOW_US))
        q: dict[str, Any] = {
            "query.service_name": params.get("service", ""),
            "query.start_time_min": _rfc3339(start_us),
            "query.start_time_max": _rfc3339(end_us),
            "query.num_traces": params.get("limit", 20),
        }
        if params.get("operation"):
            q["query.operation_name"] = params["operation"]
        if params.get("tags"):
            q["query.attributes"] = params["tags"] if isinstance(params["tags"], str) else json.dumps(params["tags"])
        for v1, v3 in (("minDuration", "query.duration_min"), ("maxDuration", "query.duration_max")):
            val = params.get(v1)
            if val and _DURATION_RE.match(str(val)):
                q[v3] = str(val).strip()
        return "/v3/traces", q
    return None


def _any_value(v: dict[str, Any]) -> tuple[str, Any]:
    if "stringValue" in v:
        return "string", v["stringValue"]
    if "boolValue" in v:
        return "bool", bool(v["boolValue"])
    if "intValue" in v:
        return "int64", int(v["intValue"])
    if "doubleValue" in v:
        return "float64", float(v["doubleValue"])
    return "string", json.dumps(v, sort_keys=True)


def _tags(attrs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for a in attrs or []:
        typ, val = _any_value(a.get("value") or {})
        out.append({"key": a.get("key", ""), "type": typ, "value": val})
    return out


def _iter_json_objects(text: str) -> list[Any]:
    """Parse one JSON document or several concatenated/newline-delimited ones."""
    dec = json.JSONDecoder()
    objs: list[Any] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        obj, i = dec.raw_decode(text, i)
        objs.append(obj)
    return objs


def otlp_to_v1_traces(payloads: list[Any]) -> list[dict[str, Any]]:
    """Convert v3 ``{"result": {"resourceSpans": [...]}}`` chunks to v1 trace dicts."""
    traces: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        result = payload.get("result", payload) if isinstance(payload, dict) else {}
        for rs in result.get("resourceSpans") or []:
            res_attrs = _tags((rs.get("resource") or {}).get("attributes"))
            service = next((t["value"] for t in res_attrs if t["key"] == "service.name"), "unknown")
            process_tags = [t for t in res_attrs if t["key"] != "service.name"]
            for ss in rs.get("scopeSpans") or []:
                for sp in ss.get("spans") or []:
                    tid = sp.get("traceId", "")
                    tr = traces.setdefault(tid, {"traceID": tid, "spans": [], "processes": {}, "_pids": {}})
                    pkey = (service, json.dumps(process_tags, sort_keys=True))
                    pid = tr["_pids"].get(pkey)
                    if pid is None:
                        pid = f"p{len(tr['_pids']) + 1}"
                        tr["_pids"][pkey] = pid
                        tr["processes"][pid] = {"serviceName": service, "tags": process_tags}
                    start_ns = int(sp.get("startTimeUnixNano") or 0)
                    end_ns = int(sp.get("endTimeUnixNano") or start_ns)
                    tags = _tags(sp.get("attributes"))
                    keys = {t["key"] for t in tags}
                    code = (sp.get("status") or {}).get("code")
                    if code in (2, "STATUS_CODE_ERROR") and "error" not in keys:
                        tags.append({"key": "error", "type": "bool", "value": True})
                    kind = _KIND.get(sp.get("kind")) if isinstance(sp.get("kind"), int) else None
                    if kind and "span.kind" not in keys:
                        tags.append({"key": "span.kind", "type": "string", "value": kind})
                    refs = []
                    if sp.get("parentSpanId"):
                        refs.append({"refType": "CHILD_OF", "traceID": tid, "spanID": sp["parentSpanId"]})
                    tr["spans"].append(
                        {
                            "traceID": tid,
                            "spanID": sp.get("spanId", ""),
                            "operationName": sp.get("name", ""),
                            "references": refs,
                            "startTime": start_ns // 1000,
                            "duration": max(end_ns - start_ns, 0) // 1000,
                            "tags": tags,
                            "logs": [],
                            "processID": pid,
                            "warnings": None,
                        }
                    )
    out = []
    for tr in traces.values():
        tr.pop("_pids", None)
        out.append(tr)
    return out


def translate_response(endpoint: str, text: str) -> Any:
    """Convert the raw v3 response body for ``endpoint`` (a v1 path) to the v1 JSON shape."""
    objs = _iter_json_objects(text) if text.strip() else []
    if endpoint == "/services":
        names: list[str] = []
        for o in objs:
            names.extend(o.get("services") or [])
        return {"data": names, "total": len(names), "limit": 0, "offset": 0, "errors": None}
    if endpoint.endswith("/operations") and endpoint.startswith("/services/"):
        names = []
        for o in objs:
            names.extend(op["name"] for op in o.get("operations") or [] if op.get("name"))
        names = list(dict.fromkeys(names))
        return {"data": names, "total": len(names), "limit": 0, "offset": 0, "errors": None}
    traces = otlp_to_v1_traces([o for o in objs if "error" not in o])
    return {"data": traces, "total": len(traces), "limit": 0, "offset": 0, "errors": None}
