import json
from pathlib import Path

import pytest

from research import benchmark_diagnostics


REPO_ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTICS_PATH = (
    REPO_ROOT
    / "benchmarks/literature-v1/candidate-evaluable/comparison-report/diagnostics.json"
)


def test_tool_phase_uses_explicit_phase_and_legacy_fallback():
    assert (
        benchmark_diagnostics._tool_phase(
            {
                "tool": "mcp__literature__search_pubmed",
                "args_summary": '{"research_phase":"gap"}',
            }
        )
        == "gap"
    )
    assert (
        benchmark_diagnostics._tool_phase(
            {
                "tool": "mcp__literature__search_pubmed",
                "args_summary": '{"query":"legacy"}',
            }
        )
        == "legacy_unphased"
    )
    assert (
        benchmark_diagnostics._tool_phase(
            {
                "tool": "mcp__literature__expand_openalex_citations",
                "args_summary": "{truncated",
            }
        )
        == "expansion"
    )


def test_search_query_key_normalizes_repeated_queries():
    assert benchmark_diagnostics._search_query_key(
        {
            "tool": "mcp__literature__search_pubmed",
            "args_summary": '{"query":"  Gene1   Endothelial  "}',
        }
    ) == "mcp__literature__search_pubmed:gene1 endothelial"


def test_repeat_rows_show_higher_mean_paper_and_function_gene_overlap():
    baseline = {
        "repeat_run_stability": [
            {
                "case_id": "brain-p10",
                "citation_jaccard": 0.1,
                "covered_gene_jaccard": 0.6,
                "function_supported_gene_jaccard": 0.5,
            }
        ]
    }
    candidate = {
        "repeat_run_stability": [
            {
                "case_id": "brain-p10",
                "citation_jaccard": 0.2,
                "covered_gene_jaccard": 0.7,
                "function_supported_gene_jaccard": 0.8,
            }
        ]
    }

    rows, summary = benchmark_diagnostics._repeat_rows(baseline, candidate)

    assert rows[0]["paperJaccardDelta"] == 0.1
    assert summary["candidateMeanPaperJaccard"] == 0.2
    assert summary["functionGeneJaccardDelta"] == 0.3


def test_diagnostics_separate_annotation_claims_from_audited_support():
    diagnostics = json.loads(DIAGNOSTICS_PATH.read_text(encoding="utf-8"))
    reconciliation = diagnostics["coverage_reconciliation"]

    assert reconciliation["summary"]["baselineMeanAnnotationClaimedGenes"] == pytest.approx(
        13.125
    )
    assert reconciliation["summary"]["candidateMeanAnnotationClaimedGenes"] == pytest.approx(
        12.625
    )
    assert reconciliation["summary"]["baselineMeanAuditedFunctionGenes"] == pytest.approx(
        8.875
    )
    assert reconciliation["summary"]["candidateMeanAuditedFunctionGenes"] == pytest.approx(
        11.875
    )
    assert (
        reconciliation["summary"][
            "sessionsWithFewerAnnotationGenesButMoreAuditedSupport"
        ]
        == 3
    )

    p11_r1 = next(
        row for row in reconciliation["rows"] if row["session"] == "brain-p11--r1"
    )
    assert p11_r1["annotationClaimedDelta"] == -3
    assert p11_r1["auditedFunctionDelta"] == 2


def test_diagnostics_include_unordered_module_similarity_and_baseline_phases():
    diagnostics = json.loads(DIAGNOSTICS_PATH.read_text(encoding="utf-8"))

    similarity = diagnostics["module_similarity"]["summary"]
    assert similarity["baselineReplicateMean"] == pytest.approx(0.757917)
    assert similarity["candidateReplicateMean"] == pytest.approx(0.65375)
    assert similarity["crossSettingMean"] == pytest.approx(0.678125)
    assert similarity["matchingAgreementCount"] == 15
    assert sum(
        row["turns"] for row in diagnostics["turns"]["baseline_phase_totals"]
    ) == 299
