# Reference — walking the user through `report.html`

`<output_dir>/report.html` is a self-contained interactive report, one card per program. Open it
and **walk the user through it** — do not just hand over a file path and call the job done.

## What a program card contains

| Section | What it is | Where it came from |
|---|---|---|
| **Hero** — label + lead | the program's name and one-paragraph claim | the LLM annotation |
| **At a glance** | top regulators · functional modules · top pathway | mixed |
| **Marker genes** | top-loading genes; **program-unique genes highlighted** | your gene-loading CSV — **deterministic** |
| **Functional modules** | the mechanistic claims, each with an **evidence badge** and linked PMIDs | LLM + literature research + the verifier |
| **Distinctive features** | what sets this program apart from the others | LLM |
| **Top regulators** | perturbations that move this program: gene, `activator`/`repressor`, confidence, log2FC, mechanistic hypothesis | **your regulator CSV if you supplied one — otherwise the model's inference** |
| **Pathways** | STRING enrichment, sorted by FDR | **deterministic** |
| **Cell-type enrichment** | signed log2FC per lineage — **depletion shown as prominently as enrichment** | your cell-type CSV |

Point out which parts are **your data** and which are **the model's inference**. If the user did
not supply a regulators file, say plainly: *"this regulator section is inference, not your
data."* That distinction is the whole point of the tool.

On cell-type enrichment: **a strong depletion often names a program better than an enrichment
does.** A program actively excluded from arterial cells is telling you something as loud as one
that marks capillaries.

## The evidence badges — read this carefully

The badge on each functional module reports **what the verifier could establish**, and the
vocabulary is deliberately three-way. Collapsing it would be its own dishonesty.

| Status | Badge shown | What it actually means |
|---|---|---|
| `supported` | **no badge** | >=1 citation **resolved** against PubMed/Crossref. Silence is the good case. |
| `partial` | **"unverified"** | **The verifier could not reach the source** — network error, rate limit, timeout. The citations are shown **as the model gave them**. |
| `unsupported` | **"no verified evidence"** | No citation survived verification: either none was offered, or every one was **actively refuted** or retracted. |

**The two things to tell the user, in these words:**

1. **No badge means verified.** The badge only appears when something is *wrong*, so an unbadged
   module is the strong case — not an unlabelled one. Users assume the opposite.

2. **`partial` / "unverified" does NOT mean fabricated.** It means **"we could not check."**
   The distinction is real and it is load-bearing:

   - A citation the verifier **actively refuted** (the server said it does not exist) is
     **dropped**, and the module falls to `unsupported`.
   - A citation the verifier **could not reach** (network, 5xx, rate limit) is **kept**, and the
     module is marked `partial` — flagged, not deleted.

   Deleting the unreachable ones would silently destroy real citations. Keeping the refuted ones
   would let fabricated ones through. So the tool does neither, and tells you which is which.

   **"Unverified" is an instruction, not a verdict: confirm it yourself before you cite it.**

`unsupported` is the one to be sceptical of. Treat that mechanism as a **hypothesis, not a cited
finding** — and say so out loud, rather than letting a confident-sounding paragraph stand
unchallenged.

## Trace one claim, end to end — always do this

Do not summarize the report and stop. **Pick one module and walk its whole chain**, so the user
sees that the citation is real and sees exactly where the tool would have caught a fake:

> **Program 48 — "tip-cell / sprouting angiogenesis"**
>
> 1. **Gene** — `Apln`, `Esm1`, `Kcne3` are top-loading, and `Esm1` is **program-unique**
>    (highlighted). Straight from your loading matrix — no model involved.
> 2. **Cell type** — enriched **+4.65** in tip-like endothelium, depleted in arterial. Your
>    cell-type CSV.
> 3. **Regulator** — from your Perturb-seq file: perturbing *X* moves this program (log2FC,
>    confidence). If you supplied no regulator file, **this step is inference — say so.**
> 4. **Literature** — the module cites **PMID 12345678**, which the verifier **resolved**. The
>    module carries **no badge**. Click the PMID; it opens the real paper on PubMed.
>
> Every link is checkable. That is the point: a fabricated PMID would have been **refuted and
> dropped**, and this module would be sitting at `unsupported`.

Clicking through one real PMID in front of the user does more for trust than any summary.

## Where the tool abstained — surface this, do not bury it

The honest parts of the report are the ones that hedge, and they are easy to skim past. Point
them out:

- modules badged **`unverified`** — the tool is telling you it could not check
- modules badged **`no verified evidence`** — the tool is telling you not to trust the citation
- regulators marked **Low confidence**
- programs whose label stayed generic — the evidence did not support a specific claim
- competing hypotheses **retained rather than collapsed** into one confident story

A tool that abstains is working. Say so. A report with no hedges anywhere is more suspicious than
one with a few.

## If `verify` never ran

If the pipeline was run with `--no-research`, or the `verify` step failed and was bypassed, then
**the citations were never checked — and the badges will not tell you that.** Do not present
those PMIDs as verified. Say plainly that verification did not run and that every citation needs
manual confirmation.

This has happened before: a run emitted three real-looking PMIDs whose verifier had never
executed. They happened to be real — but that was luck, and it was only established by checking
them by hand afterwards.
