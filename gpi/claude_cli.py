"""Run GPI synthesis requests on the local Claude Code CLI (Claude subscription).

Why this module exists
----------------------
``gpi.anthropic_batch`` and ``gpi.anthropic_live`` execute the annotate step against
Anthropic's **API**, which bills API credit and requires ``ANTHROPIC_API_KEY``.
``gpi.theme_representation.call_anthropic`` and ``gpi.presentation.run_presentation_live``
do the same for the theme and presentation steps.  This adapter executes the *same*
request envelope through the locally installed ``claude`` CLI, which authenticates with
the user's Claude subscription (``claude login``) and makes **no API-billed call**.

Contract preservation
---------------------
* Input is the unchanged ``{"requests": [{"custom_id", "params": {...}}]}`` envelope that
  ``gpi.evidence_context prepare`` and ``gpi.presentation.build_batch_requests`` write.
* ``params["output_config"]["format"]["schema"]`` -- the JSON Schema that
  ``gpi.annotation_contract.output_format`` derives from the pydantic contract -- is passed
  to the CLI as ``--json-schema``, so structured output is enforced by the same schema the
  API path uses.  ``gpi.annotation_contract.parse_annotation`` still validates every
  response afterwards, so the publication checks are unchanged.
* Output is batch-compatible JSONL
  (``{"custom_id", "result": {"type": "succeeded", "message": {...}}}``), so
  ``gpi.parse_results``, ``gpi.annotation_contract.validate_batch_contract`` and
  ``gpi.presentation.read_results`` consume it without modification.

Credential isolation
--------------------
Every CLI subprocess is launched with a **scrubbed environment**: all ``ANTHROPIC_*``
variables and the ambient ``CLAUDE_CODE_*`` session variables are removed.  The CLI uses
the subscription only when no API key is visible, and an inherited parent-session socket
would otherwise confuse a nested launch.  The subprocess is also sandboxed:
``--tools ""`` (no built-in tools), ``--strict-mcp-config`` with no MCP config,
``--setting-sources ""`` (no user/project/local settings), ``--permission-prompts none``
(anything that would prompt is denied) and a scratch working directory so no project
``CLAUDE.md`` is discovered.  Permissions are never bypassed.

Known deviations from the API path (recorded, not hidden)
---------------------------------------------------------
* ``params["max_tokens"]`` is advisory only: the CLI exposes no max-tokens flag and uses
  the model's own output ceiling.  This is permissive (a larger ceiling), so it cannot
  truncate a response the API path would have completed.
* ``total_cost_usd`` reported by the CLI is a **list-price usage estimate**
  (``costBasis: "list"``) for a subscription session, not an invoice.  It is recorded
  under ``usage_estimate_usd`` and must be read that way.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from .log_redaction import install_log_redaction
from .progress import emit_step_progress

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
install_log_redaction()
logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_CONCURRENCY = 3
DEFAULT_TIMEOUT_SECONDS = 1800.0

#: Neutral system prompt. The CLI otherwise installs its coding-agent system prompt, which
#: is wrong for scientific synthesis; this keeps the session close to a bare API call.
DEFAULT_SYSTEM_PROMPT = (
    "You are a careful scientific assistant. Follow the user's instructions exactly, "
    "use only the evidence the user supplies, and return only the requested output."
)

#: Environment variables removed before launching the CLI. Every ANTHROPIC_* variable is
#: dropped by prefix as well, so a new credential variable cannot silently re-enable
#: API billing.
BLOCKED_ENV_EXACT = frozenset(
    {
        # third-party model providers
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "AWS_BEARER_TOKEN_BEDROCK",
        # ambient parent Claude Code session state (a nested launch must not inherit it)
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_SSE_PORT",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_SESSION_ATTENDED",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_PID",
        "CLAUDE_EFFORT",
        "CLAUDE_PLUGIN_ROOT",
    }
)
BLOCKED_ENV_PREFIXES = ("ANTHROPIC_",)


class ClaudeCliError(RuntimeError):
    """Raised when the CLI is unusable (missing binary, unparseable output)."""


def blocked_env_names(environ: Optional[dict[str, str]] = None) -> list[str]:
    """Names this adapter removes from ``environ`` (sorted, for auditing)."""
    environ = os.environ if environ is None else environ
    return sorted(
        name
        for name in environ
        if name in BLOCKED_ENV_EXACT or name.startswith(BLOCKED_ENV_PREFIXES)
    )


def cli_env(environ: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Return a copy of ``environ`` with every model-credential variable removed."""
    environ = os.environ if environ is None else environ
    blocked = set(blocked_env_names(environ))
    return {k: v for k, v in environ.items() if k not in blocked}


def resolve_cli() -> str:
    """Absolute path to the ``claude`` binary (``GPI_CLAUDE_CLI`` overrides the search)."""
    override = os.environ.get("GPI_CLAUDE_CLI")
    if override:
        if not Path(override).exists():
            raise ClaudeCliError(f"GPI_CLAUDE_CLI does not exist: {override}")
        return override
    found = shutil.which("claude")
    if not found:
        raise ClaudeCliError(
            "The 'claude' CLI was not found on PATH. Install Claude Code and run "
            "'claude login', or set GPI_CLAUDE_CLI to its absolute path."
        )
    return found


def request_schema(params: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Extract the structured-output JSON Schema from a prepared request's params."""
    fmt = (params.get("output_config") or {}).get("format") or {}
    schema = fmt.get("schema")
    return schema if isinstance(schema, dict) else None


def build_argv(
    cli: str,
    *,
    model: str,
    json_schema: Optional[dict[str, Any]],
    system_prompt: Optional[str],
) -> list[str]:
    """Build the sandboxed, subscription-only ``claude -p`` argv.

    Deliberately absent: ``--bare`` (it forces ANTHROPIC_API_KEY auth and would defeat the
    subscription requirement) and every permission-bypass flag.
    """
    argv = [
        cli,
        "-p",
        "--model", model,
        "--output-format", "json",
        "--tools", "",                 # no built-in tools
        "--strict-mcp-config",         # ignore every configured MCP server
        "--setting-sources", "",       # no user / project / local settings
        "--permission-prompts", "none",  # anything that would prompt is denied
        "--disable-slash-commands",
        "--no-session-persistence",
    ]
    if system_prompt:
        argv += ["--system-prompt", system_prompt]
    if json_schema is not None:
        argv += ["--json-schema", json.dumps(json_schema)]
    return argv


def complete(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    json_schema: Optional[dict[str, Any]] = None,
    system_prompt: Optional[str] = DEFAULT_SYSTEM_PROMPT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    cli: Optional[str] = None,
) -> dict[str, Any]:
    """Run one prompt on the CLI and return its text plus usage telemetry.

    Returns ``{"text", "usage", "model_usage", "usage_estimate_usd", "num_turns",
    "duration_ms", "session_id", "resolved_models"}``.  Raises on CLI failure.
    """
    cli = cli or resolve_cli()
    argv = build_argv(cli, model=model, json_schema=json_schema, system_prompt=system_prompt)
    # A scratch cwd keeps project CLAUDE.md / AGENTS.md out of the session context.
    with tempfile.TemporaryDirectory(prefix="gpi_claude_cli_") as workspace:
        proc = subprocess.run(
            argv,
            input=prompt,
            text=True,
            capture_output=True,
            env=cli_env(),
            cwd=workspace,
            timeout=timeout,
        )
    if proc.returncode != 0:
        raise ClaudeCliError(
            f"claude CLI exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}"
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCliError(
            f"claude CLI did not return JSON: {(proc.stdout or '').strip()[:300]}"
        ) from exc
    if payload.get("is_error"):
        raise ClaudeCliError(
            f"claude CLI reported an error ({payload.get('subtype')}): "
            f"{str(payload.get('result'))[:300]}"
        )
    text = payload.get("result")
    if not isinstance(text, str) or not text.strip():
        # Structured output can arrive parsed; re-serialize it so the text contract holds.
        structured = payload.get("structured_output")
        if structured is None:
            raise ClaudeCliError("claude CLI returned an empty result.")
        text = json.dumps(structured)
    model_usage = payload.get("modelUsage") or {}
    return {
        "text": text,
        "usage": payload.get("usage"),
        "model_usage": model_usage,
        "usage_estimate_usd": payload.get("total_cost_usd"),
        "num_turns": payload.get("num_turns"),
        "duration_ms": payload.get("duration_ms"),
        "session_id": payload.get("session_id"),
        "resolved_models": sorted(model_usage) if isinstance(model_usage, dict) else [],
    }


def _succeeded_envelope(custom_id: str, model: str, completion: dict[str, Any]) -> dict[str, Any]:
    """Wrap one completion in the Anthropic batch-result shape the parsers expect."""
    return {
        "custom_id": custom_id,
        "result": {
            "type": "succeeded",
            "message": {
                "id": completion.get("session_id"),
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": completion["text"]}],
                "stop_reason": "end_turn",
                "usage": completion.get("usage") or {},
            },
        },
    }


def _errored_envelope(custom_id: str, exc: BaseException) -> dict[str, Any]:
    return {
        "custom_id": custom_id,
        "result": {
            "type": "errored",
            "error": {"type": type(exc).__name__, "message": str(exc)[:1000]},
        },
    }


def run_requests(
    requests: list[dict[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    system_prompt: Optional[str] = DEFAULT_SYSTEM_PROMPT,
    use_schema: bool = True,
    progress_label: str = "claude cli requests",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute prepared requests concurrently; return (batch envelopes, usage report)."""
    if not requests:
        raise ValueError("No requests provided for the Claude CLI adapter.")
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    cli = resolve_cli()
    total = len(requests)
    emit_step_progress(0, total, progress_label)

    lock = threading.Lock()
    state = {"done": 0}
    usage_rows: list[dict[str, Any]] = []

    def execute(index_request: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        index, request = index_request
        custom_id = str(request.get("custom_id") or f"request_{index}")
        params = request.get("params") or {}
        messages = params.get("messages") or []
        try:
            if not messages:
                raise ValueError("request has no messages")
            prompt = messages[0].get("content", "")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("request prompt is empty or not plain text")
            completion = complete(
                prompt,
                model=params.get("model") or model,
                json_schema=request_schema(params) if use_schema else None,
                system_prompt=system_prompt,
                timeout=timeout,
                cli=cli,
            )
            envelope = _succeeded_envelope(custom_id, params.get("model") or model, completion)
            row = {
                "custom_id": custom_id,
                "status": "succeeded",
                "resolved_models": completion["resolved_models"],
                "usage_estimate_usd": completion["usage_estimate_usd"],
                "num_turns": completion["num_turns"],
                "duration_ms": completion["duration_ms"],
                "requested_max_tokens_advisory": params.get("max_tokens"),
                "model_usage": completion["model_usage"],
            }
        except Exception as exc:  # noqa: BLE001 - one bad request must not sink the step
            logger.error("Claude CLI request %s failed: %s", custom_id, exc)
            envelope = _errored_envelope(custom_id, exc)
            row = {"custom_id": custom_id, "status": "errored", "error": str(exc)[:500]}
        with lock:
            state["done"] += 1
            usage_rows.append(row)
            emit_step_progress(state["done"], total, progress_label)
        return envelope

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(execute, enumerate(requests)))

    estimates = [r.get("usage_estimate_usd") for r in usage_rows if isinstance(r.get("usage_estimate_usd"), (int, float))]
    resolved = sorted({m for r in usage_rows for m in r.get("resolved_models", [])})
    report = {
        "backend": "claude_code_cli_subscription",
        "auth": "claude_subscription_oauth",
        "api_billed": False,
        "requested_model": model,
        "resolved_models": resolved,
        "n_requests": total,
        "n_succeeded": sum(r["status"] == "succeeded" for r in usage_rows),
        "n_errored": sum(r["status"] == "errored" for r in usage_rows),
        "accounting_note": (
            "total_cost_usd from a subscription CLI session is a list-price usage "
            "estimate (costBasis='list'), not an invoice."
        ),
        "usage_estimate_usd_total": round(sum(estimates), 6) if estimates else None,
        "max_tokens_note": (
            "params.max_tokens is advisory on this backend; the CLI uses the model's own "
            "output ceiling."
        ),
        "blocked_env_names": blocked_env_names(),
        "requests": sorted(usage_rows, key=lambda r: r["custom_id"]),
    }
    return results, report


def write_results(
    results: list[dict[str, Any]],
    out_path: Path,
    report: Optional[dict[str, Any]] = None,
) -> Path:
    """Write batch-compatible JSONL atomically; write ``<out>.usage.json`` beside it."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(f".{out_path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result) + "\n")
    temporary.replace(out_path)
    if report is not None:
        usage_path = out_path.with_name(f"{out_path.name}.usage.json")
        usage_tmp = usage_path.with_name(f".{usage_path.name}.tmp")
        usage_tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        usage_tmp.replace(usage_path)
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute prepared GPI requests on the local Claude Code CLI "
            "(Claude subscription; no API-billed calls)."
        )
    )
    parser.add_argument("request_file", help="Prepared anthropic_batch_request.json")
    parser.add_argument("--output", help="Output JSONL path")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Accepted for call-site compatibility; advisory only on this backend.",
    )
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--no-schema",
        action="store_true",
        help="Do not pass the request's JSON Schema to the CLI (diagnostic only).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    request_path = Path(args.request_file)
    if not request_path.exists():
        logger.error("Request file not found: %s", request_path)
        return 1
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    requests = payload.get("requests", []) if isinstance(payload, dict) else []
    output = Path(args.output or request_path.with_name(f"{request_path.stem}_results.jsonl"))
    try:
        results, report = run_requests(
            requests,
            model=args.model,
            concurrency=args.concurrency,
            timeout=args.timeout,
            use_schema=not args.no_schema,
        )
    except (ClaudeCliError, ValueError) as exc:
        logger.error(str(exc))
        return 1
    write_results(results, output, report)
    logger.info(
        "Claude CLI synthesis complete: succeeded=%s errored=%s models=%s "
        "usage_estimate_usd=%s output=%s",
        report["n_succeeded"],
        report["n_errored"],
        report["resolved_models"],
        report["usage_estimate_usd_total"],
        output,
    )
    return 0 if report["n_errored"] == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
