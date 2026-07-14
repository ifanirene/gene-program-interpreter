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

You (the skill agent) are **executor ①**. You: (1) onboard and validate the user's input
files, (2) turn their biology into a `ContextProfile` — *proposing* literature-research
context when they give you free text, (3) preview the plan and cost, (4) launch the pipeline
**in the background** and render live progress, and (5) present the cited report. The heavy
lifting is delegated to deterministic scripts (④), **parallel Claude Agent SDK literature
agents, one per program (②)**, and an Anthropic **Batch** for the LLM synthesis (③).

## Firm boundaries (do not cross)
- **Literature research happens ONLY in the ② research agents, via MCP tools.** Never research
  the literature yourself or in deterministic code — deterministic code only *validates*
  identifiers (does this PMID/DOI resolve?).
- **Parallelism is controlled in Python** (`asyncio` + semaphore in `research/research_parallel.py`),
  never by asking a manager agent to delegate programs.
- **Auth is split by executor.** The ② research agents run the local `claude` CLI on the
  user's **Claude.ai subscription** (`claude login`) — the runner withholds `ANTHROPIC_API_KEY`
  during the research step so the subscription is used, not API credit. The ③ Batch steps
  (theme / annotate / presentation) use the **API key**. So: **research → subscription, batch →
  API credit** (override with `research.auth: api` if the user has no subscription). The Agent
  SDK runs locally.

## The flow at a glance
1. **Inputs** — collect + validate the gene-loading CSV (and optional regulators / cell-type).
2. **Context** — point at an existing config, OR give a free-text description and I propose the
   biological context (cell type, conditions, and the high-leverage `context_terms`), confirm it,
   and write it into a config.
3. **Preview** — `--dry-run` prints the resolved framing + plan and **spends nothing**.
4. **Run** — I launch the pipeline in the background and render a live step-checklist +
   per-program research table.
5. **Report** — I open `report.html`, where every claim links to a resolvable PMID/DOI.

## Step 1 — Onboard the input files
- **Required — gene-loading CSV.** One row per gene per program. Canonical columns
  `Name, Score, program_id`. Common header variants auto-map (e.g. `Gene/Symbol`→Name,
  `Loading/Weight`→Score, `RowID/topic/factor/component`→program_id). Ask the user for the path;
  **never assume a tissue from the filename.**
- **Optional — regulator / Perturb-seq CSV.** `program_id, target_gene, log2_fc, significant`.
  Supports one file, or condition-keyed files (`inputs.regulators_by_condition: {young: …, aged: …}`)
  which drive the per-condition perturbation-effect plots.
- **Optional — cell-type enrichment CSV.** `cell_type, program, log2_fc, fdr`.

**Pre-flight before spending anything** (read-only), reusing the pipeline's own column mapper:
```bash
gpi --check-inputs --gene-loading <path> [--regulators <path>] [--celltype-enrichment <path>]
```
Relay its report (detected column mapping, program count + ids, row count). If a required column
can't be mapped, relay the error verbatim and ask the user to rename or repoint. **Never proceed
to a paid run on unvalidated inputs.**

## Step 2 — Establish biological context (two ways)
**Way A — point at a config.** If the user already has a run config (see `configs/`), read its
`context:` block, echo the resolved framing (Step 3), and skip to Step 3.

**Way B — free text → I propose the context.** From a description like *"aged mouse hepatocyte
Perturb-seq, interested in MASLD"*, propose a structured context. **The biology you propose is
what makes or breaks the research**, because only a few fields reach the ② agents:
- `context_terms[]` — **highest leverage.** The *normal cell-type function* vocabulary. It reaches
  every research agent verbatim **and** is folded into the PubMed-style `keyword_query` and the
  `condition_context`. Propose 6–10 terms describing what this cell type *normally does* — not a
  disease checklist. (Hepatocyte → metabolic zonation, xenobiotic/drug metabolism, bile-acid
  metabolism, gluconeogenesis & glycogen, nitrogen/urea, oxidative/mitochondrial, membrane
  transport, lipid metabolism.)
- `conditions[]` — the disease / perturbation emphasis (e.g. `aging`, `MASLD`). Also reaches the agents.
- `cell_type`, `tissue` — set the agent persona and subject noun.
- `organism` + `species_taxid` — **must match** (`9606` human, `10090` mouse); they drive STRING
  enrichment and NCBI gene lookups.
- `assay` — recorded for the report crumb but deliberately **never shown to the agents**.

**Confirmation loop.** Propose these by combining the user's words with your own domain knowledge
(a quick literature check for the canonical normal-function vocabulary of an unfamiliar cell type
is fine). Show the proposal **and its derived consequences** (Step 3 does this deterministically),
then ask the user to add/remove `context_terms` / `conditions` — the two levers. Keep edits at the
structured level; never hand-edit the derived framing strings (an explicit value silently overrides
derivation and defeats tissue-agnosticism). Loop until they approve.

## Step 3 — Assemble & preview the config (spend nothing)
Write the confirmed context into a config. Easiest is the built-in scaffold, which reuses the
tissue-agnostic skeleton (`configs/example_generic.yaml`) so you only author the `context:`:
```bash
# write a context-only stub (the confirmed structured fields), then:
gpi --emit-config --context-file <stub>.yaml \
  --gene-loading <path> [--regulators <path>] [--regulators-by-condition young=… --regulators-by-condition aged=…] \
  --output-dir runs/<name> [--programs 10,11,12] -o runs/<name>.yaml
```
`--emit-config` writes a complete, schema-correct config and prints the **resolved framing**
(`annotation_role`, `keyword_query`, `condition_context`, report crumb) — show that to the user.
For a first run or a demo, pick **3–5 representative programs** (one strong, one ambiguous, one
direction-counterfactual), not the full set — the research fan-out and Batch spend per program.

Then preview, spending nothing:
```bash
gpi --config runs/<name>.yaml --dry-run
```
`--dry-run` validates the config and prints the resolved framing + the 9-step plan. Get explicit
confirmation before any paid step. (Alternatively, if the user prefers, author the YAML directly
from `configs/example_generic.yaml` and rely on `--dry-run` to validate.)

## Step 4 — Prerequisites (only what's actually needed)
The default path needs **no external MCP servers**. The research agents use an **in-process**
literature server (`research/literature.py`) that calls PubMed / OpenAlex / Crossref directly —
nothing to install or connect, and it works headless.

Set these in the analysis project's `.env` (auto-loaded by the runner):
- **`claude login`** (Claude.ai subscription) — the ② research agents bill the subscription. If the
  user has no subscription, set `research.auth: api` to bill research to the API key instead.
- **`ANTHROPIC_API_KEY`** — the ③ Batch steps (theme / annotate / presentation).
- **`PUBMED_EMAIL`** — the in-process server sends it to NCBI (Entrez courtesy) and as the Crossref
  polite-pool contact. Set it.
- **`OPENALEX_API_KEY`** — **required for the OpenAlex verification tool**; without it PubMed +
  Crossref still work, so the run degrades rather than fails, but set it for full coverage.
- **`NCBI_API_KEY`** — recommended; lifts the PubMed rate limit 3→9 rps.
- **Runtime check:** run `gpi doctor`. If `gpi` is unavailable, follow the plugin or CLI
  installation in `README.md`; do not invent a separate install path here.

**Cost & scope.** `research.max_budget_usd` is a per-program cap; `research.concurrency` (3–5)
bounds parallelism. Confirm the program count from Step 1 — cost scales with it.

## Step 5 — Launch in the background & render live progress
Once the user confirms Step 3, launch the run **in the background** (do not block the conversation),
with `--progress plain` so the captured output is clean, parseable text:
```bash
gpi --config runs/<name>.yaml --progress plain
```
The runner writes `<output_dir>/progress.jsonl` (append-only events) and a reduced
`<output_dir>/progress.json` snapshot. **Poll `progress.json` every ~10–15 s** (`cat` it) and
re-render, updating in place, two things:

1. A **9-step checklist** with executor tags + status (✅ done · ▶ running · ⏳ pending · ⏭ skipped ·
   ❌ failed), showing each heavy step's sub-progress inline, e.g.:
   `string_enrichment [④] ▶ 2/3 · gene_summaries [④] · bundle [④] · research [②] · verify [④] ·
   theme [③] · annotate [③] ▶ batch 2/3 · presentation [③] · html_report [④]`.
2. During the `research` step, a **parallel-agent table** from `progress.json`'s `research.agents`:

   | Program | Status | Turns | Cost | Current tool |
   |---|---|---|---|---|

   plus a footer `3/5 programs done · $X total` (cost is `—` on subscription auth).

When the background process exits, check its status: if `html_report` completed → Step 6; if
`research` **degraded** (the one non-fatal step), say so — the report still renders with literature
marked incomplete. Runs **resume from cache** (`pipeline_state.json`), so a transient network/API
failure is re-runnable with the same command. (The CLI itself shows a live `rich` view on a TTY;
in this conversation you render from `progress.json` instead.)

## Step 6 — Present the report
Open `<output_dir>/report.html`. Every displayed claim links to a **resolvable** PMID/DOI; the
report shows per-program marker genes (top-loading + program-unique), functional modules with their
supporting PMIDs, the top enriched pathway, top regulators, and interactive per-condition
perturbation-effect plots. Point the user at: the parallel research audit (`research_audit/`), one
claim traced to its genes + regulators + records, and — for an ambiguous program — the abstention or
two competing hypotheses. The final program **label comes from the cross-program synthesis** (the
annotate step), not the per-program agents.

## Recovery, resume & flags
`--dry-run` (preview, spend nothing) · `--no-research` (skip ② — deterministic enrichment only,
literature marked incomplete) · `--deterministic-presentation` (skip the ③ presentation call) ·
`--start-from STEP` / `--stop-after STEP` · `--force-restart` · `--progress {auto,rich,plain,off}`.
State resumes from `<output_dir>/pipeline_state.json`.

## Reference
`docs/ARCHITECTURE.md` (module map, data contracts, the `ContextProfile` and `ResearchResult`
schemas), `gpi/context_profile.py` (context derivation), `research/protocol.md` (the SOP each
literature agent reads).
