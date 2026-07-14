# Gene Program Interpreter (GPI)

**Turn a list of weighted gene programs into a biological story — where every claim links to a real paper.**

You give GPI your gene programs (from cNMF / NMF / consensus factorization of single-cell or
Perturb-seq data) plus a short description of your experiment. GPI reads the literature for each
program *in parallel*, checks that every citation is a real, resolvable paper, and writes an
interactive HTML report. It **never invents citations**: an identifier either resolves to a real
PMID/DOI or it is dropped.

It is **tissue-agnostic** — the biology lives in a short `context` block you fill in (organism,
tissue, cell type, conditions), not in the code. Liver, T cells, tumor, brain: same pipeline.

---

## What you get

A single self-contained `report.html`. Each program gets a plain-language title, its marker
genes, mechanistic modules, the top enriched pathway, its regulators, and — if you have
Perturb-seq data — young/aged style perturbation plots.

![Program report — overview](docs/images/report_program.png)

**Every claim is traceable.** Each mechanistic module lists the exact genes and PMIDs behind it,
and an *Evidence used* line shows where the call came from (gene loadings, NCBI summaries,
Perturb-seq regulators, STRING partners, pathway enrichment, and cited literature).

![A module with resolvable citations and its evidence trail](docs/images/report_evidence.png)

**Perturb-seq is built in.** If you provide regulator effects, the report shows which perturbations
move each program, split by condition.

![Perturbation effects — young vs aged volcano plots](docs/images/report_perturbation.png)

---

## What you provide (inputs)

### Minimum — one file

A **gene-loading table** (CSV): one row per gene per program, with a gene name, a loading/score,
and a program id.

```csv
Name,Score,program_id
Sult2a2,0.00097,1
Car3,0.00093,1
Cyp2e1,0.00089,1
```

You **don't need to rename your columns** — GPI auto-detects common names:

| We need | …and accept any of these header names |
|---|---|
| gene name | `Name`, `Gene`, `Symbol`, `gene_name`, `gene_symbol`, `gene_id` |
| loading/score | `Score`, `Loading`, `Weight`, `Value`, `gene_score` |
| program id | `program_id`, `RowID`, `topic`, `factor`, `component`, `k`, `program` |

> **Check before you run anything** (free, reads your file, spends nothing):
> ```bash
> python -m gpi.run_pipeline --check-inputs --gene-loading your_loadings.csv
> ```
> It prints the detected columns, the number of programs, and the row count.

### Advanced — optional extras

Both are optional and make the report richer.

**1. Perturb-seq regulator effects** — turns on the perturbation plots and lets GPI name the
regulators of each program. One CSV, or one file **per condition** (e.g. young / aged).

| We need | …accept | 
|---|---|
| program id | `program_id`, `program_name`, `topic`, … |
| regulator gene | `target_name`, `target_gene`, `grna_target`, `regulator` |
| effect size | `log2_fc`, `log2FC`, `lfc`, `fold_change` |
| significance | `adj_pval`, `padj`, `fdr`, `q_value` (and/or a `significant` flag) |

Tab-separated (`.txt`) condition files are fine — point at them with `regulators_by_condition`
(see the config below).

**2. Cell-type enrichment** — `cell_type, program, log2_fc, fdr`. Adds which programs are enriched
in which cell types.

### Describe your biology (the `context` block)

This is what steers the literature search. The most important field is **`context_terms`** — list
6–10 phrases for *what your cell type normally does* (its normal biology), not a disease checklist.
These reach every literature agent verbatim.

```yaml
context:
  organism: mouse
  species_taxid: 10090          # 10090 = mouse, 9606 = human (must match organism)
  tissue: liver
  cell_type: hepatocyte
  conditions: [aging, MASLD]    # the disease / perturbation angle
  context_terms:                # ← highest-leverage: normal-function vocabulary
    - metabolic zonation
    - xenobiotic and drug metabolism
    - bile acid metabolism
    - gluconeogenesis and glycogen storage
    - nitrogen and urea metabolism
    - lipid metabolism
  assay: in vivo Perturb-seq
```

---

## Two ways to run GPI

Both do the same work. Pick whichever fits how you like to work.

### Way 1 — Talk to Claude (the Skill) · easiest, recommended for most biologists

If you use Claude Code, you don't have to write any config. Just point Claude at your file:

> *"Interpret my gene programs in `path/to/loadings.csv` — human CD8 T cells, chronic infection."*

Claude will: validate your file → **propose** the biology context and let you edit it →
show a **cost preview** → run the pipeline in the background with a live progress view →
open the finished report. You approve before anything is spent.

You can steer scope in plain language — *"just do programs 20–25"*, *"add my young/aged regulator
files"*, *"skip the literature step for a quick preview"*.

### Way 2 — Run the pipeline yourself (terminal) · reproducible, scriptable

Write a config once, preview it, then run it.

```bash
# 1. Preview — validates the config and prints the plan. Spends nothing.
python -m gpi.run_pipeline --config configs/liver_demo.yaml --dry-run

# 2. Full run — research on your Claude subscription, synthesis on API credit.
python -m gpi.run_pipeline --config configs/liver_demo.yaml

# Quick, free variant: enrichment + gene summaries only, no literature/LLM:
python -m gpi.run_pipeline --config configs/liver_demo.yaml --no-research
```

The output (report, research audit, annotations) lands in the config's `output_dir/`. Runs
**resume from cache**, so a dropped network connection is just re-runnable with the same command.

**Making your own config** is easy — GPI can write one for you from your biology description:

```bash
# put your context: block in a small stub file (context.yaml), then:
python -m gpi.run_pipeline --emit-config --context-file context.yaml \
  --gene-loading your_loadings.csv \
  --regulators-by-condition young=young.txt --regulators-by-condition aged=aged.txt \
  --output-dir runs/my_run --programs 20,21,22,23,24,25 \
  -o runs/my_run.yaml
```

It fills in schema-correct defaults and prints the resolved search framing so you can sanity-check
it. Or copy [`configs/liver_demo.yaml`](configs/liver_demo.yaml) (or the non-liver
[`configs/example_generic.yaml`](configs/example_generic.yaml)) and edit by hand.

> **Tip:** for a first run, pick **3–6 programs** with `programs: [...]`, not the whole set.
> Cost scales with the number of programs (see below).

---

## One-time setup

Python **3.10+**.

```bash
pip install -e .              # core install
pip install -e ".[progress]"  # optional: adds the live terminal progress view
```

**Sign in (two logins, by design):**

- **`claude login`** — the parallel literature agents run on your **Claude.ai subscription**.
  (No subscription? add `research.auth: api` to your config to bill them to the API key instead.)
- **`ANTHROPIC_API_KEY`** — the synthesis/labeling step uses the Anthropic **Batch** API.

Put your keys in a `.env` file in the repo (loaded automatically):

```bash
ANTHROPIC_API_KEY=sk-ant-...
PUBMED_EMAIL=you@example.com   # NCBI/Crossref courtesy contact — please set
OPENALEX_API_KEY=...           # enables the OpenAlex citation check (PubMed+Crossref work without it)
NCBI_API_KEY=...               # optional; speeds up PubMed lookups
```

There is **no literature database or MCP server to install** — GPI queries PubMed / OpenAlex /
Crossref directly, and works headless.

---

## Cost & safety

- `--dry-run` and `--check-inputs` **spend nothing** — use them freely.
- Literature research is capped **per program** (`research.max_budget_usd`, default \$1) and runs a
  few programs at a time (`research.concurrency`). A 6-program run is a few dollars of research plus
  the Batch synthesis.
- Nothing paid runs until you confirm (in the Skill) or launch the full command (in the terminal).

---

## Reference

| Where | What |
|---|---|
| [`configs/liver_demo.yaml`](configs/liver_demo.yaml) | worked mouse-hepatocyte example |
| [`configs/example_generic.yaml`](configs/example_generic.yaml) | non-liver (human CD8 T-cell) example |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | full module map, data contracts, schemas |

<details>
<summary><b>How it works (for developers)</b></summary>

Four executors, split by job and by billing:

| Executor | Does what | Where |
|---|---|---|
| ① Skill agent | Turns your description into a `ContextProfile`, orchestrates, presents | `.claude/skills/gene-program-interpreter/` |
| ② Research subagents | One isolated Claude Agent SDK session per program, MCP tool-using | `research/research_parallel.py` |
| ③ Anthropic Batch | Annotation, program labels, presentation, theme | `gpi/anthropic_batch.py` |
| ④ Deterministic scripts | Parsing, enrichment, bundles, **citation verification**, HTML render | `gpi/`, `research/{bundle,verify}.py` |

**Guardrails:** literature research happens *only* in ② (never in code, which only *verifies*
identifiers); parallelism is controlled in Python (asyncio + semaphore), never by a manager agent;
the Agent SDK runs locally; **research bills the Claude.ai subscription, Batch bills API credit**.

```
gpi/          deterministic core + Anthropic-Batch transforms
research/      parallel-research subsystem: schema, bundle, protocol, research_parallel, verify
configs/       run configs
docs/          ARCHITECTURE.md, images
examples/      demo inputs;  tests/  pytest suite
```
</details>

## Provenance

The deterministic front-end and HTML renderer are generalized from ProgExplorer, standardized on
the Anthropic API. The demo fixtures (`examples/liver_demo/`) are a mouse-hepatocyte in-vivo
Perturb-seq dataset used purely to exercise the pipeline. The screenshots above are from a live
run over programs 20–25 of that demo (123/123 citations resolved).
