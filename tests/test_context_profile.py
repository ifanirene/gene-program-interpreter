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
