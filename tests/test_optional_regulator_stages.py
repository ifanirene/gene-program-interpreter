"""Optional regulator work must save calls without weakening publication checks."""

import json
from pathlib import Path

import pandas as pd
import pytest

from gpi import run_pipeline as rp
from gpi.context_profile import ContextProfile
from gpi.gene_first_synthesis import merge_supplements


def annotation(pid, regulators=()):
    return dict(
        program_id=pid, label="Nitrogen handling",
        brief_summary="Glul and Rhbg support nitrogen handling.",
        overview="Glul and Rhbg support nitrogen handling.",
        modules=[dict(name="Nitrogen handling", summary="Glul and Rhbg handle nitrogen.",
                      key_genes=["Glul", "Rhbg"], pmids=[], evidence="Program genes.")],
        distinctive="Rhbg contributes ammonia transport.",
        regulators=[dict(gene=g, confidence="Low", mechanism="The mechanism is unresolved.")
                    for g in regulators],
    )


def row(pid, payload):
    return dict(custom_id=f"topic_{pid}", result=dict(type="succeeded", message=dict(
        stop_reason="end_turn", usage={"output_tokens": 20},
        content=[dict(type="text", text=json.dumps(payload))],
    )))


def setup_run(tmp_path, selected):
    genes = tmp_path / "genes.csv"
    pd.DataFrame([dict(Name=g, Score=s, program_id=p)
                  for p in (1, 2, 3) for g, s in (("Glul", 2.0), ("Rhbg", 1.0))]).to_csv(genes, index=False)
    regfile = tmp_path / "regulators.csv"
    if selected:
        pd.DataFrame([dict(program_id=p, target_gene="Mlxipl", log_2_fold_change=1.0,
                           adj_p_value=0.001, p_value=0.001, significant=True)
                      for p in selected]).to_csv(regfile, index=False)
    cfg = rp.PipelineConfig(
        profile=ContextProfile(organism="mouse", species_taxid=10090, tissue="liver",
                               cell_type="hepatocyte", interpretation_mode="gene_first"),
        gene_loading=genes, regulators=None,
        regulators_by_condition={"female": regfile} if selected else {},
        celltype_enrichment=None, output_dir=tmp_path, programs=[1, 2, 3],
        settings={"top_loading": 2, "top_unique": 0},
        annotation={"batch": False}, research={"max_budget_usd": 2.0},
    )
    paths = rp.Paths(tmp_path)
    paths.bundles_dir.mkdir()
    paths.research_dir.mkdir()
    for p in (1, 2, 3):
        bundle = dict(program_id=f"P{p}", organism="mouse", tissue="liver",
                      cell_type="hepatocyte", program_genes=["Glul", "Rhbg"],
                      distinctive_genes=[], perturbation_regulators=(
                          {"female": [{"gene": "Mlxipl"}]} if p in selected else {}),
                      regulator_effects_all_conditions=[])
        (paths.bundles_dir / f"P{p}.json").write_text(json.dumps(bundle))
        (paths.research_dir / f"P{p}.json").write_text(json.dumps(
            dict(program_id=f"P{p}", candidate_mechanisms=[], evidence=[], meta={})))
    return cfg, paths


@pytest.mark.parametrize("selected", [[], [1, 2, 3], [1, 3]])
def test_research_skips_empty_programs_and_ignores_stale_support(tmp_path, monkeypatch, selected):
    from research import research_parallel

    cfg, paths = setup_run(tmp_path, selected)
    calls = []

    async def research(bundle_paths, *, out_dir, audit_dir, **kwargs):
        calls.append((list(bundle_paths), Path(out_dir), kwargs))
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        written = []
        for path in bundle_paths:
            data = json.loads(path.read_text())
            functional = Path(out_dir) == paths.research_dir
            if functional:
                assert "perturbation_regulators" not in data
            result = dict(program_id=data["program_id"], meta={}, regulator_coverage=(
                [] if functional else [{"gene": "Mlxipl", "status": "not_researched"}]))
            output = Path(out_dir) / path.name
            output.write_text(json.dumps(result))
            written.append(output)
        return written

    monkeypatch.setattr(research_parallel, "run_research", research)
    stale = tmp_path / "regulator_research" / "research_results"
    stale.mkdir(parents=True)
    for p in (1, 2, 3):
        (stale / f"P{p}.json").write_text(json.dumps({"regulator_coverage": [{"gene": "STALE"}]}))
    functions = tmp_path / "functional_bundles"
    functions.mkdir()
    (functions / "P99.json").write_text('{"program_id": "P99"}')
    info = rp.run_research(cfg, paths, rp.Flags())
    assert info == {"n_results": 3, "n_regulator_programs": len(selected),
                    "n_regulator_skipped": 3 - len(selected)}
    assert len(calls) == (2 if selected else 1)
    assert [p.name for p in calls[-1][0]] == ["P1.json", "P2.json", "P3.json"]
    assert all(c[2]["max_budget_usd"] == 1.0 for c in calls)  # Retained passes keep their limits.
    if selected:
        assert [p.name for p in calls[0][0]] == [f"P{p}.json" for p in selected]
    for p in (1, 2, 3):
        result = json.loads((paths.research_dir / f"P{p}.json").read_text())
        assert "STALE" not in json.dumps(result)
        assert bool(result["regulator_coverage"]) == (p in selected)
        assert ("regulator_research_path" in result["meta"]) == (p in selected)


@pytest.mark.parametrize("selected", [[], [1, 2, 3], [1, 3]])
def test_annotation_publishes_mixed_programs_with_only_needed_calls(tmp_path, monkeypatch, selected):
    cfg, paths = setup_run(tmp_path, selected)
    real_subprocess = rp._run_subprocess
    calls = []

    def subprocess(argv, dry_run):
        if "gpi.anthropic_live" not in argv:
            return real_subprocess(argv, dry_run)
        requests = json.loads(paths.batch_request.read_text())["requests"]
        calls.append([r["custom_id"] for r in requests])
        results = []
        for request in requests:
            pid = int(request["custom_id"].split("_")[1])
            value = annotation(pid)
            if len(calls) == 2:
                value = {"regulators": annotation(pid, ["Mlxipl"])["regulators"]}
            results.append(row(pid, value))
        paths.batch_results.write_text("\n".join(json.dumps(r) for r in results) + "\n")

    monkeypatch.setattr(rp, "_run_subprocess", subprocess)
    rp.run_annotate(cfg, paths, rp.Flags())
    assert calls == ([['topic_1', 'topic_2', 'topic_3']] +
                     ([[f"topic_{p}" for p in selected]] if selected else []))
    for p in (1, 2, 3):
        result = json.loads((paths.annotations_dir / f"topic_{p}_annotation.json").read_text())
        assert {k: v for k, v in result.items() if k != "regulators"} == {
            k: v for k, v in annotation(p).items() if k != "regulators"}
        assert [r["gene"] for r in result["regulators"]] == (["Mlxipl"] if p in selected else [])
    assert not list(tmp_path.glob("functional_identity_review_*"))


@pytest.mark.parametrize("problem", ["missing", "unexpected", "failed", "truncated", "rewrite"])
def test_mixed_merge_rejects_bad_required_supplement(tmp_path, problem):
    core = tmp_path / "core.jsonl"
    supp = tmp_path / "supp.jsonl"
    output = tmp_path / "merged.jsonl"
    core.write_text("\n".join(json.dumps(row(p, annotation(p))) for p in (1, 2)))
    result = row(1, {"regulators": annotation(1, ["Mlxipl"])["regulators"]})
    if problem == "unexpected":
        result["custom_id"] = "topic_2"
    elif problem == "failed":
        result["result"]["type"] = "errored"
    elif problem == "truncated":
        result["result"]["message"]["stop_reason"] = "max_tokens"
    elif problem == "rewrite":
        result = row(1, {"regulators": [], "label": "Changed function"})
    supp.write_text("" if problem == "missing" else json.dumps(result))
    with pytest.raises(ValueError):
        merge_supplements(core, supp, output, expected_ids={"topic_1"})
    assert not output.exists()
