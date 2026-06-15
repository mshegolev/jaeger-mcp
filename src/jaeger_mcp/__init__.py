"""jaeger-mcp — MCP server for Jaeger distributed tracing."""

__version__ = "0.2.0"

from jaeger_mcp.facade import (
    JaegerClient,
    ServiceDep,
    Span,
    SpanChange,
    SpanIdentity,
    Trace,
    TraceComparison,
    TraceSummary,
)

__all__ = [
    "JaegerClient",
    "ServiceDep",
    "Span",
    "SpanChange",
    "SpanIdentity",
    "Trace",
    "TraceComparison",
    "TraceSummary",
]
