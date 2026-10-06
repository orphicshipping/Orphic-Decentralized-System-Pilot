"""Correlated measurement-error propagation for the existing descriptive timing fit.

This does not refit, assume independent trials, estimate process noise from residuals,
or promote a model. Full input covariance uses NET's existing covariance artifact.
Exact linear maps expose shared-error cancellation in held-out residuals. Route
uncertainty is only a local measurement contribution, never total prediction error.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction as F
import math

from . import sensor_manufacturing as m, sensor_manufacturing_analysis as a
from . import sensor_manufacturing_timing as t
from .control_contracts import bytes_ref, keys
from .core.covariance import create_covariance_artifact, validate_covariance_artifact
from .core.identities import content_identity

OPERATION = "engineering.characterize-manufacturing-time.v2"
RESULT = "ciw.manufacturing-timing-characterization.v2"
OPTIONS_PATH = "manufacturing-timing-uncertainty.json"
SCHEMA = "ciw.manufacturing-timing-uncertainty.v1"
FRAME = "manufacturing.timing.duration-vector"
MAX_AXES = 64
ZERO = F(0)


def _float(q: F) -> float:
    value = float(q)
    if not math.isfinite(value) or (q and value == 0):
        raise ValueError("Exact uncertainty result is not representable as a finite nonzero JSON number")
    return value


def _matrix_q(matrix: list) -> list:
    return [[a.encoded(v) for v in row] for row in matrix]


def _psd(matrix: list[list[F]]) -> None:
    """Exact Schur-complement check, including singular PSD matrices; no repair.

    NET's native validator also runs. This provider requires exact symmetry/PSD
    because its rational propagation cannot erase small negative directions.
    """
    n = len(matrix)
    work = deepcopy(matrix)
    if any(work[i][j] != work[j][i] for i in range(n) for j in range(n)):
        raise ValueError("Rational propagation requires exact covariance symmetry")
    for k in range(n):
        pivot = work[k][k]
        if pivot < 0:
            raise ValueError("Covariance is not exactly positive semidefinite")
        if pivot == 0:
            if any(work[k][j] != 0 for j in range(k + 1, n)):
                raise ValueError("Zero Schur pivot has nonzero covariance; not positive semidefinite")
        else:
            for i in range(k + 1, n):
                for j in range(i, n):
                    work[i][j] -= work[i][k] * work[k][j] / pivot
                    work[j][i] = work[i][j]


def propagate(weights: list[list[F]], covariance: list[list[F]]) -> list[list[F]]:
    """Exact W C W^T, retaining signed cross terms and singular directions."""
    n = len(covariance)
    if not 1 <= n <= MAX_AXES or any(len(row) != n for row in covariance):
        raise ValueError("Covariance exceeds the bounded square propagation profile")
    if not weights or any(len(row) != n for row in weights):
        raise ValueError("Linear map must cover every covariance coordinate")
    _psd(covariance)
    wc = [[sum((w[k] * covariance[k][j] for k in range(n)), ZERO)
           for j in range(n)] for w in weights]
    result = [[sum((v[k] * w[k] for k in range(n)), ZERO)
               for w in weights] for v in wc]
    if any(result[i][i] < 0 for i in range(len(result))):
        raise ValueError("Negative propagated variance; no clipping")
    return result


def weights(counts: list[int]) -> tuple[list[F], list[F]]:
    """Influence rows for the unchanged OLS estimator; holdout is never an input."""
    n, sx, sxx = len(counts), sum(counts), sum(x*x for x in counts)
    det = n * sxx - sx * sx
    if n < 3 or not det:
        raise ValueError("Setup/batch uncertainty needs an identified training design")
    return ([F(sxx - sx*x, det) for x in counts],
            [F(n*x - sx, det) for x in counts])


def band(mean: F, variance: F, factor: F, limit: F | None = None) -> dict:
    """k*u diagnostic, not a confidence/coverage claim. Classifications are exact."""
    if variance < 0 or factor <= 0:
        raise ValueError("Invalid variance or coverage multiplier")
    u = math.sqrt(_float(variance)) if variance else 0.0
    expanded = _float(factor) * u
    center = _float(mean)
    if not all(math.isfinite(v) for v in (expanded, center-expanded, center+expanded)):
        raise ValueError("Expanded uncertainty is not finitely representable")
    status = None
    if limit is not None:
        magnitude = abs(mean)
        if magnitude <= limit and factor*factor*variance <= (limit-magnitude)**2:
            status = "BAND_WITHIN_PREDECLARED_LIMIT"
        elif magnitude > limit and factor*factor*variance < (magnitude-limit)**2:
            status = "BAND_OUTSIDE_PREDECLARED_LIMIT"
        else:
            status = "BAND_OVERLAPS_PREDECLARED_LIMIT"
    return {"estimate_s": center, "variance_s2": _float(variance), "variance_exact": a.encoded(variance),
            "standard_uncertainty_s": u, "coverage_multiplier": _float(factor),
            "expanded_band_s": [center-expanded, center+expanded], "screen": status,
            "coverage_probability": None, "basis": "supplied_measurement_covariance_only"}


def _inputs(run: dict, base: dict, items: list[dict]) -> tuple:
    spec, _, files = m.project.context(run)
    roles = {v["path"]: v["kind"] for v in spec["artifacts"]}
    if OPTIONS_PATH not in files:
        return None, None, None
    options = m._json(OPTIONS_PATH, files, roles, {"model.input"})
    keys(options, {"schema", "study_ref", "protocol_ref", "covariance_path", "coverage_multiplier"})
    timing = base["timing_characterization"]
    if (options["schema"] != SCHEMA or options["study_ref"] != timing["study_ref"]
            or options["protocol_ref"] != timing["protocol_ref"]):
        raise ValueError("Uncertainty options must bind the original timing study and protocol")
    m._number(options["coverage_multiplier"])
    if not 0 < options["coverage_multiplier"] <= 10:
        raise ValueError("Require an explicit uncertainty multiplier in (0,10]")
    if options["covariance_path"] is None:
        return options, None, bytes_ref(files[OPTIONS_PATH])
    if not 1 <= len(items) <= MAX_AXES:
        raise ValueError("Joint timing covariance profile admits 1..64 original trial axes")
    path = options["covariance_path"]
    if path in {OPTIONS_PATH, t.STUDY_PATH, m.PLAN_PATH, a.OPTIONS_PATH}:
        raise ValueError("Covariance must be a separate retained native artifact")
    cov = m._json(path, files, roles, {"model.input"})
    refs = [r["native"]["observation"]["record_digest"] for r in items]
    values = [r["native"]["observation"]["value"] for r in items]
    # Axis count is bounded before invoking the generic native matrix validator.
    if type(cov) is not dict or type(cov.get("quantity_ids")) is not list or len(cov["quantity_ids"]) != len(refs):
        raise ValueError("Covariance must cover every trial, including held-out observations")
    validate_covariance_artifact(cov, expected_quantity_ids=refs,
                                 expected_units=["s"] * len(refs), expected_frame=FRAME)
    basis = content_identity({"protocol_ref": timing["protocol_ref"], "observation_refs": refs})
    if cov["basis"] != {"kind": "observation", "id": basis} or not m.project.same(cov["reference_values"], values):
        raise ValueError("Covariance basis or reference values differ from original timing observations")
    required = {timing["protocol_ref"], timing["study_ref"], *refs}
    if not required <= set(cov["provenance"]["source_evidence_ids"]):
        raise ValueError("Covariance lacks original observation/protocol/study ancestry")
    for index, item in enumerate(items):
        scalar = item["native"]["observation"]["uncertainty"]
        if scalar is not None:
            if (a.rational(scalar["matrix"][0][0]) != a.rational(cov["matrix"][index][index])
                    or scalar["covariance_id"] not in cov["provenance"]["source_covariance_ids"]):
                raise ValueError("Joint covariance conflicts with retained scalar uncertainty or its ancestry")
    return options, cov, bytes_ref(files[OPTIONS_PATH])


def _route(base: dict, plan: dict, reviews: list, row_maps: dict, n: int) -> tuple[dict, list | None]:
    """Local derivative only for one strict active path; ties are explicitly withheld."""
    projection = base["timing_characterization"]["fixed_dispatch_projection"]
    result = {"status": "NOT_AVAILABLE", "active_operations": [], "standard_uncertainty_s": None,
              "total_route_uncertainty": None, "coverage_probability": None,
              "scope": "local_measurement_component_fixed_dispatch_other_durations_conditioned_exact",
              "critical_path_switching": "not_bounded", "process_discrepancy": "not_evaluated"}
    if projection is None:
        return result, None
    updated = projection["updated_operations"]
    eligible = {r["operation_id"] for r in reviews if r["review_status"] == "CANDIDATE_WITH_CONDITIONAL_UNCERTAINTY"}
    if not set(updated) <= eligible:
        result["status"] = "WITHHELD_UNCERTAINTY_REVIEW"
        return result, None
    durations = a._durations(plan)
    timing = {r["operation_id"]: r for r in base["timing_characterization"]["series"]}
    for name in updated:
        durations[name] = a.decoded(timing[name]["candidate_duration_s"]["exact"])
    rows = base["analysis"]["selection"]["schedule"]
    ends, parents = a._dispatch(plan, rows, durations)
    tails = [name for name in ends if ends[name] == max(ends.values())]
    if len(tails) != 1:
        result["status"] = "WITHHELD_NONDIFFERENTIABLE_PATH_TIE"
        return result, None
    path, current = [], tails[0]
    while True:
        path.append(current)
        if not parents[current]:
            break
        maximum = max(ends[v] for v in parents[current])
        active = [v for v in parents[current] if ends[v] == maximum]
        if len(active) != 1 or maximum == 0:
            result["status"] = "WITHHELD_NONDIFFERENTIABLE_PATH_TIE"
            return result, None
        current = active[0]
    vector = [sum((row_maps[name][i] for name in path if name in updated), ZERO) for i in range(n)]
    result.update(status="LOCAL_MEASUREMENT_COMPONENT_ONLY", active_operations=list(reversed(path)),
                  candidate_duration_s=_float(max(ends.values())),
                  conditioned_declared_operations=projection["unchanged_declared_operations"])
    return result, vector


def characterize(run: dict, parameters: dict) -> dict:
    base = t.characterize(run, parameters)
    series = base["timing_characterization"]["series"]
    items = [r for group in series for r in group["samples"]]
    options, cov, options_ref = _inputs(run, base, items)
    extension = {"status": "JOINT_COVARIANCE_NOT_SUPPLIED", "options_ref": options_ref,
        "source_covariance": None, "propagated_covariance": None, "matrix_exact": None,
        "linear_map_exact": None, "output_coordinates": [], "series": [], "route_component": None,
        "estimator": "original_unweighted_OLS_unchanged", "input_relation": "separately_supplied_uncertainty_model_not_modified_acquisition_metadata",
        "model_discrepancy": "NOT_ESTIMATED", "future_prediction_interval": None,
        "coverage_probability": None, "physical_validation": "NOT_ESTABLISHED", "automatic_model_promotion": False}
    if cov is None:
        return {**base, "schema": RESULT, "timing_uncertainty": extension}
    matrix = [[a.rational(v) for v in row] for row in cov["matrix"]]
    _psd(matrix)
    n = len(items); positions = {r["trial"]["trial_id"]: i for i, r in enumerate(items)}
    maps, means, coordinates, records, targets = [], [], [], [], {}
    def add(name: str, kind: str, label: str, vector: list, mean: F) -> int:
        coordinates.append({"operation_id": name, "kind": kind, "label": label})
        maps.append(vector); means.append(mean)
        return len(maps)-1
    for group in series:
        name = group["operation_id"]
        record = {"operation_id": name, "status": "NOT_IDENTIFIABLE", "parameter_indices": [],
                  "target_index": None, "holdout_indices": []}
        if group["fit"]["setup_s"] is None:
            records.append(record)
            continue
        train = [r for r in group["samples"] if r["trial"]["partition"] == "train"]
        h = weights([r["trial"]["batch_count"] for r in train])
        parameter_maps = [[ZERO] * n for _ in range(2)]
        for j, row in enumerate(train):
            index = positions[row["trial"]["trial_id"]]
            parameter_maps[0][index], parameter_maps[1][index] = h[0][j], h[1][j]
        setup = a.decoded(group["fit"]["setup_s"]["exact"])
        slope = a.decoded(group["fit"]["per_batch_s"]["exact"])
        record["parameter_indices"] = [add(name, "parameter", label, vector, mean)
            for label, vector, mean in zip(("setup_s", "per_batch_s"), parameter_maps, (setup, slope))]
        count = group["target_batch_count"]
        target = [x + count*y for x, y in zip(*parameter_maps)]
        targets[name] = target
        record["target_index"] = add(name, "target_mean", f"batches:{count}", target, setup + count*slope)
        for row in group["samples"]:
            if row["trial"]["partition"] != "holdout":
                continue
            tid, count = row["trial"]["trial_id"], row["trial"]["batch_count"]
            vector = [-x-count*y for x, y in zip(*parameter_maps)]
            vector[positions[tid]] += 1
            mean = a.rational(row["native"]["observation"]["value"]) - setup - count*slope
            index = add(name, "holdout_residual", tid, vector, mean)
            record["holdout_indices"].append(index)
        record["status"] = "PROPAGATED_MEASUREMENT_COMPONENT"
        records.append(record)
    extension["source_covariance"] = deepcopy(cov)
    if not maps:
        extension.update(status="NO_IDENTIFIABLE_SERIES", series=records)
        return {**base, "schema": RESULT, "timing_uncertainty": extension}
    observed = [a.rational(r["native"]["observation"]["value"]) for r in items]
    if any(sum((w*y for w,y in zip(vector, observed)), ZERO) != mean for vector,mean in zip(maps, means)):
        raise ValueError("Linear error map differs from original fit or held-out residual arithmetic")
    propagated = propagate(maps, matrix)
    factor = a.rational(options["coverage_multiplier"])
    reviews = []
    for group, record in zip(series, records):
        review = {**record, "review_status": "NEEDS_INVESTIGATION", "parameters": [],
                  "target_mean": None, "heldout_residuals": [], "future_prediction_interval": None}
        for i in record["parameter_indices"]:
            review["parameters"].append({"label": coordinates[i]["label"], **band(means[i], propagated[i][i], factor)})
        if record["target_index"] is not None:
            i = record["target_index"]
            review["target_mean"] = band(means[i], propagated[i][i], factor)
        for i in record["holdout_indices"]:
            item = {"trial_id": coordinates[i]["label"], **band(means[i], propagated[i][i], factor,
                                                                a.rational(group["max_holdout_error_s"]))}
            item["zero_variance_consistency"] = ("NONZERO_RESIDUAL_WITH_ZERO_VARIANCE" if means[i] else "ZERO_RESIDUAL_ZERO_VARIANCE") if propagated[i][i] == 0 else None
            review["heldout_residuals"].append(item)
        if (group["status"] == "CANDIDATE_FOR_REVIEW" and review["heldout_residuals"]
                and all(v["screen"] == "BAND_WITHIN_PREDECLARED_LIMIT" and v["zero_variance_consistency"] != "NONZERO_RESIDUAL_WITH_ZERO_VARIANCE"
                        for v in review["heldout_residuals"])):
            review["review_status"] = "CANDIDATE_WITH_CONDITIONAL_UNCERTAINTY"
        reviews.append(review)
    _, _, files = m.project.context(run)
    plan = m.project.loads_json(files[m.PLAN_PATH].decode("utf-8"))
    route, route_map = _route(base, plan, reviews, targets, n)
    if route_map is not None:
        route["coordinate_index"] = add("route", "local_route_component", "fixed_dispatch", route_map, a.rational(route["candidate_duration_s"]))
        propagated = propagate(maps, matrix)
        route.update(band(means[-1], propagated[-1][-1], factor))
        # Do not present a route interval as if missing process variation were zero.
        route.pop("expanded_band_s")
    axis_ids = [content_identity({"protocol_ref": base["timing_characterization"]["protocol_ref"], **v}) for v in coordinates]
    output = create_covariance_artifact(matrix=[[_float(v) for v in row] for row in propagated],
        quantity_ids=axis_ids, units=["s"]*len(maps), frame="manufacturing.timing.derived-duration-vector",
        reference_values=[_float(v) for v in means], method="exact linear error map W C W^T with rational sidecar",
        basis={"kind": "coordinate", "id": content_identity({"options_ref": options_ref, "coordinates": coordinates})},
        provenance={"provider": "ciw.sensor_manufacturing_uncertainty", "source_evidence_ids": [options_ref],
                    "source_covariance_ids": [cov["covariance_id"]]},
        assumptions=["Original OLS estimator and exact declared batch counts held fixed",
                     "Supplied joint covariance describes only its declared error sources",
                     "No inferred independence, model-discrepancy variance, future coverage or source authenticity",
                     "Any route coordinate is a local derivative with a fixed strict critical path"])
    extension.update(status="PROPAGATED_MEASUREMENT_COMPONENT", propagated_covariance=output,
        matrix_exact=_matrix_q(propagated), linear_map_exact=_matrix_q(maps),
        output_coordinates=coordinates, series=reviews, route_component=route)
    return {**base, "schema": RESULT, "timing_uncertainty": extension}
