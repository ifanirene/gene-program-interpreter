# Gene Program Interpreter — 3-Minute Demo Video

**Storyboard + narration for the *Built with Claude: Life Sciences* hackathon (Builder Track).**

- **Format:** 1920×1080 landscape, screen-recording–led with voiceover. Max **3:00** (hard cap; aim 2:50).
- **Submission fields this serves:** the 3-min demo video (30% of score) + reuses beats for the 100–200 word written summary (at the bottom of this file).
- **What's on screen the whole time:** a real Claude Code session and the real report — no mockups. Everything narrated below is from the validated **P10 + P22** run on aged-mouse-hepatocyte Perturb-seq data.

## One-sentence pitch
> A Claude Skill that turns anonymous cNMF gene programs into a cited biological report — where creative Claude agents do the reading and deterministic code guarantees **every citation is real**.

## How the video is built to score

| Criterion (weight) | The beat that earns it |
|---|---|
| **Demo (30%)** | Watch it run live, then click a citation and land on the real paper. It holds up. |
| **Impact (25%)** | Days-per-program → minutes; trustworthy enough to actually cite; tissue-agnostic (liver today, T-cells tomorrow, zero code change). |
| **Claude Use (25%)** | The whole platform in one tool: a **Skill** (Claude Code), one **Agent SDK** subagent *per program*, **MCP** literature servers, and the **Batch API** for synthesis. |
| **Depth & Execution (20%)** | The guardrail: agents research, **deterministic code verifies** — plus honest abstention, contradiction-surfacing, and resumable-from-cache runs. |

---

## Storyboard — 6 scenes (≈180 s)

### Scene 1 — The problem (0:00 – 0:25)
- **On screen:** Open on a raw `gene_loading_K50.csv` scrolling — thousands of rows, `Name, Score, program_id`. Then cut to a Claude chat where someone pastes a gene list and asks "what is this program?" — the reply shows a slick answer with citations, and we **zoom on a PMID**. A red stamp drops: **"Retracted / does not exist."**
- **On-screen text:** `50 programs. 0 names.` → then `Fabricated citation ✗`
- **Narration:** *"Single-cell factorization hands you fifty gene programs — each just a ranked list of genes with weights. No names, no biology. Interpreting one program means days in PubMed. And if you ask a language model to do it, it hands you citations that look perfect — and don't exist. In science, one fabricated reference ends the conversation."*
- **Capture:** `runs/hepatocyte_p10_p22/string_enrichment/program_genes.json` or the source CSV for the scroll; the "fake citation" can be a quick slide.

### Scene 2 — What it is (0:25 – 0:45)
- **On screen:** A clean Claude Code terminal. User types the trigger in plain English: **"Interpret my gene programs — aged mouse hepatocyte Perturb-seq, interested in MASLD."** The `gene-program-interpreter` skill activates. Cut to the tiny `context:` block in `configs/hepatocyte_p10_p22.yaml` — highlight `organism / tissue / cell_type / conditions`.
- **On-screen text:** `A Claude Skill` → `Biology lives in the config, not the code`
- **Narration:** *"This is the Gene Program Interpreter — a Claude Skill. You point it at your programs and your experimental context, and run one command. It's tissue-agnostic: the biology lives in a context profile, not the code. Liver today, T-cells tomorrow, with zero code changes."*
- **Capture:** real Claude Code session; then `configs/hepatocyte_p10_p22.yaml` (the `context:` block).

### Scene 3 — How it works (0:45 – 1:20)
- **On screen:** A simple animated architecture diagram (build from `docs/pipeline_overview.html` or a 4-box slide). Reveal the four executors in sequence, but **dwell on the split**: many **agent** boxes fanning out (one per program) → arrows into a single **"Verify"** gate → **Batch** synthesis. Animate a citation traveling from an agent into the Verify gate; a green check stamps it.
- **On-screen text:** `Agents research → Code verifies` (this is the thesis — keep it up for the whole scene)
- **Narration:** *"Here's the idea. For every program, it launches its own Claude agent, built on the Agent SDK. Each agent uses MCP to search PubMed, OpenAlex, and bioRxiv in real time — and they run in parallel. But agents can hallucinate, so agents never get the final word. Every citation they return is handed to a deterministic verifier that checks it resolves to a real paper. Agents research; code verifies. Then the Batch API stitches the evidence across programs into a single label."*
- **Capture:** slide/animation. If short on time, screen-record `docs/pipeline_overview.html` and Ken-Burns across it.

### Scene 4 — Watch it run (1:20 – 2:05)
- **On screen:** Run the command (or replay the terminal). Two research agents fan out — show live-ish tool calls (`search_articles`, `get_article_metadata`). Then cut to the **audit JSON** and pull three numbers on lower-thirds: turns, cost, and the verifier tally. End on **`56 / 56 resolved · 0 fabricated · 0 retracted`** filling the screen.
- **On-screen text:** `P10: 31 turns  ·  P22: 27 turns  ·  ~$0.90` → `56 / 56 citations resolved`
- **Narration:** *"Let's run it — on real aged-mouse-hepatocyte Perturb-seq data, hunting for programs relevant to fatty-liver disease. Two agents fan out at once. Watch the audit trail: program 10 ran thirty-one tool-calling turns, program 22 ran twenty-seven — for about ninety cents total. Between them they returned fifty-six citations. The verifier checks every one… fifty-six of fifty-six resolve. Zero fabricated. Zero retracted. Nothing reaches the report a reviewer couldn't pull up themselves."*
- **Capture:** `runs/hepatocyte_p10_p22/research_audit/P10.audit.json` + `P22.audit.json` (cost_usd, num_turns); the 56/56 is verified from `research_results/{P10,P22}.json`.

### Scene 5 — The payoff, and the honesty (2:05 – 2:45)
- **On screen:** Open `report.html`. Program 10's card reads **"Pericentral Wnt-driven metabolic zonation"**; scroll to Program 22, **"Lipogenic-Detoxification Hepatocyte Response."** **Click a PMID → the real paper opens in a browser tab** (the money shot). Scroll to the **contradiction badge** and the **evidence-gap** list; hover so the text is legible.
- **On-screen text:** `Every claim → a resolvable paper` → `It surfaces contradictions` → `It admits what it can't find`
- **Narration:** *"Now the payoff — the report. Program 10 comes back as pericentral, Wnt-driven metabolic zonation; program 22, a lipogenic detoxification response — and every claim is traceable. Click a citation… the real paper opens. And it's honest: the agent surfaced a study that challenges the very hypothesis it's supporting, and flagged it as a contradiction. Genes it couldn't find literature for? Marked as evidence gaps — not dressed up with invented support. It tells you what it doesn't know."*
- **Capture:** the **two-program** `runs/hepatocyte_p10_p22/report.html` — **see checklist item #1, this render doesn't exist yet.** Contradiction text is real: `research_results/P10.json → contradictions[0]` (PMID 31866224).

### Scene 6 — Close (2:45 – 3:00)
- **On screen:** Split card: left "days / program", right "minutes." Below, four logos/words light up: **Skill · Agent SDK · MCP · Batch.** End card with the repo URL + license (Apache-2.0, open source per rules).
- **On-screen text:** `Interpretation you can actually cite.`
- **Narration:** *"Days per program, down to minutes — and every word you can cite. Built entirely with Claude: a Skill, the Agent SDK, MCP, and the Batch API. Interpretation you can actually trust."*

---

## Full narration script (continuous — feed to voiceover/TTS)

> Total ≈ 376 words ≈ 2:45 at a relaxed pace, leaving ~15 s of breathing room for the on-screen action (kickoff pause, the citation click). If you run long, cut the two trimmable clauses marked `[trim]`.

**[0:00]** Single-cell factorization hands you fifty gene programs — each just a ranked list of genes with weights. No names, no biology. Interpreting one program means days in PubMed. And if you ask a language model to do it, it hands you citations that look perfect — and don't exist. In science, one fabricated reference ends the conversation.

**[0:25]** This is the Gene Program Interpreter — a Claude Skill. You point it at your programs and your experimental context, and run one command. It's tissue-agnostic: the biology lives in a context profile, not the code. Liver today, T-cells tomorrow, with zero code changes.

**[0:45]** Here's the idea. For every program, it launches its own Claude agent, built on the Agent SDK. Each agent uses MCP to search PubMed, OpenAlex, and bioRxiv in real time — and they run in parallel. But agents can hallucinate, so agents never get the final word. Every citation they return is handed to a deterministic verifier that checks it resolves to a real paper. Agents research; code verifies. Then the Batch API stitches the evidence across programs into a single label.

**[1:20]** Let's run it — on real aged-mouse-hepatocyte Perturb-seq data, hunting for programs relevant to fatty-liver disease. Two agents fan out at once. Watch the audit trail: program 10 ran thirty-one tool-calling turns, program 22 ran twenty-seven — for about ninety cents total. Between them they returned fifty-six citations. The verifier checks every one… fifty-six of fifty-six resolve. Zero fabricated. Zero retracted. `[trim: Nothing reaches the report a reviewer couldn't pull up themselves.]`

**[2:05]** Now the payoff — the report. Program 10 comes back as pericentral, Wnt-driven metabolic zonation; program 22, a lipogenic detoxification response — and every claim is traceable. Click a citation… the real paper opens. And it's honest: the agent surfaced a study that challenges the very hypothesis it's supporting, and flagged it as a contradiction. Genes it couldn't find literature for? Marked as evidence gaps — not dressed up with invented support. `[trim: It tells you what it doesn't know.]`

**[2:45]** Days per program, down to minutes — and every word you can cite. Built entirely with Claude: a Skill, the Agent SDK, MCP, and the Batch API. Interpretation you can actually trust.

---

## Pre-record checklist

1. **⚠️ Render the two-program report (the Scene 5 climax doesn't exist yet).** The `hepatocyte_p10_p22` run stopped at `annotate: in_progress`, so `runs/hepatocyte_p10_p22/report.html` was never produced. Resume it — research/verify are cached, so this only spends the Batch **annotate + presentation** calls (cents), not the agents:
   ```bash
   python -m gpi.run_pipeline --config configs/hepatocyte_p10_p22.yaml
   ```
   *Zero-spend fallback:* the single-program `runs/hepatocyte_p10/report.html` is already rendered — usable if you cut the "program 22" mention from Scene 5's narration.
2. **Pre-open the tabs** so the citation click is instant on camera: the report, plus one real paper (e.g. a PMID from Program 10's Module 1) already loaded in a second tab.
3. **Numbers to caption (all verified from the run):** P10 = 31 turns / \$0.4597; P22 = 27 turns / \$0.4450; **56/56 resolved, 0 retracted**; concurrency 2; research model `claude-sonnet-4-5`.
4. **The contradiction to point at (real):** Program 10 → PMID **31866224** ("AXIN2+ pericentral hepatocytes have limited contribution to homeostasis/regeneration" — challenges the stem-cell hypothesis). Program 22 → PMID **37681411** (FASN inhibition is context-dependent).
5. **Show Claude Code on camera** in Scene 2 (type the natural-language trigger) — judges reward *seeing* Claude Code used, and it's a required tool for the Builder track.
6. **End card must show the open-source repo + license** (Apache-2.0) — the rules require the submission be open-sourced.
7. **Record clean:** hide API keys (the runner loads `.env`); use a large terminal font; 1080p; do a levels check on the voiceover.

---

## Bonus — 100–200 word written summary (required submission field)

> The Gene Program Interpreter is a Claude Skill that turns anonymous gene programs — the weighted gene lists that come out of cNMF/NMF factorization of single-cell and Perturb-seq data — into an interactive, fully-cited biological report. Its core idea is a division of labor that makes an LLM trustworthy in science: for every program it launches one Claude Agent SDK subagent that searches PubMed, OpenAlex, and bioRxiv over MCP, in parallel; then **deterministic Python verifies that every returned citation resolves to a real paper**, and the Anthropic Batch API synthesizes the evidence into a program label. Agents research; code verifies — so nothing reaches the report that a reviewer couldn't pull up themselves. On a real aged-mouse-hepatocyte Perturb-seq dataset it interpreted two programs for ~\$0.90 with **56 of 56 citations resolved, none fabricated or retracted**, and it honestly surfaced contradictory evidence and flagged genes it couldn't support rather than inventing references. The tool is tissue-agnostic — the biology lives in a context profile, so the same code interprets liver hepatocytes or CD8 T-cells with no changes. Built entirely with Claude: a Skill, the Agent SDK, MCP, and the Batch API.

*(≈195 words.)*
