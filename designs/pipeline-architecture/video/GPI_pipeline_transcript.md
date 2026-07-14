# Gene Program Interpreter — narrated walkthrough

## 00:00:00.583 — opening

Every single-cell or Perturb-seq screen hands you the same puzzle: dozens of gene programs, each just a ranked list, none of them labeled. Working out what one program actually does can eat a whole afternoon on PubMed. Enrichment gives you a vague heading; a raw language model gives you a confident guess that sails right past the biology. So we built the Gene Program Interpreter — a Claude Skill that reads your programs the way an expert would, in context, and shows its work.

## 00:00:27.125 — input

Two inputs go in: your weighted program genes, and any regulators from a Perturb-seq screen.

## 00:00:32.708 — context

First it gives each program an identity — organism, tissue, cell type, your conditions, and what these cells normally do. The perturbed regulators keep their direction, and STRING adds a conventional layer: enrichment, plus an interaction network wiring each regulator to its targets. Now it's a regulatory response in a real system, not a nameless list.

## 00:00:49.917 — research

Here's the engine. For every program, we spin up a separate Claude agent — all in parallel — to dig through the literature. Each one searches PubMed, OpenAlex, and Crossref with its own tools, and only ever sees its own brief. Python runs the fan-out, so a whole screen researches itself at once instead of one program at a time.

## 00:01:06.667 — verification

Then every reference the agents bring back is checked automatically — does it resolve, is it retracted, is it a duplicate. Anything that doesn't hold up is dropped, and disagreements and gaps are kept in plain sight rather than smoothed over.

## 00:01:18.792 — synthesis

Now it all comes together — the verified papers, the context, the genes and regulators, the database evidence — through Anthropic's Batch API, which writes each program up as clean functional modules with a name that fits across the whole set. It's candid, too. Click any claim and the real paper opens in one tap.

## 00:01:36.000 — report biology

And here's a real result. Program 22 comes back as fructose-driven lipogenesis — and the giveaway is its regulator. Mlxipl, or ChREBP, the master carbohydrate-response transcription factor, switches the program on, and the evidence connects it directly to Khk for fructose, Pklr for glycolysis, and Fasn for fat synthesis. So this isn't a vague "lipid metabolism" label — it's the fructose-to-fat pathway, pinned down by what actually drives it.

## 00:02:02.208 — summary

And the best part is how little it asks of you. A few minutes to set up, and then it runs itself. Describe your biology once — a short context block — and the same Skill handles the whole pipeline without touching code. It works through a whole screen in parallel while you step away. The days you'd spend chasing references become a hands-off pass — so your time goes back to the science, not the searching.
