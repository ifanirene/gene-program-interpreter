"""Offline tests for the Claude-subscription synthesis adapter (gpi.claude_cli).

Every test here runs with a FAKE ``claude`` binary, so the suite makes no model call and
costs nothing. What is actually under test is the part that can silently go wrong:
credential scrubbing, the sandbox flags, JSON-Schema passthrough, and the batch-result
envelope the downstream parsers depend on.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from gpi import claude_cli
from gpi.annotation_contract import Annotation, output_format, parse_annotation
from gpi.run_pipeline import (
    DOTENV_MODEL_CREDENTIAL_SKIP,
    PipelineConfig,
    enforce_subscription_only,
    load_env_file,
    subscription_only_steps,
)

ANNOTATION = {
    "program_id": 7,
    "label": "Toy arterial identity program",
    "brief_summary": "A toy annotation used only by the offline adapter tests.",
    "overview": "Toy overview text for the offline adapter test.",
    "modules": [
        {
            "name": "Toy module",
            "summary": "Toy module summary.",
            "key_genes": ["Gja5", "Cxcr4"],
            "pmids": ["12345678"],
            "evidence": "Toy evidence sentence.",
        }
    ],
    "distinctive": "Toy distinctive statement.",
    "regulators": [{"gene": "Notch1", "confidence": "High", "mechanism": "Toy mechanism."}],
}


def _fake_cli(tmp_path: Path, *, body: str) -> Path:
    """Write an executable stub that mimics `claude -p --output-format json`."""
    script = tmp_path / "fake_claude"
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


RECORDER = """
import json, os, sys
argv = sys.argv[1:]
prompt = sys.stdin.read()
record = {
    "argv": argv,
    "prompt": prompt,
    "env_anthropic": sorted(k for k in os.environ if k.startswith("ANTHROPIC_")),
    "env_claudecode": sorted(k for k in os.environ if k.startswith("CLAUDE")),
    "cwd": os.getcwd(),
}
with open(os.environ["FAKE_CLI_RECORD"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(record) + "\\n")
payload = json.loads(os.environ["FAKE_CLI_RESULT"])
print(json.dumps(payload))
"""


def _ok_payload(text: str) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "session_id": "fake-session",
            "num_turns": 2,
            "duration_ms": 1234,
            "total_cost_usd": 0.05,
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "modelUsage": {"claude-opus-5": {"costUSD": 0.05, "canonicalModel": "claude-opus-5"}},
        }
    )


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    record = tmp_path / "calls.jsonl"
    script = _fake_cli(tmp_path, body=RECORDER)
    monkeypatch.setenv("GPI_CLAUDE_CLI", str(script))
    monkeypatch.setenv("FAKE_CLI_RECORD", str(record))
    monkeypatch.setenv("FAKE_CLI_RESULT", _ok_payload(json.dumps(ANNOTATION)))
    return record


def _calls(record: Path) -> list[dict]:
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------- credential isolation

def test_cli_env_drops_every_anthropic_variable(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("ANTHROPIC_FUTURE_CREDENTIAL", "unknown-but-blocked-by-prefix")
    monkeypatch.setenv("NCBI_API_KEY", "keep-me")
    env = claude_cli.cli_env()
    assert not [k for k in env if k.startswith("ANTHROPIC_")]
    assert env["NCBI_API_KEY"] == "keep-me"


def test_cli_env_drops_parent_claude_code_session_state(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "1234")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/tmp/socket")
    env = claude_cli.cli_env()
    for name in ("CLAUDECODE", "CLAUDE_CODE_SSE_PORT", "CLAUDE_CODE_MESSAGING_SOCKET"):
        assert name not in env


def test_subprocess_never_sees_an_api_key(fake_cli, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    claude_cli.complete("hello", model="claude-opus-5", json_schema=None)
    call = _calls(fake_cli)[0]
    assert call["env_anthropic"] == []
    assert "sk-ant-should-not-leak" not in json.dumps(call)


# ------------------------------------------------------------------------------- sandboxing

def test_argv_is_sandboxed_and_never_bypasses_permissions(fake_cli):
    claude_cli.complete("hello", model="claude-opus-5")
    argv = _calls(fake_cli)[0]["argv"]
    assert argv[0] == "-p"
    for flag, value in (("--model", "claude-opus-5"), ("--output-format", "json"),
                        ("--tools", ""), ("--permission-prompts", "none"),
                        ("--setting-sources", "")):
        assert argv[argv.index(flag) + 1] == value
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    # Permission bypass and --bare (which forces API-key auth) must never appear.
    for forbidden in ("--dangerously-skip-permissions",
                      "--allow-dangerously-skip-permissions", "--bare"):
        assert forbidden not in argv
    assert "bypassPermissions" not in argv


def test_runs_in_a_scratch_cwd_outside_the_repository(fake_cli):
    claude_cli.complete("hello", model="claude-opus-5")
    cwd = Path(_calls(fake_cli)[0]["cwd"]).resolve()
    assert "gpi_claude_cli_" in cwd.name
    assert not (cwd / "AGENTS.md").exists() and not (cwd / "CLAUDE.md").exists()


# ------------------------------------------------------------------- JSON-schema passthrough

def test_real_annotation_schema_is_passed_to_the_cli(fake_cli):
    schema = output_format(Annotation)["schema"]
    params = {"model": "claude-opus-5", "max_tokens": 8192,
              "messages": [{"role": "user", "content": "annotate program 7"}],
              "output_config": {"format": output_format(Annotation)}}
    assert claude_cli.request_schema(params) == schema
    claude_cli.run_requests([{"custom_id": "topic_7", "params": params}], model="claude-opus-5")
    argv = _calls(fake_cli)[0]["argv"]
    assert json.loads(argv[argv.index("--json-schema") + 1]) == schema


def test_no_schema_flag_omits_the_schema(fake_cli):
    params = {"messages": [{"role": "user", "content": "x"}],
              "output_config": {"format": output_format(Annotation)}}
    claude_cli.run_requests([{"custom_id": "topic_7", "params": params}],
                            model="claude-opus-5", use_schema=False)
    assert "--json-schema" not in _calls(fake_cli)[0]["argv"]


# -------------------------------------------------------------------------- result envelope

def test_envelope_matches_what_the_batch_parsers_expect(fake_cli, tmp_path):
    params = {"model": "claude-opus-5", "max_tokens": 8192,
              "messages": [{"role": "user", "content": "annotate program 7"}],
              "output_config": {"format": output_format(Annotation)}}
    results, report = claude_cli.run_requests(
        [{"custom_id": "topic_7", "params": params}], model="claude-opus-5")
    row = results[0]
    assert row["custom_id"] == "topic_7"
    assert row["result"]["type"] == "succeeded"
    text = row["result"]["message"]["content"][0]["text"]
    # The downstream publication gate must accept it unchanged.
    assert parse_annotation(text, 7).program_id == 7
    assert report["api_billed"] is False
    assert report["resolved_models"] == ["claude-opus-5"]
    assert "estimate" in report["accounting_note"]

    out = tmp_path / "results.jsonl"
    claude_cli.write_results(results, out, report)
    assert json.loads(out.read_text().splitlines()[0])["custom_id"] == "topic_7"
    usage = json.loads(out.with_name(out.name + ".usage.json").read_text())
    assert usage["n_succeeded"] == 1 and usage["n_errored"] == 0


def test_parse_results_reads_the_adapter_output(fake_cli, tmp_path):
    """End-to-end with the real parser, so the contract is checked, not assumed."""
    from gpi import parse_results

    params = {"model": "claude-opus-5", "max_tokens": 8192,
              "messages": [{"role": "user", "content": "annotate program 7"}],
              "output_config": {"format": output_format(Annotation)}}
    results, report = claude_cli.run_requests(
        [{"custom_id": "topic_7", "params": params}], model="claude-opus-5")
    out = tmp_path / "results.jsonl"
    claude_cli.write_results(results, out, report)
    markdown_dir = tmp_path / "annotations"
    parse_results.parse_final_results(str(out), str(markdown_dir))
    assert (markdown_dir / "topic_7_annotation.json").exists()
    assert (markdown_dir / "topic_7_annotation.md").exists()


def test_a_failing_request_is_errored_not_fabricated(tmp_path, monkeypatch):
    script = _fake_cli(tmp_path, body="import sys; sys.stderr.write('boom'); sys.exit(2)\n")
    monkeypatch.setenv("GPI_CLAUDE_CLI", str(script))
    results, report = claude_cli.run_requests(
        [{"custom_id": "topic_7", "params": {"messages": [{"role": "user", "content": "x"}]}}],
        model="claude-opus-5")
    assert results[0]["result"]["type"] == "errored"
    assert report["n_errored"] == 1 and report["n_succeeded"] == 0


def test_cli_reported_error_is_not_treated_as_an_answer(tmp_path, monkeypatch):
    script = _fake_cli(tmp_path, body=RECORDER)
    monkeypatch.setenv("GPI_CLAUDE_CLI", str(script))
    monkeypatch.setenv("FAKE_CLI_RECORD", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("FAKE_CLI_RESULT", json.dumps(
        {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "nope"}))
    with pytest.raises(claude_cli.ClaudeCliError):
        claude_cli.complete("x", model="claude-opus-5")


# ------------------------------------------------------------------- pipeline-level guard

def _write_config(tmp_path: Path, *, backend: str, research_auth: str = "subscription") -> Path:
    gene_loading = tmp_path / "genes.csv"
    gene_loading.write_text("Name,Score,RowID\nGja5,0.1,1\n", encoding="utf-8")
    config = tmp_path / "run.yaml"
    config.write_text(
        "context:\n  organism: mouse\n  species_taxid: 10090\n"
        f"inputs:\n  gene_loading: {gene_loading}\n"
        f"output_dir: {tmp_path / 'out'}\n"
        f"research:\n  auth: {research_auth}\n"
        f"theme:\n  backend: {backend}\n"
        f"annotation:\n  backend: {backend}\n"
        f"presentation:\n  backend: {backend}\n",
        encoding="utf-8",
    )
    return config


def test_guard_removes_api_credentials_for_a_subscription_only_config(tmp_path, monkeypatch):
    cfg = PipelineConfig.from_yaml(_write_config(tmp_path, backend="claude_cli"))
    assert sorted(subscription_only_steps(cfg)) == ["annotate", "presentation", "theme"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-live")
    assert enforce_subscription_only(cfg) == ["ANTHROPIC_API_KEY"]
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_guard_leaves_an_api_configuration_alone(tmp_path, monkeypatch):
    cfg = PipelineConfig.from_yaml(_write_config(tmp_path, backend="claude_cli",
                                                 research_auth="api"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-live")
    assert enforce_subscription_only(cfg) == []
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-live"


def test_dotenv_never_loads_a_model_credential(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANTHROPIC_API_KEY=sk-ant-from-dotenv\nNCBI_API_KEY=ncbi-from-dotenv\n",
        encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    assert load_env_file(env_file) == env_file
    assert "ANTHROPIC_API_KEY" in DOTENV_MODEL_CREDENTIAL_SKIP
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert os.environ["NCBI_API_KEY"] == "ncbi-from-dotenv"


def test_presentation_claude_cli_path_returns_texts(fake_cli, monkeypatch):
    from gpi import presentation

    monkeypatch.setenv("FAKE_CLI_RESULT", _ok_payload(json.dumps(
        {"label": "Toy label", "brief_summary": "Toy brief summary for presentation."})))
    programs = {7: {"program_id": 7, "label": "Original label",
                    "brief_summary": "Original brief summary.", "modules": [],
                    "regulators": []}}
    texts = presentation.run_presentation_claude_cli(
        programs, {}, model="claude-opus-5", max_tokens=1024, effort=None,
        thinking=None, concurrency=1)
    assert 7 in texts and "Toy label" in texts[7]


def test_theme_backend_uses_the_cli(fake_cli, monkeypatch, tmp_path):
    from gpi import theme_representation

    monkeypatch.setenv("FAKE_CLI_RESULT", _ok_payload('{"theme": "toy"}'))
    payload = theme_representation.load_or_run_theme_extraction(
        "prompt text", tmp_path / "response.json", None, "claude_cli",
        "claude-opus-5", 8192)
    assert payload == {"theme": "toy"}
    assert (tmp_path / "response.json").exists()
