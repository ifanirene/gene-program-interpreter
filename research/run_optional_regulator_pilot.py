"""Run a bounded mixed-regulator pilot from a retained run's deterministic inputs.

Uses fresh Haiku research and annotations; saves an immutable source snapshot and
native manifest. --replay exercises routing with saved results and no model or
literature calls. The output must be a new directory owned by the dataset.
"""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

import pandas as pd
import yaml

from gpi import run_pipeline as pipeline
from gpi.gene_first_synthesis import selected_regulators


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def describe(path, role):
    path = Path(path).resolve()
    return dict(path=str(path), role=role, size_bytes=path.stat().st_size,
                mtime_utc=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def run(args):
    root = Path(__file__).resolve().parents[1]
    source = args.source_run.resolve()
    output = args.output.resolve()
    programs = sorted(set(args.programs))
    withheld = set(args.without_regulators)
    if not withheld <= set(programs):
        raise ValueError("Withheld programs must be selected for the pilot")
    output.mkdir(parents=True, exist_ok=False)
    provenance = output / "provenance"
    provenance.mkdir()
    inputs = []

    def copy_input(src, dest):
        src, dest = Path(src), Path(dest)
        inputs.append(describe(src, "retained input; historical execution provenance belongs to source run"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    config = yaml.safe_load(args.source_config.read_text())
    copy_input(args.source_config, provenance / "source_config.yaml")
    copy_input(config["inputs"]["gene_loading"], output / "inputs" / "gene_loading.csv")
    config["inputs"]["gene_loading"] = str(output / "inputs" / "gene_loading.csv")
    if config["inputs"].get("regulators") or config["inputs"].get("celltype_enrichment"):
        raise ValueError("This pilot requires a source with condition-keyed regulators and no celltype input")
    for condition, path in config["inputs"].get("regulators_by_condition", {}).items():
        dest = output / "inputs" / f"regulators_{condition}.csv"
        inputs.append(describe(path, "source regulator table; pilot withholds selected program rows"))
        frame = pd.read_csv(path)
        frame = frame[frame.program_id.isin(set(programs) - withheld)]
        frame.to_csv(dest, index=False)
        config["inputs"]["regulators_by_condition"][condition] = str(dest)
    paths = pipeline.Paths(output)
    paths.bundles_dir.mkdir()
    for pid in programs:
        copy_input(source / "program_bundles" / f"P{pid}.json", paths.bundles_dir / f"P{pid}.json")
        if pid in withheld:
            path = paths.bundles_dir / f"P{pid}.json"
            bundle = json.loads(path.read_text())
            for field in ("perturbation_regulators", "regulator_effects_all_conditions", "regulator_recurrence"):
                bundle.pop(field, None)
            write_json(path, bundle)
    copy_input(source / "ncbi_context.json", paths.ncbi_context)
    context = json.loads(paths.ncbi_context.read_text())
    context = {str(pid): context[str(pid)] for pid in programs}
    for pid in withheld:
        for field in list(context[str(pid)]):
            if field.startswith("regulator_"):
                context[str(pid)].pop(field)
    write_json(paths.ncbi_context, context)
    for name in ("enrichment_filtered.csv", "enrichment_full.csv"):
        copy_input(source / "string_enrichment" / name, paths.enrich_dir / name)
    for path in (source / "string_enrichment" / "figures").rglob("*.png"):
        # Retain the source layout; renderer resolves the exact program's images.
        copy_input(path, paths.figures_dir / path.relative_to(source / "string_enrichment" / "figures"))
    if (source / "theme_dictionary.json").exists():
        copy_input(source / "theme_dictionary.json", paths.theme_dict)
    if args.reuse_research or args.replay:
        for pid in programs:
            copy_input(source / "research_results" / f"P{pid}.json", paths.research_dir / f"P{pid}.json")
            if pid not in withheld:
                copy_input(source / "regulator_research" / "research_results" / f"P{pid}.json",
                           output / "regulator_research" / "research_results" / f"P{pid}.json")
            else:
                path = paths.research_dir / f"P{pid}.json"
                data = json.loads(path.read_text())
                data["regulator_coverage"] = []
                data.get("meta", {}).pop("regulator_research_path", None)
                write_json(path, data)
    replay_annotations = {}
    if args.replay:
        for pid in programs:
            src = source / "annotations" / f"topic_{pid}_annotation.json"
            copy_input(src, provenance / "replay_annotations" / src.name)
            replay_annotations[pid] = json.loads(src.read_text())
    saved_functional = None
    if args.reuse_functional_from:
        if args.replay:
            raise ValueError("Functional reuse and offline replay are separate modes")
        saved_requests = args.reuse_functional_from / "anthropic_batch_request.json"
        saved_results = args.reuse_functional_from / "anthropic_batch_request_results.jsonl"
        copy_input(saved_requests, provenance / "reused_functional_requests.json")
        copy_input(saved_results, provenance / "reused_functional_results.jsonl")
        saved_functional = (
            {r["custom_id"]: r["params"] for r in json.loads(saved_requests.read_text())["requests"]},
            {r["custom_id"]: r for r in (json.loads(line) for line in saved_results.read_text().splitlines())},
        )
    model = "claude-haiku-4-5-20251001"
    config.update(programs=programs, output_dir=str(output))
    config["context"]["report_dataset_crumb"] = "Optional regulator software pilot; deliberately withheld inputs"
    config["research"].update(model=model, concurrency=2, max_turns=30,
                              max_budget_usd=2.0, per_program_timeout=600, auth="subscription")
    config["annotation"].update(model=model, max_tokens=16384, concurrency=2, batch=False)
    config["presentation"]["model"] = model
    config_path = output / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    inputs.extend(describe(path, "resolved pilot input") for path in (output / "inputs").glob("*"))
    inputs.extend(describe(path, "resolved pilot bundle") for path in paths.bundles_dir.glob("*.json"))
    inputs.extend([describe(paths.ncbi_context, "resolved gene context"),
                   describe(config_path, "resolved run configuration")])
    snapshot = provenance / "source"
    code = []
    for directory in ("gpi", "research"):
        for path in (root / directory).iterdir():
            if path.is_file() and path.suffix in {".py", ".json", ".md"}:
                dest = snapshot / path.relative_to(root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
                code.append(describe(dest, "executed source snapshot"))
    for name in ("pyproject.toml", "uv.lock"):
        if (root / name).exists():
            shutil.copy2(root / name, snapshot / name)
            code.append(describe(snapshot / name, "dependency declaration"))
    (provenance / "working_diff.patch").write_text(subprocess.check_output(
        ["git", "diff", "--", "gpi", "research", "tests"], cwd=root, text=True))
    script = snapshot / Path(__file__).resolve().relative_to(root)
    execution = dict(command=[sys.executable, "-m", "research.run_optional_regulator_pilot", *sys.argv[1:]],
                     working_directory=str(root), script=str(script),
                     script_sha256=describe(script, "script")["sha256"],
                     git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                     git_dirty=bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root)),
                     interpreter=sys.executable, started_at=timestamp(), code=code,
                     step_scripts=[dict(script=item["path"], script_sha256=item["sha256"]) for item in code])
    environment = dict(python=sys.version, platform=platform.platform(), packages={
        name: version(name) for name in ("pandas", "pydantic", "anthropic", "claude-agent-sdk", "pyyaml")})
    write_json(provenance / "execution.json", execution)
    write_json(provenance / "environment.json", environment)
    write_json(provenance / "inputs.json", inputs)
    print(yaml.safe_dump(config, sort_keys=False), flush=True)
    cfg = pipeline.PipelineConfig.from_yaml(config_path)
    flags = pipeline.Flags(deterministic_presentation=True)
    if not args.replay:
        pipeline.load_env_file()
    else:
        # Replace only the external executors. Request preparation, routing,
        # merging, parsing, publication validation and rendering stay real.
        from research import research_parallel

        async def replay_research(bundle_paths, *, out_dir, audit_dir, **kwargs):
            functional = Path(out_dir) == paths.research_dir
            origin = source / "research_results" if functional else source / "regulator_research" / "research_results"
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            written = []
            for bundle_path in bundle_paths:
                data = json.loads((origin / bundle_path.name).read_text())
                data.setdefault("meta", {})["offline_replay_source"] = str(origin / bundle_path.name)
                dest = Path(out_dir) / bundle_path.name
                write_json(dest, data)
                written.append(dest)
            return written

        research_parallel.run_research = replay_research
        original_subprocess = pipeline._run_subprocess

        def replay_annotations_call(argv, dry_run):
            if "gpi.anthropic_live" not in argv:
                return original_subprocess(argv, dry_run)
            requests = json.loads(paths.batch_request.read_text())["requests"]
            rows = []
            for request in requests:
                pid = int(request["custom_id"].split("_")[1])
                saved = json.loads(json.dumps(replay_annotations[pid]))
                supplement = "FINALIZED PROGRAM:" in request["params"]["messages"][0]["content"]
                if supplement:
                    value = {"regulators": saved["regulators"]}
                else:
                    saved["regulators"] = []
                    value = saved
                rows.append(dict(custom_id=request["custom_id"], offline_replay=True,
                                 result=dict(type="succeeded", message=dict(stop_reason="end_turn",
                                 content=[dict(type="text", text=json.dumps(value))]))))
            paths.batch_results.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

        pipeline._run_subprocess = replay_annotations_call
    if saved_functional is not None:
        original_subprocess = pipeline._run_subprocess
        reused = False

        def reuse_functional_call(argv, dry_run):
            nonlocal reused
            if "gpi.anthropic_live" not in argv or reused:
                return original_subprocess(argv, dry_run)
            requests = json.loads(paths.batch_request.read_text())["requests"]
            rows = []
            for request in requests:
                cid = request["custom_id"]
                if request["params"] != saved_functional[0][cid]:
                    raise ValueError(f"Cannot reuse changed functional request: {cid}")
                rows.append(saved_functional[1][cid])
            paths.batch_results.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            reused = True

        pipeline._run_subprocess = reuse_functional_call
    stages = []
    audit = dict(status="running", programs=programs, withheld_regulators=sorted(withheld),
                 execution_mode="offline_replay" if args.replay else "live", stages=stages,
                 functional_responses_reused=bool(saved_functional))
    try:
        steps = ["research"] if args.replay else [] if args.reuse_research else ["research", "verify"]
        steps += ["annotate", "presentation", "html_report"]
        for step in steps:
            start = time.monotonic()
            record = dict(step=step, started_at=timestamp(), status="running")
            stages.append(record)
            write_json(output / "pilot_audit.json", audit)
            info = pipeline.STEP_RUNNERS[step](cfg, paths, flags)
            record.update(status="passed", seconds=time.monotonic()-start, info=info)
            if step == "research":
                for directory in (paths.research_dir, output / "regulator_research" / "research_results"):
                    for path in directory.glob("P*.json"):
                        data = json.loads(path.read_text())
                        if not data.get("candidate_mechanisms") or data.get("meta", {}).get("status") == "failed":
                            raise ValueError(f"Research incomplete: {path}")
            write_json(output / "pilot_audit.json", audit)
        def responses(path):
            return {r["custom_id"]: json.loads("".join(b.get("text", "") for b in r["result"]["message"]["content"]))
                    for r in (json.loads(line) for line in path.read_text().splitlines())}
        cores = responses(output / "functional_annotation_results.jsonl")
        merged = responses(paths.batch_results)
        for key, core in cores.items():
            assert {k:v for k,v in core.items() if k != "regulators"} == {
                k:v for k,v in merged[key].items() if k != "regulators"}
        supplements = json.loads((output / "regulator_annotation_requests.json").read_text())["requests"]
        expected = {f"topic_{p}" for p in programs if selected_regulators(
            json.loads((paths.bundles_dir / f"P{p}.json").read_text()))}
        assert {r["custom_id"] for r in supplements} == expected
        assert not list(output.glob("functional_identity_review_*"))
        audit.update(status="passed", functional_requests=len(cores), regulator_requests=len(supplements),
                     functional_content_preserved=True, completed_at=timestamp())
    except Exception as exc:
        audit.update(status="failed", error=f"{type(exc).__name__}: {exc}", completed_at=timestamp())
        write_json(output / "pilot_audit.json", audit)
        raise
    write_json(output / "pilot_audit.json", audit)
    artifacts = [str(path.relative_to(output)) for path in sorted(output.rglob("*")) if path.is_file()]
    output_hashes = {name: describe(output / name, "output")["sha256"] for name in artifacts}
    manifest = dict(schema_version=1, analysis_id="gpi-optional-regulator-pilot", variant_id=output.name,
                    created_at=timestamp(), provenance_mode="native", execution=execution, inputs=inputs,
                    parameters=dict(config=config, withheld_regulator_programs=sorted(withheld),
                                    research_reused=args.reuse_research or args.replay,
                                    annotations_replayed=args.replay,
                                    functional_responses_reused=bool(saved_functional), presentation="deterministic"),
                    environment=environment, outputs=dict(directory=str(output), artifacts=artifacts, sha256=output_hashes),
                    validation=dict(status="passed", checks=["pipeline publication validation", "functional content unchanged by supplement", "only selected programs receive regulator requests", "identity reviewer absent"],
                                    limitations=["Offline replay; no new model output" if args.replay else "Haiku software pilot; not a controlled biological-quality benchmark", "identifier resolution is not claim entailment"]),
                    relationships=dict(derived_from=str(source / (
                        "analysis_manifest.json" if (source / "analysis_manifest.json").exists()
                        else "provenance/execution.json")), supersedes=None))
    write_json(output / "analysis_manifest.json", manifest)
    print(json.dumps(audit, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--programs", nargs="+", type=int, required=True)
    parser.add_argument("--without-regulators", nargs="+", type=int, default=[])
    parser.add_argument("--reuse-research", action="store_true")
    parser.add_argument("--replay", action="store_true", help="Offline executor replay; no model or literature calls")
    parser.add_argument("--reuse-functional-from", type=Path,
                        help="Reuse saved functional responses only when request parameters match exactly")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
