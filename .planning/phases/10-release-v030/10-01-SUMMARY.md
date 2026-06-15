---
phase: 10-release-v030
plan: 01
subsystem: release
tags: [version-bump, changelog, readme, documentation]
dependency_graph:
  requires: [09-trace-analysis]
  provides: [v0.3.0-release-metadata]
  affects: [pyproject.toml, __init__.py, CHANGELOG.md, README.md]
tech_stack:
  added: []
  patterns: [keep-a-changelog, semver]
key_files:
  created: []
  modified:
    - pyproject.toml
    - src/jaeger_mcp/__init__.py
    - CHANGELOG.md
    - README.md
decisions:
  - "Version 0.3.0 — minor bump for two new tools (backward-compatible)"
  - "CHANGELOG follows Keep a Changelog format matching existing v0.2.0 and v0.1.0 entries"
  - "README tool table expanded to 7 rows with full usage guide sections for both new tools"
metrics:
  duration_seconds: 182
  completed_utc: "2026-06-15T18:16:00Z"
  tasks_completed: 3
  tasks_total: 3
  files_modified: 4
  test_count: 237
  test_pass: 237
  coverage_percent: 97.86
---

# Phase 10 Plan 01: Release v0.3.0 Summary

Version bump to 0.3.0 with full CHANGELOG and README updates documenting jaeger_compare_traces and jaeger_span_statistics tools, facade methods, and domain types.

## Tasks Completed

| Task | Name | Commit | Key Changes |
|------|------|--------|-------------|
| 1 | Version bump to 0.3.0 | `e46ae38` | pyproject.toml version + description, __init__.py __version__ |
| 2 | CHANGELOG and README update | `a551def` | v0.3.0 changelog section, 7-tool table, usage guide sections, facade docs |
| 3 | Test suite verification | — (verify-only) | 237/237 tests pass, 97.86% coverage, version importable |

## Changes Made

### pyproject.toml
- `version` field: `"0.2.0"` -> `"0.3.0"`
- `description` field: added "compare traces, compute span statistics" to the project description

### src/jaeger_mcp/__init__.py
- `__version__`: `"0.2.0"` -> `"0.3.0"`

### CHANGELOG.md
- Added `## [0.3.0] — 2026-06-16` section above existing v0.2.0 entry
- Documented `jaeger_compare_traces` tool with structural diff details
- Documented `jaeger_span_statistics` tool with aggregation details
- Documented `JaegerClient.compare_traces()` and `JaegerClient.span_statistics()` facade methods
- Documented new domain types: TraceComparison, SpanIdentity, SpanChange, SpanStatisticsResult, OperationStatResult

### README.md
- Intro paragraph: added "compare traces, compute span statistics"
- Tool count: 5 -> 7 in read-only bullet
- Tool table: added `jaeger_compare_traces` and `jaeger_span_statistics` rows (7 total)
- Usage guide: added `### jaeger_compare_traces` section with structural diff explanation
- Usage guide: added `### jaeger_span_statistics` section with percentile/error rate explanation
- Example queries: added trace comparison and p95 latency examples
- Library facade: added `compare_traces()` and `span_statistics()` to available methods
- Domain objects: added TraceComparison, SpanIdentity, SpanChange, SpanStatisticsResult, OperationStatResult

## Verification Results

| Check | Result |
|-------|--------|
| pyproject.toml version = "0.3.0" | PASS |
| __init__.py __version__ = "0.3.0" | PASS |
| CHANGELOG has [0.3.0] section | PASS |
| README tool table has 7 rows | PASS |
| README mentions "all 7 tools" | PASS |
| README has jaeger_compare_traces usage guide | PASS |
| README has jaeger_span_statistics usage guide | PASS |
| README facade lists compare_traces(), span_statistics() | PASS |
| README domain objects include new types | PASS |
| All 237 tests pass | PASS |
| Coverage >= 90% (97.86%) | PASS |
| Version importable as 0.3.0 | PASS |
| Ruff format clean | PASS |
| Ruff check — pre-existing warnings only (0 new) | PASS |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — this plan modifies documentation and version metadata only.

## Self-Check: PASSED

All files exist, all commits verified, all content checks pass.
