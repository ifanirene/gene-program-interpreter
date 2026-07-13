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
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

NCBI_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
OPENALEX_BASE_URL = "https://api.openalex.org"
CROSSREF_BASE_URL = "https://api.crossref.org"

MAX_SEARCH_RESULTS = 15
MAX_FETCH_IDS = 20

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
            return _crossref_record((payload or {}).get("message", {}))
        payload = await self._get(
            "crossref", f"{CROSSREF_BASE_URL}/works",
            params={"query.bibliographic": identifier, "rows": 1, **params},
        )
        items = (payload or {}).get("message", {}).get("items", [])
        return _crossref_record(items[0]) if items else None


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
LITERATURE_TOOL_NAMES = ("search_pubmed", "fetch_pubmed", "search_openalex", "resolve_doi")


def build_literature_mcp_server(
    client: Optional[LiteratureClient] = None,
    *,
    max_results_per_search: int = 10,
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

    @tool("search_pubmed",
          "Search PubMed for a query. Returns PMIDs (discovery ids) — fetch_pubmed them for "
          "canonical metadata + DOIs before citing.",
          {"query": str, "max_results": int})
    async def search_pubmed_tool(args: Mapping[str, Any]) -> Any:
        try:
            return _tool_result(await client.search_pubmed(
                str(args.get("query", "")), max_results=int(args.get("max_results", cap))))
        except Exception as exc:  # noqa: BLE001 - surface a clean error to the agent, never crash the session
            return _tool_result({"error": str(exc), "pmids": []})

    @tool("fetch_pubmed",
          "Fetch canonical PubMed metadata (pmid, doi, title, year, journal, study_type, "
          "abstract, is_preprint, is_retracted) for up to 20 PMIDs.",
          {"pmids": list})
    async def fetch_pubmed_tool(args: Mapping[str, Any]) -> Any:
        try:
            return _tool_result(await client.fetch_pubmed(list(args.get("pmids", []))))
        except Exception as exc:  # noqa: BLE001
            return _tool_result({"error": str(exc), "records": []})

    @tool("search_openalex",
          "Search OpenAlex (cross-publisher, includes preprints). Records carry doi/pmid for "
          "verification. Requires OPENALEX_API_KEY; returns an error if unavailable.",
          {"query": str, "max_results": int})
    async def search_openalex_tool(args: Mapping[str, Any]) -> Any:
        try:
            return _tool_result(await client.search_openalex(
                str(args.get("query", "")), max_results=int(args.get("max_results", cap))))
        except Exception as exc:  # noqa: BLE001
            return _tool_result({"error": str(exc), "records": []})

    @tool("resolve_doi",
          "Resolve a DOI or a bibliographic string against Crossref to get/verify a real DOI, "
          "title, and year.",
          {"identifier": str})
    async def resolve_doi_tool(args: Mapping[str, Any]) -> Any:
        try:
            return _tool_result(await client.resolve_doi(str(args.get("identifier", ""))))
        except Exception as exc:  # noqa: BLE001
            return _tool_result({"error": str(exc)})

    server = create_sdk_mcp_server(
        name=LITERATURE_SERVER_NAME,
        version="0.1.0",
        tools=[search_pubmed_tool, fetch_pubmed_tool, search_openalex_tool, resolve_doi_tool],
    )
    return server


__all__ = [
    "LiteratureClient",
    "build_literature_mcp_server",
    "normalize_doi",
    "normalize_pmid",
    "LITERATURE_SERVER_NAME",
    "LITERATURE_TOOL_NAMES",
    "MAX_SEARCH_RESULTS",
    "MAX_FETCH_IDS",
]
