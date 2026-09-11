"""Counterfactual framing checks at real interpretation boundaries; no network calls."""

import json
from dataclasses import replace

import pandas as pd
import pytest
import yaml

from gpi.context_profile import ContextProfile
from research.bundle import build_bundle


@pytest.fixture
def profiles(tmp_path):
    variants = []
    for interest in ("", "stroke angiogenesis", "SENTINEL_UNRELATED_INTEREST"):
        profile = ContextProfile(
            interpretation_mode="gene_first", organism="mouse", species_taxid=10090,
            tissue="brain", cell_type="endothelial cell",
            conditions=[interest] if interest else [], context_terms=[interest] if interest else [],
            annotation_role=interest, annotation_context=interest, keyword_query=interest,
            condition_context=interest, functional_context=interest,
        )
        path = tmp_path / "profile.yaml"
        path.write_text(yaml.safe_dump({"context": profile.resolved().to_dict()}))
        variants.extend([profile, profile.resolved(), ContextProfile.from_yaml(path)])
    return variants


@pytest.fixture
def gene_df(gene_loading_csv):
    return pd.read_csv(gene_loading_csv)


@pytest.fixture
def measured_context():
    return {10: {"regulator_validation_by_condition": {
        "stroke": {"positive_regulators": [{"regulator": "Stat3", "log2fc": 1.5}],
                   "negative_regulators": []},
    }}}


def test_research_bundle_is_interest_invariant_and_preserves_identity_and_regulators(
    profiles, gene_df, measured_context,
):
    bundles = [build_bundle(10, gene_df, p, ncbi_context=measured_context) for p in profiles]
    assert len({json.dumps(b, sort_keys=True) for b in bundles}) == 1
    bundle = bundles[0]
    assert bundle["organism"] == "mouse"
    assert bundle["tissue"] == "brain"
    assert bundle["cell_type"] == "endothelial cell"
    assert bundle["conditions"] == bundle["functions_to_consider"] == []
    assert "functions_to_consider" not in bundle["research_brief"]
    assert bundle["perturbation_regulators"]["stroke"][0]["gene"] == "Stat3"
    assert bundle["perturbation_regulators"]["stroke"][0]["log2fc"] == 1.5
    loading = set(bundle["program_genes"])
    unique = set(bundle["distinctive_genes"])
    assert loading and unique and loading.isdisjoint(unique)
    legacy = build_bundle(10, gene_df, replace(profiles[0], interpretation_mode="context_guided"))
    assert bundle["program_genes"] == legacy["program_genes"]
    assert bundle["distinctive_genes"] == legacy["distinctive_genes"]
    for change in ({"organism": "human", "species_taxid": 9606}, {"tissue": "retina"},
                   {"cell_type": "neuron"}):
        assert build_bundle(10, gene_df, replace(profiles[0], **change),
                            ncbi_context=measured_context) != bundle


def annotation_prompt(profile, gene_df, measured_context):
    from gpi.evidence_context import PROMPT_TEMPLATE, generate_prompt

    return generate_prompt(
        program_id=10, gene_df=gene_df, prompt_template=PROMPT_TEMPLATE,
        top_loading=15, top_unique=8,
        celltype_map={10: {"detail": {"enriched": [{"cell_type": "capillary", "log2_fc": 2.0}],
                           "depleted": []}}},
        enrichment_by_program={}, ncbi_data=measured_context,
        top_enrichment=7, genes_per_term=10, profile=profile,
        regulator_data={"stroke": {10: pd.DataFrame([{
            "program_id": 10, "target_gene": "Stat3", "grna_target": "Stat3", "log_2_fold_change": 1.5,
            "adj_p_value": 0.001, "p_value": 0.001,
        }])}},
    )


def test_annotation_is_interest_invariant_but_keeps_evidence_words(
    profiles, gene_df, measured_context,
):
    evidence = "Retrieved evidence links stroke angiogenesis to this gene."
    gene = build_bundle(10, gene_df, profiles[0])["program_genes"][0]
    measured_context[10]["gene_summaries"] = {gene: evidence}
    prompts = [annotation_prompt(p, gene_df, measured_context) for p in profiles]
    assert len(set(prompts)) == 1
    prompt = prompts[0]
    for preserved in ("mouse", "brain", "endothelial cell", evidence, "Stat3", "stroke",
                      "1.500", "capillary", "+2.00"):
        assert preserved in prompt
    assert "SENTINEL_UNRELATED_INTEREST" not in prompt
    for change in ({"organism": "human", "species_taxid": 9606}, {"tissue": "retina"},
                   {"cell_type": "neuron"}):
        assert annotation_prompt(replace(profiles[0], **change), gene_df, measured_context) != prompt


def test_gene_summary_command_is_interest_invariant_and_materialized_mode_survives(
    profiles, gene_loading_csv, tmp_path, monkeypatch,
):
    from gpi import run_pipeline as pipeline

    commands = []
    monkeypatch.setattr(pipeline, "_run_subprocess", lambda argv, dry_run: commands.append(argv))
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.safe_dump({
        "context": profiles[0].to_dict(), "inputs": {"gene_loading": str(gene_loading_csv)},
        "output_dir": str(tmp_path / "run"), "programs": [10],
    }))
    cfg = pipeline.PipelineConfig.from_yaml(config_path)
    paths = pipeline.Paths(cfg.output_dir)
    for profile in profiles:
        cfg.profile = profile
        materialized = pipeline.write_profile_yaml(cfg, paths, dry_run=False)
        loaded = ContextProfile.from_yaml(materialized)
        assert loaded.interpretation_mode == "gene_first"
        assert loaded.conditions == profile.conditions
        cfg.profile = loaded
        pipeline.run_gene_summaries(cfg, paths, pipeline.Flags())
    assert all(command == commands[0] for command in commands)
    command = commands[0]
    assert command[command.index("--keyword") + 1] == '("endothelial cell" OR brain)'
    assert command[command.index("--species") + 1] == "10090"
    for change, flag, expected in (
        ({"organism": "human", "species_taxid": 9606}, "--species", "9606"),
        ({"tissue": "retina"}, "--keyword", '("endothelial cell" OR retina)'),
        ({"cell_type": "neuron"}, "--keyword", '(neuron OR brain)'),
    ):
        cfg.profile = replace(profiles[0], **change)
        pipeline.run_gene_summaries(cfg, paths, pipeline.Flags())
        assert commands[-1][commands[-1].index(flag) + 1] == expected
