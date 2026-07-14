# Reference — running and monitoring

The user's single loudest complaint about this tool: *"During the run I cannot see any progress,
whether it has failed or merely waiting for something to finish… it's still a black box."*

Silence is the bug. A moving clock is the fix.

## Launch — background, always

**Use the Bash tool's `run_in_background: true` parameter.**

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" --config runs/<name>.yaml --progress plain \
  > runs/<name>.launch.log 2>&1
```

- **Do not** append `&`. The tool parameter does the detaching; a trailing `&` inside an
  already-backgrounded shell just orphans the process from the handle you need.
- **Do not** run it in the foreground. Two things break at once:
  1. You block for 15–25 minutes and cannot poll, cannot report, cannot answer the user.
  2. **The Bash tool's timeout maxes out at 600 s.** It will `SIGKILL` a *healthy* pipeline
     mid-research and leave `progress.json` frozen at `status: "running"` forever — which then
     looks exactly like a hang, and the next agent "diagnoses" a bug that never existed.
- `--progress plain` gives one clean ASCII line per event in the launch log. Use it — `rich`
  emits ANSI redraw codes that are unreadable when captured to a file.

## Poll — one blocking `gpi watch` call, looped

You cannot poll from inside the launch call. Poll with a **foreground** `gpi watch`: it blocks
until something changes (or `--timeout`), prints the delta and a liveness verdict, and ends with
a token. Looping it is the whole monitoring strategy.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" watch runs/<name> --until-change --timeout 55
```

Why blocking, foreground, and short: a model has no timer. "Poll every 45 s" is unexecutable —
nothing wakes you between calls, so you either spin-poll an unchanged file or go silent. A
55-second blocking call *is* the wait, and 55 s is comfortably under the Bash 600 s ceiling.

**The last line is a token. Obey it:**

| Token | Meaning | Do |
|---|---|---|
| `CONTINUE` | alive (a step advanced, or a healthy mid-think silence) | report the change, then call `gpi watch` again immediately |
| `DONE` | finished | stop; present the report |
| `FAILED` | a step failed and stopped the run | stop; read the printed error → playbook below |
| `STALE` | process gone / snapshot frozen | → Gate 4; confirm against the launch log before any kill |

`watch` also prints, every call, the active step + counter and a per-agent line with turns /
cost / elapsed / idle / the current tool **and its query string**. That is your report material —
read it back to the user. Tail the raw log only when you need something the snapshot does not
carry: `tail -n 20 runs/<name>.launch.log`.

For an interactive human at a terminal, `gpi watch runs/<name>` (no `--until-change`) is a live
`top`-style view that repaints until the run ends.

**REPORT EVERY POLL, EVEN WHEN NOTHING CHANGED.** *"Still on research: 2/3 done, P48 searching
PubMed for 'Cldn5 blood-brain barrier', turn 14, 6m12s elapsed"* is a **good** report. Saying
nothing for six minutes is the complaint.

## The startup window — the first ~5 minutes are silent BY DESIGN

On a cold machine, Python compiles the Agent SDK (~77 MB) and its dependencies to bytecode before
the pipeline emits anything. `gpi watch` reports this honestly:

- Before any event: a **countdown** (`Waiting for run output — 42s elapsed…`), token `CONTINUE`.
- Once imports begin: a **`preflight k/n`** step naming the module being compiled.

**Report it; do not investigate it; do NOT relaunch.** A relaunch into the same run directory is
two pipelines writing one output dir. Removing the plotting stack (v0.2.1) shortened this window,
but a cold first run on a shared filesystem can still take a few minutes.

## Liveness — `gpi watch` owns it now

`gpi watch` computes the verdict for you (process check via the pipeline's real `pid` +
`progress.json` mtime), so you no longer hand-read the snapshot fields. The rule it encodes is
still worth knowing, because it is where hand-monitoring always went wrong:

| Signal | Means | The rule |
|---|---|---|
| `progress.json` **mtime** | last snapshot write (the pipeline rewrites it ~1/s while alive) | **This is liveness.** Fresh mtime ⇒ alive, even with zero new events. |
| `updated_at` | last **event** | Can sit still 7+ min while a research agent thinks. **A frozen `updated_at` is NOT a hang** — `watch` returns `CONTINUE`. |
| `failed_step` / `steps[].error` | the failure and its reason | reported verbatim in the `FAILED` output; never paraphrase into a guess |

The trap that bit hand-monitors: `updated_at` is the *interesting* field, so it is the one you
naturally watch — and it is the one that stalls harmlessly. `gpi watch` watches the mtime instead.

## Progress artifacts

Both live in `<output_dir>/`:

| File | What |
|---|---|
| `progress.jsonl` | append-only event log — the source of truth, truncated fresh each run |
| `progress.json` | reduced snapshot, written atomically ~1/s — what you poll |
| `pipeline_state.json` | durable step state — what a **resume** reads |
| `../<name>.launch.log` | stdout/stderr of the run itself |

## The steps

A `preflight` import step (visible on a cold start as `preflight k/n`), then the nine pipeline
steps:

`string_enrichment` → `gene_summaries` → `bundle` → `research` → `verify` → `theme` →
`annotate` → `presentation` → `html_report`

**Only `research` is degradable** (`DEGRADABLE_STEPS = {"research"}`). Everything else stops
the pipeline on failure.

`verify` failing **stops the run, deliberately.** The rule is *never emit an unverified
citation*. Do not route around it — a report that renders is worth nothing if its citations were
never checked. Report the failure and let the user decide.

## Failure → action playbook

| Symptom | What it actually is | Action |
|---|---|---|
| **Re-ran after a pipeline upgrade, report is byte-identical** | **The resume trap** — see below | `--start-from annotate` |
| `updated_at` frozen 7 min, **mtime fresh** | a research agent thinking | **Nothing.** Keep polling. This is healthy. |
| **mtime** frozen > 3 min, `status: running` | the process is genuinely dead | `ps -p <pid>`, read the launch log, then **Gate 4** |
| `status: running` forever, no process | a foreground launch got SIGKILLed at 600 s | relaunch **in the background**; resume picks up completed steps |
| `research` step failed | degradable — pipeline continued | report the thinner literature; the run still produced a report |
| `verify` step failed | **not** degradable — by design | read `error`; do **not** bypass |
| Report renders but citations look unchecked | the verifier did not run | treat every citation as unverified; do not present them as verified |
| Only want the HTML re-rendered | — | `--start-from html_report` |
| Want to skip all spend | — | `--no-research` (literature marked incomplete) |

## The resume trap — this **will** bite

`pipeline_state.py::compute_config_hash` hashes the **config dict**. It does **not** hash the
prompt templates or the pipeline code.

So after the pipeline is upgraded — new prompts, new annotation logic, a bug fixed — a re-run
with the **same config** produces the **same hash**. The saved state loads, every step still
reads `completed`, and the pipeline **skips all of them.** The user sees a report identical to
the last one, concludes the fix did nothing, and concludes the tool is broken.

```bash
# Re-run the affected step and everything after it:
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" --config runs/<name>.yaml --start-from annotate --progress plain
```

`--force-restart` also works, **but it re-runs research and therefore re-pays for it.** Never
reach for `--force-restart` without telling the user it costs money again. `--start-from` is
almost always the right tool — research output is already on disk and still valid.

## Gate 4 — killing a run

**Never kill a running pipeline unless the user asks.** A slow step is not a failed step.

Before **any** destructive action, confirm the alarm against the raw log. The rule, learned the
hard way:

> **A monitoring bug looks exactly like the incident it falsely reports.**

A previous watcher script fabricated two alarms out of thin air — a `RESEARCH RESTART` from a
`prev=999` sentinel that guaranteed a spurious first-iteration trigger, and a `STEP ENDED` from
a shell word-splitting bug in `set -- $st`. Both were bugs in the *watcher*. Acting on either
would have killed a **healthy** job and dropped the run into a degraded mode for no reason.

So: if your own telemetry is the only evidence of a problem, **your telemetry is the suspect.**
Check `ps -p <pid>`, check the mtime, read the raw log — *then* ask the user.

## Recovery flags (all real; checked against the argparse)

| Flag | Effect |
|---|---|
| `--start-from STEP` | begin at STEP (the resume-trap fix) |
| `--stop-after STEP` | stop after STEP, inclusive |
| `--force-restart` | ignore saved state, re-run everything — **re-pays for research** |
| `--no-research` | skip the research step entirely; no spend; literature marked incomplete |
| `--deterministic-presentation` | use the deterministic renderer instead of the LLM batch |
| `--dry-run` | print the resolved plan; execute nothing; **free** |
| `--progress {auto,rich,plain,off}` | `plain` when captured to a file; `off` writes no snapshot |
| `--verbose` | debug logging |

There is **no `--programs` flag on a run.** `--programs` selects programs at `--emit-config`
time only — it goes into the config. To change which programs run, re-emit the config.
