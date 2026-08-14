"""Run blinded model assessment over frozen reference-quality tasks.

The runner is deliberately offline with respect to literature: it sends only deterministic,
bounded windows from the cached assessor text to a subscription-authenticated model CLI.  Raw
research artifacts are never mutated, and every returned judgment is validated by
``research.reference_quality`` before it is accepted.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from research import reference_quality


DEFAULT_WINDOW_CHARS = 24_000
DEFAULT_CHUNK_SIZE = 8
DEFAULT_TIMEOUT = 900
CHATGPT_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
DEFAULT_CODEX_EXECUTABLE = str(CHATGPT_CODEX) if CHATGPT_CODEX.is_file() else "codex"
CODEX_DISABLED_FEATURES = (
    "plugins",
    "apps",
    "skill_search",
    "multi_agent",
    "browser_use",
    "computer_use",
    "image_generation",
    "in_app_browser",
    "goals",
    "workspace_dependencies",
    "hooks",
    "unified_exec",
    "shell_tool",
)
CODEX_UNSUPPORTED_SCHEMA_KEYS = {"minLength", "uniqueItems"}


class ModelAssessorError(RuntimeError):
    """A model shard could not be completed or validated."""


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _term_candidates(task: Mapping[str, Any], link: Mapping[str, Any]) -> list[str]:
    mechanism = (task.get("mechanisms") or {}).get(str(link["mechanism_index"])) or {}
    values: list[str] = []
    values.extend(str(value) for value in (task.get("session") or {}).get("supplied_genes") or [])
    values.extend(str(value) for value in mechanism.get("agent_supporting_genes") or [])
    values.append(str(link.get("selection_reason") or ""))
    values.append(str(mechanism.get("name") or ""))
    words: list[str] = []
    for value in values:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{2,}", value):
            words.append(value)
        else:
            words.extend(re.findall(r"[A-Za-z][A-Za-z0-9-]{4,}", value))
    stop = {
        "about",
        "agent",
        "context",
        "direct",
        "directly",
        "evidence",
        "function",
        "mechanism",
        "mouse",
        "paper",
        "program",
        "role",
        "study",
        "supports",
    }
    seen: set[str] = set()
    result: list[str] = []
    for word in words:
        key = word.casefold()
        if key in stop or key in seen:
            continue
        seen.add(key)
        result.append(word)
    return result


def _merge_ranges(ranges: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1] + 160:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def bounded_text_windows(
    text: str,
    terms: Sequence[str],
    *,
    max_chars: int = DEFAULT_WINDOW_CHARS,
) -> list[dict[str, Any]]:
    """Return source-offset-preserving windows around the abstract and relevant terms."""
    if len(text) <= max_chars:
        return [{"start": 0, "end": len(text), "text": text}]
    ranges: list[tuple[int, int]] = [(0, min(len(text), 5_000))]
    lower = text.casefold()
    for term in terms:
        needle = term.casefold()
        start = 0
        matches = 0
        while matches < 6:
            index = lower.find(needle, start)
            if index < 0:
                break
            ranges.append((max(0, index - 1_000), min(len(text), index + len(term) + 1_800)))
            start = index + len(needle)
            matches += 1
    merged = _merge_ranges(ranges)
    result: list[dict[str, Any]] = []
    used = 0
    for start, end in merged:
        if used >= max_chars:
            break
        bounded_end = min(end, start + (max_chars - used))
        result.append({"start": start, "end": bounded_end, "text": text[start:bounded_end]})
        used += bounded_end - start
    return result


def compact_task(
    task: Mapping[str, Any],
    links: Sequence[Mapping[str, Any]],
    *,
    max_chars_per_paper: int = DEFAULT_WINDOW_CHARS,
    prior_judgments: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    mechanisms: dict[str, Any] = {}
    papers: dict[str, Any] = {}
    task_papers = task.get("papers") or {}
    for link in links:
        mechanism_key = str(link["mechanism_index"])
        mechanisms[mechanism_key] = (task.get("mechanisms") or {})[mechanism_key]
        paper_key = str(link["paper_key"])
        if paper_key in papers:
            continue
        paper = task_papers[paper_key]
        assessor_text = paper.get("assessor_text") or {}
        text = str(assessor_text.get("text") or "")
        paper_links = [candidate for candidate in links if str(candidate["paper_key"]) == paper_key]
        terms = [term for candidate in paper_links for term in _term_candidates(task, candidate)]
        papers[paper_key] = {
            "paper": paper.get("paper") or {},
            "assessor_text": {
                "text_type": assessor_text.get("text_type"),
                "content_hash": assessor_text.get("content_hash"),
                "source_text_length": len(text),
                "windows": bounded_text_windows(text, terms, max_chars=max_chars_per_paper),
            },
        }
    payload: dict[str, Any] = {
        "session": task.get("session") or {},
        "mechanisms": mechanisms,
        "papers": papers,
        "links": list(links),
    }
    if prior_judgments is not None:
        payload["prior_judgments"] = {
            review_id: list(reviews) for review_id, reviews in prior_judgments.items()
        }
    return payload


def _context_accuracy(declared: str | None, assessed: str) -> str:
    if not declared or assessed == "not_assessable":
        return "not_assessable"
    levels = {"indirect": 0, "partial": 1, "direct": 2}
    if declared not in levels or assessed not in levels:
        return "not_assessable"
    if levels[declared] == levels[assessed]:
        return "accurate"
    return "overclaimed" if levels[declared] > levels[assessed] else "underclaimed"


def _exact_span(text: str, proposed: str, proposed_start: Any) -> tuple[str, int, int]:
    candidate = proposed.strip()
    if not candidate:
        raise ModelAssessorError("positive judgment returned an empty evidence span")
    if isinstance(proposed_start, int) and text[proposed_start : proposed_start + len(candidate)] == candidate:
        return candidate, proposed_start, proposed_start + len(candidate)
    index = text.find(candidate)
    if index >= 0:
        return candidate, index, index + len(candidate)
    pieces = re.split(r"\s+", candidate)
    pattern = r"\s+".join(re.escape(piece) for piece in pieces if piece)
    match = re.search(pattern, text)
    if match is None:
        raise ModelAssessorError("evidence quote is not present in cached assessor text")
    return text[match.start() : match.end()], match.start(), match.end()


def normalize_reviews(
    task: Mapping[str, Any],
    links: Sequence[Mapping[str, Any]],
    structured: Mapping[str, Any],
    *,
    assessor_id: str,
) -> list[dict[str, Any]]:
    expected = {str(link["review_id"]): link for link in links}
    rows = structured.get("reviews")
    if not isinstance(rows, list):
        raise ModelAssessorError("structured output has no reviews list")
    by_id = {str(row.get("review_id") or ""): row for row in rows if isinstance(row, Mapping)}
    if set(by_id) != set(expected):
        raise ModelAssessorError(
            f"review IDs mismatch; missing={sorted(set(expected) - set(by_id))}, "
            f"extra={sorted(set(by_id) - set(expected))}"
        )
    normalized: list[dict[str, Any]] = []
    for link in links:
        review_id = str(link["review_id"])
        row = dict(by_id[review_id])
        row["assessor_id"] = assessor_id
        row["context_label_accuracy"] = _context_accuracy(
            link.get("declared_context"), str(row.get("assessed_context") or "")
        )
        span = dict(row.get("evidence_span") or {})
        if row.get("support") in reference_quality.POSITIVE_OR_CONTRADICTORY:
            paper = (task.get("papers") or {})[str(link["paper_key"])]
            assessor_text = paper.get("assessor_text") or {}
            exact, start, end = _exact_span(
                str(assessor_text.get("text") or ""),
                str(span.get("text") or ""),
                span.get("start"),
            )
            row["evidence_span"] = {
                "text": exact,
                "start": start,
                "end": end,
                "source_type": str(assessor_text.get("text_type") or ""),
            }
        else:
            row["evidence_span"] = {"text": "", "start": None, "end": None, "source_type": ""}
        normalized.append(row)
    return normalized


def _assessment_prompt(
    protocol: str,
    payload: Mapping[str, Any],
    *,
    assessor_id: str,
    adjudication: bool,
) -> str:
    role = "adjudicator" if adjudication else "independent blinded assessor"
    prior = (
        "Two independent judgments are included as Assessment A and Assessment B. Resolve every "
        "disagreement from the cached text; do not simply vote or combine their gene lists. "
        if adjudication
        else ""
    )
    return (
        f"You are an {role} for a scientific citation audit. Assessor ID: {assessor_id}.\n\n"
        f"Protocol:\n{protocol}\n\n"
        "Strict rules:\n"
        "- Return exactly one review for every supplied review_id and no others.\n"
        "- Use only the cached text windows. No web search or outside knowledge.\n"
        "- A supplied gene is studied only when this paper substantively investigates that gene "
        "with gene-specific data. A name in a list, prior-work sentence, family/paralog evidence, "
        "or pathway-only discussion does not count.\n"
        "- function_supported_genes must be a subset of studied_genes and must have gene-specific "
        "support for the assigned mechanism. Context/regulator papers can support a mechanism while "
        "leaving both supplied-gene arrays empty.\n"
        "- Missing detail is not_assessable, never no. Context is separate from functional support.\n"
        "- For supports, partial, or contradicts, copy a short exact substring from one provided "
        "window. Use original source offsets; offsets will be checked deterministically. For no or "
        "not_assessable, use empty text, null offsets, and empty source_type.\n"
        "- If background_only is the relevant limitation for a supplied gene, do not credit that "
        "gene as studied or function-supported.\n"
        "- Keep each rationale to at most two concise sentences.\n"
        f"- Set assessor_id to {assessor_id!r} in the wrapper and every review.\n"
        f"{prior}\n"
        "Frozen task payload:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _invoke_claude(
    prompt: str,
    schema: Mapping[str, Any],
    *,
    model: str,
    effort: str,
    timeout: int,
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    command = [
        "claude",
        "-p",
        "--safe-mode",
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "",
        "--model",
        model,
        "--effort",
        effort,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema, separators=(",", ":")),
    ]
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise ModelAssessorError(
            f"Claude exited {completed.returncode}: {(completed.stderr or completed.stdout)[-800:]}"
        )
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ModelAssessorError(f"Claude returned invalid JSON: {completed.stdout[-800:]}") from exc
    structured = envelope.get("structured_output")
    if not isinstance(structured, Mapping):
        result = envelope.get("result")
        structured = json.loads(result) if isinstance(result, str) else None
    if not isinstance(structured, Mapping):
        raise ModelAssessorError(f"Claude did not return structured output: {envelope.get('subtype')}")
    telemetry = {
        "model": model,
        "duration_ms": envelope.get("duration_ms"),
        "duration_api_ms": envelope.get("duration_api_ms"),
        "turns": envelope.get("num_turns"),
        "cost_usd_equivalent": envelope.get("total_cost_usd"),
        "stop_reason": envelope.get("stop_reason"),
        "terminal_reason": envelope.get("terminal_reason"),
    }
    return structured, telemetry


def _invoke_codex(
    prompt: str,
    schema_path: Path,
    *,
    model: str,
    effort: str,
    timeout: int,
    executable: str = DEFAULT_CODEX_EXECUTABLE,
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    def compatible_schema(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: compatible_schema(item)
                for key, item in value.items()
                if key not in CODEX_UNSUPPORTED_SCHEMA_KEYS
            }
        if isinstance(value, list):
            return [compatible_schema(item) for item in value]
        return value

    with tempfile.TemporaryDirectory(prefix="gpi-model-assessor-") as temporary:
        workdir = Path(temporary)
        output_path = workdir / "structured-output.json"
        output_schema_path = workdir / "output-schema.json"
        _write_json(output_schema_path, compatible_schema(_read_json(schema_path)))
        command = [
            executable,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--cd",
            str(workdir),
            "--model",
            model,
            "--config",
            f'model_reasoning_effort="{effort}"',
        ]
        for feature in CODEX_DISABLED_FEATURES:
            command.extend(("--disable", feature))
        command.extend(
            [
            "--output-schema",
            str(output_schema_path),
            "--output-last-message",
            str(output_path),
            "--json",
            "--color",
            "never",
            "-",
            ]
        )
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            cwd=workdir,
        )
        if completed.returncode != 0:
            details = "\n".join(
                value[-2_000:] for value in (completed.stdout, completed.stderr) if value
            )
            raise ModelAssessorError(
                f"Codex exited {completed.returncode}: {details}"
            )
        if not output_path.is_file():
            raise ModelAssessorError("Codex did not write its structured final response")
        try:
            structured = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ModelAssessorError(
                f"Codex returned invalid JSON: {output_path.read_text(encoding='utf-8')[-800:]}"
            ) from exc
    if not isinstance(structured, Mapping):
        raise ModelAssessorError("Codex structured output is not a JSON object")

    events: list[Mapping[str, Any]] = []
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, Mapping):
            events.append(event)
    completed_turns = [event for event in events if event.get("type") == "turn.completed"]
    usage = completed_turns[-1].get("usage") if completed_turns else None
    telemetry = {
        "provider": "codex",
        "executable": executable,
        "model": model,
        "reasoning_effort": effort,
        "turns": len(completed_turns) or None,
        "input_tokens": usage.get("input_tokens") if isinstance(usage, Mapping) else None,
        "cached_input_tokens": (
            usage.get("cached_input_tokens") if isinstance(usage, Mapping) else None
        ),
        "output_tokens": usage.get("output_tokens") if isinstance(usage, Mapping) else None,
        "cost_usd_equivalent": None,
    }
    return structured, telemetry


def _load_tasks(task_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    return [
        (path, _read_json(path))
        for path in sorted(task_dir.glob("*.json"))
        if path.name != "manifest.json"
    ]


def _reliability_value(field: str, value: Any) -> Any:
    return tuple(sorted(value or [], key=str.casefold)) if field in {
        "studied_genes",
        "function_supported_genes",
        "red_flags",
    } else value


def _disagreement_ids(primary_path: Path, secondary_path: Path) -> tuple[set[str], dict[str, Any]]:
    primary = {row["review_id"]: row for row in _read_json(primary_path)["reviews"]}
    secondary = {row["review_id"]: row for row in _read_json(secondary_path)["reviews"]}
    disagreements: set[str] = set()
    priors: dict[str, Any] = {}
    for review_id, second in secondary.items():
        first = primary[review_id]
        if any(
            _reliability_value(field, first.get(field))
            != _reliability_value(field, second.get(field))
            for field in reference_quality.RELIABILITY_FIELDS
        ):
            disagreements.add(review_id)
            priors[review_id] = [first, second]
    return disagreements, priors


def run_assessment(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = Path(args.tasks)
    packet_path = Path(args.packet)
    index_path = Path(args.assessor_index)
    schema_path = Path(args.schema)
    schema = _read_json(schema_path)
    protocol = Path(args.protocol).read_text(encoding="utf-8")
    tasks = _load_tasks(task_dir)
    if args.slot == "adjudication":
        selected_ids, priors = _disagreement_ids(Path(args.primary), Path(args.secondary))
    else:
        selected_ids = set()
        priors = {}
    seed_reviews = (
        list(_read_json(args.seed_reviews).get("reviews") or []) if args.seed_reviews else []
    )
    seed_by_id = {str(review["review_id"]): review for review in seed_reviews}
    if len(seed_by_id) != len(seed_reviews):
        raise ModelAssessorError("seed reviews contain duplicate review IDs")
    if seed_reviews:
        reference_quality.validate_reviews(
            packet_path,
            seed_reviews,
            assessor_index_path=index_path,
            require_complete=False,
        )
    work: list[tuple[str, dict[str, Any], list[dict[str, Any]], Path]] = []
    shard_root = Path(args.out).parent / "shards" / args.slot
    for task_path, task in tasks:
        links = list(task.get("links") or [])
        if args.slot == "secondary" and args.secondary_scope == "sample":
            links = [link for link in links if int(link.get("required_assessors") or 1) == 2]
        elif args.slot == "adjudication":
            links = [link for link in links if str(link["review_id"]) in selected_ids]
        links = [link for link in links if str(link["review_id"]) not in seed_by_id]
        for index in range(0, len(links), args.chunk_size):
            chunk = links[index : index + args.chunk_size]
            shard = shard_root / f"{task_path.stem}--{index // args.chunk_size + 1:02d}.json"
            work.append((task_path.stem, task, chunk, shard))

    def complete(item: tuple[str, dict[str, Any], list[dict[str, Any]], Path]) -> dict[str, Any]:
        session_id, task, links, shard = item
        if shard.is_file() and not args.overwrite:
            existing = _read_json(shard)
            return {"reviews": existing["reviews"], "telemetry": existing.get("telemetry") or {}}
        payload = compact_task(
            task,
            links,
            max_chars_per_paper=args.max_chars_per_paper,
            prior_judgments=(
                {str(link["review_id"]): priors[str(link["review_id"])] for link in links}
                if args.slot == "adjudication"
                else None
            ),
        )
        prompt = _assessment_prompt(
            protocol,
            payload,
            assessor_id=args.assessor_id,
            adjudication=args.slot == "adjudication",
        )
        last_error: Exception | None = None
        for attempt in range(1, args.attempts + 1):
            started = time.monotonic()
            try:
                if args.provider == "codex":
                    structured, telemetry = _invoke_codex(
                        prompt,
                        schema_path,
                        model=args.model,
                        effort=args.effort,
                        timeout=args.timeout,
                        executable=args.codex_executable,
                    )
                else:
                    structured, telemetry = _invoke_claude(
                        prompt,
                        schema,
                        model=args.model,
                        effort=args.effort,
                        timeout=args.timeout,
                    )
                reviews = normalize_reviews(
                    task,
                    links,
                    structured,
                    assessor_id=args.assessor_id,
                )
                reference_quality.validate_reviews(
                    packet_path,
                    reviews,
                    assessor_index_path=index_path,
                    require_complete=False,
                )
                record = {
                    "schema_version": 1,
                    "assessment_type": "model",
                    "assessor_id": args.assessor_id,
                    "slot": args.slot,
                    "session_id": session_id,
                    "attempt": attempt,
                    "review_ids": [link["review_id"] for link in links],
                    "reviews": reviews,
                    "telemetry": {**telemetry, "wall_seconds": round(time.monotonic() - started, 3)},
                }
                _write_json(shard, record)
                print(f"completed {args.slot} {shard.name}: {len(reviews)} links", flush=True)
                return {"reviews": reviews, "telemetry": record["telemetry"]}
            except Exception as exc:  # noqa: BLE001 - retry boundary records exact failure
                last_error = exc
                print(
                    f"retry {args.slot} {shard.name} attempt {attempt}: {exc}",
                    flush=True,
                )
        raise ModelAssessorError(f"{shard.name} failed after {args.attempts} attempts: {last_error}")

    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(complete, item) for item in work]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    reviews = seed_reviews + [review for result in results for review in result["reviews"]]
    order = {
        str(item["review_id"]): index
        for index, item in enumerate(_read_json(packet_path)["review_items"])
    }
    reviews.sort(key=lambda row: order[str(row["review_id"])])
    require_complete = args.slot == "primary" or (
        args.slot == "secondary" and args.secondary_scope == "all"
    )
    reference_quality.validate_reviews(
        packet_path,
        reviews,
        assessor_index_path=index_path,
        require_complete=require_complete,
    )
    known_costs = [
        float(result["telemetry"]["cost_usd_equivalent"])
        for result in results
        if result["telemetry"].get("cost_usd_equivalent") is not None
    ]
    output = {
        "schema_version": 1,
        "assessment_type": "model",
        "assessor_id": args.assessor_id,
        "slot": args.slot,
        "reviews": reviews,
        "telemetry": {
            "shards": len(results),
            "seed_reviews": len(seed_reviews),
            "known_cost_usd_equivalent": (
                round(sum(known_costs), 6) if known_costs else None
            ),
            "cost_telemetry_shards": len(known_costs),
            "unknown_cost_shards": len(results) - len(known_costs),
            "known_duration_seconds": round(
                sum(float(result["telemetry"].get("wall_seconds") or 0) for result in results), 3
            ),
        },
    }
    _write_json(args.out, output)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--packet", required=True)
    parser.add_argument("--assessor-index", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--slot", choices=("primary", "secondary", "adjudication"), required=True)
    parser.add_argument("--assessor-id", required=True)
    parser.add_argument("--provider", choices=("claude", "codex"), default="claude")
    parser.add_argument("--codex-executable", default=DEFAULT_CODEX_EXECUTABLE)
    parser.add_argument("--secondary-scope", choices=("sample", "all"), default="sample")
    parser.add_argument("--seed-reviews")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), default="medium")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--max-chars-per-paper", type=int, default=DEFAULT_WINDOW_CHARS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--primary")
    parser.add_argument("--secondary")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.chunk_size <= 0 or args.concurrency <= 0 or args.max_chars_per_paper <= 0:
        raise SystemExit("chunk size, concurrency, and text-window size must be positive")
    if args.slot == "adjudication" and (not args.primary or not args.secondary):
        raise SystemExit("adjudication requires --primary and --secondary")
    result = run_assessment(args)
    print(
        json.dumps(
            {
                "slot": args.slot,
                "reviews": len(result["reviews"]),
                "telemetry": result["telemetry"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
