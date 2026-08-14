"""Build the paired baseline-versus-candidate literature benchmark report inputs."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _mean(values: Sequence[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _sum_support(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(row.get("support_distribution") or {})
    return dict(sorted(counts.items()))


def _quality_totals(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    overclaimed = sum(
        int((row.get("context_label_accuracy_distribution") or {}).get("overclaimed") or 0)
        for row in rows
    )
    assessable = sum(
        sum(
            int((row.get("context_label_accuracy_distribution") or {}).get(value) or 0)
            for value in ("accurate", "overclaimed", "underclaimed")
        )
        for row in rows
    )
    flagged = sum(int(row.get("red_flagged_link_count") or 0) for row in rows)
    links = sum(int(row.get("review_link_count") or 0) for row in rows)
    support = _sum_support(rows)
    supportive = int(support.get("supports") or 0) + int(support.get("partial") or 0)
    return {
        "links": links,
        "context_overclaim_count": overclaimed,
        "context_assessable_count": assessable,
        "context_overclaim_rate": _ratio(overclaimed, assessable),
        "flagged_link_count": flagged,
        "flagged_link_rate": _ratio(flagged, links),
        "support_distribution": support,
        "supportive_link_count": supportive,
        "supportive_link_rate": _ratio(supportive, links),
        "contradiction_count": int(support.get("contradicts") or 0),
        "contradiction_rate": _ratio(int(support.get("contradicts") or 0), links),
    }


def _flag_counts(metrics: Mapping[str, Any], session_ids: set[str]) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                str(row["red_flag"])
                for row in metrics.get("red_flags") or []
                if str(row.get("session_id")) in session_ids
            ).items()
        )
    )


def _parameter_trials(
    baseline_run: Mapping[str, Any],
    arm_b: Mapping[str, Any],
    arm_c: Mapping[str, Any],
    arm_120: Mapping[str, Any],
    unbounded: Mapping[str, Any],
) -> list[dict[str, Any]]:
    def execution(run: Mapping[str, Any]) -> Mapping[str, Any]:
        return run.get("execution") or {}

    trials = [
        (
            "A",
            "Frozen baseline",
            baseline_run,
            "Reference; paired baseline sessions valid",
            "production default retained",
        ),
        (
            "B",
            "Retrieval-first bounded",
            arm_b,
            "Incomplete canary; 30-turn ceiling reached",
            "rejected",
        ),
        (
            "C",
            "Retrieval-first diagnostic",
            arm_c,
            "Incomplete canary; 60-turn ceiling reached",
            "rejected",
        ),
        (
            "D",
            "Retrieval-first generous",
            arm_120,
            "Incomplete and path-dependent at 120 turns",
            "rejected",
        ),
        (
            "E",
            "Retrieval-first unbounded diagnostic",
            unbounded,
            "7/8 main sessions valid; one isolated recovery",
            "quality-evaluable; operationally rejected",
        ),
    ]
    rows = []
    for order, (arm, regime, run, observed, decision) in enumerate(trials, start=1):
        config = execution(run)
        rows.append(
            {
                "order": order,
                "arm": arm,
                "regime": regime,
                "maxTurns": config.get("max_turns"),
                "turnLabel": (
                    str(config.get("max_turns"))
                    if config.get("max_turns") is not None
                    else "No client ceiling"
                ),
                "budgetUsd": config.get("max_budget_usd"),
                "timeoutSeconds": config.get("per_program_timeout_seconds"),
                "concurrency": config.get("concurrency"),
                "observed": observed,
                "decision": decision,
            }
        )
    rows.append(
        {
            "order": len(rows) + 1,
            "arm": "Next",
            "regime": "Bounded retest after work reduction",
            "maxTurns": 60,
            "turnLabel": "60",
            "budgetUsd": 1.25,
            "timeoutSeconds": 900,
            "concurrency": 6,
            "observed": "Proposed experiment; not yet validated",
            "decision": "test next",
        }
    )
    return rows


def build_comparison(
    baseline_metrics: Mapping[str, Any],
    candidate_metrics: Mapping[str, Any],
    structural: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    *,
    baseline_run: Mapping[str, Any],
    arm_b: Mapping[str, Any],
    arm_c: Mapping[str, Any],
    arm_120: Mapping[str, Any],
    unbounded: Mapping[str, Any],
) -> dict[str, Any]:
    session_ids = [str(value) for value in structural["pairing"]["session_ids"]]
    expected = set(session_ids)
    baseline_rows = {
        str(row["session_id"]): row for row in baseline_metrics.get("per_session") or []
    }
    candidate_rows = {
        str(row["session_id"]): row for row in candidate_metrics.get("per_session") or []
    }
    missing_baseline = expected - set(baseline_rows)
    missing_candidate = expected - set(candidate_rows)
    if missing_baseline or missing_candidate:
        raise ValueError(
            f"paired sessions missing; baseline={sorted(missing_baseline)}, "
            f"candidate={sorted(missing_candidate)}"
        )

    per_session = []
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for session_id in session_ids:
        baseline = baseline_rows[session_id]
        candidate = candidate_rows[session_id]
        if baseline["case_id"] != candidate["case_id"]:
            raise ValueError(f"{session_id}: baseline and candidate case IDs differ")
        row = {
            "session": session_id,
            "case": str(candidate["case_id"]),
            "program": str(candidate["program_id"]),
            "baselineCitation": float(baseline["citation_coverage"]),
            "candidateCitation": float(candidate["citation_coverage"]),
            "citationDelta": round(
                float(candidate["citation_coverage"]) - float(baseline["citation_coverage"]),
                6,
            ),
            "baselineFunction": float(baseline["function_supported_coverage"]),
            "candidateFunction": float(candidate["function_supported_coverage"]),
            "functionDelta": round(
                float(candidate["function_supported_coverage"])
                - float(baseline["function_supported_coverage"]),
                6,
            ),
            "baselineContextOverclaim": float(baseline["context_overclaim_rate"]),
            "candidateContextOverclaim": float(candidate["context_overclaim_rate"]),
            "baselineFlagRate": float(baseline["red_flagged_link_rate"]),
            "candidateFlagRate": float(candidate["red_flagged_link_rate"]),
        }
        per_session.append(row)
        grouped[row["case"]].append(row)

    per_case = []
    for case, rows in sorted(grouped.items()):
        per_case.append(
            {
                "case": case,
                "program": str(rows[0]["program"]),
                "runs": len(rows),
                "baselineCitation": _mean([row["baselineCitation"] for row in rows]),
                "candidateCitation": _mean([row["candidateCitation"] for row in rows]),
                "citationDelta": _mean([row["citationDelta"] for row in rows]),
                "baselineFunction": _mean([row["baselineFunction"] for row in rows]),
                "candidateFunction": _mean([row["candidateFunction"] for row in rows]),
                "functionDelta": _mean([row["functionDelta"] for row in rows]),
            }
        )

    baseline_selected = [baseline_rows[session_id] for session_id in session_ids]
    candidate_selected = [candidate_rows[session_id] for session_id in session_ids]
    baseline_quality = _quality_totals(baseline_selected)
    candidate_quality = _quality_totals(candidate_selected)
    baseline_flags = _flag_counts(baseline_metrics, expected)
    candidate_flags = _flag_counts(candidate_metrics, expected)
    flag_names = sorted(set(baseline_flags) | set(candidate_flags))
    red_flags = [
        {
            "flag": flag,
            "baseline": int(baseline_flags.get(flag, 0)),
            "candidate": int(candidate_flags.get(flag, 0)),
            "delta": int(candidate_flags.get(flag, 0) - baseline_flags.get(flag, 0)),
        }
        for flag in flag_names
    ]

    coverage = {
        "baselineCitation": _mean([row["baselineCitation"] for row in per_session]),
        "candidateCitation": _mean([row["candidateCitation"] for row in per_session]),
        "citationDelta": _mean([row["citationDelta"] for row in per_session]),
        "baselineFunction": _mean([row["baselineFunction"] for row in per_session]),
        "candidateFunction": _mean([row["candidateFunction"] for row in per_session]),
        "functionDelta": _mean([row["functionDelta"] for row in per_session]),
        "positiveFunctionSessions": sum(row["functionDelta"] > 0 for row in per_session),
        "sessionCount": len(per_session),
        "minimumCaseFunctionDelta": min(row["functionDelta"] for row in per_case),
        "maximumCaseFunctionDelta": max(row["functionDelta"] for row in per_case),
    }

    selection = structural["selection"]
    operations = structural["operations"]
    reliability = candidate_metrics.get("reliability") or {}
    expanded_reliability = reliability.get("expanded_full_review") or {}
    gate_rows = [
        {
            "gate": "Mean adjudicated function coverage gain ≥10 pp",
            "observed": f"{coverage['functionDelta'] * 100:.1f} pp",
            "status": "pass" if coverage["functionDelta"] >= 0.10 else "fail",
        },
        {
            "gate": "Every paired case gains ≥5 pp",
            "observed": f"minimum {coverage['minimumCaseFunctionDelta'] * 100:.1f} pp",
            "status": "pass" if coverage["minimumCaseFunctionDelta"] >= 0.05 else "fail",
        },
        {
            "gate": "Wrong-gene flags do not increase",
            "observed": (
                f"{baseline_flags.get('wrong_gene', 0)} → "
                f"{candidate_flags.get('wrong_gene', 0)}"
            ),
            "status": (
                "pass"
                if candidate_flags.get("wrong_gene", 0) <= baseline_flags.get("wrong_gene", 0)
                else "fail"
            ),
        },
        {
            "gate": "Paralog-only flags do not increase",
            "observed": (
                f"{baseline_flags.get('paralog_only', 0)} → "
                f"{candidate_flags.get('paralog_only', 0)}"
            ),
            "status": (
                "pass"
                if candidate_flags.get("paralog_only", 0)
                <= baseline_flags.get("paralog_only", 0)
                else "fail"
            ),
        },
        {
            "gate": "No new source-opposed claims",
            "observed": (
                f"Baseline: {baseline_quality['contradiction_count']}; candidate: "
                f"{candidate_quality['contradiction_count']} contradictions"
            ),
            "status": (
                "pass"
                if candidate_quality["contradiction_count"]
                <= baseline_quality["contradiction_count"]
                else "fail"
            ),
        },
        {
            "gate": "Context overclaim does not increase",
            "observed": (
                f"{baseline_quality['context_overclaim_rate'] * 100:.1f}% → "
                f"{candidate_quality['context_overclaim_rate'] * 100:.1f}%"
            ),
            "status": (
                "pass"
                if candidate_quality["context_overclaim_rate"]
                <= baseline_quality["context_overclaim_rate"]
                else "fail"
            ),
        },
        {
            "gate": "Known cost ≤1.5× baseline",
            "observed": f"{operations['candidate_to_baseline_known_cost_ratio']:.2f}×",
            "status": (
                "pass"
                if float(operations["candidate_to_baseline_known_cost_ratio"]) <= 1.5
                else "fail"
            ),
        },
        {
            "gate": "p95 latency ≤1.5× baseline",
            "observed": f"{operations['candidate_to_baseline_p95_duration_ratio']:.2f}×",
            "status": (
                "pass"
                if float(operations["candidate_to_baseline_p95_duration_ratio"]) <= 1.5
                else "fail"
            ),
        },
    ]

    return {
        "schema_version": 1,
        "assessment_type": "model",
        "decision": {
            "data_sufficiency": "sufficient_for_internal_directional_comparison",
            "production_promotion": "reject_current_unbounded_regime",
            "workflow_direction": "retain_retrieval_first_and_optimize_bounded_execution",
            "production_defaults": "30 turns / $1.00 / 600 seconds unchanged",
            "reasons": [
                "All eight paired sessions improved adjudicated function-supported coverage.",
                "All 165 candidate links were independently reviewed twice and all disagreements were adjudicated.",
                "The candidate added two source-opposed claims and failed the cost, latency, and recovery gates.",
            ],
            "not_sufficient_for": [
                "broad biological generalization beyond the four paired programs",
                "promotion of unbounded or higher production limits",
            ],
        },
        "coverage": coverage,
        "baseline_quality": baseline_quality,
        "candidate_quality": candidate_quality,
        "selection": dict(selection),
        "operations": dict(operations),
        "per_session": per_session,
        "per_case": per_case,
        "red_flags": red_flags,
        "gate_outcomes": gate_rows,
        "parameter_trials": _parameter_trials(
            baseline_run, arm_b, arm_c, arm_120, unbounded
        ),
        "reliability": {
            "fixed_sample": {
                key: reliability.get(key)
                for key in (
                    "double_scored_count",
                    "double_scored_fraction",
                    "exact_agreement_rate",
                    "disagreement_count",
                    "adjudicated_count",
                    "field_agreement_rates",
                )
            },
            "expanded_full_review": {
                key: expanded_reliability.get(key)
                for key in (
                    "double_scored_count",
                    "double_scored_fraction",
                    "exact_agreement_rate",
                    "disagreement_count",
                    "adjudicated_count",
                    "field_agreement_rates",
                )
            },
        },
        "diagnostics": dict(diagnostics),
    }


def _source(
    source_id: str,
    label: str,
    path: str,
    *,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "id": source_id,
        "label": label,
        "path": path,
        "query": {
            "language": "sql",
            "engine": "duckdb",
            "executed_at": generated_at,
            "description": f"Read the saved {label.lower()} JSON artifact.",
            "sql": f"SELECT * FROM read_json_auto('{path}');",
            "tables_used": [path],
            "metric_definitions": [
                "Coverage rates use 23 supplied genes per session; perturbation regulators are excluded.",
                "Paired changes are candidate minus baseline on identical session IDs.",
            ],
        },
    }


def _build_legacy_artifact(
    comparison: Mapping[str, Any], *, generated_at: str
) -> dict[str, Any]:
    coverage = comparison["coverage"]
    baseline_quality = comparison["baseline_quality"]
    candidate_quality = comparison["candidate_quality"]
    selection = comparison["selection"]
    operations = comparison["operations"]
    reliability = comparison["reliability"]
    full_reliability = reliability["expanded_full_review"]
    diagnostics = comparison["diagnostics"]
    annotation = diagnostics["annotation"]
    annotation_summary = annotation["summary"]
    replicate = diagnostics["replicate_stability"]
    replicate_summary = replicate["summary"]
    turns = diagnostics["turns"]
    turn_summary = turns["summary"]
    summary = [
        {
            **coverage,
            "baselineNoGeneShare": selection["baseline_retained_paper_no_supplied_gene_share"],
            "candidateNoGeneShare": selection["candidate_retained_paper_no_supplied_gene_share"],
            "noGeneShareDelta": round(
                selection["candidate_retained_paper_no_supplied_gene_share"]
                - selection["baseline_retained_paper_no_supplied_gene_share"],
                6,
            ),
            "baselineContextOverclaim": baseline_quality["context_overclaim_rate"],
            "candidateContextOverclaim": candidate_quality["context_overclaim_rate"],
            "baselineFlagRate": baseline_quality["flagged_link_rate"],
            "candidateFlagRate": candidate_quality["flagged_link_rate"],
            "baselineContradictions": baseline_quality["contradiction_count"],
            "candidateContradictions": candidate_quality["contradiction_count"],
            "costRatio": operations["candidate_to_baseline_known_cost_ratio"],
            "latencyRatio": operations["candidate_to_baseline_p95_duration_ratio"],
            "fixedExactAgreement": reliability["fixed_sample"]["exact_agreement_rate"],
            "fullExactAgreement": full_reliability["exact_agreement_rate"],
            "fullDualReviewed": full_reliability["double_scored_count"],
            "materiallyChangedAnnotations": annotation_summary["materiallyChanged"],
            "pairedAnnotations": annotation_summary["pairedSessions"],
            "baselinePaperJaccard": replicate_summary["baselineMeanPaperJaccard"],
            "candidatePaperJaccard": replicate_summary["candidateMeanPaperJaccard"],
            "paperJaccardDelta": replicate_summary["paperJaccardDelta"],
            "baselineFunctionGeneJaccard": replicate_summary[
                "baselineMeanFunctionGeneJaccard"
            ],
            "candidateFunctionGeneJaccard": replicate_summary[
                "candidateMeanFunctionGeneJaccard"
            ],
            "functionGeneJaccardDelta": replicate_summary[
                "functionGeneJaccardDelta"
            ],
            "baselineMeanTurns": turn_summary["baselineMeanTurns"],
            "candidateMeanFinalOutputTurns": turn_summary[
                "candidateMeanFinalOutputTurns"
            ],
            "candidateMeanOperationalMinimumTurns": turn_summary[
                "candidateMeanOperationalMinimumTurns"
            ],
            "retryFailureTurnShare": turn_summary[
                "candidateRetryOrFailureTurnShare"
            ],
        }
    ]
    case_rows = list(comparison["per_case"])
    session_rows = list(comparison["per_session"])
    annotation_rows = list(annotation["rows"])
    replicate_rows = list(replicate["rows"])
    turn_rows = list(turns["per_session"])
    phase_rows = [
        {
            **row,
            "phaseLabel": str(row["phase"]).replace("_", " ").title(),
        }
        for row in turns["candidate_phase_totals"]
    ]
    phase_composition_rows = [
        {
            "phase": row["phaseLabel"],
            "component": component,
            "turns": row[field],
            "operationalMinimumTurns": row["operationalMinimumTurns"],
            "shareOfRecordedMinimum": row["shareOfRecordedMinimum"],
        }
        for row in phase_rows
        for component, field in (
            ("Final-output attempts", "finalOutputTurns"),
            ("Retry or failed attempts", "retryOrFailureTurns"),
        )
        if row[field]
    ]
    quality_rows = [
        {
            "metric": "Context overclaim rate",
            "baseline": baseline_quality["context_overclaim_rate"],
            "candidate": candidate_quality["context_overclaim_rate"],
            "delta": round(
                candidate_quality["context_overclaim_rate"]
                - baseline_quality["context_overclaim_rate"],
                6,
            ),
            "unit": "percent",
            "interpretation": "Flat; lower is better",
        },
        {
            "metric": "Flagged-link rate",
            "baseline": baseline_quality["flagged_link_rate"],
            "candidate": candidate_quality["flagged_link_rate"],
            "delta": round(
                candidate_quality["flagged_link_rate"] - baseline_quality["flagged_link_rate"],
                6,
            ),
            "unit": "percent",
            "interpretation": "Improved",
        },
        {
            "metric": "Supportive-link rate",
            "baseline": baseline_quality["supportive_link_rate"],
            "candidate": candidate_quality["supportive_link_rate"],
            "delta": round(
                candidate_quality["supportive_link_rate"]
                - baseline_quality["supportive_link_rate"],
                6,
            ),
            "unit": "percent",
            "interpretation": "Slightly lower",
        },
        {
            "metric": "Contradictions",
            "baseline": baseline_quality["contradiction_count"],
            "candidate": candidate_quality["contradiction_count"],
            "delta": (
                candidate_quality["contradiction_count"]
                - baseline_quality["contradiction_count"]
            ),
            "unit": "count",
            "interpretation": "Strict gate failed",
        },
    ]
    operation_rows = [
        {
            "metric": "Known accepted-run cost",
            "ratio": operations["candidate_to_baseline_known_cost_ratio"],
            "threshold": 1.5,
        },
        {
            "metric": "p95 duration",
            "ratio": operations["candidate_to_baseline_p95_duration_ratio"],
            "threshold": 1.5,
        },
    ]
    reliability_rows = []
    for field in sorted(full_reliability["field_agreement_rates"]):
        reliability_rows.append(
            {
                "field": field,
                "fixedSampleAgreement": reliability["fixed_sample"][
                    "field_agreement_rates"
                ].get(field),
                "fullAgreement": full_reliability["field_agreement_rates"][field],
            }
        )
    phase_by_name = {row["phase"]: row for row in phase_rows}
    supplied_phase = phase_by_name["supplied_gene"]
    regulator_phase = phase_by_name["regulator"]
    gap_phase = phase_by_name["gap"]
    recommendations = [
        {
            "priority": 1,
            "improvement": "Resume after failure; do not restart retrieval",
            "evidence": (
                f"{turn_summary['candidateRetryOrFailureMinimumTurns']} turns "
                f"({turn_summary['candidateRetryOrFailureTurnShare'] * 100:.1f}% of the "
                "candidate minimum) were spent in failed or retried attempts."
            ),
            "action": (
                "Checkpoint the supplied-gene ledger, verified paper–gene edges, and phase cursor "
                "after every tool result. Repair schema-only failures locally and rerun only final "
                "synthesis; resume SDK failures from unfinished genes."
            ),
            "rationale": (
                "This is the largest clearly avoidable block and does not create a better final "
                "annotation."
            ),
            "acceptance": (
                "No full-session restart for schema errors; retry/failure turns below 10% of total; "
                "all eight sessions finish without isolated recovery."
            ),
        },
        {
            "priority": 2,
            "improvement": "Budget supplied-gene search by evidence state",
            "evidence": (
                f"Supplied-gene work used {supplied_phase['operationalMinimumTurns']} turns "
                f"({supplied_phase['shareOfRecordedMinimum'] * 100:.1f}% of the candidate "
                f"minimum), including {supplied_phase['finalOutputTurns']} turns in final-output "
                "attempts. Only "
                f"{turn_summary['candidateDuplicateSearchTurns']}/"
                f"{turn_summary['candidateSearchTurns']} search turns were exact duplicates."
            ),
            "action": (
                "Give each supplied gene one anchor query. Permit a second query only when no "
                "function-support edge is found and a third only for unresolved identifiers or "
                "direction. Stop at a trace-backed terminal state and reuse verified edges across "
                "themes and retries."
            ),
            "rationale": (
                "The cost comes from many distinct reformulations, not primarily exact duplicate "
                "queries; a terminal-state scheduler is more useful than string deduplication."
            ),
            "acceptance": (
                "Mean final supplied-gene turns at or below 38 per run (30% below 53.8), all 23 "
                "genes terminal, function-supported coverage loss no more than 2 percentage points."
            ),
        },
        {
            "priority": 3,
            "improvement": "Rank and cap the regulator pass",
            "evidence": (
                f"Regulator work used {regulator_phase['operationalMinimumTurns']} turns "
                f"({regulator_phase['shareOfRecordedMinimum'] * 100:.1f}%); final-output attempts "
                f"still used {regulator_phase['finalOutputTurns']} regulator turns."
            ),
            "action": (
                "Run regulators after supplied-gene coverage. Rank by perturbation magnitude and "
                "direct mechanism relevance; search only the top three by default, with one query "
                "and one fetch batch each. Reuse prior verified regulator context."
            ),
            "rationale": (
                "Regulators are useful context but do not enter the 23-gene coverage denominator, "
                "so uncapped regulator work has lower marginal value."
            ),
            "acceptance": (
                "Mean final regulator turns at or below 8 per run; context-overclaim no worse than "
                "baseline by more than 2 percentage points."
            ),
        },
        {
            "priority": 4,
            "improvement": "Constrain the gap phase to unresolved genes",
            "evidence": (
                f"Gap work used {gap_phase['operationalMinimumTurns']} turns, including "
                f"{gap_phase['finalOutputTurns']} in final-output attempts; brain P11 r2 alone "
                "used 27."
            ),
            "action": (
                "Enter the gap phase only for genes without a terminal state. Allow one targeted "
                "gap query per unresolved gene and prohibit broad theme expansion during this phase."
            ),
            "rationale": (
                "The current gap phase is path-dependent and can become a second open-ended search "
                "pass."
            ),
            "acceptance": (
                "No run exceeds 10 gap turns; mean adjudicated function coverage gain remains at "
                "least 10 percentage points."
            ),
        },
        {
            "priority": 5,
            "improvement": "Version the core annotation separately from evidence breadth",
            "evidence": (
                f"{annotation_summary['materiallyChanged']}/"
                f"{annotation_summary['pairedSessions']} paired annotations changed a core "
                "mechanism and the rest had meaningful refinements; none were effectively "
                f"unchanged. Mean supporting-gene Jaccard was "
                f"{annotation_summary['meanSupportingGeneJaccard']:.3f}."
            ),
            "action": (
                "Persist a stable mechanism skeleton with gene assignments and directions. Attach "
                "newly supported submechanisms as versioned additions, and emit an explicit "
                "added/removed/reassigned claim diff before replacing a prior central mechanism."
            ),
            "rationale": (
                "Higher coverage changes hypotheses, not only confidence. Silent replacement makes "
                "replicate and regime comparisons impossible to audit."
            ),
            "acceptance": (
                "Every material annotation change has a claim-level diff and evidence rationale; "
                "next-pilot replicate function-gene Jaccard is at least 0.80."
            ),
        },
        {
            "priority": 6,
            "improvement": "Reject opposite-direction evidence before synthesis",
            "evidence": (
                f"Source-opposed claims increased from "
                f"{baseline_quality['contradiction_count']} to "
                f"{candidate_quality['contradiction_count']}."
            ),
            "action": (
                "Before accepting an edge, compare the paper's direction with the mechanism claim. "
                "Require species, developmental-state, and disease-state qualifiers when direction "
                "is context-dependent."
            ),
            "rationale": (
                "Cep164 and ANGPT2 show that more retrieval can surface context-specific reversals "
                "that an unqualified synthesis turns into contradictions."
            ),
            "acceptance": "Zero source-opposed claims in the next paired pilot.",
        },
        {
            "priority": 7,
            "improvement": "Retest 60 / $1.25 / 900s only after work reduction",
            "evidence": (
                f"Even final successful outputs averaged "
                f"{turn_summary['candidateMeanFinalOutputTurns']:.1f} turns; operational usage was "
                f"at least {turn_summary['candidateMeanOperationalMinimumTurns']:.1f}, versus "
                f"{turn_summary['baselineMeanTurns']:.1f} baseline turns."
            ),
            "action": (
                "Apply priorities 1–6, then rerun the same eight pairs with a 60-turn client ceiling, "
                "$1.25 budget, 900-second timeout, and transient-only retries."
            ),
            "rationale": (
                "The previous 60-turn canary was censored; raising the ceiling again would mask "
                "workflow inefficiency instead of fixing it."
            ),
            "acceptance": (
                "All eight runs complete without recovery; mean coverage gain at least 10 points; "
                "zero new contradictions; cost and p95 latency no more than 1.5× baseline."
            ),
        },
    ]

    sources = [
        _source(
            "comparison",
            "Paired benchmark comparison",
            "benchmarks/literature-v1/candidate-evaluable/comparison-report/comparison_metrics.json",
            generated_at=generated_at,
        ),
        _source(
            "baseline",
            "Baseline adjudicated reference-quality audit",
            "benchmarks/literature-v1/baseline-v4/reference_quality/metrics.json",
            generated_at=generated_at,
        ),
        _source(
            "candidate",
            "Candidate adjudicated reference-quality audit",
            "benchmarks/literature-v1/candidate-evaluable/reference_quality/audit/metrics.json",
            generated_at=generated_at,
        ),
        _source(
            "structural",
            "Structural and operational comparison",
            "benchmarks/literature-v1/candidate-evaluable/structural_comparison.json",
            generated_at=generated_at,
        ),
        _source(
            "diagnostics",
            "Annotation, replicate-stability, and turn diagnostics",
            "benchmarks/literature-v1/candidate-evaluable/comparison-report/diagnostics.json",
            generated_at=generated_at,
        ),
    ]
    cards = [
        {
            "id": "function-card",
            "dataset": "summary",
            "description": "Mean adjudicated share of 23 supplied genes with function support.",
            "sourceId": "comparison",
            "metrics": [
                {
                    "field": "candidateFunction",
                    "format": "percent",
                    "label": "Candidate functional coverage",
                },
                {
                    "field": "baselineFunction",
                    "format": "percent",
                    "label": "Baseline",
                },
                {
                    "field": "functionDelta",
                    "format": "percent",
                    "label": "Paired change",
                    "signed": True,
                },
            ],
        },
        {
            "id": "citation-card",
            "dataset": "summary",
            "description": "Mean adjudicated share of supplied genes studied by a citation.",
            "sourceId": "comparison",
            "metrics": [
                {
                    "field": "candidateCitation",
                    "format": "percent",
                    "label": "Candidate citation coverage",
                },
                {
                    "field": "baselineCitation",
                    "format": "percent",
                    "label": "Baseline",
                },
                {
                    "field": "citationDelta",
                    "format": "percent",
                    "label": "Paired change",
                    "signed": True,
                },
            ],
        },
        {
            "id": "selection-card",
            "dataset": "summary",
            "description": "Share of retained papers studying none of the supplied genes.",
            "sourceId": "comparison",
            "metrics": [
                {
                    "field": "candidateNoGeneShare",
                    "format": "percent",
                    "label": "Candidate no-gene share",
                },
                {
                    "field": "baselineNoGeneShare",
                    "format": "percent",
                    "label": "Baseline",
                },
                {
                    "field": "noGeneShareDelta",
                    "format": "percent",
                    "label": "Change",
                    "signed": True,
                },
            ],
        },
        {
            "id": "operations-card",
            "dataset": "summary",
            "description": "Both ratios exceed the 1.5× production gate.",
            "sourceId": "comparison",
            "metrics": [
                {
                    "field": "costRatio",
                    "format": "number",
                    "label": "Known cost ratio",
                    "unit": "×",
                },
                {
                    "field": "latencyRatio",
                    "format": "number",
                    "label": "p95 latency ratio",
                    "unit": "×",
                },
            ],
        },
        {
            "id": "reliability-card",
            "dataset": "summary",
            "description": "Every candidate link was independently scored twice.",
            "sourceId": "comparison",
            "metrics": [
                {
                    "field": "fullDualReviewed",
                    "format": "number",
                    "label": "Links double-reviewed",
                },
                {
                    "field": "fullExactAgreement",
                    "format": "percent",
                    "label": "Exact all-field agreement",
                },
            ],
        },
        {
            "id": "contradiction-card",
            "dataset": "summary",
            "description": (
                "A contradiction means the retained source reports the opposite biological "
                "direction from the mechanism claim. The candidate was required to add none."
            ),
            "sourceId": "comparison",
            "metrics": [
                {
                    "field": "candidateContradictions",
                    "format": "number",
                    "label": "Candidate source-opposed claims",
                },
                {
                    "field": "baselineContradictions",
                    "format": "number",
                    "label": "Baseline source-opposed claims",
                },
            ],
        },
        {
            "id": "annotation-change-card",
            "dataset": "summary",
            "description": (
                "Independent reviewers compared the biological interpretation, not wording or "
                "citation count."
            ),
            "sourceId": "diagnostics",
            "metrics": [
                {
                    "field": "materiallyChangedAnnotations",
                    "format": "number",
                    "label": "Materially changed",
                },
                {
                    "field": "pairedAnnotations",
                    "format": "number",
                    "label": "Paired annotations",
                },
            ],
        },
        {
            "id": "paper-overlap-card",
            "dataset": "summary",
            "description": "Mean paper-set Jaccard between the two repeats of each program.",
            "sourceId": "diagnostics",
            "metrics": [
                {
                    "field": "candidatePaperJaccard",
                    "format": "percent",
                    "label": "Candidate paper overlap",
                },
                {
                    "field": "baselinePaperJaccard",
                    "format": "percent",
                    "label": "Baseline",
                },
                {
                    "field": "paperJaccardDelta",
                    "format": "percent",
                    "label": "Change",
                    "signed": True,
                },
            ],
        },
        {
            "id": "function-stability-card",
            "dataset": "summary",
            "description": (
                "Mean Jaccard of supplied genes with function-support evidence across repeats."
            ),
            "sourceId": "diagnostics",
            "metrics": [
                {
                    "field": "candidateFunctionGeneJaccard",
                    "format": "percent",
                    "label": "Candidate function-gene overlap",
                },
                {
                    "field": "baselineFunctionGeneJaccard",
                    "format": "percent",
                    "label": "Baseline",
                },
                {
                    "field": "functionGeneJaccardDelta",
                    "format": "percent",
                    "label": "Change",
                    "signed": True,
                },
            ],
        },
        {
            "id": "turns-card",
            "dataset": "summary",
            "description": (
                "Candidate operational turns include failed and recovery attempts; two failed "
                "attempts are lower bounds because terminal turn telemetry is missing."
            ),
            "sourceId": "diagnostics",
            "metrics": [
                {
                    "field": "baselineMeanTurns",
                    "format": "number",
                    "label": "Baseline mean turns",
                },
                {
                    "field": "candidateMeanFinalOutputTurns",
                    "format": "number",
                    "label": "Candidate final-output mean",
                },
                {
                    "field": "candidateMeanOperationalMinimumTurns",
                    "format": "number",
                    "label": "Candidate operational minimum",
                },
            ],
        },
    ]
    charts = [
        {
            "id": "case-coverage-chart",
            "type": "horizontalBar",
            "title": "Function-supported coverage by paired program",
            "subtitle": "Mean across two repeats; share of 23 supplied genes",
            "dataset": "case_comparison",
            "sourceId": "comparison",
            "intent": "comparison",
            "question": "Does the candidate improve function-supported coverage in every program?",
            "rationale": "Grouped horizontal bars preserve program-level baseline comparisons.",
            "layout": "full",
            "maxRows": 4,
            "valueFormat": "percent",
            "encodings": {
                "x": {"field": "program", "type": "nominal", "label": "Program"},
                "y": {
                    "fields": ["baselineFunction", "candidateFunction"],
                    "type": "quantitative",
                    "label": "Function-supported coverage",
                    "format": "percent",
                },
                "tooltip": [
                    {
                        "field": "functionDelta",
                        "type": "quantitative",
                        "label": "Paired change",
                        "format": "percent",
                    }
                ],
            },
        },
        {
            "id": "session-delta-chart",
            "type": "horizontalBar",
            "title": "Function-supported coverage change by session",
            "subtitle": "Candidate minus baseline; all eight paired sessions improved",
            "dataset": "session_comparison",
            "sourceId": "comparison",
            "intent": "comparison",
            "question": "Is the gain consistent across repeat runs?",
            "rationale": "A zero-based delta bar exposes any session-level regression.",
            "layout": "full",
            "maxRows": 8,
            "valueFormat": "percent",
            "encodings": {
                "x": {"field": "session", "type": "nominal", "label": "Session"},
                "y": {
                    "field": "functionDelta",
                    "type": "quantitative",
                    "label": "Coverage change",
                    "format": "percent",
                },
                "tooltip": [
                    {
                        "field": "baselineFunction",
                        "type": "quantitative",
                        "label": "Baseline",
                        "format": "percent",
                    },
                    {
                        "field": "candidateFunction",
                        "type": "quantitative",
                        "label": "Candidate",
                        "format": "percent",
                    },
                ],
            },
        },
        {
            "id": "replicate-paper-chart",
            "type": "horizontalBar",
            "title": "Paper overlap between repeat runs",
            "subtitle": (
                "Mean paper identity became more repeatable in three programs, but remained low"
            ),
            "dataset": "replicate_stability",
            "sourceId": "diagnostics",
            "intent": "comparison",
            "question": "Did deeper research make paper selection more reproducible?",
            "rationale": "Paired bars show the baseline and candidate Jaccard for each program.",
            "layout": "full",
            "maxRows": 4,
            "valueFormat": "percent",
            "encodings": {
                "x": {"field": "program", "type": "nominal", "label": "Program"},
                "y": {
                    "fields": ["baselinePaperJaccard", "candidatePaperJaccard"],
                    "type": "quantitative",
                    "label": "Paper-set Jaccard",
                    "format": "percent",
                },
                "tooltip": [
                    {
                        "field": "paperJaccardDelta",
                        "type": "quantitative",
                        "label": "Change",
                        "format": "percent",
                    },
                    {
                        "field": "candidateFunctionGeneJaccard",
                        "type": "quantitative",
                        "label": "Candidate function-gene Jaccard",
                        "format": "percent",
                    },
                ],
            },
        },
        {
            "id": "turns-chart",
            "type": "horizontalBar",
            "title": "Turns used by each paired session",
            "subtitle": (
                "Candidate bars include every recorded attempt; ≥ values contain incomplete "
                "failure telemetry"
            ),
            "dataset": "turns_per_session",
            "sourceId": "diagnostics",
            "intent": "comparison",
            "question": "How much agent work did the two regimes require per run?",
            "rationale": (
                "Session-level bars expose retries and the large spread hidden by cost aggregates."
            ),
            "layout": "full",
            "maxRows": 8,
            "valueFormat": "number",
            "encodings": {
                "x": {"field": "session", "type": "nominal", "label": "Session"},
                "y": {
                    "fields": ["baselineTurns", "candidateOperationalMinimumTurns"],
                    "type": "quantitative",
                    "label": "Turns",
                },
                "tooltip": [
                    {
                        "field": "candidateFinalOutputTurns",
                        "type": "quantitative",
                        "label": "Final-output attempt",
                    },
                    {
                        "field": "candidateRetryOrFailureMinimumTurns",
                        "type": "quantitative",
                        "label": "Retry / failure minimum",
                    },
                    {
                        "field": "candidateUnknownTurnAttempts",
                        "type": "quantitative",
                        "label": "Attempts with incomplete telemetry",
                    },
                ],
            },
        },
        {
            "id": "phase-turns-chart",
            "type": "bar",
            "title": "Candidate turns by research phase",
            "subtitle": "Final-output work and failed or retried work across all eight sessions",
            "dataset": "phase_composition",
            "sourceId": "diagnostics",
            "intent": "composition",
            "question": "Which research phases consume the candidate turn budget?",
            "rationale": "Stacking separates productive final-output work from retry overhead.",
            "layout": "full",
            "maxRows": 18,
            "valueFormat": "number",
            "options": {"orientation": "vertical", "grouping": "stacked"},
            "encodings": {
                "x": {"field": "phase", "type": "nominal", "label": "Research phase"},
                "y": {
                    "field": "turns",
                    "type": "quantitative",
                    "label": "Turns",
                },
                "color": {
                    "field": "component",
                    "type": "nominal",
                    "label": "Attempt type",
                    "legend": True,
                },
                "tooltip": [
                    {
                        "field": "shareOfRecordedMinimum",
                        "type": "quantitative",
                        "label": "Share of operational minimum",
                        "format": "percent",
                    }
                ],
            },
        },
        {
            "id": "operations-chart",
            "type": "horizontalBar",
            "title": "Candidate operational ratios versus baseline",
            "subtitle": "The production threshold is 1.5× for both measures",
            "dataset": "operation_ratios",
            "sourceId": "structural",
            "intent": "comparison",
            "question": "Does the diagnostic regime meet production cost and latency gates?",
            "rationale": "Ratios place cost and latency on a common baseline-normalized scale.",
            "layout": "full",
            "maxRows": 2,
            "valueFormat": "number",
            "encodings": {
                "x": {"field": "metric", "type": "nominal", "label": "Measure"},
                "y": {
                    "field": "ratio",
                    "type": "quantitative",
                    "label": "Candidate / baseline",
                },
                "tooltip": [
                    {
                        "field": "threshold",
                        "type": "quantitative",
                        "label": "Production threshold",
                    }
                ],
            },
        },
    ]
    tables = [
        {
            "id": "case-table",
            "title": "Exact paired program coverage",
            "subtitle": "Two-run means; changes are percentage-point equivalents on a 0–1 scale.",
            "dataset": "case_comparison",
            "sourceId": "comparison",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "program", "direction": "asc"},
            "columns": [
                {"field": "program", "label": "Program", "type": "text"},
                {"field": "runs", "label": "Runs", "format": "number"},
                {
                    "field": "baselineCitation",
                    "label": "Baseline citation",
                    "format": "percent",
                },
                {
                    "field": "candidateCitation",
                    "label": "Candidate citation",
                    "format": "percent",
                },
                {
                    "field": "citationDelta",
                    "label": "Citation change",
                    "format": "percent",
                    "movement": True,
                },
                {
                    "field": "baselineFunction",
                    "label": "Baseline function",
                    "format": "percent",
                },
                {
                    "field": "candidateFunction",
                    "label": "Candidate function",
                    "format": "percent",
                },
                {
                    "field": "functionDelta",
                    "label": "Function change",
                    "format": "percent",
                    "movement": True,
                },
            ],
        },
        {
            "id": "annotation-table",
            "title": "Paired annotation interpretation",
            "subtitle": (
                "Material change means a different core mechanism, gene role, or validation "
                "hypothesis—not merely added detail."
            ),
            "dataset": "annotation_comparison",
            "sourceId": "diagnostics",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "session", "direction": "asc"},
            "columns": [
                {"field": "session", "label": "Session", "type": "text"},
                {"field": "semanticRelation", "label": "Assessment", "type": "text"},
                {
                    "field": "baselineSupportingGenes",
                    "label": "Baseline support genes",
                    "format": "number",
                },
                {
                    "field": "candidateSupportingGenes",
                    "label": "Candidate support genes",
                    "format": "number",
                },
                {
                    "field": "supportingGeneJaccard",
                    "label": "Cross-regime gene Jaccard",
                    "format": "percent",
                },
                {
                    "field": "addedSupportingGenes",
                    "label": "Added supporting genes",
                    "type": "text",
                },
                {
                    "field": "removedSupportingGenes",
                    "label": "Removed supporting genes",
                    "type": "text",
                },
                {
                    "field": "decisionRelevantDifferences",
                    "label": "Biologically consequential difference",
                    "type": "text",
                },
            ],
        },
        {
            "id": "replicate-table",
            "title": "Repeat-run stability by program",
            "subtitle": (
                "Jaccard = intersection ÷ union between repeat 1 and repeat 2; 1.0 is identical."
            ),
            "dataset": "replicate_stability",
            "sourceId": "diagnostics",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "program", "direction": "asc"},
            "columns": [
                {"field": "program", "label": "Program", "type": "text"},
                {
                    "field": "baselinePaperJaccard",
                    "label": "Baseline papers",
                    "format": "percent",
                },
                {
                    "field": "candidatePaperJaccard",
                    "label": "Candidate papers",
                    "format": "percent",
                },
                {
                    "field": "paperJaccardDelta",
                    "label": "Paper change",
                    "format": "percent",
                    "movement": True,
                },
                {
                    "field": "baselineFunctionGeneJaccard",
                    "label": "Baseline function genes",
                    "format": "percent",
                },
                {
                    "field": "candidateFunctionGeneJaccard",
                    "label": "Candidate function genes",
                    "format": "percent",
                },
                {
                    "field": "functionGeneJaccardDelta",
                    "label": "Function-gene change",
                    "format": "percent",
                    "movement": True,
                },
                {
                    "field": "coveredGeneJaccardDelta",
                    "label": "Any-evidence gene change",
                    "format": "percent",
                    "movement": True,
                },
            ],
        },
        {
            "id": "turns-table",
            "title": "Turns by run",
            "subtitle": (
                "Final-output attempt is the run that produced the accepted annotation. Operational "
                "minimum also includes failed and recovery attempts."
            ),
            "dataset": "turns_per_session",
            "sourceId": "diagnostics",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {
                "field": "candidateOperationalMinimumTurns",
                "direction": "desc",
            },
            "columns": [
                {"field": "session", "label": "Session", "type": "text"},
                {"field": "baselineTurns", "label": "Baseline", "format": "number"},
                {
                    "field": "candidateFinalOutputTurns",
                    "label": "Candidate final output",
                    "format": "number",
                },
                {
                    "field": "candidateOperationalMinimumTurns",
                    "label": "Candidate operational ≥",
                    "format": "number",
                },
                {
                    "field": "candidateRetryOrFailureMinimumTurns",
                    "label": "Retry / failure ≥",
                    "format": "number",
                },
                {
                    "field": "candidateAttempts",
                    "label": "Attempts",
                    "format": "number",
                },
                {
                    "field": "candidateUnknownTurnAttempts",
                    "label": "Incomplete attempt telemetry",
                    "format": "number",
                },
                {
                    "field": "supplied_geneTurns",
                    "label": "Supplied-gene",
                    "format": "number",
                },
                {"field": "regulatorTurns", "label": "Regulator", "format": "number"},
                {"field": "gapTurns", "label": "Gap", "format": "number"},
            ],
        },
        {
            "id": "phase-table",
            "title": "Candidate turns by phase",
            "subtitle": (
                "Candidate tool traces carry phase labels. Baseline traces predate those labels, so "
                "a comparable baseline phase split cannot be reconstructed reliably."
            ),
            "dataset": "phase_turns",
            "sourceId": "diagnostics",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "operationalMinimumTurns", "direction": "desc"},
            "columns": [
                {"field": "phaseLabel", "label": "Phase", "type": "text"},
                {
                    "field": "operationalMinimumTurns",
                    "label": "Operational ≥",
                    "format": "number",
                },
                {
                    "field": "finalOutputTurns",
                    "label": "Final-output attempts",
                    "format": "number",
                },
                {
                    "field": "retryOrFailureTurns",
                    "label": "Retry / failed attempts",
                    "format": "number",
                },
                {
                    "field": "shareOfRecordedMinimum",
                    "label": "Share",
                    "format": "percent",
                },
            ],
        },
        {
            "id": "quality-table",
            "title": "Factual-quality matrix",
            "subtitle": "Paired sessions only; rates are weighted by assessable links.",
            "dataset": "quality_matrix",
            "sourceId": "comparison",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "metric", "direction": "asc"},
            "columns": [
                {"field": "metric", "label": "Metric", "type": "text"},
                {"field": "baseline", "label": "Baseline", "format": "number"},
                {"field": "candidate", "label": "Candidate", "format": "number"},
                {
                    "field": "delta",
                    "label": "Change",
                    "format": "number",
                    "movement": True,
                },
                {"field": "interpretation", "label": "Interpretation", "type": "text"},
            ],
        },
        {
            "id": "flags-table",
            "title": "Red-flag instances by reason",
            "subtitle": "Raw flag counts; the candidate contains 165 links versus 150 paired baseline links.",
            "dataset": "red_flags",
            "sourceId": "comparison",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "baseline", "direction": "desc"},
            "columns": [
                {"field": "flag", "label": "Reason", "type": "text"},
                {"field": "baseline", "label": "Baseline", "format": "number"},
                {"field": "candidate", "label": "Candidate", "format": "number"},
                {
                    "field": "delta",
                    "label": "Change",
                    "format": "number",
                    "movement": True,
                },
            ],
        },
        {
            "id": "gates-table",
            "title": "Decision gates",
            "subtitle": (
                "A contradiction requires opposite-direction evidence, not merely weak or missing "
                "support. Plan 005 allowed no increase from baseline."
            ),
            "dataset": "gate_outcomes",
            "sourceId": "comparison",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "status", "direction": "asc"},
            "columns": [
                {"field": "gate", "label": "Gate", "type": "text"},
                {"field": "observed", "label": "Observed", "type": "text"},
                {"field": "status", "label": "Result", "type": "text"},
            ],
        },
        {
            "id": "parameters-table",
            "title": "Limit search and recommended bounded retest",
            "subtitle": "The final row is a proposed experiment, not a validated production optimum.",
            "dataset": "parameter_trials",
            "sourceId": "comparison",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "order", "direction": "asc"},
            "columns": [
                {"field": "order", "label": "Order", "format": "number"},
                {"field": "arm", "label": "Arm", "type": "text"},
                {"field": "regime", "label": "Regime", "type": "text"},
                {"field": "turnLabel", "label": "Turn ceiling", "type": "text"},
                {"field": "budgetUsd", "label": "Budget cap", "format": "currency"},
                {
                    "field": "timeoutSeconds",
                    "label": "Timeout, sec",
                    "format": "number",
                },
                {"field": "concurrency", "label": "Concurrency", "format": "number"},
                {"field": "observed", "label": "Observed", "type": "text"},
                {"field": "decision", "label": "Decision", "type": "text"},
            ],
        },
        {
            "id": "reliability-table",
            "title": "Cross-model agreement",
            "subtitle": "The fixed 20% sample closely matched the full 165-link review.",
            "dataset": "reliability",
            "sourceId": "candidate",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "field", "direction": "asc"},
            "columns": [
                {"field": "field", "label": "Field", "type": "text"},
                {
                    "field": "fixedSampleAgreement",
                    "label": "Fixed sample",
                    "format": "percent",
                },
                {
                    "field": "fullAgreement",
                    "label": "Full review",
                    "format": "percent",
                },
            ],
        },
        {
            "id": "recommendations-table",
            "title": "Evidence-backed improvement plan",
            "subtitle": (
                "Priorities are ordered by avoidable turn volume, factual risk, and dependency."
            ),
            "dataset": "recommendations",
            "sourceId": "diagnostics",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "priority", "direction": "asc"},
            "columns": [
                {"field": "priority", "label": "Priority", "format": "number"},
                {"field": "improvement", "label": "Improvement", "type": "text"},
                {"field": "evidence", "label": "Observed evidence", "type": "text"},
                {"field": "action", "label": "Concrete operation", "type": "text"},
                {"field": "rationale", "label": "Why this should work", "type": "text"},
                {"field": "acceptance", "label": "Next-pilot acceptance", "type": "text"},
            ],
        },
    ]
    blocks = [
        {
            "id": "title",
            "type": "markdown",
            "layout": "full",
            "body": "# Baseline vs retrieval-first research regime",
        },
        {
            "id": "technical-summary",
            "type": "markdown",
            "layout": "full",
            "sourceId": "comparison",
            "body": (
                "## Technical summary\n\n"
                "The evidence is sufficient for an **internal directional comparison**. "
                f"Adjudicated function-supported coverage rose from "
                f"**{coverage['baselineFunction'] * 100:.1f}%** to "
                f"**{coverage['candidateFunction'] * 100:.1f}%** "
                f"(**+{coverage['functionDelta'] * 100:.1f} percentage points**), and all "
                f"{coverage['sessionCount']} paired sessions improved. The extra coverage was "
                f"biologically consequential: **{annotation_summary['materiallyChanged']}/"
                f"{annotation_summary['pairedSessions']}** paired annotations changed a core "
                "mechanism, while the other four gained meaningful refinements. Repeat-run paper "
                f"overlap rose from **{replicate_summary['baselineMeanPaperJaccard']:.3f}** to "
                f"**{replicate_summary['candidateMeanPaperJaccard']:.3f}**, but remains low; "
                "function-supported gene overlap improved more strongly, from "
                f"**{replicate_summary['baselineMeanFunctionGeneJaccard']:.3f}** to "
                f"**{replicate_summary['candidateMeanFunctionGeneJaccard']:.3f}**. The candidate "
                "introduced **2 source-opposed claims (contradictions)**. Operational work was at "
                f"least **{turn_summary['candidateToBaselineOperationalTurnRatioMinimum']:.2f}×** "
                "baseline by reconstructed turns. Known accepted-run cost was at least "
                f"**{operations['candidate_to_baseline_known_cost_ratio']:.2f}×** baseline, and "
                f"p95 latency was **{operations['candidate_to_baseline_p95_duration_ratio']:.2f}×** "
                "baseline. Keep production limits unchanged; retain the retrieval-first design, "
                "checkpoint evidence state, bound low-yield phases, add direction checks, and only "
                "then rerun a bounded pilot."
            ),
        },
        {
            "id": "decision-heading",
            "type": "markdown",
            "layout": "full",
            "body": "## The quality gain is real, but the current regime is not production-ready",
        },
        {
            "id": "headline-metrics",
            "type": "metric-strip",
            "layout": "full",
            "cardIds": [
                "function-card",
                "citation-card",
                "selection-card",
                "operations-card",
                "reliability-card",
                "contradiction-card",
            ],
        },
        {
            "id": "diagnostic-metrics",
            "type": "metric-strip",
            "layout": "full",
            "cardIds": [
                "annotation-change-card",
                "paper-overlap-card",
                "function-stability-card",
                "turns-card",
            ],
        },
        {
            "id": "annotation-finding",
            "type": "markdown",
            "layout": "full",
            "sourceId": "diagnostics",
            "body": (
                "## Half of the annotations changed a core biological interpretation\n\n"
                f"Independent Claude and Codex reviewers classified **"
                f"{annotation_summary['materiallyChanged']}/"
                f"{annotation_summary['pairedSessions']}** paired annotations as materially "
                "changed and **4/8** as the same core with meaningful refinement; **0/8** were "
                "effectively unchanged. They agreed on **7/8** decisions, with the remaining pair "
                "adjudicated separately. P11 was the most interpretation-sensitive: deeper "
                "retrieval replaced or added central vascular mechanisms. P21 was the most stable: "
                "the cholesterol/mevalonate and SCAP–INSIG1–SREBP2 core remained, with gene-level "
                "refinement. P10 and P25 show why the distinction matters: the candidate changed "
                "the predicted assay or phenotype in at least one repeat, including ciliary "
                "mechanosensing versus Hedgehog output and retention versus removal of a "
                "nitrogen/ammonia mechanism. These are useful hypothesis changes, not merely more "
                "citations. Because the runs were stochastic and the whole regime changed at once, "
                "this comparison does not prove that coverage alone caused every interpretation "
                "change."
            ),
        },
        {
            "id": "annotation-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "annotation-table",
        },
        {
            "id": "replicate-finding",
            "type": "markdown",
            "layout": "full",
            "sourceId": "diagnostics",
            "body": (
                "## Paper selection is still unstable; biological conclusions are more repeatable\n\n"
                f"Mean repeat-run paper Jaccard rose from **"
                f"{replicate_summary['baselineMeanPaperJaccard']:.3f}** to **"
                f"{replicate_summary['candidateMeanPaperJaccard']:.3f}** "
                f"(+{replicate_summary['paperJaccardDelta']:.3f}), improving in **"
                f"{replicate_summary['paperJaccardCasesImproved']}/4** programs. That is an "
                "improvement, but a Jaccard of 0.184 still means the two repeats usually retrieved "
                "different papers. Function-supported gene overlap improved more—from **"
                f"{replicate_summary['baselineMeanFunctionGeneJaccard']:.3f}** to **"
                f"{replicate_summary['candidateMeanFunctionGeneJaccard']:.3f}**. The scientific "
                "lesson is that different sources often converged on more similar gene-level "
                "conclusions. P10 is the exception: both paper and function-gene stability fell, "
                "which is consistent with its mechanosensing-versus-Hedgehog interpretation shift."
            ),
        },
        {
            "id": "replicate-chart-block",
            "type": "chart",
            "layout": "full",
            "chartId": "replicate-paper-chart",
        },
        {
            "id": "replicate-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "replicate-table",
        },
        {
            "id": "turns-finding",
            "type": "markdown",
            "layout": "full",
            "sourceId": "diagnostics",
            "body": (
                "## Candidate runs used at least 3.23× as many turns\n\n"
                f"Baseline runs used **29–42 turns** (mean **"
                f"{turn_summary['baselineMeanTurns']:.1f}**). The accepted candidate outputs alone "
                f"used **68–107 turns** (mean **"
                f"{turn_summary['candidateMeanFinalOutputTurns']:.1f}**). Including failed and "
                f"recovery attempts raises the candidate mean to at least **"
                f"{turn_summary['candidateMeanOperationalMinimumTurns']:.1f} turns**, or **"
                f"{turn_summary['candidateToBaselineOperationalTurnRatioMinimum']:.2f}×** baseline. "
                f"Failed or retried attempts account for at least **"
                f"{turn_summary['candidateRetryOrFailureMinimumTurns']} turns** (**"
                f"{turn_summary['candidateRetryOrFailureTurnShare'] * 100:.1f}%** of recorded "
                "candidate work). Two P21 failure attempts lack a terminal turn count, so their "
                "values—and the total—are lower bounds rather than exact totals."
            ),
        },
        {
            "id": "turns-chart-block",
            "type": "chart",
            "layout": "full",
            "chartId": "turns-chart",
        },
        {
            "id": "turns-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "turns-table",
        },
        {
            "id": "phase-finding",
            "type": "markdown",
            "layout": "full",
            "sourceId": "diagnostics",
            "body": (
                "## Supplied-gene search dominates turn use; exact duplicates are not the main issue\n\n"
                f"Supplied-gene work consumed **{supplied_phase['operationalMinimumTurns']} turns** "
                f"(**{supplied_phase['shareOfRecordedMinimum'] * 100:.1f}%** of the candidate "
                "minimum). Even final-output attempts averaged **53.8 supplied-gene turns per "
                f"session**, more than the entire baseline mean. Regulator work added **"
                f"{regulator_phase['operationalMinimumTurns']} turns** and gap filling added **"
                f"{gap_phase['operationalMinimumTurns']}**. Only **"
                f"{turn_summary['candidateDuplicateSearchTurns']}/"
                f"{turn_summary['candidateSearchTurns']}** search turns (**"
                f"{turn_summary['candidateDuplicateSearchRate'] * 100:.1f}%**) repeated an exact "
                "query. Therefore simple string deduplication is not the primary fix; the agent "
                "needs a persistent per-gene evidence state, stopping rules, and resume-after-"
                "failure behavior. Candidate phase labels are available from tool traces. Baseline "
                "traces predate those labels, so a baseline phase split would be guesswork and is "
                "not reported."
            ),
        },
        {
            "id": "phase-chart-block",
            "type": "chart",
            "layout": "full",
            "chartId": "phase-turns-chart",
        },
        {
            "id": "phase-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "phase-table",
        },
        {
            "id": "coverage-finding",
            "type": "markdown",
            "layout": "full",
            "sourceId": "comparison",
            "body": (
                "## Every paired program retained a factual coverage gain\n\n"
                f"Program-level function-supported gains ranged from "
                f"**+{coverage['minimumCaseFunctionDelta'] * 100:.1f}** to "
                f"**+{coverage['maximumCaseFunctionDelta'] * 100:.1f} percentage points** after "
                "full cross-model review and adjudication. This is smaller than the structural "
                "estimate because gene mentions without function support were removed, but the "
                "direction and decision threshold survive."
            ),
        },
        {
            "id": "case-chart-block",
            "type": "chart",
            "layout": "full",
            "chartId": "case-coverage-chart",
        },
        {
            "id": "case-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "case-table",
        },
        {
            "id": "session-finding",
            "type": "markdown",
            "layout": "full",
            "sourceId": "comparison",
            "body": (
                "## Repeat runs support a directional result, not broad generalization\n\n"
                f"All **{coverage['positiveFunctionSessions']}/{coverage['sessionCount']}** paired "
                "sessions improved, including the two repeats for each program. The cohort still "
                "contains only four biological programs, so this establishes a robust internal "
                "engineering signal rather than a general claim about all tissues or gene sets."
            ),
        },
        {
            "id": "session-chart-block",
            "type": "chart",
            "layout": "full",
            "chartId": "session-delta-chart",
        },
        {
            "id": "quality-finding",
            "type": "markdown",
            "layout": "full",
            "sourceId": "comparison",
            "body": (
                "## Gene targeting improved while claim calibration remains mixed\n\n"
                f"Flagged links fell from **{baseline_quality['flagged_link_rate'] * 100:.1f}%** "
                f"to **{candidate_quality['flagged_link_rate'] * 100:.1f}%**. Context overclaim "
                f"was essentially flat (**{baseline_quality['context_overclaim_rate'] * 100:.1f}%** "
                f"to **{candidate_quality['context_overclaim_rate'] * 100:.1f}%**). Wrong-gene "
                "and paralog-only instances each fell from 5 to 1, and wrong-function instances "
                "fell from 17 to 7 despite more reviewed links. The two contradictions expose "
                "condition-sensitive directionality: a fly study described Cep164 as dispensable "
                "for ciliogenesis, and an adult pathological BBB study found an ANGPT2 "
                "vessel-reinforcing role. These do not support unqualified required or "
                "destabilizing claims, so the strict factual non-regression gate does not pass."
            ),
        },
        {
            "id": "quality-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "quality-table",
        },
        {
            "id": "flags-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "flags-table",
        },
        {
            "id": "gates-finding",
            "type": "markdown",
            "layout": "full",
            "body": (
                "## Two source-opposed claims block production promotion\n\n"
                "The baseline paired set had **0 contradictions**; the candidate had **2**. This "
                "gate asks whether the new process added citations whose findings point in the "
                "opposite direction from the generated mechanism. It did, so the gate fails even "
                "though coverage improved. Weak, indirect, or irrelevant support is scored "
                "separately and is not automatically a contradiction."
            ),
        },
        {
            "id": "gates-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "gates-table",
        },
        {
            "id": "operations-finding",
            "type": "markdown",
            "layout": "full",
            "sourceId": "structural",
            "body": (
                "## Cost and latency, not evidence coverage, are the binding constraint\n\n"
                f"Known accepted-run cost was at least "
                f"**{operations['candidate_to_baseline_known_cost_ratio']:.2f}×** baseline and p95 "
                f"duration was **{operations['candidate_to_baseline_p95_duration_ratio']:.2f}×**. "
                "Both exceed the 1.5× production threshold. Missing telemetry from SDK failures "
                "makes the cost ratio a lower bound."
            ),
        },
        {
            "id": "operations-chart-block",
            "type": "chart",
            "layout": "full",
            "chartId": "operations-chart",
        },
        {
            "id": "parameters-finding",
            "type": "markdown",
            "layout": "full",
            "sourceId": "comparison",
            "body": (
                "## No tested limit set is yet an optimized production configuration\n\n"
                "The 30-, 60-, and 120-turn settings were censored or incomplete. Removing the "
                "client turn ceiling produced an evaluable quality result but failed operations. "
                "After reducing repeated searches and the regulator pass, rerun the bounded "
                "**60 turns / $1.25 / 900 seconds** setting. It is the recommended next test, not "
                "a validated optimum."
            ),
        },
        {
            "id": "parameters-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "parameters-table",
        },
        {
            "id": "definitions",
            "type": "markdown",
            "layout": "full",
            "body": (
                "## Scope and metric definitions\n\n"
                "**Cohort:** four programs (P10, P11, P21, P25), two repeats each. "
                "**Citation coverage:** unique supplied genes substantively studied by at least "
                "one retained paper, divided by 23 supplied genes. **Function-supported coverage:** "
                "the same denominator, additionally requiring support for the assigned mechanism. "
                "**Context overclaim:** declared context is more direct than the assessed evidence. "
                "**Contradiction:** the retained source explicitly reports the opposite functional "
                "direction from the generated mechanism claim; weak, indirect, or absent support "
                "alone does not qualify. The strict gate requires the candidate count to be no "
                "higher than baseline. **Replicate Jaccard:** intersection divided by union for the "
                "two repeats of a program; paper Jaccard uses retained paper IDs and function-gene "
                "Jaccard uses supplied genes with function support. **Material annotation change:** "
                "a changed core mechanism, gene role, or decision-relevant validation hypothesis. "
                "**Meaningful refinement:** the core is retained but a non-trivial mechanism, gene "
                "assignment, or specificity is added or removed. **Final-output turns:** turns in "
                "the accepted attempt. **Operational minimum turns:** all recorded final, failed, "
                "and recovery attempts; it is a lower bound where failure telemetry is incomplete. "
                "Regulators never enter the supplied-gene denominator."
            ),
        },
        {
            "id": "validation",
            "type": "markdown",
            "layout": "full",
            "sourceId": "candidate",
            "body": (
                "## Full cross-model review bounds assessor uncertainty\n\n"
                f"All **{full_reliability['double_scored_count']}** candidate links were reviewed "
                "independently by Claude Sonnet and headless Codex `gpt-5.6-sol` high reasoning. "
                f"Exact all-field agreement was **{full_reliability['exact_agreement_rate'] * 100:.1f}%**; "
                f"all **{full_reliability['disagreement_count']}** disagreements were resolved by "
                "a separate Claude Opus high-reasoning adjudicator. Gene and function-gene "
                "agreement were materially higher than exact agreement, showing that uncertainty "
                "was concentrated in context and red-flag labels."
            ),
        },
        {
            "id": "reliability-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "reliability-table",
        },
        {
            "id": "methodology",
            "type": "markdown",
            "layout": "full",
            "body": (
                "## Methodology\n\n"
                "The comparison uses identical session keys across regimes. Candidate factual "
                "metrics come from the adjudicated link-level audit, not structural gene-name "
                "matching. Reviewers received only deterministic windows from frozen PubMed "
                "abstracts or cached open-access full text; positive and contradictory judgments "
                "required exact source quotes with validated offsets. Operational ratios use "
                "accepted-run telemetry and treat missing failure costs as unknown, never zero. "
                "Annotation interpretation was assessed independently on all eight paired outputs "
                "by Claude Sonnet and headless Codex `gpt-5.6-sol` high reasoning; their one "
                "decision disagreement was adjudicated by Claude Opus. Turn counts were "
                "reconstructed from attempt records and tool traces: one recorded tool call per "
                "SDK turn plus the terminal no-tool turn for completed attempts."
            ),
        },
        {
            "id": "limitations",
            "type": "markdown",
            "layout": "full",
            "body": (
                "## Limitations, uncertainty, and robustness\n\n"
                "This is a model-assessed audit, not human ground truth. Full cross-model review "
                "reduces single-model dependence but cannot remove correlated model bias. The four "
                "programs are repeated observations, not a representative biological sample. "
                "Candidate runs used an unbounded diagnostic setting and one recovered session, so "
                "quality and operations cannot be attributed to a production-safe limit set. "
                "Annotation differences are model-assessed and confound stochastic replicate "
                "variation with the regime change; they show what changed, not that coverage alone "
                "caused it. Baseline tool calls lack phase labels, so baseline turns are exact by "
                "run but cannot be split reliably by research phase. Two failed P21 attempts have "
                "incomplete terminal telemetry, making candidate operational turns a lower bound. "
                "Two contradictions are rare but decision-relevant because the preregistered gate "
                "allowed no increase."
            ),
        },
        {
            "id": "next-steps",
            "type": "markdown",
            "layout": "full",
            "sourceId": "diagnostics",
            "body": (
                "## Recommended next improvements\n\n"
                "The next step is not to raise the limit again. First remove work that the trace "
                "shows is avoidable, then retest the bounded configuration. The table states the "
                "observed evidence, exact implementation change, causal rationale, and a measurable "
                "acceptance test for each recommendation. Priorities 1–4 target turn use; 5 makes "
                "biological changes auditable; 6 addresses the factual regression; 7 is the "
                "controlled retest after those changes."
            ),
        },
        {
            "id": "recommendations-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "recommendations-table",
        },
        {
            "id": "further-questions",
            "type": "markdown",
            "layout": "full",
            "body": (
                "## Further questions\n\n"
                "Can a per-gene evidence-state scheduler cut supplied-gene work by 30% without "
                "losing more than two coverage points? Does a top-three regulator cap preserve "
                "context calibration? Will an explicit claim diff make P10/P11 interpretation "
                "shifts reproducible across repeats? Can species/state-aware direction checks "
                "prevent the Cep164 and ANGPT2 reversals without discarding useful cross-context "
                "evidence? These questions now map directly to the next pilot's acceptance tests."
            ),
        },
    ]
    manifest = {
        "version": 1,
        "surface": "report",
        "title": "Baseline vs retrieval-first research regime",
        "description": (
            "Technical paired benchmark of factual quality, selection, reliability, and operations."
        ),
        "generatedAt": generated_at,
        "sources": sources,
        "cards": cards,
        "charts": charts,
        "tables": tables,
        "blocks": blocks,
    }
    snapshot = {
        "version": 1,
        "status": "ready",
        "generatedAt": generated_at,
        "datasets": {
            "summary": summary,
            "case_comparison": case_rows,
            "session_comparison": session_rows,
            "quality_matrix": quality_rows,
            "red_flags": list(comparison["red_flags"]),
            "gate_outcomes": list(comparison["gate_outcomes"]),
            "operation_ratios": operation_rows,
            "parameter_trials": list(comparison["parameter_trials"]),
            "reliability": reliability_rows,
            "annotation_comparison": annotation_rows,
            "replicate_stability": replicate_rows,
            "turns_per_session": turn_rows,
            "phase_turns": phase_rows,
            "phase_composition": phase_composition_rows,
            "recommendations": recommendations,
        },
    }
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": snapshot,
        "sources": sources,
    }


def build_artifact(comparison: Mapping[str, Any], *, generated_at: str) -> dict[str, Any]:
    """Build the concise, decision-first benchmark report."""
    coverage = comparison["coverage"]
    baseline_quality = comparison["baseline_quality"]
    candidate_quality = comparison["candidate_quality"]
    diagnostics = comparison["diagnostics"]
    reconciliation = diagnostics["coverage_reconciliation"]
    reconciliation_summary = reconciliation["summary"]
    module_similarity = diagnostics["module_similarity"]
    semantic_summary = module_similarity["summary"]
    turns = diagnostics["turns"]
    turn_summary = turns["summary"]
    replicate_summary = diagnostics["replicate_stability"]["summary"]

    semantic_by_program: dict[str, dict[str, Any]] = {}
    for row in module_similarity["rows"]:
        program = str(row["program"])
        target = semantic_by_program.setdefault(
            program,
            {
                "program": program,
                "baselineReplicateSimilarity": None,
                "candidateReplicateSimilarity": None,
                "crossSettingR1Similarity": None,
                "crossSettingR2Similarity": None,
            },
        )
        comparison_type = row["comparisonType"]
        score = row["moduleSemanticSimilarity"]
        if comparison_type == "baseline_replicate":
            target["baselineReplicateSimilarity"] = score
        elif comparison_type == "candidate_replicate":
            target["candidateReplicateSimilarity"] = score
        elif row["leftLabel"].endswith("r1"):
            target["crossSettingR1Similarity"] = score
        else:
            target["crossSettingR2Similarity"] = score
    semantic_rows = []
    for program in sorted(semantic_by_program):
        row = semantic_by_program[program]
        row["replicateSimilarityDelta"] = round(
            row["candidateReplicateSimilarity"]
            - row["baselineReplicateSimilarity"],
            6,
        )
        row["crossSettingMeanSimilarity"] = round(
            (
                row["crossSettingR1Similarity"]
                + row["crossSettingR2Similarity"]
            )
            / 2,
            6,
        )
        semantic_rows.append(row)

    coverage_chart_rows = [
        {
            "session": row["session"],
            "measure": measure,
            "delta": row[field],
            "candidateClaimBackedRate": row["candidateClaimBackedRate"],
        }
        for row in reconciliation["rows"]
        for measure, field in (
            ("Claimed genes", "annotationClaimedDelta"),
            ("Audited support", "auditedFunctionDelta"),
        )
    ]
    semantic_chart_rows = [
        {
            "program": row["program"],
            "comparison": label,
            "similarity": row[field],
        }
        for row in semantic_rows
        for label, field in (
            ("Base rep", "baselineReplicateSimilarity"),
            ("Cand rep", "candidateReplicateSimilarity"),
            ("Cross r1", "crossSettingR1Similarity"),
            ("Cross r2", "crossSettingR2Similarity"),
        )
    ]

    baseline_phase = {
        row["phase"]: row["turns"] for row in turns["baseline_phase_totals"]
    }
    candidate_phase = {
        row["phase"]: row for row in turns["candidate_phase_totals"]
    }
    sessions = 8

    def baseline_average(phase: str) -> float:
        return round(float(baseline_phase.get(phase, 0)) / sessions, 3)

    def candidate_average(phase: str, field: str) -> float:
        return round(float(candidate_phase.get(phase, {}).get(field, 0)) / sessions, 3)

    strategy_rows = [
        {
            "order": 1,
            "step": "Setup",
            "baselineStrategy": "Read bundle and discover literature tools.",
            "baselineAverageTurns": baseline_average("setup"),
            "candidateStrategy": "Read bundle, discover tools, initialize ledgers.",
            "candidateFinalAverageTurns": candidate_average("setup", "finalOutputTurns"),
            "candidateOperationalAverageTurns": candidate_average(
                "setup", "operationalMinimumTurns"
            ),
            "phaseControlToday": "Measured; no phase-specific ceiling.",
        },
        {
            "order": 2,
            "step": "Mixed evidence retrieval",
            "baselineStrategy": (
                "Gene, regulator, and hypothesized-theme searches are interleaved and unlabelled."
            ),
            "baselineAverageTurns": baseline_average("legacy_unphased"),
            "candidateStrategy": "Replaced by labelled passes below.",
            "candidateFinalAverageTurns": 0,
            "candidateOperationalAverageTurns": 0,
            "phaseControlToday": "Cannot be separated retrospectively.",
        },
        {
            "order": 3,
            "step": "Supplied-gene pass",
            "baselineStrategy": "No separate pass or complete terminal ledger.",
            "baselineAverageTurns": 0,
            "candidateStrategy": (
                "Research all 23 supplied genes and assign a terminal evidence state."
            ),
            "candidateFinalAverageTurns": candidate_average(
                "supplied_gene", "finalOutputTurns"
            ),
            "candidateOperationalAverageTurns": candidate_average(
                "supplied_gene", "operationalMinimumTurns"
            ),
            "phaseControlToday": "Labelled and measured; not hard-capped.",
        },
        {
            "order": 4,
            "step": "Regulator pass",
            "baselineStrategy": "Regulators are mixed into theme searches.",
            "baselineAverageTurns": 0,
            "candidateStrategy": "Separate regulator ledger after supplied-gene research.",
            "candidateFinalAverageTurns": candidate_average(
                "regulator", "finalOutputTurns"
            ),
            "candidateOperationalAverageTurns": candidate_average(
                "regulator", "operationalMinimumTurns"
            ),
            "phaseControlToday": "Called capped in the prompt, but no numeric hard cap.",
        },
        {
            "order": 5,
            "step": "Theme formation",
            "baselineStrategy": "Themes guide early retrieval.",
            "baselineAverageTurns": 0,
            "candidateStrategy": "Form one to three modules only after evidence retrieval.",
            "candidateFinalAverageTurns": candidate_average(
                "theme", "finalOutputTurns"
            ),
            "candidateOperationalAverageTurns": candidate_average(
                "theme", "operationalMinimumTurns"
            ),
            "phaseControlToday": "Labelled and measured; not hard-capped.",
        },
        {
            "order": 6,
            "step": "Citation expansion",
            "baselineStrategy": "OpenAlex searches may occur inside mixed retrieval.",
            "baselineAverageTurns": 0,
            "candidateStrategy": "At most two anchors per module, one hop each.",
            "candidateFinalAverageTurns": candidate_average(
                "expansion", "finalOutputTurns"
            ),
            "candidateOperationalAverageTurns": candidate_average(
                "expansion", "operationalMinimumTurns"
            ),
            "phaseControlToday": "Six-call maximum is prompt-enforced, not runner-enforced.",
        },
        {
            "order": 7,
            "step": "Gap pass",
            "baselineStrategy": "No explicit terminal gap pass.",
            "baselineAverageTurns": 0,
            "candidateStrategy": "Target genes still lacking evidence.",
            "candidateFinalAverageTurns": candidate_average("gap", "finalOutputTurns"),
            "candidateOperationalAverageTurns": candidate_average(
                "gap", "operationalMinimumTurns"
            ),
            "phaseControlToday": "Labelled and measured; not hard-capped.",
        },
        {
            "order": 8,
            "step": "Finalize",
            "baselineStrategy": "Submit three modules.",
            "baselineAverageTurns": baseline_average("finalize"),
            "candidateStrategy": "Validate ledgers, evidence links, and submit.",
            "candidateFinalAverageTurns": candidate_average(
                "finalize", "finalOutputTurns"
            ),
            "candidateOperationalAverageTurns": candidate_average(
                "finalize", "operationalMinimumTurns"
            ),
            "phaseControlToday": "Global turn limit applies; no reserved finalization budget.",
        },
        {
            "order": 9,
            "step": "Total",
            "baselineStrategy": "Whole accepted run.",
            "baselineAverageTurns": turn_summary["baselineMeanTurns"],
            "candidateStrategy": "Final-output attempt / all recorded attempts.",
            "candidateFinalAverageTurns": turn_summary[
                "candidateMeanFinalOutputTurns"
            ],
            "candidateOperationalAverageTurns": turn_summary[
                "candidateMeanOperationalMinimumTurns"
            ],
            "phaseControlToday": "Only whole-session turns, cost, and timeout are hard limits.",
        },
    ]
    strategy_chart_rows = [
        {
            "order": row["order"],
            "step": row["step"],
            "setting": label,
            "turns": row[field],
        }
        for row in strategy_rows
        for label, field in (
            ("Baseline", "baselineAverageTurns"),
            ("Candidate final", "candidateFinalAverageTurns"),
            ("Candidate all", "candidateOperationalAverageTurns"),
        )
    ]

    phase_control_rows = [
        {
            "order": 1,
            "control": "Whole-session turns, cost, and timeout",
            "currentState": "Hard controlled",
            "how": (
                "Client-counted assistant turns back up the SDK limit; session cost and wall time "
                "are enforced separately."
            ),
        },
        {
            "order": 2,
            "control": "Per-phase turns or tool calls",
            "currentState": "Not hard controlled",
            "how": (
                "Add a PhaseBudget configuration and check research_phase before every literature "
                "tool execution. Return a structured phase_budget_exhausted result so the agent "
                "moves on instead of terminating the session."
            ),
        },
        {
            "order": 3,
            "control": "Per-gene search allocation",
            "currentState": "Not hard controlled",
            "how": (
                "Persist per-gene query/fetch counters with the terminal ledger; allow additional "
                "queries only when the gene lacks function evidence or has unresolved direction."
            ),
        },
        {
            "order": 4,
            "control": "Finalization reserve",
            "currentState": "Not controlled",
            "how": (
                "Reserve the last two turns for validation and submit_result; reject further "
                "retrieval once the reserve is reached."
            ),
        },
        {
            "order": 5,
            "control": "Retry and recovery work",
            "currentState": "Attempts are counted but retrieval restarts",
            "how": (
                "Checkpoint ledgers, verified paper–gene edges, phase counters, and cursor after "
                "each tool result; resume only unfinished genes after transient failure."
            ),
        },
    ]

    biology_rows = [
        {
            "program": "P10",
            "stableCore": "Primary-cilium assembly is stable in both settings.",
            "candidateBenefit": (
                "Audited function support rises from 9→10 and 7→10 genes; claimed genes are more "
                "often evidence-backed."
            ),
            "candidateRisk": (
                "Candidate replicate similarity is 0.560 versus 0.972 baseline. Endothelial "
                "mechanosensing/BBB modules shift toward Hedgehog output and mitochondrial "
                "metabolism; candidate r2 reports only two modules."
            ),
            "assessment": (
                "Baseline is more context-coherent and reproducible; candidate is better grounded "
                "but fragments the secondary interpretation."
            ),
        },
        {
            "program": "P11",
            "stableCore": "Tip-cell sprouting is the only consistently recovered anchor.",
            "candidateBenefit": (
                "Audited support rises from 5→7 and 2→6 genes, adding Kcnj2, Rgs6, Slc1a1, and "
                "Tnfrsf11b evidence."
            ),
            "candidateRisk": (
                "Replicate similarity remains low (0.587 candidate, 0.577 baseline); arteriovenous "
                "identity, BBB transport, TGF-β, and survival modules are repartitioned."
            ),
            "assessment": (
                "Candidate improves evidence, not interpretive convergence. P11 is biologically "
                "heterogeneous and should not be forced into one stable three-module answer."
            ),
        },
        {
            "program": "P21",
            "stableCore": (
                "Mevalonate/cholesterol enzymes and SCAP–INSIG1–SREBP2 regulation dominate."
            ),
            "candidateBenefit": (
                "Audited support rises from 17→18 and 13→20 genes; candidate replicate similarity "
                "is 0.847 versus 0.540 baseline."
            ),
            "candidateRisk": (
                "The third MASLD/lipid-output module remains broader and less specific than the "
                "first two."
            ),
            "assessment": (
                "Candidate is clearly better here: it improves evidence coverage and resolves "
                "baseline direction inconsistencies while preserving the core biology."
            ),
        },
        {
            "program": "P25",
            "stableCore": (
                "Detoxification/conjugation, peroxisomal lipid metabolism, and sulfur metabolism "
                "are recurrent."
            ),
            "candidateBenefit": (
                "Audited support rises from 8→12 and 10→12 genes; claim-backed rates rise strongly."
            ),
            "candidateRisk": (
                "Candidate replicate similarity is 0.622 versus 0.943 baseline. Bile-acid "
                "sulfation, taurine/cysteine metabolism, and nitrogen disposal move between modules."
            ),
            "assessment": (
                "Candidate is more defensible gene by gene; baseline has more stable module "
                "boundaries. Neither is unambiguously better as a biological summary."
            ),
        },
    ]

    decision_rows = [
        {
            "dimension": "Audited function-supported coverage",
            "baseline": f"{coverage['baselineFunction'] * 100:.1f}%",
            "candidate": f"{coverage['candidateFunction'] * 100:.1f}%",
            "favours": "Candidate",
            "interpretation": "More supplied genes have paper-supported functional assignments.",
        },
        {
            "dimension": "Annotation claims backed by audited support",
            "baseline": f"{reconciliation_summary['baselineClaimBackedRate'] * 100:.1f}%",
            "candidate": f"{reconciliation_summary['candidateClaimBackedRate'] * 100:.1f}%",
            "favours": "Candidate",
            "interpretation": "Shorter module gene lists can still be more defensible.",
        },
        {
            "dimension": "Three-module replicate semantic similarity",
            "baseline": f"{semantic_summary['baselineReplicateMean']:.3f}",
            "candidate": f"{semantic_summary['candidateReplicateMean']:.3f}",
            "favours": "Baseline",
            "interpretation": "Candidate modules are not more reproducible overall.",
        },
        {
            "dimension": "Paper-set replicate Jaccard",
            "baseline": f"{replicate_summary['baselineMeanPaperJaccard']:.3f}",
            "candidate": f"{replicate_summary['candidateMeanPaperJaccard']:.3f}",
            "favours": "Candidate, weakly",
            "interpretation": "Source identity improves but remains highly unstable.",
        },
        {
            "dimension": "Context overclaim rate",
            "baseline": f"{baseline_quality['context_overclaim_rate'] * 100:.1f}%",
            "candidate": f"{candidate_quality['context_overclaim_rate'] * 100:.1f}%",
            "favours": "Tie",
            "interpretation": "Deeper retrieval does not improve context calibration.",
        },
        {
            "dimension": "Source-opposed claims",
            "baseline": str(baseline_quality["contradiction_count"]),
            "candidate": str(candidate_quality["contradiction_count"]),
            "favours": "Baseline",
            "interpretation": "Candidate introduces two direction-sensitive factual errors.",
        },
        {
            "dimension": "Mean operational turns",
            "baseline": f"{turn_summary['baselineMeanTurns']:.1f}",
            "candidate": f"≥{turn_summary['candidateMeanOperationalMinimumTurns']:.1f}",
            "favours": "Baseline",
            "interpretation": "Candidate uses at least 3.23× as much agent work.",
        },
    ]

    recommendations = [
        {
            "priority": 1,
            "change": "Use candidate retrieval under a stable module prior",
            "operation": (
                "Carry the previous three-module skeleton into the next run. Update a module only "
                "when new exact gene–paper edges change its mechanism, direction, or predicted "
                "phenotype; otherwise add evidence without renaming the module."
            ),
            "acceptance": (
                "Mean module replicate similarity ≥0.75 and no program falls >0.15 below baseline."
            ),
        },
        {
            "priority": 2,
            "change": "Hard-enforce phase and per-gene budgets",
            "operation": (
                "At the tool boundary, cap supplied-gene, regulator, theme, expansion, and gap "
                "calls; reserve finalization turns and stop further retrieval at the limit."
            ),
            "acceptance": "All eight sessions complete within 60 turns without recovery.",
        },
        {
            "priority": 3,
            "change": "Checkpoint and resume evidence state",
            "operation": (
                "Persist ledgers, verified edges, phase counters, and cursor after each tool result; "
                "retry only the unfinished portion."
            ),
            "acceptance": "Retry/failure work <10% of operational turns.",
        },
        {
            "priority": 4,
            "change": "Make annotation changes explicit",
            "operation": (
                "Emit matched-module scores and an added/removed/reassigned claim diff before "
                "replacing a module."
            ),
            "acceptance": "Every module below 0.75 similarity has a cited change rationale.",
        },
        {
            "priority": 5,
            "change": "Add direction and context checks",
            "operation": (
                "Compare paper direction with each claim and require species, developmental, and "
                "disease-state qualifiers for context-dependent findings."
            ),
            "acceptance": (
                "Zero source-opposed claims; function coverage remains at least baseline +10 points "
                "and claim-backed rate remains ≥90%."
            ),
        },
    ]

    summary = [
        {
            **coverage,
            "baselineClaimBackedRate": reconciliation_summary[
                "baselineClaimBackedRate"
            ],
            "candidateClaimBackedRate": reconciliation_summary[
                "candidateClaimBackedRate"
            ],
            "baselineReplicateSemanticSimilarity": semantic_summary[
                "baselineReplicateMean"
            ],
            "candidateReplicateSemanticSimilarity": semantic_summary[
                "candidateReplicateMean"
            ],
            "crossSettingSemanticSimilarity": semantic_summary["crossSettingMean"],
            "baselineMeanTurns": turn_summary["baselineMeanTurns"],
            "candidateMeanOperationalMinimumTurns": turn_summary[
                "candidateMeanOperationalMinimumTurns"
            ],
        }
    ]

    sources = [
        _source(
            "comparison",
            "Paired benchmark comparison",
            "benchmarks/literature-v1/candidate-evaluable/comparison-report/comparison_metrics.json",
            generated_at=generated_at,
        ),
        _source(
            "diagnostics",
            "Coverage, semantic, replicate, and turn diagnostics",
            "benchmarks/literature-v1/candidate-evaluable/comparison-report/diagnostics.json",
            generated_at=generated_at,
        ),
        _source(
            "module-similarity",
            "Two-reviewer unordered module semantic similarity",
            "benchmarks/literature-v1/candidate-evaluable/module_similarity/final.json",
            generated_at=generated_at,
        ),
        _source(
            "baseline",
            "Baseline adjudicated reference-quality audit",
            "benchmarks/literature-v1/baseline-v4/reference_quality/metrics.json",
            generated_at=generated_at,
        ),
        _source(
            "candidate",
            "Candidate adjudicated reference-quality audit",
            "benchmarks/literature-v1/candidate-evaluable/reference_quality/audit/metrics.json",
            generated_at=generated_at,
        ),
        _source(
            "structural",
            "Structural and operational comparison",
            "benchmarks/literature-v1/candidate-evaluable/structural_comparison.json",
            generated_at=generated_at,
        ),
    ]

    cards = [
        {
            "id": "coverage-card",
            "dataset": "summary",
            "description": "Share of 23 supplied genes with adjudicated function support.",
            "sourceId": "comparison",
            "metrics": [
                {
                    "field": "candidateFunction",
                    "format": "percent",
                    "label": "Candidate coverage",
                },
                {
                    "field": "baselineFunction",
                    "format": "percent",
                    "label": "Baseline",
                },
                {
                    "field": "functionDelta",
                    "format": "percent",
                    "label": "Change",
                    "signed": True,
                },
            ],
        },
        {
            "id": "grounding-card",
            "dataset": "summary",
            "description": "Module supporting genes also present in the factual audit.",
            "sourceId": "diagnostics",
            "metrics": [
                {
                    "field": "candidateClaimBackedRate",
                    "format": "percent",
                    "label": "Candidate claim-backed rate",
                },
                {
                    "field": "baselineClaimBackedRate",
                    "format": "percent",
                    "label": "Baseline",
                },
            ],
        },
        {
            "id": "semantic-card",
            "dataset": "summary",
            "description": "Mean best-matched similarity of the unordered module sets.",
            "sourceId": "module-similarity",
            "metrics": [
                {
                    "field": "candidateReplicateSemanticSimilarity",
                    "format": "percent",
                    "label": "Candidate replicate similarity",
                },
                {
                    "field": "baselineReplicateSemanticSimilarity",
                    "format": "percent",
                    "label": "Baseline",
                },
                {
                    "field": "crossSettingSemanticSimilarity",
                    "format": "percent",
                    "label": "Baseline–candidate",
                },
            ],
        },
        {
            "id": "turn-card",
            "dataset": "summary",
            "description": "Candidate includes every recorded failed and recovery attempt.",
            "sourceId": "diagnostics",
            "metrics": [
                {
                    "field": "candidateMeanOperationalMinimumTurns",
                    "format": "number",
                    "label": "Candidate operational turns ≥",
                },
                {
                    "field": "baselineMeanTurns",
                    "format": "number",
                    "label": "Baseline",
                },
            ],
        },
    ]

    charts = [
        {
            "id": "coverage-reconciliation-chart",
            "type": "bar",
            "title": "Change in annotation gene claims and audited support",
            "subtitle": (
                "Candidate minus baseline counts by paired session; audited support rises in all runs"
            ),
            "dataset": "coverage_chart",
            "sourceId": "diagnostics",
            "intent": "comparison",
            "question": "Why can annotation supporting genes fall while factual coverage rises?",
            "rationale": "Signed grouped bars separate claim breadth from audited support.",
            "layout": "full",
            "maxRows": 16,
            "valueFormat": "number",
            "options": {"orientation": "vertical", "grouping": "grouped"},
            "encodings": {
                "x": {"field": "session", "type": "nominal", "label": "Session"},
                "y": {
                    "field": "delta",
                    "type": "quantitative",
                    "label": "Gene-count change",
                },
                "color": {
                    "field": "measure",
                    "type": "nominal",
                    "label": "Measure",
                },
                "tooltip": [
                    {
                        "field": "candidateClaimBackedRate",
                        "type": "quantitative",
                        "label": "Candidate claim-backed rate",
                        "format": "percent",
                    }
                ],
            },
        },
        {
            "id": "semantic-similarity-chart",
            "type": "bar",
            "title": "Unordered module-set semantic similarity",
            "subtitle": (
                "0–1 score after best one-to-one matching; missing modules score zero"
            ),
            "dataset": "semantic_chart",
            "sourceId": "module-similarity",
            "intent": "comparison",
            "question": "Are module meanings stable across replicates and settings?",
            "rationale": "Grouped program bars make within- and cross-setting stability comparable.",
            "layout": "full",
            "maxRows": 16,
            "valueFormat": "percent",
            "options": {"orientation": "vertical", "grouping": "grouped"},
            "encodings": {
                "x": {"field": "program", "type": "nominal", "label": "Program"},
                "y": {
                    "field": "similarity",
                    "type": "quantitative",
                    "label": "Semantic similarity",
                    "format": "percent",
                },
                "color": {
                    "field": "comparison",
                    "type": "nominal",
                    "label": "Comparison",
                },
            },
        },
        {
            "id": "strategy-turns-chart",
            "type": "bar",
            "title": "Mean turns by major research step",
            "subtitle": (
                "Baseline retrieval is exact but unphased; candidate shows final-output and "
                "all-attempt operational usage"
            ),
            "dataset": "strategy_chart",
            "sourceId": "diagnostics",
            "intent": "comparison",
            "question": "Where does each strategy spend its agent turns?",
            "rationale": "Grouped bars expose candidate phase cost and baseline attribution limits.",
            "layout": "full",
            "maxRows": 27,
            "valueFormat": "number",
            "options": {"orientation": "horizontal", "grouping": "grouped"},
            "encodings": {
                "x": {"field": "step", "type": "nominal", "label": "Major step"},
                "y": {
                    "field": "turns",
                    "type": "quantitative",
                    "label": "Mean turns per session",
                },
                "color": {
                    "field": "setting",
                    "type": "nominal",
                    "label": "Setting",
                },
            },
        },
    ]

    tables = [
        {
            "id": "coverage-reconciliation-table",
            "title": "Claim breadth versus audited function support",
            "subtitle": "Exact supplied-gene counts for all eight paired sessions.",
            "dataset": "coverage_reconciliation",
            "sourceId": "diagnostics",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "session", "direction": "asc"},
            "columns": [
                {"field": "session", "label": "Session", "type": "text"},
                {
                    "field": "baselineAnnotationClaimedGenes",
                    "label": "Baseline claimed",
                    "format": "number",
                },
                {
                    "field": "candidateAnnotationClaimedGenes",
                    "label": "Candidate claimed",
                    "format": "number",
                },
                {
                    "field": "baselineAuditedFunctionGenes",
                    "label": "Baseline audited",
                    "format": "number",
                },
                {
                    "field": "candidateAuditedFunctionGenes",
                    "label": "Candidate audited",
                    "format": "number",
                },
                {
                    "field": "baselineClaimBackedRate",
                    "label": "Baseline claim-backed",
                    "format": "percent",
                },
                {
                    "field": "candidateClaimBackedRate",
                    "label": "Candidate claim-backed",
                    "format": "percent",
                },
            ],
        },
        {
            "id": "semantic-table",
            "title": "Module semantic similarity by program",
            "subtitle": (
                "Two-reviewer mean after deterministic best matching; each cross-setting score "
                "compares the same replicate."
            ),
            "dataset": "semantic_program",
            "sourceId": "module-similarity",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "program", "direction": "asc"},
            "columns": [
                {"field": "program", "label": "Program", "type": "text"},
                {
                    "field": "baselineReplicateSimilarity",
                    "label": "Baseline r1–r2",
                    "format": "percent",
                },
                {
                    "field": "candidateReplicateSimilarity",
                    "label": "Candidate r1–r2",
                    "format": "percent",
                },
                {
                    "field": "replicateSimilarityDelta",
                    "label": "Candidate change",
                    "format": "percent",
                    "movement": True,
                },
                {
                    "field": "crossSettingR1Similarity",
                    "label": "Baseline–candidate r1",
                    "format": "percent",
                },
                {
                    "field": "crossSettingR2Similarity",
                    "label": "Baseline–candidate r2",
                    "format": "percent",
                },
            ],
        },
        {
            "id": "biology-table",
            "title": "Biological assessment by program",
            "subtitle": "Evidence grounding and interpretive stability are evaluated separately.",
            "dataset": "biology_assessment",
            "sourceId": "diagnostics",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "program", "direction": "asc"},
            "columns": [
                {"field": "program", "label": "Program", "type": "text"},
                {"field": "stableCore", "label": "Stable biological core", "type": "text"},
                {
                    "field": "candidateBenefit",
                    "label": "Candidate benefit",
                    "type": "text",
                },
                {"field": "candidateRisk", "label": "Candidate risk", "type": "text"},
                {"field": "assessment", "label": "Assessment", "type": "text"},
            ],
        },
        {
            "id": "strategy-table",
            "title": "Baseline and candidate research strategy",
            "subtitle": (
                "Turns are per-session means; candidate operational values include failed attempts."
            ),
            "dataset": "strategy_turns",
            "sourceId": "diagnostics",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "order", "direction": "asc"},
            "columns": [
                {"field": "order", "label": "#", "format": "number"},
                {"field": "step", "label": "Step", "type": "text"},
                {
                    "field": "baselineStrategy",
                    "label": "Baseline strategy",
                    "type": "text",
                },
                {
                    "field": "baselineAverageTurns",
                    "label": "Baseline turns",
                    "format": "number",
                },
                {
                    "field": "candidateStrategy",
                    "label": "Candidate strategy",
                    "type": "text",
                },
                {
                    "field": "candidateFinalAverageTurns",
                    "label": "Candidate final",
                    "format": "number",
                },
                {
                    "field": "candidateOperationalAverageTurns",
                    "label": "Candidate operational ≥",
                    "format": "number",
                },
                {
                    "field": "phaseControlToday",
                    "label": "Control today",
                    "type": "text",
                },
            ],
        },
        {
            "id": "phase-control-table",
            "title": "What can actually be controlled per step",
            "subtitle": "Current enforcement versus the required hard-control mechanism.",
            "dataset": "phase_controls",
            "sourceId": "diagnostics",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "order", "direction": "asc"},
            "columns": [
                {"field": "order", "label": "#", "format": "number"},
                {"field": "control", "label": "Resource control", "type": "text"},
                {
                    "field": "currentState",
                    "label": "Current state",
                    "type": "text",
                },
                {"field": "how", "label": "How to hard-control it", "type": "text"},
            ],
        },
        {
            "id": "decision-table",
            "title": "Which strategy is better?",
            "subtitle": "The answer changes by decision dimension; no single strategy dominates.",
            "dataset": "decision_scorecard",
            "sourceId": "comparison",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "dimension", "direction": "asc"},
            "columns": [
                {"field": "dimension", "label": "Dimension", "type": "text"},
                {"field": "baseline", "label": "Baseline", "type": "text"},
                {"field": "candidate", "label": "Candidate", "type": "text"},
                {"field": "favours", "label": "Favours", "type": "text"},
                {
                    "field": "interpretation",
                    "label": "Biological / technical meaning",
                    "type": "text",
                },
            ],
        },
        {
            "id": "recommendations-table",
            "title": "Recommended hybrid process and acceptance tests",
            "subtitle": "The next pilot should improve evidence without sacrificing module stability.",
            "dataset": "recommendations",
            "sourceId": "diagnostics",
            "layout": "full",
            "density": "spacious",
            "defaultSort": {"field": "priority", "direction": "asc"},
            "columns": [
                {"field": "priority", "label": "Priority", "format": "number"},
                {"field": "change", "label": "Change", "type": "text"},
                {"field": "operation", "label": "Concrete operation", "type": "text"},
                {"field": "acceptance", "label": "Acceptance test", "type": "text"},
            ],
        },
    ]

    blocks = [
        {
            "id": "title",
            "type": "markdown",
            "layout": "full",
            "body": "# Baseline vs candidate annotation",
        },
        {
            "id": "technical-summary",
            "type": "markdown",
            "layout": "full",
            "sourceId": "comparison",
            "body": (
                "## Technical summary\n\n"
                "**The candidate improves evidence coverage and claim grounding, but it does not "
                "produce a clearly better biological annotation overall.** Adjudicated "
                f"function-supported coverage rises from **{coverage['baselineFunction'] * 100:.1f}%** "
                f"to **{coverage['candidateFunction'] * 100:.1f}%**, and the share of module gene "
                f"claims backed by the audit rises from **"
                f"{reconciliation_summary['baselineClaimBackedRate'] * 100:.1f}%** to **"
                f"{reconciliation_summary['candidateClaimBackedRate'] * 100:.1f}%**. However, "
                f"unordered three-module replicate similarity falls from **"
                f"{semantic_summary['baselineReplicateMean']:.3f}** to **"
                f"{semantic_summary['candidateReplicateMean']:.3f}**, two source-opposed claims "
                f"appear, and operational turns rise from **{turn_summary['baselineMeanTurns']:.1f}** "
                f"to at least **{turn_summary['candidateMeanOperationalMinimumTurns']:.1f}**. "
                "P21 clearly benefits; P10 and P25 gain evidence but lose interpretive stability; "
                "P11 remains intrinsically heterogeneous. The best next design is a hybrid: keep "
                "candidate evidence retrieval, but update a stable module skeleton only when new "
                "evidence justifies a semantic change."
            ),
        },
        {
            "id": "headline-metrics",
            "type": "metric-strip",
            "layout": "full",
            "cardIds": [
                "coverage-card",
                "grounding-card",
                "semantic-card",
                "turn-card",
            ],
        },
        {
            "id": "coverage-explanation",
            "type": "markdown",
            "layout": "full",
            "sourceId": "diagnostics",
            "body": (
                "## Fewer genes in a module can coexist with higher paper coverage\n\n"
                "**The apparent contradiction comes from two different denominators.** "
                "`Annotation claimed genes` counts the unique genes the generated three modules "
                "assign as supporting genes. `Audited function genes` counts supplied genes for "
                "which retained papers were adjudicated to support the assigned function. Baseline "
                "modules often named genes more broadly than their citations justified. Across all "
                f"runs, claimed genes change only from **"
                f"{reconciliation_summary['baselineMeanAnnotationClaimedGenes']:.1f}** to **"
                f"{reconciliation_summary['candidateMeanAnnotationClaimedGenes']:.1f}** per run, "
                "while audited function genes rise from **"
                f"{reconciliation_summary['baselineMeanAuditedFunctionGenes']:.1f}** to **"
                f"{reconciliation_summary['candidateMeanAuditedFunctionGenes']:.1f}**. In P11 r1, "
                "claimed genes fall 11→8 while audited support rises 5→7; in P25 r1 they fall "
                "17→13 while audited support rises 8→12. The candidate is narrower in what it "
                "places inside modules, but those assignments are more often trace-backed."
            ),
        },
        {
            "id": "coverage-chart-block",
            "type": "chart",
            "layout": "full",
            "chartId": "coverage-reconciliation-chart",
        },
        {
            "id": "coverage-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "coverage-reconciliation-table",
        },
        {
            "id": "semantic-explanation",
            "type": "markdown",
            "layout": "full",
            "sourceId": "module-similarity",
            "body": (
                "## Deeper research does not make the three-module interpretation more stable\n\n"
                "**Module order is ignored.** Two independent reviewers scored all nine possible "
                "module pairs from 0 to 1; scores were averaged, then the best one-to-one matching "
                "was selected deterministically. A missing module scores zero. The reviewers "
                f"selected the same matching in **{semantic_summary['matchingAgreementCount']}/"
                f"{semantic_summary['comparisonCount']}** comparisons, and their mean score "
                f"difference was **{semantic_summary['meanReviewerDifference']:.3f}**. Baseline "
                f"replicate similarity averages **{semantic_summary['baselineReplicateMean']:.3f}** "
                f"versus **{semantic_summary['candidateReplicateMean']:.3f}** for the candidate. "
                "This aggregate hides strong heterogeneity: candidate improves P21, is nearly flat "
                "for P11, and is less stable for P10 and P25. The cross-setting mean of **"
                f"{semantic_summary['crossSettingMean']:.3f}** shows that the two strategies often "
                "change more than wording, especially in P10."
            ),
        },
        {
            "id": "semantic-chart-block",
            "type": "chart",
            "layout": "full",
            "chartId": "semantic-similarity-chart",
        },
        {
            "id": "semantic-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "semantic-table",
        },
        {
            "id": "biology-explanation",
            "type": "markdown",
            "layout": "full",
            "sourceId": "diagnostics",
            "body": (
                "## Biological value depends on the program\n\n"
                "**Coverage is not the same as biological coherence.** Direct gene-level evidence "
                "is especially useful for P21, where a canonical cholesterol program provides a "
                "strong prior and the candidate resolves direction inconsistencies. In P10 and "
                "P25, deeper retrieval finds valid gene functions but also promotes secondary "
                "processes into module-level conclusions, changing the program summary. P11 spans "
                "tip-cell angiogenesis, arteriovenous identity, mechanosensing, BBB transport, and "
                "survival; its low replicate scores under both strategies are evidence of genuine "
                "program heterogeneity rather than a simple search failure."
            ),
        },
        {
            "id": "biology-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "biology-table",
        },
        {
            "id": "strategy-explanation",
            "type": "markdown",
            "layout": "full",
            "sourceId": "diagnostics",
            "body": (
                "## Candidate research is more systematic, but most extra work occurs before themes\n\n"
                "Baseline uses **33.1 mixed retrieval turns per run** with genes, regulators, and "
                "hypothesized themes interleaved. Candidate final-output attempts spend **53.8 "
                "turns per run on supplied genes alone**, plus 12.1 on regulators and 7.3 on the "
                "gap pass. Including retries raises supplied-gene work to 74.6 turns per run. The "
                "candidate's ordering is scientifically cleaner, but the resource cost is not "
                "currently bounded by phase."
            ),
        },
        {
            "id": "strategy-chart-block",
            "type": "chart",
            "layout": "full",
            "chartId": "strategy-turns-chart",
        },
        {
            "id": "strategy-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "strategy-table",
        },
        {
            "id": "control-explanation",
            "type": "markdown",
            "layout": "full",
            "sourceId": "diagnostics",
            "body": (
                "## Per-step resource control is feasible, but it is not implemented yet\n\n"
                "**Today the runner hard-controls only whole-session turns, cost, and timeout.** "
                "Research-phase labels provide telemetry; prompt instructions such as a capped "
                "regulator pass or six expansion calls are not hard enforcement. Real control must "
                "sit at the literature-tool boundary: count calls by `research_phase`, target gene, "
                "and tool class before execution; return a structured budget-exhausted result; "
                "persist the counters with the evidence ledger; reserve finalization turns; and "
                "resume from checkpoints after transient failures."
            ),
        },
        {
            "id": "control-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "phase-control-table",
        },
        {
            "id": "decision-explanation",
            "type": "markdown",
            "layout": "full",
            "sourceId": "comparison",
            "body": (
                "## Neither strategy dominates; use a hybrid\n\n"
                "Candidate wins on evidence coverage, gene-level grounding, and several error "
                "types. Baseline wins on semantic replicate stability, direction safety, and "
                "resource efficiency. Context overclaim is effectively tied. Therefore the "
                "biologically defensible choice is not baseline versus candidate as a package: use "
                "candidate retrieval and exact evidence edges, but preserve a stable module prior "
                "and require explicit evidence before changing its meaning."
            ),
        },
        {
            "id": "decision-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "decision-table",
        },
        {
            "id": "definitions",
            "type": "markdown",
            "layout": "full",
            "body": (
                "## Scope, definitions, and uncertainty\n\n"
                "The cohort contains four programs with two repeats each and 23 supplied genes per "
                "session. Function coverage requires adjudicated paper support for the assigned "
                "gene function. Claim-backed rate is the fraction of unique module supporting "
                "genes also present in that session's adjudicated function-supported set. Module "
                "semantic similarity is a two-model score, not human ground truth; it weights "
                "mechanism, output, gene/regulator direction, and context, and penalizes missing "
                "modules. Baseline research turns are exact, but its unlabelled literature calls "
                "cannot be split reliably among gene, regulator, and theme steps. Candidate "
                "operational turns are lower bounds for two failed P21 attempts with incomplete "
                "terminal telemetry. The experiment changes retrieval order, evidence-link "
                "requirements, and resource limits together, so it does not identify a single "
                "causal reason for annotation changes."
            ),
        },
        {
            "id": "next-steps",
            "type": "markdown",
            "layout": "full",
            "sourceId": "diagnostics",
            "body": (
                "## Recommended next experiment\n\n"
                "Implement the hybrid module prior, hard phase budgets, checkpoint/resume, module "
                "diffs, and direction checks together. Then rerun the same eight sessions at the "
                "bounded **60 turns / $1.25 / 900 seconds** setting. Promotion should require "
                "completion without recovery, at least +10 percentage points of function coverage "
                "over baseline, ≥90% claim-backed genes, zero source-opposed claims, and module "
                "replicate similarity ≥0.75 without a large program-level regression."
            ),
        },
        {
            "id": "recommendations-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "recommendations-table",
        },
        {
            "id": "further-questions",
            "type": "markdown",
            "layout": "full",
            "body": (
                "## Further questions\n\n"
                "Should P11 be allowed more than three modules or a hierarchical module with "
                "subprograms? Does preserving a baseline-derived module prior improve candidate "
                "replicate similarity without hiding genuinely new biology? Which gene-level "
                "evidence states require a second search, and can this be learned from the current "
                "ledger rather than fixed uniformly?"
            ),
        },
    ]

    manifest = {
        "version": 1,
        "surface": "report",
        "title": "Baseline vs candidate annotation",
        "description": (
            "Paired assessment of evidence coverage, module semantics, biological value, and "
            "research resource use."
        ),
        "generatedAt": generated_at,
        "sources": sources,
        "cards": cards,
        "charts": charts,
        "tables": tables,
        "blocks": blocks,
    }
    snapshot = {
        "version": 1,
        "status": "ready",
        "generatedAt": generated_at,
        "datasets": {
            "summary": summary,
            "coverage_reconciliation": list(reconciliation["rows"]),
            "coverage_chart": coverage_chart_rows,
            "semantic_program": semantic_rows,
            "semantic_chart": semantic_chart_rows,
            "biology_assessment": biology_rows,
            "strategy_turns": strategy_rows,
            "strategy_chart": strategy_chart_rows,
            "phase_controls": phase_control_rows,
            "decision_scorecard": decision_rows,
            "recommendations": recommendations,
        },
    }
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": snapshot,
        "sources": sources,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-metrics", required=True, type=Path)
    parser.add_argument("--candidate-metrics", required=True, type=Path)
    parser.add_argument("--structural-comparison", required=True, type=Path)
    parser.add_argument("--diagnostics", required=True, type=Path)
    parser.add_argument("--baseline-run", required=True, type=Path)
    parser.add_argument("--arm-b", required=True, type=Path)
    parser.add_argument("--arm-c", required=True, type=Path)
    parser.add_argument("--arm-120", required=True, type=Path)
    parser.add_argument("--unbounded", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    comparison = build_comparison(
        _read_json(args.baseline_metrics),
        _read_json(args.candidate_metrics),
        _read_json(args.structural_comparison),
        _read_json(args.diagnostics),
        baseline_run=_read_json(args.baseline_run),
        arm_b=_read_json(args.arm_b),
        arm_c=_read_json(args.arm_c),
        arm_120=_read_json(args.arm_120),
        unbounded=_read_json(args.unbounded),
    )
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    output = args.out
    output.mkdir(parents=True, exist_ok=True)
    comparison_path = output / "comparison_metrics.json"
    artifact_path = output / "artifact.json"
    _write_json(comparison_path, comparison)
    _write_json(artifact_path, build_artifact(comparison, generated_at=generated_at))
    print(
        json.dumps(
            {
                "comparison": str(comparison_path.resolve()),
                "artifact": str(artifact_path.resolve()),
                "decision": comparison["decision"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
