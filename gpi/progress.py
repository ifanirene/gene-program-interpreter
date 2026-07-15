"""Progress telemetry for the GPI pipeline (opt-in, additive).

One append-only event log (``progress.jsonl``) is the single source of truth. A background
tailer thread folds new events into a reduced snapshot (``progress.json``, written atomically
and throttled) and drives a terminal renderer. ALL emitters — the pipeline driver, the
in-process research callback, and the subprocess step modules — append via ``emit_progress``;
the tailer is the sole reducer/renderer, so in-process and subprocess events unify.

Design constraints (see plan Part C5):
  * **lightweight** — the hot path is a single small ``O_APPEND`` write (no fsync, no reduce,
    no snapshot, no network); reduce/snapshot/render happen off the hot path in the tailer.
  * **non-disruptive** — the tailer runs on its own thread and never touches the asyncio loop
    that drives the research agents; emits are fire-and-forget and exception-proof.
  * **realtime** — the jsonl is appended the instant an event occurs; the terminal repaints
    live and the snapshot refreshes ~1/s.

``progress.jsonl`` lines are small (< PIPE_BUF), so concurrent appends from the driver and
child step processes are atomic and never interleave-corrupt.

Reading ``progress.json`` (monitors, the Claude skill)
-----------------------------------------------------
* ``heartbeat_at`` is the **liveness** signal: it is re-stamped on every snapshot write and so
  advances ~1/s for as long as the process is alive, event stream or not. ``updated_at`` is the
  time of the last *event* and may legitimately sit still for many minutes while a research
  agent thinks — **a frozen ``updated_at`` is not a hang.** Judge liveness by ``heartbeat_at``
  (and ``pid``); judge activity by ``updated_at``.
* A failed step keeps its reason in ``steps[i].error``, and ``failed_step`` names it — a failed
  run always states its cause.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Tuple

from .log_redaction import redact_text

# --------------------------------------------------------------------------- event types
RUN_START = "run_start"
STEP_START = "step_start"
STEP_PROGRESS = "step_progress"
STEP_DONE = "step_done"
RESEARCH_START = "research_start"
AGENT_QUEUED = "agent_queued"
AGENT_STARTED = "agent_started"
AGENT_TOOL_CALL = "agent_tool_call"
AGENT_FINISHED = "agent_finished"
RESEARCH_DONE = "research_done"
RUN_DONE = "run_done"

_AGENT_DONE_STATES = frozenset({"ok", "incomplete", "done", "failed"})


# --------------------------------------------------------------------------- hot-path writer
def emit_progress(jsonl_path: "str | os.PathLike | None", event_type: str,
                  payload: Optional[Dict[str, Any]] = None) -> None:
    """Append one event line to ``jsonl_path``. This is the entire hot-path cost.

    A single ``O_APPEND`` ``os.write`` of a small line — atomic across processes, no fsync,
    no reduce, no snapshot. Fire-and-forget: any error is swallowed so telemetry can never
    break the pipeline. Importable by subprocess step modules with no ``rich``/emitter
    dependency. A falsy ``jsonl_path`` makes this a no-op (progress disabled).
    """
    if not jsonl_path:
        return
    try:
        rec: Dict[str, Any] = {"ts": round(time.time(), 3), "type": event_type}
        if payload:
            rec.update(payload)
        line = (json.dumps(rec, separators=(",", ":"), default=str) + "\n").encode("utf-8")
        fd = os.open(str(jsonl_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception:
        pass


# Env vars the driver sets so subprocess step modules can emit sub-progress into the same
# log with no CLI plumbing (children inherit os.environ). Absent → emit_step_progress is a no-op.
ENV_PROGRESS_JSON = "GPI_PROGRESS_JSON"
ENV_PROGRESS_STEP = "GPI_PROGRESS_STEP"


def emit_step_progress(current: Any, total: Any, detail: Optional[str] = None) -> None:
    """Emit a STEP_PROGRESS event from a subprocess step module's per-item loop.

    Reads the target log + step name from the environment (``GPI_PROGRESS_JSON`` /
    ``GPI_PROGRESS_STEP``, set by the pipeline driver). A no-op when unset — so a module run
    standalone, or under ``--progress off``, has zero progress overhead and no behavior change.
    """
    path = os.environ.get(ENV_PROGRESS_JSON)
    step = os.environ.get(ENV_PROGRESS_STEP)
    if not path or not step:
        return
    emit_progress(path, STEP_PROGRESS,
                  {"step": step, "current": current, "total": total, "detail": detail})


# --------------------------------------------------------------------------- reducer
def _iso(ts: float) -> str:
    """UTC ISO-8601, millisecond precision (the snapshot's human/agent-readable clock)."""
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds")


class SnapshotReducer:
    """Pure fold of the event stream into a reduced snapshot dict. No I/O, no SDK.

    ``apply`` is a pure fold over events. ``snapshot`` additionally reads the wall clock, for
    the two fields that must move even when no event arrives: ``heartbeat_at`` (the liveness
    signal) and the active step's ``elapsed_s``. Pass ``now`` to keep it deterministic.
    """

    def __init__(self) -> None:
        t0 = time.time()
        self.state: Dict[str, Any] = {
            "run_id": None,
            "status": "running",
            "updated_at": None,
            "started_at": _iso(t0),
            "pid": os.getpid(),
            "failed_step": None,
            "steps": [],
            "active_step": None,
            "research": {
                "n_programs": 0,
                "n_done": 0,
                "n_incomplete": 0,
                "concurrency": None,
                "auth": None,
                "total_cost_usd": 0.0,
                "agents": {},
            },
        }
        self._step_index: Dict[str, int] = {}
        self._step_start_ts: Dict[str, float] = {}
        self._step_end_ts: Dict[str, float] = {}
        # Per-agent clocks (mirror the per-step ones): start = first started/tool event,
        # end = agent_finished, last_tool = most recent tool call. snapshot() turns these into
        # elapsed_s and idle_s — idle_s is the signal that separates "thinking" from "wedged".
        self._agent_start_ts: Dict[str, float] = {}
        self._agent_end_ts: Dict[str, float] = {}
        self._agent_last_tool_ts: Dict[str, float] = {}

    def _blank_step(self, name: str) -> Dict[str, Any]:
        return {"name": name, "status": "pending", "executor": None,
                "current": None, "total": None, "detail": None,
                "error": None, "elapsed_s": None}

    def _ensure_step(self, name: Optional[str]) -> None:
        if name and name not in self._step_index:
            self.state["steps"].append(self._blank_step(name))
            self._step_index[name] = len(self.state["steps"]) - 1

    def _step(self, name: Optional[str]) -> Optional[Dict[str, Any]]:
        i = self._step_index.get(name) if name else None
        return self.state["steps"][i] if i is not None else None

    def apply(self, ev: Dict[str, Any]) -> None:
        t = ev.get("type")
        st = self.state
        ts = ev.get("ts")
        if ts is not None:
            st["updated_at"] = ts

        if t == RUN_START:
            st["run_id"] = ev.get("run_id")
            st["status"] = "running"
            if ts is not None:
                st["started_at"] = _iso(ts)
            names = list(ev.get("steps") or [])
            st["steps"] = [self._blank_step(n) for n in names]
            self._step_index = {n: i for i, n in enumerate(names)}
            self._step_start_ts.clear()
            self._step_end_ts.clear()
        elif t == STEP_START:
            name = ev.get("step")
            self._ensure_step(name)
            s = self._step(name)
            if s is not None:
                s["status"] = "in_progress"
                s["executor"] = ev.get("executor")
                s["error"] = None  # a re-run clears the previous attempt's failure
            if name and ts is not None:
                self._step_start_ts[name] = ts
                self._step_end_ts.pop(name, None)
            st["active_step"] = name
        elif t == STEP_PROGRESS:
            self._ensure_step(ev.get("step"))
            s = self._step(ev.get("step"))
            if s is not None:
                s["current"] = ev.get("current")
                s["total"] = ev.get("total")
                s["detail"] = ev.get("detail")
        elif t == STEP_DONE:
            name = ev.get("step")
            status = ev.get("status", "completed")
            s = self._step(name)
            if s is not None:
                s["status"] = status
                s["current"] = s["total"] = s["detail"] = None
                # The error is the whole point of a failed step; the reducer used to drop it,
                # leaving a dead run with no stated cause. Redact before it lands in
                # progress.json — a step error can carry an httpx URL with an api_key in it,
                # and the skill reads that file straight back into an agent's context.
                s["error"] = redact_text(ev.get("error")) if ev.get("error") else None
            if name and ts is not None:
                self._step_end_ts[name] = ts
            if status == "failed" and name:
                # Most recent failure wins: that is the one that stopped the run (an earlier
                # failure in a DEGRADABLE step did not). Every failed step keeps its own
                # ``error`` in ``steps``, so nothing is lost.
                st["failed_step"] = name
            if st["active_step"] == name:
                st["active_step"] = None
        elif t == RESEARCH_START:
            r = st["research"]
            r["n_programs"] = ev.get("n_programs", 0)
            r["concurrency"] = ev.get("concurrency")
            r["auth"] = ev.get("auth")  # 'subscription' → total_cost_usd is NOT a $ charge
        elif t in (AGENT_QUEUED, AGENT_STARTED, AGENT_TOOL_CALL, AGENT_FINISHED):
            self._apply_agent(t, ev)
        elif t == RESEARCH_DONE:
            if ev.get("total_cost_usd") is not None:
                st["research"]["total_cost_usd"] = ev.get("total_cost_usd")
        elif t == RUN_DONE:
            st["status"] = ev.get("status", "done")
            st["active_step"] = None

    def _apply_agent(self, t: str, ev: Dict[str, Any]) -> None:
        pid = str(ev.get("program_id"))
        ts = ev.get("ts")
        agents = self.state["research"]["agents"]
        a = agents.setdefault(pid, {"program_id": pid, "status": "queued", "turns": None,
                                    "cost_usd": None, "current_tool": None,
                                    "current_detail": None, "n_tools": 0, "error": None,
                                    "n_mechanisms": None, "n_evidence": None})
        if t == AGENT_QUEUED:
            a["status"] = "queued"
        elif t == AGENT_STARTED:
            a["status"] = "running"
            if ts is not None:
                self._agent_start_ts.setdefault(pid, ts)
        elif t == AGENT_TOOL_CALL:
            a["status"] = "running"
            a["current_tool"] = ev.get("tool")
            # The query string, redacted: a file_path/url arg can carry a key, and the skill
            # reads progress.json straight back into an agent's context.
            a["current_detail"] = redact_text(ev.get("detail")) if ev.get("detail") else None
            a["n_tools"] = int(a.get("n_tools") or 0) + 1
            if ev.get("turn") is not None:
                a["turns"] = ev.get("turn")
            if ts is not None:
                self._agent_start_ts.setdefault(pid, ts)  # in case AGENT_STARTED was missed
                self._agent_last_tool_ts[pid] = ts
        elif t == AGENT_FINISHED:
            a["status"] = ev.get("status", "done")
            if ev.get("num_turns") is not None:
                a["turns"] = ev.get("num_turns")
            a["cost_usd"] = ev.get("cost_usd")  # may be None on subscription auth
            a["current_tool"] = None
            a["current_detail"] = None
            # The failure reason — dropped before, so an 'incomplete' agent that fell back to a
            # deterministic result looked identical to a cheap success. Redact for the same
            # reason as a step error.
            a["error"] = redact_text(ev.get("error")) if ev.get("error") else None
            if ev.get("n_mechanisms") is not None:
                a["n_mechanisms"] = ev.get("n_mechanisms")
            if ev.get("n_evidence") is not None:
                a["n_evidence"] = ev.get("n_evidence")
            if ts is not None:
                self._agent_end_ts[pid] = ts
        r = self.state["research"]
        r["n_done"] = sum(1 for x in agents.values() if x["status"] in _AGENT_DONE_STATES)
        # A program that fell back counts as done but NOT healthy; surface it so '3/3 done'
        # cannot silently hide three failures.
        r["n_incomplete"] = sum(1 for x in agents.values()
                                if x["status"] in ("incomplete", "failed"))
        r["total_cost_usd"] = round(
            sum(float(x["cost_usd"]) for x in agents.values()
                if isinstance(x.get("cost_usd"), (int, float))), 4)

    def _elapsed(self, name: Any, status: Any, now: float) -> Optional[float]:
        """Seconds the step has been running (live) or took (final). None if never started."""
        start = self._step_start_ts.get(name)
        if start is None:
            return None
        end = self._step_end_ts.get(name)
        if end is None:
            if status != "in_progress":
                return None
            end = now  # still running — grow with the wall clock
        return round(max(0.0, end - start), 1)

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        """A JSON-serializable copy; agents rendered as a list sorted by program id.

        ``heartbeat_at`` is stamped on *every* call, so it advances even when the event stream
        is silent. That is the difference between "hung" and "working": ``updated_at`` (the last
        event) can legitimately sit still for many minutes while a research agent thinks, and a
        monitor that reads it as a hang will kill a healthy run.
        """
        st = self.state
        now = time.time() if now is None else now
        research = dict(st["research"])
        agents = research["agents"]
        agent_list: List[Dict[str, Any]] = []
        for k in sorted(agents, key=lambda k: (len(k), k)):
            ag = dict(agents[k])
            start = self._agent_start_ts.get(k)
            end = self._agent_end_ts.get(k)
            ag["elapsed_s"] = (
                round(max(0.0, (end if end is not None else now) - start), 1)
                if start is not None else None
            )
            # Idle only means something for a live agent: seconds since its last tool call.
            last = self._agent_last_tool_ts.get(k)
            ag["idle_s"] = (
                round(max(0.0, now - last), 1)
                if ag.get("status") == "running" and last is not None else None
            )
            agent_list.append(ag)
        research["agents"] = agent_list
        steps: List[Dict[str, Any]] = []
        for s in st["steps"]:
            step = dict(s)
            step["elapsed_s"] = self._elapsed(step.get("name"), step.get("status"), now)
            steps.append(step)
        return {
            "run_id": st["run_id"],
            "status": st["status"],
            "pid": st["pid"],
            "started_at": st["started_at"],
            "heartbeat_at": _iso(now),
            "updated_at": st["updated_at"],
            "failed_step": st["failed_step"],
            "steps": steps,
            "active_step": st["active_step"],
            "research": research,
        }


# --------------------------------------------------------------------------- markdown / txt
_STATUS_ICON = {"pending": "⏳", "in_progress": "▶", "completed": "✅",
                "skipped": "⏭", "failed": "❌"}


def _fmt_cost(v: Any) -> str:
    return "—" if not isinstance(v, (int, float)) else f"${float(v):.2f}"


def _short_err(err: Any, limit: int = 200) -> str:
    """One-line, length-capped error — the renderers are line-oriented."""
    text = " ".join(str(err).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_dur(seconds: Any) -> str:
    """Compact human duration: ``45s`` / ``6m12s`` / ``1h03m``."""
    if not isinstance(seconds, (int, float)):
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def _agent_activity(a: Dict[str, Any], limit: int = 60) -> str:
    """What an agent is doing / did, for the table's last column.

    Running → the tool and its query (``search_pubmed · "Madcam1 brain endothelial …"``);
    finished-ok → the yield (``3 mech · 12 evid``); fell-back → the reason. This is what makes
    the agent table worth reading instead of a row of ``search_pubmed``."""
    status = a.get("status")
    if status in ("incomplete", "failed") and a.get("error"):
        return _short_err(a["error"], limit=limit + 20)
    if status in ("ok", "done"):
        parts = []
        if a.get("n_mechanisms") is not None:
            parts.append(f"{a['n_mechanisms']} mech")
        if a.get("n_evidence") is not None:
            parts.append(f"{a['n_evidence']} evid")
        return " · ".join(parts) if parts else "done"
    tool = a.get("current_tool") or "—"
    detail = a.get("current_detail")
    return f"{tool} · {_short_err(detail, limit=limit)}" if detail else tool


def _agent_elapsed_cell(a: Dict[str, Any]) -> str:
    """Elapsed, plus an idle note while running (idle = seconds since the last tool call —
    the signal that separates a thinking agent from a wedged one)."""
    base = _fmt_dur(a.get("elapsed_s"))
    idle = a.get("idle_s")
    if a.get("status") == "running" and isinstance(idle, (int, float)) and idle >= 30:
        return f"{base} · idle {_fmt_dur(idle)}"
    return base


def _research_footer(r: Dict[str, Any]) -> str:
    """One-line research summary shared by the renderers. Leads with usage (programs + turns),
    not dollars: on the default subscription auth ``total_cost_usd`` is NOT a per-run charge, so
    a dollar figure there misleads. Show turns as the usage signal; surface ``$`` only for
    metered API auth (the annotate/presentation batch)."""
    n_done, n_prog = r.get("n_done", 0), r.get("n_programs", 0)
    inc = r.get("n_incomplete", 0)
    inc_s = f" ({inc} incomplete)" if inc else ""
    turns = sum((a.get("turns") or 0) for a in (r.get("agents") or []))
    if r.get("auth") == "subscription":
        usage_s = f"{turns} turns · on your Claude subscription (no per-run charge)"
    else:
        usage_s = f"{turns} turns · {_fmt_cost(r.get('total_cost_usd'))} API credit"
    return f"{n_done}/{n_prog} programs done{inc_s} · {usage_s}"


def render_markdown(snap: Dict[str, Any]) -> str:
    """Render a snapshot as a markdown step-checklist + (during research) an agent table."""
    lines: List[str] = []
    for s in snap.get("steps", []):
        icon = _STATUS_ICON.get(s.get("status"), "•")
        ex = f" `[{s['executor']}]`" if s.get("executor") else ""
        sub = ""
        if s.get("status") == "in_progress" and s.get("total"):
            sub = f" — {s.get('current')}/{s.get('total')}"
            if s.get("detail"):
                sub += f" ({s['detail']})"
        elif s.get("status") == "failed" and s.get("error"):
            sub = f" — {_short_err(s['error'])}"
        lines.append(f"- {icon} **{s.get('name')}**{ex}{sub}")
    md = "\n".join(lines)

    r = snap.get("research") or {}
    agents = r.get("agents") or []
    if agents:
        md += "\n\n| Program | Status | Turns | Cost | Elapsed | Activity |\n|---|---|---|---|---|---|\n"
        for a in agents:
            turns = "—" if a.get("turns") is None else a["turns"]
            activity = _agent_activity(a).replace("|", "\\|")
            md += (f"| {a.get('program_id')} | {a.get('status')} | {turns} | "
                   f"{_fmt_cost(a.get('cost_usd'))} | {_agent_elapsed_cell(a)} | {activity} |\n")
        md += f"\n_{_research_footer(r)}_"
    return md


def render_txt(snap: Dict[str, Any]) -> str:
    """Plain-text (no markdown/ANSI) rendering of a snapshot."""
    out: List[str] = []
    for s in snap.get("steps", []):
        mark = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]",
                "skipped": "[-]", "failed": "[!]"}.get(s.get("status"), "[ ]")
        ex = f" ({s['executor']})" if s.get("executor") else ""
        sub = ""
        if s.get("status") == "in_progress" and s.get("total"):
            sub = f"  {s.get('current')}/{s.get('total')}"
        elif s.get("status") == "failed" and s.get("error"):
            sub = f"  {_short_err(s['error'])}"
        out.append(f"{mark} {s.get('name')}{ex}{sub}")
    r = snap.get("research") or {}
    for a in (r.get("agents") or []):
        out.append(f"    - {a.get('program_id')}: {a.get('status')} "
                   f"turns={a.get('turns')} cost={_fmt_cost(a.get('cost_usd'))} "
                   f"elapsed={_agent_elapsed_cell(a)} · {_agent_activity(a)}")
    if r.get("agents"):
        out.append(f"    {_research_footer(r)}")
    return "\n".join(out)


# --------------------------------------------------------------------------- compact card
# Display-only expected-duration hints, keyed by step name. A step not listed simply shows no
# hint (safe default). This is presentation metadata — it drives the friendly "this step can run
# long, that's expected" note so a slow-but-healthy step never reads as a hang. Kept next to the
# renderer (not in the pipeline driver) so the card stays self-contained.
_STEP_HINTS: Dict[str, Tuple[str, int]] = {
    # step name          (human hint,                 seconds after which to add a reassurance)
    "preflight":         ("~1-5 min on a cold machine", 300),
    "string_enrichment": ("~1-2 min",                   180),
    "gene_summaries":    ("~2-4 min (NCBI lookups)",    300),
    "research":          ("~3-8 min per program",       600),
    "verify":            ("under a minute",             120),
    "theme":             ("under a minute",             120),
    "annotate":          ("Anthropic batch — can sit 15+ min, not a hang", 1800),
    "presentation":      ("Anthropic batch — can sit 15+ min, not a hang", 1800),
    "html_report":       ("seconds",                     60),
}

_BAR_WIDTH = 12


def _step_bar(steps: List[Dict[str, Any]], width: int = _BAR_WIDTH) -> str:
    """A Unicode progress bar over completed pipeline steps: ``[███████░░░░░] 4/9``."""
    total = len(steps) or 1
    done = sum(1 for s in steps if s.get("status") in ("completed", "skipped"))
    filled = round(width * done / total)
    return f"[{'█' * filled}{'░' * (width - filled)}] {done}/{len(steps)}"


def render_card(snap: Dict[str, Any]) -> str:
    """Compact, graphic status for the ``gpi watch --until-change`` path the skill echoes verbatim.

    A step bar + the active step and its live counter (+ a friendly note when a step runs long) +
    one short line per active research agent. Framed in usage terms (turns/elapsed), never dollars
    on subscription auth. Short enough to re-emit every poll without noise — the point is that the
    bar visibly steps forward and the counter moves between polls."""
    steps = snap.get("steps", [])
    out: List[str] = []
    active = next((s for s in steps if s.get("status") == "in_progress"), None)
    status = snap.get("status")
    if status in ("done", "failed"):
        head = "✅ done" if status == "done" else f"❌ failed: {snap.get('failed_step') or '?'}"
        out.append(f"{_step_bar(steps)} · {head}")
    else:
        out.append(f"{_step_bar(steps)} · {active.get('name') if active else 'starting…'}")

    if active:
        name = active.get("name")
        parts: List[str] = []
        if active.get("total"):
            parts.append(f"{active.get('current')}/{active.get('total')}")
        if active.get("detail"):
            parts.append(str(active["detail"]))
        line = "   ↳ " + (" · ".join(parts) if parts else "working")
        hint = _STEP_HINTS.get(name or "")
        if hint:
            text, long_after = hint
            elapsed = active.get("elapsed_s")
            note = ""
            if isinstance(elapsed, (int, float)) and elapsed > long_after:
                note = " — still working, that's expected"
            line += f"   (⏳ {text}{note})"
        out.append(line)

    r = snap.get("research") or {}
    auth = r.get("auth")
    for a in (r.get("agents") or []):
        st = a.get("status")
        if st not in ("running", "queued"):
            continue
        if st == "queued":
            out.append(f"   • P{a.get('program_id')}: queued")
            continue
        turns = a.get("turns")
        usage = f"turn {turns}" if turns is not None else "starting"
        cost = ""
        if auth == "api" and isinstance(a.get("cost_usd"), (int, float)):
            cost = f" · {_fmt_cost(a['cost_usd'])}"
        out.append(
            f"   • P{a.get('program_id')}: {usage} · {_agent_elapsed_cell(a)}{cost} · {_agent_activity(a)}"
        )
    if r.get("agents"):
        out.append(f"   {_research_footer(r)}")
    return "\n".join(out)


# --------------------------------------------------------------------------- plain renderer
def _plain_line(ev: Dict[str, Any]) -> Optional[str]:
    t = ev.get("type")
    if t == STEP_START:
        idx = f" ({ev.get('index')}/{ev.get('n_steps')})" if ev.get("index") else ""
        return f"[{ev.get('step')}] start{idx}"
    if t == STEP_PROGRESS:
        detail = f" {ev.get('detail')}" if ev.get("detail") else ""
        return f"[{ev.get('step')}] {ev.get('current')}/{ev.get('total')}{detail}"
    if t == STEP_DONE:
        err = f" — {_short_err(redact_text(str(ev['error'])))}" if ev.get("error") else ""
        return f"[{ev.get('step')}] {ev.get('status', 'done')}{err}"
    if t == RESEARCH_START:
        return f"[research] {ev.get('n_programs')} program(s), concurrency {ev.get('concurrency')}"
    if t == AGENT_STARTED:
        return f"[research] {ev.get('program_id')} started"
    if t == AGENT_TOOL_CALL:
        turn = f" (turn {ev.get('turn')})" if ev.get("turn") is not None else ""
        detail = ev.get("detail")
        detail_s = f' "{_short_err(redact_text(str(detail)), limit=120)}"' if detail else ""
        return f"[research] {ev.get('program_id')} → {ev.get('tool')}{turn}{detail_s}"
    if t == AGENT_FINISHED:
        err = ev.get("error")
        err_s = f" — {_short_err(redact_text(str(err)), limit=120)}" if err else ""
        return (f"[research] {ev.get('program_id')} finished {ev.get('status', 'done')} "
                f"turns={ev.get('num_turns')} cost={_fmt_cost(ev.get('cost_usd'))}{err_s}")
    if t == RESEARCH_DONE:
        n = ev.get("n_results")
        return f"[research] done ({n} result(s))" if n is not None else "[research] done"
    if t == RUN_DONE:
        return f"[run] {ev.get('status', 'done')}"
    return None


class PlainRenderer:
    """Emit one clean ASCII line per event (no ANSI). Safe for piped/captured stdout —
    this is what the Claude skill's Bash capture sees."""

    def __init__(self, stream: Optional[TextIO] = None) -> None:
        self.stream = stream or sys.stdout

    def on_event(self, ev: Dict[str, Any], snap: Dict[str, Any]) -> None:
        line = _plain_line(ev)
        if line:
            try:
                print(line, file=self.stream, flush=True)
            except Exception:
                pass

    def stop(self) -> None:
        pass


class RichRenderer:
    """Live terminal view (TTY-only): a step checklist with per-step sub-progress plus a
    parallel-agent table during the research step. Reconfigures the root logger to a
    ``RichHandler`` bound to its Console so log lines and the Live region coexist without
    corrupting each other. All rich imports are lazy — this class is only constructed when
    ``rich`` is importable (see ``make_renderer``)."""

    def __init__(self, stream: Optional[TextIO] = None) -> None:
        import logging as _logging

        from rich.console import Console
        from rich.live import Live
        from rich.logging import RichHandler

        self._console = Console(file=stream or sys.stdout)
        # Route logging through Rich so INFO lines print above the Live region.
        root = _logging.getLogger()
        self._saved_handlers = list(root.handlers)
        for h in self._saved_handlers:
            root.removeHandler(h)
        root.addHandler(RichHandler(console=self._console, show_path=False,
                                    rich_tracebacks=False, markup=False))
        self._live = Live(self._render({}), console=self._console,
                          refresh_per_second=8, transient=False)
        self._live.start()
        self._last: Dict[str, Any] = {}

    def _render(self, snap: Dict[str, Any]):
        from rich.console import Group
        from rich.table import Table
        from rich.text import Text

        steps_tbl = Table.grid(padding=(0, 1))
        for s in snap.get("steps", []):
            icon = _STATUS_ICON.get(s.get("status"), "•")
            ex = f"[{s['executor']}]" if s.get("executor") else ""
            sub = ""
            if s.get("status") == "in_progress" and s.get("total"):
                sub = f"{s.get('current')}/{s.get('total')}"
                if s.get("detail"):
                    sub += f" · {s['detail']}"
            elif s.get("status") == "failed" and s.get("error"):
                sub = _short_err(s["error"], limit=90)
            style = "bold" if s.get("status") == "in_progress" else ""
            steps_tbl.add_row(icon, Text(str(s.get("name")), style=style), ex, sub)

        renderables: List[Any] = [steps_tbl]
        r = snap.get("research") or {}
        agents = r.get("agents") or []
        if agents:
            at = Table(title=None, expand=False, pad_edge=False)
            at.add_column("Program"); at.add_column("Status"); at.add_column("Turns", justify="right")
            at.add_column("Cost", justify="right"); at.add_column("Elapsed"); at.add_column("Activity")
            for a in agents:
                turns = "—" if a.get("turns") is None else str(a["turns"])
                at.add_row(str(a.get("program_id")), str(a.get("status")), turns,
                           _fmt_cost(a.get("cost_usd")), _agent_elapsed_cell(a), _agent_activity(a))
            footer = Text(_research_footer(r), style="dim")
            renderables += [at, footer]
        return Group(*renderables)

    def on_event(self, ev: Dict[str, Any], snap: Dict[str, Any]) -> None:
        self._last = snap
        try:
            self._live.update(self._render(snap))
        except Exception:
            pass

    def stop(self) -> None:
        import logging as _logging
        try:
            self._live.update(self._render(self._last))
            self._live.stop()
        except Exception:
            pass
        # Restore the original logging handlers.
        try:
            root = _logging.getLogger()
            for h in list(root.handlers):
                root.removeHandler(h)
            for h in self._saved_handlers:
                root.addHandler(h)
        except Exception:
            pass


def _isatty(stream: Optional[TextIO]) -> bool:
    try:
        return bool(stream and stream.isatty())
    except Exception:
        return False


def make_renderer(mode: str, stdout: Optional[TextIO] = None):
    """Select a renderer for ``mode``. ``off`` → None. ``rich`` prefers the Rich renderer
    (added in Part C stage 5) and falls back to Plain; ``auto`` uses Rich only on a TTY;
    ``plain`` forces Plain. Kept dependency-light: Rich is imported lazily."""
    stdout = stdout or sys.stdout
    if mode == "off":
        return None
    if mode in ("rich", "auto"):
        if mode == "rich" or _isatty(stdout):
            rich_renderer = _try_make_rich(stdout)
            if rich_renderer is not None:
                return rich_renderer
    return PlainRenderer(stdout)


def _try_make_rich(stdout: Optional[TextIO]):
    """Build a RichRenderer if ``rich`` is installed and one is defined; else None.
    (RichRenderer is added in Part C stage 5; until then this returns None → Plain.)"""
    try:
        renderer_cls = globals().get("RichRenderer")
        if renderer_cls is None:
            return None
        import rich  # noqa: F401  (availability check)
        return renderer_cls(stdout)
    except Exception:
        return None


# --------------------------------------------------------------------------- emitter + tailer
class ProgressEmitter:
    """Owns ``progress.jsonl`` (truncated fresh per run), a background tailer thread that
    folds appended events into ``progress.json`` + the renderer, and the ``emit`` entry point.

    The tailer decouples all reduce/snapshot/render work from the emit hot path and from the
    asyncio loop. ``enabled=False`` (``--progress off``) makes ``emit`` a no-op and starts no
    thread / writes no files — a true zero-overhead baseline.
    """

    def __init__(self, jsonl_path: "str | os.PathLike", snapshot_path: "str | os.PathLike", *,
                 renderer: Any = None, enabled: bool = True,
                 poll_interval: float = 0.4, snapshot_min_interval: float = 1.0) -> None:
        self.jsonl_path = str(jsonl_path)
        self.snapshot_path = str(snapshot_path)
        self.renderer = renderer
        self.enabled = enabled
        self._reducer = SnapshotReducer()
        self._poll = poll_interval
        self._snap_min = snapshot_min_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._offset = 0
        self._last_snap = 0.0
        self._lock = threading.Lock()
        if self.enabled:
            try:
                Path(self.jsonl_path).parent.mkdir(parents=True, exist_ok=True)
                open(self.jsonl_path, "w", encoding="utf-8").close()  # fresh log per run
            except Exception:
                pass
            # Overwrite any PREVIOUS run's snapshot before anyone can read it: a resumed run
            # left progress.json saying `status: "done"`, so a monitor polling at t=0 saw a
            # finished run that had not started. Written here — synchronously, before the
            # tailer starts — so there is no window where the stale file is visible, and no
            # race with the tailer over the shared .tmp path.
            self._write_snapshot()
            self._thread = threading.Thread(target=self._tail_loop, name="gpi-progress-tailer",
                                            daemon=True)
            self._thread.start()

    def emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if self.enabled:
            emit_progress(self.jsonl_path, event_type, payload)

    # ---- tailer internals (run on the daemon thread) ----
    def _tail_loop(self) -> None:
        while not self._stop.is_set():
            self._drain()
            self._maybe_snapshot()
            self._stop.wait(self._poll)
        self._drain()
        self._write_snapshot(force=True)

    def _drain(self) -> None:
        try:
            with open(self.jsonl_path, "rb") as f:
                f.seek(self._offset)
                data = f.read()
        except FileNotFoundError:
            return
        except Exception:
            return
        if not data:
            return
        # Only consume complete (newline-terminated) lines; leave any partial tail.
        if data.endswith(b"\n"):
            consume = data
        else:
            nl = data.rfind(b"\n")
            if nl == -1:
                return
            consume = data[: nl + 1]
        self._offset += len(consume)
        for raw in consume.decode("utf-8", "replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            with self._lock:
                self._reducer.apply(ev)
                snap = self._reducer.snapshot()
            if self.renderer is not None:
                try:
                    self.renderer.on_event(ev, snap)
                except Exception:
                    pass

    def _maybe_snapshot(self) -> None:
        # Called on EVERY tailer tick, not only when _drain produced events — that is what
        # keeps `heartbeat_at` moving through a long silence (a research agent can think for
        # 7+ minutes without emitting). Do not make this conditional on new events: the
        # snapshot would freeze and a monitor would read a healthy run as hung. The throttle
        # bounds the write rate to 1/s; the tailer sleeps `poll` between ticks, so no hot spin.
        if time.time() - self._last_snap >= self._snap_min:
            self._write_snapshot()

    def _write_snapshot(self, force: bool = False) -> None:
        try:
            with self._lock:
                snap = self._reducer.snapshot()
            tmp = self.snapshot_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snap, f)
            os.replace(tmp, self.snapshot_path)
            self._last_snap = time.time()
        except Exception:
            pass

    def close(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self.renderer is not None:
            try:
                self.renderer.stop()
            except Exception:
                pass


# --------------------------------------------------------------------------- module accessor
_EMITTER: "ContextVar[Optional[ProgressEmitter]]" = ContextVar("gpi_progress_emitter", default=None)


def set_emitter(emitter: Optional[ProgressEmitter]) -> None:
    _EMITTER.set(emitter)


def get_emitter() -> Optional[ProgressEmitter]:
    return _EMITTER.get()


def make_emitter(output_dir: "str | os.PathLike", mode: str = "auto",
                 stdout: Optional[TextIO] = None) -> ProgressEmitter:
    """Construct a ProgressEmitter writing into ``output_dir`` (progress.jsonl/progress.json).
    ``mode='off'`` returns a disabled emitter (no thread, no files, emit is a no-op)."""
    output_dir = Path(output_dir)
    jsonl = output_dir / "progress.jsonl"
    snap = output_dir / "progress.json"
    if mode == "off":
        return ProgressEmitter(jsonl, snap, renderer=None, enabled=False)
    return ProgressEmitter(jsonl, snap, renderer=make_renderer(mode, stdout), enabled=True)
