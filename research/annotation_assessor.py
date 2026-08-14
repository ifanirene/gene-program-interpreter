"""Compare paired baseline and candidate biological annotations with model assessors."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from research import model_assessor


RELATIONS = {
    "same_core_interpretation",
    "same_core_with_meaningful_refinement",
    "materially_changed",
    "incomparable",
}
EVIDENCE_DEPTH = {
    "no_material_change",
    "more_gene_specific",
    "more_context_specific",
    "mixed",
}


class AnnotationAssessmentError(ValueError):
    """A paired annotation assessment is missing or internally inconsistent."""


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def compact_annotation(result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep reader-facing annotation content and omit citation-detail payloads."""
    mechanisms = []
    for mechanism in result.get("candidate_mechanisms") or []:
        mechanisms.append(
            {
                "name": mechanism.get("name"),
                "summary": mechanism.get("summary"),
                "supporting_genes": list(mechanism.get("supporting_genes") or []),
                "supporting_regulators": list(
                    mechanism.get("supporting_regulators") or []
                ),
                "status": mechanism.get("status"),
            }
        )
    return {
        "agent_summary": result.get("agent_summary"),
        "candidate_mechanisms": mechanisms,
    }


def load_pairs(path: str | Path, *, repo_root: str | Path) -> list[dict[str, Any]]:
    root = Path(repo_root)
    payload = _read_json(path)
    rows = payload.get("pairs") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise AnnotationAssessmentError("pair manifest must contain a non-empty pairs list")
    result = []
    seen: set[str] = set()
    for row in rows:
        assessment_id = str(row.get("assessment_id") or "")
        if not assessment_id or assessment_id in seen:
            raise AnnotationAssessmentError("assessment IDs must be present and unique")
        seen.add(assessment_id)
        baseline_path = root / str(row["baseline_result"])
        candidate_path = root / str(row["candidate_result"])
        result.append(
            {
                "assessment_id": assessment_id,
                "session_id": str(row["session_id"]),
                "case_id": str(row["case_id"]),
                "program_id": str(row["program_id"]),
                "baseline": compact_annotation(_read_json(baseline_path)),
                "candidate": compact_annotation(_read_json(candidate_path)),
            }
        )
    return result


def _prompt(
    pair: Mapping[str, Any],
    *,
    assessor_id: str,
    prior_assessments: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    adjudication = prior_assessments is not None
    role = "independent adjudicator" if adjudication else "independent assessor"
    prior = (
        "\nTwo prior assessments are included. Resolve their decision-level disagreement from the "
        "paired annotations; do not vote or average them.\n"
        if adjudication
        else "\n"
    )
    payload = dict(pair)
    if prior_assessments is not None:
        payload["prior_assessments"] = list(prior_assessments)
    return (
        f"You are an {role} comparing two biological gene-program annotations. "
        f"Assessor ID: {assessor_id}.\n\n"
        "Question: did the candidate annotation change the substantive biological interpretation, "
        "or mostly add evidence coverage and detail?\n\n"
        "Rubric:\n"
        "- same_core_interpretation: the central mechanisms and biological interpretation are the "
        "same; differences are verbosity, citations, examples, or minor gene lists.\n"
        "- same_core_with_meaningful_refinement: the central interpretation is retained, but the "
        "candidate adds a meaningful submechanism, gene-function assignment, context qualifier, or "
        "removes/weakens a prior conclusion.\n"
        "- materially_changed: at least one central program mechanism, direction, or biological "
        "interpretation changes enough that a reader could make a different biological decision.\n"
        "- incomparable: one annotation is missing or too incomplete for a responsible comparison.\n"
        "- Set core_interpretation_changed=true only for materially_changed.\n"
        "- Judge the annotation claims, not prose length. Ignore evidence-link implementation detail.\n"
        "- Use only the supplied paired annotations; do not browse or add outside knowledge.\n"
        "- Keep list items and rationale concise and concrete. Name mechanisms or genes when relevant.\n"
        f"- Set assessment_id to {pair['assessment_id']!r} and assessor_id to {assessor_id!r}.\n"
        f"{prior}"
        "Paired annotation payload:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def validate_assessment(
    value: Mapping[str, Any],
    *,
    assessment_id: str,
    assessor_id: str,
) -> dict[str, Any]:
    row = dict(value)
    row["assessment_id"] = assessment_id
    row["assessor_id"] = assessor_id
    relation = str(row.get("semantic_relation") or "")
    evidence_depth = str(row.get("evidence_depth_change") or "")
    if relation not in RELATIONS:
        raise AnnotationAssessmentError(f"{assessment_id}: invalid semantic relation")
    if evidence_depth not in EVIDENCE_DEPTH:
        raise AnnotationAssessmentError(f"{assessment_id}: invalid evidence-depth change")
    changed = row.get("core_interpretation_changed")
    if not isinstance(changed, bool):
        raise AnnotationAssessmentError(
            f"{assessment_id}: core_interpretation_changed must be boolean"
        )
    if changed != (relation == "materially_changed"):
        raise AnnotationAssessmentError(
            f"{assessment_id}: change boolean and semantic relation disagree"
        )
    for field in (
        "shared_core_conclusions",
        "candidate_additions",
        "candidate_removed_or_weakened",
        "decision_relevant_differences",
    ):
        values = row.get(field)
        if not isinstance(values, list) or not all(
            isinstance(item, str) and item.strip() for item in values
        ):
            raise AnnotationAssessmentError(f"{assessment_id}: invalid {field}")
        row[field] = [item.strip() for item in values]
    rationale = row.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise AnnotationAssessmentError(f"{assessment_id}: rationale is required")
    row["rationale"] = rationale.strip()
    return row


def _decision_disagrees(
    primary: Mapping[str, Any],
    secondary: Mapping[str, Any],
) -> bool:
    return (
        primary["semantic_relation"] != secondary["semantic_relation"]
        or primary["core_interpretation_changed"]
        != secondary["core_interpretation_changed"]
    )


def run_assessments(args: argparse.Namespace) -> dict[str, Any]:
    pairs = load_pairs(args.pairs, repo_root=args.repo_root)
    schema = _read_json(args.schema)
    primary_map: dict[str, Any] = {}
    secondary_map: dict[str, Any] = {}
    if args.mode == "adjudication":
        primary_map = {
            row["assessment_id"]: row for row in _read_json(args.primary)["assessments"]
        }
        secondary_map = {
            row["assessment_id"]: row for row in _read_json(args.secondary)["assessments"]
        }
        pairs = [
            pair
            for pair in pairs
            if _decision_disagrees(
                primary_map[pair["assessment_id"]],
                secondary_map[pair["assessment_id"]],
            )
        ]

    shard_root = Path(args.out).parent / "shards" / args.assessor_id

    def complete(pair: Mapping[str, Any]) -> dict[str, Any]:
        assessment_id = str(pair["assessment_id"])
        shard = shard_root / f"{assessment_id}.json"
        if shard.is_file() and not args.overwrite:
            return _read_json(shard)
        priors = (
            [primary_map[assessment_id], secondary_map[assessment_id]]
            if args.mode == "adjudication"
            else None
        )
        prompt = _prompt(
            pair,
            assessor_id=args.assessor_id,
            prior_assessments=priors,
        )
        started = time.monotonic()
        if args.provider == "codex":
            structured, telemetry = model_assessor._invoke_codex(
                prompt,
                Path(args.schema),
                model=args.model,
                effort=args.effort,
                timeout=args.timeout,
                executable=args.codex_executable,
            )
        else:
            structured, telemetry = model_assessor._invoke_claude(
                prompt,
                schema,
                model=args.model,
                effort=args.effort,
                timeout=args.timeout,
            )
        assessment = validate_assessment(
            structured,
            assessment_id=assessment_id,
            assessor_id=args.assessor_id,
        )
        result = {
            "schema_version": 1,
            "assessment": assessment,
            "telemetry": {
                **telemetry,
                "wall_seconds": round(time.monotonic() - started, 3),
            },
        }
        _write_json(shard, result)
        print(f"completed {args.assessor_id} {assessment_id}", flush=True)
        return result

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(complete, pair) for pair in pairs]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    order = {pair["assessment_id"]: index for index, pair in enumerate(pairs)}
    results.sort(key=lambda row: order[row["assessment"]["assessment_id"]])
    costs = [
        float(row["telemetry"]["cost_usd_equivalent"])
        for row in results
        if row["telemetry"].get("cost_usd_equivalent") is not None
    ]
    output = {
        "schema_version": 1,
        "assessment_type": "model",
        "mode": args.mode,
        "assessor_id": args.assessor_id,
        "assessments": [row["assessment"] for row in results],
        "telemetry": {
            "provider": args.provider,
            "model": args.model,
            "reasoning_effort": args.effort,
            "configured_concurrency": args.concurrency,
            "shards": len(results),
            "known_cost_usd_equivalent": round(sum(costs), 6) if costs else None,
            "known_duration_seconds": round(
                sum(float(row["telemetry"].get("wall_seconds") or 0) for row in results),
                3,
            ),
            "cost_telemetry_shards": len(costs),
            "unknown_cost_shards": len(results) - len(costs),
        },
    }
    _write_json(args.out, output)
    return output


def finalize_assessments(
    pairs: Sequence[Mapping[str, Any]],
    primary: Mapping[str, Any],
    secondary: Mapping[str, Any],
    adjudications: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    primary_map = {row["assessment_id"]: row for row in primary["assessments"]}
    secondary_map = {row["assessment_id"]: row for row in secondary["assessments"]}
    adjudication_map = {
        row["assessment_id"]: row for row in adjudications.get("assessments") or []
    }
    expected = {str(pair["assessment_id"]) for pair in pairs}
    if set(primary_map) != expected or set(secondary_map) != expected:
        raise AnnotationAssessmentError("primary and secondary must cover every pair")
    disagreements = {
        assessment_id
        for assessment_id in expected
        if _decision_disagrees(primary_map[assessment_id], secondary_map[assessment_id])
    }
    if set(adjudication_map) != disagreements:
        raise AnnotationAssessmentError(
            "adjudications must exactly cover decision-level disagreements"
        )
    final_map = dict(primary_map)
    final_map.update(adjudication_map)
    ordered = [final_map[str(pair["assessment_id"])] for pair in pairs]
    relation_agreement = sum(
        primary_map[assessment_id]["semantic_relation"]
        == secondary_map[assessment_id]["semantic_relation"]
        for assessment_id in expected
    )
    decision_agreement = len(expected) - len(disagreements)
    reliability = {
        "schema_version": 1,
        "assessment_type": "model",
        "double_scored_count": len(expected),
        "semantic_relation_agreement_count": relation_agreement,
        "semantic_relation_agreement_rate": round(
            relation_agreement / len(expected), 6
        ),
        "decision_agreement_count": decision_agreement,
        "decision_agreement_rate": round(decision_agreement / len(expected), 6),
        "disagreement_count": len(disagreements),
        "adjudicated_count": len(adjudication_map),
        "disagreement_ids": sorted(disagreements),
    }
    return ordered, reliability


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--pairs", required=True, type=Path)
    run.add_argument("--repo-root", default=Path("."), type=Path)
    run.add_argument("--schema", required=True, type=Path)
    run.add_argument("--out", required=True, type=Path)
    run.add_argument("--mode", choices=("independent", "adjudication"), default="independent")
    run.add_argument("--assessor-id", required=True)
    run.add_argument("--provider", choices=("claude", "codex"), required=True)
    run.add_argument("--model", required=True)
    run.add_argument(
        "--effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default="high",
    )
    run.add_argument("--concurrency", type=int, default=6)
    run.add_argument("--timeout", type=int, default=900)
    run.add_argument("--codex-executable", default=model_assessor.DEFAULT_CODEX_EXECUTABLE)
    run.add_argument("--primary", type=Path)
    run.add_argument("--secondary", type=Path)
    run.add_argument("--overwrite", action="store_true")

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--pairs", required=True, type=Path)
    finalize.add_argument("--repo-root", default=Path("."), type=Path)
    finalize.add_argument("--primary", required=True, type=Path)
    finalize.add_argument("--secondary", required=True, type=Path)
    finalize.add_argument("--adjudications", required=True, type=Path)
    finalize.add_argument("--out-final", required=True, type=Path)
    finalize.add_argument("--out-reliability", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        if args.concurrency <= 0 or args.timeout <= 0:
            raise SystemExit("concurrency and timeout must be positive")
        if args.mode == "adjudication" and (not args.primary or not args.secondary):
            raise SystemExit("adjudication requires --primary and --secondary")
        result = run_assessments(args)
        print(json.dumps(result["telemetry"], indent=2, sort_keys=True))
        return 0
    pairs = load_pairs(args.pairs, repo_root=args.repo_root)
    final, reliability = finalize_assessments(
        pairs,
        _read_json(args.primary),
        _read_json(args.secondary),
        _read_json(args.adjudications),
    )
    _write_json(
        args.out_final,
        {
            "schema_version": 1,
            "assessment_type": "model",
            "assessments": final,
        },
    )
    _write_json(args.out_reliability, reliability)
    print(
        json.dumps(
            {
                "final_assessments": len(final),
                **reliability,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
