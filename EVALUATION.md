# Evaluation suite

This repository ships a 10-question evaluation (`evaluation.xml`) built per the
mcp-builder Phase 4 specification. The suite measures whether an LLM can
productively use jaeger-mcp to answer realistic, read-only questions about
distributed traces and service topology.

## Design principles

Every question is **read-only, independent, stable, verifiable, complex, and
instance-agnostic** — same principles as sonarqube-mcp's template. Since
jaeger-mcp wraps a customer-owned Jaeger instance, no pre-solved answer shared
fixture exists. The suite ships with `__VERIFY_ON_INSTANCE__` placeholders.

## Filling in answers

1. Pick a target Jaeger (self-hosted, OpenTelemetry Collector + Jaeger, or
   Jaeger demo at https://github.com/jaegertracing/jaeger/tree/main/examples).
2. Export env vars:
   ```bash
   export JAEGER_URL=https://jaeger.example.com
   # optional: export JAEGER_TOKEN=... / JAEGER_USERNAME / JAEGER_PASSWORD
   ```
3. Solve each question manually — fastest path is to run Claude Code with this
   MCP configured and ask the question verbatim.
4. Replace the placeholder with the verified value.
5. Narrow each question to target one specific entity for stability (e.g.
   replace "first service" with a specific service name on your instance).

## Running the harness

```bash
python scripts/evaluation.py \
  -t stdio \
  -c uvx \
  -a jaeger-mcp \
  -e JAEGER_URL=$JAEGER_URL \
  -e JAEGER_TOKEN=$JAEGER_TOKEN \
  -o evaluation_report.md \
  evaluation.xml
```

Low-accuracy questions usually signal one of:

- Tool description is ambiguous → tighten in `tools.py`.
- Output schema is under/over-specified → adjust TypedDict in `models.py`.
- Question itself is ambiguous on your instance → rephrase.

## Design deviations

Same honest compromise as sonarqube-mcp: question *structure* is fixed
(validates the MCP design), *values* come from whichever Jaeger you verify
against. A shared fixture would require standing up a demo Jaeger with pinned
traces — out of scope for v0.1.0.
