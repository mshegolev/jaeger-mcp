# Changelog

All notable changes to `jaeger-mcp` will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
versioning: [SemVer](https://semver.org/).

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
