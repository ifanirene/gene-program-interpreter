"""Presentation routing: live is the default; ``--batch`` opts into the slow Batch API.

Incident it prevents: presentation is one tiny strict-JSON completion per program, but it used
to be submitted through the Anthropic Batch API — whose queue latency (minutes to hours) dwarfs
the work and made a 3-program run wait ~10 min at the very end (observed: a batch created at
18:23:48 didn't end until 18:33:55). Live per-program calls are now the default; ``--batch``
stays for cost-sensitive large runs. These tests pin the routing so a refactor can't silently
send the default back through the queue. Offline: the two network runners are monkeypatched.
"""

from __future__ import annotations

import json

import gpi.presentation as presentation


def _stub_runners(monkeypatch):
    """Replace both network paths with recorders so main()'s routing is testable offline."""
    calls: list[str] = []

    def fake_live(programs, lexicon, **kw):
        calls.append("live")
        return {}

    def fake_batch(programs, lexicon, **kw):
        calls.append("batch")
        return {}

    monkeypatch.setattr(presentation, "run_presentation_live", fake_live)
    monkeypatch.setattr(presentation, "run_presentation_batch", fake_batch)
    return calls


def test_default_is_live(annotations_dir, tmp_path, monkeypatch) -> None:
    """No flag must route to live per-program calls — never the Batch API queue."""
    calls = _stub_runners(monkeypatch)
    out = tmp_path / "presentation.json"
    rc = presentation.main(["--annotations-dir", str(annotations_dir), "--out", str(out),
                            "--model", "claude-haiku-4-5-20251001"])
    assert rc == 0
    assert calls == ["live"], f"default routed to {calls}, expected live"
    assert out.exists()


def test_batch_flag_opts_into_batch(annotations_dir, tmp_path, monkeypatch) -> None:
    """``--batch`` is the explicit escape hatch for cost-over-latency large runs."""
    calls = _stub_runners(monkeypatch)
    out = tmp_path / "presentation.json"
    rc = presentation.main(["--annotations-dir", str(annotations_dir), "--out", str(out),
                            "--model", "claude-haiku-4-5-20251001", "--batch"])
    assert rc == 0
    assert calls == ["batch"], f"--batch routed to {calls}, expected batch"


def test_deterministic_calls_no_model(annotations_dir, tmp_path, monkeypatch) -> None:
    """``--deterministic`` must invoke neither model path and still write a valid file."""
    calls = _stub_runners(monkeypatch)
    out = tmp_path / "presentation.json"
    rc = presentation.main(["--annotations-dir", str(annotations_dir), "--out", str(out),
                            "--deterministic"])
    assert rc == 0
    assert calls == [], f"deterministic invoked a model path: {calls}"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["meta"]["mode"] == "deterministic"
    assert data["programs"]
