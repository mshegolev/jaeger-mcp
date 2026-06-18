"""Tests for the module-level client cache in :mod:`jaeger_mcp._mcp`.

``get_client`` lazily instantiates a single :class:`JaegerHTTPClient`
protected by an ``asyncio.Lock`` (double-checked locking). This test covers
the happy path — repeated calls return the *same* instance, and wiping the
global cache rebuilds the client.
"""

from __future__ import annotations

import asyncio

import pytest

from jaeger_mcp import _mcp
from jaeger_mcp.client import JaegerHTTPClient


@pytest.fixture(autouse=True)
def reset_cache() -> None:
    """Clear the module-global client between tests to avoid test-order coupling."""
    if _mcp._client is not None:
        try:
            asyncio.run(_mcp._client.aclose())
        except Exception:
            pass
    _mcp._client = None
    yield  # type: ignore[misc]
    if _mcp._client is not None:
        try:
            asyncio.run(_mcp._client.aclose())
        except Exception:
            pass
    _mcp._client = None


@pytest.mark.asyncio
async def test_get_client_returns_same_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
    first = await _mcp.get_client()
    second = await _mcp.get_client()
    assert first is second
    assert isinstance(first, JaegerHTTPClient)


@pytest.mark.asyncio
async def test_get_client_raises_on_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JAEGER_URL", raising=False)
    with pytest.raises(Exception, match="JAEGER_URL"):
        await _mcp.get_client()


@pytest.mark.asyncio
async def test_cache_rebuilds_after_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
    first = await _mcp.get_client()
    _mcp._client = None
    second = await _mcp.get_client()
    assert first is not second


@pytest.mark.asyncio
async def test_get_client_no_auth_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jaeger allows unauthenticated access — client should build without token."""
    monkeypatch.setenv("JAEGER_URL", "https://jaeger.internal")
    monkeypatch.delenv("JAEGER_TOKEN", raising=False)
    monkeypatch.delenv("JAEGER_USERNAME", raising=False)
    monkeypatch.delenv("JAEGER_PASSWORD", raising=False)
    client = await _mcp.get_client()
    assert isinstance(client, JaegerHTTPClient)
    assert client.token == ""
