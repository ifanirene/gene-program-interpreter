"""Render a fresh heatmap report from a retained report and measured input tables.

No model, literature, enrichment or annotation stage is rerun. Retained source
artifacts are read only; the output must be a new dataset-owned directory.
"""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

import pandas as pd
import yaml

from gpi.column_mapper import collapse_regulator_guides, standardize_condition_regulator_results
from gpi.html_report import generate_design_a_html, measured_regulator_cards


def describe(path, role):
    path = Path(path).resolve()
    return dict(path=str(path), role=role, size_bytes=path.stat().st_size,
                mtime_utc=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def extract_json(text, variable):
    match = re.search(r'\b' + re.escape(variable) + r'\s*=\s*', text)
    if not match:
        raise ValueError(f'Missing {variable} in retained report')
    return json.JSONDecoder().raw_decode(text[match.end():])[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source, output = args.source_run.resolve(), args.output.resolve()
    if output == source or source in output.parents:
        raise ValueError('Use a fresh sibling variant; preserve the source run')
    output.mkdir(parents=True, exist_ok=False)
    provenance = output / 'provenance'
    provenance.mkdir()
    inputs = [describe(source / name, 'retained presentation input')
              for name in ('report.html', 'config.yaml', 'analysis_manifest.json')]
    config = yaml.safe_load((source / 'config.yaml').read_text())
    text = (source / 'report.html').read_text()
    programs = extract_json(text, 'PROGRAMS')
    crumb = extract_json(text, 'DATASET_CRUMB')
    threshold = float(config.get('settings', {}).get('regulator_significance_threshold', 0.05))
    frames = {}
    for condition, path in config['inputs']['regulators_by_condition'].items():
        inputs.append(describe(path, 'measured regulator input'))
        frames[condition] = collapse_regulator_guides(standardize_condition_regulator_results(
            pd.read_csv(path, sep=None, engine='python'), condition=condition,
            significance_threshold=threshold), significant_only=False)
    for p in programs.values():
        original = json.loads(json.dumps(p))
        p['regulators'] = measured_regulator_cards(p['regulators'], {
            c: frame[frame.program_id == p['id']] for c, frame in frames.items()})
        for before, after in zip(original['regulators'], p['regulators']):
            for key in ('gene', 'role', 'fc', 'confidence', 'mechanism'):
                if before.get(key) != after.get(key):
                    raise ValueError(f"Retained regulator {key} changed: {before['gene']}")
        for key in ('kegg_fig', 'process_fig'):
            value = p.get(key, '')
            if value and not value.startswith(('data:', 'http:', 'https:')):
                path = (source / value).resolve()
                if path.is_file():
                    inputs.append(describe(path, 'retained enrichment image'))
                    p[key] = 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()
        assert {k:v for k,v in original.items() if k not in ('regulators','kegg_fig','process_fig')} == {
            k:v for k,v in p.items() if k not in ('regulators','kegg_fig','process_fig')}
    snapshot = provenance / 'source'
    code = []
    for directory in ('gpi', 'research'):
        for path in sorted((root / directory).iterdir()):
            if path.is_file() and path.suffix in ('.py', '.json', '.md'):
                dest = snapshot / path.relative_to(root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
                code.append(describe(dest, 'executed source snapshot'))
    for name in ('pyproject.toml', 'uv.lock', '.claude-plugin/plugin.json'):
        dest = snapshot / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, dest)
        code.append(describe(dest, 'release or dependency declaration'))
    (provenance / 'working_diff.patch').write_text(subprocess.check_output(
        ['git', 'diff', '--', 'gpi', 'research', 'tests', 'pyproject.toml', 'uv.lock', '.claude-plugin'],
        cwd=root, text=True))
    script = snapshot / Path(__file__).resolve().relative_to(root)
    execution = dict(command=[sys.executable, '-m', 'research.render_regulator_heatmap_report', *sys.argv[1:]],
                     working_directory=str(root), script=str(script),
                     script_sha256=describe(script, 'executed script')['sha256'],
                     git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
                     git_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=root)),
                     interpreter=sys.executable, code=code)
    generated = datetime.now(timezone.utc).isoformat()
    report = generate_design_a_html(list(programs.values()), len(programs), generated, dataset_crumb=crumb)
    (output / 'report.html').write_text(report)
    artifacts = [str(path.relative_to(output)) for path in sorted(output.rglob('*')) if path.is_file()]
    manifest = dict(schema_version=1, analysis_id='gpi-regulator-heatmap-presentation',
                    variant_id=output.name, created_at=generated, provenance_mode='native',
                    execution=execution, inputs=inputs,
                    parameters=dict(source_config=config, regulator_significance_threshold=threshold,
                                    scale='symmetric per-program maximum absolute log2FC',
                                    representative_guide='lowest adjusted P, then largest absolute effect',
                                    stages_rerun=['HTML presentation'], release='0.3.0'),
                    environment=dict(python=sys.version, platform=platform.platform(), packages={
                        name:version(name) for name in ('pandas','numpy','markdown','pyyaml')}),
                    outputs=dict(directory=str(output), artifacts=artifacts, sha256={
                        name:describe(output/name,'output')['sha256'] for name in artifacts}),
                    validation=dict(status='passed', checks=['retained regulator values, roles and mechanisms unchanged',
                        'all other program content unchanged except embedding retained figures'],
                        limitations=['Presentation rerender only; biological validity remains source-run dependent']),
                    relationships=dict(derived_from=str(source/'analysis_manifest.json'), supersedes=None))
    (output/'analysis_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(f'Created {output / "report.html"}; preserved {len(programs)} programs')


if __name__ == '__main__':
    main()
