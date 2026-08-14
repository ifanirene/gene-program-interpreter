"""Offline reference-quality review and reporting for frozen literature benchmarks.

This module is deliberately separate from the production literature pipeline.  It turns a
frozen review packet into compact, per-session tasks, validates model judgments against the
frozen gene and assessor-text contracts, and generates deterministic audit outputs.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
SUPPORT_VALUES = {"supports", "partial", "no", "contradicts", "not_assessable"}
DIRECTNESS_VALUES = {"causal", "observational", "secondary", "background", "unclear"}
DIRECTION_VALUES = {"matches", "unclear", "reversed", "not_applicable"}
CONTEXT_VALUES = {"direct", "partial", "indirect", "not_assessable"}
CONTEXT_ACCURACY_VALUES = {"accurate", "overclaimed", "underclaimed", "not_assessable"}
SOURCE_TYPES = {"abstract", "full_text"}
POSITIVE_OR_CONTRADICTORY = {"supports", "partial", "contradicts"}
RELIABILITY_FIELDS = (
    "support",
    "studied_genes",
    "function_supported_genes",
    "directness",
    "direction",
    "assessed_context",
    "context_label_accuracy",
    "red_flags",
)
RED_FLAG_VALUES = {
    "wrong_gene",
    "paralog_only",
    "background_only",
    "wrong_function",
    "reversed_direction",
    "context_overclaim",
    "secondary_as_direct",
    "insufficient_text",
}


class ReferenceQualityError(ValueError):
    """A reference-quality input or review violates the frozen audit contract."""


def _read_json(path: str | Path) -> Any:
    path_obj = Path(path)
    try:
        return json.loads(path_obj.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceQualityError(f"cannot read valid JSON from {path_obj}: {exc}") from exc


def _write_json(path: Path, value: Any, *, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise ReferenceQualityError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _load_packet(packet_or_path: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    packet = _read_json(packet_or_path) if isinstance(packet_or_path, (str, Path)) else packet_or_path
    if not isinstance(packet, Mapping):
        raise ReferenceQualityError("review packet must be a JSON object")
    items = packet.get("review_items")
    cases = packet.get("review_cases")
    if not isinstance(items, list) or not items:
        raise ReferenceQualityError("review packet review_items must be a non-empty list")
    if not isinstance(cases, list) or not cases:
        raise ReferenceQualityError("review packet review_cases must be a non-empty list")
    result = dict(packet)
    review_ids = [str(item.get("review_id") or "") for item in items]
    if any(not review_id for review_id in review_ids) or len(set(review_ids)) != len(review_ids):
        raise ReferenceQualityError("review packet review_id values must be non-empty and unique")
    session_ids = [str(case.get("session_id") or "") for case in cases]
    if any(not value for value in session_ids) or len(set(session_ids)) != len(session_ids):
        raise ReferenceQualityError("review packet case session_id values must be non-empty and unique")
    return result


def _default_index_path(packet_path: str | Path) -> Path:
    path = Path(packet_path).resolve()
    # baseline-v4/review/review_packet.json -> baseline-v4/assessor_text/index.json
    return path.parent.parent / "assessor_text" / "index.json"


def _context_lookup(index_or_path: Mapping[str, Any] | str | Path | None) -> dict[tuple[str, int, str], str]:
    if index_or_path is None:
        return {}
    index = _read_json(index_or_path) if isinstance(index_or_path, (str, Path)) else index_or_path
    if not isinstance(index, Mapping) or not isinstance(index.get("links"), list):
        raise ReferenceQualityError("assessor index must contain a links list")
    result: dict[tuple[str, int, str], str] = {}
    for link in index["links"]:
        try:
            key = (
                str(link["session_id"]),
                int(link["mechanism_index"]),
                str(link["paper_key"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReferenceQualityError("assessor index contains an invalid link key") from exc
        evidence = link.get("evidence") or {}
        declared = str(evidence.get("context_match") or "")
        if declared not in {"direct", "partial", "indirect"}:
            raise ReferenceQualityError(f"invalid declared context {declared!r} for {key}")
        if key in result and result[key] != declared:
            raise ReferenceQualityError(f"conflicting declared contexts for {key}")
        result[key] = declared
    return result


def _safe_filename(value: str) -> str:
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    if not filename:
        raise ReferenceQualityError(f"cannot form a safe filename from {value!r}")
    return filename


def review_response_template() -> dict[str, Any]:
    """Return the exact response shape expected from one model assessor."""
    return {
        "review_id": None,
        "assessor_id": None,
        "support": None,
        "studied_genes": [],
        "function_supported_genes": [],
        "directness": None,
        "direction": None,
        "assessed_context": None,
        "context_label_accuracy": None,
        "evidence_span": {"text": "", "start": None, "end": None, "source_type": ""},
        "rationale": "",
        "red_flags": [],
    }


def prepare_review_tasks(
    packet_path: str | Path,
    out_dir: str | Path,
    *,
    assessor_index_path: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create compact per-session task files from a frozen review packet.

    Mechanisms and paper text are deduplicated within each session.  The original context label
    is joined from the assessor index because the public packet intentionally blinds that field.
    """
    packet = _load_packet(packet_path)
    if assessor_index_path is None:
        candidate = _default_index_path(packet_path)
        assessor_index_path = candidate if candidate.is_file() else None
    contexts = _context_lookup(assessor_index_path)
    cases = {str(case["session_id"]): case for case in packet["review_cases"]}
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in packet["review_items"]:
        session_id = str(item.get("session_id") or "")
        if session_id not in cases:
            raise ReferenceQualityError(f"review item uses unknown session_id: {session_id}")
        by_session[session_id].append(item)

    task_dir = Path(out_dir)
    manifest_sessions: list[dict[str, Any]] = []
    total_links = 0
    for session_id in sorted(by_session):
        case = cases[session_id]
        core_genes = case.get("core_genes") or []
        if not core_genes or len(core_genes) != len(set(core_genes)):
            raise ReferenceQualityError(f"{session_id}: core_genes must be non-empty and unique")
        mechanisms: dict[str, dict[str, Any]] = {}
        papers: dict[str, dict[str, Any]] = {}
        links: list[dict[str, Any]] = []
        for item in sorted(by_session[session_id], key=lambda value: str(value["review_id"])):
            mechanism_key = str(int(item["mechanism_index"]))
            mechanisms.setdefault(
                mechanism_key,
                {
                    "name": item.get("mechanism_name"),
                    "summary": item.get("mechanism_summary"),
                    "agent_supporting_genes": item.get("supporting_genes") or [],
                    "agent_supporting_regulators": item.get("supporting_regulators") or [],
                },
            )
            paper_key = str(item["paper_key"])
            papers.setdefault(
                paper_key,
                {
                    "paper": item.get("paper") or {},
                    "assessor_text": item.get("assessor_text") or {},
                },
            )
            context_key = (session_id, int(item["mechanism_index"]), paper_key)
            declared = contexts.get(context_key)
            if contexts and declared is None:
                raise ReferenceQualityError(f"assessor index has no declared context for {context_key}")
            links.append(
                {
                    "review_id": item["review_id"],
                    "mechanism_index": int(item["mechanism_index"]),
                    "paper_key": paper_key,
                    "selection_reason": item.get("selection_reason") or "",
                    "declared_context": declared,
                    "required_assessors": int(item.get("required_assessors") or 1),
                }
            )
        task = {
            "schema_version": SCHEMA_VERSION,
            "assessment_type": "model",
            "instructions": {
                "unit": "one session x mechanism x paper link",
                "context_policy": (
                    "Context is descriptive, not a support gate. Evidence from another tissue or "
                    "cell type may support a function when labeled partial or indirect."
                ),
                "span_policy": (
                    "supports, partial, and contradicts require an exact substring of the cached "
                    "assessor text with zero-based [start,end) offsets."
                ),
                "allowed_values": {
                    "support": sorted(SUPPORT_VALUES),
                    "directness": sorted(DIRECTNESS_VALUES),
                    "direction": sorted(DIRECTION_VALUES),
                    "assessed_context": sorted(CONTEXT_VALUES),
                    "context_label_accuracy": sorted(CONTEXT_ACCURACY_VALUES),
                },
            },
            "session": {
                "session_id": session_id,
                "case_id": case.get("case_id"),
                "program_id": case.get("program_id"),
                "context": case.get("context") or {},
                "supplied_genes": list(core_genes),
                "regulators": list(case.get("regulators") or []),
            },
            "mechanisms": mechanisms,
            "papers": papers,
            "links": links,
            "response_template": review_response_template(),
        }
        filename = f"{_safe_filename(session_id)}.json"
        _write_json(task_dir / filename, task, overwrite=overwrite)
        manifest_sessions.append(
            {"session_id": session_id, "task_path": filename, "review_links": len(links)}
        )
        total_links += len(links)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "assessment_type": "model",
        "packet_path": str(Path(packet_path).resolve()),
        "assessor_index_path": (
            str(Path(assessor_index_path).resolve()) if assessor_index_path is not None else None
        ),
        "sessions": manifest_sessions,
        "total_review_links": total_links,
    }
    _write_json(task_dir / "manifest.json", manifest, overwrite=overwrite)
    return manifest


def _case_map(packet: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(case["session_id"]): case for case in packet["review_cases"]}


def _item_map(packet: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(item["review_id"]): item for item in packet["review_items"]}


def _expected_context_accuracy(declared: str | None, assessed: str) -> str:
    if not declared or assessed == "not_assessable":
        return "not_assessable"
    levels = {"indirect": 0, "partial": 1, "direct": 2}
    if declared not in levels or assessed not in levels:
        return "not_assessable"
    if levels[declared] == levels[assessed]:
        return "accurate"
    if levels[declared] > levels[assessed]:
        return "overclaimed"
    return "underclaimed"


def _canonical_genes(values: Any, supplied_genes: Sequence[str], *, field: str) -> list[str]:
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ReferenceQualityError(f"{field} must be a list of gene strings")
    canonical = {gene.casefold(): gene for gene in supplied_genes}
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.strip().casefold()
        if not key or key not in canonical:
            raise ReferenceQualityError(f"{field} contains non-supplied gene {value!r}")
        if key in seen:
            raise ReferenceQualityError(f"{field} contains duplicate gene {value!r}")
        seen.add(key)
        result.append(canonical[key])
    return result


def validate_review(
    review: Mapping[str, Any],
    item: Mapping[str, Any],
    supplied_genes: Sequence[str],
    *,
    declared_context: str | None,
) -> dict[str, Any]:
    """Validate and normalize one model judgment against its frozen packet item."""
    if not isinstance(review, Mapping):
        raise ReferenceQualityError("each review must be a JSON object")
    required = set(review_response_template())
    missing = sorted(required - set(review))
    extra = sorted(set(review) - required)
    if missing or extra:
        raise ReferenceQualityError(f"review fields mismatch; missing={missing}, extra={extra}")
    normalized = dict(review)
    review_id = str(review.get("review_id") or "")
    if review_id != str(item.get("review_id") or ""):
        raise ReferenceQualityError(f"review_id does not match packet item: {review_id!r}")
    if not isinstance(review.get("assessor_id"), str) or not review["assessor_id"].strip():
        raise ReferenceQualityError(f"{review_id}: assessor_id must be a non-empty string")
    for field, allowed in (
        ("support", SUPPORT_VALUES),
        ("directness", DIRECTNESS_VALUES),
        ("direction", DIRECTION_VALUES),
        ("assessed_context", CONTEXT_VALUES),
        ("context_label_accuracy", CONTEXT_ACCURACY_VALUES),
    ):
        if review.get(field) not in allowed:
            raise ReferenceQualityError(f"{review_id}: invalid {field}: {review.get(field)!r}")

    studied = _canonical_genes(review.get("studied_genes"), supplied_genes, field="studied_genes")
    function_supported = _canonical_genes(
        review.get("function_supported_genes"),
        supplied_genes,
        field="function_supported_genes",
    )
    if not {gene.casefold() for gene in function_supported}.issubset(
        {gene.casefold() for gene in studied}
    ):
        raise ReferenceQualityError(
            f"{review_id}: function_supported_genes must be a subset of studied_genes"
        )
    if review["support"] in {"no", "not_assessable"} and function_supported:
        raise ReferenceQualityError(
            f"{review_id}: {review['support']} cannot have function_supported_genes"
        )
    normalized["studied_genes"] = studied
    normalized["function_supported_genes"] = function_supported

    span = review.get("evidence_span")
    span_fields = {"text", "start", "end", "source_type"}
    if not isinstance(span, Mapping) or set(span) != span_fields:
        raise ReferenceQualityError(
            f"{review_id}: evidence_span must contain exactly {sorted(span_fields)}"
        )
    span_text = span.get("text")
    if not isinstance(span_text, str):
        raise ReferenceQualityError(f"{review_id}: evidence_span.text must be a string")
    has_span = bool(span_text)
    if review["support"] in POSITIVE_OR_CONTRADICTORY and not has_span:
        raise ReferenceQualityError(f"{review_id}: {review['support']} requires an evidence span")
    if has_span:
        start, end = span.get("start"), span.get("end")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
        ):
            raise ReferenceQualityError(f"{review_id}: evidence offsets must be integers")
        assessor_text = item.get("assessor_text") or {}
        source_text = assessor_text.get("text")
        if not isinstance(source_text, str):
            raise ReferenceQualityError(f"{review_id}: packet assessor text is unavailable")
        if start < 0 or end <= start or end > len(source_text):
            raise ReferenceQualityError(f"{review_id}: evidence offsets are out of range")
        if source_text[start:end] != span_text:
            raise ReferenceQualityError(f"{review_id}: evidence span is not the exact cached substring")
        source_type = span.get("source_type")
        if source_type not in SOURCE_TYPES or source_type != assessor_text.get("text_type"):
            raise ReferenceQualityError(
                f"{review_id}: evidence source_type must match cached assessor text"
            )
    else:
        if span.get("start") is not None or span.get("end") is not None or span.get("source_type"):
            raise ReferenceQualityError(f"{review_id}: empty evidence span must use null offsets/source")

    expected_accuracy = _expected_context_accuracy(declared_context, review["assessed_context"])
    if review["context_label_accuracy"] != expected_accuracy:
        raise ReferenceQualityError(
            f"{review_id}: context_label_accuracy must be {expected_accuracy!r} for declared "
            f"{declared_context!r} versus assessed {review['assessed_context']!r}"
        )
    if not isinstance(review.get("rationale"), str) or not review["rationale"].strip():
        raise ReferenceQualityError(f"{review_id}: rationale must be a non-empty string")
    red_flags = review.get("red_flags")
    if (
        not isinstance(red_flags, list)
        or not all(isinstance(flag, str) and flag.strip() for flag in red_flags)
        or len({flag.strip() for flag in red_flags}) != len(red_flags)
    ):
        raise ReferenceQualityError(f"{review_id}: red_flags must be unique non-empty strings")
    unknown_flags = sorted(set(red_flags) - RED_FLAG_VALUES)
    if unknown_flags:
        raise ReferenceQualityError(
            f"{review_id}: invalid red_flags {unknown_flags}; allowed={sorted(RED_FLAG_VALUES)}"
        )
    normalized["red_flags"] = [flag.strip() for flag in red_flags]
    return normalized


def _reviews_list(reviews_or_path: Mapping[str, Any] | list[Any] | str | Path) -> list[Any]:
    value = _read_json(reviews_or_path) if isinstance(reviews_or_path, (str, Path)) else reviews_or_path
    if isinstance(value, Mapping):
        value = value.get("reviews")
    if not isinstance(value, list):
        raise ReferenceQualityError("review file must be a list or an object with a reviews list")
    return value


def validate_reviews(
    packet_or_path: Mapping[str, Any] | str | Path,
    reviews_or_path: Mapping[str, Any] | list[Any] | str | Path,
    *,
    assessor_index_path: Mapping[str, Any] | str | Path | None = None,
    require_complete: bool = True,
) -> list[dict[str, Any]]:
    """Validate final judgments; aggregation requires exactly one judgment per packet link."""
    packet = _load_packet(packet_or_path)
    if assessor_index_path is None and isinstance(packet_or_path, (str, Path)):
        candidate = _default_index_path(packet_or_path)
        assessor_index_path = candidate if candidate.is_file() else None
    contexts = _context_lookup(assessor_index_path)
    items = _item_map(packet)
    cases = _case_map(packet)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for review in _reviews_list(reviews_or_path):
        if not isinstance(review, Mapping):
            raise ReferenceQualityError("each review must be a JSON object")
        review_id = str(review.get("review_id") or "")
        item = items.get(review_id)
        if item is None:
            raise ReferenceQualityError(f"review references unknown review_id: {review_id!r}")
        if review_id in seen:
            raise ReferenceQualityError(f"duplicate final judgment for review_id {review_id}")
        seen.add(review_id)
        session_id = str(item["session_id"])
        case = cases[session_id]
        key = (session_id, int(item["mechanism_index"]), str(item["paper_key"]))
        declared = contexts.get(key)
        normalized.append(
            validate_review(
                review,
                item,
                case.get("core_genes") or [],
                declared_context=declared,
            )
        )
    if require_complete:
        missing = sorted(set(items) - seen)
        if missing:
            raise ReferenceQualityError(
                f"final reviews are incomplete: missing {len(missing)} of {len(items)} links"
            )
    return normalized


def _reliability_value(field: str, value: Any) -> Any:
    if field in {"studied_genes", "function_supported_genes", "red_flags"}:
        return tuple(sorted(value or [], key=str.casefold))
    return value


def finalize_review_sets(
    packet_or_path: Mapping[str, Any] | str | Path,
    primary_reviews: Mapping[str, Any] | list[Any] | str | Path,
    secondary_reviews: Mapping[str, Any] | list[Any] | str | Path,
    adjudications: Mapping[str, Any] | list[Any] | str | Path | None,
    *,
    assessor_index_path: Mapping[str, Any] | str | Path | None = None,
    secondary_scope: str = "sample",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate independent double scoring and apply adjudications to disagreements."""
    if secondary_scope not in {"sample", "all"}:
        raise ReferenceQualityError("secondary_scope must be 'sample' or 'all'")
    packet = _load_packet(packet_or_path)
    primary = validate_reviews(
        packet,
        primary_reviews,
        assessor_index_path=assessor_index_path,
        require_complete=True,
    )
    secondary = validate_reviews(
        packet,
        secondary_reviews,
        assessor_index_path=assessor_index_path,
        require_complete=False,
    )
    fixed_sample_ids = {
        str(item["review_id"])
        for item in packet["review_items"]
        if int(item.get("required_assessors") or 1) == 2
    }
    all_ids = {str(item["review_id"]) for item in packet["review_items"]}
    comparison_ids = all_ids if secondary_scope == "all" else fixed_sample_ids
    secondary_map = {str(review["review_id"]): review for review in secondary}
    if set(secondary_map) != comparison_ids:
        missing = sorted(comparison_ids - set(secondary_map))
        extra = sorted(set(secondary_map) - comparison_ids)
        raise ReferenceQualityError(
            f"secondary reviews must exactly match the {secondary_scope} double-score set; "
            f"missing={len(missing)}, extra={len(extra)}"
        )
    primary_map = {str(review["review_id"]): review for review in primary}
    disagreement_fields: dict[str, list[str]] = {}
    for review_id in sorted(comparison_ids):
        first = primary_map[review_id]
        second = secondary_map[review_id]
        if first["assessor_id"] == second["assessor_id"]:
            raise ReferenceQualityError(
                f"{review_id}: primary and secondary assessors must be independent"
            )
        differences = []
        for field in RELIABILITY_FIELDS:
            if _reliability_value(field, first.get(field)) == _reliability_value(
                field, second.get(field)
            ):
                continue
            else:
                differences.append(field)
        if differences:
            disagreement_fields[review_id] = differences

    adjudicated = (
        validate_reviews(
            packet,
            adjudications,
            assessor_index_path=assessor_index_path,
            require_complete=False,
        )
        if adjudications is not None
        else []
    )
    adjudication_map = {str(review["review_id"]): review for review in adjudicated}
    disagreement_ids = set(disagreement_fields)
    if set(adjudication_map) != disagreement_ids:
        missing = sorted(disagreement_ids - set(adjudication_map))
        extra = sorted(set(adjudication_map) - disagreement_ids)
        raise ReferenceQualityError(
            "adjudications must exactly match double-score disagreements; "
            f"missing={len(missing)}, extra={len(extra)}"
        )
    for review_id, review in adjudication_map.items():
        assessor_ids = {
            primary_map[review_id]["assessor_id"],
            secondary_map[review_id]["assessor_id"],
        }
        if review["assessor_id"] in assessor_ids:
            raise ReferenceQualityError(
                f"{review_id}: adjudicator must differ from both independent assessors"
            )

    final_map = dict(primary_map)
    final_map.update(adjudication_map)
    ordered_final = [final_map[str(item["review_id"])] for item in packet["review_items"]]

    def reliability_summary(ids: set[str]) -> dict[str, Any]:
        field_agreements = Counter()
        per_link = []
        disagreements = {review_id for review_id in ids if review_id in disagreement_fields}
        for review_id in sorted(ids):
            first = primary_map[review_id]
            second = secondary_map[review_id]
            differences = disagreement_fields.get(review_id, [])
            for field in RELIABILITY_FIELDS:
                if _reliability_value(field, first.get(field)) == _reliability_value(
                    field, second.get(field)
                ):
                    field_agreements[field] += 1
            per_link.append(
                {
                    "review_id": review_id,
                    "primary_assessor": first["assessor_id"],
                    "secondary_assessor": second["assessor_id"],
                    "exact_agreement": not differences,
                    "disagreement_fields": differences,
                    "adjudicator": (
                        adjudication_map[review_id]["assessor_id"]
                        if review_id in adjudication_map
                        else None
                    ),
                }
            )
        return {
            "double_scored_count": len(ids),
            "double_scored_fraction": _ratio(len(ids), len(packet["review_items"])),
            "exact_agreement_count": len(ids) - len(disagreements),
            "exact_agreement_rate": _ratio(len(ids) - len(disagreements), len(ids)),
            "disagreement_count": len(disagreements),
            "adjudicated_count": len(disagreements),
            "field_agreement_rates": {
                field: _ratio(field_agreements[field], len(ids))
                for field in RELIABILITY_FIELDS
            },
            "links": per_link,
        }

    reliability = {
        "schema_version": SCHEMA_VERSION,
        "assessment_type": "model",
        "secondary_scope": secondary_scope,
        **reliability_summary(fixed_sample_ids),
    }
    if secondary_scope == "all":
        reliability["expanded_full_review"] = reliability_summary(comparison_ids)
    return ordered_final, reliability


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return round(len(left & right) / len(union), 6) if union else 1.0


def _count_dict(values: Iterable[str], allowed: Iterable[str]) -> dict[str, int]:
    counts = Counter(values)
    return {value: int(counts.get(value, 0)) for value in sorted(allowed)}


def _derived_red_flags(review: Mapping[str, Any]) -> list[str]:
    result = list(review.get("red_flags") or [])
    for condition, flag in (
        (review.get("support") == "contradicts", "wrong_function"),
        (review.get("direction") == "reversed", "reversed_direction"),
        (review.get("context_label_accuracy") == "overclaimed", "context_overclaim"),
    ):
        if condition and flag not in result:
            result.append(flag)
    return result


def aggregate_reviews(
    packet_or_path: Mapping[str, Any] | str | Path,
    reviews: Sequence[Mapping[str, Any]],
    *,
    assessor_index_path: Mapping[str, Any] | str | Path | None = None,
) -> dict[str, Any]:
    """Aggregate already validated final reviews into per-session and repeat-run metrics."""
    packet = _load_packet(packet_or_path)
    if assessor_index_path is None and isinstance(packet_or_path, (str, Path)):
        candidate = _default_index_path(packet_or_path)
        assessor_index_path = candidate if candidate.is_file() else None
    contexts = _context_lookup(assessor_index_path)
    items = _item_map(packet)
    cases = _case_map(packet)
    review_map = {str(review["review_id"]): review for review in reviews}
    if set(review_map) != set(items):
        raise ReferenceQualityError("aggregation requires one validated judgment for every packet link")

    items_by_session: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in packet["review_items"]:
        items_by_session[str(item["session_id"])].append(item)

    per_session: list[dict[str, Any]] = []
    session_sets: dict[str, dict[str, set[str]]] = {}
    all_support: list[str] = []
    all_directness: list[str] = []
    all_accuracy: list[str] = []
    all_assessed_context: list[str] = []
    all_declared_context: list[str] = []
    red_flag_rows: list[dict[str, Any]] = []
    context_matrix: Counter[tuple[str, str]] = Counter()

    for session_id in sorted(items_by_session):
        case = cases[session_id]
        supplied = list(case.get("core_genes") or [])
        session_items = items_by_session[session_id]
        session_reviews = [review_map[str(item["review_id"])] for item in session_items]
        cited_genes = {gene for review in session_reviews for gene in review["studied_genes"]}
        functional_genes = {
            gene for review in session_reviews for gene in review["function_supported_genes"]
        }
        citations = {str(item["paper_key"]) for item in session_items}
        support_counts = _count_dict((review["support"] for review in session_reviews), SUPPORT_VALUES)
        directness_counts = _count_dict(
            (review["directness"] for review in session_reviews), DIRECTNESS_VALUES
        )
        accuracy_counts = _count_dict(
            (review["context_label_accuracy"] for review in session_reviews),
            CONTEXT_ACCURACY_VALUES,
        )
        assessed_counts = _count_dict(
            (review["assessed_context"] for review in session_reviews), CONTEXT_VALUES
        )
        declared_values: list[str] = []
        flag_count = 0
        flagged_review_ids: set[str] = set()
        for item, review in zip(session_items, session_reviews):
            key = (session_id, int(item["mechanism_index"]), str(item["paper_key"]))
            declared = contexts.get(key)
            if declared:
                declared_values.append(declared)
                context_matrix[(declared, str(review["assessed_context"]))] += 1
            flags = _derived_red_flags(review)
            flag_count += len(flags)
            if flags:
                flagged_review_ids.add(str(item["review_id"]))
            for flag in flags:
                span = review["evidence_span"]
                red_flag_rows.append(
                    {
                        "session_id": session_id,
                        "case_id": case.get("case_id"),
                        "program_id": case.get("program_id"),
                        "review_id": item["review_id"],
                        "mechanism_index": int(item["mechanism_index"]),
                        "mechanism_name": item.get("mechanism_name") or "",
                        "paper_key": item["paper_key"],
                        "pmid": (item.get("paper") or {}).get("pmid") or "",
                        "paper_title": (item.get("paper") or {}).get("title") or "",
                        "red_flag": flag,
                        "support": review["support"],
                        "declared_context": declared or "",
                        "assessed_context": review["assessed_context"],
                        "rationale": review["rationale"],
                        "evidence_span": span.get("text") or "",
                    }
                )
        assessable_accuracy = sum(
            accuracy_counts[value] for value in ("accurate", "overclaimed", "underclaimed")
        )
        row: dict[str, Any] = {
            "session_id": session_id,
            "case_id": case.get("case_id"),
            "program_id": case.get("program_id"),
            "supplied_gene_count": len(supplied),
            "cited_gene_count": len(cited_genes),
            "citation_coverage": _ratio(len(cited_genes), len(supplied)),
            "function_supported_gene_count": len(functional_genes),
            "function_supported_coverage": _ratio(len(functional_genes), len(supplied)),
            "cited_genes": sorted(cited_genes, key=str.casefold),
            "function_supported_genes": sorted(functional_genes, key=str.casefold),
            "review_link_count": len(session_items),
            "unique_citation_count": len(citations),
            "support_distribution": support_counts,
            "directness_distribution": directness_counts,
            "assessed_context_distribution": assessed_counts,
            "declared_context_distribution": _count_dict(
                declared_values, {"direct", "partial", "indirect"}
            ),
            "context_label_accuracy_distribution": accuracy_counts,
            "context_overclaim_rate": _ratio(accuracy_counts["overclaimed"], assessable_accuracy),
            "red_flagged_link_count": len(flagged_review_ids),
            "red_flagged_link_rate": _ratio(len(flagged_review_ids), len(session_items)),
            "red_flag_count": flag_count,
        }
        per_session.append(row)
        session_sets[session_id] = {
            "citations": citations,
            "cited_genes": cited_genes,
            "function_supported_genes": functional_genes,
        }
        all_support.extend(review["support"] for review in session_reviews)
        all_directness.extend(review["directness"] for review in session_reviews)
        all_accuracy.extend(review["context_label_accuracy"] for review in session_reviews)
        all_assessed_context.extend(review["assessed_context"] for review in session_reviews)
        all_declared_context.extend(declared_values)

    per_program: list[dict[str, Any]] = []
    repeats: list[dict[str, Any]] = []
    sessions_by_case: dict[str, list[str]] = defaultdict(list)
    for case in packet["review_cases"]:
        sessions_by_case[str(case.get("case_id") or "")].append(str(case["session_id"]))
    session_rows = {str(row["session_id"]): row for row in per_session}
    for case_id in sorted(sessions_by_case):
        session_ids = sorted(sessions_by_case[case_id])
        case_rows = [cases[session_id] for session_id in session_ids]
        supplied_lists = [list(case.get("core_genes") or []) for case in case_rows]
        if any(set(genes) != set(supplied_lists[0]) for genes in supplied_lists[1:]):
            raise ReferenceQualityError(f"{case_id}: repeated runs do not share supplied genes")
        supplied = supplied_lists[0]
        cited_union = set().union(
            *(session_sets[session_id]["cited_genes"] for session_id in session_ids)
        )
        functional_union = set().union(
            *(session_sets[session_id]["function_supported_genes"] for session_id in session_ids)
        )
        run_citation = [session_rows[session_id]["citation_coverage"] for session_id in session_ids]
        run_function = [
            session_rows[session_id]["function_supported_coverage"] for session_id in session_ids
        ]
        per_program.append(
            {
                "case_id": case_id,
                "program_id": case_rows[0].get("program_id"),
                "run_count": len(session_ids),
                "session_ids": session_ids,
                "supplied_gene_count": len(supplied),
                "cited_gene_count": len(cited_union),
                "citation_coverage": _ratio(len(cited_union), len(supplied)),
                "function_supported_gene_count": len(functional_union),
                "function_supported_coverage": _ratio(len(functional_union), len(supplied)),
                "uncovered_genes": sorted(set(supplied) - cited_union, key=str.casefold),
                "function_unsupported_genes": sorted(
                    set(supplied) - functional_union, key=str.casefold
                ),
                "mean_run_citation_coverage": round(sum(run_citation) / len(run_citation), 6),
                "min_run_citation_coverage": min(run_citation),
                "max_run_citation_coverage": max(run_citation),
                "mean_run_function_supported_coverage": round(
                    sum(run_function) / len(run_function), 6
                ),
            }
        )
        if len(session_ids) < 2:
            continue
        for left_index, left in enumerate(session_ids):
            for right in session_ids[left_index + 1 :]:
                repeats.append(
                    {
                        "case_id": case_id,
                        "left_session_id": left,
                        "right_session_id": right,
                        "citation_jaccard": _jaccard(
                            session_sets[left]["citations"], session_sets[right]["citations"]
                        ),
                        "covered_gene_jaccard": _jaccard(
                            session_sets[left]["cited_genes"], session_sets[right]["cited_genes"]
                        ),
                        "function_supported_gene_jaccard": _jaccard(
                            session_sets[left]["function_supported_genes"],
                            session_sets[right]["function_supported_genes"],
                        ),
                    }
                )

    accuracy_counts = _count_dict(all_accuracy, CONTEXT_ACCURACY_VALUES)
    assessable_accuracy = sum(
        accuracy_counts[value] for value in ("accurate", "overclaimed", "underclaimed")
    )
    flagged_links = {str(row["review_id"]) for row in red_flag_rows}
    aggregate = {
        "review_link_count": len(reviews),
        "session_count": len(per_session),
        "unique_paper_count": len({str(item["paper_key"]) for item in packet["review_items"]}),
        "support_distribution": _count_dict(all_support, SUPPORT_VALUES),
        "directness_distribution": _count_dict(all_directness, DIRECTNESS_VALUES),
        "assessed_context_distribution": _count_dict(all_assessed_context, CONTEXT_VALUES),
        "declared_context_distribution": _count_dict(
            all_declared_context, {"direct", "partial", "indirect"}
        ),
        "context_label_accuracy_distribution": accuracy_counts,
        "context_overclaim_rate": _ratio(accuracy_counts["overclaimed"], assessable_accuracy),
        "contradiction_rate": _ratio(all_support.count("contradicts"), len(all_support)),
        "red_flagged_link_count": len(flagged_links),
        "red_flagged_link_rate": _ratio(len(flagged_links), len(reviews)),
        "red_flag_count": len(red_flag_rows),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "assessment_type": "model",
        "notice": "These citation judgments were produced by model assessors, not humans.",
        "aggregate": aggregate,
        "per_program": per_program,
        "per_session": per_session,
        "repeat_run_stability": repeats,
        "context_calibration_matrix": [
            {"declared_context": declared, "assessed_context": assessed, "count": count}
            for (declared, assessed), count in sorted(context_matrix.items())
        ],
        "red_flags": red_flag_rows,
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise ReferenceQualityError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )


def _percent(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{100 * number:.1f}%" if math.isfinite(number) else "—"


def render_html(metrics: Mapping[str, Any]) -> str:
    """Render a small, dependency-free HTML audit report."""
    aggregate = metrics["aggregate"]
    rows = []
    for session in metrics["per_session"]:
        support = session["support_distribution"]
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(session['session_id']))}</td>"
            f"<td>{html.escape(str(session['program_id']))}</td>"
            f"<td>{session['cited_gene_count']}/{session['supplied_gene_count']} "
            f"({_percent(session['citation_coverage'])})</td>"
            f"<td>{session['function_supported_gene_count']}/{session['supplied_gene_count']} "
            f"({_percent(session['function_supported_coverage'])})</td>"
            f"<td>{support['supports']} / {support['partial']} / {support['no']} / "
            f"{support['contradicts']} / {support['not_assessable']}</td>"
            f"<td>{_percent(session['context_overclaim_rate'])}</td>"
            f"<td>{session['red_flag_count']}</td>"
            "</tr>"
        )
    repeat_rows = []
    for repeat in metrics["repeat_run_stability"]:
        repeat_rows.append(
            "<tr>"
            f"<td>{html.escape(str(repeat['case_id']))}</td>"
            f"<td>{html.escape(str(repeat['left_session_id']))}</td>"
            f"<td>{html.escape(str(repeat['right_session_id']))}</td>"
            f"<td>{repeat['citation_jaccard']:.3f}</td>"
            f"<td>{repeat['covered_gene_jaccard']:.3f}</td>"
            f"<td>{repeat['function_supported_gene_jaccard']:.3f}</td>"
            "</tr>"
        )
    flag_rows = []
    for flag in metrics["red_flags"]:
        flag_rows.append(
            "<tr>"
            f"<td>{html.escape(str(flag['session_id']))}</td>"
            f"<td>{html.escape(str(flag['red_flag']))}</td>"
            f"<td>{html.escape(str(flag['paper_title']))}</td>"
            f"<td>{html.escape(str(flag['support']))}</td>"
            f"<td>{html.escape(str(flag['rationale']))}</td>"
            "</tr>"
        )
    support_json = html.escape(json.dumps(aggregate["support_distribution"], sort_keys=True))
    directness_json = html.escape(json.dumps(aggregate["directness_distribution"], sort_keys=True))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Reference quality audit</title>
<style>
body{{font:15px/1.45 system-ui,sans-serif;margin:0;background:#f5f7fa;color:#17212b}}
main{{max-width:1180px;margin:auto;padding:32px}} h1,h2{{line-height:1.15}}
.notice{{background:#fff1c7;border-left:5px solid #c77d00;padding:12px 16px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:20px 0}}
.card{{background:white;border:1px solid #dce2e8;border-radius:8px;padding:16px}}
.number{{font-size:28px;font-weight:700}} .table{{overflow:auto;background:white;border-radius:8px}}
table{{border-collapse:collapse;width:100%}} th,td{{text-align:left;padding:9px;border-bottom:1px solid #e4e8ec;vertical-align:top}}
th{{background:#eaf0f5;position:sticky;top:0}} code{{white-space:normal}} small{{color:#52606d}}
</style></head><body><main>
<h1>Reference quality audit</h1>
<p class="notice"><strong>Model-assessed audit.</strong> These judgments were produced by models,
not human reviewers. Context is classified separately and is not used as an evidence acceptance gate.</p>
<div class="cards">
<div class="card"><div class="number">{aggregate['review_link_count']}</div><small>citation–mechanism links</small></div>
<div class="card"><div class="number">{aggregate['session_count']}</div><small>sessions</small></div>
<div class="card"><div class="number">{aggregate['unique_paper_count']}</div><small>unique papers</small></div>
<div class="card"><div class="number">{_percent(aggregate['context_overclaim_rate'])}</div><small>context overclaim rate</small></div>
<div class="card"><div class="number">{_percent(aggregate['contradiction_rate'])}</div><small>contradiction rate</small></div>
</div>
<p><strong>Support:</strong> <code>{support_json}</code><br><strong>Directness:</strong> <code>{directness_json}</code></p>
<h2>Per-session supplied-gene coverage</h2>
<p>Coverage denominator is every supplied core gene; perturbation regulators are excluded.</p>
<div class="table"><table><thead><tr><th>Session</th><th>Program</th><th>Genes cited</th>
<th>Functions supported</th><th>Support S / P / N / C / NA</th><th>Context overclaim</th><th>Flags</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>Repeat-run stability</h2><div class="table"><table><thead><tr><th>Case</th><th>Left</th><th>Right</th>
<th>Citation Jaccard</th><th>Covered-gene Jaccard</th><th>Functional-gene Jaccard</th>
</tr></thead><tbody>{''.join(repeat_rows) or '<tr><td colspan="6">No repeated cases.</td></tr>'}</tbody></table></div>
<h2>Red flags</h2><div class="table"><table><thead><tr><th>Session</th><th>Flag</th><th>Paper</th>
<th>Support</th><th>Rationale</th></tr></thead><tbody>{''.join(flag_rows) or '<tr><td colspan="5">No red flags.</td></tr>'}</tbody></table></div>
</main></body></html>"""


def build_portable_artifact(metrics: Mapping[str, Any], *, generated_at: str | None = None) -> dict[str, Any]:
    """Build the canonical portable Data Analytics report artifact."""
    timestamp = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    program_rows = [
        {
            "case": row["case_id"],
            "program": row["program_id"],
            "runs": row["run_count"],
            "suppliedGenes": row["supplied_gene_count"],
            "citedGenes": row["cited_gene_count"],
            "citationCoverage": row["citation_coverage"],
            "functionalGenes": row["function_supported_gene_count"],
            "functionCoverage": row["function_supported_coverage"],
            "meanRunCoverage": row["mean_run_citation_coverage"],
            "uncoveredGenes": ", ".join(row["uncovered_genes"]),
        }
        for row in metrics["per_program"]
    ]
    coverage_rows = [
        {
            "session": row["session_id"],
            "case": row["case_id"],
            "program": row["program_id"],
            "suppliedGenes": row["supplied_gene_count"],
            "citedGenes": row["cited_gene_count"],
            "citationCoverage": row["citation_coverage"],
            "functionalGenes": row["function_supported_gene_count"],
            "functionCoverage": row["function_supported_coverage"],
            "linkCount": row["review_link_count"],
            "uniqueCitations": row["unique_citation_count"],
            "contextOverclaim": row["context_overclaim_rate"],
            "flaggedLinks": row["red_flagged_link_count"],
            "flaggedLinkRate": row["red_flagged_link_rate"],
            "flagInstances": row["red_flag_count"],
        }
        for row in metrics["per_session"]
    ]
    repeat_rows = [
        {
            "case": row["case_id"],
            "leftRun": row["left_session_id"],
            "rightRun": row["right_session_id"],
            "citationJaccard": row["citation_jaccard"],
            "coveredGeneJaccard": row["covered_gene_jaccard"],
            "functionalGeneJaccard": row["function_supported_gene_jaccard"],
        }
        for row in metrics["repeat_run_stability"]
    ]
    flag_rows = [
        {
            "session": row["session_id"],
            "program": row["program_id"],
            "redFlag": row["red_flag"],
            "support": row["support"],
            "declaredContext": row["declared_context"],
            "assessedContext": row["assessed_context"],
            "paperTitle": row["paper_title"],
            "rationale": row["rationale"],
        }
        for row in metrics["red_flags"]
    ]
    aggregate = metrics["aggregate"]
    reliability = metrics.get("reliability")
    supportive_link_count = (
        aggregate["support_distribution"]["supports"]
        + aggregate["support_distribution"]["partial"]
    )
    mean_citation_coverage = (
        sum(row["citationCoverage"] for row in coverage_rows) / len(coverage_rows)
        if coverage_rows
        else None
    )
    mean_function_coverage = (
        sum(row["functionCoverage"] for row in coverage_rows) / len(coverage_rows)
        if coverage_rows
        else None
    )
    summary_rows = [
        {
            "review_link_count": aggregate["review_link_count"],
            "session_count": aggregate["session_count"],
            "unique_paper_count": aggregate["unique_paper_count"],
            "supportive_link_rate": _ratio(
                supportive_link_count, aggregate["review_link_count"]
            ),
            "mean_citation_coverage": mean_citation_coverage,
            "mean_function_supported_coverage": mean_function_coverage,
            "context_overclaim_rate": aggregate["context_overclaim_rate"],
            "contradiction_rate": aggregate["contradiction_rate"],
            "red_flag_count": aggregate["red_flag_count"],
            "red_flagged_link_count": aggregate["red_flagged_link_count"],
            "red_flagged_link_rate": aggregate["red_flagged_link_rate"],
            "exact_agreement_rate": (
                reliability.get("exact_agreement_rate") if reliability else None
            ),
        }
    ]
    reliability_rows = (
        [
            {
                "field": field,
                "agreementRate": rate,
                "doubleScored": reliability["double_scored_count"],
                "disagreements": reliability["disagreement_count"],
                "adjudicated": reliability["adjudicated_count"],
            }
            for field, rate in sorted(reliability["field_agreement_rates"].items())
        ]
        if reliability
        else []
    )
    lowest_coverage = min(coverage_rows, key=lambda row: row["citationCoverage"])
    highest_coverage = max(coverage_rows, key=lambda row: row["citationCoverage"])
    widest_function_gap = max(
        coverage_rows,
        key=lambda row: row["citationCoverage"] - row["functionCoverage"],
    )
    widest_gap = widest_function_gap["citationCoverage"] - widest_function_gap["functionCoverage"]
    repeat_means = {
        field: (
            sum(float(row[field]) for row in repeat_rows) / len(repeat_rows)
            if repeat_rows
            else None
        )
        for field in ("citationJaccard", "coveredGeneJaccard", "functionalGeneJaccard")
    }
    flag_counts = Counter(row["redFlag"] for row in flag_rows)
    top_flag, top_flag_count = flag_counts.most_common(1)[0] if flag_counts else (None, 0)
    sources = [
        {
            "id": "metrics",
            "label": "Reference-quality summary query",
            "path": "sources/summary.sql",
        },
        {
            "id": "sessions",
            "label": "Per-session coverage query",
            "path": "sources/session_coverage.sql",
        },
        {
            "id": "programs",
            "label": "Per-program coverage query",
            "path": "sources/program_coverage.sql",
        },
        {
            "id": "flags",
            "label": "Citation red-flag query",
            "path": "sources/red_flags.sql",
        },
        {
            "id": "repeats",
            "label": "Repeat-run stability query",
            "path": "sources/repeat_stability.sql",
        },
        {
            "id": "reliability",
            "label": "Double-score reliability query",
            "path": "sources/reliability.sql",
        },
    ]
    summary_card_ids = [
        "links-card",
        "citation-coverage-card",
        "function-coverage-card",
        "support-card",
        "context-card",
        "flags-card",
    ]
    if reliability:
        summary_card_ids.append("reliability-card")
    reliability_sentence = (
        f" Exact agreement on the fixed {reliability['double_scored_count']}-link subset was "
        f"**{_percent(reliability['exact_agreement_rate'])}**; all "
        f"{reliability['disagreement_count']} disagreements were independently adjudicated."
        if reliability
        else ""
    )
    blocks: list[dict[str, Any]] = [
        {
            "id": "title",
            "type": "markdown",
            "layout": "full",
            "body": "# Reference quality audit",
        },
        {
            "id": "technical-summary",
            "type": "markdown",
            "layout": "full",
            "sourceId": "metrics",
            "body": (
                "## Technical summary\n\n"
                f"Model assessors reviewed **{aggregate['review_link_count']} citation–mechanism "
                f"links** across **{aggregate['session_count']} sessions**. "
                f"**{_percent(_ratio(supportive_link_count, aggregate['review_link_count']))}** "
                "of links supported or partially supported their citation-specific function. "
                f"Mean supplied-gene citation coverage was **{_percent(mean_citation_coverage)}** "
                "per session, while mean function-supported coverage was "
                f"**{_percent(mean_function_coverage)}**. The agent overclaimed context directness "
                f"for **{_percent(aggregate['context_overclaim_rate'])}** of assessable links. "
                f"{reliability_sentence} These are model judgments, not human ground truth."
            ),
        },
        {
            "id": "scope",
            "type": "markdown",
            "layout": "full",
            "body": (
                "## What was measured\n\n"
                "Citation coverage is the share of all 23 supplied genes studied by at least one "
                "cited paper; function-supported coverage additionally requires support for the "
                "function assigned to that gene. Context is classified independently and is not "
                "an evidence acceptance gate. Evidence from another tissue or cell type remains "
                "useful when labeled partial or indirect."
            ),
        },
        {
            "id": "summary-heading",
            "type": "markdown",
            "layout": "full",
            "body": "## Headline audit measures",
        },
        {
            "id": "summary",
            "type": "metric-strip",
            "layout": "full",
            "cardIds": summary_card_ids,
        },
        {
            "id": "program-finding",
            "type": "markdown",
            "layout": "full",
            "body": (
                "## Per-program supplied-gene coverage\n\n"
                "For repeated programs, union coverage asks whether either run found evidence for "
                "a supplied gene; mean run coverage preserves the typical single-output result. "
                "The uncovered-gene list is the direct search target for the next agent version."
            ),
        },
        {
            "id": "program-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "program-table",
        },
        {
            "id": "coverage-finding",
            "type": "markdown",
            "layout": "full",
            "body": (
                "## Gene coverage varies by program and run\n\n"
                f"Citation coverage ranged from **{_percent(lowest_coverage['citationCoverage'])}** "
                f"in **{lowest_coverage['session']}** to "
                f"**{_percent(highest_coverage['citationCoverage'])}** in "
                f"**{highest_coverage['session']}**. The largest gap between studying a gene and "
                f"supporting its assigned function was **{_percent(widest_gap)}** in "
                f"**{widest_function_gap['session']}**. The chart separates these two questions; "
                "a gap identifies citations that are gene-relevant but functionally weak."
            ),
        },
        {
            "id": "coverage-chart-block",
            "type": "chart",
            "layout": "full",
            "chartId": "coverage-chart",
        },
        {
            "id": "coverage-detail",
            "type": "markdown",
            "layout": "full",
            "body": (
                "## Exact session-level results\n\n"
                "The table preserves the numerator, fixed 23-gene denominator, link volume, "
                "context-calibration error, and red-flag count for audit and comparison."
            ),
        },
        {
            "id": "coverage-table-block",
            "type": "table",
            "layout": "full",
            "tableId": "coverage-table",
        },
    ]
    tables: list[dict[str, Any]] = [
        {
            "id": "program-table",
            "title": "Coverage by unique program",
            "subtitle": "Citation and functional coverage use the union across repeats; mean run coverage shows typical output-level performance.",
            "dataset": "program_coverage",
            "sourceId": "programs",
            "layout": "full",
            "density": "dense",
            "defaultSort": {"field": "program", "direction": "asc"},
            "columns": [
                {"field": "program", "label": "Program", "type": "text"},
                {"field": "runs", "label": "Runs", "format": "number"},
                {"field": "suppliedGenes", "label": "Supplied genes", "format": "number"},
                {"field": "citedGenes", "label": "Genes cited", "format": "number"},
                {"field": "citationCoverage", "label": "Union citation coverage", "format": "percent"},
                {"field": "functionalGenes", "label": "Functions supported", "format": "number"},
                {"field": "functionCoverage", "label": "Union functional coverage", "format": "percent"},
                {"field": "meanRunCoverage", "label": "Mean run coverage", "format": "percent"},
                {"field": "uncoveredGenes", "label": "Uncovered supplied genes", "type": "text"},
            ],
        },
        {
            "id": "coverage-table",
            "title": "Exact per-session coverage",
            "subtitle": "Counts use every supplied core gene as the denominator; regulators are excluded.",
            "dataset": "session_coverage",
            "sourceId": "sessions",
            "layout": "full",
            "density": "dense",
            "defaultSort": {"field": "session", "direction": "asc"},
            "columns": [
                {"field": "session", "label": "Session", "type": "text"},
                {"field": "program", "label": "Program", "type": "text"},
                {"field": "suppliedGenes", "label": "Supplied genes", "format": "number"},
                {"field": "citedGenes", "label": "Genes cited", "format": "number"},
                {"field": "citationCoverage", "label": "Citation coverage", "format": "percent"},
                {
                    "field": "functionalGenes",
                    "label": "Functions supported",
                    "format": "number",
                },
                {
                    "field": "functionCoverage",
                    "label": "Function-supported coverage",
                    "format": "percent",
                },
                {"field": "linkCount", "label": "Links reviewed", "format": "number"},
                {"field": "contextOverclaim", "label": "Context overclaim", "format": "percent"},
                {"field": "flaggedLinks", "label": "Flagged links", "format": "number"},
            ],
        }
    ]
    if repeat_rows:
        tables.append(
            {
                "id": "repeat-table",
                "title": "Repeat-run stability",
                "dataset": "repeat_stability",
                "sourceId": "repeats",
                "layout": "full",
                "density": "dense",
                "columns": [
                    {"field": "case", "label": "Case", "type": "text"},
                    {"field": "leftRun", "label": "Run 1", "type": "text"},
                    {"field": "rightRun", "label": "Run 2", "type": "text"},
                    {"field": "citationJaccard", "label": "Citation Jaccard", "format": "number"},
                    {"field": "coveredGeneJaccard", "label": "Covered-gene Jaccard", "format": "number"},
                    {
                        "field": "functionalGeneJaccard",
                        "label": "Functional-gene Jaccard",
                        "format": "number",
                    },
                ],
            }
        )
        blocks.append(
            {
                "id": "repeat-finding",
                "type": "markdown",
                "layout": "full",
                "body": (
                    "## Repeat runs separate paper instability from biological coverage\n\n"
                    f"Mean citation-set Jaccard was **{repeat_means['citationJaccard']:.3f}**, "
                    f"compared with **{repeat_means['coveredGeneJaccard']:.3f}** for covered genes "
                    f"and **{repeat_means['functionalGeneJaccard']:.3f}** for function-supported "
                    "genes. Comparing these values shows whether different paper selections still "
                    "preserve the same biological coverage."
                ),
            }
        )
        blocks.append(
            {
                "id": "repeat-table-block",
                "type": "table",
                "layout": "full",
                "tableId": "repeat-table",
            }
        )
    if flag_rows:
        tables.append(
            {
                "id": "flag-table",
                "title": "Citation-level red flags",
                "dataset": "red_flags",
                "sourceId": "flags",
                "layout": "full",
                "density": "dense",
                "columns": [
                    {"field": "session", "label": "Session", "type": "text"},
                    {"field": "redFlag", "label": "Flag", "type": "text"},
                    {"field": "paperTitle", "label": "Paper", "type": "text"},
                    {"field": "support", "label": "Support", "type": "text"},
                    {"field": "rationale", "label": "Rationale", "type": "text"},
                ],
            }
        )
        blocks.append(
            {
                "id": "flag-finding",
                "type": "markdown",
                "layout": "full",
                "body": (
                    "## Citation-level failures remain traceable\n\n"
                    f"The most frequent flag was **{top_flag}** ({top_flag_count} instances). "
                    "Each flag retains the paper, support judgment, declared and assessed context, "
                    "and reviewer rationale. Context is flagged only when the agent overstates "
                    "directness, not merely because evidence is indirect."
                ),
            }
        )
        blocks.append(
            {
                "id": "flag-table-block",
                "type": "table",
                "layout": "full",
                "tableId": "flag-table",
            }
        )
    if reliability_rows:
        tables.append(
            {
                "id": "reliability-table",
                "title": "Independent model agreement by field",
                "subtitle": "Fixed 20% double-score subset; disagreements were adjudicated.",
                "dataset": "reliability",
                "sourceId": "reliability",
                "layout": "full",
                "density": "spacious",
                "defaultSort": {"field": "agreementRate", "direction": "asc"},
                "columns": [
                    {"field": "field", "label": "Judgment field", "type": "text"},
                    {"field": "agreementRate", "label": "Agreement", "format": "percent"},
                    {"field": "doubleScored", "label": "Double-scored", "format": "number"},
                    {"field": "disagreements", "label": "Any-field disagreements", "format": "number"},
                    {"field": "adjudicated", "label": "Adjudicated", "format": "number"},
                ],
            }
        )
        blocks.append(
            {
                "id": "reliability-finding",
                "type": "markdown",
                "layout": "full",
                "sourceId": "reliability",
                "body": (
                    "## Independent scoring quantifies model uncertainty\n\n"
                    f"The fixed subset included **{reliability['double_scored_count']} links "
                    f"({_percent(reliability['double_scored_fraction'])})**. Exact agreement across "
                    f"all structured fields was **{_percent(reliability['exact_agreement_rate'])}**. "
                    "Field-level rates show whether uncertainty came from functional support, gene "
                    "assignment, directness, direction, or context classification."
                ),
            }
        )
        blocks.append(
            {
                "id": "reliability-table-block",
                "type": "table",
                "layout": "full",
                "tableId": "reliability-table",
            }
        )
    blocks.extend(
        [
            {
                "id": "methodology",
                "type": "markdown",
                "layout": "full",
                "body": (
                    "## How functional support was assessed\n\n"
                    "For each session–mechanism–paper link, the assessor reduced the selection "
                    "reason to a gene–function claim, read the cached abstract or claim-relevant "
                    "article passage, and recorded support, directness, direction, studied genes, "
                    "function-supported genes, context class, and an exact source span. Positive "
                    "and contradictory judgments could not pass validation without a span that "
                    "exactly matched the frozen text."
                ),
            },
            {
                "id": "limitations",
                "type": "markdown",
                "layout": "full",
                "body": (
                    "## Limitations and robustness\n\n"
                    "This audit is model-assessed. Abstracts were accepted when they explicitly "
                    "reported the relevant result; absent detail was labeled not assessable rather "
                    "than negative. Cached article bodies omit some tables, captions, and "
                    "supplements. A deterministic 20% subset was independently double-scored, and "
                    "disagreements were adjudicated before final aggregation."
                ),
            },
            {
                "id": "next-steps",
                "type": "markdown",
                "layout": "full",
                "body": (
                    "## Recommended next steps\n\n"
                    "Prioritize uncovered supplied genes and links flagged for wrong function or "
                    "reversed direction. Improve the agent prompt so partial and indirect contexts "
                    "are stated explicitly without weakening valid cross-tissue mechanistic evidence."
                ),
            },
            {
                "id": "further-questions",
                "type": "markdown",
                "layout": "full",
                "body": (
                    "## Further questions\n\n"
                    "Do low paper-set Jaccards still preserve covered genes and functional "
                    "conclusions? Which supplied genes remain uncited across both repeats? These "
                    "distinctions determine whether optimization should target search stability, "
                    "gene coverage, or claim calibration."
                ),
            },
        ]
    )
    manifest = {
        "version": 1,
        "surface": "report",
        "title": "Reference quality audit",
        "description": "Model-assessed functional support, gene coverage, context calibration, and repeat stability.",
        "generatedAt": timestamp,
        "cards": [
            {
                "id": "links-card",
                "dataset": "summary",
                "sourceId": "metrics",
                "description": "Every retained session–mechanism–paper link.",
                "metrics": [
                    {"label": "Links reviewed", "field": "review_link_count", "format": "number"}
                ],
            },
            {
                "id": "citation-coverage-card",
                "dataset": "summary",
                "sourceId": "metrics",
                "description": "Mean share of 23 supplied genes studied per session.",
                "metrics": [
                    {
                        "label": "Mean citation coverage",
                        "field": "mean_citation_coverage",
                        "format": "percent",
                    }
                ],
            },
            {
                "id": "function-coverage-card",
                "dataset": "summary",
                "sourceId": "metrics",
                "description": "Mean share with support for the assigned function.",
                "metrics": [
                    {
                        "label": "Mean functional coverage",
                        "field": "mean_function_supported_coverage",
                        "format": "percent",
                    }
                ],
            },
            {
                "id": "support-card",
                "dataset": "summary",
                "sourceId": "metrics",
                "description": "Links scored supports or partial.",
                "metrics": [
                    {
                        "label": "Supportive links",
                        "field": "supportive_link_rate",
                        "format": "percent",
                    }
                ],
            },
            {
                "id": "context-card",
                "dataset": "summary",
                "sourceId": "metrics",
                "description": "Agent label was more direct than the assessed context.",
                "metrics": [
                    {
                        "label": "Context overclaim",
                        "field": "context_overclaim_rate",
                        "format": "percent",
                    }
                ],
            },
            {
                "id": "flags-card",
                "dataset": "summary",
                "sourceId": "metrics",
                "description": "Links with at least one citation-quality flag.",
                "metrics": [
                    {
                        "label": "Flagged links",
                        "field": "red_flagged_link_count",
                        "format": "number",
                    }
                ],
            },
        ],
        "charts": [
            {
                "id": "coverage-chart",
                "title": "Supplied-gene coverage by session",
                "subtitle": "Citation coverage versus coverage with function support",
                "type": "horizontalBar",
                "dataset": "session_coverage",
                "sourceId": "sessions",
                "layout": "full",
                "intent": "comparison",
                "question": "How much of each supplied program has citation and functional support?",
                "rationale": "A grouped horizontal bar makes gaps between cited and function-supported genes visible.",
                "encodings": {
                    "x": {"field": "session", "type": "nominal", "label": "Session"},
                    "y": {
                        "fields": ["citationCoverage", "functionCoverage"],
                        "type": "quantitative",
                        "format": "percent",
                        "label": "Share of supplied genes",
                    },
                    "tooltip": [
                        {"field": "citedGenes", "type": "quantitative", "label": "Genes cited"},
                        {
                            "field": "functionalGenes",
                            "type": "quantitative",
                            "label": "Functions supported",
                        },
                        {"field": "suppliedGenes", "type": "quantitative", "label": "Supplied genes"},
                    ],
                },
                "valueFormat": "percent",
                "maxRows": len(coverage_rows),
            }
        ],
        "tables": tables,
        "sources": sources,
        "blocks": blocks,
    }
    if reliability:
        manifest["cards"].append(
            {
                "id": "reliability-card",
                "dataset": "summary",
                "sourceId": "metrics",
                "description": "Exact agreement across all structured fields in double scoring.",
                "metrics": [
                    {
                        "label": "Exact model agreement",
                        "field": "exact_agreement_rate",
                        "format": "percent",
                    }
                ],
            }
        )
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": {
            "version": 1,
            "generatedAt": timestamp,
            "status": "ready",
            "datasets": {
                "summary": summary_rows,
                "program_coverage": program_rows,
                "session_coverage": coverage_rows,
                "repeat_stability": repeat_rows,
                "red_flags": flag_rows,
                "reliability": reliability_rows,
            },
        },
        "sources": sources,
    }


def write_report_outputs(
    metrics: Mapping[str, Any], out_dir: str | Path, *, overwrite: bool = False
) -> dict[str, str]:
    """Write metrics JSON, coverage CSVs, red-flags CSV, and HTML report."""
    output = Path(out_dir)
    paths = {
        "metrics": output / "metrics.json",
        "per_program": output / "per_program.csv",
        "per_session": output / "per_session.csv",
        "red_flags": output / "red_flags.csv",
        "artifact": output / "artifact.json",
        "report": output / "report.html",
    }
    for path in paths.values():
        if path.exists() and not overwrite:
            raise ReferenceQualityError(f"refusing to overwrite existing file: {path}")
    _write_json(paths["metrics"], metrics, overwrite=overwrite)
    program_fields = [
        "case_id", "program_id", "run_count", "session_ids", "supplied_gene_count",
        "cited_gene_count", "citation_coverage", "function_supported_gene_count",
        "function_supported_coverage", "uncovered_genes", "function_unsupported_genes",
        "mean_run_citation_coverage", "min_run_citation_coverage",
        "max_run_citation_coverage", "mean_run_function_supported_coverage",
    ]
    _write_csv(
        paths["per_program"], metrics["per_program"], program_fields, overwrite=overwrite
    )
    session_fields = [
        "session_id", "case_id", "program_id", "supplied_gene_count", "cited_gene_count",
        "citation_coverage", "function_supported_gene_count", "function_supported_coverage",
        "cited_genes", "function_supported_genes", "review_link_count", "unique_citation_count",
        "support_distribution", "directness_distribution", "assessed_context_distribution",
        "declared_context_distribution", "context_label_accuracy_distribution",
        "context_overclaim_rate", "red_flagged_link_count", "red_flagged_link_rate",
        "red_flag_count",
    ]
    _write_csv(paths["per_session"], metrics["per_session"], session_fields, overwrite=overwrite)
    flag_fields = [
        "session_id", "case_id", "program_id", "review_id", "mechanism_index",
        "mechanism_name", "paper_key", "pmid", "paper_title", "red_flag", "support",
        "declared_context", "assessed_context", "rationale", "evidence_span",
    ]
    _write_csv(paths["red_flags"], metrics["red_flags"], flag_fields, overwrite=overwrite)
    _write_json(paths["artifact"], build_portable_artifact(metrics), overwrite=overwrite)
    paths["report"].write_text(render_html(metrics), encoding="utf-8")
    return {key: str(path.resolve()) for key, path in paths.items()}


def build_report(
    packet_path: str | Path,
    reviews_path: str | Path,
    out_dir: str | Path,
    *,
    assessor_index_path: str | Path | None = None,
    reliability_path: str | Path | None = None,
    overwrite: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate complete final judgments, aggregate them, and write all audit outputs."""
    reviews = validate_reviews(
        packet_path,
        reviews_path,
        assessor_index_path=assessor_index_path,
        require_complete=True,
    )
    metrics = aggregate_reviews(
        packet_path,
        reviews,
        assessor_index_path=assessor_index_path,
    )
    if reliability_path is not None:
        reliability = _read_json(reliability_path)
        if not isinstance(reliability, Mapping):
            raise ReferenceQualityError("reliability artifact must be a JSON object")
        if int(reliability.get("double_scored_count") or 0) <= 0:
            raise ReferenceQualityError("reliability artifact has no double-scored links")
        if reliability.get("disagreement_count") != reliability.get("adjudicated_count"):
            raise ReferenceQualityError("all double-score disagreements must be adjudicated")
        metrics["reliability"] = dict(reliability)
    return metrics, write_report_outputs(metrics, out_dir, overwrite=overwrite)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="create per-session model review tasks")
    prepare.add_argument("--packet", required=True, type=Path)
    prepare.add_argument("--out", required=True, type=Path)
    prepare.add_argument("--assessor-index", type=Path)
    prepare.add_argument("--overwrite", action="store_true")
    validate = subparsers.add_parser("validate", help="validate model judgments")
    validate.add_argument("--packet", required=True, type=Path)
    validate.add_argument("--reviews", required=True, type=Path)
    validate.add_argument("--assessor-index", type=Path)
    validate.add_argument("--allow-incomplete", action="store_true")
    finalize = subparsers.add_parser(
        "finalize", help="validate double scoring and apply independent adjudications"
    )
    finalize.add_argument("--packet", required=True, type=Path)
    finalize.add_argument("--primary", required=True, type=Path)
    finalize.add_argument("--secondary", required=True, type=Path)
    finalize.add_argument("--adjudications", required=True, type=Path)
    finalize.add_argument("--out-final", required=True, type=Path)
    finalize.add_argument("--out-reliability", required=True, type=Path)
    finalize.add_argument("--assessor-index", type=Path)
    finalize.add_argument("--secondary-scope", choices=("sample", "all"), default="sample")
    finalize.add_argument("--overwrite", action="store_true")
    report = subparsers.add_parser("report", help="validate and generate audit outputs")
    report.add_argument("--packet", required=True, type=Path)
    report.add_argument("--reviews", required=True, type=Path)
    report.add_argument("--out", required=True, type=Path)
    report.add_argument("--assessor-index", type=Path)
    report.add_argument("--reliability", type=Path)
    report.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "prepare":
        result = prepare_review_tasks(
            args.packet,
            args.out,
            assessor_index_path=args.assessor_index,
            overwrite=args.overwrite,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "validate":
        result = validate_reviews(
            args.packet,
            args.reviews,
            assessor_index_path=args.assessor_index,
            require_complete=not args.allow_incomplete,
        )
        print(json.dumps({"valid_reviews": len(result)}, indent=2))
        return 0
    if args.command == "finalize":
        final_reviews, reliability = finalize_review_sets(
            args.packet,
            args.primary,
            args.secondary,
            args.adjudications,
            assessor_index_path=args.assessor_index,
            secondary_scope=args.secondary_scope,
        )
        _write_json(
            args.out_final,
            {
                "schema_version": SCHEMA_VERSION,
                "assessment_type": "model",
                "reviews": final_reviews,
            },
            overwrite=args.overwrite,
        )
        _write_json(args.out_reliability, reliability, overwrite=args.overwrite)
        expanded = reliability.get("expanded_full_review")
        expanded_summary = (
            {
                key: expanded[key]
                for key in (
                    "double_scored_count",
                    "double_scored_fraction",
                    "exact_agreement_count",
                    "exact_agreement_rate",
                    "disagreement_count",
                    "adjudicated_count",
                    "field_agreement_rates",
                )
            }
            if expanded is not None
            else None
        )
        print(
            json.dumps(
                {
                    "final_reviews": len(final_reviews),
                    "double_scored": reliability["double_scored_count"],
                    "disagreements": reliability["disagreement_count"],
                    "adjudicated": reliability["adjudicated_count"],
                    "expanded_full_review": expanded_summary,
                },
                indent=2,
            )
        )
        return 0
    metrics, paths = build_report(
        args.packet,
        args.reviews,
        args.out,
        assessor_index_path=args.assessor_index,
        reliability_path=args.reliability,
        overwrite=args.overwrite,
    )
    print(json.dumps({"aggregate": metrics["aggregate"], "outputs": paths}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
