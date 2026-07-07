# Phase 18: Regression Trace Diff - Context

**Gathered:** 2026-07-08
**Status:** Ready for planning
**Mode:** Auto-generated (yolo mode — smart discuss auto-accepted)

<domain>
## Phase Boundary

Phase 18 delivers `jaeger_regression_diff` — a new MCP tool and facade method
that classifies per-operation trace behavior changes between a baseline window and
a comparison window as regressed / recovered / appeared / removed, with a severity
score (0–100) for triage prioritization.

Deliverables:
- `jaeger_regression_diff` MCP tool (added to existing `src/jaeger_mcp/qa_tools.py`)
- `JaegerClient.regression_diff()` facade method (in `facade.py`)
- `RegressionOp` + `RegressionDiffOutput` TypedDicts (in `models.py`)
- Full test coverage in `tests/test_regression_diff.py`

</domain>

<decisions>
## Implementation Decisions

### Classification Logic (REG-02)

Semantic uplift layer applied to `WindowComparisonOutput` from `_acompare_windows`:

| Source `change_type` | condition | Phase 18 classification |
|---|---|---|
| `"slower"` | any | `"regressed"` |
| `"faster"` | error_rate_delta <= 0 | `"recovered"` |
| `"added"` | any | `"appeared"` |
| `"removed"` | any | `"removed"` |
| `"unchanged"` | any | excluded from output |
| any | error_rate_delta > 0.05 | override to `"regressed"` |

Error-rate override applies before change_type check — an "unchanged" or "faster"
operation with error rate spike (>5pp) is still classified as `"regressed"`.

### Severity Score Formula (REG-04)

- `regressed` operations: `severity_score = min(100, round(p95_delta_pct * 50 + error_rate_delta * 200))`
  - p95 latency delta contributes 50 points at +100% increase
  - error rate delta contributes 200 points per unit (0.5 = 100 points)
- `recovered`, `appeared`, `removed`: `severity_score = 0`
- Output sorted by `severity_score` descending (worst regressions first)

### Time Window Parameters (REG-03)

- `service: str` — required, validated by Pydantic pattern `^[a-zA-Z0-9._:\-]+$`
- `baseline_start: int` — required, Unix microseconds
- `baseline_end: int` — required, Unix microseconds
- `comparison_start: int | None = None` — optional, defaults to `now - 15min` in μs
- `comparison_end: int | None = None` — optional, defaults to `now` in μs
- `limit: int = 100` — traces per window (same default as `compare_windows`)

### Output TypedDicts

`RegressionOp` TypedDict fields:
- `operation: str` — operation name
- `classification: str` — `"regressed" | "recovered" | "appeared" | "removed"`
- `baseline_p95_ms: int` — baseline p95 latency in ms (from `baseline_p95_us // 1000`)
- `comparison_p95_ms: int` — comparison p95 latency in ms
- `p95_delta_ms: int` — absolute delta in ms
- `p95_delta_pct: float` — relative delta as fraction (0.5 = +50%)
- `baseline_error_rate: float` — fraction 0.0–1.0
- `comparison_error_rate: float` — fraction 0.0–1.0
- `error_rate_delta: float` — absolute delta (comparison - baseline)
- `severity_score: int` — 0–100, higher = worse

`RegressionDiffOutput` TypedDict fields:
- `service: str`
- `baseline_start: int` — Unix μs (actual start used)
- `baseline_end: int` — Unix μs (actual end used)
- `comparison_start: int` — Unix μs (actual start used)
- `comparison_end: int` — Unix μs (actual end used)
- `operations: list[RegressionOp]` — sorted by severity_score desc
- `regressed_count: int`
- `recovered_count: int`
- `appeared_count: int`
- `removed_count: int`

### Module Structure

- Add `jaeger_regression_diff` to existing `src/jaeger_mcp/qa_tools.py`
  (same file as `jaeger_find_test_traces` — cohesive QA intelligence module)
- TypedDicts added to `models.py` under the existing `# QA Test Trace Correlation`
  section (or a new `# QA Regression Diff` subsection)
- Registration already active via `from . import qa_tools as _qa_tools` in `tools.py`
  — no changes to `tools.py` needed
- Facade: `_aregression_diff()` private async + `regression_diff()` public sync
  in `facade.py`, calls `self._acompare_windows()` internally

### Reuse Strategy

`_aregression_diff(service, baseline_start, baseline_end, comparison_start,
comparison_end, limit)` calls `self._acompare_windows(service, baseline_start,
baseline_end, comparison_start, comparison_end, limit=limit)` to get
`WindowComparisonOutput`, then applies classification logic and severity formula
to produce `RegressionDiffOutput`. No duplicate HTTP logic.

`jaeger_regression_diff` MCP tool calls `client.regression_diff()` (via facade,
not `_acompare_windows` directly) — consistent with other tools calling facade methods.

### Error & Edge Cases

- All operations `unchanged`: return empty `operations` list with counts all 0
- No traces in baseline window: return `appeared` for all comparison operations
- No traces in comparison window: return `removed` for all baseline operations
- Comparison window defaults resolved before passing to `_acompare_windows`

</decisions>

<code_context>
## Existing Code Insights

### Key Analog: `compare_windows`

`jaeger_compare_windows` in `tools.py` (line 1058) and `_acompare_windows` in
`facade.py` (line 836) are the direct analogs:
- Same HTTP fetch pattern (two concurrent window fetches via `aget_many`)
- `shaping.compare_windows()` does the aggregation
- `WindowComparisonOutput` has all the raw data we need for classification
- `OperationDiff` has `change_type`, `deviation_score`, `p50/p95_delta_pct`, `error_rate_delta`

### Reusable Assets

- `JaegerClient._acompare_windows()` in `facade.py:836` — call this directly
- `shaping.compare_windows()` in `shaping.py:402` — used by `_acompare_windows`
- `output.ok()` / `output.fail()` — dual-channel result helpers
- `Annotated[int, Field(ge=0)]` pattern for timestamp parameters

### Integration Points

- `src/jaeger_mcp/models.py` — append `RegressionOp` + `RegressionDiffOutput`
- `src/jaeger_mcp/qa_tools.py` — add `jaeger_regression_diff` async MCP tool
- `src/jaeger_mcp/tools.py` — NO changes needed (already imports qa_tools)
- `src/jaeger_mcp/facade.py` — add `_aregression_diff()` + `regression_diff()`
- `tests/test_regression_diff.py` — new test file
- `tests/test_protocol.py` — add `jaeger_regression_diff` to `EXPECTED_TOOLS`

</code_context>

<specifics>
## Specific Ideas

- `p95_delta_pct` from `OperationDiff` is already a float fraction (0.5 = 50%),
  so severity = `min(100, round(diff.p95_delta_pct * 50 + diff.error_rate_delta * 200))`
- Latency fields in `OperationDiff` are in microseconds (`_us` suffix);
  convert to ms in `RegressionOp` with `// 1000`
- The `classification` field uses string literals matching REG-02 exactly:
  `"regressed"`, `"recovered"`, `"appeared"`, `"removed"`
- Tests should cover: all-clear (no regressions), pure latency regression, error
  rate override, recovery detection, appeared/removed operations, default window
  resolution

</specifics>

<deferred>
## Deferred Ideas

- Configurable severity weights (latency vs error rate ratio) — out of scope;
  hardcoded weights are sufficient for v0.6.0
- Multi-service diff (compare across all services) — out of scope; per-service is fine
- Historical baseline selection (auto-pick last-good window) — future enhancement
- Threshold configuration for error_rate_delta cutoff (0.05) — hardcoded for now

</deferred>
