"""Opt-in numerical comparisons; native NCP evidence remains the interchange.

These adapters neither discover/install providers nor bind an MCP execution
capability. Input declarations are not proof of physical calibration or timing.
All provider results are checked against independently evaluated exact-rational
reference arithmetic before a native covariance artifact is returned.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
from importlib import metadata
import json
import math
from pathlib import Path
import platform
import sys

from . import link_contract as c
from .core.covariance import create_covariance_artifact, validate_covariance_artifact

FIR_SCHEMA = "ciw.pydynamic-fir-request.v1"
COMPARISON_SCHEMA = "ciw.metrolopy-link-comparison-request.v1"
RESULT_SCHEMA = "ciw.uncertainty-provider-result.v1"
PINS = {"PyDynamic": "2.5.1", "metrolopy": "1.1.1", "numpy": "2.4.3", "scipy": "1.17.1"}
MAX_BYTES = 512 * 1024
MAX_SAMPLES = 64
MAX_TAPS = 16
CONSTRAINTS = {"kind": "unconstrained", "coordinate_basis": "declared_quantity_order"}
LIMITS = {
    "physical_qualification": "HELD",
    "calibration_authentication": "not_established",
    "timing_authentication": "not_established",
    "execution_authority": "not_granted_by_this_adapter",
    "hardware_io": "none",
}


class ProviderRefusal(ValueError):
    """A declaration, optional runtime, or numerical result is outside scope."""


def _constraints(value):
    if value != CONSTRAINTS:
        raise ProviderRefusal("Only unconstrained coordinates in declared quantity order are qualified")


def _number(value):
    c.finite(value)
    converted = float(value)
    if Fraction(converted) != Fraction(value):
        raise ProviderRefusal("Scalar input cannot be represented exactly as binary64")
    return converted


def _vector(value, minimum, maximum):
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ProviderRefusal(f"Require {minimum}..{maximum} scalar entries")
    return [_number(v) for v in value]


def _identity(provider):
    """Record the actual version and installed provider source bytes in this call.

    Release/archive/dependency identities are recorded by the provisioning and
    qualification scripts. This identity is not a signature or publisher claim.
    """
    names = (provider, "numpy", "scipy")
    packages = []
    for name in names:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError as exc:
            raise ProviderRefusal(f"Optional provider {name}=={PINS[name]} is not provisioned") from exc
        if dist.version != PINS[name]:
            raise ProviderRefusal(f"Require qualified pin {name}=={PINS[name]}, found {dist.version}")
        # Provider Python code and native numerical libraries are committed by
        # their installed RECORD; provider source is also hashed from disk.
        record = dist.read_text("RECORD")
        sources = []
        if name == provider:
            for file in sorted(dist.files or (), key=str):
                if str(file).endswith(".py"):
                    sources.append([str(file), sha256(Path(dist.locate_file(file)).read_bytes()).hexdigest()])
        packages.append({"name": name, "version": dist.version,
            "record_sha256": None if record is None else sha256(record.encode()).hexdigest(),
            "source_inventory_sha256": None if not sources else c.content_identity(sources)})
    return {"provider": provider, "packages": packages, "python": platform.python_version(),
        "adapter_sha256": sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "maintenance": "archived_upstream_since_2024_05" if provider == "PyDynamic" else "pinned_optional_provider"}


def _expected_number(value):
    result = float(value)
    if not math.isfinite(result) or (result == 0 and value != 0):
        raise ProviderRefusal("Reference result overflows or loses nonzero uncertainty to underflow")
    return result


def _check(actual, expected, label):
    """No absolute floor: tiny uncertainty and exact cancellation remain visible."""
    expected = _expected_number(expected)
    actual = float(actual)
    if not math.isfinite(actual):
        raise ProviderRefusal(f"Nonfinite provider result: {label}")
    tolerance = 0.0 if expected == 0 else 128 * math.ulp(expected)
    if abs(actual - expected) > tolerance:
        raise ProviderRefusal(f"Independent reference mismatch: {label}")
    return {"quantity": label, "actual": actual, "reference": expected,
        "absolute_tolerance": tolerance, "tolerance_basis": "128_binary64_reference_ulps_no_absolute_floor",
        "passed": True}


def _matrix_checks(actual, expected, prefix):
    if len(actual) != len(expected) or any(len(a) != len(b) for a, b in zip(actual, expected)):
        raise ProviderRefusal("Provider changed covariance dimensions")
    return [_check(actual[i][j], entry, f"{prefix}.{i}.{j}")
        for i, row in enumerate(expected) for j, entry in enumerate(row)]


def _transport_reference(matrix, jacobian):
    # This code never calls a provider or the existing Link numerical kernel.
    cov = [[Fraction(v) for v in row] for row in matrix]
    jac = [[Fraction(v) for v in row] for row in jacobian]
    size = len(cov)
    return [[sum((a[i] * cov[i][j] * b[j] for i in range(size)
                  for j in range(size)), Fraction(0)) for b in jac] for a in jac]


def fir_basis(request):
    """Joint covariance basis binds every declaration except uncertainty itself."""
    return c.content_identity({k: v for k, v in request.items() if k != "uncertainty"})


def validate_fir(request):
    c.bounded(request, MAX_BYTES)
    c.keys(request, {"schema", "input", "coefficients", "uncertainty", "sampling", "filter", "constraints"})
    if request["schema"] != FIR_SCHEMA:
        raise ProviderRefusal("Unsupported FIR request schema")
    _constraints(request["constraints"])
    source, coefficients = request["input"], request["coefficients"]
    c.keys(source, {"values", "quantity_ids", "unit", "frame", "source_ref"})
    c.keys(coefficients, {"values", "quantity_ids", "unit", "provenance_ref"})
    x = _vector(source["values"], 2, MAX_SAMPLES)
    h = _vector(coefficients["values"], 2, min(MAX_TAPS, len(x)))
    for vector in (source, coefficients):
        if type(vector["quantity_ids"]) is not list or len(vector["quantity_ids"]) != len(vector["values"]):
            raise ProviderRefusal("Quantity order must match the vector")
        for identifier in vector["quantity_ids"]:
            c.text(identifier)
    ids = source["quantity_ids"] + coefficients["quantity_ids"]
    if len(set(ids)) != len(ids):
        raise ProviderRefusal("Input and coefficient quantity IDs must be jointly unique")
    c.text(source["unit"]); c.text(source["frame"])
    if coefficients["unit"] != "1":
        raise ProviderRefusal("This FIR profile requires dimensionless coefficients")
    c.content_ref(source["source_ref"]); c.content_ref(coefficients["provenance_ref"])
    timing = request["sampling"]
    c.keys(timing, {"kind", "timestamp_event", "clock_id", "boot_id", "ticks", "tick_hz",
                    "uncertainty_state", "evidence_ref"})
    if (timing["kind"] != "uniform_acquisition" or timing["timestamp_event"] != "sensor_acquisition"
            or timing["uncertainty_state"] != "declared_exact"):
        raise ProviderRefusal("Require acquisition sampling with explicitly exact timing; receipt ticks are ineligible")
    c.text(timing["clock_id"]); c.text(timing["boot_id"]); c.content_ref(timing["evidence_ref"])
    c.integer(timing["tick_hz"], minimum=1)
    ticks = timing["ticks"]
    if type(ticks) is not list or len(ticks) != len(x):
        raise ProviderRefusal("Acquisition ticks must cover every input sample")
    for tick in ticks:
        c.integer(tick)
    period = ticks[1] - ticks[0]
    if period <= 0 or any(b-a != period for a, b in zip(ticks, ticks[1:])):
        raise ProviderRefusal("Acquisition ticks must increase at an exact uniform interval")
    filt = request["filter"]
    c.keys(filt, {"mode", "initial_conditions"})
    if filt["mode"] not in ("valid", "full") or filt["initial_conditions"] != "zero":
        raise ProviderRefusal("Qualified FIR boundary is valid/full with exact zero initial and padded conditions")
    uncertainty = request["uncertainty"]
    c.keys(uncertainty, {"state", "joint_covariance", "declaration_ref"})
    c.content_ref(uncertainty["declaration_ref"])
    state = uncertainty["state"]
    if state == "unknown":
        raise ProviderRefusal("Unknown uncertainty cannot become PyDynamic's fully certain None input")
    size = len(x) + len(h)
    if state == "declared_exact":
        if uncertainty["joint_covariance"] is not None:
            raise ProviderRefusal("Exact declaration cannot conceal a supplied covariance")
        matrix = [[0.0] * size for _ in range(size)]
    elif state == "covariance":
        cov = uncertainty["joint_covariance"]
        validate_covariance_artifact(cov, expected_quantity_ids=ids,
            expected_units=[source["unit"]]*len(x) + ["1"]*len(h), expected_frame=source["frame"])
        if (cov["reference_values"] != source["values"] + coefficients["values"]
                or cov["basis"] != {"kind": "parameter", "id": fir_basis(request)}
                or cov["provenance"]["source_evidence_ids"] != list(dict.fromkeys(
                    [source["source_ref"], coefficients["provenance_ref"], timing["evidence_ref"]]))):
            raise ProviderRefusal("Covariance must bind the exact source, coefficient, timing and reference declarations")
        matrix = cov["matrix"]
        if any(matrix[i][j] != 0 or matrix[j][i] != 0
                for i in range(len(x)) for j in range(len(x), size)):
            raise ProviderRefusal("PyDynamic convolution cannot represent input/coefficient cross-correlation")
        # Zero cross-covariance alone does not imply statistical independence.
        # The caller must retain this assumption to use second-order moments.
        if "input_and_coefficients_independent" not in cov["assumptions"]:
            raise ProviderRefusal("Declare statistical input/coefficient independence, not just zero cross-covariance")
    else:
        raise ProviderRefusal("Explicit exact, covariance, or unknown uncertainty state is required")
    for row in matrix:
        for value in row:
            _number(value)
    return deepcopy(request), x, h, matrix


def _convolution_reference(x, h, matrix, mode):
    """Exact moments for independent input/coefficient vectors, full inner covariance."""
    n, m = len(x), len(h)
    x, h = list(map(Fraction, x)), list(map(Fraction, h))
    cov = [[Fraction(v) for v in row] for row in matrix]
    indices = range(n+m-1) if mode == "full" else range(m-1, n)
    pairs = [[(k-j, j) for j in range(m) if 0 <= k-j < n] for k in indices]
    values = [sum((x[i]*h[j] for i, j in row), Fraction(0)) for row in pairs]
    output = [[sum((h[j]*h[q]*cov[i][p] + x[i]*x[p]*cov[n+j][n+q]
                    + cov[i][p]*cov[n+j][n+q]
                    for i, j in row for p, q in column), Fraction(0))
                    for column in pairs] for row in pairs]
    return values, output


def pydynamic_fir(request):
    request, x, h, matrix = validate_fir(request)
    identity = _identity("PyDynamic")
    import numpy as np
    # Official API: https://pydynamic.readthedocs.io/en/latest/PyDynamic.uncertainty.html#PyDynamic.uncertainty.propagate_convolution.convolve_unc
    from PyDynamic.uncertainty.propagate_convolution import convolve_unc
    n = len(x)
    # Always pass two-dimensional covariance. Only an explicit declaration of
    # exactness creates zero covariance; an absent/unknown budget never does.
    ux = np.asarray([row[:n] for row in matrix[:n]], dtype=float)
    uh = np.asarray([row[n:] for row in matrix[n:]], dtype=float)
    values, covariance = convolve_unc(np.asarray(x), ux, np.asarray(h), uh, mode=request["filter"]["mode"])
    values, covariance = values.tolist(), covariance.tolist()
    expected, expected_cov = _convolution_reference(x, h, matrix, request["filter"]["mode"])
    if len(values) != len(expected):
        raise ProviderRefusal("Provider changed output dimensions")
    checks = [_check(a, b, f"output.{i}") for i, (a, b) in enumerate(zip(values, expected))]
    checks += _matrix_checks(covariance, expected_cov, "covariance")
    ref = c.content_identity(request)
    input_cov = request["uncertainty"]["joint_covariance"]
    artifact = create_covariance_artifact(matrix=covariance, quantity_ids=[f"{ref}:fir:{i}" for i in range(len(values))],
        units=[request["input"]["unit"]]*len(values), frame=request["input"]["frame"], reference_values=values,
        method="independent_vector_bilinear_convolution_exact_second_moments",
        basis={"kind": "estimated_state", "id": ref},
        provenance={"provider": "ciw.uncertainty_providers.PyDynamic", "source_evidence_ids": [ref],
            "source_covariance_ids": [] if input_cov is None else [input_cov["covariance_id"]],
            "metadata": {"runtime": identity}},
        assumptions=["input_and_coefficients_independent", "exact_uniform_acquisition_timebase_declared_not_authenticated",
            "exact_zero_boundary_conditions", "timing_and_model_discrepancy_uncertainty_not_evaluated"])
    return {"schema": RESULT_SCHEMA, "operation": "pydynamic.fir.v1", "request_ref": ref,
        "provider": identity, "values": values, "covariance": artifact,
        "sampling": deepcopy(request["sampling"]), "filter": deepcopy(request["filter"]),
        "output_sample_index_start": 0 if request["filter"]["mode"] == "full" else len(h)-1,
        "coefficient_provenance_ref": request["coefficients"]["provenance_ref"],
        "uncertainty_state": request["uncertainty"]["state"], "checks": checks,
        "numerical_status": "PASS", "limits": dict(LIMITS)}


def compare_link_affine(request):
    """Unit-aware MetroloPy comparison of the existing Link affine/mean model."""
    from . import link_study as study
    c.bounded(request, MAX_BYTES)
    c.keys(request, {"schema", "study", "uncertainty_state", "constraints"})
    if request["schema"] != COMPARISON_SCHEMA:
        raise ProviderRefusal("Unsupported MetroloPy comparison schema")
    _constraints(request["constraints"])
    req = study.validate_request(request["study"])
    if request["uncertainty_state"] != "covariance" or req["calibration"]["joint_covariance"] is None:
        raise ProviderRefusal("Comparison requires explicit native joint covariance; unknown is not zero")
    decoded = study.decode(req)
    # The existing owner validates all source, coefficient and covariance
    # bindings. It remains the primary model; this provider is a cross-check.
    baseline = study.affine(req, decoded)
    identity = _identity("metrolopy")
    import metrolopy as mp
    cov = req["calibration"]["joint_covariance"]
    for value in cov["reference_values"]:
        _number(value)
    for row in cov["matrix"]:
        for value in row:
            _number(value)
    # Official API: https://nrc-cnrc.github.io/MetroloPy/_build/html/hand_made_doc.html#basic-gummy-methods
    try:
        quantities = mp.gummy.create(cov["reference_values"], unit=cov["units"], covariance_matrix=cov["matrix"])
        corrected = [quantities[-2]*value + quantities[-1] for value in quantities[:-2]]
        # A dimensionless zero start would discard the unit contract.
        mean = sum(corrected[1:], corrected[0]) / len(corrected)
        values = [float(value.x) for value in corrected]
        output_cov = [[float(v) for v in row] for row in mp.gummy.covariance_matrix(corrected)]
        mean_value, mean_variance = float(mean.x), float(mean.u)**2
    except Exception as exc:
        raise ProviderRefusal(f"MetroloPy failed within the declared numerical domain: {type(exc).__name__}: {exc}") from exc
    n = len(corrected)
    x = [Fraction(row["value"]) for row in decoded["rows"]]
    a, b = Fraction(req["calibration"]["gain"]), Fraction(req["calibration"]["offset"])
    expected = [a*v+b for v in x]
    jacobian = [[a if i == j else Fraction(0) for j in range(n)] + [x[i], Fraction(1)] for i in range(n)]
    expected_cov = _transport_reference(cov["matrix"], jacobian)
    # MetroloPy propagates first-order derivatives through the full expression,
    # including the mean, without a floating-point covariance round-trip.
    expected_mean = sum(expected, Fraction(0))/n
    expected_mean_variance = sum((v for row in expected_cov for v in row), Fraction(0))/(n*n)
    checks = [_check(v, e, f"affine.{i}") for i, (v, e) in enumerate(zip(values, expected))]
    checks += _matrix_checks(output_cov, expected_cov, "covariance")
    checks += [_check(mean_value, expected_mean, "mean"), _check(mean_variance, expected_mean_variance, "mean_variance")]
    ref = c.content_identity(request)
    provenance = {"provider": "ciw.uncertainty_providers.MetroloPy", "source_evidence_ids": [ref, decoded["capture_ref"]],
        "source_covariance_ids": [cov["covariance_id"]], "metadata": {"runtime": identity}}
    assumptions = ["conditional_first_order_joint_affine_jacobian", "unconstrained_declared_quantity_coordinates",
        "physical_calibration_and_timing_not_qualified"]
    artifact = create_covariance_artifact(matrix=output_cov, quantity_ids=baseline["quantity_ids"],
        units=[baseline["unit"]]*n, frame=decoded["channel"]["frame"], reference_values=values,
        method="metrolopy_first_order_affine_comparison", basis={"kind": "calibrated_observation", "id": study.calibration_basis(req)},
        provenance=provenance, assumptions=assumptions)
    mean_cov = create_covariance_artifact(matrix=[[mean_variance]], quantity_ids=[ref+":mean"],
        units=[baseline["unit"]], frame=decoded["channel"]["frame"], reference_values=[mean_value],
        method="metrolopy_first_order_mean_comparison", basis={"kind": "estimated_state", "id": ref},
        provenance=provenance, assumptions=assumptions)
    return {"schema": RESULT_SCHEMA, "operation": "metrolopy.link-affine-mean.v1", "request_ref": ref,
        "provider": identity, "values": values, "covariance": artifact, "mean": mean_value,
        "mean_covariance": mean_cov, "native_baseline_covariance_ref": baseline["covariance"]["covariance_id"],
        "applicability": baseline["applicability"], "checks": checks, "numerical_status": "PASS", "limits": dict(LIMITS)}


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("pydynamic-fir", "metrolopy-link"))
    parser.add_argument("request", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new evidence path")
    with args.request.open("rb") as handle:
        raw = handle.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        parser.error("Request exceeds byte bound")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    request = json.loads(raw, object_pairs_hook=pairs)
    operation = pydynamic_fir if args.operation == "pydynamic-fir" else compare_link_affine
    result = operation(request)
    result["input_bytes_ref"] = c.bytes_ref(raw)
    # Native JSON only; no provider objects, deserialization hooks or updater.
    with args.output.open("xb") as handle:
        handle.write((json.dumps(result, sort_keys=True, indent=2, allow_nan=False)+"\n").encode())


def main():
    try:
        _main()
        return 0
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RuntimeError) as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc), "physical_qualification": "HELD"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
