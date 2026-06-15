# Changelog

All notable changes to `jaeger-mcp` will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
versioning: [SemVer](https://semver.org/).

## [0.3.0] — 2026-06-16

### Added

- **`jaeger_compare_traces` tool** — structural diff between two traces
  - Matches spans by `(operationName, serviceName, parentOperation)` tuple — not span ID
  - Reports added spans, removed spans, and changed spans with duration deltas and tag differences
  - Structured output: `CompareTracesOutput` with `added_spans`, `removed_spans`, `changed_spans`, `unchanged_count`
- **`jaeger_span_statistics` tool** — per-operation latency percentiles and error rates
  - Aggregates across N traces for a service (configurable limit, default 20, max 100)
  - Per-operation stats: count, p50/p95/p99 duration (μs), error count, error rate
  - Structured output: `SpanStatisticsOutput` with per-operation `OperationStats` list
- **`JaegerClient.compare_traces()`** — facade method for in-process trace comparison
- **`JaegerClient.span_statistics()`** — facade method for in-process span stats aggregation
- New domain types: `TraceComparison`, `SpanIdentity`, `SpanChange`, `SpanStatisticsResult`, `OperationStatResult`

## [0.2.0] — 2026-06-06

### Added

- **Library facade** — `from jaeger_mcp import JaegerClient` for in-process use without MCP server
  - `JaegerClient.from_env()` constructs from `JAEGER_URL` environment variable
  - Typed domain objects: `Span`, `Trace`, `TraceSummary`, `ServiceDep`
  - Evidence-required fields: `start_utc` (UTC datetime), `error` (bool), `service_name`, `tags`
- **HTTP retry with exponential backoff** — retries on 429/500/502/503/504 with 1s/2s/4s delays
  - Configurable via `JAEGER_RETRY_ATTEMPTS` env var (default 3, set 0 to disable)
- **TTL cache for discovery endpoints** — `list_services` and `list_operations` cached in-memory
  - Configurable via `JAEGER_CACHE_TTL` env var (default 120s, set 0 to disable)
- **Configurable HTTP timeout** — via `JAEGER_TIMEOUT` env var (default 30s)
- **Structured request logging** — every HTTP request logs method, URL, status code, latency (ms)
- **SSL warning** — `WARNING` log emitted when `JAEGER_SSL_VERIFY=false`
- **trace_id hex validation** — rejects non-hex characters before making HTTP call

### Changed

- Internal HTTP client renamed from `JaegerClient` to `JaegerHTTPClient`
  - Public `JaegerClient` name now belongs to the library facade
- Data-shaping helpers extracted from `tools.py` into new `shaping.py` module
  - `tools.py` reduced from 723 to 601 lines

### Security

- Dockerfile now runs as non-root user `mcp` (was root)

### CI

- Coverage threshold enforced: `--cov-fail-under=90` (current: 95.77%)

## [0.1.0] — 2026-04-18

### Added

- `jaeger_list_services` — list all services Jaeger has observed
- `jaeger_list_operations` — list operation names for a given service
- `jaeger_search_traces` — search traces with service, operation, tags, time range, duration filters
- `jaeger_get_trace` — retrieve full trace detail with span tree, service breakdown, execution tree
- `jaeger_get_dependencies` — fetch service-to-service call graph edges
- HTTP Basic auth and Bearer token auth support (Bearer takes precedence)
- SSL verification toggle via `JAEGER_SSL_VERIFY`
- Thread-safe lazy client cache (double-checked locking)
- Structured output (`outputSchema`) for all 5 tools via FastMCP `structured_output=True`
- Markdown rendering with truncation hints for large result sets
- Actionable error messages for 401/403/404/400/429/5xx/ConnectionError/Timeout
