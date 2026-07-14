# GPI Installation & Pipeline Issues — Stanford SCG HPC

Recorded on: 2026-07-14  
System: `smsh11dsu-srcf-d15-36.scg.stanford.edu` (Stanford SCG, CentOS 7)

---

## System details

| Component | Version / path |
|-----------|---------------|
| OS | CentOS Linux 7 — kernel `3.10.0-1160.99.1.el7.x86_64` |
| System GCC | 4.8.5 (Red Hat 4.8.5-44) — **no C++17 support** |
| Conda env | `/home/irenefan/oak/perturb3` — Python 3.12.2 |
| gpi (CLI) | 0.2.0 — installed via `pip install -e .[progress]` into `perturb3` |
| Claude Code | 2.1.209 |
| uv | 0.11.28 — installed to `~/.local/bin/uv` |
| Plugin version | gene-program-interpreter 0.2.0 (`@gpi` marketplace) |
| Repo | `/oak/stanford/groups/kredhors/irenefan/perturb-seq/Final_pool/Final_pool_analysis/gene-program-interpreter` |

---

## Issue 1 — `bin/gpi` fails to build `contourpy` (C++17 not supported)

**Symptom**

```
ERROR: C++ Compiler does not support -std=c++17
× Failed to build `contourpy==1.3.3`
hint: `contourpy` (v1.3.3) was included because `gene-program-interpreter` (v0.2.0)
      depends on `matplotlib` (v3.11.0) which depends on `contourpy`
```

**Root cause**

`bin/gpi` calls `uv tool run --isolated --from "$PLUGIN_ROOT[progress]" gpi`, which creates
a fresh isolated virtualenv from source using the system Python toolchain. The system GCC
(4.8.5) predates C++17; `contourpy` 1.3.3 (a `matplotlib` dependency) requires it.

**But `matplotlib` was never used.** The report's plots are Plotly.js loaded from a CDN and the
enrichment figures are PNGs downloaded from the STRING API — the pipeline imports `matplotlib`,
`seaborn`, `pillow`, `jinja2` and `beautifulsoup4` *nowhere*. They were inherited from an
ancestor project and only ever cost install pain: they are the sole packages in the tree that
need a compiler.

**Fixed 2026-07-14 (v0.2.1) — root fix.** Those five dependencies were removed from
`pyproject.toml`. With them gone, `contourpy`/`kiwisolver` (the C++17 packages) leave the
resolution entirely and `uv` installs from prebuilt wheels only — no compiler is invoked, so the
GCC 4.8.5 build never happens. `tests/test_dependencies.py` fails if any unused dependency is
re-added. The conda-preferring `bin/gpi` hack below is **no longer needed** and was deliberately
NOT committed to the repo (it would run a possibly-stale editable install and mask packaging
bugs). A plain `claude plugin install` now works on SCG.

<details><summary>Superseded workaround (kept for the record — do not apply)</summary>

Previously we patched `bin/gpi` to prefer a conda-installed `gpi`:

```sh
CONDA_GPI="/home/irenefan/oak/perturb3/bin/gpi"
if [ -x "$CONDA_GPI" ]; then
    exec "$CONDA_GPI" "$@"
fi
```
</details>

---

## Issue 2 — `claude plugin install <local-path>` rejected

**Symptom**

```
✘ Failed to install plugin "…/gene-program-interpreter":
  Plugin not found in any configured marketplace
```

**Root cause**

`claude plugin install` only resolves names from registered marketplaces, not filesystem
paths. The `gpi` marketplace is already configured as a *directory* marketplace pointing at
the local clone, but it caches at install time and doesn't re-read on reinstall.

**Solution**

To pick up a `git pull` update:

```bash
claude plugin marketplace update gpi      # re-reads the local directory
claude plugin disable gene-program-interpreter@gpi
claude plugin install gene-program-interpreter@gpi
claude plugin list   # confirm Version: 0.2.0
```

---

## Issue 3 — `.env` credentials not found by `gpi`

**Symptom**

```
✗ ANTHROPIC_API_KEY is missing (Anthropic Batch synthesis)
✗ PUBMED_EMAIL is missing (NCBI/Crossref polite access)
```

**Root cause**

`gpi` loads `.env` from `$CWD` (the gene-program-interpreter working directory). The
project `.env` lives one level up in `Final_pool_analysis/`.

**Solution (applied)**

```bash
ln -s /oak/stanford/groups/kredhors/irenefan/perturb-seq/Final_pool/Final_pool_analysis/.env \
  /oak/stanford/groups/kredhors/irenefan/perturb-seq/Final_pool/Final_pool_analysis/gene-program-interpreter/.env
```

The symlink is in place. Running `bin/gpi doctor` from inside the repo directory should
show all green.

---

## Issue 4 — Silent startup: no output for ~4–5 minutes on first run

**Symptom**

Process runs (CPU + memory growing), log file stays empty, output directory not created.
`ps` confirms the process is alive; `ls runs/` shows nothing.

**Root cause**

`preflight_imports()` in `run_pipeline.py` imports every pipeline module up front before
creating the output directory or emitting any progress. On the first run, Python must
compile `claude-agent-sdk` (~77 MB wheel) and all transitive dependencies to `.pyc`
bytecode — this takes ~4–5 minutes on SCG. No output is written during this phase.

The output directory and `progress.json` only appear after `preflight_imports` returns.

**Fixed 2026-07-14 (v0.2.1).** The output directory and progress emitter are now created
*before* the imports, and `preflight_imports` emits a `preflight` step with a per-module
counter. The silence is now a visible, moving line — e.g. `preflight 7/12 importing
research.research_parallel` — from the first second. Removing the `matplotlib` stack (Issue 1)
also cuts a large slice of the bytecode-compile time.

To watch a background run live (works over SSH), use the new subcommand:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" watch runs/<name>
# or a single blocking poll that prints a token (CONTINUE/DONE/FAILED/STALE):
"${CLAUDE_PLUGIN_ROOT}/bin/gpi" watch runs/<name> --until-change --timeout 55
```

**Solution (still true for older builds)**

Wait at least 5 minutes before concluding the pipeline is hung. On subsequent runs,
`.pyc` caches are warm and startup is fast (~10 s).

To confirm the process is genuinely running (not hung), watch RSS memory grow:

```bash
watch -n 10 "ps -p <PID> -o pid,pcpu,rss,stat --no-headers"
# RSS will grow from ~24 MB → ~110 MB during bytecode compilation, then stabilise
```

Once `runs/<name>/progress.json` appears, poll it for step updates:

```bash
watch -n 15 "python3 -c \"import json; d=json.load(open('runs/<name>/progress.json')); print(d)\""
```

---

## Issue 5 — `--progress plain` output buffered; log stays empty

**Symptom**

`bin/gpi --config … --progress plain > run.log 2>&1 &` — `run.log` stays at 0 bytes
throughout the run.

**Root cause (corrected 2026-07-14)**

The original note here blamed the *parent* process's buffering. That was wrong: the parent's
`PlainRenderer` already prints with `flush=True`, and its `logging` handler flushes every
record — so the parent streams fine when captured to a file. The genuine culprit is the **child
step processes**. The runner spawns most steps as `python -m gpi.<module>` subprocesses with
inherited stdout, and *their* `print()` output sits in a 4–8 KB block buffer (Python
block-buffers when stdout is not a TTY) until the step ends.

This is now fixed at the source: `gpi/run_pipeline.py::_run_subprocess` sets `PYTHONUNBUFFERED=1`
in each child's environment, so a captured log receives child output as it arrives with no
special launch incantation. `MPLBACKEND` is no longer relevant — the plotting stack was removed
(see Issue 1 update).

**Also fixed:** the first ~4–5 min of apparent silence (Issue 4) is now a visible `preflight`
step with a per-module counter, and `gpi watch <run_dir>` renders live progress for a
background run — see the *Monitoring* note below.

---

## Issue 6 — `--programs` CLI flag does not override config

**Symptom**

```
bin/gpi --config configs/example_generic.yaml --programs 11,18,20 --dry-run
# dry-run still shows: programs: [9, 48, 70]
```

**Root cause**

`--programs` is only wired to `--emit-config` (config generation), not to `--config`
(pipeline execution). The `programs:` list in the YAML takes precedence.

**Solution**

Create a separate config file per program set and change both `programs:` and `output_dir:`
to avoid collisions:

```bash
cp configs/example_generic.yaml configs/brain_ec_p11_18_20.yaml
# Edit: programs: [11, 18, 20]  and  output_dir: runs/brain_ec_p11_18_20
bin/gpi --config configs/brain_ec_p11_18_20.yaml --dry-run
```

---

## Issue 7 — Volcano plots empty in the HTML report

**Symptom**

The per-program volcano plots in `report.html` are blank (no points rendered). All other
report sections (gene tables, STRING enrichment, annotation text) are present and correct.

**Observed in**

Run `runs/brain_ec_p11_18_20/report.html`, programs 11, 18, 20.  
Config: `configs/brain_ec_p11_18_20.yaml`.  
Regulator input: `examples/brain_endothelial_demo/Discovery_FP_moi15_seq2_thresh10_k100_default.csv`.

**Status**

Not investigated. Do not attempt to fix until root cause is identified.

---

## Recommended launch procedure (SCG)

Always run from inside the repo directory so relative config paths resolve correctly.

```bash
GPI_DIR=/oak/stanford/groups/kredhors/irenefan/perturb-seq/Final_pool/Final_pool_analysis/gene-program-interpreter
cd "$GPI_DIR"

# Validate
bin/gpi doctor
bin/gpi --config configs/<name>.yaml --dry-run

# Launch (background, log to file)
MPLBACKEND=Agg PYTHONUNBUFFERED=1 \
  bin/gpi --config configs/<name>.yaml --progress plain \
  2>&1 | tee runs_<name>.log &
echo "gpi PID: $!"

# Monitor (wait ~5 min for first output on cold start)
tail -f runs_<name>.log
# or poll progress.json once it appears:
watch -n 15 "python3 -c \"import json; d=json.load(open('runs/<name>/progress.json')); print(d.get('steps'))\""
```

**Resume an interrupted run** (e.g. after a node preemption):

```bash
bin/gpi --config configs/<name>.yaml --start-from research   # skip completed steps
```

**Skip the paid research step** (deterministic-only, free):

```bash
bin/gpi --config configs/<name>.yaml --no-research
```
