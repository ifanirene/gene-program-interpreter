#!/usr/bin/env python3
"""
@description
This component builds a dataset-derived generic theme dictionary before batch
annotation. It is responsible for constructing a compact evidence pack and
asking an LLM to return only theme terms that are already generic across the
dataset so downstream annotation can down-weight them.

Key features:
- Preserves blank assigned-family values as unassigned programs.
- Does not seed the extraction prompt with disease/function context terms.
- Requires strict aliases for the same concept only.
- Keeps representation scoring disabled; no family or program specificity
  ratings are computed.

Vendored from ProgExplorer `pipeline/compute_theme_representation.py` for the
Gene Program Interpreter. Anthropic-only: the Vertex and AI-gateway backends
have been dropped (see docs/ARCHITECTURE.md DROP list). The manual-literature
directory dependency has been removed; literature module names are now supplied
by the research subsystem via the optional
`literature_module_names_by_program` argument to `build_evidence_pack`.

@dependencies
- pandas: Reads pipeline CSV inputs.
- numpy: Computes empirical quantiles and ranks.
- anthropic: Optional live LLM extraction backend (Anthropic API only).

@examples
- python -m gpi.theme_representation \\
    --gene-file results/output/gene_loading_with_uniqueness.csv \\
    --evidence-source-dir results/output/evidence_source \\
    --fetch-missing-gene-descriptions \\
    --evidence-pack-output results/output/theme_contrast/evidence_pack.json \\
    --prompt-output results/output/theme_contrast/theme_prompt.md \\
    --extraction-response-output results/output/theme_contrast/theme_response.txt \\
    --output-json results/output/theme_contrast/theme_dictionary.json \\
    --output-csv results/output/theme_contrast/theme_dictionary.csv
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from .column_mapper import extract_program_id, standardize_regulator_results


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

FAMILY_COLUMNS = ("assigned_family_id", "recommended_family_id", "family_id")
MIN_GENERIC_PROGRAM_COUNT = 4
ENRICHED_FUNCTION_CATEGORIES = {"process", "kegg", "function", "component", "go", "bp"}
ENRICHED_FUNCTION_CATEGORY_ORDER = {
    "process": 0,
    "kegg": 1,
    "function": 2,
    "component": 3,
    "go": 4,
    "bp": 5,
}


def parse_topics_value(value: Optional[str]) -> Optional[List[int]]:
    if not value:
        return None
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def clean_text(value: Any, max_chars: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def default_context_guidance() -> Dict[str, str]:
    """Return no seeded disease/function terms for generic dictionary extraction."""
    return {}


def normalize_family_value(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"na", "nan", "none", "null", "unassigned"}:
        return None
    return text


def load_gene_table(path: Path, topics: Optional[Sequence[int]] = None) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "program_id" not in df.columns:
        if "RowID" not in df.columns:
            raise ValueError("Gene file must have program_id or RowID")
        df = df.copy()
        df["program_id"] = df["RowID"]
    df["program_id"] = df["program_id"].apply(extract_program_id)
    df = df.dropna(subset=["program_id", "Name"]).copy()
    df["program_id"] = df["program_id"].astype(int)
    if topics is not None:
        df = df[df["program_id"].isin({int(topic) for topic in topics})].copy()
    if "Score" in df.columns:
        df["Score"] = pd.to_numeric(df["Score"], errors="coerce")
    if "UniquenessScore" in df.columns:
        df["UniquenessScore"] = pd.to_numeric(df["UniquenessScore"], errors="coerce")
    elif "Score" in df.columns:
        df = add_global_uniqueness_scores(df)
    if "rank" in df.columns:
        df["rank"] = pd.to_numeric(df["rank"], errors="coerce")
    return df


def add_global_uniqueness_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Compute TF-IDF-style uniqueness scores when step 1 output is absent."""
    required = {"Name", "Score", "program_id"}
    missing = required - set(df.columns)
    if missing:
        logger.warning("Cannot compute uniqueness scores; missing columns: %s", sorted(missing))
        return df
    updated = df.copy()
    updated["Score"] = pd.to_numeric(updated["Score"], errors="coerce")
    valid = updated.dropna(subset=["Name", "Score", "program_id"]).copy()
    if valid.empty:
        return updated
    total_programs = valid["program_id"].nunique()
    gene_counts = valid.groupby("Name")["program_id"].nunique().astype(float)
    idf = np.log((total_programs + 1.0) / (gene_counts + 1.0))
    updated["UniquenessScore"] = np.nan
    updated.loc[valid.index, "UniquenessScore"] = (
        valid["Score"] * valid["Name"].map(idf)
    )
    return updated


def program_ids_from_gene_table(gene_df: pd.DataFrame) -> List[int]:
    return sorted(gene_df["program_id"].dropna().astype(int).unique().tolist())


def sorted_program_genes(group: pd.DataFrame) -> pd.DataFrame:
    if "rank" in group.columns and group["rank"].notna().any():
        return group.sort_values(["rank", "Name"], ascending=[True, True])
    if "Score" in group.columns and group["Score"].notna().any():
        return group.sort_values(["Score", "Name"], ascending=[False, True])
    return group.sort_values("Name")


def select_program_gene_lists(
    gene_df: pd.DataFrame,
    top_loading: int,
    top_unique: int,
) -> Dict[int, Dict[str, List[str]]]:
    gene_lists: Dict[int, Dict[str, List[str]]] = {}
    for program_id, group in gene_df.groupby("program_id", sort=True):
        ordered = sorted_program_genes(group).drop_duplicates("Name", keep="first")
        top_loading_genes = ordered["Name"].astype(str).head(top_loading).tolist()
        top_loading_set = set(top_loading_genes)
        unique_genes: List[str] = []
        if "UniquenessScore" in ordered.columns and ordered["UniquenessScore"].notna().any():
            unique_ordered = ordered.sort_values(
                ["UniquenessScore", "Name"], ascending=[False, True]
            )
            for gene in unique_ordered["Name"].astype(str).tolist():
                if gene not in top_loading_set:
                    unique_genes.append(gene)
                if len(unique_genes) >= top_unique:
                    break
        gene_lists[int(program_id)] = {
            "top_loading_genes": top_loading_genes,
            "top_unique_genes": unique_genes,
        }
    return gene_lists


def select_enrichment_gene_lists(
    gene_df: pd.DataFrame,
    top_n: int = 300,
) -> Dict[int, List[str]]:
    genes_by_program: Dict[int, List[str]] = {}
    for program_id, group in gene_df.groupby("program_id", sort=True):
        ordered = sorted_program_genes(group).drop_duplicates("Name", keep="first")
        genes_by_program[int(program_id)] = ordered["Name"].astype(str).head(top_n).tolist()
    return genes_by_program


def load_assigned_families(
    path: Optional[Path],
    program_ids: Sequence[int],
    family_column: Optional[str] = None,
) -> Dict[int, Optional[str]]:
    family_by_program: Dict[int, Optional[str]] = {int(pid): None for pid in program_ids}
    if not path:
        return family_by_program
    if not path.exists():
        logger.warning("Assigned-family file not found: %s", path)
        return family_by_program

    df = pd.read_csv(path)
    if "program_id" not in df.columns:
        raise ValueError("Assigned-family file must contain a program_id column")
    if family_column:
        if family_column not in df.columns:
            raise ValueError(
                f"Assigned-family column '{family_column}' not found in {path}"
            )
        selected_family_column = family_column
    else:
        selected_family_column = next(
            (column for column in FAMILY_COLUMNS if column in df.columns),
            None,
        )
        if selected_family_column is None:
            raise ValueError(
                "Assigned-family file must contain one of: "
                + ", ".join(FAMILY_COLUMNS)
            )

    for row in df.itertuples(index=False):
        program_id = extract_program_id(getattr(row, "program_id"))
        if program_id is None:
            continue
        program_id = int(program_id)
        if program_id not in family_by_program:
            continue
        family_by_program[program_id] = normalize_family_value(
            getattr(row, selected_family_column)
        )
    return family_by_program


def load_related_programs_from_scores(
    scores_file: Optional[Path],
    program_ids: Sequence[int],
    threshold: float = 0.2,
    max_related: int = 3,
) -> Dict[int, List[int]]:
    related_by_program: Dict[int, List[int]] = {int(pid): [] for pid in program_ids}
    if not scores_file or not scores_file.exists():
        return related_by_program
    df = pd.read_csv(scores_file)
    required = {"program_id", "related_program_id", "similarity_score", "method"}
    if required - set(df.columns):
        logger.warning("Related-program score file lacks required columns: %s", scores_file)
        return related_by_program
    selected = {int(pid) for pid in program_ids}
    overlap = df[df["method"].astype(str).str.endswith("_overlap")].copy()
    overlap["program_id"] = pd.to_numeric(overlap["program_id"], errors="coerce")
    overlap["related_program_id"] = pd.to_numeric(overlap["related_program_id"], errors="coerce")
    overlap["similarity_score"] = pd.to_numeric(overlap["similarity_score"], errors="coerce")
    overlap = overlap[
        overlap["program_id"].isin(selected)
        & overlap["related_program_id"].isin(selected)
        & (overlap["similarity_score"] >= threshold)
    ].copy()
    for program_id, group in overlap.groupby("program_id", sort=True):
        ranked = group.sort_values(
            ["similarity_score", "related_program_id"],
            ascending=[False, True],
        )
        related_by_program[int(program_id)] = (
            ranked["related_program_id"].dropna().astype(int).head(max_related).tolist()
        )
    return related_by_program


def add_snippet(
    snippets: List[Dict[str, Any]],
    program_id: int,
    source_type: str,
    label: str,
    text: str,
    genes: Optional[Iterable[str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    if not clean_text(text):
        return
    snippet_id = f"P{program_id}:{source_type}:{len(snippets) + 1}"
    payload: Dict[str, Any] = {
        "snippet_id": snippet_id,
        "source_type": source_type,
        "label": clean_text(label, 160),
        "text": clean_text(text),
    }
    if genes:
        payload["genes"] = list(dict.fromkeys(str(gene) for gene in genes if gene))
    if extra:
        payload.update(extra)
    snippets.append(payload)


def ncbi_snippets_by_program(
    ncbi_file: Optional[Path],
    program_ids: Sequence[int],
    max_gene_summaries: int = 12,
) -> Dict[int, List[Dict[str, Any]]]:
    if not ncbi_file or not ncbi_file.exists():
        return {}
    data = json.loads(ncbi_file.read_text(encoding="utf-8"))
    selected = {int(pid) for pid in program_ids}
    by_program: Dict[int, List[Dict[str, Any]]] = {}
    for raw_pid, context in data.items():
        program_id = int(raw_pid)
        if program_id not in selected:
            continue
        snippets: List[Dict[str, Any]] = []
        summaries = context.get("gene_summaries", {})
        for idx, (gene, summary) in enumerate(sorted(summaries.items())):
            if idx >= max_gene_summaries:
                break
            add_snippet(
                snippets,
                program_id,
                "gene_summary",
                str(gene),
                f"{gene}: {summary}",
                genes=[str(gene)],
            )
        evidence_map = context.get("evidence_snippets", {})
        for gene, gene_snippets in sorted(evidence_map.items()):
            for evidence in list(gene_snippets or [])[:2]:
                add_snippet(
                    snippets,
                    program_id,
                    "literature_snippet",
                    str(gene),
                    str(evidence),
                    genes=[str(gene)],
                )
        by_program[program_id] = snippets
    return by_program


def enrichment_snippets_by_program(
    enrichment_file: Optional[Path],
    program_ids: Sequence[int],
    top_enrichment: int,
) -> Dict[int, List[Dict[str, Any]]]:
    if not enrichment_file or not enrichment_file.exists():
        return {}
    df = pd.read_csv(enrichment_file)
    required = {"program_id", "category", "description"}
    if required - set(df.columns):
        logger.warning("Enrichment file lacks required theme-pack columns: %s", enrichment_file)
        return {}
    selected = {int(pid) for pid in program_ids}
    df = df[df["program_id"].astype(int).isin(selected)].copy()
    if "fdr" in df.columns:
        df["fdr"] = pd.to_numeric(df["fdr"], errors="coerce")
        df = df.sort_values(["program_id", "category", "fdr"], ascending=[True, True, True])
    snippets_by_program: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for (program_id, category), group in df.groupby(["program_id", "category"], sort=True):
        for row in group.head(top_enrichment).itertuples(index=False):
            genes = []
            if hasattr(row, "inputGenes"):
                genes = [item for item in str(row.inputGenes).split("|") if item]
            add_snippet(
                snippets_by_program[int(program_id)],
                int(program_id),
                "string_enrichment",
                str(category),
                f"{category}: {getattr(row, 'description')}",
                genes=genes[:10],
            )
    return dict(snippets_by_program)


def regulator_snippets_by_program(
    regulator_file: Optional[Path],
    program_ids: Sequence[int],
    significance_threshold: float,
    max_regulators: int = 6,
) -> Dict[int, List[Dict[str, Any]]]:
    if not regulator_file or not regulator_file.exists():
        return {}
    df = pd.read_csv(regulator_file)
    standardized = standardize_regulator_results(
        df,
        significance_threshold=significance_threshold,
    )
    selected = {int(pid) for pid in program_ids}
    standardized = standardized[
        standardized["program_id"].astype(int).isin(selected)
        & (standardized["significant"] == True)
    ].copy()
    snippets_by_program: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for program_id, group in standardized.groupby("program_id", sort=True):
        ranked = group.assign(
            abs_log2fc=group["log_2_fold_change"].abs()
        ).sort_values("abs_log2fc", ascending=False)
        parts = []
        genes = []
        for row in ranked.head(max_regulators).itertuples(index=False):
            target = str(row.grna_target)
            genes.append(target)
            role = "activator" if float(row.log_2_fold_change) < 0 else "repressor"
            parts.append(f"{target} ({role}, knockdown log2FC={row.log_2_fold_change:.3f})")
        if parts:
            add_snippet(
                snippets_by_program[int(program_id)],
                int(program_id),
                "regulator_perturbation",
                "top regulators",
                "; ".join(parts),
                genes=genes,
            )
    return dict(snippets_by_program)


def merge_snippets(*sources: Dict[int, List[Dict[str, Any]]]) -> Dict[int, List[Dict[str, Any]]]:
    merged: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for source in sources:
        for program_id, snippets in source.items():
            merged[int(program_id)].extend(snippets)
    return dict(merged)


def build_evidence_pack(
    gene_df: pd.DataFrame,
    family_by_program: Dict[int, Optional[str]],
    literature_module_names_by_program: dict[int, list[str]] | None = None,
    evidence_source_dir: Optional[Path] = None,
    ncbi_file: Optional[Path] = None,
    enrichment_file: Optional[Path] = None,
    regulator_file: Optional[Path] = None,
    related_programs_by_program: Optional[Dict[int, List[int]]] = None,
    species: int = 9606,
    fetch_missing_go_terms: bool = False,
    string_enrichment_cache_dir: Optional[Path] = None,
    top_loading: int = 20,
    top_unique: int = 10,
    enrichment_gene_count: int = 300,
    top_enrichment: int = 3,
    regulator_significance_threshold: float = 0.05,
    fetch_missing_gene_descriptions: bool = False,
    gene_description_cache: Optional[Path] = None,
) -> Dict[str, Any]:
    program_ids = program_ids_from_gene_table(gene_df)
    gene_lists = select_program_gene_lists(gene_df, top_loading, top_unique)
    evidence_source_dir = Path(evidence_source_dir) if evidence_source_dir else None
    if evidence_source_dir:
        ncbi_file = evidence_source_dir / "literature_context.json"
        enrichment_file = evidence_source_dir / "string_enrichment" / "enrichment_filtered.csv"

    literature_context = load_literature_context(ncbi_file)
    module_names_by_program = top_literature_module_names(literature_context, program_ids)
    # Optional literature-module-name overlay supplied by the research subsystem
    # (formerly the manual-literature directory). None-safe: skip when not provided.
    if literature_module_names_by_program:
        for program_id, names in literature_module_names_by_program.items():
            if names and not module_names_by_program.get(int(program_id)):
                module_names_by_program[int(program_id)] = list(names)
    gene_descriptions_by_program = gene_descriptions_for_programs(
        literature_context,
        program_ids,
    )
    if fetch_missing_gene_descriptions:
        gene_descriptions_by_program = fill_missing_gene_descriptions(
            gene_descriptions_by_program,
            gene_lists,
            program_ids,
            top_loading=5,
            top_unique=5,
            cache_path=gene_description_cache,
        )
    enriched_terms_by_program = top_enriched_functions(
        enrichment_file,
        program_ids,
        top_n=top_enrichment,
    )
    if evidence_source_dir:
        full_enrichment_file = evidence_source_dir / "string_enrichment" / "enrichment_full.csv"
        enriched_terms_by_program = fill_enriched_functions_from_full(
            enriched_terms_by_program,
            full_enrichment_file,
            program_ids,
            top_n=top_enrichment,
        )
    if fetch_missing_go_terms:
        enriched_terms_by_program = fill_missing_go_terms(
            enriched_terms_by_program,
            select_enrichment_gene_lists(gene_df, top_n=enrichment_gene_count),
            program_ids,
            species=species,
            top_n=top_enrichment,
            cache_dir=string_enrichment_cache_dir,
        )
    related_programs_by_program = related_programs_by_program or {
        int(pid): [] for pid in program_ids
    }

    programs = []
    for program_id in program_ids:
        top_loading_genes = gene_lists.get(program_id, {}).get("top_loading_genes", [])
        top_unique_genes = gene_lists.get(program_id, {}).get("top_unique_genes", [])
        snippets = compact_program_snippets(
            program_id=program_id,
            module_names=module_names_by_program.get(program_id, []),
            top_loading_genes=top_loading_genes[:5],
            top_unique_genes=top_unique_genes[:5],
            gene_descriptions=gene_descriptions_by_program.get(program_id, {}),
            enriched_terms=enriched_terms_by_program.get(program_id, []),
        )
        programs.append(
            {
                "program_id": program_id,
                "family_id": family_by_program.get(program_id),
                "related_programs": related_programs_by_program.get(program_id, []),
                "top_literature_module_names": module_names_by_program.get(program_id, [])[:3],
                "top_loading_genes": top_loading_genes,
                "top_unique_genes": top_unique_genes,
                "top_loading_gene_descriptions": [
                    {
                        "gene": gene,
                        "description": gene_descriptions_by_program.get(program_id, {}).get(gene, ""),
                    }
                    for gene in top_loading_genes[:5]
                ],
                "top_unique_gene_descriptions": [
                    {
                        "gene": gene,
                        "description": gene_descriptions_by_program.get(program_id, {}).get(gene, ""),
                    }
                    for gene in top_unique_genes[:5]
                ],
                "top_enriched_go_terms": enriched_terms_by_program.get(program_id, [])[:3],
                "top_enriched_functions": enriched_terms_by_program.get(program_id, [])[
                    :top_enrichment
                ],
                "snippets": snippets,
            }
        )

    assigned_families = sorted(
        {family for family in family_by_program.values() if family is not None}
    )
    return {
        "metadata": {
            "program_count": len(program_ids),
            "assigned_family_count": len(assigned_families),
            "assigned_families": assigned_families,
            "unassigned_program_count": sum(
                1 for pid in program_ids if family_by_program.get(pid) is None
            ),
            "context_guidance": default_context_guidance(),
            "instructions": (
                "LLM extraction must use only these snippets to propose normalized "
                "generic theme terms already supported in 4 or more programs."
            ),
        },
        "programs": programs,
    }


def load_literature_context(path: Optional[Path]) -> Dict[int, Dict[str, Any]]:
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(program_id): context for program_id, context in data.items()}


def top_literature_module_names(
    literature_context: Dict[int, Dict[str, Any]],
    program_ids: Sequence[int],
) -> Dict[int, List[str]]:
    names_by_program: Dict[int, List[str]] = {int(pid): [] for pid in program_ids}
    for program_id in program_ids:
        context = literature_context.get(int(program_id), {})
        manual = context.get("manual_literature_modules", {})
        modules = manual.get("modules", []) if isinstance(manual, dict) else []
        names = [
            clean_text(module.get("module_name"), 180)
            for module in modules
            if isinstance(module, dict) and clean_text(module.get("module_name"), 180)
        ]
        names_by_program[int(program_id)] = names[:3]
    return names_by_program


def gene_descriptions_for_programs(
    literature_context: Dict[int, Dict[str, Any]],
    program_ids: Sequence[int],
) -> Dict[int, Dict[str, str]]:
    descriptions: Dict[int, Dict[str, str]] = {int(pid): {} for pid in program_ids}
    for program_id in program_ids:
        context = literature_context.get(int(program_id), {})
        summaries = context.get("gene_summaries", {})
        if not isinstance(summaries, dict):
            continue
        descriptions[int(program_id)] = {
            str(gene): clean_text(description, 5000)
            for gene, description in summaries.items()
        }
    return descriptions


def description_genes_needed(
    gene_lists: Dict[int, Dict[str, List[str]]],
    program_ids: Sequence[int],
    top_loading: int = 5,
    top_unique: int = 5,
) -> Dict[int, List[str]]:
    needed: Dict[int, List[str]] = {}
    for program_id in program_ids:
        program_gene_lists = gene_lists.get(int(program_id), {})
        genes = (
            program_gene_lists.get("top_loading_genes", [])[:top_loading]
            + program_gene_lists.get("top_unique_genes", [])[:top_unique]
        )
        needed[int(program_id)] = list(dict.fromkeys(genes))
    return needed


def fill_missing_gene_descriptions(
    descriptions_by_program: Dict[int, Dict[str, str]],
    gene_lists: Dict[int, Dict[str, List[str]]],
    program_ids: Sequence[int],
    top_loading: int = 5,
    top_unique: int = 5,
    cache_path: Optional[Path] = None,
) -> Dict[int, Dict[str, str]]:
    """Fetch missing prompt gene descriptions from Harmonizome and cache them."""
    updated = {
        int(program_id): dict(descriptions_by_program.get(int(program_id), {}))
        for program_id in program_ids
    }
    needed_by_program = description_genes_needed(
        gene_lists,
        program_ids,
        top_loading=top_loading,
        top_unique=top_unique,
    )
    missing_genes = sorted(
        {
            gene
            for program_id, genes in needed_by_program.items()
            for gene in genes
            if not updated.get(program_id, {}).get(gene)
        }
    )
    if not missing_genes:
        return updated

    cache: Dict[str, str] = {}
    if cache_path and cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cache = {str(gene): str(desc) for gene, desc in loaded.items() if desc}
        except Exception as exc:
            logger.warning("Could not read gene-description cache %s: %s", cache_path, exc)

    still_missing = [gene for gene in missing_genes if gene not in cache]
    if still_missing:
        from .harmonizome_api import HarmonizomeClient

        logger.info(
            "Fetching %d missing Harmonizome gene descriptions for theme prompt.",
            len(still_missing),
        )
        client = HarmonizomeClient(sleep_seconds=0.05)
        fetched = client.get_gene_summaries(still_missing)
        cache.update(fetched)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    for program_id, genes in needed_by_program.items():
        program_descriptions = updated.setdefault(int(program_id), {})
        for gene in genes:
            if not program_descriptions.get(gene) and cache.get(gene):
                program_descriptions[gene] = cache[gene]
    return updated


def _sort_enriched_function_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda item: (
            float("inf") if item.get("fdr") is None else float(item["fdr"]),
            ENRICHED_FUNCTION_CATEGORY_ORDER.get(
                str(item.get("category", "")).lower(),
                99,
            ),
            item.get("term", ""),
        ),
    )


def _enriched_function_rows_from_df(
    df: pd.DataFrame,
    program_ids: Sequence[int],
    top_n: int,
) -> Dict[int, List[Dict[str, Any]]]:
    terms_by_program: Dict[int, List[Dict[str, Any]]] = {int(pid): [] for pid in program_ids}
    required = {"program_id", "category", "description", "fdr"}
    if required - set(df.columns):
        return terms_by_program
    selected = {int(pid) for pid in program_ids}
    df = df[df["program_id"].astype(int).isin(selected)].copy()
    df = df[
        df["category"].astype(str).str.lower().isin(ENRICHED_FUNCTION_CATEGORIES)
    ].copy()
    if df.empty:
        return terms_by_program
    df["fdr"] = pd.to_numeric(df["fdr"], errors="coerce")
    df["_category_order"] = (
        df["category"]
        .astype(str)
        .str.lower()
        .map(ENRICHED_FUNCTION_CATEGORY_ORDER)
        .fillna(99)
    )
    df = df.sort_values(
        ["program_id", "fdr", "_category_order", "description"],
        ascending=[True, True, True, True],
    )
    for program_id, group in df.groupby("program_id", sort=True):
        rows = []
        seen = set()
        for row in group.itertuples(index=False):
            term = clean_text(getattr(row, "description", ""), 180)
            category = clean_text(getattr(row, "category", ""), 40)
            key = (category.lower(), term.lower())
            if not term or key in seen:
                continue
            rows.append(
                {
                    "term": term,
                    "category": category,
                    "fdr": float(row.fdr) if pd.notna(row.fdr) else None,
                }
            )
            seen.add(key)
            if len(rows) >= top_n:
                break
        terms_by_program[int(program_id)] = rows
    return terms_by_program


def top_enriched_functions(
    enrichment_file: Optional[Path],
    program_ids: Sequence[int],
    top_n: int = 5,
) -> Dict[int, List[Dict[str, Any]]]:
    terms_by_program: Dict[int, List[Dict[str, Any]]] = {int(pid): [] for pid in program_ids}
    if not enrichment_file or not enrichment_file.exists():
        return terms_by_program
    df = pd.read_csv(enrichment_file)
    required = {"program_id", "category", "description", "fdr"}
    if required - set(df.columns):
        logger.warning("Enrichment file lacks required columns: %s", enrichment_file)
        return terms_by_program
    return _enriched_function_rows_from_df(df, program_ids, top_n=top_n)


def top_enriched_go_terms(
    enrichment_file: Optional[Path],
    program_ids: Sequence[int],
    top_n: int = 3,
) -> Dict[int, List[Dict[str, Any]]]:
    """Backward-compatible wrapper for callers expecting GO enrichment rows."""
    return top_enriched_functions(enrichment_file, program_ids, top_n=top_n)


def fill_enriched_functions_from_full(
    terms_by_program: Dict[int, List[Dict[str, Any]]],
    full_enrichment_file: Optional[Path],
    program_ids: Sequence[int],
    top_n: int = 5,
    min_terms_before_replacing: int = 3,
) -> Dict[int, List[Dict[str, Any]]]:
    """Use full STRING GO/KEGG results when filtered functional evidence is sparse."""
    updated = {
        int(program_id): list(terms_by_program.get(int(program_id), []))
        for program_id in program_ids
    }
    sparse = [
        int(program_id)
        for program_id in program_ids
        if len(updated[int(program_id)]) < min_terms_before_replacing
        or len(updated[int(program_id)]) < top_n
    ]
    if not sparse or not full_enrichment_file or not full_enrichment_file.exists():
        return updated
    df = pd.read_csv(full_enrichment_file)
    required = {"program_id", "category", "description", "fdr"}
    if required - set(df.columns):
        return updated
    full_rows = _enriched_function_rows_from_df(df, sparse, top_n=top_n)
    for program_id in sparse:
        existing = updated[int(program_id)]
        if len(existing) < min_terms_before_replacing:
            updated[int(program_id)] = full_rows.get(int(program_id), [])[:top_n]
            continue
        merged = list(existing)
        seen = {
            (
                str(row.get("category", "")).lower(),
                str(row.get("term", "")).lower(),
            )
            for row in merged
        }
        for row in full_rows.get(int(program_id), []):
            key = (
                str(row.get("category", "")).lower(),
                str(row.get("term", "")).lower(),
            )
            if key in seen:
                continue
            merged.append(row)
            seen.add(key)
            if len(merged) >= top_n:
                break
        updated[int(program_id)] = _sort_enriched_function_rows(merged)[:top_n]
    return updated


def fill_missing_go_terms_from_full(
    terms_by_program: Dict[int, List[Dict[str, Any]]],
    full_enrichment_file: Optional[Path],
    program_ids: Sequence[int],
    top_n: int = 3,
) -> Dict[int, List[Dict[str, Any]]]:
    """Backward-compatible wrapper for full enrichment fallback."""
    return fill_enriched_functions_from_full(
        terms_by_program,
        full_enrichment_file,
        program_ids,
        top_n=top_n,
    )


def string_cache_path(cache_dir: Path, species: int, program_id: int) -> Path:
    return cache_dir / f"species_{species}_program_{program_id}_enrichment.json"


def load_string_enrichment_helpers() -> Any:
    """Return the gpi STRING enrichment module (`call_string_enrichment` lives here).

    Repointed from ProgExplorer's file-load of `01_genes_to_string_enrichment.py`
    to the vendored `gpi.enrichment` sibling per the package import convention.
    """
    from . import enrichment as string_enrichment_module

    return string_enrichment_module


def process_string_results_to_go_terms(
    program_id: int,
    raw_results: Sequence[Dict[str, Any]],
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    rows = []
    for result in raw_results:
        category = str(result.get("category", ""))
        if category.lower() not in ENRICHED_FUNCTION_CATEGORIES:
            continue
        background = result.get("number_of_genes_in_background")
        try:
            if background is not None and int(background) >= 500:
                continue
        except (TypeError, ValueError):
            pass
        fdr = result.get("fdr", np.nan)
        try:
            fdr_float = float(fdr)
        except (TypeError, ValueError):
            fdr_float = float("nan")
        rows.append(
            {
                "program_id": int(program_id),
                "term": clean_text(result.get("description") or result.get("term"), 180),
                "category": clean_text(category, 40),
                "fdr": fdr_float if not pd.isna(fdr_float) else None,
            }
        )
    rows = _sort_enriched_function_rows(rows)
    return rows[:top_n]


def fill_missing_go_terms(
    terms_by_program: Dict[int, List[Dict[str, Any]]],
    enrichment_genes_by_program: Dict[int, List[str]],
    program_ids: Sequence[int],
    species: int = 9606,
    top_n: int = 3,
    cache_dir: Optional[Path] = None,
) -> Dict[int, List[Dict[str, Any]]]:
    """Fetch missing STRING GO Process enrichment for programs without terms."""
    updated = {
        int(program_id): list(terms_by_program.get(int(program_id), []))
        for program_id in program_ids
    }
    missing_programs = [
        int(program_id)
        for program_id in program_ids
        if not updated.get(int(program_id))
    ]
    if not missing_programs:
        return updated

    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    helper = load_string_enrichment_helpers()
    for idx, program_id in enumerate(missing_programs, start=1):
        genes = enrichment_genes_by_program.get(program_id, [])
        if not genes:
            continue
        raw_results = None
        if cache_dir:
            cache_file = string_cache_path(cache_dir, species, program_id)
            if cache_file.exists():
                raw_results = json.loads(cache_file.read_text(encoding="utf-8"))
        if raw_results is None:
            logger.info(
                "[%d/%d] Fetching STRING GO enrichment for program %s with %d genes.",
                idx,
                len(missing_programs),
                program_id,
                len(genes),
            )
            raw_results = helper.call_string_enrichment(
                genes=genes,
                species=species,
                retries=3,
                sleep_between=0.6,
            )
            if cache_dir:
                cache_file = string_cache_path(cache_dir, species, program_id)
                cache_file.write_text(json.dumps(raw_results, indent=2), encoding="utf-8")
            time.sleep(0.6)
        updated[program_id] = process_string_results_to_go_terms(
            program_id,
            raw_results,
            top_n=top_n,
        )
    return updated


def compact_program_snippets(
    program_id: int,
    module_names: Sequence[str],
    top_loading_genes: Sequence[str],
    top_unique_genes: Sequence[str],
    gene_descriptions: Dict[str, str],
    enriched_terms: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    snippets: List[Dict[str, Any]] = []
    if module_names:
        add_snippet(
            snippets,
            program_id,
            "literature_module_names",
            "top literature modules",
            "; ".join(module_names[:3]),
        )
    loading_parts = [
        f"{gene}: {gene_descriptions.get(gene, 'No description available')}"
        for gene in top_loading_genes[:5]
    ]
    if loading_parts:
        add_snippet(
            snippets,
            program_id,
            "top_loading_gene_descriptions",
            "top loading gene descriptions",
            "; ".join(loading_parts),
            genes=top_loading_genes[:5],
        )
    unique_parts = [
        f"{gene}: {gene_descriptions.get(gene, 'No description available')}"
        for gene in top_unique_genes[:5]
    ]
    if unique_parts:
        add_snippet(
            snippets,
            program_id,
            "top_unique_gene_descriptions",
            "top unique gene descriptions",
            "; ".join(unique_parts),
            genes=top_unique_genes[:5],
        )
    term_parts = [
        f"{term.get('term', '')} (FDR={term.get('fdr'):.2e})"
        if isinstance(term.get("fdr"), (float, int))
        else str(term.get("term", ""))
        for term in enriched_terms[:3]
    ]
    if term_parts:
        add_snippet(
            snippets,
            program_id,
            "go_enrichment",
            "top enriched GO terms",
            "; ".join(term_parts),
        )
    return snippets


def format_markdown_list(items: Sequence[str]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)


def format_gene_descriptions(items: Sequence[Dict[str, str]]) -> str:
    if not items:
        return "- None"
    lines = []
    for item in items:
        gene = item.get("gene", "")
        description = item.get("description", "") or "No description available"
        lines.append(f"- **{gene}:** {description}")
    return "\n".join(lines)


def format_go_terms(items: Sequence[Dict[str, Any]]) -> str:
    if not items:
        return "- None"
    lines = []
    for item in items:
        term = item.get("term", "")
        category = item.get("category", "")
        fdr = item.get("fdr")
        fdr_text = f"{float(fdr):.2e}" if isinstance(fdr, (float, int)) else "NA"
        category_text = f"{category}; " if category else ""
        lines.append(f"- {term} ({category_text}FDR={fdr_text})")
    return "\n".join(lines)


def format_context_guidance(context: Dict[str, Any]) -> str:
    if not context:
        return ""
    title = clean_text(context.get("title"), 160)
    disease_context = clean_text(context.get("disease_context"), 1200)
    functional_context = clean_text(context.get("functional_context"), 1600)
    if not title and not disease_context and not functional_context:
        return ""
    lines = []
    if title:
        lines.extend([f"## {title}", ""])
    if disease_context:
        lines.extend([f"Disease context: {disease_context}", ""])
    if functional_context:
        lines.extend(
            [
                "Functional context to consider during literature interpretation: "
                f"{functional_context}",
                "",
            ]
        )
    return "\n".join(lines).strip()


def build_evidence_pack_markdown(evidence_pack: Dict[str, Any]) -> str:
    context_markdown = format_context_guidance(
        evidence_pack.get("metadata", {}).get("context_guidance", {})
    )
    lines = [
        "# Compact All-Program Evidence Pack",
        "",
        "For each program, this pack includes only the fields requested for theme extraction.",
        "",
        f"- Program count: {evidence_pack.get('metadata', {}).get('program_count', 0)}",
        "",
    ]
    if context_markdown:
        lines.extend([context_markdown, ""])
    for program in evidence_pack.get("programs", []):
        program_id = program.get("program_id")
        lines.extend(
            [
                f"## Program {program_id}",
                "",
                f"- Program ID: {program_id}",
                "",
                "### Top 3 Literature Module Names",
                format_markdown_list(program.get("top_literature_module_names", [])[:3]),
                "",
                "### Gene Descriptions For Top 5 Loading Genes",
                format_gene_descriptions(program.get("top_loading_gene_descriptions", [])[:5]),
                "",
                "### Gene Descriptions For Top 5 Unique Genes",
                format_gene_descriptions(program.get("top_unique_gene_descriptions", [])[:5]),
                "",
                "### Top 5 Enriched Functions (GO/KEGG by FDR)",
                format_go_terms(program.get("top_enriched_functions", [])[:5]),
                "",
            ]
        )
        lines.append("")
    return "\n".join(lines).strip()


def build_theme_extraction_prompt(
    evidence_pack: Dict[str, Any],
    min_program_count: int = MIN_GENERIC_PROGRAM_COUNT,
) -> str:
    evidence_markdown = build_evidence_pack_markdown(evidence_pack)
    return f"""# Theme Term Extraction Prompt

## Background
This is a generic-theme dictionary pass before gene-program annotation. The
evidence below summarizes each program with compact module, gene, and enrichment
cues. The only goal is to identify themes that are already generic across this
dataset, so those broad labels can be down-weighted during downstream annotation.

## Goal
Return only normalized theme terms that are already generic: the same concept
must be directly supported in {min_program_count} or more programs from the supplied evidence.
Do not identify rare, family-enriched, single-program, disease-specific, or
mechanistically distinctive functional terms in this pass. Do not infer extra
themes from outside biological knowledge.

strict aliases rules:
- Aliases must be narrow synonyms, abbreviations, or exact near-equivalent
  phrasings for the same biological concept as `theme_term`.
- Do not use upstream regulators, downstream outcomes, broader pathways, cell
  states, or disease processes as aliases for one another.
- If no narrow synonym, abbreviation, or near-equivalent phrase exists, return
  an empty alias list for that theme.
- Do not include a gene symbol, regulator, cell state, disease outcome, or
  pathway member as an alias unless it is genuinely an alternate name for the
  exact same term.
- Include `evidence_program_ids` listing the programs where the same generic
  concept is directly supported. Exclude any theme with fewer than
  {min_program_count} evidence programs.

## Output Schema

```json
{{"themes":[{{"theme_term":"...","aliases":["..."],"evidence_program_ids":[1,2,3,4]}}]}}
```

## Evidence Pack

{evidence_markdown}
"""


def extract_json_payload(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def call_anthropic(prompt: str, model: str, max_tokens: int) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic package is required for --llm-backend anthropic") from exc
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for theme extraction")
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def load_or_run_theme_extraction(
    prompt: str,
    response_output: Path,
    response_file: Optional[Path],
    llm_backend: str,
    llm_model: str,
    llm_max_tokens: int,
) -> Dict[str, Any]:
    source = response_file if response_file and response_file.exists() else response_output
    if source.exists():
        logger.info("Using cached theme extraction response: %s", source)
        raw_text = source.read_text(encoding="utf-8")
        if source != response_output:
            response_output.parent.mkdir(parents=True, exist_ok=True)
            response_output.write_text(raw_text, encoding="utf-8")
        return extract_json_payload(raw_text)

    if llm_backend == "none":
        raise RuntimeError(
            "No theme extraction response exists. Provide --extraction-response-file "
            "or set --llm-backend to anthropic."
        )
    if llm_backend == "anthropic":
        raw_text = call_anthropic(prompt, llm_model, llm_max_tokens)
    else:
        raise ValueError(f"Unsupported LLM backend for theme extraction: {llm_backend}")

    response_output.parent.mkdir(parents=True, exist_ok=True)
    response_output.write_text(raw_text, encoding="utf-8")
    return extract_json_payload(raw_text)


def generic_theme_program_ids(raw: Dict[str, Any]) -> List[int]:
    """Return LLM-declared evidence programs for a generic theme."""
    values = (
        raw.get("evidence_program_ids")
        or raw.get("evidence_programs")
        or raw.get("program_ids")
        or []
    )
    ids = []
    for value in values:
        program_id = extract_program_id(value)
        if program_id is not None:
            ids.append(int(program_id))
    return sorted(set(ids))


def normalize_generic_theme_records(
    extraction: Dict[str, Any],
    min_program_count: int = MIN_GENERIC_PROGRAM_COUNT,
) -> List[Dict[str, Any]]:
    """Normalize LLM-returned generic themes and drop non-generic terms."""
    raw_themes = extraction.get("themes", [])
    if not isinstance(raw_themes, list):
        raise ValueError("Theme extraction JSON must contain a list field named 'themes'")

    normalized: List[Dict[str, Any]] = []
    seen_terms = set()
    for raw in raw_themes:
        if not isinstance(raw, dict):
            continue
        term = clean_text(raw.get("theme_term"), 120)
        term_key = term.lower()
        if not term or term_key in seen_terms:
            continue
        aliases = [
            clean_text(alias, 120)
            for alias in raw.get("aliases", [])
            if clean_text(alias, 120)
        ]
        evidence_program_ids = generic_theme_program_ids(raw)
        if len(evidence_program_ids) < min_program_count:
            continue
        seen_terms.add(term_key)
        normalized.append(
            {
                "theme_term": term,
                "aliases": list(dict.fromkeys(aliases)),
                "evidence_program_ids": evidence_program_ids,
                "evidence_program_count": len(evidence_program_ids),
                "dictionary_role": "downweight_generic_theme",
            }
        )
    return normalized


def compute_theme_dictionary(
    extraction: Dict[str, Any],
    evidence_pack: Dict[str, Any],
    min_program_count: int = MIN_GENERIC_PROGRAM_COUNT,
) -> Dict[str, Any]:
    rows = normalize_generic_theme_records(
        extraction,
        min_program_count=min_program_count,
    )
    metadata = {
        "dictionary_type": "generic_theme_downweighting",
        "minimum_program_frequency": min_program_count,
        "program_count": evidence_pack.get("metadata", {}).get("program_count", 0),
        "theme_count": len(rows),
        "score_method": (
            "LLM identifies already generic themes with direct evidence in "
            f"{min_program_count} or more programs; deterministic "
            "representation scoring is disabled."
        ),
    }
    return {"metadata": metadata, "themes": rows}


def write_theme_csv(theme_dictionary: Dict[str, Any], output_csv: Path) -> None:
    rows = []
    for theme in theme_dictionary.get("themes", []):
        rows.append(
            {
                "theme_term": theme.get("theme_term", ""),
                "aliases": "|".join(theme.get("aliases", [])),
                "evidence_program_ids": "|".join(
                    str(item) for item in theme.get("evidence_program_ids", [])
                ),
                "evidence_program_count": theme.get("evidence_program_count", 0),
                "dictionary_role": theme.get("dictionary_role", ""),
            }
        )
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output_csv, index=False)


def load_literature_module_names_from_research(
    research_evidence_dir: Optional[Path],
    program_ids: Sequence[int],
) -> Optional[Dict[int, List[str]]]:
    """Build the literature-module-name overlay from verified ResearchResults.

    Reads `research_results/{program_id}.json` via the research-evidence adapter and
    maps each program's candidate-mechanism names to `top_literature_module_names`.
    Returns None when no directory is given (overlay skipped). Best-effort by program:
    programs without research evidence simply get no overlay.
    """
    if not research_evidence_dir:
        return None
    from .research_evidence_adapter import load_research_evidence_directory

    context_by_program = load_research_evidence_directory(research_evidence_dir)
    selected = {int(pid) for pid in program_ids}
    module_names_by_program: Dict[int, List[str]] = {}
    for program_id, context in context_by_program.items():
        if int(program_id) not in selected:
            continue
        module_names_by_program[int(program_id)] = [
            str(module.get("module_name")).strip()
            for module in context.get("modules", [])
            if module.get("module_name")
        ]
    return module_names_by_program


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a dataset-derived generic theme dictionary."
    )
    parser.add_argument("--gene-file", type=Path, required=True)
    parser.add_argument(
        "--evidence-source-dir",
        type=Path,
        help=(
            "Directory containing pre-annotation evidence files such as "
            "literature_context.json and string_enrichment/enrichment_filtered.csv. "
            "Annotation markdown files are never read."
        ),
    )
    parser.add_argument("--ncbi-file", type=Path)
    parser.add_argument("--enrichment-file", type=Path)
    parser.add_argument("--regulator-file", type=Path)
    parser.add_argument(
        "--research-evidence-dir",
        type=Path,
        help=(
            "Directory of verified per-program ResearchResult JSON files "
            "(research_results/{program_id}.json). When provided, each program's "
            "candidate-mechanism names supply the literature-module-name overlay for "
            "the evidence pack. Omit to skip the overlay."
        ),
    )
    parser.add_argument(
        "--assigned-family-file",
        type=Path,
        help="Legacy option accepted for older commands; ignored by generic extraction.",
    )
    parser.add_argument("--family-column")
    parser.add_argument(
        "--program-family-score-file",
        type=Path,
        help="Optional previous-analysis score table used only to list related programs.",
    )
    parser.add_argument("--topics")
    parser.add_argument("--species", type=int, default=9606)
    parser.add_argument("--top-loading", type=int, default=20)
    parser.add_argument("--top-unique", type=int, default=10)
    parser.add_argument("--enrichment-gene-count", type=int, default=300)
    parser.add_argument("--top-enrichment", type=int, default=5)
    parser.add_argument(
        "--min-generic-program-count",
        type=int,
        default=MIN_GENERIC_PROGRAM_COUNT,
        help=(
            "Minimum number of evidence programs required to keep a generic "
            f"theme (default: {MIN_GENERIC_PROGRAM_COUNT})."
        ),
    )
    parser.add_argument("--regulator-significance-threshold", type=float, default=0.05)
    parser.add_argument(
        "--fetch-missing-gene-descriptions",
        action="store_true",
        help="Fetch missing top loading/unique gene descriptions from Harmonizome.",
    )
    parser.add_argument(
        "--gene-description-cache",
        type=Path,
        help="Optional JSON cache for fetched Harmonizome gene descriptions.",
    )
    parser.add_argument(
        "--fetch-missing-go-terms",
        action="store_true",
        help="Fetch missing GO Process enrichment terms from STRING.",
    )
    parser.add_argument(
        "--string-enrichment-cache-dir",
        type=Path,
        help="Optional cache directory for per-program STRING enrichment JSON.",
    )
    parser.add_argument(
        "--llm-backend",
        choices=["anthropic", "none"],
        default="anthropic",
    )
    parser.add_argument("--llm-model", default="claude-sonnet-4-5-20250929")
    parser.add_argument("--llm-max-tokens", type=int, default=8192)
    parser.add_argument("--extraction-response-file", type=Path)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Write the evidence pack and prompt, then exit before LLM extraction.",
    )
    parser.add_argument("--evidence-pack-output", type=Path, required=True)
    parser.add_argument("--prompt-output", type=Path, required=True)
    parser.add_argument("--extraction-response-output", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    return parser


def parse_args() -> argparse.Namespace:
    return build_parser().parse_args()


def main() -> int:
    args = parse_args()
    topics = parse_topics_value(args.topics)
    gene_df = load_gene_table(args.gene_file, topics=topics)
    program_ids = program_ids_from_gene_table(gene_df)
    family_by_program = {int(program_id): None for program_id in program_ids}
    related_programs_by_program = load_related_programs_from_scores(
        args.program_family_score_file,
        program_ids,
    )
    literature_module_names_by_program = load_literature_module_names_from_research(
        args.research_evidence_dir,
        program_ids,
    )
    evidence_pack = build_evidence_pack(
        gene_df=gene_df,
        family_by_program=family_by_program,
        literature_module_names_by_program=literature_module_names_by_program,
        evidence_source_dir=args.evidence_source_dir,
        ncbi_file=args.ncbi_file,
        enrichment_file=args.enrichment_file,
        regulator_file=args.regulator_file,
        related_programs_by_program=related_programs_by_program,
        species=args.species,
        fetch_missing_go_terms=args.fetch_missing_go_terms,
        string_enrichment_cache_dir=args.string_enrichment_cache_dir,
        top_loading=args.top_loading,
        top_unique=args.top_unique,
        enrichment_gene_count=args.enrichment_gene_count,
        top_enrichment=args.top_enrichment,
        regulator_significance_threshold=args.regulator_significance_threshold,
        fetch_missing_gene_descriptions=args.fetch_missing_gene_descriptions,
        gene_description_cache=args.gene_description_cache,
    )
    prompt = build_theme_extraction_prompt(
        evidence_pack,
        min_program_count=args.min_generic_program_count,
    )

    args.evidence_pack_output.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_pack_output.write_text(
        json.dumps(evidence_pack, indent=2),
        encoding="utf-8",
    )
    args.prompt_output.parent.mkdir(parents=True, exist_ok=True)
    args.prompt_output.write_text(prompt, encoding="utf-8")

    if args.prepare_only:
        logger.info("Prepared theme evidence pack and prompt without LLM extraction.")
        return 0

    extraction = load_or_run_theme_extraction(
        prompt=prompt,
        response_output=args.extraction_response_output,
        response_file=args.extraction_response_file,
        llm_backend=args.llm_backend,
        llm_model=args.llm_model,
        llm_max_tokens=args.llm_max_tokens,
    )
    theme_dictionary = compute_theme_dictionary(
        extraction,
        evidence_pack,
        min_program_count=args.min_generic_program_count,
    )

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(theme_dictionary, indent=2),
        encoding="utf-8",
    )
    write_theme_csv(theme_dictionary, args.output_csv)
    logger.info("Wrote theme dictionary JSON: %s", args.output_json)
    logger.info("Wrote theme dictionary CSV: %s", args.output_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
