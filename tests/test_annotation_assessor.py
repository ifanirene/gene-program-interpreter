from pathlib import Path

import pytest

from research import annotation_assessor


def _assessment(assessment_id, relation, assessor):
    return {
        "assessment_id": assessment_id,
        "assessor_id": assessor,
        "semantic_relation": relation,
        "core_interpretation_changed": relation == "materially_changed",
        "evidence_depth_change": "more_gene_specific",
        "shared_core_conclusions": ["shared"],
        "candidate_additions": [],
        "candidate_removed_or_weakened": [],
        "decision_relevant_differences": [],
        "rationale": "concise rationale",
    }


def test_compact_annotation_omits_link_level_payload():
    compact = annotation_assessor.compact_annotation(
        {
            "agent_summary": "summary",
            "candidate_mechanisms": [
                {
                    "name": "mechanism",
                    "summary": "detail",
                    "supporting_genes": ["Gene1"],
                    "supporting_regulators": ["Reg1"],
                    "evidence_links": [{"large": "payload"}],
                    "status": "supported",
                }
            ],
        }
    )

    assert compact["candidate_mechanisms"][0] == {
        "name": "mechanism",
        "summary": "detail",
        "supporting_genes": ["Gene1"],
        "supporting_regulators": ["Reg1"],
        "status": "supported",
    }


def test_finalize_uses_adjudication_for_decision_disagreement():
    pairs = [{"assessment_id": "A"}, {"assessment_id": "B"}]
    primary = {
        "assessments": [
            _assessment("A", "same_core_interpretation", "primary"),
            _assessment("B", "same_core_interpretation", "primary"),
        ]
    }
    secondary = {
        "assessments": [
            _assessment("A", "same_core_interpretation", "secondary"),
            _assessment("B", "materially_changed", "secondary"),
        ]
    }
    adjudications = {
        "assessments": [
            _assessment("B", "same_core_with_meaningful_refinement", "adjudicator")
        ]
    }

    final, reliability = annotation_assessor.finalize_assessments(
        pairs,
        primary,
        secondary,
        adjudications,
    )

    assert [row["semantic_relation"] for row in final] == [
        "same_core_interpretation",
        "same_core_with_meaningful_refinement",
    ]
    assert reliability["decision_agreement_rate"] == 0.5
    assert reliability["adjudicated_count"] == 1


def test_validate_assessment_rejects_inconsistent_change_flag():
    row = _assessment("A", "materially_changed", "primary")
    row["core_interpretation_changed"] = False

    with pytest.raises(annotation_assessor.AnnotationAssessmentError):
        annotation_assessor.validate_assessment(
            row,
            assessment_id="A",
            assessor_id="primary",
        )


def test_pair_manifest_loads_all_eight_annotations():
    root = Path(__file__).resolve().parents[1]
    pairs_path = (
        root
        / "benchmarks/literature-v1/candidate-evaluable/annotation_comparison/pairs.json"
    )

    pairs = annotation_assessor.load_pairs(pairs_path, repo_root=root)

    assert len(pairs) == 8
    assert {pair["program_id"] for pair in pairs} == {"P10", "P11", "P21", "P25"}
    assert all(pair["baseline"]["candidate_mechanisms"] for pair in pairs)
    assert all(pair["candidate"]["candidate_mechanisms"] for pair in pairs)
