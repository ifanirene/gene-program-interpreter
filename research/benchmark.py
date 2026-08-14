"""Reproducible literature benchmark tooling.

The benchmark is deliberately separate from production research behavior. ``prepare`` is
offline and only regenerates bundles. ``run`` is the sole paid command and requires an exact
approval record before it launches the production Agent-SDK handler. The remaining commands
materialize assessor text, build blinded review packets, score adjudications, compare variants,
and hash-lock a complete baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import requests
import yaml

from gpi.context_profile import ContextProfile
from research.bundle import build_all_bundles, build_bundle
from research.literature import LiteratureClient, MAX_FETCH_IDS, normalize_doi, normalize_pmid

SCHEMA_VERSION = 1
REVIEW_SCHEMA_VERSION = 1
DEFAULT_MANIFEST = Path("benchmarks/literature-v1/manifest.yaml")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REPO_ROOT = Path(__file__).resolve().parent.parent


class BenchmarkError(ValueError):
    """A benchmark contract or artifact is invalid."""


def _subscription_auth_status() -> dict[str, Any]:
    """Return a secret-free Claude subscription auth status from the SDK's own CLI."""
    try:
        import claude_agent_sdk
    except ImportError as exc:  # pragma: no cover - normal dependency validation catches this
        raise BenchmarkError("claude-agent-sdk is unavailable") from exc

    cli_name = "claude.exe" if platform.system() == "Windows" else "claude"
    bundled = Path(claude_agent_sdk.__file__).resolve().parent / "_bundled" / cli_name
    cli = bundled if bundled.is_file() else Path(shutil.which("claude") or "")
    if not cli.is_file():
        raise BenchmarkError("Claude Code CLI is unavailable for subscription auth preflight")

    # A subscription run must prove OAuth login without accidentally falling back to an API key.
    env = {key: value for key, value in os.environ.items() if key != "ANTHROPIC_API_KEY"}
    try:
        completed = subprocess.run(
            [str(cli), "auth", "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BenchmarkError(f"Claude subscription auth preflight failed: {exc}") from exc
    try:
        status = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise BenchmarkError("Claude subscription auth preflight returned invalid JSON") from exc
    return {
        "logged_in": bool(status.get("loggedIn")),
        "auth_method": str(status.get("authMethod") or "none"),
        "api_provider": str(status.get("apiProvider") or "unknown"),
        "command_exit_code": completed.returncode,
    }


def _require_subscription_auth() -> dict[str, Any]:
    """Fail before ledger creation/fan-out unless Claude subscription OAuth is usable."""
    status = _subscription_auth_status()
    if status["command_exit_code"] != 0 or not status["logged_in"]:
        raise BenchmarkError(
            "Claude subscription is not authenticated; run `claude auth login --claudeai` "
            "and retry only after `claude auth status --json` reports loggedIn=true"
        )
    return status


def _json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _json_dump_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _portable_path(root: Path, value: Any, *, field: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise BenchmarkError(f"{field} is required")
    rel = Path(raw)
    if rel.is_absolute():
        raise BenchmarkError(f"{field} must be relative to the manifest: {raw}")
    root = root.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BenchmarkError(f"{field} escapes the benchmark directory: {raw}") from exc
    return path


def _input_paths(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _input_paths(child)
    elif isinstance(value, list):
        for child in value:
            yield from _input_paths(child)


def _case_expected(case: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    expected = case.get("expected") or {}
    loading = expected.get("program_genes") or []
    distinctive = expected.get("distinctive_genes") or []
    if not all(isinstance(g, str) and g.strip() for g in [*loading, *distinctive]):
        raise BenchmarkError(f"{case.get('case_id')}: expected genes must be non-empty strings")
    if len(loading) != 15 or len(distinctive) != 8:
        raise BenchmarkError(
            f"{case.get('case_id')}: expected exactly 15 program and 8 distinctive genes"
        )
    if len(set(loading)) != 15 or len(set(distinctive)) != 8:
        raise BenchmarkError(f"{case.get('case_id')}: expected gene lists contain duplicates")
    overlap = sorted(set(loading) & set(distinctive))
    if overlap:
        raise BenchmarkError(f"{case.get('case_id')}: gene lists overlap: {overlap}")
    return list(loading), list(distinctive)


def load_manifest(path: str | Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    """Load and fully validate a portable benchmark manifest, including every file hash."""
    manifest_path = Path(path).resolve()
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError(f"manifest schema_version must be {SCHEMA_VERSION}")
    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases:
        raise BenchmarkError("manifest cases must be a non-empty list")

    root = manifest_path.parent
    case_ids: set[str] = set()
    referenced: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise BenchmarkError("each manifest case must be a mapping")
        case_id = str(case.get("case_id") or "").strip()
        if not case_id or case_id in case_ids:
            raise BenchmarkError(f"case_id is missing or duplicated: {case_id!r}")
        case_ids.add(case_id)
        program_id = str(case.get("program_id") or "")
        if not re.fullmatch(r"P\d+", program_id):
            raise BenchmarkError(f"{case_id}: program_id must look like P10")
        repeat_count = case.get("repeat_count", 1)
        if not isinstance(repeat_count, int) or repeat_count < 1:
            raise BenchmarkError(f"{case_id}: repeat_count must be a positive integer")
        _case_expected(case)

        hashes = case.get("hashes") or {}
        if not isinstance(hashes, dict) or not hashes:
            raise BenchmarkError(f"{case_id}: hashes are required")
        case_paths = [str(case.get("profile_path") or ""), *_input_paths(case.get("inputs") or {})]
        for rel in case_paths:
            path_obj = _portable_path(root, rel, field=f"{case_id} path")
            if not path_obj.is_file():
                raise BenchmarkError(f"{case_id}: portable input is missing: {rel}")
            referenced.add(rel)
            wanted = str(hashes.get(rel) or "")
            if not _SHA256_RE.fullmatch(wanted):
                raise BenchmarkError(f"{case_id}: missing/invalid SHA-256 for {rel}")
            actual = sha256_file(path_obj)
            if actual != wanted:
                raise BenchmarkError(
                    f"{case_id}: SHA-256 mismatch for {rel}: expected {wanted}, got {actual}"
                )

    sentinels = raw.get("sentinel_case_ids") or []
    if len(set(sentinels)) != len(sentinels) or not set(sentinels).issubset(case_ids):
        raise BenchmarkError("sentinel_case_ids must be unique manifest case IDs")
    by_id = {case["case_id"]: case for case in cases}
    for case_id in sentinels:
        if by_id[case_id].get("repeat_count", 1) < 2:
            raise BenchmarkError(f"sentinel {case_id} must have repeat_count >= 2")

    sessions = sum(case.get("repeat_count", 1) for case in cases)
    budget = raw.get("budget") or {}
    attempts = budget.get("max_attempts")
    per_attempt = budget.get("max_budget_usd_per_attempt")
    if budget.get("sessions") != sessions:
        raise BenchmarkError(f"budget.sessions must equal repeat total ({sessions})")
    if not isinstance(attempts, int) or attempts < 1:
        raise BenchmarkError("budget.max_attempts must be a positive integer")
    if not isinstance(per_attempt, (int, float)) or per_attempt <= 0:
        raise BenchmarkError("budget.max_budget_usd_per_attempt must be positive")
    ceiling = sessions * attempts * float(per_attempt)
    if not math.isclose(float(budget.get("max_api_equivalent_usd", -1)), ceiling):
        raise BenchmarkError(f"budget ceiling must be {ceiling:.2f}")

    execution = raw.get("execution") or {}
    if execution.get("auth") not in {"subscription", "api"}:
        raise BenchmarkError("execution.auth must be 'subscription' or 'api'")
    if not str(execution.get("model") or "").strip():
        raise BenchmarkError("execution.model is required")
    max_turns = execution.get("max_turns")
    concurrency = execution.get("concurrency")
    timeout = execution.get("per_program_timeout_seconds")
    execution_budget = execution.get("max_budget_usd")
    execution_attempts = execution.get("max_attempts")
    if max_turns is not None and (
        isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns <= 0
    ):
        raise BenchmarkError("execution.max_turns must be a positive integer or null")
    if not isinstance(concurrency, int) or not 1 <= concurrency <= 16:
        raise BenchmarkError("execution.concurrency must be an integer in [1,16]")
    for field, value in (
        ("execution.max_budget_usd", execution_budget),
        ("execution.per_program_timeout_seconds", timeout),
    ):
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
            raise BenchmarkError(f"{field} must be positive and finite")
    if execution_attempts != attempts:
        raise BenchmarkError("execution.max_attempts must equal budget.max_attempts")
    if not math.isclose(float(execution_budget), float(per_attempt)):
        raise BenchmarkError(
            "execution.max_budget_usd must equal budget.max_budget_usd_per_attempt"
        )

    raw["_manifest_path"] = str(manifest_path)
    raw["_manifest_sha256"] = sha256_file(manifest_path)
    raw["_referenced_paths"] = sorted(referenced)
    return raw


def _case_bundle(manifest: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    import pandas as pd

    root = Path(str(manifest["_manifest_path"])).parent
    inputs = case["inputs"]
    gene_path = _portable_path(root, inputs["gene_loading"], field="inputs.gene_loading")
    frame = pd.read_csv(gene_path)
    if "program_id" not in frame.columns and "RowID" in frame.columns:
        frame = frame.rename(columns={"RowID": "program_id"})
    profile = ContextProfile.from_yaml(
        _portable_path(root, case["profile_path"], field="profile_path")
    )
    context_path = inputs.get("ncbi_context")
    context = None
    if context_path:
        context = json.loads(
            _portable_path(root, context_path, field="inputs.ncbi_context").read_text(
                encoding="utf-8"
            )
        )
    return build_bundle(
        case["program_id"],
        frame,
        profile,
        ncbi_context=context,
        top_loading=15,
        top_unique=8,
    )


def _validate_bundle(case: Mapping[str, Any], bundle: Mapping[str, Any]) -> None:
    loading, distinctive = _case_expected(case)
    if bundle.get("program_id") != case.get("program_id"):
        raise BenchmarkError(f"{case['case_id']}: generated bundle program_id changed")
    if bundle.get("program_genes") != loading:
        raise BenchmarkError(f"{case['case_id']}: program_genes differ from the frozen manifest")
    if bundle.get("distinctive_genes") != distinctive:
        raise BenchmarkError(
            f"{case['case_id']}: distinctive_genes differ from the frozen manifest"
        )


def _sessions(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        for repeat in range(1, int(case.get("repeat_count", 1)) + 1):
            session_id = f"{case['case_id']}--r{repeat}"
            sessions.append(
                {
                    "session_id": session_id,
                    "case_id": case["case_id"],
                    "experiment": case["experiment"],
                    "program_id": case["program_id"],
                    "repeat": repeat,
                    "sentinel": case["case_id"] in manifest.get("sentinel_case_ids", []),
                }
            )
    return sessions


def _git_metadata() -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=_REPO_ROOT, check=False, capture_output=True, text=True
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    status = run("status", "--porcelain=v1", "--untracked-files=all")
    return {
        "head": run("rev-parse", "HEAD") or None,
        "branch": run("branch", "--show-current") or None,
        "dirty": bool(status),
        "dirty_paths": [line[3:] for line in status.splitlines() if len(line) > 3],
    }


def _source_hashes() -> dict[str, str]:
    paths = [
        "research/bundle.py",
        "research/protocol.md",
        "research/schema.py",
        "research/literature.py",
        "research/research_parallel.py",
        "research/verify.py",
        "research/benchmark.py",
        "gpi/run_pipeline.py",
    ]
    return {rel: sha256_file(_REPO_ROOT / rel) for rel in paths}


def prepare(
    manifest_path: str | Path,
    out: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate the cohort and regenerate isolated bundles.  This is strictly offline."""
    manifest = load_manifest(manifest_path)
    sessions = _sessions(manifest)
    for case in manifest["cases"]:
        _validate_bundle(case, _case_bundle(manifest, case))

    budget = manifest["budget"]
    summary: dict[str, Any] = {
        "benchmark_id": manifest.get("benchmark_id"),
        "manifest_sha256": manifest["_manifest_sha256"],
        "n_cases": len(manifest["cases"]),
        "case_ids": [case["case_id"] for case in manifest["cases"]],
        "sentinel_case_ids": list(manifest.get("sentinel_case_ids", [])),
        "n_sessions": len(sessions),
        "max_api_equivalent_usd": float(budget["max_api_equivalent_usd"]),
        "approval_required": True,
        "dry_run": dry_run,
    }
    if dry_run:
        return summary

    out_path = Path(out).resolve()
    if out_path.exists() and any(out_path.iterdir()):
        raise BenchmarkError(f"refusing to overwrite non-empty benchmark directory: {out_path}")
    out_path.mkdir(parents=True, exist_ok=True)
    case_by_id = {case["case_id"]: case for case in manifest["cases"]}
    root = Path(str(manifest["_manifest_path"])).parent

    for session in sessions:
        case = case_by_id[session["case_id"]]
        session_root = out_path / "sessions" / session["session_id"]
        bundle_dir = session_root / "program_bundles"
        inputs = case["inputs"]
        profile = ContextProfile.from_yaml(
            _portable_path(root, case["profile_path"], field="profile_path")
        )
        written = build_all_bundles(
            _portable_path(root, inputs["gene_loading"], field="inputs.gene_loading"),
            profile,
            ncbi_context_json=(
                _portable_path(root, inputs["ncbi_context"], field="inputs.ncbi_context")
                if inputs.get("ncbi_context")
                else None
            ),
            out_dir=bundle_dir,
            program_ids=[case["program_id"]],
            top_loading=15,
            top_unique=8,
        )
        bundle = json.loads(written[0].read_text(encoding="utf-8"))
        _validate_bundle(case, bundle)
        for name in ("research_results", "research_audit"):
            (session_root / name).mkdir(parents=True, exist_ok=True)
        session["bundle"] = str(written[0].relative_to(out_path))
        session["result_dir"] = str((session_root / "research_results").relative_to(out_path))
        session["audit_dir"] = str((session_root / "research_audit").relative_to(out_path))
        session["context"] = {
            "organism": profile.organism,
            "species_taxid": profile.species_taxid,
            "tissue": profile.tissue,
            "cell_type": profile.cell_type,
            "conditions": list(profile.conditions),
        }

    execution = dict(manifest.get("execution") or {})
    run_plan = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": manifest.get("benchmark_id"),
        "manifest_sha256": manifest["_manifest_sha256"],
        "approval": {
            "status": "required",
            "max_api_equivalent_usd": float(budget["max_api_equivalent_usd"]),
            "record_approval_before_launch": True,
        },
        "execution": execution,
        "sessions": sessions,
        "notes": [
            "Launch each session in its own directory; repeated sentinels must never overwrite.",
            "With subscription auth, withhold ANTHROPIC_API_KEY from the Agent-SDK process.",
            "Run the first session as a serial canary before concurrent fan-out.",
            "Persist immutable per-attempt artifacts and cumulative telemetry for every retry.",
        ],
    }
    environment = {
        "git": _git_metadata(),
        "python": sys.version,
        "platform": platform.platform(),
        "max_fetch_ids": MAX_FETCH_IDS,
        "source_hashes": _source_hashes(),
        "environment_keys_present": sorted(
            key
            for key in ("ANTHROPIC_API_KEY", "NCBI_API_KEY", "OPENALEX_API_KEY")
            if os.getenv(key)
        ),
    }
    # Never persist values of environment variables or other credentials.
    shutil.copy2(Path(str(manifest["_manifest_path"])), out_path / "manifest.snapshot.yaml")
    _json_dump(out_path / "run_plan.json", run_plan)
    _json_dump(out_path / "environment.json", environment)
    hashes = {
        str(path.relative_to(out_path)): sha256_file(path)
        for path in sorted(out_path.rglob("*"))
        if path.is_file() and path.name != "artifact_hashes.json"
    }
    _json_dump(out_path / "artifact_hashes.json", hashes)
    summary["out"] = str(out_path)
    return summary


def _validate_approval(run_dir: Path, approval_path: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    if not approval_path.is_file():
        raise BenchmarkError("an explicit approval JSON file is required before paid execution")
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    expected = float(plan.get("approval", {}).get("max_api_equivalent_usd", -1))
    if approval.get("approved") is not True:
        raise BenchmarkError("paid baseline approval must set approved=true")
    if approval.get("benchmark_id") != plan.get("benchmark_id"):
        raise BenchmarkError("approval benchmark_id does not match the run plan")
    if not math.isclose(float(approval.get("max_api_equivalent_usd", -1)), expected):
        raise BenchmarkError(f"approval must cover the exact ${expected:.2f} ceiling")
    if not str(approval.get("approved_by") or "").strip() or not str(
        approval.get("approved_at") or ""
    ).strip():
        raise BenchmarkError("approval must record approved_by and approved_at")
    # The path is accepted only as evidence; never copy arbitrary extra fields or secrets.
    return {
        "approved": True,
        "benchmark_id": approval["benchmark_id"],
        "max_api_equivalent_usd": expected,
        "approved_by": approval["approved_by"],
        "approved_at": approval["approved_at"],
        "approval_file_sha256": sha256_file(approval_path),
        "run_dir": str(run_dir),
    }


def _validate_prepared_hashes(run_dir: Path) -> None:
    hashes_path = run_dir / "artifact_hashes.json"
    if not hashes_path.is_file():
        raise BenchmarkError("prepared artifact hash manifest is missing")
    hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
    for rel, wanted in hashes.items():
        path = _portable_path(run_dir, rel, field="prepared artifact")
        if not path.is_file() or sha256_file(path) != wanted:
            raise BenchmarkError(f"prepared artifact changed before launch: {rel}")
    environment_path = run_dir / "environment.json"
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    if environment.get("source_hashes") != _source_hashes():
        raise BenchmarkError("source code changed after prepare; regenerate the baseline")


def _is_deterministic_limit_failure(audit: Mapping[str, Any]) -> bool:
    """Return true when repeating an attempt with identical limits cannot help."""
    terminal = " ".join(
        str(audit.get(field) or "").casefold()
        for field in ("error", "subtype", "stop_reason")
    )
    turn_limited = "max_turns" in terminal or "max turns" in terminal
    budget_limited = "budget" in terminal and any(
        marker in terminal for marker in ("exceed", "exhaust", "limit", "reach")
    )
    return turn_limited or budget_limited


async def _execute_paid_sessions(
    run_dir: Path,
    plan: Mapping[str, Any],
    sessions: Sequence[Mapping[str, Any]],
    ledger: dict[str, Any],
    ledger_path: Path,
) -> None:
    # Private production seam is intentional: it accepts a shared LiteratureClient (global rate
    # limiting) and a session-specific output directory (sentinel repeats cannot overwrite).
    from research.research_parallel import _handle_one_program, load_env_file, read_protocol

    execution = plan["execution"]
    concurrency = int(execution.get("concurrency", 3))
    if not 1 <= concurrency <= 16:
        raise BenchmarkError("execution.concurrency must be in [1,16]")
    sem = asyncio.Semaphore(concurrency)
    load_env_file()
    client = LiteratureClient()
    protocol = read_protocol()

    async def one(session: Mapping[str, Any]) -> bool:
        session_root = run_dir / "sessions" / session["session_id"]
        bundle_path = run_dir / session["bundle"]
        result_dir = run_dir / session["result_dir"]
        audit_dir = run_dir / session["audit_dir"]
        program_id = session["program_id"]
        async with sem:
            entry = ledger["sessions"][session["session_id"]]
            entry["status"] = "started"
            _json_dump_atomic(ledger_path, ledger)
            try:
                attempt_records: list[dict[str, Any]] = []
                max_attempts = int(execution["max_attempts"])
                for attempt in range(1, max_attempts + 1):
                    if attempt > 1:
                        # Prior bytes already live under audit/attempts/attempt-N. Clear the
                        # canonical paths so a transport crash cannot masquerade as this attempt.
                        for stale in (
                            audit_dir / f"{program_id}.audit.json",
                            result_dir / f"{program_id}.json",
                            audit_dir / f"{program_id}.raw_payload.json",
                        ):
                            stale.unlink(missing_ok=True)
                    entry["current_attempt"] = attempt
                    entry["attempt_records"] = attempt_records
                    _json_dump_atomic(ledger_path, ledger)
                    # Preserve production behavior inside each session, but make retries a
                    # benchmark concern so every paid attempt is durable before another begins.
                    await _handle_one_program(
                        bundle_path,
                        out_dir=result_dir,
                        audit_dir=audit_dir,
                        workspaces_root=session_root / "workspaces",
                        system_prompt=protocol,
                        model=str(execution["model"]),
                        max_turns=(
                            int(execution["max_turns"])
                            if execution.get("max_turns") is not None
                            else None
                        ),
                        max_budget_usd=float(execution["max_budget_usd"]),
                        per_program_timeout=float(execution["per_program_timeout_seconds"]),
                        literature_client=client,
                        max_attempts=1,
                    )
                    audit_path = audit_dir / f"{program_id}.audit.json"
                    result_path = result_dir / f"{program_id}.json"
                    audit = (
                        json.loads(audit_path.read_text(encoding="utf-8"))
                        if audit_path.is_file()
                        else {}
                    )
                    record = {
                        "attempt": attempt,
                        "status": audit.get("status"),
                        "error": audit.get("error"),
                        "cost_usd": audit.get("cost_usd"),
                        "num_turns": audit.get("num_turns"),
                        "duration_ms": audit.get("duration_ms"),
                        "tokens": audit.get("tokens"),
                        "usage": audit.get("usage"),
                        "subtype": audit.get("subtype"),
                        "is_error": audit.get("is_error"),
                        "stop_reason": audit.get("stop_reason"),
                        "tool_trace": audit.get("tool_trace") or [],
                        "audit_sha256": sha256_file(audit_path)
                        if audit_path.is_file()
                        else None,
                        "result_sha256": sha256_file(result_path)
                        if result_path.is_file()
                        else None,
                    }
                    attempt_records.append(record)
                    attempt_dir = audit_dir / "attempts" / f"attempt-{attempt}"
                    attempt_dir.mkdir(parents=True, exist_ok=True)
                    if audit_path.is_file():
                        shutil.copy2(audit_path, attempt_dir / "audit.json")
                    if result_path.is_file():
                        shutil.copy2(result_path, attempt_dir / "result.json")
                    raw_path = audit_dir / f"{program_id}.raw_payload.json"
                    if raw_path.is_file():
                        shutil.copy2(raw_path, attempt_dir / "raw_payload.json")
                    entry["attempt_records"] = attempt_records
                    _json_dump_atomic(ledger_path, ledger)
                    if audit.get("status") == "ok" or _is_deterministic_limit_failure(audit):
                        break

                audit_path = audit_dir / f"{program_id}.audit.json"
                audit = (
                    json.loads(audit_path.read_text(encoding="utf-8"))
                    if audit_path.is_file()
                    else {}
                )
                numeric_fields = ("cost_usd", "num_turns", "duration_ms")
                for field in numeric_fields:
                    values = [record.get(field) for record in attempt_records]
                    audit[field] = sum(values) if values and all(
                        isinstance(value, (int, float)) for value in values
                    ) else None
                token_records = [record.get("tokens") for record in attempt_records]
                if token_records and all(isinstance(value, dict) for value in token_records):
                    token_keys = set().union(*(value.keys() for value in token_records))
                    audit["tokens"] = {
                        key: sum(int(value.get(key) or 0) for value in token_records)
                        for key in sorted(token_keys)
                    }
                audit["tool_trace"] = [
                    {"attempt": record["attempt"], **trace}
                    for record in attempt_records
                    for trace in record["tool_trace"]
                ]
                audit["attempts"] = len(attempt_records)
                audit["attempt_records"] = attempt_records
                _json_dump_atomic(audit_path, audit)

                result_path = result_dir / f"{program_id}.json"
                if result_path.is_file():
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    meta = result.setdefault("meta", {})
                    meta.update(
                        {
                            "attempts": len(attempt_records),
                            "cost_usd": audit.get("cost_usd"),
                            "num_turns": audit.get("num_turns"),
                            "duration_ms": audit.get("duration_ms"),
                            "tokens": audit.get("tokens"),
                        }
                    )
                    _json_dump_atomic(result_path, result)
                entry.update(
                    {
                        "status": "research_complete",
                        "audit_status": audit.get("status"),
                        "attempts": audit.get("attempts"),
                        "recorded_cost_usd": audit.get("cost_usd"),
                        "audit_sha256": sha256_file(audit_path) if audit_path.is_file() else None,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - ledger must survive any paid-session failure
                entry.update(
                    {
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                        "spend_may_be_unrecorded": True,
                    }
                )
            finally:
                _json_dump_atomic(ledger_path, ledger)
            return entry.get("audit_status") == "ok"

    try:
        pending_by_id = {session["session_id"]: session for session in sessions}
        canary_id = str(ledger.get("canary_session_id") or "")
        canary_entry = ledger["sessions"].get(canary_id) if canary_id else None
        if not isinstance(canary_entry, dict):
            raise BenchmarkError("paid ledger is missing its deterministic canary session")

        if canary_entry.get("status") == "pending":
            canary = pending_by_id.get(canary_id)
            if canary is None:
                raise BenchmarkError("pending canary is missing from the execution cohort")
            ledger["fanout_status"] = "canary_running"
            _json_dump_atomic(ledger_path, ledger)
            await one(canary)

        if canary_entry.get("audit_status") != "ok" or canary_entry.get("attempts") != 1:
            ledger["fanout_status"] = "blocked_by_canary"
            ledger["fanout_blocking_reason"] = (
                "canary was not successful on its first attempt; remaining sessions were not "
                "launched"
            )
            _json_dump_atomic(ledger_path, ledger)
            return

        ledger["fanout_status"] = "running"
        _json_dump_atomic(ledger_path, ledger)
        remaining = [
            session
            for session in sessions
            if session["session_id"] != canary_id
            and ledger["sessions"][session["session_id"]].get("status") == "pending"
        ]
        await asyncio.gather(*(one(session) for session in remaining))
        ledger["fanout_status"] = "complete"
        _json_dump_atomic(ledger_path, ledger)
    finally:
        await client.aclose()


def run_paid_baseline(
    run_dir: str | Path,
    approval_path: str | Path,
) -> dict[str, Any]:
    """Execute the prepared 14-session baseline only after exact, recorded approval."""
    run_path = Path(run_dir).resolve()
    plan_path = run_path / "run_plan.json"
    if not plan_path.is_file():
        raise BenchmarkError("run_plan.json is missing; run prepare first")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    _validate_prepared_hashes(run_path)
    approval = _validate_approval(run_path, Path(approval_path).resolve(), plan)

    execution = plan.get("execution") or {}
    auth = execution.get("auth")
    saved_key: Optional[str] = None
    if auth == "subscription":
        # Do this before writing a ledger or entering the paid fan-out. The CLI can otherwise
        # turn an expired OAuth session into one failed terminal result per attempted session.
        approval["auth_preflight"] = _require_subscription_auth()
        saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    elif auth == "api" and not os.getenv("ANTHROPIC_API_KEY"):
        raise BenchmarkError("execution.auth=api requires an exported ANTHROPIC_API_KEY")

    # Persist approval and the complete allocation ledger before the first paid call. A process
    # crash can then never leave spend with no durable authorization/provenance record.
    approval_record_path = run_path / "approval_record.json"
    ledger_path = run_path / "paid_run_ledger.json"
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if ledger.get("approval_file_sha256") != approval["approval_file_sha256"]:
            raise BenchmarkError("resume requires the exact original approval record")
    else:
        allocation = float(execution["max_budget_usd"]) * int(execution["max_attempts"])
        ledger = {
            "schema_version": SCHEMA_VERSION,
            "benchmark_id": plan.get("benchmark_id"),
            "approval_file_sha256": approval["approval_file_sha256"],
            "approved_ceiling_usd": approval["max_api_equivalent_usd"],
            "canary_session_id": plan["sessions"][0]["session_id"],
            "fanout_status": "not_started",
            "sessions": {
                session["session_id"]: {
                    "program_id": session["program_id"],
                    "status": "pending",
                    "max_api_equivalent_allocation_usd": allocation,
                }
                for session in plan["sessions"]
            },
        }
        _json_dump_atomic(ledger_path, ledger)
    # The complete allocation map, not merely the session names, must be durable before call 1.
    allocation = float(execution["max_budget_usd"]) * int(execution["max_attempts"])
    for session in plan["sessions"]:
        entry = ledger["sessions"].get(session["session_id"])
        if not isinstance(entry, dict) or not math.isclose(
            float(entry.get("max_api_equivalent_allocation_usd", -1)), allocation
        ):
            raise BenchmarkError(
                f"{session['session_id']}: paid ledger allocation is missing or changed"
            )
    allocated_total = sum(
        float(entry["max_api_equivalent_allocation_usd"])
        for entry in ledger["sessions"].values()
    )
    if not math.isclose(allocated_total, approval["max_api_equivalent_usd"]):
        raise BenchmarkError("paid ledger allocations do not equal the approved ceiling")
    _json_dump_atomic(approval_record_path, approval)
    session_by_id = {session["session_id"]: session for session in plan["sessions"]}
    for session_id, entry in ledger["sessions"].items():
        if entry.get("status") != "started":
            continue
        session = session_by_id[session_id]
        result_path = run_path / session["result_dir"] / f"{session['program_id']}.json"
        audit_path = run_path / session["audit_dir"] / f"{session['program_id']}.audit.json"
        if result_path.is_file() and audit_path.is_file():
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            entry.update(
                {
                    "status": "research_complete",
                    "audit_status": audit.get("status"),
                    "attempts": audit.get("attempts"),
                    "recorded_cost_usd": audit.get("cost_usd"),
                    "audit_sha256": sha256_file(audit_path),
                }
            )
    _json_dump_atomic(ledger_path, ledger)
    ambiguous = [
        session_id
        for session_id, entry in ledger["sessions"].items()
        if entry.get("status") == "started"
    ]
    if ambiguous:
        raise BenchmarkError(
            "interrupted sessions have ambiguous spend and cannot be relaunched under the same "
            f"approval: {ambiguous}"
        )
    pending = [
        session
        for session in plan["sessions"]
        if ledger["sessions"][session["session_id"]].get("status") == "pending"
    ]
    try:
        if pending:
            asyncio.run(_execute_paid_sessions(run_path, plan, pending, ledger, ledger_path))
    finally:
        if saved_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved_key

    from research.verify import verify_directory

    session_summaries: list[dict[str, Any]] = []
    for session in plan["sessions"]:
        result_dir = run_path / session["result_dir"]
        audit_dir = run_path / session["audit_dir"]
        audit_path = audit_dir / f"{session['program_id']}.audit.json"
        result_path = result_dir / f"{session['program_id']}.json"
        if result_path.is_file() and audit_path.is_file():
            if not (audit_dir / "verification_summary.json").is_file():
                verify_directory(result_dir, audit_dir=audit_dir)
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        else:
            audit = {}
        ledger_entry = ledger["sessions"][session["session_id"]]
        if audit.get("status") == "ok" and (audit_dir / "verification_summary.json").is_file():
            ledger_entry["status"] = "verified"
            ledger_entry["verification_summary_sha256"] = sha256_file(
                audit_dir / "verification_summary.json"
            )
            _json_dump_atomic(ledger_path, ledger)
        session_summaries.append(
            {
                "session_id": session["session_id"],
                "program_id": session["program_id"],
                "status": audit.get("status"),
                "attempts": audit.get("attempts"),
                "cost_usd": audit.get("cost_usd"),
                "num_turns": audit.get("num_turns"),
                "duration_ms": audit.get("duration_ms"),
                "ledger_status": ledger_entry.get("status"),
                "telemetry_complete": bool(audit.get("attempt_records"))
                or audit.get("attempts") == 1,
            }
        )
    complete = all(
        item["status"] == "ok" and item["telemetry_complete"] for item in session_summaries
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": plan.get("benchmark_id"),
        "approval": approval,
        "execution": execution,
        "sessions": session_summaries,
        "api_equivalent_cost_usd_recorded": _strict_sum(
            item["cost_usd"] for item in session_summaries
        ),
        "freezable": complete,
        "blocking_reason": (
            None
            if complete
            else ledger.get("fanout_blocking_reason")
            or "one or more sessions failed or retried without cumulative attempt telemetry"
        ),
    }
    _json_dump_atomic(run_path / "paid_run_summary.json", summary)
    return summary


def _review_material_signature(value: Mapping[str, Any]) -> str:
    paper = value.get("paper") or value.get("evidence") or {}
    mechanism = value.get("mechanism") or {}
    assessor = value.get("assessor_text") or {}
    payload = {
        "session_id": value.get("session_id"),
        "case_id": value.get("case_id"),
        "program_id": value.get("program_id"),
        "context": value.get("context") or {},
        "mechanism_index": value.get("mechanism_index"),
        "mechanism_name": value.get("mechanism_name") or mechanism.get("name"),
        "mechanism_summary": value.get("mechanism_summary") or mechanism.get("summary"),
        "supporting_genes": value.get("supporting_genes")
        if "supporting_genes" in value
        else mechanism.get("supporting_genes") or [],
        "supporting_regulators": value.get("supporting_regulators")
        if "supporting_regulators" in value
        else mechanism.get("supporting_regulators") or [],
        "pmid": normalize_pmid(paper.get("pmid")),
        "doi": normalize_doi(paper.get("doi")),
        "title": paper.get("title"),
        "selection_reason": value.get("selection_reason")
        or paper.get("selection_reason")
        or paper.get("relevance_note"),
        "declared_role": value.get("declared_role") or paper.get("role"),
        "retrieval_status": assessor.get("retrieval_status") or value.get("retrieval_status"),
        "content_hash": assessor.get("content_hash") or value.get("content_hash"),
    }
    return _sha256_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))


def _validate_review_binding(
    run_path: Path,
    sessions: Sequence[Mapping[str, Any]],
    text_index: Mapping[str, Any],
    packet_path: Path,
) -> None:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    scoring_path = packet_path.parent / "scoring_payload.private.json"
    if not scoring_path.is_file() or sha256_file(scoring_path) != packet.get("scoring_payload_hash"):
        # Packet hashes bytes, while sha256_file hashes the exact same bytes.
        raise BenchmarkError("review packet private scoring payload is missing or mismatched")
    scoring = json.loads(scoring_path.read_text(encoding="utf-8"))
    expected_sessions = {session["session_id"] for session in sessions}
    candidates: list[tuple[str, list[Mapping[str, Any]]]] = []
    for blind in packet.get("blind_variants", []):
        cases = [
            case
            for case in scoring.get("case_payloads", [])
            if case.get("blind_variant") == blind
        ]
        if {case.get("session_id") for case in cases} == expected_sessions:
            candidates.append((blind, cases))
    if len(candidates) != 1:
        raise BenchmarkError("review packet is not uniquely bound to this run's session cohort")
    blind, cases = candidates[0]
    case_by_session = {case["session_id"]: case for case in cases}
    for session in sessions:
        case = case_by_session[session["session_id"]]
        session_root = run_path / "sessions" / session["session_id"]
        actual = {
            "bundle_sha256": sha256_file(run_path / session["bundle"]),
            "result_sha256": sha256_file(
                run_path / session["result_dir"] / f"{session['program_id']}.json"
            ),
            "audit_sha256": sha256_file(
                run_path / session["audit_dir"] / f"{session['program_id']}.audit.json"
            ),
        }
        for field, wanted in actual.items():
            if case.get(field) != wanted:
                raise BenchmarkError(
                    f"{session['session_id']}: review scoring payload {field} is from another run"
                )
        if not session_root.is_dir():
            raise BenchmarkError(f"missing session root: {session_root}")

    contract = scoring.get("run_contracts", {}).get(blind, {})
    plan = json.loads((run_path / "run_plan.json").read_text(encoding="utf-8"))
    environment = json.loads((run_path / "environment.json").read_text(encoding="utf-8"))
    if contract.get("execution") != plan.get("execution"):
        raise BenchmarkError("review execution contract is from another run")
    if contract.get("environment") != environment:
        raise BenchmarkError("review source/environment contract is from another run")
    if contract.get("text_policy") != text_index.get("text_policy"):
        raise BenchmarkError("review assessor-text policy is from another run")

    packet_signatures = sorted(
        _review_material_signature(item)
        for item in packet.get("review_items", [])
        if item.get("blind_variant") == blind
    )
    current_signatures = []
    context_by_session = {session["session_id"]: session.get("context") or {} for session in sessions}
    text_root = run_path / "assessor_text"
    for link in text_index.get("links", []):
        paper = json.loads((text_root / link["paper_path"]).read_text(encoding="utf-8"))
        current_signatures.append(
            _review_material_signature(
                {
                    **link,
                    "context": context_by_session.get(link["session_id"], {}),
                    "content_hash": paper.get("content_hash"),
                    "retrieval_status": paper.get("retrieval_status"),
                }
            )
        )
    if packet_signatures != sorted(current_signatures):
        raise BenchmarkError("review packet links/text are not an exact projection of this run")


def freeze_baseline(
    run_dir: str | Path,
    *,
    packet_path: str | Path,
    adjudications_path: str | Path,
    metrics_path: str | Path,
) -> dict[str, Any]:
    """Validate the complete baseline and write its final content-hash lock."""
    run_path = Path(run_dir).resolve()
    lock_path = run_path / "baseline-v1.lock.json"
    if lock_path.exists():
        raise BenchmarkError("baseline is already frozen")
    paid_summary_path = run_path / "paid_run_summary.json"
    if not paid_summary_path.is_file():
        raise BenchmarkError("paid_run_summary.json is missing")
    paid_summary = json.loads(paid_summary_path.read_text(encoding="utf-8"))
    if paid_summary.get("freezable") is not True:
        raise BenchmarkError(f"paid baseline is not freezable: {paid_summary.get('blocking_reason')}")
    sessions = _discover_results(run_path)
    text_root = run_path / "assessor_text"
    if not (text_root / "index.json").is_file():
        raise BenchmarkError("assessor text has not been materialized")
    text_index = json.loads((text_root / "index.json").read_text(encoding="utf-8"))
    _validate_text_index(text_root, text_index, {item["session_id"]: item for item in sessions})

    sources = {
        "review_packet.json": Path(packet_path).resolve(),
        "adjudications.json": Path(adjudications_path).resolve(),
        "metrics.json": Path(metrics_path).resolve(),
    }
    scoring_source = Path(packet_path).resolve().parent / "scoring_payload.private.json"
    if scoring_source.is_file():
        sources["scoring_payload.private.json"] = scoring_source
    key_source = (
        Path(packet_path).resolve().parent.parent
        / f"{Path(packet_path).resolve().parent.name}.blinding_key.private.json"
    )
    if key_source.is_file():
        sources["blinding_key.private.json"] = key_source
    for source in sources.values():
        if not source.is_file():
            raise BenchmarkError(f"final review artifact is missing: {source}")
    _validate_review_binding(run_path, sessions, text_index, Path(packet_path).resolve())
    with tempfile.TemporaryDirectory(prefix="gpi-benchmark-freeze-") as temp_dir:
        recomputed = score(
            packet_path,
            adjudications_path,
            Path(temp_dir) / "metrics.json",
        )
    supplied_metrics = json.loads(Path(metrics_path).read_text(encoding="utf-8"))
    if recomputed != supplied_metrics:
        raise BenchmarkError("metrics do not match a fresh score of the supplied adjudications")

    review_dir = run_path / "final_review"
    review_dir.mkdir(parents=True, exist_ok=False)
    for name, source in sources.items():
        shutil.copy2(source, review_dir / name)
    metrics = json.loads((review_dir / "metrics.json").read_text(encoding="utf-8"))
    if not metrics.get("variants"):
        raise BenchmarkError("metrics artifact contains no scored variant")

    hashes = {
        str(path.relative_to(run_path)): sha256_file(path)
        for path in sorted(run_path.rglob("*"))
        if path.is_file() and path.name not in {"artifact_hashes.json", lock_path.name}
    }
    _json_dump(run_path / "artifact_hashes.json", hashes)
    lock = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": paid_summary.get("benchmark_id"),
        "state": "frozen",
        "n_sessions": len(sessions),
        "artifact_hashes_sha256": sha256_file(run_path / "artifact_hashes.json"),
        "n_hashed_artifacts": len(hashes),
        "warning": "Any later mutation invalidates artifact_hashes.json and this lock.",
    }
    _json_dump(lock_path, lock)
    return lock


def _chunks(values: Sequence[str], size: int = MAX_FETCH_IDS) -> Iterable[list[str]]:
    if MAX_FETCH_IDS != 20:
        raise BenchmarkError(f"MAX_FETCH_IDS changed unexpectedly: {MAX_FETCH_IDS}")
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _node_text(node: Optional[ET.Element]) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def _parse_pubmed_assessor_xml(xml_text: str) -> dict[str, dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    records: dict[str, dict[str, Any]] = {}
    for citation in root.findall(".//PubmedArticle"):
        pmid = normalize_pmid(_node_text(citation.find(".//MedlineCitation/PMID")))
        if not pmid:
            continue
        article = citation.find(".//MedlineCitation/Article")
        if article is None:
            continue
        abstract_parts: list[str] = []
        for node in article.findall(".//Abstract/AbstractText"):
            text = _node_text(node)
            if text:
                label = node.attrib.get("Label")
                abstract_parts.append(f"{label}: {text}" if label else text)
        ids = {
            str(node.attrib.get("IdType", "")).lower(): _node_text(node)
            for node in citation.findall(".//PubmedData/ArticleIdList/ArticleId")
        }
        records[pmid] = {
            "abstract": "\n\n".join(abstract_parts).strip(),
            "pmcid": ids.get("pmc") or None,
            "doi": normalize_doi(ids.get("doi")),
            "title": _node_text(article.find("ArticleTitle")),
        }
    return records


def _ncbi_params() -> dict[str, str]:
    params = {"db": "pubmed", "retmode": "xml", "tool": "gene-program-interpreter"}
    if email := (os.getenv("PUBMED_EMAIL") or os.getenv("NCBI_EMAIL")):
        params["email"] = email
    if api_key := os.getenv("NCBI_API_KEY"):
        params["api_key"] = api_key
    return params


def _fetch_pubmed_assessor_text(
    pmids: Sequence[str],
    *,
    get: Callable[..., requests.Response] = requests.get,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    records: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for batch in _chunks(sorted(set(pmids))):
        try:
            response = get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params={**_ncbi_params(), "id": ",".join(batch)},
                timeout=60,
            )
            response.raise_for_status()
            parsed = _parse_pubmed_assessor_xml(response.text)
            records.update(parsed)
            for pmid in batch:
                if pmid not in parsed:
                    errors[pmid] = "not returned by PubMed"
        except requests.RequestException as exc:
            for pmid in batch:
                errors[pmid] = f"PubMed retrieval failed: {type(exc).__name__}"
    return records, errors


def _europe_pmc_lookup(
    *,
    pmid: Optional[str],
    doi: Optional[str],
    get: Callable[..., requests.Response],
) -> dict[str, Any]:
    query_value = f"EXT_ID:{pmid} AND SRC:MED" if pmid else f'DOI:"{doi}"'
    try:
        response = get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": query_value, "format": "json", "resultType": "core", "pageSize": 1},
            timeout=60,
        )
        response.raise_for_status()
    except requests.RequestException:
        return {}
    results = _response_json(response).get("resultList", {}).get("result", [])
    return results[0] if results else {}


def _pmc_full_text(
    pmcid: str, *, get: Callable[..., requests.Response]
) -> tuple[Optional[str], Optional[str]]:
    safe = str(pmcid).upper()
    if not re.fullmatch(r"PMC\d+", safe):
        return None, "invalid PMCID"
    try:
        response = get(
            f"https://www.ebi.ac.uk/europepmc/webservices/rest/{safe}/fullTextXML",
            timeout=90,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        text = "\n\n".join(
            part
            for part in (_node_text(node) for node in root.findall(".//body//p"))
            if part
        )
        return (text or None), None
    except (requests.RequestException, ET.ParseError) as exc:
        return None, f"PMC retrieval failed: {type(exc).__name__}"


def _validate_session_artifacts(run_dir: Path, session: Mapping[str, Any]) -> Path:
    session_root = run_dir / "sessions" / session["session_id"]
    result = run_dir / session["result_dir"] / f"{session['program_id']}.json"
    audit_dir = run_dir / session.get(
        "audit_dir", str((session_root / "research_audit").relative_to(run_dir))
    )
    required = [
        result,
        audit_dir / f"{session['program_id']}.audit.json",
        audit_dir / f"{session['program_id']}.raw_payload.json",
        audit_dir / f"{session['program_id']}.pre_verify.json",
        audit_dir / "verification_summary.json",
    ]
    missing = [str(path.relative_to(run_dir)) for path in required if not path.is_file()]
    if missing:
        raise BenchmarkError(
            f"{session['session_id']}: incomplete paid/verified artifact set: {missing}"
        )
    result_payload = json.loads(result.read_text(encoding="utf-8"))
    if result_payload.get("program_id") != session["program_id"]:
        raise BenchmarkError(f"{session['session_id']}: result program_id drifted")
    audit = json.loads(required[1].read_text(encoding="utf-8"))
    if audit.get("status") != "ok":
        raise BenchmarkError(f"{session['session_id']}: research audit is not successful")
    verification = json.loads(required[4].read_text(encoding="utf-8"))
    if verification.get("n_programs") not in (None, 1):
        raise BenchmarkError(f"{session['session_id']}: invalid verification summary")
    return result


def _discover_results(run_dir: Path) -> list[dict[str, Any]]:
    plan_path = run_dir / "run_plan.json"
    found: list[dict[str, Any]] = []
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        for session in plan.get("sessions", []):
            result = _validate_session_artifacts(run_dir, session)
            found.append({**session, "result_path": result})
        if len(found) != len(plan.get("sessions", [])):
            raise BenchmarkError("run plan and captured sessions differ")
        return found
    for result in sorted(run_dir.rglob("research_results/*.json")):
        program_id = result.stem
        session_id = result.parent.parent.name
        found.append(
            {
                "session_id": session_id,
                "case_id": session_id.split("--r", 1)[0],
                "program_id": program_id,
                "result_path": result,
            }
        )
    return found


def _selected_links(session: Mapping[str, Any]) -> list[dict[str, Any]]:
    result_path = Path(session["result_path"])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    evidence = {item["evidence_id"]: item for item in result.get("evidence", [])}
    links: list[dict[str, Any]] = []
    for mechanism_index, mechanism in enumerate(result.get("candidate_mechanisms", []), start=1):
        for evidence_id in mechanism.get("evidence_ids", []):
            paper = evidence.get(evidence_id)
            if not paper:
                continue
            pmid = normalize_pmid(paper.get("pmid"))
            doi = normalize_doi(paper.get("doi"))
            if not pmid and not doi:
                continue
            link_id = (
                f"{session['session_id']}::M{mechanism_index}::{evidence_id}::"
                f"{pmid or doi}"
            )
            links.append(
                {
                    "link_id": link_id,
                    "session_id": session["session_id"],
                    "case_id": session["case_id"],
                    "program_id": session["program_id"],
                    "mechanism_index": mechanism_index,
                    "mechanism": mechanism,
                    "evidence": paper,
                    "pmid": pmid,
                    "doi": doi,
                    "result_path": str(result_path),
                }
            )
    return links


def _paper_groups(
    links: Sequence[Mapping[str, Any]],
) -> tuple[list[tuple[Optional[str], Optional[str]]], dict[str, tuple[Optional[str], Optional[str]]]]:
    """Unify PMID/DOI aliases without ever comparing ``None`` to strings."""
    groups: list[dict[str, set[str]]] = []
    for link in links:
        pmids = {link["pmid"]} if link.get("pmid") else set()
        dois = {link["doi"]} if link.get("doi") else set()
        matches = [
            index
            for index, group in enumerate(groups)
            if (pmids & group["pmids"]) or (dois & group["dois"])
        ]
        if not matches:
            groups.append({"pmids": pmids, "dois": dois})
            continue
        target = groups[matches[0]]
        target["pmids"].update(pmids)
        target["dois"].update(dois)
        for index in reversed(matches[1:]):
            target["pmids"].update(groups[index]["pmids"])
            target["dois"].update(groups[index]["dois"])
            del groups[index]

    identities = [
        (
            sorted(group["pmids"])[0] if group["pmids"] else None,
            sorted(group["dois"])[0] if group["dois"] else None,
        )
        for group in groups
    ]
    identities.sort(key=lambda value: (value[0] or "", value[1] or ""))
    by_link: dict[str, tuple[Optional[str], Optional[str]]] = {}
    for link in links:
        for identity, group in zip(identities, sorted(groups, key=lambda group: (
            sorted(group["pmids"])[0] if group["pmids"] else "",
            sorted(group["dois"])[0] if group["dois"] else "",
        ))):
            if (link.get("pmid") and link["pmid"] in group["pmids"]) or (
                link.get("doi") and link["doi"] in group["dois"]
            ):
                by_link[link["link_id"]] = identity
                break
    return identities, by_link


def materialize_text(
    run_dir: str | Path,
    out: str | Path,
    *,
    dry_run: bool = False,
    get: Callable[..., requests.Response] = requests.get,
) -> dict[str, Any]:
    """Fetch full assessor text for every selected mechanism-paper link.

    Missing text is represented as ``not_assessable``; it is never negative evidence.
    """
    run_path = Path(run_dir).resolve()
    sessions = _discover_results(run_path)
    links = [link for session in sessions for link in _selected_links(session)]
    pmids = sorted({link["pmid"] for link in links if link["pmid"]})
    papers, paper_identity_by_link = _paper_groups(links)
    preview = {
        "n_sessions_with_results": len(sessions),
        "n_links": len(links),
        "n_unique_papers": len(papers),
        "n_pubmed_batches": math.ceil(len(pmids) / MAX_FETCH_IDS),
        "max_fetch_ids": MAX_FETCH_IDS,
        "dry_run": dry_run,
    }
    if dry_run:
        return preview

    out_path = Path(out).resolve()
    if out_path.exists() and any(out_path.iterdir()):
        raise BenchmarkError(f"refusing to overwrite assessor text: {out_path}")
    out_path.mkdir(parents=True, exist_ok=True)
    pubmed, pubmed_errors = _fetch_pubmed_assessor_text(pmids, get=get)
    paper_records: dict[tuple[Optional[str], Optional[str]], dict[str, Any]] = {}

    for pmid, doi in papers:
        pubmed_record = pubmed.get(pmid or "", {})
        epmc: dict[str, Any] = {}
        pmcid = pubmed_record.get("pmcid")
        if not pmcid or not pubmed_record.get("abstract"):
            epmc = _europe_pmc_lookup(pmid=pmid, doi=doi, get=get)
            pmcid = pmcid or epmc.get("pmcid")
        full_text = None
        full_error = None
        if pmcid:
            full_text, full_error = _pmc_full_text(str(pmcid), get=get)
        abstract = pubmed_record.get("abstract") or epmc.get("abstractText") or ""
        if full_text:
            text, source, text_type = full_text, "europe_pmc", "full_text"
        elif abstract:
            text = str(abstract)
            source = "pubmed" if pubmed_record.get("abstract") else "europe_pmc"
            text_type = "abstract"
        else:
            text, source, text_type = "", None, "unavailable"
        key_value = f"pmid:{pmid}" if pmid else f"doi:{doi}"
        paper_key = _sha256_bytes(key_value.encode("utf-8"))[:20]
        record = {
            "schema_version": SCHEMA_VERSION,
            "paper_key": paper_key,
            "pmid": pmid,
            "doi": doi or pubmed_record.get("doi") or normalize_doi(epmc.get("doi")),
            "pmcid": pmcid,
            "title": pubmed_record.get("title") or epmc.get("title") or None,
            "source": source,
            "text_type": text_type,
            "retrieval_status": "retrieved" if text else "not_assessable",
            "content_hash": _sha256_bytes(text.encode("utf-8")) if text else None,
            "text": text,
            "errors": [
                error
                for error in (pubmed_errors.get(pmid or ""), full_error)
                if error
            ],
        }
        relative = Path("papers") / f"{paper_key}.json"
        _json_dump(out_path / relative, record)
        record["path"] = str(relative)
        paper_records[(pmid, doi)] = record

    index_links: list[dict[str, Any]] = []
    for link in links:
        record = paper_records[paper_identity_by_link[link["link_id"]]]
        clean = {key: value for key, value in link.items() if key != "result_path"}
        clean["paper_key"] = record["paper_key"]
        clean["paper_path"] = record["path"]
        clean["retrieval_status"] = record["retrieval_status"]
        index_links.append(clean)
    index = {
        "schema_version": SCHEMA_VERSION,
        "text_policy": "PubMed full abstract; prefer open-access Europe PMC full text",
        "max_fetch_ids": MAX_FETCH_IDS,
        "links": index_links,
        "papers": [
            {key: value for key, value in record.items() if key not in {"text", "errors"}}
            for record in paper_records.values()
        ],
    }
    _json_dump(out_path / "index.json", index)
    preview.update(
        {
            "out": str(out_path),
            "n_retrieved": sum(
                record["retrieval_status"] == "retrieved" for record in paper_records.values()
            ),
            "n_not_assessable": sum(
                record["retrieval_status"] == "not_assessable"
                for record in paper_records.values()
            ),
        }
    )
    return preview


def _variant_arg(value: str) -> tuple[str, Path]:
    label, sep, path = value.partition("=")
    if not sep or not label.strip() or not path.strip():
        raise argparse.ArgumentTypeError("variant must be NAME=RUN_DIR")
    return label.strip(), Path(path).resolve()


def _stable_id(*parts: Any, length: int = 20) -> str:
    joined = "\x1f".join(str(part) for part in parts)
    return _sha256_bytes(joined.encode("utf-8"))[:length]


def _load_run_plan(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "run_plan.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _flatten_regulators(bundle: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for entries in (bundle.get("perturbation_regulators") or {}).values():
        for entry in entries or []:
            gene = entry.get("gene") if isinstance(entry, dict) else None
            if gene and gene not in result:
                result.append(str(gene))
    return result


def _audit_for_result(result_path: Path) -> dict[str, Any]:
    audit_path = result_path.parent.parent / "research_audit" / f"{result_path.stem}.audit.json"
    return json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}


def _validate_text_index(
    text_root: Path,
    index: Mapping[str, Any],
    sessions: Mapping[str, Mapping[str, Any]],
) -> None:
    expected = {
        link["link_id"]: link
        for session in sessions.values()
        for link in _selected_links(session)
    }
    actual_links = index.get("links") or []
    actual = {link.get("link_id"): link for link in actual_links}
    if len(actual) != len(actual_links):
        raise BenchmarkError("assessor text index contains duplicate link IDs")
    if set(actual) != set(expected):
        raise BenchmarkError(
            "assessor text index does not exactly cover the current retained evidence links"
        )
    for link_id, link in actual.items():
        source = expected[link_id]
        if link.get("pmid") != source.get("pmid") or link.get("doi") != source.get("doi"):
            raise BenchmarkError(f"{link_id}: assessor text identifiers are stale")
        paper_path = _portable_path(text_root, link.get("paper_path"), field="paper_path")
        if not paper_path.is_file():
            raise BenchmarkError(f"{link_id}: assessor text paper file is missing")
        paper = json.loads(paper_path.read_text(encoding="utf-8"))
        status = paper.get("retrieval_status")
        text = str(paper.get("text") or "")
        if status == "retrieved":
            actual_hash = _sha256_bytes(text.encode("utf-8"))
            if not text or paper.get("content_hash") != actual_hash:
                raise BenchmarkError(f"{link_id}: assessor text content hash mismatch")
        elif status == "not_assessable":
            if text or paper.get("content_hash") is not None:
                raise BenchmarkError(f"{link_id}: not_assessable text must be empty")
        else:
            raise BenchmarkError(f"{link_id}: invalid retrieval_status")


def _telemetry(result: Mapping[str, Any], audit: Mapping[str, Any]) -> dict[str, Any]:
    meta = result.get("meta") or {}
    records = audit.get("attempt_records") or meta.get("attempt_records") or []
    literature_trace = audit.get("literature_trace") or []
    if literature_trace:
        search_events = [
            event
            for event in literature_trace
            if str(event.get("action", "")).lower() == "search"
            and str(event.get("source", "")).lower() in {"pubmed", "openalex"}
        ]
        searches = len(search_events)
        searches_source = "literature_trace"
    else:
        tool_trace = audit.get("tool_trace") or []
        search_events = [
            event
            for event in tool_trace
            if str(event.get("tool", "")).endswith(("search_pubmed", "search_openalex"))
        ]
        searches = len(search_events) if tool_trace else len(result.get("queries") or [])
        searches_source = "tool_trace" if tool_trace else "agent_authored_fallback"

    graph_events = [
        event
        for event in literature_trace
        if any(
            token in str(event.get("action", "")).lower()
            for token in ("citation", "reference", "expand")
        )
    ]
    graph_nodes = {
        str(identifier)
        for event in graph_events
        for identifier in (event.get("returned_identifiers") or [])
    }
    graph_edges = sum(
        int(event.get("returned_count") or len(event.get("returned_identifiers") or []))
        for event in graph_events
    )
    if records:
        def total(field: str) -> Optional[float]:
            values = [record.get(field) for record in records]
            return sum(float(value) for value in values) if all(value is not None for value in values) else None

        tokens = []
        for record in records:
            value = record.get("tokens")
            tokens.append(value.get("total") if isinstance(value, dict) else value)
        return {
            "searches": searches,
            "searches_source": searches_source,
            "turns": total("turns"),
            "tokens": sum(tokens) if tokens and all(value is not None for value in tokens) else None,
            "cost_usd": total("cost_usd"),
            "duration_seconds": (
                total("duration_ms") / 1000 if total("duration_ms") is not None else total("duration_seconds")
            ),
            "attempts": len(records),
            "retries": max(0, len(records) - 1),
            "cumulative_complete": True,
            "stop_reason": records[-1].get("stop_reason"),
            "citation_graph_events": len(graph_events),
            "citation_graph_nodes": len(graph_nodes),
            "citation_graph_edges": graph_edges,
        }
    token_block = meta.get("tokens") or audit.get("tokens")
    token_total = token_block.get("total") if isinstance(token_block, dict) else token_block
    attempts = audit.get("attempts", meta.get("attempts"))
    duration_ms = audit.get("duration_ms", meta.get("duration_ms"))
    return {
        "searches": searches,
        "searches_source": searches_source,
        "turns": audit.get("num_turns", meta.get("num_turns")),
        "tokens": token_total,
        "cost_usd": audit.get("cost_usd", meta.get("cost_usd")),
        "duration_seconds": float(duration_ms) / 1000 if duration_ms is not None else None,
        "attempts": attempts,
        "retries": max(0, int(attempts) - 1) if isinstance(attempts, int) else None,
        "cumulative_complete": attempts == 1,
        "stop_reason": audit.get("stop_reason", meta.get("stop_reason")),
        "citation_graph_events": len(graph_events) if literature_trace else None,
        "citation_graph_nodes": len(graph_nodes) if literature_trace else None,
        "citation_graph_edges": graph_edges if literature_trace else None,
    }


def make_review_packet(
    variants: Mapping[str, str | Path],
    out: str | Path,
    *,
    seed: str = "literature-v1",
) -> dict[str, Any]:
    """Blind variant names and create a complete review/adjudication template."""
    if not variants:
        raise BenchmarkError("at least one variant is required")
    out_path = Path(out).resolve()
    if out_path.exists() and any(out_path.iterdir()):
        raise BenchmarkError(f"refusing to overwrite review packet: {out_path}")
    out_path.mkdir(parents=True, exist_ok=True)

    labels = sorted(variants, key=lambda label: _stable_id(seed, label))
    aliases = {label: f"Variant {chr(65 + index)}" for index, label in enumerate(labels)}
    items: list[dict[str, Any]] = []
    case_payloads: list[dict[str, Any]] = []

    for label, raw_dir in variants.items():
        run_dir = Path(raw_dir).resolve()
        text_root = run_dir / "assessor_text"
        index_path = text_root / "index.json"
        if not index_path.exists():
            raise BenchmarkError(f"missing assessor text index for {label}: {index_path}")
        index = json.loads(index_path.read_text(encoding="utf-8"))
        sessions = {item["session_id"]: item for item in _discover_results(run_dir)}
        _validate_text_index(text_root, index, sessions)
        blind = aliases[label]

        for link in index.get("links", []):
            paper = json.loads((text_root / link["paper_path"]).read_text(encoding="utf-8"))
            review_id = "RL-" + _stable_id(seed, blind, link["link_id"])
            items.append(
                {
                    "review_id": review_id,
                    "blind_variant": blind,
                    "session_id": link["session_id"],
                    "case_id": link["case_id"],
                    "program_id": link["program_id"],
                    "context": dict(sessions.get(link["session_id"], {}).get("context") or {}),
                    "mechanism_index": link["mechanism_index"],
                    "mechanism_name": link["mechanism"].get("name"),
                    "mechanism_summary": link["mechanism"].get("summary"),
                    "supporting_genes": link["mechanism"].get("supporting_genes") or [],
                    "supporting_regulators": link["mechanism"].get("supporting_regulators") or [],
                    "paper": {
                        key: link["evidence"].get(key)
                        for key in ("pmid", "doi", "title", "year", "study_type")
                    },
                    "selection_reason": link["evidence"].get("selection_reason")
                    or link["evidence"].get("relevance_note"),
                    "declared_role": link["evidence"].get("role"),
                    "assessor_text": {
                        "retrieval_status": paper["retrieval_status"],
                        "source": paper["source"],
                        "text_type": paper["text_type"],
                        "content_hash": paper["content_hash"],
                        "text": paper["text"],
                    },
                    "paper_key": paper["paper_key"],
                }
            )

        for session_id, session in sessions.items():
            result_path = Path(session["result_path"])
            result = json.loads(result_path.read_text(encoding="utf-8"))
            bundle_path = result_path.parent.parent / "program_bundles" / f"{session['program_id']}.json"
            audit_path = (
                result_path.parent.parent
                / "research_audit"
                / f"{session['program_id']}.audit.json"
            )
            if not bundle_path.exists():
                raise BenchmarkError(f"missing bundle for {session_id}: {bundle_path}")
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            case_review_id = "RC-" + _stable_id(seed, blind, session_id)
            case_payloads.append(
                {
                    "case_review_id": case_review_id,
                    "blind_variant": blind,
                    "session_id": session_id,
                    "case_id": session["case_id"],
                    "program_id": session["program_id"],
                    "context": dict(session.get("context") or {}),
                    "core_genes": [*bundle.get("program_genes", []), *bundle.get("distinctive_genes", [])],
                    "regulators": _flatten_regulators(bundle),
                    "bundle_sha256": sha256_file(bundle_path),
                    "result_sha256": sha256_file(result_path),
                    "audit_sha256": sha256_file(audit_path),
                    "mechanisms": result.get("candidate_mechanisms") or [],
                    "evidence": result.get("evidence") or [],
                    "queries": result.get("queries") or [],
                    "telemetry": _telemetry(result, _audit_for_result(result_path)),
                }
            )

    items.sort(key=lambda item: (item["blind_variant"], item["session_id"], item["review_id"]))
    n_double = math.ceil(len(items) * 0.20) if items else 0
    double_ids = {
        item["review_id"]
        for item in sorted(items, key=lambda item: _stable_id(seed, "double", item["review_id"]))[
            :n_double
        ]
    }
    for item in items:
        item["required_assessors"] = 2 if item["review_id"] in double_ids else 1

    case_payloads = sorted(
        case_payloads, key=lambda item: (item["blind_variant"], item["session_id"])
    )
    scoring_payload = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "case_payloads": case_payloads,
        "run_contracts": {
            aliases[label]: {
                "execution": _load_run_plan(Path(variants[label]).resolve()).get("execution", {}),
                "environment": (
                    json.loads(
                        (Path(variants[label]).resolve() / "environment.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    if (Path(variants[label]).resolve() / "environment.json").exists()
                    else {}
                ),
                "text_policy": json.loads(
                    (Path(variants[label]).resolve() / "assessor_text" / "index.json").read_text(
                        encoding="utf-8"
                    )
                ).get("text_policy"),
            }
            for label in variants
        },
    }
    scoring_bytes = (json.dumps(scoring_payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    packet = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "seed_hash": _sha256_bytes(seed.encode("utf-8")),
        "blind_variants": sorted(set(aliases.values())),
        "review_items": items,
        "review_cases": [
            {
                **{
                    key: case[key]
                    for key in (
                        "case_review_id",
                        "blind_variant",
                        "session_id",
                        "case_id",
                        "program_id",
                        "context",
                        "core_genes",
                        "regulators",
                    )
                },
                "mechanisms": [
                    {
                        key: mechanism.get(key)
                        for key in (
                            "name",
                            "summary",
                            "supporting_genes",
                            "supporting_regulators",
                        )
                    }
                    for mechanism in case["mechanisms"]
                ],
            }
            for case in case_payloads
        ],
        "scoring_payload_hash": _sha256_bytes(scoring_bytes),
        "double_scored_fraction": (len(double_ids) / len(items)) if items else 0.0,
        "rubric": {
            "entailment": ["entailed", "not_entailed", "not_assessable"],
            "context_match": ["exact", "partial", "mismatch", "not_assessable"],
            "paper_role": ["anchor", "context", "corroboration", "review", "conflict", "unclear"],
            "quality_scale": "1 (poor) to 5 (excellent)",
        },
    }
    template_reviews = []
    for item in items:
        for slot in range(1, item["required_assessors"] + 1):
            assessable = item["assessor_text"]["retrieval_status"] == "retrieved"
            template_reviews.append(
                {
                    "review_id": item["review_id"],
                    "assessor_slot": slot,
                    "assessor_id": None,
                    "entailment": "pending" if assessable else "not_assessable",
                    "organism_match": "pending" if assessable else "not_assessable",
                    "tissue_match": "pending" if assessable else "not_assessable",
                    "cell_type_match": "pending" if assessable else "not_assessable",
                    "condition_match": "pending" if assessable else "not_assessable",
                    "paper_role": "pending" if assessable else "unclear",
                    "paper_role_quality": None,
                    "selection_reason_quality": None,
                    "entailed_genes": [],
                    "entailed_regulators": [],
                    "notes": "",
                }
            )
    template = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "reviews": template_reviews,
        "adjudications": [],
        "case_reviews": [
            {
                "case_review_id": case["case_review_id"],
                "assessor_id": None,
                "coherence": None,
                "selection_quality": None,
                "redundant_mechanism_pairs": [],
                "notes": "",
            }
            for case in packet["review_cases"]
        ],
    }
    _json_dump(out_path / "review_packet.json", packet)
    _json_dump(out_path / "adjudications.template.json", template)
    (out_path / "scoring_payload.private.json").write_bytes(scoring_bytes)
    blinding_key_path = out_path.parent / f"{out_path.name}.blinding_key.private.json"
    _json_dump(blinding_key_path, {"variant_aliases": aliases})
    return {
        "out": str(out_path),
        "n_links": len(items),
        "n_double_scored": len(double_ids),
        "n_case_reviews": len(case_payloads),
    }


_CONTEXT_VALUES = {"exact", "partial", "mismatch", "not_assessable"}
_ENTAILMENT_VALUES = {"entailed", "not_entailed", "not_assessable"}
_ROLE_VALUES = {"anchor", "context", "corroboration", "review", "conflict", "unclear"}
_REVIEW_FIELDS = (
    "entailment",
    "organism_match",
    "tissue_match",
    "cell_type_match",
    "condition_match",
    "paper_role",
    "paper_role_quality",
    "selection_reason_quality",
    "entailed_genes",
    "entailed_regulators",
)


def _validate_review(review: Mapping[str, Any], item: Mapping[str, Any]) -> None:
    if not review.get("assessor_id"):
        raise BenchmarkError(f"{review.get('review_id')}: assessor_id is required")
    text_available = item.get("assessor_text", {}).get("retrieval_status") == "retrieved"
    if not text_available:
        fields = (
            "entailment",
            "organism_match",
            "tissue_match",
            "cell_type_match",
            "condition_match",
        )
        if any(review.get(field) != "not_assessable" for field in fields):
            raise BenchmarkError(
                f"{review.get('review_id')}: missing text must be scored not_assessable"
            )
        if review.get("paper_role") != "unclear":
            raise BenchmarkError(f"{review.get('review_id')}: missing text has unclear paper role")
        if review.get("paper_role_quality") is not None or review.get(
            "selection_reason_quality"
        ) is not None:
            raise BenchmarkError(f"{review.get('review_id')}: missing text quality must be null")
        if review.get("entailed_genes") or review.get("entailed_regulators"):
            raise BenchmarkError(f"{review.get('review_id')}: missing text cannot entail genes")
        return
    if review.get("entailment") not in _ENTAILMENT_VALUES:
        raise BenchmarkError(f"{review.get('review_id')}: invalid entailment")
    for field in ("organism_match", "tissue_match", "cell_type_match", "condition_match"):
        if review.get(field) not in _CONTEXT_VALUES:
            raise BenchmarkError(f"{review.get('review_id')}: invalid {field}")
    if review.get("paper_role") not in _ROLE_VALUES:
        raise BenchmarkError(f"{review.get('review_id')}: invalid paper_role")
    for field in ("paper_role_quality", "selection_reason_quality"):
        value = review.get(field)
        if not isinstance(value, (int, float)) or not 1 <= value <= 5:
            raise BenchmarkError(f"{review.get('review_id')}: {field} must be in [1,5]")
    allowed_genes = set(item.get("supporting_genes") or [])
    allowed_regs = set(item.get("supporting_regulators") or [])
    if not set(review.get("entailed_genes") or []).issubset(allowed_genes):
        raise BenchmarkError(f"{review.get('review_id')}: entailed_genes exceed the claim")
    if not set(review.get("entailed_regulators") or []).issubset(allowed_regs):
        raise BenchmarkError(f"{review.get('review_id')}: entailed_regulators exceed the claim")
    if review.get("entailment") != "entailed" and (
        review.get("entailed_genes") or review.get("entailed_regulators")
    ):
        raise BenchmarkError(f"{review.get('review_id')}: non-entailed links cannot entail genes")


def _normalized_review_value(field: str, value: Any) -> Any:
    if field in {"entailed_genes", "entailed_regulators"}:
        return tuple(sorted(value or []))
    return value


def _final_reviews(
    packet: Mapping[str, Any], adjudications: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    item_by_id = {item["review_id"]: item for item in packet.get("review_items", [])}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for review in adjudications.get("reviews", []):
        review_id = review.get("review_id")
        if review_id not in item_by_id:
            raise BenchmarkError(f"unknown review_id: {review_id}")
        grouped[review_id].append(review)
    adjudicated = {row["review_id"]: row for row in adjudications.get("adjudications", [])}
    final: dict[str, dict[str, Any]] = {}
    for review_id, item in item_by_id.items():
        rows = grouped.get(review_id, [])
        required = int(item["required_assessors"])
        if len(rows) != required:
            raise BenchmarkError(f"{review_id}: expected {required} independent review(s), got {len(rows)}")
        if len({row.get("assessor_id") for row in rows}) != len(rows):
            raise BenchmarkError(f"{review_id}: duplicate assessor")
        for row in rows:
            _validate_review(row, item)
        disagreement = any(
            len({_normalized_review_value(field, row.get(field)) for row in rows}) > 1
            for field in _REVIEW_FIELDS
        )
        if disagreement:
            row = adjudicated.get(review_id)
            if not row:
                raise BenchmarkError(f"{review_id}: disagreement requires adjudication")
            _validate_review(row, item)
            final[review_id] = dict(row)
        else:
            final[review_id] = dict(rows[0])
    extras = set(adjudicated) - set(item_by_id)
    if extras:
        raise BenchmarkError(f"unknown adjudication review IDs: {sorted(extras)}")
    return final


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [float(value) for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def _strict_sum(values: Iterable[Optional[float]]) -> Optional[float]:
    values = list(values)
    return sum(float(value) for value in values) if values and all(value is not None for value in values) else None


def _p95(values: Iterable[Optional[float]]) -> Optional[float]:
    values = list(values)
    if not values or any(value is None for value in values):
        return None
    clean = sorted(float(value) for value in values if value is not None)
    return clean[max(0, math.ceil(0.95 * len(clean)) - 1)]


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if a | b else 0.0


def _case_metrics(
    case: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    reviews: Mapping[str, Mapping[str, Any]],
    case_review: Mapping[str, Any],
) -> dict[str, Any]:
    core = set(case.get("core_genes") or [])
    regulators = set(case.get("regulators") or [])
    mechanisms = case.get("mechanisms") or []
    claimed_core = set().union(*(set(mech.get("supporting_genes") or []) for mech in mechanisms)) & core
    claimed_regs = set().union(*(set(mech.get("supporting_regulators") or []) for mech in mechanisms)) & regulators
    entailed_core: set[str] = set()
    entailed_regs: set[str] = set()
    for item in items:
        review = reviews[item["review_id"]]
        if review["entailment"] == "entailed":
            entailed_core.update(set(review.get("entailed_genes") or []) & core)
            entailed_regs.update(set(review.get("entailed_regulators") or []) & regulators)

    pairs = [
        _jaccard(left.get("supporting_genes") or [], right.get("supporting_genes") or [])
        for index, left in enumerate(mechanisms)
        for right in mechanisms[index + 1 :]
    ]
    redundant_pairs = case_review.get("redundant_mechanism_pairs") or []
    possible_pairs = len(mechanisms) * (len(mechanisms) - 1) // 2
    assessable = [reviews[item["review_id"]] for item in items if reviews[item["review_id"]]["entailment"] != "not_assessable"]
    telemetry = dict(case.get("telemetry") or {})
    identifier_records = {
        item["paper_key"]: item["paper"]
        for item in items
    }
    evidence_by_identifier = {}
    for evidence in case.get("evidence") or []:
        key = normalize_pmid(evidence.get("pmid")) or normalize_doi(evidence.get("doi"))
        if key:
            evidence_by_identifier[key] = evidence
    identifier_counts = {"resolved": 0, "unverified": 0, "refuted": 0, "retracted": 0}
    for paper in identifier_records.values():
        key = normalize_pmid(paper.get("pmid")) or normalize_doi(paper.get("doi"))
        evidence = evidence_by_identifier.get(key, {})
        if evidence.get("retracted") is True:
            identifier_counts["retracted"] += 1
        elif evidence.get("resolved") is True:
            identifier_counts["resolved"] += 1
        elif evidence.get("resolved") is False:
            identifier_counts["refuted"] += 1
        else:
            identifier_counts["unverified"] += 1

    context_rates = {}
    for field in ("organism_match", "tissue_match", "cell_type_match", "condition_match"):
        relevant = [review for review in reviews.values() if review.get(field) != "not_assessable"]
        context_rates[f"{field}_exact_rate"] = (
            sum(review[field] == "exact" for review in relevant) / len(relevant) if relevant else None
        )
    result = {
        "case_review_id": case["case_review_id"],
        "session_id": case["session_id"],
        "case_id": case["case_id"],
        "program_id": case["program_id"],
        "coherence": float(case_review["coherence"]),
        "selection_quality": float(case_review["selection_quality"]),
        "core_gene_count": len(core),
        "claimed_core_gene_count": len(claimed_core),
        "claimed_core_gene_coverage": len(claimed_core) / len(core) if core else None,
        "entailed_core_gene_count": len(entailed_core),
        "entailed_core_gene_coverage": len(entailed_core) / len(core) if core else None,
        "regulator_count": len(regulators),
        "claimed_regulator_count": len(claimed_regs),
        "claimed_regulator_coverage": len(claimed_regs) / len(regulators) if regulators else None,
        "entailed_regulator_count": len(entailed_regs),
        "entailed_regulator_coverage": len(entailed_regs) / len(regulators) if regulators else None,
        "mechanism_count": len(mechanisms),
        "supporting_gene_jaccard_mean": _mean(pairs),
        "supporting_gene_jaccard_max": max(pairs) if pairs else None,
        "mechanism_redundancy_count": len(redundant_pairs),
        "mechanism_redundancy_rate": len(redundant_pairs) / possible_pairs if possible_pairs else 0.0,
        "link_count": len(items),
        "assessable_link_count": len(assessable),
        "entailed_link_count": sum(review["entailment"] == "entailed" for review in assessable),
        "entailment_rate": (
            sum(review["entailment"] == "entailed" for review in assessable) / len(assessable)
            if assessable
            else None
        ),
        "paper_role_quality": _mean(reviews[item["review_id"]]["paper_role_quality"] for item in items),
        "selection_reason_quality": _mean(
            reviews[item["review_id"]]["selection_reason_quality"] for item in items
        ),
        "identifier_status_counts": identifier_counts,
        **context_rates,
        "telemetry": telemetry,
    }
    return result


def _cohort_fingerprint(cases: Sequence[Mapping[str, Any]]) -> str:
    payload = [
        {
            "case_id": case["case_id"],
            "program_id": case["program_id"],
            "context": case.get("context") or {},
            "core_genes": case.get("core_genes") or [],
            "regulators": case.get("regulators") or [],
        }
        for case in sorted(cases, key=lambda value: value["session_id"])
    ]
    return _sha256_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))


def score(
    packet_path: str | Path,
    adjudications_path: str | Path,
    out: str | Path,
) -> dict[str, Any]:
    """Validate complete human adjudications and compute per-case/aggregate metrics."""
    packet_path = Path(packet_path)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    adjudications = json.loads(Path(adjudications_path).read_text(encoding="utf-8"))
    if packet.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise BenchmarkError("unsupported review packet schema")
    scoring_path = packet_path.parent / "scoring_payload.private.json"
    if not scoring_path.is_file():
        raise BenchmarkError("private scoring payload is missing")
    scoring_bytes = scoring_path.read_bytes()
    if _sha256_bytes(scoring_bytes) != packet.get("scoring_payload_hash"):
        raise BenchmarkError("private scoring payload hash mismatch")
    scoring_payload = json.loads(scoring_bytes)
    final_reviews = _final_reviews(packet, adjudications)

    case_reviews = {row.get("case_review_id"): row for row in adjudications.get("case_reviews", [])}
    if len(case_reviews) != len(adjudications.get("case_reviews", [])):
        raise BenchmarkError("duplicate case_review_id")
    item_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in packet.get("review_items", []):
        item_groups[(item["blind_variant"], item["session_id"])].append(item)

    variant_docs: dict[str, Any] = {}
    for blind in packet.get("blind_variants", []):
        payloads = [
            case
            for case in scoring_payload.get("case_payloads", [])
            if case["blind_variant"] == blind
        ]
        per_case = []
        for case in payloads:
            case_review = case_reviews.get(case["case_review_id"])
            if not case_review or not case_review.get("assessor_id"):
                raise BenchmarkError(f"{case['case_review_id']}: completed case review is required")
            for field in ("coherence", "selection_quality"):
                value = case_review.get(field)
                if not isinstance(value, (int, float)) or not 1 <= value <= 5:
                    raise BenchmarkError(f"{case['case_review_id']}: {field} must be in [1,5]")
            redundant_pairs = case_review.get("redundant_mechanism_pairs") or []
            normalized_pairs = []
            n_mechanisms = len(case.get("mechanisms") or [])
            for pair in redundant_pairs:
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or not all(isinstance(value, int) for value in pair)
                    or not 1 <= pair[0] < pair[1] <= n_mechanisms
                ):
                    raise BenchmarkError(
                        f"{case['case_review_id']}: invalid redundant mechanism pair {pair!r}"
                    )
                normalized_pairs.append(tuple(pair))
            if len(set(normalized_pairs)) != len(normalized_pairs):
                raise BenchmarkError(f"{case['case_review_id']}: duplicate redundancy pair")
            items = item_groups[(blind, case["session_id"])]
            selected_reviews = {item["review_id"]: final_reviews[item["review_id"]] for item in items}
            per_case.append(_case_metrics(case, items, selected_reviews, case_review))

        total_core = sum(case["core_gene_count"] for case in per_case)
        total_regs = sum(case["regulator_count"] for case in per_case)
        total_assessable = sum(case["assessable_link_count"] for case in per_case)
        telemetry = [case["telemetry"] for case in per_case]
        identifier_counts = {
            key: sum(case["identifier_status_counts"][key] for case in per_case)
            for key in ("resolved", "unverified", "refuted", "retracted")
        }
        aggregate = {
            "n_sessions": len(per_case),
            "coherence_mean": _mean(case["coherence"] for case in per_case),
            "selection_quality_mean": _mean(case["selection_quality"] for case in per_case),
            "claimed_core_gene_coverage": (
                sum(case["claimed_core_gene_count"] for case in per_case) / total_core
                if total_core
                else None
            ),
            "entailed_core_gene_coverage": (
                sum(case["entailed_core_gene_count"] for case in per_case) / total_core
                if total_core
                else None
            ),
            "claimed_regulator_coverage": (
                sum(case["claimed_regulator_count"] for case in per_case) / total_regs
                if total_regs
                else None
            ),
            "entailed_regulator_coverage": (
                sum(case["entailed_regulator_count"] for case in per_case) / total_regs
                if total_regs
                else None
            ),
            "mechanism_redundancy_rate_mean": _mean(
                case["mechanism_redundancy_rate"] for case in per_case
            ),
            "supporting_gene_jaccard_mean": _mean(
                case["supporting_gene_jaccard_mean"] for case in per_case
            ),
            "entailment_rate": (
                sum(case["entailed_link_count"] for case in per_case) / total_assessable
                if total_assessable
                else None
            ),
            "organism_match_exact_rate": _mean(
                case["organism_match_exact_rate"] for case in per_case
            ),
            "tissue_match_exact_rate": _mean(case["tissue_match_exact_rate"] for case in per_case),
            "cell_type_match_exact_rate": _mean(
                case["cell_type_match_exact_rate"] for case in per_case
            ),
            "condition_match_exact_rate": _mean(
                case["condition_match_exact_rate"] for case in per_case
            ),
            "paper_role_quality_mean": _mean(case["paper_role_quality"] for case in per_case),
            "selection_reason_quality_mean": _mean(
                case["selection_reason_quality"] for case in per_case
            ),
            "identifier_status_counts": identifier_counts,
            "searches_total": _strict_sum(item.get("searches") for item in telemetry),
            "turns_total": _strict_sum(item.get("turns") for item in telemetry),
            "retries_total": _strict_sum(item.get("retries") for item in telemetry),
            "tokens_total": _strict_sum(item.get("tokens") for item in telemetry),
            "cost_usd_total": _strict_sum(item.get("cost_usd") for item in telemetry),
            "duration_seconds_total": _strict_sum(
                item.get("duration_seconds") for item in telemetry
            ),
            "cost_usd_p95": _p95(item.get("cost_usd") for item in telemetry),
            "duration_seconds_p95": _p95(
                item.get("duration_seconds") for item in telemetry
            ),
            "citation_graph_events_total": _strict_sum(
                item.get("citation_graph_events") for item in telemetry
            ),
            "citation_graph_nodes_total": _strict_sum(
                item.get("citation_graph_nodes") for item in telemetry
            ),
            "citation_graph_edges_total": _strict_sum(
                item.get("citation_graph_edges") for item in telemetry
            ),
            "telemetry_complete": all(item.get("cumulative_complete") is True for item in telemetry),
        }
        variant_docs[blind] = {
            "compatibility": {
                "cohort_fingerprint": _cohort_fingerprint(payloads),
                "pairing_keys": sorted(case["session_id"] for case in payloads),
                "execution": scoring_payload.get("run_contracts", {})
                .get(blind, {})
                .get("execution", {}),
                "assessor_text_policy": scoring_payload.get("run_contracts", {})
                .get(blind, {})
                .get("text_policy"),
                "source_environment": scoring_payload.get("run_contracts", {})
                .get(blind, {})
                .get("environment", {}),
                "bundle_fingerprints": {
                    case["session_id"]: case["bundle_sha256"] for case in payloads
                },
            },
            "aggregate": aggregate,
            "per_case": per_case,
        }

    result = {
        "schema_version": SCHEMA_VERSION,
        "variants": variant_docs,
        "metric_semantics": {
            "entailment_source": "independent human review/adjudication; never identifier resolution",
            "not_assessable": "excluded from entailment/context denominators; never negative",
            "coverage": "micro-averaged over frozen core genes; regulators reported separately",
            "missing_telemetry": "unknown (null), never zero",
        },
    }
    _json_dump(Path(out), result)
    return result


def _one_variant(document: Mapping[str, Any], name: Optional[str]) -> tuple[str, Mapping[str, Any]]:
    variants = document.get("variants") or {}
    if name:
        if name not in variants:
            raise BenchmarkError(f"variant {name!r} is absent")
        return name, variants[name]
    if len(variants) != 1:
        raise BenchmarkError("metrics file has multiple variants; select one explicitly")
    return next(iter(variants.items()))


def _numeric_leaves(value: Any, prefix: str = "") -> dict[str, float]:
    leaves: dict[str, float] = {}
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            leaves.update(_numeric_leaves(child, name))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        leaves[prefix] = float(value)
    return leaves


def compare(
    baseline_path: str | Path,
    candidate_path: str | Path,
    out: str | Path,
    *,
    baseline_variant: Optional[str] = None,
    candidate_variant: Optional[str] = None,
) -> dict[str, Any]:
    """Compare variants only after enforcing cohort, pairing, execution, and text-policy parity."""
    baseline_doc = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    candidate_doc = json.loads(Path(candidate_path).read_text(encoding="utf-8"))
    baseline_name, baseline = _one_variant(baseline_doc, baseline_variant)
    candidate_name, candidate = _one_variant(candidate_doc, candidate_variant)
    left = baseline.get("compatibility") or {}
    right = candidate.get("compatibility") or {}
    mismatches = []
    for field in ("cohort_fingerprint", "pairing_keys", "execution", "assessor_text_policy"):
        if left.get(field) != right.get(field):
            mismatches.append(field)
    if mismatches:
        raise BenchmarkError(f"variants are not comparable; contract drift in {mismatches}")
    implementation_drift = {
        field: {"baseline": left.get(field), "candidate": right.get(field)}
        for field in ("bundle_fingerprints", "source_environment")
        if left.get(field) != right.get(field)
    }
    baseline_metrics = _numeric_leaves(baseline.get("aggregate") or {})
    candidate_metrics = _numeric_leaves(candidate.get("aggregate") or {})
    common = sorted(set(baseline_metrics) & set(candidate_metrics))
    result = {
        "schema_version": SCHEMA_VERSION,
        "baseline_variant": baseline_name,
        "candidate_variant": candidate_name,
        "compatibility": left,
        "implementation_drift": implementation_drift,
        "deltas": {
            key: {
                "baseline": baseline_metrics[key],
                "candidate": candidate_metrics[key],
                "absolute": candidate_metrics[key] - baseline_metrics[key],
            }
            for key in common
        },
    }
    _json_dump(Path(out), result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Literature benchmark preparation and scoring")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare_parser = sub.add_parser("prepare", help="validate inputs and regenerate bundles offline")
    prepare_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    prepare_parser.add_argument("--out", required=True)
    prepare_parser.add_argument("--dry-run", action="store_true")

    run_parser = sub.add_parser("run", help="execute a prepared baseline after recorded approval")
    run_parser.add_argument("--run-dir", required=True)
    run_parser.add_argument("--approval-file", required=True)

    text_parser = sub.add_parser("materialize-text", help="fetch assessor text for retained links")
    text_parser.add_argument("--run-dir", required=True)
    text_parser.add_argument("--out")
    text_parser.add_argument("--dry-run", action="store_true")

    packet_parser = sub.add_parser("make-review-packet", help="blind variants and make review forms")
    packet_parser.add_argument("--variant", action="append", type=_variant_arg, default=[])
    packet_parser.add_argument("--run-dir")
    packet_parser.add_argument("--variant-name", default="baseline")
    packet_parser.add_argument("--out", required=True)
    packet_parser.add_argument("--seed", default="literature-v1")

    score_parser = sub.add_parser("score", help="score completed human adjudications")
    score_parser.add_argument("--packet", required=True)
    score_parser.add_argument("--adjudications", required=True)
    score_parser.add_argument("--out", required=True)

    compare_parser = sub.add_parser("compare", help="compare compatible metric artifacts")
    compare_parser.add_argument("--baseline", required=True)
    compare_parser.add_argument("--candidate", required=True)
    compare_parser.add_argument("--baseline-variant")
    compare_parser.add_argument("--candidate-variant")
    compare_parser.add_argument("--out", required=True)

    freeze_parser = sub.add_parser("freeze", help="hash-lock a complete reviewed baseline")
    freeze_parser.add_argument("--run-dir", required=True)
    freeze_parser.add_argument("--packet", required=True)
    freeze_parser.add_argument("--adjudications", required=True)
    freeze_parser.add_argument("--metrics", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        result = prepare(args.manifest, args.out, dry_run=args.dry_run)
    elif args.command == "run":
        result = run_paid_baseline(args.run_dir, args.approval_file)
    elif args.command == "materialize-text":
        out = args.out or str(Path(args.run_dir) / "assessor_text")
        result = materialize_text(args.run_dir, out, dry_run=args.dry_run)
    elif args.command == "make-review-packet":
        variants = dict(args.variant)
        if args.run_dir:
            variants[args.variant_name] = Path(args.run_dir).resolve()
        result = make_review_packet(variants, args.out, seed=args.seed)
    elif args.command == "score":
        result = score(args.packet, args.adjudications, args.out)
    elif args.command == "compare":
        result = compare(
            args.baseline,
            args.candidate,
            args.out,
            baseline_variant=args.baseline_variant,
            candidate_variant=args.candidate_variant,
        )
    elif args.command == "freeze":
        result = freeze_baseline(
            args.run_dir,
            packet_path=args.packet,
            adjudications_path=args.adjudications,
            metrics_path=args.metrics,
        )
    else:  # pragma: no cover - argparse enforces a command
        raise AssertionError(args.command)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
