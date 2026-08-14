# Baseline-v4 reference-quality audit protocol

## Scope and provenance

This is a model-assessed evaluation of 261 retained mechanism-paper links from 14 frozen
`baseline-v4` sessions. It is not a human ground truth. The paper corpus contains 236 unique
papers: 103 cached open-access article bodies and 133 complete PubMed abstracts. Source hashes in
`assessor_text/` are frozen. Abstracts are acceptable when they explicitly report the finding
needed for a judgment.

## Review unit

The unit is one session, one mechanism, and one cited paper. Assess the smallest gene-function
claim that explains why the paper was selected. Do not require one citation to entail every clause
of a broad mechanism summary.

## Functional support

- `supports`: the paper reports a finding consistent with the citation-specific gene-function
  claim and its direction.
- `partial`: the paper supports a narrower, weaker, associative, or incomplete form of the claim.
- `no`: the available text establishes that the paper does not study or support the cited claim.
- `contradicts`: the reported result has the opposite direction or meaning.
- `not_assessable`: available text does not contain enough information. Missing abstract detail is
  never converted to `no`.

Positive and contradictory judgments require a short exact evidence span that occurs in the frozen
assessor text. Search terms locate passages; the assessor makes the semantic judgment.

## Gene accounting

`studied_genes` contains canonical supplied-gene symbols substantively investigated by the paper.
Accepted species orthologs count. Gene aliases are normalized to the supplied symbol. Paralog or
family evidence, a pathway-only citation, and background-only mentions do not count.

`function_supported_genes` is the subset for which the cited finding supports the function assigned
in this mechanism. A paper may therefore increase citation coverage without increasing
function-supported coverage.

The primary denominator is every unique supplied program plus distinctive gene. Perturbation
regulators are evaluated separately and never enter supplied-gene coverage.

## Directness and direction

- `causal`: perturbation or a direct biochemical/physical experiment supports the relation.
- `observational`: expression, localization, association, phenotype correlation, or descriptive
  evidence.
- `secondary`: review or other synthesis of prior work.
- `background`: the relevant statement is only introductory/background material.
- `unclear`: study role cannot be resolved from available text.

Direction is `matches`, `unclear`, `reversed`, or `not_applicable`.

## Context

Context is independent of functional support:

- `direct`: target biological context.
- `partial`: closely transferable context, such as the same cell type in another tissue or a
  closely related developmental/condition setting.
- `indirect`: another tissue, cell type, species, or experimental system that still informs a
  general mechanism.
- `not_assessable`: context is not reported in available text.

Different context is not itself a failure. Compare this assessment with the agent's declared
`context_match`. Record `overclaimed` when the agent presents partial/indirect evidence as more
direct than the paper warrants, `underclaimed` for the reverse, and `accurate` otherwise.

## Red flags

Use zero or more of: `wrong_gene`, `paralog_only`, `background_only`, `wrong_function`,
`reversed_direction`, `context_overclaim`, `secondary_as_direct`, and `insufficient_text`.

## Reliability

Every link receives a primary model judgment. The packet's deterministic 20% subset receives a
second independent model judgment. Disagreements in support, genes, direction, or context
calibration are adjudicated by another model assessment. Outputs must preserve assessor IDs and
state that labels are model-assessed.

