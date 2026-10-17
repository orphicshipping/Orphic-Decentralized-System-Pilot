"""Run a bounded external CPU bead-spring study and retain its complete evidence."""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path

from .adapters.protocol import AdapterRefusal
from .data_cli import _read, _save_new
from .data_sources import retain_record
from .hoomd_contract import OPERATION, artifact_bytes, example_study, validate_result, validate_study
from .hoomd_workflow import HoomdRuntime, retain_result, source_parameters


def _add(workbench, native, family, dataset, stamp, dependencies=()):
    envelope = retain_record(native, dataset_id=dataset, revision=1, parent_source_id=None,
                             recorded_at=stamp, data_family=family)
    return workbench.add_source({"kind": "data-record", "label": dataset,
        "bytes_b64": base64.b64encode(json.dumps(envelope, allow_nan=False).encode()).decode(),
        **({"input_source_ids": list(dependencies)} if dependencies else {})})


def run_study(study, *, python, output_dir):
    from .data_workflow import comparison_parameters, project_observations
    from .hoomd_checks import QUANTITIES, reference_observations
    from .instruments import make_demo_run
    from .operations.registry import default_registry
    from .session import Session
    study = validate_study(study)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    stamp = datetime.now(timezone.utc).isoformat()
    registry = default_registry()
    registry.register(HoomdRuntime(python).operation())
    session = Session(make_demo_run(), output_dir / "session", operations=registry)
    source = _add(session.workbench, study, "hoomd-study", "particle-study", stamp)
    answer = session.handle({"protocol_version": 1, "request_id": "hoomd-study", "type": "operation.execute",
        "payload": {"operation_id": OPERATION, "parameters": source_parameters(session.workbench, source["source_id"])}})
    if answer["type"] != "response" or answer["payload"].get("status") != "completed":
        session.save_workspace(output_dir / "workspace.json")
        raise ValueError("HOOMD study refused; inspect retained workspace: " + json.dumps(answer.get("payload", {}).get("execution", {}).get("refusal", {})))
    result = answer["payload"]["result"]
    native = result["data"]["native_result"]
    retained = retain_result(session.workbench, result, dataset_id="native-particle-run", recorded_at=stamp, label="Native particle run")
    observed = project_observations(session.workbench, source_id=retained["source_id"], selector={},
        dataset_id="particle-observables", revision=1, parent_source_id=None, recorded_at=stamp, label="Particle observables")
    reference = _add(session.workbench, reference_observations(native), "observation-stream",
                     "static-mathematical-reference", stamp, [retained["source_id"]])
    comparisons = {}
    # Double precision GSD snapshots support these explicitly bounded checks.
    # These are SI absolute tolerances derived explicitly from reduced tolerances.
    scales = study["units"]
    tolerances = {"potential_energy": 1e-10 * scales["energy_J"], "kinetic_energy": 1e-10 * scales["energy_J"],
                  "pressure": 1e-10 * scales["energy_J"] / scales["length_m"]**3,
                  "radius_gyration": 1e-10 * scales["length_m"]}
    for quantity in QUANTITIES:
        compared = session.handle({"protocol_version": 1, "request_id": "reference-" + quantity,
            "type": "operation.execute", "payload": {"operation_id": "data.compare-observations.v1",
                "parameters": comparison_parameters(session.workbench, left_source_id=observed["source_id"],
                    right_source_id=reference["source_id"], quantity="hoomd." + quantity,
                    atol=tolerances[quantity], rtol=1e-10)}})
        if compared["type"] != "response" or compared["payload"].get("status") != "completed":
            raise ValueError("Independent comparison failed: " + json.dumps(compared))
        comparisons[quantity] = compared["payload"]["result"]["data"]["comparison"]
    _save_new(output_dir / "native-result.json", native, 8 * 1024 * 1024)
    for name, artifact in native["artifacts"].items():
        with (output_dir / (name + ".gsd")).open("xb") as stream:
            stream.write(artifact_bytes(artifact))
    session.save_workspace(output_dir / "workspace.json")
    report = {"schema": "ciw.hoomd-example-report.v1", "execution_status": "completed",
        "reference_status": "PASS" if all(item["outcome"]["status"] == "PASS" for item in comparisons.values()) else "FAIL", "workspace": str(output_dir / "workspace.json"),
        "operation_id": OPERATION, "result_id": result["result_id"], "runtime": native["runtime"],
        "native_artifact_sha256": {key: item["sha256"] for key, item in native["artifacts"].items()},
        "frame_counts": {key: len(item["steps"]) for key, item in native["summary"]["native_frames"].items()},
        "independent_static_comparisons": comparisons, "authority": native["authority"]}
    _save_new(output_dir / "report.json", report, 8 * 1024 * 1024)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="net hoomd", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    example = commands.add_parser("example")
    example.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("study", type=Path)
    run.add_argument("--python", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("result", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            _save_new(args.output, example_study(), 8 * 1024 * 1024)
            result = {"study": str(args.output), "physical_validation": "not_established"}
        elif args.command == "inspect":
            from .adapters.subprocess import _json
            value = validate_result(_json(_read(args.result)))
            result = {"study_ref": value["study_ref"], "summary": value["summary"], "authority": value["authority"],
                      "runtime_execution": "not_performed", "native_bytes": "verified"}
        else:
            from .adapters.subprocess import _json
            result = run_study(_json(_read(args.study)), python=args.python, output_dir=args.output_dir)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get("reference_status") == "FAIL" else 0
    except (ValueError, OSError, AdapterRefusal) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
