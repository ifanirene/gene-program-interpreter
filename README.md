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
**Anthropic API only** (no Vertex / gateway).

## Install

Python **3.10+**. Uses the Anthropic API + the Claude Agent SDK (which shells out to the
local `claude` CLI).

```bash
pip install -e .            # or: pip install -e ".[dev]" for tests
# required env (a repo .env is auto-loaded by the runner):
export ANTHROPIC_API_KEY=...   # all LLM + Batch calls
export NCBI_API_KEY=...         # PubMed E-utilities (verifier + gene summaries)
export OPENALEX_API_KEY=...     # OpenAlex (required since 2026-02-13; keyless calls fail)
export PUBMED_EMAIL=you@example.com   # NCBI wants a contact email
```

Then connect the literature MCP servers (PubMed / OpenAlex / bioRxiv) — see
[`docs/INSTALL_MCP.md`](docs/INSTALL_MCP.md). Confirm with `/mcp` before a research run.

## Quickstart

```bash
# See the plan and resolved context without spending anything:
python -m gpi.run_pipeline --config configs/liver_demo.yaml --dry-run

# Deterministic-only (no API, no MCP): enrichment + bundles, literature marked incomplete:
python -m gpi.run_pipeline --config configs/liver_demo.yaml --no-research

# Full run (spends API; needs MCP connected):
python -m gpi.run_pipeline --config configs/liver_demo.yaml
```

Output (bundles, research results + audit, annotations, `report.html`) lands in the
config's `output_dir/`. Runs **resume from cache**, so a network/MCP/API failure is
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
docs/         ARCHITECTURE.md (build contract), INSTALL_MCP.md
examples/     liver demo inputs;  tests/  pytest suite + fixtures
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full module map, data contracts,
and the `ContextProfile` / `ResearchResult` schemas.

## Provenance

The deterministic front-end and HTML renderer are vendored and generalized from
ProgExplorer (read-only source), standardized on the Anthropic API. The demo fixtures
(`examples/liver_demo/`, `tests/fixtures/`) are a mouse hepatocyte in-vivo Perturb-seq
dataset used purely to exercise the pipeline.
