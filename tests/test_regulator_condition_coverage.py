import pandas as pd

from gpi.html_report import measured_regulator_cards, parse_regulators_detailed
from gpi.evidence_context import format_condition_regulator_analysis_context


def frame(gene, fc, sig=True):
    return pd.DataFrame([dict(program_id=1, target_gene=gene, grna_target=gene,
                              log_2_fold_change=fc, significant=sig,
                              adj_p_value=0.01 if sig else 0.5, p_value=0.001)])


def test_explicit_roles_after_descriptors_and_multiline_confidence():
    for punctuation in [':', '=']:
        text = f'''## Regulator analysis
```
Insr (Insulin receptor; role{punctuation}Repressor, log2FC=+0.8):
[Confidence: High]
Propose a mechanistic hypothesis: Supporting evidence.
```
'''
        card = parse_regulators_detailed(text)[0]
        assert card['role'] == 'repressor'
        assert card['confidence'] == 'High'


def test_measured_cards_override_wrong_prose_and_preserve_all_conditions():
    cards = [dict(gene='Mlxipl', role='repressor', fc='female only')]
    out = measured_regulator_cards(cards, {'young_F': frame('Mlxipl', -3.69),
        'young_M': frame('Mlxipl', -1.69), 'aged_F': frame('Mlxipl', -3.92),
        'aged_M': frame('Mlxipl', -1.47, False)})[0]
    assert out['role'] == 'activator'
    assert 'young_M: -1.690' in out['fc']
    assert 'aged_M: -1.470 (not significant)' in out['fc']
    assert cards[0]['fc'] == 'female only'


def test_opposing_significant_directions_are_mixed():
    out = measured_regulator_cards([dict(gene='X',role='activator')],
        {'female': frame('X', -1), 'male': frame('X', 1)})[0]
    assert out['role'] == 'mixed'


def test_prompt_retains_unselected_and_nonsignificant_cohorts():
    data = {'female': {1: frame('X', -2)},
            'male': {1: pd.concat([frame('Y', -3), frame('X', -0.2, False)])}}
    prompt = format_condition_regulator_analysis_context(data, {}, 1,
                top_positive_regulators=1, top_negative_regulators=1)
    assert 'X | male | X | -0.200 | 5.000e-01 | False' in prompt
    assert 'X: 1/1 programs' in prompt
    assert 'does not establish an age or sex interaction' in prompt


def test_control_exclusion_precedes_selection():
    from gpi.evidence_context import select_top_condition_regulators
    df = pd.concat([frame('non-targeting', -9), frame('X', -1)])
    groups = select_top_condition_regulators({'female':{1:df}}, 1, 1, 1)
    assert groups['female']['positive'][0]['gene'] == 'X'


def test_bundle_full_coverage_and_breadth(gene_loading_csv):
    from research.bundle import build_bundle
    from gpi.context_profile import ContextProfile
    data={'female':{1:frame('X', -2)}, 'male':{1:frame('X', -.2, False)}}
    bundle=build_bundle(1,pd.read_csv(gene_loading_csv),ContextProfile.liver_demo(),
        ncbi_context={1:{'regulator_validation_by_condition':{'female':{
            'positive_regulators':[{'regulator':'X','log2fc':-2}]}}}},
        regulator_data=data, regulator_recurrence={'X':{'broad_top_hit':True}})
    assert len(bundle['regulator_effects_all_conditions']) == 2
    assert bundle['regulator_effects_all_conditions'][1]['significant'] is False
    assert bundle['regulator_recurrence']['X']['broad_top_hit']


def test_research_coverage_cannot_be_inferred_from_selection():
    from gpi.regulator_evidence import audit_research_coverage
    result=audit_research_coverage({'perturbation_regulators':{'male':[{'gene':'X'}]}}, {})
    assert result[0]['status']=='not_researched'


def test_workflow_rejects_regulator_numeric_prose(tmp_path):
    import pytest
    from gpi.annotation_validation import finalize_regulator_annotations
    path=tmp_path/'topic_1_annotation.md'
    path.write_text('''## Regulator analysis
```
X (activator, log2FC=-2): [Confidence: High]
Mechanistic hypothesis: X has log2FC=-2 across all conditions.
```
''')
    with pytest.raises(ValueError,match='validation failed'):
        finalize_regulator_annotations(tmp_path,{'female':{1:frame('X',-2)},
                                                'male':{1:frame('X',-.2)}},{1:['A','B']})


def test_workflow_fills_headers_from_all_measured_conditions(tmp_path):
    from gpi.annotation_validation import finalize_regulator_annotations
    path=tmp_path/'topic_1_annotation.md'
    path.write_text('''## Regulator analysis
```
X (repressor, log2FC=wrong): [Confidence: Low]
Mechanistic hypothesis: A possible supporting response.
```
''')
    finalize_regulator_annotations(tmp_path, {'female':{1:frame('X',-2)},
        'male':{1:frame('X',-.2,False)}},{1:['A','B']})
    assert 'X (activator' in path.read_text()
    assert 'male: -0.200 (not significant)' in path.read_text()


def test_broad_regulator_counts_use_all_programs():
    from gpi.regulator_evidence import recurrence
    data={'female':{pid: frame('X' if pid<3 else 'Y', -1).assign(program_id=pid)
                    for pid in range(1,5)}}
    result=recurrence(data)
    assert result['X']['top_programs']==2
    assert result['X']['tested_programs']==4
    assert result['X']['broad_top_hit']


def test_module_cannot_be_built_from_only_perturbation_targets(tmp_path):
    import pytest
    from gpi.annotation_validation import finalize_regulator_annotations
    (tmp_path/'topic_1_annotation.md').write_text('''## Functional modules and mechanisms

```
Nutrient signaling
A pathway suggested only by perturbation targets.
Key genes: X, Y
```

## Regulator analysis

```
X (activator, log2FC=measured): [Confidence: Low]
Mechanistic hypothesis: A supporting response.
```
''')
    with pytest.raises(ValueError,match='validation failed'):
        finalize_regulator_annotations(tmp_path,{'female':{1:frame('X', -2)}},{1:['A','B']})


def test_workflow_preserves_bold_regulator_headers(tmp_path):
    from gpi.annotation_validation import finalize_regulator_annotations
    path=tmp_path/'topic_1_annotation.md'
    path.write_text('''## Regulator analysis

**X (activator, log2FC=measured): [Confidence: Low]**
Mechanistic hypothesis: A supporting response.
''')
    finalize_regulator_annotations(tmp_path,{'female':{1:frame('X',-2)}},{1:['A','B']})
    assert parse_regulators_detailed(path.read_text())[0]['role']=='activator'


def test_retrieved_coverage_requires_search_and_identifiers():
    import pytest
    from pydantic import ValidationError
    from research.schema import RegulatorCoverage, AgentResearchResult
    from research.verify import normalize_agent_result
    with pytest.raises(ValidationError):
        RegulatorCoverage(gene='X',status='retrieved_support')
    record=RegulatorCoverage(gene='X',status='retrieved_support',queries=['X function'],identifiers=['PMID:123'])
    result=normalize_agent_result(AgentResearchResult(program_id='P1',regulator_coverage=[record]))
    assert result.regulator_coverage[0].identifiers==['PMID:123']
