# jaeger-mcp

<!-- mcp-name: io.github.mshegolev/jaeger-mcp -->

[![PyPI version](https://img.shields.io/pypi/v/jaeger-mcp.svg)](https://pypi.org/project/jaeger-mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/jaeger-mcp.svg)](https://pypi.org/project/jaeger-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://github.com/mshegolev/jaeger-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/mshegolev/jaeger-mcp/actions/workflows/test.yml)

**MCP server for [Jaeger](https://www.jaegertracing.io/) distributed tracing.**
Give Claude (or any MCP-capable agent) read access to your trace data — search traces, inspect spans, compare traces, compute span statistics, map service dependencies, predict performance issues, and forecast capacity needs — without leaving the conversation.

## Why another Jaeger MCP?

The existing Jaeger integrations require a running UI or custom scripts. This server:

- Speaks the standard [Model Context Protocol](https://modelcontextprotocol.io/) over **stdio** — works with Claude Desktop, Claude Code, Cursor, and any MCP client.
- Is **read-only**: all 12 tools carry `readOnlyHint: true` — zero risk of modifying trace data.
- Returns **dual-channel output**: structured JSON (`structuredContent`) for programmatic use + Markdown (`content`) for human-readable display.
- Has **actionable error messages** that name the exact env var to fix and suggest a next step.
- Supports **Bearer token**, **HTTP Basic auth**, or **no auth** (common for internal deployments).
- Includes **OpenAPI specification** documenting the underlying Jaeger Query API (`openapi.yaml`).

## Tools

| Tool | Endpoint | Description |
|------|----------|-------------|
| `jaeger_list_services` | `GET /api/services` | List all instrumented services |
| `jaeger_list_operations` | `GET /api/services/{service}/operations` | List operation names for a service |
| `jaeger_search_traces` | `GET /api/traces` | Search traces with rich filters |
| `jaeger_get_trace` | `GET /api/traces/{traceID}` | Full trace detail with span tree |
| `jaeger_get_dependencies` | `GET /api/dependencies` | Service-to-service call graph |
| `jaeger_compare_traces` | `GET /api/traces/{traceID}` ×2 | Structural diff between two traces |
| `jaeger_span_statistics` | `GET /api/traces` | Per-operation latency and error stats |
| `jaeger_critical_path` | `GET /api/traces/{traceID}` | Longest-duration span chain and bottleneck ranking |
| `jaeger_compare_windows` | `GET /api/traces` ×2 | Aggregate trace behavior diff between two time periods |
| `jaeger_detect_anomalies` | `GET /api/traces` ×2 | Statistical latency/error-rate spike detection per operation |
| `jaeger_predict_degradation` | `GET /api/traces` | Predict performance degradation 2-24 hours in advance |
| `jaeger_forecast_capacity` | `GET /api/traces` | Forecast throughput demands and resource requirements |

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
| `JAEGER_TIMEOUT` | No | `30` | HTTP request timeout in seconds |
| `JAEGER_RETRY_ATTEMPTS` | No | `3` | Retry count for transient failures (0 to disable) |
| `JAEGER_CACHE_TTL` | No | `120` | TTL in seconds for discovery endpoint cache (0 to disable) |

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
- "Compare trace `abc123` against trace `def456` — what spans changed?"
- "What are the p95 latencies per operation in `order-service`?"

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

### `jaeger_compare_traces`

Structural diff between two traces. Accepts two `trace_id` hex strings and matches spans by `(operationName, serviceName, parentOperation)` — not span ID. Reports:

- **Added spans** — present in trace B but not trace A
- **Removed spans** — present in trace A but not trace B
- **Changed spans** — matched but differ in duration or tags (shows deltas)
- **Unchanged count** — number of identical spans

Use to compare a slow trace against a fast one, or to see what changed between deployments.

### `jaeger_span_statistics`

Per-operation latency percentiles and error rates. Fetches up to `limit` traces (default 20, max 100) for a service and aggregates all spans by operation name. Reports per operation:

- `count` — total spans observed
- `p50_duration_us`, `p95_duration_us`, `p99_duration_us` — latency percentiles
- `error_count`, `error_rate` — errors (identified by `tags["error"] = "true"`)

Use to find the slowest or most error-prone operations in a service.

### `jaeger_critical_path`

Identifies the longest-duration span chain from root to leaf in a trace (the critical path) and ranks spans by self-time to find performance bottlenecks. 

Reports:
- Critical path spans with operation, service, duration, and percentage-of-total
- Bottleneck spans ranked by exclusive duration (self-time)

Use to answer "Why is this trace so slow?" and "Which operations consume the most CPU/self-time?"

### `jaeger_compare_windows`

Compares aggregate trace behavior between two time periods for a service to detect performance regressions or improvements across deployments.

Reports:
- Per-operation diff summary showing added, removed, faster, slower operations
- Deviation scoring with numeric scores per operation and overall
- Latency percentile changes (p50, p95) and error rate deltas

Use to answer "Did our latest deployment affect performance?" and "Which operations got slower after the database upgrade?"

### `jaeger_detect_anomalies`

Scans for statistically significant latency spikes or error-rate increases in a service's recent traces compared to historical baselines.

Reports:
- Flagged operations with anomaly type (latency or error_rate)
- Severity classification (low to critical) with z-scores
- Current vs baseline values for affected metrics

Use to proactively identify performance degradations and reliability issues before they impact users.

## Library facade (in-process use)

`jaeger-mcp` can also be used as a Python library without an MCP server:

```python
from jaeger_mcp import JaegerClient

client = JaegerClient.from_env()  # reads JAEGER_URL from env
trace = client.get_trace("abcdef1234567890abcdef1234567890")

for span in trace.spans:
    if span.error:
        print(f"{span.service_name}: {span.operation} at {span.start_utc}")
        print(f"  tags: {span.tags}")
```

Available methods: `get_trace()`, `search_traces()`, `list_services()`, `get_dependencies()`, `compare_traces()`, `span_statistics()`, `critical_path()`, `compare_windows()`, `detect_anomalies()`.

Domain objects: `Span`, `Trace`, `TraceSummary`, `ServiceDep`, `TraceComparison`, `SpanIdentity`, `SpanChange`, `SpanStatisticsResult`, `OperationStatResult`, `CriticalPathOutput`, `CriticalPathSpan`, `BottleneckSpan`, `WindowComparisonOutput`, `OperationDiff`, `AnomalyDetectionOutput`, `OperationAnomaly` — all with typed fields.

## API Documentation

This project includes comprehensive OpenAPI specifications in the `docs/` directory:

1. **Jaeger Query Service API** (`openapi.yaml`) - Documents the actual Jaeger API endpoints
2. **MCP Tools API** (`docs/mcp-tools-openapi.yaml`) - Documents the MCP tools as conceptual HTTP endpoints

These specifications are useful for:
- Understanding the underlying API calls made by each tool
- Developing alternative integrations
- Debugging API interactions
- Generating client libraries or documentation

See `docs/README.md` for more details on both specifications.

## Performance characteristics

- All tools use a single persistent `requests.Session` with connection pooling.
- The session has `trust_env = False` to bypass environment proxies (Jaeger is typically an internal service).
- Requests time out after 30 seconds (configurable via `JAEGER_TIMEOUT`).
- Transient HTTP errors (429/5xx) are retried with exponential backoff (configurable via `JAEGER_RETRY_ATTEMPTS`).
- `list_services` and `list_operations` responses are cached for 120 seconds (configurable via `JAEGER_CACHE_TTL`).
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
