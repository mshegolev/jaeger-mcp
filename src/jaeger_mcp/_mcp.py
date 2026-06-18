"""Shared FastMCP instance and client cache."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from jaeger_mcp.client import JaegerHTTPClient

logger = logging.getLogger(__name__)

_client: JaegerHTTPClient | None = None
_client_lock = asyncio.Lock()


@asynccontextmanager
async def app_lifespan(_app: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Server lifespan: close HTTP session on shutdown."""
    logger.debug("jaeger_mcp: startup")
    try:
        yield {}
    finally:
        global _client
        async with _client_lock:
            if _client is not None:
                try:
                    await _client.aclose()
                except Exception:
                    logger.warning("jaeger_mcp: error closing HTTP client on shutdown", exc_info=True)
                _client = None
        logger.debug("jaeger_mcp: shutdown — HTTP session closed")


mcp = FastMCP("jaeger_mcp", lifespan=app_lifespan)


async def get_client() -> JaegerHTTPClient:
    """Return a cached :class:`JaegerHTTPClient` (async lazy-init).

    Uses ``asyncio.Lock`` with double-checked locking to ensure exactly
    one :class:`JaegerHTTPClient` is constructed, even under concurrent
    ``await get_client()`` calls within the event loop.
    """
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:  # double-checked locking
                _client = JaegerHTTPClient()
    return _client
