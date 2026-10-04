"""Run the bounded institutional-data campaign against one installed NET wheel.

Invoke with python -I -X utf8 from outside the checkout. Existing source files
are read; this driver acquires no data. Each lane has a 600-second process limit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_S = 600


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def installed_binding(wheel):
    path = ROOT / "scripts/check_domain_examples.py"
    spec = importlib.util.spec_from_file_location("_net_campaign_wheel_binding", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.installed_binding(wheel)


def _count(value, *, positive=False):
    return type(value) is int and value >= (1 if positive else 0)


def _nonnegative(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def lane_summary(name, path):
    """Check evidence counts and outcomes, without claiming a new raw-data audit."""
    with path.open("rb") as handle:
        raw = handle.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("Lane report exceeds 16 MiB")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Lane report must be an object")
    if name == "numerics":
        checks, counts = data.get("checks"), data.get("counts", {})
        if (data.get("schema") != "net.institutional-numerical-tests.v1" or
                data.get("status") != "PASS_WITH_UNTESTED_PHYSICAL_SCOPE" or
                not isinstance(counts, dict) or not isinstance(checks, list) or not checks or
                not _count(counts.get("failed")) or counts["failed"] != 0 or
                not _count(counts.get("passed"), positive=True) or counts["passed"] != len(checks) or
                any(not isinstance(check, dict) or check.get("status") != "PASS" for check in checks) or
                not isinstance(data.get("real_data_counts"), dict) or
                not all(_count(value, positive=True) for value in data["real_data_counts"].values()) or
                not data["real_data_counts"] or not data.get("scope") or "blocked" not in data):
            raise ValueError("Institutional numerics lacks consistent passing evidence and sample counts")
        return {key: data[key] for key in ("status", "counts", "real_data_counts", "scope", "blocked")}
    if name == "academic":
        sources, stfe, boundary = data.get("sources"), data.get("stfe"), data.get("adapter_boundary")
        if (data.get("schema") != "net.academic-real-data-check.v1" or data.get("status") != "PASS" or
                not _count(data.get("source_rows_processed"), positive=True) or
                not isinstance(sources, list) or not sources or not isinstance(stfe, dict) or
                not isinstance(boundary, dict) or not data.get("scope") or not data.get("limitations")):
            raise ValueError("Academic lane lacks required source counts, STFE evidence or adapter boundary")
        channels = []
        for source in sources:
            if not isinstance(source, dict) or not isinstance(source.get("channels"), list) or not source["channels"]:
                raise ValueError("Academic source has no measured channels")
            channels.extend(source["channels"])
        if any(not isinstance(channel, dict) or not _count(channel.get("sample_count"), positive=True)
               or channel.get("statistics_status") != "PASS" for channel in channels):
            raise ValueError("Academic channel statistics or sample counts are incomplete")
        total = sum(channel["sample_count"] for channel in channels)
        windows, width = stfe.get("window_count"), stfe.get("samples_per_window")
        spectra = [item for channel in channels for item in channel.get("native_periodogram_vs_scipy", [])]
        if (total != data["source_rows_processed"] or stfe.get("status") != "PASS" or
                not _count(windows, positive=True) or not _count(width, positive=True) or
                windows < len(channels) or windows * width > total or
                stfe.get("replay_identity_checks") != len(channels) or
                stfe.get("missing_observation_refusals") != len(channels) or
                stfe.get("unknown_uncertainty_preserved") is not True or
                not _nonnegative(stfe.get("max_absolute_error")) or
                not spectra or any(not isinstance(item, dict) or item.get("status") != "PASS"
                                   or not _count(item.get("sample_count"), positive=True) for item in spectra) or
                boundary.get("record_reader") != "PASS" or boundary.get("operation_route") != "BLOCKED"):
            raise ValueError("Academic source, window, spectrum or adapter outcomes are inconsistent")
        return {key: data[key] for key in ("status", "source_rows_processed", "stfe", "adapter_boundary", "rwth_acquisition", "scope", "limitations") if key in data}
    counts, groups = data.get("counts"), data.get("groups")
    if (data.get("schema") != "net.institutional-epa-campaign.v1" or data.get("software_test_status") != "PASS" or
            not isinstance(counts, dict) or not isinstance(groups, list) or not groups or
            any(not _count(counts.get(key), positive=True) for key in ("rows", "groups", "sites", "complete_pairs")) or
            not _count(counts.get("missing_pairs")) or counts["groups"] != len(groups) or
            counts["complete_pairs"] + counts["missing_pairs"] != counts["rows"] or
            not data.get("diagnostic_policy") or not data.get("limits")):
        raise ValueError("EPA lane lacks measured rows, comparison groups or consistent pair counts")
    total_rows, total_missing, sites = 0, 0, set()
    for group in groups:
        if (not isinstance(group, dict) or not _count(group.get("rows"), positive=True) or
                not _count(group.get("missing_pairs")) or group["missing_pairs"] > group["rows"] or
                not isinstance(group.get("group"), dict) or not group["group"].get("site")):
            raise ValueError("EPA group counts or site identity are incomplete")
        complete = group["rows"] - group["missing_pairs"]
        pair_report = group.get("complete_pairs_only")
        if complete and (not isinstance(pair_report, dict) or
                         not _count(pair_report.get("rows"), positive=True) or pair_report["rows"] != complete or
                         not isinstance(pair_report.get("independent_decimal_metrics"), dict) or
                         any(not _nonnegative(pair_report["independent_decimal_metrics"].get(key))
                             for key in ("rmse", "max_abs_error", "max_tolerance_excess"))):
            raise ValueError("EPA complete-pair counts or independent arithmetic evidence are incomplete")
        total_rows += group["rows"]
        total_missing += group["missing_pairs"]
        sites.add(group["group"]["site"])
    if total_rows != counts["rows"] or total_missing != counts["missing_pairs"] or len(sites) != counts["sites"]:
        raise ValueError("EPA summary counts contradict its retained comparison groups")
    return {key: data[key] for key in ("software_test_status", "counts", "diagnostic_policy", "limits")}


def run_lane(name, script, arguments, report_name, destination, env):
    lane = destination / name
    lane.mkdir()
    result_dir = lane / "results"  # Harnesses own this fresh directory.
    command = [sys.executable, "-I", "-X", "utf8", str(script), *arguments,
               "--output-dir", str(result_dir)]
    started = time.perf_counter()
    entry = {"name": name, "status": "FAIL", "started_at_utc": stamp(),
             "timeout_s": TIMEOUT_S, "command": command, "cwd": str(lane),
             "log": f"{name}/process.log"}
    try:
        entry["harness_sha256"] = digest(script)
        with (lane / "process.log").open("xb") as log:
            process = subprocess.run(command, cwd=lane, env=env, stdout=log,
                                     stderr=subprocess.STDOUT, timeout=TIMEOUT_S)
        entry["exit_code"] = process.returncode
        if process.returncode != 0:
            raise ValueError("Lane exited nonzero; inspect process.log")
        report_path = result_dir / report_name
        entry["summary"] = lane_summary(name, report_path)
        entry["report"] = report_path.relative_to(destination).as_posix()
        entry["report_sha256"] = digest(report_path)
        entry["status"] = "PASS"
    except subprocess.TimeoutExpired as exc:
        entry.update(status="TIMEOUT", error_type=type(exc).__name__,
                     message=f"Lane exceeded {TIMEOUT_S} seconds")
    except Exception as exc:
        entry.update(error_type=type(exc).__name__, message=str(exc))
    finally:
        entry["finished_at_utc"] = stamp()
        entry["elapsed_s"] = time.perf_counter() - started
        log_path = lane / "process.log"
        if log_path.exists():
            entry["log_sha256"] = digest(log_path)
        write_new(lane / "outcome.json", entry)
    return entry


def campaign(args, progress=None):
    def announce(message):
        nonlocal progress
        if progress is not None:
            try:
                progress(message)
            except OSError:
                # A closed progress consumer must not interrupt the campaign
                # or prevent its final evidence from being retained.
                progress = None

    destination = args.output_dir.resolve()
    if destination.is_relative_to(ROOT):
        raise ValueError("--output-dir must be outside the checkout")
    destination.mkdir(parents=True, exist_ok=False)
    wheel = args.wheel.resolve()
    work = (args.work_dir.resolve() if args.work_dir else
            Path(tempfile.mkdtemp(prefix="net-institutional-campaign-")) / "academic")
    started = time.perf_counter()
    report = {"schema": "net.institutional-campaign.v1", "status": "FAIL",
              "started_at_utc": stamp(), "python": sys.version, "executable": sys.executable,
              "isolated": bool(sys.flags.isolated), "utf8_mode": bool(sys.flags.utf8_mode),
              "driver_sha256": digest(Path(__file__).resolve()),
              "binding_helper_sha256": digest(ROOT / "scripts/check_domain_examples.py"),
              "wheel": str(wheel), "lane_timeout_s": TIMEOUT_S,
              "inputs": {"corpus_root": str(args.corpus_root.resolve()),
                         "thermal_dir": str(args.thermal_dir.resolve()),
                         "cwru_dir": str(args.cwru_dir.resolve())},
              "academic_work_dir": str(work), "lanes": [],
              "scope": "Installed NET numerical kernels and retained data workflows on existing institutional source files",
              "physical_qualification": "not_established", "hardware_actuation": "not_performed",
              "state_admission": "not_performed", "MCP_end_to_end": "not_claimed",
              "interpretation": "Lane PASS means software checks passed. Expected source refusals, indeterminate comparisons and EPA diagnostic disagreements remain in lane reports."}
    write_new(destination / "campaign-start.json", report)
    try:
        if not sys.flags.utf8_mode:
            raise ValueError("Run with python -I -X utf8")
        if work.is_relative_to(destination) or work.is_relative_to(ROOT):
            raise ValueError("--work-dir must be outside both campaign outputs and checkout")
        announce("Checking the installed candidate wheel before running data lanes.")
        report["binding_before"] = {"status": "PASS", **installed_binding(wheel)}
        env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
        academic_args = ["--source-dir", str(args.cwru_dir.resolve()),
                         "--work-dir", str(work), "--require-installed"]
        for option in ("rwth_response", "stfe_source_root", "stfe_revision"):
            value = getattr(args, option)
            if value is not None:
                academic_args.extend(["--" + option.replace("_", "-"),
                                      str(value.resolve()) if isinstance(value, Path) else value])
        lanes = [
            ("numerics", "check_institutional_numerics.py",
             ["--nist-dir", str(args.corpus_root.resolve() / "assets/foundations"),
              "--thermal-dir", str(args.thermal_dir.resolve())], "institutional-results.json"),
            ("academic", "check_academic_data.py", academic_args, "academic-checks.json"),
            ("epa", "check_epa_real_data.py", ["--corpus", str(args.corpus_root.resolve())], "report.json"),
        ]
        for index, (name, script, arguments, result) in enumerate(lanes, start=1):
            # A failed lane never prevents the remaining independent lanes.
            announce(f"[{index}/{len(lanes)}] Running {name}; log: {destination / name / 'process.log'}")
            outcome = run_lane(name, ROOT / "scripts" / script,
                               arguments, result, destination, env)
            report["lanes"].append(outcome)
            detail = f" - {outcome['message']}" if outcome.get("message") else ""
            announce(f"[{index}/{len(lanes)}] {name}: {outcome['status']}{detail}")
    except Exception as exc:
        report.update(error_type=type(exc).__name__, message=str(exc))
    finally:
        announce("Checking the installed candidate wheel after the run.")
        try:
            report["binding_after"] = {"status": "PASS", **installed_binding(wheel)}
            if report.get("binding_before") != report["binding_after"]:
                raise ValueError("Installed wheel binding changed during campaign or preflight failed")
        except Exception as exc:
            report["binding_after"] = {"status": "FAIL", "error_type": type(exc).__name__, "message": str(exc)}
        passed = (len(report["lanes"]) == 3 and all(lane["status"] == "PASS" for lane in report["lanes"])
                  and report.get("binding_before", {}).get("status") == "PASS"
                  and report["binding_after"]["status"] == "PASS" and "error_type" not in report)
        report["status"] = "PASS" if passed else "FAIL"
        report["finished_at_utc"] = stamp()
        report["elapsed_s"] = time.perf_counter() - started
        report["counts"] = {"passed": sum(lane["status"] == "PASS" for lane in report["lanes"]),
                            "failed": sum(lane["status"] != "PASS" for lane in report["lanes"]),
                            "not_run": 3 - len(report["lanes"])}
        write_new(destination / "campaign.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("corpus-root", "thermal-dir", "cwru-dir", "output-dir", "wheel"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, help="Fresh academic scratch directory outside outputs; defaults to system temporary storage and is retained")
    parser.add_argument("--rwth-response", type=Path, help="Optional already acquired response; no acquisition is attempted")
    parser.add_argument("--stfe-source-root", type=Path)
    parser.add_argument("--stfe-revision")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress on stderr; retain the JSON result on stdout")
    args = parser.parse_args(argv)
    try:
        report = campaign(args, progress=None if args.quiet else
                          lambda message: print(message, file=sys.stderr, flush=True))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    summary = {"status": report["status"], "counts": report["counts"],
               "report": str(args.output_dir.resolve() / "campaign.json")}
    problems = [{"lane": lane["name"], "status": lane["status"],
                 "message": lane.get("message", "Inspect the retained lane report"),
                 "log": str(args.output_dir.resolve() / lane["log"])}
                for lane in report["lanes"] if lane["status"] != "PASS"]
    if report.get("message"):
        summary["error"] = report["message"]
    elif report["binding_after"]["status"] != "PASS":
        summary["error"] = report["binding_after"]["message"]
    if problems:
        summary["failed_lanes"] = problems
    print(json.dumps(summary))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
