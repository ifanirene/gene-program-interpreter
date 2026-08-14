"""Deterministic schema-v2 literature trace regressions."""

from __future__ import annotations

import asyncio
import json

from mcp.types import CallToolRequest, CallToolRequestParams

from research.literature import (
    LiteratureTraceRecorder,
    MAX_FETCH_IDS,
    build_literature_mcp_server,
)
from research.research_parallel import (
    _attempt_totals,
    _write_audit,
    derive_queries_from_trace,
    summarize_literature_trace,
)


class _FakeClient:
    async def search_pubmed(self, query, *, max_results):
        return {"query": query, "count": 2, "pmids": ["11", "22"]}

    async def search_openalex(self, query, *, max_results):
        return {
            "query": query,
            "count": 1,
            "records": [{"pmid": None, "doi": "10.1/example"}],
        }

    async def fetch_pubmed(self, pmids):
        return [{"pmid": pmid} for pmid in pmids]

    async def resolve_doi(self, identifier):
        return {"doi": "10.1/example", "title": "ok"}


class _FailingClient(_FakeClient):
    async def search_pubmed(self, query, *, max_results):
        raise RuntimeError("transport failed; token=secret-value")


async def _call(server, name, arguments):
    handler = server["instance"].request_handlers[CallToolRequest]
    return await handler(
        CallToolRequest(params=CallToolRequestParams(name=name, arguments=arguments))
    )


def test_exact_long_query_phase_targets_and_limits_are_recorded():
    events = []
    server = build_literature_mcp_server(
        _FakeClient(), trace_recorder=LiteratureTraceRecorder(events, attempt=2)
    )
    query = "Kdr " + "brain endothelial barrier " * 15
    asyncio.run(
        _call(
            server,
            "search_pubmed",
            {
                "query": query,
                "max_results": 999,
                "research_phase": "supplied_gene",
                "target_genes": ["Kdr", "Cldn5"],
            },
        )
    )
    event = events[0]
    assert event["attempt"] == 2 and event["sequence"] == 1
    assert event["query"] == query.strip()
    assert event["target_genes"] == ["Kdr", "Cldn5"]
    assert event["requested_limit"] == 999 and event["effective_limit"] == 10
    assert event["returned_identifiers"] == ["11", "22"]
    assert "abstract" not in event and "title" not in event and "headers" not in event


def test_failure_fetch_cap_and_secret_payload_exclusion():
    events = []
    server = build_literature_mcp_server(
        _FailingClient(), trace_recorder=LiteratureTraceRecorder(events)
    )
    asyncio.run(
        _call(
            server,
            "search_pubmed",
            {
                "query": "Kdr",
                "max_results": 5,
                "research_phase": "gap",
                "target_genes": ["Kdr"],
                # schema rejects unknown payload fields before they can enter the trace
            },
        )
    )
    assert events[0]["status"] == "error"
    assert "secret-value" not in events[0]["error"]
    assert "<redacted>" in events[0]["error"]
    assert all(key not in events[0] for key in ("payload", "headers", "url"))

    fetch_events = []
    fetch_server = build_literature_mcp_server(
        _FakeClient(), trace_recorder=LiteratureTraceRecorder(fetch_events)
    )
    asyncio.run(
        _call(
            fetch_server,
            "fetch_pubmed",
            {"pmids": list(range(1, 31)), "research_phase": "theme", "target_genes": []},
        )
    )
    assert fetch_events[0]["requested_limit"] == 30
    assert fetch_events[0]["effective_limit"] == MAX_FETCH_IDS == 20


def test_query_derivation_coverage_and_attempt_totals(tmp_path):
    events = [
        {"source": "pubmed", "action": "search", "query": "Kdr brain",
         "research_phase": "supplied_gene", "target_genes": ["Kdr"]},
        {"source": "pubmed", "action": "fetch", "research_phase": "supplied_gene",
         "target_genes": ["Kdr"]},
        {"source": "openalex", "action": "search", "query": "Kdr brain",
         "research_phase": "gap", "target_genes": ["Kdr"]},
        {"source": "openalex", "action": "search", "query": "Vegfa pathway",
         "research_phase": "regulator", "target_genes": ["Vegfa"]},
    ]
    assert derive_queries_from_trace(events) == ["Kdr brain", "Vegfa pathway"]
    summary = summarize_literature_trace(events, ["Kdr", "Cldn5"])
    assert summary["attempted_supplied_genes"] == ["Kdr"]
    assert summary["unattempted_supplied_genes"] == ["Cldn5"]
    assert summary["regulator_query_count"] == 1

    records = [
        {"cost_usd": 0.2, "num_turns": 3, "duration_ms": 10,
         "tokens": {"input": 1, "output": 2, "total": 3}},
        {"cost_usd": 0.3, "num_turns": 4, "duration_ms": 20,
         "tokens": {"input": 4, "output": 5, "total": 9}},
    ]
    totals = _attempt_totals(records)
    assert totals["cost_usd"] == 0.5 and totals["num_turns"] == 7
    assert totals["tokens"]["total"] == 12

    audit_dir = tmp_path / "audit"
    _write_audit(
        audit_dir, "P1", prompt="p", model="m", mcp_server_names=[], tool_trace=[],
        literature_trace=events, attempt_records=records, trace_summary=summary,
        cost_usd=totals["cost_usd"], num_turns=totals["num_turns"], status="ok",
        attempts=2, duration_ms=totals["duration_ms"], token_summary=totals["tokens"],
    )
    audit = json.loads((audit_dir / "P1.audit.json").read_text())
    assert audit["attempts"] == 2 and len(audit["attempt_records"]) == 2
    assert audit["literature_trace_schema_version"] == 2


def test_old_audit_without_trace_remains_readable():
    old = {"program_id": "P1", "tool_trace": [], "attempts": 1}
    assert derive_queries_from_trace(old.get("literature_trace") or []) == []
    assert summarize_literature_trace(old.get("literature_trace") or [], ["Kdr"])[
        "unattempted_supplied_genes"
    ] == ["Kdr"]
