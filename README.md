# Gene Program Interpreter

Interpret weighted gene programs from cNMF, NMF, single-cell, or Perturb-seq data
with parallel literature research and verified PMID/DOI citations.

## What should I install?

Use the **Claude Code plugin** if you want Claude to guide the whole workflow. The
plugin contains the skill and runs the Python pipeline for you.

Use the **CLI** only if you want to script or develop the pipeline yourself.

This is not a choice between a skill and a pipeline: the **skill is the user
interface; the pipeline is the engine**.

## Install — Claude Code plugin (recommended)

Prerequisites:

- Claude Code, signed in to the Claude account used for research
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) for the isolated
  Python runtime

If `uv` is not installed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install the plugin:

```bash
claude plugin marketplace add ifanirene/gene-program-interpreter
claude plugin install gene-program-interpreter@gpi
```

Restart Claude Code, or run `/reload-plugins`. The first use creates an isolated
Python environment; later runs reuse it.

### Configure credentials

Create a `.env` file in the directory where you will run the analysis:

```dotenv
ANTHROPIC_API_KEY=...          # Anthropic Batch: theme, annotation, presentation
PUBMED_EMAIL=you@example.com   # required courtesy contact for NCBI/Crossref
OPENALEX_API_KEY=...           # recommended; full OpenAlex verification coverage
NCBI_API_KEY=...               # recommended; higher PubMed rate limit
```

Authentication is intentionally split:

- Parallel literature agents use your **Claude login/subscription**.
- Batch synthesis uses **`ANTHROPIC_API_KEY`**.

No external MCP server is required. PubMed, OpenAlex, and Crossref tools run inside
the pipeline.

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
2. propose the biological context;
3. show a dry-run plan and cost scope;
4. ask before starting paid work;
5. monitor the run and open the cited HTML report.

## Install — standalone CLI

For scripting outside Claude Code:

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

Validate a gene-loading CSV without spending anything:

```bash
gpi --check-inputs --gene-loading path/to/gene_loading.csv
```

Required canonical columns are `Name, Score, program_id`. Common variants such as
`Gene`, `Symbol`, `Loading`, `Weight`, `RowID`, `topic`, and `factor` are mapped
automatically.

Preview your run config:

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

Outputs, including `report.html`, are written to the config's `output_dir`. Runs
resume from `pipeline_state.json` after interruption.

## Configure your own dataset

The Claude skill creates the config interactively. For manual setup, start from
[`configs/example_generic.yaml`](configs/example_generic.yaml) and change:

- `inputs.gene_loading` — required weighted gene-program CSV;
- `inputs.regulators` or `regulators_by_condition` — optional Perturb-seq effects;
- `context` — organism, tissue, cell type, conditions, and normal cell functions;
- `output_dir` and optional `programs` subset.

Run `gpi --check-inputs` first and `gpi --dry-run` before any paid run.

## How it works

| Layer | Role |
|---|---|
| Claude skill | Collects inputs, builds context, previews cost, launches and monitors |
| Python pipeline | Runs deterministic processing, caching, verification, and report generation |
| Claude Agent SDK | Runs one isolated literature-research session per program |
| Anthropic Batch | Synthesizes themes, labels, and presentation text |

Every returned PMID/DOI is resolved by deterministic verification. Unresolved evidence
is marked unsupported rather than presented as a real citation.

The biology is controlled by a tissue-agnostic `ContextProfile`; changing tissue or
condition does not require code changes.

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

Gene Program Interpreter is open-source software licensed under the
[Apache License 2.0](LICENSE).
