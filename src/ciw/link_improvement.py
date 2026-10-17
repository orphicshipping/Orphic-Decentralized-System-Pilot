"""Evidence-bound GaugeLink improvement proposals within the existing NET Session.

This is the Understand -> Operationalize -> Augment feedback calculation, not
another physical-state owner, fleet learner, calibration model or controller.
Counterfactual error scales are declarations, not measured hardware performance.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext
from hashlib import sha256
import math
from pathlib import Path
from threading import Lock

from . import link_contract as c, link_study as study, link_workflow
from .adapters.protocol import InstrumentManifest
from .control_plane import Port, experiment
from .operations.registry import Operation
from .operations.schemas import register_payload_validator, register_dependency_fields

PLAN_SCHEMA = "ciw.link-improvement-plan.v1"
OPS = ("link.review-improvement.v1", "link.audit-improvement.v1")
BLADES = ("Augment", "Measure", "Translate", "Understand", "Operationalize")
MAX_PLAN = 32768
MAX_CANDIDATES = 8
_REGISTERED = False
_LOCK = Lock()
LIMITS = {**c.LIMITS, "deployment": "not_performed", "network_sharing": "not_performed",
          "physical_improvement": "not_established"}


def validate_plan(plan, *, source, model, audit):
    c.bounded(plan, MAX_PLAN)
    c.keys(plan, {"schema", "instance_id", "bundle_id", "source_evidence_id", "model_result_id",
                  "audit_result_id", "objective", "candidates"})
    if plan["schema"] != PLAN_SCHEMA:
        raise ValueError("Unsupported improvement plan")
    c.text(plan["instance_id"]); c.content_ref(plan["bundle_id"])
    if (plan["source_evidence_id"] != source["evidence_id"] or
            plan["model_result_id"] != model["result_id"] or plan["audit_result_id"] != audit["result_id"]):
        raise ValueError("Plan must bind the exact original source/model/audit occurrences")
    request = study.source_request(source)
    decoded = study.decode(request)
    # Existing original result contracts remain authoritative.
    if (model["operation_id"] != study.OPS[2] or audit["operation_id"] != study.OPS[3]
            or model["evidence_id"] != source["evidence_id"] or audit["evidence_id"] != source["evidence_id"]
            or not study._equal(audit["parameters"]["candidate"], model)
            or not study._equal(model["data"], study.mean_model(request, model["parameters"]["calibrated"]["data"]))
            or not study._equal(audit["data"], study.audit(request, model)) or audit["data"]["status"] != "PASS"):
        raise ValueError("Require the matching, arithmetically audited retained scalar model")
    objective = plan["objective"]
    c.keys(objective, {"quantity", "unit", "max_standard_uncertainty"})
    if objective["quantity"] != "corrected_mean" or objective["unit"] != model["data"]["unit"]:
        raise ValueError("Improvement objective must use the original corrected-mean unit")
    if not 0 < c.finite(objective["max_standard_uncertainty"]):
        raise ValueError("Declare a positive standard-uncertainty objective, not an acceptance permit")
    candidates = plan["candidates"]
    if type(candidates) is not list or not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise ValueError("Require 1..8 bounded covariance counterfactuals")
    seen = set()
    for candidate in candidates:
        c.keys(candidate, {"id", "label", "kind", "scales", "rationale"})
        for key in ("id", "label", "rationale"):
            c.text(candidate[key], maximum=256)
        if candidate["id"] in seen:
            raise ValueError("Duplicate candidate identity")
        seen.add(candidate["id"])
        if candidate["kind"] != "covariance_scale_counterfactual":
            raise ValueError("Only a declared covariance counterfactual is supported; no device commands")
        scales = candidate["scales"]
        if type(scales) is not list or len(scales) != len(decoded["rows"]) + 2:
            raise ValueError("Scales must follow [every reading in retained order, gain, offset]")
        if any(not 0 <= c.finite(value) <= 1 for value in scales) or all(value == 1 for value in scales):
            raise ValueError("Scales must be in [0,1] with at least one declared reduction")
    return deepcopy(plan)


def _number(value):
    result = float(value)
    if not math.isfinite(result) or result == 0 and value != 0:
        raise ValueError("Counterfactual calculation cannot represent a finite nonzero result")
    return result


def _d(value):
    return Decimal(value) if type(value) is int else Decimal.from_float(value)


def _numeric(request, model, plan):
    """Decimal primary implementation; retain all cross terms and cancellation.

    D Sigma D is a declared change in input-error amplitudes at fixed means and
    correlation structure. It is not conditioning, adding a sensor or fitting a
    covariance from data. Native measured/corrected records are never modified.
    """
    decoded = study.decode(request)
    covariance = request["calibration"]["joint_covariance"]
    if covariance is None:
        return {"status": "UNCERTAINTY_UNKNOWN", "input_covariance_id": None,
                "retained_model_variance": model["data"]["variance"], "linearized_variance": None,
                "rounding_difference": None, "sensitivities": [], "cross_terms": [], "candidates": [],
                "ranking": [], "proposed_candidate_id": None, "objective_met": None}
    with localcontext() as ctx:
        ctx.prec = 4096
        values = [_d(row["value"]) for row in decoded["rows"]]
        n = len(values)
        numerators = [_d(request["calibration"]["gain"])] * n + [sum(values), Decimal(n)]
        gradient = [v / n for v in numerators]
        matrix = [[_d(v) for v in row] for row in covariance["matrix"]]
        dim = len(gradient)
        diagonal_numerators = [numerators[i]**2 * matrix[i][i] for i in range(dim)]
        diagonal = [v / (n*n) for v in diagonal_numerators]
        term_numerators = [(i, j, numerators[i] * (matrix[i][j] + matrix[j][i]) * numerators[j])
                 for i in range(dim) for j in range(i + 1, dim)]
        terms = [(i, j, v / (n*n)) for i, j, v in term_numerators]
        variance = (sum(diagonal_numerators) + sum(v for _, _, v in term_numerators)) / (n*n)
        if variance < 0:
            raise ValueError("Negative projected variance; no clamping or covariance repair")
        target = _d(plan["objective"]["max_standard_uncertainty"])**2
        sensitivities = [{"index": i, "quantity_id": covariance["quantity_ids"][i],
            "input_unit": covariance["units"][i], "output_unit": model["data"]["unit"],
            "gradient": _number(g), "marginal_scale": _number(abs(g) * matrix[i][i].sqrt()),
            "diagonal_variance_term": _number(diagonal[i]),
            "meaning": "marginal_scale_is_not_an_additive_attribution"} for i, g in enumerate(gradient)]
        candidates = []
        scored = []
        for candidate in plan["candidates"]:
            scales = list(map(_d, candidate["scales"]))
            changed = [[scales[i] * matrix[i][j] * scales[j] for j in range(dim)] for i in range(dim)]
            new_variance = sum((numerators[i] * changed[i][j] * numerators[j]
                                for i in range(dim) for j in range(dim)), Decimal(0)) / (n*n)
            if new_variance < 0:
                raise ValueError("Counterfactual has negative variance; no silent repair")
            delta = variance - new_variance
            status = "REDUCES_DECLARED_VARIANCE" if delta > 0 else "INCREASES_DECLARED_VARIANCE" if delta < 0 else "UNCHANGED"
            candidates.append({"candidate_id": candidate["id"], "status": status,
                "variance": _number(new_variance), "standard_uncertainty": _number(new_variance.sqrt()),
                "variance_reduction": _number(delta), "objective_met": new_variance <= target,
                "input_covariance_id": covariance["covariance_id"],
                "counterfactual_matrix": [[_number(v) for v in row] for row in changed],
                "scope": "hypothetical_D_Sigma_D_at_fixed_means_not_new_observations"})
            scored.append((new_variance, candidate["id"], delta))
        scored.sort(key=lambda item: (item[0], item[1]))
        return {"status": "CONDITIONAL_FIRST_ORDER", "input_covariance_id": covariance["covariance_id"],
            "retained_model_variance": model["data"]["variance"], "linearized_variance": _number(variance),
            "rounding_difference": _number(variance - _d(model["data"]["variance"])),
            "sensitivities": sensitivities,
            "cross_terms": [{"left_index": i, "right_index": j, "signed_variance_term": _number(v)} for i, j, v in terms],
            "candidates": candidates, "ranking": [identity for _, identity, _ in scored],
            "proposed_candidate_id": None if variance <= target else next((identity for _, identity, delta in scored if delta > 0), None),
            "objective_met": variance <= target}


def review(source, parameters):
    c.keys(parameters, {"model", "audit", "plan"})
    model, audit = parameters["model"], parameters["audit"]
    plan = validate_plan(parameters["plan"], source=source, model=model, audit=audit)
    request = study.source_request(source)
    decoded = study.decode(request)
    corrected = model["parameters"]["calibrated"]["data"]
    device = request["capture"]["manifest"]
    numeric = _numeric(request, model, plan)
    next_actions = ["Review exact instrument/cable/electrical interface before any physical experiment",
                    "Establish acquisition timing independently of report receipt",
                    "Establish traceable reference and calibration evidence; arithmetic checks are insufficient"]
    if numeric["proposed_candidate_id"] is not None:
        next_actions.append("Validate the proposed covariance reduction with a separately authorized physical experiment")
    elif numeric["status"] == "UNCERTAINTY_UNKNOWN":
        next_actions.append("Supply an evidence-supported complete joint uncertainty budget before ranking changes")
    elif numeric["objective_met"] is True:
        next_actions.append("Declared uncertainty objective already met; no numerical need for augmentation, physical qualification still required")
    else:
        next_actions.append("No declared candidate reduces projected uncertainty; revise the experimental hypothesis")
    return {"schema": "ciw.link-improvement-review.v1", "status": "PROPOSAL_ONLY",
        "instance_id": plan["instance_id"], "bundle_id": plan["bundle_id"],
        "bundle_binding": "declared_reference_checked_against_exact_bundle_by_link_pinwheel_lifecycle",
        "source_evidence_id": source["evidence_id"], "plan_ref": c.content_identity(plan),
        "model_result_id": model["result_id"], "audit_result_id": audit["result_id"],
        "hub": {"target": "Verified Physical State", "status": "NOT_ESTABLISHED",
            "representation": "projection_of_existing_evidence_not_a_new_state_owner",
            "device_id": device["device_id"], "instrument_id": device["instrument_id"],
            "manifest_ref": c.content_identity(device), "origin": decoded["origin"],
            "clock": deepcopy(decoded["clock"]), "frame": decoded["channel"]["frame"],
            "context": deepcopy(decoded["context"]), "limits": dict(LIMITS)},
        "blades": [
            {"name": "Augment", "status": "RECORDED_INPUT", "evidence": c.content_identity(request["capture"]),
             "meaning": "Retained original instrument bytes; electrical and physical compatibility not established"},
            {"name": "Measure", "status": corrected["uncertainty_status"], "evidence": corrected["calibration_ref"],
             "applicability": corrected["applicability"], "meaning": "Declared correction and full covariance; not calibration certification"},
            {"name": "Translate", "status": "RETAINED_MEASUREMENT_VIEW", "evidence": plan["bundle_id"],
             "meaning": "Existing measurement/observation/covariance contracts retain original ancestry"},
            {"name": "Understand", "status": "DECLARED_MODEL", "evidence": model["result_id"],
             "meaning": "Classical corrected mean and conditional uncertainty; not a qualified industrial predictor"},
            {"name": "Operationalize", "status": "PROPOSE_ONLY", "evidence": c.content_identity(plan),
             "meaning": "Human-reviewable next experiment; no authorization, deployment or hardware change"}],
        "objective": deepcopy(plan["objective"]), "feedback": numeric, "next_actions": next_actions,
        "scope": "one_explicit_local_instance_no_fleet_aggregation_or_training",
        "limits": dict(LIMITS)}


def execute(index, source, parameters):
    if index == 0:
        return review(source, parameters)
    c.keys(parameters, {"candidate"})
    candidate = parameters["candidate"]
    if candidate["operation_id"] != OPS[0] or candidate["evidence_id"] != source["evidence_id"]:
        raise ValueError("Improvement audit requires its exact review/source prerequisite")
    from .link_improvement_audit import audit
    return audit(source, candidate)


def _validator(index):
    def validate(operation_id, data, source, parameters, selection):
        if (operation_id != OPS[index] or selection["channel"] != link_workflow.CHANNEL
                or selection["interval_s"] != [0.0, 1.0]):
            raise ValueError("Improvement operation must use the complete retained declaration")
        if not study._equal(data, execute(index, source, parameters)):
            raise ValueError("Improvement record differs from source, plan or exact prerequisites")
    return validate


def register():
    global _REGISTERED
    study.register()
    with _LOCK:
        if not _REGISTERED:
            for i, op in enumerate(OPS):
                register_payload_validator(op, _validator(i), role=("decision", "verification")[i])
                register_dependency_fields(op, ("model", "audit") if i == 0 else ("candidate",))
            _REGISTERED = True


def runtime_identity():
    from . import link_improvement_audit
    raw = Path(__file__).read_bytes() + Path(link_improvement_audit.__file__).read_bytes()
    return {"provider": "ciw.link_improvement", "version": "1", "code_sha256": sha256(raw).hexdigest(),
            "original_study_runtime": study.runtime_identity(), "scope": "local_proposal_and_arithmetic_only"}


def registry(*, bind=True):
    register()
    result = study.registry(bind=bind)
    manifest = InstrumentManifest(instrument_id="link-improvement.v1", role="retained_instrument_capture",
        units={link_workflow.CHANNEL: "1"}, frames=(link_workflow.FRAME,), sampling={"kind": "one_declaration"},
        supported_operations=OPS, calibration_requirements={"scope": "no_physical_qualification"})
    result.advertise(manifest, runtime=runtime_identity(), capabilities={op: ["link_improvement"] for op in OPS},
        inputs={OPS[1]: {"candidate": Port("ciw.operation-result.v1").to_dict()}})
    if bind:
        for i, op in enumerate(OPS):
            result.bind(Operation(op, ("decision", "verification")[i],
                lambda source, parameters, i=i: execute(i, source, parameters), runtime_identity))
    return result


def graph(plan, model, audit):
    return experiment("link-improvement", model_id="link-improvement.v1", nodes=[
        {"node_id": "review", "operation_id": OPS[0], "parameters": {"model": model, "audit": audit, "plan": plan},
         "depends_on": [], "inputs": {}},
        {"node_id": "audit", "operation_id": OPS[1], "parameters": {}, "depends_on": [],
         "inputs": {"candidate": {"node_id": "review", "port": "result"}}}])
