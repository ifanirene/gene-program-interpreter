"""Offline regression tests for the research data-flow seam: bundle + adapter."""

import json
from pathlib import Path

import pytest

from gpi.context_profile import ContextProfile
from gpi.research_evidence_adapter import load_research_evidence_directory
from research.bundle import build_all_bundles
from research.schema import (
    AgentClaim,
    AgentMechanism,
    AgentResearchResult,
    CandidateMechanism,
    Citation,
    Claim,
    Evidence,
    ResearchResult,
)
from research.verify import normalize_agent_result


def test_flatten_normalizes_dedups_and_drops_idless():
    """The flat agent output normalizes to a deduplicated Evidence pool with assigned ids."""
    agent = AgentResearchResult(
        program_id="P10",
        candidate_mechanisms=[
            AgentMechanism(
                name="Zonation",
                supporting_genes=["Glul"],
                citations=[Citation(pmid="29059455", doi="10.1002/hep.29635")],
            )
        ],
        claims=[
            AgentClaim(statement="a", citations=[Citation(pmid="29059455")]),  # same paper -> dedup
            AgentClaim(statement="b", citations=[Citation(doi="10.1016/j.cell.2025.05.022")]),
            AgentClaim(statement="c", citations=[]),  # no evidence
            AgentClaim(statement="d", citations=[Citation(title="no identifier")]),  # dropped
        ],
    )
    rr = normalize_agent_result(agent)
    assert len(rr.evidence) == 2  # the pmid-only citation deduped into the mechanism's paper
    assert rr.candidate_mechanisms[0].evidence_ids == ["EV-001"]
    assert rr.claims[0].evidence_ids == ["EV-001"]  # matched by PMID
    assert rr.claims[1].evidence_ids == ["EV-002"]
    assert rr.claims[2].evidence_ids == []  # no citations
    assert rr.claims[3].evidence_ids == []  # identifier-less citation dropped


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
    # lean shape: gene names only, no score/uniqueness, no enrichment/overall block
    assert bundle["program_genes"] and all(isinstance(g, str) for g in bundle["program_genes"])
    assert bundle["distinctive_genes"]
    assert "top_weighted_genes" not in bundle and "enrichment" not in bundle
    # regulators = top-6 per condition, {gene, log2fc} only, as search targets
    regs = bundle.get("perturbation_regulators", {})
    assert regs and any(regs.values()), "no regulators mapped"
    for entry in next(iter(v for v in regs.values() if v)):
        assert set(entry) == {"gene", "log2fc"}
    assert isinstance(bundle["research_brief"], str) and bundle["research_brief"]
    # context is tissue-driven, not hard-coded; no assay leakage anywhere in the bundle
    assert bundle["cell_type"] == "hepatocyte"
    assert "perturb" not in json.dumps(bundle).lower().replace("perturbation_regulators", "")


def test_inprocess_literature_server_tool_surface():
    """The default in-process literature server exposes exactly the read-only tool surface
    the protocol names — no external server / plugin required to build it."""
    pytest.importorskip("claude_agent_sdk")
    from research.literature import (
        LITERATURE_TOOL_NAMES,
        build_literature_mcp_server,
        normalize_doi,
        normalize_pmid,
    )

    assert set(LITERATURE_TOOL_NAMES) == {
        "search_pubmed", "fetch_pubmed", "search_openalex", "resolve_doi"
    }
    server = build_literature_mcp_server()  # builds without any network call or external dep
    assert server is not None
    # normalizers underpin "reference only tool-returned identifiers"
    assert normalize_doi("https://doi.org/10.1002/HEP.29635") == "10.1002/hep.29635"
    assert normalize_pmid("29059455") == "29059455" and normalize_pmid("nope") is None


def test_research_wiring_inprocess_default_is_sandboxed(
    gene_loading_csv, literature_context_json, tmp_path
):
    """Default (in-process) wiring: literature tools come from the in-process ``literature``
    server, the session is sandboxed (setting_sources=[] + strict_mcp_config), and the
    side-effect denylist is applied. Locks the headless fix (no external server needed).
    """
    pytest.importorskip("claude_agent_sdk")
    from research.research_parallel import DISALLOWED_TOOLS, dry_run, resolve_allowed_tools

    # Allowlist by mode.
    assert resolve_allowed_tools({}, "inprocess")[:1] == ["mcp__literature__*"]
    assert {"mcp__pubmed__*", "mcp__openalex__*"}.issubset(
        set(resolve_allowed_tools({"pubmed": {}, "openalex": {}}, "external"))
    )
    assert "mcp__plugin_bio-research_pubmed__*" in resolve_allowed_tools({}, "plugin")

    paths = build_all_bundles(
        str(gene_loading_csv),
        ContextProfile.liver_demo(),
        ncbi_context_json=str(literature_context_json),
        out_dir=str(tmp_path / "program_bundles"),
        program_ids=[10],
    )
    summary = dry_run(paths, out_dir=str(tmp_path / "out"))  # lit_mode defaults to inprocess

    assert summary["lit_mode"] == "inprocess"
    assert summary["mcp_servers_loaded"] == []  # nothing external loaded
    assert summary["allowed_tools"][0] == "mcp__literature__*"
    pp = summary["per_program"][0]
    assert set(pp["mcp_server_names"]) == {"literature", "gpi"}
    assert pp["setting_sources"] == []  # sandboxed
    assert pp["strict_mcp_config"] is True  # ignore any settings-file MCP servers
    assert "Bash" in pp["disallowed_tools"] and "WebSearch" in pp["disallowed_tools"]
    assert set(DISALLOWED_TOOLS).issubset(set(pp["disallowed_tools"]))
    assert pp["options_ok"] is True

    # External mode still wires correctly from the repo config.
    repo_mcp = Path(__file__).resolve().parent.parent / "research" / "mcp_servers.json"
    ext = dry_run(paths, out_dir=str(tmp_path / "ext"), lit_mode="external", mcp_config_path=str(repo_mcp))
    assert ext["lit_mode"] == "external"
    assert {"pubmed", "biorxiv", "openalex"}.issubset(set(ext["mcp_servers_loaded"]))
    assert "mcp__pubmed__*" in ext["per_program"][0]["allowed_tools"]


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
