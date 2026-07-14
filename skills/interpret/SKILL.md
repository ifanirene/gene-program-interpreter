---
name: interpret
description: >-
  Interpret weighted gene programs from cNMF, NMF, consensus factorization,
  single-cell data, or Perturb-seq with parallel literature research and verified
  citations. Use when the user asks to annotate, explain, or biologically interpret
  gene programs, factors, modules, or loadings.
---

# Interpret gene programs

Guide the user through the installed `gpi` pipeline. The pipeline is the source of
truth; do not reproduce its deterministic work in the conversation.

## Boundaries

- Let the pipeline's isolated Claude Agent SDK sessions do literature research.
- Let deterministic code validate PMID/DOI identifiers.
- Never invent or manually repair a citation.
- Preview cost and scope with `--dry-run`; obtain confirmation before a paid run.
- Research uses the user's Claude login by default. Anthropic Batch steps use
  `ANTHROPIC_API_KEY` from the project `.env`.

## 1. Check the installation and inputs

Run:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" doctor
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" --check-inputs --gene-loading <path> \
  [--regulators <path>] [--celltype-enrichment <path>]
```

If `gpi` is unavailable, explain that the plugin runtime is incomplete and point to
`${CLAUDE_PLUGIN_ROOT}/README.md`. Do not continue to a paid run when input validation
fails.

The required gene-loading CSV has one row per gene per program. Canonical columns are
`Name, Score, program_id`; common aliases are mapped automatically. Optional regulator
input uses `program_id, target_gene, log2_fc, significant`.

## 2. Establish biological context

If the user provides a config, read its `context:` block. Otherwise, turn the user's
description into a small context-only YAML file containing:

```yaml
context:
  organism: mouse
  species_taxid: 10090
  tissue: liver
  cell_type: hepatocyte
  conditions: [aging, MASLD]
  context_terms:
    - metabolic zonation
    - bile acid metabolism
    - oxidative and mitochondrial metabolism
  assay: in vivo Perturb-seq
```

Check that organism and taxonomy ID agree (`9606` human, `10090` mouse). Propose 6–10
`context_terms` describing normal cell-type functions; keep disease or perturbation
emphasis in `conditions`. These are the highest-leverage research controls. Ask the user
to confirm them before building the full config.

## 3. Build and preview the run

After context confirmation, assemble the config:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" --emit-config --context-file <context.yaml> \
  --gene-loading <genes.csv> \
  [--regulators <regulators.csv>] \
  [--regulators-by-condition young=<young.csv> \
   --regulators-by-condition aged=<aged.csv>] \
  --output-dir runs/<name> [--programs 10,11,12] \
  --output runs/<name>.yaml
```

Then preview without spending anything:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" --config runs/<name>.yaml --dry-run
```

Show the resolved framing and program count. For a first run, suggest 3–5 representative
programs because research cost scales with program count. Ask for explicit approval before
the full run.

## 4. Run and monitor

After approval, launch in the background:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" --config runs/<name>.yaml --progress plain
```

Poll `<output_dir>/progress.json` about every 10–15 seconds. Report the nine pipeline
steps and, during research, each program's status, turns, cost, and current tool. A failed
research step is degradable; the report still renders with literature marked incomplete.
Runs resume from `<output_dir>/pipeline_state.json`.

Useful recovery flags are `--start-from STEP`, `--stop-after STEP`, `--force-restart`,
`--no-research`, and `--deterministic-presentation`.

## 5. Present the result

Open `<output_dir>/report.html`. Explain the main biological programs, what evidence made
the labels convincing, and where the tool abstained or retained competing hypotheses.
Trace at least one interpretation through its genes, regulators, and linked PMID/DOI.

For implementation details, read `${CLAUDE_PLUGIN_ROOT}/docs/ARCHITECTURE.md` only when
needed.
