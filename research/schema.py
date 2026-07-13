"""
Canonical research schema — Pydantic v2.

Two shapes, one file:

1. AGENT-FACING (flat) — what each per-program research subagent submits via the required
   ``submit_result`` tool (whose JSON Schema is ``submit_result_tool_schema()`` ==
   ``AgentResearchResult.model_json_schema()``). The agent attaches papers DIRECTLY to each
   candidate mechanism (``papers``); it does NOT assign evidence ids, keep a separate
   ``evidence[]`` pool, or set any status. Each paper should carry >=1 of pmid/doi (from tool
   output only); identifier-less papers are dropped during normalization (they can't be verified).

2. CANONICAL (normalized + verified) — written to ``research_results/{program_id}.json``.
   ``research/verify.py`` deterministically:
     * normalizes the flat agent output into this shape — TRUNCATING to the first 3 mechanisms,
       then building the deduplicated ``Evidence`` pool (by pmid|doi) from the kept mechanisms'
       papers, assigning ``EV-NNN`` ids, attaching ``evidence_ids`` per mechanism, carrying
       ``context_match``/``note`` onto the Evidence, and setting a provisional ``status``;
     * annotates each ``Evidence`` in place (``resolved``/``registry``/``retracted``/
       ``verify_error``) by resolving its identifiers; and
     * recomputes each ``CandidateMechanism.status`` from its evidence resolvability.

   It is then consumed by ``gpi/research_evidence_adapter.py``, which maps each
   ``CandidateMechanism`` (+ its linked ``Evidence``) into the annotation prompt's ``modules[]``,
   reading ``mechanism.status`` directly.

Reference-only IDs: agents reference only tool-returned PMIDs/DOIs. The verifier is what turns an
unresolvable identifier into an ``unsupported`` mechanism — not hidden in prose.

A RESERVED section at the bottom keeps the claim/entailment models (``Claim``, ``AgentClaim``,
``Citation``) for a FUTURE claim-vs-paper verification step (see
``docs/FUTURE_claim_verification.md``). They are NOT wired into the active pipeline.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# Active type aliases (used by the live pipeline).
ContextMatch = Literal["direct", "partial", "indirect"]
MechanismStatus = Literal["supported", "partial", "unsupported"]


# =====================================================================================
# CANONICAL (normalized + verified) — written to research_results/{program_id}.json
# =====================================================================================


class Evidence(BaseModel):
    """One paper in the deduplicated evidence pool. ``evidence_id`` (e.g. "EV-001") is
    referenced by each mechanism's ``evidence_ids``.

    Bibliographic + ``context_match``/``relevance_note`` fields are carried from the agent's
    ``AgentPaper`` during normalization. The verifier fills the ``resolved``/``registry``/
    ``retracted``/``verify_error`` block in place; agents must leave those unset.
    """

    evidence_id: str = Field(..., description="Local id, e.g. 'EV-001'.")
    pmid: Optional[str] = None
    doi: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    study_type: Optional[str] = Field(
        default=None, description="e.g. 'human cohort', 'mouse in vivo', 'review'."
    )
    context_match: Optional[ContextMatch] = Field(
        default=None,
        description="How directly the paper fits the program's cell-type context "
        "(carried from the agent's paper).",
    )
    relevance_note: Optional[str] = None

    # ---- verifier-added (annotated in place; agents leave unset) ----
    resolved: Optional[bool] = Field(
        default=None, description="True if PMID/DOI resolves; False if not; None if unverified."
    )
    registry: Optional[str] = Field(
        default=None, description="'crossref' | 'non-crossref' | 'pubmed' | None."
    )
    retracted: Optional[bool] = None
    verify_error: Optional[str] = None


class CandidateMechanism(BaseModel):
    """A proposed mechanism (1-3 per program). Maps to exactly one annotation ``module``.

    Papers are attached directly (no intermediate claim layer): the agent's ``AgentPaper[]`` are
    deduplicated into the program ``Evidence`` pool and referenced here by ``evidence_ids``.
    ``status`` is per-mechanism, derived deterministically by ``research/verify.py`` from the
    resolvability of the linked evidence:

      * 'supported'   -> >=1 resolvable (non-retracted) evidence,
      * 'partial'     -> has evidence_ids but none resolved yet / unverified,
      * 'unsupported' -> no resolvable evidence.

    The downstream synthesis assigns the final program label; the agent does NOT.
    """

    name: str
    summary: str = ""
    supporting_genes: List[str] = Field(default_factory=list)
    supporting_regulators: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    status: MechanismStatus = Field(
        default="partial",
        description="Per-mechanism support, derived from evidence resolvability by the verifier. "
        "'partial' until verification runs.",
    )


class ResearchResult(BaseModel):
    """The per-program artifact written to ``research_results/{program_id}.json``.

    ``candidate_mechanisms`` is 1-3 (hard-truncated to the first 3 during normalization).
    ``evidence`` is the deduplicated pool referenced by each mechanism's ``evidence_ids``.
    """

    program_id: str
    queries: List[str] = Field(default_factory=list)
    candidate_mechanisms: List[CandidateMechanism] = Field(
        default_factory=list,
        description="1-3 mechanisms; hard-truncated to the first 3 during normalization.",
    )
    evidence: List[Evidence] = Field(
        default_factory=list, description="Deduplicated evidence pool (by pmid|doi)."
    )
    contradictions: List[str] = Field(default_factory=list)
    evidence_gaps: List[str] = Field(default_factory=list)
    agent_summary: str = ""

    # ---- audit / provenance (runner + verifier fill these; not agent-authored) ----
    meta: dict = Field(
        default_factory=dict,
        description="Runner/verifier provenance: model, cost, turns, tool trace path, verify "
        "summary, failure/fallback status. Kept separate from summaries.",
    )

    # convenience
    def evidence_by_id(self) -> dict[str, Evidence]:
        return {e.evidence_id: e for e in self.evidence}


# =====================================================================================
# AGENT-FACING (flat) — what the research agent actually submits.
#
# The agent attaches papers INLINE to each mechanism (`papers`); it does NOT assign evidence
# ids, keep a separate `evidence[]` pool, or set status. `research/verify.py` deterministically
# normalizes this into the canonical `ResearchResult` above — truncating to 3 mechanisms,
# building the deduplicated Evidence pool, assigning ids, resolving identifiers, and deriving
# each mechanism's status. This keeps id/dedup/verification bookkeeping out of the LLM (where it
# is error-prone) and in deterministic code (where it is reliable).
# =====================================================================================


class AgentPaper(BaseModel):
    """A paper attached inline to a mechanism. A `pmid` should be set (identifiers are PMID-only
    for consistent PubMed hyperlinks; `doi` is optional and no longer required). Papers without
    any identifier are dropped during normalization — they cannot be verified."""

    pmid: Optional[str] = None
    doi: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    study_type: Optional[str] = None
    context_match: ContextMatch = Field(
        default="indirect", description="How directly this paper fits the cell-type context."
    )
    note: Optional[str] = Field(default=None, description="Why this paper supports the mechanism.")


class AgentMechanism(BaseModel):
    """A proposed functional theme (1-3 per program). Maps to one annotation module.

    Attach the specific papers that establish this theme in ``papers`` (each with a real
    tool-returned pmid and/or doi)."""

    name: str
    summary: str = ""
    supporting_genes: List[str] = Field(default_factory=list)
    supporting_regulators: List[str] = Field(default_factory=list)
    papers: List[AgentPaper] = Field(default_factory=list)


class AgentResearchResult(BaseModel):
    """What the research agent submits (flat). ``verify.py`` normalizes it to ``ResearchResult``.

    Return the strongest **1-3** mechanisms (a hard maximum of 3 is enforced during
    normalization); do not assign the final program label."""

    program_id: str
    queries: List[str] = Field(default_factory=list)
    candidate_mechanisms: List[AgentMechanism] = Field(
        default_factory=list, description="1-3 mechanisms (hard max 3, enforced in normalization)."
    )
    contradictions: List[str] = Field(default_factory=list)
    evidence_gaps: List[str] = Field(default_factory=list)
    agent_summary: str = ""


def submit_result_tool_schema() -> dict:
    """JSON Schema for the agent's required ``submit_result`` tool input — the FLAT
    ``AgentResearchResult`` (mechanisms each carry their ``papers``). ``verify.py`` does the rest."""
    return AgentResearchResult.model_json_schema()


# =====================================================================================
# RESERVED — claim-vs-paper entailment verification (NOT WIRED INTO THE ACTIVE PIPELINE)
#
# These models supported an earlier claim-level design where the agent atomized findings into
# discrete `claims`, each citing papers, and a per-claim `status` was derived from *citation
# resolution only* (NOT from whether the paper actually entailed the claim). That granularity
# was never consumed downstream (the annotation prompt reads mechanism.summary + evidence), and
# the "supported" label was misleading because entailment was never checked.
#
# They are retained here — unused by the active pipeline — as the scaffold for a FUTURE step that
# actually verifies claim-vs-paper entailment (e.g. an adjudicator that reads the paper and
# checks the claim). See docs/FUTURE_claim_verification.md. Do NOT delete.
# =====================================================================================

DirectionMatch = Literal["consistent", "conflicting", "unknown"]
ClaimStatus = Literal["supported", "partial", "unsupported"]


class Citation(BaseModel):
    """RESERVED. A paper cited inline by a claim/mechanism (superseded by ``AgentPaper``)."""

    pmid: Optional[str] = None
    doi: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    study_type: Optional[str] = None
    note: Optional[str] = Field(default=None, description="Why this paper supports the point.")


class AgentClaim(BaseModel):
    """RESERVED. A single agent-authored biological claim citing papers inline."""

    statement: str
    supporting_genes: List[str] = Field(default_factory=list)
    citations: List[Citation] = Field(default_factory=list)
    context_match: ContextMatch = Field(
        default="indirect", description="How directly the evidence fits the cell-type context."
    )


class Claim(BaseModel):
    """RESERVED. A biological claim anchored to genes/regulators and evidence ids, with a status
    derived from resolution. Not populated by the active pipeline."""

    statement: str
    supporting_genes: List[str] = Field(default_factory=list)
    supporting_regulators: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    context_match: ContextMatch = "indirect"
    direction_match: DirectionMatch = "unknown"
    status: ClaimStatus = "partial"


__all__ = [
    # active — canonical
    "Evidence",
    "CandidateMechanism",
    "ResearchResult",
    # active — agent-facing
    "AgentPaper",
    "AgentMechanism",
    "AgentResearchResult",
    "submit_result_tool_schema",
    # active — aliases
    "ContextMatch",
    "MechanismStatus",
    # reserved (future claim-vs-paper entailment verification; not wired in)
    "Citation",
    "AgentClaim",
    "Claim",
    "DirectionMatch",
    "ClaimStatus",
]
