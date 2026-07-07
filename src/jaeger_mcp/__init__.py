"""jaeger-mcp — MCP server for Jaeger distributed tracing."""

__version__ = "0.5.2"

from jaeger_mcp.facade import (
    JaegerClient,
    OperationStatResult,
    ServiceDep,
    Span,
    SpanChange,
    SpanIdentity,
    SpanStatisticsResult,
    Trace,
    TraceComparison,
    TraceSummary,
)

__all__ = [
    "JaegerClient",
    "OperationStatResult",
    "ServiceDep",
    "Span",
    "SpanChange",
    "SpanIdentity",
    "SpanStatisticsResult",
    "Trace",
    "TraceComparison",
    "TraceSummary",
]
