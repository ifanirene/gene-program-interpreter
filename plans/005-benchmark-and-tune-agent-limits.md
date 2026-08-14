# Plan 005: Benchmark the candidate and tune Agent-SDK limits

> **Status: PAIRED PILOT AND FULL MODEL AUDIT COMPLETE; PRODUCTION PROMOTION REJECTED.**
> The retrieval-first quality direction is retained, but production defaults remain unchanged
> because the unbounded diagnostic added two source-opposed claims and failed the cost, latency,
> and recovery gates.
> Drift check:
> `git diff --stat 7bbefb0..HEAD -- research/research_parallel.py gpi/run_pipeline.py benchmarks/literature-v1 configs README.md`

## Goal

Decide whether expanded retrieval needs more Agent-SDK turns, budget, or time. Do not increase
production defaults merely because the workflow has more steps.

## Scope

- Add limit validation, terminal telemetry, cumulative attempt accounting, deterministic retry
  handling, pilot artifacts, and `tests/test_research_limits.py`.
- Change defaults/config/docs only if benchmark gates justify it.

## First fix observability

Frozen `baseline-v4` observations to reproduce before changing behavior:

- The run was configured with `max_turns=30`, but 11/14 successful sessions reported 32–62 turns;
  all 14 ended with `stop_reason=end_turn`, not a max-turn error. Test CLI-version compatibility and
  enforce a client-side tool/turn ceiling if the SDK flag remains non-binding.
- The baseline agent used deferred `ToolSearch` and attempted an out-of-workspace `Read` before
  reading the correct isolated bundle. Add an exact-path Read gate and explicitly account for
  tool discovery without expanding the side-effect surface.
- The mean run fetched at least 37 PubMed records but cited only 36.3% of supplied genes. Limit
  experiments must measure phase completion and per-gene allocation, not treat more searches or
  citations as quality by themselves.

- Validate positive finite `max_turns`, `max_budget_usd`, and `per_program_timeout` before SDK use.
- Record configured values plus per-attempt stop reason, SDK turns, client-counted tool iterations,
  tokens, duration, cost, and completion of the supplied-gene, regulator, theme, expansion, and gap
  phases in Plan 003 `attempt_records[]`.
- Do not retry explicit max-turn or budget exhaustion with identical limits. Keep bounded retries for
  transient transport errors or fixable payload validation.

## Pilot

Use brain P10/P11 and liver P21/P25, two repeats each, with identical model, SDK, auth, bundles,
protocol, and assessor-text policy within B/C.

| Arm | Pipeline | Turns / budget / timeout |
|---|---|---|
| A | Frozen `baseline-v4` | observed configuration `30 / $1.00 / 600s` |
| B | Expanded candidate | `30 / $1.00 / 600s` |
| C | Expanded candidate, diagnostic ceiling | `60 / $1.25 / 900s` |

Run B first. Run C only if B has a cap-related failure, misses the graph/gap phase, or is otherwise
censored. The 60-turn ceiling clears the observed linear p95 of 49 turns, though not the 62-turn
maximum; it is a diagnostic ceiling, not a proposed default. Maximum API-equivalent spend with two
attempts is $16 for B and $20 for C. Stop for approval before each paid arm.

## Decision rule

Keep `30/$1/600` if B completes and C provides no meaningful quality benefit. Promote the smallest
tested limits only if C:

- rescues a cap-related failure or improves function-supported supplied-gene coverage by at least 5
  percentage points in a clearly truncated paired case;
- passes all Plan 001 correctness/quality gates;
- does not increase wrong-gene/paralog errors, contradictions, or context overclaim;
- keeps p95 cost and latency within 1.5× baseline.

The candidate workflow itself, independent of limit promotion, succeeds only if it:

- completes a trace-backed terminal state for all 23 supplied genes in every run;
- improves paired mean supplied-gene citation and function-supported coverage over the 36.3% and
  34.5% baselines. Target at least +10 percentage points; require at least +5 points with no
  material paired-case regression;
- reduces the 40.7% share of retained papers that study no supplied gene, without discarding useful
  regulator/context evidence from its separate ledger;
- reports context honestly while continuing to accept properly labeled partial/indirect evidence.

If promotion is justified, estimate budget as `ceil_$0.05(p95 cumulative cost × 1.15)` and timeout as
`ceil_30s(p95 duration × 1.25)`, capped at `60/$1.25/900`. Run the chosen setting on all 10 cases
before changing defaults. Needing more than the ceiling means reduce repeated work first.

## Verification

```bash
.venv/bin/pytest -q tests/test_research_limits.py tests/test_literature_benchmark.py tests/test_research_pipeline.py
.venv/bin/pytest -q
.venv/bin/ruff check --select E9,F63,F7,F82 research/research_parallel.py gpi/run_pipeline.py tests/test_research_limits.py
```

Tests must cover invalid limits, client-side ceiling enforcement, phase/unfinished-gene accounting,
deterministic vs transient retry, skip-C, retain-current, promote, and reject-over-ceiling decisions.

## Done when

- A/B/C artifacts record pairing keys, telemetry, quality metrics, cost, and latency.
- A signed decision artifact records either unchanged defaults or the evidence-backed new values.
- All default/config/doc surfaces agree with that decision.

## Stop if

- A/B runtime pairing is invalid, paid approval is absent, comparison changes more than the limits,
  or the proposed production setting exceeds `60/$1.25/900`.

## Offline implementation result — 2026-07-22

- Positive finite turns, budget, and timeout are validated before SDK construction.
- The one permitted Read is gated to the exact isolated bundle path; all other reads are denied.
- A client-counted assistant-turn ceiling now backs up the observed non-binding SDK limit.
- Each attempt records stop reason, SDK/client turns, tokens, duration, cost, and phase event
  counts; audit totals are cumulative across retries.
- Explicit turn/budget exhaustion is not retried with unchanged limits; transient SDK and payload
  failures retain bounded retry behavior.
- Added `tests/test_research_limits.py`; the full 176-test suite and scoped static checks pass.
- At the offline checkpoint, no candidate session had been launched; the subsequently authorized
  pilot and its expanded subscription-backed diagnostics are recorded below.

## Paired pilot result — 2026-07-22

- Arm B's P10 canary hit 30 turns; Arm C hit 60; a 120-turn diagnostic was path-dependent and
  also censored productive work. The final diagnostic used no client turn ceiling, a one-hour
  timeout, and subscription authentication.
- Eight valid paired candidate sessions were obtained: seven in the main run plus an isolated,
  provenance-preserving recovery of liver P21 r1. All 157 retained citations verified.
- Mean exact supplied-gene function coverage improved from 38.6% to 54.9% (+16.3 points). Every
  case improved: P10 +10.9, P11 +15.2, P21 +19.6, and P25 +19.6 percentage points.
- Papers studying no supplied gene fell from 40.7% to 14.0% in session-level unique candidate
  papers, while regulator/context evidence remained separately represented.
- The operational gate failed: known accepted-run cost was at least 3.23× baseline and p95 latency
  was 6.26× baseline (44.3 versus 7.1 minutes). One main session required isolated recovery, and
  SDK failures left incomplete cost/turn telemetry.
- All 133 unique candidate papers have assessor text (79 abstracts, 54 open-access full texts).
  All 165 links were independently scored by Claude Sonnet and headless Codex `gpt-5.6-sol` at
  high reasoning; all 102 disagreements were adjudicated by Claude Opus at high reasoning.
- Adjudicated function-supported coverage improved from 38.6% to 51.6% (+13.0 points), with gains
  in all eight paired sessions and all four cases. Wrong-gene, paralog-only, and wrong-function
  flags fell, while context overclaim remained flat.
- Source-opposed claims increased from 0 to 2. Here, a contradiction means the retained paper
  explicitly reports the opposite functional direction from the generated mechanism—not merely
  weak or indirect support. This fails the strict factual non-regression gate despite the coverage
  and targeting gains. Both reveal direction-sensitive biology that needs explicit qualifiers:
  Cep164 dispensability in flies and an ANGPT2 vessel-reinforcing role in an adult pathological
  BBB setting.
- The added coverage changed the biological output, not only its confidence. Independent paired
  annotation review classified 4/8 outputs as materially changed and 4/8 as the same core with a
  meaningful refinement; none were effectively unchanged. Reviewers agreed on 7/8 decisions and
  separately adjudicated the remaining pair. P11 was most interpretation-sensitive, while P21's
  cholesterol/mevalonate and SCAP–INSIG1–SREBP2 core was most stable.
- Module supporting-gene count is annotation breadth, not paper coverage. Candidate annotations
  named slightly fewer unique supporting genes per run (13.1→12.6), while the mean number with
  adjudicated function support rose from 8.9→11.9. Three sessions—P11 r1 and both P25 repeats—had
  fewer claimed genes but more audited support. The corresponding micro claim-backed rate rose
  from 67.6% to 94.1%.
- A separate unordered three-module semantic assessment compared all nine module pairs, averaged
  independent Claude Sonnet and headless Codex `gpt-5.6-sol` high-reasoning scores, and selected the
  best one-to-one matching. The reviewers chose the same matching in 15/16 comparisons. Baseline
  replicate similarity was 0.758, candidate replicate similarity was 0.654, and cross-setting
  similarity was 0.678. Candidate improved P21, was effectively flat for P11, and regressed for
  P10 and P25. More thorough retrieval therefore improved factual grounding but not overall
  annotation reproducibility.
- Repeat-run paper Jaccard improved from 0.120 to 0.184 in the mean and in 3/4 programs, but is
  still low. Function-supported-gene Jaccard improved more, from 0.611 to 0.731. Different papers
  therefore converged on more similar biological coverage, but source selection remains unstable;
  P10 regressed on both measures.
- Baseline sessions used 29–42 turns (mean 37.4). Accepted candidate outputs used 68–107 turns
  (mean 85.4); including failures and recovery raises the mean to at least 120.9 turns, or ≥3.23×
  baseline. Failed/retried attempts consumed at least 284 turns (29.4%). Two P21 failure attempts
  have incomplete terminal telemetry, so these operational totals are lower bounds.
- Candidate phase traces attribute 597/967 recorded minimum turns (61.7%) to supplied-gene work,
  159 to regulators, and 74 to the gap pass. Only 6/688 search turns repeated an exact query.
  Therefore the first optimization is a persistent evidence-state scheduler and resume-after-
  failure behavior, not string deduplication. Baseline traces predate phase labels, so their turns
  cannot be split into comparable phases without guessing.
- Research phases are currently telemetry, not hard resource controls. The runner enforces only
  whole-session turns, budget, and timeout; prompt phrases such as a capped regulator pass or six
  expansion calls are not runner-enforced. Hard control must check a phase/per-gene budget before
  every literature-tool call, reserve finalization turns, persist counters and evidence state, and
  return a structured exhausted signal so the agent advances rather than aborts.
- Biological decision: neither complete strategy dominates. Candidate is better for evidence
  grounding and is clearly better for P21; baseline is more semantically stable overall and safer
  on direction. The next process should combine candidate retrieval with a stable module prior,
  changing a module only when exact evidence changes its mechanism, direction, or phenotype.
- Next pilot: implement that hybrid prior, hard phase budgets, checkpoint/resume, annotation diffs,
  and direction checks, then test `60 turns / $1.25 / 900s`. Require all eight sessions to finish
  without recovery, function coverage at least baseline +10 points, claim-backed genes ≥90%, zero
  source-opposed claims, mean module replicate similarity ≥0.75, and no program more than 0.15 below
  baseline. The report records concrete operations, rationales, and acceptance tests for five
  changes.
- Model-assessor follow-up runs use six concurrent headless sessions per provider. The validated
  [comparison report](../benchmarks/literature-v1/candidate-evaluable/comparison-report/report.html)
  is under `candidate-evaluable/comparison-report/`.
