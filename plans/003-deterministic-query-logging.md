# Plan 003: Add deterministic query logging

> **Status: COMPLETE (2026-07-22).** Depends on frozen `baseline-v4` from Plan 001. Drift check:
> `git diff --stat 7bbefb0..HEAD -- research/literature.py research/research_parallel.py research/schema.py research/verify.py`

## Goal

Make the audit trace the source of truth for what was actually searched. Current `queries[]` is
agent-authored, compact trace arguments can be truncated, and earlier retry traces can disappear.

## Scope

- Modify `research/literature.py` and `research/research_parallel.py`; add focused trace tests.
- Keep compact `tool_trace` and old result/audit fields readable.
- Do not change search/fetch limits or retry policy in this plan.

## Steps

1. Record a schema-v2 `literature_trace` at the literature client execution boundary. Each event
   stores attempt/sequence, source/action, research phase, explicitly targeted genes, exact
   normalized query or identifiers, requested and effective limits, status, returned
   identifiers/count, and duration. Target genes are declared by the research workflow, not
   inferred later from query text.
2. Allowlist trace fields. Never store abstracts, titles, headers, credentials, URLs with secrets,
   or the `submit_result` payload.
3. Append events across retries. Preserve integer `attempts`; add `attempt_records[]` containing
   per-attempt terminal status, turns, tokens, duration, and cost. Keep cumulative cost/duration.
4. Derive canonical `ResearchResult.queries` from PubMed/OpenAlex search events in first-seen order,
   deduplicating only exact repeats. Fetch, DOI, citation, read, and submit calls are not queries.
5. Keep the old agent-authored query list only in raw payload provenance.
6. Derive attempted supplied-gene coverage and regulator-query counts from trace fields. Preserve
   these separately so regulator/pathway research cannot make supplied-gene coverage appear higher.

## Verification

```bash
.venv/bin/pytest -q tests/test_research_trace.py tests/test_research_pipeline.py
.venv/bin/pytest -q
.venv/bin/ruff check --select E9,F63,F7,F82 research/literature.py research/research_parallel.py
```

Tests must prove exact long-query retention, target-gene and phase retention, attempted-coverage
derivation, requested/effective limits, failure logging, retry preservation, deterministic query
derivation, secret/payload exclusion, and old-audit loading.
Regression assertions: `MAX_FETCH_IDS == 20` and BioC batch behavior remains `100`.

## Done when

- Every executed search is reconstructable from `literature_trace`.
- `ResearchResult.queries` exactly matches the ordered unique search events.
- Every supplied gene's search attempt or lack of attempt is deterministically auditable.
- Existing PMID/BioC batching is unchanged.

## Stop if

- exact executed arguments cannot be observed at the client boundary or trace recording can break a
  research run.

## Completed result — 2026-07-22

- Literature tools now append allowlisted schema-v2 events at execution time with attempt,
  sequence, source/action, phase, target genes, exact normalized query/IDs, requested/effective
  limits, result IDs/count, status, and duration.
- Search/fetch/resolve failures are traced without titles, abstracts, headers, URLs, submit
  payloads, or unredacted secret parameters.
- Canonical `ResearchResult.queries` is derived from ordered unique PubMed/OpenAlex search events;
  agent-authored queries remain only in the raw payload.
- Audit files preserve all retry traces and per-attempt terminal telemetry with cumulative
  cost/turn/duration/token totals, plus separate supplied-gene and regulator-query accounting.
- Added `tests/test_research_trace.py`; focused tests and the full 155-test suite pass. PubMed fetch
  batching remains 20 and BioC batching remains 100.
