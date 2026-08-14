# Plan 001: Freeze the benchmark and establish factual reference quality

> **Status: COMPLETE using frozen `baseline-v4`.** Run before changing research behavior. Paid runs
> require an explicit spend preview and approval.
> Drift check: `git diff --stat 7bbefb0..HEAD -- research research/bundle.py gpi/run_pipeline.py`

## Goal

Create a reproducible baseline for module coherence, gene coverage, redundancy, paper entailment,
context fit, selection quality, cost, and latency. Historical runs are reference data only because
their distinctive-gene lists and telemetry are stale.

## Scope

- Create `research/benchmark.py`, `benchmarks/literature-v1/**`, and
  `tests/test_literature_benchmark.py`.
- Do not change production research behavior, schemas, prompts, batching, or limits.

## Cohort

- Brain endothelial: P8–P11 from `configs/brain_ec_p8_11.yaml`.
- Hepatocyte: P20–P25 from `runs/liver_demo_p20_25.yaml`.
- Smoke/sentinel cases: brain P10/P11 and liver P21/P25.
- Run all 10 cases once and the four sentinels a second time.

The liver config points to missing `examples/liver_demo` files. Snapshot the verified originals
from `/Volumes/IF_PHAGE/Hepatocyte/Programs0617/` into
`benchmarks/literature-v1/inputs/liver/`. Snapshot the brain inputs too. The committed manifest must
use only these portable copies and record SHA-256 hashes.

## Steps

1. Add `manifest.yaml` with case ID, experiment, program, input/context paths, expected 15 loading
   plus 8 disjoint distinctive genes, and file hashes. Regenerate bundles with current
   `research.bundle.build_all_bundles()`; never copy old bundles.
2. Add benchmark commands: `prepare`, `materialize-text`, `make-review-packet`, `score`, and
   `compare`.
3. Materialize assessor text using PubMed abstracts and open-access PMC/Europe PMC text. Preserve
   `MAX_FETCH_IDS=20`. Record source, text type, retrieval status, and content hash. Missing text is
   `not_assessable`, not negative evidence.
4. Blind variant names and model-review every retained mechanism–paper link. Independently
   double-score at least 20% and adjudicate disagreements. Preserve the model-assessed label; a
   full human review is optional rather than a gate for the current optimization cycle.
5. Record these metrics per case and in aggregate. A holistic human coherence score is not a gate
   in the current factual audit:
   - claimed, citation-covered, and function-supported core-gene coverage; regulator coverage
     separately;
   - mechanism redundancy and supporting-gene Jaccard;
   - entailment and organism/tissue/cell/condition match;
   - paper role/reason quality and identifier status;
   - searches, turns, retries, tokens, cost, duration, and citation-graph counts.
6. Before the paid baseline, print the maximum budget. Fourteen sessions with at most two $1
   attempts have a $28 API-equivalent ceiling. Stop for approval, then capture immutable
   `baseline-v4` artifacts.

## Verification

```bash
.venv/bin/pytest -q tests/test_literature_benchmark.py
.venv/bin/pytest -q
.venv/bin/ruff check research/benchmark.py tests/test_literature_benchmark.py
.venv/bin/python -m research.benchmark prepare \
  --manifest benchmarks/literature-v1/manifest.yaml \
  --out /tmp/gpi-benchmark --dry-run
```

Expected: tests/lint pass; dry-run lists 10 cases and four sentinel IDs, validates hashes, and makes
no network or paid call. Current pre-change test baseline is `120 passed`.

## Done when

- `baseline-v4` contains bundles, results, audits, text provenance, blinded model adjudications,
  reference-quality metrics, environment/limit metadata, and hashes.
- All retained links are reviewed; resolution is never treated as entailment.
- No production file or existing `runs/` artifact changed.

## Stop if

- portable inputs cannot be reproduced, defining gene lists differ across variants, or paid baseline
  approval is absent.

## Completed result — 2026-07-22

- Frozen 14 sessions covering ten programs and four repeats.
- Audited 261 mechanism–paper links from 236 unique papers using cached abstracts/full text.
- Independently double-scored 53 links and adjudicated all 25 disagreements.
- Established the factual baselines used by Plans 004 and 005: 36.3% mean supplied-gene citation
  coverage, 34.5% function-supported coverage, 27.7% context overclaim, and 0.120 citation-set
  versus 0.698 covered-gene repeat Jaccard.
- Preserved the detailed execution record in [Plan 006](006-reference-quality-audit.md) and the
  generated artifacts under `benchmarks/literature-v1/baseline-v4/reference_quality/`.
