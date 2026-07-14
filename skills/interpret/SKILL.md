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

Show the resolved framing, the step list, and the **cost scope**:

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

Then poll **every 45–60 s in separate, subsequent tool calls** — you cannot poll from inside
the launch call. Each poll: confirm the background shell is alive (`BashOutput`), then read the
snapshot:

```bash
python3 -c '
import json, pathlib, sys, time
p = pathlib.Path(sys.argv[1]) / "progress.json"
if not p.exists():
    print("no progress.json yet - pipeline still starting"); sys.exit()
d = json.loads(p.read_text())
steps = d.get("steps", [])
done = sum(1 for x in steps if x["status"] == "completed")
print("status=%s  active=%s  steps=%d/%d  snapshot_age=%.0fs" % (
    d.get("status"), d.get("active_step") or "-", done, len(steps),
    time.time() - p.stat().st_mtime))
if d.get("failed_step"):
    print("FAILED STEP:", d["failed_step"])
for x in steps:
    if x.get("error"):
        print("  error [%s]: %s" % (x["name"], x["error"]))
r = d.get("research") or {}
if r.get("n_programs"):
    print("research: %s/%s done  cost=$%.2f" % (
        r.get("n_done"), r["n_programs"], r.get("total_cost_usd") or 0))
    for a in r.get("agents", []):
        print("  %s: %s  turns=%s  tool=%s" % (
            a["program_id"], a["status"], a.get("turns"), a.get("current_tool")))
' runs/<name>
```

**REPORT EVERY POLL, EVEN WHEN NOTHING CHANGED.** Silence *is* the complaint. A moving clock is
the fix. *"Research: 2/3 programs done, P48 on turn 14, 6m12s elapsed"* beats saying nothing.

**Read liveness correctly — this is subtle and it is where monitors go wrong:**

| Field | Means | How to read it |
|---|---|---|
| `updated_at` | last pipeline **event** | Can legitimately sit still for **7+ minutes** during healthy research while an agent thinks. **NEVER conclude a hang from this.** |
| `heartbeat_at` + `progress.json` **mtime** | last snapshot **write** | Advances ~1/s while the process lives. **This is the liveness signal.** |
| `error` / `failed_step` | the actual failure reason | Report it verbatim; do not paraphrase. |

Nine steps, in order: `string_enrichment`, `gene_summaries`, `bundle`, `research`, `verify`,
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
