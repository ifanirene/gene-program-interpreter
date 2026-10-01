# Regulator evidence in the workflow

Program genes determine names and functional modules. Perturbation targets provide supporting evidence about program responses. A strong or recurrent perturbation does not establish a direct target, a rate-limiting step, or a distinct functional module.

## Selection and complete condition coverage

Select three significant activators and three significant repressors per supplied condition, ranked by adjusted P after collapsing guides. The union of those genes is the research target set. Each selected gene also carries its estimates, significance calls and representative guide in **every** supplied condition, including conditions where it was not a top hit or was not significant. Missing measurements are explicit. Research bundles and annotation prompts both receive this coverage.

Top-hit omission must never be interpreted as a null effect. Different significance calls across cohorts are not an interaction test. Condition comparisons are descriptive.

## Broad regulators

`regulator_recurrence.json` counts significant programs and programs where a gene enters a top-three slot in either direction in any condition, over **all input programs**, before restricting the requested interpretation programs. A gene in top slots for at least half the input programs is flagged `broad_top_hit`. This is a transparent heuristic for annotation specificity, not a statistical test of biological promiscuity.

Flagged regulators remain available as supporting evidence, but must not dictate a name or create a module without multiple program genes supporting it. Existing `mask_regulators` config remains a hard exclusion applied before ranking. Controls labeled `non-targeting`, `non_targeting` or `nontargeting` are excluded automatically from biological selection; raw data and volcano plots retain them.

The 80-program pilot data produce seven flags: Dgat2 (54), Insr (52), Leng1 (48), Flcn (46), Trib1 (45), Tmprss6 (44), Insig1 (40). These counts are an example, not a hard-coded exclusion list. Broad effects can still help explain a particular program.

## Research coverage

Research output includes `regulator_coverage`: one entry per selected gene, with status `retrieved_support`, `searched_no_support` or `not_researched`, queries, identifiers and a note. Normalization preserves these records. Verification writes `research_audit/regulator_coverage.json` with selected conditions and exposes missing documented support as evidence gaps for annotation. Legacy outputs without coverage records do not establish that all selected regulators were researched. These are coverage records, not claim-entailment verification.

## Annotation and rendering

Regulator statistics belong only in regulator headers. Following parsing, `finalize_regulator_annotations` fills those headers from measured data and writes `annotations/regulator_validation.json`. It rejects regulator-statistic prose and modules with fewer than two supplied program genes before the pipeline advances to presentation. Raw model batch output is retained for diagnosis. This rule deliberately favors a clear failure over publishing another partial range; it does not automatically retry paid annotation calls.

The report also derives regulator roles and displayed condition effects from the measured inputs. Negative significant knockdown effects map to activator/blue, positive to repressor/red; opposing significant signs map to mixed/neutral. Roles describe perturbation responses rather than direct molecular regulation. Missing and nonsignificant measurements remain visible. The parser accepts `role:` and `role=` after descriptive names, and confidence on a second line.

Cards and annotation headers use the guide with the strongest adjusted-P support, matching annotation selection. Volcano plots retain their existing largest-absolute-effect guide convention, disclosed in the report. Thus the two displays need not select the same guide.

## Regression checks

`tests/test_regulator_condition_coverage.py` covers unselected and nonsignificant cohorts, descriptor/role parsing, measured role overrides, opposing signs, controls, bundle completeness, research coverage and publication validation. A temporary copy of the old three-program pilot was also checked: its regulator-statistic prose fails validation in all three programs, while rebuilt bundles retain aged-male Flcn significance and an 80-program recurrence denominator. Original pilot annotations, presentation and report were restored unchanged after this diagnostic work.

These checks do not validate biological mechanisms, semantic relevance of a module's genes, or literature entailment. A new paid synthesis was not run as part of this workflow repair.

## Hard-mask troubleshooting run

For the current troubleshooting comparison, use a top-level hard mask:

```yaml
mask_regulators: [Dgat2, Insr, Leng1, Flcn, Trib1, Tmprss6, Insig1]
```

Use a fresh output directory so earlier unmasked research and annotations are not reused. The masked pilot configuration uses `gpi_gene_first_p21_43_50_masked`; its offline audit is separate from production stage outputs. Exclusions happen before ranking, allowing the next eligible targets to fill the slots. The annotation prompt describes only the number excluded, rather than repeating their names as contextual cues. Publication validation rejects an excluded regulator if the model reintroduces it as a regulator card. Gene-loading measurements and raw screen plots are retained.

The offline check on programs 21, 43 and 50 retained all 72 slots (three in each direction × four conditions × three programs), replaced 35 selections, and found zero excluded targets in the tested regulator loaders, selections, research-bundle regulator fields, complete-condition evidence, and regulator annotation contexts. This is evidence of correct data flow, not a new literature or biological-annotation result.

## Repair after the failed live pilot (2026-09-11)

The failed live pilot exposed conflicting research instructions, leakage through
supporting evidence, variable Markdown, and sign errors. New synthesis requests
now use a validated JSON contract (`gpi/annotation_contract.py`). Code renders
one section layout, restores exact supplied gene-symbol case, and supplies roles,
complete condition effects, and observed knockdown directions from measurements.
Model prose is restricted to biological interpretation and tentative mechanisms.
The publication gate checks the current batch for missing, failed, duplicate or
truncated responses; it also requires the exact selected regulator set, distinct
supplied module members, and no excluded identities or regulator-statistic prose.
Explicit perturbation-direction prose is rejected rather than silently corrected.
These syntax checks are not a complete semantic or literature-entailment validator.

The final annotation-prompt boundary redacts every exact masked gene identity,
case-insensitively, including names in enrichment and STRING partner text. This is
strict identity masking in interpretation prompts; input measurements, enrichment
figures and raw screen plots remain unchanged. The research protocol now separates
program-gene functions from supporting regulator research and explicitly requests
coverage records for every selected regulator, including unresearched ones.

Legacy Markdown remains readable, including slash aliases and nested parentheses
in effect headers. New pipeline runs require the JSON contract. Raw model responses
are retained; schema or evidence-rule failures stop publication. The earlier run
allowed bounded model repair; the working runner removed that call on 2026-09-24.
Fresh runs are required when changing interpretation inputs.

### Structural isolation in gene-first mode

A prompt-only repair still produced a regulator-led P50 research candidate. Gene-first
runs now separate both research and synthesis. Functional research sees an allowlisted
bundle containing biological identity and program/distinctive genes, with no regulator
fields. Supporting regulator research is stored separately and runs only for programs
with selected regulators after masking. Each retained research session keeps half the
configured per-program budget; skipping the regulator pass leaves that share unspent.
The current runner's existing retry policy is unchanged. Functional research runs for
every current bundle; stale functional-bundle files are not added to the request set.

The functional annotation call receives no measured regulator block. Its label, overview,
modules and distinctive features are fixed before the regulator supplement call. That
call returns only regulator objects, and a strict schema rejects attempts to change the
functional fields. Only the regulator list is merged. Both original request/result pairs
are retained for inspection. The full 80-program run used a separate exact-gene
identity review before the regulator supplement, making three annotation calls per
program plus possible repair calls. The working runner now uses one functional
annotation call and, only when selected regulators exist, one regulator explanation
call. That explanation remains the basic step-7 prototype; its prompt is unchanged.
Mixed runs retain a functional annotation for every program and request supplements
only for selected programs. Empty supplement request/result files record a wholly
skipped stage. Missing, failed or truncated requested supplements still stop publication.
The combined
artifact omits misleading single-call usage; original files retain the per-call usage.

Regulator research coverage may remain `not_researched` or `searched_no_support`; the
report must expose that uncertainty. Structural isolation prevents measured regulator
inputs from choosing the functional result. It does not establish every biological
claim or prevent imperfect hypotheses arising from program genes or retrieved text.

The Haiku live pilot repeatedly violated prose/statistic constraints and confused Oat
with an organic-anion transporter, even after an identity review. The successful-run
model must therefore be recorded explicitly; format compliance or an LLM review is
not scientific validation. The final pilot uses the workflow's default Sonnet model
for annotation. Regulator mechanism prompts now contain only selected symbols and
qualitative observed responses, with retrieved coverage/identifiers; numerical
measurements are inserted by code. Summary CSV names no longer apply title case,
which previously changed acronyms such as DNA into Dna.

### Completed live validation

The final Sonnet annotation pilot for P21/P43/P50 completed with 7/10/17 regulator
cards and all four conditions per card. All 169 tests pass. Actual request audits
found no masked identities, gene-case mismatches, or post-supplement changes to
functional content. P50 now centers on pericentral nitrogen handling and solute
transport; Oat is correctly described as an enzyme. The final report retains
only citations chosen for revised modules, without positional research unions.
Ten regulators have retrieved-support records, twenty have searched-no-support
records, and four P43 regulators remain explicitly unresearched. Identifier checks
and targeted review are not complete scientific validation. The model change and
workflow changes were not evaluated separately. The final artifact is under
`/Volumes/IF_PHAGE/Hepatocyte/data/091026_program/analysis/gpi_gene_first_p21_43_50_fixed_v3/`.
