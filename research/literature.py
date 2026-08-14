"""
research.literature — in-process literature retrieval tools for the research agent.

Instead of depending on an external MCP server (a plugin that only exists in an
interactive session, or third-party ``uvx``/``npx`` stdio servers that must be installed
and connected), this module implements the literature tools **in-process**: plain async
``httpx`` calls to PubMed E-utilities, OpenAlex, and Crossref, wrapped as Claude Agent SDK
tools via ``create_sdk_mcp_server``. The server runs inside the orchestrator process (which
holds the API keys); the SDK bridges it to each subprocess session. Because nothing external
is launched or inherited, the tools are available **identically headless and interactive** —
this is the reliability property the external/plugin paths could not provide.

The agent only ever sees the small, read-only tool surface below. Every record carries a
tool-returned PMID and/or DOI so the deterministic verifier (``research/verify.py``) can
resolve it afterwards; web-search snippets are never a source here.

Tools (server name ``literature`` -> ``mcp__literature__<tool>``):
  * ``search_pubmed(query, max_results)``  -> PMIDs (discovery ids, not yet canonical)
  * ``fetch_pubmed(pmids)``                -> canonical metadata + DOI/year/preprint/retracted
  * ``search_openalex(query, max_results)``-> cross-publisher records (incl. preprints)
  * ``resolve_doi(identifier)``            -> Crossref metadata for a DOI / bibliographic string

Env (loaded from repo ``.env`` by the runner): ``NCBI_API_KEY`` + ``PUBMED_EMAIL`` (optional,
lift NCBI rate limit / Entrez courtesy); ``OPENALEX_API_KEY`` (required for OpenAlex — the
tool degrades to a clear error if unset). Crossref needs no key.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Mapping, MutableSequence, Optional, Sequence
from urllib.parse import quote

import httpx

from gpi.log_redaction import redact_text

logger = logging.getLogger(__name__)

NCBI_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
OPENALEX_BASE_URL = "https://api.openalex.org"
CROSSREF_BASE_URL = "https://api.crossref.org"
EUROPE_PMC_BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"

MAX_SEARCH_RESULTS = 15
MAX_FETCH_IDS = 20
MAX_ANCHORS_PER_MECHANISM = 2
CITATION_REFERENCE_POOL = 20
CITATION_CITING_POOL = 10
CITATION_KEEP_PER_DIRECTION = 5
MAX_CITATION_EXPANSIONS = 6

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_PMID_RE = re.compile(r"^\d{1,9}$")
_TAG_RE = re.compile(r"<[^>]+>")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


# --------------------------------------------------------------------------- normalizers

def normalize_doi(value: Optional[str]) -> Optional[str]:
    """Return a bare lowercase DOI, or None if *value* is not a DOI."""
    if not value:
        return None
    doi = str(value).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    doi = doi.rstrip(".,;)")
    return doi if _DOI_RE.fullmatch(doi) else None


def normalize_pmid(value: Any) -> Optional[str]:
    if value is None:
        return None
    pmid = str(value).strip()
    return pmid if _PMID_RE.fullmatch(pmid) else None


def _clean(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return " ".join(html.unescape(_TAG_RE.sub(" ", text)).split()) or None


def _bounded_query(value: Any) -> str:
    q = " ".join(str(value or "").split())
    if not q:
        raise ValueError("query must not be empty")
    return q[:512]


def _bounded_limit(value: Any, maximum: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = maximum
    return max(1, min(n, maximum))


def _research_phase(value: Any) -> str:
    phase = str(value or "unspecified").strip().casefold().replace("-", "_")
    return phase[:64] or "unspecified"


def _target_genes(values: Any) -> List[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return list(
        dict.fromkeys(str(value).strip() for value in values if str(value).strip())
    )[:100]


class LiteratureTraceRecorder:
    """Append allowlisted schema-v2 events at the literature execution boundary."""

    def __init__(
        self, events: MutableSequence[Dict[str, Any]], *, attempt: int = 1
    ) -> None:
        self.events = events
        self.attempt = int(attempt)

    def append(
        self,
        *,
        source: str,
        action: str,
        research_phase: Any,
        target_genes: Any,
        status: str,
        duration_ms: int,
        query: Optional[str] = None,
        identifiers: Optional[Sequence[str]] = None,
        requested_limit: Optional[Any] = None,
        effective_limit: Optional[int] = None,
        returned_identifiers: Optional[Sequence[str]] = None,
        error: Optional[str] = None,
        result_metadata: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> None:
        event: Dict[str, Any] = {
            "schema_version": 2,
            "attempt": self.attempt,
            "sequence": len(self.events) + 1,
            "source": source,
            "action": action,
            "research_phase": _research_phase(research_phase),
            "target_genes": _target_genes(target_genes),
            "status": status,
            "duration_ms": max(0, int(duration_ms)),
            "returned_identifiers": list(
                dict.fromkeys(str(value) for value in (returned_identifiers or []) if value)
            ),
        }
        event["returned_count"] = len(event["returned_identifiers"])
        if query is not None:
            event["query"] = query
        if identifiers is not None:
            event["identifiers"] = list(
                dict.fromkeys(str(value) for value in identifiers if value)
            )
        if requested_limit is not None:
            event["requested_limit"] = requested_limit
        if effective_limit is not None:
            event["effective_limit"] = effective_limit
        if error:
            event["error"] = redact_text(str(error))[:500]
        if result_metadata is not None:
            event["result_metadata"] = [dict(item) for item in result_metadata]
        self.events.append(event)


# ------------------------------------------------------------------------------- client

class _RateLimiter:
    """Minimal monotonic per-service rate limiter (requests/second)."""

    def __init__(self, rps: float) -> None:
        self._interval = 1.0 / rps if rps > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = time.monotonic() + self._interval


class LiteratureClient:
    """Async, read-only clients for PubMed / OpenAlex / Crossref.

    Conservative result caps, one transient retry, per-service rate limiting. API keys are
    read from the environment and never returned in records.
    """

    def __init__(
        self,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_seconds: float = 30.0,
        transient_retries: int = 1,
    ) -> None:
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": "gene-program-interpreter/0.1 (literature research)"},
        )
        self._transient_retries = max(0, min(int(transient_retries), 2))
        ncbi_rps = 9.0 if os.getenv("NCBI_API_KEY") else 3.0
        self._limiters = {
            "ncbi": _RateLimiter(ncbi_rps),
            "openalex": _RateLimiter(5.0),
            "crossref": _RateLimiter(5.0),
            "europe_pmc": _RateLimiter(3.0),
        }

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # -- transport --------------------------------------------------------------------

    async def _get(
        self, service: str, url: str, *, params: Optional[Mapping[str, Any]] = None, as_json: bool = True
    ) -> Any:
        transient = {408, 425, 429, 500, 502, 503, 504}
        last: Optional[Exception] = None
        for attempt in range(self._transient_retries + 1):
            await self._limiters[service].wait()
            try:
                resp = await self._http.get(url, params=params)
                if resp.status_code in transient:
                    raise httpx.HTTPStatusError(
                        f"transient HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json() if as_json else resp.text
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last = exc
                retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                    exc.response.status_code in transient
                )
                if not retryable or attempt >= self._transient_retries:
                    raise RuntimeError(f"{service} request failed: {exc}") from exc
                await asyncio.sleep(0.25 * (attempt + 1))
        raise RuntimeError(f"{service} request failed: {last}")

    def _ncbi_params(self) -> Dict[str, str]:
        params: Dict[str, str] = {"tool": "gene-program-interpreter"}
        if email := (os.getenv("PUBMED_EMAIL") or os.getenv("NCBI_EMAIL")):
            params["email"] = email
        if key := os.getenv("NCBI_API_KEY"):
            params["api_key"] = key
        return params

    # -- PubMed -----------------------------------------------------------------------

    async def search_pubmed(self, query: str, *, max_results: int = MAX_SEARCH_RESULTS) -> Dict[str, Any]:
        query = _bounded_query(query)
        max_results = _bounded_limit(max_results, MAX_SEARCH_RESULTS)
        payload = await self._get(
            "ncbi",
            f"{NCBI_BASE_URL}/esearch.fcgi",
            params={
                "db": "pubmed", "term": query, "retmax": max_results,
                "retmode": "json", "sort": "relevance", **self._ncbi_params(),
            },
        )
        result = payload.get("esearchresult", {}) if isinstance(payload, dict) else {}
        pmids = [p for item in result.get("idlist", []) if (p := normalize_pmid(item))]
        return {"query": query, "count": int(result.get("count", len(pmids))), "pmids": pmids[:max_results]}

    async def fetch_pubmed(self, pmids: Sequence[Any]) -> List[Dict[str, Any]]:
        norm = list(dict.fromkeys(p for p in (normalize_pmid(x) for x in pmids) if p))
        if not norm:
            return []
        if len(norm) > MAX_FETCH_IDS:
            norm = norm[:MAX_FETCH_IDS]
        xml_text = await self._get(
            "ncbi", f"{NCBI_BASE_URL}/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(norm), "retmode": "xml", **self._ncbi_params()},
            as_json=False,
        )
        return _parse_pubmed_xml(str(xml_text))

    # -- OpenAlex ---------------------------------------------------------------------

    def _openalex_params(self) -> Dict[str, str]:
        key = os.getenv("OPENALEX_API_KEY")
        if not key:
            raise RuntimeError("OPENALEX_API_KEY is not set; use search_pubmed / resolve_doi instead")
        params = {"api_key": key}
        if email := (os.getenv("OPENALEX_EMAIL") or os.getenv("OPENALEX_MAILTO")):
            params["mailto"] = email
        return params

    async def search_openalex(self, query: str, *, max_results: int = MAX_SEARCH_RESULTS) -> Dict[str, Any]:
        query = _bounded_query(query)
        max_results = _bounded_limit(max_results, MAX_SEARCH_RESULTS)
        payload = await self._get(
            "openalex", f"{OPENALEX_BASE_URL}/works",
            params={"search": query, "per-page": max_results, **self._openalex_params()},
        )
        records = [_openalex_record(item) for item in payload.get("results", [])]
        return {"query": query, "count": int(payload.get("meta", {}).get("count", 0)), "records": records[:max_results]}

    # -- Crossref ---------------------------------------------------------------------

    async def resolve_doi(self, identifier: str) -> Optional[Dict[str, Any]]:
        identifier = _bounded_query(identifier)
        params: Dict[str, Any] = {}
        if email := (os.getenv("CROSSREF_MAILTO") or os.getenv("PUBMED_EMAIL")):
            params["mailto"] = email
        doi = normalize_doi(identifier)
        if doi:
            payload = await self._get(
                "crossref", f"{CROSSREF_BASE_URL}/works/{quote(doi, safe='')}", params=params
            )
            record = _crossref_record((payload or {}).get("message", {}))
            return await self._augment_doi_from_europe_pmc(record)
        payload = await self._get(
            "crossref", f"{CROSSREF_BASE_URL}/works",
            params={"query.bibliographic": identifier, "rows": 1, **params},
        )
        items = (payload or {}).get("message", {}).get("items", [])
        record = _crossref_record(items[0]) if items else None
        return await self._augment_doi_from_europe_pmc(record)

    async def _augment_doi_from_europe_pmc(
        self, record: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        if not record or not record.get("doi"):
            return record
        try:
            payload = await self._get(
                "europe_pmc", f"{EUROPE_PMC_BASE_URL}/search",
                params={
                    "query": f'DOI:"{record["doi"]}"', "format": "json",
                    "pageSize": 1, "resultType": "core",
                },
            )
        except RuntimeError:
            record["text_type"] = "unavailable"
            return record
        results = ((payload or {}).get("resultList") or {}).get("result") or []
        if not results:
            record["text_type"] = "unavailable"
            return record
        match = results[0]
        record["pmid"] = normalize_pmid(match.get("pmid"))
        record["pmcid"] = match.get("pmcid")
        record["abstract"] = _clean(match.get("abstractText"))
        record["text_type"] = "abstract" if record["abstract"] else "unavailable"
        return record

    async def expand_openalex_citations(
        self,
        openalex_id: str,
        *,
        gene_terms: Sequence[str] = (),
        mechanism_terms: Sequence[str] = (),
        identity_terms: Sequence[str] = (),
    ) -> Dict[str, Any]:
        """Return a deterministic, bounded one-hop discovery pool for one anchor."""
        anchor_id = str(openalex_id).strip().rsplit("/", 1)[-1]
        if not re.fullmatch(r"W\d+", anchor_id, flags=re.I):
            raise ValueError("openalex_id must be a work id such as W123")
        params = self._openalex_params()
        anchor = await self._get(
            "openalex", f"{OPENALEX_BASE_URL}/works/{anchor_id}", params=params
        )
        reference_ids = [
            str(value).rsplit("/", 1)[-1]
            for value in anchor.get("referenced_works", [])[:CITATION_REFERENCE_POOL]
        ]
        reference_records: List[Dict[str, Any]] = []
        errors: Dict[str, str] = {}
        if reference_ids:
            try:
                payload = await self._get(
                    "openalex", f"{OPENALEX_BASE_URL}/works",
                    params={
                        "filter": "openalex_id:" + "|".join(reference_ids),
                        "per-page": CITATION_REFERENCE_POOL, **params,
                    },
                )
                reference_records = [
                    _openalex_record(item) for item in payload.get("results", [])
                ]
            except RuntimeError as exc:
                errors["references"] = redact_text(str(exc))
        citing_records: List[Dict[str, Any]] = []
        try:
            citing_payload = await self._get(
                "openalex", f"{OPENALEX_BASE_URL}/works",
                params={
                    "filter": f"cites:{anchor_id}", "per-page": CITATION_CITING_POOL, **params,
                },
            )
            citing_records = [
                _openalex_record(item) for item in citing_payload.get("results", [])
            ]
        except RuntimeError as exc:
            errors["citing"] = redact_text(str(exc))
        terms = list(gene_terms) + list(mechanism_terms) + list(identity_terms)
        return {
            "anchor_openalex_id": anchor_id,
            "references": _rank_citation_candidates(
                reference_records, terms, anchor_id, "reference"
            ),
            "citing": _rank_citation_candidates(citing_records, terms, anchor_id, "citing"),
            "requested_pools": {
                "references": CITATION_REFERENCE_POOL, "citing": CITATION_CITING_POOL
            },
            "errors": errors,
        }


# ------------------------------------------------------------------------------- parsers

def _node_text(node: Optional[ET.Element]) -> Optional[str]:
    if node is None:
        return None
    return " ".join("".join(node.itertext()).split()) or None


def _pubmed_year(article: ET.Element) -> Optional[int]:
    for path in (".//ArticleDate/Year", ".//Journal/JournalIssue/PubDate/Year",
                 ".//Journal/JournalIssue/PubDate/MedlineDate"):
        text = _node_text(article.find(path))
        if text and (m := _YEAR_RE.search(text)):
            return int(m.group())
    return None


def _parse_pubmed_xml(xml_text: str) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    records: List[Dict[str, Any]] = []
    for citation in root.findall(".//PubmedArticle"):
        article = citation.find(".//MedlineCitation/Article")
        medline = citation.find(".//MedlineCitation")
        if article is None or medline is None:
            continue
        ids = {
            node.attrib.get("IdType", "").lower(): (node.text or "").strip()
            for node in citation.findall(".//PubmedData/ArticleIdList/ArticleId")
        }
        pmid = normalize_pmid(_node_text(medline.find("PMID")) or ids.get("pubmed"))
        if not pmid:
            continue
        abstract_parts: List[str] = []
        for node in article.findall(".//Abstract/AbstractText"):
            if text := _node_text(node):
                label = node.attrib.get("Label")
                abstract_parts.append(f"{label}: {text}" if label else text)
        pub_types = [t for node in article.findall(".//PublicationTypeList/PublicationType")
                     if (t := _node_text(node))]
        comments = [node.attrib.get("RefType", "")
                    for node in medline.findall(".//CommentsCorrectionsList/CommentsCorrections")]
        journal_node = article.find(".//Journal")
        journal = _node_text(journal_node.find("Title")) if journal_node is not None else None
        retracted = any(t.casefold() in {"retracted publication", "retraction of publication"} for t in pub_types) \
            or any("retraction" in c.casefold() for c in comments)
        abstract = " ".join(abstract_parts) or None
        records.append({
            "source": "pubmed",
            "pmid": pmid,
            "doi": normalize_doi(ids.get("doi")),
            "title": _node_text(article.find("ArticleTitle")) or "",
            "year": _pubmed_year(article),
            "journal": journal,
            "study_type": _study_type(pub_types),
            "abstract": abstract[:1500] if abstract else None,
            "is_preprint": any("preprint" in t.casefold() for t in pub_types),
            "is_retracted": retracted,
        })
    return records


def _study_type(pub_types: Sequence[str]) -> Optional[str]:
    lowered = [t.casefold() for t in pub_types]
    for key, label in (("review", "review"), ("clinical trial", "clinical trial"),
                       ("meta-analysis", "meta-analysis"), ("randomized", "randomized trial")):
        if any(key in t for t in lowered):
            return label
    non_generic = [t for t in pub_types if t.casefold() not in {"journal article", "research support"}]
    return non_generic[0] if non_generic else (pub_types[0] if pub_types else None)


def _openalex_record(item: Mapping[str, Any]) -> Dict[str, Any]:
    ids = item.get("ids") or {}
    pmid_raw = ids.get("pmid")
    pmid = normalize_pmid(str(pmid_raw).rstrip("/").rsplit("/", 1)[-1]) if pmid_raw else None
    source = (item.get("primary_location") or {}).get("source") or {}
    return {
        "source": "openalex",
        "openalex_id": str(item.get("id") or "").rsplit("/", 1)[-1] or None,
        "pmid": pmid,
        "doi": normalize_doi(ids.get("doi") or item.get("doi")),
        "title": item.get("display_name") or item.get("title") or "",
        "year": item.get("publication_year"),
        "journal": source.get("display_name"),
        "study_type": item.get("type"),
        "is_preprint": item.get("type") == "preprint",
        "is_retracted": bool(item.get("is_retracted")),
        "cited_by_count": int(item.get("cited_by_count") or 0),
    }


def _rank_citation_candidates(
    records: Sequence[Dict[str, Any]],
    terms: Sequence[str],
    anchor_id: str,
    direction: str,
) -> List[Dict[str, Any]]:
    """Filter and rank discovery titles deterministically; titles never become evidence."""
    normalized_terms = [str(term).casefold() for term in terms if str(term).strip()]
    seen: set[str] = set()
    ranked: List[tuple[tuple[Any, ...], Dict[str, Any]]] = []
    for record in records:
        work_id = str(record.get("openalex_id") or "")
        identifier = str(record.get("pmid") or record.get("doi") or work_id)
        if not identifier or work_id.casefold() == anchor_id.casefold() or identifier in seen:
            continue
        if record.get("is_retracted"):
            continue
        seen.add(identifier)
        title = str(record.get("title") or "").casefold()
        overlap = sum(1 for term in normalized_terms if term in title)
        primary = str(record.get("study_type") or "").casefold() not in {
            "review", "meta-analysis"
        }
        score = {
            "term_overlap": overlap,
            "primary_paper": primary,
            "cited_by_count": int(record.get("cited_by_count") or 0),
        }
        item = {**record, "direction": direction, "score_components": score}
        key = (
            -overlap, -int(primary), -score["cited_by_count"],
            -(int(record.get("year") or 0)), work_id,
        )
        ranked.append((key, item))
    return [item for _, item in sorted(ranked, key=lambda pair: pair[0])[:CITATION_KEEP_PER_DIRECTION]]


def _crossref_record(item: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if not item:
        return None
    titles = item.get("title") or []
    title = titles[0] if isinstance(titles, list) and titles else str(titles or "")
    date_parts = ((item.get("published") or {}).get("date-parts")
                  or (item.get("issued") or {}).get("date-parts") or [])
    year = date_parts[0][0] if date_parts and date_parts[0] else None
    container = item.get("container-title") or []
    relation = item.get("relation") or {}
    return {
        "source": "crossref",
        "pmid": None,
        "doi": normalize_doi(item.get("DOI")),
        "title": _clean(title) or "",
        "year": year,
        "journal": container[0] if container else None,
        "study_type": item.get("type"),
        "is_preprint": str(item.get("subtype") or "").casefold() == "preprint" or item.get("type") == "posted-content",
        "is_retracted": "is-retracted-by" in relation,
    }


# -------------------------------------------------------------- in-process MCP server

def _tool_result(payload: Any) -> Dict[str, List[Dict[str, str]]]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}]}


LITERATURE_SERVER_NAME = "literature"
LITERATURE_TOOL_NAMES = (
    "search_pubmed", "fetch_pubmed", "search_openalex", "resolve_doi",
    "expand_openalex_citations",
)


def build_literature_mcp_server(
    client: Optional[LiteratureClient] = None,
    *,
    max_results_per_search: int = 10,
    trace_recorder: Optional[LiteratureTraceRecorder] = None,
) -> Any:
    """Create the in-process ``literature`` Agent SDK MCP server (lazy SDK import).

    Returns the SDK server object to register in ``ClaudeAgentOptions.mcp_servers``. If no
    ``client`` is given a default ``LiteratureClient`` is created (used for the live session;
    ``dry_run`` builds the server without ever calling a tool).
    """
    try:
        from claude_agent_sdk import create_sdk_mcp_server, tool
    except ImportError as exc:  # pragma: no cover - only when the SDK runtime is absent
        raise RuntimeError("claude-agent-sdk is required to build the literature MCP server") from exc

    client = client or LiteratureClient()
    cap = _bounded_limit(max_results_per_search, MAX_SEARCH_RESULTS)
    expansion_calls = 0
    expansion_calls_by_mechanism: Dict[str, int] = {}

    search_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"},
            "research_phase": {"type": "string"},
            "target_genes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["query", "max_results", "research_phase", "target_genes"],
        "additionalProperties": False,
    }
    fetch_schema = {
        "type": "object",
        "properties": {
            "pmids": {"type": "array", "items": {"type": ["string", "integer"]}},
            "research_phase": {"type": "string"},
            "target_genes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["pmids", "research_phase", "target_genes"],
        "additionalProperties": False,
    }
    resolve_schema = {
        "type": "object",
        "properties": {
            "identifier": {"type": "string"},
            "research_phase": {"type": "string"},
            "target_genes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["identifier", "research_phase", "target_genes"],
        "additionalProperties": False,
    }
    expansion_schema = {
        "type": "object",
        "properties": {
            "openalex_id": {"type": "string"},
            "gene_terms": {"type": "array", "items": {"type": "string"}},
            "mechanism_terms": {"type": "array", "items": {"type": "string"}},
            "mechanism_name": {"type": "string"},
            "identity_terms": {"type": "array", "items": {"type": "string"}},
            "research_phase": {"type": "string", "enum": ["expansion"]},
            "target_genes": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "openalex_id", "gene_terms", "mechanism_name", "mechanism_terms", "identity_terms",
            "research_phase", "target_genes",
        ],
        "additionalProperties": False,
    }

    @tool("search_pubmed",
          "Search PubMed for a query. Returns PMIDs (discovery ids) — fetch_pubmed them for "
          "canonical metadata + DOIs before citing. Declare research_phase and target_genes "
          "so the search is auditable.",
          search_schema)
    async def search_pubmed_tool(args: Mapping[str, Any]) -> Any:
        started = time.monotonic()
        requested = args.get("max_results", cap)
        effective = _bounded_limit(requested, cap)
        query = " ".join(str(args.get("query", "")).split())[:512]
        try:
            result = await client.search_pubmed(query, max_results=effective)
            if trace_recorder:
                trace_recorder.append(
                    source="pubmed", action="search",
                    research_phase=args.get("research_phase"),
                    target_genes=args.get("target_genes"), status="ok",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    query=result["query"], requested_limit=requested,
                    effective_limit=effective, returned_identifiers=result.get("pmids", []),
                )
            return _tool_result(result)
        except Exception as exc:  # noqa: BLE001 - surface a clean error to the agent, never crash the session
            if trace_recorder:
                trace_recorder.append(
                    source="pubmed", action="search",
                    research_phase=args.get("research_phase"),
                    target_genes=args.get("target_genes"), status="error",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    query=query, requested_limit=requested, effective_limit=effective,
                    error=str(exc),
                )
            return _tool_result({"error": redact_text(str(exc)), "pmids": []})

    @tool("fetch_pubmed",
          "Fetch canonical PubMed metadata (pmid, doi, title, year, journal, study_type, "
          "abstract, is_preprint, is_retracted) for up to 20 PMIDs.",
          fetch_schema)
    async def fetch_pubmed_tool(args: Mapping[str, Any]) -> Any:
        started = time.monotonic()
        identifiers = list(
            dict.fromkeys(
                pmid for pmid in (normalize_pmid(value) for value in args.get("pmids", []))
                if pmid
            )
        )
        effective_ids = identifiers[:MAX_FETCH_IDS]
        try:
            result = await client.fetch_pubmed(effective_ids)
            if trace_recorder:
                trace_recorder.append(
                    source="pubmed", action="fetch",
                    research_phase=args.get("research_phase"),
                    target_genes=args.get("target_genes"), status="ok",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    identifiers=effective_ids, requested_limit=len(identifiers),
                    effective_limit=len(effective_ids),
                    returned_identifiers=[record.get("pmid") for record in result],
                )
            return _tool_result(result)
        except Exception as exc:  # noqa: BLE001
            if trace_recorder:
                trace_recorder.append(
                    source="pubmed", action="fetch",
                    research_phase=args.get("research_phase"),
                    target_genes=args.get("target_genes"), status="error",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    identifiers=effective_ids, requested_limit=len(identifiers),
                    effective_limit=len(effective_ids), error=str(exc),
                )
            return _tool_result({"error": redact_text(str(exc)), "records": []})

    @tool("search_openalex",
          "Search OpenAlex (cross-publisher, includes preprints). Records carry doi/pmid for "
          "verification. Requires OPENALEX_API_KEY; returns an error if unavailable. Declare "
          "research_phase and target_genes so the search is auditable.",
          search_schema)
    async def search_openalex_tool(args: Mapping[str, Any]) -> Any:
        started = time.monotonic()
        requested = args.get("max_results", cap)
        effective = _bounded_limit(requested, cap)
        query = " ".join(str(args.get("query", "")).split())[:512]
        try:
            result = await client.search_openalex(query, max_results=effective)
            returned = []
            for record in result.get("records", []):
                if record.get("pmid"):
                    returned.append(f"PMID:{record['pmid']}")
                elif record.get("doi"):
                    returned.append(f"DOI:{record['doi']}")
            if trace_recorder:
                trace_recorder.append(
                    source="openalex", action="search",
                    research_phase=args.get("research_phase"),
                    target_genes=args.get("target_genes"), status="ok",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    query=result["query"], requested_limit=requested,
                    effective_limit=effective, returned_identifiers=returned,
                )
            return _tool_result(result)
        except Exception as exc:  # noqa: BLE001
            if trace_recorder:
                trace_recorder.append(
                    source="openalex", action="search",
                    research_phase=args.get("research_phase"),
                    target_genes=args.get("target_genes"), status="error",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    query=query, requested_limit=requested, effective_limit=effective,
                    error=str(exc),
                )
            return _tool_result({"error": redact_text(str(exc)), "records": []})

    @tool("resolve_doi",
          "Resolve a DOI or a bibliographic string against Crossref to get/verify a real DOI, "
          "title, and year.",
          resolve_schema)
    async def resolve_doi_tool(args: Mapping[str, Any]) -> Any:
        started = time.monotonic()
        identifier = " ".join(str(args.get("identifier", "")).split())[:512]
        try:
            result = await client.resolve_doi(identifier)
            returned = [f"DOI:{result['doi']}"] if result and result.get("doi") else []
            if trace_recorder:
                trace_recorder.append(
                    source="crossref", action="resolve",
                    research_phase=args.get("research_phase"),
                    target_genes=args.get("target_genes"), status="ok",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    identifiers=[identifier], returned_identifiers=returned,
                )
            return _tool_result(result)
        except Exception as exc:  # noqa: BLE001
            if trace_recorder:
                trace_recorder.append(
                    source="crossref", action="resolve",
                    research_phase=args.get("research_phase"),
                    target_genes=args.get("target_genes"), status="error",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    identifiers=[identifier], error=str(exc),
                )
            return _tool_result({"error": redact_text(str(exc))})

    @tool(
        "expand_openalex_citations",
        "Expand one selected OpenAlex anchor by one bounded citation hop. Discovery titles are "
        "not evidence; fetch abstract/full text before selecting a paper.",
        expansion_schema,
    )
    async def expand_openalex_citations_tool(args: Mapping[str, Any]) -> Any:
        nonlocal expansion_calls
        started = time.monotonic()
        anchor_id = str(args.get("openalex_id", "")).strip()
        mechanism_name = " ".join(str(args.get("mechanism_name", "")).split())
        mechanism_key = mechanism_name.casefold()
        mechanism_calls = expansion_calls_by_mechanism.get(mechanism_key, 0)
        if (
            expansion_calls >= MAX_CITATION_EXPANSIONS
            or mechanism_calls >= MAX_ANCHORS_PER_MECHANISM
        ):
            error = f"citation expansion cap reached ({MAX_CITATION_EXPANSIONS})"
            if trace_recorder:
                trace_recorder.append(
                    source="openalex", action="expand",
                    research_phase="expansion", target_genes=args.get("target_genes"),
                    status="error", duration_ms=0, identifiers=[anchor_id], error=error,
                )
            return _tool_result({"error": error, "references": [], "citing": []})
        expansion_calls += 1
        expansion_calls_by_mechanism[mechanism_key] = mechanism_calls + 1
        try:
            result = await client.expand_openalex_citations(
                anchor_id,
                gene_terms=args.get("gene_terms") or [],
                mechanism_terms=args.get("mechanism_terms") or [],
                identity_terms=args.get("identity_terms") or [],
            )
            returned = [
                str(record.get("pmid") or record.get("doi") or record.get("openalex_id"))
                for direction in ("references", "citing")
                for record in result.get(direction, [])
            ]
            if trace_recorder:
                metadata = [
                    {
                        "identifier": str(
                            record.get("pmid") or record.get("doi") or record.get("openalex_id")
                        ),
                        "direction": record.get("direction"),
                        "score_components": record.get("score_components") or {},
                    }
                    for direction in ("references", "citing")
                    for record in result.get(direction, [])
                ]
                trace_recorder.append(
                    source="openalex", action="expand",
                    research_phase="expansion", target_genes=args.get("target_genes"),
                    status="ok", duration_ms=round((time.monotonic() - started) * 1000),
                    identifiers=[anchor_id], returned_identifiers=returned,
                    requested_limit=CITATION_REFERENCE_POOL + CITATION_CITING_POOL,
                    effective_limit=len(returned),
                    result_metadata=metadata,
                )
            return _tool_result(result)
        except Exception as exc:  # noqa: BLE001
            if trace_recorder:
                trace_recorder.append(
                    source="openalex", action="expand",
                    research_phase="expansion", target_genes=args.get("target_genes"),
                    status="error", duration_ms=round((time.monotonic() - started) * 1000),
                    identifiers=[anchor_id], error=str(exc),
                )
            return _tool_result({
                "error": redact_text(str(exc)), "references": [], "citing": []
            })

    server = create_sdk_mcp_server(
        name=LITERATURE_SERVER_NAME,
        version="0.1.0",
        tools=[
            search_pubmed_tool, fetch_pubmed_tool, search_openalex_tool, resolve_doi_tool,
            expand_openalex_citations_tool,
        ],
    )
    return server


__all__ = [
    "LiteratureClient",
    "LiteratureTraceRecorder",
    "build_literature_mcp_server",
    "normalize_doi",
    "normalize_pmid",
    "LITERATURE_SERVER_NAME",
    "LITERATURE_TOOL_NAMES",
    "MAX_SEARCH_RESULTS",
    "MAX_FETCH_IDS",
    "MAX_ANCHORS_PER_MECHANISM",
    "CITATION_REFERENCE_POOL",
    "CITATION_CITING_POOL",
    "CITATION_KEEP_PER_DIRECTION",
    "MAX_CITATION_EXPANSIONS",
]
