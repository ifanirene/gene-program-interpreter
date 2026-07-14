"""The verifier's tri-state, and the mislabeling that made a packaging bug look like a bad payload.

``verify_dois`` returns ``{"ok": True | False | None}`` and the three values are NOT
interchangeable:

  * ``True``  -> the paper exists.
  * ``False`` -> it authoritatively does NOT exist. The citation is dropped and the mechanism
                 is marked ``unsupported`` ("likely fabricated").
  * ``None``  -> we COULD NOT CHECK (network error, 5xx, rate limit). The citation is KEPT and
                 the mechanism is marked ``partial`` ("couldn't check").

Collapse ``None`` into ``False`` and GPI silently deletes real papers because a request timed
out. Collapse ``False`` into ``None`` and it presents fabricated ones as merely-unverified.
The asymmetry is the product: a network blip must never be able to refute a citation.

The load-bearing consequence is that **CrossRef can never refute a DOI** — its lookup returns
``None`` for a 404 and for a dropped connection alike, so it cannot distinguish them. Only
doi.org's own 404 is authoritative. These tests pin that.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from research import _crossref

DOI = "10.1234/real-paper"


def _fake(get_json, head_status):
    return (
        patch.object(_crossref, "_get_json", get_json),
        patch.object(_crossref, "_head_status", head_status),
    )


def test_resolvable_doi_is_true() -> None:
    with patch.object(_crossref, "_get_json", lambda *a, **k: {"message": {"title": ["X"]}}), \
         patch.object(_crossref, "_head_status", lambda *a, **k: 302):
        assert _crossref.verify_dois([DOI])[DOI]["ok"] is True


def test_a_404_from_doi_org_is_an_authoritative_false() -> None:
    """The only way a citation earns ``False``: the resolver of record says it does not exist."""
    with patch.object(_crossref, "_get_json", lambda *a, **k: None), \
         patch.object(_crossref, "_head_status", lambda *a, **k: 404):
        assert _crossref.verify_dois([DOI])[DOI]["ok"] is False


def test_a_network_failure_is_none_never_false() -> None:
    """THE trap. An unreachable network must not be able to delete a real paper.

    Mocked at the ``requests`` layer, not at the helpers, so the real ``_get_json`` /
    ``_head_status`` error handling is what is under test — that is the code that actually runs
    when the network drops.
    """
    def boom(*_a, **_k):
        raise ConnectionError("network down")

    with patch.object(_crossref.requests, "get", boom), \
         patch.object(_crossref.requests, "head", boom):
        assert _crossref.verify_dois([DOI])[DOI]["ok"] is None


def test_a_timeout_is_none_never_false() -> None:
    """Same asymmetry, via the other common failure: a slow server is not a fake paper."""
    import requests as _requests

    def slow(*_a, **_k):
        raise _requests.exceptions.Timeout("read timed out")

    with patch.object(_crossref.requests, "get", slow), \
         patch.object(_crossref.requests, "head", slow):
        assert _crossref.verify_dois([DOI])[DOI]["ok"] is None


@pytest.mark.parametrize("status", [429, 500, 502, 503, None])
def test_rate_limits_and_server_errors_are_none(status: int | None) -> None:
    """A 5xx or a survived-retry 429 means 'we could not check', not 'it is fake'."""
    with patch.object(_crossref, "_get_json", lambda *a, **k: None), \
         patch.object(_crossref, "_head_status", lambda *a, **k: status):
        assert _crossref.verify_dois([DOI])[DOI]["ok"] is None


def test_crossref_alone_cannot_refute_a_doi() -> None:
    """CrossRef 404s on any DOI it does not mint (arXiv, DataCite, Zenodo...). If a CrossRef
    miss were treated as refutation, every non-CrossRef DOI would be reported as fabricated."""
    with patch.object(_crossref, "_get_json", lambda *a, **k: None), \
         patch.object(_crossref, "_head_status", lambda *a, **k: 302):
        result = _crossref.verify_dois([DOI])[DOI]
    assert result["ok"] is True, "a DOI absent from CrossRef but live at doi.org is REAL"


def test_crossref_down_still_catches_a_fabricated_doi() -> None:
    """The mirror case: losing CrossRef must not let a fake DOI through."""
    with patch.object(_crossref, "_get_json", lambda *a, **k: None), \
         patch.object(_crossref, "_head_status", lambda *a, **k: 404):
        assert _crossref.verify_dois([DOI])[DOI]["ok"] is False


def test_verification_is_reported_as_incomplete_when_nothing_could_be_checked(tmp_path) -> None:
    """'Did verification actually run?' must be machine-readable.

    In the incident it did not run at all, and nothing downstream could tell — the report
    rendered unverified PMIDs exactly as if they had passed. ``verification_complete`` is that
    missing signal.
    """
    import json

    from research.schema import Evidence, ResearchResult
    from research.verify import _write_verification_summary

    unchecked = ResearchResult(
        program_id="9",
        evidence=[
            Evidence(evidence_id="EV-001", pmid="12345678", resolved=None),  # could not check
            Evidence(evidence_id="EV-002", doi=DOI, resolved=True),          # verified
        ],
    )
    path = _write_verification_summary(tmp_path, [unchecked], skipped=[])
    summary = json.loads(path.read_text())

    assert summary["verification_complete"] is False, "one unchecked citation ⇒ not complete"
    assert summary["n_unverified"] == 1
    assert summary["n_verified"] == 1
    assert summary["n_refuted"] == 0

    # ...and a run where everything resolved is reported as complete.
    clean = ResearchResult(
        program_id="9",
        evidence=[Evidence(evidence_id="EV-001", doi=DOI, resolved=True)],
    )
    clean_summary = json.loads(_write_verification_summary(tmp_path, [clean], skipped=[]).read_text())
    assert clean_summary["verification_complete"] is True


def test_an_import_error_is_not_reported_as_a_schema_error() -> None:
    """The mislabeling that made this expensive.

    ``research_parallel`` used to import the verifier *inside* a try/except that reported every
    exception as ``"submit_result payload failed schema validation"``. So a packaging bug wore a
    bad-agent-payload costume: a valid, already-paid-for research result was discarded, and the
    loop RETRIED — which cannot fix an ImportError and doubles the bill.

    The import now sits at module scope, so a broken install fails at process start, before any
    spend. This test pins that it is not re-introduced into a try block.
    """
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "research" / "research_parallel.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))

    guarded: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.ImportFrom) and (inner.module or "").startswith("research.verify"):
                guarded.append(inner.module or "?")
    assert not guarded, (
        "research.verify is imported inside a try/except again. An infrastructure failure there "
        "gets reported as an agent schema error and triggers a paid retry that cannot help."
    )
