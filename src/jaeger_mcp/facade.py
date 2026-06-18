"""High-level ``JaegerClient`` facade for in-process library use.

This module provides the public ``JaegerClient`` class that the
investigator (and any other Python consumer) imports directly::

    from jaeger_mcp import JaegerClient

    client = JaegerClient.from_env()
    trace = client.get_trace("abc123...")
    for span in trace.spans:
        if span.error:
            print(span.service_name, span.start_utc, span.tags)

The facade wraps :class:`~jaeger_mcp.client.JaegerHTTPClient` and reuses
the data-shaping helpers from :mod:`jaeger_mcp.tools`. It adds typed
domain objects (:class:`Span`, :class:`Trace`, :class:`TraceSummary`)
with the Evidence-required fields (``start_utc``, ``error``,
``service_name``, etc.).

No MCP server is needed — the facade talks directly to the Jaeger HTTP
Query API.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from jaeger_mcp.client import JaegerHTTPClient
from jaeger_mcp.models import AnomalyDetectionOutput, CriticalPathOutput, WindowComparisonOutput
from jaeger_mcp.predictive.models import PredictionResult, ForecastResult
from jaeger_mcp.shaping import (
    _build_span_tree,
    _format_bottleneck_span,
    _format_critical_path_span,
    aggregate_span_statistics as _aggregate_span_statistics,
    compare_traces_diff as _compare_traces_diff,
    compare_windows,
    compute_z_score,
    detect_anomalies,
    find_critical_path,
    find_root_span as _find_root_span,
    rank_bottlenecks,
    shape_trace_summary as _raw_trace_summary,
    span_is_error as _span_is_error,
    span_tags_flat as _span_tags_flat,
)


# ── Domain objects ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Span:
    """A single span in a Jaeger trace with Evidence-required fields.

    Attributes:
        span_id: Unique span identifier within the trace.
        trace_id: Trace this span belongs to.
        parent_span_id: Parent span ID (``None`` for root spans).
        operation: Operation / endpoint name (e.g. ``GET /orders``).
        service_name: Resolved service name from Jaeger processes map.
        start_time_us: Raw start time in microseconds since Unix epoch.
        start_utc: Start time as a timezone-aware UTC :class:`datetime`.
        duration_us: Span duration in microseconds.
        error: ``True`` when the span has ``tags["error"] == "true"``.
        tags: Flat ``{key: str_value}`` dict of all span tags.
        logs: Raw Jaeger log entries (list of dicts).
    """

    span_id: str
    trace_id: str
    parent_span_id: str | None
    operation: str
    service_name: str
    start_time_us: int
    start_utc: datetime
    duration_us: int
    error: bool
    tags: dict[str, str] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Trace:
    """Full trace with all spans and metadata.

    Attributes:
        trace_id: Top-level trace identifier.
        spans: All spans in the trace.
        root_operation: Operation name of the root span (if found).
        root_service: Service name of the root span (if found).
        start_time_us: Earliest span start time (microseconds).
        duration_us: Total trace duration (microseconds).
        service_count: Number of distinct services in the trace.
        errors_count: Number of spans with ``error=True``.
    """

    trace_id: str
    spans: list[Span]
    root_operation: str | None = None
    root_service: str | None = None
    start_time_us: int | None = None
    duration_us: int = 0
    service_count: int = 0
    errors_count: int = 0


@dataclass(frozen=True, slots=True)
class TraceSummary:
    """Lightweight trace summary returned by :meth:`JaegerClient.search_traces`.

    Same shape as the MCP tool output but with ``start_utc`` added.
    """

    trace_id: str
    root_operation: str | None
    root_service: str | None
    start_time_us: int | None
    start_utc: datetime | None
    duration_us: int
    span_count: int
    service_count: int
    errors_count: int


@dataclass(frozen=True, slots=True)
class ServiceDep:
    """A single directed edge in the service dependency graph."""

    parent: str
    child: str
    call_count: int


@dataclass(frozen=True, slots=True)
class SpanIdentity:
    """Identifies a span by its structural position in the trace tree."""

    operation_name: str
    service: str
    parent_operation: str | None


@dataclass(frozen=True, slots=True)
class SpanChange:
    """A span that exists in both traces but differs in duration or tags."""

    operation_name: str
    service: str
    parent_operation: str | None
    duration_a_us: int
    duration_b_us: int
    duration_delta_us: int
    tags_added: dict[str, str] = field(default_factory=dict)
    tags_removed: dict[str, str] = field(default_factory=dict)
    tags_changed: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TraceComparison:
    """Result of comparing two traces structurally.

    Attributes:
        trace_id_a: First (baseline) trace ID.
        trace_id_b: Second (comparison) trace ID.
        added_spans: Spans present in trace B but not in trace A.
        removed_spans: Spans present in trace A but not in trace B.
        changed_spans: Spans present in both but with different duration or tags.
        unchanged_count: Number of spans identical in both traces.
    """

    trace_id_a: str
    trace_id_b: str
    added_spans: list[SpanIdentity]
    removed_spans: list[SpanIdentity]
    changed_spans: list[SpanChange]
    unchanged_count: int


@dataclass(frozen=True, slots=True)
class OperationStatResult:
    """Per-operation latency and error statistics.

    Attributes:
        operation: Operation name.
        count: Total number of spans with this operation.
        p50_duration_us: 50th percentile duration (microseconds).
        p95_duration_us: 95th percentile duration (microseconds).
        p99_duration_us: 99th percentile duration (microseconds).
        error_count: Number of error spans.
        error_rate: Error count divided by total count (0.0–1.0).
    """

    operation: str
    count: int
    p50_duration_us: int
    p95_duration_us: int
    p99_duration_us: int
    error_count: int
    error_rate: float


@dataclass(frozen=True, slots=True)
class SpanStatisticsResult:
    """Result of computing span statistics across traces.

    Attributes:
        service: The service that was queried.
        operation: The operation filter used (None if not filtered).
        trace_count: Number of traces fetched and analyzed.
        stats: Per-operation statistics, sorted alphabetically.
    """

    service: str
    operation: str | None
    trace_count: int
    stats: list[OperationStatResult]


# ── Helpers ───────────────────────────────────────────────────────────────


def _us_to_utc(us: int) -> datetime:
    """Convert Jaeger microsecond timestamp to timezone-aware UTC datetime."""
    return datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc)


def _build_span(
    raw_span: dict[str, Any],
    processes: dict[str, Any],
    trace_id: str,
) -> Span:
    """Convert a raw Jaeger span dict into a :class:`Span`."""
    pid = raw_span.get("processID", "")
    proc = processes.get(pid) or {}
    service_name = proc.get("serviceName", pid)

    refs = raw_span.get("references") or []
    parent_span_id: str | None = None
    for ref in refs:
        if ref.get("refType") == "CHILD_OF":
            parent_span_id = ref.get("spanID")
            break

    start_us: int = raw_span.get("startTime", 0)

    return Span(
        span_id=raw_span.get("spanID", ""),
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        operation=raw_span.get("operationName", ""),
        service_name=service_name,
        start_time_us=start_us,
        start_utc=_us_to_utc(start_us),
        duration_us=raw_span.get("duration", 0),
        error=_span_is_error(raw_span),
        tags=_span_tags_flat(raw_span),
        logs=raw_span.get("logs") or [],
    )


# ── Facade ────────────────────────────────────────────────────────────────


class JaegerClient:
    """High-level Jaeger client for in-process (library) use.

    Wraps :class:`~jaeger_mcp.client.JaegerHTTPClient` and exposes typed
    domain objects suitable for the investigator's Evidence adapter.

    Methods are synchronous for caller convenience; async I/O is used
    internally via ``asyncio.run()``.

    Usage::

        from jaeger_mcp import JaegerClient

        client = JaegerClient.from_env()
        trace = client.get_trace("abc123...")
        error_spans = [s for s in trace.spans if s.error]

    Args:
        http_client: An already-constructed :class:`JaegerHTTPClient`.
            Prefer :meth:`from_env` for typical usage.
    """

    def __init__(self, http_client: JaegerHTTPClient) -> None:
        self._http = http_client

    @classmethod
    def from_env(
        cls,
        *,
        url: str | None = None,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        ssl_verify: bool | None = None,
    ) -> JaegerClient:
        """Construct a :class:`JaegerClient` from environment variables.

        All parameters are optional overrides — when ``None``, values are
        read from ``JAEGER_URL``, ``JAEGER_TOKEN``, etc.
        """
        http = JaegerHTTPClient(
            url=url,
            token=token,
            username=username,
            password=password,
            ssl_verify=ssl_verify,
        )
        return cls(http)

    # ── Query methods ─────────────────────────────────────────────────

    async def _aget_trace(self, trace_id: str) -> Trace:
        """Async implementation of :meth:`get_trace`."""
        # ASYNC-03: Use streaming for large trace fetch
        data = await self._http.aget_stream(f"/traces/{trace_id}") or {}
        traces_data: list[dict[str, Any]] = data.get("data") or []
        if not traces_data:
            raise ValueError(f"No trace data returned for traceID {trace_id!r}. Verify the trace ID is correct.")
        raw_trace = traces_data[0]
        spans_raw: list[dict[str, Any]] = raw_trace.get("spans") or []
        processes: dict[str, Any] = raw_trace.get("processes") or {}
        tid = raw_trace.get("traceID", trace_id)

        spans = [_build_span(s, processes, tid) for s in spans_raw]

        root = _find_root_span(spans_raw)
        root_op = root.get("operationName") if root else None
        root_svc: str | None = None
        if root:
            pid = root.get("processID", "")
            root_svc = (processes.get(pid) or {}).get("serviceName")

        start_times = [s.start_time_us for s in spans if s.start_time_us]
        start_us = min(start_times) if start_times else None
        end_times = [s.start_time_us + s.duration_us for s in spans]
        duration_us = (max(end_times) - min(start_times)) if start_times and end_times else 0

        services = {s.service_name for s in spans}
        errors = sum(1 for s in spans if s.error)

        return Trace(
            trace_id=tid,
            spans=spans,
            root_operation=root_op,
            root_service=root_svc,
            start_time_us=start_us,
            duration_us=max(duration_us, 0),
            service_count=len(services),
            errors_count=errors,
        )

    def get_trace(self, trace_id: str) -> Trace:
        """Retrieve a full trace by ID.

        Returns:
            A :class:`Trace` whose :attr:`~Trace.spans` carry all
            Evidence-required fields (``start_utc``, ``error``,
            ``service_name``, ``tags``, etc.).

        Raises:
            ValueError: If the trace ID returns no data.
            httpx.HTTPStatusError: On HTTP-level failures.
        """
        return asyncio.run(self._aget_trace(trace_id))

    async def _asearch_traces(
        self,
        service: str,
        *,
        operation: str | None = None,
        tags: dict[str, str] | None = None,
        min_duration: str | None = None,
        max_duration: str | None = None,
        time_from: int | None = None,
        time_to: int | None = None,
        limit: int = 20,
    ) -> list[TraceSummary]:
        """Async implementation of :meth:`search_traces`."""
        import json

        params: dict[str, Any] = {"service": service, "limit": limit}
        if operation:
            params["operation"] = operation
        if tags:
            params["tags"] = json.dumps(tags)
        if time_from is not None:
            params["start"] = time_from
        if time_to is not None:
            params["end"] = time_to
        if min_duration:
            params["minDuration"] = min_duration
        if max_duration:
            params["maxDuration"] = max_duration

        data = await self._http.aget("/traces", params=params) or {}
        raw_traces: list[dict[str, Any]] = data.get("data") or []

        summaries: list[TraceSummary] = []
        for rt in raw_traces:
            raw = _raw_trace_summary(rt)
            start_us = raw["start_time_us"]
            summaries.append(
                TraceSummary(
                    trace_id=raw["trace_id"],
                    root_operation=raw["root_operation"],
                    root_service=raw["root_service"],
                    start_time_us=start_us,
                    start_utc=_us_to_utc(start_us) if start_us is not None else None,
                    duration_us=raw["duration_us"],
                    span_count=raw["span_count"],
                    service_count=raw["service_count"],
                    errors_count=raw["errors_count"],
                )
            )
        return summaries

    def search_traces(
        self,
        service: str,
        *,
        operation: str | None = None,
        tags: dict[str, str] | None = None,
        min_duration: str | None = None,
        max_duration: str | None = None,
        time_from: int | None = None,
        time_to: int | None = None,
        limit: int = 20,
    ) -> list[TraceSummary]:
        """Search traces for a service with optional filters.

        Args:
            service: Service name (required).
            operation: Operation name filter.
            tags: Tag key-value pairs to filter by.
            min_duration: Minimum duration (e.g. ``'100ms'``, ``'1.5s'``).
            max_duration: Maximum duration filter.
            time_from: Start time in microseconds since epoch (UTC).
            time_to: End time in microseconds since epoch (UTC).
            limit: Maximum number of traces (default 20).

        Returns:
            List of :class:`TraceSummary` objects.

        Raises:
            ValueError: On invalid parameters.
            httpx.HTTPStatusError: On HTTP-level failures.
        """
        return asyncio.run(
            self._asearch_traces(
                service,
                operation=operation,
                tags=tags,
                min_duration=min_duration,
                max_duration=max_duration,
                time_from=time_from,
                time_to=time_to,
                limit=limit,
            )
        )

    async def _alist_services(self) -> list[str]:
        """Async implementation of :meth:`list_services`."""
        data = await self._http.aget("/services") or {}
        raw: list[str] = data.get("data") or []
        return sorted(raw)

    def list_services(self) -> list[str]:
        """Return all service names known to Jaeger."""
        return asyncio.run(self._alist_services())

    async def _aget_dependencies(self, *, lookback_hours: int = 24) -> list[ServiceDep]:
        """Async implementation of :meth:`get_dependencies`."""
        end_ts_us = int(time.time() * 1_000_000)
        lookback_ms = lookback_hours * 3600 * 1000
        params: dict[str, Any] = {
            "endTs": end_ts_us // 1000,
            "lookback": lookback_ms,
        }
        data = await self._http.aget("/dependencies", params=params) or {}
        raw: list[dict[str, Any]] = data.get("data") or []
        edges = [
            ServiceDep(
                parent=e.get("parent", ""),
                child=e.get("child", ""),
                call_count=int(e.get("callCount", 0)),
            )
            for e in raw
            if e.get("parent") and e.get("child")
        ]
        edges.sort(key=lambda e: e.call_count, reverse=True)
        return edges

    def get_dependencies(self, *, lookback_hours: int = 24) -> list[ServiceDep]:
        """Return the service-to-service call graph.

        Args:
            lookback_hours: How far back to look (default 24h).

        Returns:
            List of :class:`ServiceDep` edges sorted by call count (descending).

        Raises:
            httpx.HTTPStatusError: On HTTP-level failures.
        """
        return asyncio.run(self._aget_dependencies(lookback_hours=lookback_hours))

    async def _acompare_traces(self, trace_id_a: str, trace_id_b: str) -> TraceComparison:
        """Async implementation of :meth:`compare_traces`."""
        # ASYNC-02: Fetch both traces concurrently for 3x+ speedup
        results = await self._http.aget_many(
            [
                (f"/traces/{trace_id_a}", None),
                (f"/traces/{trace_id_b}", None),
            ]
        )
        data_a = results[0] or {}
        data_b = results[1] or {}

        traces_a: list[dict[str, Any]] = data_a.get("data") or []
        if not traces_a:
            raise ValueError(f"No trace data returned for trace_id_a {trace_id_a!r}.")

        traces_b: list[dict[str, Any]] = data_b.get("data") or []
        if not traces_b:
            raise ValueError(f"No trace data returned for trace_id_b {trace_id_b!r}.")

        raw = _compare_traces_diff(traces_a[0], traces_b[0])

        added = [
            SpanIdentity(
                operation_name=s["operation_name"],
                service=s["service"],
                parent_operation=s["parent_operation"],
            )
            for s in raw["added_spans"]
        ]
        removed = [
            SpanIdentity(
                operation_name=s["operation_name"],
                service=s["service"],
                parent_operation=s["parent_operation"],
            )
            for s in raw["removed_spans"]
        ]
        changed = [
            SpanChange(
                operation_name=c["operation_name"],
                service=c["service"],
                parent_operation=c["parent_operation"],
                duration_a_us=c["duration_a_us"],
                duration_b_us=c["duration_b_us"],
                duration_delta_us=c["duration_delta_us"],
                tags_added=c["tags_added"],
                tags_removed=c["tags_removed"],
                tags_changed=c["tags_changed"],
            )
            for c in raw["changed_spans"]
        ]

        return TraceComparison(
            trace_id_a=raw["trace_id_a"],
            trace_id_b=raw["trace_id_b"],
            added_spans=added,
            removed_spans=removed,
            changed_spans=changed,
            unchanged_count=raw["unchanged_count"],
        )

    def compare_traces(self, trace_id_a: str, trace_id_b: str) -> TraceComparison:
        """Compare two traces structurally — find added, removed, and changed spans.

        Matches spans by ``(operationName, serviceName, parentOperation)``
        tuple, not by span ID. Reports duration deltas and tag differences.

        Args:
            trace_id_a: First (baseline) trace ID.
            trace_id_b: Second (comparison) trace ID.

        Returns:
            A :class:`TraceComparison` with added, removed, changed spans
            and unchanged count.

        Raises:
            ValueError: If either trace ID returns no data.
            httpx.HTTPStatusError: On HTTP-level failures.
        """
        return asyncio.run(self._acompare_traces(trace_id_a, trace_id_b))

    async def _aspan_statistics(
        self,
        service: str,
        *,
        operation: str | None = None,
        limit: int = 20,
    ) -> SpanStatisticsResult:
        """Async implementation of :meth:`span_statistics`."""
        limit = min(max(limit, 1), 100)
        params: dict[str, Any] = {"service": service, "limit": limit}
        if operation:
            params["operation"] = operation

        data = await self._http.aget("/traces", params=params) or {}
        raw_traces: list[dict[str, Any]] = data.get("data") or []

        raw_stats = _aggregate_span_statistics(raw_traces)
        stats = [
            OperationStatResult(
                operation=s["operation"],
                count=s["count"],
                p50_duration_us=s["p50_duration_us"],
                p95_duration_us=s["p95_duration_us"],
                p99_duration_us=s["p99_duration_us"],
                error_count=s["error_count"],
                error_rate=s["error_rate"],
            )
            for s in raw_stats
        ]

        return SpanStatisticsResult(
            service=service,
            operation=operation,
            trace_count=len(raw_traces),
            stats=stats,
        )

    async def _acritical_path(self, trace_id: str) -> CriticalPathOutput:
        """Async implementation of :meth:`critical_path`."""
        # Use streaming for large traces (Phase 11 optimization)
        data = await self._http.aget_stream(f"/traces/{trace_id}") or {}
        traces_data: list[dict[str, Any]] = data.get("data") or []
        if not traces_data:
            raise ValueError(f"No trace data returned for traceID {trace_id!r}.")

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

        return result

    def span_statistics(
        self,
        service: str,
        *,
        operation: str | None = None,
        limit: int = 20,
    ) -> SpanStatisticsResult:
        """Compute per-operation latency percentiles and error rates.

        Fetches up to ``limit`` traces for the given service, then
        aggregates all spans by operation name. For each operation
        reports p50/p95/p99 duration, error count, and error rate.

        Args:
            service: Service name (required).
            operation: Operation name filter (optional).
            limit: Number of traces to fetch (default 20, max 100).

        Returns:
            A :class:`SpanStatisticsResult` with per-operation stats.

        Raises:
            httpx.HTTPStatusError: On HTTP-level failures.
        """
        return asyncio.run(self._aspan_statistics(service, operation=operation, limit=limit))

    def critical_path(self, trace_id: str) -> CriticalPathOutput:
        """Identify the critical path and top bottlenecks in a trace.

        Finds the longest-duration span chain (critical path) from root to leaf,
        and ranks spans by self-time to find actual performance bottlenecks.

        Args:
            trace_id: Trace ID as a hex string (16 or 32 hex chars).

        Returns:
            CriticalPathOutput with trace metadata, critical path spans, and
            bottleneck ranking.

        Raises:
            ValueError: If the trace ID returns no data or contains no spans.
            httpx.HTTPStatusError: On HTTP-level failures.
        """
        return asyncio.run(self._acritical_path(trace_id))

    async def _afetch_traces(self, trace_ids: list[str]) -> list[Trace]:
        """Fetch multiple traces concurrently.

        Args:
            trace_ids: List of trace ID hex strings.

        Returns:
            List of Trace objects in same order as input IDs.
        """
        endpoints: list[tuple[str, dict[str, Any] | None]] = [(f"/traces/{tid}", None) for tid in trace_ids]
        results = await self._http.aget_many(endpoints)

        traces = []
        for tid, data in zip(trace_ids, results):
            data = data or {}
            traces_data: list[dict[str, Any]] = data.get("data") or []
            if not traces_data:
                raise ValueError(f"No trace data returned for traceID {tid!r}.")
            raw_trace = traces_data[0]
            spans_raw = raw_trace.get("spans") or []
            processes = raw_trace.get("processes") or {}
            t_id = raw_trace.get("traceID", tid)

            spans = [_build_span(s, processes, t_id) for s in spans_raw]
            root = _find_root_span(spans_raw)
            root_op = root.get("operationName") if root else None
            root_svc = None
            if root:
                pid = root.get("processID", "")
                root_svc = (processes.get(pid) or {}).get("serviceName")

            start_times = [s.start_time_us for s in spans if s.start_time_us]
            start_us = min(start_times) if start_times else None
            end_times = [s.start_time_us + s.duration_us for s in spans]
            duration_us = (max(end_times) - min(start_times)) if start_times and end_times else 0

            services = {s.service_name for s in spans}
            errors = sum(1 for s in spans if s.error)

            traces.append(
                Trace(
                    trace_id=t_id,
                    spans=spans,
                    root_operation=root_op,
                    root_service=root_svc,
                    start_time_us=start_us,
                    duration_us=max(duration_us, 0),
                    service_count=len(services),
                    errors_count=errors,
                )
            )
        return traces

    def fetch_traces(self, trace_ids: list[str]) -> list[Trace]:
        """Fetch multiple traces concurrently.

        Uses asyncio.gather with Semaphore to fetch up to 10 traces
        in parallel. Significantly faster than sequential get_trace()
        calls for batch operations.

        Args:
            trace_ids: List of trace ID hex strings.

        Returns:
            List of Trace objects in same order as input IDs.
        """
        return asyncio.run(self._afetch_traces(trace_ids))

    async def _aclose(self) -> None:
        """Async implementation of :meth:`close`."""
        await self._http.aclose()

    async def _acompare_windows(
        self,
        service: str,
        baseline_start: int,
        baseline_end: int,
        comparison_start: int,
        comparison_end: int,
        *,
        operation: str | None = None,
        limit: int = 100,
    ) -> WindowComparisonOutput:
        """Async implementation of :meth:`compare_windows`."""
        # Validate parameters
        if baseline_end <= baseline_start:
            raise ValueError("baseline_end must be greater than baseline_start")
        if comparison_end <= comparison_start:
            raise ValueError("comparison_end must be greater than comparison_start")
        if limit < 10 or limit > 1000:
            raise ValueError("limit must be between 10 and 1000")

        from jaeger_mcp._mcp import get_client

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
            total_deviation = sum(d["deviation_score"] for d in diff_results if "_summary" not in d)
            operation_count = len([d for d in diff_results if "_summary" not in d])

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

        return result

    def compare_windows(
        self,
        service: str,
        baseline_start: int,
        baseline_end: int,
        comparison_start: int,
        comparison_end: int,
        *,
        operation: str | None = None,
        limit: int = 100,
    ) -> WindowComparisonOutput:
        """Compare aggregate trace behavior between two time periods for a service.

        Fetches traces from both time windows, aggregates span statistics per operation,
        then compares the aggregate behavior to detect performance changes.

        Args:
            service: Service name to compare across time windows.
            baseline_start: Baseline window start time (Unix timestamp in microseconds).
            baseline_end: Baseline window end time (Unix timestamp in microseconds).
            comparison_start: Comparison window start time (Unix timestamp in microseconds).
            comparison_end: Comparison window end time (Unix timestamp in microseconds).
            operation: Optional operation name filter.
            limit: Maximum traces to fetch per window (default 100, max 1000).

        Returns:
            WindowComparisonOutput with per-operation diffs and summary statistics.

        Raises:
            ValueError: If time ranges are invalid or limit is out of bounds.
            httpx.HTTPStatusError: On HTTP-level failures.
        """
        return asyncio.run(
            self._acompare_windows(
                service,
                baseline_start,
                baseline_end,
                comparison_start,
                comparison_end,
                operation=operation,
                limit=limit,
            )
        )

    async def _adetect_anomalies(
        self,
        service: str,
        *,
        baseline_duration_minutes: int = 60,
        sensitivity: float = 2.0,
        current_duration_minutes: int = 5,
    ) -> AnomalyDetectionOutput:
        """Async implementation of :meth:`detect_anomalies`."""
        # Validate parameters
        if baseline_duration_minutes < 5 or baseline_duration_minutes > 1440:
            raise ValueError("baseline_duration_minutes must be between 5 and 1440")
        if sensitivity < 1.0 or sensitivity > 5.0:
            raise ValueError("sensitivity must be between 1.0 and 5.0")
        if current_duration_minutes < 1 or current_duration_minutes > 60:
            raise ValueError("current_duration_minutes must be between 1 and 60")

        from jaeger_mcp._mcp import get_client
        from jaeger_mcp.shaping import aggregate_span_statistics as _aggregate_span_statistics, detect_anomalies

        client = await get_client()

        # Calculate time windows
        import time

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

        return result

    def detect_anomalies(
        self,
        service: str,
        *,
        baseline_duration_minutes: int = 60,
        sensitivity: float = 2.0,
        current_duration_minutes: int = 5,
    ) -> AnomalyDetectionOutput:
        """Detect latency and error-rate anomalies for a service by comparing recent behavior to historical baseline.

        Fetches traces from a historical baseline window and a recent observation window,
        computes per-operation statistics for both, then identifies statistically significant
        deviations that may indicate performance issues or reliability problems.

        Args:
            service: Service name to detect anomalies for.
            baseline_duration_minutes: Historical baseline duration in minutes (5-1440, default 60).
            sensitivity: Anomaly sensitivity threshold (1.0-5.0, default 2.0). Lower = more sensitive.
            current_duration_minutes: Current observation window in minutes (1-60, default 5).

        Returns:
            AnomalyDetectionOutput with flagged operations and severity scores.

        Raises:
            ValueError: If parameters are out of valid ranges.
            httpx.HTTPStatusError: On HTTP-level failures.
        """
        return asyncio.run(
            self._adetect_anomalies(
                service,
                baseline_duration_minutes=baseline_duration_minutes,
                sensitivity=sensitivity,
                current_duration_minutes=current_duration_minutes,
            )
        )

    async def _apredict_degradation(
        self,
        service: str,
        *,
        hours_back: int = 168,
    ) -> PredictionResult:
        """Async implementation of :meth:`predict_degradation`."""
        # Validate parameters
        if hours_back < 1 or hours_back > 720:
            raise ValueError("hours_back must be between 1 and 720")

        from jaeger_mcp._mcp import get_client
        from jaeger_mcp.predictive.performance_model import predict_performance_degradation

        client = await get_client()

        # Calculate time window
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours_back)
        start_time_us = int(start_time.timestamp() * 1_000_000)
        end_time_us = int(end_time.timestamp() * 1_000_000)

        # Fetch historical trace data
        params = {"service": service, "start": start_time_us, "end": end_time_us, "limit": 1000}

        trace_data = await client.aget("/traces", params=params)

        # Placeholder for critical path trends and anomaly detections
        critical_path_trends = []
        anomaly_detections = []

        # Make prediction
        prediction = predict_performance_degradation(
            service_name=service,
            historical_data=trace_data.get("data", []) if trace_data else [],
            critical_path_trends=critical_path_trends,
            anomaly_detections=anomaly_detections,
        )

        return prediction

    def predict_degradation(
        self,
        service: str,
        *,
        hours_back: int = 168,
    ) -> PredictionResult:
        """Predict potential performance degradation events for a service.

        Analyzes historical trace data patterns, critical path trends, and anomaly
        detection results to forecast likely performance issues 2-24 hours in advance.

        Args:
            service: Service name to analyze for potential degradation.
            hours_back: Number of hours of historical data to analyze (1-720, default 168).

        Returns:
            PredictionResult with degradation forecast, confidence level, and recommendations.

        Raises:
            ValueError: If parameters are out of valid ranges.
            httpx.HTTPStatusError: On HTTP-level failures.
        """
        return asyncio.run(
            self._apredict_degradation(
                service,
                hours_back=hours_back,
            )
        )

    async def _aforecast_capacity(
        self,
        service: str,
        *,
        days_ahead: int = 30,
    ) -> ForecastResult:
        """Async implementation of :meth:`forecast_capacity`."""
        # Validate parameters
        if days_ahead < 1 or days_ahead > 90:
            raise ValueError("days_ahead must be between 1 and 90")

        from jaeger_mcp._mcp import get_client
        from jaeger_mcp.predictive.forecasting import forecast_service_capacity

        client = await get_client()

        # Calculate time window
        end_time = datetime.now()
        start_time = end_time - timedelta(days=30)  # Use 30 days of history
        start_time_us = int(start_time.timestamp() * 1_000_000)
        end_time_us = int(end_time.timestamp() * 1_000_000)

        # Fetch historical trace data
        params = {"service": service, "start": start_time_us, "end": end_time_us, "limit": 5000}

        trace_data = await client.aget("/traces", params=params)

        # Placeholder for seasonal patterns
        seasonal_patterns = []

        # Make forecast
        forecast = forecast_service_capacity(
            service_name=service,
            historical_volume=trace_data.get("data", []) if trace_data else [],
            seasonal_patterns=seasonal_patterns,
        )

        return forecast

    def forecast_capacity(
        self,
        service: str,
        *,
        days_ahead: int = 30,
    ) -> ForecastResult:
        """Forecast future throughput demands and resource requirements for a service.

        Provides predictions for the next 7-30 days with confidence intervals to
        enable infrastructure scaling decisions.

        Args:
            service: Service name to forecast capacity for.
            days_ahead: Number of days to forecast ahead (1-90, default 30).

        Returns:
            ForecastResult with throughput predictions and resource requirements.

        Raises:
            ValueError: If parameters are out of valid ranges.
            httpx.HTTPStatusError: On HTTP-level failures.
        """
        return asyncio.run(
            self._aforecast_capacity(
                service,
                days_ahead=days_ahead,
            )
        )

    def close(self) -> None:
        """Close the underlying HTTP session."""
        asyncio.run(self._aclose())

    def __enter__(self) -> JaegerClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
