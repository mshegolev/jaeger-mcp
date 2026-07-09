"""MCP tools for QA test trace correlation."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import Field

from jaeger_mcp import output
from jaeger_mcp._mcp import get_client, mcp
from jaeger_mcp.models import (
    FindTestTracesOutput,
    ProfileOp,
    RegressionDiffOutput,
    TestProfileOutput,
    TestTraceMatch,
)
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
                    "start": start_us,
                    "end": now_us,
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
        total = len(matches)
        matches = matches[:limit]

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


@mcp.tool(
    name="jaeger_regression_diff",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    },
    structured_output=True,
)
async def jaeger_regression_diff(
    service: Annotated[
        str,
        Field(
            description="Jaeger service name to analyse for regressions.",
            pattern=r"^[a-zA-Z0-9._:\-]+$",
        ),
    ],
    baseline_start: Annotated[
        int,
        Field(
            description="Baseline window start time (Unix microseconds).",
            ge=0,
        ),
    ],
    baseline_end: Annotated[
        int,
        Field(
            description="Baseline window end time (Unix microseconds).",
            ge=0,
        ),
    ],
    comparison_start: Annotated[
        int | None,
        Field(
            description="Comparison window start time (Unix microseconds). Defaults to now minus 15 minutes.",
            ge=0,
        ),
    ] = None,
    comparison_end: Annotated[
        int | None,
        Field(
            description="Comparison window end time (Unix microseconds). Defaults to now.",
            ge=0,
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            description="Maximum traces to fetch per window.",
            ge=10,
            le=1000,
        ),
    ] = 100,
) -> RegressionDiffOutput:
    """Compare two Jaeger time windows and classify per-operation regressions.

    Fetches traces from the baseline and comparison windows, then classifies
    each operation as regressed, recovered, appeared, or removed. Results are
    sorted by severity score (0-100) descending for easy triage.
    """
    try:
        now_us = int(time.time() * 1_000_000)
        if comparison_end is None:
            comparison_end = now_us
        if comparison_start is None:
            comparison_start = now_us - (15 * 60 * 1_000_000)

        from jaeger_mcp.facade import JaegerClient

        http_client = await get_client()
        facade = JaegerClient(http_client)
        result = await facade._aregression_diff(
            service,
            baseline_start,
            baseline_end,
            comparison_start,
            comparison_end,
            limit=limit,
        )

        n = len(result["operations"])
        r = result["regressed_count"]
        rec = result["recovered_count"]
        app = result["appeared_count"]
        rem = result["removed_count"]
        md = (
            f"Found {n} operation(s) with changes for service '{service}': "
            f"{r} regressed, {rec} recovered, {app} appeared, {rem} removed."
        )
        return output.ok(result, md)

    except Exception as exc:
        return output.fail(exc, "jaeger_regression_diff")


@mcp.tool(
    name="jaeger_test_profile",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
    },
    structured_output=True,
)
async def jaeger_test_profile(
    tags: Annotated[
        dict[str, str],
        Field(
            description=(
                "Tag key-value pairs scoping the test run — e.g."
                " {'test.run_id': 'abc123'} or {'allure.id': 'TC-42'}."
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
            description="Maximum traces to aggregate.",
            ge=1,
            le=500,
        ),
    ] = 50,
) -> TestProfileOutput:
    """Aggregate per-operation latency hotspots across all traces matching the supplied tag query.

    Operations are ranked by total wall time descending so the most expensive
    appear first.
    """
    try:
        from jaeger_mcp.facade import JaegerClient

        http_client = await get_client()
        facade = JaegerClient(http_client)
        result = await facade._atest_profile(
            tags=tags,
            service=service,
            lookback_hours=lookback_hours,
            limit=limit,
        )

        if result["trace_count"] == 0:
            md = f"No traces found matching tags {tags}."
        else:
            top = result["operations"][0]
            md = (
                f"Profiled {result['trace_count']} trace(s); top operation"
                f" '{top['operation']}' consumed {top['total_wall_time_ms']} ms"
                " wall time."
            )
        return output.ok(result, md)

    except Exception as exc:
        return output.fail(exc, "jaeger_test_profile")


__all__ = ["jaeger_find_test_traces", "jaeger_regression_diff", "jaeger_test_profile"]
