"""Helpers that produce the dual-channel tool result."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NoReturn

from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from jaeger_mcp import errors


def ok(data: Mapping[str, Any], markdown: str) -> CallToolResult:
    """Wrap ``data`` + a markdown rendering into a non-error tool result."""
    return CallToolResult(
        content=[TextContent(type="text", text=markdown)],
        structuredContent=dict(data),
    )


def fail(exc: Exception, action: str) -> NoReturn:
    """Raise a ``ToolError`` carrying the actionable error message.

    The ``NoReturn`` annotation lets type-checkers understand that control
    flow does not return from this function.
    """
    raise ToolError(errors.handle(exc, action)) from exc
