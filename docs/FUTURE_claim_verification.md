# FUTURE — claim-vs-paper entailment verification (reserved, not wired in)

**Status: designed, not active.**

## What the pipeline does today

The research agent attaches papers **directly** to each `CandidateMechanism` (`papers[]`).
`research/verify.py` deduplicates those papers into one `Evidence` pool, resolves every
identifier (Crossref + NCBI), flags retractions, and derives a per-**mechanism** `status`:

- `supported`   — ≥1 linked paper resolves and is not retracted,
- `partial`     — has papers but none resolved yet (unverified),
- `unsupported` — no resolvable paper.

That is the whole verification story right now: **existence** of the cited paper is verified;
**entailment** (does the paper actually support the mechanism?) is *not*.

## Why claims were removed

An earlier design had the agent atomize findings into discrete `claims`, each citing papers,
with a per-claim `status`. That granularity was:

- **never consumed downstream** — `gpi/research_evidence_adapter.py` maps each mechanism
  (name + summary + genes + evidence) into the annotation `modules[]`; the claim *statements*
  never reached the annotation prompt, the theme step, or the report; and
- **misleading** — a claim's `status: supported` meant only "its citations resolve," not "the
  paper supports the sentence." Entailment was never checked, so the label implied a rigor the
  pipeline did not actually provide.

So claims were dropped from the active path (see the git history and the schema change that
introduced `papers`-on-mechanisms). Papers + mechanism summaries are what the report needs.

## What a real verification step would add (the reserved scaffold)

Claim-level structure only becomes worth its cost when something actually checks
**claim ⇄ paper entailment**. The reserved code is the scaffold for that step:

- **Reserved models** — `research/schema.py`, RESERVED section: `Claim`, `AgentClaim`,
  `Citation`, plus the `DirectionMatch`/`ClaimStatus` aliases.
- **Reserved logic** — `research/verify.py`: `_reserved_apply_claims_and_meta` (the retired
  per-claim, resolution-only status pass — kept verbatim, never called).

A future implementation would, per claim:

1. fetch the cited paper's abstract / full text (the in-process `literature` tools already
   expose `fetch_pubmed`; PMC full text could be added);
2. run an **adjudicator** (LLM or NLI model) that reads the paper and judges whether it
   *entails*, is *neutral to*, or *contradicts* the claim;
3. set `status`/`direction_match` from that judgment — **not** from mere identifier resolution;
4. optionally surface adjudicated claims under their mechanism in the report.

(GeneScope's `critic.py` / `adjudication.py` are a reference design for such an adjudicator.)

Until that exists, keep claim structure **out** of the active pipeline: it adds apparent rigor
without real rigor. When adding it, wire it as an *additional* stage after `verify` — do not
resurrect the resolution-only claim status.
