"""Validated model content and deterministic annotation Markdown.

The model supplies biological prose, never the report layout or measured effects.
Legacy Markdown remains readable; pipeline requests require this JSON contract.
"""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Module(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    key_genes: list[str] = Field(min_length=2)
    pmids: list[str]
    evidence: str = Field(min_length=1)


class Regulator(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    gene: str = Field(min_length=1)
    confidence: Literal["High", "Medium", "Low"]
    mechanism: str = Field(min_length=1)


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    program_id: int
    label: str = Field(min_length=1)
    brief_summary: str = Field(min_length=1)
    overview: str = Field(min_length=1)
    modules: list[Module] = Field(min_length=1, max_length=3)
    distinctive: str = Field(min_length=1)
    regulators: list[Regulator]


class RegulatorSupplement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    regulators: list[Regulator]


def output_format(model):
    """Give the primary API call the same shape checked by local validation."""
    from anthropic import transform_schema

    schema = transform_schema(model.model_json_schema())
    if model is Annotation:
        # The service rejects the bounded word regex (and its expanded form).
        # Like transform_schema's handling of unsupported length constraints,
        # describe the rule here and enforce it in parse_annotation before
        # supplementation/publication. Do not weaken the local six-word check.
        schema["properties"]["label"]["description"] = "One to six whitespace-separated words."
    return {"type": "json_schema", "schema": schema}


def load_model_json(text):
    """Decode one JSON response, allowing only an unambiguous final code fence.

    Never select between multiple objects or repair JSON syntax. Commentary is
    discarded only at this boundary; every content validator still runs.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if text.count("```") != 2:
            raise
        match = re.fullmatch(r"([^{}]*?)```(?:json)?\s*\n(.*?)\n```\s*", text, re.S)
        if match is None:
            raise
        return json.loads(match.group(2))


def parse_annotation(text, program_id):
    result = Annotation.model_validate(load_model_json(text))
    if result.program_id != program_id:
        raise ValueError("Annotation program ID differs from request")
    if not result.label.split():
        raise ValueError("Program label must contain at least one word")
    if len(result.label.split()) > 6:
        raise ValueError(
            f"Program label exceeds six words: found {len(result.label.split())} tokens {result.label.split()!r}; remove at least {len(result.label.split()) - 6} words"
        )
    names = [r.gene.casefold() for r in result.regulators]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate regulator gene")
    for module in result.modules:
        if any(not re.fullmatch(r"\d+", pmid) for pmid in module.pmids):
            raise ValueError("PMIDs must contain digits only")
    return result


def _paragraph(value):
    # Content cannot inject report headings, tables or code fences.
    return " ".join(value.replace("```", "").split())


def render_annotation(annotation):
    a = annotation
    lines = [
        f"## Program {a.program_id} annotation",
        "",
        f"**Brief Summary:** {_paragraph(a.brief_summary)}",
        "",
        f"**Program label:** {_paragraph(a.label)}",
        "",
        "## High-level overview",
        "",
        _paragraph(a.overview),
        "",
        "## Functional modules and mechanisms",
        "",
    ]
    for m in a.modules:
        lines += [
            "```",
            _paragraph(m.name),
            _paragraph(m.summary),
            "Key genes: " + ", ".join(m.key_genes),
            "Supporting PMIDs: " + (", ".join(m.pmids) or "None"),
            "evidence used: " + _paragraph(m.evidence),
            "```",
            "",
        ]
    lines += [
        "## Distinctive features",
        "",
        _paragraph(a.distinctive),
        "",
        "## Regulator analysis",
        "",
    ]
    for r in a.regulators:
        lines += [
            "```",
            f"{r.gene} (inferred, log2FC=N/A): [Confidence: {r.confidence}]",
            "Mechanistic hypothesis: " + _paragraph(r.mechanism),
            "```",
            "",
        ]
    if not a.regulators:
        lines += ["No regulator interpretation supplied.", ""]
    return "\n".join(lines)


OUTPUT_CONTRACT = """### Output requirements (JSON only)
Return exactly one JSON object; no Markdown wrapper, tables, headings, or extra fields:
{
  "program_id": {program_id},
  "label": "biological phrase, at most 6 words",
  "brief_summary": "1-2 sentences",
  "overview": "at most 120 words; explain the strongest leading program genes first",
  "modules": [{"name": "module name", "summary": "2-4 sentences",
    "key_genes": ["supplied program gene", "another supplied program gene"],
    "pmids": ["digits-only supplied PMID"], "evidence": "specific support and limits"}],
  "distinctive": "1-2 sentences explaining distinctive program genes",
  "regulators": [{"gene": "exact supplied symbol", "confidence": "Low",
    "mechanism": "1-2 sentences on a possible mechanism and its uncertainty"}]
}
Select or consolidate research candidates into 1-3 final modules; three is a maximum,
not a target. Each module needs at least 2 distinct supplied program genes. A gene must support
that actual function, not merely supply generic carbon, ATP or nitrogen. Do not
invent a biosynthetic or secretory module from upstream substrates or regulators.
The title and overview must explain the leading gene pattern even without regulators.
Regulator names must be exact input symbols with original case, without aliases or
protein names. Include every selected regulator once. Confidence is High/Medium/Low
and concerns the mechanism, not statistical significance. With no retrieved support,
say that the mechanism is unresolved; do not manufacture one. If no regulator input
exists, use an empty regulators list; do not infer biological meaning from absent hits.
If discussing a literature-only hypothesis, label each one explicitly as inference.
Do not include regulator statistics, roles, observed knockdown directions, condition
comparisons or interaction claims anywhere in model output. The workflow inserts all
measured effects and observed directions from the complete input tables after synthesis.
Mechanisms are tentative explanations, not restatements of measured responses.
Use only supplied citations that support the exact claim; an empty pmids list is valid.
"""


def validate_batch_contract(request_path, result_path):
    """Reject missing, duplicate, failed or truncated responses before parsing/publishing."""
    from pathlib import Path

    requests = json.loads(Path(request_path).read_text())["requests"]
    expected = {r["custom_id"] for r in requests}
    seen = set()
    for line in Path(result_path).read_text().splitlines():
        result = json.loads(line)
        custom_id = result.get("custom_id")
        if custom_id not in expected or custom_id in seen:
            raise ValueError(f"Unexpected or duplicate annotation response: {custom_id}")
        seen.add(custom_id)
        envelope = result.get("result", {})
        if envelope.get("type") != "succeeded":
            raise ValueError(f"Annotation request failed: {custom_id}")
        message = envelope.get("message", {})
        if message.get("stop_reason") not in (None, "end_turn"):
            raise ValueError(f"Incomplete annotation response: {custom_id}")
        text = "".join(
            b.get("text", "") for b in message.get("content", []) if b.get("type") == "text"
        )
        parse_annotation(text, int(re.search(r"topic_(\d+)", custom_id).group(1)))
    if seen != expected:
        raise ValueError(f"Missing annotation responses: {sorted(expected - seen)}")
