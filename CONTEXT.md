# Gene Program Interpreter

GPI turns weighted gene programs, with optional measured perturbation effects, into
literature-grounded functional interpretations and an interactive report. This glossary
fixes the user-facing language used by the README, diagrams, and agent instructions.

## Inputs

**Gene program**:
A weighted list of genes from a factorization such as cNMF, supplied as `inputs.gene_loading`; the unit GPI interprets.
_Avoid_: Factor, module, component (except when quoting the source method)

**Regulator table**:
An optional measured perturbation-effect table (`inputs.regulators` or `inputs.regulators_by_condition`) linking tested genes to program responses.
_Avoid_: Perturbation file, test table

**Context**:
The biological identity of the data: organism, taxid, tissue, cell type, conditions, and normal cell functions.
_Avoid_: Profile (internal class name), metadata

**Masked regulator**:
A gene listed in `mask_regulators`, excluded before regulator ranking and replaced in prompts by `[excluded gene]`.
_Avoid_: Filtered gene, blacklisted gene

**Eligible regulator**:
One of up to three significant hits per direction per condition, after guide collapse and masking.
_Avoid_: Top regulator, selected regulator

## Settings

**Context framing**:
The setting `context.interpretation_mode`, which decides whether configured disease/process framing reaches research and annotation.
_Avoid_: Mode, interpretation mode (in prose)

**Gene-first**:
Context framing that interprets from genes and biological identity only; requires a tissue or cell type.
_Avoid_: Unbiased, discovery mode

**Context-guided**:
Context framing that also uses configured conditions and disease/process text; the default when the key is omitted.
_Avoid_: Standard mode, legacy mode

**Regulator use**:
The setting `annotation.include_regulators`, which decides whether eligible regulators enter research and annotation.
_Avoid_: Mode, Mode 1/Mode 2, regulator mode (in prose)

**Regulator-aware**:
Regulator use where program genes and eligible regulators are researched together; requires the setting `true` and a regulator table.
_Avoid_: Mode 1, with-regulators

**Regulator-blinded**:
Regulator use where research and annotation see program genes and their evidence (STRING, NCBI summaries, cell-type enrichment) but no regulator data; recorded as `regulator_blind`. Supplied tables still feed measured report plots.
_Avoid_: Blind mode, Mode 2, gene-only

## Pipeline

**Step**:
One of five user-facing phases (Prepare, Research, Verify, Annotate, Report), each grouping one or more runner stages.
_Avoid_: Phase, pass

**Runner stage**:
One of nine resumable units in `gpi/run_pipeline.py`, named exactly as `--start-from` and `--stop-after` accept them.
_Avoid_: Step (reserved for the five phases)

**Program bundle**:
The per-program evidence file in `program_bundles/` that a research session reads.

**Research session**:
One literature agent session per gene program, run in parallel with others and ended by `submit_result`.
_Avoid_: Research pass, regulator research (no separate regulator pass exists)

**Program annotation**:
The model's functional interpretation of one gene program, checked against the annotation contract before publication.
_Avoid_: Functional annotation (file-name only), label, naming

**Regulator explanation**:
A follow-up request, made only for gene-first, regulator-aware programs with eligible regulators, that adds regulator mechanisms without changing the program annotation.
_Avoid_: Supplement (code name), regulator annotation

**Measured plot**:
A report heatmap or perturbation plot built directly from regulator tables, independent of regulator use.
_Avoid_: Regulator card (superseded)

## Step-to-stage map

| Step | Runner stages |
|---|---|
| 1. Prepare | `string_enrichment`, `gene_summaries`, `bundle` |
| 2. Research | `research` |
| 3. Verify | `verify`, `theme` (optional) |
| 4. Annotate | `annotate` (includes any regulator explanation) |
| 5. Report | `presentation`, `html_report` |

## Relationships

- Context framing and regulator use are independent; all four combinations are valid.
- Regulator-aware requires both `include_regulators: true` and a regulator table; otherwise the run is regulator-blinded.
- A regulator explanation exists only for gene-first × regulator-aware; context-guided × regulator-aware handles regulators inside the program annotation.
- A tested gene that is also a program gene stays a program gene when regulator-blinded.

## Resolved ambiguities

- Direct user choice (2026-10-03): call the settings **context framing** and **regulator use**; never use bare "mode" in user-facing prose.
- Direct user choice (2026-10-03): the README presents **gene-first** as the main path, matching `configs/example_generic.yaml`.
- Direct user choice (2026-10-03): show **five steps** mapped onto the nine runner stages.
- Agent proposal: "regulator-blinded" in prose, with the recorded value `regulator_blind` named where provenance matters.
