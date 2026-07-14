# Gene Program Interpreter — Architecture Narration

**Target length:** about 6 minutes at a measured scientific-talk pace  
**Narrative arc:** hook → mechanism → evidence → application  
**How to present:** open the architecture HTML, click **Run the pipeline**, and pause on each numbered stage as you reach it below.

## Full talk track

### 0:00–0:35 — Hook: the interpretation gap

**On screen:** Title and full architecture.

> A gene program starts as a weighted list of genes. The computational signal is real, but the biological meaning is not yet obvious. A generic enrichment label may tell us that a program involves metabolism or stress, but it does not explain what the program is doing in this particular cell type, condition, or experiment. And if we ask a language model directly, we may get a fluent interpretation without a reliable path back to the literature.
>
> The Gene Program Interpreter is designed to solve both problems: biological specificity and evidence traceability.

### 0:35–1:10 — The organizing idea: context travels with the data

**On screen:** Click **Trace injected context** and point across the dark context rail.

> The dark rail across the top is the central idea of the architecture. Experimental context is not added once at the end. It travels with the program through the whole pipeline.
>
> That context includes the organism, tissue, cell type, biological conditions, and assay. For Perturb-seq, we also inject regulator effects and their direction. This matters because the same genes can mean different things in a hepatocyte, a T cell, or a tumor cell. With the context attached, the system interprets a program as a response in a specific biological system, rather than as a generic gene list.

### 1:10–1:50 — Stage 1: biological inputs

**On screen:** Pause on **01 — Biological inputs**.

> The first input is a gene-loading matrix from cNMF, NMF, or another factorization method. It tells us which genes define each program and how strongly they contribute.
>
> The second input is the context profile. An optional third input contains regulator effects from Perturb-seq: which perturbations activate or repress a program, and by how much.
>
> Together, these inputs let us ask a more useful question. We are no longer asking only, “What do these genes have in common?” We are asking, “What regulatory response do these genes and perturbations represent in this experimental context?”

### 1:50–2:35 — Stage 2: deterministic evidence pack

**On screen:** Move to **02 — Evidence pack**.

> Before an agent reads the literature, deterministic code prepares one compact evidence pack for every program. It validates the inputs, ranks the top-loading and distinctive genes, retrieves STRING enrichment and interactions, and gathers NCBI gene summaries.
>
> The output is one structured JSON bundle per program. Each bundle contains the genes, pathways, regulators, effect direction, quality-control information, and the full context profile.
>
> This step is important because it is reproducible and inspectable. The research agent receives a focused scientific brief instead of an unstructured data dump.

### 2:35–3:35 — Stage 3: parallel Agent SDK literature research

**On screen:** Move to **03 — Parallel literature research** and point to the four agent boxes.

> The pipeline now fans out. Every gene program receives its own isolated Claude Agent SDK research session. Python controls the concurrency with asyncio and a semaphore, so there is no manager agent passing information between programs.
>
> Each research agent sees only its own program bundle. It chooses the literature queries, searches through read-only tools connected to PubMed, OpenAlex, and Crossref, and returns a small set of candidate mechanisms with supporting genes, regulators, and papers.
>
> The literature tools run through an in-process MCP server. This keeps the research interface controlled and auditable, while still allowing the agent to adapt its search strategy to the biology of each program. Because programs are independent, this research runs in parallel rather than one program at a time.

### 3:35–4:35 — Stage 4: evidence verification and batch annotation

**On screen:** Move to **04 — Verify and annotate**. Point first to the black verification gate, then to the blue Batch section.

> The parallel results now converge through a deterministic trust gate. Code checks whether every PMID or DOI resolves, removes duplicate evidence, records retraction signals, and assigns each proposed mechanism a supported, partial, or unsupported status. Contradictions and evidence gaps remain visible instead of being silently removed.
>
> This is an important division of labor: agents decide what to investigate, while deterministic code checks whether the cited identifiers are real.
>
> After verification, the evidence is sent to the Anthropic Message Batch API. One request per program generates the detailed annotation, cross-program themes, and concise presentation text. Batch processing makes these independent LLM transformations efficient and consistent.
>
> One honest boundary is worth stating: the verifier confirms citation identity and status. It does not yet prove full semantic entailment between every sentence and every paper.

### 4:35–5:35 — Stage 5: the evidence-linked report

**On screen:** Move to **05 — Evidence-linked report**. Click the example PMID if appropriate.

> The final output is an interactive HTML report. It gathers the program annotation, mechanisms, top genes, pathways, regulator effects, plots, evidence status, contradictions, and gaps in one place.
>
> The Program 22 example shows the biological payoff. Instead of calling it simply a lipid program, the combined gene weights, hepatocyte context, regulator evidence, and literature support identify a lipogenic and detoxification response. In its research result, three candidate mechanisms are connected to twenty-six verified evidence records.
>
> Every displayed PMID or DOI is one click away from the source. That makes the report useful not only for exploration, but also for checking an interpretation, discussing it with collaborators, and carrying evidence into the next experiment.

### 5:35–6:00 — Close

**On screen:** Return to the full architecture and point to the blue closing statement.

> The architecture can be summarized in one line: intelligence fans out, and trust converges. The input is a set of weighted gene programs plus experimental context and optional regulator effects. The output is a context-aware, evidence-linked biological interpretation that a scientist can inspect, challenge, and cite.

## Rehearsal card

- **Input:** weighted gene programs + context profile + optional Perturb-seq regulator effects.
- **Deterministic preparation:** validation, gene ranking, STRING enrichment, NCBI summaries, one bundle per program.
- **Parallel intelligence:** one isolated Agent SDK literature researcher per program through PubMed, OpenAlex, and Crossref.
- **Trust gate:** identifier resolution, deduplication, retraction checks, evidence status, contradictions, and gaps.
- **Batch synthesis:** annotation, theme extraction, and presentation text.
- **Output:** an interactive HTML report with annotations, plots, evidence, and one-click PMID/DOI links.
- **Closing line:** “Intelligence fans out. Trust converges.”

## Optional shorter close

> The system does not replace scientific judgment. It compresses the work needed to reach an evidence-backed hypothesis, while keeping the context and the sources visible enough for a scientist to judge for themselves.
