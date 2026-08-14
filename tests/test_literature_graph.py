"""Bounded deterministic OpenAlex one-hop expansion."""

import asyncio

from research.literature import (
    CITATION_CITING_POOL,
    CITATION_KEEP_PER_DIRECTION,
    CITATION_REFERENCE_POOL,
    LiteratureClient,
)


def _work(index, *, title=None, retracted=False):
    return {
        "id": f"https://openalex.org/W{index}",
        "display_name": title or f"Kdr brain barrier paper {index}",
        "publication_year": 2020 + index % 5,
        "type": "article",
        "is_retracted": retracted,
        "cited_by_count": index,
        "ids": {"doi": f"https://doi.org/10.1234/{index}"},
        "primary_location": {"source": {"display_name": "Journal"}},
    }


class _GraphClient(LiteratureClient):
    def __init__(self, fail_references=False):
        self.calls = []
        self.fail_references = fail_references

    def _openalex_params(self):
        return {"api_key": "redacted"}

    async def _get(self, service, url, *, params=None, as_json=True):
        self.calls.append((url, dict(params or {})))
        if url.endswith("/W1"):
            return {"referenced_works": [f"https://openalex.org/W{i}" for i in range(2, 30)]}
        if str((params or {}).get("filter", "")).startswith("openalex_id:"):
            if self.fail_references:
                raise RuntimeError("reference direction failed")
            return {"results": [_work(i) for i in range(2, 12)]}
        return {"results": [_work(i) for i in range(20, 30)]}


def test_exact_pools_retention_and_stable_ranking():
    client = _GraphClient()
    first = asyncio.run(
        client.expand_openalex_citations(
            "W1", gene_terms=["Kdr"], mechanism_terms=["barrier"], identity_terms=["brain"]
        )
    )
    second = asyncio.run(
        client.expand_openalex_citations(
            "W1", gene_terms=["Kdr"], mechanism_terms=["barrier"], identity_terms=["brain"]
        )
    )
    assert first["requested_pools"] == {
        "references": CITATION_REFERENCE_POOL,
        "citing": CITATION_CITING_POOL,
    }
    assert len(first["references"]) == CITATION_KEEP_PER_DIRECTION
    assert len(first["citing"]) == CITATION_KEEP_PER_DIRECTION
    assert [r["openalex_id"] for r in first["references"]] == [
        r["openalex_id"] for r in second["references"]
    ]
    per_pages = [params.get("per-page") for _, params in client.calls if "per-page" in params]
    assert CITATION_REFERENCE_POOL in per_pages and CITATION_CITING_POOL in per_pages
    assert all("score_components" in record for record in first["references"])


def test_one_direction_failure_keeps_the_other():
    result = asyncio.run(_GraphClient(fail_references=True).expand_openalex_citations("W1"))
    assert result["references"] == []
    assert result["citing"]
    assert "references" in result["errors"]

