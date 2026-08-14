import json
from pathlib import Path

import pytest

from research import benchmark_report


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPARISON_PATH = (
    REPO_ROOT
    / "benchmarks/literature-v1/candidate-evaluable/comparison-report/comparison_metrics.json"
)


def test_adjudicated_comparison_supports_directional_but_not_production_decision():
    comparison = json.loads(COMPARISON_PATH.read_text(encoding="utf-8"))

    assert comparison["coverage"]["functionDelta"] == pytest.approx(0.130435)
    assert comparison["coverage"]["positiveFunctionSessions"] == 8
    assert min(row["functionDelta"] for row in comparison["per_case"]) >= 0.05
    assert comparison["candidate_quality"]["contradiction_count"] == 2
    assert comparison["baseline_quality"]["contradiction_count"] == 0
    assert comparison["decision"]["data_sufficiency"] == (
        "sufficient_for_internal_directional_comparison"
    )
    assert comparison["decision"]["production_promotion"] == (
        "reject_current_unbounded_regime"
    )
    assert comparison["diagnostics"]["annotation"]["summary"]["materiallyChanged"] == 4
    assert comparison["diagnostics"]["replicate_stability"]["summary"][
        "candidateMeanPaperJaccard"
    ] == pytest.approx(0.184492)
    assert comparison["diagnostics"]["turns"]["summary"][
        "candidateMeanOperationalMinimumTurns"
    ] == pytest.approx(120.875)


def test_comparison_artifact_has_reader_order_and_exploration_defaults():
    comparison = json.loads(COMPARISON_PATH.read_text(encoding="utf-8"))
    artifact = benchmark_report.build_artifact(
        comparison,
        generated_at="2026-07-23T00:00:00Z",
    )

    assert artifact["surface"] == "report"
    assert artifact["snapshot"]["status"] == "ready"
    assert artifact["manifest"]["blocks"][0]["body"] == (
        "# Baseline vs candidate annotation"
    )
    assert artifact["manifest"]["charts"]
    assert all("defaultSort" in table for table in artifact["manifest"]["tables"])
    assert all(
        "x" in chart["encodings"] and "y" in chart["encodings"]
        for chart in artifact["manifest"]["charts"]
    )
    assert {
        "coverage_reconciliation",
        "semantic_program",
        "biology_assessment",
        "strategy_turns",
        "phase_controls",
        "decision_scorecard",
        "recommendations",
    } <= set(artifact["snapshot"]["datasets"])
    assert len(artifact["snapshot"]["datasets"]["coverage_reconciliation"]) == 8
    assert len(artifact["snapshot"]["datasets"]["semantic_program"]) == 4
    assert len(artifact["snapshot"]["datasets"]["strategy_turns"]) == 9
    assert len(artifact["snapshot"]["datasets"]["recommendations"]) == 5


def test_report_explains_annotation_stability_turns_and_actions():
    comparison = json.loads(COMPARISON_PATH.read_text(encoding="utf-8"))
    artifact = benchmark_report.build_artifact(
        comparison,
        generated_at="2026-07-23T00:00:00Z",
    )
    markdown = "\n".join(
        block.get("body", "")
        for block in artifact["manifest"]["blocks"]
        if block["type"] == "markdown"
    )

    assert "The apparent contradiction comes from two different denominators" in markdown
    assert "Module order is ignored" in markdown
    assert "Baseline replicate similarity averages **0.758**" in markdown
    assert "Coverage is not the same as biological coherence" in markdown
    assert "Per-step resource control is feasible, but it is not implemented yet" in markdown
    assert "Neither strategy dominates; use a hybrid" in markdown
