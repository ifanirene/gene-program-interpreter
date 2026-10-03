import json

import pandas as pd
import pytest

from gpi.annotation_contract import parse_annotation, render_annotation
from gpi.annotation_validation import finalize_regulator_annotations
from gpi.evidence_context import generate_prompt, PROMPT_TEMPLATE
from gpi.context_profile import ContextProfile
from gpi.html_report import parse_regulators_detailed, split_final_modules


def payload():
    return dict(
        program_id=1,
        label="Nitrogen handling",
        brief_summary="Glul and Rhbg support nitrogen handling.",
        overview="GLUL and RHBG form the leading pattern.",
        modules=[
            dict(
                name="Nitrogen handling",
                summary="Glul and Rhbg support nitrogen handling.",
                key_genes=["GLUL", "RHBG"],
                pmids=[],
                evidence="Program genes.",
            )
        ],
        distinctive="Rhbg contributes ammonia transport.",
        regulators=[
            dict(
                gene="MLXIPL",
                confidence="Low",
                mechanism="MLXIPL may influence nutrient feedback; the mechanism is unresolved.",
            )
        ],
    )


def inputs():
    return {
        "female": {
            1: pd.DataFrame(
                [
                    dict(
                        target_gene="Mlxipl",
                        grna_target="Mlxipl_1",
                        program_id=1,
                        log_2_fold_change=0.9,
                        adj_p_value=0.001,
                        p_value=0.001,
                        significant=True,
                    )
                ]
            )
        }
    }


def test_contract_roundtrip_restores_case_and_measured_direction(tmp_path):
    a = parse_annotation(json.dumps(payload()), 1)
    path = tmp_path / "topic_1_annotation.md"
    path.write_text(render_annotation(a))
    path.with_suffix(".json").write_text(a.model_dump_json())
    finalize_regulator_annotations(tmp_path, inputs(), {1: ["Glul", "Rhbg"]})
    text = path.read_text()
    assert "MLXIPL" not in text and "GLUL" not in text
    cards = parse_regulators_detailed(text)
    assert len(cards) == 1 and cards[0]["gene"] == "Mlxipl" and cards[0]["role"] == "repressor"
    assert "female: +0.900 (significant)" in cards[0]["fc"]
    assert len(split_final_modules(text)[1]) == 1
    assert split_final_modules(text)[1][0]["key_genes"] == ["Glul", "Rhbg"]


def test_invalid_schema_or_program_id_fails():
    p = payload()
    p["extra_table"] = "wrong"
    with pytest.raises(ValueError):
        parse_annotation(json.dumps(p), 1)
    with pytest.raises(ValueError):
        parse_annotation(json.dumps(payload()), 2)


def test_commentary_wrapped_json_retains_all_contract_checks():
    wrapped = lambda p: "Reviewed the supplied evidence.\n```json\n" + json.dumps(p) + "\n```"
    assert parse_annotation(wrapped(payload()), 1).label == "Nitrogen handling"
    p = payload()
    p["modules"] *= 4
    with pytest.raises(ValueError, match="at most 3"):
        parse_annotation(wrapped(p), 1)
    with pytest.raises(ValueError):
        parse_annotation(wrapped(payload()) + "\n" + wrapped(payload()), 1)
    with pytest.raises(ValueError):
        parse_annotation(json.dumps(payload()) + "\n" + wrapped(payload()), 1)


def test_final_report_still_rejects_more_than_three_modules():
    p = payload()
    p["modules"] = [dict(p["modules"][0], name=f"Function {i}") for i in range(4)]
    with pytest.raises(ValueError, match="at most 3"):
        parse_annotation(json.dumps(p), 1)


@pytest.mark.parametrize("change", ["direction", "missing", "nonmember", "mask"])
def test_live_failure_modes_block_publication(tmp_path, change):
    p = payload()
    if change == "direction":
        p["regulators"][0]["mechanism"] = "Mlxipl knockdown reduces program activity."
    if change == "missing":
        p["regulators"] = []
    if change == "nonmember":
        p["modules"][0]["key_genes"] = ["Glul", "Mttp"]
    if change == "mask":
        p["overview"] = "Dgat2 suggests a different pathway."
    a = parse_annotation(json.dumps(p), 1)
    path = tmp_path / "topic_1_annotation.md"
    path.write_text(render_annotation(a))
    path.with_suffix(".json").write_text(a.model_dump_json())
    with pytest.raises(ValueError, match="validation failed"):
        finalize_regulator_annotations(tmp_path, inputs(), {1: ["Glul", "Rhbg"]}, ["Dgat2"])


def test_final_prompt_masks_supporting_channels():
    genes = pd.DataFrame(dict(Name=["Glul", "Rhbg"], Score=[2.0, 1.0], program_id=[1, 1]))
    # Injected text uses real prompt substitutions, not an empty support context.
    template = PROMPT_TEMPLATE + "\nSTRING partner: DGAT2. Enrichment genes: Insig1, Glul."
    prompt = generate_prompt(
        1,
        genes,
        template,
        2,
        0,
        {},
        {},
        {},
        7,
        10,
        ContextProfile.liver_demo(),
        masked_regulators=["Dgat2", "Insig1"],
    )
    assert "dgat2" not in prompt.casefold() and "insig1" not in prompt.casefold()
    assert "Glul" in prompt


def test_legacy_alias_header_and_nested_effect_parentheses_parse():
    text = """## Regulator analysis

```
Mlxipl / ChREBP (repressor, log2FC=female: +0.900 (significant)): [Confidence: Low]
Mechanistic hypothesis: Feedback is possible.
```
"""
    card = parse_regulators_detailed(text)[0]
    assert card["gene"] == "Mlxipl"
    assert card["fc"] == "female: +0.900 (significant)"


def test_missing_or_truncated_response_cannot_reuse_stale_output(tmp_path):
    from gpi.annotation_contract import validate_batch_contract

    req = tmp_path / "requests.json"
    req.write_text(json.dumps({"requests": [{"custom_id": "topic_1"}]}))
    result = tmp_path / "results.jsonl"
    result.write_text("")
    with pytest.raises(ValueError, match="Missing annotation"):
        validate_batch_contract(req, result)
    result.write_text(
        json.dumps(
            dict(
                custom_id="topic_1",
                result=dict(
                    type="succeeded",
                    message=dict(
                        stop_reason="max_tokens",
                        content=[dict(type="text", text=json.dumps(payload()))],
                    ),
                ),
            )
        )
    )
    with pytest.raises(ValueError, match="Incomplete annotation"):
        validate_batch_contract(req, result)


def test_functional_research_and_synthesis_are_independent_of_regulator_inputs():
    from gpi.gene_first_synthesis import functional_bundle, core_requests

    bundle = dict(
        program_id="P1",
        organism="mouse",
        tissue="liver",
        cell_type="hepatocyte",
        program_genes=["Glul"],
        distinctive_genes=["Rhbg"],
        perturbation_regulators={"female": [{"gene": "Mlxipl"}]},
    )
    before = functional_bundle(bundle)
    bundle["perturbation_regulators"] = {"female": [{"gene": "CompletelyDifferent"}]}
    bundle["regulator_effects_all_conditions"] = [{"gene": "Mttp"}]
    assert functional_bundle(bundle) == before

    def request(gene):
        return {
            "requests": [
                {
                    "custom_id": "topic_1",
                    "params": {
                        "messages": [
                            {
                                "role": "user",
                                "content": "Program genes Glul Rhbg\n#### Regulator perturbation evidence\n"
                                + gene
                                + "\n### Interpretation rules\nKeep genes first.",
                            }
                        ]
                    },
                }
            ]
        }

    assert core_requests(request("Mlxipl")) == core_requests(request("Mttp"))


def test_regulator_supplement_cannot_change_functional_fields(tmp_path):
    from gpi.gene_first_synthesis import merge_supplements

    p = payload()
    p["regulators"] = []
    core = tmp_path / "core.jsonl"
    supp = tmp_path / "supp.jsonl"
    out = tmp_path / "merged.jsonl"

    def result(value):
        return json.dumps(
            dict(
                custom_id="topic_1",
                result=dict(
                    type="succeeded",
                    message=dict(
                        stop_reason="end_turn", content=[dict(type="text", text=json.dumps(value))]
                    ),
                ),
            )
        )

    core.write_text(result(p))
    supp.write_text(result({"regulators": payload()["regulators"]}))
    merge_supplements(core, supp, out)
    merged = json.loads(json.loads(out.read_text())["result"]["message"]["content"][0]["text"])
    assert {k: v for k, v in merged.items() if k != "regulators"} == {
        k: v for k, v in p.items() if k != "regulators"
    }
    supp.write_text(result({"regulators": [], "label": "Hijacked identity"}))
    with pytest.raises(ValueError):
        merge_supplements(core, supp, out)


@pytest.mark.parametrize("invalid_core", [False, True])
def test_gene_first_annotation_skips_review_and_repair(tmp_path, monkeypatch, invalid_core):
    import gpi.run_pipeline as rp

    profile = ContextProfile(
        organism="mouse", species_taxid=10090, tissue="liver",
        cell_type="hepatocyte", interpretation_mode="gene_first",
    )
    cfg = rp.PipelineConfig(
        profile=profile, gene_loading=tmp_path / "genes.csv",
        regulators=None, regulators_by_condition={}, celltype_enrichment=None,
        output_dir=tmp_path, programs=[1], annotation={"batch": False},
    )
    paths = rp.Paths(tmp_path)
    (tmp_path / "program_bundles").mkdir()
    (tmp_path / "program_bundles" / "P1.json").write_text(
        json.dumps({"perturbation_regulators": {}, "regulator_effects_all_conditions": []})
    )
    calls = []

    class ReachedParser(Exception):
        pass

    def subprocess(argv, dry_run):
        if "gpi.evidence_context" in argv:
            paths.batch_request.write_text(json.dumps({"requests": [{
                "custom_id": "topic_1", "params": {"messages": [{
                    "role": "user", "content": "Program genes Glul Rhbg\n"
                    "#### Regulator perturbation evidence\nMlxipl\n"
                    "### Interpretation rules\nKeep genes first."
                }]}
            }]}))
        elif "gpi.anthropic_live" in argv:
            calls.append(json.loads(paths.batch_request.read_text()))
            if len(calls) == 1:
                value = payload()
                value["regulators"] = []
                if invalid_core:
                    value["label"] = "one two three four five six seven"
            else:
                value = {"regulators": []}
            paths.batch_results.write_text(json.dumps({
                "custom_id": "topic_1", "result": {"type": "succeeded", "message": {
                    "stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(value)}]
                }}
            }) + "\n")
        elif "gpi.parse_results" in argv:
            raise ReachedParser()

    monkeypatch.setattr(rp, "_run_subprocess", subprocess)
    with pytest.raises(ValueError if invalid_core else ReachedParser):
        rp.run_annotate(cfg, paths, rp.Flags())
    assert len(calls) == 1  # No selected regulators: no empty supplement call.
    assert not list(tmp_path.glob("functional_identity_review_*"))
    assert not list(tmp_path.glob("annotation_contract_repair_*"))
    if not invalid_core:
        assert (tmp_path / "functional_annotation_results.jsonl").exists()


def test_module_gene_list_has_no_upper_limit():
    p = payload()
    p["modules"][0]["key_genes"] = [f"Gene{i}" for i in range(14)]
    assert len(parse_annotation(json.dumps(p), 1).modules[0].key_genes) == 14


def test_primary_request_enforces_json_shape_without_gene_cap():
    from gpi.evidence_context import build_annotation_requests

    genes = pd.DataFrame(dict(Name=["Glul", "Rhbg"], Score=[2.0, 1.0], program_id=[1, 1]))
    request = build_annotation_requests([1], genes, ContextProfile.liver_demo())[0]
    fmt = request["params"]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    label_schema = fmt["schema"]["properties"]["label"]
    assert "six" in label_schema["description"]
    assert "pattern" not in label_schema  # Rejected by the live schema compiler.
    for count in range(1, 7):
        value = payload()
        value["label"] = " ".join(["word"] * count)
        assert parse_annotation(json.dumps(value), 1).label == value["label"]
    value["label"] = "one two three four five six seven"
    with pytest.raises(ValueError, match="exceeds six words"):
        parse_annotation(json.dumps(value), 1)
    value["label"] = "  \t "
    with pytest.raises(ValueError, match="at least one word"):
        parse_annotation(json.dumps(value), 1)
    assert "maxItems" not in fmt["schema"]["$defs"]["Module"]["properties"]["key_genes"]


def test_mechanism_request_omits_numeric_effects_and_biased_research_candidates(tmp_path):
    from gpi.gene_first_synthesis import supplement_requests

    support = tmp_path / "research_results"
    support.mkdir(parents=True)
    (tmp_path / "program_bundles").mkdir()
    (tmp_path / "program_bundles" / "P1.json").write_text(
        json.dumps(
            {
                "perturbation_regulators": {"female": [{"gene": "Mlxipl"}]},
                "regulator_effects_all_conditions": [
                    {
                        "gene": "Mlxipl",
                        "condition": "female",
                        "available": True,
                        "significant": True,
                        "log2fc": 0.987654,
                    }
                ],
            }
        )
    )
    (support / "P1.json").write_text(
        json.dumps(
            {
                "regulator_coverage": [],
                "candidate_mechanisms": [{"name": "Biased candidate"}],
                "evidence": [],
            }
        )
    )
    p = payload()
    p["regulators"] = []
    core = tmp_path / "core.jsonl"
    core.write_text(
        json.dumps(
            dict(
                custom_id="topic_1",
                result=dict(
                    type="succeeded", message=dict(content=[dict(type="text", text=json.dumps(p))])
                ),
            )
        )
    )
    req = {
        "requests": [
            {
                "custom_id": "topic_1",
                "params": {
                    "messages": [{"role": "user", "content": "original numeric context .987654"}]
                },
            }
        ]
    }
    content = supplement_requests(req, core, support)["requests"][0]["params"]["messages"][0][
        "content"
    ]
    assert ".987654" not in content and "Biased candidate" not in content
    assert "Mlxipl" in content and "increased" in content


def test_unsupplied_module_citation_blocks_publication(tmp_path):
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    sources = tmp_path / "research_results"
    sources.mkdir()
    (sources / "P1.json").write_text(
        json.dumps({"candidate_mechanisms": [{"name": "Nitrogen"}], "evidence": [{"pmid": "123"}]})
    )
    p = payload()
    p["modules"][0]["pmids"] = ["999"]
    a = parse_annotation(json.dumps(p), 1)
    path = annotations / "topic_1_annotation.md"
    path.write_text(render_annotation(a))
    path.with_suffix(".json").write_text(a.model_dump_json())
    with pytest.raises(ValueError, match="validation failed"):
        finalize_regulator_annotations(annotations, inputs(), {1: ["Glul", "Rhbg"]})


def test_structured_report_never_reintroduces_discarded_citations_by_rank(tmp_path):
    from gpi.html_report import generate_report

    ann = tmp_path / "annotations"
    ann.mkdir()
    enr = tmp_path / "enrichment"
    enr.mkdir()
    research = tmp_path / "research"
    research.mkdir()
    a = parse_annotation(json.dumps(payload()), 1)
    (ann / "topic_1_annotation.md").write_text(render_annotation(a))
    (ann / "topic_1_annotation.json").write_text(a.model_dump_json())
    (research / "P1.json").write_text(
        json.dumps(
            {
                "modules": [
                    {
                        "title": "Discarded different theme",
                        "status": "supported",
                        "pmids": ["99999999"],
                    }
                ],
                "evidence_gaps": ["PRIVATE_GAP_SENTINEL"],
                "contradictions": ["PRIVATE_CONTRADICTION_SENTINEL"],
                "evidence": [{"context_match": "indirect", "relevance_note": "PRIVATE_NOTE_SENTINEL"}],
            }
        )
    )
    (tmp_path / "summary.csv").write_text("Topic,Name\n1,Nitrogen handling\n")
    (tmp_path / "genes.csv").write_text("Name,Score,RowID\nGlul,2,1\nRhbg,1,1\n")
    out = tmp_path / "report.html"
    generate_report(
        summary_csv=str(tmp_path / "summary.csv"),
        annotations_dir=str(ann),
        enrichment_dir=str(enr),
        volcano_csv=None,
        volcano_condition_csvs=None,
        gene_loading_csv=str(tmp_path / "genes.csv"),
        output_html=str(out),
        research_results_dir=str(research),
    )
    programs = json.JSONDecoder().raw_decode(out.read_text().split("window.PROGRAMS = ", 1)[1])[0]
    assert programs["1"]["modules"][0]["pmids"] == []
    assert "evidence_gaps" not in programs["1"]
    assert "contradictions" not in programs["1"]
    assert "PRIVATE_" not in out.read_text()
