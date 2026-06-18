"""TypedDict output schemas for every MCP tool.

These schemas are read by FastMCP (``structured_output=True``) to generate
a JSON-Schema ``outputSchema`` for each tool. Clients that support
structured data use that schema to validate the ``structuredContent``
payload; clients that don't use the markdown ``content`` block instead.

**Python / Pydantic compat note.** We deliberately avoid
``Required`` / ``NotRequired`` qualifiers: Pydantic 2.13+ mishandles them
during runtime schema generation on Py < 3.12 (see
https://errors.pydantic.dev/2.13/u/typed-dict-version). Optional fields use
``| None`` convention; the code always sets the key (``None`` when absent).
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 12):
    from typing import TypedDict
else:
    from typing_extensions import TypedDict


# ── Services ─────────────────────────────────────────────────────────────────


class ServicesOutput(TypedDict):
    services_count: int
    truncated: bool
    services: list[str]


# ── Operations ───────────────────────────────────────────────────────────────


class OperationsOutput(TypedDict):
    service: str
    operations_count: int
    truncated: bool
    operations: list[str]


# ── Trace search ─────────────────────────────────────────────────────────────


class TraceSummary(TypedDict):
    trace_id: str
    root_operation: str | None
    root_service: str | None
    start_time_us: int | None
    duration_us: int
    span_count: int
    service_count: int
    errors_count: int


class SearchTracesOutput(TypedDict):
    service: str
    operation: str | None
    returned: int
    truncated: bool
    traces: list[TraceSummary]


# ── Trace detail ─────────────────────────────────────────────────────────────


class SpanRef(TypedDict):
    trace_id: str
    span_id: str
    ref_type: str


class SpanDetail(TypedDict):
    span_id: str
    operation_name: str
    service: str
    start_time_us: int
    duration_us: int
    is_error: bool
    parent_span_id: str | None
    tags: dict[str, str]


class ServiceSpanStats(TypedDict):
    service: str
    span_count: int
    total_duration_us: int
    error_count: int


class ExecutionNode(TypedDict):
    span_id: str
    operation: str
    service: str
    duration_us: int
    is_error: bool
    children: list[str]


class TraceDetailOutput(TypedDict):
    trace_id: str
    span_count: int
    service_count: int
    root_operation: str | None
    root_service: str | None
    start_time_us: int | None
    total_duration_us: int
    errors_count: int
    services: list[ServiceSpanStats]
    spans: list[SpanDetail]
    execution_tree: list[ExecutionNode]


# ── Dependencies ─────────────────────────────────────────────────────────────


class DependencyEdge(TypedDict):
    parent: str
    child: str
    call_count: int


class DependenciesOutput(TypedDict):
    end_ts_us: int
    lookback_hours: int
    edge_count: int
    edges: list[DependencyEdge]


# ── Trace comparison ─────────────────────────────────────────────────────


class MatchedSpanSummary(TypedDict):
    operation_name: str
    service: str
    parent_operation: str | None


class ChangedSpan(TypedDict):
    operation_name: str
    service: str
    parent_operation: str | None
    duration_a_us: int
    duration_b_us: int
    duration_delta_us: int
    tags_added: dict[str, str]
    tags_removed: dict[str, str]
    tags_changed: dict[str, str]


class CompareTracesOutput(TypedDict):
    trace_id_a: str
    trace_id_b: str
    added_spans: list[MatchedSpanSummary]
    removed_spans: list[MatchedSpanSummary]
    changed_spans: list[ChangedSpan]
    unchanged_count: int


# ── Span statistics ──────────────────────────────────────────────────────


class OperationStats(TypedDict):
    operation: str
    count: int
    p50_duration_us: int
    p95_duration_us: int
    p99_duration_us: int
    error_count: int
    error_rate: float


class SpanStatisticsOutput(TypedDict):
    service: str
    operation: str | None
    trace_count: int
    stats: list[OperationStats]


# ── Batch Window Comparison ──────────────────────────────────────────────────


class OperationDiff(TypedDict):
    """Difference in behavior for a single operation between two time windows."""

    operation: str
    baseline_count: int
    comparison_count: int
    count_delta: int
    baseline_p50_us: int
    comparison_p50_us: int
    p50_delta_us: int
    p50_delta_pct: float
    baseline_p95_us: int
    comparison_p95_us: int
    p95_delta_us: int
    p95_delta_pct: float
    baseline_error_rate: float
    comparison_error_rate: float
    error_rate_delta: float
    change_type: str  # "added", "removed", "faster", "slower", "unchanged"
    deviation_score: float


class WindowComparisonOutput(TypedDict):
    """Comparison of aggregate trace behavior between two time windows."""

    service: str
    baseline_start: int
    baseline_end: int
    comparison_start: int
    comparison_end: int
    operations: list[OperationDiff]
    total_operations: int
    added_count: int
    removed_count: int
    faster_count: int
    slower_count: int
    overall_deviation_score: float


# ── Critical Path Analysis ───────────────────────────────────────────────────


class CriticalPathSpan(TypedDict):
    """A span on the critical path with timing information."""

    span_id: str
    operation: str
    service: str
    duration_us: int
    cumulative_duration_us: int
    percentage_of_total: float


class BottleneckSpan(TypedDict):
    """A span ranked by self-time (bottleneck)."""

    span_id: str
    operation: str
    service: str
    duration_us: int
    self_time_us: int
    self_time_percentage: float


class CriticalPathOutput(TypedDict):
    """Critical path analysis result."""

    trace_id: str
    root_operation: str | None
    total_duration_us: int
    critical_path: list[CriticalPathSpan]
    critical_path_duration_us: int
    critical_path_percentage: float
    bottlenecks: list[BottleneckSpan]
    bottleneck_count: int


# ── Anomaly Detection ────────────────────────────────────────────────────────


class OperationAnomaly(TypedDict):
    """An operation flagged as anomalous with details."""

    operation: str
    anomaly_type: str  # "latency" or "error_rate"
    baseline_stat: str  # "p95_duration_us" or "error_rate"
    baseline_value: float
    current_value: float
    z_score: float
    severity: str  # "low", "medium", "high", "critical"
    trace_count: int


class AnomalyDetectionOutput(TypedDict):
    """Results of anomaly detection for a service."""

    service: str
    baseline_start: int
    baseline_end: int
    current_start: int
    current_end: int
    anomalies: list[OperationAnomaly]
    total_anomalies: int
    latency_anomalies: int
    error_rate_anomalies: int
    sensitivity: float
