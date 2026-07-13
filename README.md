# Gene Program Interpreter

Interpret weighted **gene programs** (from cNMF / NMF / consensus factorization of
single-cell or Perturb-seq data) with **parallel, auditable literature research**.

Given weighted gene programs + experimental context (+ optional Perturb-seq regulator
effects), the tool validates inputs, runs deterministic enrichment/gene-summary fetches,
launches **one isolated Claude literature agent per program**, **verifies every returned
citation**, synthesizes the evidence, and renders an interactive HTML report where **every
scientific claim links to a resolvable PMID/DOI**. It never fabricates citations — an
emitted identifier either resolves to a real paper or is flagged `unsupported`.

It is **tissue-agnostic**: the biology lives entirely in a `ContextProfile` (organism,
tissue, cell type, conditions), not in the code. A liver/MASLD profile reproduces the
original ProgExplorer behavior; a CD8 T-cell profile works with zero code change.

## How it works — four executors

| Executor | Does what | Where |
|---|---|---|
| ① Skill agent | Interprets context → `ContextProfile`, orchestrates, presents | `.claude/skills/gene-program-interpreter/SKILL.md` |
| ② Research subagents | One Claude Agent SDK session per program, MCP tool-using, isolated | `research/research_parallel.py` |
| ③ Anthropic Batch | Batchable LLM transforms: annotation, presentation, theme | `gpi/anthropic_batch.py` |
| ④ Deterministic scripts | Parsing, fetches, bundles, **evidence verification**, HTML render | `gpi/`, `research/{bundle,verify}.py` |

**Guardrails:** literature research happens *only* in ② via MCP; parallelism is controlled
in Python (asyncio + semaphore), never by a manager agent; the Agent SDK runs locally;
inference goes to Anthropic — **research on the Claude.ai subscription, batch on API credit**
(no Vertex / gateway).

## Install

Python **3.10+**. Uses the Claude Agent SDK (which shells out to the local `claude` CLI)
for the research agents, and the Anthropic Batch API for the synthesis steps.

```bash
pip install -e .            # or: pip install -e ".[dev]" for tests
```

**Auth is split by executor.** The parallel research agents (executor ②) run the local
`claude` CLI on your **Claude.ai subscription** — run `claude login` (no subscription? set
`research.auth: api` to bill research to the API key instead). The Batch synthesis steps
(executor ③) use `ANTHROPIC_API_KEY`.

The literature layer is **in-process** (`research/literature.py` calls PubMed / OpenAlex /
Crossref directly) — there is **no external MCP server to install or connect**. Set these
in a repo `.env` (auto-loaded by the runner):

```bash
ANTHROPIC_API_KEY=...          # executor ③ Batch steps (theme / annotate / presentation)
PUBMED_EMAIL=you@example.com   # NCBI Entrez courtesy + Crossref polite pool
OPENALEX_API_KEY=...           # required for the OpenAlex tool (PubMed + Crossref still work without it)
NCBI_API_KEY=...               # recommended; lifts the PubMed rate limit 3→9 rps
```

## Quickstart

```bash
# See the plan and resolved context without spending anything:
python -m gpi.run_pipeline --config configs/liver_demo.yaml --dry-run

# Deterministic-only (no LLM): enrichment + bundles, literature marked incomplete:
python -m gpi.run_pipeline --config configs/liver_demo.yaml --no-research

# Full run (research on your subscription, batch on API credit):
python -m gpi.run_pipeline --config configs/liver_demo.yaml
```

Output (bundles, research results + audit, annotations, `report.html`) lands in the
config's `output_dir/`. Runs **resume from cache**, so a network/API failure is
re-runnable.

## Configuring your own dataset

Copy [`configs/liver_demo.yaml`](configs/liver_demo.yaml), point `inputs` at your
gene-loading CSV (`Name,Score,program_id[,source_program,rank]`) and optional
regulator CSV, and **replace the `context:` block** with your biology.
[`configs/example_generic.yaml`](configs/example_generic.yaml) shows a non-liver
(human CD8 T-cell) context.

## Layout

```
gpi/          deterministic core + Anthropic-batch transforms (vendored/generalized from ProgExplorer)
research/     the parallel-research subsystem: schema, bundle, protocol, research_parallel, verify
configs/      run configs (liver_demo, example_generic)
docs/         ARCHITECTURE.md (build contract)
examples/     liver demo inputs;  tests/  pytest suite + fixtures
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full module map, data contracts,
and the `ContextProfile` / `ResearchResult` schemas.

## Provenance

The deterministic front-end and HTML renderer are vendored and generalized from
ProgExplorer (read-only source), standardized on the Anthropic API. The demo fixtures
(`examples/liver_demo/`, `tests/fixtures/`) are a mouse hepatocyte in-vivo Perturb-seq
dataset used purely to exercise the pipeline.
