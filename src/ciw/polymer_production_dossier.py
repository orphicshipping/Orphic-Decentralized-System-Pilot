"""Retain a polymer production dossier using NET's existing Session occurrences.

No network transport, machine grant, PLC write, new admission path, or safety loop.
A hash binds content; it does not authenticate an operator or certify a part.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from hashlib import sha256
import math

from .control_contracts import load, save_new
from .core.identities import new_identity
from .operations.runner import check_seal, digest, seal
from . import polymer_workflow as workflow
from .polymer_production_dossier_contract import AUTHORITY, MAX_BYTES, validate_request

DOSSIER_SCHEMA = "ciw.polymer-microfab-dossier.v1"


def runtime_identity() -> dict:
    from . import polymer_production_dossier_contract
    modules = [Path(__file__), Path(polymer_production_dossier_contract.__file__)]
    raw = b"\0".join(p.name.encode() + b"\0" + p.read_text(encoding="utf-8").replace("\r\n", "\n").encode()
                     for p in modules)
    return {"provider": "ciw.polymer-microfab-projection.v1", "code_sha256": sha256(raw).hexdigest(),
            "source_normalization": "utf8_lf", "scope": "data_only_production_binding_and_residual_projection"}


def _residual(entry: dict, assessment: dict, policy: dict) -> dict:
    """Project validated inputs; public callers use run/inspect, not this helper.

    Compare held-out temperature with the retained forecast at the SAME time.

    Unknown correlation is not zero correlation. u_pred + u_obs is an upper bound
    on standard uncertainty for any admissible covariance, not validated coverage.
    Model discrepancy is not supplied and therefore cannot be statistically removed.
    """
    cooling = assessment["engineering"]["cooling"]
    followup = entry["followup"]
    common = {"schema": "ciw.polymer-prediction-residual.v1", "quantity": "part_temperature", "unit": "K",
              "model_ref": digest(entry["cycle"]["model"]), "prediction_ref": digest(cooling),
              "followup_ref": None if followup is None else digest(followup), "policy_ref": digest(policy),
              "frame": entry["cycle"]["frame"], "clock_id": entry["cycle"]["clock"]["id"],
              "target_time_s": entry["cycle"]["clock"]["end_s"] + entry["cycle"]["model"]["cooling"]["forecast_horizon_s"],
              "uncertainty_semantics": "sum_of_standard_uncertainties_unknown_correlation_upper_bound",
              "model_discrepancy": "not_quantified", "coverage_probability": "not_validated",
              "time_semantics": "exact_nominal_forecast_time_not_certified_clock_alignment"}
    if cooling["status"] != "ESTIMATED" or followup is None:
        return {**common, "status": "INDETERMINATE", "reason": "MODEL_ABSTAINED" if cooling["status"] != "ESTIMATED" else "MISSING_FOLLOWUP"}
    delta = followup["value"] - cooling["temperature_k"]
    uncertainty_bound = followup["standard_uncertainty"] + cooling["standard_uncertainty_k"]
    radius = policy["coverage_factor"] * uncertainty_bound + cooling["timing_temperature_bound_k"]
    low, high = delta - radius, delta + radius
    if not all(math.isfinite(x) for x in (delta, uncertainty_bound, radius, low, high)):
        raise ValueError("Residual arithmetic exceeds the finite numerical profile")
    limit = policy["absolute_tolerance_k"]
    status = ("CONFORMING" if -limit <= low and high <= limit else
              "NONCONFORMING" if high < -limit or low > limit else "INDETERMINATE")
    return {**common, "status": status, "residual_k": delta, "interval_k": [low, high],
            "standard_uncertainty_upper_bound_k": uncertainty_bound,
            "timing_temperature_bound_k": cooling["timing_temperature_bound_k"],
            "absolute_tolerance_k": limit,
            "reason": "DECLARED_RESIDUAL_INTERVAL_SCREEN_NOT_PHYSICAL_MODEL_QUALIFICATION"}


def _dossier(request, inspections):
    rows = []
    for index, (entry, inspection) in enumerate(zip(request["entries"], inspections, strict=True)):
        assessment = inspection["assessment"]
        comparison = (_residual(entry, assessment, request["residual_policy"]) if assessment is not None else
                      {"status": "INDETERMINATE", "reason": "ASSESSMENT_UNAVAILABLE"})
        rows.append({"serial_id": entry["serial_id"], "part_number": request["part"]["part_number"],
                     "part_revision": request["part"]["revision"], "work_order_id": request["order"]["work_order_id"],
                     "cell_id": request["cell"]["cell_id"], "cavity_id": entry["cavity_id"],
                     "identity": deepcopy(entry["cycle"]["identity"]), "source_kind": inspection["source_kind"],
                     "directory": f"part-{index:04d}", "cycle_request_ref": digest(entry["cycle"]),
                     "source_evidence_id": inspection["evidence_id"], "ingress_ref": inspection["ingress_ref"],
                     "inspection_ref": digest(inspection), "workflow_status": inspection["status"],
                     "occurrences": deepcopy(inspection["occurrences"]),
                     "part_conformity": "INDETERMINATE" if assessment is None else assessment["metrology"]["status"],
                     "metrology": None if assessment is None else deepcopy(assessment["metrology"]),
                     "residual": comparison,
                     "supervisory_proposal": None if assessment is None else deepcopy(assessment["control"]),
                     "proposal_execution": "NOT_AUTHORIZED"})
    count = len(rows)
    software = "PASS" if all(r["workflow_status"] == "PASS" for r in rows) else "REFUSE"
    decisions = [s for row in rows for s in (row["part_conformity"], row["residual"]["status"])]
    if software != "PASS":
        decision = "HOLD_WORKFLOW_REFUSAL"
    elif "NONCONFORMING" in decisions:
        decision = "HOLD_NONCONFORMING"
    elif count != request["order"]["quantity"]:
        decision = "HOLD_INCOMPLETE_ORDER"
    elif "INDETERMINATE" in decisions:
        decision = "HOLD_INDETERMINATE_EVIDENCE"
    else:
        decision = "READY_FOR_ENGINEERING_REVIEW"
    return seal({"schema": DOSSIER_SCHEMA, "request_ref": digest(request),
                 "projection_runtime": runtime_identity(),
                 "part_ref": digest(request["part"]), "order_ref": digest(request["order"]),
                 "cell_ref": digest(request["cell"]), "material_ref": digest(request["material"]),
                 "workflow_status": software, "engineering_review": decision,
                 "ordered_quantity": request["order"]["quantity"], "retained_quantity": count,
                 "source_kind": rows[0]["source_kind"], "parts": rows,
                 "physical_identity_semantics": "operator_declared_content_bound_not_authenticated",
                 "bom_route_scope": "versioned_references_only_not_expanded_or_executed",
                 "authority": deepcopy(AUTHORITY)})


def _regular(path: Path, budget: int):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > budget:
        raise ValueError("Require a bounded regular retained file, not a symlink")


def load_request(path: Path) -> dict:
    path = Path(path)
    _regular(path, MAX_BYTES)
    return validate_request(load(path))


def run(request: dict, destination: Path) -> dict:
    request = validate_request(request)  # Validate everything before any output or provider execution.
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    save_new(destination / "request.json", request)
    try:
        inspections = [workflow.run(entry["cycle"], destination / f"part-{i:04d}", ingress=entry["ingress"])
                       for i, entry in enumerate(request["entries"])]
        dossier = _dossier(request, inspections)
        save_new(destination / "dossier.json", dossier)
        return dossier
    except Exception as exc:
        # Preserve partial child attempts. Never present an interrupted batch as complete.
        save_new(destination / "failure.json", seal({"schema": "ciw.polymer-microfab-failure.v1",
                 "request_ref": digest(request), "status": "REFUSE", "error_type": type(exc).__name__,
                 "reason": "INCOMPLETE_RUN_PARTIAL_EVIDENCE_RETAINED", "authority": deepcopy(AUTHORITY)}))
        raise


def _read(destination: Path):
    destination = Path(destination)
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError("Require a regular production dossier directory")
    request = load_request(destination / "request.json")
    expected = {"request.json", "dossier.json"} | {f"part-{i:04d}" for i in range(len(request["entries"]))}
    if {p.name for p in destination.iterdir()} != expected:
        raise ValueError("Incomplete or unexpected production dossier inventory")
    inspections = []
    for i, entry in enumerate(request["entries"]):
        child = destination / f"part-{i:04d}"
        if child.is_symlink() or not child.is_dir():
            raise ValueError("Require a regular retained child workspace directory")
        retained = load_request_cycle(child / "request.json")
        if digest(retained) != digest(entry["cycle"]):
            raise ValueError("Retained child request differs from production genealogy")
        inspection = workflow.inspect(child)
        ingress_ref = None if entry["ingress"] is None else digest(entry["ingress"])
        if inspection["ingress_ref"] != ingress_ref:
            raise ValueError("Retained native ingress differs from production genealogy")
        inspections.append(inspection)
    _regular(destination / "dossier.json", 8 * 1024 * 1024)
    dossier = load(destination / "dossier.json")
    check_seal(dossier)
    if dossier.get("projection_runtime") != runtime_identity():
        raise ValueError("Dossier projection runtime differs; retain the original artifact and explicitly rerun its request into a new destination")
    if dossier != _dossier(request, inspections):
        raise ValueError("Dossier differs from its retained evidence, decisions or fixed authority")
    return request, dossier


def load_request_cycle(path):
    from .polymer_contract import MAX_BYTES as cycle_budget, validate_request as validate_cycle
    _regular(path, cycle_budget)
    return validate_cycle(load(path))


def inspect(destination: Path) -> dict:
    """Validate retained structure/bindings and projections; no numerical-provider replay."""
    return deepcopy(_read(destination)[1])


def verify_retained(destination: Path) -> dict:
    request, dossier = _read(destination)
    audits = [workflow.verify_retained(Path(destination) / f"part-{i:04d}") for i in range(len(request["entries"]))]
    passed = all(a.get("status") == "PASS" and a.get("fresh_numerical_verification") is True for a in audits)
    return seal({"schema": "ciw.polymer-microfab-verification.v1", "verification_id": new_identity("verification"),
                 "dossier_ref": digest(dossier), "request_ref": digest(request), "status": "PASS" if passed else "REFUSE",
                 "cycle_audits": audits, "residual_check": "deterministic_projection_recomputed_not_independent_oracle",
                 "fresh_numerical_verification": True, "physical_validation": "not_established",
                 "authority": deepcopy(AUTHORITY)})


def replay(destination: Path, output: Path) -> dict:
    request, _ = _read(destination)
    return run(request, output)
