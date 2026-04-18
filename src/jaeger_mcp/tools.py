"""MCP tools for Jaeger distributed tracing.

5 read-only tools covering the Jaeger HTTP Query API surface most useful to
an agent diagnosing latency, errors, or service topology:

- ``jaeger_list_services``     — discover which services Jaeger has seen
- ``jaeger_list_operations``   — list operations for a service
- ``jaeger_search_traces``     — search traces with rich filters
- ``jaeger_get_trace``         — retrieve full trace with span tree
- ``jaeger_get_dependencies``  — service-to-service call graph

**Threading model.** All tools are synchronous ``def``. FastMCP runs them
in a worker thread via ``anyio.to_thread.run_sync``, so blocking HTTP
calls don't block the asyncio event loop.
"""

from __future__ import annotations

import time
from typing import Annotated, Any

from pydantic import Field

from jaeger_mcp import output
from jaeger_mcp._mcp import get_client, mcp
from jaeger_mcp.models import (
    DependenciesOutput,
    DependencyEdge,
    ExecutionNode,
    OperationsOutput,
    SearchTracesOutput,
    ServicesOutput,
    ServiceSpanStats,
    SpanDetail,
    TraceDetailOutput,
    TraceSummary,
)

_LIST_CAP = 500
_MD_ITEM_LIMIT = 20


# ── Helpers ────────────────────────────────────────────────────────────────


def _truncation_hint(total: int, shown: int, noun: str) -> str:
    """Return a markdown truncation hint when items are capped."""
    return f"\n\n_Showing first {shown} of {total} {noun} — see the structured content for the full list._"


def _span_is_error(span: dict[str, Any]) -> bool:
    """Return True if a Jaeger span has error=true in its tags."""
    for tag in span.get("tags") or []:
        if tag.get("key") == "error" and str(tag.get("value", "")).lower() in ("true", "1"):
            return True
    return False


def _span_tags_flat(span: dict[str, Any]) -> dict[str, str]:
    """Convert Jaeger tag list to a flat {key: str(value)} dict."""
    result: dict[str, str] = {}
    for tag in span.get("tags") or []:
        k = tag.get("key")
        v = tag.get("value")
        if k:
            result[k] = str(v) if v is not None else ""
    return result


def _find_root_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the span with no parent (root span)."""
    all_ids = {s.get("spanID") for s in spans}
    for span in spans:
        refs = span.get("references") or []
        parent_ids = {r.get("spanID") for r in refs if r.get("refType") == "CHILD_OF"}
        if not parent_ids or not parent_ids.intersection(all_ids):
            return span
    # Fallback: earliest start
    if spans:
        return min(spans, key=lambda s: s.get("startTime", 0))
    return None


def _build_execution_tree(spans: list[dict[str, Any]], processes: dict[str, Any]) -> list[ExecutionNode]:
    """Build a flat execution tree list (each node knows its children)."""
    # Map spanID → span
    span_map: dict[str, dict[str, Any]] = {s["spanID"]: s for s in spans if s.get("spanID")}
    # Map spanID → [child spanIDs]
    children_map: dict[str, list[str]] = {sid: [] for sid in span_map}
    for span in spans:
        refs = span.get("references") or []
        for ref in refs:
            if ref.get("refType") == "CHILD_OF":
                parent_id = ref.get("spanID")
                if parent_id and parent_id in children_map:
                    children_map[parent_id].append(span["spanID"])

    nodes: list[ExecutionNode] = []
    for span_id, span in span_map.items():
        pid = span.get("processID", "")
        proc = processes.get(pid) or {}
        service = proc.get("serviceName", pid)
        node: ExecutionNode = {
            "span_id": span_id,
            "operation": span.get("operationName", ""),
            "service": service,
            "duration_us": span.get("duration", 0),
            "is_error": _span_is_error(span),
            "children": children_map.get(span_id, []),
        }
        nodes.append(node)
    return nodes


def _shape_trace_summary(trace: dict[str, Any]) -> TraceSummary:
    """Convert a Jaeger trace dict into a :class:`TraceSummary`."""
    spans: list[dict[str, Any]] = trace.get("spans") or []
    processes: dict[str, Any] = trace.get("processes") or {}

    root = _find_root_span(spans)
    root_op: str | None = root.get("operationName") if root else None
    root_service: str | None = None
    if root:
        pid = root.get("processID", "")
        root_service = (processes.get(pid) or {}).get("serviceName")

    start_times = [s.get("startTime", 0) for s in spans if s.get("startTime")]
    start_time_us: int | None = min(start_times) if start_times else None

    # Duration = end of last span minus start of first
    end_times = [(s.get("startTime", 0) + s.get("duration", 0)) for s in spans]
    duration_us = (max(end_times) - min(start_times)) if start_times and end_times else 0

    service_ids = set(s.get("processID") for s in spans if s.get("processID"))
    errors_count = sum(1 for s in spans if _span_is_error(s))

    return {
        "trace_id": trace.get("traceID", ""),
        "root_operation": root_op,
        "root_service": root_service,
        "start_time_us": start_time_us,
        "duration_us": max(duration_us, 0),
        "span_count": len(spans),
        "service_count": len(service_ids),
        "errors_count": errors_count,
    }


def _shape_span_detail(span: dict[str, Any], processes: dict[str, Any]) -> SpanDetail:
    """Convert a Jaeger span to :class:`SpanDetail`."""
    pid = span.get("processID", "")
    proc = processes.get(pid) or {}
    refs = span.get("references") or []
    parent_id: str | None = None
    for ref in refs:
        if ref.get("refType") == "CHILD_OF":
            parent_id = ref.get("spanID")
            break

    return {
        "span_id": span.get("spanID", ""),
        "operation_name": span.get("operationName", ""),
        "service": proc.get("serviceName", pid),
        "start_time_us": span.get("startTime", 0),
        "duration_us": span.get("duration", 0),
        "is_error": _span_is_error(span),
        "parent_span_id": parent_id,
        "tags": _span_tags_flat(span),
    }


# ── Tools ──────────────────────────────────────────────────────────────────


@mcp.tool(
    name="jaeger_list_services",
    annotations={
        "title": "List Services",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def jaeger_list_services() -> ServicesOutput:
    """List all services that Jaeger has observed traces for.

    Wraps ``GET /api/services``. Jaeger returns all services at once — no
    pagination. Output is capped at 500 services with a truncation hint.

    Use this first to discover valid service names before calling
    ``jaeger_list_operations`` or ``jaeger_search_traces``.

    Examples:
        - Use when: "What services does Jaeger know about?"
          → call with no parameters; read the ``services`` list.
        - Use when: "Is `payment-service` instrumented?"
          → check if `payment-service` appears in the services list.
        - Use when: Starting a debugging session — list services first,
          then pick one for ``jaeger_list_operations`` or
          ``jaeger_search_traces``.
        - Don't use when: You already know the service name and want to
          search its traces (call ``jaeger_search_traces`` directly).
        - Don't use when: You want the dependency graph between services
          (call ``jaeger_get_dependencies``).

    Returns:
        dict with keys ``services_count`` / ``truncated`` / ``services``.
    """
    try:
        client = get_client()
        data = client.get("/services") or {}
        raw: list[str] = data.get("data") or []

        truncated = len(raw) > _LIST_CAP
        services = sorted(raw[:_LIST_CAP])

        result: ServicesOutput = {
            "services_count": len(services),
            "truncated": truncated,
            "services": services,
        }
        md = f"## Jaeger Services ({len(services)} shown" + (" — truncated at 500" if truncated else "") + ")\n\n"
        md_services = services[:_MD_ITEM_LIMIT]
        md += "\n".join(f"- `{s}`" for s in md_services)
        if len(services) > _MD_ITEM_LIMIT:
            md += _truncation_hint(len(services), _MD_ITEM_LIMIT, "services")
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, "listing Jaeger services")


@mcp.tool(
    name="jaeger_list_operations",
    annotations={
        "title": "List Operations",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def jaeger_list_operations(
    service: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description="Service name exactly as returned by jaeger_list_services.",
        ),
    ],
) -> OperationsOutput:
    """List all operation names Jaeger has seen for a given service.

    Wraps ``GET /api/services/{service}/operations``. Useful for discovering
    which operation names to pass as filters to ``jaeger_search_traces``.
    Output is capped at 500 operations.

    Examples:
        - Use when: "What HTTP endpoints does `order-service` expose in tracing?"
          → ``service='order-service'``.
        - Use when: You want to search for a specific slow operation but need
          the exact name — list operations first, then pass it to
          ``jaeger_search_traces``.
        - Use when: Auditing which gRPC methods a service traces.
        - Don't use when: You don't have a specific service — start with
          ``jaeger_list_services`` first.
        - Don't use when: You want to search traces immediately (skip this
          step if you already know the operation name).

    Returns:
        dict with ``service`` / ``operations_count`` / ``truncated`` /
        ``operations`` (sorted alphabetically).
    """
    try:
        client = get_client()
        data = client.get(f"/services/{service}/operations") or {}
        raw: list[str] = data.get("data") or []

        truncated = len(raw) > _LIST_CAP
        operations = sorted(raw[:_LIST_CAP])

        result: OperationsOutput = {
            "service": service,
            "operations_count": len(operations),
            "truncated": truncated,
            "operations": operations,
        }
        suffix = " — truncated at 500" if truncated else ""
        md = f"## Operations for `{service}` ({len(operations)} shown{suffix})\n\n"
        md_ops = operations[:_MD_ITEM_LIMIT]
        md += "\n".join(f"- `{op}`" for op in md_ops)
        if len(operations) > _MD_ITEM_LIMIT:
            md += _truncation_hint(len(operations), _MD_ITEM_LIMIT, "operations")
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"listing operations for service {service!r}")


@mcp.tool(
    name="jaeger_search_traces",
    annotations={
        "title": "Search Traces",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def jaeger_search_traces(
    service: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description=(
                "Service name to search traces for (required). Use jaeger_list_services to discover valid names."
            ),
        ),
    ],
    operation: Annotated[
        str | None,
        Field(
            default=None,
            max_length=500,
            description=(
                "Operation name filter (optional). Use jaeger_list_operations to discover valid names. "
                "Example: 'GET /api/orders' or 'grpc.health.v1.Health/Check'."
            ),
        ),
    ] = None,
    tags: Annotated[
        str | None,
        Field(
            default=None,
            max_length=2000,
            description=(
                "JSON string of tag key-value pairs to filter by (optional). "
                'Example: \'{"http.status_code":"500"}\' to find 5xx errors, '
                'or \'{"error":"true"}\' for any error spans.'
            ),
        ),
    ] = None,
    start: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            description=(
                "Start time in microseconds since Unix epoch UTC (optional). "
                "Example: 1713400000000000 for 2024-04-18 00:00:00 UTC."
            ),
        ),
    ] = None,
    end: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            description=(
                "End time in microseconds since Unix epoch UTC (optional). "
                "If omitted and start is set, defaults to now."
            ),
        ),
    ] = None,
    min_duration: Annotated[
        str | None,
        Field(
            default=None,
            max_length=20,
            description=(
                "Minimum trace duration filter (optional). Format: '100ms', '1.5s', '2m'. Use to find slow traces."
            ),
        ),
    ] = None,
    max_duration: Annotated[
        str | None,
        Field(
            default=None,
            max_length=20,
            description=(
                "Maximum trace duration filter (optional). Format: '100ms', '500ms'. "
                "Use to find fast traces or exclude outliers."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            default=20,
            ge=1,
            le=1500,
            description="Maximum number of traces to return (1-1500, default 20).",
        ),
    ] = 20,
) -> SearchTracesOutput:
    """Search Jaeger traces with rich filters.

    Wraps ``GET /api/traces``. Returns a list of trace summaries — use
    ``jaeger_get_trace`` to drill into a specific trace for span details.

    The ``tags`` parameter accepts a JSON string so the LLM can construct
    arbitrary tag filters. Durations (``min_duration``/``max_duration``) are
    forwarded as-is to Jaeger (e.g. ``'100ms'``, ``'1.5s'``).

    Examples:
        - Use when: "Show me recent 500 errors in `order-service`"
          → ``service='order-service'``, ``tags='{"http.status_code":"500"}'``.
        - Use when: "Find slow traces (>1s) for `checkout` endpoint"
          → ``service='checkout'``, ``operation='POST /checkout'``,
          ``min_duration='1s'``.
        - Use when: "Give me the last 5 traces in the last hour"
          → ``limit=5``, set ``start`` to (now - 3600s) in microseconds.
        - Don't use when: You already have a traceID and want full details
          (call ``jaeger_get_trace`` directly — one fewer round trip).
        - Don't use when: You want service dependency topology
          (call ``jaeger_get_dependencies``).

    Returns:
        dict with ``service`` / ``operation`` / ``returned`` / ``truncated`` /
        ``traces`` (list of :class:`TraceSummary`).
    """
    try:
        import json

        client = get_client()
        params: dict[str, Any] = {
            "service": service,
            "limit": limit,
        }
        if operation:
            params["operation"] = operation
        if tags:
            # Validate it's parseable JSON — raise ValueError with a clear message.
            try:
                json.loads(tags)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f'tags must be a valid JSON string (e.g. \'{{"error":"true"}}\'), got: {tags!r}. '
                    f"JSON parse error: {e}"
                ) from e
            params["tags"] = tags
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        if min_duration:
            params["minDuration"] = min_duration
        if max_duration:
            params["maxDuration"] = max_duration

        data = client.get("/traces", params=params) or {}
        raw_traces: list[dict[str, Any]] = data.get("data") or []

        summaries: list[TraceSummary] = [_shape_trace_summary(t) for t in raw_traces]
        truncated = len(summaries) >= limit

        result: SearchTracesOutput = {
            "service": service,
            "operation": operation,
            "returned": len(summaries),
            "truncated": truncated,
            "traces": summaries,
        }
        heading = (
            f"## Traces for `{service}`"
            + (f" / `{operation}`" if operation else "")
            + f" — {len(summaries)} returned"
            + (" (may be more — increase limit)" if truncated else "")
        )
        md_traces = summaries[:_MD_ITEM_LIMIT]
        rows = [
            f"- `{t['trace_id']}` — {t['root_operation'] or '?'} "
            f"| {t['duration_us']}µs | {t['span_count']} spans"
            + (f" | **{t['errors_count']} error(s)**" if t["errors_count"] else "")
            for t in md_traces
        ]
        md = heading + "\n\n" + "\n".join(rows)
        if len(summaries) > _MD_ITEM_LIMIT:
            md += _truncation_hint(len(summaries), _MD_ITEM_LIMIT, "traces")
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"searching traces for service {service!r}")


@mcp.tool(
    name="jaeger_get_trace",
    annotations={
        "title": "Get Trace",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def jaeger_get_trace(
    trace_id: Annotated[
        str,
        Field(
            min_length=16,
            max_length=32,
            description=(
                "Trace ID as a hex string (16 or 32 hex chars). "
                "Example: 'abcdef1234567890abcdef1234567890'. "
                "Obtain from jaeger_search_traces."
            ),
        ),
    ],
) -> TraceDetailOutput:
    """Retrieve full trace detail with all spans, service breakdown, and execution tree.

    Wraps ``GET /api/traces/{traceID}``. Returns every span in the trace,
    per-service statistics, and a flat execution tree (each node lists its
    child span IDs) that summarises the call hierarchy.

    Error spans are identified by ``tags["error"] = "true"``.

    Examples:
        - Use when: "Why is trace `abc123...` slow — show me the span breakdown"
          → ``trace_id='abc123...'``; inspect ``services`` for the heaviest service
          and ``execution_tree`` for the call hierarchy.
        - Use when: "Which service caused the error in trace `xyz...`?"
          → check ``spans`` where ``is_error=true``.
        - Use when: You found a slow/failed trace in ``jaeger_search_traces``
          and need full detail.
        - Don't use when: You don't have a specific traceID — use
          ``jaeger_search_traces`` to find one first.
        - Don't use when: You only want aggregate data across many traces
          (use ``jaeger_search_traces`` with filters instead).

    Returns:
        dict with ``trace_id`` / ``span_count`` / ``service_count`` /
        ``root_operation`` / ``root_service`` / ``start_time_us`` /
        ``total_duration_us`` / ``errors_count`` / ``services`` (per-service stats) /
        ``spans`` (all spans) / ``execution_tree``.
    """
    try:
        client = get_client()
        data = client.get(f"/traces/{trace_id}") or {}
        traces_data: list[dict[str, Any]] = data.get("data") or []
        if not traces_data:
            raise ValueError(
                f"No trace data returned for traceID {trace_id!r}. "
                "Verify the trace ID is correct (obtain from jaeger_search_traces)."
            )
        trace = traces_data[0]
        spans: list[dict[str, Any]] = trace.get("spans") or []
        processes: dict[str, Any] = trace.get("processes") or {}

        # Per-service stats
        svc_stats: dict[str, dict[str, int]] = {}
        for span in spans:
            pid = span.get("processID", "")
            svc = (processes.get(pid) or {}).get("serviceName", pid)
            if svc not in svc_stats:
                svc_stats[svc] = {"span_count": 0, "total_duration_us": 0, "error_count": 0}
            svc_stats[svc]["span_count"] += 1
            svc_stats[svc]["total_duration_us"] += span.get("duration", 0)
            if _span_is_error(span):
                svc_stats[svc]["error_count"] += 1

        services: list[ServiceSpanStats] = [
            {
                "service": svc,
                "span_count": stats["span_count"],
                "total_duration_us": stats["total_duration_us"],
                "error_count": stats["error_count"],
            }
            for svc, stats in sorted(svc_stats.items(), key=lambda x: x[1]["total_duration_us"], reverse=True)
        ]

        span_details: list[SpanDetail] = [_shape_span_detail(s, processes) for s in spans]
        execution_tree = _build_execution_tree(spans, processes)

        root = _find_root_span(spans)
        root_op = root.get("operationName") if root else None
        root_svc: str | None = None
        if root:
            pid = root.get("processID", "")
            root_svc = (processes.get(pid) or {}).get("serviceName")

        start_times = [s.get("startTime", 0) for s in spans if s.get("startTime")]
        start_time_us: int | None = min(start_times) if start_times else None
        end_times = [(s.get("startTime", 0) + s.get("duration", 0)) for s in spans]
        total_duration_us = (max(end_times) - min(start_times)) if start_times and end_times else 0
        errors_count = sum(s["is_error"] for s in span_details)

        result: TraceDetailOutput = {
            "trace_id": trace.get("traceID", trace_id),
            "span_count": len(spans),
            "service_count": len(svc_stats),
            "root_operation": root_op,
            "root_service": root_svc,
            "start_time_us": start_time_us,
            "total_duration_us": max(total_duration_us, 0),
            "errors_count": errors_count,
            "services": services,
            "spans": span_details,
            "execution_tree": execution_tree,
        }

        # Markdown summary
        md = (
            f"## Trace `{result['trace_id']}`\n\n"
            f"- **Root:** `{root_op or '?'}` on `{root_svc or '?'}`\n"
            f"- **Duration:** {result['total_duration_us']}µs\n"
            f"- **Spans:** {len(spans)} across {len(svc_stats)} service(s)\n"
            f"- **Errors:** {errors_count}\n\n"
            "### Service Breakdown\n\n"
        )
        for svc in services:
            md += (
                f"- `{svc['service']}` — {svc['span_count']} span(s), "
                f"{svc['total_duration_us']}µs total"
                + (f", **{svc['error_count']} error(s)**" if svc["error_count"] else "")
                + "\n"
            )
        if len(span_details) > _MD_ITEM_LIMIT:
            md += _truncation_hint(len(span_details), _MD_ITEM_LIMIT, "spans shown in text")
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"fetching trace {trace_id!r}")


@mcp.tool(
    name="jaeger_get_dependencies",
    annotations={
        "title": "Get Dependencies",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def jaeger_get_dependencies(
    end_ts: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            description=(
                "End timestamp in microseconds since Unix epoch UTC (optional). "
                "Defaults to now. Example: 1713400000000000."
            ),
        ),
    ] = None,
    lookback_hours: Annotated[
        int,
        Field(
            default=24,
            ge=1,
            le=720,
            description="Number of hours to look back from end_ts (1-720, default 24).",
        ),
    ] = 24,
) -> DependenciesOutput:
    """Retrieve the service-to-service call graph from Jaeger.

    Wraps ``GET /api/dependencies``. Returns directed edges (parent → child)
    with ``call_count`` — the number of spans where parent called child in
    the lookback window.

    Use this to understand service topology, find high fan-out services, or
    verify that a new service is connected as expected.

    Examples:
        - Use when: "What services does `order-service` call?"
          → check edges where ``parent='order-service'``.
        - Use when: "Map the full service dependency graph for the last 7 days"
          → ``lookback_hours=168``.
        - Use when: "Which services are called most frequently?"
          → sort edges by ``call_count`` descending.
        - Don't use when: You want detailed span timings (use
          ``jaeger_search_traces`` + ``jaeger_get_trace`` instead).
        - Don't use when: You need real-time data — Jaeger's dependency graph
          is aggregated and may lag by minutes.

    Returns:
        dict with ``end_ts_us`` / ``lookback_hours`` / ``edge_count`` /
        ``edges`` (list of ``{parent, child, call_count}``).
    """
    try:
        client = get_client()
        end_ts_us = end_ts if end_ts is not None else int(time.time() * 1_000_000)
        lookback_ms = lookback_hours * 3600 * 1000  # Jaeger expects milliseconds for lookback

        params: dict[str, Any] = {
            "endTs": end_ts_us // 1000,  # Jaeger API uses milliseconds
            "lookback": lookback_ms,
        }
        data = client.get("/dependencies", params=params) or {}
        raw: list[dict[str, Any]] = data.get("data") or []

        edges: list[DependencyEdge] = [
            {
                "parent": e.get("parent", ""),
                "child": e.get("child", ""),
                "call_count": int(e.get("callCount", 0)),
            }
            for e in raw
            if e.get("parent") and e.get("child")
        ]
        edges.sort(key=lambda e: e["call_count"], reverse=True)

        result: DependenciesOutput = {
            "end_ts_us": end_ts_us,
            "lookback_hours": lookback_hours,
            "edge_count": len(edges),
            "edges": edges,
        }
        md = f"## Service Dependencies (last {lookback_hours}h, {len(edges)} edges)\n\n"
        md_edges = edges[:_MD_ITEM_LIMIT]
        md += "\n".join(f"- `{e['parent']}` → `{e['child']}` ({e['call_count']:,} calls)" for e in md_edges)
        if len(edges) > _MD_ITEM_LIMIT:
            md += _truncation_hint(len(edges), _MD_ITEM_LIMIT, "edges")
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, "fetching Jaeger service dependencies")
