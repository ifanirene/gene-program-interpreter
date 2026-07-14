#!/usr/bin/env python3
"""Reproducible presentation layer for the Gene Program Interpreter.

This module merges ProgExplorer's ``pipeline/presentation_layer.py`` (the field
logic) and ``pipeline/06_generate_presentation.py`` (the orchestration) into one
module and standardizes it on the Anthropic API (see ``docs/ARCHITECTURE.md``).

It turns each finished program annotation (the markdown produced by the
annotation step) into the small set of *display* fields the redesigned report
consumes but that are not part of the scientific annotation schema:

- ``lead``        : a punchy one-sentence introduction for the hero section
- ``lead_html``   : the lead with salient gene/pathway terms wrapped in <b>
- ``tags``        : 2-3 short context chips
- ``module_short``: a short label per functional module (for the glance strip)

Two generation paths are supported, by design:

1. Deterministic (no model). ``deterministic_presentation`` derives every field
   from the annotation text plus a curated lexicon. Fully reproducible and used
   as the guaranteed ④ fallback so the report never depends on a model being run
   (used both with ``--deterministic`` and whenever the batch fails).

2. Anthropic Batch (③). ``build_presentation_prompt`` emits a strict-JSON prompt;
   requests for every program are submitted through the vendored batch infra
   (``gpi.anthropic_batch``: ``submit_batch`` / ``check_batch`` / ``fetch_results``),
   polled to completion, and the per-program model text is validated by
   ``parse_presentation_response`` against faithfulness rules (highlights must be
   substrings of the lead, module_short must align to the modules, tags are
   length/count-capped). Anything missing or invalid falls back to the
   deterministic value.

Highlighting is always deterministic and verifiable: only known gene symbols,
regulator names, and curated lexicon phrases are emphasized, so emphasis can
never invent content.

Output contract (``presentation.json``, consumed by ``gpi/html_report.py``)::

    {"meta": {...},
     "programs": {"<id>": {"lead", "lead_html", "tags": [],
                           "module_short": [], "source"}}}

CLI::

    python -m gpi.presentation --annotations-dir DIR [--deterministic]
        [--out presentation.json] [--lexicon L] [--emit-js JS]
        [--model M] [--max-tokens N] [--effort ...] [--thinking adaptive]
        [--results R.jsonl] [--force] [--poll-interval S] [--timeout S]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .anthropic_batch import (
    MODEL as DEFAULT_BATCH_MODEL,
    POLL_INTERVAL_SECONDS,
    check_batch,
    fetch_results,
    submit_batch,
)
from .log_redaction import install_log_redaction
from .progress import emit_step_progress

PROMPT_VERSION = "presentation-v1"

# Default lexicon, shipped as package data INSIDE this package (dataset-editable, not code).
# It used to live at <repo>/configs/, resolved via parent.parent — which is the repo root in a
# checkout and site-packages/ in a wheel. configs/ is not a declared package, so it shipped to
# nobody: every installed user silently fell back to DEFAULT_LEXICON below. To override it,
# drop a configs/presentation_lexicon.json next to your run config (run_pipeline finds it) or
# pass --lexicon.
DEFAULT_LEXICON_PATH = Path(__file__).resolve().parent / "presentation_lexicon.json"

DEFAULT_LEXICON: dict[str, Any] = {
    "max_tags": 3,
    "max_highlights": 7,
    "lead_max_words": 45,
    "module_short_max_words": 4,
    "phrases": [],
}

_FIELD_RE = re.compile(
    r"^\s*(Key genes|Supporting PMIDs|Evidence used|Proposed mechanism)\s*:\s*(.*)$",
    re.I,
)
_MODULE_HEAD_RE = re.compile(r"^\s*Module\s*\d*\s*:\s*(.*)$", re.I)
_REGULATOR_HEAD_RE = re.compile(
    r"^\s*([A-Za-z0-9/().+\-]+?)\s*\((repressor|activator)\b", re.I
)
_FENCE_RE = re.compile(r"```(.*?)```", re.S)

_MODULE_STOPWORDS = {
    "the", "of", "in", "with", "for", "a", "an", "and", "to", "via", "by",
    "their", "its", "that", "this", "on", "into", "from",
}


# ---------------------------------------------------------------------------
# Lexicon
# ---------------------------------------------------------------------------

def load_lexicon(path: Optional[Path | str]) -> dict[str, Any]:
    """Load the presentation lexicon JSON, merged onto sensible defaults."""
    lexicon = dict(DEFAULT_LEXICON)
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for key, value in data.items():
            if key.startswith("_"):
                continue
            lexicon[key] = value
    return lexicon


# ---------------------------------------------------------------------------
# Annotation parsing (self-contained so this module is independently testable)
# ---------------------------------------------------------------------------

def _join_wrapped(lines: list[str]) -> str:
    return " ".join(part.strip() for part in lines if part.strip()).strip()


def _strip_rules(body: str) -> str:
    kept = [ln for ln in body.splitlines() if ln.strip() != "---"]
    return _join_wrapped(kept)


def _extract_section(md: str, names: str) -> str:
    pat = re.compile(
        r"(?ms)^#{2,3}\s*(?:\d+\.\s*)?(?:" + names + r")\s*\n(?P<body>.*?)"
        r"(?=^#{2,3}\s|\Z)"
    )
    match = pat.search(md)
    if not match:
        return ""
    # Drop any fenced sub-blocks (e.g. module/regulator code blocks).
    body = _FENCE_RE.sub("", match.group("body"))
    return _strip_rules(body)


def _parse_modules(md: str) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for block in _FENCE_RE.findall(md):
        lines = [ln.rstrip() for ln in block.strip("\n").splitlines()]
        if not lines:
            continue
        head = _MODULE_HEAD_RE.match(lines[0])
        if not head:
            continue
        title = head.group(1).strip()
        summary_lines: list[str] = []
        fields: dict[str, list[str]] = {}
        current: Optional[str] = None
        for line in lines[1:]:
            field = _FIELD_RE.match(line)
            if field:
                current = field.group(1).lower()
                fields.setdefault(current, []).append(field.group(2).strip())
            elif current:
                fields[current].append(line.strip())
            else:
                summary_lines.append(line.strip())
        kg_text = _join_wrapped(fields.get("key genes", []))
        key_genes = [g.strip() for g in re.split(r"[,;]", kg_text) if g.strip()]
        modules.append(
            {
                "title": title,
                "summary": _join_wrapped(summary_lines),
                "key_genes": key_genes,
            }
        )
    return modules


def _parse_regulators(md: str) -> list[dict[str, str]]:
    regulators: list[dict[str, str]] = []
    for block in _FENCE_RE.findall(md):
        stripped = block.strip()
        if not stripped:
            continue
        first = stripped.splitlines()[0]
        head = _REGULATOR_HEAD_RE.match(first)
        if head:
            regulators.append(
                {"gene": head.group(1).strip(), "role": head.group(2).lower()}
            )
    return regulators


def _first_line_value(md: str, label: str) -> str:
    match = re.search(rf"\*\*{label}:\*\*\s*([^\n]+)", md, re.I)
    return match.group(1).strip() if match else ""


def parse_annotation(md: str, program_id: Optional[int] = None) -> dict[str, Any]:
    """Parse an annotation markdown into the fields the presentation layer needs."""
    return {
        "id": program_id,
        "label": _first_line_value(md, "Program label"),
        "summary": _first_line_value(md, "Brief Summary"),
        "overview": _extract_section(md, "High-level overview"),
        "distinctive": _extract_section(md, "Distinctive features"),
        "modules": _parse_modules(md),
        "regulators": _parse_regulators(md),
    }


# ---------------------------------------------------------------------------
# Highlighting (deterministic, verifiable)
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def salient_symbols(program: dict[str, Any]) -> set[str]:
    """Gene symbols safe to emphasize: module key genes + regulator names."""
    symbols: set[str] = set()
    for module in program.get("modules", []):
        for gene in module.get("key_genes", []):
            if gene:
                symbols.add(gene.strip())
    for regulator in program.get("regulators", []):
        for part in re.split(r"[\/\s]+", regulator.get("gene", "")):
            part = part.strip()
            if part:
                symbols.add(part)
    return {s for s in symbols if len(s) >= 2}


def _build_terms(program: dict[str, Any], lexicon: dict[str, Any],
                 extra: Optional[list[str]] = None) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    # Gene symbols: case-sensitive, alphanumeric boundaries.
    for symbol in sorted(salient_symbols(program), key=len, reverse=True):
        patterns.append(
            re.compile(r"(?<![A-Za-z0-9])" + re.escape(symbol) + r"(?![A-Za-z0-9])")
        )
    # Curated phrases: case-insensitive, word boundaries.
    phrases = sorted(lexicon.get("phrases", []), key=len, reverse=True)
    for phrase in phrases:
        patterns.append(
            re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.I)
        )
    # Agent-provided exact spans (already validated as substrings of the lead).
    for span in sorted(extra or [], key=len, reverse=True):
        if span.strip():
            patterns.append(re.compile(re.escape(span)))
    return patterns


def highlight_text(text: str, patterns: list[re.Pattern[str]]) -> str:
    """Wrap the first non-overlapping match of each term in <b>, HTML-escaping the rest."""
    if not text:
        return ""
    spans: list[tuple[int, int]] = []
    for pat in patterns:
        for match in pat.finditer(text):
            if match.end() > match.start():
                spans.append((match.start(), match.end()))
    if not spans:
        return _esc(text)
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    chosen: list[tuple[int, int]] = []
    last_end = -1
    for start, end in spans:
        if start >= last_end:
            chosen.append((start, end))
            last_end = end
    out: list[str] = []
    prev = 0
    for start, end in chosen:
        out.append(_esc(text[prev:start]))
        out.append("<b>" + _esc(text[start:end]) + "</b>")
        prev = end
    out.append(_esc(text[prev:]))
    return "".join(out)


# ---------------------------------------------------------------------------
# Deterministic field derivation
# ---------------------------------------------------------------------------

def _first_sentence(text: str) -> str:
    match = re.search(r"(.+?[.!?])(\s|$)", text.strip())
    return match.group(1).strip() if match else text.strip()


def shorten_title(title: str, max_words: int = 4) -> str:
    """Heuristic short label for a module title (deterministic fallback)."""
    cleaned = re.sub(r"^\s*Module\s*\d*\s*:\s*", "", title).strip()
    cleaned = re.split(r"[,;:]", cleaned)[0]
    cleaned = re.split(r"\band\b", cleaned, flags=re.I)[0].strip()
    words = cleaned.split()
    kept: list[str] = []
    for word in words:
        normalized = re.sub(r"-mediated$", "", word, flags=re.I)
        if normalized.lower().strip("-") in _MODULE_STOPWORDS:
            continue
        kept.append(normalized)
    if not kept:
        kept = words
    short = " ".join(kept[:max_words]).strip(" -")
    return short or cleaned.strip()


def lexicon_tags(program: dict[str, Any], lexicon: dict[str, Any]) -> list[str]:
    """Pick up to max_tags curated phrases that appear in the label/summary."""
    haystack = f"{program.get('label', '')} {program.get('summary', '')}"
    max_tags = int(lexicon.get("max_tags", 3))
    found: list[str] = []
    for phrase in lexicon.get("phrases", []):
        if not re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", haystack, re.I):
            continue
        low = phrase.lower()
        # Skip near-duplicates where one tag contains the other (e.g. keep
        # "Wnt/β-catenin", drop the redundant "β-catenin").
        if any(low in f.lower() or f.lower() in low for f in found):
            continue
        found.append(phrase)
        if len(found) >= max_tags:
            break
    return found


def deterministic_presentation(program: dict[str, Any],
                               lexicon: dict[str, Any]) -> dict[str, Any]:
    """Derive all display fields from the annotation alone (no model)."""
    lead = program.get("summary", "").strip()
    if not lead:
        lead = _first_sentence(program.get("overview", ""))
    max_words = int(lexicon.get("module_short_max_words", 4))
    return {
        "lead": lead,
        "tags": lexicon_tags(program, lexicon),
        "module_short": [
            shorten_title(m.get("title", ""), max_words)
            for m in program.get("modules", [])
        ],
        "highlights": [],
        "source": "deterministic",
    }


# ---------------------------------------------------------------------------
# Agent prompt + response validation
# ---------------------------------------------------------------------------

def build_presentation_prompt(program: dict[str, Any],
                              lexicon: dict[str, Any]) -> str:
    """Strict-JSON prompt asking the model only to condense/relabel/emphasize."""
    n = len(program.get("modules", []))
    max_tags = int(lexicon.get("max_tags", 3))
    lead_max = int(lexicon.get("lead_max_words", 45))
    short_max = int(lexicon.get("module_short_max_words", 4))
    max_hl = int(lexicon.get("max_highlights", 7))

    modules_block = "\n".join(
        f"{i + 1}. {m.get('title', '')} — {m.get('summary', '')}"
        for i, m in enumerate(program.get("modules", []))
    )
    regulators_block = ", ".join(
        f"{r.get('gene', '')} ({r.get('role', '')})"
        for r in program.get("regulators", [])
    ) or "none listed"

    return (
        "You are refining DISPLAY metadata for one gene-program report card. "
        "Do NOT introduce new biological claims, genes, or facts; only condense, "
        "relabel, and select emphasis from the text provided.\n\n"
        f"Program {program.get('id')}: {program.get('label', '')}\n"
        f"Brief summary: {program.get('summary', '')}\n"
        f"High-level overview: {program.get('overview', '')}\n"
        f"Functional modules:\n{modules_block}\n"
        f"Distinctive features: {program.get('distinctive', '')}\n"
        f"Regulators: {regulators_block}\n\n"
        "Return ONLY a JSON object (no prose, no code fence) with these keys:\n"
        f'- "lead": one researcher-facing sentence (<= {lead_max} words) that '
        "introduces the program, faithful to the summary/overview. Plain text, "
        "no markdown.\n"
        f'- "tags": {max_tags} or fewer very short context chips (<= 3 words '
        "each), e.g. the key signaling axis and the disease/aging context. Use "
        "only concepts present above.\n"
        f'- "module_short": an array of EXACTLY {n} short labels (<= {short_max} '
        "words each), one per module in the SAME order, capturing each module's "
        "core idea.\n"
        f'- "highlights": an array of {max_hl} or fewer short EXACT substrings of '
        'your "lead" to bold (key genes, pathways, or axes).\n'
    )


def _extract_json(raw: str) -> Optional[dict[str, Any]]:
    if not raw:
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.S)
        if brace:
            text = brace.group(0)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _clean_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(",;:.") + "…"


def parse_presentation_response(raw: str, program: dict[str, Any],
                                lexicon: dict[str, Any]) -> dict[str, Any]:
    """Validate model output against faithfulness rules; fall back per field."""
    fallback = deterministic_presentation(program, lexicon)
    obj = _extract_json(raw)
    if obj is None:
        return fallback

    result = dict(fallback)
    result["source"] = "agent"
    used_agent = False

    lead = _clean_str(obj.get("lead"))
    if lead:
        result["lead"] = _truncate_words(lead, int(lexicon.get("lead_max_words", 45)) + 10)
        used_agent = True

    tags_raw = obj.get("tags")
    if isinstance(tags_raw, list):
        tags = [_clean_str(t) for t in tags_raw]
        tags = [t for t in tags if t and len(t) <= 28][: int(lexicon.get("max_tags", 3))]
        if tags:
            result["tags"] = tags
            used_agent = True

    modules = program.get("modules", [])
    shorts_raw = obj.get("module_short")
    if isinstance(shorts_raw, list) and len(shorts_raw) == len(modules) and modules:
        shorts = [_clean_str(s) for s in shorts_raw]
        if all(shorts):
            short_max = int(lexicon.get("module_short_max_words", 4))
            result["module_short"] = [_truncate_words(s, short_max) for s in shorts]
            used_agent = True

    # Highlights must be exact substrings of the final lead (faithfulness gate).
    highlights_raw = obj.get("highlights")
    if isinstance(highlights_raw, list):
        lead_text = result["lead"]
        highlights = [
            _clean_str(h) for h in highlights_raw
            if _clean_str(h) and _clean_str(h) in lead_text
        ][: int(lexicon.get("max_highlights", 7))]
        result["highlights"] = highlights

    if not used_agent:
        return fallback
    return result


# ---------------------------------------------------------------------------
# Apply (render-ready output)
# ---------------------------------------------------------------------------

def apply_presentation(program: dict[str, Any], pres: dict[str, Any],
                       lexicon: dict[str, Any]) -> dict[str, Any]:
    """Produce render-ready fields, including a highlighted lead_html."""
    patterns = _build_terms(program, lexicon, extra=pres.get("highlights"))
    return {
        "lead": pres.get("lead", ""),
        "lead_html": highlight_text(pres.get("lead", ""), patterns),
        "tags": list(pres.get("tags", [])),
        "module_short": list(pres.get("module_short", [])),
        "source": pres.get("source", "deterministic"),
    }


def program_content_hash(program: dict[str, Any], model: str = "",
                         prompt_version: str = PROMPT_VERSION) -> str:
    """Stable hash of the inputs that determine the presentation output."""
    payload = {
        "label": program.get("label", ""),
        "summary": program.get("summary", ""),
        "overview": program.get("overview", ""),
        "distinctive": program.get("distinctive", ""),
        "modules": [
            {"title": m.get("title", ""), "summary": m.get("summary", ""),
             "key_genes": m.get("key_genes", [])}
            for m in program.get("modules", [])
        ],
        "regulators": program.get("regulators", []),
        "model": model,
        "prompt_version": prompt_version,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ===========================================================================
# Orchestration (from 06_generate_presentation.py, Anthropic-only)
# ===========================================================================

def discover_programs(annotations_dir: Path) -> dict[int, dict[str, Any]]:
    """Parse every ``topic_<N>_annotation.md`` in a directory into program dicts."""
    programs: dict[int, dict[str, Any]] = {}
    for path in sorted(annotations_dir.glob("topic_*_annotation.md")):
        match = re.match(r"topic_(\d+)_annotation", path.name)
        if not match:
            continue
        program_id = int(match.group(1))
        md = path.read_text(encoding="utf-8")
        programs[program_id] = parse_annotation(md, program_id)
    return programs


def _extract_text(data: dict[str, Any]) -> str:
    """Extract the assistant text from one Anthropic Batch result record.

    Only the Anthropic Batch shape is parsed: ``fetch_results`` writes
    ``result.model_dump()`` per line, i.e.
    ``{"custom_id", "result": {"type": "succeeded", "message": {"content": [...]}}}``.
    The Vertex/gateway-normalized ``response.content`` branch and the OpenAI
    ``choices[0].message.content`` branch from the original ``06`` were dropped
    per the ``docs/ARCHITECTURE.md`` DROP list.
    """
    def from_content(content: object) -> str:
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        return text
        return ""

    result = data.get("result") or {}
    if isinstance(result, dict) and result.get("type") == "succeeded":
        return from_content(result.get("message", {}).get("content", []))
    return ""


def read_results(path: Path) -> dict[int, str]:
    """Read an Anthropic Batch results JSONL into ``{program_id: assistant_text}``."""
    texts: dict[int, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        custom_id = data.get("custom_id", "")
        match = re.match(r"topic_(\d+)", custom_id or "")
        if not match:
            continue
        texts[int(match.group(1))] = _extract_text(data)
    return texts


def load_cache(output_path: Path) -> dict[str, Any]:
    """Load a previously written presentation.json's ``programs`` block, if any."""
    if output_path.exists():
        try:
            data = json.loads(output_path.read_text(encoding="utf-8"))
            return data.get("programs", {})
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


# ---------------------------------------------------------------------------
# Anthropic Batch path (③): assemble → submit → poll → fetch → parse
# ---------------------------------------------------------------------------

def build_batch_requests(programs: dict[int, dict[str, Any]], lexicon: dict[str, Any],
                         model: str, max_tokens: int, effort: Optional[str],
                         thinking: Optional[str]) -> list[dict[str, Any]]:
    """Assemble one Anthropic Batch request per program via ``build_presentation_prompt``."""
    requests: list[dict[str, Any]] = []
    for program_id in sorted(programs):
        prompt = build_presentation_prompt(programs[program_id], lexicon)
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if thinking:
            params["thinking"] = {"type": thinking}
        if effort:
            params["output_config"] = {"effort": effort}
        requests.append(
            {"custom_id": f"topic_{program_id}_presentation", "params": params}
        )
    return requests


def run_presentation_batch(programs: dict[int, dict[str, Any]], lexicon: dict[str, Any],
                           *, model: str, max_tokens: int, effort: Optional[str],
                           thinking: Optional[str], results_path: Path,
                           poll_interval: int = POLL_INTERVAL_SECONDS,
                           timeout: Optional[float] = None) -> dict[int, str]:
    """Submit presentation requests to the Anthropic Batch API, poll, and parse.

    Assembles requests with :func:`build_batch_requests`, submits via
    ``gpi.anthropic_batch.submit_batch``, polls ``check_batch`` until the batch
    ``ended``, downloads with ``fetch_results`` to ``results_path``, and returns
    ``{program_id: assistant_text}`` via :func:`read_results`.

    Raises ``RuntimeError``/``TimeoutError`` on batch failure; the caller falls
    back to the deterministic path.

    Each poll also emits a STEP_PROGRESS event (``gpi.progress.emit_step_progress``), so the
    minutes spent waiting on the batch show up as ``n/total`` in the live view and in
    ``progress.json`` instead of reading as a hung step. No plumbing needed: the emitter picks
    up ``GPI_PROGRESS_JSON`` / ``GPI_PROGRESS_STEP`` from the environment the driver exports,
    and is a no-op when they are unset (standalone run, or ``--progress off``).
    """
    requests = build_batch_requests(
        programs, lexicon, model, max_tokens, effort, thinking
    )
    n_requests = len(requests)
    batch_id = submit_batch(requests)
    print(f"Submitted presentation batch {batch_id} ({n_requests} requests).")
    emit_step_progress(0, n_requests, "batch submitted")

    deadline = time.time() + timeout if timeout else None
    while True:
        status = check_batch(batch_id)
        processing_status = status["processing_status"]
        counts = status["counts"]
        print(
            f"  batch {batch_id}: {processing_status} | "
            f"succeeded={counts['succeeded']} errored={counts['errored']} "
            f"processing={counts['processing']}"
        )
        # Terminal requests, against the number we actually submitted — a truer total than
        # re-deriving it from the counts, which omit whatever has not been dispatched yet.
        done = counts["succeeded"] + counts["errored"] + counts["canceled"]
        emit_step_progress(done, n_requests, f"batch {processing_status}")
        if processing_status == "ended":
            break
        if processing_status in ("canceling", "canceled"):
            raise RuntimeError(f"Batch {batch_id} was canceled ({processing_status}).")
        if deadline is not None and time.time() > deadline:
            raise TimeoutError(
                f"Batch {batch_id} did not end within {timeout}s (last "
                f"status={processing_status})."
            )
        time.sleep(poll_interval)

    emit_step_progress(n_requests, n_requests, "fetching results")
    fetch_results(batch_id, results_path)
    return read_results(results_path)


def build_presentation(programs: dict[int, dict[str, Any]], lexicon: dict[str, Any],
                       model: str, results_texts: Optional[dict[int, str]],
                       cache: dict[str, Any], force: bool) -> dict[str, Any]:
    """Build the full presentation.json structure.

    Per program: content-hash cache hit (agent-sourced) is reused on deterministic
    reruns; otherwise, if agent text is present it is validated by
    ``parse_presentation_response``, else ``deterministic_presentation`` is used;
    the result is rendered via ``apply_presentation``.
    """
    out: dict[str, Any] = {
        "meta": {
            "prompt_version": PROMPT_VERSION,
            "model": model,
            "mode": "agent" if results_texts is not None else "deterministic",
            "n_programs": len(programs),
        },
        "programs": {},
    }
    n_agent = n_fallback = n_cached = 0
    for program_id in sorted(programs):
        program = programs[program_id]
        content_hash = program_content_hash(program, model=model)
        cached = cache.get(str(program_id))

        if (cached and not force and cached.get("content_hash") == content_hash
                and results_texts is None and cached.get("source") == "agent"):
            # Preserve a previously validated agent result on deterministic reruns.
            out["programs"][str(program_id)] = cached
            n_cached += 1
            continue

        if results_texts is not None and results_texts.get(program_id, "").strip():
            pres = parse_presentation_response(
                results_texts[program_id], program, lexicon
            )
        else:
            pres = deterministic_presentation(program, lexicon)

        applied = apply_presentation(program, pres, lexicon)
        applied["content_hash"] = content_hash
        out["programs"][str(program_id)] = applied
        if applied["source"] == "agent":
            n_agent += 1
        else:
            n_fallback += 1

    out["meta"].update(
        {"n_agent": n_agent, "n_deterministic": n_fallback, "n_cached": n_cached}
    )
    print(
        f"Presentation built: {n_agent} agent, {n_fallback} deterministic, "
        f"{n_cached} cached (of {len(programs)} programs)."
    )
    return out


def emit_js(presentation: dict[str, Any], path: Path) -> None:
    """Write ``window.PRESENTATION = {...};`` for the standalone design preview."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(presentation["programs"], ensure_ascii=False)
    path.write_text(f"window.PRESENTATION = {payload};\n", encoding="utf-8")
    print(f"Wrote presentation JS to {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--annotations-dir", required=True,
                        help="Directory with topic_<N>_annotation.md files")
    parser.add_argument("--lexicon", default=None,
                        help="Presentation lexicon JSON "
                             f"(default: {DEFAULT_LEXICON_PATH} if present)")
    parser.add_argument("--out", "--output", dest="out", default=None,
                        help="presentation.json path "
                             "(default: <annotations-dir>/presentation.json)")
    parser.add_argument("--emit-js", default=None,
                        help="Optional path to also write window.PRESENTATION JS")
    parser.add_argument("--deterministic", action="store_true",
                        help="Force the deterministic path (no Anthropic Batch call)")
    parser.add_argument("--model", default="",
                        help="Model recorded in metadata / used in batch requests "
                             f"(batch default: {DEFAULT_BATCH_MODEL})")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                        help="Claude output_config.effort for the batch request")
    parser.add_argument("--thinking", choices=["adaptive"],
                        help="Claude thinking mode for the batch request")
    parser.add_argument("--results", default=None,
                        help="Pre-fetched Anthropic Batch results JSONL to fold in "
                             "(skips submitting a new batch)")
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL_SECONDS,
                        help="Seconds between batch status polls")
    parser.add_argument("--timeout", type=float, default=None,
                        help="Max seconds to wait for the batch (default: no limit)")
    parser.add_argument("--force", action="store_true",
                        help="Ignore cached agent entries and rebuild every program")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    # This module runs as its own process (``python -m gpi.presentation``), which does not
    # inherit the driver's logging config — importing anthropic_batch reconfigures the root
    # logger to INFO here, so httpx would log every request URL. Redact before that can happen.
    install_log_redaction()
    args = build_arg_parser().parse_args(argv)

    annotations_dir = Path(args.annotations_dir)
    if not annotations_dir.is_dir():
        print(f"Annotations directory not found: {annotations_dir}", file=sys.stderr)
        return 2

    # Resolve lexicon: explicit path (error if missing) or repo default (skip if absent).
    if args.lexicon:
        lexicon_path: Optional[Path | str] = args.lexicon
    elif DEFAULT_LEXICON_PATH.exists():
        lexicon_path = DEFAULT_LEXICON_PATH
    else:
        lexicon_path = None
        print(f"Note: default lexicon {DEFAULT_LEXICON_PATH} not found; "
              "using built-in defaults (no curated phrases).", file=sys.stderr)
    lexicon = load_lexicon(lexicon_path)

    programs = discover_programs(annotations_dir)
    if not programs:
        print(f"No topic_*_annotation.md files found in {annotations_dir}",
              file=sys.stderr)
        return 3

    output_path = (Path(args.out) if args.out
                   else annotations_dir / "presentation.json")
    cache = load_cache(output_path)

    # Decide the source of agent text:
    #   --deterministic  -> None (④ deterministic)
    #   --results FILE   -> fold pre-fetched batch results
    #   otherwise        -> submit/poll a live Anthropic Batch (③), fall back on failure
    results_texts: Optional[dict[int, str]]
    if args.deterministic:
        results_texts = None
        model = args.model
    elif args.results:
        results_texts = read_results(Path(args.results))
        model = args.model or DEFAULT_BATCH_MODEL
    else:
        model = args.model or DEFAULT_BATCH_MODEL
        results_path = output_path.with_name(
            f"{output_path.stem}_batch_results.jsonl"
        )
        try:
            results_texts = run_presentation_batch(
                programs, lexicon, model=model, max_tokens=args.max_tokens,
                effort=args.effort, thinking=args.thinking,
                results_path=results_path, poll_interval=args.poll_interval,
                timeout=args.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - fall back to deterministic ④
            print(f"Anthropic Batch path failed ({exc!r}); falling back to the "
                  "deterministic presentation.", file=sys.stderr)
            results_texts = None
            model = args.model  # deterministic metadata

    presentation = build_presentation(
        programs, lexicon, model, results_texts, cache, args.force
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(presentation, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote presentation to {output_path}")

    if args.emit_js:
        emit_js(presentation, Path(args.emit_js))

    return 0


if __name__ == "__main__":
    sys.exit(main())
