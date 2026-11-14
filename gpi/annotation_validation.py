"""Fail publication of regulator statistics copied into free prose.

Quantitative regulator headings are replaced from measured inputs. This checks
format and gene membership, not whether a literature mechanism is biologically true.
"""

import json
import re
from pathlib import Path

import pandas as pd

from .html_report import parse_regulators_detailed, measured_regulator_cards, split_final_modules
from .column_mapper import collapse_regulator_guides

_STAT = re.compile(r"log[₂]?2?FC|adj(?:usted)?[ _-]*p(?:val(?:ue)?)?\s*[=<>≤≥]", re.I)


def finalize_regulator_annotations(
    directory, regulator_data, program_genes, masked_regulators=None, program_membership=None
):
    """Publish annotations only when they satisfy every content guard.

    ``program_genes`` is the top-loading + program-unique EMPHASIS shortlist. It drives
    symbol canonicalization, where a small, high-confidence symbol list is what you want.

    ``program_membership`` is the authoritative MEMBERSHIP set per program -- every gene the
    loading table supplies for that program (300 here), from
    ``gpi.evidence_context.program_membership_genes``. Module ``key_genes`` are checked
    against it. Validating module genes against the emphasis shortlist instead rejects real
    program members: P48's Rhoc (loading rank 32), Pfn1 (38) and Fscn1 (46) are all genuine
    members that fall outside the 23-gene shortlist, and the STRING enrichment member-gene
    lists shown in the prompt are drawn from this same membership set. When
    ``program_membership`` is omitted the check falls back to ``program_genes``, preserving
    the older behaviour for callers that do not supply it.
    """
    errors = []
    updates = {}
    for path in sorted(Path(directory).glob("topic_*_annotation.md")):
        pid = int(re.search(r"topic_(\d+)", path.name).group(1))
        text = path.read_text()
        structured = path.with_suffix(".json")
        if structured.exists():
            from .annotation_contract import parse_annotation, render_annotation
            from .evidence_context import select_top_condition_regulators

            annotation = parse_annotation(structured.read_text(), pid)
            selected = (
                {
                    r["gene"]
                    for groups in select_top_condition_regulators(regulator_data, pid).values()
                    for rows in groups.values()
                    for r in rows
                }
                if regulator_data
                else set()
            )
            symbols = {g.casefold(): g for g in program_genes.get(pid, [])}
            symbols.update({g.casefold(): g for g in selected})

            def canonical(value):
                if isinstance(value, str):
                    for key, gene in sorted(symbols.items(), key=lambda item: -len(item[0])):
                        value = re.sub(
                            r"(?<![\w])" + re.escape(key) + r"(?![\w])", gene, value, flags=re.I
                        )
                    return value
                if isinstance(value, list):
                    return [canonical(v) for v in value]
                if isinstance(value, dict):
                    return {k: canonical(v) for k, v in value.items()}
                return value

            annotation = type(annotation).model_validate(canonical(annotation.model_dump()))
            returned = {r.gene for r in annotation.regulators}
            if returned != selected:
                errors.append(
                    dict(
                        program_id=pid,
                        issue="Regulator set differs from selected inputs",
                        missing=sorted(selected - returned),
                        unexpected=sorted(returned - selected),
                    )
                )
            for module in annotation.modules:
                # Membership, not emphasis. Genes outside THIS program's supplied loading
                # set are still rejected, including a gene that belongs only to another
                # program and a gene absent from the loading table altogether.
                allowed_members = set(
                    (program_membership or {}).get(pid) or program_genes.get(pid, [])
                )
                distinct = set(module.key_genes)
                outside = sorted(distinct - allowed_members)
                if len(distinct) < 2 or outside:
                    errors.append(
                        dict(
                            program_id=pid,
                            issue="Module genes must be at least 2 distinct genes of this program",
                            module=module.name,
                            n_distinct_genes=len(distinct),
                            genes_outside_program=outside,
                        )
                    )
            source_path = Path(directory).parent / "research_results" / f"P{pid}.json"
            if source_path.exists():
                source = json.loads(source_path.read_text())
                if source.get("candidate_mechanisms"):
                    supplied_pmids = {
                        str(e.get("pmid")) for e in source.get("evidence", []) if e.get("pmid")
                    }
                    unknown = {
                        pmid for m in annotation.modules for pmid in m.pmids
                    } - supplied_pmids
                    if unknown:
                        errors.append(
                            dict(
                                program_id=pid,
                                issue="Module cites identifiers absent from supplied research",
                                identifiers=sorted(unknown),
                            )
                        )
            prose = " ".join(
                [
                    annotation.brief_summary,
                    annotation.overview,
                    annotation.distinctive,
                    *[m.summary + " " + m.evidence for m in annotation.modules],
                    *[r.mechanism for r in annotation.regulators],
                ]
            )
            for sentence in re.split(r"(?<=[.!?])\s+", prose):
                if re.search(
                    r"knock(?:down|out)|silenc(?:ing|ed)|deplet(?:ion|ed)", sentence, re.I
                ) and re.search(
                    r"increas|decreas|reduc|enhanc|suppress|activat|repress|upregulat|downregulat",
                    sentence,
                    re.I,
                ):
                    errors.append(
                        dict(
                            program_id=pid,
                            issue="Model restates observed perturbation direction; use measured output",
                            excerpt=sentence,
                        )
                    )
                if re.search(
                    r"age.?[×x].?sex|age.sex interaction|sex.age interaction", sentence, re.I
                ):
                    errors.append(
                        dict(
                            program_id=pid, issue="Unsupported interaction claim", excerpt=sentence
                        )
                    )
            text = render_annotation(annotation)
            updates[structured] = annotation.model_dump_json(indent=2)
        cards = parse_regulators_detailed(text)
        mask = {g.casefold() for g in (masked_regulators or [])}
        for card in cards:
            if card["gene"].casefold() in mask:
                errors.append(
                    dict(
                        program_id=pid,
                        issue="Excluded regulator reintroduced in annotation",
                        gene=card["gene"],
                    )
                )
        frames = {
            c: collapse_regulator_guides(
                d.get(pid, pd.DataFrame(columns=["target_gene"])), significant_only=False
            )
            for c, d in regulator_data.items()
        }
        measured = measured_regulator_cards(cards, frames)
        genes = {r["gene"] for r in cards}
        # A model may supply arbitrary role/range header text; only data-driven
        # headings are published. Remaining numeric claims require correction.
        section = text.partition("## Regulator analysis")[2]
        for paragraph in re.split(r"\n\s*\n", text):
            for gene in genes:
                paragraph = re.sub(rf"(?m)^[ \t]*(?:\*\*)?{re.escape(gene)}\s*\(.*$", "", paragraph)
            if _STAT.search(paragraph) and (
                "Mechanistic hypothesis:" in paragraph
                or "mechanistic hypothesis:" in paragraph
                or paragraph in section
                or any(re.search(rf"\b{re.escape(g)}\b", paragraph) for g in genes)
            ):
                errors.append(
                    dict(
                        program_id=pid,
                        issue="Regulator statistics in prose; keep statistics in the measured heading",
                        excerpt=paragraph[:200],
                    )
                )
        _, modules = split_final_modules(text)
        # Markdown-level repeat of the module-gene rule, read back from the rendered report.
        # It must use the SAME membership universe as the JSON-level check above, or a module
        # built on real program genes outside the emphasis shortlist would pass one and fail
        # the other.
        allowed = {
            g.casefold()
            for g in ((program_membership or {}).get(pid) or program_genes.get(pid, []))
        }
        for module in modules:
            # Report parser exposes member genes; require genuine genes of this program.
            members = module.get("key_genes", [])
            if len({g.casefold() for g in members} & allowed) < 2:
                errors.append(
                    dict(
                        program_id=pid,
                        issue="Module has fewer than two genes of this program",
                        module=module.get("title", ""),
                    )
                )
        if structured.exists() and any(
            re.search(r"(?<![\w])" + re.escape(g) + r"(?![\w])", text, re.I)
            for g in (masked_regulators or [])
        ):
            errors.append(
                dict(program_id=pid, issue="Excluded identity reintroduced in annotation text")
            )
        for card in measured:
            if not frames:
                continue
            pattern = rf"(?mi)^([ \t]*(?:\*\*)?{re.escape(card['gene'])}\s*\().*$"
            heading = f"{card['gene']} ({card['role']}, log2FC={card.get('fc', 'unavailable')}): [Confidence: {card.get('confidence', 'not assessed')}]"
            text = re.sub(
                pattern,
                lambda match: (
                    ("**" + heading + "**") if match.group(0).lstrip().startswith("**") else heading
                ),
                text,
            )
        if structured.exists() and measured:
            observations = [
                f"- {card['gene']}: {card['observation']}"
                for card in measured
                if card.get("observation")
            ]
            text += "\n\n## Measured perturbation responses\n\n" + "\n".join(observations) + "\n"
        updates[path] = text
    audit = Path(directory) / "regulator_validation.json"
    audit.write_text(json.dumps(dict(passed=not errors, errors=errors), indent=2))
    if errors:
        raise ValueError(f"Regulator annotation validation failed; see {audit}")
    for path, text in updates.items():
        path.write_text(text)
    return str(audit)
