---
name: interpret
description: >-
  Interpret weighted gene programs from cNMF, NMF, consensus factorization,
  single-cell data, or Perturb-seq with parallel literature research and verified
  citations. Use when the user asks to annotate, explain, or biologically interpret
  gene programs, factors, modules, or loadings.
---

# Interpret gene programs

The `gpi` pipeline is the engine. You are the interface. Do not reproduce its
deterministic work in the conversation, and never invent or hand-repair a citation.

The binary is **`${CLAUDE_PLUGIN_ROOT}/bin/gpi`** — it is NOT on `PATH`. Always call it by that
full path. `doctor`, `--check-inputs`, and `--dry-run` are **free**; everything else spends the
user's money.

## The four gates

This skill has four hard gates. **Each gate is an `AskUserQuestion` tool call — not a
question in prose.** A question asked in prose gets talked past; the model answers itself and
moves on. That is exactly how the last run spent the user's money without permission.

| Gate | Before | Fixes |
|---|---|---|
| 1 INPUTS | anything else | user never learns the optional files exist |
| 2 CONTEXT | building the config | wrong/assumed biology, dead search terms |
| 3 SPEND | the paid run | self-authorized spending |
| 4 KILL | any `kill` / destructive act | killing a healthy job on a false alarm |

---

## 0. Pre-flight (free)

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" doctor
```

If `gpi` is missing, the plugin runtime is incomplete — point to
`${CLAUDE_PLUGIN_ROOT}/README.md` and stop. Fix any failed check before continuing.

**`doctor` prints the exact `.env` file it loaded and the fix for any missing key.** The two
keys a paid run *requires* are `ANTHROPIC_API_KEY` (batch synthesis) and `PUBMED_EMAIL`
(NCBI/Crossref). If `doctor` shows either as ✗, **relay its one-line fix verbatim to the user**
— it already contains the absolute path, e.g.:

```
✗ ANTHROPIC_API_KEY is missing (Anthropic Batch synthesis)
    → add it:  echo 'ANTHROPIC_API_KEY=sk-ant-…' >> /abs/path/.env
```

Tell them to put their real key after the `=`, then re-run `doctor`. Do **not** proceed to any
paid gate while a required key is ✗ — the run will die mid-way at the batch step and waste the
research spend that came before it. (The user enters their own key; never ask them to paste it to
you.) A `doctor` that reports "no .env found" means their keys live somewhere `gpi` did not look —
`gpi` searches the working directory and every parent, then `$CLAUDE_PLUGIN_ROOT`; the simplest
fix is a `.env` in the directory they run from.

## Gate 1 — INPUTS (`AskUserQuestion`)

**Glob the data directory before you ask anything.** Search for `*loading*`, `*regulat*`,
`*perturb*`, `*celltype*`, `*cell_type*`, `*enrich*`. Show the user what you found *and what
each file buys them* — a bare filename means nothing to someone deciding whether to hunt for it.

| Input | Buys | Without it |
|---|---|---|
| **gene loading** (required) | the programs themselves | nothing runs |
| **regulators** | the report's regulator section is grounded in the user's own perturbation data | that section is the model's **inference** — a plausible guess presented next to real data |
| **cell-type enrichment** | which cell types each program is on *and off* in; **depletion** often names a program better than enrichment does | the model has no idea which cells express the program |

Then **ask**: which of these to include, and *"do you have a file I did not find?"* Never
silently proceed with gene loading alone because it was the only thing you globbed up.

Validate everything the user names (free, read-only):

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" --check-inputs \
  --gene-loading <genes.csv> \
  [--regulators <regulators.csv>] \
  [--celltype-enrichment <celltype.csv>]
```

**The gene-loading pass is what gates a paid run.** Do not continue while it fails.

Pre-flight predicts the run: regulator and cell-type files are read with the same separator
sniffing the pipeline uses, so a tab-separated file passes. Gene loading is read strictly
comma-separated, because that is what `gpi.enrichment` does — a ✗ there is real.

## Gate 2 — CONTEXT (`AskUserQuestion`, two parts)

### 2a. What state are the cells in? — ASK, never assume

**Do not default to homeostasis.** Offer these, **multi-select** — a postnatal Perturb-seq
screen is *both* development *and* genetic perturbation:

`homeostasis` · `development` · `aging` · `injury / regeneration` · `disease (name it)` ·
`hypoxia / ischemia` · `inflammation` · `genetic perturbation`

The answer becomes `conditions:`. Also confirm organism, tissue, cell type, and assay.
Organism and taxid must agree — `9606` human, `10090` mouse (full table in
[reference/context.md](reference/context.md)).

### 2b. Draft `context_terms`, then get them approved

**Rules: 1–3 words each. No conjunctions. 6–8 terms maximum.**

This is not a style preference. `gpi/context_profile.py` **phrase-quotes any term containing
whitespace** before sending it to PubMed. So a term is searched as a *literal phrase*:

- `blood-brain barrier` → `"blood-brain barrier"` → a real phrase, ~100k papers. **Good.**
- `tight junctions and paracellular permeability` → `"tight junctions and paracellular
  permeability"` → matches **~0 papers**. A dead slot the user pays for. **Bad.**

Good: `blood-brain barrier`, `arteriovenous zonation`, `tip cells`, `angiogenesis`.
Bad: `TGF-beta and BMP signalling in endothelium`.

Write a context-only YAML stub:

```yaml
context:
  organism: mouse
  species_taxid: 10090
  tissue: brain
  cell_type: endothelial cell
  conditions: [development, genetic perturbation]
  context_terms:
    - blood-brain barrier
    - arteriovenous zonation
    - tip cells
    - angiogenesis
  assay: in vivo Perturb-seq
```

Build the config, which **prints the derived `keyword_query`**:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" --emit-config \
  --context-file <context.yaml> \
  --gene-loading <genes.csv> \
  [--regulators <regulators.csv>] \
  [--regulators-by-condition young=<young.csv> --regulators-by-condition aged=<aged.csv>] \
  [--celltype-enrichment <celltype.csv>] \
  --output-dir runs/<name> [--programs 9,48,70] \
  --output runs/<name>.yaml
```

`--programs` is an **`--emit-config` flag only** — it selects programs *into the config*. There
is no `--programs` on a run.

**SHOW THE USER THE DERIVED `keyword_query` AND ASK.** It is the only way they can see a dead
slot before paying for it. (If the emitted YAML shows `celltype_enrichment: null` even though
you passed the flag, you are on an older build — set it by hand under `inputs:`.)

## Gate 3 — SPEND (`AskUserQuestion`)

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" --config runs/<name>.yaml --dry-run
```

**Show the user the resolved input paths from the dry-run header.** It now lists every input
file — gene loading, regulators, each `regulators[<condition>]`, cell-type enrichment — as an
**absolute path with a `[✓]`/`[✗ MISSING]` check**, plus the `output_dir`, the exact `.env` in
effect, and per-key credential status. This is the user's one chance to catch a wrong or
unmounted file (a `✗ MISSING`, or the right name in the wrong directory) before paying. Read the
paths back to them; do not just say "inputs look fine."

Then show the resolved framing, the step list, and the **cost scope**:

> **`research.max_budget_usd` is a PER-PROGRAM (per-session) cap, not a total.** The worst case
> is `max_budget_usd × n_programs`. At the default `1.0`, a 20-program run can cost **$20**, not
> $1. This is not hypothetical: a real 6-program run cost **$4.14** under that same `1.0` "cap".

Quote the user a **range**, not the cap: typical spend is ~$0.30–$0.70 per program, worst case
`max_budget_usd` per program.

Suggest 3–5 representative programs for a first run. Then **STOP and ask.**

**NONE of the following is approval. These are the exact rationalizations used to spend the
user's money last time:**

- the user named a program number
- spend is capped by the config
- `--dry-run` passed
- the user approved a *previous* run
- the user seems to be in a hurry

Only an **explicit yes to THIS run, at THIS cost** is approval.

## 4. Run and monitor — the run is NOT a black box

**Launch with the Bash tool's `run_in_background: true` parameter.** Do not append `&`; do not
run this in the foreground. A foreground call blocks you for 15–25 minutes and the Bash tool's
600 s ceiling will **SIGKILL a perfectly healthy pipeline** mid-run.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" --config runs/<name>.yaml --progress plain \
  > runs/<name>.launch.log 2>&1
```

Then **watch it with a blocking poll** — one foreground `gpi watch` call, looped. Each call
sleeps until something happens (or 55 s), prints what changed and whether the run is alive, and
ends with a single token you obey. You **cannot** poll from inside the launch call; `watch` is a
separate, read-only process that folds the same progress log.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" watch runs/<name> --until-change --timeout 55
```

Run this in the **foreground** (NOT `run_in_background`). It is short (≤ 55 s) so it never
approaches the Bash 600 s ceiling, and blocking is the whole point: it is what actually makes
55 s pass, so you are not spin-polling an unchanged snapshot or, worse, going silent.

**The last line of the output is a token. Obey it:**

| Token | Meaning | Do |
|---|---|---|
| `CONTINUE` | alive — a step advanced, or it is healthily mid-think | Report what changed, then **call `gpi watch` again immediately.** |
| `DONE` | run finished | Stop polling → §5. |
| `FAILED` | a step failed and stopped the run | Stop polling. Read the printed error → failure playbook. |
| `STALE` | process gone / snapshot frozen | → **Gate 4**. Confirm against `runs/<name>.launch.log` before any kill. |

**Your turn does not end while the run is alive.** There is no timer that will wake you; if you
stop calling `gpi watch`, the user sees silence — and silence is the entire complaint. On
`CONTINUE`, always loop.

**REPORT EVERY POLL, EVEN WHEN NOTHING CHANGED.** The `watch` output hands you exactly what to
say: the active step and its counter, and per-agent turns / cost / elapsed / the current tool
**and its query** — e.g. *"P48 is searching PubMed for 'Madcam1 brain endothelial venous
identity', turn 14, 6m12s elapsed."* A moving clock is the fix.

**The first ~5 minutes have no snapshot BY DESIGN.** On a cold machine Python compiles the Agent
SDK and its dependencies to bytecode before the first event. `gpi watch` reports this as a
countdown, then as a `preflight k/n` step once imports begin. **Report the countdown; do NOT
investigate it; do NOT relaunch** — a second launch into the same run directory is two pipelines
fighting over one output dir.

`gpi watch` owns the liveness verdict now (process check + snapshot mtime), so you no longer
hand-read `updated_at` vs `heartbeat_at`. The rule it encodes still holds and is worth knowing: a
frozen `updated_at` during research is an agent **thinking**, not a hang — `watch` returns
`CONTINUE`, and a failure always names its step and error verbatim.

Ten steps run in order — a `preflight` import step, then the nine pipeline steps:
`string_enrichment`, `gene_summaries`, `bundle`, `research`, `verify`,
`theme`, `annotate`, `presentation`, `html_report`.

**Only `research` is degradable.** `verify` failing **stops the pipeline** — deliberately.
The rule is *never emit an unverified citation*. Do not "work around" a verify failure into a
report; report the failure.

## Gate 4 — KILL (`AskUserQuestion`)

**Never kill a running pipeline unless the user asks.** A slow step is not a failed step.

Before *any* destructive action, confirm the alarm against the raw log
(`runs/<name>.launch.log`) — **a monitoring bug looks exactly like the incident it falsely
reports.** A previous watcher script fabricated two alarms ("RESEARCH RESTART", "STEP ENDED")
out of a sentinel-value bug and a shell word-splitting bug, and nearly killed a healthy job.
If your own telemetry is the only evidence, your telemetry is the suspect.

## 5. Present the result

Open `runs/<name>/report.html`. Walk the user through it — see
[reference/report.md](reference/report.md). At minimum: trace one claim from gene → regulator →
verified PMID, and explain that a `partial` mechanism means **"we could not check this"**, not
"fabricated".

## Failure playbook

| Symptom | Cause | Action |
|---|---|---|
| **Re-ran after a pipeline upgrade; report is identical** | **The resume trap.** `compute_config_hash` hashes the **config**, not the prompt templates. An unchanged config ⇒ a resume sees `annotate: completed` and **skips it**. The user concludes the tool is broken. | `--start-from annotate`. `--force-restart` also works but **re-pays for research** — say so before using it. |
| `verify` failed, pipeline stopped | Working as designed | Read `failed_step`/`error`. Do not bypass. |
| `research` failed | Degradable | Pipeline continues; literature marked incomplete. Tell the user the report is thinner. |
| `updated_at` frozen 7 min, mtime fresh | Healthy agent thinking | **Do nothing.** Keep polling. |
| `progress.json` mtime frozen > 3 min | Process is dead | Check the launch log and `pid`, then Gate 4. |
| Need to re-render only | — | `--start-from html_report` |

Other recovery flags: `--start-from STEP`, `--stop-after STEP`, `--force-restart`,
`--no-research`, `--deterministic-presentation`, `--progress {auto,rich,plain,off}`.

## Reference

- [reference/context.md](reference/context.md) — condition menu, organism/taxid table,
  good vs. bad `context_terms` vocabularies and the PubMed-phrase rationale.
- [reference/monitoring.md](reference/monitoring.md) — poll recipes, the liveness table, the
  full failure→action playbook.
- [reference/report.md](reference/report.md) — how to walk the user through `report.html`.

Implementation details: `${CLAUDE_PLUGIN_ROOT}/docs/ARCHITECTURE.md`, only when needed.
