"""Identifier-pair and DOI-only evidence verification."""

from pathlib import Path

from gpi.research_evidence_adapter import _map_research_result
from research.schema import CandidateMechanism, Evidence, ResearchResult
from research.verify import _resolve_evidence


def test_conflicting_pmid_doi_pair_is_quarantined(monkeypatch):
    monkeypatch.setattr(
        "research.verify.verify_dois",
        lambda dois: {doi: {"ok": True, "registry": "crossref"} for doi in dois},
    )
    monkeypatch.setattr(
        "research.verify.resolve_pmids",
        lambda pmids: {
            "1": {"resolved": True, "doi": "10.1/right", "title": "right"}
        },
    )
    result = ResearchResult(
        program_id="P1",
        candidate_mechanisms=[CandidateMechanism(name="x", evidence_ids=["EV-001"])],
        evidence=[Evidence(evidence_id="EV-001", pmid="1", doi="10.1/wrong")],
    )
    _resolve_evidence(result)
    evidence = result.evidence[0]
    assert evidence.identifier_status == "conflict"
    assert evidence.resolved is False
    assert "conflict" in evidence.verify_error


def test_matching_pair_and_doi_only_evidence_surface(monkeypatch):
    monkeypatch.setattr(
        "research.verify.verify_dois",
        lambda dois: {
            doi: {"ok": True, "registry": "crossref", "title": "paper"} for doi in dois
        },
    )
    monkeypatch.setattr(
        "research.verify.resolve_pmids",
        lambda pmids: {"1": {"resolved": True, "doi": "10.1/same"}} if pmids else {},
    )
    matching = ResearchResult(
        program_id="P1", evidence=[Evidence(evidence_id="EV-001", pmid="1", doi="10.1/same")]
    )
    _resolve_evidence(matching)
    assert matching.evidence[0].identifier_status == "matching"

    doi_only = ResearchResult(
        program_id="P2",
        candidate_mechanisms=[CandidateMechanism(name="DOI mechanism", evidence_ids=["EV-001"])],
        evidence=[Evidence(evidence_id="EV-001", doi="10.1/only", resolved=True)],
    )
    _, context = _map_research_result(doi_only, source_file=Path("P2.json"))
    assert context["modules"][0]["evidence_ids"] == ["DOI:10.1/only"]

