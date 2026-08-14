"""Score unordered three-module annotation similarity with independent model assessors."""

from __future__ import annotations

import argparse
import concurrent.futures
import itertools
import json
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from research import model_assessor


RELATIONSHIPS = {
    "same_core",
    "meaningful_refinement",
    "partial_overlap",
    "context_only",
    "different",
    "opposed",
}


class ModuleSimilarityError(ValueError):
    """A module-similarity assessment is missing or malformed."""


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _compact_modules(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    modules = []
    for index, mechanism in enumerate(result.get("candidate_mechanisms") or [], start=1):
        modules.append(
            {
                "module": index,
                "name": mechanism.get("name"),
                "summary": mechanism.get("summary"),
                "supporting_genes": list(mechanism.get("supporting_genes") or []),
                "supporting_regulators": list(
                    mechanism.get("supporting_regulators") or []
                ),
            }
        )
    if not 1 <= len(modules) <= 3:
        raise ModuleSimilarityError(
            f"expected one to three candidate mechanisms, found {len(modules)}"
        )
    while len(modules) < 3:
        modules.append(
            {
                "module": len(modules) + 1,
                "name": "(no module reported)",
                "summary": "",
                "supporting_genes": [],
                "supporting_regulators": [],
                "present": False,
            }
        )
    return modules


def load_comparisons(
    pair_manifest: str | Path,
    *,
    repo_root: str | Path,
) -> list[dict[str, Any]]:
    """Create replicate and cross-setting comparisons from the paired-session manifest."""
    root = Path(repo_root)
    payload = _read_json(pair_manifest)
    pairs = list(payload.get("pairs") or [])
    if not pairs:
        raise ModuleSimilarityError("pair manifest must contain pairs")

    sessions: dict[str, dict[str, Any]] = {}
    by_case: dict[str, list[str]] = defaultdict(list)
    for pair in pairs:
        session_id = str(pair["session_id"])
        case_id = str(pair["case_id"])
        sessions[session_id] = {
            "session_id": session_id,
            "case_id": case_id,
            "program_id": str(pair["program_id"]),
            "baseline": _compact_modules(
                _read_json(root / str(pair["baseline_result"]))
            ),
            "candidate": _compact_modules(
                _read_json(root / str(pair["candidate_result"]))
            ),
        }
        by_case[case_id].append(session_id)

    comparisons = []
    for case_id in sorted(by_case):
        session_ids = sorted(by_case[case_id])
        if len(session_ids) != 2:
            raise ModuleSimilarityError(
                f"{case_id}: expected exactly two replicate sessions"
            )
        left_session, right_session = (sessions[value] for value in session_ids)
        program_id = left_session["program_id"]
        for regime in ("baseline", "candidate"):
            comparisons.append(
                {
                    "comparison_id": f"{case_id}--{regime}-replicate",
                    "case_id": case_id,
                    "program_id": program_id,
                    "comparison_type": f"{regime}_replicate",
                    "left_label": f"{regime} r1",
                    "right_label": f"{regime} r2",
                    "left_modules": left_session[regime],
                    "right_modules": right_session[regime],
                }
            )
        for session in (left_session, right_session):
            replicate = session["session_id"].rsplit("--", 1)[-1]
            comparisons.append(
                {
                    "comparison_id": f"{case_id}--{replicate}--cross-setting",
                    "case_id": case_id,
                    "program_id": program_id,
                    "comparison_type": "cross_setting",
                    "left_label": f"baseline {replicate}",
                    "right_label": f"candidate {replicate}",
                    "left_modules": session["baseline"],
                    "right_modules": session["candidate"],
                }
            )
    return comparisons


def _prompt(comparison: Mapping[str, Any], *, assessor_id: str) -> str:
    return (
        "You are an independent biological annotation assessor. Compare two unordered sets of "
        "exactly three gene-program modules. Score the semantic similarity of every one of the "
        "nine possible module pairs; a deterministic optimizer will later choose the best "
        "one-to-one assignment. Do not choose the final assignment yourself.\n\n"
        "Judge biological meaning, not wording length or module order. Weight these features in "
        "descending importance: (1) core process/mechanism, (2) cellular or physiological output "
        "and predicted validation phenotype, (3) gene/regulator roles and direction, and (4) "
        "tissue/developmental/disease context. A shared broad tissue label alone is weak "
        "similarity. Different gene lists can still support the same module; overlapping genes do "
        "not make modules similar when their asserted mechanism or direction differs.\n\n"
        "Similarity anchors:\n"
        "- 1.00: effectively the same mechanism, output, and direction; only wording or minor "
        "detail differs.\n"
        "- 0.75: same core mechanism with a meaningful refinement, narrower scope, or added "
        "submechanism.\n"
        "- 0.50: same broad biological process but different central mechanism, output, or gene "
        "role.\n"
        "- 0.25: contextual or peripheral overlap only.\n"
        "- 0.00: unrelated or biologically opposed.\n"
        "A slot named '(no module reported)' represents a genuinely missing module. Every pair "
        "involving that slot must receive 0.00 and relationship='different'; this makes the final "
        "set score penalize missing modules.\n"
        "Use the continuous 0–1 range. Use relationship='opposed' when directions or functional "
        "interpretations conflict, even if genes overlap. Keep each rationale to one sentence. "
        "Use only the supplied annotations and do not browse.\n\n"
        f"Set comparison_id to {comparison['comparison_id']!r} and assessor_id to "
        f"{assessor_id!r}. Return all nine ordered cells exactly once.\n\n"
        "Comparison payload:\n"
        + json.dumps(comparison, ensure_ascii=False, separators=(",", ":"))
    )


def validate_assessment(
    value: Mapping[str, Any],
    *,
    comparison_id: str,
    assessor_id: str,
) -> dict[str, Any]:
    row = dict(value)
    row["comparison_id"] = comparison_id
    row["assessor_id"] = assessor_id
    cells = row.get("pair_scores")
    if not isinstance(cells, list) or len(cells) != 9:
        raise ModuleSimilarityError(f"{comparison_id}: expected nine pair scores")
    seen: set[tuple[int, int]] = set()
    normalized = []
    for cell in cells:
        if not isinstance(cell, Mapping):
            raise ModuleSimilarityError(f"{comparison_id}: invalid score cell")
        left = int(cell.get("left_module") or 0)
        right = int(cell.get("right_module") or 0)
        key = (left, right)
        if left not in {1, 2, 3} or right not in {1, 2, 3} or key in seen:
            raise ModuleSimilarityError(f"{comparison_id}: invalid module-pair grid")
        seen.add(key)
        similarity = cell.get("similarity")
        if not isinstance(similarity, (int, float)) or isinstance(similarity, bool):
            raise ModuleSimilarityError(f"{comparison_id}: similarity must be numeric")
        similarity = float(similarity)
        if not 0 <= similarity <= 1:
            raise ModuleSimilarityError(f"{comparison_id}: similarity outside 0–1")
        relationship = str(cell.get("relationship") or "")
        if relationship not in RELATIONSHIPS:
            raise ModuleSimilarityError(f"{comparison_id}: invalid relationship")
        rationale = str(cell.get("rationale") or "").strip()
        if not rationale:
            raise ModuleSimilarityError(f"{comparison_id}: rationale is required")
        normalized.append(
            {
                "left_module": left,
                "right_module": right,
                "similarity": round(similarity, 6),
                "relationship": relationship,
                "rationale": rationale,
            }
        )
    normalized.sort(key=lambda cell: (cell["left_module"], cell["right_module"]))
    if seen != set(itertools.product((1, 2, 3), repeat=2)):
        raise ModuleSimilarityError(f"{comparison_id}: incomplete module-pair grid")
    overall = str(row.get("overall_rationale") or "").strip()
    if not overall:
        raise ModuleSimilarityError(f"{comparison_id}: overall rationale is required")
    return {
        "comparison_id": comparison_id,
        "assessor_id": assessor_id,
        "pair_scores": normalized,
        "overall_rationale": overall,
    }


def optimal_assignment(
    pair_scores: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Maximize mean semantic similarity across the six possible 3×3 assignments."""
    score_map = {
        (int(cell["left_module"]), int(cell["right_module"])): float(
            cell["similarity"]
        )
        for cell in pair_scores
    }
    best: tuple[float, tuple[int, ...]] | None = None
    for permutation in itertools.permutations((1, 2, 3)):
        total = sum(score_map[(left, right)] for left, right in enumerate(permutation, 1))
        candidate = (total, permutation)
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    total, permutation = best
    matches = [
        {
            "leftModule": left,
            "rightModule": right,
            "similarity": round(score_map[(left, right)], 6),
        }
        for left, right in enumerate(permutation, 1)
    ]
    return {
        "moduleSemanticSimilarity": round(total / 3, 6),
        "matching": matches,
    }


def run_assessments(args: argparse.Namespace) -> dict[str, Any]:
    comparisons = load_comparisons(args.pairs, repo_root=args.repo_root)
    schema = _read_json(args.schema)
    shard_root = Path(args.out).parent / "shards" / args.assessor_id

    def complete(comparison: Mapping[str, Any]) -> dict[str, Any]:
        comparison_id = str(comparison["comparison_id"])
        shard = shard_root / f"{comparison_id}.json"
        if shard.is_file() and not args.overwrite:
            return _read_json(shard)
        prompt = _prompt(comparison, assessor_id=args.assessor_id)
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
            comparison_id=comparison_id,
            assessor_id=args.assessor_id,
        )
        assessment.update(optimal_assignment(assessment["pair_scores"]))
        result = {
            "schema_version": 1,
            "assessment": assessment,
            "telemetry": {
                **telemetry,
                "wall_seconds": round(time.monotonic() - started, 3),
            },
        }
        _write_json(shard, result)
        print(f"completed {args.assessor_id} {comparison_id}", flush=True)
        return result

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(complete, comparison) for comparison in comparisons]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    order = {
        comparison["comparison_id"]: index
        for index, comparison in enumerate(comparisons)
    }
    results.sort(key=lambda row: order[row["assessment"]["comparison_id"]])
    costs = [
        float(row["telemetry"]["cost_usd_equivalent"])
        for row in results
        if row["telemetry"].get("cost_usd_equivalent") is not None
    ]
    output = {
        "schema_version": 1,
        "assessment_type": "model",
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


def combine_assessments(
    comparisons: Sequence[Mapping[str, Any]],
    primary: Mapping[str, Any],
    secondary: Mapping[str, Any],
) -> dict[str, Any]:
    primary_map = {row["comparison_id"]: row for row in primary["assessments"]}
    secondary_map = {row["comparison_id"]: row for row in secondary["assessments"]}
    expected = {str(row["comparison_id"]) for row in comparisons}
    if set(primary_map) != expected or set(secondary_map) != expected:
        raise ModuleSimilarityError("both assessors must cover every comparison")
    comparison_map = {str(row["comparison_id"]): row for row in comparisons}
    rows = []
    for comparison in comparisons:
        comparison_id = str(comparison["comparison_id"])
        left = primary_map[comparison_id]
        right = secondary_map[comparison_id]
        left_cells = {
            (cell["left_module"], cell["right_module"]): cell
            for cell in left["pair_scores"]
        }
        right_cells = {
            (cell["left_module"], cell["right_module"]): cell
            for cell in right["pair_scores"]
        }
        averaged_cells = [
            {
                "left_module": key[0],
                "right_module": key[1],
                "similarity": round(
                    (
                        float(left_cells[key]["similarity"])
                        + float(right_cells[key]["similarity"])
                    )
                    / 2,
                    6,
                ),
            }
            for key in sorted(left_cells)
        ]
        combined = optimal_assignment(averaged_cells)
        left_score = float(left["moduleSemanticSimilarity"])
        right_score = float(right["moduleSemanticSimilarity"])
        left_mapping = [
            (match["leftModule"], match["rightModule"]) for match in left["matching"]
        ]
        right_mapping = [
            (match["leftModule"], match["rightModule"]) for match in right["matching"]
        ]
        source = comparison_map[comparison_id]
        matched = []
        for match in combined["matching"]:
            left_module = int(match["leftModule"])
            right_module = int(match["rightModule"])
            matched.append(
                {
                    **match,
                    "leftName": source["left_modules"][left_module - 1]["name"],
                    "rightName": source["right_modules"][right_module - 1]["name"],
                }
            )
        rows.append(
            {
                "comparisonId": comparison_id,
                "case": str(comparison["case_id"]),
                "program": str(comparison["program_id"]),
                "comparisonType": str(comparison["comparison_type"]),
                "leftLabel": str(comparison["left_label"]),
                "rightLabel": str(comparison["right_label"]),
                **combined,
                "matching": matched,
                "primaryScore": left_score,
                "secondaryScore": right_score,
                "reviewerMin": min(left_score, right_score),
                "reviewerMax": max(left_score, right_score),
                "reviewerDifference": round(abs(left_score - right_score), 6),
                "matchingAgreement": left_mapping == right_mapping,
                "primaryRationale": left["overall_rationale"],
                "secondaryRationale": right["overall_rationale"],
            }
        )

    def mean_for(comparison_type: str) -> float:
        values = [
            row["moduleSemanticSimilarity"]
            for row in rows
            if row["comparisonType"] == comparison_type
        ]
        return round(sum(values) / len(values), 6) if values else 0.0

    return {
        "schema_version": 1,
        "assessment_type": "two_model_mean_with_deterministic_assignment",
        "method": {
            "description": (
                "Two independent assessors score all nine module pairs on a 0–1 semantic scale; "
                "cell scores are averaged and the best one-to-one assignment is selected from "
                "the six possible permutations."
            ),
            "anchors": {
                "1.00": "same mechanism, output, and direction",
                "0.75": "same core with meaningful refinement",
                "0.50": "same broad process but different central mechanism or output",
                "0.25": "contextual or peripheral overlap only",
                "0.00": "unrelated or opposed",
            },
        },
        "summary": {
            "comparisonCount": len(rows),
            "baselineReplicateMean": mean_for("baseline_replicate"),
            "candidateReplicateMean": mean_for("candidate_replicate"),
            "crossSettingMean": mean_for("cross_setting"),
            "meanReviewerDifference": round(
                sum(row["reviewerDifference"] for row in rows) / len(rows), 6
            ),
            "matchingAgreementCount": sum(row["matchingAgreement"] for row in rows),
        },
        "rows": rows,
        "reviewers": {
            "primary": dict(primary.get("telemetry") or {}),
            "secondary": dict(secondary.get("telemetry") or {}),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--pairs", required=True, type=Path)
    run.add_argument("--repo-root", default=Path("."), type=Path)
    run.add_argument("--schema", required=True, type=Path)
    run.add_argument("--out", required=True, type=Path)
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
    run.add_argument("--overwrite", action="store_true")

    combine = subparsers.add_parser("combine")
    combine.add_argument("--pairs", required=True, type=Path)
    combine.add_argument("--repo-root", default=Path("."), type=Path)
    combine.add_argument("--primary", required=True, type=Path)
    combine.add_argument("--secondary", required=True, type=Path)
    combine.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    comparisons = load_comparisons(args.pairs, repo_root=args.repo_root)
    if args.command == "run":
        if args.concurrency <= 0 or args.timeout <= 0:
            raise SystemExit("concurrency and timeout must be positive")
        output = run_assessments(args)
        print(json.dumps(output["telemetry"], indent=2, sort_keys=True))
        return 0
    output = combine_assessments(
        comparisons,
        _read_json(args.primary),
        _read_json(args.secondary),
    )
    _write_json(args.out, output)
    print(json.dumps(output["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
