"""FastMCP server entry point for Jaeger MCP."""

from __future__ import annotations

# Importing the tools module attaches @mcp.tool decorators to the shared
# FastMCP instance.
from jaeger_mcp import tools as _tools  # noqa: F401
from jaeger_mcp._mcp import app_lifespan, mcp


def main() -> None:
    """Entry point for the ``jaeger-mcp`` console script (stdio)."""
    mcp.run()


__all__ = ["mcp", "app_lifespan", "main"]


if __name__ == "__main__":
    main()
