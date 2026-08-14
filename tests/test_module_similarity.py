import pytest

from research import module_similarity


def _grid(diagonal, off_diagonal=0.1):
    return [
        {
            "left_module": left,
            "right_module": right,
            "similarity": diagonal if left == right else off_diagonal,
            "relationship": "same_core" if left == right else "different",
            "rationale": "test",
        }
        for left in (1, 2, 3)
        for right in (1, 2, 3)
    ]


def test_optimal_assignment_is_order_invariant():
    grid = _grid(0.2)
    for cell in grid:
        cell["similarity"] = 0.9 if cell["right_module"] == 4 - cell["left_module"] else 0.1

    result = module_similarity.optimal_assignment(grid)

    assert result["moduleSemanticSimilarity"] == pytest.approx(0.9)
    assert [(row["leftModule"], row["rightModule"]) for row in result["matching"]] == [
        (1, 3),
        (2, 2),
        (3, 1),
    ]


def test_validate_assessment_requires_complete_three_by_three_grid():
    with pytest.raises(module_similarity.ModuleSimilarityError):
        module_similarity.validate_assessment(
            {
                "pair_scores": _grid(0.8)[:-1],
                "overall_rationale": "test",
            },
            comparison_id="comparison",
            assessor_id="assessor",
        )


def test_combine_averages_cells_before_assignment():
    comparison = {
        "comparison_id": "p10--baseline-replicate",
        "case_id": "brain-p10",
        "program_id": "P10",
        "comparison_type": "baseline_replicate",
        "left_label": "baseline r1",
        "right_label": "baseline r2",
        "left_modules": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
        "right_modules": [{"name": "A2"}, {"name": "B2"}, {"name": "C2"}],
    }
    primary_grid = _grid(0.8)
    secondary_grid = _grid(1.0)
    primary = {
        "assessments": [
            {
                "comparison_id": comparison["comparison_id"],
                "pair_scores": primary_grid,
                "moduleSemanticSimilarity": 0.8,
                "matching": module_similarity.optimal_assignment(primary_grid)["matching"],
                "overall_rationale": "primary",
            }
        ]
    }
    secondary = {
        "assessments": [
            {
                "comparison_id": comparison["comparison_id"],
                "pair_scores": secondary_grid,
                "moduleSemanticSimilarity": 1.0,
                "matching": module_similarity.optimal_assignment(secondary_grid)[
                    "matching"
                ],
                "overall_rationale": "secondary",
            }
        ]
    }

    result = module_similarity.combine_assessments(
        [comparison],
        primary,
        secondary,
    )

    assert result["rows"][0]["moduleSemanticSimilarity"] == pytest.approx(0.9)
    assert result["summary"]["baselineReplicateMean"] == pytest.approx(0.9)
