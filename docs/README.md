# Documentation

This directory contains OpenAPI specifications for the jaeger-mcp project.

## OpenAPI Specifications

### 1. Jaeger Query Service API (`../openapi.yaml`)

This specification documents the actual Jaeger Query Service API endpoints that the jaeger-mcp server interacts with. It covers:

- `/api/services` - List all instrumented services
- `/api/services/{service}/operations` - List operations for a service
- `/api/traces` - Search and retrieve traces
- `/api/traces/{traceID}` - Retrieve a specific trace
- `/api/dependencies` - Get service dependency graph

This is useful for understanding the underlying API that the MCP tools communicate with.

### 2. Jaeger MCP Tools API (`mcp-tools-openapi.yaml`)

This specification documents the MCP tools provided by the jaeger-mcp server as if they were HTTP endpoints. Since MCP actually works over stdio, this is a conceptual representation for documentation purposes.

The specification covers all 10 MCP tools:
1. `jaeger_list_services` - Discover which services Jaeger has seen
2. `jaeger_list_operations` - List operations for a service
3. `jaeger_search_traces` - Search traces with rich filters
4. `jaeger_get_trace` - Retrieve full trace with span tree
5. `jaeger_get_dependencies` - Service-to-service call graph
6. `jaeger_compare_traces` - Structural diff between two traces
7. `jaeger_span_statistics` - Per-operation latency and error stats
8. `jaeger_critical_path` - Longest-duration span chain and bottleneck ranking
9. `jaeger_compare_windows` - Aggregate trace behavior diff between time periods
10. `jaeger_detect_anomalies` - Statistical latency/error-rate spike detection

## Usage

You can view these specifications using any OpenAPI viewer such as:
- [Swagger UI](https://swagger.io/tools/swagger-ui/)
- [ReDoc](https://github.com/Redocly/redoc)
- VS Code extensions that support OpenAPI preview

For example, to view with Redoc:

```bash
npx redoc-cli serve ../openapi.yaml
```

Or with Swagger UI:

```bash
docker run -p 8080:8080 -e SWAGGER_JSON=/specs/openapi.yaml -v $(pwd):/specs swaggerapi/swagger-ui
```