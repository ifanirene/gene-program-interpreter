"""
Config-driven pipeline orchestrator for the Gene Program Interpreter.

This is the keystone that wires the already-built modules in order. It is NOT a
faithful vendor of ProgExplorer's ``run_pipeline.py`` — the pipeline shape changed
(a parallel literature-research subsystem was inserted, Anthropic-only). It REUSES
``gpi.pipeline_state`` for resume/caching.

Step order (executor code in brackets — ④ deterministic, ② research agents,
③ Anthropic Batch). Each step writes into ``output_dir/`` and is skippable via
pipeline_state resume:

  1. string_enrichment [④]  gpi.enrichment            → top genes + STRING enrichment CSVs
  2. gene_summaries    [④]  gpi.gene_summaries         → ncbi_context.json
  3. bundle            [④]  research.bundle            → program_bundles/{id}.json
  4. research          [②]  research.research_parallel → research_results/{id}.json (gated; spends $)
  5. verify            [④]  research.verify            → annotate ResearchResults in place + dedup
  6. theme             [③]  gpi.theme_representation   → theme_dictionary.json (gated; spends $)
  7. annotate          [③]  gpi.evidence_context + gpi.anthropic_batch + gpi.parse_results
  8. presentation      [③]  gpi.presentation           → presentation.json
  9. html_report       [④]  gpi.html_report            → report.html

CLI:
  python -m gpi.run_pipeline --config configs/example_generic.yaml \
      [--start-from STEP] [--stop-after STEP] [--no-research] \
      [--deterministic-presentation] [--force-restart] [--dry-run]

The ``context:`` block of the config is materialized into a
``gpi.context_profile.ContextProfile`` and threaded into steps 3/7/9 by writing a
resolved ``profile.yaml`` into ``output_dir`` and passing its path (those modules
accept ``--profile``). Nothing about any specific tissue is hard-coded here.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import shutil
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from . import __version__
from .context_profile import ContextProfile
from .log_redaction import install_log_redaction
from . import pipeline_state as ps
from .progress import (
    RESEARCH_DONE,
    RESEARCH_START,
    RUN_DONE,
    RUN_START,
    STEP_DONE,
    STEP_PROGRESS,
    STEP_START,
    get_emitter,
    make_emitter,
    set_emitter,
)

logger = logging.getLogger("gpi.pipeline")

# Repo root = parent of this package dir (gpi/..). Used only as a development
# fallback; an installed CLI loads .env from the directory where the user runs it.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def env_search_candidates() -> List[Path]:
    """The ordered ``.env`` locations ``load_env_file`` tries, first match wins.

    The project's own ``.env`` must win, so we walk **upward** from the current directory the
    way git finds ``.git`` — the SCG failure was a ``.env`` one level above where the user ran
    ``gpi``, which the old cwd-only lookup missed. After the working tree we try the installed
    plugin dir (``$CLAUDE_PLUGIN_ROOT``) and finally the source checkout (dev fallback).
    """
    candidates: List[Path] = []
    seen: set = set()

    def add(p: Path) -> None:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            candidates.append(rp)

    cwd = Path.cwd().resolve()
    for directory in (cwd, *cwd.parents):
        add(directory / ".env")
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        add(Path(plugin_root) / ".env")
    add(_REPO_ROOT / ".env")
    return candidates


def load_env_file(path: Optional[Path] = None) -> Optional[Path]:
    """Populate ``os.environ`` from ``.env`` (only keys not already set). Return the file used.

    The runner spawns most steps as subprocesses (``python -m gpi.theme_representation``,
    ``gpi.evidence_context``, ``gpi.anthropic_batch``, ...) that read ``ANTHROPIC_API_KEY``
    /``NCBI_API_KEY``/``OPENALEX_API_KEY`` from their inherited environment. Load the
    current project's ``.env`` here so those keys are present for EVERY step,
    including a resumed run that starts *after* the research step (previously only the
    research step loaded ``.env``, so ``--start-from theme`` in a fresh process failed).
    Mirrors ``research.research_parallel.load_env_file`` without importing the Agent SDK.

    Returns the path actually loaded, or ``None`` if no ``.env`` was found — so ``doctor`` can
    tell the user exactly which file supplied the keys (or which locations it searched).
    """
    if path is not None:
        chosen: Optional[Path] = path if path.exists() else None
    else:
        chosen = next((c for c in env_search_candidates() if c.exists()), None)
    if chosen is None:
        return None
    for raw in chosen.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
    return chosen

# The one canonical step order. (pipeline_state.STEP_NAMES is the legacy ProgExplorer
# list; we drive our own order here and let mark_step lazily create entries.)
STEP_ORDER: List[str] = [
    "string_enrichment",
    "gene_summaries",
    "bundle",
    "research",
    "verify",
    "theme",
    "annotate",
    "presentation",
    "html_report",
]

# Executor code per step, purely for human-readable plan output.
STEP_EXECUTOR: Dict[str, str] = {
    "string_enrichment": "4",
    "gene_summaries": "4",
    "bundle": "4",
    "research": "2",
    "verify": "4",
    "theme": "3/4",
    "annotate": "3",
    "presentation": "3/4",
    "html_report": "4",
}

# The Python module each step ultimately imports. Used only by preflight_imports(), which
# imports them all up front so a broken install fails at $0 instead of mid-run: most steps
# import their module inside a subprocess, and the research step imports its verifier inside
# a retry loop that reports *any* exception as a bad agent payload — so a packaging error
# there is indistinguishable from a bad payload, and costs a full paid research run to find.
STEP_MODULES: Dict[str, List[str]] = {
    "string_enrichment": ["gpi.enrichment"],
    "gene_summaries": ["gpi.gene_summaries"],
    "bundle": ["research.bundle"],
    "research": ["research.research_parallel"],
    "verify": ["research.verify"],
    "theme": ["gpi.theme_representation"],
    "annotate": ["gpi.evidence_context", "gpi.anthropic_batch", "gpi.parse_results"],
    "presentation": ["gpi.presentation"],
    "html_report": ["gpi.html_report"],
}


def default_config_blocks() -> Dict[str, Dict[str, Any]]:
    """Return the packaged defaults used by ``--emit-config``.

    Keep this independent of the source checkout so a wheel or Claude plugin can build a
    complete config from any working directory.
    """
    return {
        "settings": {
            "n_top_genes": 300,
            "top_loading": 15,
            "top_unique": 8,
            "top_enrichment": 7,
            "genes_per_term": 10,
        },
        "research": {
            "enabled": True,
            "concurrency": 4,
            "model": "claude-sonnet-4-6",
            "max_turns": 30,
            "max_budget_usd": 1.0,
            "per_program_timeout": 600,
        },
        "annotation": {
            "model": "claude-sonnet-4-6",
            "max_tokens": 8192,
            "batch": True,
        },
        "theme": {
            "enabled": True,
            "model": "claude-sonnet-4-6",
            "min_generic_program_count": 4,
        },
        "presentation": {
            "model": "claude-haiku-4-5-20251001",
            "deterministic_fallback": True,
        },
    }

# Steps that must NOT stop the pipeline on failure (spec §8: a failed research
# step degrades — downstream keeps running with literature marked incomplete).
DEGRADABLE_STEPS = {"research"}


class StepError(RuntimeError):
    """Raised when a non-degradable step fails; stops the pipeline."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _resolve(path: Optional[str], base: Path) -> Optional[Path]:
    """Resolve a config path relative to ``base`` (unless absolute). ``None``/blank -> None."""
    if path in (None, "", "null"):
        return None
    p = Path(path)
    return p if p.is_absolute() else (base / p)


@dataclass
class PipelineConfig:
    """Resolved run configuration for one Gene Program Interpreter run."""

    profile: ContextProfile

    # --- Inputs ---
    gene_loading: Path
    regulators: Optional[Path]
    # Condition-keyed regulators (e.g. {"young": path, "aged": path}); feed the
    # gene_summaries regulator_validation_by_condition path (bundle perturbation_regulators).
    regulators_by_condition: Dict[str, Path]
    celltype_enrichment: Optional[Path]

    # --- Output ---
    output_dir: Path

    # --- Program selection (None => all programs in the loading CSV) ---
    programs: Optional[List[int]]

    # --- Deterministic settings block ---
    settings: Dict[str, Any] = field(default_factory=dict)

    # --- Sub-configs (executors 2 & 3) ---
    research: Dict[str, Any] = field(default_factory=dict)
    annotation: Dict[str, Any] = field(default_factory=dict)
    theme: Dict[str, Any] = field(default_factory=dict)
    presentation: Dict[str, Any] = field(default_factory=dict)

    # --- Provenance ---
    raw: Dict[str, Any] = field(default_factory=dict)
    config_path: Optional[Path] = None
    base_dir: Path = field(default_factory=Path.cwd)

    # ---- convenience accessors on the settings block ----
    def setting(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    @property
    def species_taxid(self) -> int:
        return int(self.profile.species_taxid)

    @property
    def programs_arg(self) -> Optional[str]:
        """Comma-joined program ids for CLI ``--topics``/``--programs`` (or None)."""
        if not self.programs:
            return None
        return ",".join(str(p) for p in self.programs)

    def config_hash(self) -> str:
        return ps.compute_config_hash(self.raw)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "PipelineConfig":
        config_path = Path(path).resolve()
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"config {config_path} did not parse to a mapping")

        # Paths in the config are relative to the current working directory
        # (the repo root, per the documented invocation), not to configs/.
        base = Path.cwd()

        context_block = raw.get("context")
        if not isinstance(context_block, dict):
            raise ValueError(
                f"config {config_path} is missing a 'context:' block "
                "(required to build the ContextProfile)."
            )
        profile = ContextProfile.from_dict(context_block)

        inputs = raw.get("inputs", {}) or {}
        gene_loading = _resolve(inputs.get("gene_loading"), base)
        if gene_loading is None:
            raise ValueError(f"config {config_path}: inputs.gene_loading is required.")

        output_dir = _resolve(raw.get("output_dir"), base)
        if output_dir is None:
            raise ValueError(f"config {config_path}: output_dir is required.")

        programs = raw.get("programs")
        if programs is not None:
            programs = [int(p) for p in programs]

        # Condition-keyed regulator files, e.g. inputs.regulators_by_condition:
        #   {young: <path>, aged: <path>}. Each resolves relative to cwd like other inputs.
        reg_by_cond_raw = inputs.get("regulators_by_condition") or {}
        regulators_by_condition = {
            str(cond): _resolve(path, base)
            for cond, path in reg_by_cond_raw.items()
            if _resolve(path, base) is not None
        }

        return cls(
            profile=profile,
            gene_loading=gene_loading,
            regulators=_resolve(inputs.get("regulators"), base),
            regulators_by_condition=regulators_by_condition,
            celltype_enrichment=_resolve(inputs.get("celltype_enrichment"), base),
            output_dir=output_dir,
            programs=programs,
            settings=raw.get("settings", {}) or {},
            research=raw.get("research", {}) or {},
            annotation=raw.get("annotation", {}) or {},
            theme=raw.get("theme", {}) or {},
            presentation=raw.get("presentation", {}) or {},
            raw=raw,
            config_path=config_path,
            base_dir=base,
        )


# ---------------------------------------------------------------------------
# Output layout
# ---------------------------------------------------------------------------


@dataclass
class Paths:
    """All output paths derived from ``output_dir``. Single source of truth so
    every step reads/writes the same locations across resume runs."""

    out: Path

    def __post_init__(self) -> None:
        o = self.out
        self.enrich_dir = o / "string_enrichment"
        self.enrichment_filtered = self.enrich_dir / "enrichment_filtered.csv"
        self.enrichment_full = self.enrich_dir / "enrichment_full.csv"
        self.genes_json = self.enrich_dir / "program_genes.json"
        self.overview_csv = self.enrich_dir / "gene_overview.csv"
        self.figures_dir = self.enrich_dir / "figures"
        # Cell-type enrichment, written by the string_enrichment step when
        # inputs.celltype_enrichment is set. `celltype_detail` is long-format and carries
        # signed log2FC; it is what the annotation prompt consumes. `celltype_summary` is
        # the legacy bucketed table, kept for backwards compatibility.
        self.celltype_summary = self.enrich_dir / "celltype_summary.csv"
        self.celltype_detail = self.enrich_dir / "celltype_detail.csv"

        self.ncbi_context = o / "ncbi_context.json"
        self.ncbi_summary = o / "ncbi_summary.csv"

        self.bundles_dir = o / "program_bundles"
        self.research_dir = o / "research_results"
        self.audit_dir = o / "research_audit"

        self.theme_dir = o / "theme"
        self.theme_dict = o / "theme_dictionary.json"

        self.batch_request = o / "anthropic_batch_request.json"
        # gpi.anthropic_batch submit --wait writes "<stem>_results.jsonl"
        self.batch_results = o / "anthropic_batch_request_results.jsonl"
        self.annotations_dir = o / "annotations"
        self.summary_csv = o / "summary.csv"

        self.presentation_json = o / "presentation.json"
        self.report_html = o / "report.html"

        self.profile_yaml = o / "profile.yaml"
        self.state_path = o / "pipeline_state.json"


@dataclass
class Flags:
    dry_run: bool = False
    no_research: bool = False
    deterministic_presentation: bool = False
    progress: str = "auto"  # auto | rich | plain | off (see gpi.progress)


# ---------------------------------------------------------------------------
# Command helpers
# ---------------------------------------------------------------------------


def _pymod(module: str, *args: Any) -> List[str]:
    """Build a ``python -m <module> ...`` argv, stringifying every arg."""
    return [sys.executable, "-m", module, *[str(a) for a in args]]


def _log_cmd(argv: List[str]) -> None:
    logger.info("  CMD  %s", " ".join(shlex.quote(a) for a in argv))


def _log_call(desc: str) -> None:
    logger.info("  CALL %s", desc)


def _run_subprocess(argv: List[str], dry_run: bool) -> None:
    _log_cmd(argv)
    if dry_run:
        return
    # PYTHONUNBUFFERED=1 so a child step's stdout reaches a redirected log line-by-line instead
    # of sitting in a 4-8 KB block buffer until the step ends. The parent's own progress
    # renderer already flushes; the children were the ones going dark when captured to a file
    # (misdiagnosed in docs/INSTALLATION_ISSUES_SCG.md Issue 5 as a parent-side buffering bug).
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    result = subprocess.run(argv, env=env)
    if result.returncode != 0:
        raise StepError(
            f"subprocess failed (exit {result.returncode}): "
            + " ".join(shlex.quote(a) for a in argv)
        )


def write_profile_yaml(cfg: PipelineConfig, paths: Paths, dry_run: bool) -> Path:
    """Materialize the resolved ContextProfile as ``output_dir/profile.yaml`` (a
    ``context:`` block) so modules that accept ``--profile`` see identical, pinned
    framing. Written eagerly (cheap, deterministic) even on partial runs."""
    if not dry_run:
        paths.out.mkdir(parents=True, exist_ok=True)
        payload = {"context": cfg.profile.resolved().to_dict()}
        paths.profile_yaml.write_text(
            yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
        )
    return paths.profile_yaml


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
#
# Each runner returns a dict of ``info`` for the state file (may be empty). It
# LOGS every command/call it would run and only executes when ``dry_run`` is
# False. Gating (research/theme enabled, --no-research) is decided by the caller
# via ``step_is_gated`` so it shows consistently in both plan and real runs.


def _topics_args(cfg: PipelineConfig) -> List[str]:
    return ["--topics", cfg.programs_arg] if cfg.programs_arg else []


def run_string_enrichment(cfg: PipelineConfig, paths: Paths, flags: Flags) -> Dict[str, Any]:
    argv = _pymod(
        "gpi.enrichment", "all",
        "--input", cfg.gene_loading,
        "--n-top", cfg.setting("n_top_genes", 300),
        "--species", cfg.species_taxid,
        "--json-out", paths.genes_json,
        "--csv-out", paths.overview_csv,
        "--out-csv-full", paths.enrichment_full,
        "--out-csv-filtered", paths.enrichment_filtered,
        "--figures-dir", paths.figures_dir,
        *_topics_args(cfg),
    )
    if cfg.celltype_enrichment:
        argv += ["--celltype-enrichment", str(cfg.celltype_enrichment)]
    _run_subprocess(argv, flags.dry_run)
    return {"enrichment_filtered": str(paths.enrichment_filtered)}


def run_gene_summaries(cfg: PipelineConfig, paths: Paths, flags: Flags) -> Dict[str, Any]:
    argv = _pymod(
        "gpi.gene_summaries",
        "--input", cfg.gene_loading,
        "--json-out", paths.ncbi_context,
        "--csv-out", paths.ncbi_summary,
        "--keyword", cfg.profile.resolved_keyword_query(),
        "--species", cfg.species_taxid,
        "--top-loading", cfg.setting("top_loading", 15),
        "--top-unique", cfg.setting("top_unique", 8),
        *_topics_args(cfg),
    )
    if cfg.regulators:
        argv += ["--regulator-file", str(cfg.regulators)]
    # Condition-keyed regulators -> regulator_validation_by_condition (bundle
    # perturbation_regulators). One repeatable --regulator-condition-file cond=path.
    for cond, path in cfg.regulators_by_condition.items():
        argv += ["--regulator-condition-file", f"{cond}={path}"]
    # PubTator stays OFF by default (no --use-pubtator).
    _run_subprocess(argv, flags.dry_run)
    return {"ncbi_context": str(paths.ncbi_context)}


def run_bundle(cfg: PipelineConfig, paths: Paths, flags: Flags) -> Dict[str, Any]:
    enrichment_csv = paths.enrichment_filtered if paths.enrichment_filtered.exists() else None
    ncbi_json = paths.ncbi_context if paths.ncbi_context.exists() else None
    _log_call(
        "research.bundle.build_all_bundles("
        f"gene_loading_csv={cfg.gene_loading}, profile=<{cfg.profile.resolved_annotation_role()}>, "
        f"enrichment_csv={enrichment_csv}, ncbi_context_json={ncbi_json}, "
        f"out_dir={paths.bundles_dir}, program_ids={cfg.programs}, "
        f"top_loading={cfg.setting('top_loading', 15)}, "
        f"top_enrichment={cfg.setting('top_enrichment', 7)})"
    )
    if flags.dry_run:
        return {}
    from research.bundle import build_all_bundles

    written = build_all_bundles(
        cfg.gene_loading,
        cfg.profile,
        enrichment_csv=enrichment_csv,
        ncbi_context_json=ncbi_json,
        out_dir=paths.bundles_dir,
        program_ids=cfg.programs,
        top_loading=int(cfg.setting("top_loading", 15)),
        top_enrichment=int(cfg.setting("top_enrichment", 7)),
    )
    return {"n_bundles": len(written), "bundles": [str(p) for p in written]}


def run_research(cfg: PipelineConfig, paths: Paths, flags: Flags) -> Dict[str, Any]:
    bundle_paths = sorted(paths.bundles_dir.glob("*.json"))
    _log_call(
        "asyncio.run(research.research_parallel.run_research("
        f"bundle_paths=<{len(bundle_paths)} bundles>, out_dir={paths.research_dir}, "
        f"audit_dir={paths.audit_dir}, concurrency={cfg.research.get('concurrency', 4)}, "
        f"model={cfg.research.get('model')}, max_turns={cfg.research.get('max_turns', 30)}, "
        f"max_budget_usd={cfg.research.get('max_budget_usd', 1.0)}, "
        f"per_program_timeout={cfg.research.get('per_program_timeout', 600)}))  "
        "[LIVE Agent SDK; in-process literature MCP; spends money]"
    )
    if flags.dry_run:
        return {}
    if not bundle_paths:
        raise StepError(
            f"research: no program bundles found in {paths.bundles_dir}; run the bundle step first."
        )
    # Lazy import: research_parallel imports claude_agent_sdk at module load, which
    # is only installed when actually doing research.
    from research.research_parallel import run_research as _run_research

    # Auth split: the research subagents run the local ``claude`` CLI on the user's
    # Claude.ai subscription (``research.auth: subscription``, the default), NOT API credit.
    # The CLI uses the subscription only when ANTHROPIC_API_KEY is absent from its
    # environment, so withhold the key for the duration of the research step, then restore
    # it for the API-billed batch steps (theme/annotate/presentation). Set
    # ``research.auth: api`` to bill research to the API key instead.
    auth = str(cfg.research.get("auth", "subscription")).lower()
    saved_key: Optional[str] = None
    if auth != "api":
        saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)
        if saved_key is not None:
            logger.info(
                "research: using Claude.ai subscription for the Agent-SDK CLI "
                "(ANTHROPIC_API_KEY withheld; restored afterward for batch steps)."
            )
    # Bridge live per-agent progress up to the pipeline emitter (opaque asyncio.run otherwise).
    emitter = get_emitter()
    concurrency = int(cfg.research.get("concurrency", 4))
    if emitter is not None:
        emitter.emit(RESEARCH_START, {"n_programs": len(bundle_paths),
                                      "concurrency": concurrency, "auth": auth})
    try:
        written = asyncio.run(
            _run_research(
                bundle_paths,
                out_dir=paths.research_dir,
                audit_dir=paths.audit_dir,
                concurrency=concurrency,
                model=cfg.research.get("model", "claude-sonnet-4-6"),
                max_turns=int(cfg.research.get("max_turns", 30)),
                max_budget_usd=float(cfg.research.get("max_budget_usd", 1.0)),
                per_program_timeout=float(cfg.research.get("per_program_timeout", 600)),
                progress_cb=(emitter.emit if emitter is not None else None),
            )
        )
    finally:
        if saved_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved_key
    if emitter is not None:
        emitter.emit(RESEARCH_DONE, {"n_results": len(written)})
    return {"n_results": len(written)}


def run_verify(cfg: PipelineConfig, paths: Paths, flags: Flags) -> Dict[str, Any]:
    _log_call(
        f"research.verify.verify_directory(directory={paths.research_dir}, "
        f"audit_dir={paths.audit_dir})  [a few keyless CrossRef/NCBI calls]"
    )
    if flags.dry_run:
        return {}
    if not paths.research_dir.is_dir() or not any(paths.research_dir.glob("*.json")):
        logger.warning(
            "verify: no research results in %s — skipping (literature incomplete).",
            paths.research_dir,
        )
        return {"skipped": True, "reason": "no research results"}
    from research.verify import verify_directory

    summary = verify_directory(paths.research_dir, audit_dir=paths.audit_dir)

    # Say out loud when verification did not fully run. The whole point of this step is the
    # promise that no unverified citation reaches the report; a run that quietly could not keep
    # that promise must not look like one that did. Unreachable citations are KEPT (and marked
    # 'partial' in the report) rather than dropped — a network failure is not evidence a paper
    # is fake — so this is a warning, not an error.
    if summary.get("verification_complete") is False:
        logger.warning(
            "verify: INCOMPLETE — %s of %s citation(s) could not be checked%s. They are kept "
            "and marked unverified in the report, not treated as fabricated. Summary: %s",
            summary.get("n_unverified"), summary.get("n_citations"),
            f", {summary['n_files_skipped']} result file(s) skipped"
            if summary.get("n_files_skipped") else "",
            summary.get("verification_summary"),
        )
    return {
        "n_programs": summary.get("n_programs"),
        "audit_dir": summary.get("audit_dir"),
        "verification_complete": summary.get("verification_complete"),
        "n_unverified": summary.get("n_unverified"),
    }


def run_theme(cfg: PipelineConfig, paths: Paths, flags: Flags) -> Dict[str, Any]:
    argv = _pymod(
        "gpi.theme_representation",
        "--gene-file", cfg.gene_loading,
        "--species", cfg.species_taxid,
        "--top-loading", cfg.setting("top_loading", 15),
        "--top-unique", cfg.setting("top_unique", 8),
        "--top-enrichment", cfg.setting("top_enrichment", 7),
        "--enrichment-gene-count", cfg.setting("n_top_genes", 300),
        "--min-generic-program-count", cfg.theme.get("min_generic_program_count", 4),
        "--llm-backend", "anthropic",
        "--llm-model", cfg.theme.get("model", "claude-sonnet-4-6"),
        "--evidence-source-dir", paths.out,
        "--evidence-pack-output", paths.theme_dir / "evidence_pack.json",
        "--prompt-output", paths.theme_dir / "theme_prompt.md",
        "--extraction-response-output", paths.theme_dir / "theme_response.json",
        "--output-json", paths.theme_dict,
        "--output-csv", paths.theme_dir / "theme_dictionary.csv",
        *_topics_args(cfg),
    )
    if paths.ncbi_context.exists():
        argv += ["--ncbi-file", str(paths.ncbi_context)]
    if paths.enrichment_filtered.exists():
        argv += ["--enrichment-file", str(paths.enrichment_filtered)]
    if cfg.regulators:
        argv += ["--regulator-file", str(cfg.regulators)]
    if paths.research_dir.exists():
        argv += ["--research-evidence-dir", str(paths.research_dir)]
    _run_subprocess(argv, flags.dry_run)
    return {"theme_dictionary": str(paths.theme_dict)}


def run_annotate(cfg: PipelineConfig, paths: Paths, flags: Flags) -> Dict[str, Any]:
    # (a) assemble one annotation request per program (evidence_context prepare)
    profile_yaml = write_profile_yaml(cfg, paths, flags.dry_run)
    prepare = _pymod(
        "gpi.evidence_context", "prepare",
        "--gene-file", cfg.gene_loading,
        "--profile", profile_yaml,
        "--model", cfg.annotation.get("model", "claude-sonnet-4-6"),
        "--max-tokens", cfg.annotation.get("max_tokens", 8192),
        "--research-evidence-dir", paths.research_dir,
        "--top-loading", cfg.setting("top_loading", 15),
        "--top-unique", cfg.setting("top_unique", 8),
        "--top-enrichment", cfg.setting("top_enrichment", 7),
        "--genes-per-term", cfg.setting("genes_per_term", 10),
        "--output-file", paths.batch_request,
        *_topics_args(cfg),
    )
    if paths.enrichment_filtered.exists():
        prepare += ["--enrichment-file", str(paths.enrichment_filtered)]
    # Cell-type enrichment -> the annotation prompt's "Cell-type enrichment" section. Point
    # at the file, not the directory: --celltype-dir would also match a stale legacy summary
    # left behind by an earlier run, silently preferring it over the current detail table.
    if paths.celltype_detail.exists():
        prepare += ["--celltype-file", str(paths.celltype_detail)]
    elif paths.celltype_summary.exists():
        prepare += ["--celltype-file", str(paths.celltype_summary)]
    if paths.ncbi_context.exists():
        prepare += ["--ncbi-file", str(paths.ncbi_context)]
    if paths.theme_dict.exists():
        prepare += ["--theme-dictionary-file", str(paths.theme_dict)]
    if cfg.regulators:
        prepare += ["--regulator-file", str(cfg.regulators)]
    # Condition-keyed regulators -> the annotation prompt's "Regulator perturbation
    # evidence" section (evidence_context supports --regulator-condition-file cond=path).
    for cond, path in cfg.regulators_by_condition.items():
        prepare += ["--regulator-condition-file", f"{cond}={path}"]
    # Mask promiscuous, non-program-specific regulators from the annotation regulator
    # evidence (annotation.mask_regulators in the config), both conditions.
    for gene in cfg.annotation.get("mask_regulators", []) or []:
        prepare += ["--mask-regulator", str(gene)]
    _run_subprocess(prepare, flags.dry_run)

    # (b) submit the batch and wait for results (Anthropic Batch API — spends money)
    submit = _pymod(
        "gpi.anthropic_batch", "submit", paths.batch_request,
        "--model", cfg.annotation.get("model", "claude-sonnet-4-6"),
        "--max-tokens", cfg.annotation.get("max_tokens", 8192),
        "--wait",
    )
    _run_subprocess(submit, flags.dry_run)

    # (c) parse results -> per-topic markdown + summary CSV (uses parse_final_results)
    parse = _pymod(
        "gpi.parse_results",
        "--results-jsonl", paths.batch_results,
        "--markdown-dir", paths.annotations_dir,
        "--summary-csv", paths.summary_csv,
        "--gene-loading-file", cfg.gene_loading,
    )
    _run_subprocess(parse, flags.dry_run)
    return {"annotations_dir": str(paths.annotations_dir), "summary_csv": str(paths.summary_csv)}


def run_presentation(cfg: PipelineConfig, paths: Paths, flags: Flags) -> Dict[str, Any]:
    argv = _pymod(
        "gpi.presentation",
        "--annotations-dir", paths.annotations_dir,
        "--out", paths.presentation_json,
        "--model", cfg.presentation.get("model", ""),
    )
    lexicon = cfg.base_dir / "configs" / "presentation_lexicon.json"
    if lexicon.exists():
        argv += ["--lexicon", str(lexicon)]
    # CLI --deterministic-presentation forces the ④ deterministic renderer (no LLM).
    if flags.deterministic_presentation:
        argv += ["--deterministic"]
    _run_subprocess(argv, flags.dry_run)
    return {"presentation_json": str(paths.presentation_json)}


def run_html_report(cfg: PipelineConfig, paths: Paths, flags: Flags) -> Dict[str, Any]:
    argv = _pymod(
        "gpi.html_report",
        "--summary-csv", paths.summary_csv,
        "--annotations-dir", paths.annotations_dir,
        "--enrichment-dir", paths.figures_dir,
        "--gene-loading-csv", cfg.gene_loading,
        "--output-html", paths.report_html,
        "--dataset-crumb", cfg.profile.resolved_report_dataset_crumb(),
        "--top-loading", cfg.setting("top_loading", 15),
        "--top-unique", cfg.setting("top_unique", 8),
    )
    # Deterministic pathway list + "Top pathway" chip come from the STRING
    # enrichment CSV (enrichment was dropped from the annotation LLM output).
    if paths.enrichment_filtered.exists():
        argv += ["--enrichment-filtered-csv", str(paths.enrichment_filtered)]
    # Cell-type enrichment comes from the CSV, not from the annotation text: the model is
    # never asked to restate it, so the report's old regex only ever matched nothing.
    if paths.celltype_detail.exists():
        argv += ["--celltype-file", str(paths.celltype_detail)]
    if paths.presentation_json.exists():
        argv += ["--presentation-json", str(paths.presentation_json)]
    if paths.research_dir.is_dir():
        argv += ["--research-results-dir", str(paths.research_dir)]
    if cfg.regulators:
        argv += ["--volcano-csv", str(cfg.regulators)]
    # Per-condition regulator matrices drive the interactive perturbation-effect
    # plots (one panel per condition). Without these the section renders empty.
    for cond, path in cfg.regulators_by_condition.items():
        argv += ["--volcano-condition-csv", f"{cond}={path}"]
    _run_subprocess(argv, flags.dry_run)
    return {"report_html": str(paths.report_html)}


STEP_RUNNERS: Dict[str, Callable[[PipelineConfig, Paths, Flags], Dict[str, Any]]] = {
    "string_enrichment": run_string_enrichment,
    "gene_summaries": run_gene_summaries,
    "bundle": run_bundle,
    "research": run_research,
    "verify": run_verify,
    "theme": run_theme,
    "annotate": run_annotate,
    "presentation": run_presentation,
    "html_report": run_html_report,
}


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def step_is_gated(step: str, cfg: PipelineConfig, flags: Flags) -> Optional[str]:
    """Return a human reason string if ``step`` should be skipped for this run
    (disabled in config or by a CLI flag), else None."""
    if step == "research":
        if flags.no_research:
            return "--no-research"
        if not cfg.research.get("enabled", True):
            return "research.enabled=false"
    if step == "theme" and not cfg.theme.get("enabled", True):
        return "theme.enabled=false"
    return None


def preflight_imports(
    active_steps: List[str],
    cfg: PipelineConfig,
    flags: Flags,
    emitter: Optional[Any] = None,
) -> None:
    """Import every module the planned steps need, before any paid work starts.

    A missing module used to surface only when the step that needed it ran — for the
    verifier, that was *after* the research step had already been paid for, and the failure
    was reported as a bad agent payload rather than a broken install. Importing here turns a
    packaging regression into a $0 failure at startup with an honest error message.

    This is also the pipeline's longest silence: on a cold machine Python compiles the Agent
    SDK (~77 MB) and its transitive deps to bytecode here, which can take minutes on a shared
    filesystem while nothing else has happened yet. So when an ``emitter`` is given we surface
    it as a ``preflight`` step with a per-module counter — the difference between "hung" and
    "importing 7/12 research.research_parallel". ``preflight`` is deliberately NOT in
    ``STEP_ORDER`` or ``pipeline_state``: it is pure startup, never resumed or skipped.
    """
    targets = [
        (step, module)
        for step in active_steps
        if not step_is_gated(step, cfg, flags)
        for module in STEP_MODULES.get(step, [])
    ]
    total = len(targets)
    if emitter is not None:
        emitter.emit(STEP_START, {"step": "preflight", "executor": "import"})
    broken: List[str] = []
    for i, (step, module) in enumerate(targets, start=1):
        # Emit BEFORE the import: the import is the slow part, so this names the module that is
        # compiling right now, not the one that just finished.
        if emitter is not None:
            emitter.emit(STEP_PROGRESS,
                         {"step": "preflight", "current": i, "total": total, "detail": module})
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 — any import failure is fatal here
            broken.append(f"  {step:<17} needs {module}\n      {type(exc).__name__}: {exc}")
    if broken:
        if emitter is not None:
            emitter.emit(STEP_DONE, {"step": "preflight", "status": "failed",
                                     "error": "; ".join(b.split("\n")[-1].strip() for b in broken)})
        raise SystemExit(
            "Install is incomplete — these modules could not be imported:\n\n"
            + "\n".join(broken)
            + "\n\nNo paid work was started. This usually means the package was built without a "
            "file it needs at runtime.\nReinstall the plugin; if it persists, please report it "
            "with the error above."
        )
    if emitter is not None:
        emitter.emit(STEP_DONE, {"step": "preflight", "status": "completed"})


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _fresh_state(cfg: PipelineConfig) -> ps.PipelineState:
    state = ps.init_state(cfg.config_hash(), cfg.programs)
    # Replace the legacy step set with our canonical order.
    state.steps = {name: ps.StepState() for name in STEP_ORDER}
    return state


def _load_or_init_state(cfg: PipelineConfig, paths: Paths, force_restart: bool) -> ps.PipelineState:
    existing = ps.load_state(paths.state_path)
    if existing is None or force_restart:
        if force_restart and existing is not None:
            logger.info("Forcing a fresh run (--force-restart): resetting pipeline state.")
        return _fresh_state(cfg)

    current_hash = cfg.config_hash()
    if existing.config_hash != current_hash:
        logger.warning(
            "Config changed since last run (hash %s -> %s); starting fresh state.",
            existing.config_hash[:12], current_hash[:12],
        )
        return _fresh_state(cfg)

    # Ensure every canonical step has an entry (older/legacy state files).
    for name in STEP_ORDER:
        existing.steps.setdefault(name, ps.StepState())
    return existing


def _slice_steps(start_from: Optional[str], stop_after: Optional[str]) -> List[str]:
    steps = list(STEP_ORDER)
    if start_from:
        if start_from not in STEP_ORDER:
            raise ValueError(f"--start-from '{start_from}' is not a known step {STEP_ORDER}")
        steps = steps[STEP_ORDER.index(start_from):]
    if stop_after:
        if stop_after not in STEP_ORDER:
            raise ValueError(f"--stop-after '{stop_after}' is not a known step {STEP_ORDER}")
        # keep steps up to and including stop_after (from the already-sliced head)
        if stop_after in steps:
            steps = steps[: steps.index(stop_after) + 1]
        else:
            raise ValueError(
                f"--stop-after '{stop_after}' precedes --start-from '{start_from}'."
            )
    return steps


# ---------------------------------------------------------------------------
# Plan / dry-run
# ---------------------------------------------------------------------------


def _print_framing(cfg: PipelineConfig) -> None:
    p = cfg.profile
    print("Resolved ContextProfile framing:")
    print(f"  organism / taxid : {p.organism} / {p.species_taxid}")
    print(f"  tissue           : {p.tissue or '(none)'}")
    print(f"  cell_type        : {p.cell_type or '(none)'}")
    print(f"  conditions       : {', '.join(p.conditions) or '(none)'}")
    print(f"  annotation_role  : {p.resolved_annotation_role()}")
    print(f"  annotation_ctx   : {p.resolved_annotation_context()}")
    print(f"  keyword_query    : {p.resolved_keyword_query()}")
    print(f"  condition_context: {p.resolved_condition_context()}")
    print(f"  report_crumb     : {p.resolved_report_dataset_crumb()}")
    # The keyword_query above is sent to PubMed literally, so a badly-shaped context_term
    # becomes a slot that matches nothing. Surface that here — this is the last free moment
    # before the user approves a paid run.
    warnings = p.validate()
    if warnings:
        print("\nContext warnings (the run will still work — these degrade research quality):")
        for w in warnings:
            print(f"  ! {w}")


def print_plan(
    cfg: PipelineConfig,
    paths: Paths,
    flags: Flags,
    active_steps: List[str],
    state: ps.PipelineState,
) -> None:
    def _fmt_input(label: str, p: Any) -> str:
        if p is None:
            return f"{label:<20}: (none)"
        pth = Path(p)
        mark = "✓" if pth.exists() else "✗ MISSING"
        return f"{label:<20}: {pth.resolve()}  [{mark}]"

    print("=" * 78)
    print("GENE PROGRAM INTERPRETER — resolved run plan (DRY RUN, nothing executed)")
    print("=" * 78)
    print(f"{'config':<20}: {Path(cfg.config_path).resolve() if cfg.config_path else '(none)'}")
    print(f"{'output_dir':<20}: {paths.out.resolve()}")
    # Every input path the run will read, absolute and existence-checked — including the two
    # (celltype_enrichment, regulators_by_condition) the old header omitted, which are exactly
    # the ones most likely to be silently wrong.
    print(_fmt_input("gene_loading", cfg.gene_loading))
    print(_fmt_input("regulators", cfg.regulators))
    for cond, path in (cfg.regulators_by_condition or {}).items():
        print(_fmt_input(f"regulators[{cond}]", path))
    print(_fmt_input("celltype_enrichment", getattr(cfg, "celltype_enrichment", None)))
    print(f"{'programs':<20}: {cfg.programs if cfg.programs is not None else 'ALL'}")
    print(f"{'config_hash':<20}: {cfg.config_hash()[:16]}")
    # Credentials: which .env is in effect, and which keys are actually present. This is the
    # confirmation screen, so surface what a paid run depends on before it is authorized.
    env_path = next((c for c in env_search_candidates() if c.exists()), None)
    print(f"{'.env':<20}: {env_path or '(none found — keys must be in the environment)'}")
    cred = " · ".join(
        f"{k} {'✓' if os.environ.get(k) else '✗'}"
        for k in ("ANTHROPIC_API_KEY", "PUBMED_EMAIL", "OPENALEX_API_KEY", "NCBI_API_KEY")
    )
    print(f"{'credentials':<20}: {cred}")
    print("-" * 78)
    _print_framing(cfg)
    print("-" * 78)
    print(f"Steps to run (order; executor code): {active_steps}")
    print("-" * 78)
    for step in active_steps:
        reason = step_is_gated(step, cfg, flags)
        status = state.steps.get(step, ps.StepState()).status
        header = f"[{STEP_EXECUTOR.get(step, '?')}] {step}"
        if reason:
            print(f"\n### {header}  — SKIP ({reason}; downstream degrades)")
            continue
        if status == "completed":
            print(f"\n### {header}  — already completed (would skip on resume)")
        else:
            print(f"\n### {header}")
        STEP_RUNNERS[step](cfg, paths, flags)  # dry_run=True => only logs commands
    print("\n" + "=" * 78)
    print("End of plan. No commands were executed; no API or MCP calls were made.")
    print("=" * 78)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run_pipeline(
    cfg: PipelineConfig,
    flags: Flags,
    *,
    start_from: Optional[str] = None,
    stop_after: Optional[str] = None,
    force_restart: bool = False,
) -> int:
    # Load the repo .env FIRST so every step (including a resume that starts after the
    # research step) inherits ANTHROPIC_API_KEY / NCBI_API_KEY / OPENALEX_API_KEY.
    load_env_file()
    paths = Paths(cfg.output_dir)
    active_steps = _slice_steps(start_from, stop_after)
    state = _load_or_init_state(cfg, paths, force_restart)

    if flags.dry_run:
        print_plan(cfg, paths, flags, active_steps, state)
        return 0

    # Keep API keys out of the logs: httpx logs every request URL at INFO, which put
    # NCBI_API_KEY in cleartext dozens of times per run. Install before anything makes a call.
    install_log_redaction()

    # Create the output dir and progress emitter BEFORE importing the step modules. The import
    # phase is the pipeline's longest cold-start silence (compiling the Agent SDK to bytecode),
    # and it used to run against an empty run directory with nothing to poll — which reads
    # exactly like a hang. Now it is a visible 'preflight' step from the first second.
    paths.out.mkdir(parents=True, exist_ok=True)

    # Progress telemetry (opt-in via --progress; 'off' → zero-overhead no-op). The emitter
    # owns a background tailer that renders a live view to the terminal (rich on a TTY, clean
    # ASCII otherwise) and writes progress.json for the Claude skill to poll. set_emitter()
    # exposes it to the research step (executor ②) so per-agent events flow to the same log.
    n_steps = len(active_steps)
    emitter = make_emitter(paths.out, mode=flags.progress, stdout=sys.stdout)
    set_emitter(emitter)
    # Subprocess step modules (enrichment, gene_summaries, anthropic_batch) emit sub-progress
    # into the same log via env the children inherit; per-step name is set in the loop.
    if flags.progress != "off":
        os.environ["GPI_PROGRESS_JSON"] = str(paths.out / "progress.jsonl")
    else:
        os.environ.pop("GPI_PROGRESS_JSON", None)
    # 'preflight' leads the advertised step list but is not one of the STEP_ORDER steps.
    emitter.emit(RUN_START, {"run_id": paths.out.name,
                             "steps": ["preflight"] + list(active_steps),
                             "n_steps": n_steps + 1})

    final_status = "done"
    try:
        # Fail a broken install at $0, before the first paid step — reported as the preflight
        # step. Inside the try so a failure still emits RUN_DONE and flushes the final snapshot
        # (close() drains the log), leaving the failed preflight visible to a monitor.
        try:
            preflight_imports(active_steps, cfg, flags, emitter=emitter)
        except SystemExit:
            final_status = "failed"
            raise

        # Always write the resolved profile so --profile threading is available.
        write_profile_yaml(cfg, paths, dry_run=False)
        ps.save_state(paths.state_path, state)
        logger.info("Run plan: %s", active_steps)
        for index, step in enumerate(active_steps, start=1):
            executor = STEP_EXECUTOR.get(step, "?")
            os.environ["GPI_PROGRESS_STEP"] = step  # subprocess sub-progress → this step
            reason = step_is_gated(step, cfg, flags)
            if reason:
                logger.info("[%s] SKIP (%s) — downstream degrades.", step, reason)
                ps.mark_step(state, step, "skipped", {"reason": reason})
                ps.save_state(paths.state_path, state)
                emitter.emit(STEP_DONE, {"step": step, "status": "skipped"})
                continue

            if not force_restart and state.steps.get(step, ps.StepState()).status == "completed":
                logger.info("[%s] already completed — skipping (resume).", step)
                # Show it as done in the progress view even though it isn't re-run.
                emitter.emit(STEP_START, {"step": step, "executor": executor,
                                          "index": index, "n_steps": n_steps})
                emitter.emit(STEP_DONE, {"step": step, "status": "completed"})
                continue

            logger.info("[%s] starting (executor %s)...", step, executor)
            emitter.emit(STEP_START, {"step": step, "executor": executor,
                                      "index": index, "n_steps": n_steps})
            ps.mark_step(state, step, "in_progress")
            ps.save_state(paths.state_path, state)
            try:
                info = STEP_RUNNERS[step](cfg, paths, flags) or {}
            except Exception as exc:  # noqa: BLE001 — report loudly, decide degrade vs stop
                ps.mark_step(state, step, "failed", {"error": str(exc)})
                ps.save_state(paths.state_path, state)
                emitter.emit(STEP_DONE, {"step": step, "status": "failed", "error": str(exc)})
                if step in DEGRADABLE_STEPS:
                    logger.error(
                        "[%s] FAILED but is degradable — continuing with literature "
                        "marked incomplete. Error: %s", step, exc,
                    )
                    continue
                logger.error("[%s] FAILED — stopping pipeline. Error: %s", step, exc)
                final_status = "failed"
                return 1
            ps.mark_step(state, step, "completed", info)
            ps.save_state(paths.state_path, state)
            emitter.emit(STEP_DONE, {"step": step, "status": "completed"})
            logger.info("[%s] completed.", step)

        logger.info("Pipeline finished. State: %s", paths.state_path)
        return 0
    finally:
        emitter.emit(RUN_DONE, {"status": final_status})
        emitter.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gpi",
        description="Config-driven orchestrator for the Gene Program Interpreter.",
        epilog="Run 'gpi doctor' to check the Claude login and project configuration.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", help="Run config YAML (see configs/example_generic.yaml). Required to run the pipeline.")
    # --- Pre-flight helpers (read-only; do not run the pipeline) ---
    parser.add_argument(
        "--check-inputs", action="store_true",
        help="Validate the input CSV(s) (column mapping, program count, row count) and exit. "
             "Reads paths from --config or the direct --gene-loading/--regulators flags.",
    )
    parser.add_argument(
        "--emit-config", action="store_true",
        help="Assemble a complete run config from a context stub (--context-file) + input paths, "
             "print the resolved framing, and write/print the config. Does not run the pipeline.",
    )
    parser.add_argument("--context-file", help="[--emit-config] Context-only YAML stub (a bare or nested `context:` block).")
    parser.add_argument("--gene-loading", help="[--check-inputs/--emit-config] Gene-loading CSV path.")
    parser.add_argument("--regulators", help="[--check-inputs/--emit-config] Single regulator CSV path.")
    parser.add_argument("--regulators-by-condition", action="append", default=None,
                        help="[--emit-config] Condition-keyed regulator file as cond=path (repeatable).")
    parser.add_argument("--celltype-enrichment",
                        help="[--check-inputs/--emit-config] Cell-type enrichment CSV path.")
    parser.add_argument("--output-dir", help="[--emit-config] output_dir to write into the config.")
    parser.add_argument("--programs", help="[--emit-config] Comma-separated program ids (default: all).")
    parser.add_argument("-o", "--output", help="[--emit-config] Write the config here (default: stdout).")
    parser.add_argument("--start-from", help=f"Begin at this step {STEP_ORDER}.")
    parser.add_argument("--stop-after", help="Stop after this step (inclusive).")
    parser.add_argument(
        "--no-research", action="store_true",
        help="Skip the ② research step (no Agent SDK / MCP / spend); literature marked incomplete.",
    )
    parser.add_argument(
        "--deterministic-presentation", action="store_true",
        help="Force the deterministic (④) presentation renderer instead of the ③ LLM batch.",
    )
    parser.add_argument(
        "--force-restart", action="store_true",
        help="Ignore any saved state and re-run every step from scratch.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the resolved plan (framing + per-step commands) WITHOUT executing.",
    )
    parser.add_argument(
        "--progress", choices=["auto", "rich", "plain", "off"], default="auto",
        help="Live progress view: 'auto' (rich on a TTY, clean ASCII when piped/captured), "
             "'rich', 'plain' (one ASCII line per event), or 'off' (no progress files/output).",
    )
    parser.add_argument(
        "--no-progress", dest="progress", action="store_const", const="off",
        help="Alias for --progress off.",
    )
    parser.add_argument("--verbose", action="store_true", help="Debug-level logging.")
    return parser


def _parse_cond_args(values: Optional[List[str]]) -> Dict[str, str]:
    """Parse repeated ``cond=path`` args into an ordered {cond: path} dict."""
    out: Dict[str, str] = {}
    for v in values or []:
        if "=" not in v:
            raise SystemExit(f"--regulators-by-condition expects cond=path, got {v!r}")
        cond, path = v.split("=", 1)
        out[cond.strip()] = path.strip()
    return out


def cmd_check_inputs(args: argparse.Namespace) -> int:
    """Read-only pre-flight: map columns + count programs/rows for the input CSV(s). Spends
    nothing. Reuses gpi.column_mapper so the alias table and error messages match the pipeline."""
    import pandas as pd

    from .column_mapper import (
        extract_program_id,
        standardize_celltype_enrichment,
        standardize_gene_loading,
        standardize_regulator_results,
    )

    gene_loading = args.gene_loading
    regulators = args.regulators
    celltype = args.celltype_enrichment
    if args.config:
        cfg = PipelineConfig.from_yaml(args.config)
        gene_loading = gene_loading or str(cfg.gene_loading)
        regulators = regulators or (str(cfg.regulators) if cfg.regulators else None)
        celltype = celltype or (str(cfg.celltype_enrichment) if cfg.celltype_enrichment else None)

    print("Input pre-flight (read-only; nothing is spent):")
    ok = True
    if not gene_loading:
        print("  ✗ no gene-loading CSV provided (use --gene-loading or --config).")
        return 2
    try:
        df = pd.read_csv(gene_loading)
        std = standardize_gene_loading(df)
        pids = sorted({p for p in (extract_program_id(v) for v in std["program_id"]) if p is not None})
        preview = pids[:10] + (["..."] if len(pids) > 10 else [])
        print(f"  ✓ gene loading: {gene_loading}")
        print(f"      columns {list(df.columns)} → Name/Score/program_id")
        print(f"      programs: {len(pids)}  {preview}")
        print(f"      rows: {len(df)}")
    except Exception as exc:  # noqa: BLE001 — surface the mapper's found-vs-expected message
        print(f"  ✗ gene loading: {gene_loading}\n      {exc}")
        ok = False

    # Sniff the separator for the two inputs whose *pipeline* readers sniff it
    # (evidence_context, gene_summaries, html_report all use sep=None). Reading them strictly
    # here made pre-flight reject tab-separated files the pipeline handles perfectly well —
    # pre-flight must predict the run, not impose a stricter rule than it. Gene loading is
    # deliberately left strict: gpi.enrichment reads it comma-only, so sniffing here would
    # turn a false failure into the far worse false pass.
    if regulators:
        try:
            rdf = standardize_regulator_results(pd.read_csv(regulators, sep=None, engine="python"))
            sig = int(rdf["significant"].sum()) if "significant" in rdf.columns else "n/a"
            print(f"  ✓ regulators: {regulators}  (rows: {len(rdf)}, significant: {sig})")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ regulators: {regulators}\n      {exc}")
            ok = False

    if celltype:
        try:
            cdf = standardize_celltype_enrichment(
                pd.read_csv(celltype, sep=None, engine="python")
            )
            print(f"  ✓ cell-type enrichment: {celltype}  (rows: {len(cdf)})")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ cell-type enrichment: {celltype}\n      {exc}")
            ok = False

    print("\nPre-flight OK — safe to build a config and run." if ok
          else "\nPre-flight FAILED — fix the inputs above before running.")
    return 0 if ok else 1


def cmd_emit_config(args: argparse.Namespace) -> int:
    """Assemble a complete, schema-correct run config from a context-only stub + input paths.

    The `context:` block comes from the stub (via ContextProfile, so the derived framing is
    consistent); the other blocks use packaged defaults. Also prints the auto-derived framing
    so the user reviews what the research agents will actually see."""
    if not args.context_file:
        raise SystemExit("--emit-config requires --context-file (a context-only YAML stub).")
    if not args.gene_loading or not args.output_dir:
        raise SystemExit("--emit-config requires --gene-loading and --output-dir.")

    profile = ContextProfile.from_yaml(Path(args.context_file))
    defaults = default_config_blocks()

    inputs: Dict[str, Any] = {"gene_loading": args.gene_loading}
    if args.regulators:
        inputs["regulators"] = args.regulators
    cond = _parse_cond_args(args.regulators_by_condition)
    if cond:
        inputs["regulators_by_condition"] = cond
    inputs["celltype_enrichment"] = args.celltype_enrichment

    programs: Optional[List[int]] = None
    if args.programs:
        programs = [int(x) for x in str(args.programs).split(",") if x.strip()]

    config = {
        "context": {
            "organism": profile.organism,
            "species_taxid": profile.species_taxid,
            "tissue": profile.tissue,
            "cell_type": profile.cell_type,
            "conditions": list(profile.conditions),
            "context_terms": list(profile.context_terms),
            "assay": profile.assay,
        },
        "inputs": inputs,
        "output_dir": args.output_dir,
        "programs": programs,
        **defaults,
    }
    rendered = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print(f"Wrote config: {args.output}")
    else:
        print(rendered)

    print("Resolved framing (auto-derived from the context — this is what the pipeline uses):")
    print(f"  annotation_role  : {profile.resolved_annotation_role()}")
    print(f"  keyword_query    : {profile.resolved_keyword_query()}")
    print(f"  condition_context: {profile.resolved_condition_context()}")
    print(f"  report crumb     : {profile.resolved_report_dataset_crumb()}")
    return 0


def cmd_doctor(argv: Optional[List[str]] = None) -> int:
    """Check the local runtime and credentials without making network or paid calls."""
    parser = argparse.ArgumentParser(
        prog="gpi doctor",
        description="Check whether Gene Program Interpreter is ready for a full run.",
    )
    parser.add_argument(
        "--config",
        help="Optional run config to parse and check for missing local input files.",
    )
    args = parser.parse_args(argv)

    env_path = load_env_file()
    failures: List[str] = []
    warnings: List[str] = []

    def report(level: str, message: str) -> None:
        marker = {"ok": "✓", "warn": "!", "fail": "✗"}[level]
        print(f"  {marker} {message}")

    print(f"Gene Program Interpreter v{__version__} doctor (read-only; no API calls):")
    report("ok", f"gpi version {__version__} "
                 f"(if this looks stale: `claude plugin marketplace update gpi` → disable → install)")
    py_ok = sys.version_info >= (3, 10)
    report("ok" if py_ok else "fail", f"Python {sys.version.split()[0]} (requires 3.10+)")
    if not py_ok:
        failures.append("Python 3.10+ is required")

    claude = shutil.which("claude")
    if not claude:
        report("fail", "Claude Code CLI was not found on PATH")
        failures.append("install Claude Code and run `claude login`")
    else:
        try:
            auth = subprocess.run(
                [claude, "auth", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            payload = json.loads(auth.stdout) if auth.stdout.strip() else {}
            logged_in = bool(payload.get("loggedIn"))
            method = payload.get("authMethod") or payload.get("subscriptionType") or "authenticated"
            if auth.returncode == 0 and logged_in:
                report("ok", f"Claude login is active ({method})")
            else:
                report("fail", "Claude login is not active; run `claude login`")
                failures.append("Claude login is required for research")
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            report("warn", "Could not verify Claude login automatically; run `claude auth status`")
            warnings.append("Claude login status was not verified")

    # Where credentials come from — show it. A .env in the wrong place is the single most
    # common setup failure (keys silently absent, then a paid step dies mid-run). The user
    # asked to see the exact path, and a missing key should come with a copy-pasteable fix.
    env_target = env_path or (Path.cwd() / ".env")
    if env_path is not None:
        report("ok", f".env loaded from {env_path}")
    else:
        searched = ", ".join(str(c) for c in env_search_candidates()[:3])
        report("warn", f"no .env file found (searched {searched}, and parent dirs)")
        warnings.append("no .env file found")

    # (key, purpose, example value for the fix hint).
    required_env = [
        ("ANTHROPIC_API_KEY", "Anthropic Batch synthesis", "sk-ant-…"),
        ("PUBMED_EMAIL", "NCBI/Crossref polite access", "you@example.com"),
    ]
    for key, purpose, example in required_env:
        if os.environ.get(key):
            report("ok", f"{key} is set ({purpose})")
        else:
            report("fail", f"{key} is missing ({purpose})")
            print(f"      → add it:  echo '{key}={example}' >> {env_target}")
            failures.append(f"set {key}")

    optional_env = {
        "OPENALEX_API_KEY": "full OpenAlex verification coverage",
        "NCBI_API_KEY": "higher PubMed rate limits",
    }
    for key, purpose in optional_env.items():
        if os.environ.get(key):
            report("ok", f"{key} is set ({purpose})")
        else:
            report("warn", f"{key} is not set ({purpose}; optional)")
            warnings.append(f"{key} is optional but recommended")

    if args.config:
        try:
            cfg = PipelineConfig.from_yaml(args.config)
            missing = [p for p in [cfg.gene_loading, cfg.regulators, *cfg.regulators_by_condition.values()]
                       if p is not None and not p.exists()]
            if missing:
                for path in missing:
                    report("fail", f"configured input does not exist: {path}")
                failures.append("one or more configured inputs are missing")
            else:
                report("ok", f"config parsed and local inputs exist: {args.config}")
        except Exception as exc:  # noqa: BLE001 — doctor must turn parse errors into guidance
            report("fail", f"config could not be loaded: {exc}")
            failures.append("fix the run config")

    if failures:
        print(f"\nNot ready: {len(failures)} required check(s) failed.")
        return 1
    suffix = f" ({len(warnings)} optional warning(s))" if warnings else ""
    print(f"\nReady for a full run{suffix}.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "doctor":
        return cmd_doctor(raw_argv[1:])
    if raw_argv and raw_argv[0] == "watch":
        from .watch import cmd_watch  # local import: keeps the fast paths free of it
        return cmd_watch(raw_argv[1:])

    args = build_arg_parser().parse_args(raw_argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    # Read-only pre-flight helpers (do not run the pipeline).
    if args.check_inputs:
        return cmd_check_inputs(args)
    if args.emit_config:
        return cmd_emit_config(args)

    if not args.config:
        raise SystemExit("--config is required to run the pipeline "
                         "(or use --check-inputs / --emit-config).")
    cfg = PipelineConfig.from_yaml(args.config)
    flags = Flags(
        dry_run=args.dry_run,
        no_research=args.no_research,
        deterministic_presentation=args.deterministic_presentation,
        progress=args.progress,
    )
    return run_pipeline(
        cfg,
        flags,
        start_from=args.start_from,
        stop_after=args.stop_after,
        force_restart=args.force_restart,
    )


if __name__ == "__main__":
    raise SystemExit(main())
