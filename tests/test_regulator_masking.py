"""Regression tests for pipeline-wide regulator masking."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from gpi.context_profile import ContextProfile
from gpi.evidence_context import (
    format_regulator_analysis_context,
    load_regulator_data as load_prompt_regulator_data,
)
from gpi.gene_summaries import (
    get_top_regulators,
    load_condition_regulator_data,
    load_regulator_data,
    select_top_condition_regulators,
    validate_program_regulators,
)
from gpi.run_pipeline import Flags, Paths, PipelineConfig, run_gene_summaries
from research.bundle import build_all_bundles


MASKED = ["GlobalActivator", "GlobalRepressor"]


def _single_regulator_csv(path: Path) -> Path:
    pd.DataFrame(
        [
            ("X1", "GlobalActivator", -1.0, 0.001, True),
            ("X1", "SpecificActivator", -0.8, 0.002, True),
            ("X1", "BackupActivator", -0.7, 0.003, True),
            ("X1", "GlobalRepressor", 1.0, 0.001, True),
            ("X1", "SpecificRepressor", 0.8, 0.002, True),
            ("X1", "BackupRepressor", 0.7, 0.003, True),
        ],
        columns=[
            "response_id",
            "grna_target",
            "log_2_fold_change",
            "p_value",
            "significant",
        ],
    ).to_csv(path, index=False)
    return path


def test_mask_precedes_single_file_ranking_research_and_annotation(
    tmp_path: Path,
    gene_loading_csv: Path,
    monkeypatch,
) -> None:
    regulator_csv = _single_regulator_csv(tmp_path / "regulators.csv")

    # The upstream loader removes masked genes before any top-N operation.
    loaded = load_regulator_data(regulator_csv, masked_regulators=MASKED)
    assert set(loaded[1]["grna_target"]) == {
        "SpecificActivator",
        "BackupActivator",
        "SpecificRepressor",
        "BackupRepressor",
    }
    selected = get_top_regulators(loaded, 1, top_n=1)
    assert [row["gene"] for row in selected["positive"]] == ["SpecificActivator"]
    assert [row["gene"] for row in selected["negative"]] == ["SpecificRepressor"]

    # Avoid a network call; this test is about selection and data flow.
    monkeypatch.setattr(
        "gpi.gene_summaries.batch_validate_regulators",
        lambda **_kwargs: {},
    )
    validation = validate_program_regulators(
        program_id=1,
        regulator_data=loaded,
        program_genes=["IFIT3", "IFIT1"],
        top_n_regulators=1,
    )

    # Program bundles are exactly what the research agents receive.
    ncbi_context = tmp_path / "ncbi_context.json"
    ncbi_context.write_text(
        json.dumps({"1": {"regulator_validation": validation}}),
        encoding="utf-8",
    )
    bundle_paths = build_all_bundles(
        gene_loading_csv,
        ContextProfile.liver_demo(),
        ncbi_context_json=ncbi_context,
        out_dir=tmp_path / "bundles",
        program_ids=[1],
    )
    bundle = json.loads(bundle_paths[0].read_text(encoding="utf-8"))
    assert [row["gene"] for row in bundle["perturbation_regulators"]["all"]] == [
        "SpecificActivator",
        "SpecificRepressor",
    ]

    # Prompt assembly reloads inputs, so it applies the mask defensively too.
    prompt_data = load_prompt_regulator_data(
        regulator_csv,
        masked_regulators=["globalactivator", "globalrepressor"],
    )
    context = format_regulator_analysis_context(
        prompt_data,
        {1: {"regulator_validation": validation}},
        program_id=1,
        top_positive_regulators=1,
        top_negative_regulators=1,
        masked_regulators=MASKED,
    )
    assert "SpecificActivator" in context
    assert "SpecificRepressor" in context
    assert "GlobalActivator" not in context
    assert "GlobalRepressor" not in context


def test_condition_mask_handles_guide_suffixes_before_top_n(tmp_path: Path) -> None:
    condition_csv = tmp_path / "young.tsv"
    pd.DataFrame(
        [
            ("Program_1", "GlobalActivator_1", -1.0, 0.001, 0.001),
            ("Program_1", "SpecificActivator_1", -0.8, 0.002, 0.002),
            ("Program_1", "GlobalRepressor-P2", 1.0, 0.001, 0.001),
            ("Program_1", "SpecificRepressor-P1", 0.8, 0.002, 0.002),
        ],
        columns=["program_name", "target_name", "log2FC", "p_value", "adj_pval"],
    ).to_csv(condition_csv, sep="\t", index=False)

    loaded = load_condition_regulator_data(
        {"young": condition_csv},
        masked_regulators=["globalactivator", "GLOBALREPRESSOR"],
    )
    selected = select_top_condition_regulators(
        loaded,
        program_id=1,
        top_n_positive=1,
        top_n_negative=1,
    )
    assert [row["gene"] for row in selected["young"]["positive"]] == [
        "SpecificActivator"
    ]
    assert [row["gene"] for row in selected["young"]["negative"]] == [
        "SpecificRepressor"
    ]


def test_pipeline_forwards_global_and_legacy_masks_to_upstream_step(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = PipelineConfig(
        profile=ContextProfile.liver_demo(),
        gene_loading=Path("genes.csv"),
        regulators=Path("regulators.csv"),
        regulators_by_condition={},
        celltype_enrichment=None,
        output_dir=tmp_path,
        programs=[1],
        annotation={"mask_regulators": ["LegacyMask", "globalmask"]},
        raw={"mask_regulators": ["GlobalMask"]},
    )
    assert cfg.masked_regulators == ["GlobalMask", "LegacyMask"]

    calls: list[list[str]] = []
    monkeypatch.setattr(
        "gpi.run_pipeline._run_subprocess",
        lambda argv, _dry_run: calls.append(argv),
    )
    run_gene_summaries(cfg, Paths(tmp_path), Flags(dry_run=True))

    argv = calls[0]
    masked = [argv[index + 1] for index, value in enumerate(argv) if value == "--mask-regulator"]
    assert masked == ["GlobalMask", "LegacyMask"]
