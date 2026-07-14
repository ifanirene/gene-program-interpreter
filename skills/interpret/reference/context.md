# Reference — biological context

Everything the research agents see is derived from the `context:` block. Get it wrong and you
pay for searches that return nothing. This is the highest-leverage thing in the whole run.

## Organism / taxid

`species_taxid` drives STRING enrichment **and** NCBI gene summaries. If it disagrees with
`organism`, enrichment silently runs against the wrong proteome.

| Organism | `species_taxid` |
|---|---|
| human | `9606` |
| mouse | `10090` |
| rat | `10116` |
| zebrafish | `7955` |
| fruit fly (*D. melanogaster*) | `7227` |
| nematode (*C. elegans*) | `6239` |
| yeast (*S. cerevisiae*) | `4932` |
| chicken | `9031` |
| *Xenopus tropicalis* | `8364` |
| pig | `9823` |
| macaque (*M. mulatta*) | `9544` |

## `conditions:` — the condition menu

**Ask. Never assume homeostasis.** A gene program in a developing tissue means something
entirely different from the same program at steady state, and the research agents will chase
the wrong literature.

Offer these as a **multi-select** — real experiments are usually more than one:

| Condition | Use when |
|---|---|
| `homeostasis` | adult, unperturbed, steady state |
| `development` | embryonic or postnatal; anything still maturing |
| `aging` | aged vs. young comparison |
| `injury` / `regeneration` | wounding, ischemia-reperfusion, resection, repair |
| `disease (name it)` | **name the actual disease** — `MASLD`, `glioblastoma`, not "disease" |
| `hypoxia` / `ischemia` | low oxygen, stroke, tumour core |
| `inflammation` | LPS, cytokine challenge, immune infiltration |
| `genetic perturbation` | Perturb-seq, CRISPR screen, knockout |

A postnatal Perturb-seq screen is **both** `development` **and** `genetic perturbation`. Pick
both. Conditions are short labels — put the *biology* in `context_terms`.

`conditions` feed `keyword_query` and `condition_context` (as "with attention to X and Y"), so
they steer the search without dominating it. Keep the cell type's **normal** function in
`context_terms`; keep the perturbation/disease emphasis in `conditions`.

## `context_terms:` — and why they must be short

### The mechanism (this is the whole reason for the rules)

`gpi/context_profile.py::_quote_term` phrase-quotes any term containing **whitespace, parens,
or a slash**:

```python
if re.search(r"[\s()/]", term):
    return f'"{term}"'
```

The terms are then OR-joined into `keyword_query`:

```
(endothelial OR brain OR "blood-brain barrier" OR angiogenesis OR "tip cells")
```

So **every multi-word term is sent to PubMed as a literal phrase.** PubMed will match that
phrase, verbatim, in the exact word order. That is *great* for a real term of art and
*catastrophic* for a sentence.

### The rules

**1–3 words. No conjunctions. 6–8 terms maximum.**

| | Term | As sent to PubMed | Hits |
|---|---|---|---|
| ✅ | `blood-brain barrier` | `"blood-brain barrier"` | ~100k — a real term of art |
| ✅ | `angiogenesis` | `angiogenesis` (unquoted, no whitespace) | ~500k |
| ✅ | `tip cells` | `"tip cells"` | thousands |
| ❌ | `tight junctions and paracellular permeability` | `"tight junctions and paracellular permeability"` | **~0.** A dead slot. |
| ❌ | `TGF-beta and BMP signalling in endothelium` | the whole sentence, quoted | **~0.** |

A dead slot is not free — it occupies one of ~8 search terms the user is paying an agent to
work through. Two dead slots and a quarter of the run's search budget is spent on nothing.

**The tell is a conjunction.** `and`, `or`, `in`, `of`, `with` inside a term almost always means
you have written a sentence, not a search term. Split it into two terms:
`tight junctions` + `paracellular permeability`.

### Vocabularies by tissue

Good starting sets — 1–3 words each, describing the cell type's **normal** biology:

**Brain endothelium** — `blood-brain barrier`, `arteriovenous zonation`, `tip cells`,
`angiogenesis`, `tight junctions`, `transcytosis`, `pericyte crosstalk`

**Liver / hepatocyte** — `metabolic zonation`, `bile acid metabolism`, `lipogenesis`,
`gluconeogenesis`, `xenobiotic metabolism`, `acute phase response`

**Immune / T cell** — `T cell exhaustion`, `cytotoxicity`, `antigen presentation`,
`clonal expansion`, `memory differentiation`

**Fibroblast / stroma** — `extracellular matrix`, `myofibroblast`, `collagen deposition`,
`wound healing`, `TGF-beta signalling` *(3 words, no conjunction — fine)*

**Generic fallbacks (any tissue)** — `cell cycle`, `oxidative phosphorylation`,
`unfolded protein response`, `interferon response`, `apoptosis`, `epithelial mesenchymal
transition`

Note `TGF-beta signalling` is fine (a real phrase) while `TGF-beta and BMP signalling in
endothelium` is not. The difference is whether the phrase is one people actually write.

## Verifying before you spend

`--emit-config` and `--dry-run` both **print the derived `keyword_query`** (from
`_print_framing`). Show it to the user. It is the only way to see a dead slot before paying for
it — the query is what the agents actually search.

```
keyword_query    : (endothelial cell OR brain OR development OR "blood-brain barrier" OR ...)
```

Read it back term by term. Anything that looks like a sentence is a dead slot. Fix it and
re-emit — both commands are free.

## Other framing strings

All are auto-derived from the structured fields when left blank; override only with reason.

| Field | Derived as | Notes |
|---|---|---|
| `annotation_role` | `"<cell_type> biologist"` | deliberately neutral — never disease-loaded, never the assay |
| `annotation_context` | `"a consensus gene expression program in <organism> <cell_type>s, in the context of <conditions>"` | never mentions the assay |
| `keyword_query` | `(cell_type OR tissue OR conditions… OR context_terms…)` | the one to check |
| `condition_context` | leads with **normal** biology from `context_terms`, then `"with attention to <conditions>"` | keeps disease secondary so normal function is still found |
| `report_dataset_crumb` | `"<organism> <tissue> <assay>"` | hero kicker in the HTML report |
