---
name: gene-program-interpreter
description: >-
  Interpret weighted gene programs (from cNMF/NMF/consensus factorization of
  single-cell or Perturb-seq data) with parallel, auditable literature research.
  Use when a user has gene programs / gene modules / factor loadings and wants a
  biological interpretation grounded in real, verified citations — not
  fabricated ones. Tissue-agnostic: works for any organism/tissue/condition via
  a ContextProfile. Produces an interactive HTML report where every claim links
  to a resolvable PMID/DOI. Triggers: "interpret my gene programs", "annotate
  these cNMF factors", "what do these Perturb-seq programs mean", "gene module
  interpretation with literature".
license: Apache-2.0
---

# Gene Program Interpreter

You (the skill agent) are **executor ①**: you interpret the user's dataset and
free-text context into a `ContextProfile`, check prerequisites, set cost caps, run
the pipeline, and present the report. The heavy lifting is delegated to deterministic
scripts (④), an Anthropic **Batch** for the LLM transforms (③), and — the core new
capability — one **parallel Claude Agent SDK literature agent per program** (②).

## Firm boundaries (do not cross)
- **Literature research happens ONLY in the research agents, via MCP.** Never research
  the literature yourself or in deterministic code — deterministic code only *validates*
  identifiers (does this PMID/DOI resolve?).
- **Parallelism is controlled in Python** (`research/research_parallel.py`, asyncio +
  semaphore), never by asking a manager agent to delegate programs.
- **Anthropic API only.** The Agent SDK runs locally.

## Inputs
- **Required:** a gene-loading CSV — columns `Name,Score,program_id[,source_program,rank]`
  (gene symbol, loading weight, program id). One row per gene per program.
- **Optional:** a regulator/Perturb-seq CSV (`program_id,target_gene,log2_fc,significant,...`)
  and a cell-type enrichment CSV.
- **Context (the key step):** turn the user's free-text description ("aged mouse
  hepatocyte Perturb-seq, interested in MASLD") into a **`ContextProfile`** — organism,
  species taxid, tissue, cell_type, conditions[], context_terms[], assay. The framing
  strings (annotation role, keyword query, disease context) are auto-derived, so you only
  fill the structured fields. This is what makes the tool tissue-agnostic — never assume
  liver/hepatocyte from the file shape.

## Workflow (one command)
Everything is driven by a run config (see `configs/liver_demo.yaml` for the shape;
`configs/example_generic.yaml` for a non-liver example). Build a config for the user's
dataset (their input paths + a `context:` block), then:

```bash
# from the repo root, with the project env (has anthropic + claude-agent-sdk)
python -m gpi.run_pipeline --config configs/<their>.yaml
```

Useful flags: `--dry-run` (print the plan + resolved context, spend nothing),
`--no-research` (skip the ② fan-out — deterministic enrichment only, literature marked
incomplete), `--deterministic-presentation` (skip the ③ presentation call),
`--start-from STEP` / `--stop-after STEP`, `--force-restart`. Runs resume from cache
(`pipeline_state.json`), so a network/MCP/API failure is re-runnable.

The steps, in order: `string_enrichment` → `gene_summaries` → `bundle` →
`research` (②) → `verify` (④) → `theme` → `annotate` (③ Batch) → `presentation` →
`html_report`. Output lands in the config's `output_dir/` (bundles, research_results,
research_audit, annotations, `report.html`).

## Before the research step — check prerequisites (spec §8 gate)
1. **MCP servers.** The literature agents need PubMed / OpenAlex / bioRxiv MCP servers
   connected. Run `/mcp` and confirm they're up. If not, see `docs/INSTALL_MCP.md`
   (remote `life-sciences` plugins are the zero-config path). Without them, run
   `--no-research` and tell the user the literature layer is disabled.
2. **Env keys.** `ANTHROPIC_API_KEY` (required, all LLM + Batch), `NCBI_API_KEY` and
   `OPENALEX_API_KEY` (literature retrieval), and set `PUBMED_EMAIL` (NCBI wants a contact
   email; the runner warns if it's missing).
3. **Cost caps.** The research fan-out and Batch calls spend money. The config's
   `research.max_budget_usd` is a per-program cap; `research.concurrency` (3–5) bounds
   parallelism. For a demo, run 3–5 representative programs (one strong, one ambiguous,
   one direction counterfactual), not the full dataset.

## Presenting the result
Open `report.html`. Every displayed claim links to a **resolvable** PMID/DOI; the report
visually separates supported / partial / contradictory / missing evidence. Point the user
at: the parallel research status/audit (`research_audit/`), one claim traced to its
genes + regulators + records, and — for an ambiguous program — the abstention or two
competing hypotheses. The final program **label is assigned by the cross-program
synthesis** (the annotate step), not by the per-program agents, which deliberately don't.

## Architecture reference
`docs/ARCHITECTURE.md` is the full build contract (module map, data contracts, the
`ContextProfile` and `ResearchResult` schemas). `research/protocol.md` is the SOP each
literature agent reads.
