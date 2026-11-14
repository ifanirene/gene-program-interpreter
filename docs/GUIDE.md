# GPI user guide

Reference for running GPI beyond the defaults. Start with the [README](../README.md);
terms follow [`CONTEXT.md`](../CONTEXT.md).

**Contents** — [Inputs](#inputs) · [Choose how GPI interprets](#choose-how-gpi-interprets) ·
[Standalone CLI](#standalone-cli) · [Cost, resume, and failures](#cost-resume-and-failures) ·
[Pipeline steps and stages](#pipeline-steps-and-stages)

## Inputs

The [README](../README.md#what-you-provide) shows the required gene-loading CSV and its
accepted column names. Two inputs are optional:

- a **regulator table** of measured Perturb-seq effects (`program_id, target_gene, log2_fc,
  significant`), which adds perturbation plots and, unless you blind it, regulator evidence
  — see [Choose how GPI interprets](#choose-how-gpi-interprets);
- a **cell-type enrichment** table (`cell_type, program, log2_fc`, plus `direction` and/or
  `fdr`).

Cell-type enrichment is worth supplying: its signed log2FC values reach the annotation model
directly, and they are what let it distinguish a **cell-type identity** program from a
**cross-cell-type functional** one. Strong depletion in a lineage is as informative as
enrichment.

### Demo dataset

[`examples/brain_endothelial_demo/`](../examples/brain_endothelial_demo) ships a real dataset you
can run end to end: a mouse brain endothelial Perturb-seq screen (cNMF, k=100) from postnatal
brain, targeting 166 vascular signalling regulators.

| File | Role |
|---|---|
| `FB_moi15_seq2_loading_gene_k100_top300.csv` | gene loadings — 100 programs × 300 genes |
| `Discovery_FP_moi15_seq2_thresh10_k100_default.csv` | regulator effects — 16,200 tested pairs |
| `FP_moi15_seq2_cnmf_program_markers_celltype_l2_top10_enriched_depleted.csv` | cell-type enrichment — signed log2FC per lineage |

[`configs/example_generic.yaml`](../configs/example_generic.yaml) is a runnable config for this
dataset, wiring up all three files and scoped to three programs (9, 48, 70) with contrasting
cell-type signals. The live report source and enrichment figures are in the same folder.

## Choose how GPI interprets

The [README](../README.md#choose-how-gpi-interprets) shows the four combinations of the two
settings. This section covers what each one withholds and how reruns behave. Every requested
program is interpreted in all four combinations, and blinding withholds measurements from
interpretation, not from the report's measured heatmap and perturbation plots.

**Context framing** (`context.interpretation_mode`). *Gene-first* interprets from genes and
biological identity — organism, tissue, cell type — and requires a tissue or cell type. It
withholds free-text conditions, `context_terms`, and explicit framing overrides such as
`annotation_role` ([full list](ARCHITECTURE.md)) from research and annotation; they stay
recorded for provenance. Diseases or processes supported by the evidence itself can still
appear, and condition labels inside regulator tables are measurements, not framing, so they
stay intact. In regulator-aware runs, gene-first also moves regulator mechanisms into a
separate regulator explanation written after the program annotation is fixed.
*Context-guided* passes conditions, `context_terms`, and those overrides to research and
annotation, so keep `context_terms` to the cell type's *normal* biology and put disease or
perturbation emphasis in `conditions`. The demo config,
[`configs/example_generic.yaml`](../configs/example_generic.yaml), uses gene-first.

**Regulator use** (`annotation.include_regulators`). *Eligible regulators* are up to three
significant hits per direction per condition, after guide collapse and `mask_regulators`
exclusion; a program with none still gets its program annotation and skips only the regulator
explanation. A tested gene that is also a program gene stays a program gene when blinded. To
keep the plots but blind interpretation, retain the table paths and set:

```yaml
annotation:
  include_regulators: false
```

**Changing either setting.** Use a new `output_dir`, which also keeps the earlier run.
Rerunning in the same directory reuses stages completed under the old setting — neither a
code update nor `--start-from` invalidates them, and `--force-restart` recomputes everything
but discards the earlier run's resume state. Blinded runs also refuse saved literature
without matching blinded provenance. `--dry-run` prints the resolved settings before any paid
work; in Claude, the skill shows them at the approval step. Gene-first changes what the
models see; it has not been shown to improve biological accuracy.

Details: [regulator evidence workflow](regulator-evidence-workflow.md) ·
[architecture and data contracts](ARCHITECTURE.md).

## Standalone CLI

Install the CLI and try the demo with the commands in the
[README](../README.md#standalone-cli); it reads the same `.env`. This section covers your own
configs, scoping, and reruns.

**Author a config for your own data.** Copy `configs/example_generic.yaml` and change:

- `inputs.gene_loading` — required gene-program CSV;
- `inputs.regulators` or `inputs.regulators_by_condition` — optional regulator table(s);
- `inputs.celltype_enrichment` — optional cell-type enrichment table;
- `context` — organism, tissue, cell type, conditions, and normal cell functions;
- `context.interpretation_mode` and `annotation.include_regulators` — the two settings in
  [Choose how GPI interprets](#choose-how-gpi-interprets);
- `mask_regulators` — optional genes excluded before regulator ranking, STRING validation,
  research, and annotation; next-best regulators fill their places;
- `output_dir`, and an optional `programs` subset.

Or let the CLI assemble one from a context stub and your input paths:

```bash
gpi --emit-config --context-file context.yaml \
    --gene-loading genes.csv --output-dir runs/my_run -o runs/my_run.yaml
```

Scope a first pass with the config's `programs:` key, or with `--programs 9,48,70` when
emitting one. Use `--stop-after bundle` for preparation without literature agents or model
synthesis. `--no-research` skips literature agents, but later model stages can still incur
API charges. `--progress plain` gives terminal progress without the rich display.

Outputs land in the config's `output_dir`. Interrupted runs resume from
`pipeline_state.json`; `--start-from`, `--stop-after`, and `--force-restart` control where a
rerun picks up. Run `gpi --help` for the full flag list.

## Cost, resume, and failures

- Input validation, dry runs, and installation checks make **no paid API calls**.
- In Claude, the skill asks for explicit approval before starting paid work.
- Literature research has a configurable budget and concurrency limit. `max_budget_usd` is a
  cap **per program**, not per run — the worst case for a run is `max_budget_usd × len(programs)`.
- Runs cache completed stages in `pipeline_state.json`, so a network failure is resumable
  rather than repaid.
- A failed research stage degrades gracefully: the report still renders, with the affected
  literature marked incomplete.

## Pipeline steps and stages

| Layer | Role |
|---|---|
| Claude skill | Collects inputs, confirms context and spend approval, launches and monitors |
| Python runner | Owns stage order, resume state, concurrency, validation, and saved artifacts |
| Claude Agent SDK | Runs isolated per-program research sessions; agents choose read-only literature queries |
| Anthropic model API | Writes shared themes, program annotations, regulator explanations, and presentation text |

| Step | Runner stages | What happens |
|---|---|---|
| 1. Prepare | `string_enrichment` · `gene_summaries` · `bundle` | Gene weights, STRING enrichment, NCBI summaries, and any regulator evidence become one JSON per program in `program_bundles/`. Cell-type enrichment is formatted here for the annotation prompt; research agents do not see it. |
| 2. Research | `research` | One agent session per program, run in parallel, queries PubMed, OpenAlex, and Crossref through in-process tools and returns structured evidence via `submit_result` to `research_results/` and `research_audit/`. |
| 3. Verify | `verify` · `theme` | Code checks citation identifiers and metadata, keeping unresolved evidence labeled. An optional model call extracts shared themes (`theme.enabled: false` skips it). |
| 4. Annotate | `annotate` | The model writes each program annotation — Batch by default, live with `annotation.batch: false` — and code checks it against the annotation contract. Any regulator explanation follows and may add only regulator objects. |
| 5. Report | `presentation` · `html_report` | Presentation text comes from a live call with a deterministic fallback (`--deterministic-presentation` skips the call). Code checks gene membership, citations, and measured effects, then renders `report.html`; invalid, missing, or truncated annotations stop the report. |

Stage names are exactly what `--start-from` and `--stop-after` accept. Measured effects,
conditions, and significance always come from your input tables, never from a model.

**For agents operating GPI:** follow [`skills/interpret/SKILL.md`](../skills/interpret/SKILL.md)
for input, context, and paid-run approval gates, and use the terms defined in
[`CONTEXT.md`](../CONTEXT.md). Use [`configs/example_generic.yaml`](../configs/example_generic.yaml)
as the runnable example.
The implementation entry points are [`gpi/run_pipeline.py`](../gpi/run_pipeline.py),
[`gpi/gene_first_synthesis.py`](../gpi/gene_first_synthesis.py), and
[`research/research_parallel.py`](../research/research_parallel.py). Data contracts and further
details live in [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) and
[`docs/regulator-evidence-workflow.md`](regulator-evidence-workflow.md).

```text
.claude-plugin/   plugin and marketplace manifests
skills/           distributable Claude skill
bin/gpi           plugin runtime wrapper
gpi/              deterministic processing, model API steps, and reporting
research/         parallel research agents, protocol, and citation verification
configs/          example run configurations
tests/            offline regression tests and fixtures
```

## Claude subscription synthesis

Set `annotation.backend: claude_cli` and `theme.backend: claude_cli` to use the
logged-in Claude Code subscription for these stages. Set
`presentation.backend: claude_cli` for presentation synthesis, and retain
`research.auth: subscription` for literature research. Set each stage's model
explicitly. The CLI adapter removes API credentials from child processes and
records the resolved model. Its dollar usage values are list-price estimates,
not invoices; API `max_tokens` is not an enforced CLI output cap.

Module gene membership uses each program's top `settings.n_top_genes` loading
genes (300 by default in the example). The top-loading and unique-gene shortlist
still emphasizes leading evidence. Every module must list at least two distinct
members of its own program; enrichment evidence does not permit outside genes.
Cell-type enrichment reaches annotation as signed enrichment/depletion values.
Missing entries in the selected marker table do not establish no enrichment.

### Measured regulator display

When perturbation tables are supplied, report regulator cards must match a measured
target for that program. The parser reads only the Regulator analysis section; module
headings cannot become regulators. The Markdown publication gate rejects unmeasured
regulator names, in addition to the structured annotation guard.

The report keeps the research selection: up to three significant unmasked targets
per effect direction and condition, ranked by adjusted P using the existing selector.
It then adds every masked target that has a significant measured response in at least
one condition. Masked targets do not consume those top-three slots. Both the banner
and regulator table show this same union. The pipeline forwards its configured mask;
standalone HTML generation accepts repeated `--restore-display-regulator` arguments.
Nonsignificant or unmeasured masked targets stay excluded. Annotation prose can enrich
selected targets but cannot add extra targets.
Cell-type enrichment appears in the overview with signed effects. Pathway member
genes and enrichment figures are expandable, and the regulator effect table is compact.

### Default regulator significance

When an input regulator table has no explicit `significant` column, GPI defaults
to **adjusted P (q) < 0.1**, with a strict boundary: q=0.1 is excluded. This default
is shared by gene summaries, research bundles, theme evidence, annotation context,
and report cards and volcano plots, for both single and condition-specific tables.
If adjusted P values are absent, GPI uses raw P < 0.1 as a fallback; this does not
apply a multiple-testing correction. Supplied significance flags remain authoritative,
including upstream QC exclusions. Standalone commands accept
`--regulator-significance-threshold` to override the fallback cutoff. This regulator
cutoff is separate from cell-type and pathway enrichment thresholds.
