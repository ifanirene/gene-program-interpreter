"""Tests for the isolated model-assessed reference-quality audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research import reference_quality


def _fixture() -> tuple[dict, dict]:
    cases = []
    items = []
    index_links = []
    definitions = [
        ("case-p1--r1", "RL-1", "paper-1", "Gene A promotes repair.", "partial", "PM1"),
        ("case-p1--r2", "RL-2", "paper-2", "Gene B blocks repair.", "direct", "PM2"),
    ]
    for session_id, review_id, paper_key, text, declared, pmid in definitions:
        cases.append(
            {
                "case_id": "case-p1",
                "session_id": session_id,
                "program_id": "P1",
                "core_genes": ["GeneA", "GeneB"],
                "regulators": ["Reg1"],
                "context": {"organism": "mouse", "tissue": "brain", "cell_type": "endothelial"},
                "mechanisms": [],
            }
        )
        items.append(
            {
                "review_id": review_id,
                "session_id": session_id,
                "case_id": "case-p1",
                "program_id": "P1",
                "mechanism_index": 1,
                "mechanism_name": "Repair",
                "mechanism_summary": "Repair mechanism",
                "supporting_genes": ["GeneA", "GeneB"],
                "supporting_regulators": [],
                "paper_key": paper_key,
                "paper": {"pmid": pmid, "title": f"Paper {pmid}"},
                "assessor_text": {"text": text, "text_type": "abstract"},
                "selection_reason": "Selected for repair",
                "required_assessors": 1,
            }
        )
        index_links.append(
            {
                "session_id": session_id,
                "mechanism_index": 1,
                "paper_key": paper_key,
                "evidence": {"context_match": declared},
            }
        )
    return (
        {"schema_version": 1, "review_cases": cases, "review_items": items},
        {"schema_version": 1, "links": index_links},
    )


def _review(item: dict, *, gene: str, assessed_context: str, accuracy: str) -> dict:
    text = item["assessor_text"]["text"]
    evidence = text[: text.index(".") + 1]
    return {
        "review_id": item["review_id"],
        "assessor_id": "model-a",
        "support": "supports",
        "studied_genes": [gene],
        "function_supported_genes": [gene],
        "directness": "causal",
        "direction": "matches",
        "assessed_context": assessed_context,
        "context_label_accuracy": accuracy,
        "evidence_span": {
            "text": evidence,
            "start": 0,
            "end": len(evidence),
            "source_type": "abstract",
        },
        "rationale": "The abstract directly reports the assigned function.",
        "red_flags": [],
    }


def test_prepare_review_tasks_deduplicates_payload_and_joins_declared_context(tmp_path):
    packet, index = _fixture()
    packet_path = tmp_path / "review" / "review_packet.json"
    index_path = tmp_path / "assessor_text" / "index.json"
    packet_path.parent.mkdir(parents=True)
    index_path.parent.mkdir(parents=True)
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    index_path.write_text(json.dumps(index), encoding="utf-8")

    manifest = reference_quality.prepare_review_tasks(packet_path, tmp_path / "tasks")

    assert manifest["total_review_links"] == 2
    assert len(manifest["sessions"]) == 2
    task = json.loads((tmp_path / "tasks" / "case-p1--r1.json").read_text())
    assert task["session"]["supplied_genes"] == ["GeneA", "GeneB"]
    assert task["links"][0]["declared_context"] == "partial"
    assert list(task["papers"]) == ["paper-1"]
    assert task["response_template"] == reference_quality.review_response_template()

    with pytest.raises(reference_quality.ReferenceQualityError, match="refusing to overwrite"):
        reference_quality.prepare_review_tasks(packet_path, tmp_path / "tasks")


def test_validate_review_requires_supplied_gene_and_exact_positive_span():
    packet, index = _fixture()
    item = packet["review_items"][0]
    valid = _review(item, gene="genea", assessed_context="indirect", accuracy="overclaimed")

    normalized = reference_quality.validate_reviews(
        packet, [valid], assessor_index_path=index, require_complete=False
    )
    assert normalized[0]["studied_genes"] == ["GeneA"]

    bad_gene = dict(valid, studied_genes=["Reg1"], function_supported_genes=[])
    with pytest.raises(reference_quality.ReferenceQualityError, match="non-supplied gene"):
        reference_quality.validate_reviews(
            packet, [bad_gene], assessor_index_path=index, require_complete=False
        )

    bad_span = dict(valid, evidence_span=dict(valid["evidence_span"], text="not cached"))
    with pytest.raises(reference_quality.ReferenceQualityError, match="exact cached substring"):
        reference_quality.validate_reviews(
            packet, [bad_span], assessor_index_path=index, require_complete=False
        )

    no_span = dict(
        valid,
        evidence_span={"text": "", "start": None, "end": None, "source_type": ""},
    )
    with pytest.raises(reference_quality.ReferenceQualityError, match="requires an evidence span"):
        reference_quality.validate_reviews(
            packet, [no_span], assessor_index_path=index, require_complete=False
        )


def test_validate_review_enforces_context_calibration_and_red_flag_vocabulary():
    packet, index = _fixture()
    item = packet["review_items"][0]
    valid = _review(item, gene="GeneA", assessed_context="indirect", accuracy="overclaimed")

    inaccurate = dict(valid, context_label_accuracy="accurate")
    with pytest.raises(reference_quality.ReferenceQualityError, match="must be 'overclaimed'"):
        reference_quality.validate_reviews(
            packet, [inaccurate], assessor_index_path=index, require_complete=False
        )

    unknown_flag = dict(valid, red_flags=["made_up"])
    with pytest.raises(reference_quality.ReferenceQualityError, match="invalid red_flags"):
        reference_quality.validate_reviews(
            packet, [unknown_flag], assessor_index_path=index, require_complete=False
        )


def test_aggregate_coverage_repeat_jaccards_and_portable_outputs(tmp_path):
    packet, index = _fixture()
    reviews = [
        _review(
            packet["review_items"][0],
            gene="GeneA",
            assessed_context="indirect",
            accuracy="overclaimed",
        ),
        _review(
            packet["review_items"][1],
            gene="GeneB",
            assessed_context="direct",
            accuracy="accurate",
        ),
    ]
    normalized = reference_quality.validate_reviews(
        packet, reviews, assessor_index_path=index, require_complete=True
    )
    metrics = reference_quality.aggregate_reviews(
        packet, normalized, assessor_index_path=index
    )

    assert metrics["assessment_type"] == "model"
    assert [row["citation_coverage"] for row in metrics["per_session"]] == [0.5, 0.5]
    assert [row["function_supported_coverage"] for row in metrics["per_session"]] == [0.5, 0.5]
    assert metrics["aggregate"]["context_overclaim_rate"] == 0.5
    repeat = metrics["repeat_run_stability"][0]
    assert repeat["citation_jaccard"] == 0.0
    assert repeat["covered_gene_jaccard"] == 0.0
    assert repeat["function_supported_gene_jaccard"] == 0.0

    paths = reference_quality.write_report_outputs(metrics, tmp_path / "out")
    assert all(Path(path).is_file() for path in paths.values())
    artifact = json.loads((tmp_path / "out" / "artifact.json").read_text())
    assert artifact["surface"] == "report"
    assert artifact["manifest"]["charts"][0]["type"] == "horizontalBar"
    blocks = artifact["manifest"]["blocks"]
    assert blocks[0]["body"] == "# Reference quality audit"
    assert any(block["type"] == "metric-strip" for block in blocks)
    assert any(block["type"] == "chart" for block in blocks)
    assert any(block["type"] == "table" for block in blocks)
    bodies = "\n".join(block.get("body", "") for block in blocks)
    assert "## Technical summary" in bodies
    assert "## Limitations and robustness" in bodies
    assert all(not Path(source["path"]).is_absolute() for source in artifact["sources"])
    assert artifact["snapshot"]["datasets"]["session_coverage"][0]["citationCoverage"] == 0.5
    assert all(source["path"].endswith(".sql") for source in artifact["sources"])


def test_finalize_review_sets_requires_independent_adjudication():
    packet, index = _fixture()
    packet["review_items"][0]["required_assessors"] = 2
    primary = [
        _review(
            packet["review_items"][0],
            gene="GeneA",
            assessed_context="indirect",
            accuracy="overclaimed",
        ),
        _review(
            packet["review_items"][1],
            gene="GeneB",
            assessed_context="direct",
            accuracy="accurate",
        ),
    ]
    secondary_review = dict(primary[0], assessor_id="model-b", support="partial")
    adjudication = dict(primary[0], assessor_id="model-c")

    final, reliability = reference_quality.finalize_review_sets(
        packet,
        primary,
        [secondary_review],
        [adjudication],
        assessor_index_path=index,
    )

    assert len(final) == 2
    assert final[0]["assessor_id"] == "model-c"
    assert reliability["double_scored_count"] == 1
    assert reliability["disagreement_count"] == 1
    assert reliability["adjudicated_count"] == 1
    assert reliability["field_agreement_rates"]["support"] == 0.0
    metrics = reference_quality.aggregate_reviews(packet, final, assessor_index_path=index)
    metrics["reliability"] = reliability
    artifact = reference_quality.build_portable_artifact(metrics, generated_at="2026-07-22T00:00:00Z")
    assert "reliability-card" in artifact["manifest"]["blocks"][4]["cardIds"]
    assert any(table["id"] == "reliability-table" for table in artifact["manifest"]["tables"])

    with pytest.raises(reference_quality.ReferenceQualityError, match="adjudications must exactly"):
        reference_quality.finalize_review_sets(
            packet,
            primary,
            [secondary_review],
            [],
            assessor_index_path=index,
        )


def test_finalize_review_sets_supports_full_secondary_with_fixed_sample_reliability():
    packet, index = _fixture()
    packet["review_items"][0]["required_assessors"] = 2
    primary = [
        _review(
            packet["review_items"][0],
            gene="GeneA",
            assessed_context="indirect",
            accuracy="overclaimed",
        ),
        _review(
            packet["review_items"][1],
            gene="GeneB",
            assessed_context="direct",
            accuracy="accurate",
        ),
    ]
    secondary = [
        dict(primary[0], assessor_id="model-b", support="partial"),
        dict(primary[1], assessor_id="model-b"),
    ]
    adjudication = dict(primary[0], assessor_id="model-c")

    final, reliability = reference_quality.finalize_review_sets(
        packet,
        primary,
        secondary,
        [adjudication],
        assessor_index_path=index,
        secondary_scope="all",
    )

    assert len(final) == 2
    assert reliability["secondary_scope"] == "all"
    assert reliability["double_scored_count"] == 1
    assert reliability["disagreement_count"] == 1
    assert reliability["expanded_full_review"]["double_scored_count"] == 2
    assert reliability["expanded_full_review"]["disagreement_count"] == 1
