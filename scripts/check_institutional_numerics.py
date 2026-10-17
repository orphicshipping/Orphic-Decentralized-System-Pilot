"""Execute NET kernels against pinned NIST and Oxford original source files.

No synthetic measurement, fabricated device identity, oscillator wrapper,
calibration or physical qualification is used. SciPy is a research dependency.
The fixed tolerances are numerical software criteria, never sensor specifications.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys

NIST_HASHES = {
    "Lew.dat": "081b231f686d01fa00247b8072909206947c8321bd62e060d9aaae5f4866c44e",
    "NumAcc4.dat": "ca310dc767f5130f980f8280bbe69e26ba414a4d85a89fc11b6c767b828e5fe4",
}
OXFORD_HASHES = {
    "Temperature_data.mat": "2e2d650d0426ecb780cd3941884885dc4d55a389cd3e77adfae8f3e96b90d7df",
    "Voltage_current_data.mat": "5f83c6b4fb7e498e2abb9c508d3839ece049ff84a2895eea44742b2d9c1e44af",
    "Impedance_data.mat": "af666e6dd935a588f1bce979a9b900398ca29f28d3834f27ac34c90e416d1b51",
    "MainScript.m": "327f5392805b4313a515bde035485aa867908b65014df255878013c696cb231a",
    "LICENSE.txt": "e32c16b7c636bd65e0eb3963fb8571804bb69bd76bbc81ce2620d33fd7ce69a6",
    "README.md": "616b0eee35bcbeea71902151e16dad4c40fb729cbc8d57460e6435c815c50090",
}
COMMIT = "194919b94b45f66309475bc88aa8e8826eb603bb"
ATOL = 1e-12


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def pinned(path, expected):
    with path.open("rb") as stream:
        raw = stream.read(10_000_001)
    if len(raw) > 10_000_000 or digest(raw) != expected:
        raise ValueError("Original source byte mismatch or size violation: " + str(path))
    return raw


def decimal_summary(values):
    with localcontext() as context:
        context.prec = 70
        values = [Decimal.from_float(v) if isinstance(v, float) else Decimal(v) for v in values]
        n = Decimal(len(values))
        mean = sum(values) / n
        rms = (sum(v * v for v in values) / n).sqrt()
        ss = sum((v - mean) ** 2 for v in values)
        return {"mean": str(mean), "rms": str(rms),
                "minimum": str(min(values)), "maximum": str(max(values)),
                "sample_std": str((ss / (n - 1)).sqrt()),
                "lag1": str(sum((a - mean) * (b - mean) for a, b in zip(values[:-1], values[1:])) / ss)}


def numeric_error(actual, reference):
    # Do not round the independent high-precision oracle back to binary64 before
    # subtracting. One output ULP permits representational rounding at large scale.
    target = Decimal(reference)
    error = abs(Decimal.from_float(actual) - target)
    allowance = Decimal(str(ATOL)) + Decimal.from_float(math.ulp(float(target)))
    return {"absolute_error": str(error), "allowed_error": str(allowance),
            "absolute_tolerance": ATOL, "binary64_rounding_allowance_ulp": 1,
            "passed": error <= allowance}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nist-dir", type=Path, required=True)
    parser.add_argument("--thermal-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scipy-path", type=Path)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    if args.scipy_path:
        sys.path.insert(0, str(args.scipy_path.resolve()))
    import numpy as np
    import scipy
    from scipy.io import loadmat
    from scipy.signal import periodogram
    from ciw.adapters import oscillator
    from ciw.control_contracts import observation, record, bytes_ref
    from ciw.control_checks import compare, inspect_record, validate_comparison

    checks, sources, derived = [], [], []
    source_bytes = {}
    def check(name, passed, **details):
        checks.append({"id": name, "status": "PASS" if passed else "FAIL", **details})
    for base, hashes, provider in ((args.nist_dir, NIST_HASHES, "NIST"), (args.thermal_dir, OXFORD_HASHES, "Oxford author repository")):
        for filename, expected in hashes.items():
            path = base / filename
            raw = pinned(path, expected)
            source_bytes[filename] = raw
            sources.append({"path": str(path.resolve()), "file": filename, "sha256": digest(raw), "bytes": len(raw), "provider": provider})
            check("source_bytes:" + filename, True, expected_sha256=expected)

    nist_results = []
    for filename in NIST_HASHES:
        text = source_bytes[filename].decode("ascii")
        rows = text.splitlines()
        values = [Decimal(row.strip()) for row in rows[60:] if row.strip()]
        result = oscillator.summarize_values([float(v) for v in values], "source_unit_unspecified")
        independent = decimal_summary(values)
        certified = {"mean": rows[40].split(":")[-1].split()[0], "sample_std": rows[41].split(":")[-1].split()[0], "lag1": rows[42].split(":")[-1].split()[0]}
        errors = {key: numeric_error(result[key], independent[key]) for key in ("mean", "rms", "minimum", "maximum")}
        for key, error in errors.items():
            check(filename + ":NET_" + key, error["passed"], numerical_error=error,
                  layer="ciw.adapters.oscillator.summarize_values", real_measurement=(filename == "Lew.dat"))
        for key in certified:
            error = abs(Decimal(independent[key]) - Decimal(certified[key]))
            check(filename + ":Decimal_" + key + "_against_NIST", error <= Decimal(str(ATOL)), absolute_error=str(error), tolerance=str(ATOL),
                  layer="independent_Decimal_reference_not_NET_statistic", real_measurement=(filename == "Lew.dat"))
        nist_results.append({"source": filename, "origin": "observed_beam_deflections" if filename == "Lew.dat" else "constructed_numerical_stress_benchmark_not_real_measurements", "sample_count": len(values), "native_summary": result, "independent_decimal": independent, "certified": certified})

    matrices = loadmat(io.BytesIO(source_bytes["Temperature_data.mat"]))
    thermal_results = []
    ref = bytes_ref(source_bytes["Temperature_data.mat"])
    raw_source = ref
    for array_name in ("T_data_1", "T_data_2"):
        array = matrices[array_name]
        times = array[:, 0]
        spacing_error = float(np.max(np.abs(np.diff(times) - 1.1)))
        check(array_name + ":temperature_time_grid", bool(np.all(np.diff(times) > 0) and spacing_error <= 1e-9), max_spacing_error_s=spacing_error, assumed_acquisition_epoch=None)
        for column, quantity in ((1, "surface_temperature"), (2, "core_temperature"), (3, "coolant_temperature")):
            values = array[:, column]
            native = oscillator.summarize_values(values, "degC")
            independent = decimal_summary(values.tolist())
            errors = {key: numeric_error(native[key], independent[key]) for key in ("mean", "rms", "minimum", "maximum")}
            check(array_name + ":" + quantity + ":NET_summary", all(error["passed"] for error in errors.values()), numerical_errors=errors, samples=len(values))
            spectrum = oscillator.periodogram_values(values, "degC", 1 / 1.1)
            frequencies, psd = periodogram(values, fs=1 / 1.1, window="hann", detrend="constant", scaling="density")
            error = float(np.max(np.abs(np.array(spectrum["psd"]) - psd)))
            # Independent implementation and scale-aware numerical tolerance.
            psd_limit = ATOL + 1e-12 * float(np.max(np.abs(psd)))
            check(array_name + ":" + quantity + ":NET_periodogram", bool(np.array_equal(spectrum["frequency_hz"], frequencies) and error <= psd_limit), max_psd_error=error, allowed_error=psd_limit, reference="scipy.signal.periodogram")
            thermal_results.append({"array": array_name, "matlab_column_1_based": column + 1, "quantity": quantity, "samples": len(values), "native_summary": native, "independent_decimal_summary": independent, "psd_sha256": digest(json.dumps(spectrum, sort_keys=True).encode()), "psd_peak_hz": spectrum["peak_frequency_hz"]})

    # A bounded real-data subset exercises the native observation/CLI pipeline.
    # Choice is positional, frozen before looking at residuals, with no fitting.
    array = matrices["T_data_2"][:128]
    selection = {"source_sha256": ref, "array": "T_data_2", "source_rows_zero_based": [0, 128], "matlab_columns_1_based": {"time": 1, "surface": 2, "core": 3}, "selection_rule": "first_128_rows_for_bounded_transport_check", "conversion": "Kelvin = Celsius + 273.15", "uncertainty": None, "calibration": "not_established", "epoch": None}
    write_json(out / "oxford-selection.json", selection)
    selection_ref = bytes_ref((out / "oxford-selection.json").read_bytes())
    def observations(values, unit, quantity, provider):
        return [observation(identity={"model_id": "source-column-projection:no-estimator", "entity_id": "Oxford-published-cell:serial-unspecified", "execution_id": None},
            clock={"id": "Oxford:T_data_2:relative-seconds", "time_s": float(t)}, frame="Oxford-published-battery-temperature-channels",
            quantity=quantity, value=float(value), unit=unit, uncertainty=None,
            provenance={"provider": provider, "sources": [raw_source, selection_ref], "semantics": "observed"}) for t, value in zip(array[:, 0], values)]
    celsius = observations(array[:, 1], "degC", "surface_temperature", "author_MAT_column_2")
    kelvin = observations(array[:, 1] + 273.15, "K", "surface_temperature", "explicit_Celsius_to_Kelvin")
    decimal_kelvin = observations([float(Decimal(str(v)) + Decimal("273.15")) for v in array[:, 1]], "K", "surface_temperature", "independent_Decimal_conversion")
    core = observations(array[:, 2], "degC", "core_temperature", "author_MAT_column_3")
    for name, left, right, expected in (("kelvin_numeric_conversion", kelvin, decimal_kelvin, "PASS"), ("unit_mismatch_refusal", celsius, kelvin, "INDETERMINATE"), ("surface_core_quantity_refusal", celsius, core, "INDETERMINATE"), ("missing_timestamp_refusal", celsius, celsius[1:], "INDETERMINATE")):
        comparison = compare(left, right, atol=ATOL)
        validate_comparison(comparison)
        path = out / (name + ".json")
        write_json(path, comparison)
        check("native_comparison:" + name, comparison["outcome"]["status"] == expected, expected_outcome=expected, actual_outcome=comparison["outcome"], artifact=path.name)
    for name, rows in (("oxford-surface-kelvin", kelvin), ("oxford-surface-kelvin-decimal", decimal_kelvin)):
        stream = record("observation-stream", observations=rows)
        inspect_record(stream)
        write_json(out / (name + ".json"), stream)
    commands = [
        [sys.executable, "-m", "ciw.net", "inspect", str(out / "oxford-surface-kelvin.json"), "--json"],
        [sys.executable, "-m", "ciw.net", "compare", str(out / "oxford-surface-kelvin.json"), str(out / "oxford-surface-kelvin-decimal.json"), "--atol", str(ATOL), "--json"],
    ]
    for index, command in enumerate(commands):
        run = subprocess.run(command, capture_output=True, text=True, timeout=60)
        log = out / ("net-cli-" + str(index) + ".txt")
        log.write_text(run.stdout + run.stderr, encoding="utf-8")
        parsed = json.loads(run.stdout) if run.returncode == 0 else None
        summary = ({key: value for key, value in parsed.items() if key != "record"} if index == 0 else parsed.get("outcome")) if isinstance(parsed, dict) else None
        valid = (run.returncode == 0 and isinstance(parsed, dict) and
                 (parsed.get("integrity") == "checked" if index == 0 else parsed.get("outcome", {}).get("status") == "PASS"))
        check("NET_CLI:" + ("inspect" if index == 0 else "compare"), valid, command=command, returncode=run.returncode, log=log.name, output=summary)

    for path in sorted(out.glob("*")):
        if path.is_file() and path.name not in {"institutional-results.json", "README.txt"}:
            derived.append({"file": path.name, "sha256": digest(path.read_bytes()), "bytes": path.stat().st_size})
    report = {
        "schema": "net.institutional-numerical-tests.v1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS_WITH_UNTESTED_PHYSICAL_SCOPE" if all(c["status"] == "PASS" for c in checks) else "FAIL",
        "runtime": {"python": sys.version, "numpy": np.__version__, "scipy": scipy.__version__, "net_kernel": str(Path(oscillator.__file__).resolve()), "kernel_sha256": digest(Path(oscillator.__file__).read_bytes()), "harness_sha256": digest(Path(__file__).read_bytes())},
        "sources": sources, "checks": checks, "counts": {"passed": sum(c["status"] == "PASS" for c in checks), "failed": sum(c["status"] == "FAIL" for c in checks)},
        "nist": nist_results, "oxford": thermal_results, "derived_artifacts": derived,
        "source_links": ["https://www.itl.nist.gov/div898/strd/univ/data/Lew.dat", "https://www.itl.nist.gov/div898/strd/univ/certmethdef/lew.html", "https://www.nist.gov/itl/sed/products-services/statistical-reference-data-sets-strd", "https://ora.ox.ac.uk/objects/uuid%3Ae7f5b3d0-bda9-4e88-81ec-743767600288", "https://arxiv.org/abs/1501.06160", "https://github.com/robert-richardson/EKF-Battery-Impedance-Temperature/tree/" + COMMIT],
        "real_data_counts": {"NIST_Lew_observations": 200, "Oxford_temperature_rows": 8661, "Oxford_temperature_values": 25983, "native_transport_rows": 128},
        "scope": "NET source-neutral mean/minimum/maximum/RMS and periodogram kernels; native observation serialization, comparison validation, CLI inspect/compare. NumAcc4 is an additional constructed stress benchmark. NIST sample standard deviation and lag correlation are independent Decimal corpus checks because NET does not implement those statistics.",
        "blocked": ["Full measured-thermal estimator requires independently measured power, sensor calibration/application evidence, acquisition epoch and devices, justified NET model parameters and frozen acceptance criteria not supplied by Oxford source.", "No physical accuracy, sensor uncertainty coverage, metrological traceability, hardware execution or State Ledger admission is established.", "These helpers are direct numerical APIs; no generic real-data Workbench statistics adapter or end-to-end MCP instrument claim is made.", "Oxford cycle 2 inspected for software arithmetic only; no fit, model choice or physical acceptance threshold was selected from it."],
    }
    write_json(out / "institutional-results.json", report)
    print(json.dumps({"status": report["status"], "counts": report["counts"], "report": str(out / "institutional-results.json")}))
    return int(any(c["status"] == "FAIL" for c in checks))


if __name__ == "__main__":
    raise SystemExit(main())
