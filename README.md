# jaeger-mcp

<!-- mcp-name: io.github.mshegolev/jaeger-mcp -->

[![PyPI version](https://img.shields.io/pypi/v/jaeger-mcp.svg)](https://pypi.org/project/jaeger-mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/jaeger-mcp.svg)](https://pypi.org/project/jaeger-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://github.com/mshegolev/jaeger-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/mshegolev/jaeger-mcp/actions/workflows/test.yml)

**MCP server for [Jaeger](https://www.jaegertracing.io/) distributed tracing.**
Give Claude (or any MCP-capable agent) read access to your trace data — search traces, inspect spans, map service dependencies — without leaving the conversation.

## Why another Jaeger MCP?

The existing Jaeger integrations require a running UI or custom scripts. This server:

- Speaks the standard [Model Context Protocol](https://modelcontextprotocol.io/) over **stdio** — works with Claude Desktop, Claude Code, Cursor, and any MCP client.
- Is **read-only**: all 5 tools carry `readOnlyHint: true` — zero risk of modifying trace data.
- Returns **dual-channel output**: structured JSON (`structuredContent`) for programmatic use + Markdown (`content`) for human-readable display.
- Has **actionable error messages** that name the exact env var to fix and suggest a next step.
- Supports **Bearer token**, **HTTP Basic auth**, or **no auth** (common for internal deployments).

## Tools

| Tool | Endpoint | Description |
|------|----------|-------------|
| `jaeger_list_services` | `GET /api/services` | List all instrumented services |
| `jaeger_list_operations` | `GET /api/services/{service}/operations` | List operation names for a service |
| `jaeger_search_traces` | `GET /api/traces` | Search traces with rich filters |
| `jaeger_get_trace` | `GET /api/traces/{traceID}` | Full trace detail with span tree |
| `jaeger_get_dependencies` | `GET /api/dependencies` | Service-to-service call graph |

## Installation

```bash
pip install jaeger-mcp
```

Or run directly without installing:

```bash
uvx jaeger-mcp
```

## Configuration

All configuration is via environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JAEGER_URL` | **Yes** | — | Jaeger query service URL, e.g. `https://jaeger.example.com` |
| `JAEGER_TOKEN` | No | — | Bearer token (takes precedence over Basic auth) |
| `JAEGER_USERNAME` | No | — | HTTP Basic auth username |
| `JAEGER_PASSWORD` | No | — | HTTP Basic auth password |
| `JAEGER_SSL_VERIFY` | No | `true` | Set `false` for self-signed certificates |

Copy `.env.example` to `.env` and fill in your values.

## Claude Desktop / Claude Code setup

Add to your MCP config (`claude_desktop_config.json` or `.claude/mcp.json`):

```json
{
  "mcpServers": {
    "jaeger": {
      "command": "jaeger-mcp",
      "env": {
        "JAEGER_URL": "https://jaeger.example.com",
        "JAEGER_TOKEN": "your-token-here"
      }
    }
  }
}
```

Or with `uvx` (no install required):

```json
{
  "mcpServers": {
    "jaeger": {
      "command": "uvx",
      "args": ["jaeger-mcp"],
      "env": {
        "JAEGER_URL": "https://jaeger.example.com"
      }
    }
  }
}
```

## Docker

```bash
docker run --rm -e JAEGER_URL=https://jaeger.example.com jaeger-mcp
```

## Example queries

Once configured, ask Claude:

- "What services does Jaeger know about?"
- "Find traces with HTTP 500 errors in `order-service` from the last hour"
- "Show me the slowest traces (over 2 seconds) for `GET /checkout`"
- "What caused the error in trace `abcdef1234567890`?"
- "Map the service dependency graph for the last 7 days"
- "Which services call `postgres` most frequently?"

## Tool usage guide

### `jaeger_list_services`

Returns all service names Jaeger has seen. **Start here** when you don't know which services are instrumented. Output is capped at 500 services with a truncation hint.

### `jaeger_list_operations`

Returns all operation names for a given service (e.g. HTTP route names, gRPC method names). Use to discover valid operation names before filtering `jaeger_search_traces`.

### `jaeger_search_traces`

The main search tool. Filters:

- `service` (required) — service name from `jaeger_list_services`
- `operation` — narrow to a specific endpoint
- `tags` — JSON string of tag filters, e.g. `{"http.status_code":"500"}` or `{"error":"true"}`
- `start` / `end` — time range in **microseconds** UTC
- `min_duration` / `max_duration` — duration strings like `"100ms"`, `"1.5s"`, `"2m"`
- `limit` — default 20, max 1500

Returns trace summaries with `trace_id`, `duration_us`, `span_count`, `service_count`, `root_operation`, `errors_count`.

### `jaeger_get_trace`

Full trace detail. Accepts a `trace_id` (hex string, 16-32 chars) and returns:

- All spans with tags, service names, parent/child relationships
- Per-service statistics (span count, total duration, error count)
- Execution tree (each node lists its child span IDs)

Error spans are identified by `tags["error"] = "true"`.

### `jaeger_get_dependencies`

Service topology graph. Returns directed edges `(parent → child)` with `call_count`. Use `lookback_hours` (default 24, max 720) to control the window.

## Performance characteristics

- All tools use a single persistent `requests.Session` with connection pooling.
- The session has `trust_env = False` to bypass environment proxies (Jaeger is typically an internal service).
- Requests time out after 30 seconds.
- `jaeger_search_traces` passes `limit` directly to Jaeger — avoid requesting more traces than needed.
- `jaeger_get_trace` fetches the full trace in one call — large traces (thousands of spans) may be slow.
- `jaeger_get_dependencies` aggregates over the full lookback window; large windows may be slow on busy clusters.

## Development

```bash
git clone https://github.com/mshegolev/jaeger-mcp
cd jaeger-mcp
pip install -e '.[dev]'
pytest tests/ -v
ruff check src tests
ruff format src tests
```

## License

MIT — see [LICENSE](LICENSE).
