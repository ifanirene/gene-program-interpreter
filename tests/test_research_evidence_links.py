"""Retrieval-first ledger and mechanism-local evidence-link contract."""

import pytest

from research.research_parallel import (
    canonicalize_supplied_gene_ledger,
    validate_retrieval_contract,
)
from research.schema import (
    AgentMechanism,
    AgentPaper,
    AgentResearchResult,
    GeneResearchLedgerEntry,
    RegulatorResearchLedgerEntry,
)
from research.verify import normalize_agent_result


def _valid_agent():
    return AgentResearchResult(
        program_id="P1",
        supplied_gene_ledger=[
            GeneResearchLedgerEntry(gene="Kdr", state="evidence_found", search_attempted=True),
            GeneResearchLedgerEntry(
                gene="Cldn5", state="searched_no_evidence", search_attempted=True
            ),
        ],
        regulator_ledger=[
            RegulatorResearchLedgerEntry(gene="Vegfa", search_attempted=True)
        ],
        candidate_mechanisms=[
            AgentMechanism(
                name="Barrier signaling",
                supporting_genes=["Kdr"],
                papers=[
                    AgentPaper(
                        pmid="1", text_type="abstract", role="anchor",
                        selection_reason="Kdr is experimentally studied",
                        studied_genes=["Kdr"], function_supported_genes=["Kdr"],
                        finding="Kdr supports barrier signaling", direction="positive",
                        context="brain endothelium", limitation="abstract only",
                        evidence_span="Kdr regulated endothelial barrier function",
                    )
                ],
            )
        ],
    )


def _bundle():
    return {
        "program_genes": ["Kdr"],
        "distinctive_genes": ["Cldn5"],
        "perturbation_regulators": {"all": [{"gene": "Vegfa"}]},
    }


def _trace():
    return [
        {"action": "search", "research_phase": "supplied_gene", "target_genes": ["Kdr"]},
        {"action": "search", "research_phase": "gap", "target_genes": ["Cldn5"]},
        {"action": "search", "research_phase": "regulator", "target_genes": ["Vegfa"]},
    ]


def test_complete_ledgers_and_exact_function_edges_normalize():
    agent = _valid_agent()
    validate_retrieval_contract(agent, _bundle(), _trace())
    result = normalize_agent_result(agent)
    mechanism = result.candidate_mechanisms[0]
    assert mechanism.evidence_ids == ["EV-001"]
    link = mechanism.evidence_links[0]
    assert link.role == "anchor" and link.selection_reason
    assert link.studied_genes == ["Kdr"]
    assert link.function_supported_genes == ["Kdr"]
    assert result.supplied_gene_ledger[1].state == "searched_no_evidence"


@pytest.mark.parametrize("mutation", ["missing_ledger", "untraced", "title_only", "paralog"])
def test_incomplete_or_inexact_contract_is_rejected(mutation):
    agent = _valid_agent()
    trace = _trace()
    if mutation == "missing_ledger":
        agent.supplied_gene_ledger.pop()
    elif mutation == "untraced":
        trace = trace[:1]
    elif mutation == "title_only":
        agent.candidate_mechanisms[0].papers[0].text_type = "unavailable"
    else:
        paper = agent.candidate_mechanisms[0].papers[0]
        paper.studied_genes = ["Kdrb"]
        paper.function_supported_genes = ["Kdrb"]
    with pytest.raises(ValueError):
        validate_retrieval_contract(agent, _bundle(), trace)


def test_function_supported_genes_must_be_studied():
    with pytest.raises(ValueError):
        AgentPaper(
            pmid="1", text_type="abstract", selection_reason="x",
            studied_genes=["Kdrb"], function_supported_genes=["Kdr"],
        )


def test_optimistic_ledger_state_is_conservatively_canonicalized():
    agent = _valid_agent()
    mechanism = agent.candidate_mechanisms[0]
    mechanism.supporting_genes = []
    mechanism.papers[0].function_supported_genes = []

    corrections = canonicalize_supplied_gene_ledger(agent)

    assert corrections == [
        {
            "gene": "Kdr",
            "agent_state": "evidence_found",
            "canonical_state": "searched_no_evidence",
            "reason": "no exact mechanism-local function-supported paper edge",
        }
    ]
    assert agent.supplied_gene_ledger[0].state == "searched_no_evidence"
    validate_retrieval_contract(agent, _bundle(), _trace())
