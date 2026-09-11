from gpi.evidence_context import PROMPT_TEMPLATE, format_regulator_analysis_context
from gpi.html_report import parse_regulators_detailed


def test_missing_regulator_input_is_not_reported_as_no_significant_hits():
    context = format_regulator_analysis_context({}, {}, program_id=1)

    assert "No regulator perturbation input was supplied" in context
    assert "No significant regulators identified" not in context
    assert "log2FC=N/A" in context


def test_prompt_requires_inferred_regulators_to_be_labeled():
    assert "label each one explicitly as inference" in PROMPT_TEMPLATE
    assert "do not infer biological meaning from" in PROMPT_TEMPLATE


def test_report_parses_literature_inferred_regulators_without_fake_fold_change():
    annotation = """
### 4. Regulator analysis

No regulator perturbation input was supplied for this run.

```
Hnf4a (nuclear receptor, hepatocyte identity master regulator, log2FC=N/A): [Confidence: Medium — inference from program genes]
Propose a mechanistic hypothesis: HNF4A may coordinate the hepatocyte identity genes.
```
"""

    assert parse_regulators_detailed(annotation) == [
        {
            "gene": "Hnf4a",
            "role": "inferred",
            "fc": "",
            "confidence": "Medium — inference from program genes",
            "mechanism": "HNF4A may coordinate the hepatocyte identity genes.",
        }
    ]
