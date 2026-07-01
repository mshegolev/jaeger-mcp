"""Tests for JGR-05: Structured request logging and SSL warning."""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from jaeger_mcp.client import JaegerHTTPClient


class TestRequestLogging:
    @respx.mock
    async def test_successful_request_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """HTTP requests log method, url, status, ms."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        respx.get("https://jaeger.example.com/api/services").mock(
            return_value=httpx.Response(200, json={"data": ["svc"]}),
        )
        client = JaegerHTTPClient(retry_attempts=0, cache_ttl=0)
        try:
            with caplog.at_level(logging.INFO, logger="jaeger_mcp.client"):
                await client.aget("/services")
            assert any("method=GET" in r.message for r in caplog.records)
            assert any("status=200" in r.message for r in caplog.records)
            assert any("ms=" in r.message for r in caplog.records)
        finally:
            await client.aclose()

    @respx.mock
    async def test_failed_request_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Failed HTTP requests also log."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        respx.get("https://jaeger.example.com/api/services").mock(
            return_value=httpx.Response(400, json={"errors": ["fail"]}),
        )
        client = JaegerHTTPClient(retry_attempts=0, cache_ttl=0)
        try:
            with caplog.at_level(logging.INFO, logger="jaeger_mcp.client"):
                with pytest.raises(httpx.HTTPStatusError):
                    await client.aget("/services")
            # Should have logged both the 400 status and ERR
            log_msgs = [r.message for r in caplog.records]
            assert any("status=400" in m or "status=ERR" in m for m in log_msgs)
        finally:
            await client.aclose()


class TestSSLWarningLog:
    def test_ssl_disabled_logs_warning(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        """SSL verify disabled emits WARNING log."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        with caplog.at_level(logging.WARNING, logger="jaeger_mcp.client"):
            JaegerHTTPClient(ssl_verify=False)  # construction emits the warning
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("ssl_verify=false" in m for m in warning_msgs)

    def test_ssl_enabled_no_warning(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        """SSL verify enabled does NOT emit warning."""
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        with caplog.at_level(logging.WARNING, logger="jaeger_mcp.client"):
            JaegerHTTPClient(ssl_verify=True)  # construction must NOT warn
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert not any("ssl_verify" in m for m in warning_msgs)
