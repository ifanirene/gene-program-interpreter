"""Run annotation requests through Anthropic's live Messages API.

The evidence-context step writes the same request envelope used by the Message
Batches API.  This module executes those requests directly, with bounded
concurrency, and writes batch-compatible JSONL so the existing parser and
reporting steps do not need a second response format.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

from .log_redaction import install_log_redaction
from .progress import emit_step_progress

try:
    import anthropic

    ANTHROPIC_AVAILABLE = True
except ImportError:
    anthropic = None  # type: ignore
    ANTHROPIC_AVAILABLE = False


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
install_log_redaction()
logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_CONCURRENCY = 4


def _client():
    """Return an AsyncAnthropic client using ``ANTHROPIC_API_KEY``."""
    if not ANTHROPIC_AVAILABLE:
        raise RuntimeError("anthropic package not installed. Run: pip install anthropic")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not found in environment. Set it or add it to the run .env file."
        )
    return anthropic.AsyncAnthropic(api_key=api_key)


def _request_params(
    request: dict[str, Any],
    *,
    model: str,
    max_tokens: int,
    thinking: Optional[str],
    effort: Optional[str],
) -> dict[str, Any]:
    params = dict(request.get("params", {}))
    if not params.get("messages"):
        raise ValueError("request has no messages")
    params.setdefault("model", model)
    params.setdefault("max_tokens", max_tokens)
    if thinking:
        params["thinking"] = {"type": thinking}
    if effort:
        output_config = dict(params.get("output_config", {}))
        output_config["effort"] = effort
        params["output_config"] = output_config
    return params


async def _execute_requests(
    requests: list[dict[str, Any]],
    *,
    client: Any,
    concurrency: int,
    model: str,
    max_tokens: int,
    thinking: Optional[str],
    effort: Optional[str],
) -> list[dict[str, Any]]:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    semaphore = asyncio.Semaphore(concurrency)
    completed = 0
    total = len(requests)
    emit_step_progress(0, total, "live requests")

    async def execute(index: int, request: dict[str, Any]) -> dict[str, Any]:
        nonlocal completed
        custom_id = str(request.get("custom_id") or f"request_{index}")
        try:
            params = _request_params(
                request,
                model=model,
                max_tokens=max_tokens,
                thinking=thinking,
                effort=effort,
            )
            async with semaphore:
                message = await client.messages.create(**params)
            result: dict[str, Any] = {
                "custom_id": custom_id,
                "result": {"type": "succeeded", "message": message.model_dump()},
            }
        except Exception as exc:
            logger.error("Live annotation request %s failed: %s", custom_id, exc)
            result = {
                "custom_id": custom_id,
                "result": {
                    "type": "errored",
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                },
            }
        completed += 1
        emit_step_progress(completed, total, "live requests")
        return result

    return await asyncio.gather(
        *(execute(index, request) for index, request in enumerate(requests))
    )


def run_live_requests(
    requests: list[dict[str, Any]],
    out_path: Path,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    model: str = MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    thinking: Optional[str] = None,
    effort: Optional[str] = None,
    client: Any = None,
) -> dict[str, int]:
    """Execute requests live and write batch-compatible JSONL results."""
    if not requests:
        raise ValueError("No requests provided for live annotation.")
    live_client = client if client is not None else _client()
    results = asyncio.run(
        _execute_requests(
            requests,
            client=live_client,
            concurrency=concurrency,
            model=model,
            max_tokens=max_tokens,
            thinking=thinking,
            effort=effort,
        )
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(f".{out_path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result) + "\n")
    temporary.replace(out_path)

    succeeded = sum(r["result"]["type"] == "succeeded" for r in results)
    errored = len(results) - succeeded
    logger.info(
        "Live annotation complete: succeeded=%s errored=%s output=%s",
        succeeded,
        errored,
        out_path,
    )
    return {"succeeded": succeeded, "errored": errored}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute prepared GPI annotation requests through the live Anthropic API."
    )
    parser.add_argument("request_file", help="Prepared anthropic_batch_request.json")
    parser.add_argument("--output", help="Output JSONL path")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--thinking", choices=["adaptive"])
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    request_path = Path(args.request_file)
    if not request_path.exists():
        logger.error("Request file not found: %s", request_path)
        return 1
    with request_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    requests = payload.get("requests", []) if isinstance(payload, dict) else []
    output = Path(
        args.output or request_path.with_name(f"{request_path.stem}_results.jsonl")
    )
    try:
        counts = run_live_requests(
            requests,
            output,
            concurrency=args.concurrency,
            model=args.model,
            max_tokens=args.max_tokens,
            thinking=args.thinking,
            effort=args.effort,
        )
    except (RuntimeError, ValueError) as exc:
        logger.error(str(exc))
        return 1
    return 0 if counts["errored"] == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
