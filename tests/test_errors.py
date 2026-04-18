"""Unit tests for :mod:`jaeger_mcp.errors`.

Verifies that every HTTP status we special-case produces an actionable
message that names the relevant env vars where appropriate and hints at
a concrete next step. Network failures are simulated via :mod:`responses`.
"""

from __future__ import annotations

import pytest
import requests
import responses

from jaeger_mcp.errors import ConfigError, handle


def _http_error(
    status: int,
    url: str = "https://jaeger.example.com/api/services",
    body: str | None = None,
) -> requests.HTTPError:
    """Trigger a real ``requests.HTTPError`` carrying a response with ``status``."""
    with responses.RequestsMock() as rsps:
        if body is None:
            rsps.add(responses.GET, url, json={}, status=status)
        else:
            rsps.add(responses.GET, url, body=body, status=status)
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
        except requests.HTTPError as e:
            return e
    raise AssertionError(f"expected HTTPError for status {status}")  # pragma: no cover


class TestConfigError:
    def test_message_mentions_env_vars(self) -> None:
        msg = handle(ConfigError("JAEGER_URL is not set"), "listing services")
        assert "configuration problem" in msg
        assert "listing services" in msg
        assert "JAEGER_URL" in msg
        assert "JAEGER_TOKEN" in msg

    def test_message_mentions_ssl_verify(self) -> None:
        msg = handle(ConfigError("bad ssl"), "connecting")
        assert "JAEGER_SSL_VERIFY" in msg


class TestHttpStatusMapping:
    def test_401_mentions_token_and_basic(self) -> None:
        msg = handle(_http_error(401), "listing services")
        assert "401" in msg
        assert "JAEGER_TOKEN" in msg
        assert "JAEGER_USERNAME" in msg or "Basic" in msg

    def test_401_suggests_unauthenticated_option(self) -> None:
        msg = handle(_http_error(401), "listing services")
        assert "no auth" in msg.lower() or "unauthenticated" in msg.lower() or "internal" in msg.lower()

    def test_403_mentions_credentials(self) -> None:
        msg = handle(_http_error(403), "fetching trace abc123")
        assert "403" in msg
        assert "JAEGER_TOKEN" in msg or "credentials" in msg.lower() or "permission" in msg.lower()
        assert "fetching trace abc123" in msg

    def test_404_suggests_discovery(self) -> None:
        msg = handle(_http_error(404), "fetching trace xyz")
        assert "404" in msg
        assert "jaeger_list_services" in msg or "jaeger_search_traces" in msg

    def test_400_includes_body_snippet(self) -> None:
        err = _http_error(400, body="Invalid parameter: tags must be JSON")
        msg = handle(err, "searching traces")
        assert "400" in msg
        assert "Invalid parameter" in msg

    def test_429_suggests_backoff(self) -> None:
        msg = handle(_http_error(429), "searching traces")
        assert "429" in msg
        assert "Wait" in msg or "rate" in msg or "limit" in msg

    @pytest.mark.parametrize("code", [500, 502, 503, 504])
    def test_5xx_flags_transient(self, code: int) -> None:
        msg = handle(_http_error(code), "fetching dependencies")
        assert str(code) in msg
        assert "transient" in msg or "api/services" in msg

    def test_unknown_4xx_includes_body_snippet(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://jaeger.example.com/api/x",
                body="teapot!" * 50,
                status=418,
            )
            try:
                r = requests.get("https://jaeger.example.com/api/x", timeout=5)
                r.raise_for_status()
            except requests.HTTPError as e:
                msg = handle(e, "teapot call")
                assert "418" in msg
                assert "teapot" in msg


class TestNetworkErrors:
    def test_connection_error_mentions_url_and_port(self) -> None:
        msg = handle(requests.ConnectionError("DNS fail"), "listing services")
        assert "connect" in msg.lower()
        assert "JAEGER_URL" in msg
        assert "16686" in msg

    def test_timeout_mentions_limit(self) -> None:
        msg = handle(requests.Timeout("slow"), "searching traces")
        assert "timed out" in msg
        assert "limit" in msg

    def test_unexpected_exception_fallthrough(self) -> None:
        msg = handle(RuntimeError("kaboom"), "something")
        assert "RuntimeError" in msg
        assert "kaboom" in msg
        assert "something" in msg

    def test_value_error_surfaces_cleanly(self) -> None:
        msg = handle(ValueError("tags must be valid JSON"), "searching traces for svc")
        assert msg.startswith("Error: invalid input while searching traces for svc")
        assert "tags must be valid JSON" in msg
        assert "unexpected" not in msg
