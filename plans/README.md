# Implementation plans

Prepared with the improve skill on 2026-09-10 against main `8a82659`.

| Plan | Purpose | Priority | Effort | Depends on | Status |
|---|---|---|---|---|---|
| [006](006-gene-first-context.md) | Opt-in gene-first research and annotation with offline keyword-isolation tests | P1 | S–M; one session | None | DONE* |

Plan numbering continues after the experimental branch's plans 001–005 to avoid future collisions. This plan executes independently on current main; it does not require those experimental changes.

## Findings considered and deferred

- Merge all of `codex/agent-optimise`: not appropriate for tonight; retains keyword exposure and adds substantial unrelated runtime/evaluation changes.
- Additional disease-relevance model pass: deferred to avoid another paid stage before a large run.
- Full keyword-ablation biological benchmark: deferred; offline input invariance is the release gate for this bounded fix, not proof of biological accuracy.
- Force different program labels: rejected; shared biology can warrant similar annotations.
- Redesign resume/cache invalidation: deferred; use a fresh output directory for the new mode.

*Implementation complete; 135 tests passed and one isolated-wheel missing-httpx failure reproduced on the unchanged baseline. See plan 006 completion record.
