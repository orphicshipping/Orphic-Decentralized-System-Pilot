"""Run native NET/STFE calculations on pinned public CWRU measurements.

No raw-source bytes are redistributed. STFE event/receipt times describe the
declared offline replay in sample ticks, never invented acquisition latency.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import math
from pathlib import Path
import subprocess
import sys
import sysconfig
import uuid
import zipfile

import numpy as np

if not __debug__:
    raise RuntimeError("Verification cannot run with Python optimization enabled")

ROOT = Path(__file__).resolve().parents[1]
from ciw.adapters.oscillator import periodogram_values, summarize_values
from ciw.adapters.protocol import AdapterRefusal, InstrumentManifest
from ciw.adapters.registry import default_registry

SOURCES = {
    "CWRU-97.mat": {
        "sha256": "16bf48babcf1c7ac224bc1a81cd9eafdb27e42d5cf559761907e067e8eeadf3c",
        "url": "https://engineering.case.edu/sites/default/files/97.mat",
        "condition": "normal_0hp",
        "bytes": 3903344,
        "channels": {"X097_DE_time": 243938, "X097_FE_time": 243938},
    },
    "CWRU-105.mat": {
        "sha256": "f80b0ea04fd06b372a0eaec7c056543ea37e4bb4727a5b173d2a5bacd2aa9cab",
        "url": "https://engineering.case.edu/sites/default/files/105.mat",
        "condition": "inner_race_0.007in_0hp",
        "bytes": 2910768,
        "channels": {"X105_DE_time": 121265, "X105_FE_time": 121265, "X105_BA_time": 121265},
    },
}
UNIT = "source_amplitude_unit_unverified"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def create_run_directories(output: Path, work: Path) -> None:
    output, work = output.resolve(), work.resolve()
    if work == output or work.is_relative_to(output):
        raise ValueError("Original-sample working receipts must remain outside the output directory")
    if output.exists() or work.exists():
        raise FileExistsError("Both output and work run directories must be fresh; existing evidence is never overwritten")
    output.mkdir(parents=True, exist_ok=False)
    work.mkdir(parents=True, exist_ok=False)


def load_stfe(source_root: Path, revision: str | None):
    source_root = source_root.resolve()
    kernel = source_root / "stfe/window.py"
    if not kernel.is_file():
        raise FileNotFoundError(f"Explicit STFE source kernel is missing: {kernel}")
    if revision is None:
        revision = subprocess.check_output(["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True).strip()
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise ValueError("STFE implementation revision must be the recorded 40-character Git revision")
    spec = importlib.util.spec_from_file_location("academic_explicit_stfe_kernel", kernel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, {"kind": "explicit_local_source_file", "source_root": str(source_root),
                    "path": str(kernel), "sha256": sha256(kernel), "declared_revision": revision}


def acquisition_manifest(stfe_binding: dict) -> dict:
    return {"schema": "net.academic-input-acquisition.v1",
        "raw_data_included": False,
        "instructions": "Download each official URL to its relative_path under a private working directory. Verify both bytes and SHA-256 before loading. Pass that academic directory as --source-dir. Raw file redistribution terms remain unresolved.",
        "files": [{"relative_path": "inputs/academic/" + name, "original_url": source["url"],
                   "sha256": source["sha256"], "bytes": source["bytes"],
                   "condition": source["condition"], "expected_channels_and_counts": source["channels"],
                   "media_kind": "MATLAB MAT v5", "redistribution": "not_included_terms_unresolved"}
                  for name, source in SOURCES.items()],
        "primary_source_pages": [
            "https://engineering.case.edu/bearingdatacenter/12k-drive-end-bearing-fault-data",
            "https://engineering.case.edu/bearingdatacenter/apparatus-and-procedures",
            "https://publications.rwth-aachen.de/record/1016199"],
        "stfe_source_binding": {"relative_kernel_path": "instruments/measurement/signal-processing/stfe/window.py",
                                "sha256": stfe_binding["sha256"], "revision": stfe_binding["declared_revision"]},
        "reproduction": "Use python -I scripts/check_academic_data.py --require-installed with a regular wheel install of NET plus its numerical-checks extra. Pass fresh --output-dir and --work-dir. For an exported tree without .git, also pass --stfe-revision from this manifest; --stfe-source-root explicitly selects the provider tree."}


def reject_nonarchive(path: Path) -> None:
    """An HTTP success response is not evidence that a data archive arrived."""
    if not zipfile.is_zipfile(path):
        raise ValueError("download is not a valid ZIP; HTML/challenge response is not experimental data")


def window_starts(count: int, max_windows: int) -> list[int]:
    if count < 1 or max_windows < 1:
        raise ValueError("positive sample and window counts required")
    complete = count // 32
    return sorted(set(np.linspace(0, max(0, complete - 1), min(max_windows, max(1, complete)), dtype=int) * 32))


def stfe_request(values, *, channel: str, start: int, source_digest: str, revision: str, created: str) -> dict:
    values = [float(v) for v in values]
    refs = [f"cwru:{source_digest}:{channel}:sample:{start+i}" for i in range(len(values))]
    end = start + len(values)
    return {
        "operation_id": "stfe.window-mean.v1",
        "source_batch_ref": f"cwru-file:{source_digest}",
        "source_batch_digest": "sha256:" + source_digest,
        "samples": [{"observation_ref": ref, "event_time": start+i,
                     "received_at": end, "value": value, "missing": False}
                    for i, (ref, value) in enumerate(zip(refs, values))],
        "window": {"start": start, "end": end, "received_by": end,
                   "decision_time": end, "sample_period": 1, "max_lateness": len(values)},
        "channel_id": channel, "value_unit": UNIT,
        "frame": "cwru:as_published_accelerometer_channel",
        "clock_basis": "offline-replay-sample-ticks-not-acquisition-time",
        "clock_mapping_ref": "mapping:sample-index-to-offline-replay-ticks:identity",
        "frame_mapping_ref": "mapping:source-channel-to-replay-channel:identity",
        "calibration_refs": [],
        "uncertainty": {"status": "unknown", "crosscov_policy": "unknown", "matrix": None,
                        "observation_refs": refs},
        "execution_id": "execution:" + uuid.uuid4().hex,
        "created_at": created, "implementation_revision": revision,
    }


def adapter_boundary(values, source: dict) -> dict:
    manifest = InstrumentManifest(
        instrument_id="cwru-vibration-research-record.v1", units={"acceleration": UNIT},
        frames=("cwru:drive-end",), supported_operations=(),
        calibration_requirements={"status": "not_available"})
    values = [float(v) for v in values[:64]]
    fs = 12000.0
    run = {"run_id": "cwru:105:DE:bounded-reader-check", "evidence_id": "sha256:" + source["sha256"],
           "instrument": manifest.instrument_id,
           "metadata": {"duration_s": len(values)/fs, "sample_count": len(values),
                        "sample_rate_hz": fs, "coordinate_frame": "cwru:drive-end",
                        "provenance": {"url": source["url"], "sample_slice": [0, len(values)]},
                        "manifest": manifest.to_dict()},
           "time_s": (np.arange(len(values))/fs).tolist(),
           "channels": {"acceleration": {"unit": UNIT, "values": values}}}
    registry = default_registry()
    registry.validate_run(run)
    try:
        registry.execute("spectrum.periodogram.v1", run, {"channel": "acceleration", "interval_s": [0, len(values)/fs]})
    except AdapterRefusal as exc:
        return {"record_reader": "PASS", "operation_route": "BLOCKED", "refusal": exc.to_dict(),
                "default_registered_instruments": [m["instrument_id"] for m in registry.manifests()],
                "action": "Add an explicitly bound measured-series adapter; do not rename measured vibration as an analytic oscillator."}
    raise AssertionError("Unbound real-data execution unexpectedly succeeded")


def run(source_dir: Path, output: Path, work: Path, max_windows: int, rwth_response: Path | None = None,
        *, stfe_source_root: Path | None = None, stfe_revision: str | None = None,
        require_installed: bool = False) -> dict:
    from scipy import __version__ as scipy_version
    from scipy.io import loadmat
    from scipy.signal import periodogram
    kernel_path = Path(inspect.getfile(summarize_values)).resolve()
    if Path(inspect.getfile(periodogram_values)).resolve() != kernel_path:
        raise RuntimeError("Statistics and spectrum kernels resolve to different modules")
    site_packages = Path(sysconfig.get_path("purelib")).resolve()
    installed_binding = kernel_path.is_relative_to(site_packages)
    if require_installed and not installed_binding:
        raise RuntimeError(f"Expected installed ciw kernel under {site_packages}; resolved {kernel_path}")
    stfe_module, stfe_binding = load_stfe(stfe_source_root or ROOT / "instruments/measurement/signal-processing", stfe_revision)
    window_mean, ContractError = stfe_module.window_mean, stfe_module.ContractError
    create_run_directories(output, work)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    revision = stfe_binding["declared_revision"]
    report = {"schema": "net.academic-real-data-check.v1", "created_at_utc": created,
              "status": "PASS", "python": sys.version, "python_executable": sys.executable,
              "isolated_python": bool(sys.flags.isolated),
              "ciw_distribution_version": importlib.metadata.version("computational-instrumentation-workbench"),
              "ciw_kernel_binding": {"path": str(kernel_path), "sha256": sha256(kernel_path),
                                     "under_runtime_site_packages": installed_binding},
              "stfe_kernel_binding": stfe_binding,
              "harness": {"path": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__))},
              "numpy_version": np.__version__, "scipy_version": scipy_version,
              "sources": [], "checks": [], "source_rows_processed": 0,
              "scope": "Software processing and boundary checks on real published university measurements; not physical qualification.",
              "limitations": ["Source amplitude units and calibration uncertainty are unverified; no SI amplitude or accuracy claim.",
                  "STFE times represent offline replay sample ticks. Original event/receipt times are unavailable.",
                  "Unknown covariance remains unknown; no fabricated uncertainty or independence assumption.",
                  "Baseline sample rate remains unverified, so baseline Hz spectra are not calculated.",
                  "No classification training, health threshold selection, or predictive physical model validation."]}
    windows = []
    for name, source in SOURCES.items():
        path = source_dir / name
        actual = sha256(path)
        if actual != source["sha256"] or path.stat().st_size != source["bytes"]:
            raise ValueError(f"Source hash mismatch: {name}")
        mat = loadmat(path)
        entry = {"file": name, "loaded_path": str(path.resolve()), "sha256": actual, "bytes": path.stat().st_size,
                 "url": source["url"], "condition": source["condition"], "channels": []}
        report["sources"].append(entry)
        for channel, expected_count in source["channels"].items():
            values = np.asarray(mat[channel], dtype=np.float64).ravel()
            if len(values) != expected_count or not np.isfinite(values).all():
                raise ValueError("Unexpected source shape or nonfinite samples")
            native = summarize_values(values, UNIT)
            reference = {"mean": math.fsum(map(float, values))/len(values),
                         "rms": math.sqrt(math.fsum(float(v)*float(v) for v in values)/len(values)),
                         "minimum": float(np.min(values)), "maximum": float(np.max(values))}
            for key, value in reference.items():
                assert math.isclose(native[key], value, rel_tol=2e-13, abs_tol=1e-15), (channel, key)
            details = {"channel": channel, "sample_count": len(values), "statistics": native,
                       "independent_reference": reference, "statistics_status": "PASS"}
            entry["channels"].append(details)
            report["source_rows_processed"] += len(values)
            for start_value in window_starts(len(values), max_windows):
                start = int(start_value)
                selected = values[start:start+32]
                request = stfe_request(selected, channel=channel, start=start,
                    source_digest=actual, revision=revision, created=created)
                receipt = window_mean(request)
                artifact = receipt["result_artifact"]
                mean = artifact["components"][0]["value"]
                reference_mean = math.fsum(map(float, selected))/len(selected)
                assert math.isclose(mean, reference_mean, rel_tol=1e-13, abs_tol=1e-15)
                assert artifact["covariance"]["status"] == "unknown"
                assert artifact["covariance"]["matrix"] is None
                windows.append({"file": name, "channel": channel, "start_sample": start,
                    "end_sample_exclusive": start+len(selected), "mean": mean,
                    "reference_mean": reference_mean, "absolute_error": abs(mean-reference_mean),
                    "uncertainty_status": "unknown", "numerical_result_id": receipt["numerical_result_id"]})
                if start == 0:
                    replay = deepcopy(request)
                    replay["execution_id"] = "execution:" + uuid.uuid4().hex
                    repeated = window_mean(replay)
                    assert receipt["numerical_result_id"] == repeated["numerical_result_id"]
                    assert artifact["result_id"] != repeated["result_artifact"]["result_id"]
                    damaged = deepcopy(request)
                    damaged["samples"][1]["missing"] = True
                    try:
                        window_mean(damaged)
                    except ContractError as exc:
                        assert "missing" in str(exc)
                    else:
                        raise AssertionError("Missing observation was accepted")
                    # Kept in work because these receipts contain original amplitude samples.
                    write_new_json(work / f"{channel}-first-window.json",
                                   {"request": request, "receipt": receipt})
            if channel == "X105_DE_time":
                report["adapter_boundary"] = adapter_boundary(values, source)
                spectra = []
                for count in (31, 32, len(values)-1, len(values)):
                    selected = values[:count]
                    native_spectrum = periodogram_values(selected, UNIT, 12000.0)
                    frequency, psd = periodogram(selected, fs=12000., window="hann", detrend="constant", scaling="density")
                    np.testing.assert_allclose(native_spectrum["frequency_hz"], frequency, rtol=1e-14, atol=1e-13)
                    np.testing.assert_allclose(native_spectrum["psd"], psd, rtol=2e-10, atol=1e-16)
                    integrated = math.fsum(native_spectrum["psd"]) * 12000./count
                    window = 0.5-0.5*np.cos(2*np.pi*np.arange(count)/count)
                    weighted_power = float(np.sum(((selected-selected.mean())*window)**2)/np.sum(window**2))
                    assert math.isclose(integrated, weighted_power, rel_tol=1e-12, abs_tol=1e-15)
                    spectra.append({"start_sample": 0, "sample_count": count, "sample_rate_hz": 12000.,
                        "status": "PASS", "frequency_bins": len(frequency),
                        "peak_frequency_hz": native_spectrum["peak_frequency_hz"],
                        "maximum_absolute_psd_error": float(np.max(np.abs(np.asarray(native_spectrum["psd"])-psd))),
                        "integrated_psd": integrated, "independent_hann_weighted_power": weighted_power})
                details["native_periodogram_vs_scipy"] = spectra
    report["stfe"] = {"status": "PASS", "window_count": len(windows), "samples_per_window": 32,
        "max_absolute_error": max(w["absolute_error"] for w in windows),
        "window_selection": "At most requested count per channel, deterministic evenly spread nonoverlapping complete 32-sample windows; no model fitting.",
        "replay_identity_checks": len(report["sources"][0]["channels"])+len(report["sources"][1]["channels"]),
        "missing_observation_refusals": 5, "unknown_uncertainty_preserved": True}
    if rwth_response is not None:
        status = {"url": "https://publications.rwth-aachen.de/record/1016199/files/ExperimentalData.zip?version=1",
                  "bytes": rwth_response.stat().st_size, "sha256": sha256(rwth_response)}
        try:
            reject_nonarchive(rwth_response)
        except ValueError as exc:
            status.update(status="BLOCKED", reason=str(exc), numerical_test_performed=False)
        else:
            status.update(status="ARCHIVE_AVAILABLE_REQUIRES_SCHEMA_REVIEW", numerical_test_performed=False)
        report["rwth_acquisition"] = status
    with (output / "stfe-window-checks.csv").open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(windows[0]))
        writer.writeheader()
        writer.writerows(windows)
    write_new_json(output / "input-acquisition-manifest.json", acquisition_manifest(stfe_binding))
    write_new_json(output / "academic-checks.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--max-windows-per-channel", type=int, default=128)
    parser.add_argument("--rwth-response", type=Path)
    parser.add_argument("--stfe-source-root", type=Path, help="Explicit local STFE provider source; defaults to the bundled instrument tree")
    parser.add_argument("--stfe-revision", help="Recorded provider revision, required when its exported source tree lacks Git metadata")
    parser.add_argument("--require-installed", action="store_true", help="Refuse a ciw import outside this runtime's site-packages")
    args = parser.parse_args()
    report = run(args.source_dir, args.output_dir, args.work_dir, args.max_windows_per_channel, args.rwth_response,
                 stfe_source_root=args.stfe_source_root, stfe_revision=args.stfe_revision,
                 require_installed=args.require_installed)
    print(json.dumps({"status": report["status"], "source_rows_processed": report["source_rows_processed"],
                      "stfe": report["stfe"], "adapter_boundary": report["adapter_boundary"],
                      "rwth_acquisition": report.get("rwth_acquisition")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
