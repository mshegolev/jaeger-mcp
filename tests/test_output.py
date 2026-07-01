"""Tests for output.py edge cases and close/lifecycle gaps (JGR-17)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from mcp.server.fastmcp.exceptions import ToolError

from jaeger_mcp import output
from jaeger_mcp.client import JaegerHTTPClient

# ── output.ok() edge cases ───────────────────────────────────────────────


class TestOutputOk:
    def test_ok_returns_structured_content(self) -> None:
        result = output.ok({"key": "value"}, "# Title")
        assert result.structuredContent == {"key": "value"}
        assert result.content[0].text == "# Title"

    def test_ok_with_empty_markdown(self) -> None:
        result = output.ok({"key": "value"}, "")
        assert result.content[0].text == ""
        assert result.structuredContent == {"key": "value"}

    def test_ok_with_empty_data(self) -> None:
        result = output.ok({}, "No data")
        assert result.structuredContent == {}

    def test_ok_with_nested_data(self) -> None:
        data = {"items": [1, 2, 3], "nested": {"a": "b"}}
        result = output.ok(data, "md")
        assert result.structuredContent["items"] == [1, 2, 3]


# ── output.fail() ────────────────────────────────────────────────────────


class TestOutputFail:
    def test_fail_raises_tool_error(self) -> None:
        with pytest.raises(ToolError):
            output.fail(ValueError("test error"), "doing something")

    def test_fail_wraps_original_exception(self) -> None:
        original = RuntimeError("boom")
        with pytest.raises(ToolError) as exc_info:
            output.fail(original, "test action")
        assert exc_info.value.__cause__ is original


# ── JGR-12: Credential zeroing on close ──────────────────────────────────


class TestCredentialZeroing:
    def test_close_zeros_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        client = JaegerHTTPClient(token="secret-token", username="admin", password="pass123")
        assert client.token == "secret-token"
        assert client.username == "admin"
        assert client.password == "pass123"
        client.close()
        assert client.token == ""
        assert client.username == ""
        assert client.password == ""

    def test_close_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling close() twice does not raise."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        client = JaegerHTTPClient()
        client.close()
        client.close()  # should not raise
        assert client.token == ""


# ── JGR-13: Shutdown exception logging ───────────────────────────────────


class TestShutdownLogging:
    @pytest.mark.anyio
    async def test_shutdown_logs_close_exception(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """If _client.aclose() raises, the exception is logged as WARNING."""
        import jaeger_mcp._mcp as mcp_mod

        mock_client = MagicMock()
        mock_client.aclose = AsyncMock(side_effect=RuntimeError("close failed"))

        # Run the lifespan context manager with a mock app
        mock_app = MagicMock()
        monkeypatch.setattr(mcp_mod, "_client", mock_client)

        with caplog.at_level(logging.WARNING, logger="jaeger_mcp._mcp"):
            async with mcp_mod.app_lifespan(mock_app):
                pass  # simulate normal server run

        # The aclose() was called and the exception was logged (not raised)
        mock_client.aclose.assert_called_once()
        assert any("error closing HTTP client" in r.message for r in caplog.records)


# ── Empty response body ──────────────────────────────────────────────────


class TestEmptyResponseBody:
    @respx.mock
    async def test_empty_body_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        respx.get("https://jaeger.example.com/api/services").mock(
            return_value=httpx.Response(200, content=b""),
        )
        client = JaegerHTTPClient(retry_attempts=0, cache_ttl=0)
        try:
            result = await client.aget("/services")
            assert result is None
        finally:
            await client.aclose()
