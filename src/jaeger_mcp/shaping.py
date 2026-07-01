"""Data-shaping helpers for Jaeger API responses.

Pure functions that convert raw Jaeger JSON dicts into typed output
schemas (:mod:`jaeger_mcp.models` TypedDicts). No I/O — these are
reused by both MCP tools (:mod:`jaeger_mcp.tools`) and the library
facade (:mod:`jaeger_mcp.facade`).
"""

from __future__ import annotations

from typing import Any

from jaeger_mcp.models import (
    BottleneckSpan,
    ChangedSpan,
    CompareTracesOutput,
    CriticalPathSpan,
    ExecutionNode,
    MatchedSpanSummary,
    OperationStats,
    SpanDetail,
    TraceSummary,
)

_LIST_CAP = 500
_MD_ITEM_LIMIT = 20


def truncation_hint(total: int, shown: int, noun: str) -> str:
    """Return a markdown truncation hint when items are capped."""
    return f"\n\n_Showing first {shown} of {total} {noun} — see the structured content for the full list._"


def span_is_error(span: dict[str, Any]) -> bool:
    """Return True if a Jaeger span has error=true in its tags."""
    for tag in span.get("tags") or []:
        if tag.get("key") == "error" and str(tag.get("value", "")).lower() in ("true", "1"):
            return True
    return False


def span_tags_flat(span: dict[str, Any]) -> dict[str, str]:
    """Convert Jaeger tag list to a flat {key: str(value)} dict."""
    result: dict[str, str] = {}
    for tag in span.get("tags") or []:
        k = tag.get("key")
        v = tag.get("value")
        if k:
            result[k] = str(v) if v is not None else ""
    return result


def find_root_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
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


def build_execution_tree(spans: list[dict[str, Any]], processes: dict[str, Any]) -> list[ExecutionNode]:
    """Build a flat execution tree list (each node knows its children)."""
    span_map: dict[str, dict[str, Any]] = {s["spanID"]: s for s in spans if s.get("spanID")}
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
            "is_error": span_is_error(span),
            "children": children_map.get(span_id, []),
        }
        nodes.append(node)
    return nodes


def shape_trace_summary(trace: dict[str, Any]) -> TraceSummary:
    """Convert a Jaeger trace dict into a :class:`TraceSummary`."""
    spans: list[dict[str, Any]] = trace.get("spans") or []
    processes: dict[str, Any] = trace.get("processes") or {}

    root = find_root_span(spans)
    root_op: str | None = root.get("operationName") if root else None
    root_service: str | None = None
    if root:
        pid = root.get("processID", "")
        root_service = (processes.get(pid) or {}).get("serviceName")

    start_times = [s.get("startTime", 0) for s in spans if s.get("startTime")]
    start_time_us: int | None = min(start_times) if start_times else None

    end_times = [(s.get("startTime", 0) + s.get("duration", 0)) for s in spans]
    duration_us = (max(end_times) - min(start_times)) if start_times and end_times else 0

    service_ids = set(s.get("processID") for s in spans if s.get("processID"))
    errors_count = sum(1 for s in spans if span_is_error(s))

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


def shape_span_detail(span: dict[str, Any], processes: dict[str, Any]) -> SpanDetail:
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
        "is_error": span_is_error(span),
        "parent_span_id": parent_id,
        "tags": span_tags_flat(span),
    }


# ── Trace comparison ──────────────────────────────────────────────────────


def span_match_key(
    span: dict[str, Any],
    processes: dict[str, Any],
    span_map: dict[str, dict[str, Any]],
) -> tuple[str, str, str | None]:
    """Return the structural identity key for a span.

    The key is ``(operationName, serviceName, parentOperation)`` — the
    ``parentOperation`` is the operation name of the parent span (resolved
    from ``CHILD_OF`` references), or ``None`` for root spans.

    Args:
        span: Raw Jaeger span dict.
        processes: Jaeger processes map (processID → {serviceName, …}).
        span_map: Mapping of spanID → span dict for parent resolution.

    Returns:
        A 3-tuple suitable as a dict key for span matching.
    """
    operation_name: str = span.get("operationName", "")
    pid = span.get("processID", "")
    service_name: str = (processes.get(pid) or {}).get("serviceName", pid)

    parent_operation: str | None = None
    for ref in span.get("references") or []:
        if ref.get("refType") == "CHILD_OF":
            parent_id = ref.get("spanID")
            if parent_id and parent_id in span_map:
                parent_operation = span_map[parent_id].get("operationName", "")
            break

    return (operation_name, service_name, parent_operation)


def compare_traces_diff(
    trace_a: dict[str, Any],
    trace_b: dict[str, Any],
) -> CompareTracesOutput:
    """Structurally diff two raw Jaeger traces.

    Matches spans by ``(operationName, serviceName, parentOperation)``
    tuple (not span IDs).  Duplicate keys (e.g. loop calls) are paired
    by position order.

    Args:
        trace_a: Raw Jaeger trace dict (baseline).
        trace_b: Raw Jaeger trace dict (comparison).

    Returns:
        A :class:`CompareTracesOutput` with added, removed, changed spans
        and unchanged count.
    """
    spans_a: list[dict[str, Any]] = trace_a.get("spans") or []
    spans_b: list[dict[str, Any]] = trace_b.get("spans") or []
    procs_a: dict[str, Any] = trace_a.get("processes") or {}
    procs_b: dict[str, Any] = trace_b.get("processes") or {}

    span_map_a: dict[str, dict[str, Any]] = {s["spanID"]: s for s in spans_a if s.get("spanID")}
    span_map_b: dict[str, dict[str, Any]] = {s["spanID"]: s for s in spans_b if s.get("spanID")}

    # Group spans by match key, preserving order for duplicate handling.
    def _group_by_key(
        spans: list[dict[str, Any]],
        processes: dict[str, Any],
        smap: dict[str, dict[str, Any]],
    ) -> dict[tuple[str, str, str | None], list[dict[str, Any]]]:
        groups: dict[tuple[str, str, str | None], list[dict[str, Any]]] = {}
        for span in spans:
            key = span_match_key(span, processes, smap)
            groups.setdefault(key, []).append(span)
        return groups

    groups_a = _group_by_key(spans_a, procs_a, span_map_a)
    groups_b = _group_by_key(spans_b, procs_b, span_map_b)

    all_keys = set(groups_a) | set(groups_b)

    added_spans: list[MatchedSpanSummary] = []
    removed_spans: list[MatchedSpanSummary] = []
    changed_spans: list[ChangedSpan] = []
    unchanged_count = 0

    def _make_summary(key: tuple[str, str, str | None]) -> MatchedSpanSummary:
        return {
            "operation_name": key[0],
            "service": key[1],
            "parent_operation": key[2],
        }

    for key in sorted(all_keys):
        list_a = groups_a.get(key, [])
        list_b = groups_b.get(key, [])

        paired = min(len(list_a), len(list_b))

        # Pair by position order (D-02)
        for i in range(paired):
            sa = list_a[i]
            sb = list_b[i]

            dur_a = sa.get("duration", 0)
            dur_b = sb.get("duration", 0)
            tags_a = span_tags_flat(sa)
            tags_b = span_tags_flat(sb)

            t_added = {k: tags_b[k] for k in tags_b if k not in tags_a}
            t_removed = {k: tags_a[k] for k in tags_a if k not in tags_b}
            t_changed = {k: tags_b[k] for k in tags_a if k in tags_b and tags_a[k] != tags_b[k]}

            if dur_a != dur_b or t_added or t_removed or t_changed:
                changed_spans.append(
                    {
                        "operation_name": key[0],
                        "service": key[1],
                        "parent_operation": key[2],
                        "duration_a_us": dur_a,
                        "duration_b_us": dur_b,
                        "duration_delta_us": dur_b - dur_a,
                        "tags_added": t_added,
                        "tags_removed": t_removed,
                        "tags_changed": t_changed,
                    }
                )
            else:
                unchanged_count += 1

        # Extras in A → removed
        for _ in range(paired, len(list_a)):
            removed_spans.append(_make_summary(key))

        # Extras in B → added
        for _ in range(paired, len(list_b)):
            added_spans.append(_make_summary(key))

    return {
        "trace_id_a": trace_a.get("traceID", ""),
        "trace_id_b": trace_b.get("traceID", ""),
        "added_spans": added_spans,
        "removed_spans": removed_spans,
        "changed_spans": changed_spans,
        "unchanged_count": unchanged_count,
    }


# ── Span statistics ───────────────────────────────────────────────────────


def compute_percentile(sorted_values: list[int], percentile: float) -> int:
    """Compute a percentile using linear interpolation.

    Args:
        sorted_values: Pre-sorted list of integer values (ascending).
        percentile: Percentile to compute (0-100).

    Returns:
        Interpolated percentile value as int.
    """
    if not sorted_values:
        return 0
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    # Linear interpolation (matches numpy default)
    rank = (percentile / 100.0) * (n - 1)
    low = int(rank)
    high = min(low + 1, n - 1)
    frac = rank - low
    return int(sorted_values[low] + frac * (sorted_values[high] - sorted_values[low]))


def aggregate_span_statistics(traces: list[dict[str, Any]]) -> list[OperationStats]:
    """Aggregate per-operation latency and error stats across traces.

    Args:
        traces: List of raw Jaeger trace dicts (each containing 'spans').

    Returns:
        List of :class:`OperationStats`, one per distinct operation,
        sorted alphabetically by operation name.
    """
    # Collect durations and error flags per operation
    ops: dict[str, dict[str, Any]] = {}
    for trace in traces:
        for span in trace.get("spans") or []:
            op = span.get("operationName", "")
            if op not in ops:
                ops[op] = {"durations": [], "error_count": 0}
            ops[op]["durations"].append(span.get("duration", 0))
            if span_is_error(span):
                ops[op]["error_count"] += 1

    stats: list[OperationStats] = []
    for op in sorted(ops):
        data = ops[op]
        durations = sorted(data["durations"])
        count = len(durations)
        error_count = data["error_count"]
        stats.append(
            {
                "operation": op,
                "count": count,
                "p50_duration_us": compute_percentile(durations, 50),
                "p95_duration_us": compute_percentile(durations, 95),
                "p99_duration_us": compute_percentile(durations, 99),
                "error_count": error_count,
                "error_rate": error_count / count if count else 0.0,
            }
        )
    return stats


# ── Batch Window Comparison ──────────────────────────────────────────────


def compute_deviation_score(baseline_stat: dict, comparison_stat: dict) -> float:
    """Compute a normalized deviation score between two operation stats.

    Combines relative changes in count, latency percentiles, and error rate
    into a single score indicating overall behavioral change magnitude.

    Returns:
        Normalized score between 0 (identical) and 1+ (highly different).
    """
    # Handle edge cases
    if baseline_stat["count"] == 0 and comparison_stat["count"] == 0:
        return 0.0

    score = 0.0

    # Count change contribution (normalized by max count)
    max_count = max(baseline_stat["count"], comparison_stat["count"], 1)
    count_change = abs(comparison_stat["count"] - baseline_stat["count"]) / max_count
    score += count_change * 0.3  # Weight: 30%

    # Latency change contribution (normalized by baseline p95)
    baseline_p95 = baseline_stat["p95_duration_us"]
    if baseline_p95 > 0:
        p95_change = abs(comparison_stat["p95_duration_us"] - baseline_p95) / baseline_p95
        score += p95_change * 0.5  # Weight: 50%

    # Error rate change contribution
    error_change = abs(comparison_stat["error_rate"] - baseline_stat["error_rate"])
    score += error_change * 0.2  # Weight: 20%

    return min(score, 10.0)  # Cap at 10.0 to prevent extreme outliers


def compare_windows(baseline_stats: list[dict], comparison_stats: list[dict]) -> list[dict]:
    """Compare operation statistics between two time windows.

    Args:
        baseline_stats: Aggregated stats from baseline time window.
        comparison_stats: Aggregated stats from comparison time window.

    Returns:
        List of OperationDiff dictionaries with change analysis.
    """
    # Create lookup dictionaries for efficient matching
    baseline_dict = {stat["operation"]: stat for stat in baseline_stats}
    comparison_dict = {stat["operation"]: stat for stat in comparison_stats}

    # Get all unique operations
    all_operations = set(baseline_dict.keys()) | set(comparison_dict.keys())

    results = []
    added_count = 0
    removed_count = 0
    faster_count = 0
    slower_count = 0
    total_deviation = 0.0

    for operation in sorted(all_operations):
        baseline_stat = baseline_dict.get(operation)
        comparison_stat = comparison_dict.get(operation)

        # Determine change type
        if baseline_stat is None:
            change_type = "added"
            added_count += 1
        elif comparison_stat is None:
            change_type = "removed"
            removed_count += 1
        else:
            # Both exist - compare latencies
            baseline_p95 = baseline_stat["p95_duration_us"]
            comparison_p95 = comparison_stat["p95_duration_us"]

            if comparison_p95 < baseline_p95 * 0.95:  # 5% improvement threshold
                change_type = "faster"
                faster_count += 1
            elif comparison_p95 > baseline_p95 * 1.05:  # 5% degradation threshold
                change_type = "slower"
                slower_count += 1
            else:
                change_type = "unchanged"

        # Create diff entry
        if baseline_stat and comparison_stat:
            # Both windows have data
            count_delta = comparison_stat["count"] - baseline_stat["count"]
            p50_delta_us = comparison_stat["p50_duration_us"] - baseline_stat["p50_duration_us"]
            p50_delta_pct = (
                (p50_delta_us / baseline_stat["p50_duration_us"] * 100) if baseline_stat["p50_duration_us"] > 0 else 0
            )
            p95_delta_us = comparison_stat["p95_duration_us"] - baseline_stat["p95_duration_us"]
            p95_delta_pct = (
                (p95_delta_us / baseline_stat["p95_duration_us"] * 100) if baseline_stat["p95_duration_us"] > 0 else 0
            )
            error_rate_delta = comparison_stat["error_rate"] - baseline_stat["error_rate"]

            deviation_score = compute_deviation_score(baseline_stat, comparison_stat)
            total_deviation += deviation_score

            diff = {
                "operation": operation,
                "baseline_count": baseline_stat["count"],
                "comparison_count": comparison_stat["count"],
                "count_delta": count_delta,
                "baseline_p50_us": baseline_stat["p50_duration_us"],
                "comparison_p50_us": comparison_stat["p50_duration_us"],
                "p50_delta_us": p50_delta_us,
                "p50_delta_pct": round(p50_delta_pct, 1),
                "baseline_p95_us": baseline_stat["p95_duration_us"],
                "comparison_p95_us": comparison_stat["p95_duration_us"],
                "p95_delta_us": p95_delta_us,
                "p95_delta_pct": round(p95_delta_pct, 1),
                "baseline_error_rate": round(baseline_stat["error_rate"], 4),
                "comparison_error_rate": round(comparison_stat["error_rate"], 4),
                "error_rate_delta": round(error_rate_delta, 4),
                "change_type": change_type,
                "deviation_score": round(deviation_score, 3),
            }
        elif baseline_stat:
            # Only baseline has data (removed)
            diff = {
                "operation": operation,
                "baseline_count": baseline_stat["count"],
                "comparison_count": 0,
                "count_delta": -baseline_stat["count"],
                "baseline_p50_us": baseline_stat["p50_duration_us"],
                "comparison_p50_us": 0,
                "p50_delta_us": -baseline_stat["p50_duration_us"],
                "p50_delta_pct": -100.0,
                "baseline_p95_us": baseline_stat["p95_duration_us"],
                "comparison_p95_us": 0,
                "p95_delta_us": -baseline_stat["p95_duration_us"],
                "p95_delta_pct": -100.0,
                "baseline_error_rate": round(baseline_stat["error_rate"], 4),
                "comparison_error_rate": 0.0,
                "error_rate_delta": -round(baseline_stat["error_rate"], 4),
                "change_type": change_type,
                "deviation_score": 1.0,  # High deviation for removed operations
            }
            total_deviation += 1.0
        else:
            # Only comparison has data (added)
            diff = {
                "operation": operation,
                "baseline_count": 0,
                "comparison_count": comparison_stat["count"] if comparison_stat else 0,
                "count_delta": comparison_stat["count"] if comparison_stat else 0,
                "baseline_p50_us": 0,
                "comparison_p50_us": comparison_stat["p50_duration_us"] if comparison_stat else 0,
                "p50_delta_us": comparison_stat["p50_duration_us"] if comparison_stat else 0,
                "p50_delta_pct": 100.0 if comparison_stat else 0.0,
                "baseline_p95_us": 0,
                "comparison_p95_us": comparison_stat["p95_duration_us"] if comparison_stat else 0,
                "p95_delta_us": comparison_stat["p95_duration_us"] if comparison_stat else 0,
                "p95_delta_pct": 100.0 if comparison_stat else 0.0,
                "baseline_error_rate": 0.0,
                "comparison_error_rate": round(comparison_stat["error_rate"], 4) if comparison_stat else 0.0,
                "error_rate_delta": round(comparison_stat["error_rate"], 4) if comparison_stat else 0.0,
                "change_type": change_type,
                "deviation_score": 1.0,  # High deviation for added operations
            }
            total_deviation += 1.0

        results.append(diff)

    # Sort by deviation score descending (most changed first)
    results.sort(key=lambda x: x["deviation_score"], reverse=True)

    # Add summary counts to results metadata (will be extracted in tool)
    results.append(
        {
            "_summary": {
                "added_count": added_count,
                "removed_count": removed_count,
                "faster_count": faster_count,
                "slower_count": slower_count,
                "total_deviation": total_deviation,
                "operation_count": len([r for r in results if "_summary" not in r]),
            }
        }
    )

    return results


# ── Critical Path Analysis ───────────────────────────────────────────────


def _build_span_tree(spans: list[dict]) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Build parent-child relationships from spans.

    Returns:
        Tuple of (span_lookup_by_id, children_lookup_by_parent_id).
    """
    span_dict = {span["spanID"]: span for span in spans}
    children = {}

    for span in spans:
        span_id = span["spanID"]
        # Initialize empty children list for all spans
        if span_id not in children:
            children[span_id] = []

        # Find parent from references
        parent_id = None
        for ref in span.get("references", []):
            if ref.get("refType") == "CHILD_OF":
                parent_id = ref.get("spanID")
                break

        if parent_id:
            if parent_id not in children:
                children[parent_id] = []
            children[parent_id].append(span_id)

    return span_dict, children


def find_critical_path(spans: list[dict]) -> list[dict]:
    """Find the longest-duration path from root to leaf in the span tree.

    Uses dynamic programming to compute the longest path:
    - For each node, compute max cumulative duration of paths starting from that node
    - Track the next node in the optimal path for reconstruction

    Returns:
        List of spans in critical path order (root to leaf).
    """
    if not spans:
        return []

    span_dict, children = _build_span_tree(spans)
    root_span = find_root_span(spans)
    if not root_span:
        return []

    # Memoization dictionaries
    max_duration_from = {}  # span_id -> max cumulative duration from this node
    next_in_path = {}  # span_id -> next span_id in optimal path

    def compute_max_duration(span_id: str) -> int:
        """Recursive helper with memoization."""
        if span_id in max_duration_from:
            return max_duration_from[span_id]

        span = span_dict[span_id]
        duration = span.get("duration", 0)

        # Base case: leaf node
        if not children.get(span_id):
            max_duration_from[span_id] = duration
            return duration

        # Recursive case: max of children + own duration
        max_child_duration = 0
        next_best_child = None

        for child_id in children[span_id]:
            child_duration = compute_max_duration(child_id)
            if child_duration > max_child_duration:
                max_child_duration = child_duration
                next_best_child = child_id

        max_duration_from[span_id] = duration + max_child_duration
        if next_best_child:
            next_in_path[span_id] = next_best_child

        return max_duration_from[span_id]

    # Compute max durations starting from root
    compute_max_duration(root_span["spanID"])

    # Reconstruct path
    path = []
    current_id = root_span["spanID"]
    while current_id:
        path.append(span_dict[current_id])
        current_id = next_in_path.get(current_id)

    return path


def rank_bottlenecks(spans: list[dict], limit: int = 50) -> list[dict]:
    """Rank spans by self-time (duration - sum of child durations).

    Args:
        spans: List of all spans in the trace.
        limit: Maximum number of bottlenecks to return.

    Returns:
        List of spans sorted by self-time descending, limited to `limit`.
    """
    if not spans:
        return []

    span_dict, children = _build_span_tree(spans)

    # Calculate self-time for each span
    spans_with_self_time = []
    for span in spans:
        span_id = span["spanID"]
        duration = span.get("duration", 0)

        # Sum child durations
        child_duration_sum = 0
        for child_id in children.get(span_id, []):
            child_span = span_dict.get(child_id, {})
            child_duration_sum += child_span.get("duration", 0)

        self_time = duration - child_duration_sum
        if self_time > 0:  # Only include spans with positive self-time
            spans_with_self_time.append((span, self_time))

    # Sort by self-time descending and limit
    spans_with_self_time.sort(key=lambda x: x[1], reverse=True)
    return [span for span, _ in spans_with_self_time[:limit]]


def _format_critical_path_span(
    span: dict, cumulative_duration: int, total_duration: int, processes: dict
) -> CriticalPathSpan:
    """Convert a span to CriticalPathSpan format."""
    process_id = span.get("processID", "")
    service = (processes.get(process_id, {}) or {}).get("serviceName", "unknown")

    duration = span.get("duration", 0)
    percentage = (duration / total_duration * 100) if total_duration > 0 else 0

    return {
        "span_id": span["spanID"],
        "operation": span.get("operationName", "unknown"),
        "service": service,
        "duration_us": duration,
        "cumulative_duration_us": cumulative_duration,
        "percentage_of_total": round(percentage, 1),
    }


def _format_bottleneck_span(span: dict, self_time: int, total_duration: int, processes: dict) -> BottleneckSpan:
    """Convert a span to BottleneckSpan format."""
    process_id = span.get("processID", "")
    service = (processes.get(process_id, {}) or {}).get("serviceName", "unknown")

    duration = span.get("duration", 0)
    self_percentage = (self_time / total_duration * 100) if total_duration > 0 else 0

    return {
        "span_id": span["spanID"],
        "operation": span.get("operationName", "unknown"),
        "service": service,
        "duration_us": duration,
        "self_time_us": self_time,
        "self_time_percentage": round(self_percentage, 1),
    }


# ── Anomaly Detection ────────────────────────────────────────────────────


def compute_z_score(current_value: float, baseline_mean: float, baseline_std: float) -> float:
    """Compute z-score for anomaly detection.

    Z-score represents how many standard deviations an element is from the mean.
    Higher absolute values indicate more significant deviations.

    Returns:
        Z-score (positive = above baseline, negative = below baseline).
        Returns 0.0 if baseline_std is 0 (no variance).
    """
    if baseline_std == 0:
        return 0.0
    return (current_value - baseline_mean) / baseline_std


def detect_anomalies(
    current_stats: list[OperationStats], baseline_stats: list[OperationStats], sensitivity: float = 2.0
) -> list[dict]:
    """Detect anomalies by comparing current stats to baseline stats.

    Args:
        current_stats: Recent operation statistics.
        baseline_stats: Historical baseline statistics.
        sensitivity: Sigma threshold for anomaly detection (default 2.0).

    Returns:
        List of OperationAnomaly dictionaries with flagged operations.
    """
    # Create lookup dictionaries for efficient matching
    baseline_dict = {stat["operation"]: stat for stat in baseline_stats}
    current_dict = {stat["operation"]: stat for stat in current_stats}

    # Get all operations that exist in both periods
    common_operations = set(baseline_dict.keys()) & set(current_dict.keys())

    anomalies = []
    latency_count = 0
    error_rate_count = 0

    for operation in sorted(common_operations):
        baseline_stat = baseline_dict[operation]
        current_stat = current_dict[operation]

        # Skip operations with insufficient data
        if baseline_stat["count"] < 5 or current_stat["count"] < 5:
            continue

        # Latency anomaly detection (p95 and p99)
        # For simplicity, we'll use a simple comparison approach rather than true statistical modeling
        # In a real implementation, we'd collect multiple baseline samples to compute mean/std
        baseline_p95 = baseline_stat["p95_duration_us"]
        current_p95 = current_stat["p95_duration_us"]
        baseline_p99 = baseline_stat["p99_duration_us"]
        current_p99 = current_stat["p99_duration_us"]

        # Simple ratio-based detection for latency
        if baseline_p95 > 0:
            p95_ratio = current_p95 / baseline_p95
            if p95_ratio > 1.5:  # 50% increase
                z_score = compute_z_score(
                    float(current_p95), float(baseline_p95), float(baseline_p95 * 0.2)
                )  # Assume 20% std
                severity = "critical" if p95_ratio > 2.0 else "high" if p95_ratio > 1.75 else "medium"

                anomalies.append(
                    {
                        "operation": operation,
                        "anomaly_type": "latency",
                        "baseline_stat": "p95_duration_us",
                        "baseline_value": float(baseline_p95),
                        "current_value": float(current_p95),
                        "z_score": round(z_score, 2),
                        "severity": severity,
                        "trace_count": current_stat["count"],
                    }
                )
                latency_count += 1

        if baseline_p99 > 0:
            p99_ratio = current_p99 / baseline_p99
            if p99_ratio > 1.5:  # 50% increase
                z_score = compute_z_score(
                    float(current_p99), float(baseline_p99), float(baseline_p99 * 0.2)
                )  # Assume 20% std
                severity = "critical" if p99_ratio > 2.0 else "high" if p99_ratio > 1.75 else "medium"

                anomalies.append(
                    {
                        "operation": operation,
                        "anomaly_type": "latency",
                        "baseline_stat": "p99_duration_us",
                        "baseline_value": float(baseline_p99),
                        "current_value": float(current_p99),
                        "z_score": round(z_score, 2),
                        "severity": severity,
                        "trace_count": current_stat["count"],
                    }
                )
                latency_count += 1

        # Error rate anomaly detection
        baseline_error_rate = baseline_stat["error_rate"]
        current_error_rate = current_stat["error_rate"]

        # Simple threshold-based detection for error rates
        if baseline_error_rate > 0:
            error_ratio = current_error_rate / baseline_error_rate
            if error_ratio > 2.0:  # 100% increase
                z_score = compute_z_score(
                    current_error_rate, baseline_error_rate, baseline_error_rate * 0.5
                )  # Assume 50% std
                severity = "critical" if error_ratio > 5.0 else "high" if error_ratio > 3.0 else "medium"

                anomalies.append(
                    {
                        "operation": operation,
                        "anomaly_type": "error_rate",
                        "baseline_stat": "error_rate",
                        "baseline_value": round(baseline_error_rate, 4),
                        "current_value": round(current_error_rate, 4),
                        "z_score": round(z_score, 2),
                        "severity": severity,
                        "trace_count": current_stat["count"],
                    }
                )
                error_rate_count += 1
        elif current_error_rate > 0.01:  # New errors appearing (more than 1%)
            anomalies.append(
                {
                    "operation": operation,
                    "anomaly_type": "error_rate",
                    "baseline_stat": "error_rate",
                    "baseline_value": 0.0,
                    "current_value": round(current_error_rate, 4),
                    "z_score": 5.0,  # High z-score for new errors
                    "severity": "high" if current_error_rate > 0.05 else "medium",
                    "trace_count": current_stat["count"],
                }
            )
            error_rate_count += 1

    # Sort by severity and z-score descending
    anomalies.sort(key=lambda x: (x["severity"], abs(x["z_score"])), reverse=True)

    # Add summary to results
    anomalies.append(
        {
            "_summary": {
                "total_anomalies": len([a for a in anomalies if "_summary" not in a]),
                "latency_anomalies": latency_count,
                "error_rate_anomalies": error_rate_count,
            }
        }
    )

    return anomalies


# ── Backward-compatible aliases (underscore-prefixed) ─────────────────
# These allow existing imports from tools.py and facade.py to keep working
# during transition. Prefer the public names above for new code.

_truncation_hint = truncation_hint
_span_is_error = span_is_error
_span_tags_flat = span_tags_flat
_find_root_span = find_root_span
_build_execution_tree = build_execution_tree
_shape_trace_summary = shape_trace_summary
_shape_span_detail = shape_span_detail
_span_match_key = span_match_key
_compare_traces_diff = compare_traces_diff
_compute_percentile = compute_percentile
_aggregate_span_statistics = aggregate_span_statistics
