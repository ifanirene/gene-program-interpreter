"""The report's per-program volcano plots render for a single-regulator run.

Incident it prevents: the brain-endothelial report showed "No perturbation data available." on
top of 162 correctly-computed volcano points per program. ``generate_report`` writes two keys —
``volcano`` (single-regulator runs) and ``condition_volcano`` (condition-keyed runs) — but the
JavaScript only ever read ``condition_volcano``. And the single-regulator CSV was read
comma-only, so a tab-separated file passed pre-flight and then rendered empty.

The JS itself needs a browser to exercise; here we prove the Python half end-to-end — the points
are embedded and the folding helper that makes the renderer draw them is present — for both a
comma- and a tab-separated single-regulator file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from gpi.html_report import generate_report

REPO_ROOT = Path(__file__).resolve().parent.parent
REGULATORS_CSV = (REPO_ROOT / "examples" / "brain_endothelial_demo"
                  / "Discovery_FP_moi15_seq2_thresh10_k100_default.csv")


def _render(tmp_path: Path, regulators_path: Path) -> str:
    (tmp_path / "summary.csv").write_text("Topic,Name\n1,Program 1\n2,Program 2\n")
    (tmp_path / "genes.csv").write_text("Name,Score,RowID\nCldn5,0.9,1\nPecam1,0.8,2\nEng,0.7,3\n")
    (tmp_path / "ann").mkdir()
    (tmp_path / "enr").mkdir()
    out = tmp_path / "report.html"
    generate_report(
        summary_csv=str(tmp_path / "summary.csv"),
        annotations_dir=str(tmp_path / "ann"),
        enrichment_dir=str(tmp_path / "enr"),
        volcano_csv=str(regulators_path),
        volcano_condition_csvs=None,
        gene_loading_csv=str(tmp_path / "genes.csv"),
        output_html=str(out),
    )
    return out.read_text(encoding="utf-8")


def _nonempty_volcano_count(html: str) -> int:
    return sum(1 for v in re.findall(r'"volcano":\s*(\[[^\]]*\])', html) if v.strip() != "[]")


@pytest.mark.skipif(not REGULATORS_CSV.exists(), reason="brain-endothelial demo data not present")
def test_single_regulator_run_embeds_points_and_the_folding_helper(tmp_path) -> None:
    """The single-file case: points must be embedded, AND condVolcanoOf must be present so the
    renderer folds `volcano` into a panel instead of showing 'No perturbation data'."""
    html = _render(tmp_path, REGULATORS_CSV)
    assert "condVolcanoOf" in html, "the volcano-folding helper is missing from the report"
    assert _nonempty_volcano_count(html) >= 1, "no program has embedded volcano points"


@pytest.mark.skipif(not REGULATORS_CSV.exists(), reason="brain-endothelial demo data not present")
def test_single_regulator_read_sniffs_the_separator(tmp_path) -> None:
    """A TAB-separated single-regulator file must render the same points as the CSV. It used to
    pass --check-inputs (which sniffs) and then render empty (the report read comma-only)."""
    tsv = tmp_path / "regulators.tsv"
    pd.read_csv(REGULATORS_CSV, sep=None, engine="python").to_csv(tsv, sep="\t", index=False)
    html = _render(tmp_path, tsv)
    assert _nonempty_volcano_count(html) >= 1, "tab-separated regulators rendered empty"
