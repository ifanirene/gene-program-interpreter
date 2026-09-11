from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from gpi.anthropic_live import run_live_requests


class FakeMessages:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def create(self, **params):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return SimpleNamespace(
            model_dump=lambda: {
                "content": [{"type": "text", "text": params["messages"][0]["content"]}],
                "model": params["model"],
            }
        )


def test_live_requests_are_concurrent_and_batch_compatible(tmp_path: Path) -> None:
    messages = FakeMessages()
    client = SimpleNamespace(messages=messages)
    requests = [
        {"custom_id": f"topic_{i}", "params": {"messages": [{"role": "user", "content": str(i)}]}}
        for i in range(1, 4)
    ]
    output = tmp_path / "results.jsonl"

    counts = run_live_requests(requests, output, concurrency=2, client=client)

    assert counts == {"succeeded": 3, "errored": 0}
    assert messages.max_active == 2
    results = [json.loads(line) for line in output.read_text().splitlines()]
    assert [row["custom_id"] for row in results] == ["topic_1", "topic_2", "topic_3"]
    assert all(row["result"]["type"] == "succeeded" for row in results)
    assert results[0]["result"]["message"]["model"] == "claude-sonnet-4-6"


def test_live_request_error_is_written_without_losing_other_results(tmp_path: Path) -> None:
    client = SimpleNamespace(messages=FakeMessages())
    requests = [
        {"custom_id": "topic_1", "params": {"messages": [{"role": "user", "content": "ok"}]}},
        {"custom_id": "topic_2", "params": {"messages": []}},
    ]
    output = tmp_path / "results.jsonl"

    counts = run_live_requests(requests, output, client=client)

    assert counts == {"succeeded": 1, "errored": 1}
    results = [json.loads(line) for line in output.read_text().splitlines()]
    assert results[1]["result"]["type"] == "errored"
    assert "no messages" in results[1]["result"]["error"]["message"]
