"""``gpi watch`` — the read-only tailer whose token contract makes the skill's poll loop work.

Incident it prevents: the skill's old recipe ("poll progress.json every 45-60 s in separate
tool calls") is unexecutable — a model has no timer, so it printed "no progress.json yet" into a
5-minute cold-start void and gave up. ``gpi watch --until-change`` blocks, folds the same event
log the pipeline writes, and ends with a token the skill obeys.
"""

from __future__ import annotations

import json
import os
import time

from gpi import watch


def _write(run_dir, events, *, status="running", pid=None, snap_age=0.0):
    run_dir.mkdir(parents=True, exist_ok=True)
    jsonl = run_dir / "progress.jsonl"
    jsonl.write_text("\n".join(json.dumps(e) for e in events) + ("\n" if events else ""))
    snap = run_dir / "progress.json"
    snap.write_text(json.dumps({"status": status, "pid": pid if pid is not None else os.getpid()}))
    if snap_age:
        old = time.time() - snap_age
        os.utime(snap, (old, old))
    return jsonl, snap


def test_watch_waits_for_a_run_that_has_not_written_anything_yet(tmp_path, capsys) -> None:
    """The cold-start void: no progress.jsonl for minutes. watch must print a countdown and
    return CONTINUE — NOT 'no progress.json' and NOT an error that reads like a failed launch."""
    run = tmp_path / "run"
    run.mkdir()
    rc = watch.cmd_watch([str(run), "--until-change", "--timeout", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip().splitlines()[-1] == "CONTINUE"
    assert "Waiting for run output" in out


def test_watch_reads_a_run_it_did_not_start(tmp_path, capsys) -> None:
    """watch is a pure fold over another process's progress.jsonl — it needs no handle to the
    pipeline, which is the whole point for a background/SSH run."""
    run = tmp_path / "run"
    _write(run, [
        {"ts": 1.0, "type": "run_start", "run_id": "r", "steps": ["preflight", "research"]},
        {"ts": 2.0, "type": "step_done", "step": "preflight", "status": "completed"},
        {"ts": 3.0, "type": "research_start", "n_programs": 2, "concurrency": 4, "auth": "subscription"},
        {"ts": 4.0, "type": "agent_started", "program_id": "9"},
    ])
    rc = watch.cmd_watch([str(run), "--until-change", "--timeout", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "research" in out
    assert out.strip().splitlines()[-1] == "CONTINUE"  # alive: fresh snapshot


def test_watch_calls_a_thinking_agent_alive_and_a_dead_process_dead(tmp_path) -> None:
    """A monitoring bug looks exactly like the incident it falsely reports. Liveness must key on
    the pipeline's OWN pid (from progress.json, not the re-folded snapshot, which would carry the
    watcher's pid) plus snapshot mtime: fresh snapshot ⇒ alive even with zero new events; gone
    process + old snapshot ⇒ dead."""
    events = [
        {"ts": 1.0, "type": "run_start", "run_id": "r", "steps": ["research"]},
        {"ts": 2.0, "type": "step_start", "step": "research", "executor": "②"},
    ]
    # Alive: our own pid, fresh snapshot, no new events → the thinking-agent case.
    alive = tmp_path / "alive"
    _, snap = _write(alive, events)
    state, _ = watch.liveness(watch._fold(watch._read_events(alive / "progress.jsonl")), snap)
    assert state == "alive"

    # Dead: a pid that is not running, stale snapshot, status still 'running'.
    dead = tmp_path / "dead"
    _, snap_d = _write(dead, events, pid=2_147_480_000, snap_age=400.0)
    state_d, _ = watch.liveness(watch._fold(watch._read_events(dead / "progress.jsonl")), snap_d)
    assert state_d == "dead"


def test_until_change_prints_a_token_the_skill_can_obey(tmp_path, capsys) -> None:
    """The whole skill fix rests on this: DONE at run_done, FAILED at a failed step, and the
    token is always the LAST line, alone."""
    done = tmp_path / "done"
    _write(done, [
        {"ts": 1.0, "type": "run_start", "run_id": "r", "steps": ["research"]},
        {"ts": 2.0, "type": "run_done", "status": "done"},
    ], status="done")
    assert watch.cmd_watch([str(done), "--until-change", "--timeout", "1"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "DONE"

    failed = tmp_path / "failed"
    _write(failed, [
        {"ts": 1.0, "type": "run_start", "run_id": "r", "steps": ["verify"]},
        {"ts": 2.0, "type": "step_done", "step": "verify", "status": "failed",
         "error": "citation 10.1/x did not resolve"},
        {"ts": 3.0, "type": "run_done", "status": "failed"},
    ], status="failed")
    assert watch.cmd_watch([str(failed), "--until-change", "--timeout", "1"]) == 0
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == "FAILED"


def test_until_change_wakes_early_on_a_milestone(tmp_path, capsys) -> None:
    """A milestone that ARRIVES during the block wakes it well before the timeout — otherwise the
    skill's cadence would be pinned to the timeout instead of to real events."""
    run = tmp_path / "run"
    jsonl, _ = _write(run, [
        {"ts": 1.0, "type": "run_start", "run_id": "r", "steps": ["research"]},
        {"ts": 2.0, "type": "agent_started", "program_id": "9"},
    ])
    import threading

    def _append_soon():
        time.sleep(0.6)
        with open(jsonl, "a") as f:
            f.write(json.dumps({"ts": 3.0, "type": "agent_finished", "program_id": "9",
                                "status": "ok", "num_turns": 40, "cost_usd": 0.6}) + "\n")

    threading.Thread(target=_append_soon, daemon=True).start()
    t0 = time.time()
    watch.cmd_watch([str(run), "--until-change", "--timeout", "30"])
    elapsed = time.time() - t0
    out = capsys.readouterr().out
    assert elapsed < 5.0, f"did not wake early on the milestone (took {elapsed:.1f}s)"
    assert "agent_finished" in out or "finished" in out
    assert out.strip().splitlines()[-1] == "CONTINUE"
