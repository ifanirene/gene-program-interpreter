"""Regression tests for the user-facing install and onboarding surface."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from gpi import run_pipeline


def test_emit_config_uses_packaged_defaults(tmp_path, monkeypatch):
    """Config scaffolding must work without a source-tree configs/ directory."""
    monkeypatch.chdir(tmp_path)
    context = tmp_path / "context.yaml"
    context.write_text(
        """\
context:
  organism: mouse
  species_taxid: 10090
  tissue: liver
  cell_type: hepatocyte
  conditions: [aging]
  context_terms: [metabolic zonation]
  assay: single-cell RNA-seq
""",
        encoding="utf-8",
    )
    output = tmp_path / "run.yaml"

    rc = run_pipeline.main(
        [
            "--emit-config",
            "--context-file",
            str(context),
            "--gene-loading",
            "genes.csv",
            "--output-dir",
            "runs/demo",
            "--output",
            str(output),
        ]
    )

    assert rc == 0
    config = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert config["research"]["concurrency"] == 4
    assert config["annotation"]["batch"] is True
    assert config["inputs"]["gene_loading"] == "genes.csv"


def test_doctor_reports_ready_without_printing_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-value")
    monkeypatch.setenv("PUBMED_EMAIL", "person@example.com")
    monkeypatch.setenv("OPENALEX_API_KEY", "openalex-secret")
    monkeypatch.setenv("NCBI_API_KEY", "ncbi-secret")
    monkeypatch.setattr(run_pipeline.shutil, "which", lambda _: "/fake/claude")
    monkeypatch.setattr(
        run_pipeline.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"loggedIn": True, "authMethod": "claude.ai"}),
        ),
    )

    assert run_pipeline.main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "Ready for a full run" in output
    assert "Claude login is active" in output
    assert "secret-value" not in output


def test_plugin_manifest_and_runner_are_present():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    marketplace = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())

    assert manifest["name"] == "gene-program-interpreter"
    assert marketplace["plugins"][0]["source"] == "./"
    assert (root / "skills" / "interpret" / "SKILL.md").is_file()
    runner = root / "bin" / "gpi"
    assert runner.is_file() and runner.stat().st_mode & 0o111
