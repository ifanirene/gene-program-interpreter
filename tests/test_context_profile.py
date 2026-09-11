"""ContextProfile generalization contract (the tissue-agnostic linchpin)."""

from gpi.context_profile import ContextProfile


def test_liver_demo_lean_framing():
    """Lean hepatocyte framing: neutral role, no assay exposure, normal biology first."""
    p = ContextProfile.liver_demo()
    # neutral role — NOT a disease-loaded persona
    assert p.resolved_annotation_role() == "hepatocyte biologist"
    # the assay (Perturb-seq) is never surfaced to the agent
    surfaced = " ".join(
        [
            p.resolved_annotation_role(),
            p.resolved_annotation_context(),
            p.resolved_condition_context(),
            p.resolved_keyword_query(),
        ]
    ).lower()
    assert "perturb" not in surfaced
    # normal hepatocyte functions lead; aging/MASLD/lipid metabolism kept
    dis = p.resolved_condition_context()
    assert "metabolic zonation" in dis and "bile acid" in dis
    assert "MASLD" in dis and "aging" in dis and "lipid metabolism" in dis
    # disease-checklist terms dropped from the framing
    for dropped in ("steatosis", "insulin resistance", "fibrosis", "inflammation"):
        assert dropped not in dis


def test_generic_profile_has_no_liver_leakage():
    p = ContextProfile(
        organism="human",
        species_taxid=9606,
        cell_type="CD8 T cell",
        conditions=["exhaustion"],
        context_terms=["tumor microenvironment"],
        assay="CRISPR Perturb-seq",
    )
    blob = " ".join(
        [
            p.resolved_annotation_role(),
            p.resolved_annotation_context(),
            p.resolved_keyword_query(),
            p.resolved_condition_context(),
        ]
    ).lower()
    for liver_term in ("liver", "hepatocyte", "masld", "steatosis", "fibrosis"):
        assert liver_term not in blob
    assert "cd8 t cell" in blob and "exhaustion" in blob


def test_explicit_override_wins_over_derivation():
    p = ContextProfile(cell_type="hepatocyte", annotation_role="custom role")
    assert p.resolved_annotation_role() == "custom role"


def test_resolved_materializes_blanks():
    p = ContextProfile.liver_demo().resolved()
    assert p.annotation_role and p.annotation_context and p.keyword_query and p.condition_context


def test_from_dict_accepts_aliases():
    p = ContextProfile.from_dict({"species": 9606, "celltype": "neuron", "organism": "human"})
    assert p.species_taxid == 9606 and p.cell_type == "neuron"


def test_prompt_fields_keys():
    keys = set(ContextProfile.liver_demo().prompt_fields())
    assert keys == {
        "annotation_role",
        "annotation_context",
        "search_keyword",
        "condition_context",
        "functional_context",
    }


def test_gene_first_projection_is_neutral_independent_and_survives_materialization(tmp_path):
    import yaml

    original = ContextProfile(
        interpretation_mode="gene_first", organism="human", species_taxid=9606,
        tissue="brain", cell_type="endothelial cell", conditions=["stroke"],
        context_terms=["angiogenesis"], annotation_role="stroke expert",
        annotation_context="stroke", keyword_query="stroke", condition_context="stroke",
        functional_context="angiogenesis", assay="custom assay", report_dataset_crumb="stroke study",
        evidence_context_types=["direct", "indirect"],
    )
    before = original.to_dict()
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump({"context": original.resolved().to_dict()}))
    for profile in (original, original.resolved(), ContextProfile.from_yaml(path)):
        effective = profile.for_interpretation()
        assert effective.prompt_fields() == {
            "annotation_role": "endothelial cell biologist",
            "annotation_context": "a consensus gene expression program in human endothelial cells",
            "search_keyword": '("endothelial cell" OR brain)',
            "condition_context": "Context: brain tissue; endothelial cell cellular function.",
            "functional_context": "",
        }
        assert effective.species_taxid == 9606
        assert effective.evidence_context_types == ["direct", "indirect"]
        assert effective.conditions == effective.context_terms == []
        assert effective.for_interpretation() == effective
        effective.evidence_context_types.append("mixed")
    assert original.to_dict() == before


def test_context_guided_projection_preserves_overrides_without_sharing_lists():
    original = ContextProfile.liver_demo().resolved()
    effective = original.for_interpretation()
    assert effective == original
    effective.conditions.append("new condition")
    assert "new condition" not in original.conditions


def test_interpretation_mode_validation_at_construction_and_loading(tmp_path):
    import pytest
    import yaml

    invalid = [
        ({"interpretation_mode": "gene_frist", "tissue": "brain"}, "interpretation_mode"),
        ({"interpretation_mode": "gene_first", "cell_type": "  "}, "tissue or cell_type"),
    ]
    for data, message in invalid:
        with pytest.raises(ValueError, match=message):
            ContextProfile(**data)
        with pytest.raises(ValueError, match=message):
            ContextProfile.from_dict(data)
        path = tmp_path / "bad.yaml"
        path.write_text(yaml.safe_dump({"context": data}))
        with pytest.raises(ValueError, match=message):
            ContextProfile.from_yaml(path)
    for identity in ({"tissue": "brain"}, {"cell_type": "neuron"}):
        assert ContextProfile(interpretation_mode="gene_first", **identity).for_interpretation()
    assert ContextProfile().interpretation_mode == "context_guided"
