"""Every stage must select the SAME genes for a program.

The pipeline shows the user a gene list (HTML report), summarizes one (gene_summaries),
prompts an LLM with one (evidence_context / theme_representation) and pays an agent to
research one (research/bundle). Those had drifted into five separate implementations, and
the research bundle's had silently degraded: it ranked uniqueness *within* the top-loading
genes, so `distinctive_genes` was a strict subset that surfaced nothing new.

They now all delegate to ``gpi.enrichment.select_program_gene_sets``. These tests assert the
properties that made the old bug possible, so it cannot come back quietly.
"""

import pandas as pd
import pytest

from gpi.enrichment import (
    add_global_uniqueness_scores,
    rank_program_genes_by_loading,
    select_program_gene_sets,
)

TOP_LOADING = 15
TOP_UNIQUE = 8


@pytest.fixture(scope="module")
def gene_df(gene_loading_csv):
    df = pd.read_csv(gene_loading_csv)
    if "program_id" not in df.columns and "RowID" in df.columns:
        df = df.rename(columns={"RowID": "program_id"})
    return df


@pytest.fixture(scope="module")
def program_ids(gene_df):
    return sorted({int(p) for p in pd.to_numeric(gene_df["program_id"], errors="coerce").dropna()})


def test_selector_sets_are_disjoint_and_sized(gene_df, program_ids):
    """The core invariant: two SEPARATE ranked sets, never one sliced twice."""
    for pid in program_ids:
        loading, unique = select_program_gene_sets(
            gene_df, pid, top_loading=TOP_LOADING, top_unique=TOP_UNIQUE
        )
        assert not set(loading) & set(unique), f"P{pid}: sets overlap"
        assert len(loading) == TOP_LOADING and len(unique) == TOP_UNIQUE
        assert len(set(loading)) == len(loading) and len(set(unique)) == len(unique)


def test_unique_genes_come_from_outside_the_loading_pool(gene_df, program_ids):
    """The regression that started this: unique genes must be reachable only by ranking the
    program's WHOLE gene set. If they all sat inside the top-loading pool, the second list
    would be decoration."""
    for pid in program_ids:
        loading, unique = select_program_gene_sets(
            gene_df, pid, top_loading=TOP_LOADING, top_unique=TOP_UNIQUE
        )
        full_order = rank_program_genes_by_loading(gene_df, pid)
        assert set(unique).issubset(set(full_order))          # real genes of this program
        assert set(unique).isdisjoint(set(full_order[:TOP_LOADING]))  # but not top-loading ones


def test_all_stages_select_identical_genes(gene_loading_csv, gene_df, program_ids):
    """report == gene summaries == evidence context == themes == research bundle."""
    from gpi.evidence_context import load_gene_table as ec_load, select_program_genes
    from gpi.gene_summaries import load_program_genes
    from gpi.html_report import build_panel_stats
    from gpi.theme_representation import load_gene_table as tr_load, select_program_gene_lists

    expected = {
        pid: select_program_gene_sets(
            gene_df, pid, top_loading=TOP_LOADING, top_unique=TOP_UNIQUE
        )
        for pid in program_ids
    }

    summaries = load_program_genes(
        gene_loading_csv, top_n_loading=TOP_LOADING, top_n_unique=TOP_UNIQUE
    )
    themes = select_program_gene_lists(tr_load(gene_loading_csv), TOP_LOADING, TOP_UNIQUE)
    ec_df = ec_load(gene_loading_csv)
    panel = build_panel_stats(
        pd.DataFrame({"Topic": program_ids}),
        str(gene_loading_csv),
        top_loading=TOP_LOADING,
        top_unique=TOP_UNIQUE,
    )

    for pid in program_ids:
        loading, unique = expected[pid]
        assert (summaries[pid]["top_loading"], summaries[pid]["top_unique"]) == (loading, unique)
        assert summaries[pid]["drivers"] == loading + unique
        assert (themes[pid]["top_loading_genes"], themes[pid]["top_unique_genes"]) == (loading, unique)
        assert select_program_genes(ec_df, pid, TOP_LOADING, TOP_UNIQUE) == (loading, unique)
        assert panel[pid]["top_loading"].split(", ") == loading
        assert panel[pid]["unique"].split(", ") == unique


def test_wide_gene_lists_stay_supersets_of_top_loading(gene_loading_csv, gene_df, program_ids):
    """members / all_genes / the enrichment list share the selector's loading order, so the
    headline genes are always a prefix of them — not a separately-sorted near-copy."""
    from gpi.gene_summaries import load_program_genes
    from gpi.theme_representation import load_gene_table as tr_load, select_enrichment_gene_lists

    summaries = load_program_genes(
        gene_loading_csv, top_n_loading=TOP_LOADING, top_n_unique=TOP_UNIQUE
    )
    enrichment_lists = select_enrichment_gene_lists(tr_load(gene_loading_csv), top_n=300)

    for pid in program_ids:
        loading = summaries[pid]["top_loading"]
        assert summaries[pid]["all_genes"][:TOP_LOADING] == loading
        assert summaries[pid]["members"][:TOP_LOADING] == loading
        assert enrichment_lists[pid][:TOP_LOADING] == loading


def test_selector_degrades_instead_of_raising():
    """A report must render an empty panel, not crash, on an unusable table."""
    assert select_program_gene_sets(pd.DataFrame(), 1) == ([], [])
    assert rank_program_genes_by_loading(pd.DataFrame(), 1) == []
    usable = pd.DataFrame({"Name": ["A"], "Score": [1.0], "program_id": [1]})
    assert select_program_gene_sets(usable, 999) == ([], [])   # absent program
    assert select_program_gene_sets(usable, 1, top_loading=0, top_unique=0) == ([], [])


def test_one_canonical_uniqueness_metric():
    """Uniqueness is Score x IDF: a gene in every program is discounted below a rarer gene
    with the same loading. Previously copy-pasted into four modules."""
    df = pd.DataFrame(
        {
            "Name": ["UBIQ", "RARE", "UBIQ", "UBIQ"],
            "Score": [1.0, 1.0, 1.0, 1.0],
            "program_id": [1, 1, 2, 3],
        }
    )
    scored = add_global_uniqueness_scores(df)
    ubiq = scored.loc[scored["Name"] == "UBIQ", "UniquenessScore"].iloc[0]
    rare = scored.loc[scored["Name"] == "RARE", "UniquenessScore"].iloc[0]
    assert rare > ubiq
    # Scores tie, so the stable sort makes row order decide top-loading (UBIQ, row 0); the
    # distinctive slot then goes to the gene loading ranking missed — which is the point.
    loading, unique = select_program_gene_sets(df, 1, top_loading=1, top_unique=1)
    assert loading == ["UBIQ"] and unique == ["RARE"]
