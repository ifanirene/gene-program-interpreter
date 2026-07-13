"""Offline regression tests for the research data-flow seam: bundle + adapter."""

import json

from gpi.context_profile import ContextProfile
from gpi.research_evidence_adapter import load_research_evidence_directory
from research.bundle import build_all_bundles
from research.schema import CandidateMechanism, Claim, Evidence, ResearchResult


def test_bundle_from_fixtures(gene_loading_csv, literature_context_json, tmp_path):
    """A program bundle assembles top genes + regulators from the real fixtures."""
    paths = build_all_bundles(
        str(gene_loading_csv),
        ContextProfile.liver_demo(),
        ncbi_context_json=str(literature_context_json),
        out_dir=str(tmp_path / "program_bundles"),
        program_ids=[10],
    )
    assert paths, "no bundle written"
    bundle = json.loads(paths[0].read_text())
    assert bundle["top_weighted_genes"], "empty top genes"
    assert all("gene" in g and "score" in g for g in bundle["top_weighted_genes"])
    # program 10 has regulator_validation_by_condition in the fixture
    regs = bundle["regulators"]
    assert regs["activators"] or regs["repressors"], "no regulators mapped"
    assert isinstance(bundle["research_brief"], str) and bundle["research_brief"]
    # context is tissue-driven, not hard-coded
    assert bundle["context"]["cell_type"] == "hepatocyte"


def test_adapter_maps_research_result_to_modules(tmp_path):
    """A verified ResearchResult maps to the annotation `modules[]` shape."""
    rr = ResearchResult(
        program_id="P10",
        candidate_mechanisms=[
            CandidateMechanism(
                name="Oxidative phosphorylation capacity",
                summary="ETC complexes support hepatocyte ATP synthesis.",
                supporting_genes=["Ndufa6", "Sdhb"],
                evidence_ids=["EV-001"],
            ),
            CandidateMechanism(
                name="Sparse mechanism",
                supporting_genes=["Xyz1"],
                evidence_ids=["EV-002"],
            ),
        ],
        claims=[
            Claim(
                statement="ETC capacity is repressed by insulin.",
                supporting_genes=["Ndufa6"],
                evidence_ids=["EV-001"],
                context_match="direct",
                direction_match="consistent",
                status="supported",
            ),
        ],
        evidence=[
            Evidence(evidence_id="EV-001", pmid="27346353", doi="10.1016/j.celrep.2016.06.006", title="x"),
            Evidence(evidence_id="EV-002", title="no identifier"),  # unresolvable
        ],
    )
    rdir = tmp_path / "research_results"
    rdir.mkdir()
    (rdir / "P10.json").write_text(rr.model_dump_json())

    ctx = load_research_evidence_directory(rdir, selected_program_ids=[10])
    assert 10 in ctx
    modules = ctx[10]["modules"]
    assert len(modules) == 2
    m0 = modules[0]
    assert m0["module_name"] == "Oxidative phosphorylation capacity"
    assert m0["status"] == "supported"
    # evidence_ids carry PMID and/or DOI for the resolvable mechanism
    joined = " ".join(m0["evidence_ids"])
    assert "PMID:27346353" in joined and "10.1016/j.celrep.2016.06.006" in joined
    # the sparse mechanism (only an id-less evidence) has no resolvable ids
    assert modules[1]["evidence_ids"] == []
