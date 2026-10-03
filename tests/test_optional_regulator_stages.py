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


def setup_run(tmp_path, selected, include=True, mode='gene_first'):
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
                               cell_type="hepatocyte", interpretation_mode=mode),
        gene_loading=genes, regulators=None,
        regulators_by_condition={"female": regfile} if selected else {},
        celltype_enrichment=None, output_dir=tmp_path, programs=[1, 2, 3],
        settings={"top_loading": 2, "top_unique": 0},
        annotation={"batch": False, "include_regulators": include}, research={"max_budget_usd": 2.0},
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
            dict(program_id=f"P{p}", candidate_mechanisms=[], evidence=[],
                 meta={"regulator_mode": cfg.regulator_mode})))
    return cfg, paths


@pytest.mark.parametrize("selected", [[], [1, 2, 3], [1, 3]])
@pytest.mark.parametrize("include", [True, False])
@pytest.mark.parametrize("mode", ['gene_first', 'context_guided'])
def test_one_research_session_per_program_respects_visibility_and_stale_support(
    tmp_path, monkeypatch, selected, include, mode,
):
    from research import research_parallel

    cfg, paths = setup_run(tmp_path, selected, include, mode)
    calls = []

    async def research(bundle_paths, *, out_dir, audit_dir, **kwargs):
        calls.append((list(bundle_paths), Path(out_dir), kwargs))
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        written = []
        for path in bundle_paths:
            data = json.loads(path.read_text())
            has_regs = bool(data.get('perturbation_regulators'))
            if not cfg.include_regulators:
                assert "perturbation_regulators" not in data
                assert 'Mlxipl' not in json.dumps(data)
            else:
                assert has_regs == (int(path.stem[1:]) in selected)
                assert data['program_genes'] == ['Glul', 'Rhbg']
            result = dict(program_id=data["program_id"], meta={}, regulator_coverage=(
                [{"gene": "Mlxipl", "status": "not_researched"}] if has_regs else []))
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
    assert info == {"n_results": 3, "n_regulator_programs": len(selected) if include else 0,
                    "regulator_mode": cfg.regulator_mode}
    assert len(calls) == 1
    assert [p.name for p in calls[-1][0]] == ["P1.json", "P2.json", "P3.json"]
    assert calls[0][2]["max_budget_usd"] == 2.0  # One session uses the full per-program cap.
    for p in (1, 2, 3):
        result = json.loads((paths.research_dir / f"P{p}.json").read_text())
        assert "STALE" not in json.dumps(result)
        assert bool(result["regulator_coverage"]) == (include and p in selected)
        assert 'regulator_research_path' not in result['meta']
        assert result['meta']['regulator_mode'] == cfg.regulator_mode


@pytest.mark.parametrize("selected", [[], [1, 2, 3], [1, 3]])
@pytest.mark.parametrize("include", [True, False])
@pytest.mark.parametrize("mode", ['gene_first', 'context_guided'])
def test_annotation_publishes_mixed_programs_with_only_needed_calls(tmp_path, monkeypatch, selected, include, mode):
    cfg, paths = setup_run(tmp_path, selected, include, mode)
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
            prompt = request['params']['messages'][0]['content']
            if not cfg.include_regulators:
                assert 'Mlxipl' not in prompt
                assert 'Regulator evidence is intentionally withheld' in prompt
            elif pid in selected:
                assert 'Mlxipl' in prompt
            value = annotation(pid)
            if len(calls) == 2:
                value = {"regulators": annotation(pid, ["Mlxipl"])["regulators"]}
            elif mode == 'context_guided' and cfg.include_regulators and pid in selected:
                value = annotation(pid, ['Mlxipl'])
            results.append(row(pid, value))
        paths.batch_results.write_text("\n".join(json.dumps(r) for r in results) + "\n")

    monkeypatch.setattr(rp, "_run_subprocess", subprocess)
    rp.run_annotate(cfg, paths, rp.Flags())
    assert calls == ([['topic_1', 'topic_2', 'topic_3']] +
                     ([[f"topic_{p}" for p in selected]] if selected and include and mode == 'gene_first' else []))
    for p in (1, 2, 3):
        result = json.loads((paths.annotations_dir / f"topic_{p}_annotation.json").read_text())
        assert {k: v for k, v in result.items() if k != "regulators"} == {
            k: v for k, v in annotation(p).items() if k != "regulators"}
        assert [r["gene"] for r in result["regulators"]] == (["Mlxipl"] if include and p in selected else [])
    assert not list(tmp_path.glob("functional_identity_review_*"))
    assert json.loads((tmp_path/'annotation_mode.json').read_text())['regulator_mode'] == cfg.regulator_mode


def test_blind_annotation_rejects_reused_aware_research_before_model_call(tmp_path, monkeypatch):
    cfg, paths = setup_run(tmp_path, [1], include=False)
    (paths.research_dir / 'P1.json').write_text(json.dumps(
        {'meta': {'regulator_mode': 'regulator_aware'}, 'evidence_gaps': ['Mlxipl']}))
    monkeypatch.setattr(rp, '_run_subprocess', lambda *a: pytest.fail('must reject before any model call'))
    with pytest.raises(rp.StepError, match='cannot reuse'):
        rp.run_annotate(cfg, paths, rp.Flags())


@pytest.mark.parametrize('mode', ['gene_first', 'context_guided'])
def test_blind_bundle_removes_cached_perturbation_context_but_keeps_program_members(tmp_path, mode):
    cfg, paths = setup_run(tmp_path, [1], include=False, mode=mode)
    cfg.profile.conditions = ['aging']
    cfg.profile.context_terms = ['metabolism']
    genes = pd.read_csv(cfg.gene_loading).replace({'Rhbg': 'Mlxipl'})
    genes.to_csv(cfg.gene_loading, index=False)
    paths.ncbi_context.write_text(json.dumps({'1': {'regulator_validation': {
        'positive_regulators': [{'regulator': 'SENTINEL_REGULATOR', 'log2fc': -2.0}],
        'negative_regulators': [],
    }}}))
    rp.run_bundle(cfg, paths, rp.Flags())
    bundle = json.loads((paths.bundles_dir / 'P1.json').read_text())
    assert bundle['program_genes'] == ['Glul', 'Mlxipl']
    assert 'SENTINEL_REGULATOR' not in json.dumps(bundle)
    assert not any(key.startswith('regulator') or key == 'perturbation_regulators' for key in bundle)
    assert ('aging' in json.dumps(bundle)) == (mode == 'context_guided')


def test_blind_upstream_commands_omit_tables_but_report_retains_them(tmp_path, monkeypatch):
    cfg, paths = setup_run(tmp_path, [1], include=False)
    commands = []
    monkeypatch.setattr(rp, '_run_subprocess', lambda argv, dry_run: commands.append(argv))
    rp.run_gene_summaries(cfg, paths, rp.Flags(dry_run=True))
    rp.run_theme(cfg, paths, rp.Flags(dry_run=True))
    rp.run_html_report(cfg, paths, rp.Flags(dry_run=True))
    assert all('--regulator-file' not in argv and '--regulator-condition-file' not in argv
               for argv in commands[:2])
    assert '--regulators-display-only' in commands[2]
    assert f"female={cfg.regulators_by_condition['female']}" in commands[2]


@pytest.mark.parametrize('consumer', [rp.run_presentation, rp.run_html_report])
def test_blind_report_cannot_mislabel_reused_aware_annotations(tmp_path, monkeypatch, consumer):
    cfg, paths = setup_run(tmp_path, [1], include=False)
    (tmp_path/'annotation_mode.json').write_text('{"regulator_mode": "regulator_aware"}')
    monkeypatch.setattr(rp, '_run_subprocess', lambda *a: pytest.fail('must reject before consuming old annotations'))
    with pytest.raises(rp.StepError, match='requires annotations recorded'):
        consumer(cfg, paths, rp.Flags())


@pytest.mark.parametrize('bad_value', ['false', 0, None])
def test_visibility_setting_rejects_non_boolean_values(tmp_path, bad_value):
    cfg, _ = setup_run(tmp_path, [])
    cfg.annotation['include_regulators'] = bad_value
    with pytest.raises(ValueError, match='true or false'):
        cfg.__post_init__()


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
