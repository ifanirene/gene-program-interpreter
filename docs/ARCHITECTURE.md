# Gene Program Interpreter — Architecture & Build Contract

This is the **single source of truth** for everyone (human or minion) implementing a
piece of this project. Read it fully before writing code. If anything here is ambiguous
or contradicts a task spec, **stop and ask the orchestrator** — do not guess.

## Mission

A tissue-agnostic Claude Code **plugin** backed by an installable Python pipeline. The
plugin skill interprets weighted gene programs (from cNMF/NMF/consensus factorization),
while the pipeline keeps ProgExplorer's deterministic fetches + HTML renderer and replaces
its **manual** literature step with **parallel Claude Agent SDK literature agents** (one per
program) + a **deterministic evidence verifier**.

### Firm guardrails (never violate)
1. **Literature research happens ONLY in the research agents, via MCP.** Deterministic
   code may *validate* identifiers (does this PMID/DOI resolve?) but must not *research*.
2. **Parallelism is controlled in Python (`asyncio` + `Semaphore`)**, never by a manager agent.
3. **Anthropic only** (no Vertex / AI-gateway / Bedrock). **Auth is split by executor:**
   the research subagents (②) run the local `claude` CLI on the user's **Claude.ai
   subscription** (`claude login`) — `ANTHROPIC_API_KEY` is *withheld* from the research
   step so the CLI uses the subscription, not API credit. The batch transforms (③,
   annotation/presentation/theme) use the **Batch API** (`client.messages.batches.*`) on
   the **API key**. So: **research → subscription, batch → API credit.** (Override via
   `research.auth: api` in the config to bill research to the API key too.)
4. The Agent SDK runs **locally**.
5. **Source is read-only.** Vendor FROM `/Volumes/IF_PHAGE/ProgExplorer/` (never mutate it).
   **Never** read/touch `/Volumes/IF_PHAGE/GeneScope/` — off-limits.

## Executors (who runs each step)
- **① skill agent** — the user's Claude Code session running `SKILL.md`; interprets context, orchestrates.
- **② research subagents** — Claude Agent SDK sessions, one per program, MCP tool-using, real-time.
- **③ Anthropic Batch** — batchable per-program LLM transforms (annotation, presentation, theme).
- **④ deterministic scripts** — parsing, fetches, bundle assembly, verify+dedup, HTML render.

## Environment & conventions
- **Python:** 3.10+. The Claude plugin uses `uv` to create an isolated, persistent
  environment; `pyproject.toml` is the dependency source of truth and includes
  `claude-agent-sdk`.
- **Import & run convention:** `gpi` and `research` are packages. Use **relative imports
  within a package** (`from .column_mapper import ColumnMapper`). Vendored modules that had a
  CLI keep an `argparse` `main()`; users invoke the `gpi` entry point and the orchestrator
  invokes internal modules as `python -m gpi.<module> ...` (NOT `python gpi/<module>.py`).
  Rewrite every ProgExplorer sibling import
  `from column_mapper import X` → `from .column_mapper import X`.
- **Env keys** live in the analysis project's `.env` (ANTHROPIC_API_KEY, PUBMED_EMAIL,
  NCBI_API_KEY, OPENALEX_API_KEY). Load via `os.environ`; the verifier's DOI check is
  **keyless** (CrossRef + doi.org).
- **Verify every module imports** before handing back with `uv run pytest`.
- **Any LLM-touching test uses a cheap Anthropic model** (Haiku: `claude-haiku-4-5-20251001`)
  or mocks. Do not spend on Opus/Sonnet in tests.

## Module map (ProgExplorer source → target)  — treatment codes: ④=deterministic, ③=Anthropic

| Target | Source (ProgExplorer `pipeline/`) | Treatment |
|---|---|---|
| `gpi/column_mapper.py` | `column_mapper.py` | ④ vendor as-is |
| `gpi/string_api.py` | `string_api.py` | ④ vendor as-is |
| `gpi/ncbi_api.py` | `ncbi_api.py` | ④ vendor as-is (also used by verifier via `esummary`) |
| `gpi/harmonizome_api.py` | `harmonizome_api.py` | ④ vendor as-is |
| `gpi/pipeline_state.py` | `pipeline_state.py` | ④ vendor as-is |
| `gpi/parse_results.py` | `04_parse_and_summarize.py` | ④ vendor as-is |
| `gpi/enrichment.py` | `01_genes_to_string_enrichment.py` | ④ vendor; imports `gpi.column_mapper` |
| `gpi/gene_summaries.py` | `02_fetch_ncbi_data.py` | ④ vendor; **PubTator/BioC gated OFF by default** |
| `gpi/context_profile.py` | NEW | ④ (written by orchestrator) |
| `gpi/research_evidence_adapter.py` | `manual_literature_adapter.py` | RENAME + consume `ResearchResult` |
| `gpi/evidence_context.py` | `03_*` format_* + `PROMPT_TEMPLATE` + `generate_prompt` + `load_prompt_literature_context` | ③ generalize via `ContextProfile` |
| `gpi/anthropic_batch.py` | `03_*` `cmd_submit_anthropic`/`cmd_check_anthropic`/`cmd_results_anthropic` | ③ Anthropic-only |
| `gpi/theme_representation.py` | `compute_theme_representation.py` | ③ pack+extract; **drop Vertex/gateway** |
| `gpi/presentation.py` | `06_generate_presentation.py` + `presentation_layer.py` | ③ LLM batch + ④ deterministic fallback |
| `gpi/html_report.py` | `05_generate_html_report.py` | ④ EXTEND for evidence status + DOI links |
| `gpi/run_pipeline.py` | `run_pipeline.py` | ④ generalize config; Anthropic-only |
| `research/schema.py` | NEW (spec §5) | ④ (written by orchestrator) |
| `research/protocol.md` | NEW | shared agent protocol |
| `research/bundle.py` | NEW | ④ builds `program_bundles/{id}.json` |
| `research/research_parallel.py` | NEW | ② asyncio + Agent SDK fan-out |
| `research/verify.py` | NEW | ④ verifier; uses `research/_crossref.py::verify_dois` + `gpi/ncbi_api.py` |
| `research/literature.py` | NEW | ② in-process literature MCP server (PubMed/OpenAlex/Crossref) wired into each SDK session |

### DROP entirely (never vendor)
Vertex/GCS/gateway everything: `convert_to_vertex_jsonl`, `cmd_submit_vertex*`, `upload_to_gcs`,
`cmd_submit_gateway`, `create_ai_gateway_client`, `VERTEX_MODEL_MAP`, `VERTEX_*` constants,
`call_vertex`, `call_ai_gateway`, `resolve_env_reference`, and the `google.genai`/`openai`
import guards. Keep only the `anthropic` import guard. Default any `llm_backend` seam to `"anthropic"`.

## `gpi.context_profile.ContextProfile` (the generalization linchpin)
Replaces ProgExplorer's hard-coded liver constants (in `03`:
`DEFAULT_ANNOTATION_ROLE`, `DEFAULT_ANNOTATION_CONTEXT`, `DEFAULT_SEARCH_KEYWORD`,
`LIVER_DISEASE_CONTEXT`, `LIVER_FUNCTIONAL_CONTEXT`, and the `PROMPT_TEMPLATE` opening
sentence). Structured fields; blank framing strings are auto-derived from them, so a liver
profile reproduces the original liver text and any other tissue works with no code change.
See `gpi/context_profile.py` for the authoritative dataclass and derivations. Key fields:
`organism`, `species_taxid`, `tissue`, `cell_type`, `conditions[]`, `context_terms[]`,
`assay`, and derived `annotation_role`, `annotation_context`, `keyword_query`,
`condition_context`, `functional_context`, `report_dataset_crumb`. Threaded into
`evidence_context.py`, `research_evidence_adapter.py`, `theme_representation.py`,
`run_pipeline.PipelineConfig`, and each program bundle's `context` block.

## `research.schema` (canonical research contract, spec §5)
Pydantic v2 models — authoritative definition in `research/schema.py`. Papers attach
**directly to each mechanism** (no separate claims layer):
`ResearchResult{program_id, queries[], candidate_mechanisms[CandidateMechanism],
evidence[Evidence], contradictions[], evidence_gaps[], agent_summary, meta}`.
`CandidateMechanism{name, summary, supporting_genes[], supporting_regulators[], evidence_ids[],
status ∈ {supported,partial,unsupported}}` — `status` is per-mechanism, derived by the verifier
from evidence resolvability. The agent submits the flat `AgentResearchResult` whose
`AgentMechanism.papers[AgentPaper{pmid,doi,title,year,study_type,context_match,note}]` are
normalized into the deduplicated `Evidence` pool (hard-capped to 3 mechanisms). `Evidence`
carries `context_match` plus verifier-added fields (`resolved`, `registry`, `retracted`,
`verify_error`) annotated **in place** (no second schema). The claim-level models
(`Claim`/`AgentClaim`/`Citation`) are RESERVED for a future entailment-verification step and are
NOT wired into the active pipeline — see `docs/FUTURE_claim_verification.md`.

## Key data contracts (verbatim from ProgExplorer recon)
- **Gene loading CSV:** `Name,Score,program_id,source_program,rank`. Top-loading via
  `extract_top_genes_by_program`; uniqueness via `build_uniqueness_table` (`UniquenessScore`
  = TF-IDF-weighted `Score`). (There is **no** `select_program_genes` — use those two.)
- **Regulators CSV:** `program_id,target_gene,log2_fc,significant,source_program`.
- **Enrichment CSV (`01` output):** columns `program_id,category,term,term_id,description,fdr,
  p_value,number_of_genes,number_of_genes_in_background,ncbiTaxonId,inputGenes` (`inputGenes` `|`-joined).
  Filtered = `category ∈ {Process,KEGG}` and `background<500`.
- **`ncbi_context.json` (`02` output, keyed by program id str):** per program `{meta, top_papers,
  gene_summaries, gene_summaries_source, evidence_snippets, [regulator_validation |
  regulator_validation_by_condition]}`. `regulator_validation` = `{positive_regulators:[...],
  negative_regulators:[...]}`, each `{regulator, log2fc, string_interactions:[{target,score}], ...}`.
- **Program bundle JSON (`research/bundle.py`, spec §3):** `{program_id, context (ContextProfile
  as dict), top_weighted_genes:[{gene,score,uniqueness}], enrichment:{KEGG:[...],Process:[...],...},
  regulators:{activators:[...],repressors:[...]}, effect_direction:{...}, qc:{...}, research_brief:str}`.
- **`presentation.json → programs[<id>]`:** `{lead, lead_html, tags:[], module_short:[], source}`.
  Produced by the ③ LLM batch (`build_presentation_prompt`) with ④ `deterministic_presentation` fallback.
- **HTML report per-program card dict (`05` `generate_design_a_html`):** keys `id,name,label,summary,
  lead_html,tags,module_short,presentation_source,top_loading[],unique[],celltype,modules[],
  distinctive,regulators[],pathways[],annotation_text,kegg_fig,process_fig,volcano,condition_volcano`.
  Each `modules[]`: `{title,summary,key_genes[],pmids[],evidence,mechanism}`. **Extend for evidence
  status:** add DOI links (recon: current renderer does PMID-only) and visually separate
  supported/partial/contradictory/missing (spec §10), driven by mechanism `status`/`contradictions`/`evidence_gaps`.
- **The `modules[]` prompt-context shape** the annotation prompt consumes (via
  `evidence_context.format_research_evidence_context`, renamed from `format_manual_literature_context`):
  `{module_rank, module_name, supporting_genes[], evidence_ids[] (PMID and/or DOI),
  literature_summary, status}`. `research_evidence_adapter.py` maps each `ResearchResult`
  `candidate_mechanism` (reading `mechanism.status` directly) + its linked `evidence` into this shape.

## Verification expectations (every component)
Actually exercise it: import it, run its CLI on a fixture (`tests/fixtures/`), or unit-test it.
Report what you ran and observed (shapes, counts, a sanity value). Fixtures available:
`tests/fixtures/inputs/{gene_loading,regulators}.csv` (18 liver programs),
`tests/fixtures/literature/literature_context.json` (programs 2/10/18),
`tests/fixtures/annotations/topic_{2,10,18}_annotation.md`, and a real literature prompt.
