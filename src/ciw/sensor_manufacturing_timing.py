"""Evidence-bound setup/batch timing identification inside existing NET Sessions.

Native observations remain original records. A separate retained protocol fixes
trial counts, resource/configuration, train/holdout partition and error criterion.
Exact OLS describes the selected samples; it creates no probabilistic error model,
physical qualification, replacement acquisition schema or manufacturing authority.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction as F
import math

from . import sensor_manufacturing as m, sensor_manufacturing_analysis as a
from .control_contracts import bytes_ref, content_ref, keys, text
from .core.identities import content_identity

OPERATION = "engineering.characterize-manufacturing-time.v1"
STUDY_PATH = "manufacturing-timing-study.json"
SCHEMA = "ciw.manufacturing-timing-study.v1"
PROTOCOL = "ciw.manufacturing-timing-protocol.v1"
RESULT = "ciw.manufacturing-timing-characterization.v1"
MAX_SERIES, MAX_TRIALS, MAX_OBSERVATIONS = 16, 64, 512
ZERO = F(0)


def _quantity(value: F) -> dict:
    return {"value": float(value), "exact": a.encoded(value)}


def _errors(values: list[F]) -> dict:
    n = len(values)
    if not n:
        return {"count": 0, "mean_error_s": None, "mae_s": None,
                "mse_s2": None, "mse_q": None, "rmse_s": None, "max_absolute_error_s": None}
    mse = sum((v*v for v in values), ZERO)/n
    return {"count": n, "mean_error_s": float(sum(values, ZERO)/n),
            "mae_s": float(sum(map(abs, values), ZERO)/n),
            "mse_s2": float(mse), "mse_q": a.encoded(mse), "rmse_s": math.sqrt(float(mse)),
            "max_absolute_error_s": float(max(map(abs, values)))}


def _ols(points: list[tuple[int, F]]) -> dict:
    """Two-parameter unweighted descriptive fit with explicit rank deficiency."""
    n = len(points)
    sx = sum(x for x, _ in points); sxx = sum(x*x for x, _ in points)
    sy = sum((y for _, y in points), ZERO); sxy = sum((x*y for x, y in points), ZERO)
    det = n*sxx-sx*sx
    if n < 3 or det == 0:
        return {"status": "NOT_IDENTIFIABLE", "setup_s": None, "per_batch_s": None,
                "design_determinant": det, "train_errors": None,
                "normal_equations": None, "nonnegative_parameters": None}
    batch = (n*sxy-sx*sy)/det; setup = (sy-batch*sx)/n
    residuals = [y-setup-batch*x for x, y in points]
    # These equalities check arithmetic, not independent physical validity.
    constant = sum(residuals, ZERO); slope = sum((x*r for (x, _), r in zip(points, residuals)), ZERO)
    if constant or slope:
        raise ValueError("OLS normal-equation consistency failure")
    return {"status": "IDENTIFIED_DESCRIPTIVE_FIT", "setup_s": _quantity(setup),
            "per_batch_s": _quantity(batch), "design_determinant": det,
            "train_errors": _errors(residuals),
            "normal_equations": {"sum_residual_q": a.encoded(constant), "sum_x_residual_q": a.encoded(slope)},
            "nonnegative_parameters": setup >= 0 and batch >= 0}


def _inputs(run: dict, baseline: dict) -> tuple:
    spec, inventory, files = m.project.context(run)
    roles = {v["path"]: v["kind"] for v in spec["artifacts"]}
    study = m._json(STUDY_PATH, files, roles, {"model.input"})
    keys(study, {"schema", "protocol_path", "bindings"})
    if study["schema"] != SCHEMA:
        raise ValueError("Unsupported timing study")
    protocol_path = study["protocol_path"]
    if protocol_path in {m.PLAN_PATH, a.OPTIONS_PATH, STUDY_PATH}:
        raise ValueError("Protocol must be a distinct retained artifact")
    protocol = m._json(protocol_path, files, roles, {"model.input"})
    keys(protocol, {"schema", "definition_ref", "timing_basis", "duration_scope", "setup_policy", "series"})
    if (protocol["schema"] != PROTOCOL or protocol["definition_ref"] != baseline["definition_ref"]
            or protocol["timing_basis"] != "reported_elapsed_duration_not_receipt_timestamp_difference"
            or protocol["duration_scope"] != "one_setup_plus_declared_batches_excluding_queue"
            or protocol["setup_policy"] != "reset_before_every_trial"):
        raise ValueError("Timing protocol definition or elapsed-time semantics mismatch")
    protocol_ref = bytes_ref(files[protocol_path])
    plan = m._json(m.PLAN_PATH, files, roles)
    steps = {r["operation_id"]: r for r in plan["operations"]}
    resources = {r["resource_id"]: r for r in plan["resources"]}
    native = m.project.compile_project(run, m.PARAMETERS)
    observations = {r["observation"]["record_digest"]: r for r in native["observations"]}
    groups = m._named(protocol["series"], "operation_id", MAX_SERIES)
    bindings = study["bindings"]
    if type(bindings) is not dict or not 1 <= len(bindings) <= MAX_OBSERVATIONS:
        raise ValueError("Timing observation bindings exceed budget or are empty")
    used_trials, used_refs, stream_paths, selected = set(), set(), set(), {}
    for name in sorted(groups):
        group = groups[name]
        keys(group, {"operation_id", "operation_ref", "resource_id", "resource_ref", "batch_size",
                     "stream_path", "model_id", "clock_id", "frame", "semantics", "trials", "max_holdout_error_s"})
        if name not in steps or group["operation_ref"] != content_identity(steps[name]):
            raise ValueError("Timing operation binding changed")
        rid = group["resource_id"]
        if (rid not in resources or rid not in steps[name]["allowed_resources"]
                or group["resource_ref"] != content_identity(resources[rid])
                or type(group["batch_size"]) is not int or group["batch_size"] != steps[name]["batch_size"]):
            raise ValueError("Timing resource/configuration/batch binding changed")
        for field in ("model_id", "clock_id", "frame"):
            text(group[field])
        if group["semantics"] not in {"observed", "simulated"}:
            raise ValueError("Timing identification requires observed or explicitly simulated durations")
        stream = group["stream_path"]
        if stream not in spec["native_streams"]:
            raise ValueError("Timing stream is not an original selected native stream")
        stream_paths.add(stream)
        m._number(group["max_holdout_error_s"])
        samples = []
        for trial in m._list(group["trials"], MAX_TRIALS, 1):
            keys(trial, {"trial_id", "partition", "batch_count"})
            tid = text(trial["trial_id"]); m._count(trial["batch_count"])
            if trial["partition"] not in {"train", "holdout"} or tid in used_trials or tid not in bindings:
                raise ValueError("Missing, duplicate or invalid predeclared trial")
            used_trials.add(tid)
            ref = content_ref(bindings[tid])
            if ref in used_refs or ref not in observations:
                raise ValueError("Timing observation is missing or reused across trials/partitions")
            used_refs.add(ref)
            original = observations[ref]; row = original["observation"]
            if (original["source_path"] != stream or row["identity"]["entity_id"] != tid
                    or row["identity"]["model_id"] != group["model_id"]
                    or row["clock"]["id"] != group["clock_id"] or row["frame"] != group["frame"]
                    or row["quantity"] != "duration" or row["unit"] != "s"
                    or row["provenance"]["semantics"] != group["semantics"]):
                raise ValueError("Timing observation identity, origin, stream, clock, frame or unit mismatch")
            required = {protocol_ref, baseline["definition_ref"]}
            if not required <= set(row["provenance"]["sources"]):
                raise ValueError("Original timing observation lacks exact protocol/definition references")
            m._number(row["value"])
            samples.append({"trial": deepcopy(trial), "native": deepcopy(original)})
        selected[name] = samples
    if set(bindings) != used_trials:
        raise ValueError("Unplanned timing bindings cannot enter the study")
    expected_refs = {ref for ref, row in observations.items() if row["source_path"] in stream_paths}
    if expected_refs != used_refs:
        raise ValueError("Every observation in the dedicated timing streams must be accounted for")
    return plan, groups, selected, protocol_ref, inventory["files"][STUDY_PATH]["sha256"]


def characterize(run: dict, parameters: dict) -> dict:
    baseline = a.analyze(run, parameters)
    plan, groups, samples, protocol_ref, study_ref = _inputs(run, baseline)
    selection = baseline["analysis"]["selection"]
    dispatch = selection["schedule"]
    selected_resources = {r["operation_id"]: r["resource_id"] for r in dispatch or []}
    sensitivity = {r["operation_id"]: r["makespan_reduction_s"]
                   for r in (baseline["analysis"]["diagnostics"] or {}).get("duration_sensitivity", [])}
    nominal = a._durations(plan); revised = dict(nominal)
    steps = {r["operation_id"]: r for r in plan["operations"]}
    results, recommendations, applied = [], [], []
    for name in sorted(groups):
        group, items, step = groups[name], samples[name], steps[name]
        def pairs(partition):
            return [(r["trial"]["batch_count"], a.rational(r["native"]["observation"]["value"]))
                    for r in items if r["trial"]["partition"] == partition]
        train, holdout = pairs("train"), pairs("holdout")
        fit = _ols(train)
        old_setup, old_batch = a.rational(step["setup_s"]), a.rational(step["per_batch_s"])
        old_errors = _errors([y-old_setup-old_batch*x for x, y in holdout])
        batch_count = (plan["order"]["quantity"]+step["batch_size"]-1)//step["batch_size"]
        reasons = []
        if len(train) < 3: reasons.append("AT_LEAST_THREE_TRAINING_TRIALS_REQUIRED")
        if len({x for x, _ in train}) < 2: reasons.append("VARY_BATCH_COUNT_TO_SEPARATE_SETUP_AND_BATCH_TIME")
        if len(holdout) < 2: reasons.append("AT_LEAST_TWO_HELD_OUT_TRIALS_REQUIRED")
        new_errors, prediction, trial_residuals = None, None, []
        if fit["setup_s"] is not None:
            setup, batch = a.decoded(fit["setup_s"]["exact"]), a.decoded(fit["per_batch_s"]["exact"])
            new_errors = _errors([y-setup-batch*x for x, y in holdout])
            prediction = _quantity(setup+batch*batch_count)
            if not fit["nonnegative_parameters"]:
                reasons.append("NEGATIVE_FITTED_SETUP_OR_BATCH_TIME_NO_CLAMP")
            support = [min(x for x, _ in train), max(x for x, _ in train)]
            if not support[0] <= batch_count <= support[1]:
                reasons.append("TARGET_BATCH_COUNT_OUTSIDE_TRAINING_SUPPORT")
            if any(not support[0] <= x <= support[1] for x, _ in holdout):
                reasons.append("HELD_OUT_BATCH_COUNT_OUTSIDE_TRAINING_SUPPORT")
            if holdout:
                errors = [y-setup-batch*x for x, y in holdout]
                if max(map(abs, errors)) > a.rational(group["max_holdout_error_s"]):
                    reasons.append("HELD_OUT_ERROR_EXCEEDS_PREDECLARED_LIMIT")
                if a.decoded(new_errors["mse_q"]) > a.decoded(old_errors["mse_q"]):
                    reasons.append("HELD_OUT_MSE_WORSE_THAN_ORIGINAL_MODEL")
            for item in items:
                x, y = item["trial"]["batch_count"], a.rational(item["native"]["observation"]["value"])
                trial_residuals.append({"trial_id": item["trial"]["trial_id"],
                    "partition": item["trial"]["partition"], "batch_count": x,
                    "observation_ref": item["native"]["observation"]["record_digest"],
                    "predicted_s": _quantity(setup+batch*x), "residual_s": _quantity(y-setup-batch*x)})
        else:
            support = None
        if selected_resources.get(name) != group["resource_id"]:
            reasons.append("CHARACTERIZED_RESOURCE_IS_NOT_THE_SELECTED_DISPATCH_RESOURCE")
        eligible = not reasons
        if eligible:
            revised[name] = a.decoded(prediction["exact"]); applied.append(name)
        results.append({"operation_id": name, "operation_ref": group["operation_ref"],
            "resource_id": group["resource_id"], "resource_ref": group["resource_ref"],
            "semantics": group["semantics"], "fit": fit, "baseline_holdout_errors": old_errors,
            "fitted_holdout_errors": new_errors, "training_batch_support": support,
            "target_batch_count": batch_count, "candidate_duration_s": prediction,
            "status": "CANDIDATE_FOR_REVIEW" if eligible else "NEEDS_INVESTIGATION",
            "reasons": reasons, "max_holdout_error_s": group["max_holdout_error_s"],
            "samples": items, "residuals": trial_residuals,
            "parameter_covariance": None, "prediction_interval": None,
            "uncertainty_treatment": "native_uncertainty_retained_not_propagated_unweighted_descriptive_fit",
            "future_coverage": None, "automatic_model_promotion": False})
        if reasons:
            recommendations.append({"operation_id": name, "reasons": reasons,
                "nominal_ten_percent_route_leverage_s": sensitivity.get(name),
                "next_action": "NEW_PROTOCOL_WITH_VARIED_BATCH_COUNTS" if fit["setup_s"] is None
                               else "INVESTIGATE_HELD_OUT_RESIDUALS_AND_APPLICABILITY",
                "authority": "experiment_design_only_no_equipment_execution"})
    # Prioritize original declared route leverage, not a claimed value of information.
    recommendations.sort(key=lambda r: (-(r["nominal_ten_percent_route_leverage_s"] or 0), r["operation_id"]))
    projection = None
    if dispatch is not None and applied:
        ends, _ = a._dispatch(plan, dispatch, revised)
        old = a.decoded(selection["audit"]["makespan_q"]); new = max(ends.values(), default=ZERO)
        projection = {"baseline_duration_s": _quantity(old), "candidate_duration_s": _quantity(new),
            "model_revision_delta_s": _quantity(new-old), "updated_operations": applied,
            "unchanged_declared_operations": sorted(set(steps)-set(applied)),
            "dispatch": "fixed_original_selected_resources_and_order_not_reoptimized",
            "basis": "mixed_fitted_and_declared_durations" if len(applied) < len(steps) else "fitted_durations",
            "claim": "prediction_update_not_a_process_speedup_or_observed_route_duration",
            "uncertainty_interval": None, "physical_savings_established": False}
    return {**baseline, "schema": RESULT,
        "timing_characterization": {"protocol_ref": protocol_ref, "study_ref": study_ref,
            "series": results, "fixed_dispatch_projection": projection, "next_experiments": recommendations,
            "method": "exact_unweighted_OLS_elapsed_seconds_equals_setup_plus_batch_count_times_batch_seconds",
            "source_authentication": "NOT_ESTABLISHED", "trial_counts": "protocol_declarations_not_authenticated_machine_counts",
            "partition": "hash_bound_predeclared_roles_not_proof_of_prospective_registration",
            "independence_or_distribution": None, "model_promotion": "NOT_PERFORMED",
            "physical_validation": "NOT_ESTABLISHED"}}


def prepare_protocol(run: dict, *, operation_id: str, resource_id: str, stream_path: str,
                     model_id: str, clock_id: str, frame: str, semantics: str,
                     train_batches: list[int], holdout_batches: list[int], max_holdout_error_s: float) -> dict:
    """Prepare explicit trial instructions without acquiring, fitting or changing source.

    Counts and error criteria are operator inputs, not inferred sensor capabilities.
    This defines a trial protocol, not production-unit identities or machine commands.
    """
    baseline = m.compile_definition(run, m.PARAMETERS)
    _, _, _, _, plan, _, resources, steps = m._definition(run)
    if operation_id not in steps or resource_id not in resources:
        raise ValueError("Choose an existing declared operation and resource")
    step = steps[operation_id]
    schedule_row = next(r for r in baseline["schedule"]["instructions"] if r["operation_id"] == operation_id)
    if resource_id not in step["allowed_resources"] or resource_id in schedule_row["rejected_resources"]:
        raise ValueError("Choose an explicitly eligible resource for the timing protocol")
    for values, minimum in ((train_batches, 3), (holdout_batches, 2)):
        for value in m._list(values, MAX_TRIALS, minimum):
            m._count(value)
    if len(train_batches)+len(holdout_batches) > MAX_TRIALS or len(set(train_batches)) < 2:
        raise ValueError("Vary training batch counts within the total trial budget")
    target = (plan["order"]["quantity"]+step["batch_size"]-1)//step["batch_size"]
    if any(not min(train_batches) <= value <= max(train_batches) for value in [target, *holdout_batches]):
        raise ValueError("Training support must cover the target and all held-out batch counts")
    m.project.packets.relative(stream_path)
    if stream_path in {m.PLAN_PATH, a.OPTIONS_PATH, STUDY_PATH}:
        raise ValueError("Choose a dedicated observation-stream path")
    for value in (model_id, clock_id, frame):
        text(value)
    if semantics not in {"observed", "simulated"}:
        raise ValueError("Choose explicit observed or simulated timing semantics")
    m._number(max_holdout_error_s)
    trials = []
    for partition, counts in (("train", train_batches), ("holdout", holdout_batches)):
        for index, count in enumerate(counts):
            tid = content_identity({"operation_ref": content_identity(step), "resource_ref": content_identity(resources[resource_id]),
                                    "partition": partition, "index": index, "batch_count": count})
            trials.append({"trial_id": tid, "partition": partition, "batch_count": count})
    return {"schema": PROTOCOL, "definition_ref": baseline["definition_ref"],
        "timing_basis": "reported_elapsed_duration_not_receipt_timestamp_difference",
        "duration_scope": "one_setup_plus_declared_batches_excluding_queue", "setup_policy": "reset_before_every_trial",
        "series": [{"operation_id": operation_id, "operation_ref": content_identity(step),
                    "resource_id": resource_id, "resource_ref": content_identity(resources[resource_id]),
                    "batch_size": step["batch_size"], "stream_path": stream_path, "model_id": model_id,
                    "clock_id": clock_id, "frame": frame, "semantics": semantics,
                    "trials": trials, "max_holdout_error_s": max_holdout_error_s}]}
