"""Anthropic Batch API submit/poll for the Gene Program Interpreter.

Anthropic-only extraction of ProgExplorer's ``03_submit_and_monitor_batch.py``
(``cmd_submit_anthropic`` / ``cmd_check_anthropic`` / ``cmd_results_anthropic``).
All Vertex / GCS / AI-gateway code paths have been dropped (see
``docs/ARCHITECTURE.md`` DROP list).

Clean importable API
--------------------
- ``submit_batch(requests, *, thinking=None, effort=None) -> str``
- ``check_batch(batch_id) -> dict``
- ``fetch_results(batch_id, out_path) -> Path``

CLI (mirrors the original subcommand behaviour)
-----------------------------------------------
- ``python -m gpi.anthropic_batch submit  <batch_file> [--model M] [--max-tokens N]
      [--thinking adaptive] [--effort low|medium|high|xhigh|max] [--wait]``
- ``python -m gpi.anthropic_batch check   [--batch-id ID | --batch-file F]``
- ``python -m gpi.anthropic_batch results [--batch-id ID | --batch-file F] [--output F]``

The module imports even when ``anthropic`` is not installed; the functions raise
a clear error at call time.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from .progress import emit_step_progress

# Anthropic imports (direct Anthropic Batch API - the only supported backend).
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    anthropic = None  # type: ignore
    ANTHROPIC_AVAILABLE = False

from gpi.log_redaction import install_log_redaction

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
# This module runs as its own subprocess and configures the root logger itself, so it does
# NOT inherit the driver's redaction. httpx logs every request URL at INFO, and the NCBI /
# STRING calls carry api_key and email in the query string — this is where the key actually
# leaked into runs/*.log. Install here, at import, before any record can be emitted.
install_log_redaction()
logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"  # Anthropic model name
DEFAULT_MAX_TOKENS = 8192
POLL_INTERVAL_SECONDS = 30


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _client():
    """Return an ``anthropic.Anthropic`` client using ``ANTHROPIC_API_KEY``.

    Raises a clear error if the ``anthropic`` package is missing or the API key
    is not set in the environment.
    """
    if not ANTHROPIC_AVAILABLE:
        raise RuntimeError(
            "anthropic package not installed. Run: pip install anthropic"
        )
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not found in environment. Set it or add to .env file."
        )
    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def submit_batch(
    requests: list[dict],
    *,
    thinking: Optional[str] = None,
    effort: Optional[str] = None,
) -> str:
    """Submit a list of batch requests to the Anthropic Batch API.

    Each element of ``requests`` is expected to carry ``custom_id`` and
    ``params`` (with at least ``messages``); ``params`` may also carry ``model``,
    ``max_tokens``, ``thinking`` and ``output_config``. Missing ``model`` /
    ``max_tokens`` default to :data:`MODEL` / :data:`DEFAULT_MAX_TOKENS`.

    Args:
        requests: batch requests, each ``{"custom_id", "params": {...}}``.
        thinking: optional thinking-mode override applied to all requests,
            e.g. ``"adaptive"`` becomes ``{"type": "adaptive"}``.
        effort: optional ``output_config.effort`` override applied to all
            requests, e.g. ``"high"``.

    Returns:
        The created batch's id.
    """
    client = _client()

    if not requests:
        raise ValueError("No requests provided to submit_batch.")

    anthropic_requests: list[dict] = []
    for req in requests:
        custom_id = req.get("custom_id", f"request_{len(anthropic_requests)}")
        params = req.get("params", {})
        messages = params.get("messages", [])

        if not messages:
            logger.warning(f"Skipping request {custom_id}: no messages")
            continue

        built = {
            "custom_id": custom_id,
            "params": {
                "model": params.get("model", MODEL),
                "max_tokens": params.get("max_tokens", DEFAULT_MAX_TOKENS),
                "messages": messages,
            },
        }
        # thinking: explicit override wins, else preserve any per-request value.
        if thinking:
            built["params"]["thinking"] = {"type": thinking}
        elif params.get("thinking"):
            built["params"]["thinking"] = params["thinking"]
        # output_config.effort: explicit override merges onto any per-request value.
        output_config = dict(params.get("output_config", {}))
        if effort:
            output_config["effort"] = effort
        if output_config:
            built["params"]["output_config"] = output_config

        anthropic_requests.append(built)

    if not anthropic_requests:
        raise ValueError("No valid requests to submit (all lacked messages).")

    logger.info(f"Submitting {len(anthropic_requests)} requests to Anthropic Batch API...")
    batch = client.messages.batches.create(requests=anthropic_requests)
    logger.info(f"Batch created: {batch.id} (status={batch.processing_status})")
    return batch.id


def check_batch(batch_id: str) -> dict:
    """Retrieve a batch and return its status and per-request counts.

    Returns:
        ``{"processing_status": str, "counts": {"succeeded", "errored",
        "processing", "canceled"}}``.
    """
    client = _client()
    batch = client.messages.batches.retrieve(batch_id)
    counts = batch.request_counts
    return {
        "processing_status": batch.processing_status,
        "counts": {
            "succeeded": counts.succeeded,
            "errored": counts.errored,
            "processing": counts.processing,
            "canceled": counts.canceled,
        },
    }


def fetch_results(batch_id: str, out_path: Path) -> Path:
    """Download results from an ``ended`` batch, writing JSONL to ``out_path``.

    Each line is ``json.dumps(result.model_dump())`` for one batch result.

    Raises:
        RuntimeError: if the batch has not yet ``ended``.

    Returns:
        ``out_path``.
    """
    client = _client()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        raise RuntimeError(
            f"Batch {batch_id} is still processing: {batch.processing_status}"
        )

    out_path = Path(out_path)
    logger.info(f"Downloading results for {batch_id} to {out_path}...")
    with open(out_path, "w", encoding="utf-8") as f:
        for result in client.messages.batches.results(batch_id):
            f.write(json.dumps(result.model_dump()) + "\n")
    logger.info(
        f"Wrote results: succeeded={batch.request_counts.succeeded} "
        f"errored={batch.request_counts.errored}"
    )
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_batch_id(args: argparse.Namespace) -> Optional[str]:
    """Resolve a batch id from ``--batch-id`` or a saved ``.batch_id`` file."""
    batch_id = args.batch_id
    if not batch_id and getattr(args, "batch_file", None):
        batch_id_file = Path(args.batch_file).with_suffix(".batch_id")
        if batch_id_file.exists():
            batch_id = batch_id_file.read_text(encoding="utf-8").strip()
    return batch_id


def _chunk_requests(requests: list[dict], batch_size: Optional[int]) -> list[list[dict]]:
    """Split requests into deterministic API batches, preserving input order."""
    if batch_size is None:
        return [requests]
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    return [requests[index:index + batch_size] for index in range(0, len(requests), batch_size)]


def _save_batch_ids(batch_path: Path, batch_ids: list[str], chunk_sizes: list[int]) -> Path:
    """Persist every submitted batch id so chunked jobs remain auditable/recoverable."""
    output = batch_path.with_suffix(".batch_ids.json")
    output.write_text(
        json.dumps(
            {
                "source": str(batch_path),
                "batch_ids": batch_ids,
                "chunk_sizes": chunk_sizes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("Saved %d batch ID(s) to %s", len(batch_ids), output)
    return output


def _combined_counts(statuses: list[dict]) -> dict[str, int]:
    keys = ("succeeded", "errored", "processing", "canceled")
    return {
        key: sum(int(status["counts"].get(key, 0)) for status in statuses)
        for key in keys
    }


def _fetch_chunked_results(batch_path: Path, batch_ids: list[str]) -> Path:
    """Fetch each chunk and concatenate JSONL into the pipeline's canonical result path."""
    output = batch_path.with_name(f"{batch_path.stem}_results.jsonl")
    part_paths: list[Path] = []
    for index, batch_id in enumerate(batch_ids, start=1):
        part_path = batch_path.with_name(
            f"{batch_path.stem}_part{index:02d}_results.jsonl"
        )
        part_paths.append(fetch_results(batch_id, part_path))

    with output.open("w", encoding="utf-8") as combined:
        for part_path in part_paths:
            with part_path.open("r", encoding="utf-8") as part:
                for line in part:
                    combined.write(line)
    logger.info("Merged %d batch result file(s) into %s", len(part_paths), output)
    return output


def cmd_submit(args: argparse.Namespace) -> int:
    """Submit a prepared batch JSON file to the Anthropic Batch API."""
    if not args.batch_file:
        logger.error("batch_file is required.")
        return 1

    batch_path = Path(args.batch_file)
    if not batch_path.exists():
        logger.error(f"Batch file not found: {batch_path}")
        return 1

    with open(batch_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    requests_list = data.get("requests", [])
    if not requests_list:
        logger.error("No requests found in batch file.")
        return 1

    logger.info(f"Loaded {len(requests_list)} requests from {batch_path}")

    # Inject CLI model / max_tokens defaults into any request missing them,
    # so submit_batch's per-request defaulting sees the CLI-chosen values.
    for req in requests_list:
        params = req.setdefault("params", {})
        params.setdefault("model", args.model)
        params.setdefault("max_tokens", args.max_tokens)

    try:
        request_chunks = _chunk_requests(requests_list, args.batch_size)
        batch_ids = [
            submit_batch(chunk, thinking=args.thinking, effort=args.effort)
            for chunk in request_chunks
        ]
    except (RuntimeError, ValueError) as exc:
        logger.error(str(exc))
        return 1

    print(f"\nCreated {len(batch_ids)} batch job(s)!")
    for index, (batch_id, chunk) in enumerate(
        zip(batch_ids, request_chunks), start=1
    ):
        print(f"  Part {index}: {batch_id} ({len(chunk)} requests)")

    # Keep the legacy single-id sidecar for unchunked runs and a complete JSON
    # sidecar for both single and chunked runs.
    if len(batch_ids) == 1:
        batch_id_file = batch_path.with_suffix(".batch_id")
        batch_id_file.write_text(batch_ids[0], encoding="utf-8")
        logger.info("Saved batch ID to %s", batch_id_file)
    batch_ids_file = _save_batch_ids(
        batch_path, batch_ids, [len(chunk) for chunk in request_chunks]
    )

    if args.wait:
        emit_step_progress(0, len(requests_list), "submitted")
        print(
            f"\nWaiting for {len(batch_ids)} batch job(s) "
            f"(checking every {POLL_INTERVAL_SECONDS}s)..."
        )
        statuses = [check_batch(batch_id) for batch_id in batch_ids]
        while any(status["processing_status"] == "in_progress" for status in statuses):
            time.sleep(POLL_INTERVAL_SECONDS)
            statuses = [check_batch(batch_id) for batch_id in batch_ids]
            counts = _combined_counts(statuses)
            done = counts["succeeded"] + counts["errored"] + counts["canceled"]
            print(
                f"  Jobs ended: "
                f"{sum(s['processing_status'] == 'ended' for s in statuses)}/{len(statuses)} "
                f"| Requests completed: {done}/{len(requests_list)}"
            )
            emit_step_progress(done, len(requests_list), "processing")

        final_states = [status["processing_status"] for status in statuses]
        print(f"\nFinal job states: {final_states}")
        if all(state == "ended" for state in final_states):
            emit_step_progress(len(requests_list), len(requests_list), "fetching results")
            output_file = _fetch_chunked_results(batch_path, batch_ids)
            counts = _combined_counts(statuses)
            print(f"SUCCESS! Results saved to: {output_file}")
            print(f"  Succeeded: {counts['succeeded']}")
            print(f"  Errored: {counts['errored']}")
        else:
            print(f"One or more batch jobs did not complete; IDs: {batch_ids_file}")
            return 3
    else:
        print(f"\nBatch IDs saved to: {batch_ids_file}")
        print("Use the check/results subcommands with each saved batch ID.")

    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Check the status of an Anthropic batch job."""
    batch_id = _resolve_batch_id(args)
    if not batch_id:
        logger.error("--batch-id is required (or provide --batch-file with saved .batch_id)")
        return 1

    try:
        status = check_batch(batch_id)
    except RuntimeError as exc:
        logger.error(str(exc))
        return 1

    c = status["counts"]
    print(f"Batch ID: {batch_id}")
    print(f"Status: {status['processing_status']}")
    print("Requests:")
    print(f"  Processing: {c['processing']}")
    print(f"  Succeeded: {c['succeeded']}")
    print(f"  Errored: {c['errored']}")
    print(f"  Canceled: {c['canceled']}")

    if status["processing_status"] == "ended":
        print("\nBatch completed! Retrieve results with:")
        print(f"  python -m gpi.anthropic_batch results --batch-id {batch_id}")

    return 0


def cmd_results(args: argparse.Namespace) -> int:
    """Download results from a completed Anthropic batch job."""
    batch_id = _resolve_batch_id(args)
    if not batch_id:
        logger.error("--batch-id is required (or provide --batch-file with saved .batch_id)")
        return 1

    output_file = Path(args.output or f"batch_{batch_id}_results.jsonl")

    try:
        fetch_results(batch_id, output_file)
    except RuntimeError as exc:
        logger.error(str(exc))
        return 1

    print(f"Results saved to: {output_file}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Submit/poll Anthropic Batch API jobs for the Gene Program Interpreter.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_submit = subparsers.add_parser(
        "submit", help="Submit batch to Anthropic Batch API"
    )
    p_submit.add_argument(
        "batch_file", nargs="?", help="Path to the prepared batch JSON file"
    )
    p_submit.add_argument(
        "--model", default=MODEL, help=f"Model to use (default: {MODEL})"
    )
    p_submit.add_argument(
        "--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
        help=f"Max tokens for response (default: {DEFAULT_MAX_TOKENS})",
    )
    p_submit.add_argument(
        "--thinking", choices=["adaptive"],
        help="Claude thinking mode override, e.g. adaptive",
    )
    p_submit.add_argument(
        "--effort", choices=["low", "medium", "high", "xhigh", "max"],
        help="Claude output_config.effort override",
    )
    p_submit.add_argument(
        "--wait", action="store_true",
        help="Wait for job completion and download results",
    )
    p_submit.add_argument(
        "--batch-size", type=int,
        help="Maximum requests per Anthropic batch job (default: submit one job)",
    )
    p_submit.set_defaults(func=cmd_submit)

    p_check = subparsers.add_parser(
        "check", help="Check status of Anthropic batch job"
    )
    p_check.add_argument("--batch-id", help="Anthropic batch ID")
    p_check.add_argument(
        "--batch-file", help="Path to batch JSON (will read .batch_id file)"
    )
    p_check.set_defaults(func=cmd_check)

    p_results = subparsers.add_parser(
        "results", help="Download results from completed Anthropic batch"
    )
    p_results.add_argument("--batch-id", help="Anthropic batch ID")
    p_results.add_argument(
        "--batch-file", help="Path to batch JSON (will read .batch_id file)"
    )
    p_results.add_argument("--output", help="Output JSONL file path")
    p_results.set_defaults(func=cmd_results)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
