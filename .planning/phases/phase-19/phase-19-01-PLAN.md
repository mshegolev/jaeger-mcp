---
phase: phase-19
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/jaeger_mcp/shaping.py
  - src/jaeger_mcp/models.py
  - src/jaeger_mcp/qa_tools.py
autonomous: true
requirements:
  - PROF-01
  - PROF-02
  - PROF-03
tags:
  - mcp-tool
  - qa-intelligence
  - aggregation

must_haves:
  truths:
    - "aggregate_span_statistics returns dicts containing total_duration_us and mean_duration_us for each operation"
    - "jaeger_span_statistics tool output is unchanged (no total/mean fields leak into OperationStatResult)"
    - "jaeger_test_profile MCP tool is registered and accepts tags dict + optional service/lookback_hours/limit"
    - "ProfileOp TypedDict models per-operation profiling rows with ms latency fields"
    - "TestProfileOutput TypedDict wraps tag_query, service_filter, trace_count, and operations list"
  artifacts:
    - path: "src/jaeger_mcp/shaping.py"
      provides: "aggregate_span_statistics extended with total_duration_us + mean_duration_us"
      contains: "total_duration_us"
    - path: "src/jaeger_mcp/models.py"
      provides: "OperationStats extended; ProfileOp + TestProfileOutput TypedDicts added"
      contains: "class ProfileOp"
    - path: "src/jaeger_mcp/qa_tools.py"
      provides: "jaeger_test_profile async MCP tool"
      contains: "async def jaeger_test_profile"
  key_links:
    - from: "src/jaeger_mcp/qa_tools.py"
      to: "src/jaeger_mcp/models.py"
      via: "imports ProfileOp and TestProfileOutput for the tool return type"
      pattern: "ProfileOp"
    - from: "src/jaeger_mcp/qa_tools.py"
      to: "src/jaeger_mcp/shaping.py"
      via: "facade._atest_profile consumes aggregate_span_statistics output with total/mean fields"
      pattern: "aggregate_span_statistics"
---

<objective>
Lay the foundation for the `jaeger_test_profile` tool (per D-PROF foundation, PROF-01/02/03).

This plan adds the data structures and the MCP tool surface. It extends the
single shared aggregation helper (`aggregate_span_statistics`) with two new
backward-compatible fields, defines the `ProfileOp` / `TestProfileOutput`
TypedDicts, and registers the `jaeger_test_profile` async MCP tool that will
delegate to `JaegerClient._atest_profile` (built in Plan 02).

Purpose: Plan 02 (facade + tests) cannot be written until these types and the
tool registration exist — the protocol test asserts the tool is registered, and
the facade returns `TestProfileOutput`. This plan ships the contract.
Output: Extended `shaping.py`, extended `models.py`, new tool in `qa_tools.py`.
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

# Structural analogs (read these before editing):
@src/jaeger_mcp/shaping.py
@src/jaeger_mcp/models.py
@src/jaeger_mcp/qa_tools.py
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Extend aggregate_span_statistics with total + mean fields (backward compatible)</name>
  <files>src/jaeger_mcp/shaping.py</files>
  <read_first>
    - src/jaeger_mcp/shaping.py (lines 325-363: aggregate_span_statistics full body; note the stats.append dict literal and the sorted(ops) alphabetical sort)
    - src/jaeger_mcp/models.py (lines 165-172: OperationStats TypedDict — the dict shape this helper returns)
    - .planning/phases/phase-19/19-CONTEXT.md (decision "Per-Operation Stats & Aggregation (PROF-02)": total_duration_us = sum of span durations; mean_duration_us = total_duration_us // count; backward compat via additive TypedDict fields)
  </read_first>
  <behavior>
    - Test: aggregate_span_statistics on a trace with one span of duration 5000 us returns a dict where total_duration_us == 5000 and mean_duration_us == 5000.
    - Test: aggregate_span_statistics on a trace with two spans of the same operation, durations 3000 and 7000 us, returns total_duration_us == 10000 and mean_duration_us == 5000.
    - Test: existing fields (count, p50_duration_us, p95_duration_us, p99_duration_us, error_count, error_rate) are still present and unchanged.
    - Regression test: the dict still sorts operations alphabetically by operation name (the profiling re-sort happens in the facade, NOT here).
  </behavior>
  <action>
    In the `stats.append({...})` dict literal inside aggregate_span_statistics, add two keys BEFORE the closing brace:
    - `total_duration_us`: int — sum of the `durations` list for this operation. Compute as `sum(durations)`.
    - `mean_duration_us`: int — `total_duration_us // count` when count > 0 else 0.

    Name the local for the sum explicitly (e.g. `total = sum(durations)`) to keep the append literal readable. Do NOT change the alphabetical `sorted(ops)` ordering — the total-wall-time-descending sort is a profiling concern owned by the facade in Plan 02, not the shared aggregation helper.

    Also update the function docstring to mention the two new fields in the returned dicts. No other caller behavior changes: `_aspan_statistics` constructs OperationStatResult by named keys and ignores the new keys; `_acompare_windows` and `_adetect_anomalies` read by named key too.
  </action>
  <verify>
    <automated>cd /opt/develop/aiqa/mcps/jaeger-mcp &amp;&amp; python -c "from jaeger_mcp.shaping import aggregate_span_statistics as a; r=a([{'traceID':'t','spans':[{'operationName':'GET /x','duration':3000},{'operationName':'GET /x','duration':7000}],'processes':{}}]); d=r[0]; assert d['total_duration_us']==10000, d; assert d['mean_duration_us']==5000, d; assert d['count']==2, d; print('ok', d)"</automated>
  </verify>
  <done>
    aggregate_span_statistics returns dicts containing total_duration_us (== sum of durations) and mean_duration_us (== total // count) for every operation. Existing field values are unchanged. The helper still sorts alphabetically by operation name.
  </done>
</task>

<task type="auto">
  <name>Task 2: Extend OperationStats + add ProfileOp and TestProfileOutput TypedDicts in models.py</name>
  <files>src/jaeger_mcp/models.py</files>
  <read_first>
    - src/jaeger_mcp/models.py (lines 162-179: OperationStats + SpanStatisticsOutput — add the two new fields to OperationStats only, NOT to SpanStatisticsOutput)
    - src/jaeger_mcp/models.py (lines 316-347: RegressionOp + RegressionDiffOutput — the structural template for ProfileOp + TestProfileOutput; note the section comment style "# ── QA ... ───" and ms-unit convention)
    - .planning/phases/phase-19/19-CONTEXT.md (decisions "Per-Operation Stats & Aggregation (PROF-02)" for the ProfileOp field list and "Output Shape & Sorting (PROF-03)" for TestProfileOutput fields)
  </read_first>
  <action>
    Per D-PROF-02 and D-PROF-03:

    1. Extend the `OperationStats` TypedDict (around line 165) by adding, after `error_rate: float`, two fields:
       - `total_duration_us: int`
       - `mean_duration_us: int`
       Do NOT add these to `SpanStatisticsOutput` — that is the wire shape of jaeger_span_statistics and must stay unchanged (backward-compat constraint).

    2. Add a new section near the end of the file (after the QA Regression Diff section, after RegressionDiffOutput) with a comment header in the established style: `# ── QA Test Performance Profiling ────────────────────────────────────────────`

    3. Define `class ProfileOp(TypedDict)` with a one-line docstring "Per-operation performance row in a test-run profile." and these fields (all latency in ms, per CONTEXT.md ms convention matching RegressionOp):
       - `operation: str`
       - `total_wall_time_ms: int`
       - `call_count: int`
       - `mean_ms: int`
       - `p50_ms: int`
       - `p95_ms: int`
       - `p99_ms: int`
       - `error_count: int`
       - `error_rate: float`

    4. Define `class TestProfileOutput(TypedDict)` with a one-line docstring "Aggregated test-run profile result for jaeger_test_profile." and these fields:
       - `tag_query: dict[str, str]`
       - `service_filter: str | None`
       - `trace_count: int`
       - `operations: list[ProfileOp]`

    Add `ProfileOp` and `TestProfileOutput` to any TYPE_CHECKING export block if RegressionDiffOutput is exported there — mirror whatever pattern models.py uses for its other QA TypedDicts.
  </action>
  <verify>
    <automated>cd /opt/develop/aiqa/mcps/jaeger-mcp &amp;&amp; python -c "from jaeger_mcp.models import OperationStats, ProfileOp, TestProfileOutput; import typing; assert 'total_duration_us' in OperationStats.__annotations__, OperationStats.__annotations__; assert 'mean_duration_us' in OperationStats.__annotations__; assert set(ProfileOp.__annotations__) == {'operation','total_wall_time_ms','call_count','mean_ms','p50_ms','p95_ms','p99_ms','error_count','error_rate'}, ProfileOp.__annotations__; assert set(TestProfileOutput.__annotations__) == {'tag_query','service_filter','trace_count','operations'}, TestProfileOutput.__annotations__; assert 'total_duration_us' not in __import__('jaeger_mcp.models', fromlist=['SpanStatisticsOutput']).SpanStatisticsOutput.__annotations__; print('ok')"</automated>
  </verify>
  <done>
    OperationStats has total_duration_us and mean_duration_us. ProfileOp and TestProfileOutput exist with exactly the field sets specified. SpanStatisticsOutput is unchanged (no total/mean fields).
  </done>
</task>

<task type="auto">
  <name>Task 3: Add jaeger_test_profile async MCP tool to qa_tools.py</name>
  <files>src/jaeger_mcp/qa_tools.py</files>
  <read_first>
    - src/jaeger_mcp/qa_tools.py (lines 1-18: imports, _MAX_SERVICES, _Endpoint; lines 21-62: jaeger_find_test_traces signature — the tag dict + service/lookback_hours/limit param shape to mirror; lines 160-246: jaeger_regression_diff — the tool that instantiates JaegerClient(http_client) and calls facade._aregression_diff, plus its markdown-summary pattern and output.ok/output.fail usage; line 246: __all__)
    - src/jaeger_mcp/facade.py (lines 1260-1336: _afind_test_traces tag-discovery loop — NOT to be inlined here; the tool delegates to facade._atest_profile which is built in Plan 02)
    - .planning/phases/phase-19/19-CONTEXT.md (decision "Module Placement & Integration": tool instantiates JaegerClient(http_client) and calls facade._atest_profile; decision "Error & Edge Cases": no matches → trace_count=0 + empty operations + summary naming the tags)
  </read_first>
  <action>
    Per D-PROF-01 and D-PROF-integration:

    1. Update the import line `from jaeger_mcp.models import ...` (currently line 14) to also import `ProfileOp` and `TestProfileOutput`. Keep existing imports (FindTestTracesOutput, RegressionDiffOutput, TestTraceMatch).

    2. Add a new `@mcp.tool(...)` decorated async function `jaeger_test_profile` AFTER `jaeger_regression_diff` and BEFORE `__all__`. Mirror the decorator block of `jaeger_find_test_traces` exactly: `name="jaeger_test_profile"`, `annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}`, `structured_output=True`.

    3. Parameters (mirror jaeger_find_test_traces bounds per CONTEXT.md "Tag Input Shape (PROF-01)"):
       - `tags: Annotated[dict[str, str], Field(description="Tag key-value pairs scoping the test run — e.g. {'test.run_id': 'abc123'} or {'allure.id': 'TC-42'}.")]` — required, no default.
       - `service: Annotated[str | None, Field(description="Jaeger service name. If omitted, all services (up to 20) are searched concurrently.")] = None`
       - `lookback_hours: Annotated[int, Field(description="Hours back from now to search.", ge=1, le=168)] = 1`
       - `limit: Annotated[int, Field(description="Maximum traces to aggregate.", ge=1, le=500)] = 50`

    4. Return type annotation: `-> TestProfileOutput`.

    5. Docstring: one short paragraph — "Aggregate per-operation latency hotspots across all traces matching the supplied tag query. Operations are ranked by total wall time descending so the most expensive appear first."

    6. Body — wrap in try/except exactly like jaeger_regression_diff:
       - `from jaeger_mcp.facade import JaegerClient`
       - `http_client = await get_client()`
       - `facade = JaegerClient(http_client)`
       - `result = await facade._atest_profile(tags=tags, service=service, lookback_hours=lookback_hours, limit=limit)` (the facade method is built in Plan 02; calling it here is correct since Plan 02 depends on this plan and ships before execution is verified end-to-end).
       - Build markdown summary: if `result["trace_count"] == 0` → `f"No traces found matching tags {tags}."`; else name the top operation: `f"Profiled {result['trace_count']} trace(s); top operation '{result['operations'][0]['operation']}' consumed {result['operations'][0]['total_wall_time_ms']} ms wall time."`
       - `return output.ok(result, md)`
       - except branch: `return output.fail(exc, "jaeger_test_profile")`

    7. Update `__all__` (line 246) to include `"jaeger_test_profile"`: `__all__ = ["jaeger_find_test_traces", "jaeger_regression_diff", "jaeger_test_profile"]`

    Do NOT inline the tag-discovery loop — that belongs to the facade. The tool is a thin wrapper that instantiates JaegerClient and delegates, exactly like jaeger_regression_diff.
  </action>
  <verify>
    <automated>cd /opt/develop/aiqa/mcps/jaeger-mcp &amp;&amp; python -c "import inspect, jaeger_mcp.qa_tools as q; f=q.jaeger_test_profile; assert inspect.iscoroutinefunction(f); assert 'jaeger_test_profile' in q.__all__; src=inspect.getsource(f); assert 'facade._atest_profile' in src; assert 'JaegerClient(http_client)' in src; assert 'output.fail(exc' in src; print('ok')"</automated>
  </verify>
  <done>
    jaeger_test_profile is an async MCP tool with the exact param shape (tags dict required; service/lookback_hours/limit optional with the specified bounds), delegates to facade._atest_profile via JaegerClient(http_client), builds the two markdown-summary branches, is in __all__, and is importable without error.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| MCP client → jaeger_test_profile tool | Untrusted caller supplies `tags` dict, `service`, `lookback_hours`, `limit` params |
| Tool → Jaeger HTTP API | The facade constructs `/traces?tags=...` with a JSON-serialized tags string; values reach an external HTTP query |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-19-01 | Tampering | `tags` dict values | mitigate | Pydantic Field type `dict[str, str]` coerces/rejects non-string values; JSON serialization is standard-library `json.dumps`. No tag value is interpolated into a URL path — it goes into a query param consumed by Jaeger's own parser. |
| T-19-02 | Tampering | `lookback_hours` / `limit` bounds | mitigate | Field constraints `ge=1, le=168` and `ge=1, le=500` reject out-of-range ints at the MCP layer before any HTTP call. |
| T-19-03 | Information Disclosure | markdown summary echoing tags | accept | The summary echoes the caller-supplied tag query back to the caller in their own session; no cross-user leakage. Tag values are test-run identifiers, not secrets. |
| T-19-04 | Repudiation | tool invocation | accept | Read-only analytics tool; Jaeger server-side access logs are the audit authority, not this tool. |
| T-19-SC | Tampering | (none) | accept | No new package installs in this plan — only stdlib + existing project deps are used. |
</threat_model>

<verification>
- `python -c "from jaeger_mcp.shaping import aggregate_span_statistics"` exits 0
- `python -c "from jaeger_mcp.models import ProfileOp, TestProfileOutput, OperationStats"` exits 0
- `python -c "import jaeger_mcp.qa_tools as q; assert 'jaeger_test_profile' in q.__all__"` exits 0
- Full existing test suite still green: `pytest tests/ -q` exits 0 (confirms backward compat — span_statistics output unchanged).
- Per-task automated checks (above) all pass.
</verification>

<success_criteria>
- aggregate_span_statistics emits total_duration_us + mean_duration_us; existing jaeger_span_statistics output is byte-for-byte unchanged (verified by the full test suite passing).
- ProfileOp and TestProfileOutput TypedDicts exist with the exact field sets from CONTEXT.md.
- jaeger_test_profile async MCP tool is registered, in __all__, delegates to facade._atest_profile, and mirrors the find_test_traces param contract.
- No task reduces a PROF requirement below what CONTEXT.md specifies.
</success_criteria>

<output>
Create `.planning/phases/phase-19/phase-19-01-SUMMARY.md` when done.
</output>
