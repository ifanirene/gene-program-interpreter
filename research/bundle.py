"""
research.bundle — program-bundle assembler (executor 4).

Emits one immutable, read-only ``program_bundles/{program_id}.json`` per gene program.
Each bundle is the *complete prompt* a single literature research subagent reads
(``research/research_parallel.py``). Fully deterministic and OFFLINE.

The bundle is deliberately lean — every piece of information appears exactly ONCE, and
nothing that isn't the agent's job is included:

    {
      "program_id": "P10",
      "organism": "mouse",
      "cell_type": "hepatocyte",
      "conditions": ["aging", "MASLD"],
      "functions_to_consider": [ ...normal cell-type functions... ],   # context, once
      "program_genes": [ ...gene names... ],                           # top-loading, names only
      "distinctive_genes": [ ...gene names... ],                       # program-specific, names only
      "perturbation_regulators": {                                     # top-6 per condition, SEARCH TARGETS
          "young": [ {"gene": "Dgat2", "log2fc": 1.83}, ... ],
          "aged":  [ ... ]
      },
      "research_brief": "<short instruction that references the fields above>"
    }

No assay/Perturb-seq wording, no annotation fields, no enrichment block, no "overall"
regulators, no loading/uniqueness scores, no long decimals. The perturbation regulators
are the genes whose knockout most changes the program — the agent researches them the
same way as the program genes.
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

# How many perturbation regulators to surface per condition (treated as search targets).
TOP_REGULATORS_PER_CONDITION = 6


# --------------------------------------------------------------------------- helpers

def _program_key(program_id: Union[int, str]) -> Tuple[int, str]:
    """Normalize a program identifier to ``(int_key, "P{int}")``."""
    n = extract_program_id(program_id)
    if n is None:
        raise ValueError(f"Could not parse a numeric program id from {program_id!r}")
    return int(n), f"P{int(n)}"


def _validate_gene_df(gene_df: pd.DataFrame) -> None:
    if not isinstance(gene_df, pd.DataFrame):
        raise TypeError(f"gene_df must be a pandas DataFrame, got {type(gene_df)}")
    if gene_df.empty:
        raise ValueError("gene_df is empty; nothing to build.")


def _resolve_program_context(
    ncbi_context: Optional[Dict[str, Any]], int_key: int
) -> Optional[Dict[str, Any]]:
    """Return the per-program ncbi_context entry (accepts full keyed dict or unwrapped)."""
    if not ncbi_context or not isinstance(ncbi_context, dict):
        return None
    if "regulator_validation" in ncbi_context or "regulator_validation_by_condition" in ncbi_context:
        return ncbi_context
    for candidate in (str(int_key), int_key):
        if candidate in ncbi_context:
            return ncbi_context[candidate]
    return None


# -------------------------------------------------------------------------- genes

def _program_and_distinctive_genes(
    gene_df: pd.DataFrame, int_key: int, top_loading: int
) -> Tuple[List[str], List[str]]:
    """Return (program_genes, distinctive_genes) as plain name lists.

    program_genes: top-N by loading Score. distinctive_genes: the same pool re-ranked
    by global uniqueness (TF-IDF-weighted Score). Uniqueness is used internally to rank
    but is NOT surfaced in the bundle — the agent needs gene identities, not scores.
    """
    id_col = resolve_program_id_column(gene_df)
    top_map = extract_top_genes_by_program(gene_df, n_top=top_loading, id_col=id_col)
    program_genes = list(top_map.get(str(int_key), []))

    uniq_df = build_uniqueness_table(gene_df, id_col=id_col)
    prog = uniq_df[uniq_df["program_id"].astype(int) == int_key]
    uniq_by_gene = dict(zip(prog["Name"].astype(str), prog["UniquenessScore"]))
    ranked = sorted(
        (g for g in program_genes if g in uniq_by_gene),
        key=lambda g: uniq_by_gene[g],
        reverse=True,
    )
    distinctive_genes = ranked[:10] if ranked else program_genes[:10]
    return program_genes, distinctive_genes


# --------------------------------------------------------------- perturbation regulators

def _top_condition_regulators(ctx: Optional[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Top-6 regulators per condition (by |log2fc|), as {gene, log2fc(2dp)}.

    Combines activators + repressors per condition and keeps the strongest by absolute
    effect — these are the genes whose perturbation most changes the program, handed to
    the agent as additional search targets. Returns {} when no regulator data is present.
    """

    def _pick(block: Dict[str, Any]) -> List[Dict[str, Any]]:
        pooled: Dict[str, float] = {}
        for side in ("positive_regulators", "negative_regulators"):
            for r in block.get(side) or []:
                name = str(r.get("regulator", "")).strip()
                lfc = r.get("log2fc")
                if not name or lfc is None:
                    continue
                if name not in pooled or abs(lfc) > abs(pooled[name]):
                    pooled[name] = float(lfc)
        ranked = sorted(pooled.items(), key=lambda kv: -abs(kv[1]))[:TOP_REGULATORS_PER_CONDITION]
        return [{"gene": g, "log2fc": round(lfc, 2)} for g, lfc in ranked]

    if not ctx:
        return {}
    by_cond = ctx.get("regulator_validation_by_condition")
    if by_cond:
        out = {cond: _pick(by_cond[cond] or {}) for cond in sorted(by_cond)}
        return {c: v for c, v in out.items() if v}
    flat = ctx.get("regulator_validation")
    if flat:
        picked = _pick(flat)
        return {"all": picked} if picked else {}
    return {}


# ---------------------------------------------------------------------- research_brief

def _build_research_brief(
    label: str,
    profile: ContextProfile,
    has_regulators: bool,
) -> str:
    """Short instruction that REFERENCES the bundle's fields (no re-listing of genes or
    context). One question; regulators are additional search targets, not background."""
    rp = profile.resolved()
    role = rp.resolved_annotation_role()
    subject = rp.cell_type or rp.tissue or rp.organism or "cell"

    reg_clause = (
        " and the genes in `perturbation_regulators` (research these the same way as the "
        "program genes)"
        if has_regulators
        else ""
    )
    lines = [
        f"# Program {label} — {rp.organism} {subject} gene program",
        "",
        f"You are a {role}. Determine the shared biological function of this program's genes.",
        "",
        f"Research the genes in `program_genes` and `distinctive_genes`{reg_clause}, within the "
        "cell-type functions listed in `functions_to_consider`. Land on 1-3 coherent functional "
        "themes, each supported by several genes and specific retrieved papers.",
        "",
        "Cite ONLY PMIDs/DOIs your tools return — never fabricate an identifier, title, or "
        "quotation. Do not assign the final program label.",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------------- public API

def build_bundle(
    program_id: Union[int, str],
    gene_df: pd.DataFrame,
    profile: ContextProfile,
    *,
    ncbi_context: Optional[Dict[str, Any]] = None,
    top_loading: int = 20,
    **_ignored: Any,  # accept legacy kwargs (enrichment_df, top_enrichment) without using them
) -> Dict[str, Any]:
    """Assemble the lean, immutable program-bundle dict for one program. Offline; no network."""
    _validate_gene_df(gene_df)
    int_key, label = _program_key(program_id)

    program_genes, distinctive_genes = _program_and_distinctive_genes(gene_df, int_key, top_loading)
    if not program_genes:
        raise ValueError(f"No genes found for program {label} in gene_df — check id and CSV.")

    regulators = _top_condition_regulators(_resolve_program_context(ncbi_context, int_key))

    rp = profile.resolved()
    bundle: Dict[str, Any] = {
        "program_id": label,
        "organism": rp.organism,
        "cell_type": rp.cell_type,
        "conditions": rp.conditions,
        "functions_to_consider": rp.context_terms,
        "program_genes": program_genes,
        "distinctive_genes": distinctive_genes,
    }
    if regulators:
        bundle["perturbation_regulators"] = regulators
    bundle["research_brief"] = _build_research_brief(label, profile, bool(regulators))
    return bundle


def build_all_bundles(
    gene_loading_csv: Union[str, Path],
    profile: ContextProfile,
    *,
    ncbi_context_json: Optional[Union[str, Path]] = None,
    out_dir: Union[str, Path] = "program_bundles",
    program_ids: Optional[List[Union[int, str]]] = None,
    top_loading: int = 20,
    **_ignored: Any,  # accept legacy kwargs (enrichment_csv, top_enrichment) without using them
) -> List[Path]:
    """Build and write one ``{out_dir}/{program_id}.json`` per requested program."""
    gene_path = Path(gene_loading_csv)
    if not gene_path.exists():
        raise FileNotFoundError(f"gene-loading CSV not found: {gene_path}")
    gene_df = pd.read_csv(gene_path)
    # Common real-world alias: some loadings label the program column "RowID".
    if "program_id" not in gene_df.columns and "RowID" in gene_df.columns:
        gene_df = gene_df.rename(columns={"RowID": "program_id"})
    _validate_gene_df(gene_df)

    ncbi_context: Optional[Dict[str, Any]] = None
    if ncbi_context_json:
        ctx_path = Path(ncbi_context_json)
        if not ctx_path.exists():
            raise FileNotFoundError(f"ncbi_context JSON not found: {ctx_path}")
        ncbi_context = json.loads(ctx_path.read_text(encoding="utf-8"))

    if program_ids is None:
        id_col = resolve_program_id_column(gene_df)
        program_ids = sorted({extract_program_id(v) for v in gene_df[id_col].tolist()} - {None})

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []
    for pid in program_ids:
        bundle = build_bundle(pid, gene_df, profile, ncbi_context=ncbi_context, top_loading=top_loading)
        dest = out_path / f"{bundle['program_id']}.json"
        dest.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        n_reg = sum(len(v) for v in bundle.get("perturbation_regulators", {}).values())
        logger.info("Wrote %s (genes=%d, regulators=%d)", dest, len(bundle["program_genes"]), n_reg)
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
    parser.add_argument("--gene-loading", required=True, help="Gene-loading CSV (Name,Score,program_id|RowID,...).")
    parser.add_argument("--ncbi-context", help="Optional ncbi_context.json (regulator validation by condition).")
    parser.add_argument("--profile", help="Optional ContextProfile YAML (default: liver_demo()).")
    parser.add_argument("--programs", help="Comma-separated program ids (e.g. '2,10,18'). Default: all.")
    parser.add_argument("--out-dir", default="program_bundles", help="Output directory for bundle JSONs.")
    parser.add_argument("--top-loading", type=int, default=20, help="Top-N weighted genes per program.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    profile = ContextProfile.from_yaml(Path(args.profile)) if args.profile else ContextProfile.liver_demo()
    written = build_all_bundles(
        args.gene_loading,
        profile,
        ncbi_context_json=args.ncbi_context,
        out_dir=args.out_dir,
        program_ids=_parse_programs(args.programs),
        top_loading=args.top_loading,
    )
    logger.info("Wrote %d bundle(s) to %s", len(written), args.out_dir)


if __name__ == "__main__":
    main()
