"""
@description
This component adapts *verified* research artifacts into deterministic pipeline
context for downstream prompt generation. It is the evidence-ingest seam: it consumes
the `ResearchResult` objects produced by the research subsystem (one per program,
written as `research_results/{program_id}.json`, verified by `research/verify.py`) and
maps each into the SAME `modules[]` prompt-context shape the annotation engine already
expects — so the annotation engine runs unchanged.

It replaces the old `manual_literature_adapter.py`, which loaded hand-written
manual-literature JSON with a `functional_modules` key. The normalization/validation
patterns (dedupe-preserve-order, PMID validation, per-program loading,
`iter_*_module_rows` CSV flattening, `summarize_*` audit) are preserved.

Mapping (per `ResearchResult`):
  * ONE module per `candidate_mechanism`; `module_rank` is the 1-based index.
  * `module_name`         = mechanism.name
  * `supporting_genes`    = mechanism.supporting_genes
  * `literature_summary`  = mechanism.summary
  * `evidence_ids`        = resolvable identifiers ("PMID:<pmid>" and/or "DOI:<doi>")
                            for the mechanism's linked `Evidence` (a paper is included
                            only if its `Evidence` carries a pmid or doi; both are
                            emitted when present).
  * `status`              = aggregate of linked claims' statuses (see `_module_status`).

@dependencies
- research.schema.ResearchResult: the canonical, verified evidence contract.
- gpi.context_profile.DEFAULT_EVIDENCE_CONTEXT_TYPES: profile-driven evidence vocabulary.

@examples
- load_research_evidence_directory(Path("research_results"), selected_program_ids=[10])
- iter_research_evidence_module_rows(context_by_program)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from research.schema import (
    CandidateMechanism,
    Claim,
    Evidence,
    ResearchResult,
)

# Profile-driven evidence vocabulary. Default to the tissue-agnostic set defined in
# gpi.context_profile — do NOT hard-code liver evidence types here. Callers that want a
# different vocabulary should thread a resolved ContextProfile's `evidence_context_types`.
from gpi.context_profile import DEFAULT_EVIDENCE_CONTEXT_TYPES

VALID_EVIDENCE_CONTEXT_TYPES: List[str] = list(DEFAULT_EVIDENCE_CONTEXT_TYPES)

# Fixed vocabulary for a module's aggregated status (mirrors research.schema.ClaimStatus).
_STATUS_PRIORITY = {"supported": 3, "partial": 2, "unsupported": 1}


# ----------------------------------------------------------------------------- helpers
def extract_program_id_from_filename(path: Path) -> int:
    """Extract a program ID from a research-result filename (e.g. 'P10.json', '10.json')."""
    match = re.search(r"(\d+)", path.stem)
    if not match:
        raise ValueError(f"Could not infer program_id from filename: {path.name}")
    return int(match.group(1))


def _coerce_program_id(value: Any, *, source_file: Optional[Path] = None) -> int:
    """Coerce a program id (int, '10', or 'P10') to an int, falling back to filename."""
    if value is not None:
        text = str(value).strip()
        if re.fullmatch(r"\d+", text):
            return int(text)
        match = re.search(r"(\d+)", text)
        if match:
            return int(match.group(1))
    if source_file is not None:
        return extract_program_id_from_filename(source_file)
    raise ValueError(f"Could not infer program_id from value: {value!r}")


def _dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    """Case-insensitive dedupe that preserves first-seen order (from the old adapter)."""
    seen: Set[str] = set()
    deduped: List[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", str(value).strip())
        if not text:
            continue
        key = text.upper()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped


def _normalize_genes(values: Iterable[Any]) -> List[str]:
    """Normalize a list of gene symbols (dedupe-preserve-order, drop blanks)."""
    normalized: List[str] = []
    for item in values or []:
        if isinstance(item, dict):
            item = item.get("gene")
        text = str(item).strip() if item is not None else ""
        if text:
            normalized.append(text)
    return _dedupe_preserve_order(normalized)


def _normalize_pmid(raw: Any) -> Optional[str]:
    """Return a bare numeric PMID string, tolerating a 'PMID:' prefix; else None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    text = re.sub(r"^pmid[:\s]*", "", text, flags=re.I).strip()
    if not re.fullmatch(r"\d+", text):
        return None
    return text


def _normalize_doi(raw: Any) -> Optional[str]:
    """Return a bare DOI string, stripping URL/'doi:' prefixes; else None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text, flags=re.I).strip()
    text = re.sub(r"^doi[:\s]*", "", text, flags=re.I).strip()
    if not text or not text.startswith("10."):
        return None
    return text


def _evidence_id_strings(
    evidence_ids: Iterable[str],
    evidence_by_id: Dict[str, Evidence],
) -> List[str]:
    """Resolve a mechanism/claim's local evidence ids to 'PMID:<pmid>'/'DOI:<doi>' strings.

    A paper contributes only if its linked `Evidence` carries a pmid or doi. Both are
    emitted when present. Order follows the input evidence-id order.
    """
    resolved: List[str] = []
    for eid in evidence_ids or []:
        evidence = evidence_by_id.get(str(eid).strip())
        if evidence is None:
            continue
        pmid = _normalize_pmid(evidence.pmid)
        doi = _normalize_doi(evidence.doi)
        if pmid:
            resolved.append(f"PMID:{pmid}")
        if doi:
            resolved.append(f"DOI:{doi}")
    return _dedupe_preserve_order(resolved)


def _has_resolvable_evidence(
    evidence_ids: Iterable[str],
    evidence_by_id: Dict[str, Evidence],
) -> bool:
    """True if any linked Evidence carries a pmid or doi (i.e. a resolvable identifier)."""
    return bool(_evidence_id_strings(evidence_ids, evidence_by_id))


def _linked_claims(
    mechanism: CandidateMechanism,
    claims: Sequence[Claim],
) -> List[Claim]:
    """Claims linked to a mechanism by shared local evidence-id overlap."""
    mech_evidence = {str(e).strip() for e in mechanism.evidence_ids if str(e).strip()}
    if not mech_evidence:
        return []
    linked: List[Claim] = []
    for claim in claims:
        claim_evidence = {str(e).strip() for e in claim.evidence_ids if str(e).strip()}
        if mech_evidence & claim_evidence:
            linked.append(claim)
    return linked


def _aggregate_status(statuses: Iterable[str]) -> Optional[str]:
    """Aggregate claim statuses: any 'supported' > any 'partial' > 'unsupported'."""
    best: Optional[str] = None
    best_rank = 0
    for status in statuses:
        rank = _STATUS_PRIORITY.get(status, 0)
        if rank > best_rank:
            best_rank = rank
            best = status
    return best


def _module_status(
    mechanism: CandidateMechanism,
    claims: Sequence[Claim],
    evidence_by_id: Dict[str, Evidence],
) -> str:
    """Aggregate module status from linked claims, with an evidence-resolvability fallback.

    If any linked claim is 'supported' -> 'supported'; else if any 'partial' -> 'partial';
    else 'unsupported'. If no claim shares evidence with the mechanism, default from the
    mechanism's own evidence resolvability: any resolvable (pmid/doi) evidence -> 'partial',
    none -> 'unsupported'.
    """
    linked = _linked_claims(mechanism, claims)
    aggregated = _aggregate_status(c.status for c in linked)
    if aggregated is not None:
        return aggregated
    if _has_resolvable_evidence(mechanism.evidence_ids, evidence_by_id):
        return "partial"
    return "unsupported"


# ----------------------------------------------------------------------- core mapping
def _map_research_result(
    result: ResearchResult,
    *,
    source_file: Path,
) -> Tuple[int, Dict[str, Any]]:
    """Map one verified `ResearchResult` into the annotation `modules[]` context shape."""
    program_id = _coerce_program_id(result.program_id, source_file=source_file)
    evidence_by_id = result.evidence_by_id()

    modules: List[Dict[str, Any]] = []
    for rank, mechanism in enumerate(result.candidate_mechanisms, start=1):
        module_name = str(mechanism.name).strip()
        if not module_name:
            raise ValueError(
                f"{source_file.name}: candidate_mechanism {rank} missing name"
            )
        literature_summary = re.sub(r"\s+", " ", str(mechanism.summary or "").strip())
        modules.append(
            {
                "module_rank": rank,
                "module_name": module_name,
                "supporting_genes": _normalize_genes(mechanism.supporting_genes),
                "evidence_ids": _evidence_id_strings(
                    mechanism.evidence_ids, evidence_by_id
                ),
                "literature_summary": literature_summary,
                "status": _module_status(mechanism, result.claims, evidence_by_id),
                "source_file": source_file.name,
            }
        )

    genes_with_limited_literature = _compute_limited_genes(modules, result.claims)

    unique_evidence_ids = sorted(
        {eid for module in modules for eid in module.get("evidence_ids", [])}
    )
    context = {
        "modules": modules,
        "genes_with_limited_literature": genes_with_limited_literature,
        "source_summary": {
            "source_files": [source_file.name],
            "n_modules": len(modules),
            "n_unique_evidence_ids": len(unique_evidence_ids),
            "n_limited_genes": len(genes_with_limited_literature),
        },
    }
    return program_id, context


def _compute_limited_genes(
    modules: Sequence[Dict[str, Any]],
    claims: Sequence[Claim],
) -> List[str]:
    """Genes that appear only in unsupported evidence.

    Collect supporting_genes from modules with status 'unsupported' and from claims with
    status 'unsupported', then drop any gene that also appears in a 'supported'/'partial'
    module. The adapter does not have the full program gene list, so this is a best-effort
    proxy for "genes with limited literature" (renamed from the old
    `genes_with_limited_hepatocyte_literature`).
    """
    supported_or_partial: Set[str] = set()
    for module in modules:
        if module.get("status") in {"supported", "partial"}:
            for gene in module.get("supporting_genes", []):
                supported_or_partial.add(gene.upper())

    limited_candidates: List[str] = []
    for module in modules:
        if module.get("status") == "unsupported":
            limited_candidates.extend(module.get("supporting_genes", []))
    for claim in claims:
        if claim.status == "unsupported":
            limited_candidates.extend(claim.supporting_genes)

    limited = [
        gene
        for gene in _normalize_genes(limited_candidates)
        if gene.upper() not in supported_or_partial
    ]
    return limited


# ------------------------------------------------------------------------- public API
def normalize_research_result_file(path: Path) -> Tuple[int, Dict[str, Any]]:
    """Read one `research_results/*.json` file and map it to (program_id, context)."""
    path = Path(path)
    try:
        result = ResearchResult.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pydantic ValidationError, JSON errors, OSError
        raise ValueError(f"{path.name}: invalid ResearchResult JSON: {exc}") from exc
    return _map_research_result(result, source_file=path)


def load_research_evidence_directory(
    directory: Path,
    selected_program_ids: Optional[Sequence[Any]] = None,
) -> Dict[int, Dict[str, Any]]:
    """Load and map every `*.json` ResearchResult in `directory` into annotation context.

    Returns `{program_id_int: context_dict}` where each context matches the shape the old
    `load_manual_literature_directory` produced (with renamed evidence fields). If
    `selected_program_ids` is given, every requested program must be present or this
    raises (missing research evidence fails early); ids may be ints, '10', or 'P10'.
    """
    source_dir = Path(directory)
    if not source_dir.exists():
        raise ValueError(f"Research evidence directory not found: {source_dir}")
    if not source_dir.is_dir():
        raise ValueError(f"Research evidence path is not a directory: {source_dir}")

    selected: Optional[Set[int]] = (
        {_coerce_program_id(pid) for pid in selected_program_ids}
        if selected_program_ids is not None
        else None
    )

    loaded: Dict[int, Dict[str, Any]] = {}
    for path in sorted(source_dir.glob("*.json")):
        program_id, context = normalize_research_result_file(path)
        if selected is not None and program_id not in selected:
            continue
        if program_id in loaded:
            if selected is not None:
                continue
            raise ValueError(
                f"Duplicate research evidence for program {program_id} "
                f"({loaded[program_id]['source_summary']['source_files']} vs {path.name})"
            )
        loaded[program_id] = context

    if not loaded and selected is None:
        raise ValueError(f"No research evidence JSON files found in: {source_dir}")

    if selected is not None:
        missing = sorted(selected - set(loaded))
        if missing:
            missing_text = ", ".join(str(pid) for pid in missing)
            raise ValueError(
                f"Missing research evidence for programs: {missing_text}"
            )
        return {pid: loaded[pid] for pid in sorted(selected)}

    return {pid: loaded[pid] for pid in sorted(loaded)}


def summarize_research_evidence_context(
    context_by_program: Dict[int, Dict[str, Any]]
) -> Dict[str, Any]:
    """Summarize normalized research evidence for audit output (counts only)."""
    unique_evidence_ids: Set[str] = set()
    source_files: Set[str] = set()
    module_count = 0
    limited_gene_count = 0
    status_counts: Dict[str, int] = {"supported": 0, "partial": 0, "unsupported": 0}

    for context in context_by_program.values():
        modules = context.get("modules", [])
        module_count += len(modules)
        limited_gene_count += len(context.get("genes_with_limited_literature", []))
        for module in modules:
            unique_evidence_ids.update(module.get("evidence_ids", []))
            source_file = module.get("source_file")
            if source_file:
                source_files.add(str(source_file))
            status = module.get("status")
            if status in status_counts:
                status_counts[status] += 1

    return {
        "query_mode": "research_evidence",
        "programs": len(context_by_program),
        "modules": module_count,
        "unique_evidence_ids": len(unique_evidence_ids),
        "limited_genes": limited_gene_count,
        "status_counts": status_counts,
        "source_files": sorted(source_files),
    }


def iter_research_evidence_module_rows(
    context_by_program: Dict[int, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Flatten normalized research modules into rows for CSV export."""
    rows: List[Dict[str, Any]] = []
    for program_id, context in sorted(context_by_program.items()):
        for module in context.get("modules", []):
            supporting_genes = module.get("supporting_genes", [])
            evidence_ids = module.get("evidence_ids", [])
            rows.append(
                {
                    "program_id": program_id,
                    "module_rank": module.get("module_rank"),
                    "module_name": module.get("module_name", ""),
                    "status": module.get("status", ""),
                    "supporting_genes": "|".join(supporting_genes),
                    "evidence_ids": "|".join(evidence_ids),
                    "n_supporting_genes": len(supporting_genes),
                    "n_evidence_ids": len(evidence_ids),
                    "literature_summary": module.get("literature_summary", ""),
                    "source_file": module.get("source_file", ""),
                }
            )
    return rows


__all__ = [
    "VALID_EVIDENCE_CONTEXT_TYPES",
    "extract_program_id_from_filename",
    "normalize_research_result_file",
    "load_research_evidence_directory",
    "summarize_research_evidence_context",
    "iter_research_evidence_module_rows",
]
