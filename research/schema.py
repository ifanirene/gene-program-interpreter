"""
Canonical research schema (spec §5) — Pydantic v2.

This is the single evidence contract. It is:
  * produced by each per-program research subagent (via a required `submit_result` tool
    whose JSON Schema is `ResearchResult.model_json_schema()`),
  * annotated **in place** by the deterministic verifier (`research/verify.py`) — the
    verifier fills `Evidence.resolved/registry/retracted/verify_error` and may downgrade
    `Claim.status`; there is no second schema, and
  * consumed by `gpi/research_evidence_adapter.py`, which maps each supported
    `CandidateMechanism` + its `claims`/`evidence` into the annotation prompt's `modules[]`.

Reference-only IDs: agents must reference only tool-returned PMIDs/DOIs. The verifier is
what turns an unresolvable identifier into `status="unsupported"` — not hidden in prose.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

ContextMatch = Literal["direct", "partial", "indirect"]
DirectionMatch = Literal["consistent", "conflicting", "unknown"]
ClaimStatus = Literal["supported", "partial", "unsupported"]


class Evidence(BaseModel):
    """One paper. `evidence_id` (e.g. "EV-001") is referenced by claims/mechanisms.

    The agent supplies the bibliographic fields from tool output only. The verifier
    fills the `*_verify` block; agents must leave those unset.
    """

    evidence_id: str = Field(..., description="Local id, e.g. 'EV-001'.")
    pmid: Optional[str] = None
    doi: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    study_type: Optional[str] = Field(
        default=None, description="e.g. 'human cohort', 'mouse in vivo', 'review'."
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


class Claim(BaseModel):
    """A biological claim, anchored to genes/regulators and evidence ids."""

    statement: str
    supporting_genes: List[str] = Field(default_factory=list)
    supporting_regulators: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    context_match: ContextMatch = "indirect"
    direction_match: DirectionMatch = "unknown"
    status: ClaimStatus = "partial"


class CandidateMechanism(BaseModel):
    """A proposed mechanism (1–3 per program). Maps to one annotation `module`.

    The downstream synthesis assigns the final program label; the agent does NOT.
    """

    name: str
    summary: str = ""
    supporting_genes: List[str] = Field(default_factory=list)
    supporting_regulators: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)


class ResearchResult(BaseModel):
    """The per-program artifact written to `research_results/{program_id}.json`."""

    program_id: str
    queries: List[str] = Field(default_factory=list)
    candidate_mechanisms: List[CandidateMechanism] = Field(default_factory=list)
    claims: List[Claim] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    contradictions: List[str] = Field(default_factory=list)
    evidence_gaps: List[str] = Field(default_factory=list)
    agent_summary: str = ""

    # ---- audit / provenance (runner + verifier fill these; not agent-authored) ----
    meta: dict = Field(
        default_factory=dict,
        description="Runner/verifier provenance: model, cost, turns, tool trace path, "
        "verify summary, failure/fallback status. Kept separate from summaries.",
    )

    # convenience
    def evidence_by_id(self) -> dict[str, Evidence]:
        return {e.evidence_id: e for e in self.evidence}


def submit_result_tool_schema() -> dict:
    """JSON Schema for the agent's required `submit_result` tool input.

    The runner registers a tool that accepts exactly a `ResearchResult` (minus the
    verifier/audit fields the agent must not author).
    """
    schema = ResearchResult.model_json_schema()
    return schema


__all__ = [
    "Evidence",
    "Claim",
    "CandidateMechanism",
    "ResearchResult",
    "ContextMatch",
    "DirectionMatch",
    "ClaimStatus",
    "submit_result_tool_schema",
]
