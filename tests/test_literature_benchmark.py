"""Offline contract tests for the literature benchmark."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from research import benchmark


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "benchmarks" / "literature-v1" / "manifest.yaml"


class FakeResponse:
    def __init__(self, *, text: str = "", payload=None, status: int = 200):
        self.text = text
        self._payload = payload if payload is not None else {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise benchmark.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_manifest_freezes_exact_cohort_hashes_and_gene_contract():
    manifest = benchmark.load_manifest(MANIFEST)

    assert len(manifest["cases"]) == 10
    assert sum(case["repeat_count"] for case in manifest["cases"]) == 14
    assert manifest["sentinel_case_ids"] == [
        "brain-p10",
        "brain-p11",
        "liver-p21",
        "liver-p25",
    ]
    assert manifest["budget"]["max_api_equivalent_usd"] == 28.0
    assert manifest["execution"]["auth"] == "subscription"
    for case in manifest["cases"]:
        loading = case["expected"]["program_genes"]
        distinctive = case["expected"]["distinctive_genes"]
        assert len(loading) == 15 and len(distinctive) == 8
        assert set(loading).isdisjoint(distinctive)


def test_manifest_rejects_nonportable_paths(tmp_path):
    raw = benchmark.yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    raw["cases"][0]["profile_path"] = str(MANIFEST.resolve())
    path = tmp_path / "manifest.yaml"
    path.write_text(benchmark.yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(benchmark.BenchmarkError, match="must be relative"):
        benchmark.load_manifest(path)


def test_manifest_ties_execution_budget_to_approved_ceiling(tmp_path, monkeypatch):
    root = MANIFEST.parent
    original_portable_path = benchmark._portable_path
    monkeypatch.setattr(
        benchmark,
        "_portable_path",
        lambda _root, value, *, field: original_portable_path(root, value, field=field),
    )
    raw = benchmark.yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    raw["execution"]["max_budget_usd"] = 100.0
    path = tmp_path / "manifest.yaml"
    path.write_text(benchmark.yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(benchmark.BenchmarkError, match="must equal budget"):
        benchmark.load_manifest(path)


def test_prepare_dry_run_is_offline_and_writes_nothing(tmp_path, monkeypatch):
    def network_forbidden(*_args, **_kwargs):
        raise AssertionError("dry-run attempted network access")

    monkeypatch.setattr(benchmark.requests, "get", network_forbidden)
    out = tmp_path / "should-not-exist"
    summary = benchmark.prepare(MANIFEST, out, dry_run=True)

    assert summary["n_cases"] == 10
    assert summary["n_sessions"] == 14
    assert summary["approval_required"] is True
    assert summary["max_api_equivalent_usd"] == 28.0
    assert not out.exists()


def test_prepare_regenerates_isolated_repeat_bundles_and_metadata(tmp_path):
    out = tmp_path / "baseline-v1"
    summary = benchmark.prepare(MANIFEST, out)

    plan = json.loads((out / "run_plan.json").read_text())
    assert summary["n_sessions"] == 14
    assert plan["approval"]["status"] == "required"
    assert plan["execution"]["auth"] == "subscription"
    assert len({session["session_id"] for session in plan["sessions"]}) == 14
    assert (
        out / "sessions" / "brain-p10--r1" / "program_bundles" / "P10.json"
    ).is_file()
    assert (
        out / "sessions" / "brain-p10--r2" / "program_bundles" / "P10.json"
    ).is_file()
    assert not list(out.rglob("research_results/*.json"))
    environment_text = (out / "environment.json").read_text()
    assert "source_hashes" in environment_text and "max_fetch_ids" in environment_text
    assert (out / "artifact_hashes.json").is_file()

    with pytest.raises(benchmark.BenchmarkError, match="refusing to overwrite"):
        benchmark.prepare(MANIFEST, out)


def test_paid_executor_requires_exact_recorded_approval(tmp_path):
    out = tmp_path / "baseline-v1"
    benchmark.prepare(MANIFEST, out)
    approval = tmp_path / "approval.json"
    approval.write_text(
        json.dumps(
            {
                "approved": True,
                "benchmark_id": "literature-v1",
                "max_api_equivalent_usd": 27.0,
                "approved_by": "test",
                "approved_at": "2026-07-20T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(benchmark.BenchmarkError, match=r"exact \$28.00 ceiling"):
        benchmark.run_paid_baseline(out, approval)


def test_paid_executor_persists_approval_and_ledger_before_launch(tmp_path, monkeypatch):
    out = tmp_path / "baseline-v1"
    benchmark.prepare(MANIFEST, out)
    approval = tmp_path / "approval.json"
    approval.write_text(
        json.dumps(
            {
                "approved": True,
                "benchmark_id": "literature-v1",
                "max_api_equivalent_usd": 28.0,
                "approved_by": "test",
                "approved_at": "2026-07-20T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    async def stop_before_paid(run_dir, _plan, _sessions, _ledger, _ledger_path):
        assert (run_dir / "approval_record.json").is_file()
        assert (run_dir / "paid_run_ledger.json").is_file()
        allocations = [
            entry["max_api_equivalent_allocation_usd"]
            for entry in _ledger["sessions"].values()
        ]
        assert allocations == [2.0] * 14
        assert sum(allocations) == 28.0
        raise RuntimeError("stop before paid test")

    monkeypatch.setattr(
        benchmark,
        "_subscription_auth_status",
        lambda: {
            "logged_in": True,
            "auth_method": "claude.ai",
            "api_provider": "firstParty",
            "command_exit_code": 0,
        },
    )
    monkeypatch.setattr(benchmark, "_execute_paid_sessions", stop_before_paid)
    with pytest.raises(RuntimeError, match="stop before paid test"):
        benchmark.run_paid_baseline(out, approval)


def test_paid_executor_rejects_expired_subscription_before_writing_ledger(
    tmp_path, monkeypatch
):
    out = tmp_path / "baseline-v1"
    benchmark.prepare(MANIFEST, out)
    approval = tmp_path / "approval.json"
    approval.write_text(
        json.dumps(
            {
                "approved": True,
                "benchmark_id": "literature-v1",
                "max_api_equivalent_usd": 28.0,
                "approved_by": "test",
                "approved_at": "2026-07-20T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        benchmark,
        "_subscription_auth_status",
        lambda: {
            "logged_in": False,
            "auth_method": "none",
            "api_provider": "firstParty",
            "command_exit_code": 1,
        },
    )

    with pytest.raises(benchmark.BenchmarkError, match="not authenticated"):
        benchmark.run_paid_baseline(out, approval)

    assert not (out / "approval_record.json").exists()
    assert not (out / "paid_run_ledger.json").exists()


@pytest.mark.parametrize(
    ("terminal_error", "expected_attempts"),
    [
        (None, 2),
        ("client max_turns reached (30)", 1),
        ("budget limit reached", 1),
    ],
)
def test_failed_canary_blocks_remaining_paid_sessions(
    tmp_path, monkeypatch, terminal_error, expected_attempts
):
    from research import research_parallel

    run_dir = tmp_path / "run"
    sessions = []
    for index in range(1, 4):
        session_id = f"case-p{index}--r1"
        sessions.append(
            {
                "session_id": session_id,
                "program_id": f"P{index}",
                "bundle": f"sessions/{session_id}/program_bundles/P{index}.json",
                "result_dir": f"sessions/{session_id}/research_results",
                "audit_dir": f"sessions/{session_id}/research_audit",
            }
        )
        bundle = run_dir / sessions[-1]["bundle"]
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text(json.dumps({"program_id": f"P{index}"}), encoding="utf-8")

    ledger = {
        "canary_session_id": sessions[0]["session_id"],
        "fanout_status": "not_started",
        "sessions": {
            session["session_id"]: {
                "program_id": session["program_id"],
                "status": "pending",
                "max_api_equivalent_allocation_usd": 2.0,
            }
            for session in sessions
        },
    }
    ledger_path = run_dir / "paid_run_ledger.json"
    calls = []

    async def failed_handler(bundle_path, *, out_dir, audit_dir, **_kwargs):
        program_id = bundle_path.stem
        calls.append(program_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        audit_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{program_id}.json").write_text("{}", encoding="utf-8")
        (audit_dir / f"{program_id}.audit.json").write_text(
            json.dumps(
                {
                    "status": "incomplete",
                    "attempts": 1,
                    "cost_usd": None,
                    "error": terminal_error,
                }
            ),
            encoding="utf-8",
        )

    class FakeClient:
        async def aclose(self):
            return None

    monkeypatch.setattr(research_parallel, "_handle_one_program", failed_handler)
    monkeypatch.setattr(research_parallel, "load_env_file", lambda: None)
    monkeypatch.setattr(research_parallel, "read_protocol", lambda: "protocol")
    monkeypatch.setattr(benchmark, "LiteratureClient", FakeClient)

    asyncio.run(
        benchmark._execute_paid_sessions(
            run_dir,
            {
                "execution": {
                    "concurrency": 3,
                    "model": "test-model",
                    "max_turns": 30,
                    "max_budget_usd": 1.0,
                    "max_attempts": 2,
                    "per_program_timeout_seconds": 600,
                }
            },
            sessions,
            ledger,
            ledger_path,
        )
    )

    assert calls == ["P1"] * expected_attempts
    assert ledger["fanout_status"] == "blocked_by_canary"
    assert ledger["sessions"][sessions[0]["session_id"]]["attempts"] == expected_attempts
    assert (
        len(ledger["sessions"][sessions[0]["session_id"]]["attempt_records"])
        == expected_attempts
    )
    assert (
        run_dir
        / sessions[0]["audit_dir"]
        / "attempts"
        / "attempt-1"
        / "audit.json"
    ).is_file()
    assert ledger["sessions"][sessions[1]["session_id"]]["status"] == "pending"
    assert ledger["sessions"][sessions[2]["session_id"]]["status"] == "pending"


def _pubmed_xml(pmids):
    articles = []
    for pmid in pmids:
        articles.append(
            f"""
            <PubmedArticle>
              <MedlineCitation><PMID>{pmid}</PMID><Article>
                <ArticleTitle>Title {pmid}</ArticleTitle>
                <Abstract><AbstractText Label="BACKGROUND">A full abstract for {pmid}.</AbstractText></Abstract>
              </Article></MedlineCitation>
              <PubmedData><ArticleIdList>
                <ArticleId IdType="pubmed">{pmid}</ArticleId>
                <ArticleId IdType="pmc">PMC{pmid}</ArticleId>
              </ArticleIdList></PubmedData>
            </PubmedArticle>
            """
        )
    return "<PubmedArticleSet>" + "".join(articles) + "</PubmedArticleSet>"


def test_pubmed_assessor_fetch_preserves_exact_20_id_batches_and_full_abstract():
    calls = []

    def fake_get(_url, *, params, timeout):
        ids = params["id"].split(",")
        calls.append(ids)
        return FakeResponse(text=_pubmed_xml(ids))

    pmids = [str(index) for index in range(1, 22)]
    records, errors = benchmark._fetch_pubmed_assessor_text(pmids, get=fake_get)

    assert benchmark.MAX_FETCH_IDS == 20
    assert [len(call) for call in calls] == [20, 1]
    assert len(records) == 21 and not errors
    assert records["1"]["abstract"] == "BACKGROUND: A full abstract for 1."


def test_mixed_pmid_and_doi_only_records_deduplicate_without_sort_errors():
    links = [
        {"link_id": "a", "pmid": "1", "doi": "10.1/a"},
        {"link_id": "b", "pmid": None, "doi": "10.1/a"},
        {"link_id": "c", "pmid": "2", "doi": None},
    ]

    papers, by_link = benchmark._paper_groups(links)

    assert papers == [("1", "10.1/a"), ("2", None)]
    assert by_link["a"] == by_link["b"] == ("1", "10.1/a")
    assert by_link["c"] == ("2", None)


def _write_minimal_run(run_dir: Path, *, n_links: int = 1) -> None:
    session_id = "brain-p1--r1"
    session_root = run_dir / "sessions" / session_id
    result_dir = session_root / "research_results"
    audit_dir = session_root / "research_audit"
    bundle_dir = session_root / "program_bundles"
    for path in (result_dir, audit_dir, bundle_dir, run_dir / "assessor_text" / "papers"):
        path.mkdir(parents=True, exist_ok=True)

    evidence = []
    evidence_ids = []
    links = []
    for index in range(1, n_links + 1):
        evidence_id = f"EV-{index:03d}"
        pmid = str(100 + index)
        paper_key = f"paper-{index}"
        paper = {
            "evidence_id": evidence_id,
            "pmid": pmid,
            "doi": None,
            "title": f"Paper {index}",
            "year": 2020,
            "study_type": "primary",
            "context_match": "direct",
            "relevance_note": "Selected for the mechanism.",
            "resolved": True,
            "registry": "pubmed",
            "retracted": False,
            "verify_error": None,
        }
        evidence.append(paper)
        evidence_ids.append(evidence_id)
        links.append(
            {
                "link_id": f"{session_id}::M1::{evidence_id}::{pmid}",
                "session_id": session_id,
                "case_id": "brain-p1",
                "program_id": "P1",
                "mechanism_index": 1,
                "mechanism": {
                    "name": "Barrier transport",
                    "summary": "G1 and R1 regulate transport.",
                    "supporting_genes": ["G1"],
                    "supporting_regulators": ["R1"],
                    "evidence_ids": evidence_ids.copy(),
                    "status": "supported",
                },
                "evidence": paper,
                "pmid": pmid,
                "doi": None,
                "paper_key": paper_key,
                "paper_path": f"papers/{paper_key}.json",
                "retrieval_status": "retrieved",
            }
        )
        (run_dir / "assessor_text" / "papers" / f"{paper_key}.json").write_text(
            json.dumps(
                {
                    "paper_key": paper_key,
                    "pmid": pmid,
                    "doi": None,
                    "pmcid": None,
                    "title": f"Paper {index}",
                    "source": "pubmed",
                    "text_type": "abstract",
                    "retrieval_status": "retrieved",
                    "content_hash": benchmark._sha256_bytes(
                        f"Assessor text {index}".encode("utf-8")
                    ),
                    "text": f"Assessor text {index}",
                    "errors": [],
                }
            ),
            encoding="utf-8",
        )

    mechanism = {
        "name": "Barrier transport",
        "summary": "G1 and R1 regulate transport.",
        "supporting_genes": ["G1"],
        "supporting_regulators": ["R1"],
        "evidence_ids": evidence_ids,
        "status": "supported",
    }
    result = {
        "program_id": "P1",
        "queries": ["G1 brain endothelium"],
        "candidate_mechanisms": [mechanism],
        "evidence": evidence,
        "contradictions": [],
        "evidence_gaps": [],
        "agent_summary": "summary",
        "meta": {"num_turns": 5, "cost_usd": 0.2, "attempts": 1},
    }
    (result_dir / "P1.json").write_text(json.dumps(result), encoding="utf-8")
    (audit_dir / "P1.audit.json").write_text(
        json.dumps(
            {
                "program_id": "P1",
                "cost_usd": 0.2,
                "num_turns": 5,
                "duration_ms": 1000,
                "tokens": {"total": 100},
                "attempts": 1,
                "status": "ok",
            }
        ),
        encoding="utf-8",
    )
    (audit_dir / "P1.raw_payload.json").write_text(json.dumps(result), encoding="utf-8")
    (audit_dir / "P1.pre_verify.json").write_text(json.dumps(result), encoding="utf-8")
    (audit_dir / "verification_summary.json").write_text(
        json.dumps({"n_programs": 1, "verification_complete": True}), encoding="utf-8"
    )
    (bundle_dir / "P1.json").write_text(
        json.dumps(
            {
                "program_id": "P1",
                "program_genes": ["G1"],
                "distinctive_genes": ["G2"],
                "perturbation_regulators": {"all": [{"gene": "R1", "log2fc": 1.0}]},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "run_plan.json").write_text(
        json.dumps(
            {
                "execution": {
                    "auth": "subscription",
                    "model": "test-model",
                    "max_turns": 30,
                    "max_budget_usd": 1.0,
                },
                "sessions": [
                    {
                        "session_id": session_id,
                        "case_id": "brain-p1",
                        "program_id": "P1",
                        "bundle": f"sessions/{session_id}/program_bundles/P1.json",
                        "result_dir": f"sessions/{session_id}/research_results",
                        "audit_dir": f"sessions/{session_id}/research_audit",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "environment.json").write_text("{}", encoding="utf-8")
    (run_dir / "assessor_text" / "index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "text_policy": "test assessor text policy",
                "max_fetch_ids": 20,
                "links": links,
                "papers": [],
            }
        ),
        encoding="utf-8",
    )


def test_materialize_text_marks_missing_text_not_assessable(tmp_path):
    run_dir = tmp_path / "run"
    result_dir = run_dir / "sessions" / "case--r1" / "research_results"
    result_dir.mkdir(parents=True)
    (result_dir / "P1.json").write_text(
        json.dumps(
            {
                "program_id": "P1",
                "candidate_mechanisms": [
                    {
                        "name": "M",
                        "summary": "",
                        "supporting_genes": [],
                        "supporting_regulators": [],
                        "evidence_ids": ["EV-001"],
                        "status": "partial",
                    }
                ],
                "evidence": [{"evidence_id": "EV-001", "doi": "10.1000/missing"}],
            }
        ),
        encoding="utf-8",
    )

    def empty_get(*_args, **_kwargs):
        return FakeResponse(payload={"resultList": {"result": []}})

    out = run_dir / "assessor_text"
    summary = benchmark.materialize_text(run_dir, out, get=empty_get)
    index = json.loads((out / "index.json").read_text())
    paper = json.loads(next((out / "papers").glob("*.json")).read_text())

    assert summary["n_not_assessable"] == 1
    assert index["links"][0]["retrieval_status"] == "not_assessable"
    assert paper["retrieval_status"] == "not_assessable"
    assert paper["content_hash"] is None and paper["text"] == ""


def _complete_adjudications(template_path: Path, *, entailment: str = "not_entailed"):
    data = json.loads(template_path.read_text())
    for review in data["reviews"]:
        review.update(
            {
                "assessor_id": f"reviewer-{review['assessor_slot']}",
                "entailment": entailment,
                "organism_match": "exact",
                "tissue_match": "exact",
                "cell_type_match": "exact",
                "condition_match": "partial",
                "paper_role": "anchor",
                "paper_role_quality": 4,
                "selection_reason_quality": 3,
                "entailed_genes": ["G1"] if entailment == "entailed" else [],
                "entailed_regulators": ["R1"] if entailment == "entailed" else [],
            }
        )
    for case in data["case_reviews"]:
        case.update({"assessor_id": "case-reviewer", "coherence": 4, "selection_quality": 3})
    return data


def test_review_packet_blinds_names_and_double_scores_twenty_percent(tmp_path):
    run_dir = tmp_path / "named-baseline"
    _write_minimal_run(run_dir, n_links=5)
    review_dir = tmp_path / "review"
    summary = benchmark.make_review_packet({"secret-name": run_dir}, review_dir, seed="seed")
    packet_text = (review_dir / "review_packet.json").read_text()
    packet = json.loads(packet_text)

    assert summary["n_links"] == 5 and summary["n_double_scored"] == 1
    assert "secret-name" not in packet_text
    assert packet["blind_variants"] == ["Variant A"]
    assert sorted(item["required_assessors"] for item in packet["review_items"]) == [1, 1, 1, 1, 2]
    assert (tmp_path / "review.blinding_key.private.json").is_file()


def test_review_packet_rejects_incomplete_paid_artifacts(tmp_path):
    run_dir = tmp_path / "run"
    _write_minimal_run(run_dir)
    (run_dir / "sessions" / "brain-p1--r1" / "research_audit" / "P1.raw_payload.json").unlink()

    with pytest.raises(benchmark.BenchmarkError, match="incomplete paid/verified artifact"):
        benchmark.make_review_packet({"baseline": run_dir}, tmp_path / "review")


def test_review_packet_binding_detects_result_from_another_run(tmp_path):
    run_dir = tmp_path / "run"
    _write_minimal_run(run_dir)
    review_dir = tmp_path / "review"
    benchmark.make_review_packet({"baseline": run_dir}, review_dir)
    sessions = benchmark._discover_results(run_dir)
    text_index = json.loads((run_dir / "assessor_text" / "index.json").read_text())

    result_path = run_dir / "sessions" / "brain-p1--r1" / "research_results" / "P1.json"
    result = json.loads(result_path.read_text())
    result["agent_summary"] = "tampered after review"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(benchmark.BenchmarkError, match="result_sha256 is from another run"):
        benchmark._validate_review_binding(
            run_dir,
            sessions,
            text_index,
            review_dir / "review_packet.json",
        )


def test_missing_assessor_text_is_forced_to_not_assessable():
    item = {
        "review_id": "RL-1",
        "assessor_text": {"retrieval_status": "not_assessable"},
        "supporting_genes": ["G1"],
        "supporting_regulators": [],
    }
    invalid = {
        "review_id": "RL-1",
        "assessor_id": "reviewer",
        "entailment": "entailed",
        "organism_match": "exact",
        "tissue_match": "exact",
        "cell_type_match": "exact",
        "condition_match": "exact",
        "paper_role": "anchor",
        "paper_role_quality": 5,
        "selection_reason_quality": 5,
        "entailed_genes": ["G1"],
        "entailed_regulators": [],
    }

    with pytest.raises(benchmark.BenchmarkError, match="must be scored not_assessable"):
        benchmark._validate_review(invalid, item)


def test_score_uses_human_entailment_not_identifier_resolution(tmp_path):
    run_dir = tmp_path / "run"
    _write_minimal_run(run_dir, n_links=5)
    review_dir = tmp_path / "review"
    benchmark.make_review_packet({"baseline": run_dir}, review_dir, seed="seed")
    completed = _complete_adjudications(review_dir / "adjudications.template.json")
    adjudications = tmp_path / "completed.json"
    adjudications.write_text(json.dumps(completed), encoding="utf-8")

    scored = benchmark.score(
        review_dir / "review_packet.json", adjudications, tmp_path / "metrics.json"
    )
    metrics = scored["variants"]["Variant A"]

    # Every identifier resolves, but reviewers found no entailment. Resolution is not support.
    assert metrics["aggregate"]["identifier_status_counts"]["resolved"] == 5
    assert metrics["aggregate"]["entailment_rate"] == 0.0
    assert metrics["aggregate"]["entailed_core_gene_coverage"] == 0.0
    assert metrics["aggregate"]["telemetry_complete"] is True


def test_score_requires_adjudication_for_double_score_disagreement(tmp_path):
    run_dir = tmp_path / "run"
    _write_minimal_run(run_dir, n_links=5)
    review_dir = tmp_path / "review"
    benchmark.make_review_packet({"baseline": run_dir}, review_dir, seed="seed")
    completed = _complete_adjudications(review_dir / "adjudications.template.json")
    grouped = {}
    for review in completed["reviews"]:
        grouped.setdefault(review["review_id"], []).append(review)
    double_rows = next(rows for rows in grouped.values() if len(rows) == 2)
    double_rows[1]["entailment"] = "entailed"
    double_rows[1]["entailed_genes"] = ["G1"]
    double_rows[1]["entailed_regulators"] = ["R1"]
    path = tmp_path / "disagreement.json"
    path.write_text(json.dumps(completed), encoding="utf-8")

    with pytest.raises(benchmark.BenchmarkError, match="requires adjudication"):
        benchmark.score(review_dir / "review_packet.json", path, tmp_path / "metrics.json")


def test_compare_computes_deltas_and_rejects_contract_drift(tmp_path):
    compatibility = {
        "cohort_fingerprint": "same",
        "pairing_keys": ["case--r1"],
        "execution": {"model": "m", "auth": "subscription"},
        "assessor_text_policy": "same",
    }
    baseline = {
        "schema_version": 1,
        "variants": {
            "baseline": {"compatibility": compatibility, "aggregate": {"entailment_rate": 0.5}}
        },
    }
    candidate = {
        "schema_version": 1,
        "variants": {
            "candidate": {"compatibility": compatibility, "aggregate": {"entailment_rate": 0.7}}
        },
    }
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    result = benchmark.compare(baseline_path, candidate_path, tmp_path / "compare.json")
    assert result["deltas"]["entailment_rate"]["absolute"] == pytest.approx(0.2)

    candidate["variants"]["candidate"]["compatibility"] = {
        **compatibility,
        "cohort_fingerprint": "changed",
    }
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    with pytest.raises(benchmark.BenchmarkError, match="contract drift"):
        benchmark.compare(baseline_path, candidate_path, tmp_path / "bad.json")
