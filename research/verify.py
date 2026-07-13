"""
research.verify — deterministic evidence verifier (executor 4).

Guardrail #1 (ARCHITECTURE.md): this is deterministic code that *validates*
identifiers — it does NOT research. It takes the per-program ``ResearchResult``
artifacts written by the research subagents and annotates them **in place**:

  * resolves every ``Evidence`` DOI (CrossRef + doi.org, keyless) and/or PMID
    (NCBI E-utilities ``esummary``), reconciling PMID<->DOI when one is missing;
  * flags retractions (CrossRef ``update-to`` / subtype);
  * downgrades any ``Claim`` whose cited evidence does not resolve (or is
    retracted) to ``status="unsupported"`` — never invents support;
  * dedups evidence across programs (same DOI/PMID -> one canonical record,
    keeping per-program ``evidence_id`` references intact);
  * writes each annotated ``ResearchResult`` back to its file (same schema), and
    keeps a raw pre-verify copy under a sibling ``research_audit/`` dir.

Reused building blocks:
  * ``verify_dois`` / ``crossref_lookup`` from ``literature-review/kernel.py``
    (loaded robustly by path — that file is not a package). ``verify_dois`` is
    KEYLESS and returns per DOI ``{ok: True|False|None, title?, authors?, year?,
    journal?, retracted?, registry?}``.
  * A small keyless-or-keyed PMID resolver (``resolve_pmids``) implemented here
    against ``esummary.fcgi?db=pubmed`` — NcbiClient has no PMID-metadata method.

CLI:  ``python -m research.verify --results-dir research_results/ [--audit-dir research_audit/]``
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import requests

from research.schema import (
    AgentResearchResult,
    CandidateMechanism,
    Citation,
    Claim,
    Evidence,
    ResearchResult,
)

# ---------------------------------------------------------------------------
# Robustly load literature-review/kernel.py (NOT a package) by repo-relative path
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_KERNEL_PATH = _REPO_ROOT / "literature-review" / "kernel.py"


def _load_kernel():
    if not _KERNEL_PATH.exists():
        raise FileNotFoundError(
            f"literature-review/kernel.py not found at {_KERNEL_PATH}; "
            "the verifier reuses its keyless verify_dois/crossref_lookup."
        )
    spec = importlib.util.spec_from_file_location("litreview_kernel", _KERNEL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_kernel = _load_kernel()
verify_dois = _kernel.verify_dois          # keyless CrossRef + doi.org HEAD
crossref_lookup = _kernel.crossref_lookup  # free-text -> DOI (kept available)

# ---------------------------------------------------------------------------
# PMID resolver (NCBI E-utilities esummary) — keyless or keyed
# ---------------------------------------------------------------------------
EUTILS_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_RATE_NO_KEY = 0.34   # ~3 req/s, polite
_RATE_WITH_KEY = 0.11  # ~9 req/s


def _ncbi_api_key() -> Optional[str]:
    """NCBI key from env; fall back to the repo .env (never printed)."""
    k = os.environ.get("NCBI_API_KEY")
    if k:
        return k.strip() or None
    envf = _REPO_ROOT / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            line = line.strip()
            if line.startswith("NCBI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


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


def normalize_agent_result(agent: AgentResearchResult) -> ResearchResult:
    """Turn a flat ``AgentResearchResult`` (inline citations) into a canonical
    ``ResearchResult``: dedup the cited papers into one ``Evidence`` pool (by DOI/PMID),
    assign ``EV-NNN`` ids, and rewrite each claim/mechanism to reference those ids.

    Citations without any identifier are dropped (they cannot be verified). Claim
    ``status`` is left ``"partial"`` here and finalized by the resolution pass.
    """
    pool: list[Evidence] = []
    id_by_key: dict[tuple, str] = {}

    def _keys(c: Citation) -> list[tuple]:
        ks: list[tuple] = []
        nd = _norm_doi(c.doi)
        if nd:
            ks.append(("doi", nd))
        if c.pmid and str(c.pmid).strip():
            ks.append(("pmid", str(c.pmid).strip()))
        return ks

    def _intern(c: Citation) -> Optional[str]:
        ks = _keys(c)
        if not ks:
            return None  # no identifier -> cannot verify -> drop
        eid = next((id_by_key[k] for k in ks if k in id_by_key), None)
        if eid is None:
            eid = f"EV-{len(pool) + 1:03d}"
            pool.append(
                Evidence(
                    evidence_id=eid,
                    pmid=(str(c.pmid).strip() if c.pmid else None),
                    doi=(str(c.doi).strip() if c.doi else None),
                    title=c.title,
                    year=c.year,
                    study_type=c.study_type,
                    relevance_note=c.note,
                )
            )
        else:
            ev = next(e for e in pool if e.evidence_id == eid)
            if not ev.pmid and c.pmid:
                ev.pmid = str(c.pmid).strip()
            if not ev.doi and c.doi:
                ev.doi = str(c.doi).strip()
            ev.title = ev.title or c.title
            ev.year = ev.year or c.year
            ev.study_type = ev.study_type or c.study_type
            ev.relevance_note = ev.relevance_note or c.note
        for k in ks:
            id_by_key.setdefault(k, eid)
        return eid

    def _ids(citations: list[Citation]) -> list[str]:
        seen: list[str] = []
        for c in citations:
            eid = _intern(c)
            if eid and eid not in seen:
                seen.append(eid)
        return seen

    mechanisms = [
        CandidateMechanism(
            name=m.name,
            summary=m.summary,
            supporting_genes=m.supporting_genes,
            evidence_ids=_ids(m.citations),
        )
        for m in agent.candidate_mechanisms
    ]
    claims = [
        Claim(
            statement=c.statement,
            supporting_genes=c.supporting_genes,
            evidence_ids=_ids(c.citations),
            context_match=c.context_match,
            status="partial",
        )
        for c in agent.claims
    ]
    return ResearchResult(
        program_id=agent.program_id,
        queries=agent.queries,
        candidate_mechanisms=mechanisms,
        claims=claims,
        evidence=pool,
        contradictions=agent.contradictions,
        evidence_gaps=agent.evidence_gaps,
        agent_summary=agent.agent_summary,
        meta={"normalized_from_agent": True},
    )


# ---------------------------------------------------------------------------
# Per-evidence resolution (network) — annotates Evidence in place
# ---------------------------------------------------------------------------
def _resolve_evidence(rr: ResearchResult) -> None:
    """Resolve every ``Evidence`` DOI/PMID and annotate resolved/registry/
    retracted/verify_error in place; reconcile a missing DOI from the PMID."""
    dois = sorted({e.doi.strip() for e in rr.evidence if e.doi and e.doi.strip()})
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
            d = pr.get("doi") if pr else None
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
            dr = doi_res.get(doi)
            if dr is not None:
                ok = dr.get("ok")
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
# Claim downgrade + meta summary (pure; assumes evidence already annotated)
# ---------------------------------------------------------------------------
def _apply_claims_and_meta(rr: ResearchResult) -> None:
    ev_by_id = rr.evidence_by_id()
    notes: list[str] = []
    n_downgraded = 0

    # audit: duplicate evidence_ids within this program
    seen: set[str] = set()
    for e in rr.evidence:
        if e.evidence_id in seen:
            notes.append(f"duplicate evidence_id within program: {e.evidence_id!r}")
        seen.add(e.evidence_id)

    for i, claim in enumerate(rr.claims):
        refs = [ev_by_id.get(eid) for eid in claim.evidence_ids]
        missing = [eid for eid, ev in zip(claim.evidence_ids, refs) if ev is None]
        if missing:
            notes.append(f"claim[{i}] references unknown evidence_id(s): {missing}")
        present = [ev for ev in refs if ev is not None]

        # Status is DERIVED from resolution (the agent does not set it):
        #   all cited papers resolve & none retracted -> supported
        #   some resolve                              -> partial
        #   none resolve / all retracted / no cites   -> unsupported
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def verify_research_result(result) -> ResearchResult:
    """Verify one result: normalize the flat agent output if needed, resolve every
    evidence identifier, reconcile PMID<->DOI, flag retractions, derive each claim's
    status from resolution, and populate ``meta['verify']``. Accepts either a flat
    ``AgentResearchResult`` or a canonical ``ResearchResult``; returns the canonical one."""
    if isinstance(result, AgentResearchResult):
        rr = normalize_agent_result(result)
    elif isinstance(result, ResearchResult):
        rr = result
    else:
        raise TypeError(f"expected ResearchResult or AgentResearchResult, got {type(result)!r}")
    _resolve_evidence(rr)
    _apply_claims_and_meta(rr)
    return rr


def _load_result(raw: str) -> ResearchResult:
    """Load a result file as either the flat ``AgentResearchResult`` (agent output) or
    the canonical ``ResearchResult``, returning the canonical form. A flat file has no
    top-level ``evidence`` array; its claims/mechanisms carry inline ``citations``."""
    data = json.loads(raw)
    entries = (data.get("claims") or []) + (data.get("candidate_mechanisms") or [])
    is_flat = "evidence" not in data or any(
        isinstance(x, dict) and "citations" in x for x in entries
    )
    if is_flat:
        return normalize_agent_result(AgentResearchResult.model_validate(data))
    return ResearchResult.model_validate(data)


def verify_directory(directory: Path, audit_dir: Optional[Path] = None) -> dict:
    """Verify every ``*.json`` ``ResearchResult`` in ``directory`` in place.

    Writes a raw pre-verify copy to ``{audit_dir}/{program_id}.pre_verify.json``
    (default sibling ``research_audit/``) BEFORE overwriting, dedups evidence
    across programs, and returns an audit summary dict.
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
    for f in files:
        raw = f.read_text()
        try:
            rr = _load_result(raw)  # accepts flat agent output OR canonical form
        except Exception as e:  # noqa: BLE001 - fail loudly with the offending file
            raise ValueError(f"{f} is not a valid research result: {e}") from e
        rrs.append(rr)
        file_of[id(rr)] = f
        # raw record kept separate from summaries (spec P1): pre-verify snapshot
        (audit_dir / f"{rr.program_id}.pre_verify.json").write_text(raw)

    # 1) resolve identifiers within each program (batched network calls)
    for rr in rrs:
        _resolve_evidence(rr)
    # 2) dedup evidence across programs (unify canonical verification fields)
    dedup = _canonicalize_across(rrs)
    # 3) apply claim downgrade + meta after cross-program merge
    for rr in rrs:
        _apply_claims_and_meta(rr)

    # 4) write annotated ResearchResults back in place
    programs: dict[str, dict] = {}
    for rr in rrs:
        file_of[id(rr)].write_text(rr.model_dump_json(indent=2))
        programs[rr.program_id] = rr.meta["verify"]

    return {
        "directory": str(directory),
        "audit_dir": str(audit_dir),
        "n_programs": len(rrs),
        **dedup,
        "n_claims_downgraded_total": sum(
            p["n_claims_downgraded"] for p in programs.values()
        ),
        "n_retracted_total": sum(p["n_retracted"] for p in programs.values()),
        "programs": programs,
    }


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
    "crossref_lookup",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
