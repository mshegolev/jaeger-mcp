"""Unit tests for pure helpers in :mod:`jaeger_mcp.client`.

These tests avoid the network entirely — they cover env-var parsing, URL
validation, :class:`JaegerHTTPClient` construction (which raises
:class:`ConfigError` when JAEGER_URL is missing), and auth selection.
"""

from __future__ import annotations

import asyncio

import pytest

from jaeger_mcp.client import JaegerHTTPClient, _parse_bool, _validate_url
from jaeger_mcp.errors import ConfigError


class TestParseBool:
    @pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on", "YES"])
    def test_truthy_strings(self, value: str) -> None:
        assert _parse_bool(value, default=False) is True

    @pytest.mark.parametrize("value", ["false", "False", "0", "no", "off", "OFF"])
    def test_falsy_strings(self, value: str) -> None:
        assert _parse_bool(value, default=True) is False

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_returns_default(self, value: str | None) -> None:
        assert _parse_bool(value, default=True) is True
        assert _parse_bool(value, default=False) is False

    def test_bool_passthrough(self) -> None:
        assert _parse_bool(True, default=False) is True
        assert _parse_bool(False, default=True) is False


class TestValidateUrl:
    def test_strips_trailing_slash(self) -> None:
        assert _validate_url("https://jaeger.example.com/") == "https://jaeger.example.com"

    def test_strips_whitespace(self) -> None:
        assert _validate_url("  https://jaeger.example.com  ") == "https://jaeger.example.com"

    def test_preserves_no_trailing_slash(self) -> None:
        assert _validate_url("https://jaeger.example.com") == "https://jaeger.example.com"

    def test_http_scheme_allowed(self) -> None:
        assert _validate_url("http://jaeger.local:16686") == "http://jaeger.local:16686"

    def test_empty_raises(self) -> None:
        with pytest.raises(ConfigError, match="JAEGER_URL is not set"):
            _validate_url("")

    def test_missing_scheme_raises(self) -> None:
        with pytest.raises(ConfigError, match="http:// or https://"):
            _validate_url("jaeger.example.com")

    def test_wrong_scheme_raises(self) -> None:
        with pytest.raises(ConfigError, match="http:// or https://"):
            _validate_url("ftp://jaeger.example.com")

    def test_missing_host_raises(self) -> None:
        with pytest.raises(ConfigError, match="missing host"):
            _validate_url("https://")

    def test_url_with_port(self) -> None:
        assert _validate_url("http://localhost:16686") == "http://localhost:16686"

    def test_url_with_path(self) -> None:
        result = _validate_url("https://jaeger.example.com/path/")
        assert result == "https://jaeger.example.com/path"


class TestJaegerHTTPClientInit:
    def test_missing_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JAEGER_URL", raising=False)
        with pytest.raises(ConfigError, match="JAEGER_URL"):
            JaegerHTTPClient()

    def test_happy_path_no_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com/")
        monkeypatch.delenv("JAEGER_TOKEN", raising=False)
        monkeypatch.delenv("JAEGER_USERNAME", raising=False)
        monkeypatch.delenv("JAEGER_PASSWORD", raising=False)
        client = JaegerHTTPClient()
        assert client.url == "https://jaeger.example.com"
        assert client.api_url == "https://jaeger.example.com/api"
        assert client.ssl_verify is True
        assert "Authorization" not in client._headers
        assert client._auth is None

    def test_bearer_auth_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.setenv("JAEGER_TOKEN", "mytoken123")  # pragma: allowlist secret
        client = JaegerHTTPClient()
        assert client._headers["Authorization"] == "Bearer mytoken123"
        assert client._auth is None

    def test_basic_auth_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.delenv("JAEGER_TOKEN", raising=False)
        monkeypatch.setenv("JAEGER_USERNAME", "admin")
        monkeypatch.setenv("JAEGER_PASSWORD", "secret")  # pragma: allowlist secret
        client = JaegerHTTPClient()
        assert "Authorization" not in client._headers
        assert client._auth is not None

    def test_bearer_takes_precedence_over_basic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.setenv("JAEGER_TOKEN", "bearer-wins")  # pragma: allowlist secret
        monkeypatch.setenv("JAEGER_USERNAME", "admin")
        monkeypatch.setenv("JAEGER_PASSWORD", "secret")  # pragma: allowlist secret
        client = JaegerHTTPClient()
        assert client._headers["Authorization"] == "Bearer bearer-wins"
        assert client._auth is None

    def test_overrides_take_precedence_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_URL", "https://env.example.com")
        monkeypatch.setenv("JAEGER_TOKEN", "env-token")  # pragma: allowlist secret
        client = JaegerHTTPClient(
            url="https://explicit.example.com",
            token="explicit-token",  # pragma: allowlist secret
            ssl_verify=True,
        )
        assert client.url == "https://explicit.example.com"
        assert client.token == "explicit-token"  # pragma: allowlist secret
        assert client.ssl_verify is True

    def test_ssl_verify_default_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.delenv("JAEGER_SSL_VERIFY", raising=False)
        client = JaegerHTTPClient()
        assert client.ssl_verify is True

    def test_ssl_verify_false_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        monkeypatch.setenv("JAEGER_SSL_VERIFY", "false")
        client = JaegerHTTPClient()
        assert client.ssl_verify is False

    def test_user_agent_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JAEGER_URL", "https://jaeger.example.com")
        client = JaegerHTTPClient()
        assert client._headers["User-Agent"] == "jaeger-mcp"


class TestClientNotReusedAcrossEventLoops:
    """A cached httpx.AsyncClient must not outlive the loop that created it.

    Found in production, not in review. The investigator walks 6 candidate services for an
    order-id trace search through `asyncio.to_thread -> sync get() -> asyncio.run`, and every
    other candidate failed:

        answered cleanly: EnP.ECHRG.Payments, consumer-kafka-go, EnP.ECHRG.OrderPaymentManagement
        FAILED: EnP.ECHRG.Subscriptions / EnP.ECHRG.History / EnP.EORD.OrderServiceGo
                RuntimeError: Event loop is closed

    The decisive measurement was re-running a FAILING candidate alone: it succeeded. So the fault
    was never the service name — it was this shared instance. `get()` runs each call under its own
    asyncio.run(), which closes that loop on return; the cached client's pool holds loop-bound
    resources, so the next call raises. The alternation comes from the failure itself clearing the
    client, letting the call after it rebuild and succeed.

    Consequence for the caller: `exhausted` never became True, so "no traces for this order" could
    never be stated honestly — a wrong-negative machine.
    """

    def _client(self) -> JaegerHTTPClient:
        return JaegerHTTPClient(url="https://jaeger.example.com", ssl_verify=False)

    def test_a_new_loop_gets_a_new_client(self) -> None:
        client = self._client()
        seen: list[object] = []

        async def probe() -> None:
            seen.append(await client._ensure_client())

        asyncio.run(probe())  # loop A, closed on return
        asyncio.run(probe())  # loop B

        assert seen[0] is not seen[1], (
            "the client created under the first (now closed) loop was reused under the second; "
            "its pool is loop-bound, which is what raises RuntimeError: Event loop is closed"
        )

    def test_the_same_loop_still_reuses_one_client(self) -> None:
        """The fix must not throw away pooling inside a single loop."""
        client = self._client()

        async def probe() -> bool:
            return (await client._ensure_client()) is (await client._ensure_client())

        assert asyncio.run(probe()) is True

    def test_loop_marker_is_cleared_on_aclose(self) -> None:
        """Otherwise a rebuilt client could be matched against a stale loop identity."""
        client = self._client()

        async def build_then_close() -> None:
            await client._ensure_client()
            assert client._client_loop is not None
            await client.aclose()

        asyncio.run(build_then_close())
        assert client._client is None
        assert client._client_loop is None

    def test_six_sequential_sync_calls_all_answer(self) -> None:
        """The shape of the prod failure: 6 candidates, each in its own thread and loop.

        Uses a real keep-alive HTTP server rather than a mocked transport: the bug lives in the
        connection pool, and a mocked transport has none.

        HONEST LIMIT: this test passes with AND without the fix. Over plain HTTP the pool keeps
        nothing the closed loop owned, so it does not discriminate — the discriminating test is
        test_a_new_loop_gets_a_new_client above (verified: it fails when the fix is reverted).
        Reproducing the prod failure itself needs TLS, which prod has and this does not. It is kept
        as a shape guard: if the sync-get path ever breaks outright, this catches it.
        """
        import json as _json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # keep-alive, as prod's Jaeger does

            def do_GET(self) -> None:  # noqa: N802
                body = _json.dumps({"data": ["svc"]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            client = JaegerHTTPClient(url=f"http://127.0.0.1:{server.server_address[1]}", ssl_verify=False)

            async def walk() -> list[str]:
                outcomes = []
                for _ in range(6):
                    try:
                        await asyncio.to_thread(client.get, "/services")
                        outcomes.append("ok")
                    except Exception as exc:  # noqa: BLE001 - the outcome IS the assertion
                        outcomes.append(f"{type(exc).__name__}: {exc}")
                return outcomes

            assert asyncio.run(walk()) == ["ok"] * 6
        finally:
            server.shutdown()
