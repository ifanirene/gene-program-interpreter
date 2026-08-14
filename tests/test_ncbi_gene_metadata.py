"""Focused tests for taxid-specific NCBI metadata and flexible bundle guidance."""

from __future__ import annotations

import pandas as pd
import pytest

from gpi.context_profile import ContextProfile
from gpi.ncbi_api import NcbiClient
from research.bundle import build_bundle


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.parametrize("taxid", [9606, 10116])
def test_gene_metadata_uses_explicit_taxid_and_keeps_unresolved(monkeypatch, taxid):
    client = NcbiClient(api_key="test")
    terms = []

    def fake_get(url, params, retries=3):
        if url.endswith("esearch.fcgi"):
            terms.append(params["term"])
            ids = ["123"] if params["term"].startswith("KDR[") else []
            return _Response({"esearchresult": {"idlist": ids}})
        return _Response(
            {
                "result": {
                    "123": {
                        "name": "KDR",
                        "description": "kinase insert domain receptor",
                        "otheraliases": "VEGFR2, Flk1, VEGFR2, KDR",
                    }
                }
            }
        )

    monkeypatch.setattr(client, "_get", fake_get)
    metadata = client.resolve_gene_metadata(["KDR", "Missing"], species_taxid=taxid)

    assert all(f"txid{taxid}[Organism:exp]" in term for term in terms)
    assert metadata["KDR"] == {
        "canonical_symbol": "KDR",
        "entrez_id": "123",
        "description": "kinase insert domain receptor",
        "aliases": ["Flk1", "VEGFR2"],
        "source": "NCBI Gene",
        "status": "resolved",
    }
    assert metadata["Missing"]["status"] == "unresolved"
    assert metadata["Missing"]["entrez_id"] is None


def _gene_frame():
    return pd.DataFrame(
        {
            "program_id": [1] * 6,
            "Name": ["Kdr", "A", "B", "C", "D", "E"],
            "Score": [6, 5, 4, 3, 2, 1],
            "UniquenessScore": [1, 2, 3, 4, 5, 6],
        }
    )


@pytest.mark.parametrize(
    ("profile", "identity"),
    [
        (ContextProfile(organism="mouse", tissue="brain", cell_type="endothelial cell"),
         ["endothelial cell", "brain"]),
        (ContextProfile(organism="mouse", cell_type="brain endothelial cell"),
         ["brain endothelial cell"]),
        (ContextProfile(organism="mouse", tissue="brain"), ["brain"]),
        (ContextProfile(organism="human", species_taxid=9606), []),
    ],
)
def test_bundle_guidance_follows_profile_without_assay(profile, identity):
    bundle = build_bundle(1, _gene_frame(), profile, top_loading=3, top_unique=2)
    assert bundle["tissue"] == profile.tissue
    assert bundle["query_guidance"]["identity_terms"] == identity
    assert bundle["query_guidance"]["condition_terms"] == []
    assert bundle["query_guidance"]["patterns"][-1] == "(GENE OR ALIAS)"
    assert "assay" not in str(bundle["query_guidance"]).casefold()


def test_bundle_compacts_metadata_aliases_and_description():
    aliases = ["z", "A6", "a2", "A1", "A5", "A4", "A3", "Kdr"]
    context = {
        "gene_metadata": {
            "Kdr": {
                "canonical_symbol": "Kdr",
                "entrez_id": 16542,
                "description": "x" * 400,
                "aliases": aliases,
                "source": "NCBI Gene",
                "status": "resolved",
            }
        }
    }
    bundle = build_bundle(
        1, _gene_frame(), ContextProfile(tissue="brain"),
        ncbi_context=context, top_loading=3, top_unique=2,
    )
    record = bundle["gene_metadata"]["Kdr"]
    assert record["entrez_id"] == "16542"
    assert record["aliases"] == ["A1", "a2", "A3", "A4", "A5", "A6"]
    assert len(record["description"]) == 280 and record["description"].endswith("…")

