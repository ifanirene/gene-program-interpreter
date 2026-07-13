"""
research.bundle — program-bundle assembler (executor 4, spec 3).

Emits one immutable, read-only ``program_bundles/{program_id}.json`` per gene program.
Each bundle is the *complete input* a single literature research subagent consumes
(``research/research_parallel.py``). This module is fully deterministic and OFFLINE:
it never calls STRING / NCBI / any network service. It only reshapes artifacts that
upstream deterministic steps already produced:

  * gene-loading CSV     -> ``top_weighted_genes`` (via ``gpi.enrichment``)
  * STRING-enrichment CSV -> ``enrichment`` (grouped by category; optional)
  * ``ncbi_context.json`` -> ``regulators`` + ``effect_direction`` (optional)
  * ``ContextProfile``    -> ``context`` + a tissue-agnostic ``research_brief``

The brief is generalized through ``ContextProfile`` — there is no hard-coded
liver/hepatocyte text; a liver profile reproduces liver framing and any other
tissue/cell-type works with no code change.

Bundle shape (spec 3)::

    {
      "program_id": "P10",
      "context": { ...ContextProfile.resolved().to_dict()... },
      "top_weighted_genes": [ {"gene", "score", "uniqueness"} ],   # ordered, top-N
      "enrichment": { "KEGG": [ {"term","description","fdr","genes"} ], "Process": [...], ... },
      "regulators": { "activators": [ {"regulator","log2fc","targets":[{"target","score"}]} ],
                      "repressors": [...] },
      "effect_direction": { ...per-condition / overall top activators & repressors... },
      "qc": { "n_genes", "n_regulators", "has_enrichment", "has_regulators", ... },
      "research_brief": "<generated text the agent reads>"
    }
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd

from gpi.context_profile import ContextProfile
from gpi.enrichment import (
    build_uniqueness_table,
    extract_program_id,
    extract_top_genes_by_program,
    resolve_program_id_column,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# Stable output shape for the enrichment block: these keys always exist so downstream
# consumers can rely on the schema even when a category (or the whole CSV) is absent.
CANONICAL_ENRICHMENT_CATEGORIES: List[str] = ["KEGG", "Process", "Function", "Component"]

# How many regulators to surface in the compact `effect_direction` summary per side.
TOP_EFFECT_REGULATORS = 5


# --------------------------------------------------------------------------- helpers

def _program_key(program_id: Union[int, str]) -> Tuple[int, str]:
    """Normalize a program identifier to ``(int_key, "P{int}")``.

    Accepts 10, "10", "P10", "Program_10", "topic_10", etc. Raises on anything
    that does not parse to an integer — we fail loudly rather than mislabel a bundle.
    """
    n = extract_program_id(program_id)
    if n is None:
        raise ValueError(f"Could not parse a numeric program id from {program_id!r}")
    return int(n), f"P{int(n)}"


def _resolve_program_context(
    ncbi_context: Optional[Dict[str, Any]], int_key: int
) -> Optional[Dict[str, Any]]:
    """Return the per-program ncbi_context entry, accepting either the full keyed
    dict (``{"10": {...}}``) or an already-unwrapped per-program dict."""
    if not ncbi_context:
        return None
    if not isinstance(ncbi_context, dict):
        raise TypeError(f"ncbi_context must be a dict, got {type(ncbi_context)}")
    # Already a per-program entry?
    if "regulator_validation" in ncbi_context or "regulator_validation_by_condition" in ncbi_context:
        return ncbi_context
    # Full dict keyed by program id (str or int).
    for candidate in (str(int_key), int_key):
        if candidate in ncbi_context:
            return ncbi_context[candidate]
    return None


def _validate_gene_df(gene_df: pd.DataFrame) -> None:
    if not isinstance(gene_df, pd.DataFrame):
        raise TypeError(f"gene_df must be a pandas DataFrame, got {type(gene_df)}")
    if gene_df.empty:
        raise ValueError("gene_df is empty; nothing to build.")


# ------------------------------------------------------------------ top_weighted_genes

def _build_top_weighted_genes(
    gene_df: pd.DataFrame, int_key: int, top_loading: int
) -> List[Dict[str, Any]]:
    """Top-N genes for one program by Score, each carrying its global UniquenessScore.

    Uses ``extract_top_genes_by_program`` for the ordered top-N gene names and
    ``build_uniqueness_table`` (global TF-IDF-weighted Score) for the uniqueness value.
    """
    id_col = resolve_program_id_column(gene_df)

    # Ordered top-N gene names for this program (already deduped, Score-descending).
    top_map = extract_top_genes_by_program(gene_df, n_top=top_loading, id_col=id_col)
    ordered_genes = top_map.get(str(int_key), [])

    # Global uniqueness table -> per-(program, gene) Score + UniquenessScore lookup.
    uniq_df = build_uniqueness_table(gene_df, id_col=id_col)
    prog_uniq = uniq_df[uniq_df["program_id"].astype(int) == int_key]
    score_by_gene = dict(zip(prog_uniq["Name"].astype(str), prog_uniq["Score"]))
    uniq_by_gene = dict(zip(prog_uniq["Name"].astype(str), prog_uniq["UniquenessScore"]))

    out: List[Dict[str, Any]] = []
    for gene in ordered_genes:
        out.append(
            {
                "gene": gene,
                "score": float(score_by_gene[gene]) if gene in score_by_gene else None,
                "uniqueness": float(uniq_by_gene[gene]) if gene in uniq_by_gene else None,
            }
        )
    return out


# ------------------------------------------------------------------------- enrichment

def _build_enrichment(
    enrichment_df: Optional[pd.DataFrame], int_key: int, top_enrichment: int
) -> Tuple[Dict[str, List[Dict[str, Any]]], bool]:
    """Group this program's STRING-enrichment terms by category, keeping the
    lowest-FDR ``top_enrichment`` per category. Returns (block, has_enrichment)."""
    block: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in CANONICAL_ENRICHMENT_CATEGORIES}
    if enrichment_df is None or enrichment_df.empty:
        return block, False

    required = {"program_id", "category", "term", "description", "fdr"}
    missing = required - set(enrichment_df.columns)
    if missing:
        raise ValueError(f"enrichment CSV missing required columns: {sorted(missing)}")

    df = enrichment_df.copy()
    df["program_id"] = pd.to_numeric(df["program_id"], errors="coerce")
    df = df[df["program_id"] == int_key]
    if df.empty:
        return block, False

    df["fdr"] = pd.to_numeric(df["fdr"], errors="coerce")
    has_any = False
    for category, sub in df.groupby("category", sort=True):
        sub_sorted = sub.sort_values("fdr", ascending=True, na_position="last").head(top_enrichment)
        terms: List[Dict[str, Any]] = []
        for _, row in sub_sorted.iterrows():
            input_genes = row.get("inputGenes", "")
            if isinstance(input_genes, str) and input_genes.strip():
                genes = [g for g in input_genes.split("|") if g.strip()]
            else:
                genes = []
            terms.append(
                {
                    "term": str(row.get("term", "")),
                    "term_id": str(row.get("term_id", "")) if "term_id" in row else "",
                    "description": str(row.get("description", "")),
                    "fdr": float(row["fdr"]) if pd.notna(row["fdr"]) else None,
                    "genes": genes,
                }
            )
        if terms:
            has_any = True
        block[str(category)] = terms  # preserves canonical keys; adds any extra categories

    return block, has_any


# ------------------------------------------------------------ regulators + effect_direction

def _regulator_entry(reg: Dict[str, Any]) -> Dict[str, Any]:
    """Reshape one upstream regulator record into the bundle's flat shape."""
    targets = [
        {"target": str(t.get("target", "")), "score": t.get("score")}
        for t in (reg.get("string_interactions") or [])
    ]
    return {
        "regulator": str(reg.get("regulator", "")),
        "log2fc": reg.get("log2fc"),
        "targets": targets,
    }


def _dedupe_by_effect(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dedupe regulators by name (a regulator may recur across conditions), keeping the
    entry with the largest |log2fc|; sort deterministically by |log2fc| descending."""
    best: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        name = e["regulator"]
        prev = best.get(name)
        cur_mag = abs(e["log2fc"]) if e.get("log2fc") is not None else -1.0
        prev_mag = abs(prev["log2fc"]) if prev and prev.get("log2fc") is not None else -2.0
        if prev is None or cur_mag > prev_mag:
            best[name] = e
    return sorted(
        best.values(),
        key=lambda e: (-(abs(e["log2fc"]) if e.get("log2fc") is not None else -1.0), e["regulator"]),
    )


def _summarize_side(regs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compact top-K summary (name + log2fc only), preserving the upstream order,
    which is significance-ranked (most significant first)."""
    return [
        {"regulator": str(r.get("regulator", "")), "log2fc": r.get("log2fc")}
        for r in regs[:TOP_EFFECT_REGULATORS]
    ]


def _build_regulators_and_effect(
    ctx: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any], bool]:
    """Build (regulators, effect_direction, has_regulators) from a per-program
    ncbi_context entry that carries ``regulator_validation`` or
    ``regulator_validation_by_condition``. Maps positive->activators, negative->repressors."""
    empty_regs: Dict[str, List[Dict[str, Any]]] = {"activators": [], "repressors": []}
    if not ctx:
        return empty_regs, {}, False

    by_cond = ctx.get("regulator_validation_by_condition")
    flat = ctx.get("regulator_validation")

    if by_cond:
        conditions = sorted(by_cond.keys())
        all_activators: List[Dict[str, Any]] = []
        all_repressors: List[Dict[str, Any]] = []
        per_condition: Dict[str, Any] = {}
        for cond in conditions:
            block = by_cond[cond] or {}
            pos = [_regulator_entry(r) for r in (block.get("positive_regulators") or [])]
            neg = [_regulator_entry(r) for r in (block.get("negative_regulators") or [])]
            all_activators.extend(pos)
            all_repressors.extend(neg)
            per_condition[cond] = {
                "top_activators": _summarize_side(pos),
                "top_repressors": _summarize_side(neg),
            }
        activators = _dedupe_by_effect(all_activators)
        repressors = _dedupe_by_effect(all_repressors)
        regulators = {"activators": activators, "repressors": repressors}
        effect_direction = {
            "mode": "by_condition",
            "conditions": conditions,
            "by_condition": per_condition,
            "overall": {
                "top_activators": _summarize_side(activators),
                "top_repressors": _summarize_side(repressors),
            },
        }
        has = bool(activators or repressors)
        return regulators, effect_direction, has

    if flat:
        pos = [_regulator_entry(r) for r in (flat.get("positive_regulators") or [])]
        neg = [_regulator_entry(r) for r in (flat.get("negative_regulators") or [])]
        regulators = {"activators": pos, "repressors": neg}
        effect_direction = {
            "mode": "overall",
            "top_activators": _summarize_side(pos),
            "top_repressors": _summarize_side(neg),
        }
        return regulators, effect_direction, bool(pos or neg)

    return empty_regs, {}, False


# ---------------------------------------------------------------------- research_brief

def _regulator_names(regs: List[Dict[str, Any]], limit: int = 4) -> List[str]:
    """A few regulator names only (no log2fc, no direction) for light background."""
    names: List[str] = []
    for r in regs:
        name = str(r.get("regulator", "")).strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _background_regulator_names(
    effect_direction: Dict[str, Any], limit: int = 4
) -> List[str]:
    """Collect a few regulator names across sides/conditions for one background line."""
    pooled: List[Dict[str, Any]] = []
    if effect_direction.get("mode") == "by_condition":
        overall = effect_direction.get("overall", {})
        pooled += overall.get("top_activators", []) or []
        pooled += overall.get("top_repressors", []) or []
    else:
        pooled += effect_direction.get("top_activators", []) or []
        pooled += effect_direction.get("top_repressors", []) or []
    return _regulator_names(pooled, limit=limit)


def _unique_gene_names(top_genes: List[Dict[str, Any]], limit: int = 10) -> List[str]:
    """The program's most distinctive genes: top-loading genes re-ranked by uniqueness.

    Falls back to loading order when no uniqueness score is present."""
    scored = [g for g in top_genes if g.get("uniqueness") is not None]
    if scored:
        ranked = sorted(scored, key=lambda g: g["uniqueness"], reverse=True)
        return [g["gene"] for g in ranked[:limit]]
    return [g["gene"] for g in top_genes[:limit]]


def _build_research_brief(
    label: str,
    profile: ContextProfile,
    top_genes: List[Dict[str, Any]],
    effect_direction: Dict[str, Any],
    has_regulators: bool,
) -> str:
    """Generate the lean, tissue-agnostic brief the agent reads.

    Frames exactly ONE question — the shared biological function of the program's
    genes — with the top-loading and distinctive (unique) genes front and center.
    Framing comes entirely from ``profile.resolved_*`` accessors (which omit the
    assay), so no hard-coded tissue/cell-type and no assay/Perturb-seq leakage.
    Regulators are light background only: a single line naming a few, with an
    explicit instruction NOT to investigate their mechanism or direction."""
    rp = profile.resolved()
    role = rp.resolved_annotation_role()
    context_sentence = rp.resolved_annotation_context()
    condition_ctx = rp.resolved_condition_context()

    gene_names = [g["gene"] for g in top_genes]
    top_for_brief = gene_names[: min(15, len(gene_names))]
    unique_for_brief = _unique_gene_names(top_genes, limit=10)

    lines: List[str] = []
    lines.append(f"# Research brief — program {label}")
    lines.append("")
    lines.append(f"You are assisting a {role}.")
    lines.append(f"This program is {context_sentence}.")
    if condition_ctx:
        lines.append(condition_ctx)
    lines.append("")

    lines.append("## Your one question")
    lines.append(
        "**What is the shared biological function of this program's genes?** "
        "Work from the genes below and land on 1-3 coherent functional themes, each "
        "supported by several genes and specific retrieved papers."
    )
    lines.append("")

    lines.append("## The program's genes (your evidence)")
    lines.append(f"Top-loading genes (by loading score): {', '.join(top_for_brief)}.")
    if unique_for_brief:
        lines.append(
            f"Most distinctive (unique) genes for this program: {', '.join(unique_for_brief)}."
        )
    if rp.context_terms:
        lines.append(f"Candidate functional themes to consider: {', '.join(rp.context_terms)}.")
    lines.append("")

    # Regulators are light background ONLY — one line at most, names only.
    if has_regulators:
        bg_names = _background_regulator_names(effect_direction, limit=4)
        if bg_names:
            lines.append(
                f"Background (not your focus): upstream regulators such as "
                f"{', '.join(bg_names)} are associated with this program. Do NOT "
                "investigate regulator mechanism or perturbation direction — spend "
                "your effort on the genes' shared function."
            )
            lines.append("")

    lines.append("## Rules")
    lines.append(
        "- Reference ONLY PMIDs/DOIs your tools return; never fabricate an identifier, "
        "title, or quotation. A deterministic verifier resolves every one afterward."
    )
    lines.append("- Do NOT assign the final program label; downstream synthesis does that.")
    return "\n".join(lines)


# ------------------------------------------------------------------------- public API

def build_bundle(
    program_id: Union[int, str],
    gene_df: pd.DataFrame,
    profile: ContextProfile,
    *,
    enrichment_df: Optional[pd.DataFrame] = None,
    ncbi_context: Optional[Dict[str, Any]] = None,
    top_loading: int = 20,
    top_enrichment: int = 7,
) -> Dict[str, Any]:
    """Assemble the immutable program-bundle dict for one program. Offline; no network."""
    _validate_gene_df(gene_df)
    int_key, label = _program_key(program_id)

    top_weighted_genes = _build_top_weighted_genes(gene_df, int_key, top_loading)
    if not top_weighted_genes:
        raise ValueError(
            f"No genes found for program {label} (int key {int_key}) in gene_df — "
            "check the program id and the loading CSV."
        )

    enrichment, has_enrichment = _build_enrichment(enrichment_df, int_key, top_enrichment)

    ctx = _resolve_program_context(ncbi_context, int_key)
    regulators, effect_direction, has_regulators = _build_regulators_and_effect(ctx)

    research_brief = _build_research_brief(
        label, profile, top_weighted_genes, effect_direction, has_regulators
    )

    n_activators = len(regulators["activators"])
    n_repressors = len(regulators["repressors"])
    n_enrichment_terms = sum(len(v) for v in enrichment.values())

    qc = {
        "n_genes": len(top_weighted_genes),
        "n_regulators": n_activators + n_repressors,
        "n_activators": n_activators,
        "n_repressors": n_repressors,
        "n_enrichment_terms": n_enrichment_terms,
        "has_enrichment": has_enrichment,
        "has_regulators": has_regulators,
        "has_uniqueness": any(g["uniqueness"] is not None for g in top_weighted_genes),
        "effect_direction_mode": effect_direction.get("mode", "none"),
    }

    return {
        "program_id": label,
        "context": profile.resolved().to_dict(),
        "top_weighted_genes": top_weighted_genes,
        "enrichment": enrichment,
        "regulators": regulators,
        "effect_direction": effect_direction,
        "qc": qc,
        "research_brief": research_brief,
    }


def build_all_bundles(
    gene_loading_csv: Union[str, Path],
    profile: ContextProfile,
    *,
    enrichment_csv: Optional[Union[str, Path]] = None,
    ncbi_context_json: Optional[Union[str, Path]] = None,
    out_dir: Union[str, Path] = "program_bundles",
    program_ids: Optional[List[Union[int, str]]] = None,
    top_loading: int = 20,
    top_enrichment: int = 7,
) -> List[Path]:
    """Build and write one ``{out_dir}/{program_id}.json`` per requested program.

    Returns the list of written paths. Deterministic and offline.
    """
    gene_path = Path(gene_loading_csv)
    if not gene_path.exists():
        raise FileNotFoundError(f"gene-loading CSV not found: {gene_path}")
    gene_df = pd.read_csv(gene_path)
    _validate_gene_df(gene_df)

    enrichment_df: Optional[pd.DataFrame] = None
    if enrichment_csv:
        enr_path = Path(enrichment_csv)
        if not enr_path.exists():
            raise FileNotFoundError(f"enrichment CSV not found: {enr_path}")
        enrichment_df = pd.read_csv(enr_path)

    ncbi_context: Optional[Dict[str, Any]] = None
    if ncbi_context_json:
        ctx_path = Path(ncbi_context_json)
        if not ctx_path.exists():
            raise FileNotFoundError(f"ncbi_context JSON not found: {ctx_path}")
        ncbi_context = json.loads(ctx_path.read_text(encoding="utf-8"))

    # Which programs? Default = every program present in the loading CSV, sorted.
    if program_ids is None:
        id_col = resolve_program_id_column(gene_df)
        parsed = sorted({extract_program_id(v) for v in gene_df[id_col].tolist()} - {None})
        program_ids = list(parsed)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []
    for pid in program_ids:
        bundle = build_bundle(
            pid,
            gene_df,
            profile,
            enrichment_df=enrichment_df,
            ncbi_context=ncbi_context,
            top_loading=top_loading,
            top_enrichment=top_enrichment,
        )
        dest = out_path / f"{bundle['program_id']}.json"
        dest.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        logger.info(
            "Wrote %s (genes=%d, regulators=%d, enrichment=%s)",
            dest,
            bundle["qc"]["n_genes"],
            bundle["qc"]["n_regulators"],
            bundle["qc"]["has_enrichment"],
        )
        written.append(dest)

    return written


# -------------------------------------------------------------------------------- CLI

def _parse_programs(value: Optional[str]) -> Optional[List[Union[int, str]]]:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assemble immutable program_bundles/{program_id}.json for literature agents.",
    )
    parser.add_argument("--gene-loading", required=True, help="Gene-loading CSV (Name,Score,program_id,...).")
    parser.add_argument("--enrichment", help="Optional STRING-enrichment CSV.")
    parser.add_argument("--ncbi-context", help="Optional ncbi_context.json (regulator validation).")
    parser.add_argument("--profile", help="Optional ContextProfile YAML (default: liver_demo()).")
    parser.add_argument("--programs", help="Comma-separated program ids (e.g. '2,10,18'). Default: all.")
    parser.add_argument("--out-dir", default="program_bundles", help="Output directory for bundle JSONs.")
    parser.add_argument("--top-loading", type=int, default=20, help="Top-N weighted genes per program.")
    parser.add_argument("--top-enrichment", type=int, default=7, help="Top-N enrichment terms per category.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.profile:
        profile = ContextProfile.from_yaml(Path(args.profile))
    else:
        profile = ContextProfile.liver_demo()

    written = build_all_bundles(
        args.gene_loading,
        profile,
        enrichment_csv=args.enrichment,
        ncbi_context_json=args.ncbi_context,
        out_dir=args.out_dir,
        program_ids=_parse_programs(args.programs),
        top_loading=args.top_loading,
        top_enrichment=args.top_enrichment,
    )
    logger.info("Wrote %d bundle(s) to %s", len(written), args.out_dir)


if __name__ == "__main__":
    main()
