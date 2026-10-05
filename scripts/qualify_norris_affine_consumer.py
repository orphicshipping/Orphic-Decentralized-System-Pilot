"""Evaluate NIST Norris using the existing native sensitivity affine-map API.

This is evaluation of source-certified coefficients, never regression fitting,
calibration, uncertainty qualification, clock construction, or physical truth.
Physical x/y units are absent in the retained source and remain unspecified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys
import tempfile

from ciw.adapters.subprocess import _bounded_process, _json

NORRIS_SHA256 = "cc3fd14d1c5fa891d5653000c9d7732c30db842cca49fc051abde1c19d67ab7d"
SOURCE_URL = "https://www.itl.nist.gov/div898/strd/lls/data/LINKS/DATA/Norris.dat"
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"
POLICY = {"scope": "evaluate_source_certified_affine_coefficients_and_analytical_derivative",
    "coefficient_fitting": "not_performed", "physical_units": "not_declared_in_retained_source",
    "time_or_clock": "not_required_not_invented", "calibration": "not_established",
    "uncertainty": "no_joint_covariance_supplied_not_invented", "physical_qualification": "not_established",
    "decimal_precision": 80, "prediction_atol": 1e-12, "prediction_rtol": 2e-15,
    "derivative_atol": 1e-15, "rss_atol": 1e-10, "residual_sd_atol": 1e-12}
_WORKER = r'''
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
# Load dependencies before making the copied, package-only tree importable.
# The operator's original source root (and its sibling modules) is never added.
import numpy as np
sys.path.insert(0, str(root))
import sensitivity
from sensitivity.reference_models import affine_map
from sensitivity.models import DifferentiableModel
for name, module in tuple(sys.modules.items()):
    if name == 'sensitivity' or name.startswith('sensitivity.'):
        if not pathlib.Path(module.__file__).resolve().is_relative_to(root):
            raise ValueError('Native sensitivity module left explicit source root')
source = json.load(sys.stdin)
model = affine_map([[float(source['b1'])]], [float(source['b0'])], name='NIST-Norris-certified-line')
rows = []
for row in source['rows']:
    x = [float(row['x'])]
    rows.append({'prediction': float(model.evaluate(x)[0]),
                 'derivative': float(model.analytical_jacobian(x)[0, 0])})
print(json.dumps({'rows': rows, 'numpy': np.__version__,
    'model_class': type(model).__module__ + '.' + type(model).__name__,
    'model_name': model.name}, allow_nan=False))
'''


def parse_norris(raw: bytes) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= 16384:
        raise ValueError("Norris source exceeds bounded byte profile")
    try:
        text = raw.decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Norris must retain ASCII data") from exc
    if "Dataset Name:  Norris (Norris.dat)" not in text or "y = B0 + B1*x + e" not in text:
        raise ValueError("Require the declared Norris affine model")
    coefficients = {}
    for name in ("B0", "B1"):
        found = re.findall(r"^\s*" + name + r"\s+(" + NUMBER + r")\s+(" + NUMBER + r")\s*$", text, re.M)
        if len(found) != 1:
            raise ValueError("Require one certified coefficient and standard-error declaration")
        coefficients[name.lower()] = found[0][0]
        coefficients[name.lower() + "_standard_error"] = found[0][1]
    residual = re.findall(r"^Residual\s+(\d+)\s+(" + NUMBER + r")\s+(" + NUMBER + r")\s*$", text, re.M)
    deviation = re.findall(r"^\s*Standard Deviation\s+(" + NUMBER + r")\s*$", text, re.M)
    if len(residual) != 1 or len(deviation) != 1 or residual[0][0] != "34":
        raise ValueError("Require original certified residual statistics")
    marker = re.findall(r"^Data:\s+y\s+x\s*$", text, re.M)
    if len(marker) != 1:
        raise ValueError("Require source y,x column declaration")
    tail = text.split(marker[0], 1)[1]
    rows = []
    for line in tail.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"\s*(" + NUMBER + r")\s+(" + NUMBER + r")\s*", line)
        if match is None:
            raise ValueError("Norris row must preserve exactly y and x")
        rows.append({"y": match[1], "x": match[2]})
    if len(rows) != 36:
        raise ValueError("Require all 36 Norris rows in original order")
    for number in [*coefficients.values(), residual[0][1], residual[0][2], deviation[0],
                   *(row[key] for row in rows for key in ("x", "y"))]:
        if not Decimal(number).is_finite() or abs(Decimal(number)) > Decimal("1e8"):
            raise ValueError("Norris number exceeds reviewed finite profile")
    return {**coefficients, "rows": rows, "degrees_of_freedom": 34,
            "certified_rss": residual[0][1], "certified_residual_variance": residual[0][2],
            "certified_residual_sd": deviation[0], "source_columns": ["y", "x"],
            "units": {"x": "not_declared_in_retained_source", "y": "not_declared_in_retained_source"},
            "evidence_class": "observed_data_with_certified_regression_statistics"}


def read_source(path: Path) -> tuple[bytes, dict]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Require the original regular Norris source")
    with path.open("rb") as stream:
        raw = stream.read(16385)
    if sha256(raw).hexdigest() != NORRIS_SHA256:
        raise ValueError("Norris SHA256 differs from reviewed acquisition")
    return raw, parse_norris(raw)


class AffineRuntime:
    """Explicit local source binding; retained data never chooses Python modules."""
    def __init__(self, source_root: Path):
        self.root = source_root.resolve()
        self.hashes = self._hashes()

    def _hashes(self):
        package = self.root / "sensitivity"
        if not (package / "reference_models.py").is_file() or not (package / "models.py").is_file():
            raise ValueError("Missing existing native sensitivity source")
        result = {}
        for path in sorted(package.rglob("*.py")):
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise ValueError("Native source leaves explicit binding")
            result[str(path.relative_to(self.root))] = sha256(path.read_bytes()).hexdigest()
        return result

    def identity(self):
        if self._hashes() != self.hashes:
            raise ValueError("Native sensitivity source changed after binding")
        return {"provider": "sensitivity.reference_models.affine_map",
                "evaluation": "sensitivity.models.DifferentiableModel.evaluate",
                "derivative": "sensitivity.models.DifferentiableModel.analytical_jacobian",
                "source_files_sha256": self.hashes,
                "binding": "explicit_local_source_bytes_copied_without_bytecode_or_siblings"}

    def execute(self, source: dict) -> dict:
        self.identity()
        # -B suppresses bytecode writes, not reads. Import only a fresh snapshot
        # of the exact hashed source bytes, with no pre-existing __pycache__.
        with tempfile.TemporaryDirectory(prefix="ciw-norris-source-") as temporary:
            isolated = Path(temporary)
            for relative, expected in self.hashes.items():
                path = self.root / relative
                if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                    raise ValueError("Native source leaves explicit binding")
                raw_source = path.read_bytes()
                if sha256(raw_source).hexdigest() != expected:
                    raise ValueError("Native sensitivity source changed during snapshot")
                destination = isolated / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw_source)
            self.identity()
            code, raw = _bounded_process([sys.executable, "-I", "-B", "-c", _WORKER, str(isolated)],
                cwd=isolated, timeout=30, limit=1048576, stdin=json.dumps(source, allow_nan=False).encode())
        if code:
            raise ValueError("Existing native affine consumer refused")
        result = _json(raw)
        self.identity()
        if (set(result) != {"rows", "numpy", "model_class", "model_name"}
                or result["model_class"] != "sensitivity.models.DifferentiableModel"
                or result["model_name"] != "NIST-Norris-certified-line"
                or len(result["rows"]) != len(source["rows"])):
            raise ValueError("Unexpected native affine output")
        return result


def check_reference(source, native):
    checks = []
    with localcontext() as context:
        context.prec = POLICY["decimal_precision"]
        b0, b1 = Decimal(source["b0"]), Decimal(source["b1"])
        decimal_rss = Decimal(0)
        residuals = []
        for index, (row, output) in enumerate(zip(source["rows"], native["rows"], strict=True)):
            if set(output) != {"prediction", "derivative"} or any(type(v) not in (int, float) or not math.isfinite(v) for v in output.values()):
                raise ValueError("Native affine output requires finite numerical predictions/derivatives")
            x, y = Decimal(row["x"]), Decimal(row["y"])
            expected = b0 + b1 * x
            error = abs(Decimal.from_float(float(output["prediction"])) - expected)
            tolerance = Decimal(str(POLICY["prediction_atol"])) + Decimal(str(POLICY["prediction_rtol"])) * abs(expected)
            derivative_error = abs(Decimal.from_float(float(output["derivative"])) - b1)
            if error > tolerance or derivative_error > Decimal(str(POLICY["derivative_atol"])):
                raise ValueError("Native affine evaluation differs from independent decimal reference")
            decimal_rss += (y - expected) ** 2
            residuals.append(float(row["y"]) - output["prediction"])
            checks.append({"row_index": index, **row, **output, "decimal_prediction": str(expected),
                "decimal_derivative": str(b1), "absolute_prediction_error": float(error),
                "absolute_derivative_error": float(derivative_error)})
        rss = math.fsum(value * value for value in residuals)
        sd = math.sqrt(rss / source["degrees_of_freedom"])
        if (abs(Decimal.from_float(rss) - Decimal(source["certified_rss"])) > Decimal(str(POLICY["rss_atol"]))
                or abs(decimal_rss - Decimal(source["certified_rss"])) > Decimal(str(POLICY["rss_atol"]))
                or abs(sd - float(source["certified_residual_sd"])) > POLICY["residual_sd_atol"]):
            raise ValueError("Evaluated residual statistics differ from NIST certified values")
    return {"status": "PASS", "rows": checks, "evaluated_rss": rss, "decimal_evaluated_rss": str(decimal_rss),
        "evaluated_residual_sd": sd, "certified_rss": source["certified_rss"],
        "certified_residual_sd": source["certified_residual_sd"],
        "max_prediction_absolute_error": max(row["absolute_prediction_error"] for row in checks),
        "max_derivative_absolute_error": max(row["absolute_derivative_error"] for row in checks)}


def qualify(source_path: Path, source_root: Path, destination: Path) -> dict:
    raw, source = read_source(source_path)
    runtime = AffineRuntime(source_root)
    identity = runtime.identity()
    result = runtime.execute(source)
    checked = check_reference(source, result)
    replay = runtime.execute(source)
    if result != replay or runtime.identity() != identity:
        raise ValueError("Native affine fresh-process replay changed")
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "Norris.dat").write_bytes(raw)
    report = {"schema": "ciw.academic-norris-affine-qualification.v1", "status": "PASS",
        "recorded_at": datetime.now(timezone.utc).isoformat(), "source_sha256": NORRIS_SHA256,
        "source_url": SOURCE_URL, "source_bytes": len(raw), "policy": POLICY, "source": source,
        "runtime": {**identity, "numpy": result["numpy"]}, "comparison": checked,
        "native_process_executions": 2, "native_evaluations": 72, "native_analytical_derivatives": 72,
        "fresh_process_replay": "exact_match", "mapping_script_sha256": sha256(Path(__file__).read_bytes()).hexdigest()}
    (destination / "qualification.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--norris", type=Path, required=True)
    parser.add_argument("--sensitivity-source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = qualify(args.norris, args.sensitivity_source_root, args.output_dir)
    print(json.dumps({"status": report["status"], "source_rows": len(report["source"]["rows"]),
        "native_evaluations": report["native_evaluations"], "native_derivatives": report["native_analytical_derivatives"],
        "max_prediction_absolute_error": report["comparison"]["max_prediction_absolute_error"],
        "evaluated_rss": report["comparison"]["evaluated_rss"]}))


if __name__ == "__main__":
    main()
