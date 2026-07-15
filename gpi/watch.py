"""``gpi watch <run_dir>`` — a read-only progress tailer for a run started by another process.

Why this exists: the pipeline runs in the background (the skill launches it, or the user
``nohup``s it over SSH), and "I can't see any progress" was the top complaint. The skill's old
recipe — "poll progress.json every 45-60 s in separate tool calls" — is unexecutable: a model
has no timer and nothing wakes it, so it either spin-polls or goes silent. ``watch`` is the
missing primitive: a **foreground, blocking** call that sleeps until something happens, prints
what changed and whether the run is alive, and (in ``--until-change`` mode) ends its output with
a single obey-me token so the skill's loop is a straight-line "call → report → call again".

It is strictly read-only. It folds ``progress.jsonl`` (the append-only event log written by the
running pipeline) with the SAME ``SnapshotReducer`` the pipeline uses, and renders with the same
renderers — so there is one implementation of both the fold and the liveness rules, not a
drifting copy in a shell heredoc.

Liveness is judged the way ``progress.py`` documents: ``progress.json``'s mtime is the heartbeat
(the pipeline's tailer rewrites it ~1/s while alive), NOT ``updated_at`` (the last event, which
can sit still for many minutes while a research agent thinks). A fresh snapshot means alive even
with zero new events; only a gone process or a long-frozen snapshot is a problem.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .progress import (
    AGENT_FINISHED,
    AGENT_STARTED,
    RESEARCH_DONE,
    RESEARCH_START,
    RUN_DONE,
    RUN_START,
    STEP_DONE,
    STEP_PROGRESS,
    STEP_START,
    SnapshotReducer,
    _plain_line,
    render_card,
    render_txt,
)

# Coarse events that wake ``--until-change`` immediately, so the card's step counter visibly
# moves between polls. STEP_PROGRESS is included: the startup steps emit it at LOW frequency
# (string_enrichment per-program ~3-N, gene_summaries 2 phases, preflight per-module), and those
# ticks are exactly what made the first minutes feel silent. High-frequency AGENT_TOOL_CALL stays
# OUT — it fires dozens of times per program and is folded into the digest at timeout instead, so
# the skill gets a ≤timeout summary, not a call per tool call. Silence between milestones is
# normal (an agent thinking) and is reported as "alive", not chased.
_MILESTONES = frozenset({
    RUN_START, STEP_START, STEP_PROGRESS, STEP_DONE, RESEARCH_START,
    AGENT_STARTED, AGENT_FINISHED, RESEARCH_DONE, RUN_DONE,
})

# A snapshot younger than this is proof the pipeline is alive. Generous vs. the ~1/s write rate
# to tolerate NFS attribute-cache lag without ever crying "hung" at a healthy run.
_FRESH_S = 15.0
# Alive process + a snapshot this old = genuinely wedged tailer; report it, but only this late.
_STALE_S = 180.0

# Liveness state -> the token the skill obeys (SKILL.md §4). dead/stale both mean "stop the loop
# and check" (Gate 4); alive means "keep going".
_TOKEN = {"alive": "CONTINUE", "done": "DONE", "failed": "FAILED",
          "dead": "STALE", "stale": "STALE"}


def _pid_alive(pid: Any) -> Optional[bool]:
    """True/False if we can tell, None if not (e.g. pid missing or a different machine).

    ``os.kill(pid, 0)`` probes without signalling: alive → returns, dead → ProcessLookupError,
    alive-but-not-ours → PermissionError (still alive)."""
    if not isinstance(pid, int):
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def _read_events(jsonl_path: Path) -> List[Dict[str, Any]]:
    """Every complete event line in the log. Re-read whole each tick — a real run's log is tens
    of KB, so this is free, and it needs no offset bookkeeping and survives a fresh-run truncate."""
    try:
        text = jsonl_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    events: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:  # noqa: BLE001 — a torn final line is skipped, picked up next read
            continue
    return events


def _fold(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    reducer = SnapshotReducer()
    for ev in events:
        reducer.apply(ev)
    return reducer.snapshot()


def _mtime_age(path: Path) -> Optional[float]:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _pipeline_pid(snapshot_path: Path) -> Optional[int]:
    """The pipeline's OWN pid, read from the progress.json it wrote.

    Not from the folded snapshot: ``SnapshotReducer`` stamps ``os.getpid()`` at init, so a
    re-fold in the *watch* process would carry the watcher's pid and make every run look alive.
    progress.json is written by the pipeline (atomic os.replace, never torn), so its pid is
    authoritative."""
    try:
        pid = json.loads(snapshot_path.read_text(encoding="utf-8")).get("pid")
        return pid if isinstance(pid, int) else None
    except Exception:  # noqa: BLE001 — missing/mid-rotation file just means "unknown"
        return None


def liveness(snap: Dict[str, Any], snapshot_path: Path) -> Tuple[str, str]:
    """Classify a run as one of alive / done / failed / dead / stale, with a reason.

    Errs toward "alive": only a provably-gone process, or a very old snapshot, is ever called
    bad. The whole point is to not fabricate an alarm — a monitoring bug looks exactly like the
    incident it falsely reports, and a false "dead" gets a healthy run killed."""
    status = snap.get("status")
    if status == "failed" or snap.get("failed_step"):
        fs = snap.get("failed_step")
        return ("failed", f"failed at step '{fs}'" if fs else "run reported failure")
    if status == "done":
        return ("done", "run completed")
    age = _mtime_age(snapshot_path)
    pid = _pipeline_pid(snapshot_path)
    alive = _pid_alive(pid)
    if age is not None and age < _FRESH_S:
        return ("alive", "snapshot is fresh (pipeline heartbeat active)")
    if alive is False:
        age_s = f"{int(age)}s" if age is not None else "unknown"
        return ("dead", f"process {pid} is not running (snapshot {age_s} old)")
    if age is not None and age > _STALE_S:
        return ("stale", f"snapshot {int(age)}s old but process {pid} still present")
    return ("alive", "process present; between events")


# ----------------------------------------------------------------------------- --until-change

def _print_report(snap: Dict[str, Any], snapshot_path: Path,
                  new_events: List[Dict[str, Any]], waited_s: float) -> str:
    """Print the human report and return the terminal token (its own final line)."""
    run_id = snap.get("run_id") or snapshot_path.parent.name
    print(f"── gpi v{__version__} watch: {run_id} ──")
    milestones = [e for e in new_events if e.get("type") in _MILESTONES]
    if milestones:
        print("Changed since last check:")
        for ev in milestones[-10:]:
            line = _plain_line(ev)
            if line:
                print(f"  {line}")
    else:
        print(f"No milestone in the last {int(waited_s)}s "
              "(this is normal — a research agent can think for minutes).")
    # The compact card is what the skill echoes to the user: a step bar + live counter + a line
    # per active agent, in usage terms (no dollars on subscription). render_txt stays for the
    # human TUI (_run_continuous).
    body = render_card(snap)
    if body:
        print(body)
    state, reason = liveness(snap, snapshot_path)
    print(f"Liveness: {state} — {reason}")
    # The token is the LAST line, alone — that is the contract the skill keys on.
    token = _TOKEN.get(state, "CONTINUE")
    print(token)
    return token


def _run_until_change(jsonl: Path, snapshot: Path, timeout: float, poll: float) -> int:
    start = time.time()
    deadline = start + timeout
    base = _read_events(jsonl)
    base_n = len(base)

    # Fast path: if the run is already terminal (done/failed/dead), there is nothing to wait
    # for — report the final state now instead of blocking for the whole timeout.
    if base:
        base_snap = _fold(base)
        if liveness(base_snap, snapshot)[0] in ("done", "failed", "dead"):
            _print_report(base_snap, snapshot, [], 0.0)
            return 0

    while True:
        now = time.time()
        if jsonl.exists():
            events = _read_events(jsonl)
            if len(events) > base_n:
                fresh = events[base_n:]
                if any(e.get("type") in _MILESTONES for e in fresh):
                    _print_report(_fold(events), snapshot, fresh, now - start)
                    return 0
        if now >= deadline:
            events = _read_events(jsonl)
            if not events:
                # Cold start: nothing on disk yet. This is expected for the first minutes while
                # Python compiles the Agent SDK to bytecode — a countdown, never an error.
                run_id = jsonl.parent.name
                print(f"── gpi v{__version__} watch: {run_id} ──")
                print(f"Waiting for run output — {int(now - start)}s elapsed, none yet. "
                      "A cold first run compiles dependencies for up to ~5 min before the first "
                      "event; this is normal. Do not relaunch.")
                print("CONTINUE")
                return 0
            _print_report(_fold(events), snapshot, events[base_n:], now - start)
            return 0
        time.sleep(min(poll, max(0.05, deadline - time.time())))


# ----------------------------------------------------------------------------- continuous view

def _run_continuous(jsonl: Path, snapshot: Path, poll: float) -> int:
    """A live ``top``-style view until the run ends or Ctrl-C. Rich on a TTY, plain text when
    piped. For an interactive human watching over SSH."""
    on_tty = False
    try:
        on_tty = bool(sys.stdout.isatty())
    except Exception:
        on_tty = False

    renderer = None
    if on_tty:
        try:
            from .progress import RichRenderer
            renderer = RichRenderer()
        except Exception:
            renderer = None
    try:
        while True:
            events = _read_events(jsonl)
            snap = _fold(events) if events else None
            if snap is None:
                sys.stdout.write("\rwaiting for run output (cold start can take ~5 min)…   ")
                sys.stdout.flush()
            elif renderer is not None:
                renderer.on_event({}, snap)
            else:
                # Plain re-render: clear screen, repaint the checklist.
                sys.stdout.write("\x1b[2J\x1b[H")
                print(render_txt(snap))
                state, reason = liveness(snap, snapshot)
                print(f"\nLiveness: {state} — {reason}")
            if snap is not None:
                state, _ = liveness(snap, snapshot)
                if state in ("done", "failed", "dead"):
                    break
            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        if renderer is not None:
            try:
                renderer.stop()
            except Exception:
                pass
    # A final, clean snapshot after the Live view is torn down.
    events = _read_events(jsonl)
    if events:
        snap = _fold(events)
        print(render_txt(snap))
        state, reason = liveness(snap, snapshot)
        print(f"Liveness: {state} — {reason}")
    return 0


def cmd_watch(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="gpi watch",
        description="Read-only live view of a run's progress (folds its progress.jsonl).",
    )
    parser.add_argument("run_dir", help="The run's output directory (contains progress.jsonl).")
    parser.add_argument(
        "--until-change", action="store_true",
        help="Block until a milestone event or --timeout, print what changed plus a liveness "
             "verdict, and exit 0. The last line is a token: CONTINUE / DONE / FAILED / STALE.",
    )
    parser.add_argument("--timeout", type=float, default=55.0,
                        help="[--until-change] Max seconds to block (default 55).")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="Poll interval in seconds (default 2.0).")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    jsonl = run_dir / "progress.jsonl"
    snapshot = run_dir / "progress.json"

    if not run_dir.exists():
        print(f"gpi watch: run directory does not exist yet: {run_dir}")
        print("CONTINUE" if args.until_change else "")
        return 0

    poll = max(0.2, min(args.interval, 10.0))
    if args.until_change:
        return _run_until_change(jsonl, snapshot, timeout=max(1.0, args.timeout), poll=poll)
    return _run_continuous(jsonl, snapshot, poll=poll)
