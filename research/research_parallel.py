"""
research.research_parallel — parallel literature-research runner (executor 2, spec M4).

Fans out one **isolated** Claude Agent SDK session per gene program under a Python
``asyncio`` orchestrator bounded by a ``Semaphore`` (spec: k=3-5). There is NO manager
agent — parallelism is controlled entirely here, in Python. Each session:

  * has a fresh ``cwd`` workspace containing ONLY its own program bundle
    (``<workspaces>/{program_id}/program_bundles/{program_id}.json``), so the agent's
    ``Read`` tool can never see another program's bundle;
  * is given the shared research protocol (``research/protocol.md``) as its system prompt;
  * is wired to the read-only literature tools via an in-process ``literature`` MCP server
    (``research/literature.py``) whose tools call PubMed / OpenAlex / Crossref via ``httpx``
    — nothing external is launched or inherited, so it works identically headless and
    interactive — plus an in-process ``gpi`` MCP server exposing a single ``submit_result`` tool;
  * is sandboxed (``setting_sources=[]`` + ``strict_mcp_config=True`` + a side-effect
    ``disallowed_tools`` denylist);
  * is capped by SDK-native ``max_turns`` and ``max_budget_usd`` (per-session cost cap),
    and by a Python ``asyncio`` per-program wall-clock timeout.

The AGENT decides every query; deterministic code never researches on its own initiative.
The literature tools execute their HTTP calls inside this process (which holds the API
keys) when the agent invokes them — the SDK bridges the in-process server to the
subprocess. This module captures the agent's single ``submit_result``
payload, records an audit trace, and — on any failure — writes a deterministic minimal
``ResearchResult`` so downstream stages stay whole.

The submitted payload is written to ``{audit_dir}/{program_id}.raw_payload.json`` the moment it
arrives, BEFORE validation: research is the step that costs money, so the bytes the user paid for
must survive anything that goes wrong afterwards (they can be re-normalized offline, for free).

Run:
    python -m research.research_parallel --bundles program_bundles/ \
        --out-dir research_results/ --concurrency 4 \
        --model claude-sonnet-4-6 --max-turns 30 --max-budget-usd 1.0
    python -m research.research_parallel ... --dry-run   # build+validate config, no API spend
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)
from pydantic import ValidationError

from research.literature import (
    LITERATURE_SERVER_NAME,
    LiteratureClient,
    build_literature_mcp_server,
)
from research.schema import AgentResearchResult, ResearchResult, submit_result_tool_schema

# Imported at MODULE SCOPE on purpose: a broken install (this used to load
# literature-review/kernel.py by path — a file no wheel contains) must fail at process start,
# BEFORE any session is launched and any money is spent. Buried in the per-program try/except
# below, the same ImportError was reported as "payload failed schema validation", which then
# triggered a retry that could not possibly help.
from research.verify import normalize_agent_result

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- constants

# The literature tools are served by a single in-process SDK MCP server
# (research/literature.py) whose tools call PubMed / OpenAlex / Crossref directly via httpx.
# Nothing external is launched or inherited, so the tools are available IDENTICALLY headless
# and interactive, and no server install / MCP config is required.

# Tools every session may use regardless of source (exact matches). ``Read`` stays allowed so
# the agent can read its own bundle from the session cwd.
BASE_ALLOWED_TOOLS: List[str] = ["mcp__gpi__submit_result", "Read"]

# Belt-and-suspenders denylist (in addition to deny-by-default + strict_mcp_config): the
# side-effecting / off-task built-ins the research agent must never use. This is what the
# earlier headless run leaked into (Bash/WebSearch/WebFetch/Skill/...) via user allow-rules.
DISALLOWED_TOOLS: List[str] = [
    "Bash", "Write", "Edit", "NotebookEdit", "WebSearch", "WebFetch",
    "Agent", "Task", "Skill", "SendMessage", "KillShell", "TodoWrite",
]


def resolve_allowed_tools() -> List[str]:
    """The read-only allowlist for one session: the single in-process ``literature`` server
    family, plus ``Read`` and the submit tool."""
    return [f"mcp__{LITERATURE_SERVER_NAME}__*"] + list(BASE_ALLOWED_TOOLS)

# In-process SDK MCP server that hosts the submit_result tool.
SUBMIT_SERVER_NAME = "gpi"
SUBMIT_TOOL_NAME = "submit_result"

# Repo root = parent of this package dir (research/..). Used only as a development
# fallback for .env; protocol.md is packaged alongside this module.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROTOCOL_PATH = Path(__file__).resolve().parent / "protocol.md"


# ----------------------------------------------------------------------------- env / config

# The research subagents authenticate the local ``claude`` CLI via the user's Claude.ai
# subscription (``claude login``), NOT an Anthropic API key. Loading ANTHROPIC_API_KEY here
# would make the CLI bill API credit and shadow the subscription (the SDK warns
# "claude.ai connectors are disabled because ANTHROPIC_API_KEY … takes precedence"). So the
# research env deliberately EXCLUDES the API key; the literature keys (NCBI/OpenAlex/PUBMED)
# still load because the in-process literature server needs them. Batch steps (executor 3)
# set ANTHROPIC_API_KEY themselves — research runs on the subscription, batch on API credit.
_RESEARCH_ENV_SKIP = frozenset({"ANTHROPIC_API_KEY"})


def load_env_file(
    path: Optional[Path] = None, skip: frozenset = _RESEARCH_ENV_SKIP
) -> None:
    """Populate ``os.environ`` from the current project's ``.env``.

    Deliberately minimal (no dependency on python-dotenv). Never overwrites a key that is
    already present in the environment, so an explicitly-exported value wins. Keys in
    ``skip`` are never loaded (default: ANTHROPIC_API_KEY, so the research CLI uses the
    Claude.ai subscription rather than API credit).
    """
    if path is None:
        path = Path.cwd() / ".env"
        if not path.exists() and (_REPO_ROOT / ".env").exists():
            path = _REPO_ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ and key not in skip:
            os.environ[key] = val


# ------------------------------------------------------------------- submit_result tool

def _make_submit_tool(holder: Dict[str, Any]):
    """Build a fresh ``submit_result`` SDK MCP tool that captures the agent's payload.

    ``holder`` is a per-program mutable dict; the tool writes the raw ``ResearchResult``
    payload into ``holder['payload']`` and increments ``holder['calls']``. Validation into
    the pydantic model happens later in Python — the tool itself is tolerant so a slightly
    off-schema submission is still captured (and then validated / flagged), never dropped.
    """

    @tool(SUBMIT_TOOL_NAME, "Submit your findings for this program (call exactly once). Attach "
          "the papers that establish each mechanism inline in that mechanism's papers[]; do not "
          "assign evidence ids or status, and return at most 3 mechanisms.",
          submit_result_tool_schema())
    async def submit_result(args: Dict[str, Any]) -> Dict[str, Any]:
        # Backup capture; the authoritative payload is read from the ToolUseBlock stream in
        # _drive_once (works even under a mocked query() with no live tool bridge).
        holder["handler_payload"] = args
        pid = args.get("program_id", "?") if isinstance(args, dict) else "?"
        return {
            "content": [
                {"type": "text", "text": f"submit_result received for program {pid}. Recorded."}
            ]
        }

    return submit_result


# --------------------------------------------------------------------------- permissions

def _tool_allowed(tool_name: str, allowed: List[str]) -> bool:
    """True iff ``tool_name`` is permitted by ``allowed`` (prefix "*" or exact)."""
    for pat in allowed:
        if pat.endswith("*"):
            if tool_name.startswith(pat[:-1]):
                return True
        elif tool_name == pat:
            return True
    return False


def _make_can_use_tool(allowed: List[str]):
    """Build a deny-by-default permission callback bound to this session's allowlist.

    ``allowed_tools`` already auto-approves the read-only families without a prompt; this
    callback is the second line of defence so that any tool OUTSIDE the allowlist is denied
    immediately instead of raising a permission prompt that would hang a headless session.
    NOTE: whole-tool ``mcp__…__*`` entries are auto-approved by the SDK *before* this
    callback runs (``CanUseToolShadowedWarning``); with ``setting_sources=[]`` (the local
    path) there are no external allow-rules, so nothing outside ``allowed`` slips through.
    """
    allowed = list(allowed)

    async def _can_use_tool(tool_name: str, tool_input: Dict[str, Any], context: Any):
        if _tool_allowed(tool_name, allowed):
            return PermissionResultAllow()
        return PermissionResultDeny(
            message=f"Tool '{tool_name}' is not in the research allowlist (read-only literature + submit_result).",
        )

    return _can_use_tool


# --------------------------------------------------------------------------- options build

def read_protocol(protocol_path: Path = _PROTOCOL_PATH) -> str:
    if not protocol_path.exists():
        raise FileNotFoundError(f"Research protocol not found: {protocol_path}")
    return protocol_path.read_text(encoding="utf-8")


def build_options(
    *,
    workspace: Path,
    system_prompt: str,
    submit_holder: Dict[str, Any],
    model: str,
    max_turns: int,
    max_budget_usd: float,
    literature_client: Optional[LiteratureClient] = None,
) -> ClaudeAgentOptions:
    """Construct the per-program ``ClaudeAgentOptions`` (one isolated session).

    Registers the in-process ``literature`` server (research/literature.py) alongside a fresh
    in-process ``gpi`` server holding this program's ``submit_result`` tool — the agent's
    literature tools run in THIS process, so retrieval works identically headless and
    interactive with nothing external to install. The session is sandboxed
    (``setting_sources=[]`` + ``strict_mcp_config=True``), so no user allow-rule can shadow
    the deny callback. Also applies the read-only allowlist, a side-effect denylist, and the
    SDK-native turn/budget caps.
    """
    submit_tool = _make_submit_tool(submit_holder)
    gpi_server = create_sdk_mcp_server(name=SUBMIT_SERVER_NAME, version="1.0.0", tools=[submit_tool])

    mcp_servers: Dict[str, Any] = {
        SUBMIT_SERVER_NAME: gpi_server,
        LITERATURE_SERVER_NAME: build_literature_mcp_server(literature_client),
    }

    allowed = resolve_allowed_tools()
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        allowed_tools=list(allowed),
        disallowed_tools=list(DISALLOWED_TOOLS),   # explicit denylist (belt-and-suspenders)
        can_use_tool=_make_can_use_tool(allowed),
        permission_mode="default",          # deny-by-default via can_use_tool; no blanket bypass
        setting_sources=[],                 # sandbox: isolate from user/project settings
        strict_mcp_config=True,             # ignore any settings-file MCP servers when sandboxed
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,      # per-session cost cap (spec 8 gate)
        model=model,
        cwd=str(workspace),
    )


def build_prompt(program_id: str) -> str:
    """Short launch instruction; the bundle path is relative to the session cwd."""
    return (
        f"Research program {program_id}. Read program_bundles/{program_id}.json (the only "
        f"file available to you), follow the protocol exactly, and call the submit_result "
        f"tool exactly once with your complete ResearchResult."
    )


async def _prompt_stream(text: str):
    """Streaming-mode input for ``query()``.

    ``can_use_tool`` requires streaming input (an ``AsyncIterable[dict]``) — a plain str
    raises "can_use_tool callback requires streaming mode" in claude-agent-sdk 0.2.116.
    The envelope mirrors the SDK's own string->stream conversion
    (``_internal/client.py``): a single ``type: user`` message that ends the input.
    """
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
    }


# ------------------------------------------------------------------------- workspace prep

def _read_bundle_program_id(bundle_path: Path) -> str:
    """Program id for a bundle = its JSON ``program_id`` (fallback: filename stem)."""
    try:
        data = json.loads(bundle_path.read_text(encoding="utf-8"))
        pid = data.get("program_id")
        if isinstance(pid, str) and pid.strip():
            return pid.strip()
    except Exception:  # noqa: BLE001 - fall back to stem, but never silently mislabel later
        pass
    return bundle_path.stem


def _prepare_workspace(bundle_path: Path, workspaces_root: Path, program_id: str) -> Path:
    """Create ``<workspaces_root>/{program_id}/program_bundles/{program_id}.json`` holding
    ONLY this program's bundle, and return the workspace root (the session ``cwd``)."""
    workspace = workspaces_root / program_id
    bundles_dir = workspace / "program_bundles"
    if workspace.exists():
        shutil.rmtree(workspace)
    bundles_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundle_path, bundles_dir / f"{program_id}.json")
    return workspace


# ----------------------------------------------------------------------------- audit / io

def _summarize_args(args: Any, limit: int = 240) -> str:
    """Compact one-line summary of a tool call's input (for the audit trace).

    Never dumps a full ``submit_result`` payload; just enough to audit what was queried
    (and to support the downstream 'no PMID/DOI outside the tool trace' check)."""
    try:
        s = json.dumps(args, default=str, sort_keys=True)
    except Exception:  # noqa: BLE001
        s = str(args)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _write_audit(
    audit_dir: Path,
    program_id: str,
    *,
    prompt: str,
    model: str,
    mcp_server_names: List[str],
    tool_trace: List[Dict[str, Any]],
    cost_usd: Optional[float],
    num_turns: Optional[int],
    status: str,
    error: Optional[str] = None,
    attempts: int = 1,
) -> Path:
    audit_path = audit_dir / f"{program_id}.audit.json"
    _write_json(
        audit_path,
        {
            "program_id": program_id,
            "prompt": prompt,
            "model": model,
            "mcp_servers": sorted(mcp_server_names),
            "tool_trace": tool_trace,
            "cost_usd": cost_usd,
            "num_turns": num_turns,
            "status": status,
            "attempts": attempts,
            "error": error,
        },
    )
    return audit_path


def _fallback_result(program_id: str, reason: str, **extra_meta: Any) -> ResearchResult:
    """Deterministic minimal, schema-valid ResearchResult for any failure path.

    Keeps downstream deterministic enrichment intact and marks the literature section
    incomplete (never fabricates evidence)."""
    meta = {"status": "incomplete", "reason": reason}
    meta.update(extra_meta)
    return ResearchResult(
        program_id=program_id,
        agent_summary="literature research incomplete",
        meta=meta,
    )


# --------------------------------------------------------------------------- session driver

async def _drive_once(
    *,
    prompt: str,
    options: ClaudeAgentOptions,
    submit_holder: Dict[str, Any],
    per_program_timeout: float,
    program_id: str = "",
    progress_cb: Optional[Callable[[str, dict], None]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run ONE query() session to completion (or timeout). Returns (tool_trace, result_info).

    ``result_info`` carries the terminal ResultMessage fields (cost/turns/subtype/is_error).
    Raises ``asyncio.TimeoutError`` on wall-clock overrun; propagates SDK exceptions.

    ``progress_cb`` (if given) is called with (event_type, payload) on each literature tool
    call — the authoritative live signal (``can_use_tool`` is shadowed for whole ``mcp__*``
    tools, so this ToolUseBlock loop is the only place that sees every query). It is a bare
    callable (no ``gpi`` import here) and each call is a single small append downstream.
    """
    tool_trace: List[Dict[str, Any]] = []
    result_info: Dict[str, Any] = {}
    submit_tool_fullname = f"mcp__{SUBMIT_SERVER_NAME}__{SUBMIT_TOOL_NAME}"

    async def _run() -> None:
        async for msg in query(prompt=_prompt_stream(prompt), options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        tool_trace.append(
                            {"tool": block.name, "args_summary": _summarize_args(block.input)}
                        )
                        # Authoritative submit capture: the ToolUseBlock input IS the payload.
                        if block.name == submit_tool_fullname:
                            submit_holder["payload"] = block.input
                            submit_holder["calls"] = submit_holder.get("calls", 0) + 1
                        elif progress_cb is not None:
                            # A literature query — surface it live (fire-and-forget).
                            try:
                                progress_cb("agent_tool_call", {
                                    "program_id": program_id,
                                    "tool": block.name.replace("mcp__", "").replace("__", "."),
                                    "turn": len(tool_trace),
                                })
                            except Exception:
                                pass
            elif isinstance(msg, ResultMessage):
                result_info.update(
                    {
                        "subtype": msg.subtype,
                        "is_error": msg.is_error,
                        "num_turns": msg.num_turns,
                        "total_cost_usd": msg.total_cost_usd,
                        "result": msg.result,
                    }
                )

    await asyncio.wait_for(_run(), timeout=per_program_timeout)
    return tool_trace, result_info


async def _handle_one_program(
    bundle_path: Path,
    *,
    out_dir: Path,
    audit_dir: Path,
    workspaces_root: Path,
    system_prompt: str,
    model: str,
    max_turns: int,
    max_budget_usd: float,
    per_program_timeout: float,
    literature_client: Optional[LiteratureClient] = None,
    progress_cb: Optional[Callable[[str, dict], None]] = None,
    max_attempts: int = 2,
) -> Path:
    """Drive one program end-to-end: prepare workspace, run the session (retry once on
    transient failure), persist the raw submit_result payload, validate it, and always write a
    schema-valid ResearchResult + an audit record. Falls back deterministically on any
    failure. Returns the path to the written ``research_results/{program_id}.json``.

    Retries are for failures a retry can actually fix (no submit_result, a transient SDK error,
    an agent-authored payload that fails schema validation). An INFRASTRUCTURE failure is not one
    of them: it stops the loop immediately rather than paying for a second session that would hit
    the same wall."""
    program_id = _read_bundle_program_id(bundle_path)
    prompt = build_prompt(program_id)
    server_names = sorted([LITERATURE_SERVER_NAME, SUBMIT_SERVER_NAME])

    last_error: Optional[str] = None
    last_trace: List[Dict[str, Any]] = []
    last_result_info: Dict[str, Any] = {}
    attempts = 0

    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        submit_holder: Dict[str, Any] = {}
        try:
            workspace = _prepare_workspace(bundle_path, workspaces_root, program_id)
            options = build_options(
                workspace=workspace,
                system_prompt=system_prompt,
                submit_holder=submit_holder,
                model=model,
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
                literature_client=literature_client,
            )
            tool_trace, result_info = await _drive_once(
                prompt=prompt,
                options=options,
                submit_holder=submit_holder,
                per_program_timeout=per_program_timeout,
                program_id=program_id,
                progress_cb=progress_cb,
            )
            last_trace, last_result_info = tool_trace, result_info

            payload = submit_holder.get("payload")
            if payload is None:
                # No submit_result -> could be max_turns / budget / model just didn't call it.
                subtype = result_info.get("subtype", "")
                if subtype == "error_max_turns" or (result_info.get("num_turns") or 0) >= max_turns:
                    last_error = "max_turns reached without submit_result"
                elif "budget" in str(subtype).lower():
                    last_error = "budget exceeded without submit_result"
                else:
                    last_error = "session ended without calling submit_result"
                continue  # transient-ish; retry once then fall back

            # Persist the bytes the user PAID FOR the moment they arrive — before validation,
            # before normalization, before anything that can throw. Whatever goes wrong
            # downstream, the raw payload is on disk and can be re-normalized offline for free.
            raw_payload_path = audit_dir / f"{program_id}.raw_payload.json"
            try:
                _write_json(raw_payload_path, payload)
                logger.info(
                    "Program %s: raw submit_result payload -> %s", program_id, raw_payload_path
                )
            except OSError as exc:  # a write failure must not discard a good session
                logger.warning(
                    "Program %s: could not persist the raw payload to %s: %s",
                    program_id, raw_payload_path, exc,
                )

            # Belt-and-suspenders: warn on a LEGACY payload shape (claims[]/inline citations).
            # Pydantic ignores unknown keys, so a legacy submission would silently normalize to
            # empty evidence — surface it instead of failing silently.
            if isinstance(payload, dict):
                _mechs = payload.get("candidate_mechanisms") or []
                if "claims" in payload or any(
                    isinstance(m, dict) and "citations" in m for m in _mechs
                ):
                    logger.warning(
                        "Program %s: submit_result payload uses the LEGACY claims/citations shape; "
                        "the current schema attaches papers[] to each mechanism — legacy fields are "
                        "ignored and evidence may be empty.",
                        program_id,
                    )

            # Validate the flat agent payload, then normalize to the canonical schema
            # (build the dedup'd evidence pool + assign ids). The verifier resolves it later.
            #
            # The two failure modes here are NOT the same and must never be conflated:
            #   * ValidationError -> the AGENT produced bad JSON. Retrying can fix that.
            #   * anything else   -> INFRASTRUCTURE (a bad install, a broken import, disk).
            #     Retrying cannot fix it and only doubles the bill, so stop after recording it.
            #     The raw payload is already on disk (above) and can be re-normalized for free.
            try:
                rr = normalize_agent_result(AgentResearchResult.model_validate(payload))
            except ValidationError as ve:
                last_error = f"submit_result payload failed schema validation: {ve}"
                continue
            except Exception as exc:  # noqa: BLE001 - infrastructure, not the agent's fault
                last_error = (
                    f"infrastructure error while normalizing payload: "
                    f"{type(exc).__name__}: {exc}"
                )
                logger.error(
                    "Program %s: %s — NOT retrying (a retry cannot fix this and would spend "
                    "again); the raw payload is preserved at %s.",
                    program_id, last_error, raw_payload_path,
                )
                break  # out of the retry loop -> deterministic fallback below

            # Enforce the program id echo (protocol says echo exactly; correct if drifted).
            if rr.program_id != program_id:
                logger.warning(
                    "Program %s: agent echoed program_id=%r; overriding to bundle id.",
                    program_id, rr.program_id,
                )
                rr.program_id = program_id

            rr.meta.update(
                {
                    "status": "ok",
                    "model": model,
                    "cost_usd": result_info.get("total_cost_usd"),
                    "num_turns": result_info.get("num_turns"),
                    "attempts": attempt,
                    "n_submit_calls": submit_holder.get("calls", 1),
                    "tool_trace_path": str((audit_dir / f"{program_id}.audit.json")),
                    "raw_payload_path": str(raw_payload_path),
                }
            )
            if submit_holder.get("calls", 1) > 1:
                rr.meta["warning"] = "submit_result called more than once; kept the last payload."

            result_path = out_dir / f"{program_id}.json"
            _write_json(result_path, json.loads(rr.model_dump_json()))
            _write_audit(
                audit_dir, program_id,
                prompt=prompt, model=model, mcp_server_names=server_names,
                tool_trace=tool_trace,
                cost_usd=result_info.get("total_cost_usd"),
                num_turns=result_info.get("num_turns"),
                status="ok", attempts=attempt,
            )
            logger.info(
                "Program %s: ok (turns=%s, cost=%s, mechanisms=%d, evidence=%d)",
                program_id, result_info.get("num_turns"), result_info.get("total_cost_usd"),
                len(rr.candidate_mechanisms), len(rr.evidence),
            )
            if progress_cb is not None:
                try:
                    progress_cb("agent_finished", {
                        "program_id": program_id, "status": "ok",
                        "num_turns": result_info.get("num_turns"),
                        "cost_usd": result_info.get("total_cost_usd"),
                        "n_mechanisms": len(rr.candidate_mechanisms),
                        "n_evidence": len(rr.evidence),
                    })
                except Exception:
                    pass
            return result_path

        except asyncio.TimeoutError:
            last_error = f"per_program_timeout ({per_program_timeout}s) exceeded"
            logger.warning("Program %s attempt %d: %s", program_id, attempt, last_error)
        except Exception as exc:  # noqa: BLE001 - any SDK/transport error is retry-then-fallback
            last_error = f"SDK error: {type(exc).__name__}: {exc}"
            logger.warning("Program %s attempt %d: %s", program_id, attempt, last_error)

    # ---- all attempts exhausted -> deterministic fallback ----
    rr = _fallback_result(
        program_id,
        reason=last_error or "unknown failure",
        model=model,
        attempts=attempts,
        cost_usd=last_result_info.get("total_cost_usd"),
        num_turns=last_result_info.get("num_turns"),
        tool_trace_path=str((audit_dir / f"{program_id}.audit.json")),
    )
    result_path = out_dir / f"{program_id}.json"
    _write_json(result_path, json.loads(rr.model_dump_json()))
    _write_audit(
        audit_dir, program_id,
        prompt=prompt, model=model, mcp_server_names=server_names,
        tool_trace=last_trace,
        cost_usd=last_result_info.get("total_cost_usd"),
        num_turns=last_result_info.get("num_turns"),
        status="incomplete", attempts=attempts, error=last_error,
    )
    logger.error("Program %s: fallback written (reason: %s)", program_id, last_error)
    if progress_cb is not None:
        try:
            progress_cb("agent_finished", {
                "program_id": program_id, "status": "incomplete",
                "num_turns": last_result_info.get("num_turns"),
                "cost_usd": last_result_info.get("total_cost_usd"),
                "error": last_error,
            })
        except Exception:
            pass
    return result_path


# --------------------------------------------------------------------------- public runner

async def run_research(
    bundle_paths: List[Path],
    *,
    out_dir: str | Path = "research_results",
    audit_dir: str | Path = "research_audit",
    concurrency: int = 4,
    model: str = "claude-sonnet-4-6",
    max_turns: int = 30,
    max_budget_usd: float = 1.0,
    per_program_timeout: float = 600,
    progress_cb: Optional[Callable[[str, dict], None]] = None,
) -> List[Path]:
    """Run per-program research sessions concurrently, bounded by ``Semaphore(concurrency)``.

    One isolated Agent SDK session per bundle; each writes a schema-valid
    ``research_results/{program_id}.json`` (real result or deterministic fallback) and an
    ``research_audit/{program_id}.audit.json`` record. Returns the result paths.

    The literature tools are served by an in-process SDK MCP server (research/literature.py)
    — nothing external to install, works headless. A single ``LiteratureClient`` is shared
    across concurrent sessions so its rate limiters honour the global NCBI/OpenAlex limits.

    ``progress_cb`` (optional, a bare ``(event_type, payload)`` callable — no ``gpi`` import
    here) receives ``agent_queued`` / ``agent_started`` / ``agent_tool_call`` /
    ``agent_finished`` events so the caller can render a live per-agent view. Each call is a
    single cheap append downstream and is fire-and-forget.
    """
    if not bundle_paths:
        raise ValueError("run_research received no bundle paths.")
    if not (1 <= concurrency <= 16):
        raise ValueError(f"concurrency must be in [1,16], got {concurrency}")

    load_env_file()
    out_dir = Path(out_dir)
    audit_dir = Path(audit_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    workspaces_root = out_dir.parent / "workspaces"

    system_prompt = read_protocol()

    # One shared literature client for the whole run so per-service rate limiters coordinate
    # across concurrent sessions. Closed in the finally below.
    lit_client = LiteratureClient()

    # Deterministic order so runs are reproducible / auditable.
    bundle_paths = sorted(Path(b) for b in bundle_paths)
    sem = asyncio.Semaphore(concurrency)

    def _emit(event_type: str, payload: dict) -> None:
        if progress_cb is not None:
            try:
                progress_cb(event_type, payload)
            except Exception:
                pass

    async def _guarded(bundle_path: Path) -> Path:
        pid = _read_bundle_program_id(bundle_path)
        _emit("agent_queued", {"program_id": pid})  # waiting on the semaphore
        async with sem:
            _emit("agent_started", {"program_id": pid})  # a slot opened → session begins
            return await _handle_one_program(
                bundle_path,
                out_dir=out_dir,
                audit_dir=audit_dir,
                workspaces_root=workspaces_root,
                system_prompt=system_prompt,
                model=model,
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
                per_program_timeout=per_program_timeout,
                literature_client=lit_client,
                progress_cb=progress_cb,
            )

    try:
        results = await asyncio.gather(*(_guarded(b) for b in bundle_paths))
    finally:
        await lit_client.aclose()
    logger.info("run_research complete: %d program(s) -> %s", len(results), out_dir)
    return list(results)


# -------------------------------------------------------------------------------- dry-run

def dry_run(
    bundle_paths: List[Path],
    *,
    out_dir: str | Path = "research_results",
    model: str = "claude-sonnet-4-6",
    max_turns: int = 30,
    max_budget_usd: float = 1.0,
) -> Dict[str, Any]:
    """Build + validate the full launch config for each bundle WITHOUT launching sessions.

    No API spend, no external process is started, no literature tool is ever called.
    Verifies the submit_result tool + schema construct and that a valid ``ClaudeAgentOptions``
    is built per program. The in-process ``literature`` server is built (never called).
    Returns a summary dict.
    """
    load_env_file()
    system_prompt = read_protocol()
    schema = submit_result_tool_schema()
    workspaces_root = Path(out_dir).parent / "workspaces"

    per_program: List[Dict[str, Any]] = []
    for bundle_path in sorted(Path(b) for b in bundle_paths):
        program_id = _read_bundle_program_id(bundle_path)
        holder: Dict[str, Any] = {}
        options = build_options(
            workspace=workspaces_root / program_id,   # not created in dry-run
            system_prompt=system_prompt,
            submit_holder=holder,
            model=model,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
        )
        per_program.append(
            {
                "program_id": program_id,
                "bundle": str(bundle_path),
                "prompt": build_prompt(program_id),
                "mcp_server_names": sorted(options.mcp_servers.keys()),
                "allowed_tools": list(options.allowed_tools),
                "disallowed_tools": list(getattr(options, "disallowed_tools", []) or []),
                "permission_mode": options.permission_mode,
                "setting_sources": options.setting_sources,
                "strict_mcp_config": getattr(options, "strict_mcp_config", None),
                "max_turns": options.max_turns,
                "max_budget_usd": options.max_budget_usd,
                "model": options.model,
                "options_ok": isinstance(options, ClaudeAgentOptions),
            }
        )

    summary = {
        "n_bundles": len(per_program),
        "submit_tool_schema_title": schema.get("title"),
        "submit_tool_schema_type": schema.get("type"),
        "allowed_tools": resolve_allowed_tools(),
        "permission_mode": "default",
        "per_program": per_program,
    }
    return summary


# ------------------------------------------------------------------------------------ CLI

def _collect_bundles(bundles_arg: str) -> List[Path]:
    """Resolve --bundles (a directory of *.json, or a single *.json) to sorted paths."""
    p = Path(bundles_arg)
    if p.is_dir():
        paths = sorted(p.glob("*.json"))
        if not paths:
            raise FileNotFoundError(f"No *.json bundles found in directory {p}")
        return paths
    if p.is_file():
        return [p]
    raise FileNotFoundError(f"--bundles path does not exist: {p}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parallel literature-research runner (executor 2): one isolated Claude "
        "Agent SDK session per program bundle, fanned out under asyncio+Semaphore.",
    )
    parser.add_argument("--bundles", required=True, help="Directory of program_bundles/*.json (or one *.json).")
    parser.add_argument("--out-dir", default="research_results", help="Output dir for ResearchResult JSONs.")
    parser.add_argument("--audit-dir", default="research_audit", help="Output dir for per-program audit JSONs.")
    parser.add_argument("--concurrency", type=int, default=4, help="Max concurrent sessions (k=3-5 recommended).")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Anthropic model for the subagents.")
    parser.add_argument("--max-turns", type=int, default=30, help="SDK max_turns per session.")
    parser.add_argument("--max-budget-usd", type=float, default=1.0, help="SDK per-session cost cap (USD).")
    parser.add_argument("--per-program-timeout", type=float, default=600, help="Wall-clock timeout per program (s).")
    parser.add_argument("--dry-run", action="store_true", help="Build+validate config only; no sessions, no spend.")
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    bundles = _collect_bundles(args.bundles)

    if args.dry_run:
        summary = dry_run(
            bundles,
            out_dir=args.out_dir,
            model=args.model,
            max_turns=args.max_turns,
            max_budget_usd=args.max_budget_usd,
        )
        print(json.dumps(summary, indent=2))
        return

    paths = asyncio.run(
        run_research(
            bundles,
            out_dir=args.out_dir,
            audit_dir=args.audit_dir,
            concurrency=args.concurrency,
            model=args.model,
            max_turns=args.max_turns,
            max_budget_usd=args.max_budget_usd,
            per_program_timeout=args.per_program_timeout,
        )
    )
    logger.info("Wrote %d result file(s) to %s", len(paths), args.out_dir)


if __name__ == "__main__":
    main()
