"""Shared pytest fixtures + path setup for the Gene Program Interpreter tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# Make `gpi` and `research` importable without an editable install.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def gene_loading_csv() -> Path:
    return FIXTURES / "inputs" / "gene_loading.csv"


@pytest.fixture(scope="session")
def regulators_csv() -> Path:
    return FIXTURES / "inputs" / "regulators.csv"


@pytest.fixture(scope="session")
def literature_context_json() -> Path:
    return FIXTURES / "literature" / "literature_context.json"


@pytest.fixture(scope="session")
def annotations_dir() -> Path:
    return FIXTURES / "annotations"
