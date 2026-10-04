"""Time small provider-free examples against an explicitly installed candidate wheel.

Run with python -I, from outside the checkout. Every example is mandatory and
runs in its own bounded process. Results establish only the declared toy cases.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
import zipfile


EXAMPLES = (
    "oscillator", "impact", "fluid-reservoir", "fluid-wave",
    "thermal-observer", "measured-thermal", "polymer", "temperature", "structural-rewrite",
)
ROOT = Path(__file__).resolve().parents[1]
DIST = "computational-instrumentation-workbench"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def installed_binding(wheel):
    """Bind every installed package file to the selected wheel, without source fallback."""
    require(sys.flags.isolated == 1, "Run with python -I to exclude checkout and user-site imports")
    require(not Path.cwd().resolve().is_relative_to(ROOT), "Run from outside the checkout")
    distribution = importlib.metadata.distribution(DIST)
    installed = Path(distribution.locate_file("ciw")).resolve(strict=True)
    require(not installed.is_relative_to(ROOT), "Install the candidate wheel outside the checkout")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    wheel_hash = digest(wheel)
    require(direct.get("url") == wheel.as_uri(), "Installed package must come from --wheel")
    require(direct.get("archive_info", {}).get("hashes", {}).get("sha256") == wheel_hash,
            "Installed distribution does not bind the selected wheel hash")
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.startswith("ciw/") and not name.endswith("/")]
        require(names and len(names) == len(set(names)), "Missing or ambiguous wheel package files")
        for name in names:
            require(".." not in Path(name).parts and "\\" not in name, "Unsafe wheel member")
            require(archive.read(name) == Path(distribution.locate_file(name)).read_bytes(),
                    "Installed package differs from wheel: " + name)
    return {"wheel": wheel.name, "wheel_sha256": wheel_hash,
            "installed_package": str(installed), "checked_package_files": len(names),
            "versions": {name: importlib.metadata.version(name) for name in (DIST, "numpy", "websockets")}}


def cli(directory, *args):
    """Use the installed public net command and retain its exact output."""
    from ciw.net import main
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        status = main(list(args))
    with (directory / "commands.log").open("a", encoding="utf-8") as stream:
        stream.write("net " + " ".join(args) + "\n" + out.getvalue() + err.getvalue() + "\n")
    reason = (err.getvalue() or out.getvalue()).strip()[:1000]
    require(status == 0, f"net {' '.join(args)} failed ({status}): {reason}; see commands.log")
    return json.loads(out.getvalue())


def oscillator(directory):
    from ciw.instruments import make_demo_run, validate_run, compute_statistics, compute_spectrum
    run = make_demo_run()
    validate_run(run)
    save(directory / "recording.json", run)
    q = run["channels"]["q"]["values"]
    v = run["channels"]["v"]["values"]
    energy = run["channels"]["energy"]["values"]
    require(len(q) == 768 and q[0] == 1.0 and v[0] == 0.0, "Oscillator sampling or initial condition changed")
    require(all(b <= a + 1e-12 for a, b in zip(energy, energy[1:])), "Damped energy increased")
    stats = compute_statistics(run, "q", [0.0, 12.0])
    spectrum = compute_spectrum(run, "q", [0.0, 12.0])
    save(directory / "statistics.json", stats)
    save(directory / "spectrum.json", spectrum)
    require(math.isclose(stats["mean"], math.fsum(q) / len(q), abs_tol=1e-14), "Mean differs from scalar reference")
    return {"scope": "analytic_synthetic_oscillator_and_analysis", "samples": len(q),
            "initial_energy_j": energy[0], "final_energy_j": energy[-1], "mean_q_m": stats["mean"]}


def impact(directory):
    request, bundle = directory / "request.json", directory / "run"
    cli(directory, "impact", "example", "--output", str(request))
    result = cli(directory, "impact", "run", str(request), "--output-dir", str(bundle))
    fresh = cli(directory, "impact", "verify", str(bundle))
    save(directory / "fresh-verification.json", fresh)
    require(result["status"] == fresh["status"] == "LOCAL", "Elastic impact is not locally qualified")
    require(fresh["fresh_numerical_verification"] is True, "Impact audit was not recomputed")
    cli(directory, "impact", "export", str(bundle), "--output", str(directory / "trajectory.csv"))
    return {"scope": "bounded_elastic_contact_benchmark", "qualification": result["status"],
            "metrics": result["metrics"], "checks": result["checks"]}


def fluid(directory, profile):
    request, bundle = directory / "request.json", directory / "run"
    cli(directory, "fluid", "example", "--profile", profile, "--output", str(request))
    result = cli(directory, "fluid", "run", "--request", str(request), "--output-dir", str(bundle))
    fresh = cli(directory, "fluid", "verify", str(bundle), "--output", str(directory / "fresh-verification.json"))
    require(result["status"] == fresh["status"] == "LOCAL", "Fluid example is not locally qualified")
    require(fresh["fresh_numerical_verification"] is True, "Fluid audit was not recomputed")
    require(fresh["fresh_verification_id"] != result["verification_id"], "Fluid audit reused an occurrence")
    return {"scope": "bounded_" + profile + "_benchmark", "qualification": result["status"],
            "metrics": result["metrics"], "checks": result["checks"]}


def thermal(directory, kind):
    from importlib.resources import files
    from ciw import scientific
    from ciw.control_contracts import bytes_ref
    from ciw.control_checks import compare
    from ciw.instruments import make_demo_run
    from ciw.scientific_observations import export_observations
    from ciw.session import Session
    if kind == "thermal-observer":
        raw = files("ciw").joinpath("operator_examples/thermal-source.json").read_bytes()
        (directory / "source.json").write_bytes(raw)
    else:
        raw = (directory / "source.json").read_bytes()
    entity = json.loads(raw)["asset_id"] if kind == "measured-thermal" else "example/core-shell"
    session = Session(make_demo_run(), directory / "original")
    selected = scientific.execute(session, kind, raw, label="declared synthetic example", repositories={})
    original_step = session.workbench.get_bundle(selected["bundle_id"])["steps"][0]
    workspace = session.save_workspace(directory / "original/workspace.json")
    binding = bytes_ref(workspace.read_bytes())
    view = export_observations(workspace, expected_sha256=binding, bundle_id=selected["bundle_id"],
                               stage="posterior", entity_id=entity)
    save(directory / "posterior.json", view)
    with scientific.open_workspace(workspace, directory / "replay", expected_sha256=binding) as restored:
        replay = scientific.replay(restored, selected["bundle_id"], repositories={})
        fresh_step = restored.workbench.get_bundle(replay["bundle"]["bundle_id"])["steps"][0]
    fresh_path = directory / "replay/workspace.json"
    fresh = export_observations(fresh_path, expected_sha256=bytes_ref(fresh_path.read_bytes()),
                               bundle_id=replay["bundle"]["bundle_id"], stage="posterior", entity_id=entity)
    save(directory / "replayed-posterior.json", fresh)
    comparison = compare(view.get("stream", view)["observations"], fresh.get("stream", fresh)["observations"], atol=0.0)
    save(directory / "replay-comparison.json", comparison)
    require(comparison["outcome"]["status"] == "PASS", "Thermal replay values changed")
    require(original_step["execution_id"] != fresh_step["execution_id"], "Thermal replay reused execution")
    details = {"scope": "synthetic_" + kind.replace("-", "_") + "_retained_lifecycle",
               "source_sha256": digest(directory / "source.json"),
               "samples": len(view.get("stream", view)["observations"]), "replay_comparison": comparison["outcome"]}
    if kind == "measured-thermal":
        from ciw import measured_thermal
        source = measured_thermal.validate_source(raw)
        data = measured_thermal.calculate(source)
        require(source["origin"] == "test_fixture", "Measured example must stay labelled as a synthetic fixture")
        require(data["evaluation"]["status"] == "PASS", "Declared fixture core comparison failed")
        details["held_out_comparison"] = data["evaluation"]
        save(directory / "fixture-evaluation.json", data)
    return details


def polymer(directory):
    result = cli(directory, "polymer", "demo", "--process", "injection_molding", "--output-dir", str(directory / "run"))
    require(result["numerical_audit_status"] == "PASS", "Polymer numerical audit failed")
    return {"scope": "synthetic_injection_molding_reference_models_and_toy_control",
            "workflow_status": result["status"], "numerical_audit_status": result["numerical_audit_status"],
            "part_conformity": result["part_conformity"], "cooling_status": result["cooling_status"]}


def temperature(directory):
    from ciw.temperature_contract import example_request
    from ciw.temperature_processing import process_request, validate_report
    request = example_request()
    report = process_request(request)
    validate_report(request, report)
    save(directory / "request.json", request)
    save(directory / "temperature-report.json", report)
    require(report["acceptance"]["status"] == "indeterminate", "Incomplete budget must not pass acceptance")
    require(report["uncertainty"]["status"] == "incomplete_declared_budget", "Synthetic budget unexpectedly complete")
    require(report["polymer_temperature"]["status"] == "unavailable", "Synthetic data cannot establish polymer temperature")
    covariance = report["calibrated_sensor_temperature"]["covariance_K2"]
    expected = [[0.30, 0.33], [0.33, 0.42]]
    require(all(math.isclose(actual, target, abs_tol=1e-12, rel_tol=0.0)
                for row, reference in zip(covariance, expected) for actual, target in zip(row, reference)),
            "Temperature joint covariance differs from hand-calculated fixture")
    return {"scope": "synthetic_affine_temperature_and_covariance_processing",
            "acceptance": "indeterminate", "uncertainty": "incomplete_declared_budget",
            "covariance_k2": covariance, "physical_qualification": "not_established"}


def structural_rewrite(directory):
    request, history = directory / "request.json", directory / "history.json"
    cli(directory, "rewrite", "example", "--profile", "thermal", "--output", str(request))
    result = cli(directory, "rewrite", "run", str(request), "--output", str(history))
    fresh = cli(directory, "rewrite", "verify", str(history), "--output", str(directory / "verification.json"))
    require(result["status"] == "COMPLETE" and fresh["status"] == "PASS", "Structural rewrite did not complete")
    record = json.loads(history.read_text(encoding="utf-8"))
    nodes = record["states"][-1]["graph"]["nodes"]
    capacity = math.fsum(row["parameters"]["heat_capacity"]["value"] for row in nodes if row["kind"] == "thermal_zone")
    require(len(nodes) == 2 and capacity == 100.0, "Thermal split did not preserve declared capacity")
    return {"scope": "bounded_structural_rewrite_replay_only", "states": result["states"],
            "total_heat_capacity_j_per_k": capacity, "numerical_simulation": "not_performed"}


def run_example(name, directory, installed):
    import ciw
    require(Path(ciw.__file__).resolve().parent == installed, "Example imported outside selected wheel")
    functions = {"oscillator": oscillator, "impact": impact, "polymer": polymer, "temperature": temperature,
                 "structural-rewrite": structural_rewrite}
    if name.startswith("fluid-"):
        result = fluid(directory, name.removeprefix("fluid-"))
    elif name in {"thermal-observer", "measured-thermal"}:
        result = thermal(directory, name)
    else:
        result = functions[name](directory)
    for name, module in tuple(sys.modules.items()):
        if name == "ciw" or name.startswith("ciw."):
            require(Path(module.__file__).resolve().is_relative_to(installed), "CIW module imported outside wheel: " + name)
    return result


def qualify(wheel, destination, revision, timeout_s=120):
    require(re.fullmatch(r"[0-9a-fA-F]{40}", revision), "--source-revision requires a full Git SHA")
    require(1 <= timeout_s <= 120, "Each example timeout must be between 1 and 120 seconds")
    destination.mkdir(parents=True, exist_ok=False)
    report = {"schema": "ciw.domain-examples.v1", "status": "FAIL", "candidate_revision": revision,
              "revision_binding": "caller_supplied_checkout_revision; wheel_bytes_bound_separately",
              "runner_sha256": digest(Path(__file__)), "python": sys.version,
              "platform": platform.platform(), "example_timeout_s": timeout_s, "suite_timeout_s": 540,
              "scope": "bounded_provider_free_software_examples", "physical_validation": "not_established",
              "external_provider_qualification": "not_performed", "state_admission": "not_performed",
              "examples": [{"name": name, "status": "NOT_RUN", "elapsed_s": None} for name in EXAMPLES]}
    start = time.perf_counter()
    try:
        report["installed_binding"] = installed_binding(wheel)
        env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
        for entry in report["examples"]:
            directory = destination / entry["name"]
            directory.mkdir()
            started = time.perf_counter()
            try:
                if entry["name"] == "measured-thermal":
                    shutil.copyfile(ROOT / "examples/measured-thermal/source.json", directory / "source.json")
                command = [sys.executable, "-I", str(Path(__file__).resolve()), "--_example", entry["name"],
                           "--output-dir", str(directory), "--_installed", report["installed_binding"]["installed_package"]]
                remaining = 540 - (time.perf_counter() - start)
                require(remaining > 0, "The 540-second suite budget was exhausted")
                with (directory / "process.log").open("wb") as log:
                    completed = subprocess.run(command, cwd=directory, env=env, stdout=log,
                                               stderr=subprocess.STDOUT, timeout=min(timeout_s, remaining))
                entry["exit_code"] = completed.returncode
                require(completed.returncode == 0, "Example process failed; see process.log")
                entry["details"] = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                entry["status"] = "PASS"
            except Exception as exc:
                entry.update(status="FAIL", error_type=type(exc).__name__, message=str(exc))
            finally:
                entry["elapsed_s"] = time.perf_counter() - started
                save(directory / "outcome.json", entry)
                save(destination / "report.json", report)
        report["status"] = "PASS" if all(row["status"] == "PASS" for row in report["examples"]) else "FAIL"
    except Exception as exc:
        report.update(error_type=type(exc).__name__, message=str(exc))
    finally:
        report["elapsed_s"] = time.perf_counter() - start
        report["artifact_sha256"] = {path.relative_to(destination).as_posix(): digest(path)
                                      for path in sorted(destination.rglob("*"))
                                      if path.is_file() and path != destination / "report.json"}
        save(destination / "report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--_example", choices=EXAMPLES, help=argparse.SUPPRESS)
    parser.add_argument("--_installed", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._example:
        try:
            result = run_example(args._example, args.output_dir.resolve(), args._installed.resolve())
            save(args.output_dir / "result.json", result)
            return 0
        except Exception:
            traceback.print_exc()
            return 1
    if args.wheel is None or args.source_revision is None:
        parser.error("--wheel and --source-revision are required")
    try:
        result = qualify(args.wheel.resolve(strict=True), args.output_dir.resolve(), args.source_revision, args.timeout_s)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    summary = {"status": result["status"], "elapsed_s": result["elapsed_s"],
               "report": str(args.output_dir.resolve() / "report.json"),
               "failed_examples": [
                   {"name": row["name"], "message": row.get("message", "Example failed"),
                    "logs": [str(args.output_dir.resolve() / row["name"] / name)
                             for name in ("process.log", "commands.log")
                             if (args.output_dir / row["name"] / name).is_file()]}
                   for row in result["examples"] if row["status"] == "FAIL"]}
    if "message" in result:
        summary["message"] = result["message"]
    print(json.dumps(summary))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
