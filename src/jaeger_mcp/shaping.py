"""Data-shaping helpers for Jaeger API responses.

Pure functions that convert raw Jaeger JSON dicts into typed output
schemas (:mod:`jaeger_mcp.models` TypedDicts). No I/O — these are
reused by both MCP tools (:mod:`jaeger_mcp.tools`) and the library
facade (:mod:`jaeger_mcp.facade`).
"""

from __future__ import annotations

from typing import Any

from jaeger_mcp.models import (
    ChangedSpan,
    CompareTracesOutput,
    ExecutionNode,
    MatchedSpanSummary,
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
        for i in range(paired, len(list_a)):
            removed_spans.append(_make_summary(key))

        # Extras in B → added
        for i in range(paired, len(list_b)):
            added_spans.append(_make_summary(key))

    return {
        "trace_id_a": trace_a.get("traceID", ""),
        "trace_id_b": trace_b.get("traceID", ""),
        "added_spans": added_spans,
        "removed_spans": removed_spans,
        "changed_spans": changed_spans,
        "unchanged_count": unchanged_count,
    }


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
