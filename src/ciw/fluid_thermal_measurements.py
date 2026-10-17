"""Development-only measured fluid-temperature intake and reference comparison.

This module does not register or alter the released synthetic thermal/fluid
operations. Evidence declarations are retained assertions, not authentication.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import re
import sys

SCHEMA = "ciw.fluid-thermal-measurements.v1"
CALIBRATION_SCHEMA = "ciw.fluid-temperature-calibration.v1"
LIMIT = 8 * 1024 * 1024
MAX_ROWS = 20000
READING_COLUMNS = ["timestamp_s", "temperature", "standard_uncertainty", "quality"]
ESTIMATE_COLUMNS = ["timestamp_s", "temperature_k"]


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected.split()):
        raise ValueError("Missing or unexpected fields; expected: " + expected)


def _text(value):
    if type(value) is not str or not value.strip() or len(value) > 8192:
        raise ValueError("Require bounded nonempty text")
    return value


def _number(value, low=0, high=1e12):
    if type(value) not in (float, int) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Require a finite number in the supported range")
    return float(value)


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Require a bounded integer")
    return value


def _time(value):
    _text(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Timestamps must be explicit UTC")
    return parsed


def _ids(value):
    if type(value) is not list or any(type(x) is not str or not x for x in value) or len(set(value)) != len(value):
        raise ValueError("Require unique text identifiers")
    return set(value)


def _hash(raw):
    return "sha256:" + sha256(raw).hexdigest()


def _digest(value):
    if type(value) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("Require a SHA-256 content identity")
    return value


def _json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("Duplicate JSON key")
            out[key] = value
        return out
    def invalid(value):
        raise ValueError("Nonfinite JSON number: " + value)
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=invalid)


def _dump(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _csv(raw, prediction=False):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    columns = ESTIMATE_COLUMNS if prediction else READING_COLUMNS
    if reader.fieldnames != columns:
        raise ValueError("CSV requires ordered columns: " + ",".join(columns))
    rows = []
    previous = -1.0
    for record in reader:
        if len(rows) >= MAX_ROWS or set(record) != set(columns) or any(v is None for v in record.values()):
            raise ValueError("CSV row shape or row limit exceeded")
        timestamp = _number(float(record["timestamp_s"]), 0, 86400)
        if timestamp <= previous:
            raise ValueError("Timestamps must be strictly increasing without duplicates")
        previous = timestamp
        if prediction:
            rows.append({"timestamp_s": timestamp, "temperature_k": _number(float(record["temperature_k"]), 1, 2000)})
            continue
        quality = record["quality"]
        if quality not in ("valid", "missing"):
            raise ValueError("Unsupported measurement quality")
        if quality == "missing":
            if record["temperature"] or record["standard_uncertainty"]:
                raise ValueError("Missing observations must retain empty value and uncertainty")
            temperature = uncertainty = None
        else:
            temperature = _number(float(record["temperature"]), -273.15, 2000)
            uncertainty = _number(float(record["standard_uncertainty"]), 0, 100)
        rows.append({"timestamp_s": timestamp, "raw_temperature": temperature,
                     "raw_standard_uncertainty": uncertainty, "quality": quality})
    if not rows:
        raise ValueError("Measurement CSV cannot be empty")
    return rows


class Campaign:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        self.retained = {}
        raw = self.path.read_bytes()
        if not 0 < len(raw) <= LIMIT:
            raise ValueError("Manifest size outside supported range")
        self.raw_manifest = raw
        self.manifest = _json(raw)
        self.blockers = []

    def block(self, reason):
        if reason not in self.blockers:
            self.blockers.append(reason)

    def read(self, descriptor):
        _keys(descriptor, "file sha256")
        name = _text(descriptor["file"])
        path = (self.root / name).resolve()
        if Path(name).is_absolute() or not path.is_relative_to(self.root) or path == self.path:
            raise ValueError("Evidence must be a local file inside the campaign directory")
        if not path.is_file() or not 0 < path.stat().st_size <= LIMIT:
            raise ValueError("Evidence file missing or too large")
        raw = path.read_bytes()
        identity = _hash(raw)
        if identity != _digest(descriptor["sha256"]):
            raise ValueError("Evidence content hash mismatch: " + name)
        self.retained[identity] = raw
        if sum(map(len, self.retained.values())) > 64 * 1024 * 1024:
            raise ValueError("Campaign exceeds 64 MiB retained evidence budget")
        return raw

    def calibration(self, role, channel):
        _keys(channel, "instrument_id logger_id location calibration")
        for key in ("instrument_id", "logger_id", "location"):
            _text(channel[key])
        if channel["calibration"] is None:
            self.block(role + ":calibration_missing")
            return None
        cal = _json(self.read(channel["calibration"]))
        _keys(cal, "schema calibration_id instrument_id logger_id input_unit input_min input_max scale offset_k coefficient_covariance residual_standard_uncertainty_k valid_from_utc valid_until_utc certificate traceability_statement fit_run_ids")
        if cal["schema"] != CALIBRATION_SCHEMA or cal["input_unit"] not in ("K", "degC"):
            raise ValueError("Unsupported calibration schema or units; raw electrical conversion is not supported")
        _text(cal["calibration_id"])
        if any(cal[key] != channel[key] for key in ("instrument_id", "logger_id")):
            raise ValueError("Calibration is bound to a different instrument/readout chain")
        lo = _number(cal["input_min"], -273.15, 2000)
        hi = _number(cal["input_max"], -273.15, 2000)
        if lo >= hi or _time(cal["valid_from_utc"]) >= _time(cal["valid_until_utc"]):
            raise ValueError("Invalid calibration applicability interval")
        _number(cal["scale"], .5, 1.5)
        _number(cal["offset_k"], -100, 100)
        _number(cal["residual_standard_uncertainty_k"], 0, 100)
        cov = cal["coefficient_covariance"]
        if type(cov) is not list or len(cov) != 2 or any(type(row) is not list or len(row) != 2 for row in cov):
            raise ValueError("Require a 2x2 calibration coefficient covariance")
        a, b, c, d = [_number(x, -1e4, 1e4) for row in cov for x in row]
        if b != c or a < 0 or d < 0 or a * d < b * b:
            raise ValueError("Calibration covariance must be symmetric positive semidefinite")
        _ids(cal["fit_run_ids"])
        if cal["certificate"] is None:
            self.block(role + ":calibration_certificate_missing")
        else:
            self.read(cal["certificate"])
        if not cal["traceability_statement"]:
            self.block(role + ":traceability_statement_missing")
        else:
            _text(cal["traceability_statement"])
        return cal

    def calibrated(self, rows, cal, run, role, scope):
        if cal is None:
            return None
        if (_time(run["acquisition_start_utc"]) < _time(cal["valid_from_utc"]) or
                _time(run["acquisition_end_utc"]) > _time(cal["valid_until_utc"])):
            raise ValueError("Calibration does not apply throughout acquisition")
        result = []
        for row in rows:
            output = dict(row)
            if row["quality"] == "missing":
                output.update(temperature_k=None, standard_uncertainty_k=None)
            else:
                raw = row["raw_temperature"]
                if not cal["input_min"] <= raw <= cal["input_max"]:
                    raise ValueError("Observation outside calibration range")
                x = raw + (273.15 if cal["input_unit"] == "degC" else 0)
                a, b = cal["scale"], cal["offset_k"]
                temperature = a * x + b
                if not scope["min_temperature_k"] <= temperature <= scope["max_temperature_k"]:
                    raise ValueError("Calibrated temperature outside declared fluid scope")
                cov = cal["coefficient_covariance"]
                variance = x*x*cov[0][0] + 2*x*cov[0][1] + cov[1][1]
                if variance < -1e-10:
                    raise ValueError("Invalid propagated coefficient variance")
                variance = max(0, variance) + (a*row["raw_standard_uncertainty"])**2 + cal["residual_standard_uncertainty_k"]**2
                if role == "reference":
                    variance += sum(_number(value, 0, 100)**2 for value in run["reference_extra_uncertainty_k"].values())
                output.update(temperature_k=temperature, standard_uncertainty_k=math.sqrt(variance))
            result.append(output)
        return result

    def evaluate(self):
        m = self.manifest
        _keys(m, "schema campaign_id evidence_kind scope channels runs held_out_policy criteria uncertainty_budget")
        if m["schema"] != SCHEMA or m["evidence_kind"] not in ("measured", "synthetic"):
            raise ValueError("Unsupported campaign schema or evidence kind")
        _text(m["campaign_id"])
        if m["evidence_kind"] != "measured":
            self.block("synthetic_data_cannot_establish_physical_validation")
        budget = m["uncertainty_budget"]
        if budget is None:
            self.block("reviewed_uncertainty_budget_missing")
        else:
            _keys(budget, "assumption reviewed_by evidence")
            if budget["assumption"] != "independent_disjoint_components":
                self.block("correlated_uncertainty_budget_requires_extended_adapter")
            if not budget["reviewed_by"] or budget["evidence"] is None:
                self.block("reviewed_uncertainty_budget_missing")
            else:
                _text(budget["reviewed_by"])
                self.read(budget["evidence"])
        scope = m["scope"]
        _keys(scope, "fluid phase measurand installation_id mixing_description min_temperature_k max_temperature_k min_pressure_pa max_pressure_pa")
        if (scope["fluid"], scope["phase"], scope["measurand"]) != ("water", "liquid", "local_fluid_temperature"):
            raise ValueError("v1 supports local temperature of declared single-phase liquid water only")
        for key in ("installation_id", "mixing_description"):
            _text(scope[key])
        lo = _number(scope["min_temperature_k"], 278.15, 353.15)
        hi = _number(scope["max_temperature_k"], 278.15, 353.15)
        plo = _number(scope["min_pressure_pa"], 80000, 120000)
        phi = _number(scope["max_pressure_pa"], 80000, 120000)
        if lo >= hi or plo >= phi:
            raise ValueError("Invalid single-phase scope bounds")
        _keys(m["channels"], "observation reference")
        calibrations = {role: self.calibration(role, channel) for role, channel in m["channels"].items()}
        for key in ("instrument_id", "logger_id"):
            if m["channels"]["observation"][key] == m["channels"]["reference"][key]:
                self.block("independent_reference_" + key + "_required")
        runs = m["runs"]
        if type(runs) is not list or len(runs) > 100:
            raise ValueError("Require at most 100 runs")
        if not runs:
            self.block("measured_runs_missing")
        ids, held, development, seen_data = set(), set(), set(), set()
        inference, evaluation, metrics = [], [], []
        for run in runs:
            _keys(run, "run_id split phase fluid_batch_id acquisition_start_utc acquisition_end_utc pressure_pa observation reference estimates reference_extra_uncertainty_k")
            run_id = _text(run["run_id"])
            if run_id in ids or run["split"] not in ("development", "held_out") or run["phase"] not in ("heating", "cooling", "hold"):
                raise ValueError("Run identity, split or phase invalid")
            ids.add(run_id)
            (held if run["split"] == "held_out" else development).add(run_id)
            _text(run["fluid_batch_id"])
            start, end = _time(run["acquisition_start_utc"]), _time(run["acquisition_end_utc"])
            if not 0 < (end - start).total_seconds() <= 86400:
                raise ValueError("Acquisition must span at most one day")
            if not plo <= _number(run["pressure_pa"]) <= phi:
                raise ValueError("Pressure outside declared fluid scope")
            _keys(run["reference_extra_uncertainty_k"], "installation timing dynamic")
            for value in run["reference_extra_uncertainty_k"].values():
                _number(value, 0, 100)
            raw = self.read(run["observation"])
            identity = _hash(raw)
            # Reused bytes cannot become a distinct held-out physical run by renaming.
            if identity in seen_data:
                raise ValueError("Observation bytes reused across runs")
            seen_data.add(identity)
            obs = _csv(raw)
            if not any(row["quality"] == "valid" for row in obs):
                self.block(run_id + ":no_valid_observation_samples")
            if obs[-1]["timestamp_s"] > (end-start).total_seconds():
                raise ValueError("Observation timestamps exceed acquisition interval")
            corrected = self.calibrated(obs, calibrations["observation"], run, "observation", scope)
            inference.append({"run_id": run_id, "split": run["split"], "observation_sha256": identity,
                              "observations": corrected if corrected is not None else obs})
            if run["split"] != "held_out":
                if run["reference"] is not None or run["estimates"] is not None:
                    raise ValueError("This profile evaluates reference targets only for held-out runs")
                continue
            if run["reference"] is None or run["estimates"] is None:
                self.block(run_id + ":held_out_reference_or_estimates_missing")
                continue
            refraw = self.read(run["reference"])
            reference_hash = _hash(refraw)
            if reference_hash in seen_data:
                raise ValueError("Reference bytes duplicate observation or another run")
            seen_data.add(reference_hash)
            refs = _csv(refraw)
            preds = _csv(self.read(run["estimates"]), prediction=True)
            times = [r["timestamp_s"] for r in obs]
            if times != [r["timestamp_s"] for r in refs] or times != [r["timestamp_s"] for r in preds]:
                raise ValueError("Require explicit common timestamps; no implicit alignment or interpolation")
            refs = self.calibrated(refs, calibrations["reference"], run, "reference", scope)
            evaluation.append({"run_id": run_id, "reference_sha256": reference_hash,
                               "reference": refs, "estimates": preds})
            if refs is not None:
                pairs = [(p["temperature_k"]-r["temperature_k"], r["standard_uncertainty_k"])
                         for p, r in zip(preds, refs) if r["quality"] == "valid"]
                if not pairs:
                    self.block(run_id + ":no_valid_reference_samples")
                    continue
                errors, uncertainty = zip(*pairs)
                metrics.append({"run_id": run_id, "phase": run["phase"], "sample_count": len(pairs),
                                "scored_fraction": len(pairs)/len(refs), "bias_k": sum(errors)/len(errors),
                                "rmse_k": math.sqrt(sum(e*e for e in errors)/len(errors)),
                                "max_abs_error_k": max(map(abs, errors)),
                                "max_reference_standard_uncertainty_k": max(uncertainty),
                                "reference_temperature_span_k": max(r["temperature_k"] for r in refs if r["quality"] == "valid")-min(r["temperature_k"] for r in refs if r["quality"] == "valid"),
                                "_pairs": pairs})
        for cal in calibrations.values():
            if cal is not None and _ids(cal["fit_run_ids"]) & held:
                raise ValueError("Held-out runs were used in a calibration fit")
        self.validate_policy(held, development, runs)
        decision = self.assess(metrics, held)
        for row in metrics:
            row.pop("_pairs")
        report = {"schema": "ciw.fluid-thermal-measurement-report.v1", "campaign_id": m["campaign_id"],
                  "manifest_sha256": _hash(self.raw_manifest), "evidence_kind": m["evidence_kind"],
                  "processing_status": "valid", "evidence_status": "blocked_missing_evidence" if self.blockers else "eligible_for_review",
                  "blockers": self.blockers, "criteria_result": decision, "physical_validation": "not_established",
                  "release_status": "unchanged", "per_run_metrics": metrics,
                  "held_out_run_count": len(held), "evaluated_run_count": len(metrics),
                  "evidence_authentication": "operator_assertions_and_local_hashes_only",
                  "uncertainty_scope": "pointwise_standard_uncertainty; shared calibration terms not reduced by sample count; no coverage or confidence claim",
                  "uncertainty_assumption": "computation assumes disjoint, mutually uncorrelated input, coefficient-fit, residual and extra components; see budget and blockers for evidence",
                  "input_hashes": sorted(self.retained)}
        return report, {"schema": "ciw.fluid-temperature-inference-input.v1", "runs": inference}, {"runs": evaluation}

    def validate_policy(self, held, development, runs):
        policy = self.manifest["held_out_policy"]
        if not held:
            self.block("held_out_runs_missing")
        if policy is None:
            self.block("held_out_policy_missing")
            return
        _keys(policy, "development_run_ids reserved_run_ids model_sha256 model_artifact prediction_sha256_by_run criteria_sha256 model_frozen_at_utc predictions_frozen_at_utc reference_unsealed_at_utc split_frozen_at_utc reference_used_for_fit independence_statement reviewed_by")
        if _ids(policy["reserved_run_ids"]) != held or _ids(policy["development_run_ids"]) != development:
            raise ValueError("Held-out partition does not match whole campaign runs")
        _digest(policy["model_sha256"])
        if policy["model_artifact"] is None:
            self.block("frozen_model_artifact_missing")
        elif _hash(self.read(policy["model_artifact"])) != policy["model_sha256"]:
            raise ValueError("Frozen model identity does not match retained artifact")
        prediction_hashes = policy["prediction_sha256_by_run"]
        actual_hashes = {r["run_id"]: r["estimates"]["sha256"] for r in runs
                         if r["split"] == "held_out" and r["estimates"] is not None}
        if type(prediction_hashes) is not dict or prediction_hashes != actual_hashes:
            raise ValueError("Frozen prediction identities do not match held-out estimate files")
        for value in prediction_hashes.values():
            _digest(value)
        if policy["criteria_sha256"] is None:
            self.block("frozen_criteria_identity_missing")
        elif _digest(policy["criteria_sha256"]) != _hash(_dump(self.manifest["criteria"])):
            raise ValueError("Frozen criteria identity does not match current criteria")
        model, predictions, unsealed, split = [_time(policy[key]) for key in ("model_frozen_at_utc", "predictions_frozen_at_utc", "reference_unsealed_at_utc", "split_frozen_at_utc")]
        if not split <= model <= predictions < unsealed:
            raise ValueError("Model and prediction freezes must precede reference access")
        if any(split >= _time(r["acquisition_start_utc"]) for r in runs if r["split"] == "held_out"):
            raise ValueError("Prospective held-out split must precede reserved acquisition")
        if any(model > _time(r["acquisition_start_utc"]) or predictions < _time(r["acquisition_end_utc"])
               for r in runs if r["split"] == "held_out"):
            raise ValueError("Freeze chronology does not bracket held-out acquisition")
        if type(policy["reference_used_for_fit"]) is not bool:
            raise ValueError("reference_used_for_fit must be boolean")
        if policy["reference_used_for_fit"]:
            raise ValueError("Reference target leakage invalidates held-out assessment")
        if not policy["independence_statement"] or not policy["reviewed_by"]:
            self.block("held_out_independence_review_missing")
        else:
            _text(policy["independence_statement"])
            _text(policy["reviewed_by"])

    def assess(self, metrics, held):
        criteria = self.manifest["criteria"]
        if criteria is None:
            self.block("agreed_criteria_missing")
            return "not_evaluated"
        _keys(criteria, "status approved_by approved_at_utc decision_rule coverage_factor min_held_out_runs required_phases min_scored_fraction min_temperature_span_k max_abs_bias_k max_rmse_k max_abs_error_k max_reference_expanded_uncertainty_k")
        if criteria["status"] not in ("draft", "agreed"):
            raise ValueError("Unsupported criteria status")
        if criteria["status"] == "draft":
            self.block("agreed_criteria_missing")
            return "not_evaluated"
        _text(criteria["approved_by"])
        approved = _time(criteria["approved_at_utc"])
        if any(approved >= _time(r["acquisition_start_utc"]) for r in self.manifest["runs"] if r["split"] == "held_out"):
            raise ValueError("Acceptance criteria must be agreed before confirmatory acquisition")
        policy = self.manifest["held_out_policy"]
        if policy is not None and approved >= _time(policy["reference_unsealed_at_utc"]):
            raise ValueError("Acceptance criteria must be agreed before opening held-out references")
        if criteria["decision_rule"] != "conservative_pointwise_reference_guard":
            raise ValueError("Unsupported uncertainty decision rule")
        k = _number(criteria["coverage_factor"], 1, 10)
        minimum = _integer(criteria["min_held_out_runs"], 1, 100)
        phases = _ids(criteria["required_phases"])
        if not phases or not phases <= {"heating", "cooling", "hold"}:
            raise ValueError("Require valid qualification phases")
        fraction = _number(criteria["min_scored_fraction"], .01, 1)
        for key in ("min_temperature_span_k", "max_abs_bias_k", "max_rmse_k", "max_abs_error_k", "max_reference_expanded_uncertainty_k"):
            _number(criteria[key], 0, 1000)
        if len(metrics) != len(held) or len(metrics) < minimum or not phases <= {row["phase"] for row in metrics}:
            self.block("required_held_out_run_or_condition_coverage_missing")
        for row in metrics:
            guards = [abs(e)+k*u for e, u in row["_pairs"]]
            row["guarded_max_abs_error_k"] = max(guards)
            row["guarded_rmse_k"] = math.sqrt(sum(g*g for g in guards)/len(guards))
            row["guarded_abs_bias_k"] = abs(row["bias_k"]) + k*sum(u for _, u in row["_pairs"])/len(guards)
            row["criteria_passed"] = (row["scored_fraction"] >= fraction and
                row["reference_temperature_span_k"] >= criteria["min_temperature_span_k"] and
                row["guarded_abs_bias_k"] <= criteria["max_abs_bias_k"] and
                row["guarded_rmse_k"] <= criteria["max_rmse_k"] and
                row["guarded_max_abs_error_k"] <= criteria["max_abs_error_k"] and
                k*row["max_reference_standard_uncertainty_k"] <= criteria["max_reference_expanded_uncertainty_k"])
        if self.blockers:
            return "not_evaluated"
        return "passed_declared_criteria" if all(r["criteria_passed"] for r in metrics) else "failed_declared_criteria"


def adapt_campaign(manifest_path, output_dir):
    """Validate completely before creating a new, never-overwritten bundle."""
    campaign = Campaign(manifest_path)
    report, inference, evaluation = campaign.evaluate()
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    (target / "raw").mkdir()
    (target / "campaign.json").write_bytes(campaign.raw_manifest)
    for identity, raw in campaign.retained.items():
        (target / "raw" / (identity.split(":")[1] + ".bin")).write_bytes(raw)
    # Publish the result last so an interrupted write cannot look complete.
    for name, data in (("inference-input.json", inference), ("reference-evaluation.json", evaluation), ("report.json", report)):
        (target / name).write_bytes(_dump(data))
    return report


def write_templates(output_dir):
    """Create an explicitly unqualified intake skeleton with no invented data."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=False)
    channel = lambda role: {"instrument_id": "TBD-"+role, "logger_id": "TBD-"+role, "location": "TBD-"+role, "calibration": None}
    manifest = {"schema": SCHEMA, "campaign_id": "water-pilot-draft", "evidence_kind": "measured",
        "scope": {"fluid": "water", "phase": "liquid", "measurand": "local_fluid_temperature", "installation_id": "TBD",
                  "mixing_description": "TBD; local probe temperature only", "min_temperature_k": 293.15,
                  "max_temperature_k": 333.15, "min_pressure_pa": 90000, "max_pressure_pa": 110000},
        "channels": {role: channel(role) for role in ("observation", "reference")}, "runs": [],
        "held_out_policy": None, "criteria": None, "uncertainty_budget": None}
    (root / "campaign.json").write_bytes(_dump(manifest))
    (root / "readings.template.csv").write_text(",".join(READING_COLUMNS)+"\n", encoding="utf-8")
    (root / "estimates.template.csv").write_text(",".join(ESTIMATE_COLUMNS)+"\n", encoding="utf-8")
    calibration = {
        "schema": CALIBRATION_SCHEMA, "calibration_id": None, "instrument_id": None,
        "logger_id": None, "input_unit": None, "input_min": None, "input_max": None,
        "scale": None, "offset_k": None, "coefficient_covariance": None,
        "residual_standard_uncertainty_k": None, "valid_from_utc": None,
        "valid_until_utc": None, "certificate": None, "traceability_statement": None,
        "fit_run_ids": []}
    run = {"run_id": None, "split": None, "phase": None, "fluid_batch_id": None,
           "acquisition_start_utc": None, "acquisition_end_utc": None, "pressure_pa": None,
           "observation": None, "reference": None, "estimates": None,
           "reference_extra_uncertainty_k": {"installation": None, "timing": None, "dynamic": None}}
    criteria = {"status": "draft", "approved_by": None, "approved_at_utc": None,
                "decision_rule": "conservative_pointwise_reference_guard", "coverage_factor": None,
                "min_held_out_runs": None, "required_phases": [], "min_scored_fraction": None,
                "min_temperature_span_k": None, "max_abs_bias_k": None, "max_rmse_k": None,
                "max_abs_error_k": None, "max_reference_expanded_uncertainty_k": None}
    policy = {"development_run_ids": [], "reserved_run_ids": [], "model_sha256": None,
              "model_artifact": None, "prediction_sha256_by_run": {}, "criteria_sha256": None,
              "split_frozen_at_utc": None, "model_frozen_at_utc": None,
              "predictions_frozen_at_utc": None, "reference_unsealed_at_utc": None,
              "reference_used_for_fit": None, "independence_statement": None, "reviewed_by": None}
    budget = {"assumption": None, "reviewed_by": None, "evidence": None}
    for name, value in (("calibration", calibration), ("run", run), ("criteria", criteria),
                        ("held-out-policy", policy), ("uncertainty-budget", budget)):
        (root / (name + ".template.json")).write_bytes(_dump(value))
    return root / "campaign.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create an empty, unapproved water pilot skeleton")
    init.add_argument("--output-dir", required=True)
    adapt = sub.add_parser("adapt", help="Retain evidence and assess declared held-out criteria")
    adapt.add_argument("--manifest", required=True)
    adapt.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(write_templates(args.output_dir))
            return 0
        report = adapt_campaign(args.manifest, args.output_dir)
        print(json.dumps(report, indent=2, allow_nan=False))
        return 0 if report["criteria_result"] == "passed_declared_criteria" else 2
    except (ValueError, OSError, KeyError, TypeError, OverflowError, csv.Error) as error:
        print(json.dumps({"status": "refused", "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
