"""Offline regression tests for the research data-flow seam: bundle + adapter."""

import json
from pathlib import Path

import pytest

from gpi.context_profile import ContextProfile
from gpi.research_evidence_adapter import load_research_evidence_directory
from research.bundle import build_all_bundles
from research.schema import (
    AgentMechanism,
    AgentPaper,
    AgentResearchResult,
    CandidateMechanism,
    Evidence,
    ResearchResult,
)
from research.verify import _apply_mechanism_status_and_meta, normalize_agent_result


def test_flatten_normalizes_dedups_and_drops_idless():
    """The flat agent output (papers attached per mechanism) normalizes to a deduplicated
    Evidence pool with assigned ids, drops id-less papers, carries context_match, sets a
    provisional per-mechanism status, and hard-caps to 3 mechanisms."""
    agent = AgentResearchResult(
        program_id="P10",
        candidate_mechanisms=[
            AgentMechanism(
                name="Zonation",
                supporting_genes=["Glul"],
                papers=[
                    AgentPaper(pmid="29059455", doi="10.1002/hep.29635",
                               context_match="direct", note="n1"),
                    AgentPaper(pmid="29059455"),                       # same paper -> dedup
                    AgentPaper(title="no identifier"),                  # dropped (no id)
                ],
            ),
            AgentMechanism(
                name="Lipogenesis",
                supporting_genes=["Fasn"],
                papers=[AgentPaper(doi="10.1016/j.cell.2025.05.022")],
            ),
            AgentMechanism(name="Empty", supporting_genes=["Xyz1"], papers=[]),  # no papers
            AgentMechanism(name="FourthDropped", papers=[AgentPaper(pmid="99999")]),  # > 3 -> cut
        ],
    )
    rr = normalize_agent_result(agent)

    assert len(rr.candidate_mechanisms) == 3  # hard 3-cap; the 4th is dropped
    assert not hasattr(rr, "claims") or "claims" not in rr.model_dump()  # no claims layer
    # the 4th mechanism's paper never enters the pool (truncated before pooling)
    assert len(rr.evidence) == 2
    assert not any(e.pmid == "99999" for e in rr.evidence)
    # dedup: mechanism 0's two identical-PMID papers collapse to one Evidence
    assert rr.candidate_mechanisms[0].evidence_ids == ["EV-001"]
    assert rr.candidate_mechanisms[1].evidence_ids == ["EV-002"]
    assert rr.candidate_mechanisms[2].evidence_ids == []  # no papers
    # context_match + note carried onto the Evidence (first-seen wins)
    ev0 = rr.evidence_by_id()["EV-001"]
    assert ev0.context_match == "direct" and ev0.relevance_note == "n1"
    # provisional status: has evidence + unresolved -> 'partial'; no evidence -> 'unsupported'
    assert [m.status for m in rr.candidate_mechanisms] == ["partial", "partial", "unsupported"]


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


def test_bundle_gene_sets_are_disjoint_and_match_the_report(gene_loading_csv, tmp_path):
    """`distinctive_genes` must be a SEPARATE ranked set, not a re-sort of `program_genes`.

    Regression guard for a real bug: the bundle used to rank uniqueness *within* the
    top-loading genes, so every "distinctive" gene was already a program gene — a second
    list that added nothing. The two sets must be disjoint, correctly sized, and identical
    to what the HTML report shows the reader, since both now come from one selector
    (`gpi.enrichment.select_program_gene_sets`).
    """
    import pandas as pd
    from gpi.html_report import build_panel_stats

    programs = [1, 2, 10]
    paths = build_all_bundles(
        str(gene_loading_csv),
        ContextProfile.liver_demo(),
        out_dir=str(tmp_path / "program_bundles"),
        program_ids=programs,
        top_loading=15,
        top_unique=8,
    )
    assert len(paths) == len(programs)

    panel = build_panel_stats(
        pd.DataFrame({"Topic": programs}), str(gene_loading_csv), top_loading=15, top_unique=8
    )

    for path, pid in zip(paths, programs):
        bundle = json.loads(path.read_text())
        loading, distinctive = bundle["program_genes"], bundle["distinctive_genes"]

        # the actual bug: a uniqueness ranking confined to the loading pool
        assert not set(loading) & set(distinctive), (
            f"P{pid}: distinctive_genes overlaps program_genes {sorted(set(loading) & set(distinctive))}"
        )
        assert len(loading) == 15 and len(distinctive) == 8
        assert len(set(loading)) == 15 and len(set(distinctive)) == 8  # no dupes within a set

        # the agent must research exactly what the reader sees
        assert loading == panel[pid]["top_loading"].split(", ")
        assert distinctive == panel[pid]["unique"].split(", ")


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

    # The allowlist is the single in-process literature family + Read + submit.
    assert resolve_allowed_tools()[0] == "mcp__literature__*"

    paths = build_all_bundles(
        str(gene_loading_csv),
        ContextProfile.liver_demo(),
        ncbi_context_json=str(literature_context_json),
        out_dir=str(tmp_path / "program_bundles"),
        program_ids=[10],
    )
    summary = dry_run(paths, out_dir=str(tmp_path / "out"))

    assert summary["allowed_tools"][0] == "mcp__literature__*"
    pp = summary["per_program"][0]
    assert set(pp["mcp_server_names"]) == {"literature", "gpi"}  # in-process only
    assert pp["setting_sources"] == []  # sandboxed
    assert pp["strict_mcp_config"] is True  # ignore any settings-file MCP servers
    assert "Bash" in pp["disallowed_tools"] and "WebSearch" in pp["disallowed_tools"]
    assert set(DISALLOWED_TOOLS).issubset(set(pp["disallowed_tools"]))
    assert pp["options_ok"] is True


def test_adapter_maps_research_result_to_modules(tmp_path):
    """A verified ResearchResult maps to the annotation `modules[]` shape, reading each
    mechanism's own status (no claims layer) and its resolvable evidence ids."""
    rr = ResearchResult(
        program_id="P10",
        candidate_mechanisms=[
            CandidateMechanism(
                name="Oxidative phosphorylation capacity",
                summary="ETC complexes support hepatocyte ATP synthesis.",
                supporting_genes=["Ndufa6", "Sdhb"],
                evidence_ids=["EV-001"],
                status="supported",
            ),
            CandidateMechanism(
                name="Sparse mechanism",
                supporting_genes=["Xyz1"],
                evidence_ids=["EV-002"],
                status="unsupported",
            ),
        ],
        evidence=[
            # a real, verifier-resolved paper
            Evidence(evidence_id="EV-001", pmid="27346353", doi="10.1016/j.celrep.2016.06.006",
                     title="x", resolved=True),
            # a proven-unresolvable (fabricated) paper: carries a PMID but resolved is False
            Evidence(evidence_id="EV-002", pmid="99999999", title="fabricated", resolved=False),
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
    assert m0["status"] == "supported"  # re-derived from the resolved evidence
    # evidence_ids are PMID-only for the resolvable mechanism (DOI is a fallback used only
    # when a paper has no PMID); a paper carrying a PMID never emits its DOI.
    joined = " ".join(m0["evidence_ids"])
    assert "PMID:27346353" in joined and "10.1016/j.celrep.2016.06.006" not in joined
    # the sparse mechanism's only paper is proven-unresolvable -> unsupported, AND its fabricated
    # PMID must NOT surface as a citation (no fabricated links in the report/prompt).
    assert modules[1]["status"] == "unsupported"
    assert modules[1]["evidence_ids"] == []
    # module dict keys stay EXACTLY the annotation-facing contract (+ source_file) — this is
    # the seam annotation/theme/report depend on; a change here would ripple downstream.
    assert set(m0) == {"module_rank", "module_name", "supporting_genes",
                       "evidence_ids", "literature_summary", "status", "source_file"}
    # limited-genes: Xyz1 lives only in the unsupported mechanism
    assert ctx[10]["genes_with_limited_literature"] == ["Xyz1"]


def test_normalize_dedup_is_order_independent_for_split_ids():
    """A paper whose pmid and doi first appear on two SEPARATE mechanisms is unified into ONE
    Evidence when a later mechanism cites both — regardless of citation order (deterministic)."""

    def build(order):
        papers = {
            "pmid": AgentPaper(pmid="900"),
            "doi": AgentPaper(doi="10.9/z"),
            "both": AgentPaper(pmid="900", doi="10.9/z"),
        }
        mechs = [AgentMechanism(name=k, supporting_genes=[k], papers=[papers[k]]) for k in order]
        return normalize_agent_result(
            AgentResearchResult(program_id="P1", candidate_mechanisms=mechs)
        )

    a = build(["pmid", "doi", "both"])
    b = build(["both", "pmid", "doi"])
    assert len(a.evidence) == 1 and len(b.evidence) == 1  # one paper -> one record, either order
    for rr in (a, b):
        canon = rr.evidence[0].evidence_id
        assert all(m.evidence_ids == [canon] for m in rr.candidate_mechanisms)
        e = rr.evidence[0]
        assert e.pmid == "900" and e.doi == "10.9/z"  # both ids merged onto the survivor


def test_post_resolution_recompute_and_verify_meta():
    """_apply_mechanism_status_and_meta reads each Evidence's resolved/retracted flags:
    resolved+non-retracted -> supported; resolved+retracted -> unsupported; unresolved-only ->
    partial; no evidence -> unsupported. It writes the renamed verify-meta keys."""
    rr = ResearchResult(
        program_id="P9",
        candidate_mechanisms=[
            CandidateMechanism(name="ok", evidence_ids=["EV-001"]),
            CandidateMechanism(name="retracted", evidence_ids=["EV-002"]),
            CandidateMechanism(name="unverified", evidence_ids=["EV-003"]),
            CandidateMechanism(name="none", evidence_ids=[]),
        ],
        evidence=[
            Evidence(evidence_id="EV-001", pmid="1", resolved=True, retracted=False),
            Evidence(evidence_id="EV-002", pmid="2", resolved=True, retracted=True),
            Evidence(evidence_id="EV-003", pmid="3", resolved=None),
        ],
    )
    _apply_mechanism_status_and_meta(rr)
    assert [m.status for m in rr.candidate_mechanisms] == [
        "supported", "unsupported", "partial", "unsupported",
    ]
    vm = rr.meta["verify"]
    assert vm["n_mechanisms"] == 4 and vm["n_mechanisms_unsupported"] == 2
    assert vm["mechanism_status"] == ["supported", "unsupported", "partial", "unsupported"]
    assert vm["n_evidence"] == 3 and vm["n_resolved"] == 2 and vm["n_retracted"] == 1


def test_adapter_limited_genes_excludes_gene_shared_with_supported(tmp_path):
    """A gene in BOTH a supported and an unsupported mechanism is NOT flagged as limited."""
    rr = ResearchResult(
        program_id="P11",
        candidate_mechanisms=[
            CandidateMechanism(name="Supported", supporting_genes=["Shared", "Sole1"],
                               evidence_ids=["EV-001"]),
            CandidateMechanism(name="Unsupported", supporting_genes=["Shared", "OnlyUnsupported"],
                               evidence_ids=[]),
        ],
        evidence=[Evidence(evidence_id="EV-001", pmid="1", resolved=True)],
    )
    rdir = tmp_path / "rr"
    rdir.mkdir()
    (rdir / "P11.json").write_text(rr.model_dump_json())
    ctx = load_research_evidence_directory(rdir, selected_program_ids=[11])
    limited = ctx[11]["genes_with_limited_literature"]
    assert "OnlyUnsupported" in limited  # only in the unsupported mechanism
    assert "Shared" not in limited       # also in a supported mechanism -> excluded
    assert "Sole1" not in limited        # only in the supported mechanism
