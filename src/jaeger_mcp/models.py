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
