# Plan 004: Add retrieval-first evidence, DOI support, and capped citation expansion

> **Status: COMPLETE (2026-07-22).** Plans 002 and 003 are complete. Benchmark finding: retrieval volume was ample, but
> retrieval was concentrated on a few genes and mechanism/regulator papers. Drift check:
> `git diff --stat 7bbefb0..HEAD -- research/protocol.md research/schema.py research/literature.py research/verify.py gpi/research_evidence_adapter.py gpi/evidence_context.py gpi/html_report.py`

## Goal

Research the supplied genes and build gene–paper evidence edges before deciding on shared themes.
Then form mechanisms from that retrieved evidence, explain why every paper was selected, accept
DOI-only evidence, and add a small auditable one-hop citation expansion.

## Scope

- Modify the protocol, research schema/client/verifier, downstream evidence adapter/context/report,
  and focused tests.
- Do not change final program labeling, general search-result limits, PMID/BioC batching, or add a
  production LLM entailment judge.

## Evidence contract

- Add a supplied-gene research ledger. Every supplied defining gene (23 in this benchmark) ends in
  exactly one retrieval state: `evidence_found`, `searched_no_evidence`, or
  `unresolved_identifier`. A search attempt is required before `searched_no_evidence`; absence from
  an abstract alone is not negative evidence.
- Keep a separate regulator ledger. Regulator and pathway papers remain useful, but never satisfy
  supplied-gene coverage.
- Store explicit `studied_genes[]` and `function_supported_genes[]` on every mechanism–paper link.
  A gene enters `studied_genes[]` only when the retrieved text studies that exact gene or an
  accepted ortholog; `function_supported_genes[]` must be a subset tied to the mechanism's claimed
  function. Paralogs and family-only background remain useful context but do not count as coverage.
- Store interpretation on a mechanism–paper `EvidenceLink`, not the globally deduplicated paper.
- Required roles: `anchor`, `context`, `corroboration`, `review`, `conflict`.
- Every new link has a non-empty `selection_reason`; retain legacy `note` compatibility.
- Keep `evidence_ids` as a derived compatibility field.
- A paper selected under any role needs retrieved abstract/full text. Title-only records remain
  discovery candidates and cannot count as support or context.

## Required workflow

1. Read tissue, supplied-gene metadata/aliases, regulator metadata, and query guidance. Initialize
   separate supplied-gene and regulator ledgers; do not pre-commit to themes.
2. Run the supplied-gene coverage pass. Target every supplied gene in at least one recorded search;
   queries may group biologically related genes, but each target must be explicit in the trace.
   Broaden tissue/cell terms when direct evidence is sparse and retain useful cross-context evidence
   as partial or indirect.
3. Fetch abstracts/full text for promising records and build a gene-level evidence table: paper,
   exact studied gene, finding/function, direction, context, limitation, and source span. Record
   searched genes with no usable evidence instead of silently dropping them.
4. Research perturbation regulators in a separate capped pass. These papers may explain pathway
   control but cannot backfill supplied-gene evidence.
5. Only after every supplied gene has a ledger state, cluster the retrieved findings into one to
   three mechanisms. A claimed supporting gene must have at least one gene–paper edge that supports
   the assigned function; genes with related biology but no such edge stay in the evidence-gap list.
6. Pick at most two anchors per mechanism, expand them once, fetch text for promising graph hits,
   then run one targeted gap pass for `searched_no_evidence` genes and submit.

This changes retrieval allocation, not raw volume. `baseline-v4` already fetched at least 521 PubMed
records, while 105/258 retained paper-within-run instances studied no supplied gene.

## DOI handling

1. Normalize and verify an exact DOI with Crossref.
2. Query Europe PMC by DOI for PMID/PMCID/abstract; use existing PubMed fetch when a PMID exists.
3. Retain a valid DOI when no PMID exists. Mark text as `abstract`, `full_text`, or `unavailable`.
4. Verify PMID↔DOI identity before merging records. A mismatch is `conflict`, unresolved, and cannot
   support, surface, or bridge two records. Network uncertainty remains `unverified`, not false.
5. Carry DOI-only evidence through the adapter, annotation output (`Supporting DOIs`), and safe
   `https://doi.org/` report links.

## Citation expansion

Add `expand_openalex_citations` with server-enforced constants:

```python
MAX_ANCHORS_PER_MECHANISM = 2
CITATION_REFERENCE_POOL = 20
CITATION_CITING_POOL = 10
CITATION_KEEP_PER_DIRECTION = 5
```

- At most three mechanism slots and six expansion calls per program.
- For each anchor, request exactly 20 references and 10 citing works; OpenAlex may return fewer.
- Filter self, retracted, idless, and duplicate records. Retain no more than five per direction.
- Rank deterministically by gene/alias, mechanism, tissue, and cell-title overlap; prefer primary
  papers on ties, then use citation count, year, and OpenAlex ID only as stable tie-breakers.
- Return score components and direction in the trace. Missing OpenAlex access disables only graph
  expansion; one failed direction does not fail the other.

## Verification

```bash
.venv/bin/pytest -q tests/test_research_evidence_links.py tests/test_literature_graph.py tests/test_research_verification.py tests/test_research_pipeline.py
.venv/bin/pytest -q
.venv/bin/ruff check --select E9,F63,F7,F82 research gpi/research_evidence_adapter.py gpi/evidence_context.py gpi/html_report.py
```

Tests must cover full supplied-gene ledger completion before theme formation, separate regulator
accounting, exact gene–paper edges, paralog exclusion, retrieval order, all roles/reasons, per-link
dedup, 2-anchor/6-call caps, exact 20/10 pools and ≤5/≤5 retention, stable ranking, partial failure,
title-only exclusion, DOI-only end-to-end rendering, and matching/conflicting/unverified PMID–DOI
pairs.

## Done when

- Text retrieval precedes mechanism formation.
- All supplied genes have a trace-backed terminal ledger state, and every claimed supporting gene
  has a source-backed function-supporting gene–paper edge.
- Regulator-only and pathway-only papers are reported separately from supplied-gene coverage.
- Every selected paper has a mechanism-local role, reason, and assessable text.
- Graph and batching caps are enforced and visible in the trace.
- DOI-only evidence works end to end; identifier conflicts never count as support.

## Stop if

- the bounded graph cannot be implemented without exposing all 30 candidates to the model, or DOI
  support requires discarding valid papers without PMIDs. Missing OpenAlex service access skips the
  optional expansion and does not stop the core gene-evidence workflow.

## Research-session stop condition

Submit only when all supplied genes have a terminal ledger state, every claimed supporting gene has
a function-supporting evidence edge, the separate regulator pass and bounded graph/gap passes are
complete or explicitly skipped, and no selected paper is title-only. If a runtime ceiling arrives
first, return an `incomplete` phase marker and the unfinished genes; never present early theme
completion as a complete program result.

## Completed result — 2026-07-22

- Added exact supplied-gene and separate regulator ledgers, trace-backed terminal-state
  validation, and rejection of incomplete/title-only submissions before normalization.
- Added mechanism-local evidence links with role, selection reason, studied genes,
  function-supported genes, finding, direction, context, limitation, and source span. Supporting
  genes without exact function edges and paralog substitutions are rejected.
- Added Crossref-first DOI resolution with Europe PMC enrichment, DOI-only propagation through
  annotation/report surfaces, and quarantine of proven PMID–DOI conflicts.
- Added bounded OpenAlex expansion with two anchors per mechanism, six calls per program, exact
  20-reference/10-citing pools, at most five retained per direction, deterministic score
  components, partial-direction failure, and trace visibility.
- Added focused evidence-link, graph, and identifier-verification tests. The full 176-test suite
  and scoped static checks pass; PubMed/BioC batching remains 20/100.
