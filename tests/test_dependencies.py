"""Every declared runtime dependency must actually be imported by the shipped code.

This is a regression suite for a bug that reached a user. ``pyproject.toml`` declared
``matplotlib``, ``seaborn``, ``pillow``, ``jinja2`` and ``beautifulsoup4`` in ``dependencies``,
but not one of them is imported anywhere in ``gpi/`` or ``research/`` — the report's plots are
Plotly.js from a CDN and the enrichment figures are PNGs downloaded from the STRING API. They
were inherited from an ancestor project and never removed.

They were not harmless. ``matplotlib`` pulls ``contourpy``, which needs a C++17 compiler, and
``uv tool run --from …`` (what ``bin/gpi`` does) builds the environment from source. On Stanford
SCG's CentOS 7 (GCC 4.8.5, no C++17) the install died compiling a package the pipeline never
runs — five minutes of a scientist's time spent building dead weight, then a hard failure.

The lesson generalizes past those five packages: a dependency nobody imports is a liability with
no offsetting value — extra install surface, extra attack surface, extra ways to break on an
unusual toolchain. So this test fails on the *mechanism* (a declared runtime dep with no import
site), not on the five specific names.
"""

from __future__ import annotations

import ast
import re
import tomllib
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNED_PACKAGES = ("gpi", "research")

# A distribution's PyPI name is often not its import name. ``importlib.metadata`` knows the
# real mapping for whatever is installed; this table only has to cover cases that installed
# metadata might miss, and doubles as human-readable documentation of the non-obvious ones.
_KNOWN_IMPORT_NAMES = {
    "pyyaml": {"yaml"},
    "pillow": {"PIL"},
    "beautifulsoup4": {"bs4"},
    "claude-agent-sdk": {"claude_agent_sdk"},
    "anthropic": {"anthropic"},
    "pydantic": {"pydantic"},
}


def _normalize(dist_name: str) -> str:
    """PEP 503 normalization so ``PyYAML``/``pyyaml``/``py_yaml`` all compare equal."""
    return re.sub(r"[-_.]+", "-", dist_name).lower()


def _declared_runtime_dependencies() -> list[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    out = []
    for spec in data["project"]["dependencies"]:
        # Strip version/marker/extra specifiers: "anthropic>=0.40" -> "anthropic".
        name = re.split(r"[<>=!~;\[\s]", spec, maxsplit=1)[0].strip()
        if name:
            out.append(name)
    return out


def _import_names_for(dist_name: str) -> set[str]:
    """Top-level import modules a distribution provides (installed metadata ∪ known table)."""
    names: set[str] = set(_KNOWN_IMPORT_NAMES.get(_normalize(dist_name), set()))
    try:
        top_level = metadata.distribution(dist_name).read_text("top_level.txt")
    except metadata.PackageNotFoundError:
        top_level = None
    if top_level:
        names.update(line.strip() for line in top_level.splitlines() if line.strip())
    # Last-resort fallback: assume the import name equals the normalized dist name with
    # hyphens as underscores (true for pandas, numpy, requests, markdown, ...).
    names.add(_normalize(dist_name).replace("-", "_"))
    return names


def _imported_top_level_modules() -> set[str]:
    """Every top-level module name imported anywhere under the shipped packages."""
    imported: set[str] = set()
    for package in SCANNED_PACKAGES:
        for path in (REPO_ROOT / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])
    return imported


def test_declared_runtime_deps_are_actually_imported() -> None:
    """A declared runtime dependency that nothing imports is dead weight — and dead weight is
    what broke the SCG install. Fail on any such dependency, not just the five we removed."""
    imported = _imported_top_level_modules()
    unused = [
        dep
        for dep in _declared_runtime_dependencies()
        if not (_import_names_for(dep) & imported)
    ]
    assert not unused, (
        "These runtime dependencies are declared in pyproject.toml but imported nowhere in "
        f"{SCANNED_PACKAGES}: {unused}. Remove them, or if one is a genuine transitive/runtime "
        "requirement with no direct import, add it to _KNOWN_IMPORT_NAMES with a comment "
        "explaining why. An unused dependency is install surface with no value — and the reason "
        "GPI would not install on an older compiler."
    )


def test_the_matplotlib_stack_stays_gone() -> None:
    """Pin the specific regression. These five are the ones that pulled the C++17 build; if any
    returns to pyproject.toml, this fails by name with the reason attached."""
    banned = {"matplotlib", "seaborn", "pillow", "jinja2", "beautifulsoup4"}
    declared = {_normalize(d) for d in _declared_runtime_dependencies()}
    resurrected = sorted(banned & declared)
    assert not resurrected, (
        f"{resurrected} is back in runtime dependencies. It is imported nowhere and pulls a "
        "C++17-compiled subtree (contourpy/kiwisolver) that breaks `uv` installs on older "
        "toolchains such as SCG's GCC 4.8.5. If plotting is genuinely needed, add it as an "
        "optional extra, not a hard runtime dependency."
    )
