# Plan 006: Audit reference quality with model assessors

> **Status: COMPLETE; INTEGRATED EVIDENCE RECORD, NOT A SEPARATE PIPELINE STAGE.** Applies to the
> frozen `baseline-v4` literature run. Its results close Plan 001, revise Plan 004, and define Plan
> 005 comparison metrics. This is a model-assessed citation audit, not human ground truth.

## Goal

Measure whether the agent cited the supplied genes, whether each paper supports the function for
which it was cited, and whether direct/partial/indirect context labels are honest. Exact tissue or
cell context is descriptive; it is not an evidence acceptance gate.

## Cohort

- All 261 retained mechanism-paper links from the 14 `baseline-v4` sessions.
- Ten unique programs: brain endothelial P8-P11 and hepatocyte P20-P25.
- Repeat-run stability for P10, P11, P21, and P25.
- Per-program supplied-gene denominator: the unique 15 program genes plus 8 distinctive genes.
  Perturbation regulators are reported separately.

## Assessment unit and rubric

Review each `session x mechanism x paper` link. Split broad mechanism prose into the smallest
gene-function claim that the paper was selected to support. Record:

- supplied genes actually studied, including accepted orthologs but excluding paralogs and
  background-only mentions;
- supplied genes for which the cited finding supports the assigned function;
- functional support: `supports`, `partial`, `no`, `contradicts`, or `not_assessable`;
- evidence directness: `causal`, `observational`, `secondary`, or `background`;
- direction: `matches`, `unclear`, or `reversed`;
- context: `direct`, `partial`, or `indirect`, plus whether the agent's declared context label was
  accurate;
- a short rationale and a traceable evidence span from the cached source.

Different tissue or cell-type evidence may support a general gene function. Penalize an inaccurate
context claim, especially indirect evidence presented as direct, rather than penalizing the evidence
for not being an exact context match.

## Text strategy

1. Deduplicate retrieval across the 236 unique papers and preserve the frozen source hashes.
2. Use the existing 103 open-access full texts and 133 complete PubMed abstracts.
3. Use abstracts as the default high-yield evidence source when they explicitly report the relevant
   result. Absence from an abstract is not evidence against a claim.
4. Attempt lawful full-text retrieval only for ambiguous anchors, direction questions,
   contradictions, and assessor disagreements. Cache any augmentation separately from the frozen
   baseline artifacts.
5. Mark insufficient text `not_assessable`; never convert missing detail into `no`.

## Metrics

Primary per-session metrics:

- supplied-gene citation coverage = unique supplied genes studied by at least one cited paper /
  all unique supplied genes;
- supplied-gene function-supported coverage = unique supplied genes with at least one supported or
  partially supported assigned function / all unique supplied genes;
- citation functional-support distribution and contradiction rate;
- directness distribution;
- agent context-label accuracy and direct-overclaim rate;
- red flags by reason: wrong gene, paralog, background-only, wrong function, reversed direction,
  context overclaim, inappropriate secondary source, or insufficient text.

For repeat runs, also report citation-set Jaccard, covered-gene Jaccard, function-supported-gene
Jaccard, and stability of mechanism-level conclusions.

## Execution

1. Add a versioned reference-quality schema, validator, deterministic aggregator, and report
   generator without changing production research behavior.
2. Build compact per-session review tasks from the frozen packet. Search locates candidate passages;
   the assessor makes the semantic judgment and records the supporting span.
3. Assign one primary model assessor to every link. Independently double-score the packet's fixed
   20% subset and adjudicate disagreements.
4. Validate every evidence span against the cached source and every reviewed gene against the
   supplied-gene list.
5. Generate JSON, CSV, and HTML outputs with per-program tables, link-level rationales, red flags,
   and repeat-run stability.

## Verification

- All 261 links have a final structured judgment.
- The fixed double-score subset is at least 20% and uses independent assessors.
- Every positive/contradictory judgment has a source-backed evidence span.
- Coverage denominators reproduce the supplied input lists; regulators never enter that denominator.
- Abstract-only ambiguity is `not_assessable`, not negative evidence.
- All outputs state that assessments were performed by models rather than humans.

## Done when

`baseline-v4/reference_quality/` contains the protocol, review shards, adjudicated link judgments,
per-session and aggregate metrics, repeat-run stability, a red-flag appendix, and a readable HTML
report.

## Execution result — 2026-07-22

- Completed all 261 citation–mechanism links across 14 sessions and 10 programs.
- Independently double-scored the fixed 53-link subset (20.3%); 25 disagreements were adjudicated.
  Exact all-field agreement was 52.8%, while studied-gene and function-supported-gene agreement was
  94.3% and 90.6%, respectively.
- Final support: 182 `supports`, 70 `partial`, 3 `no`, 4 `contradicts`, and 2 `not_assessable`.
- Mean run-level supplied-gene citation coverage was 36.3%; mean function-supported coverage was
  34.5%. Citation coverage ranged from 13.0% (P11 run 2) to 73.9% (P21 run 1).
- The agent overclaimed context directness on 72 of 260 assessable links (27.7%). Indirect or partial
  evidence remained eligible for functional support.
- Mean repeat-run citation Jaccard was 0.120, compared with 0.698 for covered genes and 0.611 for
  function-supported genes: paper selection was unstable, but biological coverage was more stable.
- Wrote the adjudicated judgments, reliability artifact, per-program and per-session CSVs, red-flag
  appendix, metrics JSON, and validated portable HTML report under `baseline-v4/reference_quality/`.
