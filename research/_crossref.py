"""
research._crossref — keyless DOI verification (CrossRef + doi.org HEAD), vendored INTO the package.

``verify_dois`` and its helpers are ported from ``literature-review/kernel.py``. That file lives
in a **hyphenated** directory, so it can never be a Python package and is NOT in the built wheel
(``pyproject.toml`` packages only ``gpi`` and ``research``): loading it by path worked in a source
checkout and raised ``FileNotFoundError`` on every plugin install. This module ships with the
``research`` package, so the verifier is importable wherever GPI is.

Two deliberate deviations from the kernel, both behaviour-preserving:

  * transport is ``requests`` (already a hard dependency, and what ``research.verify`` already
    uses) rather than ``urllib.request``. The observable semantics are identical: the HEAD does
    NOT follow redirects, so doi.org's *own* status is read (302 = registered, 404 = not
    registered) rather than the publisher's; one 2 s retry on 429; and any transport failure
    collapses to "no status".
  * the polite-pool contact comes from ``CROSSREF_MAILTO`` / ``PUBMED_EMAIL``. The kernel's
    contact helper called ``import host`` — a skill-runtime module that does not exist here — so
    it always returned None and GPI has never been in CrossRef's polite pool.

THE TRI-STATE IS LOAD-BEARING. Do not collapse it:

  ============  ==========================================  ==================================
  ``ok``        meaning                                     downstream effect
  ============  ==========================================  ==================================
  ``True``      resolves (CrossRef hit, or doi.org 2xx/3xx) evidence ``resolved=True``
  ``False``     authoritatively refuted (doi.org 404, or a  citation **dropped**, mechanism
                malformed DOI) — fabricated or a typo       ``unsupported``
  ``None``      could NOT be checked (network error,        citation **kept**, mechanism
                timeout, 5xx, rate limit)                   ``partial``
  ============  ==========================================  ==================================

A network failure is ``None``, never ``False``: turning a blip into ``False`` silently deletes
real papers, and turning a 404 into ``None`` lets fabricated ones through. CrossRef alone can
never refute a DOI — ``_get_json`` returns ``None`` for a 404 and for a dropped connection
alike — so the doi.org HEAD is the only arbiter of a negative.
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)

CROSSREF_WORKS = "https://api.crossref.org/works"
DOI_RESOLVER = "https://doi.org"

_UA_BASE = "gene-program-interpreter/0.1 (literature verification)"

_CROSSREF_PAUSE = 0.06   # polite pacing between CrossRef lookups (kernel parity)
_RETRY_AFTER_429 = 2.0   # one retry on rate-limit, then give up (kernel parity)
_GET_TIMEOUT = 15.0
_HEAD_TIMEOUT = 10.0


# --------------------------------------------------------------------------- env / contact

def env_value(name: str) -> Optional[str]:
    """``os.environ[name]``, falling back to a ``.env`` in the CURRENT WORKING DIRECTORY.

    Deliberately cwd-relative: in an installed wheel ``__file__`` points into site-packages,
    where the user's ``.env`` never lives — a repo-relative lookup is meaningless there. The
    value is never logged or printed.
    """
    val = os.environ.get(name)
    if val and val.strip():
        return val.strip()
    envf = Path.cwd() / ".env"
    if not envf.exists():
        return None
    try:
        lines = envf.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_val = line.partition("=")
        if key.strip() == name:
            return raw_val.strip().strip('"').strip("'") or None
    return None


_user_agent_cache: Optional[str] = None


def _user_agent() -> str:
    """User-Agent carrying the polite-pool contact CrossRef asks for (``mailto:``).

    Computed once per process. Non-ASCII is stripped so the header can never break the request.
    """
    global _user_agent_cache
    if _user_agent_cache is None:
        contact = env_value("CROSSREF_MAILTO") or env_value("PUBMED_EMAIL")
        ua = _UA_BASE + (f" (mailto:{contact})" if contact else "")
        _user_agent_cache = ua.encode("ascii", "ignore").decode("ascii")
    return _user_agent_cache


# ------------------------------------------------------------------------------- transport

def _get_json(url: str, timeout: float = _GET_TIMEOUT) -> Optional[dict]:
    """GET ``url`` and JSON-decode. One 2 s retry on HTTP 429; ``None`` on ANY failure.

    ``None`` here is NOT an authoritative negative: a 404, a timeout and a malformed body all
    look the same. It means only "CrossRef gave us no record"; the caller must fall through to
    the doi.org HEAD before concluding anything.
    """
    headers = {"User-Agent": _user_agent()}
    for attempt in (0, 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 429 and attempt == 0:
                time.sleep(_RETRY_AFTER_429)
                continue
            if not (200 <= resp.status_code < 300):
                return None
            return resp.json()
        except Exception:  # noqa: BLE001 - kernel parity: any failure means "no record"
            return None
    return None


def _head_status(url: str, timeout: float = _HEAD_TIMEOUT) -> Optional[int]:
    """HEAD ``url`` WITHOUT following redirects; return the origin server's own status.

    doi.org answers 302 for a registered DOI and 404 for an unregistered one. Following the
    redirect would report the *publisher's* status instead, which answers a different question
    (is the page up?) than the one that matters (does this DOI exist?). One 2 s retry on 429.

    Returns ``None`` — and ONLY ``None`` — when no status could be obtained at all (DNS failure,
    connection refused, timeout). That is the "could not check" signal; it must never be read as
    a refutation.
    """
    headers = {"User-Agent": _user_agent()}
    for attempt in (0, 1):
        try:
            resp = requests.head(url, headers=headers, timeout=timeout, allow_redirects=False)
        except Exception:  # noqa: BLE001 - no status obtained -> "could not check"
            return None
        if resp.status_code == 429 and attempt == 0:
            time.sleep(_RETRY_AFTER_429)
            continue
        return resp.status_code
    return None


# --------------------------------------------------------------------------------- helpers

def quote_doi_path(doi: str) -> str:
    """URL-encode a DOI path; unquote each segment first so a pre-encoded ``%28`` stays
    single-encoded (the caller may pass either form)."""
    return "/".join(
        urllib.parse.quote(urllib.parse.unquote(seg), safe="") for seg in doi.split("/")
    )


def crossref_year(m: dict) -> Optional[int]:
    """Safely extract the publication year from a CrossRef ``message`` record."""
    dp = (m.get("published") or {}).get("date-parts") or [[None]]
    return (dp[0] or [None])[0]


def short_authors(names: List[str]) -> Optional[str]:
    """Collapse an author list to note form: first three names, semicolon-separated (names may
    carry internal commas), then 'et al.' when more authors exist or any entry is nameless.
    Returns None when the record carries no author names at all."""
    kept = [n.strip() for n in names if n and n.strip()]
    if not kept:
        return None
    more = len(names) > 3 or len(kept) < len(names)
    return "; ".join(kept[:3]) + (" et al." if more else "")


def crossref_authors(m: dict) -> Optional[str]:
    """Note-form author names (family names) from a CrossRef ``message`` record."""
    return short_authors(
        [a.get("family") or a.get("name") or "" for a in (m.get("author") or [])]
    )


# ---------------------------------------------------------------------------- verify_dois

def verify_dois(dois: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Resolve each DOI against CrossRef, with a doi.org HEAD fallback for DataCite/mEDRA/arXiv
    DOIs. Returns ``{doi (lowercased, stripped): record}`` where each record carries ``ok``:

      * ``ok=True``  — resolves (CrossRef hit, or doi.org 2xx/3xx);
      * ``ok=False`` — does NOT resolve (doi.org 404, or a dot-segment in the DOI): likely
        fabricated or a typo. The citation is DROPPED downstream and its mechanism marked
        ``unsupported``;
      * ``ok=None``  — could NOT be verified (network/transient/5xx/rate-limit). The citation is
        KEPT and its mechanism marked ``partial``. **Never** report a network failure as False.

    A CrossRef miss is not a refutation — ``_get_json`` returns None for a 404 and for a dropped
    connection alike — so every negative has to come from the doi.org HEAD, which distinguishes
    "no such DOI" (404) from "could not ask" (None / 5xx / 429).

    ``retracted`` is True/False only on a CrossRef hit; None when the registry is non-CrossRef or
    the lookup was unverified. Optional keys (``title``/``authors``/``year``/``journal``/
    ``registry``/``error``) appear exactly as the kernel emitted them.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for raw in dois:
        d = str(raw).strip()
        if not d:
            continue
        key = d.lower()
        if key in out:
            continue  # case-insensitive duplicate; already resolved above

        # A DOI whose path carries an empty / '.' / '..' segment is malformed (and would traverse
        # the request path). Refuse it WITHOUT a network call — authoritative, not a blip.
        segs = urllib.parse.unquote(d).split("/")
        if any(seg in ("", ".", "..") for seg in segs[1:]):
            out[key] = {"ok": False, "error": "dot-segment in DOI"}
            continue

        enc = quote_doi_path(d)
        j = _get_json(f"{CROSSREF_WORKS}/{enc}")
        time.sleep(_CROSSREF_PAUSE)
        if j and "message" in j:
            m = j["message"]
            title = (m.get("title") or [""])[0]
            upd = [u.get("type", "") for u in (m.get("update-to") or [])]
            retracted = (
                any("retract" in t.lower() for t in upd)
                or str(m.get("subtype") or "").lower() == "retraction"
                or str(title).upper().startswith("RETRACTED")
            )
            out[key] = {
                "ok": True,
                "title": title,
                "authors": crossref_authors(m),
                "year": crossref_year(m),
                "journal": (m.get("container-title") or [""])[0],
                "retracted": retracted,
                "registry": "crossref",
            }
            continue

        # CrossRef had no record. That is NOT a refutation (a network failure looks identical).
        # doi.org is the only arbiter of a negative:
        code = _head_status(f"{DOI_RESOLVER}/{enc}")
        if code is not None and 200 <= code < 400:
            # registered outside CrossRef (DataCite / mEDRA / arXiv): exists, retraction unknown
            out[key] = {"ok": True, "registry": "non-crossref", "retracted": None}
        elif code == 404:
            out[key] = {"ok": False}                     # authoritative: no such DOI
        else:
            # no status, 5xx, or a 429 that survived its retry -> COULD NOT CHECK, never False
            out[key] = {"ok": None, "error": "unverified (network)", "retracted": None}
    return out


__all__ = [
    "verify_dois",
    "env_value",
    "quote_doi_path",
    "crossref_authors",
    "crossref_year",
    "short_authors",
]
