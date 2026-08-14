# Plan 002: Add flexible context and gene metadata

> **Status: COMPLETE (2026-07-22).** Depends on frozen `baseline-v4` from Plan 001. Drift check:
> `git diff --stat 7bbefb0..HEAD -- gpi/context_profile.py gpi/ncbi_api.py gpi/gene_summaries.py research/bundle.py research/protocol.md`

## Goal

Give the agent enough biological context to search well across different experiments without
forcing one rigid query. Real profiles include specific `brain endothelial cell`, broad
`endothelial cell + brain`, hepatocyte aging/MASLD, and human profiles without tissue.

## Scope

- Modify `gpi/ncbi_api.py`, `gpi/gene_summaries.py`, `research/bundle.py`, and
  `research/protocol.md`; add focused tests.
- Preserve legacy `gene_summaries`, defining gene lists, assay exclusion, and batching settings.

## Target bundle additions

```json
{
  "tissue": "brain",
  "gene_metadata": {
    "Kdr": {
      "canonical_symbol": "Kdr",
      "entrez_id": "16542",
      "description": "kinase insert domain protein receptor",
      "aliases": ["Flk1", "Vegfr2"],
      "source": "NCBI Gene"
    }
  },
  "query_guidance": {
    "organism": "mouse",
    "identity_terms": ["endothelial cell", "brain"],
    "condition_terms": ["development"],
    "function_terms": ["blood-brain barrier"],
    "patterns": ["(GENE OR ALIAS) AND (CELL_TYPE OR TISSUE)"]
  }
}
```

## Steps

1. Make NCBI Gene lookup use the profile’s `species_taxid`; current normalization otherwise defaults
   to mouse. Return canonical symbol, Entrez ID, official description, aliases, and explicit
   unresolved status.
2. Resolve metadata once per run for defining genes and surfaced regulators. Keep the existing
   `gene_summaries` field for compatibility.
3. Add tissue and compact per-program metadata to bundles: at most six stable aliases per gene and
   descriptions clipped near 280 characters. Never rewrite input gene symbols.
4. Build optional query components from the actual profile. Keep tissue and cell type separate;
   omit empty groups; treat conditions as optional lenses; provide a broad fallback rather than one
   mandatory literal query. Never include assay.
5. Update the protocol to use useful aliases selectively and to adapt patterns to the experiment.
   Metadata supports the per-gene evidence pass in Plan 004; it must not pre-group genes into themes
   or force an exact tissue match before retrieval.

## Verification

```bash
.venv/bin/pytest -q tests/test_context_profile.py tests/test_research_pipeline.py tests/test_ncbi_gene_metadata.py
.venv/bin/pytest -q
.venv/bin/ruff check --select E9,F63,F7,F82 gpi/ncbi_api.py gpi/gene_summaries.py research/bundle.py
```

Tests must cover mouse, human, another taxid, unresolved genes, broad cell+tissue, specific cell,
tissue-only, empty conditions, deterministic alias order, and legacy contexts.

## Done when

- Representative brain/liver/human bundles contain correct flexible guidance and compact metadata.
- Defining gene lists are identical to baseline; assay is absent.
- `MAX_FETCH_IDS=20` and BioC batch `100` are unchanged.

## Stop if

- metadata silently maps unresolved genes, guidance forces a condition into every search, or old
  `ncbi_context.json` files require migration.

## Completed result — 2026-07-22

- NCBI Gene resolution now uses the configured taxonomy ID and preserves explicit unresolved
  records without rewriting supplied symbols.
- Per-run context contains defining-gene and surfaced-regulator metadata; bundles compact aliases
  to six, descriptions to 280 characters, and retain legacy `gene_summaries` compatibility.
- Bundles now carry tissue plus profile-derived identity, condition, function, and broad fallback
  query components. Conditions remain optional and assay is absent.
- Added `tests/test_ncbi_gene_metadata.py`; focused tests and the full 155-test suite pass. PubMed
  fetch batching remains 20 and BioC batching remains 100.
