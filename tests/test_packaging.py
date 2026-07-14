"""The packaging contract: everything the pipeline imports must ship in the wheel.

This is a regression suite for a bug that reached users. ``research/verify.py`` loaded
``literature-review/kernel.py`` by filesystem path. That directory is hyphenated, so it can
never be a Python package and setuptools could not ship it — but nothing caught that, because
both of the ways we run the code in development put the *source tree* on ``sys.path``:

  * ``tests/conftest.py`` inserts the repo root, and
  * the dev venv is an *editable* install, whose ``__editable__`` finder maps ``gpi`` and
    ``research`` straight back to the source.

``bin/gpi`` does neither. It runs ``uv tool run --isolated --from "$PLUGIN_ROOT"``, which
builds a real wheel — so the import failed on every plugin install, and only there.

The lesson generalizes past this one file: a test that imports the package the same way the
developer does can never see a packaging bug. So the wheel test below builds a wheel and
imports it from a *separate interpreter in a separate environment*. Do not "simplify" it by
importing in-process — that would restore the exact blindness it exists to remove.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_PACKAGES = ("gpi", "research")

# Every module the pipeline can import at runtime (gpi.run_pipeline.STEP_MODULES, plus the
# orchestrator itself). If a step's module is not importable from the wheel, that step is
# dead on a real install.
RUNTIME_MODULES = [
    "gpi.run_pipeline",
    "gpi.enrichment",
    "gpi.gene_summaries",
    "gpi.theme_representation",
    "gpi.evidence_context",
    "gpi.anthropic_batch",
    "gpi.parse_results",
    "gpi.presentation",
    "gpi.html_report",
    "gpi.progress",
    "gpi.log_redaction",
    "research.bundle",
    "research.research_parallel",
    "research.verify",
]


def _shipped_sources() -> list[Path]:
    return [
        path
        for package in SHIPPED_PACKAGES
        for path in (REPO_ROOT / package).rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def test_no_shipped_module_loads_code_by_path() -> None:
    """Path-loading is how the bug got in. Ban the mechanism, not just the one instance.

    ``importlib.util.spec_from_file_location`` reaches outside the package to a filesystem
    path that exists in a source checkout and does not exist in a wheel. Any use of it in
    shipped code is a packaging bug waiting to happen, so this fails on the *mechanism*.
    """
    offenders = [
        f"{path.relative_to(REPO_ROOT)}"
        for path in _shipped_sources()
        if "spec_from_file_location" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "Shipped modules load code by filesystem path, which cannot survive a wheel build: "
        f"{offenders}. Import from a real package instead."
    )


def _code_string_constants(path: Path) -> list[str]:
    """Every string literal in ``path`` that is real code, not prose.

    Walks the AST, so comments are excluded for free (they never enter it), and drops
    docstrings explicitly. The distinction matters: the modules that *fixed* this bug
    naturally discuss ``literature-review`` in their docstrings, and a plain text grep cannot
    tell an explanation of a path from a use of one.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_no_shipped_module_references_the_unpackageable_directory() -> None:
    """``literature-review`` is hyphenated: it is not, and cannot become, an importable
    package. A *code* reference to it is unreachable on any real install — but a docstring
    explaining why we no longer use it is fine, so this checks string literals, not text."""
    offenders = [
        f"{path.relative_to(REPO_ROOT)}"
        for path in _shipped_sources()
        if any("literature-review" in s for s in _code_string_constants(path))
    ]
    assert not offenders, (
        f"Shipped modules reference the non-package directory 'literature-review': {offenders}"
    )


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed; it is what bin/gpi uses to build and run the wheel")
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run(
        ["uv", "build", "--wheel", "-o", str(out)],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return wheels[0]


def test_wheel_contains_every_shipped_source_file(built_wheel: Path) -> None:
    """Cheap structural check: no source file silently dropped from the distribution."""
    with zipfile.ZipFile(built_wheel) as zf:
        shipped = {name for name in zf.namelist() if name.endswith(".py")}
    missing = [
        str(path.relative_to(REPO_ROOT))
        for path in _shipped_sources()
        if str(path.relative_to(REPO_ROOT)) not in shipped
    ]
    assert not missing, f"source files are in the repo but not in the wheel: {missing}"


def test_every_runtime_module_imports_from_the_wheel(built_wheel: Path) -> None:
    """The real thing: import each runtime module from the wheel, in a clean environment.

    ``uv run --isolated --no-project`` is what makes this meaningful — it builds a fresh
    environment from the wheel's own declared dependencies, with no repo root on ``sys.path``
    and no editable-install finder. It is the closest reproduction of a user's ``bin/gpi``.

    A failure here means the plugin is broken for every user, no matter how green the rest of
    the suite is.
    """
    program = "\n".join(f"import {module}" for module in RUNTIME_MODULES)
    result = subprocess.run(
        [
            "uv", "run", "--isolated", "--no-project",
            "--with", str(built_wheel),
            "python", "-c", program + "\nprint('ok')",
        ],
        cwd=built_wheel.parent,  # never the repo root: '' on sys.path must not find the source
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "A pipeline module failed to import from the built wheel — the plugin is broken on a "
        f"real install:\n{result.stderr[-3000:]}"
    )
    assert "ok" in result.stdout


def test_verifier_does_not_depend_on_the_source_checkout(built_wheel: Path) -> None:
    """Pin the specific regression: ``verify_dois`` must resolve inside the wheel.

    It now lives in ``research._crossref``. If someone reintroduces the path-loading shim,
    this fails even if the module still imports in the dev venv.
    """
    result = subprocess.run(
        [
            "uv", "run", "--isolated", "--no-project",
            "--with", str(built_wheel),
            "python", "-c",
            "import research.verify as v; print(v.verify_dois.__module__)",
        ],
        cwd=built_wheel.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert result.stdout.strip().startswith("research."), (
        f"verify_dois resolved to {result.stdout.strip()!r}, which is outside the shipped "
        "package — it must come from a module that ships in the wheel."
    )
