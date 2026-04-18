"""Shared FastMCP instance and client cache."""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from jaeger_mcp.client import JaegerClient

logger = logging.getLogger(__name__)

_client: JaegerClient | None = None
_client_lock = threading.Lock()


@asynccontextmanager
async def app_lifespan(_app: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Server lifespan: close HTTP session on shutdown."""
    logger.debug("jaeger_mcp: startup")
    try:
        yield {}
    finally:
        global _client
        with _client_lock:
            if _client is not None:
                try:
                    _client.close()
                except Exception:
                    pass
                _client = None
        logger.debug("jaeger_mcp: shutdown — HTTP session closed")


mcp = FastMCP("jaeger_mcp", lifespan=app_lifespan)


def get_client() -> JaegerClient:
    """Return a cached :class:`JaegerClient` (thread-safe lazy-init).

    FastMCP runs synchronous tools in worker threads via
    ``anyio.to_thread.run_sync``; concurrent first-calls could otherwise
    race on the ``_client`` global. The lock ensures exactly one instance
    is constructed.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:  # double-checked locking
                _client = JaegerClient()
    return _client
