"""
research.verify — deterministic evidence verifier (executor 4).

Guardrail #1 (ARCHITECTURE.md): this is deterministic code that *validates*
identifiers — it does NOT research. It takes the per-program ``ResearchResult``
artifacts written by the research subagents and annotates them **in place**:

  * resolves every ``Evidence`` DOI (CrossRef + doi.org, keyless) and/or PMID
    (NCBI E-utilities ``esummary``), reconciling PMID<->DOI when one is missing;
  * flags retractions (CrossRef ``update-to`` / subtype);
  * recomputes each ``CandidateMechanism.status`` from its evidence resolvability
    ('supported' if >=1 resolvable non-retracted paper, 'partial' if only
    unverified, else 'unsupported') — never invents support;
  * dedups evidence across programs (same DOI/PMID -> one canonical record,
    keeping per-program ``evidence_id`` references intact);
  * writes each annotated ``ResearchResult`` back to its file (same schema), and
    keeps a raw pre-verify copy under a sibling ``research_audit/`` dir.

Reused building blocks:
  * ``verify_dois`` from ``research/_crossref.py`` — the keyless CrossRef +
    doi.org HEAD verifier, vendored INTO this package (it used to be loaded by
    path out of ``literature-review/kernel.py``, a hyphenated directory that can
    never be a package and is therefore absent from the built wheel — so every
    plugin install raised on import). It returns per DOI ``{ok: True|False|None,
    title?, authors?, year?, journal?, retracted?, registry?}``, where ``ok=None``
    means COULD NOT CHECK (the citation is kept) and ``ok=False`` means
    authoritatively refuted (the citation is dropped).
  * A small keyless-or-keyed PMID resolver (``resolve_pmids``) implemented here
    against ``esummary.fcgi?db=pubmed`` — NcbiClient has no PMID-metadata method.

CLI:  ``python -m research.verify --results-dir research_results/ [--audit-dir research_audit/]``
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import requests

from research._crossref import env_value, verify_dois
from research.schema import (
    AgentPaper,
    AgentResearchResult,
    CandidateMechanism,
    Evidence,
    ResearchResult,
)

# RESERVED (see the reserved section at the end of this file): kept importable for the
# preserved, unwired claim-vs-paper entailment logic. Not used by the active pipeline.
from research.schema import Citation, Claim  # noqa: F401

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PMID resolver (NCBI E-utilities esummary) — keyless or keyed
# ---------------------------------------------------------------------------
EUTILS_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_RATE_NO_KEY = 0.34   # ~3 req/s, polite
_RATE_WITH_KEY = 0.11  # ~9 req/s


def _ncbi_api_key() -> Optional[str]:
    """NCBI key from the environment, falling back to a ``.env`` in the CURRENT WORKING
    DIRECTORY — where the user runs ``gpi``. (A repo-relative ``.env`` lookup is meaningless
    once this package is installed as a wheel: ``__file__`` then points into site-packages.)
    Never printed or logged."""
    return env_value("NCBI_API_KEY")


def _parse_year(s) -> Optional[int]:
    if not s:
        return None
    m = re.search(r"(\d{4})", str(s))
    return int(m.group(1)) if m else None


def resolve_pmids(pmids: list[str]) -> dict[str, dict]:
    """Resolve PubMed IDs via ``esummary.fcgi?db=pubmed&retmode=json``.

    Returns per PMID ``{resolved, title?, year?, doi?, error?}`` where
      resolved=True  -> the UID has a real PubMed record (title present);
      resolved=False -> UID absent/errored/empty (fabricated or deleted);
      resolved=None  -> could not be verified (network/HTTP/JSON) — do NOT flag.

    ``doi`` is extracted from the record's ``articleids`` (``idtype == "doi"``)
    so the caller can reconcile a PMID-only evidence back to its DOI.
    """
    ids = sorted({str(p).strip() for p in pmids if p and str(p).strip()})
    out: dict[str, dict] = {}
    if not ids:
        return out

    key = _ncbi_api_key()
    params = {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
    if key:
        params["api_key"] = key
    time.sleep(_RATE_WITH_KEY if key else _RATE_NO_KEY)

    try:
        resp = requests.get(EUTILS_ESUMMARY, params=params, timeout=60)
    except requests.RequestException as e:
        for p in ids:
            out[p] = {"resolved": None, "error": f"network: {e}"}
        return out
    if resp.status_code != 200:
        for p in ids:
            out[p] = {"resolved": None, "error": f"http {resp.status_code}"}
        return out
    try:
        data = resp.json()
    except ValueError as e:
        for p in ids:
            out[p] = {"resolved": None, "error": f"json: {e}"}
        return out

    result = data.get("result", {}) if isinstance(data, dict) else {}
    for p in ids:
        item = result.get(p)
        # Missing UID (absent from result) or an explicit per-record error ->
        # the PMID does not resolve.
        if not item or item.get("error"):
            out[p] = {
                "resolved": False,
                "error": (item or {}).get("error", "uid absent from esummary result"),
            }
            continue
        title = item.get("title") or None
        year = _parse_year(item.get("sortpubdate") or item.get("pubdate"))
        doi = None
        for aid in item.get("articleids", []) or []:
            if aid.get("idtype") == "doi" and aid.get("value"):
                doi = str(aid["value"]).strip()
                break
        if not title:
            # A live PubMed record always carries a title; empty => not a real record.
            out[p] = {"resolved": False, "error": "empty record (no title)"}
            continue
        rec = {"resolved": True, "title": title, "year": year}
        if doi:
            rec["doi"] = doi
        out[p] = rec
    return out


# ---------------------------------------------------------------------------
# Flat (agent) -> canonical: build the deduplicated Evidence pool + assign ids
# ---------------------------------------------------------------------------
def _norm_doi(doi: Optional[str]) -> Optional[str]:
    """Canonicalize a DOI for keying: strip a doi.org/ prefix, lower-case."""
    if not doi:
        return None
    d = str(doi).strip()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d, flags=re.I)
    d = re.sub(r"^doi:\s*", "", d, flags=re.I)
    return d.strip().lower() or None


MAX_MECHANISMS = 3  # hard cap enforced during normalization (schema documents 1-3)


def _derive_mechanism_status(mech: CandidateMechanism, ev_by_id: dict[str, Evidence]) -> str:
    """Per-mechanism support, from its linked evidence's resolvability. Used for BOTH the
    provisional status in ``normalize_agent_result`` (evidence unresolved -> 'partial') and the
    post-resolution recompute in verify — identical semantics either way:

      * 'supported'   -> >=1 linked evidence resolves (``resolved is True``) and is not retracted,
      * 'partial'     -> has linked evidence but none resolved yet (some ``resolved is None``),
      * 'unsupported' -> no linked evidence, or all resolved False / retracted.
    """
    present = [ev for eid in mech.evidence_ids if (ev := ev_by_id.get(eid)) is not None]
    if not present:
        return "unsupported"
    if any(ev.resolved is True and ev.retracted is not True for ev in present):
        return "supported"
    if any(ev.resolved is None for ev in present):
        return "partial"
    return "unsupported"


def normalize_agent_result(agent: AgentResearchResult) -> ResearchResult:
    """Turn a flat ``AgentResearchResult`` (papers attached per mechanism) into a canonical
    ``ResearchResult``: keep only the first ``MAX_MECHANISMS`` mechanisms, dedup their papers
    into one ``Evidence`` pool (by DOI/PMID), assign ``EV-NNN`` ids, and reference those ids
    from each mechanism. Papers without any identifier are dropped (they cannot be verified). A
    provisional per-mechanism ``status`` is set here and finalized by the resolution pass.
    """
    pool: list[Evidence] = []
    id_by_key: dict[tuple, str] = {}
    alias: dict[str, str] = {}  # a merged-away evidence id -> the surviving canonical id

    def _canon(eid: str) -> str:
        while eid in alias:
            eid = alias[eid]
        return eid

    def _find(eid: str) -> Evidence:
        return next(e for e in pool if e.evidence_id == eid)

    def _keys(p: AgentPaper) -> list[tuple]:
        ks: list[tuple] = []
        nd = _norm_doi(p.doi)
        if nd:
            ks.append(("doi", nd))
        if p.pmid and str(p.pmid).strip():
            ks.append(("pmid", str(p.pmid).strip()))
        return ks

    def _absorb_paper(ev: Evidence, p: AgentPaper) -> None:  # first-seen wins
        if not ev.pmid and p.pmid:
            ev.pmid = str(p.pmid).strip()
        if not ev.doi and p.doi:
            ev.doi = str(p.doi).strip()
        ev.title = ev.title or p.title
        ev.year = ev.year or p.year
        ev.study_type = ev.study_type or p.study_type
        ev.context_match = ev.context_match or p.context_match
        ev.relevance_note = ev.relevance_note or p.note

    def _absorb_evidence(ev: Evidence, other: Evidence) -> None:
        if not ev.pmid and other.pmid:
            ev.pmid = other.pmid
        if not ev.doi and other.doi:
            ev.doi = other.doi
        ev.title = ev.title or other.title
        ev.year = ev.year or other.year
        ev.study_type = ev.study_type or other.study_type
        ev.context_match = ev.context_match or other.context_match
        ev.relevance_note = ev.relevance_note or other.relevance_note

    def _intern(p: AgentPaper) -> Optional[str]:
        ks = _keys(p)
        if not ks:
            return None  # no identifier -> cannot verify -> drop
        # Every distinct existing record that any of this paper's ids already points at. A paper
        # carrying BOTH a pmid and a doi that were first interned on two SEPARATE records unifies
        # them here (order-independent) — so identical inputs always yield the same pool size.
        existing: list[str] = []
        for k in ks:
            if k in id_by_key:
                c = _canon(id_by_key[k])
                if c not in existing:
                    existing.append(c)
        if not existing:
            eid = f"EV-{len(pool) + 1:03d}"
            pool.append(
                Evidence(
                    evidence_id=eid,
                    pmid=(str(p.pmid).strip() if p.pmid else None),
                    doi=(str(p.doi).strip() if p.doi else None),
                    title=p.title,
                    year=p.year,
                    study_type=p.study_type,
                    context_match=p.context_match,
                    relevance_note=p.note,
                )
            )
        else:
            eid = min(existing, key=lambda x: int(x.split("-")[1]))  # lowest-numbered = canonical
            ev = _find(eid)
            _absorb_paper(ev, p)
            for other in existing:
                if other != eid:
                    _absorb_evidence(ev, _find(other))
                    alias[other] = eid  # union the co-cited record into the canonical one
        for k in ks:
            id_by_key[k] = eid
        return eid

    def _ids(papers: list[AgentPaper]) -> list[str]:
        seen: list[str] = []
        for p in papers:
            eid = _intern(p)
            if eid and eid not in seen:
                seen.append(eid)
        return seen

    # Hard 3-cap: truncate FIRST, then build the pool only from the kept mechanisms
    # (so no orphan evidence from dropped 4th+ mechanisms enters the pool).
    kept = agent.candidate_mechanisms[:MAX_MECHANISMS]
    mechanisms = [
        CandidateMechanism(
            name=m.name,
            summary=m.summary,
            supporting_genes=m.supporting_genes,
            supporting_regulators=m.supporting_regulators,
            evidence_ids=_ids(m.papers),
        )
        for m in kept
    ]

    # Collapse any records unified mid-build: drop the aliased pool entries and remap every
    # mechanism's evidence_ids onto their canonical id (order-preserving, deduped).
    if alias:
        pool[:] = [e for e in pool if _canon(e.evidence_id) == e.evidence_id]
        for mech in mechanisms:
            remapped: list[str] = []
            for eid in mech.evidence_ids:
                c = _canon(eid)
                if c not in remapped:
                    remapped.append(c)
            mech.evidence_ids = remapped

    ev_by_id = {e.evidence_id: e for e in pool}
    for mech in mechanisms:
        mech.status = _derive_mechanism_status(mech, ev_by_id)  # provisional (pre-resolution)

    meta: dict = {"normalized_from_agent": True}
    if len(agent.candidate_mechanisms) > MAX_MECHANISMS:
        meta["mechanisms_truncated"] = len(agent.candidate_mechanisms) - MAX_MECHANISMS
    return ResearchResult(
        program_id=agent.program_id,
        queries=agent.queries,
        candidate_mechanisms=mechanisms,
        evidence=pool,
        contradictions=agent.contradictions,
        evidence_gaps=agent.evidence_gaps,
        agent_summary=agent.agent_summary,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Per-evidence resolution (network) — annotates Evidence in place
# ---------------------------------------------------------------------------
def _resolve_evidence(rr: ResearchResult) -> None:
    """Resolve every ``Evidence`` DOI/PMID and annotate resolved/registry/
    retracted/verify_error in place; reconcile a missing DOI from the PMID.

    Every DOI is passed through ``_norm_doi`` before it reaches ``verify_dois`` and every lookup
    uses the same key, so producer and consumer agree (``verify_dois`` keys by the lowercased,
    stripped DOI). Normalizing on the way IN also means an agent that submits
    ``https://doi.org/10.x/y`` gets the bare DOI verified rather than a mangled URL 404-ing and
    the real paper being discarded as fabricated."""
    dois = sorted({d for e in rr.evidence if (d := _norm_doi(e.doi))})
    pmids = sorted({e.pmid.strip() for e in rr.evidence if e.pmid and e.pmid.strip()})

    doi_res = verify_dois(dois) if dois else {}
    pmid_res = resolve_pmids(pmids) if pmids else {}

    # Reconcile PMID -> DOI, then verify any newly-surfaced DOIs so PMID-only
    # evidence still gets a registry / retraction status.
    recon: set[str] = set()
    for e in rr.evidence:
        if e.doi:
            continue
        if e.pmid:
            pr = pmid_res.get(e.pmid.strip())
            d = _norm_doi(pr.get("doi")) if pr else None
            if d and d not in doi_res:
                recon.add(d)
    if recon:
        doi_res.update(verify_dois(sorted(recon)))

    for e in rr.evidence:
        doi = e.doi.strip() if e.doi else None
        pmid = e.pmid.strip() if e.pmid else None

        # Fill a missing DOI from the PMID record (reconciliation).
        if not doi and pmid:
            pr = pmid_res.get(pmid)
            if pr and pr.get("doi"):
                doi = pr["doi"]
                e.doi = doi

        outcomes: list[Optional[bool]] = []
        errs: list[str] = []
        registry: Optional[str] = None
        retracted: Optional[bool] = None
        title: Optional[str] = None
        year: Optional[int] = None

        if doi:
            dr = doi_res.get(_norm_doi(doi))  # same normalization used to build the batch
            if dr is not None:
                ok = dr.get("ok")
                # ok is the TRI-STATE (True/False/None) and rides through untouched: None
                # ("could not check") must never be collapsed into False ("refuted").
                outcomes.append(ok)
                if ok:
                    registry = dr.get("registry")
                    retracted = dr.get("retracted")
                    title = title or dr.get("title")
                    year = year or dr.get("year")
                if dr.get("error"):
                    errs.append(f"doi:{dr['error']}")

        if pmid:
            pr = pmid_res.get(pmid)
            if pr is not None:
                r = pr.get("resolved")
                outcomes.append(r)
                if r:
                    title = title or pr.get("title")
                    year = year or pr.get("year")
                    if registry is None:
                        registry = "pubmed"
                if pr.get("error"):
                    errs.append(f"pmid:{pr['error']}")

        # resolved: True if either id resolves; False if a provided id does not
        # resolve (and none resolves); None if only-unverified / no identifier.
        if any(o is True for o in outcomes):
            e.resolved = True
        elif any(o is False for o in outcomes):
            e.resolved = False
        elif outcomes:
            e.resolved = None
        else:
            e.resolved = None
            errs.append("no identifier provided")

        e.registry = registry
        e.retracted = retracted
        if title and not e.title:
            e.title = title
        if year and not e.year:
            e.year = year
        e.verify_error = "; ".join(errs) if errs else None


# ---------------------------------------------------------------------------
# Cross-program evidence dedup (union-find over shared DOI/PMID)
# ---------------------------------------------------------------------------
def _canonicalize_across(rrs: list[ResearchResult]) -> dict:
    """Merge evidence records that share a DOI or PMID into one canonical set of
    verification fields, written back onto every member. ``evidence_id`` refs are
    never touched, so per-program claim references stay intact."""
    records: list[Evidence] = [e for rr in rrs for e in rr.evidence]
    n = len(records)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # deterministic: lower index is root

    key_to_idx: dict[tuple, int] = {}
    for i, ev in enumerate(records):
        keys = []
        if ev.doi and ev.doi.strip():
            keys.append(("doi", ev.doi.strip().lower()))
        if ev.pmid and ev.pmid.strip():
            keys.append(("pmid", ev.pmid.strip()))
        for k in keys:
            if k in key_to_idx:
                union(key_to_idx[k], i)
            else:
                key_to_idx[k] = i

    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)

    for idxs in comps.values():
        members = [records[i] for i in idxs]
        pmids = sorted({m.pmid.strip() for m in members if m.pmid and m.pmid.strip()})
        dois = sorted(
            {m.doi.strip() for m in members if m.doi and m.doi.strip()},
            key=str.lower,
        )
        pmid = pmids[0] if pmids else None
        doi = dois[0] if dois else None
        if any(m.resolved is True for m in members):
            resolved: Optional[bool] = True
        elif any(m.resolved is False for m in members):
            resolved = False
        else:
            resolved = None
        if any(m.retracted is True for m in members):
            retracted: Optional[bool] = True
        elif any(m.retracted is False for m in members):
            retracted = False
        else:
            retracted = None
        registry = None
        for pref in ("crossref", "pubmed", "non-crossref"):
            if any(m.registry == pref for m in members):
                registry = pref
                break
        if registry is None:
            registry = next((m.registry for m in members if m.registry), None)
        title = next((m.title for m in members if m.title), None)
        year = next((m.year for m in members if m.year), None)

        for m in members:
            if pmid and not (m.pmid and m.pmid.strip()):
                m.pmid = pmid
            if doi and not (m.doi and m.doi.strip()):
                m.doi = doi
            m.resolved = resolved
            m.retracted = retracted
            if registry:
                m.registry = registry
            if title and not m.title:
                m.title = title
            if year and not m.year:
                m.year = year

    return {
        "n_evidence_total": n,
        "n_canonical_evidence": len(comps),
        "n_duplicates_merged": n - len(comps),
    }


# ---------------------------------------------------------------------------
# Per-mechanism status recompute + meta summary (pure; assumes evidence annotated)
# ---------------------------------------------------------------------------
def _apply_mechanism_status_and_meta(rr: ResearchResult) -> None:
    """Recompute each ``CandidateMechanism.status`` from its (now-resolved) evidence and write
    the verify meta summary. Pure: assumes ``_resolve_evidence`` already annotated the pool."""
    ev_by_id = rr.evidence_by_id()
    notes: list[str] = []

    # audit: duplicate evidence_ids within this program
    seen: set[str] = set()
    for e in rr.evidence:
        if e.evidence_id in seen:
            notes.append(f"duplicate evidence_id within program: {e.evidence_id!r}")
        seen.add(e.evidence_id)

    n_unsupported = 0
    for i, mech in enumerate(rr.candidate_mechanisms):
        missing = [eid for eid in mech.evidence_ids if eid not in ev_by_id]
        if missing:
            notes.append(f"mechanism[{i}] references unknown evidence_id(s): {missing}")
        if any(
            (ev := ev_by_id.get(eid)) is not None and ev.retracted is True
            for eid in mech.evidence_ids
        ):
            notes.append(f"mechanism[{i}] cites retracted evidence")
        mech.status = _derive_mechanism_status(mech, ev_by_id)
        if mech.status == "unsupported":
            n_unsupported += 1

    verify_meta = {
        "n_evidence": len(rr.evidence),
        "n_resolved": sum(1 for e in rr.evidence if e.resolved is True),
        "n_unresolved": sum(1 for e in rr.evidence if e.resolved is False),
        "n_retracted": sum(1 for e in rr.evidence if e.retracted is True),
        "n_mechanisms": len(rr.candidate_mechanisms),
        "n_mechanisms_unsupported": n_unsupported,
        "mechanism_status": [m.status for m in rr.candidate_mechanisms],
    }
    if notes:
        verify_meta["notes"] = notes
    rr.meta["verify"] = verify_meta


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def verify_research_result(result) -> ResearchResult:
    """Verify one result: normalize the flat agent output if needed, resolve every
    evidence identifier, reconcile PMID<->DOI, flag retractions, recompute each mechanism's
    status from resolution, and populate ``meta['verify']``. Accepts either a flat
    ``AgentResearchResult`` or a canonical ``ResearchResult``; returns the canonical one."""
    if isinstance(result, AgentResearchResult):
        rr = normalize_agent_result(result)
    elif isinstance(result, ResearchResult):
        rr = result
    else:
        raise TypeError(f"expected ResearchResult or AgentResearchResult, got {type(result)!r}")
    _resolve_evidence(rr)
    _apply_mechanism_status_and_meta(rr)
    return rr


def _load_result(raw: str) -> ResearchResult:
    """Load a result file as either the flat ``AgentResearchResult`` (agent output) or the
    canonical ``ResearchResult``, returning the canonical form. A flat file has no top-level
    ``evidence`` array; its mechanisms carry inline ``papers`` (rather than ``evidence_ids``)."""
    data = json.loads(raw)
    mechs = data.get("candidate_mechanisms") or []
    is_flat = "evidence" not in data or any(
        isinstance(m, dict) and "papers" in m for m in mechs
    )
    if is_flat:
        return normalize_agent_result(AgentResearchResult.model_validate(data))
    return ResearchResult.model_validate(data)


def _write_verification_summary(
    audit_dir: Path,
    rrs: list[ResearchResult],
    skipped: list[dict],
) -> Path:
    """Write ``{audit_dir}/verification_summary.json`` — the machine-readable answer to "did
    verification actually run?".

    ``verification_complete`` is True only when EVERY citation got a non-``None`` verdict (no
    "could not check") and no result file had to be skipped. A ``None`` verdict means the DOI/PMID
    could not be reached, so the citation is kept but unproven — a run carrying any of those has
    not been fully verified, and nothing downstream could tell until now.

    It goes in the AUDIT dir, not the results dir: ``verify_directory`` and
    ``gpi.research_evidence_adapter`` both glob ``research_results/*.json`` expecting one
    ``ResearchResult`` per file, so a summary file there would be parsed as a program.
    """
    evidence = [e for rr in rrs for e in rr.evidence]
    n_unverified = sum(1 for e in evidence if e.resolved is None)
    summary = {
        "verification_complete": n_unverified == 0 and not skipped,
        "n_citations": len(evidence),
        "n_verified": sum(1 for e in evidence if e.resolved is True),
        "n_unverified": n_unverified,           # resolved is None -> could not check (kept)
        "n_refuted": sum(1 for e in evidence if e.resolved is False),  # dropped downstream
        "n_retracted": sum(1 for e in evidence if e.retracted is True),
        "n_programs": len(rrs),
        "n_files_skipped": len(skipped),
        "files_skipped": skipped,
    }
    path = audit_dir / "verification_summary.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info(
        "verify: %d/%d citation(s) verified across %d program(s) (complete=%s) -> %s",
        summary["n_verified"], summary["n_citations"], summary["n_programs"],
        summary["verification_complete"], path,
    )
    return path


def verify_directory(directory: Path, audit_dir: Optional[Path] = None) -> dict:
    """Verify every ``*.json`` ``ResearchResult`` in ``directory`` in place.

    Writes a raw pre-verify copy to ``{audit_dir}/{program_id}.pre_verify.json``
    (default sibling ``research_audit/``) BEFORE overwriting, dedups evidence
    across programs, writes ``{audit_dir}/verification_summary.json``, and returns
    an audit summary dict.

    Loading is per-file: an unreadable / off-schema result is logged and SKIPPED rather than
    sinking the other programs' (already paid-for) research. It raises only when files were
    present and NONE of them loaded.

    This step is deliberately NOT degradable — "never emit an unverified citation" is a feature —
    so resolution failures still ride through as ``resolved=None`` (kept, mechanism ``partial``)
    or ``resolved=False`` (dropped, mechanism ``unsupported``); neither is silently upgraded.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise NotADirectoryError(f"results dir not found: {directory}")
    if audit_dir is None:
        audit_dir = directory.parent / "research_audit"
    audit_dir = Path(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(directory.glob("*.json"))
    rrs: list[ResearchResult] = []
    file_of: dict[int, Path] = {}
    skipped: list[dict] = []
    for f in files:
        try:
            raw = f.read_text()
            rr = _load_result(raw)  # accepts flat agent output OR canonical form
        except Exception as e:  # noqa: BLE001 - one bad file must not sink the good programs
            logger.error("verify: skipping %s — not a valid research result: %s", f, e)
            skipped.append({"file": str(f), "error": str(e)})
            continue
        rrs.append(rr)
        file_of[id(rr)] = f
        # raw record kept separate from summaries (spec P1): pre-verify snapshot
        (audit_dir / f"{rr.program_id}.pre_verify.json").write_text(raw)

    if files and not rrs:
        raise ValueError(
            f"no valid research result loaded from {directory}: all {len(files)} file(s) failed "
            f"({'; '.join(s['error'] for s in skipped)})"
        )

    # 1) resolve identifiers within each program (batched network calls)
    for rr in rrs:
        _resolve_evidence(rr)
    # 2) dedup evidence across programs (unify canonical verification fields)
    dedup = _canonicalize_across(rrs)
    # 3) recompute per-mechanism status + meta after cross-program merge
    for rr in rrs:
        _apply_mechanism_status_and_meta(rr)

    # 4) write annotated ResearchResults back in place
    programs: dict[str, dict] = {}
    for rr in rrs:
        file_of[id(rr)].write_text(rr.model_dump_json(indent=2))
        programs[rr.program_id] = rr.meta["verify"]

    # 5) machine-readable "did verification actually run?" record
    summary_path = _write_verification_summary(audit_dir, rrs, skipped)

    return {
        "directory": str(directory),
        "audit_dir": str(audit_dir),
        "verification_summary": str(summary_path),
        "n_programs": len(rrs),
        "n_files_skipped": len(skipped),
        **dedup,
        "n_mechanisms_unsupported_total": sum(
            p["n_mechanisms_unsupported"] for p in programs.values()
        ),
        "n_retracted_total": sum(p["n_retracted"] for p in programs.values()),
        "programs": programs,
    }


# ---------------------------------------------------------------------------
# RESERVED — claim-vs-paper entailment verification (NOT WIRED INTO THE ACTIVE PIPELINE)
#
# The active pipeline attaches papers directly to mechanisms and derives a per-mechanism
# status from evidence RESOLUTION only (see _apply_mechanism_status_and_meta). The function
# below is the retired per-claim variant, preserved verbatim for a FUTURE step that actually
# checks claim-vs-paper ENTAILMENT (an adjudicator that reads the paper and confirms the
# claim). It operates on the reserved research.schema.Claim layer (ResearchResult no longer
# carries `claims`), so it is dead code today — do NOT call it. See
# docs/FUTURE_claim_verification.md.
# ---------------------------------------------------------------------------
def _reserved_apply_claims_and_meta(rr) -> None:  # pragma: no cover - reserved, unused
    ev_by_id = rr.evidence_by_id()
    notes: list[str] = []
    n_downgraded = 0
    seen: set[str] = set()
    for e in rr.evidence:
        if e.evidence_id in seen:
            notes.append(f"duplicate evidence_id within program: {e.evidence_id!r}")
        seen.add(e.evidence_id)
    for i, claim in enumerate(getattr(rr, "claims", [])):
        refs = [ev_by_id.get(eid) for eid in claim.evidence_ids]
        missing = [eid for eid, ev in zip(claim.evidence_ids, refs) if ev is None]
        if missing:
            notes.append(f"claim[{i}] references unknown evidence_id(s): {missing}")
        present = [ev for ev in refs if ev is not None]
        # NOTE: this only checks citation RESOLUTION, not paper->claim entailment.
        n_ok = sum(1 for ev in present if ev.resolved is True and ev.retracted is not True)
        retracted_any = any(ev.retracted is True for ev in present)
        if not present or n_ok == 0:
            claim.status = "unsupported"
        elif n_ok == len(present):
            claim.status = "supported"
        else:
            claim.status = "partial"
        if retracted_any:
            notes.append(f"claim[{i}] cites retracted evidence")
        if claim.status == "unsupported":
            n_downgraded += 1
    verify_meta = {
        "n_evidence": len(rr.evidence),
        "n_resolved": sum(1 for e in rr.evidence if e.resolved is True),
        "n_unresolved": sum(1 for e in rr.evidence if e.resolved is False),
        "n_retracted": sum(1 for e in rr.evidence if e.retracted is True),
        "n_claims_downgraded": n_downgraded,
    }
    if notes:
        verify_meta["notes"] = notes
    rr.meta["verify"] = verify_meta


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m research.verify",
        description="Deterministic evidence verifier for the Gene Program Interpreter.",
    )
    ap.add_argument(
        "--results-dir",
        required=True,
        type=Path,
        help="Directory of per-program ResearchResult *.json files (annotated in place).",
    )
    ap.add_argument(
        "--audit-dir",
        type=Path,
        default=None,
        help="Where to write raw pre-verify snapshots (default: sibling research_audit/).",
    )
    args = ap.parse_args(argv)
    summary = verify_directory(args.results_dir, audit_dir=args.audit_dir)
    print(json.dumps(summary, indent=2))
    return 0


__all__ = [
    "verify_research_result",
    "verify_directory",
    "normalize_agent_result",
    "resolve_pmids",
    "verify_dois",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
