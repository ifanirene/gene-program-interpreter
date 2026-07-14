"""Cell-type enrichment must reach the annotation model, with its effect sizes intact.

For the whole life of this pipeline it did not. The prompt slot, the formatter, the loader and
the CLI flags all existed — but nothing ever passed ``--celltype-file``, so every annotation
prompt ever generated said *"Cell-type enrichment: Not available."* Nothing failed; the
evidence was simply absent, which is the kind of bug a test suite only catches if it asserts on
what the model actually sees.

So these tests assert on the rendered prompt text, not on the plumbing.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from gpi.enrichment import generate_celltype_summary
from gpi.evidence_context import format_celltype_context, load_celltype_annotations

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_CELLTYPE_CSV = (
    REPO_ROOT / "examples" / "brain_endothelial_demo"
    / "FP_moi15_seq2_cnmf_program_markers_celltype_l2_top10_enriched_depleted.csv"
)

# Program 9 is the regression case. Its enrichment is modest (+1.23 in capillary) while its
# depletions are ~3x stronger (-3.59, -3.44 in arterial types). The old bucketed format kept
# the cell-type *names* and dropped the *numbers*, so it rendered this as "weakly enriched in
# Capillary" — hiding that the program is actively excluded from the arterial lineage.
P9_ENRICHED = ("Capillary", 1.234)
P9_DEPLETED = [
    ("Cycling artery", -3.590),
    ("Large-artery", -3.441),
    ("Choroid-plexus", -3.217),
    ("Arteriole", -2.094),
    ("Venous", -1.839),
]


@pytest.fixture
def detail_csv(tmp_path: Path) -> Path:
    rows = [
        {"program": 9, "cell_type": P9_ENRICHED[0], "direction": "enriched",
         "log2_fc": P9_ENRICHED[1], "rank_selected": 2},
        *[
            {"program": 9, "cell_type": name, "direction": "depleted",
             "log2_fc": fc, "rank_selected": 1}
            for name, fc in P9_DEPLETED
        ],
    ]
    path = tmp_path / "celltype_detail.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_real_input_has_no_fdr_column() -> None:
    """Guards the premise. The pipeline used to *require* ``fdr`` and died at step 1 on this
    file — over a column that was used in exactly one line."""
    assert REAL_CELLTYPE_CSV.exists()
    assert "fdr" not in pd.read_csv(REAL_CELLTYPE_CSV).columns


def test_summary_generates_without_an_fdr_column(tmp_path: Path) -> None:
    """The real file is already top-10 filtered upstream, so every row is significant."""
    out = tmp_path / "celltype_summary.csv"
    n_programs = generate_celltype_summary(REAL_CELLTYPE_CSV, out)
    assert n_programs > 0
    assert out.exists()
    # ...and the additive long-format table lands beside it, carrying the signed effect sizes.
    detail = out.parent / "celltype_detail.csv"
    assert detail.exists()
    df = pd.read_csv(detail)
    assert list(df.columns) == ["program", "cell_type", "direction", "log2_fc", "rank_selected"]
    assert (df["log2_fc"] < 0).any(), "signed log2FC lost: no depleted rows survived"


def test_depletion_keeps_its_sign(tmp_path: Path) -> None:
    """A depleted row must stay negative. Taking abs() here is what erased the P9 signal."""
    out = tmp_path / "celltype_summary.csv"
    generate_celltype_summary(REAL_CELLTYPE_CSV, out)
    df = pd.read_csv(out.parent / "celltype_detail.csv")
    depleted = df[df["direction"] == "depleted"]
    assert not depleted.empty
    assert (depleted["log2_fc"] < 0).all()
    enriched = df[df["direction"] == "enriched"]
    assert (enriched["log2_fc"] > 0).all()


def test_prompt_block_carries_effect_sizes_and_both_directions(detail_csv: Path) -> None:
    """The whole point: what the annotation model actually reads."""
    celltype_map = load_celltype_annotations(None, detail_csv)
    block = format_celltype_context(celltype_map, 9)

    assert "Not available" not in block
    assert "#### Cell-type enrichment" in block

    # Enrichment, with a signed magnitude.
    assert "Capillary (+1.23)" in block

    # Depletion is present, and rendered as prominently as enrichment. This is the assertion
    # that would have failed for the entire history of the project.
    assert "Depleted:" in block
    assert "Cycling artery (-3.59)" in block
    assert "Large-artery (-3.44)" in block

    # Strongest |effect| leads within a direction, so the model reads the biggest signal first.
    depleted_line = next(l for l in block.splitlines() if l.startswith("- Depleted:"))
    order = [name for name, _ in P9_DEPLETED]
    positions = [depleted_line.index(name) for name in order]
    assert positions == sorted(positions), f"not sorted by |log2FC|: {depleted_line}"


def test_absence_is_not_evidence_of_absence(detail_csv: Path) -> None:
    """The upstream file is top-10 filtered, so a missing cell type means 'not in the top 10',
    not 'not expressed'. Without this caveat in the prompt the model reads absence as a finding.
    """
    block = format_celltype_context(load_celltype_annotations(None, detail_csv), 9)
    assert "not evidence of absence" in block


def test_missing_data_still_says_not_available() -> None:
    """The honest empty case must survive — silence is not the same as no signal."""
    assert "Not available" in format_celltype_context({}, 9)
