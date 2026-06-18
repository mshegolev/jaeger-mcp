"""MCP tools for Jaeger distributed tracing.

7 read-only tools covering the Jaeger HTTP Query API surface most useful to
an agent diagnosing latency, errors, or service topology:

- ``jaeger_list_services``     — discover which services Jaeger has seen
- ``jaeger_list_operations``   — list operations for a service
- ``jaeger_search_traces``     — search traces with rich filters
- ``jaeger_get_trace``         — retrieve full trace with span tree
- ``jaeger_get_dependencies``  — service-to-service call graph
- ``jaeger_compare_traces``    — structural diff between two traces
- ``jaeger_span_statistics``   — per-operation latency and error stats

All tools are ``async def``. FastMCP calls them directly in the event
loop — no thread pool overhead.
"""

from __future__ import annotations

import time
from typing import Annotated, Any

from pydantic import Field

from jaeger_mcp import output
from jaeger_mcp._mcp import get_client, mcp
from jaeger_mcp.models import (
    AnomalyDetectionOutput,
    CompareTracesOutput,
    CriticalPathOutput,
    CriticalPathSpan,
    BottleneckSpan,
    DependenciesOutput,
    DependencyEdge,
    OperationAnomaly,
    OperationDiff,
    OperationsOutput,
    SearchTracesOutput,
    ServicesOutput,
    ServiceSpanStats,
    SpanDetail,
    SpanStatisticsOutput,
    TraceDetailOutput,
    TraceSummary,
    WindowComparisonOutput,
)
import time
from typing import cast

from jaeger_mcp.shaping import (
    _LIST_CAP,
    _MD_ITEM_LIMIT,
    _build_span_tree,
    _format_bottleneck_span,
    _format_critical_path_span,
    aggregate_span_statistics as _aggregate_span_statistics,
    build_execution_tree as _build_execution_tree,
    compare_traces_diff as _compare_traces_diff,
    compare_windows,
    compute_deviation_score,
    compute_z_score,
    detect_anomalies,
    find_critical_path,
    find_root_span as _find_root_span,
    rank_bottlenecks,
    shape_span_detail as _shape_span_detail,
    shape_trace_summary as _shape_trace_summary,
    span_is_error as _span_is_error,
    truncation_hint as _truncation_hint,
)


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
async def jaeger_list_services() -> ServicesOutput:
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
        client = await get_client()
        data = await client.aget("/services") or {}
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
        return output.ok(result, md)
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
async def jaeger_list_operations(
    service: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            pattern=r"^[a-zA-Z0-9._:\-]+$",
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
        client = await get_client()
        data = await client.aget(f"/services/{service}/operations") or {}
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
        return output.ok(result, md)
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
async def jaeger_search_traces(
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

        client = await get_client()
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

        data = await client.aget("/traces", params=params) or {}
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
        return output.ok(result, md)
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
async def jaeger_get_trace(
    trace_id: Annotated[
        str,
        Field(
            min_length=16,
            max_length=32,
            pattern=r"^[0-9a-fA-F]+$",
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
        client = await get_client()
        # ASYNC-03: Use streaming for large trace fetch
        data = await client.aget_stream(f"/traces/{trace_id}") or {}
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

        # Reuse shared trace metadata logic (JGR-14: dedup).
        summary = _shape_trace_summary(trace)
        root_op = summary["root_operation"]
        root_svc = summary["root_service"]
        start_time_us = summary["start_time_us"]
        total_duration_us = summary["duration_us"]
        errors_count = summary["errors_count"]

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
        return output.ok(result, md)
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
async def jaeger_get_dependencies(
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
        client = await get_client()
        end_ts_us = end_ts if end_ts is not None else int(time.time() * 1_000_000)
        lookback_ms = lookback_hours * 3600 * 1000  # Jaeger expects milliseconds for lookback

        params: dict[str, Any] = {
            "endTs": end_ts_us // 1000,  # Jaeger API uses milliseconds
            "lookback": lookback_ms,
        }
        data = await client.aget("/dependencies", params=params) or {}
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
        return output.ok(result, md)
    except Exception as exc:
        output.fail(exc, "fetching Jaeger service dependencies")


@mcp.tool(
    name="jaeger_compare_traces",
    annotations={
        "title": "Compare Traces",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
async def jaeger_compare_traces(
    trace_id_a: Annotated[
        str,
        Field(
            min_length=16,
            max_length=32,
            pattern=r"^[0-9a-fA-F]+$",
            description=(
                "First trace ID (baseline) as a hex string (16 or 32 hex chars). Obtain from jaeger_search_traces."
            ),
        ),
    ],
    trace_id_b: Annotated[
        str,
        Field(
            min_length=16,
            max_length=32,
            pattern=r"^[0-9a-fA-F]+$",
            description=(
                "Second trace ID (comparison) as a hex string (16 or 32 hex chars). Obtain from jaeger_search_traces."
            ),
        ),
    ],
) -> CompareTracesOutput:
    """Compare two traces structurally — find added, removed, and changed spans.

    Fetches both traces from Jaeger and performs a structural diff by matching
    spans on ``(operationName, serviceName, parentOperation)`` — not span IDs,
    which differ across traces. Reports duration deltas and tag differences for
    changed spans.

    Examples:
        - Use when: "What changed between a fast and slow request?"
          → pass the trace IDs of both requests; inspect ``changed_spans``
          for duration deltas.
        - Use when: "Did a deployment add new service calls?"
          → compare a pre-deploy trace with a post-deploy trace; check
          ``added_spans`` for new operations.
        - Use when: "Are these two traces structurally identical?"
          → if ``added_spans``, ``removed_spans``, and ``changed_spans``
          are all empty, the traces have the same structure.
        - Don't use when: You want aggregate statistics across many traces
          (use ``jaeger_span_statistics`` instead, once available).
        - Don't use when: You only have one trace — use ``jaeger_get_trace``
          for single-trace inspection.

    Returns:
        dict with ``trace_id_a`` / ``trace_id_b`` / ``added_spans`` /
        ``removed_spans`` / ``changed_spans`` (with duration + tag deltas) /
        ``unchanged_count``.
    """
    try:
        client = await get_client()

        # ASYNC-02: Fetch both traces concurrently for 3x+ speedup
        results = await client.aget_many(
            [
                (f"/traces/{trace_id_a}", None),
                (f"/traces/{trace_id_b}", None),
            ]
        )
        data_a = results[0] or {}
        data_b = results[1] or {}

        traces_a: list[dict[str, Any]] = data_a.get("data") or []
        if not traces_a:
            raise ValueError(
                f"No trace data returned for trace_id_a {trace_id_a!r}. "
                "Verify the trace ID is correct (obtain from jaeger_search_traces)."
            )

        traces_b: list[dict[str, Any]] = data_b.get("data") or []
        if not traces_b:
            raise ValueError(
                f"No trace data returned for trace_id_b {trace_id_b!r}. "
                "Verify the trace ID is correct (obtain from jaeger_search_traces)."
            )

        result = _compare_traces_diff(traces_a[0], traces_b[0])

        # Markdown summary
        added = result["added_spans"]
        removed = result["removed_spans"]
        changed = result["changed_spans"]
        unchanged = result["unchanged_count"]
        total = len(added) + len(removed) + len(changed) + unchanged

        md = (
            f"## Trace Comparison\n\n"
            f"- **Trace A:** `{result['trace_id_a']}`\n"
            f"- **Trace B:** `{result['trace_id_b']}`\n"
            f"- **Total span keys:** {total}\n"
            f"- **Unchanged:** {unchanged} | "
            f"**Added:** {len(added)} | "
            f"**Removed:** {len(removed)} | "
            f"**Changed:** {len(changed)}\n"
        )

        if added:
            md += "\n### Added Spans (in B, not in A)\n\n"
            for s in added:
                md += f"- `{s['service']}` / `{s['operation_name']}`\n"

        if removed:
            md += "\n### Removed Spans (in A, not in B)\n\n"
            for s in removed:
                md += f"- `{s['service']}` / `{s['operation_name']}`\n"

        if changed:
            md += "\n### Changed Spans\n\n"
            for c in changed:
                delta = c["duration_delta_us"]
                sign = "+" if delta > 0 else ""
                md += f"- `{c['service']}` / `{c['operation_name']}` — {sign}{delta}µs"
                tag_changes = len(c["tags_added"]) + len(c["tags_removed"]) + len(c["tags_changed"])
                if tag_changes:
                    md += f" ({tag_changes} tag change(s))"
                md += "\n"

        return output.ok(result, md)
    except Exception as exc:
        output.fail(exc, f"comparing traces {trace_id_a!r} vs {trace_id_b!r}")


@mcp.tool(
    name="jaeger_span_statistics",
    annotations={
        "title": "Span Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
async def jaeger_span_statistics(
    service: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description=(
                "Service name to compute statistics for (required). Use jaeger_list_services to discover valid names."
            ),
        ),
    ],
    operation: Annotated[
        str | None,
        Field(
            default=None,
            max_length=500,
            description=(
                "Operation name filter (optional). When set, only traces "
                "matching this operation are fetched. Use jaeger_list_operations "
                "to discover valid names."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            default=20,
            ge=1,
            le=100,
            description="Number of traces to fetch and analyze (1-100, default 20).",
        ),
    ] = 20,
) -> SpanStatisticsOutput:
    """Compute per-operation latency percentiles and error rates across recent traces.

    Fetches up to ``limit`` traces for the given service (optionally filtered
    by operation), then aggregates all spans by operation name. For each
    operation reports: span count, p50/p95/p99 duration in microseconds,
    error count, and error rate.

    Duration values are in microseconds (integer). Error rate is
    ``error_count / span_count`` (float, 0.0–1.0).

    Examples:
        - Use when: "What are the p95 latencies for each endpoint in `order-service`?"
          → ``service='order-service'``; inspect each operation's ``p95_duration_us``.
        - Use when: "How often does the `POST /checkout` endpoint error?"
          → ``service='checkout-svc'``, ``operation='POST /checkout'``; check
          ``error_rate`` in the stats.
        - Use when: "Compare latency distributions across operations"
          → look at p50 vs p99 spread to identify high-variance operations.
        - Use when: "Get a larger sample for more accurate stats"
          → ``limit=100`` for higher confidence percentiles.
        - Don't use when: You want to compare two specific traces
          (use ``jaeger_compare_traces`` instead).
        - Don't use when: You want full span detail for a single trace
          (use ``jaeger_get_trace`` instead).

    Returns:
        dict with ``service`` / ``operation`` / ``trace_count`` /
        ``stats`` (list of per-operation stats with count, p50/p95/p99
        duration_us, error_count, error_rate).
    """
    try:
        client = await get_client()
        params: dict[str, Any] = {
            "service": service,
            "limit": limit,
        }
        if operation:
            params["operation"] = operation

        # Search for traces
        search_data = await client.aget("/traces", params=params) or {}
        raw_traces: list[dict[str, Any]] = search_data.get("data") or []

        # Aggregate span statistics
        stats = _aggregate_span_statistics(raw_traces)

        result: SpanStatisticsOutput = {
            "service": service,
            "operation": operation,
            "trace_count": len(raw_traces),
            "stats": stats,
        }

        # Markdown summary
        md = (
            f"## Span Statistics for `{service}`"
            + (f" / `{operation}`" if operation else "")
            + f"\n\n**Traces analyzed:** {len(raw_traces)}"
            + f" | **Operations:** {len(stats)}\n"
        )
        if stats:
            md += "\n| Operation | Count | p50 (µs) | p95 (µs) | p99 (µs) | Errors | Error Rate |\n"
            md += "|-----------|------:|--------:|---------:|---------:|-------:|-----------:|\n"
            for s in stats[:_MD_ITEM_LIMIT]:
                rate_pct = f"{s['error_rate']:.1%}"
                md += (
                    f"| `{s['operation']}` | {s['count']} "
                    f"| {s['p50_duration_us']:,} | {s['p95_duration_us']:,} "
                    f"| {s['p99_duration_us']:,} | {s['error_count']} | {rate_pct} |\n"
                )
            if len(stats) > _MD_ITEM_LIMIT:
                md += _truncation_hint(len(stats), _MD_ITEM_LIMIT, "operations")
        else:
            md += "\n_No spans found in the analyzed traces._\n"

        return output.ok(result, md)
    except Exception as exc:
        output.fail(exc, f"computing span statistics for service {service!r}")


# ── Critical Path Analysis ───────────────────────────────────────────────


@mcp.tool(
    name="jaeger_critical_path",
    annotations={
        "title": "Critical Path Analysis",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
async def jaeger_critical_path(
    trace_id: Annotated[
        str,
        Field(
            min_length=16,
            max_length=32,
            pattern=r"^[0-9a-fA-F]+$",
            description=("Trace ID as a hex string (16 or 32 hex chars). Obtain from jaeger_search_traces."),
        ),
    ],
) -> CriticalPathOutput:
    """Identify the critical path and top bottlenecks in a trace.

    Finds the longest-duration span chain (critical path) from root to leaf,
    and ranks spans by self-time to find actual performance bottlenecks.

    Examples:
        - Use when: "Why is this trace so slow?"
          → call with the slow trace ID; examine the critical_path_duration_us
          and critical_path_percentage to see how much of the total time is
          spent on the longest path.
        - Use when: "Which operations are consuming the most CPU/self-time?"
          → check the bottlenecks list sorted by self_time_us descending.
        - Use when: Debugging performance regressions — compare critical path
          percentages before/after changes.
        - Don't use when: You want aggregate statistics across many traces
          (use jaeger_span_statistics for that).
        - Don't use when: You need to compare two traces structurally
          (use jaeger_compare_traces for that).

    Returns:
        dict with trace metadata, critical path spans, and bottleneck ranking.
    """
    try:
        client = await get_client()

        # Use streaming for large traces (Phase 11 optimization)
        data = await client.aget_stream(f"/traces/{trace_id}") or {}
        traces_data: list[dict[str, Any]] = data.get("data") or []
        if not traces_data:
            raise ValueError(
                f"No trace data returned for trace_id {trace_id!r}. "
                "Verify the trace ID is correct (obtain from jaeger_search_traces)."
            )

        trace = traces_data[0]
        spans = trace.get("spans") or []
        if not spans:
            raise ValueError(f"Trace {trace_id!r} contains no spans.")

        # Extract trace metadata
        trace_id_actual = trace.get("traceID", trace_id)
        processes = trace.get("processes", {})

        # Find root operation
        root_span = _find_root_span(spans)
        root_operation = root_span.get("operationName") if root_span else None

        # Calculate total trace duration
        start_times = [s.get("startTime", 0) for s in spans if s.get("startTime")]
        end_times = [(s.get("startTime", 0) + s.get("duration", 0)) for s in spans if s.get("startTime")]
        total_duration_us = (max(end_times) - min(start_times)) if start_times and end_times else 0

        # Find critical path
        critical_path_spans = find_critical_path(spans)

        # Calculate cumulative durations for critical path
        cumulative_durations = []
        cumulative = 0
        for span in critical_path_spans:
            cumulative += span.get("duration", 0)
            cumulative_durations.append(cumulative)

        critical_path_duration_us = cumulative_durations[-1] if cumulative_durations else 0
        critical_path_percentage = (critical_path_duration_us / total_duration_us * 100) if total_duration_us > 0 else 0

        # Format critical path output
        formatted_critical_path = [
            _format_critical_path_span(span, cum_dur, total_duration_us, processes)
            for span, cum_dur in zip(critical_path_spans, cumulative_durations)
        ]

        # Rank bottlenecks
        bottleneck_spans = rank_bottlenecks(spans, limit=50)

        # Calculate self-times for bottlenecks
        span_dict, children = _build_span_tree(spans)
        formatted_bottlenecks = []
        for span in bottleneck_spans:
            span_id = span["spanID"]
            duration = span.get("duration", 0)

            # Sum child durations
            child_duration_sum = 0
            for child_id in children.get(span_id, []):
                child_span = span_dict.get(child_id, {})
                child_duration_sum += child_span.get("duration", 0)

            self_time = duration - child_duration_sum
            if self_time > 0:
                formatted_bottlenecks.append(_format_bottleneck_span(span, self_time, total_duration_us, processes))

        # Create output
        result: CriticalPathOutput = {
            "trace_id": trace_id_actual,
            "root_operation": root_operation,
            "total_duration_us": total_duration_us,
            "critical_path": formatted_critical_path,
            "critical_path_duration_us": critical_path_duration_us,
            "critical_path_percentage": round(critical_path_percentage, 1),
            "bottlenecks": formatted_bottlenecks,
            "bottleneck_count": len(formatted_bottlenecks),
        }

        # Generate markdown summary
        md = f"## Critical Path Analysis\n\n"
        md += f"- **Trace ID:** `{trace_id_actual}`\n"
        if root_operation:
            md += f"- **Root Operation:** `{root_operation}`\n"
        md += f"- **Total Duration:** {total_duration_us:,}μs ({total_duration_us / 1000000:.3f}s)\n"
        md += f"- **Critical Path Duration:** {critical_path_duration_us:,}μs ({critical_path_duration_us / 1000000:.3f}s)\n"
        md += f"- **Critical Path Percentage:** {critical_path_percentage:.1f}%\n"
        md += f"- **Bottlenecks Found:** {len(formatted_bottlenecks)}\n\n"

        if formatted_critical_path:
            md += "### Critical Path (Longest Duration Chain)\n\n"
            md += "| Operation | Service | Duration | Cumulative | % of Total |\n"
            md += "|-----------|---------|----------|------------|------------|\n"
            for span in formatted_critical_path:
                md += f"| `{span['operation']}` | `{span['service']}` | {span['duration_us']:,}μs | {span['cumulative_duration_us']:,}μs | {span['percentage_of_total']:.1f}% |\n"
            md += "\n"

        if formatted_bottlenecks:
            md += "### Top Bottlenecks (Self-Time Ranked)\n\n"
            md += "| Operation | Service | Duration | Self-Time | % of Total |\n"
            md += "|-----------|---------|----------|-----------|------------|\n"
            for span in formatted_bottlenecks[:10]:  # Show top 10
                md += f"| `{span['operation']}` | `{span['service']}` | {span['duration_us']:,}μs | {span['self_time_us']:,}μs | {span['self_time_percentage']:.1f}% |\n"
            if len(formatted_bottlenecks) > 10:
                md += f"\n*... and {len(formatted_bottlenecks) - 10} more (showing top 10)*\n"

        return output.ok(result, md)

    except Exception as exc:
        return output.fail(exc, f"analyzing critical path for trace {trace_id!r}")


# ── Batch Window Comparison ──────────────────────────────────────────────


@mcp.tool(
    name="jaeger_compare_windows",
    annotations={
        "title": "Compare Time Windows",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
async def jaeger_compare_windows(
    service: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="Service name to compare across time windows.",
        ),
    ],
    baseline_start: Annotated[
        int,
        Field(
            ge=0,
            description="Baseline window start time (Unix timestamp in microseconds).",
        ),
    ],
    baseline_end: Annotated[
        int,
        Field(
            gt=0,
            description="Baseline window end time (Unix timestamp in microseconds).",
        ),
    ],
    comparison_start: Annotated[
        int,
        Field(
            ge=0,
            description="Comparison window start time (Unix timestamp in microseconds).",
        ),
    ],
    comparison_end: Annotated[
        int,
        Field(
            gt=0,
            description="Comparison window end time (Unix timestamp in microseconds).",
        ),
    ],
    operation: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=100,
            description="Optional operation name filter.",
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            ge=10,
            le=1000,
            description="Maximum traces to fetch per window (default 100).",
        ),
    ] = 100,
) -> WindowComparisonOutput:
    """Compare aggregate trace behavior between two time periods for a service.

    Fetches traces from both time windows, aggregates span statistics per operation,
    then compares the aggregate behavior to detect performance changes.

    Examples:
        - Use when: "Did our latest deployment affect performance?"
          → compare pre-deploy and post-deploy time windows for the service.
        - Use when: "Which operations got slower after the database upgrade?"
          → check the comparison_p95_us and p95_delta_pct columns for increases.
        - Use when: "Are we seeing new error patterns?"
          → look for operations with increased error_rate_delta.
        - Use when: "Did we add or remove any API endpoints?"
          → check added_count and removed_count in the summary.
        - Don't use when: You want to compare two specific traces
          (use jaeger_compare_traces instead).
        - Don't use when: You want full span detail for a single trace
          (use jaeger_get_trace instead).

    Returns:
        WindowComparisonOutput with per-operation diffs and summary statistics.
    """
    try:
        import time
        from typing import cast

        client = await get_client()

        # Convert timestamps to milliseconds for Jaeger API
        baseline_params = {
            "service": service,
            "start": baseline_start // 1000,  # microseconds to milliseconds
            "end": baseline_end // 1000,
            "limit": limit,
        }
        comparison_params = {
            "service": service,
            "start": comparison_start // 1000,
            "end": comparison_end // 1000,
            "limit": limit,
        }

        if operation:
            baseline_params["operation"] = operation
            comparison_params["operation"] = operation

        # Use concurrent fetching for both windows (Phase 11 optimization)
        results = await client.aget_many(
            [
                ("/traces", baseline_params),
                ("/traces", comparison_params),
            ]
        )

        baseline_data = results[0] or {}
        comparison_data = results[1] or {}

        baseline_traces: list[dict[str, Any]] = baseline_data.get("data") or []
        comparison_traces: list[dict[str, Any]] = comparison_data.get("data") or []

        # Aggregate statistics for each window
        baseline_stats = _aggregate_span_statistics(baseline_traces)
        comparison_stats = _aggregate_span_statistics(comparison_traces)

        # Convert OperationStats to dict for compare_windows function
        baseline_stats_dicts = [dict(stat) for stat in baseline_stats]
        comparison_stats_dicts = [dict(stat) for stat in comparison_stats]

        # Compare windows
        diff_results = compare_windows(baseline_stats_dicts, comparison_stats_dicts)

        # Extract summary from diff results (added by compare_windows)
        summary = diff_results.pop() if diff_results and "_summary" in diff_results[-1] else {}
        if summary:
            summary_data = summary["_summary"]
            added_count = summary_data["added_count"]
            removed_count = summary_data["removed_count"]
            faster_count = summary_data["faster_count"]
            slower_count = summary_data["slower_count"]
            total_deviation = summary_data["total_deviation"]
            operation_count = summary_data["operation_count"]
        else:
            # Fallback counts
            added_count = sum(1 for d in diff_results if d["change_type"] == "added")
            removed_count = sum(1 for d in diff_results if d["change_type"] == "removed")
            faster_count = sum(1 for d in diff_results if d["change_type"] == "faster")
            slower_count = sum(1 for d in diff_results if d["change_type"] == "slower")
            total_deviation = sum(d["deviation_score"] for d in diff_results)
            operation_count = len(diff_results)

        # Calculate overall deviation score (normalized by operation count)
        overall_deviation_score = (total_deviation / operation_count) if operation_count > 0 else 0.0

        # Create output
        operations_data = [d for d in diff_results if "_summary" not in d]
        result: WindowComparisonOutput = {
            "service": service,
            "baseline_start": baseline_start,
            "baseline_end": baseline_end,
            "comparison_start": comparison_start,
            "comparison_end": comparison_end,
            "operations": operations_data,  # type: ignore
            "total_operations": operation_count,
            "added_count": added_count,
            "removed_count": removed_count,
            "faster_count": faster_count,
            "slower_count": slower_count,
            "overall_deviation_score": round(overall_deviation_score, 3),
        }

        # Generate markdown summary
        md = f"## Window Comparison Analysis\n\n"
        md += f"- **Service:** `{service}`\n"
        if operation:
            md += f"- **Operation Filter:** `{operation}`\n"
        md += f"- **Baseline Window:** {baseline_start // 1000000} → {baseline_end // 1000000} (Δ={(baseline_end - baseline_start) // 1000000}s)\n"
        md += f"- **Comparison Window:** {comparison_start // 1000000} → {comparison_end // 1000000} (Δ={(comparison_end - comparison_start) // 1000000}s)\n"
        md += f"- **Traces Analyzed:** {len(baseline_traces)} baseline, {len(comparison_traces)} comparison\n"
        md += f"- **Operations Compared:** {operation_count}\n"
        md += f"- **Overall Deviation Score:** {overall_deviation_score:.3f}\n\n"

        md += f"### Summary\n\n"
        md += f"- **Added Operations:** {added_count} | "
        md += f"**Removed Operations:** {removed_count}\n"
        md += f"- **Faster Operations:** {faster_count} | "
        md += f"**Slower Operations:** {slower_count}\n\n"

        if diff_results:
            md += "### Top Changed Operations (by Deviation Score)\n\n"
            md += "| Operation | Change | Count Δ | p95 Δ | Error Δ | Deviation |\n"
            md += "|-----------|--------|---------|-------|---------|-----------|\n"

            # Show top 15 most changed operations
            for diff in diff_results[:15]:
                if "_summary" in diff:
                    continue
                operation_name = diff["operation"]
                change_type = diff["change_type"]
                count_delta = diff["count_delta"]
                p95_delta_pct = diff["p95_delta_pct"]
                error_delta = diff["error_rate_delta"]
                deviation = diff["deviation_score"]

                # Format change type with emoji
                change_display = {
                    "added": "➕ Added",
                    "removed": "➖ Removed",
                    "faster": "⬇️ Faster",
                    "slower": "⬆️ Slower",
                    "unchanged": "➡️ Unchanged",
                }.get(change_type, change_type)

                # Format deltas with signs
                count_display = f"{count_delta:+d}" if count_delta != 0 else "0"
                p95_display = f"{p95_delta_pct:+.1f}%" if abs(p95_delta_pct) >= 0.1 else "0%"
                error_display = f"{error_delta:+.2f}" if abs(error_delta) >= 0.01 else "0.00"

                md += f"| `{operation_name}` | {change_display} | {count_display} | {p95_display} | {error_display} | {deviation:.3f} |\n"

            if len(diff_results) > 15:
                md += f"\n*... and {len(diff_results) - 15} more operations (showing top 15)*\n"
        else:
            md += "_No operations found in either time window._\n"

        return output.ok(result, md)

    except Exception as exc:
        return output.fail(exc, f"comparing windows for service {service!r}")


# ── Anomaly Detection ────────────────────────────────────────────────────


@mcp.tool(
    name="jaeger_detect_anomalies",
    annotations={
        "title": "Detect Anomalies",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
async def jaeger_detect_anomalies(
    service: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="Service name to detect anomalies for.",
        ),
    ],
    baseline_duration_minutes: Annotated[
        int,
        Field(
            ge=5,
            le=1440,
            description="Historical baseline duration in minutes (5-1440, default 60).",
        ),
    ] = 60,
    sensitivity: Annotated[
        float,
        Field(
            ge=1.0,
            le=5.0,
            description="Anomaly sensitivity threshold (1.0-5.0, default 2.0). Lower = more sensitive.",
        ),
    ] = 2.0,
    current_duration_minutes: Annotated[
        int,
        Field(
            ge=1,
            le=60,
            description="Current observation window in minutes (1-60, default 5).",
        ),
    ] = 5,
) -> AnomalyDetectionOutput:
    """Detect latency and error-rate anomalies for a service by comparing recent behavior to historical baseline.

    Fetches traces from a historical baseline window and a recent observation window,
    computes per-operation statistics for both, then identifies statistically significant
    deviations that may indicate performance issues or reliability problems.

    Examples:
        - Use when: "Are there any new performance issues in `order-service`?"
          → `service='order-service'` (uses default 60-minute baseline, 5-minute current).
        - Use when: "Be more sensitive to subtle changes"
          → set `sensitivity=1.5` (lower threshold).
        - Use when: "Check for issues over the last 24 hours against previous week"
          → `baseline_duration_minutes=10080`, `current_duration_minutes=1440`.
        - Don't use when: You want to compare two specific time periods
          (use jaeger_compare_windows instead).
        - Don't use when: You want full span detail for a single trace
          (use jaeger_get_trace instead).

    Returns:
        AnomalyDetectionOutput with flagged operations and severity scores.
    """
    try:
        import time

        client = await get_client()

        # Calculate time windows
        now_us = int(time.time() * 1_000_000)
        current_end_us = now_us
        current_start_us = now_us - (current_duration_minutes * 60 * 1_000_000)
        baseline_end_us = current_start_us
        baseline_start_us = baseline_end_us - (baseline_duration_minutes * 60 * 1_000_000)

        # Convert timestamps to milliseconds for Jaeger API
        baseline_params = {
            "service": service,
            "start": baseline_start_us // 1000,
            "end": baseline_end_us // 1000,
            "limit": 200,  # Larger limit for baseline
        }
        current_params = {
            "service": service,
            "start": current_start_us // 1000,
            "end": current_end_us // 1000,
            "limit": 100,  # Smaller limit for current (more recent)
        }

        # Use concurrent fetching for both windows (Phase 11 optimization)
        results = await client.aget_many(
            [
                ("/traces", baseline_params),
                ("/traces", current_params),
            ]
        )

        baseline_data = results[0] or {}
        current_data = results[1] or {}

        baseline_traces: list[dict[str, Any]] = baseline_data.get("data") or []
        current_traces: list[dict[str, Any]] = current_data.get("data") or []

        # Aggregate statistics for each window
        baseline_stats = _aggregate_span_statistics(baseline_traces)
        current_stats = _aggregate_span_statistics(current_traces)

        # Detect anomalies
        anomaly_results = detect_anomalies(current_stats, baseline_stats, sensitivity)

        # Extract summary from anomaly results (added by detect_anomalies)
        summary = anomaly_results.pop() if anomaly_results and "_summary" in anomaly_results[-1] else {}
        if summary:
            summary_data = summary["_summary"]
            total_anomalies = summary_data["total_anomalies"]
            latency_anomalies = summary_data["latency_anomalies"]
            error_rate_anomalies = summary_data["error_rate_anomalies"]
        else:
            # Fallback counts
            total_anomalies = len([a for a in anomaly_results if "_summary" not in a])
            latency_anomalies = sum(1 for a in anomaly_results if a.get("anomaly_type") == "latency")
            error_rate_anomalies = sum(1 for a in anomaly_results if a.get("anomaly_type") == "error_rate")

        # Create output
        anomalies_data = [a for a in anomaly_results if "_summary" not in a]
        result: AnomalyDetectionOutput = {
            "service": service,
            "baseline_start": baseline_start_us,
            "baseline_end": baseline_end_us,
            "current_start": current_start_us,
            "current_end": current_end_us,
            "anomalies": anomalies_data,  # type: ignore
            "total_anomalies": total_anomalies,
            "latency_anomalies": latency_anomalies,
            "error_rate_anomalies": error_rate_anomalies,
            "sensitivity": sensitivity,
        }

        # Generate markdown summary
        md = f"## Anomaly Detection Results\n\n"
        md += f"- **Service:** `{service}`\n"
        md += (
            f"- **Baseline Window:** {baseline_duration_minutes} minutes ago → {current_duration_minutes} minutes ago\n"
        )
        md += f"- **Current Window:** Last {current_duration_minutes} minutes\n"
        md += f"- **Sensitivity:** {sensitivity}\n"
        md += f"- **Traces Analyzed:** {len(baseline_traces)} baseline, {len(current_traces)} current\n"
        md += f"- **Anomalies Detected:** {total_anomalies}\n"
        md += f"  - **Latency Anomalies:** {latency_anomalies}\n"
        md += f"  - **Error Rate Anomalies:** {error_rate_anomalies}\n\n"

        if anomaly_results:
            md += "### Top Anomalies (by Severity)\n\n"
            md += "| Operation | Type | Stat | Current | Baseline | Z-Score | Severity |\n"
            md += "|-----------|------|------|---------|----------|---------|----------|\n"

            # Show top 15 most severe anomalies
            for anomaly in anomaly_results[:15]:
                if "_summary" in anomaly:
                    continue
                operation = anomaly["operation"]
                anomaly_type = anomaly["anomaly_type"]
                stat = anomaly["baseline_stat"]
                current_val = anomaly["current_value"]
                baseline_val = anomaly["baseline_value"]
                z_score = anomaly["z_score"]
                severity = anomaly["severity"]

                # Format values appropriately
                if stat.endswith("_us"):
                    current_display = f"{int(current_val):,}μs"
                    baseline_display = f"{int(baseline_val):,}μs"
                else:
                    current_display = f"{current_val:.4f}"
                    baseline_display = f"{baseline_val:.4f}"

                # Format severity with emoji
                severity_display = {
                    "critical": "🔴 Critical",
                    "high": "🟠 High",
                    "medium": "🟡 Medium",
                    "low": "🟢 Low",
                }.get(severity, severity)

                md += f"| `{operation}` | {anomaly_type} | {stat} | {current_display} | {baseline_display} | {z_score:+.2f} | {severity_display} |\n"

            if len(anomaly_results) > 15:
                md += f"\n*... and {len(anomaly_results) - 15} more anomalies (showing top 15)*\n"
        else:
            md += "_No anomalies detected with current sensitivity threshold._\n"

        return output.ok(result, md)

    except Exception as exc:
        return output.fail(
            exc,
            f"detecting anomalies for service {service!r} "
            f"(baseline: {baseline_duration_minutes}min, sensitivity: {sensitivity})",
        )


# ── Predictive Analytics ─────────────────────────────────────────────────


from .predictive.tools import (
    jaeger_predict_degradation,
    jaeger_forecast_capacity,
)
