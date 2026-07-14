# Gene Program Interpreter — 2-Minute Demo Video

**Storyboard + narration for the *Built with Claude: Life Sciences* hackathon (Builder Track).**
Structured with the `paper-narrative` arc: **hook → mechanism → evidence → application.**

- **Format:** 1920×1080 landscape, screen-recording–led with voiceover. Target **2:00** (≤ 3:00 cap).
- **Serves:** the demo video (30% of score) + the 100–200 word written summary (bottom of this file).
- **Everything narrated is from real, committed runs** on aged-mouse-hepatocyte Perturb-seq data — no mockups, no invented numbers. Cost/time figures are aggregates across recent runs (see checklist).

## Pitch (one sentence)
> A Claude Skill that interprets gene programs **in their experimental context** — cell type, tissue, and Perturb-seq regulators injected throughout — and grounds every claim in a **citation that deterministic code has verified is real**.

## The narrative (paper-narrative brief)
- **Hook / most-arresting contrast:** every experiment yields *dozens* of programs; today you either get a **generic enrichment label** or a **plausible-but-ungrounded LLM guess** (e.g. GeneAgent) that ignores *your* cell type — and invents citations.
- **Mechanism:** (1) **context injected everywhere** — cell type, tissue, conditions, and Perturb-seq regulators/direction flow into the literature agent, the database evidence, and the annotation; (2) **agents research, deterministic code verifies.**
- **Evidence:** it's been run across recent programs at consistent, low cost — and every verified citation resolved to a real paper — then one worked example (Program 22) that reads a program as a *regulatory response*.
- **Application:** days→minutes, citable, tissue-agnostic, and drop-in for any scientist's agent.
- **Kill list (what got cut to hit 2:00):** the standalone "here's the config" scene, the 4-box architecture animation, and the separate days-vs-minutes card. Nothing on the arc was cut.

## How the 2 minutes map to the rubric

| Criterion (weight) | Beat that earns it |
|---|---|
| **Demo (30%)** | The run + clicking a citation to the real paper (Beat 3). |
| **Impact (25%)** | Days→minutes at ~$0.50/program, citable, tissue-agnostic; a named user's daily bottleneck removed (Beat 1 + 4). |
| **Claude Use (25%)** | Skill + one Agent SDK subagent *per program* + MCP + Batch API (Beat 2). |
| **Depth & Execution (20%)** | Context threaded end-to-end; deterministic verify; consistent across runs (Beat 2 + 3). |

---

## Storyboard — 4 beats (≈120 s)

### Beat 1 — HOOK · the gap (0:00 – 0:20)
- **On screen:** A `K=50` program matrix / a raw gene-loading CSV scrolling — dozens of anonymous ranked lists. Quick cut to two failed answers side by side: a **generic enrichment label** ("metabolic process") and a **slick LLM answer** whose PMID we zoom on — a red stamp: **"doesn't exist."**
- **On-screen text:** `Dozens of programs / experiment` → `Generic label ✗   ·   Ungrounded LLM guess ✗ (e.g. GeneAgent)`
- **Narration:** *"Every single-cell or Perturb-seq experiment spits out dozens of gene programs — ranked gene lists with no names. Interpreting one takes days in PubMed. Enrichment tools hand you generic labels; ask an LLM and it sounds confident, but it isn't grounded in* your *cell type — and it invents citations."*

### Beat 2 — MECHANISM · what's different (0:20 – 0:55)
- **On screen:** Claude Code session — user types **"Interpret my gene programs — aged mouse hepatocyte Perturb-seq, MASLD."** Skill activates. Two quick overlays: (1) a `context:` chip — `cell_type · tissue · conditions · regulators` — animating into each pipeline stage; (2) a small fan-out of **agent** boxes collapsing into a single **✓ Verify** gate.
- **On-screen text:** `A Claude Skill` → `Context in every step` → `Agents research · Code verifies`
- **Narration:** *"The Gene Program Interpreter is a Claude Skill that fixes both problems. First, context: your cell type, tissue, conditions — and for Perturb-seq, the regulators and their direction — are injected into every step, so a program isn't a generic gene list, it's a regulatory response in* your *system. Second, trust: for each program it runs its own Claude agent that searches the literature over MCP, then deterministic code verifies every single citation. Agents read; code checks — nothing plausible-but-fake survives."*
- **Capture:** real Claude Code session; the `context:` block of `configs/hepatocyte_p10_p22.yaml`.

### Beat 3 — EVIDENCE · consistent runs, one worked example (0:55 – 1:40)
- **On screen:** Kick off a run; several agents work in parallel. Cut to a stats card built from the audit files — **cost/program, turns/program, citations resolved** across recent runs. Then open the **5-program report** (`hepatocyte_p1_5`) for a wall of cards, and land on the **Program 22 card** (`hepatocyte_p10_p22`): label **"Lipogenic-Detoxification Hepatocyte Response,"** with the regulators **Mlxipl (ChREBP), Insig1, Scap** highlighted as the drivers the screen flagged. **Click a PMID → the real paper opens.**
- **On-screen text:** `~$0.50 & a few min / program` → `citations verified, not invented` → `Program 22: a regulatory response, not a gene list`
- **Narration:** *"We've run this across recent programs from this dataset. Each takes a few minutes of autonomous research and around fifty cents — and because programs run in parallel, a batch of five finishes in about four minutes. Over those runs the verifier checked hundreds of citations, and every one resolved to a real paper — none fabricated, none retracted. Then, the biology. Program 22 isn't just 'lipid genes' — it's read as a lipogenic response, coupling fat synthesis with fructose metabolism, driven by the regulators ChREBP, Insig1, and Scap that the perturbation screen flagged. Every claim links to the paper behind it — click one, and there it is."*
- **Capture:** stats from `runs/**/research_audit/*.audit.json`; reports at `runs/hepatocyte_p1_5/report.html` and `runs/hepatocyte_p10_p22/report.html` (both rendered); P22 regulators from `research_results/P22.json`.

### Beat 4 — APPLICATION · reach + close (1:40 – 2:00)
- **On screen:** The `context:` block edits from `hepatocyte` → `CD8 T-cell` and the same report regenerates. Four words light up: **Skill · Agent SDK · MCP · Batch.** End card: repo URL + Apache-2.0.
- **On-screen text:** `Liver → T-cells, zero code change` → `Interpretation you can actually cite.`
- **Narration:** *"Days per program become minutes, and everything is citable. Swap the context profile and the same tool interprets CD8 T-cells instead of liver — no code changes. And because it's a Claude Skill, it drops straight into any scientist's agent. Built entirely with Claude — a Skill, the Agent SDK, MCP, and the Batch API. Interpretation you can actually cite."*

---

## Full narration script (continuous — feed to voiceover/TTS)

> ≈ 290 words ≈ **2:00** at a relaxed pace, leaving room for the fan-out and the citation click to breathe. Two `[trim]` clauses if you run long.

**[0:00]** Every single-cell or Perturb-seq experiment spits out dozens of gene programs — ranked gene lists with no names. Interpreting one takes days in PubMed. Enrichment tools hand you generic labels; ask an LLM and it sounds confident, but it isn't grounded in *your* cell type — and it invents citations.

**[0:20]** The Gene Program Interpreter is a Claude Skill that fixes both problems. First, context: your cell type, tissue, conditions — and for Perturb-seq, the regulators and their direction — are injected into every step, so a program isn't a generic gene list, it's a regulatory response in *your* system. Second, trust: for each program it runs its own Claude agent that searches the literature over MCP, then deterministic code verifies every single citation. Agents read; code checks — nothing plausible-but-fake survives.

**[0:55]** We've run this across recent programs from this dataset. Each takes a few minutes of autonomous research and around fifty cents — and because programs run in parallel, a batch of five finishes in about four minutes. Over those runs the verifier checked hundreds of citations, and every one resolved to a real paper — none fabricated, none retracted. Then, the biology. Program 22 isn't just "lipid genes" — it's read as a lipogenic response, coupling fat synthesis with fructose metabolism, driven by the regulators ChREBP, Insig1, and Scap that the perturbation screen flagged. `[trim: Every claim links to the paper behind it —]` click one, and there it is.

**[1:40]** Days per program become minutes, and everything is citable. Swap the context profile and the same tool interprets CD8 T-cells instead of liver — no code changes. `[trim: And because it's a Claude Skill, it drops straight into any scientist's agent.]` Built entirely with Claude — a Skill, the Agent SDK, MCP, and the Batch API. Interpretation you can actually cite.

---

## Pre-record checklist

1. **Reports are rendered — use both.** `runs/hepatocyte_p1_5/report.html` (5 program cards → the "wall of cards" overview that shows this isn't a one-program toy) and `runs/hepatocyte_p10_p22/report.html` (→ the **Program 22** close-up). Pre-open both, plus one real paper (a PMID from the P22 card) in a second tab so the citation click is instant.
2. **Regulator names to highlight on the P22 card (real):** `Mlxipl (ChREBP), Insig1, Dgat2, Scap, Gckr` — from `research_results/P22.json`, mechanism "De novo lipogenesis." Say "ChREBP" in the voiceover; caption "Mlxipl" so it matches the data. The richer P22 story: it couples de novo lipogenesis with **fructose** metabolism (Khk) and detoxification — ChREBP-driven and MASLD-relevant.
3. **Aggregate cost/time to caption (all measured from the audit files across recent runs — verify before recording):**
   - **~10 program runs** across `hepatocyte_p1_5` (P1–P5), `hepatocyte_p10_12` (P10–P12), `hepatocyte_p10_p22` (P10, P22) — all completed successfully.
   - **Cost:** mean **~$0.48/program** (range $0.38–$0.59); ~$4.75 total.
   - **Turns:** **~30 tool-calling turns/program** (range 23–47).
   - **Time:** a few minutes/program; parallelized — a **5-program batch finished in ~4 min** of research wall-clock.
   - **Citations:** **all resolved, 0 retracted** (219/219 across these runs at last count).
   - Recompute with: `find runs -path "*research_audit/*.audit.json"` → average `cost_usd` / `num_turns`; count `resolved` in `research_results/*.json`.
4. **Show Claude Code on camera** in Beat 2 (type the natural-language trigger) — the Builder track 
6. **End card shows the open-source repo + Apache-2.0** (rules require it — and add a root `LICENSE` file, currently missing).
7. **Record clean:** hide API keys, large terminal font, 1080p, voiceover levels check.

---

## Bonus — 100–200 word written summary (required submission field)

> The Gene Program Interpreter is a Claude Skill for **context-aware** interpretation of gene programs from Perturb-seq and single-cell data. Every experiment yields dozens of programs, and today interpreting one takes days of manual literature review — while enrichment tools give generic labels and LLM-only tools sound plausible without being grounded in the specific biological context. GPI injects that context — cell type, tissue, conditions, and, for Perturb-seq, the supporting regulators, perturbation effects, gene weights, and direction — throughout the workflow, so each program is read as a *context-specific regulatory response* rather than a generic gene list. It combines deterministic database evidence (enrichment, interactions), one Claude Agent SDK literature agent per program searching over MCP, a **deterministic verifier that confirms every citation resolves to a real paper**, and Anthropic Batch-API annotation — into an auditable, interactive HTML report. Across recent runs it interprets a program for about half a dollar and a few minutes of autonomous research, with every verified citation that you can read directly. Packaging it as a Skill makes the workflow plug-and-play and agent-native. Any scientist could use their Claude to refine context interactively on their own data, and generate publication-ready resource page.

*(≈205 words.)*
