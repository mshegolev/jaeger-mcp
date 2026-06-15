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

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from jaeger_mcp.client import JaegerHTTPClient
from jaeger_mcp.shaping import (
    compare_traces_diff as _compare_traces_diff,
    find_root_span as _find_root_span,
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

    def get_trace(self, trace_id: str) -> Trace:
        """Retrieve a full trace by ID.

        Returns:
            A :class:`Trace` whose :attr:`~Trace.spans` carry all
            Evidence-required fields (``start_utc``, ``error``,
            ``service_name``, ``tags``, etc.).

        Raises:
            ValueError: If the trace ID returns no data.
            requests.HTTPError: On HTTP-level failures.
        """
        data = self._http.get(f"/traces/{trace_id}") or {}
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
        """
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

        data = self._http.get("/traces", params=params) or {}
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

    def list_services(self) -> list[str]:
        """Return all service names known to Jaeger."""
        data = self._http.get("/services") or {}
        raw: list[str] = data.get("data") or []
        return sorted(raw)

    def get_dependencies(self, *, lookback_hours: int = 24) -> list[ServiceDep]:
        """Return the service-to-service call graph.

        Args:
            lookback_hours: How far back to look (default 24h).

        Returns:
            List of :class:`ServiceDep` edges sorted by call count (descending).
        """
        end_ts_us = int(time.time() * 1_000_000)
        lookback_ms = lookback_hours * 3600 * 1000
        params: dict[str, Any] = {
            "endTs": end_ts_us // 1000,
            "lookback": lookback_ms,
        }
        data = self._http.get("/dependencies", params=params) or {}
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
            requests.HTTPError: On HTTP-level failures.
        """
        data_a = self._http.get(f"/traces/{trace_id_a}") or {}
        traces_a: list[dict[str, Any]] = data_a.get("data") or []
        if not traces_a:
            raise ValueError(f"No trace data returned for trace_id_a {trace_id_a!r}.")

        data_b = self._http.get(f"/traces/{trace_id_b}") or {}
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

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._http.close()

    def __enter__(self) -> JaegerClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
