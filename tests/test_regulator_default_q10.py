"""Regulator eligibility must agree across pipeline stages without supplied flags."""
import json
import re

import pandas as pd
import pytest

from gpi import column_mapper, evidence_context, gene_summaries, html_report, theme_representation


@pytest.fixture
def regulator_table(tmp_path):
    # Raw P would admit every row: eligibility must use adjusted P when supplied.
    table = pd.DataFrame(dict(
        program_id=[1] * 6,
        grna_target=['Below05', 'Between05and10', 'Below10', 'At10', 'Above10', 'Missing'],
        log2FC=[1.0] * 6, p_value=[0.001] * 6,
        adj_pvalue=[0.049, 0.08, 0.09999, 0.1, 0.10001, None],
    ))
    path = tmp_path / 'regulators.csv'
    table.to_csv(path, index=False)
    return table, path


EXPECTED = {'Below05', 'Between05and10', 'Below10'}


@pytest.mark.parametrize('condition_keyed', [False, True])
def test_default_is_strict_adjusted_p_below_point_one(regulator_table, condition_keyed):
    raw, _ = regulator_table
    if condition_keyed:
        result = column_mapper.standardize_condition_regulator_results(raw, condition='all')
    else:
        result = column_mapper.standardize_regulator_results(raw)
    assert set(result.loc[result.significant, 'grna_target']) == EXPECTED
    # Users can still explicitly choose another cutoff.
    kwargs = {'condition': 'all'} if condition_keyed else {}
    loader = (column_mapper.standardize_condition_regulator_results if condition_keyed
              else column_mapper.standardize_regulator_results)
    stricter = loader(raw, significance_threshold=0.05, **kwargs)
    assert set(stricter.loc[stricter.significant, 'grna_target']) == {'Below05'}


@pytest.mark.parametrize('module', [gene_summaries, evidence_context])
def test_gene_context_and_annotation_context_use_same_default(regulator_table, module):
    _, path = regulator_table
    single = module.load_regulator_data(path)[1]
    condition = module.load_condition_regulator_data({'all': path})['all'][1]
    assert set(single.grna_target) == EXPECTED
    assert set(condition.loc[condition.significant, 'grna_target']) == EXPECTED


@pytest.mark.parametrize('condition_keyed', [False, True])
def test_full_report_selects_q08_and_excludes_q10(regulator_table, tmp_path, condition_keyed):
    _, path = regulator_table
    (tmp_path / 'summary.csv').write_text('Topic,Name\n1,Program 1\n')
    (tmp_path / 'genes.csv').write_text('Name,Score,program_id\nCldn5,2,1\nPecam1,1,1\n')
    (tmp_path / 'ann').mkdir()
    (tmp_path / 'figures').mkdir()
    report = tmp_path / 'report.html'
    html_report.generate_report(
        summary_csv=str(tmp_path / 'summary.csv'), annotations_dir=str(tmp_path / 'ann'),
        enrichment_dir=str(tmp_path / 'figures'), gene_loading_csv=str(tmp_path / 'genes.csv'),
        output_html=str(report), volcano_csv=None if condition_keyed else str(path),
        volcano_condition_csvs={'all': path} if condition_keyed else None,
    )
    program = json.loads(re.search(r'window.PROGRAMS = (.*);', report.read_text()).group(1))['1']
    assert {r['gene'] for r in program['regulators']} == EXPECTED
    points = program['condition_volcano']['all'] if condition_keyed else program['volcano']
    assert {p['g'] for p in points if p['s']} == EXPECTED


def test_prompt_and_theme_cli_defaults_use_q10():
    annotation = evidence_context.build_parser().parse_args(['prepare'])
    theme = theme_representation.build_parser().parse_args([
        '--gene-file', 'genes.csv', '--evidence-pack-output', 'evidence.json',
        '--prompt-output', 'prompt.md', '--extraction-response-output', 'response.json',
        '--output-json', 'themes.json', '--output-csv', 'themes.csv',
    ])
    assert annotation.regulator_significance_threshold == 0.1
    assert theme.regulator_significance_threshold == 0.1
