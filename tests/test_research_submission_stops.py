import asyncio
import json

from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, ToolUseBlock
from research import research_parallel as rp


def terminal():
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                         is_error=False, num_turns=2, session_id="isolated-test", stop_reason="end_turn")


def test_refusal_before_later_end_turn_is_retained_without_partial_content(tmp_path, monkeypatch):
    async def stream(**kwargs):
        yield StreamEvent(uuid="d1", session_id="isolated-test", event={"type":"content_block_delta", "delta":{"type":"input_json_delta", "partial_json":"DO_NOT_ARCHIVE_PARTIAL_CONTENT"}})
        yield StreamEvent(uuid="d2", session_id="isolated-test", event={"type":"message_delta", "delta":{"stop_reason":"refusal", "stop_details":{"type":"refusal", "category":"bio"}}})
        yield terminal()
    monkeypatch.setattr(rp, "query", stream)
    path = tmp_path / "messages.jsonl"
    trace, info = asyncio.run(rp._drive_once(prompt="test", options=None, submit_holder={},
                          per_program_timeout=5, transcript_path=path))
    assert info.get("provider_refusal") is True
    assert info["stop_reason"] == "end_turn"
    assert info["api_stop_events"][0]["category"] == "bio"
    assert "DO_NOT_ARCHIVE_PARTIAL_CONTENT" not in path.read_text()


def test_refused_partial_result_cannot_be_success_and_is_not_retried(tmp_path, monkeypatch):
    calls = []
    async def stream(**kwargs):
        calls.append(1)
        yield AssistantMessage(content=[ToolUseBlock(id="s", name="mcp__gpi__submit_result", input={"program_id":"P22"})], model="test")
        yield StreamEvent(uuid="d", session_id="isolated-test", event={"type":"message_delta", "delta":{"stop_reason":"refusal", "stop_details":{"category":"bio"}}})
        yield terminal()
    monkeypatch.setattr(rp, "query", stream)
    monkeypatch.setattr(rp, "build_options", lambda **kwargs: None)
    bundle = tmp_path / "P22.json"
    bundle.write_text('{"program_id":"P22"}')
    result = asyncio.run(rp._handle_one_program(bundle, out_dir=tmp_path / "out", audit_dir=tmp_path / "audit",
                          workspaces_root=tmp_path / "workspaces", system_prompt="test", model="test",
                          max_turns=5, max_budget_usd=1, per_program_timeout=5, max_attempts=2))
    data = json.loads(result.read_text())
    audit = json.loads((tmp_path / "audit/P22.audit.json").read_text())
    assert data["meta"]["status"] == "incomplete"
    assert audit["status"] == "provider_refusal"
    assert audit["provider_refusal"] is True
    assert len(calls) == 1
    assert (tmp_path / "audit/P22.raw_payload.json").exists()


def test_aging_task_rejects_generic_schema_success(tmp_path, monkeypatch):
    async def stream(**kwargs):
        yield AssistantMessage(content=[ToolUseBlock(id="s", name="mcp__gpi__submit_result", input={"program_id":"P22"})], model="test")
        yield terminal()
    monkeypatch.setattr(rp, "query", stream)
    monkeypatch.setattr(rp, "build_options", lambda **kwargs: None)
    bundle = tmp_path / "P22.json"
    bundle.write_text('{"program_id":"P22"}')
    result = asyncio.run(rp._handle_one_program(bundle, out_dir=tmp_path / "out", audit_dir=tmp_path / "audit",
                          workspaces_root=tmp_path / "workspaces", system_prompt="test", model="test",
                          max_turns=5, max_budget_usd=1, per_program_timeout=5, max_attempts=1,
                          require_aging_annotation=True))
    assert json.loads(result.read_text())["meta"]["status"] == "incomplete"
    assert "aging_annotation" in json.loads((tmp_path / "audit/P22.audit.json").read_text())["error"]


def test_aging_submit_tool_requires_nonnull_annotation():
    submit = rp._make_submit_tool({}, require_aging_annotation=True)
    schema = submit.input_schema
    assert "aging_annotation" in schema["required"]
    assert "anyOf" not in schema["properties"]["aging_annotation"]
