---
phase: phase-17
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/jaeger_mcp/models.py
  - src/jaeger_mcp/qa_tools.py
  - src/jaeger_mcp/tools.py
autonomous: true
requirements:
  - TTC-01
  - TTC-02
  - TTC-03
must_haves:
  truths:
    - "QA engineer can call jaeger_find_test_traces with a dict of tags and get matching traces back"
    - "Tool accepts both Allure schema (allure.id, allure.suite) and pytest schema (test.name, test.run_id) without separate code paths"
    - "Each result trace includes trace_id, root_service, duration_ms, start_time (ISO-8601), has_error, span_count"
    - "When service=None the tool searches up to 20 services concurrently; when service is given only that service is searched"
    - "Results are sorted newest-first by start_time"
    - "When no traces match the tool returns empty list and total_count=0 (not an error)"
  artifacts:
    - path: src/jaeger_mcp/models.py
      provides: TestTraceMatch and FindTestTracesOutput TypedDicts
      contains: "class TestTraceMatch"
    - path: src/jaeger_mcp/qa_tools.py
      provides: jaeger_find_test_traces MCP tool
      exports: ["jaeger_find_test_traces"]
    - path: src/jaeger_mcp/tools.py
      provides: side-effect import of qa_tools for MCP registration
      contains: "qa_tools"
  key_links:
    - from: src/jaeger_mcp/qa_tools.py
      to: src/jaeger_mcp/models.py
      via: "from .models import FindTestTracesOutput, TestTraceMatch"
      pattern: "from \\.models import"
    - from: src/jaeger_mcp/tools.py
      to: src/jaeger_mcp/qa_tools.py
      via: "import qa_tools side-effect registration"
      pattern: "qa_tools"
---

<objective>
Add `TestTraceMatch` and `FindTestTracesOutput` TypedDicts to models.py, create
the `jaeger_find_test_traces` MCP tool in `qa_tools.py`, and register it via a
side-effect import in `tools.py`.

Purpose: Delivers TTC-01 (search by test tag), TTC-02 (unified tag-map, no
schema coupling), and TTC-03 (result fields) per Phase 17 CONTEXT.md decisions.

Output: Two new TypedDicts in models.py, a new qa_tools.py module with the
async MCP tool, and one new import line in tools.py.
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/phases/phase-17/17-CONTEXT.md
@.planning/phases/phase-17/phase-17-PATTERNS.md
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add TestTraceMatch and FindTestTracesOutput to models.py</name>
  <files>src/jaeger_mcp/models.py</files>
  <read_first>
    - src/jaeger_mcp/models.py — read the entire file to understand existing TypedDict
      structure, especially OperationAnomaly + AnomalyDetectionOutput (the nested
      list-of-items pattern to mirror) and the compatibility shim at the top
      (sys.version_info >= 3.12 guard for TypedDict import)
  </read_first>
  <action>
    Append the following section at the very end of src/jaeger_mcp/models.py, after
    the last existing TypedDict class. No other edits needed.

    Section header comment: "# ── QA Test Trace Correlation ────────────────────────"

    Class TestTraceMatch (TypedDict):
    - docstring: "A single trace matching the supplied tag query."
    - fields exactly as specified in CONTEXT.md decisions:
        trace_id: str
        root_service: str | None
        duration_ms: int          (Jaeger microseconds divided by 1000 at call site)
        start_time: str           (ISO-8601 UTC string, e.g. "2026-07-08T10:30:00Z")
        has_error: bool
        span_count: int

    Class FindTestTracesOutput (TypedDict):
    - docstring: "Aggregated result of jaeger_find_test_traces."
    - fields exactly as specified in CONTEXT.md decisions:
        traces: list[TestTraceMatch]
        total_count: int
        tag_query: dict[str, str]   (echo of the input tags — per D decision)
        service_filter: str | None  (echo of the input service param)

    Do NOT add inline comments, examples, or extra docstring prose beyond the
    single-sentence docstrings above. Follow the terse style of existing TypedDicts
    (e.g. OperationAnomaly). Do NOT add forward-reference strings — the
    `from __future__ import annotations` at file top already covers this.
  </action>
  <verify>
    <automated>cd /opt/develop/aiqa/mcps/jaeger-mcp && python -c "from jaeger_mcp.models import TestTraceMatch, FindTestTracesOutput; print('OK')"</automated>
  </verify>
  <acceptance_criteria>
    - `from jaeger_mcp.models import TestTraceMatch, FindTestTracesOutput` succeeds
    - TestTraceMatch has exactly 6 fields: trace_id, root_service, duration_ms,
      start_time, has_error, span_count with the types listed above
    - FindTestTracesOutput has exactly 4 fields: traces, total_count, tag_query,
      service_filter with the types listed above
    - mypy passes: `mypy src/jaeger_mcp/models.py --ignore-missing-imports`
  </acceptance_criteria>
  <done>Both TypedDicts importable; mypy clean; no changes to any other part of models.py</done>
</task>

<task type="auto">
  <name>Task 2: Create src/jaeger_mcp/qa_tools.py with jaeger_find_test_traces MCP tool</name>
  <files>src/jaeger_mcp/qa_tools.py</files>
  <read_first>
    - src/jaeger_mcp/predictive/tools.py — read entirely; this is the exact structural
      analog (@mcp.tool decorator, async def, Annotated+Field parameters, try/except,
      output.ok/output.fail pattern)
    - src/jaeger_mcp/client.py — read aget() and aget_many() signatures (lines
      around def aget and def aget_many) to understand how to call concurrent fetches
    - src/jaeger_mcp/shaping.py — read shape_trace_summary() signature and return
      fields (duration_us, start_time_us, errors_count, span_count, root_service)
    - src/jaeger_mcp/output.py — read ok() and fail() signatures
    - src/jaeger_mcp/errors.py — read handle() signature
    - .planning/phases/phase-17/17-CONTEXT.md — re-read decisions section for
      exact parameter names, defaults, and edge cases
  </read_first>
  <action>
    Create src/jaeger_mcp/qa_tools.py as a brand-new file. It must follow the
    same module layout as predictive/tools.py (side-effect @mcp.tool registration
    — the module is imported once; the decorator fires and registers the tool on
    the shared FastMCP instance).

    Imports required (in this order, following isort conventions in the project):
    - from __future__ import annotations
    - standard lib: datetime, json
    - typing: Annotated
    - pydantic: Field
    - project internals:
        from . import errors, output
        from .client import JaegerHTTPClient
        from .models import FindTestTracesOutput, TestTraceMatch
        from .server import mcp  (shared FastMCP instance — same as predictive/tools.py)
        from .shaping import shape_trace_summary

    Tool registration decorator on jaeger_find_test_traces:
    - @mcp.tool(name="jaeger_find_test_traces", annotations={"readOnlyHint": True,
      "destructiveHint": False, "idempotentHint": True}, structured_output=True)

    Async function signature (per D decisions in CONTEXT.md):
    - tags: Annotated[dict[str, str], Field(description="Tag key-value pairs to
      filter traces. Any framework tag schema works — e.g. {'allure.id': 'TC-42'}
      or {'test.run_id': 'abc123'}.")]
    - service: Annotated[str | None, Field(description="Jaeger service name. If
      omitted, all services (up to 20) are searched concurrently.")] = None
    - lookback_hours: Annotated[int, Field(description="Hours back from now to
      search.", ge=1, le=168)] = 1
    - limit: Annotated[int, Field(description="Maximum traces to return total.",
      ge=1, le=500)] = 50
    - Return type: FindTestTracesOutput

    Internal implementation logic (wrap in try/except Exception → output.fail):

    1. Build the Jaeger tags query string: json.dumps(tags) — pass as the "tags"
       query param to the Jaeger search API (consistent with _asearch_traces).

    2. Determine service list:
       - If service is not None: services = [service]
       - If service is None: fetch all services via aget() on /api/services endpoint
         (check how existing tools do it — follow _adetect_anomalies pattern in
         facade.py if it lists services, or build a simple GET to /api/services).
         Cap to first 20 services: services = all_services[:20]

    3. Build per-service search URLs and call aget_many() for concurrent fetches.
       Each URL: /api/traces?service={svc}&tags={json_tags}&lookback={lookback_hours}h&limit={limit}
       Use lookback param as "{lookback_hours}h" string (Jaeger API convention).

    4. Collect raw traces from all responses (Jaeger returns {"data": [...]}).
       Deduplicate by trace_id (a trace may appear under multiple services).

    5. For each unique raw trace, call shape_trace_summary() to extract fields.
       Build TestTraceMatch:
       - trace_id: from raw trace
       - root_service: from shape result
       - duration_ms: shape_result.duration_us // 1000
       - start_time: datetime.utcfromtimestamp(shape_result.start_time_us / 1e6)
           .strftime("%Y-%m-%dT%H:%M:%SZ")
       - has_error: shape_result.errors_count > 0
       - span_count: shape_result.span_count

    6. Sort by start_time descending (newest-first). Apply global limit cap.

    7. Return output.ok(FindTestTracesOutput(
           traces=matches[:limit],
           total_count=len(matches[:limit]),
           tag_query=tags,
           service_filter=service,
       ), markdown_summary) where markdown_summary is a short human-readable
       message: "Found {N} trace(s) matching {tags}" or "No traces found matching
       tags {tags}" when N==0.

    Error handling: wrap the entire body in try/except Exception as exc, return
    output.fail(exc, "jaeger_find_test_traces") following predictive/tools.py.

    Do NOT use blocking IO (no requests, no httpx.get — use the async aget/aget_many).
    Do NOT hardcode service names. Do NOT normalize tag keys (pass through as-is).
  </action>
  <verify>
    <automated>cd /opt/develop/aiqa/mcps/jaeger-mcp && python -c "import jaeger_mcp.qa_tools; print('registered')"</automated>
  </verify>
  <acceptance_criteria>
    - Module imports without error and registers jaeger_find_test_traces on the
      shared FastMCP instance (visible via mcp.list_tools())
    - Function signature has parameters: tags, service, lookback_hours, limit with
      correct types and defaults (service=None, lookback_hours=1, limit=50)
    - Return type annotation is FindTestTracesOutput
    - tags parameter accepts dict[str, str] with no schema validation (any keys allowed)
    - When service=None code path builds a service list from /api/services endpoint
    - Service list is capped at 20 entries
    - mypy src/jaeger_mcp/qa_tools.py --ignore-missing-imports passes
  </acceptance_criteria>
  <done>qa_tools.py importable; jaeger_find_test_traces registered on MCP; mypy clean</done>
</task>

<task type="auto">
  <name>Task 3: Register qa_tools in tools.py via side-effect import</name>
  <files>src/jaeger_mcp/tools.py</files>
  <read_first>
    - src/jaeger_mcp/tools.py — read the bottom section (last ~15 lines) to see
      the existing side-effect import block for predictive tools
  </read_first>
  <action>
    Append a new import block at the very bottom of src/jaeger_mcp/tools.py,
    after the existing predictive import block. Follow the same comment + noqa
    pattern already present.

    Add a comment line: "# ── QA / Test Intelligence tools ─────────────────────"
    Then add:
        from . import qa_tools as _qa_tools  # noqa: E402,F401

    No other changes to tools.py. Do NOT reorder existing imports or modify
    any other section of the file.
  </action>
  <verify>
    <automated>cd /opt/develop/aiqa/mcps/jaeger-mcp && python -c "import jaeger_mcp.tools; from jaeger_mcp.server import mcp; import asyncio; tools = asyncio.run(mcp.list_tools()); names = {t.name for t in tools}; assert 'jaeger_find_test_traces' in names, f'Not found: {names}'"</automated>
  </verify>
  <acceptance_criteria>
    - jaeger_find_test_traces appears in mcp.list_tools() after importing tools
    - All previously registered tools still appear (no regressions)
    - The new import follows noqa: E402,F401 convention
    - `python -m pytest tests/test_protocol.py -x` will fail (expected — plan 02
      updates EXPECTED_TOOLS); running just this import check passes
  </acceptance_criteria>
  <done>jaeger_find_test_traces visible in MCP tool list; existing tool registrations unaffected</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| MCP client → jaeger_find_test_traces | Untrusted caller supplies tags dict and optional service name |
| qa_tools → Jaeger HTTP API | Internal network call; Jaeger is trusted but responses must be safely parsed |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-17-01 | Tampering | tags dict → Jaeger query | mitigate | json.dumps serialization converts dict to safe JSON string; Pydantic dict[str,str] enforces value types at MCP boundary |
| T-17-02 | Information Disclosure | service=None reveals all service names | accept | Jaeger is an internal tool; service names are not PII; no external exposure |
| T-17-03 | Denial of Service | limit=500 × 20 services concurrent queries | mitigate | 20-service hard cap in code; limit param bounded to 500 by Field(le=500); aget_many uses connection pool |
| T-17-04 | Spoofing | Jaeger API response injection | accept | Internal network only; Jaeger does not accept external connections in this deployment |
</threat_model>

<verification>
After all three tasks complete:

```bash
cd /opt/develop/aiqa/mcps/jaeger-mcp

# 1. Models importable
python -c "from jaeger_mcp.models import TestTraceMatch, FindTestTracesOutput; print('models OK')"

# 2. Tool registered
python -c "
import asyncio
import jaeger_mcp.tools
from jaeger_mcp.server import mcp
tools = asyncio.run(mcp.list_tools())
names = {t.name for t in tools}
assert 'jaeger_find_test_traces' in names
print('tool registered OK')
"

# 3. Static analysis
mypy src/jaeger_mcp/models.py src/jaeger_mcp/qa_tools.py src/jaeger_mcp/tools.py --ignore-missing-imports
```
</verification>

<success_criteria>
- TestTraceMatch and FindTestTracesOutput TypedDicts defined in models.py with exact fields from CONTEXT.md
- qa_tools.py implements jaeger_find_test_traces per all D-decisions in CONTEXT.md
- Tool visible in mcp.list_tools() after importing jaeger_mcp.tools
- mypy passes on all three modified/created files
- No changes to tests yet (covered in plan 02)
</success_criteria>

<output>
Create `.planning/phases/phase-17/phase-17-01-SUMMARY.md` when done.

## Artifacts This Phase Produces

New symbols introduced:
- `TestTraceMatch` (TypedDict) — src/jaeger_mcp/models.py
- `FindTestTracesOutput` (TypedDict) — src/jaeger_mcp/models.py
- `jaeger_find_test_traces` (async MCP tool function) — src/jaeger_mcp/qa_tools.py
- `_qa_tools` (side-effect import alias) — src/jaeger_mcp/tools.py (bottom)

New files:
- `src/jaeger_mcp/qa_tools.py`
</output>
