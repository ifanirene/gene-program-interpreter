"""Control regulator visibility and attach explanations without rewriting program fields."""

import json
import re
from pathlib import Path

from .annotation_contract import (
    RegulatorSupplement,
    load_model_json,
    output_format,
    parse_annotation,
)


def functional_bundle(bundle, *, profile=None):
    keys = ("program_id", "organism", "tissue", "cell_type", "program_genes", "distinctive_genes")
    result = {k: bundle[k] for k in keys if k in bundle}
    if profile is not None and profile.interpretation_mode == 'context_guided':
        from research.bundle import _build_research_brief
        for key in ('conditions', 'functions_to_consider'):
            if key in bundle:
                result[key] = bundle[key]
        result['research_brief'] = _build_research_brief(bundle['program_id'], profile, False)
        result['research_brief'] += ' No perturbation evidence is supplied. Do not infer perturbation regulators. Return an empty regulator_coverage list.'
        return result
    result["research_brief"] = (
        "Determine the shared function of the leading program genes and distinctive genes. "
        "Explore hypotheses without a fixed count. Retain distinct supported mechanisms with "
        "specific roles for at least two supplied program genes each; do not add weak themes to fill a quota. "
        "The final report's three-category maximum does not limit research candidates. "
        "No perturbation evidence is supplied in this functional-discovery pass. "
        "Do not infer perturbation regulators. Return an empty regulator_coverage list. "
        "Retrieve papers to test your hypotheses and report unresolved gaps."
    )
    return result


def core_requests(requests, *, include_regulators=False):
    result = json.loads(json.dumps(requests))
    for request in result["requests"]:
        prompt = request["params"]["messages"][0]["content"]
        # The regulator block is last in Supporting evidence; remove it before the
        # interpretation rules. The NCBI formatter already selects program-gene summaries.
        if not include_regulators:
            prompt = re.sub(
                r"#### Regulator perturbation evidence.*?(?=### Interpretation rules)",
                "", prompt, flags=re.S,
            )
            prompt += "\nFUNCTIONAL PASS: Regulator evidence is intentionally withheld. Return regulators: []. Do not infer any regulators.\n"
        else:
            prompt += ("\nPROGRAM ANNOTATION: Use program genes and supplied regulator research together. "
                       "Regulator evidence may support or qualify gene-supported functions, but cannot replace "
                       "program-gene support. Return regulators: [] in this response; the following explanation "
                       "request supplies regulator objects without changing these program fields.\n")
        request["params"]["messages"][0]["content"] = prompt
    return result


def selected_regulators(bundle):
    """Use the selected, already-masked bundle inputs to gate regulator work."""
    return sorted(
        {r["gene"] for rows in bundle.get("perturbation_regulators", {}).values() for r in rows}
    )


def supplement_requests(full_requests, core_results, support_dir):
    cores = {}
    for line in Path(core_results).read_text().splitlines():
        row = json.loads(line)
        message = row["result"]["message"]
        text = "".join(b.get("text", "") for b in message["content"] if b.get("type") == "text")
        pid = int(re.search(r"topic_(\d+)", row["custom_id"]).group(1))
        core = parse_annotation(text, pid)
        if core.regulators:
            raise ValueError(f"Functional pass introduced regulators for {pid}")
        cores[row["custom_id"]] = core
    result = json.loads(json.dumps(full_requests))
    selected_requests = []
    for request in result["requests"]:
        core = cores[request["custom_id"]]
        support = Path(support_dir) / f"P{core.program_id}.json"
        root = Path(support_dir).parent
        bundle = json.loads((root / "program_bundles" / f"P{core.program_id}.json").read_text())
        selected = selected_regulators(bundle)
        if not selected:
            continue
        evidence = "No program research available."
        responses = []
        for effect in bundle.get("regulator_effects_all_conditions", []):
            if not effect.get("available"):
                direction = "unavailable"
            elif not effect.get("significant"):
                direction = "no statistically supported change"
            else:
                direction = (
                    "increased"
                    if effect["log2fc"] > 0
                    else "decreased"
                    if effect["log2fc"] < 0
                    else "unchanged"
                )
            responses.append(
                dict(gene=effect["gene"], condition=effect["condition"], response=direction)
            )
        regulator_context = json.dumps(
            dict(selected_genes=selected, observed_program_responses=responses), indent=2
        )
        if support.exists():
            raw = json.loads(support.read_text())
            evidence = json.dumps(
                {k: raw.get(k, []) for k in ("regulator_coverage", "evidence", "evidence_gaps", "contradictions")},
                indent=2,
            )
        prompt = """The following program interpretation is finalized. You cannot edit its label,
overview, modules, summary or genes. Explain the selected perturbation regulators only.
Return JSON only: {"regulators": [{"gene": "exact input gene symbol, original case",
"confidence": "High|Medium|Low", "mechanism": "1-2 sentences: tentative mechanism and limits"}]}.
Include EVERY selected regulator once. No aliases in gene fields. Do not restate
observed knockdown directions, effect sizes, significance, roles or condition comparisons.
These measured observations are inserted by code. Do not claim an age/sex interaction.
Describe gene functions and tentative links to this program. Do not recount knockdown,
knockout, silencing or depletion experiments in this prose, including literature experiments;
summarize their mechanistic implication with attribution and retain supplied citations.
For unresearched genes or genes with no retrieved mechanistic support, explicitly say
the mechanism is unresolved. General gene function is not evidence of how it controls
this program. Research candidates below may be biased; do not adopt their program labels.
If a regulator is not among the program genes, do not describe it as program expression.
Use research qualifications where helpful, but do not reproduce audit notes, ratings,
or gap/contradiction lists in the annotation. Keep relevant uncertainty concise.
"""
        prompt += "\nFINALIZED PROGRAM:\n" + core.model_dump_json(indent=2)
        prompt += "\nSELECTED REGULATORS AND QUALITATIVE RESPONSES:\n" + regulator_context
        prompt += "\nPROGRAM AND REGULATOR RESEARCH:\n" + evidence
        request["params"]["messages"][0]["content"] = prompt
        config = dict(request["params"].get("output_config", {}))
        config["format"] = output_format(RegulatorSupplement)
        request["params"]["output_config"] = config
        selected_requests.append(request)
    result["requests"] = selected_requests
    return result


def merge_supplements(core_path, supplement_path, output_path, *, expected_ids=None):
    core_rows = [json.loads(line) for line in Path(core_path).read_text().splitlines()]
    core_ids = {row["custom_id"] for row in core_rows}
    # The caller must explicitly declare skipped programs. Missing responses for
    # requested supplements remain an error, including in mixed runs.
    expected = core_ids if expected_ids is None else set(expected_ids)
    if not expected <= core_ids:
        raise ValueError("Regulator request has no functional annotation")
    extras = {}
    for line in Path(supplement_path).read_text().splitlines():
        row = json.loads(line)
        if row["custom_id"] in extras:
            raise ValueError("Duplicate regulator supplement")
        if row.get("result", {}).get("type") != "succeeded":
            raise ValueError("Regulator synthesis failed")
        message = row["result"]["message"]
        if message.get("stop_reason") != "end_turn":
            raise ValueError("Regulator synthesis incomplete")
        text = "".join(b.get("text", "") for b in message["content"] if b.get("type") == "text")
        extras[row["custom_id"]] = RegulatorSupplement.model_validate(load_model_json(text))
    if set(extras) != expected:
        raise ValueError("Missing or unexpected regulator supplement")
    for row in core_rows:
        message = row["result"]["message"]
        text = "".join(b.get("text", "") for b in message["content"] if b.get("type") == "text")
        a = parse_annotation(text, int(re.search(r"topic_(\d+)", row["custom_id"]).group(1)))
        if a.regulators:
            raise ValueError("Functional pass introduced regulators")
        if row["custom_id"] not in expected:
            continue  # Preserve the original functional response, including usage.
        a.regulators = extras[row["custom_id"]].regulators
        message["content"] = [{"type": "text", "text": a.model_dump_json()}]
        # Raw per-call usage remains in the separate original result files.
        message.pop("usage", None)
    Path(output_path).write_text("\n".join(json.dumps(row) for row in core_rows) + "\n")
