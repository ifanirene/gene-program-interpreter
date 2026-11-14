"""Module-gene membership is the program's full supplied loading set, not the shortlist.

Investigator decision, 2026-11-14: "Module gene could list other top 300 gene outside of the
shortlist." These tests pin the repaired rule and the guards that must NOT have moved:

  * a gene of the program that falls outside the top15+8 emphasis shortlist is ACCEPTED;
  * a gene belonging only to another program is REJECTED;
  * a gene absent from the loading table is REJECTED;
  * a duplicated gene and a single-gene module are REJECTED (>= 2 distinct still holds);
  * the regulator-set guard and the citation guard are unchanged;
  * the runner actually wires full membership into the validator (not just the library).

Everything here is offline. No model is called.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from gpi.annotation_contract import OUTPUT_CONTRACT, render_annotation, parse_annotation
from gpi.annotation_validation import finalize_regulator_annotations
from gpi.evidence_context import program_membership_genes, select_program_genes

# Program 7 loading table: 3 emphasised genes, then members that fall outside the
# emphasis shortlist. Program 8 exists only so "belongs to another program" is testable.
LOADING = pd.DataFrame(
    {
        "Name": ["Aaa", "Bbb", "Ccc", "Ddd", "Eee", "Fff", "Zzz1", "Zzz2"],
        "Score": [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.95, 0.85],
        "RowID": [7, 7, 7, 7, 7, 7, 8, 8],
    }
)


def _annotation(modules, program_id=7, regulators=None):
    return {
        "program_id": program_id,
        "label": "Toy membership program",
        "brief_summary": "Toy brief summary for the membership tests.",
        "overview": "Toy overview for the membership tests.",
        "modules": modules,
        "distinctive": "Toy distinctive sentence.",
        "regulators": regulators if regulators is not None else [],
    }


def _module(name, key_genes, pmids=None):
    return {
        "name": name,
        "summary": "Toy module summary.",
        "key_genes": key_genes,
        "pmids": pmids or [],
        "evidence": "Toy evidence.",
    }


def _write(tmp_path: Path, annotation: dict) -> Path:
    directory = tmp_path / "annotations"
    directory.mkdir(parents=True, exist_ok=True)
    pid = annotation["program_id"]
    (directory / f"topic_{pid}_annotation.json").write_text(
        json.dumps(annotation), encoding="utf-8")
    (directory / f"topic_{pid}_annotation.md").write_text(
        render_annotation(parse_annotation(json.dumps(annotation), pid)), encoding="utf-8")
    return directory


def _audit(directory: Path) -> dict:
    """finalize_regulator_annotations raises on failure and always writes this audit."""
    return json.loads((Path(directory) / "regulator_validation.json").read_text())


def _run(tmp_path, annotation, *, membership=True, regulator_data=None):
    directory = _write(tmp_path, annotation)
    pid = annotation["program_id"]
    emphasis = {pid: [g for grp in select_program_genes(LOADING, pid, 2, 1) for g in grp]}
    kwargs = {}
    if membership:
        kwargs["program_membership"] = {pid: program_membership_genes(LOADING, pid)}
    try:
        finalize_regulator_annotations(directory, regulator_data or {}, emphasis, None, **kwargs)
    except ValueError:
        pass  # the audit file carries the verdict; assertions read it
    return _audit(directory), directory


# ------------------------------------------------------------------ membership source

def test_membership_is_the_whole_program_not_the_shortlist():
    emphasis = [g for grp in select_program_genes(LOADING, 7, 2, 1) for g in grp]
    membership = program_membership_genes(LOADING, 7)
    assert membership == ["Aaa", "Bbb", "Ccc", "Ddd", "Eee", "Fff"]  # score order
    assert set(emphasis) < set(membership)
    assert "Zzz1" not in membership and "Zzz2" not in membership
    assert program_membership_genes(LOADING, 7, 2) == ["Aaa", "Bbb"]


# --------------------------------------------------------------------- the repaired rule

def test_member_gene_outside_the_emphasis_shortlist_is_accepted(tmp_path):
    emphasis = {g for grp in select_program_genes(LOADING, 7, 2, 1) for g in grp}
    outside = [g for g in program_membership_genes(LOADING, 7) if g not in emphasis]
    assert len(outside) >= 2, "fixture must exercise genes outside the shortlist"
    result, _ = _run(tmp_path, _annotation([_module("Outside-shortlist module", outside[:2])]))
    assert result["passed"] is True, result


def test_the_old_shortlist_rule_would_have_rejected_the_same_module(tmp_path):
    """Regression guard: proves the fix, not the fixture."""
    emphasis = {g for grp in select_program_genes(LOADING, 7, 2, 1) for g in grp}
    outside = [g for g in program_membership_genes(LOADING, 7) if g not in emphasis][:2]
    result, _ = _run(tmp_path, _annotation([_module("m", outside)]), membership=False)
    assert result["passed"] is False
    assert result["errors"][0]["genes_outside_program"] == sorted(outside)


# ------------------------------------------------------------------------ still rejected

def test_gene_from_another_program_only_is_rejected(tmp_path):
    result, _ = _run(tmp_path, _annotation([_module("Cross-program module", ["Aaa", "Zzz1"])]))
    assert result["passed"] is False
    assert result["errors"][0]["genes_outside_program"] == ["Zzz1"]


def test_gene_absent_from_the_loading_table_is_rejected(tmp_path):
    result, _ = _run(tmp_path, _annotation([_module("Invented module", ["Aaa", "Nosuchgene"])]))
    assert result["passed"] is False
    assert result["errors"][0]["genes_outside_program"] == ["Nosuchgene"]


def test_duplicate_gene_does_not_satisfy_two_distinct(tmp_path):
    result, _ = _run(tmp_path, _annotation([_module("Duplicated module", ["Aaa", "Aaa"])]))
    assert result["passed"] is False
    assert result["errors"][0]["n_distinct_genes"] == 1
    assert result["errors"][0]["genes_outside_program"] == []


def test_single_gene_module_is_rejected(tmp_path):
    # The pydantic contract requires >= 2 entries, so a 1-gene module cannot even parse.
    with pytest.raises(Exception):
        parse_annotation(json.dumps(_annotation([_module("Single", ["Aaa"])])), 7)
    # And a 2-entry module that collapses to one distinct gene is caught by the validator.
    result, _ = _run(tmp_path, _annotation([_module("Collapses", ["Bbb", "Bbb"])]))
    assert result["passed"] is False


# ------------------------------------------------------- guards that must not have moved

def test_regulator_set_guard_is_unchanged(tmp_path):
    regulator_data = {
        "all": {
            7: pd.DataFrame(
                {
                    "program_id": [7, 7],
                    "grna_target": ["Reg1", "Reg2"],
                    "target_gene": ["Reg1", "Reg2"],
                    "log_2_fold_change": [0.5, -0.4],
                    "p_value": [1e-6, 1e-5],
                    "adj_p_value": [1e-4, 1e-3],
                    "significant": [True, True],
                }
            )
        }
    }
    annotation = _annotation(
        [_module("Fine module", ["Aaa", "Ddd"])],
        regulators=[{"gene": "Reg1", "confidence": "Low", "mechanism": "Toy mechanism."}],
    )
    result, _ = _run(tmp_path, annotation, regulator_data=regulator_data)
    assert result["passed"] is False
    issues = [e["issue"] for e in result["errors"]]
    assert "Regulator set differs from selected inputs" in issues


def test_citation_guard_is_unchanged(tmp_path):
    directory = _write(tmp_path, _annotation([_module("Cited", ["Aaa", "Ddd"], ["99999999"])]))
    research = directory.parent / "research_results"
    research.mkdir(parents=True, exist_ok=True)
    (research / "P7.json").write_text(json.dumps({
        "candidate_mechanisms": [{"name": "toy"}],
        "evidence": [{"pmid": "12345678"}],
    }), encoding="utf-8")
    try:
        finalize_regulator_annotations(
            directory, {},
            {7: [g for grp in select_program_genes(LOADING, 7, 2, 1) for g in grp]},
            None,
            program_membership={7: program_membership_genes(LOADING, 7)},
        )
    except ValueError:
        pass
    result = _audit(directory)
    assert result["passed"] is False
    issues = [e["issue"] for e in result["errors"]]
    assert "Module cites identifiers absent from supplied research" in issues


# ------------------------------------------------------------------------ prompt wording

def test_output_contract_states_the_membership_rule():
    text = OUTPUT_CONTRACT
    assert "at least 2 distinct genes of THIS program" in text
    assert "enrichment member-gene lists" in text
    assert "may NOT repeat the same gene" in text


# --------------------------------------------------------------------- the runner wiring

def test_runner_passes_full_membership_to_the_validator(tmp_path, monkeypatch):
    """The library fix is useless if run_annotate still sends the shortlist."""
    from gpi import run_pipeline

    captured = {}

    def fake_finalize(directory, data, program_genes, masked, program_membership=None):
        captured["emphasis"] = program_genes
        captured["membership"] = program_membership
        return {"passed": True, "errors": []}

    monkeypatch.setattr(
        "gpi.annotation_validation.finalize_regulator_annotations", fake_finalize)

    loading_csv = tmp_path / "genes.csv"
    LOADING.to_csv(loading_csv, index=False)
    out = tmp_path / "out"
    (out / "annotations").mkdir(parents=True)
    for pid in (7,):
        (out / "annotations" / f"topic_{pid}_annotation.json").write_text("{}")
    request = {"requests": [{"custom_id": "topic_7", "params": {
        "model": "m", "max_tokens": 10, "messages": [{"role": "user", "content": "x"}]}}]}
    (out / "anthropic_batch_request.json").write_text(json.dumps(request))
    (out / "full_annotation_requests.json").write_text(json.dumps(request))
    (out / "anthropic_batch_request_results.jsonl").write_text(json.dumps({
        "custom_id": "topic_7",
        "result": {"type": "succeeded", "message": {"content": [{"type": "text", "text": "{}"}]}},
    }) + "\n")

    config = tmp_path / "run.yaml"
    config.write_text(
        "context:\n  organism: mouse\n  species_taxid: 10090\n  tissue: brain\n"
        "  cell_type: brain endothelial cell\n  interpretation_mode: gene_first\n"
        f"inputs:\n  gene_loading: {loading_csv}\n"
        f"output_dir: {out}\n"
        "settings:\n  n_top_genes: 300\n  top_loading: 2\n  top_unique: 1\n"
        "annotation:\n  backend: claude_cli\n  include_regulators: false\n",
        encoding="utf-8")
    cfg = run_pipeline.PipelineConfig.from_yaml(config)
    paths = run_pipeline.Paths(cfg.output_dir)
    flags = run_pipeline.Flags(reuse_synthesis=True, progress="off")

    monkeypatch.setattr(run_pipeline, "_run_subprocess", lambda *a, **k: None)
    monkeypatch.setattr(run_pipeline, "validate_batch_contract", None, raising=False)
    monkeypatch.setattr("gpi.annotation_contract.validate_batch_contract",
                        lambda *a, **k: None)
    monkeypatch.setattr("gpi.parse_results.main", lambda *a, **k: 0, raising=False)
    monkeypatch.setattr(run_pipeline, "_check_research_mode", lambda *a, **k: None)

    run_pipeline.run_annotate(cfg, paths, flags)

    assert captured["membership"] is not None, "runner did not pass program_membership"
    assert set(captured["membership"][7]) == set(program_membership_genes(LOADING, 7))
    assert set(captured["emphasis"][7]) < set(captured["membership"][7])
