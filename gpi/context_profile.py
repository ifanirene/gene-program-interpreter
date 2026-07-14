"""
Tissue-agnostic experimental-context profile.

This is the generalization linchpin. ProgExplorer hard-coded a liver/MASLD context
in five constants and a prompt sentence:

    DEFAULT_ANNOTATION_ROLE     = "hepatocyte aging and MASLD biologist"
    DEFAULT_ANNOTATION_CONTEXT  = "a consensus gene expression program from in vivo
                                   Perturb-seq of hepatocyte regulators, interpreted in
                                   aging and MASLD context"
    DEFAULT_SEARCH_KEYWORD      = '("hepatocyte" OR hepatocytes OR liver OR ... OR aging)'
    LIVER_DISEASE_CONTEXT       = "Context: in vivo Perturb-seq of hepatocyte regulators;
                                   liver tissue; ...; fibrosis."
    LIVER_FUNCTIONAL_CONTEXT    = ""

`ContextProfile` carries *structured* fields (organism, tissue, cell_type,
conditions, context_terms, assay). The four framing strings the annotation prompt and
research agents need are **derived** from those fields when left blank, so:

  * a liver profile reproduces the original liver text (see `ContextProfile.liver_demo()`), and
  * any other tissue/condition works with no code change — you only change the profile.

Every derived string can also be set explicitly; an explicit value always wins.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# Fixed vocabulary for a claim's context match (spec §5). The adapter validates
# research evidence against this set; it is NOT tissue-specific.
DEFAULT_EVIDENCE_CONTEXT_TYPES: List[str] = ["direct", "partial", "indirect", "mixed"]

# Advisory thresholds for `ContextProfile.validate()`. Warnings only — never blocking.
# A concept longer than 3 words is phrase-matched literally by PubMed and returns ~0 hits
# ("BMP signalling in endothelium" is not a phrase anyone writes), hence "prefer 1-3 words".
MAX_TERM_WORDS: int = 3
MAX_CONTEXT_TERMS: int = 10  # beyond this the OR-query is diluted

# Conjunctions inside a context term that really mean "OR". A multi-word term is sent
# to PubMed as a literal phrase, so "tight junctions and paracellular permeability"
# phrase-matches almost nothing — a dead slot that still costs a research turn. Split
# such a term into separately-quoted concepts *for the query only*.
# `-` is deliberately NOT a delimiter: "TGF-beta" and "blood-brain" must survive whole.
_CONJUNCTION_RE = re.compile(r"\s+(?:and|&)\s+|\s*/\s*", re.IGNORECASE)

# Connective fragments left behind by a split; they carry no query signal.
_STOP_FRAGMENTS = frozenset({"a", "an", "and", "or", "the", "&"})

# Organism <-> NCBI taxid pairs we can actually check. Anything else is left alone.
_ORGANISM_TAXIDS: Dict[str, int] = {
    "human": 9606,
    "homo sapiens": 9606,
    "mouse": 10090,
    "murine": 10090,
    "mus musculus": 10090,
}
_TAXID_NAMES: Dict[int, str] = {9606: "human", 10090: "mouse"}


def _quote_term(term: str) -> str:
    """Quote a term for a boolean keyword query if it contains whitespace/punct."""
    term = term.strip()
    if not term:
        return ""
    if re.search(r"[\s()/]", term):
        return f'"{term}"'
    return term


def _split_conjunctions(term: str) -> List[str]:
    """Split one context term into the separate concepts a boolean query should OR.

        "tight junctions and paracellular permeability"
            -> ["tight junctions", "paracellular permeability"]

    Query-side only — the caller must not write the result back onto the profile, since
    the raw term is also prose for the research agent (`functions_to_consider`), where
    "TGF-beta and BMP signalling in endothelium" is perfectly good English.

    A single-concept term is returned unchanged, and a hyphenated token is never broken.
    """
    concepts: List[str] = []
    for part in _CONJUNCTION_RE.split(term or ""):
        part = part.strip(" \t,;")
        if not part or part.lower() in _STOP_FRAGMENTS:
            continue
        concepts.append(part)
    return concepts


@dataclass
class ContextProfile:
    """Experimental context for one gene-program dataset.

    Structured fields describe the biology; the four framing strings
    (`annotation_role`, `annotation_context`, `keyword_query`, `condition_context`)
    are auto-derived from them when blank. Use `.resolved()` to materialize a copy
    with every blank filled, or the `resolved_*` accessors individually.
    """

    # --- Organism ---
    organism: str = "mouse"           # human-readable, e.g. "mouse", "human"
    species_taxid: int = 10090        # NCBI taxid; STRING enrichment + NCBI gene (9606 human, 10090 mouse)

    # --- Biological context ---
    tissue: str = ""                  # e.g. "liver"
    cell_type: str = ""               # e.g. "hepatocyte"
    conditions: List[str] = field(default_factory=list)     # short labels, e.g. ["aging", "MASLD"]
    context_terms: List[str] = field(default_factory=list)  # extra domain terms for the condition block
    assay: str = "Perturb-seq"        # e.g. "in vivo Perturb-seq"

    # --- LLM framing (blank => derived from the fields above) ---
    annotation_role: str = ""         # replaces DEFAULT_ANNOTATION_ROLE
    annotation_context: str = ""      # replaces DEFAULT_ANNOTATION_CONTEXT & PROMPT_TEMPLATE opener
    keyword_query: str = ""           # replaces DEFAULT_SEARCH_KEYWORD (PubMed-style OR query)
    condition_context: str = ""       # replaces LIVER_DISEASE_CONTEXT block
    functional_context: str = ""      # replaces LIVER_FUNCTIONAL_CONTEXT (usually "")
    report_dataset_crumb: str = ""    # hero kicker in the HTML report

    # --- Evidence vocabulary ---
    evidence_context_types: List[str] = field(
        default_factory=lambda: list(DEFAULT_EVIDENCE_CONTEXT_TYPES)
    )

    # ------------------------------------------------------------------ derivations
    def _subject(self) -> str:
        """The biological subject noun: cell_type, else tissue, else organism."""
        return self.cell_type or self.tissue or self.organism or "cell"

    def resolved_annotation_role(self) -> str:
        # Deliberately neutral: no disease-loaded persona (e.g. NOT "aging and
        # MASLD biologist") and never the assay. Just the cell/tissue domain.
        if self.annotation_role:
            return self.annotation_role
        subject = self.cell_type or self.tissue
        if subject:
            return f"{subject} biologist"
        return "cell and molecular biologist"

    def resolved_annotation_context(self) -> str:
        # Agent-/prompt-facing framing. Does NOT mention the assay (e.g. never
        # "in vivo Perturb-seq") and leads with the cell type, not "regulators".
        if self.annotation_context:
            return self.annotation_context
        subject = self._subject()
        organism = f"{self.organism} " if self.organism else ""
        base = f"a consensus gene expression program in {organism}{subject}s"
        if self.conditions:
            base += f", in the context of {' and '.join(self.conditions)}"
        return base

    def resolved_keyword_query(self) -> str:
        # The query is handed to PubMed literally, so every term is split on its
        # conjunctions first (see `_split_conjunctions`) and each concept is quoted on
        # its own. `context_terms` itself is NEVER rewritten — the raw phrasing is the
        # prose the research agent reads.
        if self.keyword_query:
            return self.keyword_query
        terms: List[str] = []
        seen: Set[str] = set()
        for t in [self.cell_type, self.tissue, *self.conditions, *self.context_terms]:
            for concept in _split_conjunctions(t):
                q = _quote_term(concept)
                if q and q.lower() not in seen:
                    seen.add(q.lower())
                    terms.append(q)
        if not terms:
            return ""
        return "(" + " OR ".join(terms) + ")"

    def resolved_condition_context(self) -> str:
        # Leads with NORMAL cell-type biology (from context_terms), then keeps
        # the disease/aging conditions as secondary emphasis. No assay exposed.
        if self.condition_context:
            return self.condition_context
        subject = self._subject()
        segs: List[str] = []
        if self.tissue:
            segs.append(f"{self.tissue} tissue")
        if self.context_terms:
            segs.append(f"{subject} biology spanning {', '.join(self.context_terms)}")
        elif self.cell_type:
            segs.append(f"{self.cell_type} cellular function")
        if not segs and not self.conditions:
            return ""
        head = "Context: " + "; ".join(segs) if segs else "Context"
        if self.conditions:
            head += f"; with attention to {' and '.join(self.conditions)}"
        return head + "."

    def resolved_report_dataset_crumb(self) -> str:
        if self.report_dataset_crumb:
            return self.report_dataset_crumb
        bits = [b for b in [self.organism, self.tissue, self.assay] if b]
        return " ".join(bits)

    def resolved(self) -> "ContextProfile":
        """Return a copy with every derived framing string materialized."""
        data = asdict(self)
        data["annotation_role"] = self.resolved_annotation_role()
        data["annotation_context"] = self.resolved_annotation_context()
        data["keyword_query"] = self.resolved_keyword_query()
        data["condition_context"] = self.resolved_condition_context()
        data["report_dataset_crumb"] = self.resolved_report_dataset_crumb()
        return ContextProfile(**data)

    # ------------------------------------------------------------------ validation
    def validate(self) -> List[str]:
        """Return human-readable warnings about this profile. Never raises, never blocks.

        These are query-quality nudges, not errors: a run with warnings still works, it
        just spends its research budget worse. The caller decides how to surface them
        (`--emit-config`, `doctor`, the skill); this method only reports.
        """
        warnings: List[str] = []

        # Over-specific concepts. Judged AFTER conjunction-splitting, because that is
        # what actually reaches PubMed — a long term that splits cleanly is fine.
        for term in self.context_terms:
            if not str(term).strip():
                warnings.append(
                    "context_terms contains an empty term — remove it; it adds nothing "
                    "to the query and nothing to the research brief."
                )
                continue
            for concept in _split_conjunctions(str(term)):
                n_words = len(concept.split())
                if n_words > MAX_TERM_WORDS:
                    warnings.append(
                        f"context_terms: {concept!r} is {n_words} words — too specific. "
                        "PubMed phrase-matches this literally, so it will return almost "
                        "nothing and the slot is wasted. Prefer 1-3 word concepts "
                        "(e.g. 'tight junctions', 'paracellular permeability')."
                    )

        if len(self.context_terms) > MAX_CONTEXT_TERMS:
            warnings.append(
                f"context_terms has {len(self.context_terms)} entries (> {MAX_CONTEXT_TERMS}) — "
                "this dilutes the query; every term competes for the same retrieval budget. "
                "Keep the highest-signal concepts for this cell type."
            )

        for cond in self.conditions:
            if not str(cond).strip():
                warnings.append("conditions contains an empty entry — remove it.")

        # organism vs. species_taxid. Wrong taxid silently sends STRING enrichment and
        # NCBI gene lookups to the wrong species. Only checked for organisms we know.
        expected = _ORGANISM_TAXIDS.get(self.organism.strip().lower())
        if expected is not None and int(self.species_taxid) != expected:
            actual = _TAXID_NAMES.get(int(self.species_taxid))
            actual_desc = f" ({actual})" if actual else ""
            warnings.append(
                f"organism is {self.organism!r} but species_taxid is {self.species_taxid}"
                f"{actual_desc} — enrichment and gene lookups would use the wrong species. "
                f"Set species_taxid: {expected}."
            )

        # An empty query means the literature agents retrieve nothing on-context at all.
        if not self.resolved_keyword_query():
            warnings.append(
                "the literature query is empty — set cell_type / tissue / context_terms, "
                "or the research step has nothing on-context to search for."
            )

        return warnings

    # ------------------------------------------------------------------ prompt hook
    def prompt_fields(self) -> Dict[str, str]:
        """The substitution values `gpi.evidence_context.generate_prompt` interpolates.

        Keys align with the `PROMPT_TEMPLATE` placeholders / `generate_prompt` replacements:
        `annotation_role`, `annotation_context`, `search_keyword`, `condition_context`
        (was `liver_disease_context`), `functional_context` (was `liver_functional_context`).
        """
        return {
            "annotation_role": self.resolved_annotation_role(),
            "annotation_context": self.resolved_annotation_context(),
            "search_keyword": self.resolved_keyword_query(),
            "condition_context": self.resolved_condition_context(),
            "functional_context": self.functional_context,
        }

    # ------------------------------------------------------------------ (de)serialize
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextProfile":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        # accept a couple of friendly aliases
        aliases = {"species": "species_taxid", "celltype": "cell_type", "assay_type": "assay"}
        clean: Dict[str, Any] = {}
        for k, v in (data or {}).items():
            key = aliases.get(k, k)
            if key in known and v is not None:
                clean[key] = v
        return cls(**clean)

    @classmethod
    def from_yaml(cls, path: Path) -> "ContextProfile":
        import yaml  # local import: keeps module import light

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        # allow the profile to be nested under a "context"/"profile" key
        if isinstance(raw, dict) and "context" in raw and isinstance(raw["context"], dict):
            raw = raw["context"]
        elif isinstance(raw, dict) and "profile" in raw and isinstance(raw["profile"], dict):
            raw = raw["profile"]
        return cls.from_dict(raw)

    # ------------------------------------------------------------------ presets
    @classmethod
    def liver_demo(cls) -> "ContextProfile":
        """Mouse hepatocyte context, framed around NORMAL hepatocyte biology.

        Per user steer: the agent-facing framing leads with normal hepatocyte
        cellular functions (zonation, xenobiotic/bile-acid/nitrogen metabolism,
        transport) rather than a disease checklist; aging + MASLD + lipid
        metabolism are kept as secondary emphasis. The assay is recorded but is
        NOT exposed to the agent (see the resolved_* methods)."""
        return cls(
            organism="mouse",
            species_taxid=10090,
            tissue="liver",
            cell_type="hepatocyte",
            conditions=["aging", "MASLD"],
            context_terms=[
                "metabolic zonation",
                "xenobiotic and drug metabolism",
                "bile acid metabolism",
                "gluconeogenesis and glycogen storage",
                "nitrogen and urea metabolism",
                "oxidative and mitochondrial metabolism",
                "membrane transport and solute carriers",
                "lipid metabolism",
            ],
            assay="in vivo Perturb-seq",  # recorded, but never surfaced to the agent
            report_dataset_crumb="Mouse hepatocyte Perturb-seq",
        )


__all__ = ["ContextProfile", "DEFAULT_EVIDENCE_CONTEXT_TYPES"]
