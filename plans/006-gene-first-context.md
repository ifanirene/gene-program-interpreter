# Plan 006: Add an opt-in gene-first interpretation mode

## Status and purpose

- Status: DONE — implementation complete; pre-existing packaging test failure documented below.
- Priority: P1
- Effort: S–M, one focused implementation session; no paid evaluation in this plan.
- Risk: medium. Prompt behavior changes intentionally; schemas, models and budgets remain stable.
- Depends on: none. Implement on current main, not on the experimental branch.
- Planned at: `8a826594b173114887cb2f9934d6b76395b16917`, 2026-09-10.
- Repository: `/Volumes/IF_PHAGE/gene-program-interpreter`.
- Number 006 avoids collisions with plans 001–005 in `codex/agent-optimise`.

The user plans to run many programs tonight. Configured diseases and biological processes currently steer literature research and final annotation toward similar themes. Add `context.interpretation_mode: gene_first` so discovery and core annotation depend on genes and biological identity, without consuming configured disease/process interests. Keep existing behavior for configurations that omit this mode. Add no model calls and do not expand retrieval budgets.

This is a bounded input-isolation fix, not proof of improved biological accuracy. Model knowledge may generate tentative search hypotheses; retrieved literature must support cited findings. A separate disease-relevance pass is deferred.

## Drift and workspace checks

Run from the repository root:

```bash
git status --short
git diff --stat 8a826594b173114887cb2f9934d6b76395b16917..HEAD -- gpi/context_profile.py research/bundle.py research/protocol.md gpi/evidence_context.py gpi/run_pipeline.py tests configs/example_generic.yaml README.md
```

At planning time, main is at the SHA above, with user-owned untracked `AGENTS.md` and `docs/agents/`. Preserve them. Inspect any subsequent in-scope changes before editing; reconcile compatible changes rather than replacing them. Use a `codex/` branch, e.g. `codex/gene-first-context`. Do not merge `codex/agent-optimise`, push, launch a paid job, or change the user's actual run config as part of this plan. Commit only if the execution instruction authorizes it.

## Current state and evidence

- `gpi/context_profile.py:157`: `resolved_keyword_query()` constructs PubMed terms from `[self.cell_type, self.tissue, *self.conditions, *self.context_terms]`. An explicit `keyword_query` overrides derivation. Other explicit overrides include `annotation_role`, `annotation_context`, `condition_context`, and `functional_context`.
- `research/bundle.py:186`: the research brief says to research genes “within the cell-type functions listed in `functions_to_consider`.” `build_bundle()` exposes `conditions`, `functions_to_consider`, and that brief. It already uses the shared selector for disjoint loading and distinctive gene sets; preserve this selector.
- `research/protocol.md`: currently permits knowledge-based grouping followed by literature confirmation. It expects configured functions as inputs. Its three-mechanism limit and identifier rules are unrelated to this fix.
- `gpi/evidence_context.py:1410` places these fields together:

```text
### Primary evidence
{gene_context}
{research_evidence_context}
{condition_context}
{functional_context}
```

- `generate_prompt()` uses both `profile.prompt_fields()` and `_context_phrase(profile)`; the latter includes `profile.conditions`. Sanitizing only one leaves a second entry point.
- `gpi/run_pipeline.py:518`, `run_gene_summaries()`, passes `cfg.profile.resolved_keyword_query()` as `--keyword`. The underlying search is `(program genes) AND keyword`; retain biological identity without interest terms here too.
- `write_profile_yaml()` writes `cfg.profile.resolved().to_dict()`. Consumers reload this materialized profile. Tests must cover this round trip because explicit old framing can otherwise re-enter.
- `cmd_emit_config()` manually enumerates context fields. A new field will be lost unless explicitly carried through.
- `_print_framing()` and emitted-config diagnostics display the original derived framing; update them to show the effective interpretation framing and selected mode.
- `_load_or_init_state()` hashes configuration, not source code. The loop skips completed steps. A fresh output directory is required for the first gene-first run; this plan does not redesign resume or caching.

The existing experimental branch (`50d8162`) retains the biased brief and adds the same conditions/functions to query guidance. It contains useful gene–paper accounting, but its eight-session pilot did not vary keyword profiles. Stored model assessments report improved function coverage alongside two new source-opposed claims and failed operational gates. Do not import its ledger, citation expansion, benchmark infrastructure, or runtime redesign tonight.

## Design contract

Add `interpretation_mode: str = "context_guided"` to `ContextProfile`. Accept exactly `context_guided` and `gene_first`; reject unknown values with a clear error before paid work. The example config should explicitly opt into `gene_first`; all existing unspecified configs remain compatible.

Add a single method, `for_interpretation() -> ContextProfile`, that produces the effective profile without mutating the original:

- In `context_guided`, return an equivalent independent profile retaining existing framing behavior.
- In `gene_first`, construct a fresh profile carrying organism, species taxid, tissue, cell type, and evidence-context vocabulary. Clear `conditions`, `context_terms`, and all five explicit interpretation overrides (`annotation_role`, `annotation_context`, `keyword_query`, `condition_context`, `functional_context`). Preserve original report/assay metadata on the original profile; it is not a discovery instruction.
- Derive the neutral persona/query from identity fields. Do not copy materialized framing from `.resolved()` into the effective profile.
- Make the projection idempotent and avoid recursive resolution. One simple implementation returns an effective profile with the legacy/default mode and only the intended fields populated; its existing accessors can then run unchanged.
- For `gene_first`, require a nonempty tissue or cell type, with a clear validation error. Do not silently produce an empty PubMed clause or guess an identity from disease terms.

Apply this projection at each interpretation boundary: bundle construction, annotation prompt construction, pipeline gene-summary query construction, and framing diagnostics. Retain the original profile for serialization, configuration hashing, report metadata, and provenance. `--emit-config` must retain the selected mode.

The raw config fields `conditions` are currently ambiguous between experiment facts and research interests. For this first mode, **all free-text profile conditions are excluded from core discovery/annotation framing**. Measured condition-specific regulator results retain their supplied labels and values; do not rewrite experimental data. The README must state this distinction explicitly. A later typed separation of experimental facts and optional interests is out of scope.

Evidence-derived diseases/processes remain legitimate. Never scrub matching words from gene summaries, papers, enrichment, cell-type measurements, or regulator results. The invariant applies to changes in config framing while holding measured evidence constant.

## Allowed edits

- `gpi/context_profile.py`
- `research/bundle.py`
- `research/protocol.md`
- `gpi/evidence_context.py`
- `gpi/run_pipeline.py`
- `tests/test_context_profile.py`
- `tests/test_research_pipeline.py`
- `tests/test_cli_ux.py`
- `tests/test_gene_first_context.py` (new integration-level regression tests)
- `configs/example_generic.yaml`
- `README.md`
- This plan and `plans/README.md` for completion records.

Out of scope: research schema/verifier changes; new retrieval tools; additional model stages; per-gene mandatory searches; checkpoint/scheduler/cache redesign; budgets/concurrency/model changes; annotation/report schema changes; dependency or version changes; real input data and user run configs; experimental-branch merges.

## Implementation steps and verification

### 1. Add the effective-profile projection

Implement the mode and projection above using the existing dataclass/serialization conventions. Use `tests/test_context_profile.py` as the unit-test pattern. Test ordinary construction, `from_dict`, nested YAML, `.resolved()` round trips, invalid mode, missing identity, idempotence, and original-object immutability. Existing context-guided tests must continue passing unchanged.

Verify: `.venv/bin/python -m pytest -q tests/test_context_profile.py` → all pass.

### 2. Connect all active interpretation boundaries

At the start of `build_bundle()`, use the effective profile throughout bundle assembly and `_build_research_brief()`. Include tissue explicitly alongside organism/cell type. Remove the instruction restricting research to configured functions when the effective profile has none; avoid an unconditional reference to an empty `functions_to_consider` list. Preserve existing context-guided behavior. Gene-first bundles should not carry interest terms in any field or brief.

Adjust the protocol to allow identity-only bundles: use model knowledge to propose provisional functional hypotheses, retrieve to test/revise them, and allow functions outside any configured interest list. Do not require a new exhaustive search pass. Keep existing tool names, evidence rules and mechanism limits.

At the start of `generate_prompt()`, project once before any profile-dependent phrases or substitutions. This covers `_context_phrase`, persona, and primary-evidence blocks. Keep all supplied biological evidence intact.

Use the effective query in `run_gene_summaries()`. Do not change standalone `gene_summaries.py` defaults or retrieval APIs. Update framing diagnostics and `cmd_emit_config()` persistence. Continue writing the original resolved profile with its mode so downstream readers know how to project it.

Verify: `.venv/bin/python -m pytest -q tests/test_context_profile.py tests/test_research_pipeline.py tests/test_no_regulator_prompt.py tests/test_cli_ux.py tests/test_gene_first_context.py` → all pass.

### 3. Add behavior-level counterfactual tests

Use the existing `gene_loading_csv` fixture and program 10. Follow `test_bundle_from_fixtures` and `test_bundle_gene_sets_are_disjoint_and_match_the_report`; do not mock the actual bundle or prompt builders.

Create three gene-first profiles with identical organism, taxid, tissue, cell type, and measured evidence: (a) no interests; (b) real disease/process interests; (c) unrelated sentinel interests. Vary every free-text override too, including keyword query and persona. Require exact equality of:

1. Complete serialized research bundles.
2. Complete generated annotation prompts.
3. The actual `--keyword` argument emitted by `run_gene_summaries()`; capture the subprocess invocation using monkeypatch, with no external calls.

Repeat across original and `.resolved()`/YAML-reloaded profiles. Require organism, tissue and cell type to remain represented, and taxid to remain passed to NCBI. Changing biological identity must change the corresponding outputs. Existing gene selection and supplied regulator evidence must remain unchanged.

Add a fixture evidence sentence containing a disease/process term also present in config interests; assert it survives in the annotation prompt. This prevents implementation by destructive word filtering.

Test `--emit-config` preserves gene_first and the effective dry-run framing is consistent with the query and annotation inputs. Verify mode changes participate in the existing config hash. Do not add a new resume system.

Verify: `.venv/bin/python -m pytest -q tests/test_gene_first_context.py tests/test_cli_ux.py` → all pass, without launching a network research session.

### 4. Document and finish

Add a short README section and set the example config's explicit mode. Explain the excluded free-text fields, preserved identity/measured evidence, legacy default, and fresh-output requirement. This run-config fragment is illustrative only:

```yaml
context:
  interpretation_mode: gene_first
  organism: mouse
  species_taxid: 10090
  tissue: brain
  cell_type: brain endothelial cell
# Other inputs/settings remain those of the actual dataset.
output_dir: runs/brain_gene_first_fresh
```

No new paid stages or increased per-program limits. This cannot guarantee unchanged total spend because the model can choose different searches within existing caps.

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check --select E9,F63,F7,F82 gpi/context_profile.py research/bundle.py gpi/evidence_context.py gpi/run_pipeline.py tests/test_gene_first_context.py
git diff --check
git diff --stat
git status --short
```

Expected: tests pass, targeted fatal lint passes, no whitespace errors, only allowed task edits plus the preserved pre-existing untracked files. Existing focused baseline: 18 tests passed in context_profile/research_pipeline/no_regulator_prompt; fatal lint passed on the four production Python files during planning. The full suite has not been run by the advisor. The repo provides pytest and ruff through `.venv`; do not invent a mandatory typecheck command or install new dependencies.

Review the diff for both invariance and preserved identity/evidence. Update the index to DONE only after completion. Report the commands/results and the exact implementation commit or uncommitted state.

## Tonight's handoff and acceptance

- [x] Gene-first behavior is opt-in, validated, and survives config emission/materialization.
- [x] Counterfactual config-interest changes leave bundles, annotation prompts, and effective PubMed keyword arguments identical.
- [x] Organism, tissue, cell type, taxid and measured evidence survive.
- [x] Tests distinguish evidence-derived process words from configured interests.
- [x] No schema changes, new model calls, raised budgets, or experimental-branch merge.
- [ ] Full pytest is entirely green: 135 passed, one baseline packaging failure (see completion record).
- [x] Targeted fatal lint and diff checks pass.
- [x] README instructs using a **new output directory**. Do not rely on a code update alone or `--start-from` to invalidate old completed stages.

Before the user's large run, inspect `gpi --config ACTUAL_CONFIG --dry-run` using the implemented checkout/runtime and a fresh output path. The executor should provide the concrete command once the actual run config is known; do not fabricate its path. A dry run does not validate biological annotation quality. A small paid smoke run would give useful operational evidence, but it is a separate action requiring the user's run instructions; do not silently launch it under this plan.

## Stop conditions and deferred work

Stop and report if satisfying the input invariant needs changing schemas, adding a model stage, raising budgets, or editing files beyond this scope. If gene-first loses measured regulator/cell-type evidence or biological identity, fix that within scope before claiming completion. Report unrelated baseline failures separately; do not repair the whole repo.

Defer the optional disease-relevance pass, typed experimental-context fields, large repeated biological evaluations, exact gene–paper ledgers, citation expansion, and checkpointing. Shared annotations are not inherently erroneous: never force label diversity. The later evaluation must assess evidence support and keyword sensitivity separately from within-program repeatability and between-program differences.


## Completion record — 2026-09-10

Implemented on current `main` per the user's explicit implement instruction, which overrides
this plan's suggested new branch. Original user-owned `AGENTS.md` and `docs/agents/` remain
untracked and untouched. No paid research, push, actual run-config edits, schema changes,
new model stages, dependency changes, or budget changes were made.

- Test-first cycles exercised profile projection/validation, full bundle and prompt invariance,
  pipeline subprocess arguments, and config emission/dry-run framing.
- Focused command from step 2: **28 passed** (one existing `datetime.utcnow()` warning).
- Full `.venv/bin/python -m pytest -q`: **135 passed, 1 failed**. Failure:
  `tests/test_packaging.py::test_every_runtime_module_imports_from_the_wheel` raises
  `ModuleNotFoundError: No module named 'httpx'` in the isolated wheel environment.
- Repeated that exact packaging test in a temporary clean export of baseline
  `8a826594b173114887cb2f9934d6b76395b16917`: identical missing-httpx failure. This is an
  existing clean-install dependency problem, outside this plan's dependency-change scope.
- Targeted fatal ruff checks and `git diff --check` pass. No typechecker is configured.
- Independent code-review axes: Standards **0 findings**; Spec **0 findings**.
- The runnable example opts into gene-first, and its comments clarify that retained conditions
  and context terms are provenance. README requires a fresh output directory.

Acceptance establishes deterministic input isolation and evidence preservation, not improved
biological annotation quality. The actual run-config path has not been supplied; its dry run
and any paid smoke run remain user-run preparation, not part of this implementation.
