"""Build annotation, replicate-stability, and turn-allocation benchmark diagnostics."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


PHASES = (
    "setup",
    "supplied_gene",
    "regulator",
    "theme",
    "expansion",
    "gap",
    "finalize",
    "legacy_unphased",
    "unclassified_literature",
    "other",
)


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _mean(values: Sequence[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return round(len(left & right) / len(union), 6) if union else 1.0


def _annotation_gene_set(result: Mapping[str, Any]) -> set[str]:
    return {
        str(gene)
        for mechanism in result.get("candidate_mechanisms") or []
        for gene in mechanism.get("supporting_genes") or []
    }


def _tool_phase(event: Mapping[str, Any]) -> str:
    tool = str(event.get("tool") or "")
    if tool in {"Read", "Glob", "ToolSearch"}:
        return "setup"
    if "submit_result" in tool:
        return "finalize"
    if not tool.startswith("mcp__literature__"):
        return "other"
    if "expand_openalex_citations" in tool:
        return "expansion"
    summary = str(event.get("args_summary") or "")
    phase = None
    try:
        parsed = json.loads(summary)
        phase = parsed.get("research_phase")
    except json.JSONDecodeError:
        match = re.search(r'"research_phase"\s*:\s*"([^"]+)"', summary)
        phase = match.group(1) if match else None
    if phase in {"supplied_gene", "regulator", "theme", "expansion", "gap"}:
        return str(phase)
    return "legacy_unphased" if phase is None else "unclassified_literature"


def _search_query_key(event: Mapping[str, Any]) -> str | None:
    tool = str(event.get("tool") or "")
    if tool not in {
        "mcp__literature__search_pubmed",
        "mcp__literature__search_openalex",
    }:
        return None
    try:
        parsed = json.loads(str(event.get("args_summary") or ""))
    except json.JSONDecodeError:
        return None
    query = " ".join(str(parsed.get("query") or "").casefold().split())
    return f"{tool}:{query}" if query else None


def _attempt_diagnostics(
    audit_path: Path,
    *,
    regime: str,
) -> list[dict[str, Any]]:
    audit = _read_json(audit_path)
    rows = []
    for record in audit.get("attempt_records") or []:
        tools = list(record.get("tool_trace") or [])
        phase_counts = Counter(_tool_phase(event) for event in tools)
        reported_turns = record.get("num_turns")
        exact = isinstance(reported_turns, int)
        minimum_turns = int(reported_turns) if exact else len(tools)
        if exact and minimum_turns < len(tools):
            raise ValueError(f"{audit_path}: reported turns are below recorded tool calls")
        if exact:
            phase_counts["finalize"] += minimum_turns - len(tools)
        rows.append(
            {
                "audit": str(audit_path),
                "regime": regime,
                "attempt": int(record.get("attempt") or len(rows) + 1),
                "status": str(record.get("status") or audit.get("status") or "unknown"),
                "reportedTurns": reported_turns,
                "minimumTurns": minimum_turns,
                "turnsExact": exact,
                "recordedToolCalls": len(tools),
                "searchQueryKeys": [
                    key for event in tools if (key := _search_query_key(event))
                ],
                "phaseTurns": {
                    phase: int(phase_counts.get(phase, 0)) for phase in PHASES
                },
            }
        )
    return rows


def _repeat_rows(
    baseline_metrics: Mapping[str, Any],
    candidate_metrics: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    baseline = {
        str(row["case_id"]): row
        for row in baseline_metrics.get("repeat_run_stability") or []
    }
    candidate = {
        str(row["case_id"]): row
        for row in candidate_metrics.get("repeat_run_stability") or []
    }
    if set(baseline) != set(candidate):
        raise ValueError("baseline and candidate repeat-stability cases differ")
    rows = []
    for case_id in sorted(baseline):
        left = baseline[case_id]
        right = candidate[case_id]
        rows.append(
            {
                "case": case_id,
                "program": case_id.split("-")[-1].upper(),
                "baselinePaperJaccard": float(left["citation_jaccard"]),
                "candidatePaperJaccard": float(right["citation_jaccard"]),
                "paperJaccardDelta": round(
                    float(right["citation_jaccard"])
                    - float(left["citation_jaccard"]),
                    6,
                ),
                "baselineCoveredGeneJaccard": float(left["covered_gene_jaccard"]),
                "candidateCoveredGeneJaccard": float(right["covered_gene_jaccard"]),
                "coveredGeneJaccardDelta": round(
                    float(right["covered_gene_jaccard"])
                    - float(left["covered_gene_jaccard"]),
                    6,
                ),
                "baselineFunctionGeneJaccard": float(
                    left["function_supported_gene_jaccard"]
                ),
                "candidateFunctionGeneJaccard": float(
                    right["function_supported_gene_jaccard"]
                ),
                "functionGeneJaccardDelta": round(
                    float(right["function_supported_gene_jaccard"])
                    - float(left["function_supported_gene_jaccard"]),
                    6,
                ),
            }
        )
    summary = {
        "cases": len(rows),
        "baselineMeanPaperJaccard": _mean(
            [row["baselinePaperJaccard"] for row in rows]
        ),
        "candidateMeanPaperJaccard": _mean(
            [row["candidatePaperJaccard"] for row in rows]
        ),
        "paperJaccardDelta": _mean([row["paperJaccardDelta"] for row in rows]),
        "paperJaccardCasesImproved": sum(row["paperJaccardDelta"] > 0 for row in rows),
        "baselineMeanCoveredGeneJaccard": _mean(
            [row["baselineCoveredGeneJaccard"] for row in rows]
        ),
        "candidateMeanCoveredGeneJaccard": _mean(
            [row["candidateCoveredGeneJaccard"] for row in rows]
        ),
        "coveredGeneJaccardDelta": _mean(
            [row["coveredGeneJaccardDelta"] for row in rows]
        ),
        "baselineMeanFunctionGeneJaccard": _mean(
            [row["baselineFunctionGeneJaccard"] for row in rows]
        ),
        "candidateMeanFunctionGeneJaccard": _mean(
            [row["candidateFunctionGeneJaccard"] for row in rows]
        ),
        "functionGeneJaccardDelta": _mean(
            [row["functionGeneJaccardDelta"] for row in rows]
        ),
    }
    return rows, summary


def build_diagnostics(
    pair_manifest: Mapping[str, Any],
    *,
    repo_root: str | Path,
    baseline_metrics: Mapping[str, Any],
    candidate_metrics: Mapping[str, Any],
    annotation_final: Mapping[str, Any],
    annotation_reliability: Mapping[str, Any],
    module_similarity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(repo_root)
    pairs = list(pair_manifest.get("pairs") or [])
    assessments = {
        str(row["assessment_id"]): row
        for row in annotation_final.get("assessments") or []
    }
    if {str(row["assessment_id"]) for row in pairs} != set(assessments):
        raise ValueError("annotation assessments must exactly cover the pair manifest")

    annotation_rows = []
    reconciliation_rows = []
    turn_rows = []
    baseline_phase_totals: Counter[str] = Counter()
    phase_totals: Counter[str] = Counter()
    final_phase_totals: Counter[str] = Counter()
    baseline_metric_rows = {
        str(row["session_id"]): row
        for row in baseline_metrics.get("per_session") or []
    }
    candidate_metric_rows = {
        str(row["session_id"]): row
        for row in candidate_metrics.get("per_session") or []
    }
    for pair in pairs:
        assessment_id = str(pair["assessment_id"])
        session_id = str(pair["session_id"])
        baseline_result = _read_json(root / str(pair["baseline_result"]))
        candidate_result = _read_json(root / str(pair["candidate_result"]))
        baseline_genes = _annotation_gene_set(baseline_result)
        candidate_genes = _annotation_gene_set(candidate_result)
        baseline_audited_genes = set(
            baseline_metric_rows[session_id]["function_supported_genes"]
        )
        candidate_audited_genes = set(
            candidate_metric_rows[session_id]["function_supported_genes"]
        )
        baseline_backed = baseline_genes & baseline_audited_genes
        candidate_backed = candidate_genes & candidate_audited_genes
        assessment = assessments[assessment_id]
        annotation_rows.append(
            {
                "assessmentId": assessment_id,
                "session": session_id,
                "case": str(pair["case_id"]),
                "program": str(pair["program_id"]),
                "semanticRelation": assessment["semantic_relation"],
                "coreInterpretationChanged": assessment[
                    "core_interpretation_changed"
                ],
                "evidenceDepthChange": assessment["evidence_depth_change"],
                "baselineMechanisms": len(
                    baseline_result.get("candidate_mechanisms") or []
                ),
                "candidateMechanisms": len(
                    candidate_result.get("candidate_mechanisms") or []
                ),
                "baselineSupportingGenes": len(baseline_genes),
                "candidateSupportingGenes": len(candidate_genes),
                "supportingGeneJaccard": _jaccard(baseline_genes, candidate_genes),
                "addedSupportingGenes": ", ".join(
                    sorted(candidate_genes - baseline_genes, key=str.casefold)
                ),
                "removedSupportingGenes": ", ".join(
                    sorted(baseline_genes - candidate_genes, key=str.casefold)
                ),
                "sharedCoreConclusions": "; ".join(
                    assessment["shared_core_conclusions"]
                ),
                "candidateAdditions": "; ".join(assessment["candidate_additions"]),
                "removedOrWeakened": "; ".join(
                    assessment["candidate_removed_or_weakened"]
                ),
                "decisionRelevantDifferences": "; ".join(
                    assessment["decision_relevant_differences"]
                ),
                "rationale": assessment["rationale"],
            }
        )
        reconciliation_rows.append(
            {
                "session": session_id,
                "case": str(pair["case_id"]),
                "program": str(pair["program_id"]),
                "baselineAnnotationClaimedGenes": len(baseline_genes),
                "candidateAnnotationClaimedGenes": len(candidate_genes),
                "annotationClaimedDelta": len(candidate_genes) - len(baseline_genes),
                "baselineAuditedFunctionGenes": len(baseline_audited_genes),
                "candidateAuditedFunctionGenes": len(candidate_audited_genes),
                "auditedFunctionDelta": (
                    len(candidate_audited_genes) - len(baseline_audited_genes)
                ),
                "baselineClaimBackedGenes": len(baseline_backed),
                "candidateClaimBackedGenes": len(candidate_backed),
                "baselineClaimBackedRate": (
                    round(len(baseline_backed) / len(baseline_genes), 6)
                    if baseline_genes
                    else 1.0
                ),
                "candidateClaimBackedRate": (
                    round(len(candidate_backed) / len(candidate_genes), 6)
                    if candidate_genes
                    else 1.0
                ),
                "baselineClaimedButNotAudited": ", ".join(
                    sorted(baseline_genes - baseline_audited_genes, key=str.casefold)
                ),
                "candidateClaimedButNotAudited": ", ".join(
                    sorted(candidate_genes - candidate_audited_genes, key=str.casefold)
                ),
            }
        )

        baseline_attempts = [
            attempt
            for relative in pair["baseline_audits"]
            for attempt in _attempt_diagnostics(root / str(relative), regime="baseline")
        ]
        candidate_attempts = [
            attempt
            for relative in pair["candidate_audits"]
            for attempt in _attempt_diagnostics(root / str(relative), regime="candidate")
        ]
        for attempt in baseline_attempts:
            baseline_phase_totals.update(attempt["phaseTurns"])
        successful = [row for row in candidate_attempts if row["status"] == "ok"]
        if not successful:
            raise ValueError(f"{assessment_id}: no successful candidate attempt")
        final_attempt = successful[-1]
        baseline_minimum = sum(row["minimumTurns"] for row in baseline_attempts)
        candidate_minimum = sum(row["minimumTurns"] for row in candidate_attempts)
        candidate_unknown_attempts = sum(not row["turnsExact"] for row in candidate_attempts)
        search_query_keys = [
            key for attempt in candidate_attempts for key in attempt["searchQueryKeys"]
        ]
        for attempt in candidate_attempts:
            phase_totals.update(attempt["phaseTurns"])
        final_phase_totals.update(final_attempt["phaseTurns"])
        phase_counts = {
            phase: sum(row["phaseTurns"][phase] for row in candidate_attempts)
            for phase in PHASES
        }
        turn_rows.append(
            {
                "session": str(pair["session_id"]),
                "case": str(pair["case_id"]),
                "program": str(pair["program_id"]),
                "baselineTurns": baseline_minimum,
                "candidateFinalOutputTurns": final_attempt["minimumTurns"],
                "candidateOperationalMinimumTurns": candidate_minimum,
                "candidateRetryOrFailureMinimumTurns": (
                    candidate_minimum - final_attempt["minimumTurns"]
                ),
                "candidateAttempts": len(candidate_attempts),
                "candidateUnknownTurnAttempts": candidate_unknown_attempts,
                "candidateOperationalTurnsExact": candidate_unknown_attempts == 0,
                "candidateSearchTurns": len(search_query_keys),
                "candidateDuplicateSearchTurns": (
                    len(search_query_keys) - len(set(search_query_keys))
                ),
                "turnRatioMinimum": round(
                    candidate_minimum / baseline_minimum, 6
                ),
                **{f"{phase}Turns": phase_counts[phase] for phase in PHASES},
                **{
                    f"final{phase.title().replace('_', '')}Turns": final_attempt[
                        "phaseTurns"
                    ][phase]
                    for phase in PHASES
                },
            }
        )

    relation_counts = Counter(row["semanticRelation"] for row in annotation_rows)
    depth_counts = Counter(row["evidenceDepthChange"] for row in annotation_rows)
    repeat_rows, repeat_summary = _repeat_rows(baseline_metrics, candidate_metrics)
    baseline_claimed = sum(
        row["baselineAnnotationClaimedGenes"] for row in reconciliation_rows
    )
    candidate_claimed = sum(
        row["candidateAnnotationClaimedGenes"] for row in reconciliation_rows
    )
    phase_rows = [
        {
            "phase": phase,
            "operationalMinimumTurns": int(phase_totals.get(phase, 0)),
            "finalOutputTurns": int(final_phase_totals.get(phase, 0)),
            "retryOrFailureTurns": int(
                phase_totals.get(phase, 0) - final_phase_totals.get(phase, 0)
            ),
            "shareOfRecordedMinimum": round(
                phase_totals.get(phase, 0)
                / sum(phase_totals.values()),
                6,
            ),
        }
        for phase in PHASES
        if phase_totals.get(phase, 0)
    ]
    return {
        "schema_version": 1,
        "assessment_type": "model_and_trace_reconstruction",
        "annotation": {
            "summary": {
                "pairedSessions": len(annotation_rows),
                "coreInterpretationChanged": sum(
                    row["coreInterpretationChanged"] for row in annotation_rows
                ),
                "sameCoreInterpretation": relation_counts["same_core_interpretation"],
                "sameCoreWithMeaningfulRefinement": relation_counts[
                    "same_core_with_meaningful_refinement"
                ],
                "materiallyChanged": relation_counts["materially_changed"],
                "incomparable": relation_counts["incomparable"],
                "meanSupportingGeneJaccard": _mean(
                    [row["supportingGeneJaccard"] for row in annotation_rows]
                ),
                "evidenceDepthChangeCounts": dict(sorted(depth_counts.items())),
            },
            "rows": annotation_rows,
            "reliability": dict(annotation_reliability),
        },
        "coverage_reconciliation": {
            "summary": {
                "sessions": len(reconciliation_rows),
                "baselineMeanAnnotationClaimedGenes": _mean(
                    [
                        float(row["baselineAnnotationClaimedGenes"])
                        for row in reconciliation_rows
                    ]
                ),
                "candidateMeanAnnotationClaimedGenes": _mean(
                    [
                        float(row["candidateAnnotationClaimedGenes"])
                        for row in reconciliation_rows
                    ]
                ),
                "baselineMeanAuditedFunctionGenes": _mean(
                    [
                        float(row["baselineAuditedFunctionGenes"])
                        for row in reconciliation_rows
                    ]
                ),
                "candidateMeanAuditedFunctionGenes": _mean(
                    [
                        float(row["candidateAuditedFunctionGenes"])
                        for row in reconciliation_rows
                    ]
                ),
                "baselineClaimBackedRate": round(
                    sum(row["baselineClaimBackedGenes"] for row in reconciliation_rows)
                    / baseline_claimed,
                    6,
                ),
                "candidateClaimBackedRate": round(
                    sum(row["candidateClaimBackedGenes"] for row in reconciliation_rows)
                    / candidate_claimed,
                    6,
                ),
                "sessionsWithFewerAnnotationGenesButMoreAuditedSupport": sum(
                    row["annotationClaimedDelta"] < 0
                    and row["auditedFunctionDelta"] > 0
                    for row in reconciliation_rows
                ),
            },
            "rows": reconciliation_rows,
        },
        "module_similarity": dict(module_similarity or {}),
        "replicate_stability": {
            "summary": repeat_summary,
            "rows": repeat_rows,
        },
        "turns": {
            "summary": {
                "baselineMeanTurns": _mean(
                    [float(row["baselineTurns"]) for row in turn_rows]
                ),
                "candidateMeanFinalOutputTurns": _mean(
                    [float(row["candidateFinalOutputTurns"]) for row in turn_rows]
                ),
                "candidateMeanOperationalMinimumTurns": _mean(
                    [
                        float(row["candidateOperationalMinimumTurns"])
                        for row in turn_rows
                    ]
                ),
                "candidateToBaselineOperationalTurnRatioMinimum": round(
                    sum(row["candidateOperationalMinimumTurns"] for row in turn_rows)
                    / sum(row["baselineTurns"] for row in turn_rows),
                    6,
                ),
                "sessionsWithUnknownAttemptTurns": sum(
                    not row["candidateOperationalTurnsExact"] for row in turn_rows
                ),
                "unknownAttemptCount": sum(
                    row["candidateUnknownTurnAttempts"] for row in turn_rows
                ),
                "candidateSearchTurns": sum(
                    row["candidateSearchTurns"] for row in turn_rows
                ),
                "candidateDuplicateSearchTurns": sum(
                    row["candidateDuplicateSearchTurns"] for row in turn_rows
                ),
                "candidateDuplicateSearchRate": round(
                    sum(row["candidateDuplicateSearchTurns"] for row in turn_rows)
                    / sum(row["candidateSearchTurns"] for row in turn_rows),
                    6,
                ),
                "candidateRetryOrFailureMinimumTurns": sum(
                    row["candidateRetryOrFailureMinimumTurns"] for row in turn_rows
                ),
                "candidateRetryOrFailureTurnShare": round(
                    sum(row["candidateRetryOrFailureMinimumTurns"] for row in turn_rows)
                    / sum(row["candidateOperationalMinimumTurns"] for row in turn_rows),
                    6,
                ),
                "phaseAttribution": (
                    "candidate phases reconstructed from one recorded tool call per SDK turn; "
                    "completed attempts add their terminal no-tool turn to finalize"
                ),
                "baselinePhaseLimitation": (
                    "baseline literature calls predate research_phase labels and cannot be split "
                    "reliably into supplied-gene, regulator, theme, expansion, and gap turns"
                ),
            },
            "per_session": turn_rows,
            "candidate_phase_totals": phase_rows,
            "baseline_phase_totals": [
                {
                    "phase": phase,
                    "turns": int(baseline_phase_totals.get(phase, 0)),
                }
                for phase in PHASES
                if baseline_phase_totals.get(phase, 0)
            ],
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", required=True, type=Path)
    parser.add_argument("--repo-root", default=Path("."), type=Path)
    parser.add_argument("--baseline-metrics", required=True, type=Path)
    parser.add_argument("--candidate-metrics", required=True, type=Path)
    parser.add_argument("--annotation-final", required=True, type=Path)
    parser.add_argument("--annotation-reliability", required=True, type=Path)
    parser.add_argument("--module-similarity", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    diagnostics = build_diagnostics(
        _read_json(args.pairs),
        repo_root=args.repo_root,
        baseline_metrics=_read_json(args.baseline_metrics),
        candidate_metrics=_read_json(args.candidate_metrics),
        annotation_final=_read_json(args.annotation_final),
        annotation_reliability=_read_json(args.annotation_reliability),
        module_similarity=(
            _read_json(args.module_similarity) if args.module_similarity else None
        ),
    )
    _write_json(args.out, diagnostics)
    print(
        json.dumps(
            {
                "out": str(args.out.resolve()),
                "annotation": diagnostics["annotation"]["summary"],
                "replicate_stability": diagnostics["replicate_stability"]["summary"],
                "turns": diagnostics["turns"]["summary"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
