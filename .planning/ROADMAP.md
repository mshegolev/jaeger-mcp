# jaeger-mcp — Roadmap

## Milestones

- SHIPPED **M1: Investigator Facade** — Phase 1 (shipped 2026-06-06)
- SHIPPED **M2: Production Hardening** — Phases 2-4 (shipped 2026-06-06)
- SHIPPED **M3: Release v0.2.0** — Phase 5 (shipped 2026-06-06)
- SHIPPED **M4: Tech Debt Cleanup** — Phases 6-7 (shipped 2026-06-08)
- SHIPPED **M5: Trace Analysis** — Phases 8-10 (shipped 2026-06-16)
- [ ] **v0.4.0: Advanced Trace Analytics** — Phases 11-15

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

<details>
<summary>M5: Trace Analysis (Phases 8-10) — SHIPPED 2026-06-16</summary>

- [x] Phase 8: Trace Comparison — COMP-01, COMP-02, COMP-03, COMP-04
- [x] Phase 9: Span Statistics — STAT-01, STAT-02, STAT-03, STAT-04
- [x] Phase 10: Release v0.3.0 — REL-01, REL-02, REL-03

</details>

### v0.4.0: Advanced Trace Analytics (Phases 11-15)

- [ ] **Phase 11: Async Transport** — Migrate to async HTTP, concurrent fetching, streaming for scale
- [ ] **Phase 12: Critical Path Analysis** — Longest-duration span chain and bottleneck ranking
- [ ] **Phase 13: Batch Window Comparison** — Aggregate trace behavior diff between two time periods
- [ ] **Phase 14: Anomaly Detection** — Statistical latency/error-rate spike detection per operation
- [ ] **Phase 15: Release v0.4.0** — Version bump, changelog, README with 10 tools

## Phase Details

### Phase 11: Async Transport
**Goal**: The system handles deep traces (500+ spans) and wide queries (100+ traces) without timeouts or memory pressure, while preserving the existing sync API for backward compatibility
**Depends on**: Nothing (foundational for this milestone)
**Requirements**: ASYNC-01, ASYNC-02, ASYNC-03, ASYNC-04
**Success Criteria** (what must be TRUE):
  1. All existing MCP tools and facade methods continue to pass their tests without modification (backward compatibility)
  2. Fetching 10+ traces concurrently completes faster than sequential fetching by at least 3x
  3. A trace with 500+ spans can be fetched and processed without the response being fully buffered in memory before processing begins
  4. The sync `JaegerClient` API works identically to before — callers do not need to use `async/await`
**Plans:** 3 plans
Plans:
- [ ] 11-01-PLAN.md — Async HTTP transport foundation (httpx + retry + errors + test migration)
- [ ] 11-02-PLAN.md — Async MCP integration + sync facade preservation
- [ ] 11-03-PLAN.md — Concurrent fetching + streaming for large traces

### Phase 12: Critical Path Analysis
**Goal**: Users can identify the longest-duration span chain and the highest self-time bottleneck spans in any trace
**Depends on**: Phase 11 (uses async transport for trace fetching)
**Requirements**: CRIT-01, CRIT-02, CRIT-03, CRIT-04
**Success Criteria** (what must be TRUE):
  1. User can call `jaeger_critical_path` with a trace ID and receive the root-to-leaf span chain that accounts for the most wall-clock time
  2. Each span in the critical path output shows operation, service, duration, and percentage of total trace duration
  3. User can see top-N spans ranked by self-time (exclusive duration) to find the actual bottleneck operations
  4. `JaegerClient.critical_path()` returns the same analysis programmatically for in-process use
**Plans**: TBD

### Phase 13: Batch Window Comparison
**Goal**: Users can compare aggregate trace behavior between two time periods to detect performance regressions or improvements across deployments
**Depends on**: Phase 11 (concurrent trace fetching for batch queries)
**Requirements**: BATCH-01, BATCH-02, BATCH-03, BATCH-04, BATCH-05
**Success Criteria** (what must be TRUE):
  1. User can call `jaeger_compare_windows` with a service name, baseline time window, and comparison time window, and receive a summary of behavioral changes
  2. The output lists per-operation diffs showing which operations were added, removed, became slower, or became faster
  3. Each operation and the overall comparison include a numeric deviation score indicating the magnitude of change
  4. `JaegerClient.compare_windows()` returns the same analysis programmatically for in-process use
**Plans**: TBD

### Phase 14: Anomaly Detection
**Goal**: Users can scan a service's recent traces and get flagged operations with statistically significant latency spikes or error-rate increases
**Depends on**: Phase 11 (async fetching for historical baseline), builds on span_statistics patterns from Phase 9
**Requirements**: ANOM-01, ANOM-02, ANOM-03, ANOM-04, ANOM-05, ANOM-06
**Success Criteria** (what must be TRUE):
  1. User can call `jaeger_detect_anomalies` for a service and receive a list of operations with anomalous latency or error rates
  2. The tool computes a statistical baseline from a configurable historical time window (defaulting to the last 1 hour)
  3. Operations with p95/p99 latency significantly above baseline are flagged as latency anomalies
  4. Operations with error rates significantly above baseline are flagged as error-rate anomalies
  5. Users can tune anomaly sensitivity via configurable sigma/percentile thresholds
**Plans**: TBD

### Phase 15: Release v0.4.0
**Goal**: v0.4.0 is published with complete documentation reflecting 10 MCP tools and all new analytics capabilities
**Depends on**: Phases 11-14 (all features complete)
**Requirements**: REL-04, REL-05, REL-06
**Success Criteria** (what must be TRUE):
  1. `pyproject.toml` shows version 0.4.0 and all tests pass
  2. CHANGELOG contains a v0.4.0 section documenting async transport, critical path, batch comparison, and anomaly detection
  3. README documents 10 MCP tools (up from 7) with usage examples for the new analytics tools
**Plans**: TBD

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
| 9. Span Statistics | M5 | STAT-01..04 | Done | 2026-06-16 |
| 10. Release v0.3.0 | M5 | REL-01..03 | Done | 2026-06-16 |
| 11. Async Transport | v0.4.0 | ASYNC-01..04 | Not started | - |
| 12. Critical Path Analysis | v0.4.0 | CRIT-01..04 | Not started | - |
| 13. Batch Window Comparison | v0.4.0 | BATCH-01..05 | Not started | - |
| 14. Anomaly Detection | v0.4.0 | ANOM-01..06 | Not started | - |
| 15. Release v0.4.0 | v0.4.0 | REL-04..06 | Not started | - |
