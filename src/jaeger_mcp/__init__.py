"""jaeger-mcp — MCP server for Jaeger distributed tracing."""

__version__ = "0.2.0"

from jaeger_mcp.facade import JaegerClient, ServiceDep, Span, Trace, TraceSummary

__all__ = [
    "JaegerClient",
    "Span",
    "Trace",
    "TraceSummary",
    "ServiceDep",
]
