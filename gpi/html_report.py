"""
Reading Desk - HTML Report Generator

Professional scientific design with:
- Program stats at TOP
- 1:1 volcano plot with ALL program points
- PRIORITY_GENES labeling
- Full-text search
- Clean grayscale + teal accent
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from markdown import markdown
from .column_mapper import (
    standardize_condition_regulator_results,
    standardize_gene_loading,
    standardize_regulator_results,
)

PRIORITY_GENES = [
    "Hif1a", "Epas1", "Arnt", "Fzd4", "Fzd6", "Idh2", "Mdh2", "Ogdh",
    "Hsp90ab1", "Hspa5", "Creb3l2", "Fkbp8", "Kdr", "Egln1", "Egln2", "Foxo1", "Foxo3"
]


def parse_condition_path_args(values: list[str] | None) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"Expected condition=path value, got {value!r}.")
        condition, path = value.split("=", 1)
        condition = condition.strip()
        if not condition:
            raise ValueError(f"Condition cannot be empty in {value!r}.")
        parsed[condition] = Path(path)
    return parsed


def extract_program_stats(annotation_md: str) -> dict:
    """Extract stats from annotation markdown."""
    stats = {'top_loading': '', 'unique': '', 'celltype': ''}
    
    # Extract top-loading genes
    match = re.search(r'\*\*Top-loading genes?:\*\*\s*([^\n]+)', annotation_md, re.I)
    if match:
        stats['top_loading'] = match.group(1).strip()
    
    # Extract unique genes
    match = re.search(r'\*\*Unique genes?:\*\*\s*([^\n]+)', annotation_md, re.I)
    if match:
        stats['unique'] = match.group(1).strip()
    
    # Extract cell-type enrichment
    match = re.search(r'\*\*Cell-type enrichment:\*\*\s*([^\n]+)', annotation_md, re.I)
    if match:
        stats['celltype'] = match.group(1).strip()
    
    # Extract brief summary
    match = re.search(r'\*\*Brief Summary:\*\*\s*([^\n]+)', annotation_md, re.I)
    if match:
        stats['summary'] = match.group(1).strip()
    
    # Extract program label
    match = re.search(r'\*\*Program label:\*\*\s*([^\n]+)', annotation_md, re.I)
    if match:
        stats['label'] = match.group(1).strip()
    
    return stats


def split_pathway_enrichment(annotation_md: str) -> tuple[str, str]:
    """Remove a Pathway Enrichment section so the report can place it by figures."""
    # Case-insensitive: the live model varies the casing ("Pathway enrichment").
    heading_pattern = re.compile(
        r"(?msi)^#{2,3}\s*(?:\d+\.\s*)?Pathway Enrichment\s*\n"
        r"(?P<body>.*?)(?=^#{2,3}\s*(?:\d+\.\s*)?"
        r"(?:High-level overview|Functional modules|Distinctive features|"
        r"Regulator analysis)\b|\Z)"
    )
    match = heading_pattern.search(annotation_md)
    if match:
        section_md = match.group(0).strip()
        remaining_md = (annotation_md[:match.start()] + annotation_md[match.end():]).strip()
        return remaining_md, section_md

    bold_pattern = re.compile(
        r"(?ms)^\d+\.\s+\*\*Pathway Enrichment\*\*\s*\n"
        r"(?P<body>.*?)(?=^\d+\.\s+\*\*"
        r"(?:High-level overview|Functional modules|Distinctive features|"
        r"Regulator analysis)\*\*|\Z)"
    )
    match = bold_pattern.search(annotation_md)
    if match:
        section_md = "## Pathway Enrichment\n" + match.group("body").strip()
        remaining_md = (annotation_md[:match.start()] + annotation_md[match.end():]).strip()
        return remaining_md, section_md

    return annotation_md, ""


def _clean_line_value(line: str) -> str:
    return re.sub(r"\s+", " ", line.split(":", 1)[1].strip()) if ":" in line else ""


def _split_csv_values(value: str) -> list[str]:
    if not value or value.strip().lower() == "none":
        return []
    return [
        item.strip()
        for item in re.split(r"[,;|]", value)
        if item.strip()
    ]


def _extract_pmids(text: str) -> list[str]:
    seen: set[str] = set()
    pmids: list[str] = []
    for pmid in re.findall(r"\b\d{6,9}\b", text or ""):
        if pmid in seen:
            continue
        seen.add(pmid)
        pmids.append(pmid)
    return pmids


def _extract_dois(text: str) -> list[str]:
    """Pull DOIs from free text.

    Recognizes bare DOIs (``10.xxxx/yyyy``) as well as ``doi:``-prefixed and
    ``https://doi.org/``-prefixed forms. Deduplicates while preserving order.
    Additive to the legacy PMID-only parsing so annotation markdown that carries
    DOIs renders links, while markdown without DOIs is unaffected.
    """
    seen: set[str] = set()
    dois: list[str] = []
    # Strip a leading resolver/prefix, then match the DOI core. A DOI is
    # 10.<registrant>/<suffix>; the suffix runs until whitespace or a closing
    # bracket/quote that cannot be part of a DOI in this context.
    pattern = re.compile(
        r"(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?\b(10\.\d{4,9}/[^\s\"'<>\]\)]+)",
        re.I,
    )
    for match in pattern.finditer(text or ""):
        doi = match.group(1).rstrip(".,;")
        if doi in seen:
            continue
        seen.add(doi)
        dois.append(doi)
    return dois


def _demarkup(line: str) -> str:
    """Strip leading heading markers (``###``) and bold ``**`` from a line so field-label
    matching is agnostic to the model's markdown style (plain / bold-labeled / H3)."""
    return re.sub(r"^\s*#{1,6}\s*", "", line).replace("**", "").strip()


def _parse_module_block(block: str) -> dict[str, object] | None:
    lines = [_demarkup(line) for line in block.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return None

    module = {
        "title": lines[0],
        "summary": lines[1],
        "key_genes": [],
        "pmids": [],
        "dois": [],
        "evidence": "",
        "mechanism": "",
    }
    evidence_text = ""
    for line in lines[2:]:
        lower = line.lower()
        if lower.startswith("key genes:"):
            module["key_genes"] = _split_csv_values(_clean_line_value(line))
        elif lower.startswith("supporting pmids:"):
            module["pmids"] = _extract_pmids(_clean_line_value(line))
        elif lower.startswith("supporting dois:"):
            module["dois"] = _extract_dois(_clean_line_value(line))
        elif lower.startswith("evidence used:"):
            evidence_text = _clean_line_value(line)
            module["evidence"] = evidence_text
        elif lower.startswith("proposed mechanism:"):
            module["mechanism"] = _clean_line_value(line)
        elif module["mechanism"]:
            module["mechanism"] = f"{module['mechanism']} {line}"
        elif module["evidence"]:
            evidence_text = f"{evidence_text} {line}"
            module["evidence"] = evidence_text
        else:
            module["summary"] = f"{module['summary']} {line}"

    if not module["pmids"]:
        module["pmids"] = _extract_pmids(evidence_text)
    if not module["dois"]:
        module["dois"] = _extract_dois(evidence_text)
    return module


def _fenced_blocks(text: str) -> list[str]:
    """Return the contents of every ```fenced``` code block in ``text``."""
    return re.findall(r"```(?:[a-zA-Z0-9_-]+)?\s*\n(.*?)```", text, flags=re.S)


def _bold_header_blocks(section_md: str, header_re: "re.Pattern[str]") -> list[str]:
    """Split ``section_md`` into blocks that each begin at a bold-header line.

    Fallback for annotations that use ``**Header ...**`` bold headers instead of the
    canonical ``` fenced blocks (the live model does this consistently). A block runs
    from one header line up to the next; the ``**`` markers are stripped so the
    downstream per-block parsers (which expect plain ``Module 1: ...`` /
    ``gene (role ...)`` lines) see clean text. Returns [] if no header matches.
    """
    lines = section_md.splitlines()
    starts = [i for i, ln in enumerate(lines) if header_re.match(ln.replace("**", ""))]
    if not starts:
        return []
    starts.append(len(lines))
    blocks: list[str] = []
    for i in range(len(starts) - 1):
        block = "\n".join(lines[starts[i]:starts[i + 1]]).replace("**", "").strip()
        if block:
            blocks.append(block)
    return blocks


def _extract_section_body(annotation_md: str, section_name: str) -> str:
    """Return the body of a ``## <section_name>`` / ``### <section_name>`` section
    (case-insensitive), up to the next heading, or '' if absent. Also handles the
    numbered-bold ``N. **<section_name>**`` heading variant."""
    esc = re.escape(section_name)
    for pat in (
        re.compile(rf"(?ims)^#{{2,3}}\s*(?:\d+\.\s*)?{esc}\s*\n(?P<body>.*?)(?=^#{{2,3}}\s|\Z)"),
        re.compile(rf"(?ims)^\d+\.\s+\*\*{esc}\*\*\s*\n(?P<body>.*?)(?=^\d+\.\s+\*\*|\Z)"),
    ):
        m = pat.search(annotation_md)
        if m:
            return m.group("body").strip()
    return ""


# A final-module head: "Module 1: ...", tolerant of a leading list marker.
_MODULE_HEAD_RE = re.compile(r"^\s*(?:[-*]\s*)?Module\s+\d+\s*:", re.I)

# A whole-line-bold header (the module NAME on its own line). Covers BOTH module-header
# formats the live model emits: "**Module 1: Name**" and just "**Name**".
_BOLD_LINE_RE = re.compile(r"^\s*\*\*.+\*\*\s*$")


def _bold_line_blocks(section_md: str) -> list[str]:
    """Split a functional-modules section into blocks headed by a whole-line-bold line
    (the module name). Fallback for annotations whose module headers are just the bold
    name, with no 'Module N:' prefix. Bold markers are stripped so the per-block parser
    sees a clean title line. Fields (Key genes:, Supporting PMIDs:, ...) are not bold, so
    only the module names split."""
    lines = section_md.splitlines()
    starts = [i for i, ln in enumerate(lines) if _BOLD_LINE_RE.match(ln)]
    if not starts:
        return []
    starts.append(len(lines))
    blocks: list[str] = []
    for i in range(len(starts) - 1):
        block = "\n".join(lines[starts[i]:starts[i + 1]]).replace("**", "").strip()
        if block:
            blocks.append(block)
    return blocks


# An H3 heading line (module NAME as ``### Name``) — another format the live model
# emits. Excludes horizontal rules and empty headings.
_H3_HEAD_RE = re.compile(r"^\s*###\s+\S")


def _h3_header_blocks(section_md: str) -> list[str]:
    """Split a functional-modules section into blocks headed by an H3 (``### Name``) line.

    Fallback for annotations whose module headers are markdown H3 headings and whose
    fields are bold-labeled (``**Key genes:**`` ...). The ``###`` marker on the header
    line and all ``**`` bold markers are stripped so the per-block parser sees a clean
    title line and plain ``Key genes:``/``Supporting PMIDs:``/``Evidence used:`` labels.
    Horizontal-rule lines (``---``) are dropped so a trailing separator before the next
    section does not leak into the last module's text."""
    lines = section_md.splitlines()
    starts = [i for i, ln in enumerate(lines) if _H3_HEAD_RE.match(ln)]
    if not starts:
        return []
    starts.append(len(lines))
    blocks: list[str] = []
    for i in range(len(starts) - 1):
        chunk = list(lines[starts[i]:starts[i + 1]])
        chunk[0] = re.sub(r"^\s*#{2,6}\s*", "", chunk[0])  # strip the '### ' heading marker
        kept = [ln for ln in chunk if ln.strip() not in {"---", "***", "___"}]
        block = "\n".join(kept).replace("**", "").strip()
        if block:
            blocks.append(block)
    return blocks


def split_final_modules(annotation_md: str) -> tuple[str, list[dict[str, object]]]:
    """Extract final functional modules so the report can render them as cards."""
    patterns = [
        re.compile(
        r"(?ms)^#{2,3}\s*(?:\d+\.\s*)?Functional modules(?: and mechanisms)?\s*\n"
        r"(?P<body>.*?)(?=^#{2,3}\s*(?:\d+\.\s*)?"
        r"(?:Distinctive features|Regulator analysis|Pathway Enrichment|High-level overview)\b|\Z)"
        ),
        re.compile(
            r"(?ms)^\d+\.\s+\*\*Functional modules(?: and mechanisms)?\*\*\s*\n"
            r"(?P<body>.*?)(?=^\d+\.\s+\*\*"
            r"(?:Distinctive features|Regulator analysis|Pathway Enrichment|High-level overview)\*\*|\Z)"
        ),
    ]
    match = next((pattern.search(annotation_md) for pattern in patterns if pattern.search(annotation_md)), None)
    if not match:
        return annotation_md, []

    section_body = match.group("body")
    # Canonical: each module in a ``` fenced block. Fallbacks (the live model varies its
    # header style run-to-run): "Module N:" headers, then "### Name" H3 headings, then a
    # whole-line-bold module name (**Name** or **Module 1: Name**). Try the more specific
    # H3 splitter before the bold-line one.
    blocks = (
        _fenced_blocks(section_body)
        or _bold_header_blocks(section_body, _MODULE_HEAD_RE)
        or _h3_header_blocks(section_body)
        or _bold_line_blocks(section_body)
    )
    modules = [parsed for block in blocks if (parsed := _parse_module_block(block))]
    remaining_md = (annotation_md[:match.start()] + annotation_md[match.end():]).strip()
    return remaining_md, modules


# Regulator head: tolerant of role clauses such as "(repressor, ...)",
# "(activator in both aged and young conditions, ...)", "(activators, ...)".
_REG_HEAD_RE = re.compile(
    r"^\s*(?P<gene>[A-Za-z0-9/().+\-]+?)\s*\(\s*(?P<role>repressor|activator)s?\b",
    re.I,
)
_REG_FC_RE = re.compile(r"log[\u2082\u2083]?2?FC\s*=\s*(?P<fc>[^)\]\n]*)", re.I)
_REG_CONF_RE = re.compile(r"\[\s*Confidence\s*:\s*(?P<conf>[^\]]+)\]", re.I)
# Both "Mechanistic hypothesis:" and "Propose a mechanistic hypothesis:" occur.
_REG_MECH_RE = re.compile(
    r"^\s*(?:Propose(?:d)?\s+a\s+)?Mechanistic\s+hypothesis\s*:\s*(?P<text>.*)$",
    re.I,
)
_PATHWAY_LINE_RE = re.compile(
    r"^\s*[-*]?\s*(?P<source>[^:]+?)\s*:\s*(?P<term>.+?)\s*"
    r"\(\s*FDR\s*=\s*(?P<fdr>[^)]+)\)\s*[-\u2013]\s*member genes\s*:\s*(?P<genes>.+)$",
    re.I,
)


def _parse_regulator_block(block: str) -> dict[str, str] | None:
    """Parse one regulator block ("gene (role, log2FC=...): [Confidence: ...]" + a
    mechanistic-hypothesis paragraph) into a card, or None if the head doesn't match."""
    lines = [line.rstrip() for line in block.strip("\n").splitlines() if line.strip()]
    if not lines:
        return None
    head = _REG_HEAD_RE.match(lines[0])
    if not head:
        return None
    fc_match = _REG_FC_RE.search(lines[0])
    fc = re.sub(r"\s+", " ", fc_match.group("fc").strip()).rstrip(":").strip() if fc_match else ""
    conf_match = _REG_CONF_RE.search(lines[0])
    confidence = conf_match.group("conf").strip() if conf_match else ""

    mechanism_parts: list[str] = []
    capturing = False
    for line in lines[1:]:
        field = _REG_MECH_RE.match(line)
        if field:
            capturing = True
            if field.group("text").strip():
                mechanism_parts.append(field.group("text").strip())
        elif capturing:
            mechanism_parts.append(line.strip())
    mechanism = " ".join(mechanism_parts).strip()
    # Fallback when the mechanism label is absent: use the remaining prose.
    if not mechanism and len(lines) > 1:
        mechanism = " ".join(line.strip() for line in lines[1:]).strip()

    return {
        "gene": head.group("gene").strip(),
        "role": head.group("role").lower(),
        "fc": fc,
        "confidence": confidence or "—",
        "mechanism": mechanism,
    }


def parse_regulators_detailed(annotation_md: str) -> list[dict[str, str]]:
    """Parse the Regulator-analysis section into structured cards.

    Each card carries gene, role (repressor/activator), the raw fold-change
    string (e.g. "+1.831 young / +0.359 aged"), a confidence label, and the
    mechanistic hypothesis prose. The role clause and mechanism label vary between
    annotations, so matching is intentionally permissive.

    Canonical annotations wrap each regulator in a ``` fenced block; the live model
    instead emits ``**gene (role, ...): [Confidence: ...]**`` bold headers. Try the
    fenced blocks first, then fall back to bold-header blocks scoped to the
    "Regulator analysis" section (so module/pathway prose is never misread).
    """
    blocks = [b for b in _fenced_blocks(annotation_md) if _REG_HEAD_RE.match(b.strip().splitlines()[0] if b.strip() else "")]
    if not blocks:
        section = _extract_section_body(annotation_md, "Regulator analysis")
        if section:
            blocks = _bold_header_blocks(section, _REG_HEAD_RE)
    return [card for block in blocks if (card := _parse_regulator_block(block))]


def parse_pathways(annotation_md: str) -> list[dict[str, object]]:
    """Parse the Pathway Enrichment block into structured, sortable terms."""
    _, section_md = split_pathway_enrichment(annotation_md)
    if not section_md:
        return []
    pathways: list[dict[str, object]] = []
    for line in section_md.splitlines():
        match = _PATHWAY_LINE_RE.match(line)
        if not match:
            continue
        genes = [gene.strip() for gene in re.split(r"[,;]", match.group("genes")) if gene.strip()]
        pathways.append(
            {
                "source": match.group("source").strip(),
                "term": match.group("term").strip(),
                "fdr": match.group("fdr").strip(),
                "genes": genes,
            }
        )
    return pathways


def extract_distinctive(annotation_md: str) -> str:
    """Pull the Distinctive-features prose (keeping *markdown italics*)."""
    pattern = re.compile(
        r"(?ms)^#{2,3}\s*(?:\d+\.\s*)?Distinctive features\s*\n"
        r"(?P<body>.*?)(?=^#{2,3}\s|\Z)"
    )
    match = pattern.search(annotation_md)
    if not match:
        return ""
    body = re.sub(r"```.*?```", "", match.group("body"), flags=re.S)
    lines = [
        line.strip()
        for line in body.splitlines()
        if line.strip() and line.strip() != "---"
    ]
    return " ".join(lines).strip()


def clean_report_annotation_body(annotation_md: str) -> str:
    """Remove report-header fields that are already shown in the top panel."""
    lines: list[str] = []
    skip_patterns = [
        re.compile(r"^##\s+Program\s+\d+\s+annotation\s*$", re.I),
        re.compile(r"^-\s+\*\*Brief Summary:\*\*", re.I),
        re.compile(r"^-\s+\*\*Program label:\*\*", re.I),
    ]
    for line in annotation_md.splitlines():
        if any(pattern.search(line.strip()) for pattern in skip_patterns):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def add_global_uniqueness_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Compute a TF-IDF-like uniqueness score when the input table lacks one."""
    required = {"Name", "Score", "program_id"}
    if not required.issubset(df.columns):
        return df

    updated = df.copy()
    updated["Score"] = pd.to_numeric(updated["Score"], errors="coerce")
    updated["program_id"] = pd.to_numeric(updated["program_id"], errors="coerce")

    valid = updated.dropna(subset=["Name", "Score", "program_id"]).copy()
    if valid.empty:
        return updated

    valid["program_id"] = valid["program_id"].astype(int)
    total_programs = valid["program_id"].nunique()
    gene_counts = valid.groupby("Name")["program_id"].nunique().astype(float)
    idf = np.log((total_programs + 1.0) / (gene_counts + 1.0))
    valid["UniquenessScore"] = valid["Score"] * valid["Name"].map(idf)

    updated["UniquenessScore"] = np.nan
    updated.loc[valid.index, "UniquenessScore"] = valid["UniquenessScore"]
    return updated


def build_panel_stats(
    summary_df: pd.DataFrame,
    gene_loading_csv: str,
    top_loading: int = 15,
    top_unique: int = 8,
) -> dict[int, dict]:
    """Build report top-panel data without relying on LLM output sections.

    The gene loading CSV is standardized to expose a ``program_id`` column
    (mouse-hepatocyte inputs carry the program id in ``RowID``); without this
    the uniqueness block below is silently skipped and the report shows no
    program-unique genes. ``top_loading``/``top_unique`` mirror the same
    settings the annotation pipeline uses so the report displays exactly the
    genes that were fed to the LLM.
    """
    panel_stats: dict[int, dict] = {}
    gene_df = pd.DataFrame()
    if gene_loading_csv and os.path.exists(gene_loading_csv):
        gene_df = pd.read_csv(gene_loading_csv)
        if "program_id" not in gene_df.columns:
            try:
                gene_df = standardize_gene_loading(gene_df)
            except Exception:
                pass
        if "UniquenessScore" not in gene_df.columns or gene_df["UniquenessScore"].isna().all():
            gene_df = add_global_uniqueness_scores(gene_df)

    for _, row in summary_df.iterrows():
        topic_id = int(row['Topic'])
        stats = {
            'top_loading': '',
            'unique': '',
            'celltype': 'Not available',
        }

        if 'Top_Genes' in row and not pd.isna(row['Top_Genes']):
            stats['top_loading'] = str(row['Top_Genes']).strip()

        celltype_columns = [
            'Cell_Type_Enrichment',
            'Cell-type enrichment',
            'Celltype',
            'celltype',
        ]
        for column in celltype_columns:
            if column in row and not pd.isna(row[column]):
                stats['celltype'] = str(row[column]).strip()
                break

        if not gene_df.empty and {'Name', 'program_id'}.issubset(gene_df.columns):
            program_genes = gene_df[gene_df['program_id'].astype(int) == topic_id].copy()
            if not program_genes.empty:
                if 'Score' in program_genes.columns:
                    program_genes['_score'] = pd.to_numeric(
                        program_genes['Score'], errors='coerce'
                    )
                    top_loading_df = program_genes.sort_values(
                        '_score', ascending=False
                    )
                else:
                    top_loading_df = program_genes

                # Recompute the top-loading list from the loading CSV (Score
                # order) so it matches the annotation pipeline exactly, rather
                # than trusting summary.csv Top_Genes which may carry a
                # different count.
                top_loading_names = (
                    top_loading_df['Name'].astype(str).head(top_loading).tolist()
                )
                stats['top_loading'] = ', '.join(top_loading_names)

                if 'UniquenessScore' in program_genes.columns:
                    program_genes['_uniqueness'] = pd.to_numeric(
                        program_genes['UniquenessScore'], errors='coerce'
                    )
                    top_unique_df = program_genes.sort_values(
                        '_uniqueness', ascending=False
                    )
                    top_loading_set = set(top_loading_names)
                    unique_names = [
                        gene
                        for gene in top_unique_df['Name'].astype(str).tolist()
                        if gene not in top_loading_set
                    ]
                    stats['unique'] = ', '.join(
                        unique_names[:top_unique]
                    )

        panel_stats[topic_id] = stats

    return panel_stats


def build_pathways_by_program(
    enrichment_filtered_csv: str | None,
    top_n: int = 12,
) -> dict[int, list[dict[str, object]]]:
    """Build per-program pathway lists directly from the STRING enrichment CSV.

    Enrichment was removed from the annotation LLM output, so the report's
    "Top pathway" chip and pathway list are now sourced deterministically from
    ``string_enrichment/enrichment_filtered.csv`` (columns: program_id,
    category, description, fdr, inputGenes). Terms are ranked by FDR ascending
    and capped at ``top_n`` per program.
    """
    pathways_by_program: dict[int, list[dict[str, object]]] = {}
    if not enrichment_filtered_csv or not os.path.exists(enrichment_filtered_csv):
        return pathways_by_program
    df = pd.read_csv(enrichment_filtered_csv)
    if df.empty or "program_id" not in df.columns:
        return pathways_by_program
    df = df.copy()
    df["_fdr"] = pd.to_numeric(df.get("fdr"), errors="coerce")
    for pid, group in df.groupby("program_id"):
        try:
            program_id = int(pid)
        except (TypeError, ValueError):
            continue
        ordered = group.sort_values("_fdr", na_position="last").head(top_n)
        terms: list[dict[str, object]] = []
        for _, row in ordered.iterrows():
            raw_genes = str(row.get("inputGenes", "") or "")
            genes = [g.strip() for g in raw_genes.split("|") if g.strip()]
            fdr = row.get("fdr")
            terms.append(
                {
                    "source": str(row.get("category", "") or "").strip(),
                    "term": str(row.get("description", "") or "").strip(),
                    "fdr": "" if pd.isna(fdr) else str(fdr),
                    "genes": genes,
                }
            )
        pathways_by_program[program_id] = terms
    return pathways_by_program


def build_condition_volcano_by_program(
    volcano_condition_csvs: dict[str, Path],
    regulator_significance_threshold: float = 0.05,
) -> dict[int, dict[str, list[dict]]]:
    """Build per-program, per-condition volcano data with duplicate guides collapsed."""
    volcano_by_program: dict[int, dict[str, list[dict]]] = {}
    fallback_threshold = (
        regulator_significance_threshold
        if regulator_significance_threshold > 0
        else 0.05
    )
    for condition, path in volcano_condition_csvs.items():
        if not path.exists():
            continue
        df = standardize_condition_regulator_results(
            pd.read_csv(path, sep=None, engine="python"),
            condition=condition,
            significance_threshold=regulator_significance_threshold,
        )
        df = collapse_volcano_guides(df)
        pvalue_col = (
            "adj_p_value"
            if "adj_p_value" in df.columns and not df["adj_p_value"].isna().all()
            else "p_value"
        )
        pvalues = pd.to_numeric(df[pvalue_col], errors="coerce")
        fallback_pvalues = pd.Series(
            np.where(df["significant"], fallback_threshold, 1.0),
            index=df.index,
        )
        pvalues = pvalues.fillna(fallback_pvalues).replace(0, 1e-300)
        df["neg_log10_pvalue"] = -np.log10(pvalues)
        df.loc[np.isinf(df["neg_log10_pvalue"]), "neg_log10_pvalue"] = 300

        for tid, group in df.groupby("program_id"):
            condition_map = volcano_by_program.setdefault(int(tid), {})
            group = group.sort_values("target_gene")
            condition_map[condition] = [
                {
                    "g": row["target_gene"],
                    "guide": row["grna_target"],
                    "fc": round(row["log_2_fold_change"], 3),
                    "p": round(row["neg_log10_pvalue"], 2),
                    "s": bool(row["significant"]),
                    "condition": condition,
                }
                for _, row in group.iterrows()
            ]
    return volcano_by_program


def collapse_volcano_guides(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the strongest plotted guide per gene for condition volcano views."""
    if df.empty:
        return df.copy()
    work = df.copy()
    pvalue_col = (
        "adj_p_value"
        if "adj_p_value" in work.columns and not work["adj_p_value"].isna().all()
        else "p_value"
    )
    group_cols = ["program_id", "target_gene"]
    if "condition" in work.columns:
        group_cols.insert(0, "condition")
    work["_abs_lfc"] = pd.to_numeric(
        work["log_2_fold_change"], errors="coerce"
    ).abs()
    work["_volcano_rank_p"] = pd.to_numeric(
        work[pvalue_col], errors="coerce"
    ).fillna(float("inf"))
    sort_cols = group_cols + ["_abs_lfc", "_volcano_rank_p"]
    ascending = [True] * len(group_cols) + [False, True]
    return (
        work.sort_values(sort_cols, ascending=ascending)
        .drop_duplicates(group_cols, keep="first")
        .drop(columns=["_abs_lfc", "_volcano_rank_p"])
    )


def load_presentation(presentation_json: str | None) -> dict[str, dict]:
    """Load the reproducible presentation layer (lead_html, tags, module_short).

    Keyed by string program id. Returns an empty dict if the file is absent so
    the report degrades gracefully to its built-in summary rendering.
    """
    if not presentation_json or not os.path.exists(presentation_json):
        return {}
    try:
        with open(presentation_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data.get("programs", {})


def load_celltype_detail(celltype_file: str | None) -> dict[int, list[dict]]:
    """Load the long-format cell-type enrichment table, keyed by program id.

    Read from the CSV rather than scraped out of the annotation markdown: the model is never
    asked to restate its cell-type evidence, so scraping for it only ever found nothing. Rows
    are sorted by |log2FC| so the strongest association leads, whichever direction it points.
    """
    if not celltype_file or not os.path.exists(celltype_file):
        return {}
    try:
        df = pd.read_csv(celltype_file)
    except (pd.errors.ParserError, OSError, ValueError):
        return {}
    needed = {"program", "cell_type", "direction", "log2_fc"}
    if not needed.issubset(df.columns):
        return {}  # a legacy bucketed summary — no effect sizes to render

    by_program: dict[int, list[dict]] = {}
    for _, row in df.iterrows():
        try:
            program = int(row["program"])
            log2_fc = float(row["log2_fc"])
        except (TypeError, ValueError):
            continue
        by_program.setdefault(program, []).append({
            "cell_type": str(row["cell_type"]).strip(),
            "direction": str(row["direction"]).strip().lower(),
            "log2_fc": log2_fc,
        })
    for rows in by_program.values():
        rows.sort(key=lambda r: abs(r["log2_fc"]), reverse=True)
    return by_program


def _coerce_evidence_note(item: object) -> str:
    """Render a contradiction / evidence-gap entry as a display string.

    Entries may arrive as plain strings or as small dicts (e.g. a ``Claim``-like
    record). We pull the most human-readable field without inventing content;
    unknown shapes are JSON-encoded so nothing is silently dropped.
    """
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("text", "statement", "description", "summary", "note", "claim", "detail"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(item, ensure_ascii=False)
    return str(item)


def _normalize_module_entry(entry: object) -> dict[str, object]:
    """Carry across verbatim ``status``/``dois``/``pmids``/``title`` from a
    *pre-shaped* module (or a bare ``candidate_mechanism``). Non-dict entries and
    unknown shapes yield ``{}`` so nothing downstream breaks."""
    if not isinstance(entry, dict):
        return {}
    norm: dict[str, object] = {}
    status = entry.get("status")
    if isinstance(status, str) and status.strip():
        norm["status"] = status.strip().lower()
    dois = entry.get("dois")
    if isinstance(dois, list):
        norm["dois"] = [str(d).strip() for d in dois if str(d).strip()]
    pmids = entry.get("pmids")
    if isinstance(pmids, list):
        norm["pmids"] = [str(x).strip() for x in pmids if str(x).strip()]
    title = entry.get("title") or entry.get("name") or entry.get("mechanism")
    if isinstance(title, str) and title.strip():
        norm["title"] = title.strip()
    return norm


def _derive_modules_from_research_result(data: dict) -> list[dict[str, object]]:
    """Derive per-module ``{title, status, pmids, dois}`` from a *raw*
    ``ResearchResult`` (``research/schema.py``), whose ``candidate_mechanisms`` +
    ``claims`` + ``evidence`` are NOT pre-joined.

    Reuses the research adapter's mechanism→claims/evidence join
    (``research_evidence_adapter._map_research_result``) so the DOI/status logic
    lives in exactly one place. The adapter emits ``evidence_ids`` as
    ``"PMID:<pmid>"`` / ``"DOI:<doi>"`` strings and a claims-aggregated ``status``;
    here we split those back into ``pmids``/``dois`` lists for rendering.

    Defensive: any import or validation failure yields ``[]`` so an unfamiliar
    shape still renders gracefully.
    """
    try:
        from research.schema import ResearchResult
        from gpi.research_evidence_adapter import _map_research_result
    except Exception:
        return []
    try:
        result = ResearchResult.model_validate(data)
        _, context = _map_research_result(
            result, source_file=Path(f"P{result.program_id}.json")
        )
    except Exception:
        return []

    modules: list[dict[str, object]] = []
    for mod in context.get("modules", []):
        pmids: list[str] = []
        dois: list[str] = []
        for eid in mod.get("evidence_ids", []):
            text = str(eid).strip()
            if text.upper().startswith("PMID:"):
                value = text.split(":", 1)[1].strip()
                if value:
                    pmids.append(value)
            elif text.upper().startswith("DOI:"):
                value = text.split(":", 1)[1].strip()
                if value:
                    dois.append(value)
        norm: dict[str, object] = {}
        title = mod.get("module_name")
        if isinstance(title, str) and title.strip():
            norm["title"] = title.strip()
        status = mod.get("status")
        if isinstance(status, str) and status.strip():
            norm["status"] = status.strip().lower()
        if pmids:
            norm["pmids"] = pmids
        if dois:
            norm["dois"] = dois
        modules.append(norm)
    return modules


def _normalize_research_results(data: dict) -> dict:
    """Normalize a companion ``research_results/{id}.json`` into card fields.

    Defensive and backward-compatible: unknown / missing keys yield empty lists,
    so a program without a research file (or with an unfamiliar shape) renders
    exactly as before. Returns::

        {
          "contradictions": [str, ...],
          "evidence_gaps":  [str, ...],
          "modules":        [{status?, dois?, pmids?, title?}, ...],
        }

    ``modules`` is used to *augment* (never replace) the annotation-derived
    modules: only the ``status``/``dois``/``pmids`` fields are carried across,
    matched positionally (by module rank). Three input shapes are handled:
      * a pre-shaped ``modules`` list -> fields copied verbatim;
      * a *raw* ``ResearchResult`` (has ``candidate_mechanisms`` + ``evidence``,
        with no pre-joined ``modules``) -> ``status``/``pmids``/``dois`` are
        *derived* by reusing the research adapter's join;
      * a bare ``candidate_mechanisms`` list (no evidence) -> title-only, verbatim.
    """
    if not isinstance(data, dict):
        return {"contradictions": [], "evidence_gaps": [], "modules": []}

    contradictions = [
        note
        for item in (data.get("contradictions") or [])
        if (note := _coerce_evidence_note(item))
    ]
    evidence_gaps = [
        note
        for item in (data.get("evidence_gaps") or [])
        if (note := _coerce_evidence_note(item))
    ]

    raw_modules = data.get("modules")
    if isinstance(raw_modules, list):
        modules = [_normalize_module_entry(entry) for entry in raw_modules]
    elif "candidate_mechanisms" in data and "evidence" in data:
        # Raw ResearchResult: mechanisms/claims/evidence are not pre-joined, so
        # derive status + real pmids/dois via the adapter's join.
        modules = _derive_modules_from_research_result(data)
    else:
        candidate = data.get("candidate_mechanisms")
        modules = (
            [_normalize_module_entry(entry) for entry in candidate]
            if isinstance(candidate, list)
            else []
        )

    return {
        "contradictions": contradictions,
        "evidence_gaps": evidence_gaps,
        "modules": modules,
    }


def load_research_results(research_results_dir: str | None, program_id: int) -> dict:
    """Load + normalize ``{research_results_dir}/{program_id}.json`` if present.

    Returns empty (falsy) evidence when the directory or file is absent or
    unreadable, so the report degrades gracefully to its legacy rendering.
    """
    empty = {"contradictions": [], "evidence_gaps": [], "modules": []}
    if not research_results_dir:
        return empty
    base = Path(research_results_dir)
    # The research subsystem writes files as ``P{id}.json``; older callers used
    # ``{id}.json`` or ``program_{id}.json``. Accept the first that exists.
    candidates = [
        base / f"{program_id}.json",
        base / f"P{program_id}.json",
        base / f"program_{program_id}.json",
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        return empty
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return empty
    return _normalize_research_results(data)


def _merge_research_modules(
    parsed_modules: list[dict[str, object]],
    research_modules: list[dict[str, object]],
) -> None:
    """Attach research ``status``/``dois``/``pmids`` onto parsed modules in place.

    Matched positionally by rank (index). Only fields present on the research
    entry are copied; DOIs/PMIDs are merged (union, order-preserving) with any
    already parsed from the annotation markdown. Extra research modules beyond
    the parsed count are ignored (there is nothing to render them against).
    """
    for parsed, research in zip(parsed_modules, research_modules):
        if not isinstance(research, dict) or not research:
            continue
        if research.get("status"):
            parsed["status"] = research["status"]
        for key in ("dois", "pmids"):
            extra = research.get(key)
            if not extra:
                continue
            existing = list(parsed.get(key) or [])
            seen = set(existing)
            for item in extra:
                if item not in seen:
                    seen.add(item)
                    existing.append(item)
            parsed[key] = existing


def generate_report(
    summary_csv: str,
    annotations_dir: str,
    enrichment_dir: str,
    volcano_csv: str | None,
    volcano_condition_csvs: dict[str, Path] | None,
    gene_loading_csv: str,
    output_html: str,
    regulator_significance_threshold: float = 0.05,
    presentation_json: str | None = None,
    dataset_crumb: str = "",
    research_results_dir: str | None = None,
    top_loading: int = 15,
    top_unique: int = 8,
    enrichment_filtered_csv: str | None = None,
    celltype_file: str | None = None,
):
    """Generate the Program Explorer HTML report."""

    # Load data
    summary_df = pd.read_csv(summary_csv)
    panel_stats = build_panel_stats(
        summary_df, gene_loading_csv, top_loading=top_loading, top_unique=top_unique
    )
    pathways_by_program = build_pathways_by_program(enrichment_filtered_csv)
    presentation = load_presentation(presentation_json)
    celltype_by_program = load_celltype_detail(celltype_file)
    volcano_df = None
    if volcano_csv and os.path.exists(volcano_csv):
        # Sniff the separator (sep=None) exactly as --check-inputs, evidence_context, and the
        # per-condition read above all do. A comma-only read here meant a tab-separated
        # single-regulator file passed pre-flight with a ✓ and then silently rendered empty —
        # the one read in the pipeline that did not honor the separator the rest promised.
        volcano_df = standardize_regulator_results(
            pd.read_csv(volcano_csv, sep=None, engine="python"),
            significance_threshold=regulator_significance_threshold,
        )
    
    # Process volcano data by program
    volcano_by_program = {}
    if volcano_df is not None:
        pvalue_col = (
            'adj_p_value'
            if 'adj_p_value' in volcano_df.columns and not volcano_df['adj_p_value'].isna().all()
            else 'p_value'
        )
        pvalues = pd.to_numeric(volcano_df[pvalue_col], errors='coerce')
        fallback_threshold = regulator_significance_threshold if regulator_significance_threshold > 0 else 0.05
        fallback_pvalues = pd.Series(
            np.where(volcano_df['significant'], fallback_threshold, 1.0),
            index=volcano_df.index,
        )
        pvalues = pvalues.fillna(fallback_pvalues).replace(0, 1e-300)
        volcano_df['neg_log10_pvalue'] = -np.log10(pvalues)
        volcano_df.loc[np.isinf(volcano_df['neg_log10_pvalue']), 'neg_log10_pvalue'] = 300
        
        for tid, group in volcano_df.groupby('program_id'):
            volcano_by_program[int(tid)] = [
                {
                    'g': row['grna_target'],
                    'fc': round(row['log_2_fold_change'], 3),
                    'p': round(row['neg_log10_pvalue'], 2),
                    's': bool(row['significant'])
                }
                for _, row in group.iterrows()
            ]
    condition_volcano_by_program = (
        build_condition_volcano_by_program(
            volcano_condition_csvs,
            regulator_significance_threshold=regulator_significance_threshold,
        )
        if volcano_condition_csvs
        else {}
    )
    
    # Build per-program data
    programs_data = []
    for _, row in summary_df.iterrows():
        topic_id = int(row['Topic'])
        topic_name = row['Name']
        
        # Read annotation
        ann_path = Path(annotations_dir) / f"topic_{topic_id}_annotation.md"
        annotation_md = ann_path.read_text(encoding='utf-8') if ann_path.exists() else ""
        
        # Extract stats
        stats = extract_program_stats(annotation_md)
        fallback_stats = panel_stats.get(topic_id, {})
        annotation_body_md, pathway_enrichment_md = split_pathway_enrichment(annotation_md)
        annotation_body_md, final_modules = split_final_modules(annotation_body_md)
        annotation_body_md = clean_report_annotation_body(annotation_body_md)

        # Optional evidence status / DOIs / contradictions / gaps (spec §10).
        # Absent research file => empty evidence => legacy rendering unchanged.
        research = load_research_results(research_results_dir, topic_id)
        _merge_research_modules(final_modules, research.get("modules", []))
        
        # Enrichment paths
        enr_rel = os.path.relpath(enrichment_dir, os.path.dirname(output_html))

        # Reproducible presentation fields (generated by step 6); fall back to
        # the plain brief summary / module titles when absent.
        pres = presentation.get(str(topic_id), {})

        top_loading_str = stats.get('top_loading') or fallback_stats.get('top_loading', '')
        unique_str = stats.get('unique') or fallback_stats.get('unique', '')

        programs_data.append({
            'id': topic_id,
            'name': topic_name,
            'label': stats.get('label', topic_name),
            'summary': stats.get('summary', ''),
            'lead_html': pres.get('lead_html', ''),
            'tags': pres.get('tags', []),
            'module_short': pres.get('module_short', []),
            'presentation_source': pres.get('source', ''),
            'top_loading': _split_csv_values(top_loading_str),
            'unique': _split_csv_values(unique_str),
            'celltype': stats.get('celltype') or fallback_stats.get('celltype', ''),
            'celltype_detail': celltype_by_program.get(topic_id, []),
            'modules': final_modules,
            'contradictions': research.get('contradictions', []),
            'evidence_gaps': research.get('evidence_gaps', []),
            'distinctive': extract_distinctive(annotation_md),
            'regulators': parse_regulators_detailed(annotation_md),
            'pathways': pathways_by_program.get(topic_id) or parse_pathways(annotation_md),
            'annotation_text': annotation_md,  # For full-text search
            'kegg_fig': f"{enr_rel}/program_{topic_id}_kegg_enrichment.png",
            'process_fig': f"{enr_rel}/program_{topic_id}_process_enrichment.png",
            'volcano': volcano_by_program.get(topic_id, []),
            'condition_volcano': condition_volcano_by_program.get(topic_id, {}),
        })
    
    generated_on = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    # Generate HTML
    html = generate_design_a_html(
        programs_data, len(programs_data), generated_on, dataset_crumb=dataset_crumb
    )
    
    Path(output_html).parent.mkdir(parents=True, exist_ok=True)
    with open(output_html, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"✓ Program Explorer report: {output_html}")
    print(f"  - {len(programs_data)} programs")


def generate_design_a_html(programs_data, num_programs, generated_on, dataset_crumb=""):
    """Generate the redesigned Program Explorer detail report.

    Ports the polished ``Program Detail`` mockup: a left program rail, a hero
    (kicker + label + highlighted lead + context chips), an at-a-glance strip
    (top regulators / functional modules / top pathway), and collapsible
    sections for marker genes, functional modules, distinctive features,
    regulators, pathway enrichment, and per-condition volcano plots.

    Data is injected via placeholder substitution so the static CSS/JS template
    needs no brace escaping.
    """

    def _js_safe(blob: str) -> str:
        # Prevent embedded "</script>" or "<!--" from terminating the inline
        # <script> early; keeps arbitrary annotation text safe.
        return blob.replace("</", "<\\/").replace("<!--", "<\\!--")

    programs_json = _js_safe(
        json.dumps({p["id"]: p for p in programs_data}, ensure_ascii=False)
    )
    priority_json = json.dumps(PRIORITY_GENES, ensure_ascii=False)
    program_list_json = _js_safe(
        json.dumps([[p["id"], p["name"]] for p in programs_data], ensure_ascii=False)
    )
    crumb_json = json.dumps(dataset_crumb or "", ensure_ascii=False)
    sub_text = (
        (dataset_crumb or "Program Explorer")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )

    template = r'''<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gene Program Annotations — Program Explorer</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        :root {
            --bg: #f6f7f8;
            --surface: #ffffff;
            --surface-soft: #f3f5f5;
            --surface-sunk: #eef1f1;
            --text: #14201d;
            --text-soft: #43504c;
            --muted: #6b7672;
            --border: #e4e7e6;
            --border-strong: #d2d7d5;
            --shadow-soft: 0 1px 2px rgba(16,32,28,.05);
            --accent: #0d9488;
            --accent-strong: #0f766e;
            --accent-soft: #e6f6f3;
            --accent-text: #0b6b61;
            --up: #b8615a;
            --up-soft: #f4ebe9;
            --down: #3d7d9e;
            --down-soft: #e8eff2;
            /* Evidence status palette (spec 10): supported / partial /
               contradictory / missing (grey). */
            --ok: #15803d;
            --ok-soft: #e7f6ec;
            --warn: #b45309;
            --warn-soft: #fbf1e3;
            --bad: #b91c1c;
            --bad-soft: #fbeaea;
            --gap: #6b7672;
            --gap-soft: #eef1f1;
            --radius: 12px;
            --radius-sm: 8px;
            --maxw: 1180px;
        }
        html[data-theme="dark"] {
            --bg: #0c0f0e;
            --surface: #141917;
            --surface-soft: #1a201e;
            --surface-sunk: #0f1413;
            --text: #e8ecea;
            --text-soft: #b7c0bc;
            --muted: #8a938f;
            --border: #262d2a;
            --border-strong: #333b37;
            --shadow-soft: none;
            --accent-soft: #0e2a27;
            --accent-text: #5eead4;
            --up: #d98e86;
            --up-soft: #271815;
            --down: #7fb0c9;
            --down-soft: #13222a;
            --ok: #4ade80;
            --ok-soft: #10281a;
            --warn: #fbbf24;
            --warn-soft: #2a2110;
            --bad: #f87171;
            --bad-soft: #2a1414;
            --gap: #8a938f;
            --gap-soft: #1a201e;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        html { scroll-behavior: smooth; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
            background: var(--bg); color: var(--text); line-height: 1.6; font-size: 15px;
            -webkit-font-smoothing: antialiased;
        }
        a { color: var(--accent-text); }

        .topbar {
            position: sticky; top: 0; z-index: 60;
            display: flex; align-items: center; gap: 14px; padding: 10px 20px;
            background: color-mix(in srgb, var(--surface) 88%, transparent);
            backdrop-filter: saturate(1.4) blur(8px); border-bottom: 1px solid var(--border);
        }
        .brand { display: flex; align-items: baseline; gap: 8px; font-weight: 700; letter-spacing: -.01em; }
        .brand .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--accent); display: inline-block; }
        .brand .sub { font-weight: 500; font-size: 12px; color: var(--muted); }
        .topbar .spacer { flex: 1; }
        .search-input {
            font: inherit; font-size: 12.5px; padding: 6px 11px; border-radius: 8px;
            border: 1px solid var(--border); background: var(--surface); color: var(--text);
            width: min(260px, 38vw);
        }
        .search-input:focus { outline: none; border-color: var(--accent); }
        .metacount { font-size: 11.5px; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
        .iconbtn { font: inherit; font-size: 12px; font-weight: 600; padding: 6px 12px; border-radius: 8px;
            border: 1px solid var(--border); background: var(--surface); color: var(--text); cursor: pointer; }
        .iconbtn:hover { border-color: var(--border-strong); }

        .shell { display: grid; grid-template-columns: 250px minmax(0, 1fr); }
        .rail { position: sticky; top: 53px; align-self: start; height: calc(100vh - 53px); overflow-y: auto;
            border-right: 1px solid var(--border); padding: 18px 12px 32px 18px; background: var(--bg); }
        .rail h2 { font-size: 10.5px; text-transform: uppercase; letter-spacing: .1em; color: var(--muted); margin: 4px 6px 10px; }
        .rail a { display: block; padding: 7px 10px; border-radius: 8px; text-decoration: none; color: var(--text-soft);
            font-size: 12.5px; margin-bottom: 2px; line-height: 1.35; cursor: pointer; }
        .rail a .num { color: var(--muted); font-variant-numeric: tabular-nums; margin-right: 8px; font-weight: 700; }
        .rail a:hover { background: var(--surface-soft); color: var(--text); }
        .rail a.active { background: var(--accent-soft); color: var(--accent-text); font-weight: 650; }
        .rail a.active .num { color: var(--accent-text); }

        .canvas { padding: 28px clamp(18px, 3vw, 40px) 80px; }
        .wrap { max-width: var(--maxw); margin: 0 auto; }

        .hero { margin-bottom: 22px; }
        .kicker { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 11.5px; font-weight: 700;
            letter-spacing: .07em; text-transform: uppercase; color: var(--accent-text); margin-bottom: 12px; }
        .kicker .pid { background: var(--accent); color: #fff; padding: 2px 9px; border-radius: 999px; letter-spacing: .02em; }
        .kicker .crumb { color: var(--muted); font-weight: 600; }
        h1.title { font-size: clamp(26px, 3.3vw, 37px); line-height: 1.13; letter-spacing: -.018em; font-weight: 760;
            text-wrap: balance; margin-bottom: 14px; }
        .lead { font-size: 16.5px; line-height: 1.62; color: var(--text-soft); max-width: none; text-wrap: pretty; }
        .lead b { color: var(--text); font-weight: 680; }
        .ctx { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
        .ctx .chip { font-size: 12.5px; font-weight: 600; padding: 5px 11px; border-radius: 999px;
            background: var(--accent-soft); color: var(--accent-text); }

        .glance { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: var(--border);
            border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; margin: 24px 0 30px; box-shadow: var(--shadow-soft); }
        .glance .cell { background: var(--surface); padding: 14px 16px; }
        .glance .cell .k { font-size: 10.5px; text-transform: uppercase; letter-spacing: .07em; color: var(--muted); font-weight: 700; margin-bottom: 7px; }
        .glance .cell .v { font-size: 15px; font-weight: 650; line-height: 1.35; }
        .glance .cell .v small { display: block; font-weight: 500; color: var(--muted); font-size: 12px; margin-top: 3px; }
        .reglist { display: flex; flex-wrap: wrap; gap: 4px 10px; }
        .reglist .rg { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 14px; font-weight: 700; white-space: nowrap; }
        .modlist { display: flex; flex-direction: column; gap: 3px; }
        .modlist span { font-size: 13px; font-weight: 600; color: var(--text-soft); display: flex; gap: 7px; }
        .modlist span b { color: var(--accent-text); font-weight: 800; }

        .genes { display: flex; flex-wrap: wrap; gap: 5px; }
        .gene { font-size: 11.5px; font-weight: 550; font-family: ui-monospace, "SF Mono", Menlo, monospace; letter-spacing: -.01em;
            padding: 1.5px 6px; border-radius: 5px; background: var(--surface-soft); border: 1px solid var(--border); color: var(--text-soft); }
        .gene.uniq { background: var(--accent-soft); border-color: transparent; color: var(--accent-text); font-weight: 600; }

        .section { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
            margin-bottom: 16px; box-shadow: var(--shadow-soft); overflow: hidden; }
        .section > .head { display: flex; align-items: center; gap: 12px; width: 100%; text-align: left;
            padding: 16px 20px; border: 0; background: transparent; color: inherit; font: inherit; cursor: pointer; }
        .section > .head .htitle { font-size: 16.5px; font-weight: 700; letter-spacing: -.01em; }
        .section > .head .hmeta { font-size: 12.5px; color: var(--muted); font-weight: 500; }
        .section > .head .chev { margin-left: auto; color: var(--muted); transition: transform .18s ease; }
        .section.open > .head .chev { transform: rotate(90deg); }
        .section > .body { padding: 0 20px 20px; }
        .section.collapsed > .body { display: none; }
        .kkey { font-weight: 700; }
        .kkey.rep { color: var(--up); }
        .kkey.act { color: var(--down); }

        .modules { display: grid; gap: 14px; }
        .mod { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 16px 16px 6px; background: var(--surface); }
        .mod .mhead { display: flex; gap: 10px; align-items: baseline; margin-bottom: 8px; }
        .evbadge { margin-left: auto; flex: none; font-size: 10px; font-weight: 700; text-transform: uppercase;
                   letter-spacing: .04em; padding: 3px 8px; border-radius: 999px; cursor: help; white-space: nowrap; }
        .ev-partial { color: var(--warn); background: var(--warn-soft); border: 1px solid var(--warn); }
        .ev-unsupported { color: var(--bad); background: var(--bad-soft); border: 1px solid var(--bad); }
        .cttable { display: flex; flex-direction: column; gap: 4px; }
        .ctrow { display: flex; align-items: baseline; gap: 10px; padding: 7px 11px;
                 border-radius: var(--radius-sm); font-size: 13px; }
        .ctrow.ct-enr { background: var(--ok-soft); }
        .ctrow.ct-dep { background: var(--bad-soft); }
        .ctname { flex: 1; font-weight: 600; }
        .ctfc { flex: none; font-variant-numeric: tabular-nums; font-weight: 700; }
        .ct-enr .ctfc { color: var(--ok); }
        .ct-dep .ctfc { color: var(--bad); }
        .ctnote { margin-top: 10px; font-size: 11px; line-height: 1.5; color: var(--muted); }
        .mod .mnum { flex: none; width: 24px; height: 24px; border-radius: 7px; background: var(--accent-soft);
            color: var(--accent-text); font-size: 12px; font-weight: 800; display: grid; place-items: center; }
        .mod h3 { font-size: 15.5px; font-weight: 700; line-height: 1.3; }
        .mod .msum { color: var(--text-soft); font-size: 14px; margin: 6px 0 12px; }
        details { margin: 8px 0; }
        details > summary { cursor: pointer; list-style: none; font-size: 12.5px; font-weight: 650; color: var(--accent-text);
            display: inline-flex; align-items: center; gap: 6px; padding: 4px 0; }
        details > summary::-webkit-details-marker { display: none; }
        details > summary::before { content: "\25b8"; font-size: 10px; transition: transform .15s; }
        details[open] > summary::before { transform: rotate(90deg); }
        .disc { padding: 8px 0 4px; border-top: 1px dashed var(--border); margin-top: 6px; }
        .disc .field { margin-bottom: 12px; }
        .disc .field .flabel { font-size: 10.5px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); font-weight: 700; margin-bottom: 4px; }
        .disc .field p { font-size: 13.5px; color: var(--text-soft); line-height: 1.6; }
        .pmidrow { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; margin-top: 10px; }
        .pmidlabel { font-size: 10px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 700; flex: none; }
        .pmids { display: flex; flex-wrap: wrap; gap: 5px; }
        .pmids a { font-size: 11px; font-weight: 600; font-variant-numeric: tabular-nums; text-decoration: none;
            padding: 1.5px 7px; border-radius: 5px; background: var(--surface-soft); border: 1px solid var(--border); color: var(--accent-text); }
        .pmids a:hover { border-color: var(--accent); }
        /* DOI links: mirror the PMID styling (spec 10). */
        .doirow { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; margin-top: 6px; }
        .doi { display: flex; flex-wrap: wrap; gap: 5px; }
        .doi a { font-size: 11px; font-weight: 600; text-decoration: none; word-break: break-all;
            padding: 1.5px 7px; border-radius: 5px; background: var(--surface-soft); border: 1px solid var(--border); color: var(--accent-text); }
        .doi a:hover { border-color: var(--accent); }

        .distinctive p { font-size: 15px; color: var(--text-soft); line-height: 1.7; }
        .distinctive p i { color: var(--text); font-style: italic; }

        .reg { border: 1px solid var(--border); border-radius: var(--radius-sm); margin-bottom: 7px; }
        .reg .rhead { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 9px 14px; }
        .reg .rgene { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 15px; font-weight: 700; }
        .reg .rgene.rep { color: var(--up); }
        .reg .rgene.act { color: var(--down); }
        .reg .role { font-size: 11.5px; font-weight: 600; }
        .reg .role.rep { color: var(--up); }
        .reg .role.act { color: var(--down); }
        .conf { font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--border-strong); color: var(--muted); }
        .conf.high { border-color: var(--accent); color: var(--accent-text); }
        .reg .fc { margin-left: auto; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
        .reg .fc b { color: var(--text-soft); }
        .reg .rbody { padding: 0 14px 10px; }
        .reg .rbody details { margin: 0; }
        .reg .rbody p { font-size: 13.5px; color: var(--text-soft); line-height: 1.6; margin-top: 6px; }

        .pwsort { display: flex; gap: 8px; align-items: center; font-size: 11.5px; color: var(--muted); margin: 18px 0 8px; font-weight: 600; }
        .pw { padding: 8px 0; border-bottom: 1px solid var(--border); }
        .pw:last-child { border-bottom: 0; }
        .pw .pwtop { display: flex; align-items: baseline; gap: 9px; margin-bottom: 6px; }
        .pw .src { font-size: 9.5px; font-weight: 800; letter-spacing: .04em; padding: 1px 5px; border-radius: 4px;
            background: var(--surface-sunk); color: var(--muted); text-transform: uppercase; white-space: nowrap; }
        .pw .pwname { font-size: 13.5px; font-weight: 650; }
        .pw .pwfdr { margin-left: auto; font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
        .pw .track { height: 6px; border-radius: 999px; background: var(--surface-sunk); overflow: hidden; margin-bottom: 6px; }
        .pw .fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--accent), var(--accent-strong)); }
        .pw .members { font-size: 11px; color: var(--muted); line-height: 1.5; font-family: ui-monospace, "SF Mono", Menlo, monospace; }

        .figgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 4px; }
        .figbox { border: 1px solid var(--border); border-radius: var(--radius-sm); overflow: hidden; background: var(--surface); }
        .figbox .figcap { display: flex; align-items: baseline; gap: 6px; font-size: 12px; font-weight: 600; padding: 9px 12px;
            background: var(--surface-soft); border-bottom: 1px solid var(--border); color: var(--text-soft); }
        .figbox .figopen { margin-left: auto; font-size: 11px; font-weight: 500; color: var(--accent-text); }
        .figbox a { display: block; }
        .figbox img { width: 100%; height: auto; display: block; cursor: zoom-in; }
        .figbox .figmiss { padding: 36px 16px; text-align: center; color: var(--muted); font-size: 12px; }
        .volgrid { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr); gap: 12px; }
        .volcard { min-width: 0; border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 8px 4px; }
        .volcard .vtitle { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 700; padding: 6px 6px 2px; text-transform: capitalize; }
        .volcard .vtitle .dotc { font-size: 11px; font-weight: 600; color: var(--muted); margin-left: auto; }
        .volplot { width: 100%; height: 340px; min-width: 0; }
        .note { font-size: 11.5px; color: var(--muted); margin-top: 10px; }

        @media (max-width: 880px) {
            .shell { grid-template-columns: 1fr; }
            .rail { display: none; }
            .glance { grid-template-columns: 1fr; }
            .figgrid, .volgrid { grid-template-columns: 1fr; }
            .topbar { flex-wrap: wrap; }
            .search-input { width: 100%; }
        }
    </style>
</head>
<body>
    <header class="topbar">
        <div class="brand"><span class="dot"></span>Gene Programs<span class="sub">__DATASET_SUB__</span></div>
        <input type="text" class="search-input" id="search" placeholder="Search programs, genes, pathways&hellip;" oninput="filterRail()">
        <div class="spacer"></div>
        <span class="metacount">__NUM_PROGRAMS__ programs &middot; __GENERATED_ON__</span>
        <button class="iconbtn" onclick="prev()" title="Previous program">&larr;</button>
        <button class="iconbtn" onclick="next()" title="Next program">&rarr;</button>
        <button class="iconbtn" id="themebtn" onclick="toggleTheme()">Dark</button>
    </header>

    <div class="shell">
        <aside class="rail">
            <h2>__NUM_PROGRAMS__ programs</h2>
            <nav id="rail"></nav>
        </aside>
        <main class="canvas"><div class="wrap" id="main"></div></main>
    </div>

    <script>
    window.PROGRAMS = __PROGRAMS_JSON__;
    window.PRIORITY_GENES = __PRIORITY_JSON__;
    var PROGRAM_LIST = __PROGRAM_LIST_JSON__;
    var DATASET_CRUMB = __DATASET_CRUMB_JSON__;
    </script>
    <script>
    const esc = s => String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    const mdItalic = s => esc(s).replace(/\*(.+?)\*/g, "<i>$1</i>");
    const roleCls = r => r === "activator" ? "act" : "rep";
    const negLog = fdr => { const v = -Math.log10(parseFloat(fdr)); return isFinite(v) ? v : 0; };
    const cid = c => String(c).replace(/[^a-z0-9]/gi, "_");
    // Unify the two ways perturbation data reaches a program: condition-keyed runs populate
    // `condition_volcano` (one panel per condition); single-regulator runs populate `volcano`
    // (one unnamed panel). The renderer only ever draws condition panels, so a single-regulator
    // program used to show "No perturbation data" on top of real, parsed points. Fold `volcano`
    // into a lone "perturbation" panel when there are no explicit conditions.
    const condVolcanoOf = p => {
        const cv = p.condition_volcano || {};
        if (Object.keys(cv).length) return cv;
        return (p.volcano && p.volcano.length) ? {"perturbation": p.volcano} : {};
    };
    const asArray = v => Array.isArray(v) ? v : (v ? String(v).split(/,\s*/).filter(Boolean) : []);
    const cap = s => String(s).charAt(0).toUpperCase() + String(s).slice(1);

    // Split a module's evidence identifiers into PMIDs and DOIs. Accepts legacy
    // m.pmids, new m.dois, and prefixed entries ("PMID:123" / "DOI:10.x/y")
    // mixed into either list (or an optional m.evidence_ids list).
    function evidenceIds(m){
        const pmids = [], dois = [];
        const push = raw => {
            let s = String(raw==null?"":raw).trim();
            if(!s) return;
            const low = s.toLowerCase();
            if(low.startsWith("doi:")){ const d = s.slice(4).trim(); if(d) dois.push(d); return; }
            if(low.startsWith("pmid:")){ const p = s.slice(5).trim(); if(p) pmids.push(p); return; }
            if(/^https?:\/\/(?:dx\.)?doi\.org\//i.test(s)){ dois.push(s.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i,"")); return; }
            if(/^10\.\d{4,9}\//.test(s)){ dois.push(s); return; }
            if(/^\d{6,9}$/.test(s)){ pmids.push(s); return; }
        };
        (m.pmids||[]).forEach(push);
        (m.dois||[]).forEach(push);
        (m.evidence_ids||[]).forEach(push);
        return { pmids: [...new Set(pmids)], dois: [...new Set(dois)] };
    }

    // Evidence badge. Only the exceptional states get chrome — a verified module is the norm
    // and a badge on every one would be wallpaper. The two states are NOT the same thing, and
    // collapsing them would be its own dishonesty: 'partial' means the verifier could not
    // reach the source, NOT that the citation is fake.
    const EV_BADGE = {
        partial: ["unverified",
            "Citations could not be checked — the verifier could not reach PubMed/Crossref "
            + "(network, rate limit). They are shown as the model gave them. Confirm before citing."],
        unsupported: ["no verified evidence",
            "No citation for this module survived verification. Treat the mechanism as a "
            + "hypothesis, not a cited finding."],
    };
    function evBadge(status){
        const hit = EV_BADGE[String(status==null?"":status).toLowerCase()];
        if(!hit) return "";
        return `<span class="evbadge ev-${esc(String(status).toLowerCase())}" title="${esc(hit[1])}">${esc(hit[0])}</span>`;
    }

    // Cell-type enrichment: signed log2FC per lineage, strongest |effect| first. Depletion is
    // rendered as prominently as enrichment — a program actively excluded from a lineage says
    // as much as one that marks it, and the bucketed format this replaces hid the magnitudes.
    function celltypeRow(c){
        const dep = String(c.direction||"").toLowerCase() === "depleted";
        const n = Number(c.log2_fc);
        const v = (n >= 0 ? "+" : "") + (isFinite(n) ? n.toFixed(2) : "?");
        return `<div class="ctrow ${dep?"ct-dep":"ct-enr"}">
            <span class="ctname">${esc(c.cell_type)}</span><span class="ctfc">${esc(v)}</span></div>`;
    }
    function fmtFDR(fdr){
        const parts = String(fdr).split(/e-?/i);
        if(parts.length < 2) return "FDR " + fdr;
        const sup = String(parts[1]).replace(/[0-9]/g, d => "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"[+d]);
        return `FDR ${parts[0]}\u00d710\u207b${sup}`;
    }

    const IDS = PROGRAM_LIST.map(x => x[0]);
    let currentId = null;

    // Build a lowercased full-text search index per program.
    const SEARCH_INDEX = {};
    IDS.forEach(id => {
        const p = PROGRAMS[id] || {};
        const parts = [
            p.name, p.label, p.summary, p.lead_html, (p.tags||[]).join(" "),
            asArray(p.top_loading).join(" "), asArray(p.unique).join(" "),
            ...(p.modules||[]).flatMap(m => [m.title, m.summary, (m.key_genes||[]).join(" "), (m.pmids||[]).join(" "), (m.dois||[]).join(" "), m.status, m.evidence, m.mechanism]),
            ...(p.regulators||[]).flatMap(r => [r.gene, r.role, r.mechanism]),
            ...(p.pathways||[]).flatMap(pw => [pw.term, pw.source, (pw.genes||[]).join(" ")]),
            (p.contradictions||[]).join(" "), (p.evidence_gaps||[]).join(" "),
            p.distinctive, p.annotation_text
        ];
        SEARCH_INDEX[id] = parts.filter(Boolean).join(" ").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").toLowerCase();
    });

    function resolvePres(p){
        const tags = (p.tags && p.tags.length) ? p.tags : [];
        const moduleShort = (p.module_short && p.module_short.length)
            ? p.module_short : (p.modules||[]).map(m => m.title);
        const leadHtml = p.lead_html || esc(p.summary || "");
        return { leadHtml, tags, moduleShort };
    }

    function buildRail(){
        document.getElementById("rail").innerHTML = PROGRAM_LIST.map(([n,t]) =>
            `<a class="${n===currentId?"active":""}" data-id="${n}" onclick="render(${n});return false;"><span class="num">${n}</span>${esc(t)}</a>`
        ).join("");
        filterRail();
    }

    function filterRail(){
        const q = (document.getElementById("search").value || "").toLowerCase().trim();
        document.querySelectorAll(".rail a").forEach(a => {
            const hay = SEARCH_INDEX[a.dataset.id] || "";
            a.style.display = (!q || hay.includes(q)) ? "" : "none";
        });
    }

    function render(id){
        id = +id;
        const p = PROGRAMS[id];
        if(!p) return;
        const pres = resolvePres(p);
        currentId = id;
        document.querySelectorAll(".rail a").forEach(a => a.classList.toggle("active", +a.dataset.id === id));
        history.replaceState(null, "", "#program-" + id);
        window.scrollTo(0,0);

        const topGenes = asArray(p.top_loading).slice(0,15);
        const uniq = asArray(p.unique).slice(0,8);
        const modules = p.modules || [];
        const regs = p.regulators || [];
        const pathways = p.pathways || [];
        const sortedPw = [...pathways].sort((a,b) => parseFloat(a.fdr) - parseFloat(b.fdr));
        const topPw = sortedPw[0];
        const maxNl = sortedPw.length ? Math.max(...sortedPw.map(x => negLog(x.fdr))) : 1;
        const conds = Object.keys(condVolcanoOf(p)).sort();

        const regGlance = regs.length
            ? regs.map(r => `<span class="rg" style="color:var(--${r.role==="activator"?"down":"up"})">${esc(r.gene)}</span>`).join("")
            : `<span class="rg" style="color:var(--muted)">\u2014</span>`;
        const modGlance = pres.moduleShort.map((m,i) => `<span><b>${i+1}</b>${esc(m)}</span>`).join("");
        const crumbHtml = DATASET_CRUMB ? `<span class="crumb">${esc(DATASET_CRUMB)}</span>` : "";

        const volCards = conds.length
            ? conds.map(c => `<div class="volcard"><div class="vtitle">${esc(c)}<span class="dotc" id="vc-${cid(c)}"></span></div><div id="vol-${cid(c)}" class="volplot"></div></div>`).join("")
            : `<p class="note">No perturbation data available.</p>`;

        document.getElementById("main").innerHTML = `
        <header class="hero">
            <div class="kicker"><span class="pid">Program ${id}</span>${crumbHtml}</div>
            <h1 class="title">${esc(p.label || p.name || ("Program " + id))}</h1>
            <p class="lead">${pres.leadHtml}</p>
            ${pres.tags.length ? `<div class="ctx">${pres.tags.map(c => `<span class="chip">${esc(c)}</span>`).join("")}</div>` : ""}
        </header>

        <section class="glance" aria-label="At a glance">
            <div class="cell">
                <div class="k">Top regulators</div>
                <div class="v"><div class="reglist">${regGlance}</div>
                    <small><span class="kkey rep">repressor</span> &middot; <span class="kkey act">activator</span></small></div>
            </div>
            <div class="cell">
                <div class="k">Functional modules</div>
                <div class="v"><div class="modlist">${modGlance || "<span style=\"color:var(--muted)\">\u2014</span>"}</div></div>
            </div>
            <div class="cell">
                <div class="k">Top pathway</div>
                <div class="v">${topPw ? `${esc(topPw.term)}<small>${fmtFDR(topPw.fdr)}</small>` : `<span style="color:var(--muted)">No enrichment</span>`}</div>
            </div>
        </section>

        <section class="section open" id="sec-genes">
            <button class="head" onclick="toggleSection('sec-genes')">
                <span class="htitle">Marker genes</span>
                <span class="hmeta">${topGenes.length} top-loading &middot; ${uniq.length} program-unique (highlighted)</span>
                <span class="chev">\u203a</span></button>
            <div class="body"><div class="genes">
                ${topGenes.map(g => `<span class="gene">${esc(g)}</span>`).join("")}
                ${uniq.map(g => `<span class="gene uniq">${esc(g)}</span>`).join("")}
            </div></div>
        </section>

        ${(p.celltype_detail||[]).length ? `<section class="section open" id="sec-celltype">
            <button class="head" onclick="toggleSection('sec-celltype')">
                <span class="htitle">Cell-type enrichment</span>
                <span class="hmeta">log₂FC of program score, cells of that type vs. all others</span>
                <span class="chev">›</span></button>
            <div class="body">
                <div class="cttable">${p.celltype_detail.map(celltypeRow).join("")}</div>
                <p class="ctnote">Only cell types for which this program ranked in the top 10 markers are
                    listed. A type that is absent was not tested into that top 10 — which is not
                    evidence of absence.</p>
            </div>
        </section>` : ""}

        <section class="section open" id="sec-modules">
            <button class="head" onclick="toggleSection('sec-modules')">
                <span class="htitle">Functional modules</span>
                <span class="hmeta">${modules.length} mechanistic module${modules.length===1?"":"s"}</span>
                <span class="chev">\u203a</span></button>
            <div class="body"><div class="modules">${modules.map((m,i) => {
                const ev = evidenceIds(m);
                return `
                <article class="mod">
                    <div class="mhead"><span class="mnum">${i+1}</span><h3>${esc(m.title)}</h3>${evBadge(m.status)}</div>
                    ${m.summary ? `<p class="msum">${esc(m.summary)}</p>` : ""}
                    ${(m.key_genes||[]).length ? `<div class="genes">${m.key_genes.map(g => `<span class="gene">${esc(g)}</span>`).join("")}</div>` : ""}
                    ${ev.pmids.length ? `<div class="pmidrow"><span class="pmidlabel">PMID</span><div class="pmids">${ev.pmids.map(x => `<a href="https://pubmed.ncbi.nlm.nih.gov/${esc(x)}/" target="_blank" rel="noopener">${esc(x)}</a>`).join("")}</div></div>` : ""}
                    ${m.evidence ? `<details><summary>Evidence used</summary><div class="disc">
                        <div class="field"><p>${esc(m.evidence)}</p></div>
                    </div></details>` : ""}
                </article>`;}).join("")}</div></div>
        </section>

        ${p.distinctive ? `<section class="section open distinctive" id="sec-distinct">
            <button class="head" onclick="toggleSection('sec-distinct')">
                <span class="htitle">Distinctive features</span>
                <span class="hmeta">what sets this program apart</span>
                <span class="chev">\u203a</span></button>
            <div class="body"><p>${mdItalic(p.distinctive)}</p></div>
        </section>` : ""}

        <section class="section open" id="sec-regs">
            <button class="head" onclick="toggleSection('sec-regs')">
                <span class="htitle">Top regulators</span>
                <span class="hmeta">top perturbations that move this program &middot; <span class="kkey rep">repressor</span> / <span class="kkey act">activator</span></span>
                <span class="chev">\u203a</span></button>
            <div class="body">${regs.length ? regs.map(r => `
                <div class="reg">
                    <div class="rhead">
                        <span class="rgene ${roleCls(r.role)}">${esc(r.gene)}</span>
                        <span class="role ${roleCls(r.role)}">${esc(r.role)}</span>
                        ${(r.confidence && r.confidence!=="\u2014") ? `<span class="conf ${String(r.confidence).toLowerCase()}">${esc(r.confidence)} confidence</span>` : ""}
                        ${r.fc ? `<span class="fc">log\u2082FC <b>${esc(r.fc)}</b></span>` : ""}
                    </div>
                    ${r.mechanism ? `<div class="rbody"><details><summary>Mechanistic hypothesis</summary><p>${esc(r.mechanism)}</p></details></div>` : ""}
                </div>`).join("") : `<p class="note">No regulator hits reported for this program.</p>`}</div>
        </section>

        <section class="section collapsed" id="sec-pw">
            <button class="head" onclick="toggleSection('sec-pw')">
                <span class="htitle">Pathway enrichment</span>
                <span class="hmeta">ranked by \u2212log\u2081\u2080(FDR)</span>
                <span class="chev">\u203a</span></button>
            <div class="body">
                <div class="figgrid">
                    <div class="figbox"><div class="figcap">KEGG pathway enrichment<span class="figopen">open full size \u2197</span></div><a href="${esc(p.kegg_fig)}" target="_blank" rel="noopener"><img src="${esc(p.kegg_fig)}" alt="KEGG enrichment" onerror="this.parentNode.outerHTML='<div class=figmiss>Figure not found</div>'"></a></div>
                    <div class="figbox"><div class="figcap">Biological process enrichment<span class="figopen">open full size \u2197</span></div><a href="${esc(p.process_fig)}" target="_blank" rel="noopener"><img src="${esc(p.process_fig)}" alt="Process enrichment" onerror="this.parentNode.outerHTML='<div class=figmiss>Figure not found</div>'"></a></div>
                </div>
                ${sortedPw.length ? `<div class="pwsort">Enriched terms (annotation) \u00b7 bar length = \u2212log\u2081\u2080(FDR)</div>
                ${sortedPw.map(pw => `
                    <div class="pw">
                        <div class="pwtop"><span class="src">${esc(pw.source)}</span><span class="pwname">${esc(pw.term)}</span><span class="pwfdr">${fmtFDR(pw.fdr)}</span></div>
                        <div class="track"><div class="fill" style="width:${(negLog(pw.fdr)/maxNl*100).toFixed(1)}%"></div></div>
                        <div class="members">${(pw.genes||[]).map(esc).join(", ")}</div>
                    </div>`).join("")}` : ""}
            </div>
        </section>

        <section class="section collapsed" id="sec-volcano">
            <button class="head" onclick="toggleSection('sec-volcano')">
                <span class="htitle">Perturbation effects</span>
                <span class="hmeta">regulator screen &middot; log\u2082FC vs significance</span>
                <span class="chev">\u203a</span></button>
            <div class="body">
                <div class="volgrid">${volCards}</div>
                <p class="note">Red = positive log\u2082FC (program up on knockdown), blue = negative. Top hits and known regulators are labelled.</p>
            </div>
        </section>`;

        document.querySelector(".rail a.active") && document.querySelector(".rail a.active").scrollIntoView({block:"nearest"});
        if(document.getElementById("sec-volcano").classList.contains("open")) drawVolcanoes(p);
    }

    function cssv(n){ return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

    function renderVolcano(data, elId){
        const el = document.getElementById(elId);
        if(!el) return;
        if(!data || !data.length){ el.innerHTML = '<p class="note" style="padding:40px;text-align:center">No perturbation data</p>'; return; }
        const sig = data.filter(d => d.s), non = data.filter(d => !d.s);
        const top = [...sig].sort((a,b) => b.p - a.p).slice(0,6).map(d => d.g);
        const prio = window.PRIORITY_GENES || [];
        const labelSet = new Set(sig.filter(d => top.includes(d.g) || prio.includes(d.g)).map(d => d.g));
        const mk = (arr,colorFn,size,op) => ({
            x:arr.map(d=>d.fc), y:arr.map(d=>d.p), mode:"markers",
            marker:{color:typeof colorFn==="function"?arr.map(colorFn):colorFn, size, opacity:op},
            text:arr.map(d=>`<b>${d.g}</b><br>guide: ${d.guide||d.g}<br>log\u2082FC: ${d.fc}<br>\u2212log\u2081\u2080p: ${d.p}`),
            hoverinfo:"text", showlegend:false
        });
        const ann = sig.filter(d => labelSet.has(d.g)).map(d => ({x:d.fc,y:d.p,text:d.g,showarrow:false,yshift:11,font:{size:10,color:cssv("--text")}}));
        Plotly.newPlot(elId,[
            mk(non, cssv("--border-strong")||"#bbb", 5, .5),
            mk(sig, d => d.fc>0 ? cssv("--up") : cssv("--down"), 8, .9)
        ],{
            margin:{t:8,b:42,l:44,r:10}, height:340, autosize:true,
            paper_bgcolor:"rgba(0,0,0,0)", plot_bgcolor:"rgba(0,0,0,0)",
            font:{color:cssv("--text-soft"),size:10.5},
            xaxis:{title:"log\u2082 fold change", zeroline:true, zerolinecolor:cssv("--border-strong"), gridcolor:cssv("--border")},
            yaxis:{title:"\u2212log\u2081\u2080(p)", gridcolor:cssv("--border")},
            annotations:ann
        },{responsive:true,displayModeBar:false});
    }

    function drawVolcanoes(p){
        const cv = condVolcanoOf(p);
        Object.keys(cv).sort().forEach(c => {
            const arr = cv[c] || [];
            const sigN = arr.filter(d => d.s).length;
            const cnt = document.getElementById("vc-" + cid(c));
            if(cnt) cnt.textContent = `${arr.length} guides \u00b7 ${sigN} significant`;
            renderVolcano(arr, "vol-" + cid(c));
        });
    }

    function toggleSection(id){
        const s = document.getElementById(id);
        s.classList.toggle("collapsed"); s.classList.toggle("open");
        if(id==="sec-volcano" && s.classList.contains("open") && currentId!=null) setTimeout(() => drawVolcanoes(PROGRAMS[currentId]), 30);
    }
    function toggleTheme(){
        const d = document.documentElement.dataset.theme === "dark";
        document.documentElement.dataset.theme = d ? "light" : "dark";
        document.getElementById("themebtn").textContent = d ? "Dark" : "Light";
        if(currentId!=null && document.getElementById("sec-volcano").classList.contains("open")) drawVolcanoes(PROGRAMS[currentId]);
    }
    function prev(){ const i = IDS.indexOf(currentId); if(i > 0) render(IDS[i-1]); }
    function next(){ const i = IDS.indexOf(currentId); if(i >= 0 && i < IDS.length-1) render(IDS[i+1]); }
    document.addEventListener("keydown", e => {
        if(e.target && /input|textarea|select/i.test(e.target.tagName)) return;
        if(e.key==="ArrowLeft") prev();
        if(e.key==="ArrowRight") next();
    });
    let _rsz;
    window.addEventListener("resize", () => {
        clearTimeout(_rsz);
        _rsz = setTimeout(() => {
            if(currentId!=null && document.getElementById("sec-volcano").classList.contains("open")){
                const cv = PROGRAMS[currentId].condition_volcano || {};
                Object.keys(cv).forEach(c => { const el = document.getElementById("vol-" + cid(c)); if(el && el.data) Plotly.Plots.resize(el); });
            }
        }, 150);
    });

    window.addEventListener("hashchange", () => {
        const m = (location.hash.match(/program-(\d+)/) || [])[1];
        if(m && PROGRAMS[+m] && +m !== currentId) render(+m);
    });

    buildRail();
    (function(){
        const m = (location.hash.match(/program-(\d+)/) || [])[1];
        const id = (m && PROGRAMS[+m]) ? +m : IDS[0];
        render(id);
    })();
    </script>
</body>
</html>'''

    return (
        template
        .replace("__PROGRAMS_JSON__", programs_json)
        .replace("__PRIORITY_JSON__", priority_json)
        .replace("__PROGRAM_LIST_JSON__", program_list_json)
        .replace("__DATASET_CRUMB_JSON__", crumb_json)
        .replace("__DATASET_SUB__", sub_text)
        .replace("__NUM_PROGRAMS__", str(num_programs))
        .replace("__GENERATED_ON__", generated_on)
    )


"""
@description
Configuration loader for HTML report generation.
It is responsible for reading JSON/YAML configs and applying per-step defaults
with CLI override precedence.

Key features:
- Supports JSON and YAML (if PyYAML is installed).
- Applies config values when CLI flags are omitted.

@dependencies
- json: Built-in JSON parser
- yaml (optional): YAML parser when available
- sys: CLI inspection for override detection
"""


def load_config(config_path: str | None) -> dict:
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


def get_cli_overrides(argv: list[str]) -> set[str]:
    overrides: set[str] = set()
    for token in argv:
        if token.startswith("--"):
            name = token[2:]
            if "=" in name:
                name = name.split("=", 1)[0]
            overrides.add(name.replace("-", "_"))
    return overrides


def apply_config_overrides(
    args: argparse.Namespace, config: dict, cli_overrides: set[str]
) -> argparse.Namespace:
    steps_cfg = config.get("steps", {}) if isinstance(config.get("steps", {}), dict) else {}
    step_cfg = steps_cfg.get("html_report", {})
    if not isinstance(step_cfg, dict):
        return args

    for key, value in step_cfg.items():
        dest = str(key).replace("-", "_")
        if dest in cli_overrides:
            continue
        if hasattr(args, dest):
            setattr(args, dest, value)
    return args


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Path to config file (YAML or JSON)")
    parser.add_argument("--summary-csv")
    parser.add_argument("--annotations-dir")
    parser.add_argument("--enrichment-dir")
    parser.add_argument("--volcano-csv")
    parser.add_argument(
        "--volcano-condition-csv",
        action="append",
        help="Condition-specific regulator matrix as condition=path; repeatable",
    )
    parser.add_argument("--gene-loading-csv")
    parser.add_argument(
        "--enrichment-filtered-csv",
        help="STRING enrichment_filtered.csv; sources the deterministic "
        "per-program pathway list and 'Top pathway' chip",
    )
    parser.add_argument(
        "--celltype-file",
        help="Long-format celltype_detail.csv (program, cell_type, direction, log2_fc); "
        "sources the per-program cell-type enrichment section",
    )
    parser.add_argument("--top-loading", type=int, default=15)
    parser.add_argument("--top-unique", type=int, default=8)
    parser.add_argument("--output-html")
    parser.add_argument(
        "--presentation-json",
        help="Optional presentation.json from step 6 (lead_html, tags, module_short)",
    )
    parser.add_argument(
        "--research-results-dir",
        help="Optional directory of per-program research_results/{id}.json carrying "
        "module status, DOIs, contradictions and evidence gaps (spec 10)",
    )
    parser.add_argument(
        "--dataset-crumb",
        default="",
        help="Short dataset descriptor shown in the hero kicker (e.g. 'Hepatocyte \u00b7 mouse liver \u00b7 Perturb-seq')",
    )
    parser.add_argument(
        "--regulator-significance-threshold",
        type=float,
        default=0.05,
        help="Adjusted p-value threshold used when the regulator table has no explicit significance column",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    cli_overrides = get_cli_overrides(sys.argv)
    args = apply_config_overrides(args, config, cli_overrides)

    required = [
        ("summary_csv", "--summary-csv"),
        ("annotations_dir", "--annotations-dir"),
        ("enrichment_dir", "--enrichment-dir"),
        ("gene_loading_csv", "--gene-loading-csv"),
        ("output_html", "--output-html"),
    ]
    missing = [flag for attr, flag in required if not getattr(args, attr)]
    if missing:
        raise SystemExit(f"Missing required arguments: {', '.join(missing)}")

    generate_report(
        args.summary_csv, args.annotations_dir, args.enrichment_dir,
        args.volcano_csv,
        parse_condition_path_args(args.volcano_condition_csv),
        args.gene_loading_csv,
        args.output_html,
        regulator_significance_threshold=args.regulator_significance_threshold,
        presentation_json=getattr(args, "presentation_json", None),
        dataset_crumb=getattr(args, "dataset_crumb", "") or "",
        research_results_dir=getattr(args, "research_results_dir", None),
        top_loading=int(getattr(args, "top_loading", 15) or 15),
        top_unique=int(getattr(args, "top_unique", 8) or 8),
        enrichment_filtered_csv=getattr(args, "enrichment_filtered_csv", None),
        celltype_file=getattr(args, "celltype_file", None),
    )
