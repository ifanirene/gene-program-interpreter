"""Chunked Anthropic annotation-batch submission."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gpi import anthropic_batch


def test_submit_chunks_and_merges_results(tmp_path: Path, monkeypatch) -> None:
    batch_file = tmp_path / "annotation_requests.json"
    requests = [
        {"custom_id": f"P{index}", "params": {"messages": [{"role": "user", "content": "x"}]}}
        for index in range(1, 54)
    ]
    batch_file.write_text(json.dumps({"requests": requests}), encoding="utf-8")

    submitted: dict[str, list[dict]] = {}

    def fake_submit(chunk, **_kwargs):
        batch_id = f"batch-{len(submitted) + 1}"
        submitted[batch_id] = list(chunk)
        return batch_id

    def fake_check(batch_id):
        return {
            "processing_status": "ended",
            "counts": {
                "succeeded": len(submitted[batch_id]),
                "errored": 0,
                "processing": 0,
                "canceled": 0,
            },
        }

    def fake_fetch(batch_id, output):
        output.write_text(
            "".join(json.dumps({"custom_id": req["custom_id"]}) + "\n" for req in submitted[batch_id]),
            encoding="utf-8",
        )
        return output

    monkeypatch.setattr(anthropic_batch, "submit_batch", fake_submit)
    monkeypatch.setattr(anthropic_batch, "check_batch", fake_check)
    monkeypatch.setattr(anthropic_batch, "fetch_results", fake_fetch)

    args = argparse.Namespace(
        batch_file=str(batch_file),
        model="claude-sonnet-4-6",
        max_tokens=8192,
        thinking=None,
        effort=None,
        wait=True,
        batch_size=25,
    )
    assert anthropic_batch.cmd_submit(args) == 0
    assert [len(chunk) for chunk in submitted.values()] == [25, 25, 3]

    ids = json.loads(batch_file.with_suffix(".batch_ids.json").read_text(encoding="utf-8"))
    assert ids["chunk_sizes"] == [25, 25, 3]
    merged = batch_file.with_name("annotation_requests_results.jsonl")
    assert len(merged.read_text(encoding="utf-8").splitlines()) == 53
