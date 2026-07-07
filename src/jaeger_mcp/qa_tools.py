"""MCP tools for QA test trace correlation."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import Field

from jaeger_mcp import output
from jaeger_mcp._mcp import get_client, mcp
from jaeger_mcp.models import FindTestTracesOutput, TestTraceMatch
from jaeger_mcp.shaping import shape_trace_summary

_MAX_SERVICES = 20
_Endpoint = tuple[str, dict[str, Any] | None]


@mcp.tool(
    name="jaeger_find_test_traces",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    },
    structured_output=True,
)
async def jaeger_find_test_traces(
    tags: Annotated[
        dict[str, str],
        Field(
            description=(
                "Tag key-value pairs to filter traces. Any framework tag schema"
                " works — e.g. {'allure.id': 'TC-42'} or {'test.run_id': 'abc123'}."
            ),
        ),
    ],
    service: Annotated[
        str | None,
        Field(
            description=("Jaeger service name. If omitted, all services (up to 20) are searched concurrently."),
        ),
    ] = None,
    lookback_hours: Annotated[
        int,
        Field(
            description="Hours back from now to search.",
            ge=1,
            le=168,
        ),
    ] = 1,
    limit: Annotated[
        int,
        Field(
            description="Maximum traces to return total.",
            ge=1,
            le=500,
        ),
    ] = 50,
) -> FindTestTracesOutput:
    """Find Jaeger traces matching the supplied tag query.

    Accepts any tag key-value schema (Allure, pytest, custom) without
    normalization. When service is omitted, searches all known services
    concurrently (capped at 20). Results are sorted newest-first.
    """
    try:
        client = await get_client()

        if service is not None:
            services: list[str] = [service]
        else:
            svc_data = await client.aget("/services") or {}
            all_services: list[str] = svc_data.get("data") or []
            services = all_services[:_MAX_SERVICES]

        now_us = int(time.time() * 1_000_000)
        start_us = now_us - (lookback_hours * 3600 * 1_000_000)
        tags_str = json.dumps(tags)

        endpoints: list[_Endpoint] = [
            (
                "/traces",
                {
                    "service": svc,
                    "tags": tags_str,
                    "start": start_us // 1000,
                    "end": now_us // 1000,
                    "limit": limit,
                },
            )
            for svc in services
        ]

        results = await client.aget_many(endpoints)

        seen: set[str] = set()
        raw_traces: list[dict] = []
        for res in results:
            for trace in (res or {}).get("data") or []:
                tid: str = trace.get("traceID", "")
                if tid and tid not in seen:
                    seen.add(tid)
                    raw_traces.append(trace)

        matches: list[TestTraceMatch] = []
        for trace in raw_traces:
            summary = shape_trace_summary(trace)
            start_time_us = summary["start_time_us"]
            if start_time_us is not None:
                start_iso = datetime.fromtimestamp(start_time_us / 1_000_000, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            else:
                start_iso = ""
            matches.append(
                TestTraceMatch(
                    trace_id=summary["trace_id"],
                    root_service=summary["root_service"],
                    duration_ms=summary["duration_us"] // 1000,
                    start_time=start_iso,
                    has_error=summary["errors_count"] > 0,
                    span_count=summary["span_count"],
                )
            )

        matches.sort(key=lambda m: m["start_time"], reverse=True)
        matches = matches[:limit]
        total = len(matches)

        result_out: FindTestTracesOutput = {
            "traces": matches,
            "total_count": total,
            "tag_query": tags,
            "service_filter": service,
        }

        if total == 0:
            md = f"No traces found matching tags {tags}"
        else:
            md = f"Found {total} trace(s) matching {tags}"

        return output.ok(result_out, md)

    except Exception as exc:
        return output.fail(exc, "jaeger_find_test_traces")


__all__ = ["jaeger_find_test_traces"]
