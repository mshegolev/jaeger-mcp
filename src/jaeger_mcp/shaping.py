"""Data-shaping helpers for Jaeger API responses.

Pure functions that convert raw Jaeger JSON dicts into typed output
schemas (:mod:`jaeger_mcp.models` TypedDicts). No I/O — these are
reused by both MCP tools (:mod:`jaeger_mcp.tools`) and the library
facade (:mod:`jaeger_mcp.facade`).
"""

from __future__ import annotations

from typing import Any

from jaeger_mcp.models import (
    ExecutionNode,
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
