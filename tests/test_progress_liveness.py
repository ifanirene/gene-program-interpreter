"""What a monitor needs in order to tell a working run from a dead one.

The user's report was "I cannot see any progress, whether it has failed or merely waiting for
something to finish." Two separate defects sat behind that:

  * When a step failed, ``run_pipeline`` *did* send the reason, and the snapshot reducer
    silently **dropped it** — so a monitor could only ever say "failed", with no cause.
  * ``updated_at`` is the time of the last pipeline EVENT, and during healthy literature
    research it can legitimately sit still for 7+ minutes while an agent thinks. A monitor that
    reads a frozen ``updated_at`` as a hang will kill a healthy job — which very nearly
    happened. ``heartbeat_at`` is the time of the last snapshot WRITE, and it advances while the
    process lives. That is the liveness signal; ``updated_at`` is not.
"""

from __future__ import annotations

import json
from pathlib import Path

from gpi.progress import RUN_START, STEP_DONE, STEP_START, SnapshotReducer

STEPS = ["string_enrichment", "bundle", "research", "verify", "annotate"]


def _reducer(t0: float = 1_000_000.0) -> SnapshotReducer:
    reducer = SnapshotReducer()
    reducer.apply({"type": RUN_START, "ts": t0, "run_id": "test-run",
                   "steps": STEPS, "n_steps": len(STEPS)})
    return reducer


def _step(snapshot: dict, name: str) -> dict:
    return next(s for s in snapshot["steps"] if s["name"] == name)


def test_a_failed_step_keeps_its_error() -> None:
    """The reducer used to drop this, so the monitor showed a dead run with no cause."""
    reducer = _reducer()
    reducer.apply({"type": STEP_START, "ts": 1_000_001.0, "step": "research", "executor": "2"})
    reducer.apply({"type": STEP_DONE, "ts": 1_000_002.0, "step": "research",
                   "status": "failed", "error": "boom: no module named x"})
    assert _step(reducer.snapshot(), "research")["error"] == "boom: no module named x"


def test_the_run_names_the_step_that_killed_it() -> None:
    reducer = _reducer()
    reducer.apply({"type": STEP_START, "ts": 1_000_001.0, "step": "verify", "executor": "4"})
    reducer.apply({"type": STEP_DONE, "ts": 1_000_002.0, "step": "verify",
                   "status": "failed", "error": "kernel.py not found"})
    assert reducer.snapshot()["failed_step"] == "verify"


def test_a_successful_step_carries_no_error() -> None:
    reducer = _reducer()
    reducer.apply({"type": STEP_START, "ts": 1_000_001.0, "step": "bundle", "executor": "4"})
    reducer.apply({"type": STEP_DONE, "ts": 1_000_002.0, "step": "bundle", "status": "completed"})
    snapshot = reducer.snapshot()
    assert _step(snapshot, "bundle")["error"] is None
    assert snapshot["failed_step"] is None


def test_heartbeat_advances_while_nothing_happens() -> None:
    """The distinction the whole monitoring story rests on.

    No events are applied between the two snapshots — exactly the state of a healthy research
    step whose agent is mid-think. ``heartbeat_at`` must still move, or a monitor cannot tell
    this apart from a hang, and the safe-looking action (kill it) destroys paid work.
    """
    reducer = _reducer()
    reducer.apply({"type": STEP_START, "ts": 1_000_001.0, "step": "research", "executor": "2"})

    first = reducer.snapshot(now=1_000_010.0)
    second = reducer.snapshot(now=1_000_040.0)  # 30s later, zero events in between

    assert first["updated_at"] == second["updated_at"], (
        "no event occurred, so the EVENT clock must not move — that is what makes it useless "
        "as a liveness signal"
    )
    assert second["heartbeat_at"] != first["heartbeat_at"], (
        "heartbeat_at froze with the event clock — a monitor would read a healthy, thinking "
        "research agent as a hung process and kill it"
    )


def test_the_snapshot_identifies_the_process() -> None:
    """A monitor that cannot name the process cannot check whether it is still alive."""
    snapshot = _reducer().snapshot()
    assert isinstance(snapshot["pid"], int)
    assert snapshot["started_at"]


def test_a_resumed_run_does_not_serve_the_previous_run_as_done(tmp_path: Path) -> None:
    """``progress.json`` was never truncated at start, so a monitor polling a resumed run could
    read the PREVIOUS run's ``status: "done"`` and report a finished pipeline that had not begun.
    """
    from gpi.progress import make_emitter  # noqa: PLC0415

    snapshot_path = tmp_path / "progress.json"
    snapshot_path.write_text(
        json.dumps({"status": "done", "run_id": "an-old-run"}), encoding="utf-8"
    )

    emitter = make_emitter(tmp_path, mode="plain")
    try:
        current = json.loads(snapshot_path.read_text())
        assert current.get("status") != "done", (
            "the previous run's snapshot survived into this one — a monitor polling at t=0 sees "
            "a completed run that never started"
        )
    finally:
        emitter.close()
