---
phase: phase-17
plan: 02
type: execute
wave: 2
depends_on:
  - phase-17-01
files_modified:
  - src/jaeger_mcp/facade.py
  - tests/test_qa_tools.py
  - tests/test_protocol.py
autonomous: true
requirements:
  - TTC-01
  - TTC-02
  - TTC-03
  - TTC-04
must_haves:
  truths:
    - "JaegerClient.find_test_traces() callable in-process without MCP transport (facade pattern)"
    - "Tests confirm tool returns empty list for no-match, correct fields for match, and sorts newest-first"
    - "test_protocol.py includes jaeger_find_test_traces in EXPECTED_TOOLS and passes"
  artifacts:
    - path: src/jaeger_mcp/facade.py
      provides: find_test_traces() sync facade method on JaegerClient
      contains: "def find_test_traces"
    - path: tests/test_qa_tools.py
      provides: mocked HTTP tests for jaeger_find_test_traces
      exports: ["test_find_test_traces_single_service", "test_find_test_traces_no_match", "test_find_test_traces_multi_service"]
    - path: tests/test_protocol.py
      provides: updated EXPECTED_TOOLS with jaeger_find_test_traces entry
      contains: "jaeger_find_test_traces"
  key_links:
    - from: src/jaeger_mcp/facade.py
      to: src/jaeger_mcp/models.py
      via: "from .models import FindTestTracesOutput"
      pattern: "find_test_traces"
    - from: tests/test_qa_tools.py
      to: src/jaeger_mcp/qa_tools.py
      via: "direct async call via mcp fixture or responses mock"
      pattern: "jaeger_find_test_traces"
---

<objective>
Add the `find_test_traces()` sync facade method to JaegerClient, create a
comprehensive mocked-HTTP test suite in `tests/test_qa_tools.py`, and update
`tests/test_protocol.py` EXPECTED_TOOLS to include `jaeger_find_test_traces`.

Purpose: Delivers TTC-04 (programmatic in-process use) and provides test
coverage proving TTC-01 through TTC-03 work correctly.

Output: One new facade method, one new test file, one updated test file.
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/phases/phase-17/17-CONTEXT.md
@.planning/phases/phase-17/phase-17-PATTERNS.md
@.planning/phases/phase-17/phase-17-01-SUMMARY.md
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add find_test_traces() facade method to JaegerClient in facade.py</name>
  <files>src/jaeger_mcp/facade.py</files>
  <read_first>
    - src/jaeger_mcp/facade.py — read entirely to understand: (a) how _asearch_traces
      and _adetect_anomalies private async helpers are structured, (b) the sync
      public method pattern using asyncio.run(), (c) existing imports, (d) where to
      append without breaking the class structure
    - src/jaeger_mcp/qa_tools.py (created in plan 01) — read to understand the
      internal _afind_test_traces async logic so the facade helper delegates correctly
  </read_first>
  <action>
    Add two methods to JaegerClient in facade.py. Follow the exact pattern of
    existing private+public method pairs (e.g. _adetect_anomalies + detect_anomalies).

    Private async helper `_afind_test_traces`:
    - Signature: async def _afind_test_traces(self, tags: dict[str, str],
        service: str | None = None, lookback_hours: int = 1, limit: int = 50)
        -> FindTestTracesOutput
    - This method should delegate to the same logic already in qa_tools.py. The
      cleanest approach (consistent with _adetect_anomalies pattern) is to call
      the qa_tools async function directly by importing it, OR to replicate the
      same HTTP calls using self._client (the JaegerHTTPClient). Choose whichever
      is cleaner per the existing pattern — if other _a* methods call module-level
      async functions, follow that; if they call self._client directly, follow that.
    - Key constraint: must return FindTestTracesOutput

    Public sync wrapper `find_test_traces`:
    - Signature: def find_test_traces(self, tags: dict[str, str],
        service: str | None = None, lookback_hours: int = 1, limit: int = 50)
        -> FindTestTracesOutput
    - Body: return asyncio.run(self._afind_test_traces(tags=tags, service=service,
        lookback_hours=lookback_hours, limit=limit))
    - Docstring: "Return traces matching ``tags`` for in-process callers."

    Import to add (at top with other model imports):
    - from .models import FindTestTracesOutput (add to existing models import line
      or add new line — do not duplicate)

    Do NOT change any existing methods. Append both new methods at the end of
    the JaegerClient class body.
  </action>
  <verify>
    <automated>cd /opt/develop/aiqa/mcps/jaeger-mcp && python -c "from jaeger_mcp.facade import JaegerClient; import inspect; assert hasattr(JaegerClient, 'find_test_traces'); sig = inspect.signature(JaegerClient.find_test_traces); params = list(sig.parameters); assert params == ['self','tags','service','lookback_hours','limit'], params; print('facade OK')"</automated>
  </verify>
  <acceptance_criteria>
    - JaegerClient.find_test_traces method exists with correct signature
    - Method is synchronous (not async) — callers do not need asyncio.run()
    - Return type annotation is FindTestTracesOutput
    - mypy src/jaeger_mcp/facade.py --ignore-missing-imports passes
    - Existing facade methods (search_traces, detect_anomalies, etc.) still work
  </acceptance_criteria>
  <done>JaegerClient.find_test_traces() callable; mypy clean; no regressions in existing methods</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Create tests/test_qa_tools.py with mocked HTTP tests</name>
  <files>tests/test_qa_tools.py</files>
  <read_first>
    - tests/test_tools_integration.py — read entirely; this is the exact structural
      analog (responses library mock, async tool invocation pattern, fixture setup)
    - src/jaeger_mcp/qa_tools.py (plan 01 output) — read to understand the Jaeger
      API URL patterns, query params, and response shape expected
    - src/jaeger_mcp/models.py — verify TestTraceMatch and FindTestTracesOutput field names
  </read_first>
  <behavior>
    - test_find_test_traces_single_service: Given service="frontend" and
      tags={"allure.id": "TC-42"}, mocked /api/traces returns 2 traces;
      expect total_count=2, traces sorted newest-first, each trace has all 6 fields
      (trace_id, root_service, duration_ms, start_time, has_error, span_count)

    - test_find_test_traces_no_match: Mocked response returns {"data": []};
      expect output.traces == [], total_count == 0, no exception raised

    - test_find_test_traces_multi_service: service=None triggers /api/services call
      returning ["svc-a", "svc-b"]; then concurrent /api/traces called for each;
      expect results deduplicated and merged; total_count correct

    - test_find_test_traces_limit: Mocked response returns 5 traces but limit=2;
      expect only 2 traces returned

    - test_find_test_traces_tag_echo: tag_query in output equals the input tags dict

    - test_find_test_traces_service_filter: service_filter in output equals the
      input service param (both non-None and None cases)
  </behavior>
  <action>
    Create tests/test_qa_tools.py following the exact test structure of
    tests/test_tools_integration.py (which uses the `responses` library for
    mocking HTTP calls, pytest fixtures, and async test helpers).

    Test file structure:
    1. Imports: responses, pytest, asyncio, and project internals (qa_tools function,
       models)
    2. Constants: FAKE_TRACE raw dicts (minimum fields Jaeger returns) — at least
       2 fake traces with different start times (to verify sort order)
    3. One pytest fixture: activate responses mock and register default URL patterns
    4. Test functions as described in behavior block above

    Mock data guidelines:
    - Jaeger trace dict minimum shape: {"traceID": "abc123", "spans": [...],
      "processes": {...}} — check shaping.py to see what shape_trace_summary reads
    - Use two fake traces with start_time_us differing by 60_000_000 (60 seconds)
      to verify newest-first sort
    - For multi-service test: mock /api/services to return ["svc-a","svc-b"], then
      mock /api/traces for each service

    Do NOT use unittest.mock.patch on the tool function itself — mock at the HTTP
    layer (responses library) consistent with test_tools_integration.py.

    Run tests as async via asyncio.run() or pytest-asyncio if already configured —
    check conftest.py or existing test files for the pattern used.
  </action>
  <verify>
    <automated>cd /opt/develop/aiqa/mcps/jaeger-mcp && python -m pytest tests/test_qa_tools.py -v 2>&1 | tail -20</automated>
  </verify>
  <acceptance_criteria>
    - All 6 test functions pass
    - No HTTP calls leave the process (responses library intercepts all)
    - test_find_test_traces_no_match does not raise any exception (empty result)
    - test_find_test_traces_multi_service verifies /api/services was called
    - Coverage: tests/test_qa_tools.py covers the main success path, no-match path,
      and multi-service path of qa_tools.jaeger_find_test_traces
  </acceptance_criteria>
  <done>pytest tests/test_qa_tools.py -v passes all 6 tests with mocked HTTP</done>
</task>

<task type="auto">
  <name>Task 3: Update tests/test_protocol.py EXPECTED_TOOLS for jaeger_find_test_traces</name>
  <files>tests/test_protocol.py</files>
  <read_first>
    - tests/test_protocol.py — read the EXPECTED_TOOLS dict (roughly lines 24-119)
      to understand the exact dict structure: keys are tool names, values have
      readOnlyHint, destructiveHint, idempotentHint, required_params (set),
      optional_params (set)
  </read_first>
  <action>
    Add one entry to the EXPECTED_TOOLS dict in tests/test_protocol.py.
    Insert it after the last existing entry (before the closing `}`).

    Entry to add:
        "jaeger_find_test_traces": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "required_params": {"tags"},
            "optional_params": {"service", "lookback_hours", "limit"},
        },

    No other changes to test_protocol.py. The test_all_tools_registered assertion
    uses set equality, so this entry must exactly match what the tool registers.
  </action>
  <verify>
    <automated>cd /opt/develop/aiqa/mcps/jaeger-mcp && python -m pytest tests/test_protocol.py -v 2>&1 | tail -20</automated>
  </verify>
  <acceptance_criteria>
    - test_all_tools_registered passes (jaeger_find_test_traces in both registered
      set and EXPECTED_TOOLS)
    - All parametrized tests pass for jaeger_find_test_traces (readOnlyHint, etc.)
    - No previously passing protocol tests regress
    - `python -m pytest tests/ -v` exits 0
  </acceptance_criteria>
  <done>pytest tests/test_protocol.py and tests/test_qa_tools.py both pass; full test suite green</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| in-process caller → JaegerClient.find_test_traces() | Trusted internal call; same process as MCP server |
| test suite → responses mock | No external network; contained within test process |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-17-05 | Tampering | facade.find_test_traces() tags parameter | mitigate | Inherited from TTC-01 boundary — json.dumps at qa_tools layer; facade passes dict through unchanged |
| T-17-06 | Repudiation | test_qa_tools.py mocked responses | accept | Tests are internal CI artifacts; mock boundary is test-only |
</threat_model>

<verification>
After all three tasks complete:

```bash
cd /opt/develop/aiqa/mcps/jaeger-mcp

# Full test suite
python -m pytest tests/ -v --tb=short 2>&1 | tail -30

# Facade signature check
python -c "
from jaeger_mcp.facade import JaegerClient
import inspect
sig = inspect.signature(JaegerClient.find_test_traces)
print('facade params:', list(sig.parameters))
"

# Static analysis
mypy src/jaeger_mcp/facade.py --ignore-missing-imports
```
</verification>

<success_criteria>
- JaegerClient.find_test_traces() implemented and importable
- All 6 tests in test_qa_tools.py pass with mocked HTTP
- test_protocol.py passes with jaeger_find_test_traces in EXPECTED_TOOLS
- Full pytest suite exits 0
- mypy clean on facade.py
- TTC-01 through TTC-04 all provably satisfied by passing tests
</success_criteria>

<output>
Create `.planning/phases/phase-17/phase-17-02-SUMMARY.md` when done.

## Artifacts This Phase Produces

New symbols introduced:
- `JaegerClient._afind_test_traces` (private async method) — src/jaeger_mcp/facade.py
- `JaegerClient.find_test_traces` (public sync facade method) — src/jaeger_mcp/facade.py

New files:
- `tests/test_qa_tools.py` — 6 test functions covering tool behavior

Modified files:
- `tests/test_protocol.py` — EXPECTED_TOOLS dict gains "jaeger_find_test_traces" entry
</output>
