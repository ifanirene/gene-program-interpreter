# Gene Program Interpreter (GPI)

**Turn weighted gene programs into a biological story where every claim links to a real paper.**

GPI interprets programs from cNMF, NMF, single-cell, or Perturb-seq data. It runs
parallel Claude literature research, verifies every PMID/DOI, and produces an
interactive HTML report. Unresolved citations are marked unsupported rather than
presented as evidence.

The biology is tissue-agnostic: organism, tissue, cell type, and conditions live in a
small context profile instead of the code.

## Install first

### Recommended: Claude Code plugin

Use the plugin if you want Claude to validate the data, build the biological context,
preview cost, run the pipeline, and present the report.

This is not a choice between a skill and a pipeline: the **skill is the user interface;
the Python pipeline is the engine**.

Prerequisites:

- Claude Code, signed in to the Claude account used for research
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) for the isolated
  Python runtime

Install `uv` if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install GPI from this public GitHub marketplace:

```bash
claude plugin marketplace add ifanirene/gene-program-interpreter
claude plugin install gene-program-interpreter@gpi
```

Restart Claude Code or run `/reload-plugins`. The first use creates an isolated Python
environment; later runs reuse it.

### Configure credentials

Create `.env` in the directory where you will run the analysis:

```dotenv
ANTHROPIC_API_KEY=...          # Anthropic Batch: themes, annotation, presentation
PUBMED_EMAIL=you@example.com   # required courtesy contact for NCBI/Crossref
OPENALEX_API_KEY=...           # recommended; full OpenAlex verification coverage
NCBI_API_KEY=...               # recommended; higher PubMed rate limit
```

Authentication is intentionally split:

- Parallel literature agents use your **Claude login/subscription**.
- Batch synthesis uses **`ANTHROPIC_API_KEY`**.

No external MCP server is required. PubMed, OpenAlex, and Crossref tools run inside the
pipeline.

## What you get

The main output is a self-contained `report.html`. Each program gets a plain-language
title, marker genes, mechanistic modules, enriched pathways, regulators, and linked
evidence.

![Program report overview](docs/images/report_program.png)

Every mechanistic claim lists its genes and verified PMIDs/DOIs, plus the deterministic
evidence used to support the interpretation.

![A module with resolvable citations and its evidence trail](docs/images/report_evidence.png)

If you provide Perturb-seq regulator effects, the report also shows which perturbations
move each program, including condition-specific comparisons.

![Perturbation effects across conditions](docs/images/report_perturbation.png)

## What you provide

The minimum input is one gene-loading CSV with one row per gene per program:

```csv
Name,Score,program_id
Sult2a2,0.00097,1
Car3,0.00093,1
Cyp2e1,0.00089,1
```

Common column names are detected automatically:

| Required value | Accepted examples |
|---|---|
| gene name | `Name`, `Gene`, `Symbol`, `gene_name`, `gene_symbol` |
| loading | `Score`, `Loading`, `Weight`, `Value`, `gene_score` |
| program | `program_id`, `RowID`, `topic`, `factor`, `component` |

Optional inputs add Perturb-seq regulator effects or cell-type enrichment.

## First use in Claude

Start Claude Code in the directory containing your data, then run:

```text
/gene-program-interpreter:interpret path/to/gene_loading.csv
```

You can also ask naturally:

```text
Interpret these cNMF programs in aged mouse hepatocytes: path/to/gene_loading.csv
```

Claude will:

1. check the installation and input columns;
2. propose the biological context for your review;
3. show a dry-run plan and cost scope;
4. ask before starting paid work;
5. monitor the run and open the cited HTML report.

For a first run, use 3–6 representative programs because research cost scales with the
program count.

## Standalone CLI installation

Use the CLI directly if you want a scriptable workflow outside Claude Code:

```bash
uv tool install "gene-program-interpreter[progress] @ git+https://github.com/ifanirene/gene-program-interpreter.git"
gpi doctor
```

For development:

```bash
git clone https://github.com/ifanirene/gene-program-interpreter.git
cd gene-program-interpreter
uv sync --extra dev --extra progress
uv run pytest
```

`pip install -e .` still works for contributors, but it is not the recommended user
installation.

## Manual CLI workflow

Validate input without spending anything:

```bash
gpi --check-inputs --gene-loading path/to/gene_loading.csv
```

Preview a run config:

```bash
gpi --config path/to/run.yaml --dry-run
```

Run deterministic enrichment without literature agents:

```bash
gpi --config path/to/run.yaml --no-research
```

Run the full pipeline:

```bash
gpi --config path/to/run.yaml
```

Outputs are written to the config's `output_dir`. Interrupted runs resume from
`pipeline_state.json`.

## Configure your own dataset

The Claude skill creates the config interactively. For manual setup, start from
[`configs/example_generic.yaml`](configs/example_generic.yaml) and change:

- `inputs.gene_loading` — required weighted gene-program CSV;
- `inputs.regulators` or `regulators_by_condition` — optional Perturb-seq effects;
- `context` — organism, tissue, cell type, conditions, and normal cell functions;
- `output_dir` and optional `programs` subset.

The context terms are the highest-leverage research control. Use 6–10 phrases describing
the cell type's normal biology, while keeping disease or perturbation emphasis in
`conditions`.

Run `gpi --check-inputs` first and `gpi --dry-run` before any paid run.

## Cost and safety

- `--check-inputs`, `--dry-run`, and `gpi doctor` make no paid API calls.
- Literature research has a configurable per-program budget and concurrency limit.
- The Claude skill asks for approval before starting paid work.
- Runs cache completed steps, so network failures are resumable.

## How it works

| Layer | Role |
|---|---|
| Claude skill | Collects inputs, builds context, previews cost, launches and monitors |
| Python pipeline | Runs deterministic processing, caching, verification, and reporting |
| Claude Agent SDK | Runs one isolated literature-research session per program |
| Anthropic Batch | Synthesizes themes, labels, and presentation text |

## Repository layout

```text
.claude-plugin/   plugin and marketplace manifests
skills/           distributable Claude skill
bin/gpi           plugin runtime wrapper
gpi/              deterministic pipeline and Anthropic Batch steps
research/         parallel research agents, protocol, and citation verification
configs/          example run configurations
tests/            offline regression tests and fixtures
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for data contracts and the
complete module map.

## License

Gene Program Interpreter is open-source software under the OSI-approved
[Apache License 2.0](LICENSE).

## Provenance

The deterministic front end and HTML renderer are generalized from ProgExplorer and
standardized on the Anthropic API. Demo fixtures are used only to exercise the pipeline;
the report screenshots above come from a live demo run with 123 of 123 citations resolved.
