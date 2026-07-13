"""Every module imports cleanly together — catches cross-module integration breaks.

Independently-built modules can each pass in isolation yet clash when co-imported
(name collisions, circular imports, mismatched relative-import paths). This asserts
the whole package composes.
"""

import importlib

import pytest

GPI_MODULES = [
    "gpi.context_profile",
    "gpi.column_mapper",
    "gpi.string_api",
    "gpi.ncbi_api",
    "gpi.harmonizome_api",
    "gpi.pipeline_state",
    "gpi.parse_results",
    "gpi.enrichment",
    "gpi.gene_summaries",
    "gpi.research_evidence_adapter",
    "gpi.anthropic_batch",
    "gpi.evidence_context",
    "gpi.theme_representation",
    "gpi.presentation",
    "gpi.html_report",
]

RESEARCH_MODULES = [
    "research.schema",
    "research.bundle",
    "research.verify",
    "research.research_parallel",
]


@pytest.mark.parametrize("mod", GPI_MODULES + RESEARCH_MODULES)
def test_module_imports(mod):
    assert importlib.import_module(mod) is not None


def test_key_public_symbols_present():
    from gpi.context_profile import ContextProfile
    from gpi.anthropic_batch import submit_batch
    from gpi.evidence_context import generate_prompt, format_research_evidence_context
    from gpi.research_evidence_adapter import load_research_evidence_directory
    from research.schema import ResearchResult
    from research.bundle import build_bundle
    from research.verify import verify_research_result

    assert all(
        callable(x)
        for x in (
            ContextProfile,
            submit_batch,
            generate_prompt,
            format_research_evidence_context,
            load_research_evidence_directory,
            ResearchResult,
            build_bundle,
            verify_research_result,
        )
    )
