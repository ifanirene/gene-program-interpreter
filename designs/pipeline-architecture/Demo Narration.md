# Gene Program Interpreter — Demo Narration

**Runtime ≈ 2 min · manual scroll.** Read one block per screen as you scroll it into
view; pause on the highlighted stages (01, 03, 05, 06). Cues in **bold** say what should
be on screen as you speak.

---

**▶ Title on screen — "Gene Program Interpreter"**

> This is the Gene Program Interpreter — it turns weighted gene programs into biological
> interpretation, where every claim links to a real, verified citation. Let me trace one
> real program, Program 10, from start to finish.

**▶ Scroll to · 01 Context & regulator injection** *(highlight)*

> We start by capturing biological context. Each weighted gene program — here, from mouse
> hepatocyte Perturb-seq — is packaged into a self-contained brief: its marker genes, the
> cell-type context and the vocabulary of normal liver function, and its activating and
> repressing regulators. This brief is the only thing the research agent will see.

**▶ Scroll to · 02 STRING evidence collection**

> Next we gather conventional STRING evidence — GO and KEGG functional enrichment over the
> genes, and the protein–protein interaction network. Here it links Program 10's
> lipid-metabolism regulators to their confidence-scored targets, converging on a shared
> Ppara hub — real associations, computed before any model runs.

**▶ Scroll to · 03 Parallel literature research** *(highlight)*

> Then, the literature. One isolated Claude agent per program runs in parallel, orchestrated
> by Python — there is no manager agent. Each agent searches PubMed, OpenAlex, and Crossref
> on its own, and returns candidate mechanisms with the papers that back them.

**▶ Scroll to · 04 Deterministic verification**

> Every identifier those agents return is verified — resolved against CrossRef and NCBI, and
> checked for retraction. For Program 10, thirty of thirty citations resolved, none
> fabricated. A paper that disagrees is kept on purpose, and genes with no evidence are
> logged as gaps.

**▶ Scroll to · 05 Batch annotation & synthesis** *(highlight)*

> The verified evidence, plus the STRING enrichment, is synthesized through the Anthropic
> Batch API into functional modules and a cross-program label. Research runs on the Claude
> subscription; batch synthesis on API credit.

**▶ Scroll to · 06 One-click evidence report** *(highlight — click a citation live)*

> And it all lands in one interactive report. Each module is colored by evidence status, and
> every claim is a single click from the primary paper — PMID, DOI, straight to PubMed.
> That's the point: an interpretation a collaborator can actually check.

**▶ Scroll to · footer / legend**

> From three hundred anonymous genes to a named, cited, and checkable biology — grounded in a
> real run, with no fabricated citations.

---

### Optional live beats
- On **02**, trace one edge — e.g. `Dgat2 → Ppara` (STRING score 726) — and point out that
  edge width encodes confidence; the whole network comes from the run, not a stock image.
- On **03**, point at the three agent lanes ticking through `search_pubmed → fetch_pubmed →
  resolve_doi → submit_result` — that's the parallel fan-out.
- On **06**, actually click **PMID 29059455**; it expands to the real paper (*Angiocrine Wnt
  signaling controls liver growth…*, Hepatology 2018) with a live PubMed link.
- For a hands-free run, press **Follow Program 10** instead of scrolling (it auto-advances;
  ask me to re-pace it to match this script).
