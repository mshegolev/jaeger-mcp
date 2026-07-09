---
phase: phase-19
plan: 02
type: execute
wave: 2
depends_on:
  - phase-19-01
files_modified:
  - src/jaeger_mcp/facade.py
  - tests/test_test_profile.py
  - tests/test_protocol.py
autonomous: true
requirements:
  - PROF-01
  - PROF-02
  - PROF-03
  - PROF-04
tags:
  - facade
  - tests
  - qa-intelligence

must_haves:
  truths:
    - "JaegerClient.test_profile(tags, ...) returns a TestProfileOutput with operations sorted by total_wall_time_ms descending"
    - "Calling jaeger_test_profile with a tag pair that matches traces returns per-operation rows with total wall time, call count, mean/p50/p95/p99 ms, and error count"
    - "Calling jaeger_test_profile with tags matching no traces returns trace_count=0 and an empty operations list"
    - "The tool is registered in the MCP catalogue (EXPECTED_TOOLS in test_protocol.py passes)"
    - "jaeger_span_statistics still produces its original output shape after the aggregate_span_statistics extension (regression test)"
  artifacts:
    - path: "src/jaeger_mcp/facade.py"
      provides: "_atest_profile private async + test_profile public sync facade methods"
      contains: "_atest_profile"
    - path: "tests/test_test_profile.py"
      provides: "full test coverage for jaeger_test_profile (ordering, ms units, edge cases, span_statistics backward compat)"
      contains: "jaeger_test_profile"
    - path: "tests/test_protocol.py"
      provides: "jaeger_test_profile added to EXPECTED_TOOLS so the registration test passes"
      contains: "jaeger_test_profile"
  key_links:
    - from: "src/jaeger_mcp/facade.py"
      to: "src/jaeger_mcp/shaping.py"
      via: "_atest_profile calls aggregate_span_statistics (aliased _aggregate_span_statistics) and reads total_duration_us + mean_duration_us"
      pattern: "_aggregate_span_statistics"
    - from: "src/jaeger_mcp/facade.py"
      to: "src/jaeger_mcp/models.py"
      via: "builds ProfileOp rows and returns TestProfileOutput"
      pattern: "ProfileOp"
    - from: "tests/test_test_profile.py"
      to: "src/jaeger_mcp/qa_tools.py"
      via: "imports jaeger_test_profile and invokes it with respx-mocked HTTP"
      pattern: "jaeger_test_profile"
---

<objective>
Complete the `jaeger_test_profile` feature (per D-PROF facade+tests, PROF-04 + the full PROF-01..03 behavior coverage).

Plan 02 builds the facade methods (`_atest_profile` private async + `test_profile`
public sync via `asyncio.run`) that the tool registered in Plan 01 delegates to,
reusing the tag-discovery loop from `_afind_test_traces` and the extended
`aggregate_span_statistics`. It then adds full test coverage in a new
`tests/test_test_profile.py` (mirroring `tests/test_regression_diff.py`) and
registers the tool in `tests/test_protocol.py`'s `EXPECTED_TOOLS`.

Purpose: This is where the feature becomes verifiable end-to-end. The facade
closes the loop between the tool and the shared aggregation; the tests prove
PROF-01..04 are satisfied and that the aggregation extension is backward
compatible.
Output: Extended `facade.py`, new `tests/test_test_profile.py`, extended `tests/test_protocol.py`.
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/phase-19/19-CONTEXT.md
@.planning/REQUIREMENTS.md

# Plan 01 shipped these (the contract this plan consumes):
@.planning/phases/phase-19/phase-19-01-PLAN.md

# Structural analogs (read these before editing):
@src/jaeger_mcp/facade.py
@src/jaeger_mcp/qa_tools.py
@tests/test_regression_diff.py
@tests/test_protocol.py
@src/jaeger_mcp/shaping.py
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add _atest_profile + test_profile to JaegerClient in facade.py</name>
  <files>src/jaeger_mcp/facade.py</files>
  <read_first>
    - src/jaeger_mcp/facade.py (lines 1260-1353: _afind_test_traces + find_test_traces — the tag-discovery loop to reuse verbatim: /services fan-out capped at _FIND_TEST_MAX_SERVICES, /traces with json.dumps(tags) + start/end us window, dedupe by traceID, collect raw_traces)
    - src/jaeger_mcp/facade.py (lines 1355-1477: _aregression_diff + regression_diff — the private-async + public-sync-via-asyncio.run pattern to mirror; note how RegressionOp rows are built with // 1000 ms conversion then sorted by a key descending)
    - src/jaeger_mcp/facade.py (lines 605-640: _aspan_statistics — confirms OperationStatResult is built by named keys, so the extended source dict's total/mean keys are ignored — the backward-compat guarantee)
    - src/jaeger_mcp/facade.py (line 47: `aggregate_span_statistics as _aggregate_span_statistics` import — already present, reuse the alias)
    - src/jaeger_mcp/facade.py (lines ~1-60: top imports — confirm OperationStats/RegressionOp/ProfileOp import location; add ProfileOp + TestProfileOutput to the models import)
    - .planning/phases/phase-19/19-CONTEXT.md (decision "Reuse Strategy": steps 1-3; decision "Per-Operation Stats & Aggregation": ms conversion formulas total_duration_us // 1000 and mean_duration_us // 1000)
  </read_first>
  <behavior>
    - Given mocked /services returning ["svc-a"] and /traces returning one trace with two spans of operation "GET /x" (durations 3000 and 7000 us) and one span of "GET /y" (duration 50000 us): test_profile returns trace_count == 1 and operations sorted with "GET /y" first (total_wall_time_ms == 50) then "GET /x" (total_wall_time_ms == 10).
    - "GET /x" row has call_count == 2, mean_ms == 5 (10000 // 1000 // 2 == 5, integer ms), p50/p95/p99 derived from the extended aggregation.
    - Given /traces returning empty data for all services: test_profile returns trace_count == 0 and operations == [].
    - An error span (tags include error=true) increments error_count on its operation and sets error_rate > 0.0.
  </behavior>
  <action>
    Per D-PROF-reuse and D-PROF-04:

    1. Update the `from jaeger_mcp.models import (...)` block at the top of facade.py to also import `ProfileOp` and `TestProfileOutput` (alongside the existing RegressionOp, RegressionDiffOutput, TestTraceMatch, FindTestTracesOutput imports). Mirror whatever grouping/ordering the existing import uses.

    2. Add `async def _atest_profile(self, tags: dict[str, str], service: str | None = None, lookback_hours: int = 1, limit: int = 50) -> TestProfileOutput:` AFTER `regression_diff` / its public sync wrapper (i.e., as the last method before `close`). Docstring: "Async implementation of :meth:`test_profile`."

       Body — reuse the EXACT discovery loop from `_afind_test_traces` (lines 1270-1304): service fan-out via `self._http.aget("/services")` capped at `_FIND_TEST_MAX_SERVICES`, build `(service, tags=json.dumps(tags), start, end, limit)` endpoints, `results = await self._http.aget_many(endpoints)`, dedupe by `traceID` into `raw_traces`. Do NOT call `_afind_test_traces` (it returns shaped TestTraceMatch rows, not raw traces) — copy the raw-trace collection loop. If the planner/executor sees clean duplication, inline the loop; a shared helper is optional and NOT required (CONTEXT.md leaves this to judgment).

       Then:
       - `raw_stats = _aggregate_span_statistics(raw_traces)` (the extended helper now includes total_duration_us + mean_duration_us).
       - Build `ops: list[ProfileOp]` by mapping each stat dict to a ProfileOp with ms conversions:
         - `total_wall_time_ms = s["total_duration_us"] // 1000`
         - `mean_ms = s["mean_duration_us"] // 1000`
         - `p50_ms = s["p50_duration_us"] // 1000`, `p95_ms = s["p95_duration_us"] // 1000`, `p99_ms = s["p99_duration_us"] // 1000`
         - `operation = s["operation"]`, `call_count = s["count"]`, `error_count = s["error_count"]`, `error_rate = s["error_rate"]`
       - `ops.sort(key=lambda o: o["total_wall_time_ms"], reverse=True)` — this is the PROF-03 hotspot-first ordering.
       - Return `TestProfileOutput(tag_query=tags, service_filter=service, trace_count=len(raw_traces), operations=ops)`.

    3. Add `def test_profile(self, tags: dict[str, str], service: str | None = None, lookback_hours: int = 1, limit: int = 50) -> TestProfileOutput:` immediately after `_atest_profile`. Docstring mirrors `find_test_traces` / `regression_diff` style: a short paragraph describing the per-operation hotspot profile, then Args/Returns/Raises sections (Raises: httpx.HTTPStatusError on HTTP-level failures). Body is `return asyncio.run(self._atest_profile(tags=tags, service=service, lookback_hours=lookback_hours, limit=limit))`.
  </action>
  <verify>
    <automated>cd /opt/develop/aiqa/mcps/jaeger-mcp &amp;&amp; python -c "import inspect, jaeger_mcp.facade as f; c=f.JaegerClient; assert inspect.iscoroutinefunction(c._atest_profile); assert hasattr(c,'test_profile') and not inspect.iscoroutinefunction(c.test_profile); src=inspect.getsource(c._atest_profile); assert '_aggregate_span_statistics' in src; assert 'total_wall_time_ms' in src; assert 'reverse=True' in src; print('ok')"</automated>
  </verify>
  <done>
    JaegerClient exposes _atest_profile (private async) and test_profile (public sync via asyncio.run). _atest_profile reuses the find_test_traces tag-discovery loop, calls _aggregate_span_statistics, builds ProfileOp rows with ms conversions, sorts by total_wall_time_ms descending, and returns a TestProfileOutput with trace_count and operations.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Create tests/test_test_profile.py with full behavior coverage</name>
  <files>tests/test_test_profile.py</files>
  <read_first>
    - tests/test_regression_diff.py (full file — the structural template: respx.mock + MockTransport, BASE url, _make_rd_trace builder, _ok(data) response helper, test functions calling jaeger_regression_diff directly)
    - src/jaeger_mcp/qa_tools.py (lines 21-62: jaeger_find_test_traces param shape; the jaeger_test_profile tool added in Plan 01 to import and call)
    - .planning/phases/phase-19/19-CONTEXT.md (decision "Error & Edge Cases": no matches → trace_count=0 + empty operations; specifics section: tests should cover no matches, single-op hotspot ordering, multi-op sort by total wall time desc, error_count/error_rate propagation, ms-unit conversion, aggregate_span_statistics extension + existing callers unaffected)
  </read_first>
  <behavior>
    - test_no_matching_traces: /services returns ["svc"], /traces returns {"data": []} → result trace_count == 0, operations == [], markdown summary starts with "No traces found".
    - test_single_operation_hotspot_first: two operations where op B has higher total duration → operations[0]["operation"] == B, sorted descending by total_wall_time_ms.
    - test_multi_op_sort_descending: three operations with distinct totals → [o["total_wall_time_ms"] for o in operations] is strictly non-increasing.
    - test_ms_unit_conversion: span duration 15_000 us → total_wall_time_ms == 15 (not 15000), mean_ms consistent.
    - test_error_count_and_rate_propagation: a span with error tag → its operation error_count >= 1 and error_rate > 0.0.
    - test_aggregate_span_statistics_extension: direct unit test on aggregate_span_statistics — total_duration_us and mean_duration_us correct for a multi-span operation.
    - test_span_statistics_output_unchanged: regression test calling jaeger_span_statistics (or _aspan_statistics) and asserting the result stats dicts do NOT contain total_duration_us / mean_duration_us keys (backward compat).
  </behavior>
  <action>
    Create tests/test_test_profile.py mirroring tests/test_regression_diff.py structure:

    1. Module docstring: "Tests for jaeger_test_profile MCP tool (mocked HTTP via respx)." explaining the tool calls JaegerClient._atest_profile which fan-outs /services + /traces.

    2. Imports: `from __future__ import annotations`, `import asyncio`, `import httpx`, `import pytest`, `import respx`, `from jaeger_mcp import _mcp`, `from jaeger_mcp.qa_tools import jaeger_test_profile`. Also `from jaeger_mcp.shaping import aggregate_span_statistics` for the direct extension unit test.

    3. Constants/helpers mirroring test_regression_diff.py:
       - `BASE = "https://jaeger.example.com"` (match the conftest/transport base used by test_regression_diff.py — read it to confirm the exact BASE).
       - `_make_tp_trace(trace_id, spans, ...)` — a builder that accepts a list of (operation, duration_us, has_error) tuples and returns a minimal Jaeger trace dict with a processes block. Keep it minimal but multi-span capable.
       - `_ok(data)` returning `httpx.Response(200, json={"data": data})`.
       - `_services(names)` returning `httpx.Response(200, json={"data": names})`.

    4. Use the respx routing pattern from test_regression_diff.py (mock `/api/services` and `/api/traces` — confirm the exact path prefix from the existing test file; Jaeger base path may be `/api/`). Call `jaeger_test_profile(tags={"test.run_id": "run-1"})` directly.

    5. Write the test functions listed in <behavior> above. Each test uses `with respx.mock:` and `httpx.AsyncClient(base_url=BASE, transport=...)` or the project's established client fixture — read test_regression_diff.py's exact client-construction pattern and mirror it (the test must set the Jaeger base URL the way the project's test harness expects).

    6. For test_span_statistics_output_unchanged: construct a JaegerClient, call _aspan_statistics or the jaeger_span_statistics tool with one trace, and assert no key in the returned stat dict starts with 'total_' or 'mean_' (i.e., the wire shape OperationStatResult / SpanStatisticsOutput are unaffected).

    All assertions must be exact-value (durations, counts, ordering), never "truthy" or "looks right".
  </action>
  <verify>
    <automated>cd /opt/develop/aiqa/mcps/jaeger-mcp &amp;&amp; python -m pytest tests/test_test_profile.py -q</automated>
  </verify>
  <done>
    tests/test_test_profile.py exists and pytest tests/test_test_profile.py exits 0. Coverage includes: no-match empty result, single-op hotspot ordering, multi-op descending sort, ms-unit conversion, error propagation, the aggregate_span_statistics extension correctness, and a backward-compat regression test proving span_statistics output is unchanged.
  </done>
</task>

<task type="auto">
  <name>Task 3: Register jaeger_test_profile in EXPECTED_TOOLS in test_protocol.py</name>
  <files>tests/test_protocol.py</files>
  <read_first>
    - tests/test_protocol.py (lines 119-133: the EXPECTED_TOOLS dict entries for jaeger_find_test_traces and jaeger_regression_diff — copy this shape exactly; note required_params is a set of param-name strings, optional_params likewise)
    - src/jaeger_mcp/qa_tools.py (the jaeger_test_profile signature from Plan 01: tags is required; service, lookback_hours, limit are optional)
  </read_first>
  <action>
    Per D-PROF-integration:

    In the `EXPECTED_TOOLS` dict (around line 132, after the jaeger_regression_diff entry and before the closing `}`), add a new key:

    "jaeger_test_profile": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"tags"},
        "optional_params": {"service", "lookback_hours", "limit"},
    },

    Match the exact formatting (indentation, trailing comma) of the adjacent entries. required_params and optional_params must match the Plan 01 tool signature exactly: `tags` required; `service`, `lookback_hours`, `limit` optional.

    No other change to test_protocol.py.
  </action>
  <verify>
    <automated>cd /opt/develop/aiqa/mcps/jaeger-mcp &amp;&amp; python -m pytest tests/test_protocol.py -q</automated>
  </verify>
  <done>
    EXPECTED_TOOLS contains jaeger_test_profile with the correct hint/params shape. tests/test_protocol.py passes (test_all_tools_registered, test_tool_hints, and the parametrized per-tool test all green), confirming the tool is registered in the live MCP catalogue.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| MCP client → jaeger_test_profile → facade | Caller-supplied tags/service/lookback/limit cross into the facade, which builds Jaeger HTTP query params |
| facade → Jaeger HTTP API | `json.dumps(tags)` lands in a `?tags=` query param; service name in `?service=` |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-19-05 | Tampering | service name interpolation into `/traces?service=` | mitigate | The MCP-layer Field has no regex pattern here (matches find_test_traces precedent); the facade passes it straight to httpx query params which URL-encodes. service is also bounded by the Jaeger server's own /services list when omitted. No SQL/path injection surface (read-only GET). |
| T-19-06 | Information Disclosure | total/mean/percentile latency values returned to caller | accept | Caller already has access to the traces (they queried by their own tags); the aggregation discloses nothing beyond what the raw spans contain. No cross-tenant data — tags scope to the caller's own test run. |
| T-19-07 | Denial of Service | large trace sets (limit up to 500) | mitigate | `limit` is bounded `ge=1, le=500` at the MCP layer (Plan 01); aggregation is O(spans) in-memory; no unbounded recursion. |
| T-19-08 | Elevation of Privilege | facade method callable in-process | accept | `test_profile()` is a read-only analytics method on JaegerClient; no authz decision is made, no writes. Same trust level as existing find_test_traces / regression_diff. |
| T-19-SC | Tampering | (none) | accept | No new package installs — only existing project deps (httpx, respx, pytest) used. |
</threat_model>

<verification>
- `python -m pytest tests/test_test_profile.py -q` exits 0 (new feature tests).
- `python -m pytest tests/test_protocol.py -q` exits 0 (tool registration).
- `python -m pytest tests/ -q` exits 0 (full suite — confirms backward compat and no regressions from the aggregate_span_statistics extension).
- `python -c "from jaeger_mcp.facade import JaegerClient; JaegerClient.test_profile; JaegerClient._atest_profile"` exits 0.
</verification>

<success_criteria>
- JaegerClient.test_profile() returns a TestProfileOutput whose operations are sorted by total_wall_time_ms descending (PROF-03, PROF-04).
- Each ProfileOp row carries total wall time, call count, mean/p50/p95/p99 ms, error count, error rate (PROF-02).
- No-match case returns trace_count=0 and empty operations (edge case).
- jaeger_span_statistics output is unchanged — regression test proves the aggregate_span_statistics extension is backward compatible.
- jaeger_test_profile is registered in the MCP catalogue (EXPECTED_TOOLS test passes, PROF-01).
</success_criteria>

<output>
Create `.planning/phases/phase-19/phase-19-02-SUMMARY.md` when done.
</output>
