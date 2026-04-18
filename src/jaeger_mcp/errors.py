"""Actionable error messages for Jaeger HTTP errors."""

from __future__ import annotations

import requests


class ConfigError(ValueError):
    """Raised when required environment variables are missing or malformed.

    Subclass of :class:`ValueError` so callers can continue to use
    ``isinstance(..., ValueError)``, but narrow enough that :func:`handle`
    can distinguish config errors from Pydantic validation errors bubbling
    up from tool input.
    """


def handle(exc: Exception, action: str) -> str:
    """Convert an exception raised while performing ``action`` into an
    LLM-readable string with a suggested next step.

    The goal is that the agent sees *why* the call failed and *what it could
    do about it* without needing to inspect a Python traceback.
    """
    if isinstance(exc, ConfigError):
        return (
            f"Error: configuration problem while {action} — {exc}. "
            "Check JAEGER_URL, JAEGER_TOKEN, JAEGER_USERNAME, JAEGER_PASSWORD, "
            "JAEGER_SSL_VERIFY environment variables."
        )

    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else None
        if code == 401:
            return (
                f"Error: authentication failed (HTTP 401) while {action}. "
                "Verify JAEGER_TOKEN (Bearer) or JAEGER_USERNAME/JAEGER_PASSWORD (Basic auth) "
                "are set correctly. Many internal Jaeger instances require no auth — "
                "try unsetting those env vars if this is an internal deployment."
            )
        if code == 403:
            return (
                f"Error: forbidden (HTTP 403) while {action}. "
                "The provided credentials lack permission for this Jaeger resource. "
                "Check JAEGER_TOKEN or JAEGER_USERNAME/JAEGER_PASSWORD and Jaeger's "
                "RBAC configuration."
            )
        if code == 404:
            return (
                f"Error: resource not found (HTTP 404) while {action}. "
                "Check the traceID or service name is correct. "
                "Use jaeger_list_services to discover valid service names, "
                "or jaeger_search_traces to find trace IDs."
            )
        if code == 400:
            body = ""
            if exc.response is not None:
                try:
                    body = exc.response.text[:300]
                except Exception:
                    pass
            return (
                f"Error: bad request (HTTP 400) while {action}. "
                "Jaeger rejected the parameters — check query params like tags JSON format, "
                f"time ranges (microseconds UTC), or duration format (e.g. '100ms', '1.5s'). "
                f"Response: {body}"
            )
        if code == 429:
            return (
                f"Error: rate-limited (HTTP 429) while {action}. "
                "Wait 30-60s before retrying; reduce the limit parameter or narrow the time range."
            )
        if code is not None and 500 <= code < 600:
            return (
                f"Error: Jaeger server error (HTTP {code}) while {action}. "
                "This is usually transient — retry in a few seconds; "
                "check Jaeger query service health at JAEGER_URL/api/services."
            )
        body = ""
        if exc.response is not None:
            try:
                body = exc.response.text[:200]
            except Exception:
                pass
        return f"Error: HTTP {code} while {action}. Response: {body}"

    if isinstance(exc, requests.ConnectionError):
        return (
            f"Error: could not connect to Jaeger while {action}. "
            "Check JAEGER_URL is set and reachable (e.g. https://jaeger.example.com). "
            "Jaeger query service runs on port 16686 by default."
        )

    if isinstance(exc, requests.Timeout):
        return (
            f"Error: request timed out while {action}. "
            "Check network latency and retry; reduce the limit parameter if searching traces."
        )

    if isinstance(exc, ValueError):
        return f"Error: invalid input while {action} — {exc}"

    return f"Error: unexpected {type(exc).__name__} while {action}: {exc}"
