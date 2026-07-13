"""Unit tests for gpi.progress — the SnapshotReducer fold and markdown rendering.

Pure: no SDK, no network, no filesystem (the reducer/renderers are deterministic functions
over an event list).
"""

from gpi.progress import (
    AGENT_FINISHED,
    AGENT_STARTED,
    AGENT_TOOL_CALL,
    RESEARCH_START,
    RUN_DONE,
    RUN_START,
    STEP_DONE,
    STEP_PROGRESS,
    STEP_START,
    SnapshotReducer,
    render_markdown,
    render_txt,
)

STEPS = ["string_enrichment", "gene_summaries", "bundle", "research", "html_report"]


def _reduce(events):
    r = SnapshotReducer()
    for ev in events:
        r.apply(ev)
    return r.snapshot()


def test_step_lifecycle_and_subprogress():
    snap = _reduce([
        {"type": RUN_START, "steps": STEPS, "run_id": "r1"},
        {"type": STEP_START, "step": "string_enrichment", "executor": "4", "index": 1, "n_steps": 5},
        {"type": STEP_PROGRESS, "step": "string_enrichment", "current": 2, "total": 3, "detail": "program 11"},
        {"type": STEP_DONE, "step": "string_enrichment", "status": "completed"},
        {"type": STEP_START, "step": "gene_summaries", "executor": "4", "index": 2, "n_steps": 5},
    ])
    steps = {s["name"]: s for s in snap["steps"]}
    assert [s["name"] for s in snap["steps"]] == STEPS  # order preserved
    assert steps["string_enrichment"]["status"] == "completed"
    # sub-progress is cleared once the step completes
    assert steps["string_enrichment"]["current"] is None
    assert steps["gene_summaries"]["status"] == "in_progress"
    assert steps["bundle"]["status"] == "pending"
    assert snap["active_step"] == "gene_summaries"


def test_midflight_subprogress_visible():
    snap = _reduce([
        {"type": RUN_START, "steps": STEPS},
        {"type": STEP_START, "step": "gene_summaries", "executor": "4"},
        {"type": STEP_PROGRESS, "step": "gene_summaries", "current": 40, "total": 69, "detail": "NCBI"},
    ])
    s = next(x for x in snap["steps"] if x["name"] == "gene_summaries")
    assert (s["current"], s["total"]) == (40, 69)
    md = render_markdown(snap)
    assert "40/69" in md  # sub-progress surfaces in the checklist


def test_research_agent_table_and_cost_none():
    snap = _reduce([
        {"type": RUN_START, "steps": STEPS},
        {"type": STEP_START, "step": "research", "executor": "2"},
        {"type": RESEARCH_START, "n_programs": 3, "concurrency": 3},
        {"type": AGENT_STARTED, "program_id": "P10"},
        {"type": AGENT_TOOL_CALL, "program_id": "P10", "tool": "mcp__literature__search_pubmed", "turn": 2},
        {"type": AGENT_FINISHED, "program_id": "P10", "status": "ok", "num_turns": 12, "cost_usd": 0.42},
        {"type": AGENT_STARTED, "program_id": "P11"},
        # subscription auth → cost_usd is None; must not crash and must not count toward total
        {"type": AGENT_FINISHED, "program_id": "P11", "status": "ok", "num_turns": 9, "cost_usd": None},
    ])
    r = snap["research"]
    assert r["n_programs"] == 3
    assert r["n_done"] == 2
    assert r["total_cost_usd"] == 0.42  # only P10 counted; None ignored
    agents = {a["program_id"]: a for a in r["agents"]}
    assert agents["P10"]["turns"] == 12 and agents["P10"]["status"] == "ok"
    assert agents["P11"]["cost_usd"] is None
    md = render_markdown(snap)
    assert "| P10 |" in md and "| P11 |" in md
    assert "$0.42" in md and "—" in md  # None cost rendered as em dash


def test_run_done_and_txt_render():
    snap = _reduce([
        {"type": RUN_START, "steps": STEPS},
        {"type": STEP_DONE, "step": "string_enrichment", "status": "completed"},
        {"type": RUN_DONE, "status": "done"},
    ])
    assert snap["status"] == "done"
    assert snap["active_step"] is None
    txt = render_txt(snap)
    assert "[x] string_enrichment" in txt


def test_reducer_tolerates_unknown_and_out_of_order():
    # Unknown event types and a step_progress before its step_start must not raise.
    snap = _reduce([
        {"type": "totally_unknown", "foo": 1},
        {"type": STEP_PROGRESS, "step": "research", "current": 1, "total": 3},
        {"type": RUN_START, "steps": STEPS},
    ])
    assert isinstance(snap["steps"], list)
