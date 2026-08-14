import json
import subprocess
from pathlib import Path

from research import model_assessor


def test_model_assessor_defaults_to_six_parallel_headless_sessions():
    parser = model_assessor.build_parser()
    assert parser.get_default("concurrency") == 6


def test_bounded_text_windows_preserve_source_offsets_and_limit():
    text = "A" * 6_000 + " targetGene decisive evidence " + "B" * 30_000
    windows = model_assessor.bounded_text_windows(text, ["targetGene"], max_chars=8_000)

    assert sum(window["end"] - window["start"] for window in windows) <= 8_000
    assert any("targetGene" in window["text"] for window in windows)
    assert all(text[window["start"] : window["end"]] == window["text"] for window in windows)


def test_normalize_reviews_recomputes_offsets_and_context_accuracy():
    source = "prefix Exact support sentence. suffix"
    task = {
        "papers": {
            "paper": {
                "assessor_text": {"text": source, "text_type": "abstract"},
            }
        }
    }
    links = [
        {
            "review_id": "RL-1",
            "paper_key": "paper",
            "declared_context": "direct",
        }
    ]
    structured = {
        "reviews": [
            {
                "review_id": "RL-1",
                "assessor_id": "wrong",
                "support": "supports",
                "studied_genes": ["Gene1"],
                "function_supported_genes": ["Gene1"],
                "directness": "causal",
                "direction": "matches",
                "assessed_context": "indirect",
                "context_label_accuracy": "accurate",
                "evidence_span": {
                    "text": "Exact support sentence.",
                    "start": 0,
                    "end": 1,
                    "source_type": "full_text",
                },
                "rationale": "Direct experimental support.",
                "red_flags": [],
            }
        ]
    }

    [review] = model_assessor.normalize_reviews(
        task,
        links,
        structured,
        assessor_id="expected",
    )

    assert review["assessor_id"] == "expected"
    assert review["context_label_accuracy"] == "overclaimed"
    assert review["evidence_span"] == {
        "text": "Exact support sentence.",
        "start": 7,
        "end": 30,
        "source_type": "abstract",
    }


def test_invoke_codex_uses_isolated_headless_subscription_cli(monkeypatch, tmp_path):
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["reviews"],
                "properties": {
                    "reviews": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        output_schema_path = Path(command[command.index("--output-schema") + 1])
        captured["output_schema"] = json.loads(output_schema_path.read_text(encoding="utf-8"))
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"assessor_id":"secondary","reviews":[]}', encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 101,
                        "cached_input_tokens": 11,
                        "output_tokens": 23,
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(model_assessor.subprocess, "run", fake_run)

    structured, telemetry = model_assessor._invoke_codex(
        "frozen task",
        schema_path,
        model="gpt-5.6-sol",
        effort="high",
        timeout=900,
        executable="codex-test",
    )

    command = captured["command"]
    assert command[:2] == ["codex-test", "exec"]
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"
    assert command[command.index("--config") + 1] == 'model_reasoning_effort="high"'
    for feature in model_assessor.CODEX_DISABLED_FEATURES:
        assert ["--disable", feature] == command[
            command.index(feature) - 1 : command.index(feature) + 1
        ]
    assert captured["output_schema"] == {
        "type": "object",
        "required": ["reviews"],
        "properties": {
            "reviews": {
                "type": "array",
                "items": {"type": "string"},
            }
        },
    }
    assert captured["kwargs"]["input"] == "frozen task"
    assert captured["kwargs"]["cwd"] == Path(command[command.index("--cd") + 1])
    assert structured == {"assessor_id": "secondary", "reviews": []}
    assert telemetry == {
        "provider": "codex",
        "executable": "codex-test",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "turns": 1,
        "input_tokens": 101,
        "cached_input_tokens": 11,
        "output_tokens": 23,
        "cost_usd_equivalent": None,
    }
