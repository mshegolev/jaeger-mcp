# jaeger-mcp — Roadmap

## Milestones

- SHIPPED **M1: Investigator Facade** — Phase 1 (shipped 2026-06-06)
- SHIPPED **M2: Production Hardening** — Phases 2-4 (shipped 2026-06-06)
- SHIPPED **M3: Release v0.2.0** — Phase 5 (shipped 2026-06-06)
- SHIPPED **M4: Tech Debt Cleanup** — Phases 6-7 (shipped 2026-06-08)
- **M5: Trace Analysis** — Phases 8-10 (in progress)

## Phases

<details>
<summary>M1: Investigator Facade (Phase 1) — SHIPPED 2026-06-06</summary>

- [x] Phase 1: Investigator Facade — JGR-01, JGR-02

</details>

<details>
<summary>M2: Production Hardening (Phases 2-4) — SHIPPED 2026-06-06</summary>

- [x] Phase 2: Reliability — JGR-03, JGR-04, JGR-09
- [x] Phase 3: Observability & Structure — JGR-05, JGR-08
- [x] Phase 4: Security & CI — JGR-06, JGR-07, JGR-10

</details>

<details>
<summary>M3: Release v0.2.0 (Phase 5) — SHIPPED 2026-06-06</summary>

- [x] Phase 5: Release v0.2.0 — JGR-11

</details>

<details>
<summary>M4: Tech Debt Cleanup (Phases 6-7) — SHIPPED 2026-06-08</summary>

- [x] Phase 6: Code Quality — JGR-12, JGR-13, JGR-14, JGR-15
- [x] Phase 7: Validation & Tests — JGR-16, JGR-17

</details>

### M5: Trace Analysis (Phases 8-10) — In Progress

- [x] Phase 8: Trace Comparison — COMP-01, COMP-02, COMP-03, COMP-04
- [ ] Phase 9: Span Statistics — STAT-01, STAT-02, STAT-03, STAT-04
- [ ] Phase 10: Release v0.3.0 — REL-01, REL-02, REL-03

#### Phase 8: Trace Comparison

**Goal:** Implement structural trace diff — compare two traces by matching spans on operation+service, output added/removed/changed spans.

**Requirements:** COMP-01, COMP-02, COMP-03, COMP-04

**Depends on:** Existing jaeger_get_trace tool and shaping.py helpers

**Plans:** 3 plans

Plans:
- [x] 08-01-PLAN.md — TypedDicts + span matching/diff logic in models.py and shaping.py
- [x] 08-02-PLAN.md — jaeger_compare_traces MCP tool + integration/protocol tests
- [x] 08-03-PLAN.md — JaegerClient.compare_traces() facade method + facade tests

**Success criteria:**
1. `jaeger_compare_traces` tool registered and discoverable via MCP
2. Structural diff correctly identifies added, removed, and changed spans
3. Spans matched by operation name + service (not span ID)
4. `JaegerClient.compare_traces()` facade method works in-process
5. Tests cover happy path, empty traces, identical traces, fully different traces

#### Phase 9: Span Statistics

**Goal:** Implement per-operation statistics aggregation — fetch N traces, compute latency percentiles and error rates per operation.

**Requirements:** STAT-01, STAT-02, STAT-03, STAT-04

**Depends on:** Phase 8 (shared shaping patterns), existing jaeger_search_traces tool

**Plans:** 3 plans

Plans:
- [ ] 09-01-PLAN.md — TypedDicts + percentile/aggregation logic in models.py and shaping.py
- [ ] 09-02-PLAN.md — jaeger_span_statistics MCP tool + integration/protocol tests
- [ ] 09-03-PLAN.md — JaegerClient.span_statistics() facade method + facade tests

**Success criteria:**
1. `jaeger_span_statistics` tool registered and discoverable via MCP
2. Correct p50/p95/p99 duration computation per operation
3. Error count and error rate per operation are accurate
4. Trace depth configurable via `limit` parameter (default 20, max 100)
5. `JaegerClient.span_statistics()` facade method works in-process

#### Phase 10: Release v0.3.0

**Goal:** Bump version, update documentation, prepare for publish.

**Requirements:** REL-01, REL-02, REL-03

**Depends on:** Phase 8 and Phase 9 (both tools must be complete)

**Success criteria:**
1. `pyproject.toml` version is `0.3.0`
2. CHANGELOG has v0.3.0 section with both new tools documented
3. README shows 7 tools, has trace analysis section with usage examples
4. All tests pass, coverage >= 90%

## Progress Table

| Phase | Milestone | Requirements | Status | Completed |
|-------|-----------|-------------|--------|-----------|
| 1. Investigator Facade | M1 | JGR-01, JGR-02 | Done | 2026-06-06 |
| 2. Reliability | M2 | JGR-03, JGR-04, JGR-09 | Done | 2026-06-06 |
| 3. Observability & Structure | M2 | JGR-05, JGR-08 | Done | 2026-06-06 |
| 4. Security & CI | M2 | JGR-06, JGR-07, JGR-10 | Done | 2026-06-06 |
| 5. Release v0.2.0 | M3 | JGR-11 | Done | 2026-06-06 |
| 6. Code Quality | M4 | JGR-12..15 | Done | 2026-06-08 |
| 7. Validation & Tests | M4 | JGR-16, JGR-17 | Done | 2026-06-08 |
| 8. Trace Comparison | M5 | COMP-01..04 | Done | 2026-06-16 |
| 9. Span Statistics | M5 | STAT-01..04 | Not started | - |
| 10. Release v0.3.0 | M5 | REL-01..03 | Not started | - |
