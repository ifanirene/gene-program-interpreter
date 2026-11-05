"""Optional aging context must not invent findings or turn missing weights into zero."""
import json
import re
import subprocess

from gpi.html_report import generate_design_a_html


def helpers():
    html=generate_design_a_html([],0,'2026-11-05')
    esc=re.search(r'    const esc = .*?;\n',html).group()
    start=html.index('    function ridgeAgeCell(')
    end=html.index('    const IDS =',start)
    return esc+html[start:end]


def evaluate(expression):
    result=subprocess.run(['node','-e',helpers()+"\nprocess.stdout.write(JSON.stringify("+expression+"));"],text=True,capture_output=True,check=True)
    return json.loads(result.stdout)


def test_ridge_optional_and_missing_not_zero():
    assert evaluate('ridgeAgeCell({})')==''
    rendered=evaluate("ridgeAgeCell({ridge_age:{F:{std_weight:-0.125,age_difference:0.02},M:{std_weight:null,age_difference:null}}})")
    assert '-0.125 · +2.00' in rendered
    assert '— · —' in rendered
    assert '0.000' not in rendered
    zero=evaluate("ridgeAgeCell({ridge_age:{F:{std_weight:0,age_difference:0}}})")
    assert '0.000 · 0.00' in zero


def test_no_aging_note_without_finding_and_escape_supplied_text():
    assert evaluate('moduleAging({})')==''
    assert evaluate('moduleAging({aging:null})')==''
    rendered=evaluate("moduleAging({aging:{finding:'<script>bad</script>',evidence_scope:'Old mouse liver',supporting_pmids:['39979263','bad/url'],intervention_benefit:null}})")
    assert '<script>' not in rendered
    assert '&lt;script&gt;' in rendered
    assert 'Old mouse liver' in rendered
    assert 'https://pubmed.ncbi.nlm.nih.gov/39979263/' in rendered
    assert 'bad/url' not in rendered
    assert 'Potential benefit' not in rendered
