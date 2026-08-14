"""Offline limit validation and client-side ceiling tests."""

import asyncio
import math

import pytest
from claude_agent_sdk import AssistantMessage

from research.research_parallel import (
    ResearchLimitExceeded,
    _drive_once,
    _make_can_use_tool,
    validate_research_limits,
)


@pytest.mark.parametrize(
    "values",
    [
        (0, 1.0, 10),
        (-1, 1.0, 10),
        (True, 1.0, 10),
        (10, 0, 10),
        (10, math.inf, 10),
        (10, math.nan, 10),
        (10, 1.0, 0),
        (10, 1.0, math.inf),
    ],
)
def test_invalid_limits_fail_before_sdk(values):
    with pytest.raises(ValueError):
        validate_research_limits(*values)


def test_valid_limits_pass():
    validate_research_limits(30, 1.0, 600)
    validate_research_limits(None, 5.0, 3600)


def test_client_side_turn_ceiling_is_binding(monkeypatch):
    async def fake_query(*, prompt, options):
        for _ in range(4):
            yield AssistantMessage(content=[], model="test")

    monkeypatch.setattr("research.research_parallel.query", fake_query)

    async def run():
        await _drive_once(
            prompt="p", options=object(), submit_holder={}, per_program_timeout=5,
            client_turn_limit=3,
        )

    with pytest.raises(ResearchLimitExceeded, match="client max_turns"):
        asyncio.run(run())


def test_read_gate_allows_only_exact_bundle(tmp_path):
    bundle = tmp_path / "program_bundles" / "P1.json"
    bundle.parent.mkdir()
    bundle.write_text("{}")
    gate = _make_can_use_tool([], allowed_read_path=bundle)

    allowed = asyncio.run(gate("Read", {"file_path": str(bundle)}, None))
    denied = asyncio.run(gate("Read", {"file_path": "/tmp/other.json"}, None))
    assert allowed.__class__.__name__ == "PermissionResultAllow"
    assert denied.__class__.__name__ == "PermissionResultDeny"
