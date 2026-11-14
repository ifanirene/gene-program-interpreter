"""Regulator-only citations need verification without supporting gene candidates."""
import pytest
from research.schema import AgentResearchResult
from research.verify import normalize_agent_result


def make_result(identifiers, papers=None):
    return AgentResearchResult.model_validate(dict(program_id='P1',
        candidate_mechanisms=[dict(name='Gene function',supporting_genes=['A','B'],papers=papers or [])],
        regulator_coverage=[dict(gene='Target',status='retrieved_support',
            queries=['actual query'],identifiers=identifiers)]))


def test_regulator_references_enter_pool_but_do_not_change_candidate_links():
    result=normalize_agent_result(make_result(
        ['111','PMID: 222','https://doi.org/10.1234/Regulator'],
        [dict(pmid='111',doi='10.1234/gene',title='Gene paper')] ))
    assert len(result.evidence)==3
    assert result.candidate_mechanisms[0].evidence_ids==['EV-001']
    assert result.evidence[0].title=='Gene paper'
    assert result.evidence[1].pmid=='222'
    assert result.evidence[2].doi=='10.1234/regulator'
    assert all(e.resolved is None for e in result.evidence)


def test_regulator_only_evidence_does_not_support_a_gene_candidate():
    result=normalize_agent_result(make_result(['222']))
    assert len(result.evidence)==1
    assert result.candidate_mechanisms[0].evidence_ids==[]
    assert result.candidate_mechanisms[0].status=='unsupported'


def test_existing_pmid_doi_and_pubmed_url_references_are_deduplicated():
    result=normalize_agent_result(make_result(
        ['111','https://pubmed.ncbi.nlm.nih.gov/111/','doi:10.1234/gene'],
        [dict(pmid='111',doi='10.1234/gene')] ))
    assert len(result.evidence)==1


def test_unparseable_regulator_reference_does_not_escape_verification():
    with pytest.raises(ValueError,match='Unrecognized regulator reference'):
        normalize_agent_result(make_result(['not an identifier']))
