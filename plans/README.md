# Literature Pipeline Optimization — Progress Ledger

This file is the formal source of truth for execution status, gates, benchmark evidence, and the
next authorized step. Update it whenever a gate changes or a plan produces an artifact. Detailed
designs remain in the numbered plan documents.

Baseline design commit: `7bbefb0` (2026-07-20). Last progress update: 2026-07-23.

## Execution order

`001 → (002 and 003 in parallel) → 004 → 005`

The completed reference-quality audit is evidence inside this sequence: it closes the factual
baseline work in Plan 001, sharpens the retrieval-first contract in Plan 004, and supplies the
comparison metrics for Plan 005. It is not an additional production stage.

## Status definitions

- `COMPLETE`: required artifacts exist and the gate is satisfied.
- `READY`: dependencies are satisfied; implementation may begin.
- `WAITING`: a named dependency is incomplete.
- `BLOCKED`: progress requires user authority or an external state change.

## Progress

| Plan | Outcome | Status | Evidence / decision | Next gate |
|---|---|---|---|---|
| [001](001-establish-literature-benchmark.md) | Freeze the benchmark and establish factual reference quality | **COMPLETE** | Frozen `baseline-v4`; 14 sessions, 261 links, 236 papers; model audit complete with independent double scoring and adjudication | Preserve artifacts and defining gene lists |
| [002](002-context-metadata-query-guidance.md) | Add flexible context, aliases, and gene metadata | **COMPLETE** | Taxid-specific NCBI metadata, unresolved states, compact aliases/descriptions, tissue, and profile-derived query guidance implemented; focused and full tests pass | Preserve defining gene lists and assay exclusion |
| [003](003-deterministic-query-logging.md) | Record exact searches, returned IDs, research phase, and target genes | **COMPLETE** | Schema-v2 execution-boundary trace, deterministic query derivation, separate supplied-gene/regulator accounting, and cumulative retry records implemented; focused and full tests pass | Use trace as Plan 004 ledger provenance |
| [004](004-retrieval-first-evidence-graph-doi.md) | Research supplied genes before forming themes; build gene–paper evidence edges | **COMPLETE** | Retrieval-first ledgers, exact evidence links, DOI conflict handling, DOI-only flow, and bounded OpenAlex expansion implemented; full tests pass | Preserve contract for paired pilot |
| [005](005-benchmark-and-tune-agent-limits.md) | Benchmark candidate quality and enforce/tune runtime limits | **ASSESSMENT COMPLETE; PROMOTION REJECTED** | [Validated report](../benchmarks/literature-v1/candidate-evaluable/comparison-report/report.html): function coverage improved +13.0 points and claim-backed module genes rose 67.6%→94.1%, but unordered module replicate similarity fell 0.758→0.654; operational turns were ≥3.23× baseline and source-opposed claims rose from 0 to 2 | Implement a stable module prior, hard phase budgets, resumable evidence state, annotation diffs, and direction checks; then re-run `60/$1.25/900s` |

Plan 005's current frontier is the hybrid process: candidate retrieval and exact evidence edges
under a stable three-module prior, with resumable evidence-state scheduling and hard phase budgets,
followed by a bounded paired rerun. Candidate work is concentrated in supplied-gene research
(61.7% of recorded minimum turns), while exact duplicate searches account for only 0.9%; simple
string deduplication is not the primary lever.
The subscription-backed assessment is complete; follow-up assessor runs may use six concurrent
headless sessions per provider.

## Baseline findings now governing the plan

| Finding from `baseline-v4` | Interpretation | Integrated response |
|---|---|---|
| Mean run-level supplied-gene citation coverage was 36.3%; function-supported coverage was 34.5% | Mechanism narratives were much better supported than the supplied gene lists | Plan 004 now requires a completed supplied-gene evidence ledger before theme formation |
| 105/258 retained paper-within-run instances studied no supplied gene; only 20/258 studied multiple supplied genes | Regulator/pathway papers consumed evidence volume, while papers remained concentrated on a few genes | Separate supplied-gene and regulator accounting; require explicit gene–paper edges |
| At least 521 PubMed records were fetched across the runs | More raw retrieval alone is unlikely to solve coverage | Reallocate retrieval across genes; do not increase result counts by default |
| Mean citation-set Jaccard was 0.120, but covered-gene Jaccard was 0.698 | Different papers can preserve biological coverage | Plan 005 evaluates gene/function coverage separately from paper identity |
| Context directness was overclaimed on 72/260 assessable links | Context labels need calibration, but cross-tissue evidence remains useful | Keep context separate from functional support; partial/indirect evidence is valid when labeled honestly |
| All runs ended naturally, yet 11/14 exceeded configured `max_turns=30` (25–62 turns) | The SDK turn cap was non-binding in this run | Plan 005 adds client-side enforcement and phase-aware stopping telemetry |

Detailed model judgments and reliability results are preserved in the completed
[reference-quality audit record](006-reference-quality-audit.md) and
[`baseline-v4/reference_quality/`](../benchmarks/literature-v1/baseline-v4/reference_quality/).
A full human review is not a current gate; a small blinded human spot check remains optional before
a production release.

## Current decisions

- The unit of retrieval accountability is a supplied gene–paper claim, not a mechanism-level paper
  bucket.
- Every supplied gene must receive a documented research outcome before themes are finalized:
  `evidence_found`, `searched_no_evidence`, or `unresolved_identifier`.
- Perturbation regulators remain biologically important but use a separate ledger and never count
  toward supplied-gene coverage.
- Theme formation remains capped at three mechanisms, but the cap applies only after the gene
  evidence pass.
- Keep production limits unchanged: the pilot showed a measurable coverage benefit, but unbounded
  diagnostic execution failed the contradiction, cost, latency, and recovery gates. Optimize
  repeated gene/regulator work and demonstrate a bounded setting before promotion.
- Do not replace a stable module interpretation merely because more papers were retrieved. Preserve
  a module skeleton and require exact evidence for a change in mechanism, direction, or predicted
  phenotype; otherwise add evidence without renaming the module.
- Treat module supporting-gene count as annotation breadth, not paper coverage. The factual metric
  is the audited set of supplied genes whose assigned function is supported by a retained paper.

## Non-negotiable rules

- Do not change PubMed fetch batching (`MAX_FETCH_IDS=20`) or the BioC batch size (`100`).
- Keep `program_genes` and `distinctive_genes` unchanged between baseline and candidate.
- Conditions guide searches but are never forced into every query.
- OpenAlex titles are discovery data, not evidence. A selected paper needs abstract/full text.
- Store paper role, selection reason, studied genes, and function-supported genes per
  mechanism–paper link.
- Accept valid DOI-only papers. Quarantine conflicting PMID/DOI pairs.
- Keep assay absent from research guidance.

## Deferred

- Production LLM-based entailment scoring.
- Full blinded human review of every citation link.
- Changing the implicit assay default.

## Progress-update protocol

When work advances, update all applicable items in this file in the same change:

1. change the plan status and named gate;
2. link the produced artifact or verification result;
3. record any decision that changes a downstream plan;
4. identify exactly one current next frontier;
5. preserve failed or superseded benchmark evidence rather than rewriting history.
