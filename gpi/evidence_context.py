"""
Annotation-prompt / evidence-context assembler for the Gene Program Interpreter.

This is the PROMPT/CONTEXT-ASSEMBLY half of ProgExplorer's
`pipeline/03_submit_and_monitor_batch.py`. It builds, per program, the LLM
annotation prompt from deterministic evidence:

  * top-loading + uniqueness genes,
  * STRING KEGG/GO enrichment,
  * NCBI/Harmonizome gene summaries and evidence snippets,
  * regulator-perturbation evidence (single or condition-specific),
  * research-evidence modules (from the Agent-SDK literature research), and
  * generic-theme down-weighting guidance.

The batch-SUBMIT half lives in `gpi.anthropic_batch`; this module only ASSEMBLES
request dicts (`build_annotation_requests`) ready for `anthropic_batch.submit_batch`.

Generalization: the liver/MASLD framing that ProgExplorer hard-coded (five module
constants + the PROMPT_TEMPLATE opening sentence) is now supplied by a
`gpi.context_profile.ContextProfile`. `generate_prompt` reads the framing strings
from `profile.prompt_fields()`, so a liver profile reproduces the original text and
any other tissue/condition works with no code change.

@examples
  python -m gpi.evidence_context prepare \
    --gene-file tests/fixtures/inputs/gene_loading.csv \
    --ncbi-file results/output/ncbi_context.json \
    --regulator-file tests/fixtures/inputs/regulators.csv \
    --output-file results/output/llm_batches/batch_request.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from .column_mapper import (
    apply_program_id_offset,
    collapse_regulator_guides,
    sort_regulator_rows_by_significance,
    standardize_condition_regulator_results,
    standardize_regulator_results,
)
from .context_profile import ContextProfile
from .enrichment import ensure_global_uniqueness, select_program_gene_sets
from .research_evidence_adapter import load_research_evidence_directory

# Load environment variables from .env file (optional).
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from gpi.log_redaction import install_log_redaction

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
# This module runs as its own subprocess and configures the root logger itself, so it does
# NOT inherit the driver's redaction. httpx logs every request URL at INFO, and the NCBI /
# STRING calls carry api_key and email in the query string — this is where the key actually
# leaked into runs/*.log. Install here, at import, before any record can be emitted.
install_log_redaction()
logger = logging.getLogger(__name__)

# Default Anthropic model stored in prepared batch requests.
MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 8192


# =============================================================================
# Config loading (prepare CLI)
# =============================================================================
def load_config(config_path: Optional[str]) -> Dict[str, Any]:
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.exists():
        raise SystemExit(f"Config file not found: {path}")

    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise SystemExit("PyYAML is required for YAML configs.") from exc
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise SystemExit("Config must be a mapping at the top level.")
    return data


def get_cli_overrides(argv: List[str]) -> Set[str]:
    overrides: Set[str] = set()
    for token in argv:
        if token.startswith("--"):
            name = token[2:]
            if "=" in name:
                name = name.split("=", 1)[0]
            overrides.add(name.replace("-", "_"))
    return overrides


def parse_topics_value(value: Optional[object]) -> Optional[List[int]]:
    if value is None:
        return None
    if isinstance(value, list):
        return [int(v) for v in value]
    if isinstance(value, str):
        return [int(t.strip()) for t in value.split(",") if t.strip()]
    return None


def apply_test_mode(
    args: argparse.Namespace, config: Dict[str, Any], cli_overrides: Set[str]
) -> argparse.Namespace:
    test_cfg = config.get("test", {}) if isinstance(config.get("test", {}), dict) else {}
    enabled = bool(test_cfg.get("enabled") or config.get("test_mode"))
    if not enabled:
        return args

    topics = test_cfg.get("topics") or test_cfg.get("programs") or config.get("test_programs")
    if hasattr(args, "topics") and "topics" not in cli_overrides and not getattr(args, "topics", None):
        if topics is not None:
            args.topics = topics

    num_topics = test_cfg.get("num_topics") or test_cfg.get("num_programs")
    if hasattr(args, "num_topics") and "num_topics" not in cli_overrides and not getattr(args, "num_topics", None):
        if num_topics is not None:
            args.num_topics = num_topics
    return args


def apply_config_overrides(
    args: argparse.Namespace, config: Dict[str, Any], cli_overrides: Set[str]
) -> argparse.Namespace:
    """Apply prepare-only config defaults (Vertex/gateway sections dropped)."""
    if args.command != "prepare":
        return args
    input_cfg = config.get("input", {}) if isinstance(config.get("input", {}), dict) else {}
    top_level_defaults = {
        "gene_file": config.get("gene_file") or input_cfg.get("gene_loading"),
        "regulator_file": config.get("regulator_file") or input_cfg.get("regulator_file"),
        "program_id_offset": config.get("program_id_offset"),
        "model": config.get("llm_model"),
        "max_tokens": config.get("llm_max_tokens"),
        "thinking": config.get("llm_thinking"),
        "effort": config.get("llm_output_effort"),
        "profile": config.get("profile"),
        "research_evidence_dir": config.get("research_evidence_dir"),
        "top_loading": config.get("top_loading"),
        "top_unique": config.get("top_unique"),
        "top_enrichment": config.get("top_enrichment"),
        "genes_per_term": config.get("genes_per_term"),
        "top_positive_regulators": config.get("top_positive_regulators"),
        "top_negative_regulators": config.get("top_negative_regulators"),
        "regulator_significance_threshold": config.get("regulator_significance_threshold"),
    }
    for dest, value in top_level_defaults.items():
        if value is None or dest in cli_overrides:
            continue
        if hasattr(args, dest):
            setattr(args, dest, value)

    steps_cfg = config.get("steps", {}) if isinstance(config.get("steps", {}), dict) else {}
    step_cfg = steps_cfg.get("batch_prepare", {}) if isinstance(steps_cfg, dict) else {}
    if isinstance(step_cfg, dict):
        for key, value in step_cfg.items():
            dest = key.replace("-", "_")
            if dest in cli_overrides:
                continue
            if hasattr(args, dest):
                setattr(args, dest, value)
    return args


# =============================================================================
# Gene table + uniqueness
# =============================================================================
def _split_pipe_list(value: object) -> List[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def _parse_program_id(value: object) -> Optional[int]:
    if isinstance(value, (int, float)) and not pd.isna(value):
        return int(value)
    text = str(value).strip()
    if text.lower().startswith("program_"):
        text = text.split("_", 1)[-1]
    try:
        return int(text)
    except ValueError:
        return None


def ensure_program_id_column(df: pd.DataFrame, program_id_offset: int = 0) -> pd.DataFrame:
    if "program_id" in df.columns:
        return df
    if "RowID" not in df.columns:
        raise ValueError("CSV must have 'program_id' or 'RowID'")
    updated = df.copy()
    updated["program_id"] = updated["RowID"]
    updated = apply_program_id_offset(updated, program_id_offset)
    return updated


# Uniqueness scoring lives in gpi.enrichment (add_global_uniqueness_scores /
# ensure_global_uniqueness) — imported above rather than re-implemented here.


def load_gene_table(gene_file: Path, program_id_offset: int = 0) -> pd.DataFrame:
    if not gene_file.exists():
        raise FileNotFoundError(f"Gene file not found: {gene_file}")
    df = pd.read_csv(gene_file)
    df = ensure_program_id_column(df, program_id_offset=program_id_offset)
    required_cols = {"Name", "Score", "program_id"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Gene file missing required columns: {missing}")
    df = ensure_global_uniqueness(df, logger)
    return df


def select_program_genes(
    gene_df: pd.DataFrame,
    program_id: int,
    top_loading: int,
    top_unique: int,
) -> Tuple[List[str], List[str]]:
    """(top-loading genes, program-unique genes) for one program — see
    ``gpi.enrichment.select_program_gene_sets``, the pipeline-wide selector this defers to so
    the annotation prompt is built from the same genes the report shows and the research agent
    reads."""
    return select_program_gene_sets(
        gene_df, program_id, top_loading=top_loading, top_unique=top_unique
    )


# =============================================================================
# Cell-type context
# =============================================================================
# Candidate filenames searched in --celltype-dir, most informative first. The long-format
# detail table is preferred over the legacy bucketed summaries because it carries signed
# effect sizes; the format is ultimately detected by COLUMNS, not by filename, since either
# CLI flag may point at either format.
CELLTYPE_FILENAMES = (
    "celltype_detail.csv",
    "celltype_summary.csv",
    "program_celltype_annotations_summary.csv",
)

# Minimal column set identifying the long-format table written by `gpi.enrichment`
# (`rank_selected` is optional, so it is not part of the signature).
CELLTYPE_DETAIL_COLUMNS = {"program", "cell_type", "direction", "log2_fc"}

# The parenthetical is an anti-overclaiming guard, not padding: the upstream marker table is
# top-N filtered, so a cell type's ABSENCE means "did not rank into the top markers", never
# "not expressed there". Without this the model reads absence as evidence of absence.
CELLTYPE_DETAIL_PREAMBLE = (
    "Program activity by cell type (log2FC of program score, cells of that type vs. all others;\n"
    "this program ranked in the top 10 markers for each type listed — types not listed were not\n"
    "tested into the top 10, which is not evidence of absence)."
)


def _resolve_celltype_path(
    celltype_dir: Optional[Path], celltype_file: Optional[Path] = None
) -> Optional[Path]:
    """Pick the cell-type table to read: an explicit file wins, else search the directory."""
    if celltype_file and celltype_file.exists():
        return celltype_file
    if not celltype_dir:
        logger.warning("Cell-type table not found (no directory or file supplied).")
        return None
    for filename in CELLTYPE_FILENAMES:
        candidate = celltype_dir / filename
        if candidate.exists():
            return candidate
    logger.warning("Cell-type summary not found in: %s", celltype_dir)
    return None


def _load_celltype_detail(df: pd.DataFrame) -> Dict[int, Dict[str, Any]]:
    """Parse the long-format cell-type table (one row per program x cell type).

    Keeps the SIGNED log2FC so the prompt can show that a program is actively excluded
    from a lineage, which the legacy enriched/depleted buckets threw away.
    """
    detail_map: Dict[int, Dict[str, Any]] = {}
    for _, row in df.iterrows():
        program_id = _parse_program_id(row.get("program"))
        if program_id is None:
            continue
        cell_type = str(row.get("cell_type", "")).strip()
        if not cell_type:
            continue
        direction = str(row.get("direction", "")).strip().lower()
        if direction not in ("enriched", "depleted"):
            logger.warning(
                "Skipping cell-type row with unknown direction %r (program %s, %s).",
                row.get("direction"),
                program_id,
                cell_type,
            )
            continue
        try:
            log2_fc = float(row.get("log2_fc"))
        except (TypeError, ValueError):
            continue
        if not np.isfinite(log2_fc):
            continue
        program_detail = detail_map.setdefault(
            program_id, {"detail": {"enriched": [], "depleted": []}}
        )
        program_detail["detail"][direction].append(
            {"cell_type": cell_type, "log2_fc": log2_fc}
        )
    return detail_map


def _load_celltype_summary(df: pd.DataFrame) -> Dict[int, Dict[str, Any]]:
    """Parse the legacy wide/bucketed cell-type summary (names only, no effect sizes)."""
    required_cols = {
        "program",
        "highly_cell_type_specific",
        "moderately_enriched",
        "weakly_enriched",
    }
    missing = required_cols - set(df.columns)
    if missing:
        logger.warning("Cell-type summary missing columns: %s", missing)
        return {}
    depleted_column = None
    if "depleted" in df.columns:
        depleted_column = "depleted"
    elif "significantly_lower_expression" in df.columns:
        depleted_column = "significantly_lower_expression"
        logger.warning(
            "Cell-type summary uses legacy column 'significantly_lower_expression'; "
            "prefer 'depleted' in regenerated summaries."
        )
    else:
        logger.warning("Cell-type summary missing depleted column ('depleted').")
        return {}

    annotation_map: Dict[int, Dict[str, Any]] = {}
    for _, row in df.iterrows():
        program_id = _parse_program_id(row.get("program"))
        if program_id is None:
            continue
        annotation_map[program_id] = {
            "highly_cell_type_specific": _split_pipe_list(
                row.get("highly_cell_type_specific")
            ),
            "moderately_enriched": _split_pipe_list(row.get("moderately_enriched")),
            "weakly_enriched": _split_pipe_list(row.get("weakly_enriched")),
            "depleted": _split_pipe_list(row.get(depleted_column)),
        }
    return annotation_map


def load_celltype_annotations(
    celltype_dir: Optional[Path], celltype_file: Optional[Path] = None
) -> Dict[int, Dict[str, Any]]:
    """Load per-program cell-type context, preferring the signed long format.

    Returns program_id -> either `{"detail": {"enriched": [...], "depleted": [...]}}` (long
    format; each entry is `{"cell_type": str, "log2_fc": float}` with a SIGNED log2FC) or the
    legacy bucket mapping (`highly_cell_type_specific`/`moderately_enriched`/`weakly_enriched`/
    `depleted` -> list of cell-type names). `format_celltype_context` renders both.
    """
    summary_path = _resolve_celltype_path(celltype_dir, celltype_file)
    if summary_path is None:
        return {}

    df = pd.read_csv(summary_path)
    if CELLTYPE_DETAIL_COLUMNS <= set(df.columns):
        return _load_celltype_detail(df)
    return _load_celltype_summary(df)


def _format_celltype_detail(detail: Dict[str, List[Dict[str, Any]]]) -> str:
    """Render signed per-cell-type log2FCs, strongest |log2FC| first within each direction."""
    lines = ["#### Cell-type enrichment", CELLTYPE_DETAIL_PREAMBLE]
    for direction, label in (("enriched", "Enriched"), ("depleted", "Depleted")):
        entries = detail.get(direction) or []
        if not entries:
            continue
        ranked = sorted(entries, key=lambda entry: abs(entry["log2_fc"]), reverse=True)
        rendered = ", ".join(
            f"{entry['cell_type']} ({entry['log2_fc']:+.2f})" for entry in ranked
        )
        lines.append(f"- {label}: {rendered}")
    if len(lines) == 2:
        return "#### Cell-type enrichment: Not available."
    return "\n".join(lines)


def format_celltype_context(
    annotation_map: Dict[int, Dict[str, Any]], program_id: int
) -> str:
    program_info = annotation_map.get(program_id)
    if not program_info:
        return "#### Cell-type enrichment: Not available."

    detail = program_info.get("detail")
    if isinstance(detail, dict):
        return _format_celltype_detail(detail)

    lines = ["#### Cell-type enrichment"]
    label_map = {
        "highly_cell_type_specific": "Highly specific",
        "moderately_enriched": "Moderately enriched",
        "weakly_enriched": "Weakly enriched",
        "depleted": "Depleted in",
    }
    for key, label in label_map.items():
        values = program_info.get(key, [])
        if values:
            lines.append(f"- {label}: {', '.join(values)}")
    if len(lines) == 1:
        return "#### Cell-type enrichment: Not available."
    return "\n".join(lines)


# =============================================================================
# Enrichment context
# =============================================================================
def prepare_enrichment_mapping(
    enrichment_file: Optional[Path],
) -> Dict[int, Dict[str, List[dict]]]:
    if not enrichment_file:
        return {}
    if not enrichment_file.exists():
        logger.warning("Enrichment file not found: %s", enrichment_file)
        return {}

    df = pd.read_csv(enrichment_file)
    required_cols = {"program_id", "category", "description", "fdr", "inputGenes"}
    missing = required_cols - set(df.columns)
    if missing:
        logger.warning("Enrichment file missing required columns: %s", missing)
        return {}

    df["category"] = df["category"].astype(str).str.strip()
    df_sorted = df.sort_values(["program_id", "category", "fdr"], ascending=[True, True, True])

    enrichment_by_program: Dict[int, Dict[str, List[dict]]] = {}
    for (pid, cat), sub in df_sorted.groupby(["program_id", "category"], sort=False):  # type: ignore
        if cat not in ("KEGG", "Process", "Function", "Component"):
            continue
        program_map = enrichment_by_program.setdefault(int(pid), {})
        program_map[cat] = sub.to_dict(orient="records")
    return enrichment_by_program


def build_enrichment_context(
    enrichment_by_program: Dict[int, Dict[str, List[dict]]],
    program_id: int,
    top_enrichment: int,
    genes_per_term: int,
) -> str:
    program_context = enrichment_by_program.get(program_id, {})
    if not program_context:
        return "#### Top KEGG/GO enrichment: Not available."

    category_labels = {
        "KEGG": "KEGG",
        "Process": "GO Process",
        "Function": "GO Function",
        "Component": "GO Component",
    }
    rows_with_category: List[Tuple[float, str, dict]] = []
    for category, rows in program_context.items():
        if category not in category_labels:
            continue
        for row in rows:
            fdr = row.get("fdr")
            try:
                fdr_value = float(fdr)
            except (TypeError, ValueError):
                fdr_value = float("inf")
            rows_with_category.append((fdr_value, category, row))

    selected_rows = sorted(rows_with_category, key=lambda item: item[0])[:top_enrichment]
    if not selected_rows:
        return "#### Top KEGG/GO enrichment: Not available."

    lines = ["#### Top KEGG/GO enrichment", ""]
    for fdr_value, category, row in selected_rows:
        desc = row.get("description") or row.get("term") or "NA"
        fdr_str = f"{fdr_value:.2e}" if np.isfinite(fdr_value) else str(row.get("fdr"))
        genes = _split_pipe_list(row.get("inputGenes"))
        genes = genes[:genes_per_term]
        genes_str = ", ".join(genes) if genes else "NA"
        lines.append(
            f"- {category_labels[category]}: {desc} (FDR={fdr_str}) "
            f"- member genes: {genes_str}"
        )
    return "\n".join(lines)


# =============================================================================
# NCBI / literature context + research evidence
# =============================================================================
def load_ncbi_context(json_path: Optional[Path]) -> Dict[int, Dict[str, Any]]:
    if not json_path or not json_path.exists():
        if json_path:
            logger.warning("NCBI file not found: %s", json_path)
        return {}

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return {int(k): v for k, v in data.items()}
    except Exception as e:
        logger.error("Error parsing NCBI JSON: %s", e)
        return {}


def load_prompt_literature_context(
    ncbi_file: Optional[Path],
    research_evidence_dir: Optional[Path],
    program_ids: Sequence[int],
) -> Dict[int, Dict[str, Any]]:
    """Load literature context for prompt preparation from existing artifacts.

    Merges NCBI gene/evidence context with per-program research-evidence modules
    (Agent-SDK literature research, via `research_evidence_adapter`). Research
    modules are stored under the per-program key ``research_evidence_modules``.
    """
    context = load_ncbi_context(ncbi_file)
    if not research_evidence_dir:
        return context

    research_context = load_research_evidence_directory(
        research_evidence_dir,
        selected_program_ids=program_ids,
    )
    for program_id, research in research_context.items():
        program_context = context.setdefault(int(program_id), {})
        source_summary = research.get("source_summary", {})
        program_context.setdefault("top_papers", [])
        program_context.setdefault("gene_summaries", {})
        program_context.setdefault("gene_summaries_source", "none")
        program_context.setdefault("evidence_snippets", {})
        program_context["research_evidence_modules"] = research
        program_context.setdefault("meta", {})
        program_context["meta"].update(
            {
                "query_mode": "research_evidence",
                "total_hits": int(source_summary.get("n_modules") or 0),
                "source_files": source_summary.get("source_files", []),
                "literature_audit": {
                    "query_mode": "research_evidence",
                    "research_modules": int(source_summary.get("n_modules") or 0),
                    "research_unique_evidence_ids": int(
                        source_summary.get("n_unique_evidence_ids") or 0
                    ),
                    "research_limited_genes": int(
                        source_summary.get("n_limited_genes") or 0
                    ),
                    "research_source_files": source_summary.get("source_files", []),
                },
            }
        )
    return context


def has_research_evidence_context(ctx: Dict[str, Any]) -> bool:
    research = ctx.get("research_evidence_modules")
    return isinstance(research, dict) and bool(research.get("modules"))


def research_evidence_genes(ctx: Dict[str, Any]) -> Set[str]:
    genes: Set[str] = set()
    research = ctx.get("research_evidence_modules", {})
    if not isinstance(research, dict):
        return genes
    for module in research.get("modules", []):
        if not isinstance(module, dict):
            continue
        genes.update(str(gene) for gene in module.get("supporting_genes", []) if gene)
    limited_genes = research.get("genes_with_limited_literature", [])
    genes.update(str(gene) for gene in limited_genes if gene)
    return genes


def format_research_evidence_context(ctx: Dict[str, Any]) -> str:
    """Render research-evidence modules for the prompt.

    Reads the `modules[]` shape produced by `research_evidence_adapter`:
    `{module_rank, module_name, supporting_genes[], evidence_ids[] (PMID and/or
    DOI), literature_summary, status}`. The per-module `status` is NOT surfaced to
    the LLM (it still drives the report's badges) so it does not bias final-module
    selection; the modules are framed as verified candidates, not fixed boundaries.
    """
    research = ctx.get("research_evidence_modules", {})
    if not isinstance(research, dict):
        return ""
    modules = research.get("modules", [])
    if not isinstance(modules, list) or not modules:
        return ""

    lines = [
        "#### Research-evidence modules",
        "",
        "Use these as high-priority evidence, not fixed final module boundaries. "
        "Each module carries a verification status "
        "(supported/partial/unsupported).",
        "",
    ]
    for idx, module in enumerate(modules, start=1):
        if not isinstance(module, dict):
            continue
        rank = module.get("module_rank") or idx
        module_name = str(module.get("module_name", "")).strip()
        if not module_name:
            continue
        genes = ", ".join(module.get("supporting_genes", [])) or "None listed"
        evidence_ids = ", ".join(module.get("evidence_ids", [])) or "None listed"
        summary = str(module.get("literature_summary", "")).strip()
        lines.extend(
            [
                f"Module {rank}: {module_name}",
                f"- Supporting genes: {genes}",
                f"- Supporting evidence (PMID): {evidence_ids}",
                f"- Literature summary: {summary}",
                "",
            ]
        )

    return "\n".join(lines).strip()


def format_ncbi_context(
    ncbi_data: Dict[int, Dict[str, Any]],
    program_id: int,
    allowed_genes: Optional[Set[str]] = None,
) -> str:
    ctx = ncbi_data.get(program_id)
    if not ctx:
        return "Literature evidence: None available."

    lines = []

    # 1. Official Gene Summaries (filtered by allowed_genes).
    summaries = ctx.get("gene_summaries", {})
    if summaries:
        source = str(ctx.get("gene_summaries_source", "ncbi")).lower()
        source_label = "Harmonizome" if source == "harmonizome" else "Entrez (NCBI)"
        lines.append(f"\n#### Gene Summaries ({source_label}):")
        sorted_genes = sorted(summaries.keys())
        count = 0
        for gene in sorted_genes:
            if allowed_genes and gene not in allowed_genes:
                continue
            desc = summaries[gene]
            if source == "ncbi":
                desc = re.sub(
                    r"\s*\[provided by .*?\]\.?",
                    "",
                    desc,
                    flags=re.IGNORECASE,
                )
            lines.append(f"- {gene}: {desc}")
            count += 1

        if count == 0:
            lines.append("None available for selected genes.")

    research_mode = has_research_evidence_context(ctx)

    # 2. Aggregated Evidence (snippets). Suppressed when research modules are present.
    ev_map = {} if research_mode else ctx.get("evidence_snippets", {})
    if ev_map:
        lines.append("\nAggregated Evidence (Contextual sentences from literature):")
        sorted_ev_genes = sorted(ev_map.keys())
        has_snippets = False
        for sym in sorted_ev_genes:
            if allowed_genes and sym not in allowed_genes:
                continue
            s_list = ev_map[sym]
            if s_list:
                seen_normalized = set()
                seen_pmids = set()
                gene_snippets = []
                for s in s_list:
                    pmid_match = re.search(r"\(PMID:(\d+)\)", s)
                    pmid = pmid_match.group(1) if pmid_match else None
                    if pmid:
                        if pmid in seen_pmids:
                            continue
                        seen_pmids.add(pmid)
                    norm = s.strip(" .")
                    if norm in seen_normalized:
                        continue
                    seen_normalized.add(norm)
                    clean_s = s.strip()
                    while ".." in clean_s:
                        clean_s = clean_s.replace("..", ".")
                    if not clean_s.endswith("."):
                        clean_s += "."
                    gene_snippets.append(f"- {sym}: {clean_s}")
                    if len(gene_snippets) >= 5:
                        break
                if gene_snippets:
                    has_snippets = True
                    lines.extend(gene_snippets)

        if not has_snippets:
            lines.append("None found.")

    return "\n".join(lines)


# =============================================================================
# Regulator context
# =============================================================================
def load_regulator_data(
    csv_path: Optional[Path],
    significance_threshold: float = 0.05,
) -> Dict[int, pd.DataFrame]:
    """Load significant regulators from SCEPTRE results CSV.

    Returns a dict mapping program_id -> DataFrame with regulator columns.
    """
    if not csv_path or not csv_path.exists():
        if csv_path:
            logger.warning("Regulator file not found: %s", csv_path)
        return {}

    try:
        df = pd.read_csv(csv_path)
        df = standardize_regulator_results(
            df, significance_threshold=significance_threshold
        )
        df = df[df["significant"] == True].copy()

        result = {}
        for pid, group in df.groupby("program_id"):
            keep_cols = ["grna_target", "log_2_fold_change", "p_value", "significant"]
            if "adj_p_value" in group.columns:
                keep_cols.append("adj_p_value")
            result[pid] = group[keep_cols].copy()

        logger.info("Loaded regulators for %d programs", len(result))
        return result
    except Exception as e:
        logger.error("Error loading regulator data: %s", e)
        return {}


def parse_condition_path_args(values: Optional[List[str]]) -> Dict[str, Path]:
    """Parse repeated condition=path CLI values into a mapping."""
    parsed: Dict[str, Path] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"Expected condition=path value, got {value!r}.")
        condition, path = value.split("=", 1)
        condition = condition.strip()
        if not condition:
            raise ValueError(f"Condition cannot be empty in {value!r}.")
        parsed[condition] = Path(path)
    return parsed


def coerce_optional_bool(value: object) -> object:
    if pd.isna(value):
        return value
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "t", "1", "yes", "y"}:
        return True
    if text in {"false", "f", "0", "no", "n"}:
        return False
    return bool(value)


def load_condition_regulator_data(
    regulator_files: Dict[str, Path],
    regulator_qc_files: Optional[Dict[str, Path]] = None,
    significance_threshold: float = 0.05,
    program_id_offset: int = 0,
) -> Dict[str, Dict[int, pd.DataFrame]]:
    """Load condition-specific program regulator matrices.

    Returns {condition: {program_id: dataframe}}. Rows include significant and
    non-significant entries so the same loader can support prompt selection and
    volcano plots.
    """
    regulator_qc_files = regulator_qc_files or {}
    by_condition: Dict[str, Dict[int, pd.DataFrame]] = {}
    for condition, path in regulator_files.items():
        if not path.exists():
            logger.warning("Condition regulator file not found: %s", path)
            continue
        try:
            raw = pd.read_csv(path, sep=None, engine="python")
            df = standardize_condition_regulator_results(
                raw,
                condition=condition,
                significance_threshold=significance_threshold,
                program_id_offset=0,
            )
            qc_path = regulator_qc_files.get(condition)
            if qc_path and qc_path.exists():
                qc = pd.read_csv(qc_path)
                if "target_gene" not in qc.columns and "response_id" in qc.columns:
                    qc["target_gene"] = qc["response_id"]
                qc_cols = [
                    col
                    for col in ["target_gene", "pass_qc", "on_target", "pct_KD"]
                    if col in qc.columns
                ]
                if "target_gene" in qc_cols:
                    qc_keep = qc[qc_cols].drop_duplicates("target_gene")
                    df = df.merge(qc_keep, on="target_gene", how="left")
                    for bool_col in ["pass_qc", "on_target"]:
                        if bool_col in df.columns:
                            df[bool_col] = df[bool_col].map(coerce_optional_bool)
            by_program: Dict[int, pd.DataFrame] = {}
            keep_cols = [
                "program_id",
                "condition",
                "grna_target",
                "target_gene",
                "log_2_fold_change",
                "p_value",
                "significant",
            ]
            if "adj_p_value" in df.columns:
                keep_cols.append("adj_p_value")
            for optional in ["pass_qc", "on_target", "pct_KD"]:
                if optional in df.columns:
                    keep_cols.append(optional)
            for pid, group in df.groupby("program_id"):
                by_program[int(pid)] = group[keep_cols].copy()
            by_condition[condition] = by_program
        except Exception as exc:
            logger.error("Error loading condition regulator data %s: %s", path, exc)
    return by_condition


def select_top_condition_regulators(
    regulator_data: Dict[str, Dict[int, pd.DataFrame]],
    program_id: int,
    top_positive_regulators: int = 3,
    top_negative_regulators: int = 3,
    masked_regulators: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Select nonredundant top activators/repressors by adjusted p-value.

    ``masked_regulators`` (case-insensitive gene symbols) are dropped BEFORE the top-N
    cut so promiscuous, non-program-specific regulators do not consume activator/repressor
    slots — the top-N is filled from the remaining program-specific regulators instead.
    """
    mask = {str(m).strip().lower() for m in (masked_regulators or []) if str(m).strip()}
    selected: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for condition, by_program in regulator_data.items():
        reg_df = by_program.get(program_id)
        if reg_df is None or reg_df.empty:
            selected[condition] = {"positive": [], "negative": []}
            continue
        collapsed = collapse_regulator_guides(reg_df, significant_only=True)
        if mask and not collapsed.empty:
            gene_col = "target_gene" if "target_gene" in collapsed.columns else "grna_target"
            if gene_col in collapsed.columns:
                collapsed = collapsed[
                    ~collapsed[gene_col].astype(str).str.strip().str.lower().isin(mask)
                ]
        positive = sort_regulator_rows_by_significance(
            collapsed[collapsed["log_2_fold_change"] < 0]
        ).head(top_positive_regulators)
        negative = sort_regulator_rows_by_significance(
            collapsed[collapsed["log_2_fold_change"] > 0]
        ).head(top_negative_regulators)

        def row_to_dict(row: pd.Series) -> Dict[str, Any]:
            item: Dict[str, Any] = {
                "gene": row.get("target_gene") or row.get("grna_target"),
                "guide": row.get("grna_target"),
                "condition": condition,
                "log2fc": float(row.get("log_2_fold_change")),
                "pvalue": row.get("p_value"),
                "adj_pvalue": row.get("adj_p_value"),
            }
            for optional in ["pass_qc", "on_target", "pct_KD"]:
                if optional in row and pd.notna(row[optional]):
                    value = row[optional]
                    if optional in {"pass_qc", "on_target"}:
                        value = coerce_optional_bool(value)
                    item[optional] = value
            return item

        selected[condition] = {
            "positive": [row_to_dict(row) for _, row in positive.iterrows()],
            "negative": [row_to_dict(row) for _, row in negative.iterrows()],
        }
    return selected


def format_regulator_analysis_context(
    regulator_data: Dict[Any, Any],
    ncbi_data: Dict[int, Dict[str, Any]],
    program_id: int,
    top_positive_regulators: int = 3,
    top_negative_regulators: int = 3,
    min_score: int = 400,
    masked_regulators: Optional[Iterable[str]] = None,
) -> str:
    """Format comprehensive regulator analysis with compact STRING interactions.

    Dispatches to the condition-specific formatter when `regulator_data` is a
    condition-keyed mapping. ``masked_regulators`` (case-insensitive gene symbols) are
    excluded from selection so promiscuous regulators never fill a top-N slot.
    """
    mask = {str(m).strip().lower() for m in (masked_regulators or []) if str(m).strip()}
    if regulator_data and all(
        isinstance(value, dict) for value in regulator_data.values()
    ):
        return format_condition_regulator_analysis_context(
            regulator_data,  # type: ignore[arg-type]
            ncbi_data,
            program_id,
            top_positive_regulators=top_positive_regulators,
            top_negative_regulators=top_negative_regulators,
            min_score=min_score,
            masked_regulators=masked_regulators,
        )

    reg_df = regulator_data.get(program_id)
    ctx = ncbi_data.get(program_id, {})
    validation = ctx.get("regulator_validation")

    if reg_df is None or len(reg_df) == 0:
        return (
            "#### Regulator perturbation evidence\n"
            "No significant regulators identified from Perturb-seq."
        )

    lines = ["#### Regulator perturbation evidence"]
    lines.append(
        f"(Top {top_positive_regulators} activators + "
        f"{top_negative_regulators} repressors by adjusted p-value. Arrows (->) list the "
        "regulator's STRING functional-association partners among the program genes; the "
        "number in parentheses is the STRING v12 combined score (0-1000: >=400 medium, "
        ">=700 high confidence), queried live per regulator against the program genes.)"
    )
    lines.append("")

    def format_compact_interactions(val: Dict[str, Any]) -> str:
        string_ints = val.get("string_interactions", [])
        if not string_ints:
            return ""
        parts = []
        for si in string_ints:
            target = si.get("target", si.get("target_gene", "?"))
            score = si.get("score", 0)
            if score >= min_score:
                parts.append(f"{target}({score})")
        if not parts:
            return ""
        return " → " + ", ".join(parts[:8])

    activators = validation.get("positive_regulators", []) if validation else []
    repressors = validation.get("negative_regulators", []) if validation else []
    if mask:
        activators = [r for r in activators
                      if str(r.get("regulator", r.get("gene", ""))).strip().lower() not in mask]
        repressors = [r for r in repressors
                      if str(r.get("regulator", r.get("gene", ""))).strip().lower() not in mask]
    n_activators = min(len(activators), top_positive_regulators)
    n_repressors = min(len(repressors), top_negative_regulators)

    if activators:
        lines.append("Activators (knockdown reduces program activity):")
        for reg in activators[:n_activators]:
            gene = reg.get("regulator", "")
            log2fc = reg.get("log2fc", 0)
            interactions_str = format_compact_interactions(reg)
            if interactions_str:
                lines.append(f"- **{gene}** (log2FC={log2fc:.3f}){interactions_str}")
            else:
                lines.append(f"- {gene} (log2FC={log2fc:.3f})")
        lines.append("")

    if repressors:
        lines.append("Repressors (knockdown increases program activity):")
        for reg in repressors[:n_repressors]:
            gene = reg.get("regulator", "")
            log2fc = reg.get("log2fc", 0)
            interactions_str = format_compact_interactions(reg)
            if interactions_str:
                lines.append(f"- **{gene}** (log2FC={log2fc:+.3f}){interactions_str}")
            else:
                lines.append(f"- {gene} (log2FC={log2fc:+.3f})")
        lines.append("")

    return "\n".join(lines)


def format_condition_regulator_analysis_context(
    regulator_data: Dict[str, Dict[int, pd.DataFrame]],
    ncbi_data: Dict[int, Dict[str, Any]],
    program_id: int,
    top_positive_regulators: int = 3,
    top_negative_regulators: int = 3,
    min_score: int = 400,
    masked_regulators: Optional[Iterable[str]] = None,
) -> str:
    """Format per-condition regulator evidence for annotation prompts."""
    selected = select_top_condition_regulators(
        regulator_data,
        program_id=program_id,
        top_positive_regulators=top_positive_regulators,
        top_negative_regulators=top_negative_regulators,
        masked_regulators=masked_regulators,
    )
    if not selected or all(
        not groups["positive"] and not groups["negative"]
        for groups in selected.values()
    ):
        return (
            "#### Regulator perturbation evidence\n"
            "No significant regulators identified from Perturb-seq."
        )

    ctx = ncbi_data.get(program_id, {})
    validation_by_condition = ctx.get("regulator_validation_by_condition", {})
    if not isinstance(validation_by_condition, dict):
        validation_by_condition = {}

    def validation_map(condition: str, direction: str) -> Dict[str, Dict[str, Any]]:
        validation = validation_by_condition.get(condition, {})
        if not isinstance(validation, dict):
            return {}
        key = "positive_regulators" if direction == "positive" else "negative_regulators"
        regs = validation.get(key, [])
        if not isinstance(regs, list):
            return {}
        return {
            str(reg.get("regulator") or reg.get("gene")): reg
            for reg in regs
            if isinstance(reg, dict)
        }

    def format_compact_interactions(val: Dict[str, Any]) -> str:
        string_ints = val.get("string_interactions", [])
        if not string_ints:
            return ""
        parts = []
        for si in string_ints:
            if not isinstance(si, dict):
                continue
            target = si.get("target", si.get("target_gene", "?"))
            score = si.get("score", 0)
            if score >= min_score:
                parts.append(f"{target}({score})")
        return " -> " + ", ".join(parts[:8]) if parts else ""

    def format_optional_qc(reg: Dict[str, Any]) -> str:
        parts: List[str] = []
        if "adj_pvalue" in reg and pd.notna(reg["adj_pvalue"]):
            parts.append(f"adjP={float(reg['adj_pvalue']):.2e}")
        elif "pvalue" in reg and pd.notna(reg["pvalue"]):
            parts.append(f"P={float(reg['pvalue']):.2e}")
        if "pct_KD" in reg:
            parts.append(f"KD={float(reg['pct_KD']):.1f}%")
        if "pass_qc" in reg:
            parts.append(f"QC={'pass' if reg['pass_qc'] else 'fail'}")
        return "; ".join(parts)

    lines = ["#### Regulator perturbation evidence"]
    lines.append(
        f"(Top {top_positive_regulators} activators and "
        f"{top_negative_regulators} repressors per condition; duplicate guides "
        "collapsed and ranked by adjusted p-value. Arrows (->) list the regulator's "
        "STRING functional-association partners among the program genes; the number in "
        "parentheses is the STRING v12 combined score (0-1000: >=400 medium, >=700 high "
        "confidence), queried live per regulator against the program genes.)"
    )
    _mask = sorted({str(m).strip() for m in (masked_regulators or []) if str(m).strip()})
    if _mask:
        lines.append(
            f"(Promiscuous, non-program-specific regulators masked from selection: {', '.join(_mask)}.)"
        )
    lines.append("")
    for condition in sorted(selected.keys()):
        condition_label = condition[:1].upper() + condition[1:]
        groups = selected[condition]
        lines.append(f"{condition_label} condition")
        if groups["positive"]:
            lines.append("Activators (knockdown reduces program activity):")
            val_by_gene = validation_map(condition, "positive")
            for reg in groups["positive"]:
                gene = str(reg.get("gene", ""))
                qc = format_optional_qc(reg)
                interactions = format_compact_interactions(val_by_gene.get(gene, {}))
                suffix = f"; {qc}" if qc else ""
                lines.append(
                    f"- {gene} (log2FC={reg['log2fc']:.3f}{suffix}){interactions}"
                )
        if groups["negative"]:
            lines.append("Repressors (knockdown increases program activity):")
            val_by_gene = validation_map(condition, "negative")
            for reg in groups["negative"]:
                gene = str(reg.get("gene", ""))
                qc = format_optional_qc(reg)
                interactions = format_compact_interactions(val_by_gene.get(gene, {}))
                suffix = f"; {qc}" if qc else ""
                lines.append(
                    f"- {gene} (log2FC={reg['log2fc']:+.3f}{suffix}){interactions}"
                )
        lines.append("")
    return "\n".join(lines).strip()


# =============================================================================
# Theme-contrast context
# =============================================================================
def load_theme_dictionary(path: Optional[Path]) -> Dict[str, Any]:
    """Load the dataset-derived theme dictionary for prompt contrast guidance."""
    if not path:
        return {}
    if not path.exists():
        logger.warning("Theme dictionary file not found: %s", path)
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not parse theme dictionary %s: %s", path, exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _theme_program_ids(theme: Dict[str, Any]) -> Set[int]:
    ids: Set[int] = set()
    for value in theme.get("evidence_program_ids", []):
        parsed = _parse_program_id(value)
        if parsed is not None:
            ids.add(parsed)
    return ids


def format_theme_contrast_context(
    theme_dictionary: Dict[str, Any],
    program_id: int,
    max_themes: Optional[int] = None,
) -> str:
    """Format generic theme down-weighting guidance for one program."""
    themes = theme_dictionary.get("themes", [])
    if not isinstance(themes, list) or not themes:
        return "Not available."

    generic_themes = sorted(
        [theme for theme in themes if isinstance(theme, dict)],
        key=lambda theme: (
            -len(_theme_program_ids(theme)),
            str(theme.get("theme_term", "")),
        ),
    )
    if max_themes is not None:
        generic_themes = generic_themes[:max_themes]
    lines = []
    for theme in generic_themes:
        term = str(theme.get("theme_term", "")).strip()
        if not term:
            continue
        aliases = theme.get("aliases", [])
        clean_aliases = [str(alias).strip() for alias in aliases if str(alias).strip()]
        if clean_aliases:
            lines.append(f"- {term}: {', '.join(clean_aliases)}")
        else:
            lines.append(f"- {term}")
    return "\n".join(lines)


# =============================================================================
# Prompt assembly
# =============================================================================
def _context_phrase(profile: ContextProfile) -> str:
    """A short noun phrase for the biological context, e.g. 'hepatocyte, liver,
    aging, MASLD'. Replaces ProgExplorer's hard-coded 'hepatocyte/liver/MASLD'
    inline mentions so no tissue leaks into a generic profile's framing."""
    bits: List[str] = []
    for piece in [profile.cell_type, profile.tissue, *profile.conditions]:
        piece = str(piece).strip()
        if piece and piece not in bits:
            bits.append(piece)
    if not bits:
        return "the experimental"
    return ", ".join(bits)


def generate_prompt(
    program_id: int,
    gene_df: pd.DataFrame,
    prompt_template: str,
    top_loading: int,
    top_unique: int,
    celltype_map: Dict[int, Dict[str, Any]],
    enrichment_by_program: Dict[int, Dict[str, List[dict]]],
    ncbi_data: Dict[int, Dict[str, Any]],
    top_enrichment: int,
    genes_per_term: int,
    profile: ContextProfile,
    regulator_data: Optional[Dict[int, pd.DataFrame]] = None,
    top_positive_regulators: int = 3,
    top_negative_regulators: int = 3,
    theme_dictionary: Optional[Dict[str, Any]] = None,
    masked_regulators: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Assemble the annotation prompt for one program.

    Framing strings (role/context/keyword/condition/functional) are taken from
    `profile.prompt_fields()` so no tissue is hard-coded.
    """
    top_loading_genes, unique_genes = select_program_genes(
        gene_df=gene_df,
        program_id=program_id,
        top_loading=top_loading,
        top_unique=top_unique,
    )
    if not top_loading_genes:
        logger.warning("No genes found for Program %s", program_id)
        return None

    enrichment_context = build_enrichment_context(
        enrichment_by_program=enrichment_by_program,
        program_id=program_id,
        top_enrichment=top_enrichment,
        genes_per_term=genes_per_term,
    )

    celltype_context = format_celltype_context(celltype_map, program_id)

    gene_context = (
        "#### Program genes\n\n"
        "Top-loading genes:\n"
        f"{', '.join(top_loading_genes)}"
    )
    if unique_genes:
        gene_context += "\n\nUnique genes:\n" f"{', '.join(unique_genes)}"

    program_ncbi_context = ncbi_data.get(program_id, {})
    research_evidence_context = format_research_evidence_context(program_ncbi_context)
    context_phrase = _context_phrase(profile)
    if research_evidence_context:
        evidence_guidance = (
            "- Primary evidence: program genes and research-evidence modules\n"
            "- Supporting evidence: top KEGG/GO enrichment, regulator perturbation "
            f"evidence, gene summaries, cell-type enrichment, and {context_phrase} context.\n"
            "- Refine final module labels, boundaries, and gene membership using "
            "all supplied evidence, with primary evidence carrying the most weight."
        )
    else:
        evidence_guidance = (
            "- Primary evidence: program genes\n"
            "- Supporting evidence: top KEGG/GO enrichment, regulator perturbation "
            f"evidence, gene summaries, cell-type enrichment, and {context_phrase} context.\n"
            "- Refine final module labels, boundaries, and gene membership using "
            "all supplied evidence, with primary evidence carrying the most weight."
        )
    allowed_genes = (
        set(top_loading_genes)
        | set(unique_genes)
        | research_evidence_genes(program_ncbi_context)
    )
    ncbi_context = format_ncbi_context(ncbi_data, program_id, allowed_genes=allowed_genes)

    regulator_analysis = format_regulator_analysis_context(
        regulator_data or {},
        ncbi_data,
        program_id,
        top_positive_regulators=top_positive_regulators,
        top_negative_regulators=top_negative_regulators,
        masked_regulators=masked_regulators,
    )
    theme_contrast_context = format_theme_contrast_context(
        theme_dictionary or {},
        program_id,
    )

    framing = profile.prompt_fields()

    return (
        prompt_template.replace("{program_id}", str(program_id))
        .replace("{gene_context}", gene_context)
        .replace("{research_evidence_context}", research_evidence_context)
        .replace("{regulator_analysis}", regulator_analysis)
        .replace("{theme_contrast_context}", theme_contrast_context)
        .replace("{celltype_context}", celltype_context)
        .replace("{enrichment_context}", enrichment_context)
        .replace("{ncbi_context}", ncbi_context)
        .replace("{context_phrase}", context_phrase)
        .replace("{annotation_role}", framing["annotation_role"])
        .replace("{annotation_context}", framing["annotation_context"])
        .replace("{search_keyword}", framing["search_keyword"])
        .replace("{condition_context}", framing["condition_context"])
        .replace("{functional_context}", framing["functional_context"])
        .replace("{evidence_guidance}", evidence_guidance)
    )


def build_annotation_requests(
    program_ids: Sequence[int],
    gene_df: pd.DataFrame,
    profile: ContextProfile,
    *,
    celltype_map: Optional[Dict[int, Dict[str, Any]]] = None,
    enrichment_by_program: Optional[Dict[int, Dict[str, List[dict]]]] = None,
    ncbi_data: Optional[Dict[int, Dict[str, Any]]] = None,
    regulator_data: Optional[Dict[Any, Any]] = None,
    theme_dictionary: Optional[Dict[str, Any]] = None,
    model: str = MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    top_loading: int = 20,
    top_unique: int = 10,
    top_enrichment: int = 7,
    genes_per_term: int = 10,
    top_positive_regulators: int = 3,
    top_negative_regulators: int = 3,
    thinking: Optional[str] = None,
    effort: Optional[str] = None,
    masked_regulators: Optional[Iterable[str]] = None,
) -> List[dict]:
    """Build Anthropic-batch request dicts for a set of programs.

    Returns a list of `{"custom_id": f"topic_{pid}", "params": {...}}` ready for
    `gpi.anthropic_batch.submit_batch`. Programs with no genes are skipped.
    """
    celltype_map = celltype_map or {}
    enrichment_by_program = enrichment_by_program or {}
    ncbi_data = ncbi_data or {}

    requests: List[dict] = []
    for program_id in program_ids:
        prompt = generate_prompt(
            program_id=program_id,
            gene_df=gene_df,
            prompt_template=PROMPT_TEMPLATE,
            top_loading=top_loading,
            top_unique=top_unique,
            celltype_map=celltype_map,
            enrichment_by_program=enrichment_by_program,
            ncbi_data=ncbi_data,
            top_enrichment=top_enrichment,
            genes_per_term=genes_per_term,
            profile=profile,
            regulator_data=regulator_data,
            top_positive_regulators=top_positive_regulators,
            top_negative_regulators=top_negative_regulators,
            theme_dictionary=theme_dictionary,
            masked_regulators=masked_regulators,
        )
        if not prompt:
            continue
        params: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if thinking:
            params["thinking"] = {"type": thinking}
        if effort:
            params["output_config"] = {"effort": effort}
        requests.append({"custom_id": f"topic_{program_id}", "params": params})
    return requests


PROMPT_TEMPLATE = """
## Program annotation task

You are a {annotation_role}. Interpret Program {program_id}, {annotation_context}.

### Task
- Provide a specific, evidence-anchored interpretation of Program {program_id}.
{evidence_guidance}

### Primary evidence
{gene_context}
{research_evidence_context}
{condition_context}
{functional_context}

### Supporting evidence
{ncbi_context}
{enrichment_context}
{celltype_context}
{regulator_analysis}

### Interpretation rules
- Cite genes and supplied evidence for biological claims.
- Use the cell-type log2FC values to judge whether this is a cell-type identity program or a cross-cell-type functional program. Strong depletion in a lineage is as informative as enrichment.
- Treat research-evidence modules as candidate modules, not fixed final boundaries.
- Add 1-2 de novo functional theme candidates when primary gene descriptions, regulators, or enrichments support them.
- Do not automatically select all research-evidence candidates; a de novo candidate may replace a research-evidence candidate when it is more specific or clearly supported by evidence.
- Do not refer to upstream labels such as "research-evidence Module 1", "research module", or "candidate module" anywhere in the final output, including the evidence used field; use genes, pathways, regulator evidence, and Supporting PMIDs to trace evidence instead.
- Include all supplied PMIDs, only if a final module strongly overlaps a research-evidence module.
- Select 1-3 final modules from this candidate pool, ranked by specificity and reasoning; generic-theme-dominated modules should be down-weighted (refer to generic themes to down-weight section).
- Final program label should be decided after considering all selected modules and should be a coherent, human-readable biological phrase; If the selected modules are distinct and do not naturally relate, pick a label based on the most representative module. Avoid generic dictionary terms, cell-type filler such as "state/identity" unless necessary.

### Generic themes to down-weight
{theme_contrast_context}

### Output requirements (GitHub-flavored Markdown)
Start with: `## Program {program_id} annotation`

CRITICALLY, include the following two lines near the top, exactly with these bold labels:
- **Brief Summary:** <1-2 sentences>
- **Program label:** <=6 words

Then provide the following sections:

1. **High-level overview (<=120 words)**
   - Main theme(s) grounded in the primary evidence.
   - Connect to {context_phrase} context only when supported by the supplied genes and curated literature evidence.

2. **Functional modules and mechanisms**
   Group genes into 1-3 final modules. For each module, use this exact format:
   ```
   Module name
   A 2-4 sentence summary — directly reuse or refine the matching research-evidence module's literature summary, folding in notable additional evidence (regulator perturbation, gene summaries, or {context_phrase} context) only when it adds specificity. For a de novo module with no literature summary, write a concise evidence-anchored summary.
   Key genes: list 2-10
   Supporting PMIDs: comma-separated PMIDs directly supporting this final module, or None
   evidence used: cite any of the supplied evidence that supports this module — program genes, regulator perturbations, enrichment terms, cell-type context, NCBI gene summaries, and/or literature — not only genes and PMIDs. Refer to evidence by its content (gene names, term/pathway names, regulator names), never by upstream labels.
   ```

3. **Distinctive features**
   - Describe what is most distinctive about Program {program_id} in 1-2 sentences. Cite unique genes and provide reasoning.
   - If evidence is limited or mixed, say so explicitly.

4. **Regulator analysis**
   List 1-3 most prominent regulators from Perturb-seq, for each regulator use this exact format:
   ```
   regulator_name (role, log2FC=X): [Confidence: High/Medium/Low]
   Propose a mechanistic hypothesis: How might this regulator control the program's genes/pathways? Cite program genes and evidence.
   ```
"""


# =============================================================================
# CLI: prepare
# =============================================================================
def _load_profile(profile_arg: Optional[str]) -> ContextProfile:
    if not profile_arg:
        return ContextProfile.liver_demo()
    path = Path(profile_arg)
    if not path.exists():
        raise SystemExit(f"Profile file not found: {path}")
    return ContextProfile.from_yaml(path)


def cmd_prepare(args: argparse.Namespace) -> int:
    """Prepare a batch request JSON file for the given gene CSV."""
    if not args.gene_file:
        logger.error("--gene-file is required (via CLI or config).")
        return 2
    try:
        gene_df = load_gene_table(
            Path(args.gene_file),
            program_id_offset=getattr(args, "program_id_offset", 0) or 0,
        )
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error reading gene file: %s", exc)
        return 2

    profile = _load_profile(getattr(args, "profile", None))

    program_ids = sorted(gene_df["program_id"].dropna().astype(int).unique().tolist())

    selected_topics = parse_topics_value(args.topics)
    if selected_topics:
        program_ids = [pid for pid in program_ids if pid in selected_topics]
        logger.info("Limiting to specific topics: %s", program_ids)
    elif args.num_topics:
        program_ids = program_ids[: args.num_topics]
        logger.info("Limiting to first %s topics for testing.", args.num_topics)

    celltype_file = Path(args.celltype_file) if args.celltype_file else None
    celltype_dir = Path(args.celltype_dir) if args.celltype_dir else None
    celltype_map = (
        load_celltype_annotations(celltype_dir, celltype_file)
        if celltype_dir or celltype_file
        else {}
    )
    enrichment_by_program = prepare_enrichment_mapping(
        Path(args.enrichment_file) if args.enrichment_file else None
    )
    ncbi_data = load_prompt_literature_context(
        ncbi_file=Path(args.ncbi_file) if args.ncbi_file else None,
        research_evidence_dir=Path(args.research_evidence_dir)
        if args.research_evidence_dir
        else None,
        program_ids=program_ids,
    )
    theme_dictionary = load_theme_dictionary(
        Path(args.theme_dictionary_file) if args.theme_dictionary_file else None
    )
    condition_files = parse_condition_path_args(
        getattr(args, "regulator_condition_file", None)
    )
    condition_qc_files = parse_condition_path_args(
        getattr(args, "regulator_qc_file", None)
    )
    if condition_files:
        regulator_data: Dict[Any, Any] = load_condition_regulator_data(
            condition_files,
            regulator_qc_files=condition_qc_files,
            significance_threshold=args.regulator_significance_threshold,
        )
    else:
        regulator_data = load_regulator_data(
            Path(args.regulator_file) if args.regulator_file else None,
            significance_threshold=args.regulator_significance_threshold,
        )

    batch_requests = build_annotation_requests(
        program_ids,
        gene_df,
        profile,
        celltype_map=celltype_map,
        enrichment_by_program=enrichment_by_program,
        ncbi_data=ncbi_data,
        regulator_data=regulator_data,
        theme_dictionary=theme_dictionary,
        model=args.model or MODEL,
        max_tokens=args.max_tokens or DEFAULT_MAX_TOKENS,
        top_loading=args.top_loading,
        top_unique=args.top_unique,
        top_enrichment=args.top_enrichment,
        genes_per_term=args.genes_per_term,
        top_positive_regulators=args.top_positive_regulators,
        top_negative_regulators=args.top_negative_regulators,
        thinking=args.thinking,
        effort=args.effort,
        masked_regulators=list(getattr(args, "mask_regulator", None) or []),
    )

    if not batch_requests:
        logger.error("No requests were generated. Aborting.")
        return 3

    payload = {"requests": batch_requests}
    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logger.info(
            "Successfully created batch request file at %s (%d requests)",
            output_path,
            len(batch_requests),
        )
        return 0
    except OSError as exc:
        logger.error("Error writing to file: %s", exc)
        return 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assemble Anthropic batch annotation prompts from program evidence."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("prepare", help="Build the batch request JSON only")
    p.add_argument("--config", help="Path to config file (YAML or JSON)")
    p.add_argument(
        "--gene-file",
        help="Gene CSV with columns Name, Score, program_id (or RowID).",
    )
    p.add_argument(
        "--program-id-offset",
        type=int,
        default=0,
        help="Integer offset added when gene input uses RowID instead of program_id",
    )
    p.add_argument(
        "--profile",
        help="ContextProfile YAML (default: ContextProfile.liver_demo()).",
    )
    p.add_argument(
        "--model",
        default=MODEL,
        help=f"Model stored in prepared requests (default: {MODEL})",
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help="Max tokens stored in prepared requests",
    )
    p.add_argument(
        "--thinking",
        choices=["adaptive"],
        help="Claude thinking mode for prepared requests",
    )
    p.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="Claude output_config.effort for prepared requests",
    )
    p.add_argument(
        "--celltype-dir",
        help="Directory containing a program cell-type CSV (celltype_detail.csv preferred)",
    )
    p.add_argument(
        "--celltype-file",
        help="Path to a cell-type CSV, long-format or legacy summary; format is detected from "
             "its columns (overrides --celltype-dir)",
    )
    p.add_argument("--enrichment-file", help="STRING enrichment CSV (Process/KEGG) with inputGenes")
    p.add_argument("--ncbi-file", help="NCBI context JSON (from gene_summaries fetch)")
    p.add_argument(
        "--research-evidence-dir",
        help="Directory of per-program ResearchResult JSON files to merge into prompts",
    )
    p.add_argument("--theme-dictionary-file", help="Theme dictionary JSON for down-weighting")
    p.add_argument("--top-loading", type=int, default=20, help="Top-loading genes per program")
    p.add_argument("--top-unique", type=int, default=10, help="Unique genes per program")
    p.add_argument("--top-enrichment", type=int, default=7, help="Top-N enrichment rows by FDR")
    p.add_argument("--genes-per-term", type=int, default=10, help="Member genes per enrichment term")
    p.add_argument(
        "--output-file",
        default="anthropic_batch_request.json",
        help="Path to save the generated batch request JSON",
    )
    p.add_argument("--num-topics", type=int, help="Limit number of topics (testing)")
    p.add_argument("--topics", type=str, help="Comma-separated topic IDs to process")
    p.add_argument("--regulator-file", type=str, help="CSV with regulator results")
    p.add_argument(
        "--regulator-condition-file",
        action="append",
        help="Condition-specific regulator matrix as condition=path; repeatable",
    )
    p.add_argument(
        "--regulator-qc-file",
        action="append",
        help="Condition-specific regulator QC table as condition=path; repeatable",
    )
    p.add_argument("--top-positive-regulators", type=int, default=3)
    p.add_argument("--top-negative-regulators", type=int, default=3)
    p.add_argument(
        "--mask-regulator", action="append", metavar="GENE",
        help="Regulator gene to mask from the annotation's regulator evidence (promiscuous, "
             "non-program-specific); repeatable. Masked before top-N selection, both conditions.",
    )
    p.add_argument("--regulator-significance-threshold", type=float, default=0.05)
    p.set_defaults(func=cmd_prepare)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(getattr(args, "config", None))
    if config:
        cli_overrides = get_cli_overrides(argv)
        args = apply_config_overrides(args, config, cli_overrides)
        args = apply_test_mode(args, config, cli_overrides)

    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
