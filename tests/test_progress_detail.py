"""Per-agent telemetry detail: the query, the failure reason, and the clock.

These pin the fixes for a specific complaint: during research the log said only
``P22 → search_pubmed (turn 14)``, repeated dozens of times, and a program that quietly fell
back to a deterministic result looked identical to a cheap success. You could not tell a
productive agent from a looping one, or a real result from a fallback.

Voice: each test names the incident it prevents (cf. tests/test_progress_liveness.py).
"""

from __future__ import annotations

import json
import time

from gpi.progress import (
    AGENT_FINISHED,
    AGENT_STARTED,
    AGENT_TOOL_CALL,
    RESEARCH_START,
    SnapshotReducer,
    _research_footer,
)
from research.research_parallel import _tool_detail

# Linux PIPE_BUF — the bound under which a single O_APPEND os.write is atomic across processes.
PIPE_BUF = 4096


def _agent(snap, pid):
    return next(a for a in snap["research"]["agents"] if a["program_id"] == str(pid))


def test_a_tool_call_says_what_it_asked_for() -> None:
    """For six minutes the log said `P22 → search_pubmed (turn 14)`, forty times over — the
    query string was computed one line above the emit and thrown away. Now it rides along."""
    detail = _tool_detail({"query": "Madcam1 brain endothelial venous identity", "max_results": 8})
    assert detail == "Madcam1 brain endothelial venous identity"

    red = SnapshotReducer()
    red.apply({"type": AGENT_TOOL_CALL, "ts": 1000.0, "program_id": "22", "turn": 14,
               "tool": "literature.search_pubmed", "detail": detail})
    assert _agent(red.snapshot(now=1001.0), 22)["current_detail"] == detail


def test_a_tool_call_event_stays_under_pipe_buf() -> None:
    """The cap in _tool_detail is load-bearing, not cosmetic. agent_tool_call events are
    appended to ONE shared progress.jsonl by a bare O_APPEND os.write, whose cross-process
    atomicity holds only below PIPE_BUF. An unbounded argument (here 10 KB) would tear
    interleaved writes from concurrent agents and corrupt the fold."""
    huge = {"query": "x" * 10_000, "note": "y" * 10_000}
    payload = {"program_id": "48", "tool": "literature.search_pubmed", "turn": 99,
               "detail": _tool_detail(huge)}
    # Serialize exactly as emit_progress does (gpi/progress.py::emit_progress).
    rec = {"ts": round(time.time(), 3), "type": AGENT_TOOL_CALL, **payload}
    line = (json.dumps(rec, separators=(",", ":"), default=str) + "\n").encode("utf-8")
    assert len(line) < PIPE_BUF, f"agent_tool_call line is {len(line)} B — would tear at PIPE_BUF"


def test_a_failed_agent_states_its_reason() -> None:
    """The step-level twin of this bug (a failed step dropping its error) already has a
    regression test; the agent level did not. research_parallel sent the error on the fallback
    path and _apply_agent dropped it, so an 'incomplete' program looked like a cheap success."""
    red = SnapshotReducer()
    red.apply({"type": AGENT_FINISHED, "ts": 1400.0, "program_id": "70", "status": "incomplete",
               "num_turns": 2, "cost_usd": None, "error": "per_program_timeout (900s) exceeded"})
    assert _agent(red.snapshot(), 70)["error"] == "per_program_timeout (900s) exceeded"


def test_a_failed_program_is_not_counted_as_a_healthy_one() -> None:
    """'3/3 done' must not silently hide failures. incomplete/failed count toward n_done (the
    program is finished) but ALSO toward n_incomplete, so the footer can say '(1 incomplete)'."""
    red = SnapshotReducer()
    red.apply({"type": AGENT_FINISHED, "ts": 1.0, "program_id": "9", "status": "ok",
               "num_turns": 40, "cost_usd": 0.6})
    red.apply({"type": AGENT_FINISHED, "ts": 2.0, "program_id": "48", "status": "ok",
               "num_turns": 41, "cost_usd": 0.7})
    red.apply({"type": AGENT_FINISHED, "ts": 3.0, "program_id": "70", "status": "incomplete",
               "num_turns": 2, "cost_usd": None, "error": "boom"})
    r = red.snapshot()["research"]
    assert r["n_done"] == 3 and r["n_incomplete"] == 1
    assert "(1 incomplete)" in _research_footer(r)


def test_an_agent_reports_how_long_it_has_been_thinking() -> None:
    """idle_s (seconds since the last tool call) is the signal that separates a thinking agent
    from a wedged one. Before, the only way to get it was to diff two snapshots by hand."""
    red = SnapshotReducer()
    red.apply({"type": AGENT_STARTED, "ts": 1000.0, "program_id": "9"})
    red.apply({"type": AGENT_TOOL_CALL, "ts": 1005.0, "program_id": "9", "turn": 1,
               "tool": "literature.search_pubmed", "detail": "Cldn5"})
    a = _agent(red.snapshot(now=1050.0), 9)
    assert a["elapsed_s"] == 50.0   # since started
    assert a["idle_s"] == 45.0      # since last tool call


def test_a_key_in_a_tool_argument_never_reaches_the_snapshot() -> None:
    """The skill reads progress.json straight back into an agent's context, so a secret in a
    tool argument (a URL with api_key=, a bare sk- key) must be redacted before it lands."""
    red = SnapshotReducer()
    red.apply({"type": AGENT_TOOL_CALL, "ts": 1.0, "program_id": "9", "turn": 1,
               "tool": "resolve_doi",
               "detail": "https://eutils.ncbi.nlm.nih.gov/e?api_key=SECRET123456 sk-ant-ABCDEFGH1234567890"})
    detail = _agent(red.snapshot(), 9)["current_detail"]
    assert "SECRET123456" not in detail
    assert "sk-ant-ABCDEFGH1234567890" not in detail


def test_subscription_cost_is_not_labeled_as_a_dollar_charge() -> None:
    """Default research auth is the Claude.ai subscription, so total_cost_usd is API-equivalent
    accounting, NOT a separate charge. Labeling it '$2.33 total' overstates the bill."""
    red = SnapshotReducer()
    red.apply({"type": RESEARCH_START, "ts": 1.0, "n_programs": 3, "concurrency": 4,
               "auth": "subscription"})
    footer = _research_footer(red.snapshot()["research"])
    assert "subscription" in footer.lower()
