"""Development scalar-readout calibration and held-out measurement campaigns.

Profiles preserve quantity and medium semantics. Nothing here actuates hardware,
implements a new estimator, authenticates evidence, or changes a release.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
from pathlib import Path
import sys

from .measurement_evidence import (EvidenceStore, decimal_float, digest, dump, hash_id, identifiers,
                                   integer, keys, load, number, text, utc)
from .physical_measurement_profiles import PROFILES, DEFERRED, get_profile, normalize

SCHEMA = "ciw.physical-measurements.v1"
CALIBRATION_SCHEMA = "ciw.scalar-readout-calibration.v1"
MAX_ROWS = 20000
READING_COLUMNS = ["timestamp_s", "value", "standard_uncertainty", "quality"]
ESTIMATE_COLUMNS = ["timestamp_s", "estimate"]
CHAIN_FIELDS = ("sensor_id", "instrument_id", "readout_id", "channel_id")


def read_csv(raw, estimates=False):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    columns = ESTIMATE_COLUMNS if estimates else READING_COLUMNS
    if reader.fieldnames != columns:
        raise ValueError("CSV requires exact ordered columns: " + ",".join(columns))
    rows = []
    previous = -1.0
    for record in reader:
        if len(rows) >= MAX_ROWS or set(record) != set(columns) or any(v is None for v in record.values()):
            raise ValueError("CSV row shape or size limit violated")
        timestamp = number(decimal_float(record["timestamp_s"]), 0, 86400)
        if timestamp <= previous:
            raise ValueError("Require strictly increasing timestamps")
        previous = timestamp
        if estimates:
            rows.append({"timestamp_s": timestamp, "estimate": number(decimal_float(record["estimate"]))})
            continue
        quality = record["quality"]
        if quality == "missing":
            if record["value"] or record["standard_uncertainty"]:
                raise ValueError("Missing reading must have empty value and uncertainty")
            value = uncertainty = None
        elif quality == "valid":
            value = number(decimal_float(record["value"]))
            uncertainty = number(decimal_float(record["standard_uncertainty"]), 0)
        else:
            raise ValueError("Unsupported reading quality")
        rows.append({"timestamp_s": timestamp, "raw_value": value,
                     "raw_standard_uncertainty": uncertainty, "quality": quality})
    if not rows:
        raise ValueError("A measurement CSV must contain readings")
    return rows


def covariance(value):
    if type(value) is not list or len(value) != 2 or any(type(r) is not list or len(r) != 2 for r in value):
        raise ValueError("Require a 2x2 coefficient covariance")
    a, b, c, d = [number(x) for r in value for x in r]
    if b != c or a < 0 or d < 0 or abs(b) > math.sqrt(a)*math.sqrt(d):
        raise ValueError("Coefficient covariance must be symmetric positive semidefinite")
    return [[a, b], [c, d]]


def rms(values):
    return math.hypot(*values)/math.sqrt(len(values))


class MeasurementCampaign(EvidenceStore):
    def scope(self):
        scope = self.manifest["scope"]
        keys(scope, "medium minimum maximum resolution sampling_interval_s bandwidth_hz conditions instrument_specification")
        if scope["medium"] is None:
            self.block("medium_unspecified")
        elif scope["medium"] not in self.profile["media"]:
            raise ValueError("Medium is not supported by the selected quantity profile")
        for name in ("minimum", "maximum", "resolution", "sampling_interval_s", "bandwidth_hz"):
            if scope[name] is None:
                self.block("specification_missing:" + name)
            else:
                number(scope[name])
        if scope["minimum"] is not None:
            normalize(self.profile_id, self.unit, scope["minimum"], 0)
        if scope["maximum"] is not None:
            normalize(self.profile_id, self.unit, scope["maximum"], 0)
        if scope["minimum"] is not None and scope["maximum"] is not None and scope["minimum"] >= scope["maximum"]:
            raise ValueError("Declared operating range must increase")
        for name in ("resolution", "sampling_interval_s", "bandwidth_hz"):
            if scope[name] is not None and scope[name] <= 0:
                raise ValueError("Resolution, sampling interval and bandwidth must be positive")
        if scope["sampling_interval_s"] is not None:
            number(scope["sampling_interval_s"], 1e-6, 86400)
        if scope["instrument_specification"] is None:
            self.block("instrument_specification_evidence_missing")
        else:
            self.read(scope["instrument_specification"])
        keys(scope["conditions"], " ".join(self.profile["condition_fields"]))
        for field, value in scope["conditions"].items():
            if value is None:
                self.block("condition_unspecified:" + field)
            else:
                text(value)
                allowed = self.profile.get("condition_allowed_values", {}).get(field)
                if allowed is not None and value not in allowed:
                    raise ValueError("Unsupported condition value: " + field)
        return scope

    def calibration(self, role):
        channel = self.manifest["channels"][role]
        keys(channel, "sensor_id instrument_id readout_id channel_id unit calibration")
        for field in CHAIN_FIELDS:
            if channel[field] is None:
                self.block(role + ":identity_missing:" + field)
            else:
                text(channel[field])
        if channel["unit"] is None:
            self.block(role + ":readout_unit_missing")
        elif channel["unit"] not in self.profile["units"]:
            raise ValueError("Channel unit is incompatible with the selected measurand")
        if channel["calibration"] is None:
            self.block(role + ":calibration_missing")
            return None
        cal = load(self.read(channel["calibration"]))
        keys(cal, "schema calibration_id profile_id sensor_id instrument_id readout_id channel_id input_unit canonical_unit scope_sha256 model input_min input_max scale offset coefficient_covariance residual_standard_uncertainty valid_from_utc valid_until_utc validity_policy certificate traceability_statement fit_run_ids")
        if cal["schema"] != CALIBRATION_SCHEMA or cal["profile_id"] != self.profile_id or cal["canonical_unit"] != self.unit:
            raise ValueError("Calibration quantity/profile binding differs")
        if cal["model"] != "affine_canonical_readout":
            raise ValueError("This adapter requires a justified affine engineering-readout calibration")
        text(cal["calibration_id"])
        if any(cal[field] is None or cal[field] != channel[field] for field in CHAIN_FIELDS):
            raise ValueError("Calibration does not match the complete sensor/readout/channel chain")
        if cal["input_unit"] != channel["unit"] or cal["input_unit"] not in self.profile["units"]:
            raise ValueError("Calibration readout unit differs")
        if hash_id(cal["scope_sha256"]) != digest(dump(self.manifest["scope"])):
            raise ValueError("Calibration does not cover this exact scope and conditions")
        if number(cal["input_min"]) >= number(cal["input_max"]):
            raise ValueError("Calibration input range must increase")
        if number(cal["scale"]) == 0:
            raise ValueError("Zero calibration scale is unsupported")
        number(cal["offset"])
        covariance(cal["coefficient_covariance"])
        number(cal["residual_standard_uncertainty"], 0)
        if utc(cal["valid_from_utc"]) >= utc(cal["valid_until_utc"]):
            raise ValueError("Invalid calibration applicability dates")
        identifiers(cal["fit_run_ids"])
        for field in ("certificate", "validity_policy"):
            if cal[field] is None:
                self.block(role + ":" + field + "_missing")
            else:
                self.read(cal[field])
        if cal["traceability_statement"] is None:
            self.block(role + ":traceability_statement_missing")
        else:
            text(cal["traceability_statement"])
        return cal

    def calibrate(self, rows, cal, run, role):
        channel = self.manifest["channels"][role]
        if cal is None:
            return {"calibration_status": "not_applied", "input_unit": channel["unit"], "rows": rows}
        if utc(run["acquisition_start_utc"]) < utc(cal["valid_from_utc"]) or utc(run["acquisition_end_utc"]) > utc(cal["valid_until_utc"]):
            raise ValueError("Calibration is not applicable for the complete acquisition")
        result = []
        for row in rows:
            output = dict(row)
            if row["quality"] == "missing":
                output.update(value=None, standard_uncertainty=None)
            else:
                raw = row["raw_value"]
                if not cal["input_min"] <= raw <= cal["input_max"]:
                    raise ValueError("Readout exceeds calibration applicability range")
                x, u = normalize(self.profile_id, channel["unit"], raw, row["raw_standard_uncertainty"])
                c = cal["coefficient_covariance"]
                coefficient_variance = number(x*x*c[0][0]+2*x*c[0][1]+c[1][1])
                if coefficient_variance < 0:
                    # Roundoff is admitted only relative to the contributing terms.
                    bound = 1e-14*(abs(x*x*c[0][0])+abs(2*x*c[0][1])+abs(c[1][1]))
                    if coefficient_variance < -bound:
                        raise ValueError("Negative propagated coefficient variance")
                    coefficient_variance = 0.0
                components = [cal["scale"]*u, math.sqrt(coefficient_variance), cal["residual_standard_uncertainty"]]
                if role == "reference":
                    components.extend(run["reference_extra_uncertainty"].values())
                y, uncertainty = normalize(self.profile_id, self.unit,
                                            number(cal["scale"]*x+cal["offset"]), math.hypot(*components))
                lo, hi = self.manifest["scope"]["minimum"], self.manifest["scope"]["maximum"]
                if (lo is not None and y < lo) or (hi is not None and y > hi):
                    raise ValueError("Corrected measurement exceeds declared operating range")
                output.update(value=y, standard_uncertainty=uncertainty)
            result.append(output)
        return {"calibration_status": "applied", "input_unit": channel["unit"],
                "unit": self.unit, "calibration_id": cal["calibration_id"], "rows": result}

    def evaluate(self):
        m = self.manifest
        keys(m, "schema campaign_id profile_id evidence_kind scope channels runs uncertainty_budget held_out_policy criteria")
        if m["schema"] != SCHEMA or m["evidence_kind"] not in ("measured", "synthetic"):
            raise ValueError("Unsupported measurement campaign")
        text(m["campaign_id"])
        self.profile_id = m["profile_id"]
        self.profile = get_profile(self.profile_id)
        self.unit = self.profile["canonical_unit"]
        if m["evidence_kind"] != "measured":
            self.block("synthetic_data_cannot_establish_physical_validation")
        scope = self.scope()
        budget = m["uncertainty_budget"]
        if budget is None:
            self.block("reviewed_uncertainty_budget_missing")
        else:
            keys(budget, "assumption reviewed_by evidence")
            if budget["assumption"] != "independent_disjoint_components":
                self.block("unsupported_cross_component_covariance")
            if budget["reviewed_by"] is None or budget["evidence"] is None:
                self.block("reviewed_uncertainty_budget_missing")
            else:
                text(budget["reviewed_by"])
                self.read(budget["evidence"])
        keys(m["channels"], "observation reference")
        calibrations = {role: self.calibration(role) for role in ("observation", "reference")}
        for field in ("sensor_id", "instrument_id", "readout_id"):
            left, right = (m["channels"][r][field] for r in ("observation", "reference"))
            if left is not None and left == right:
                self.block("independent_reference_chain_required:" + field)
        runs = m["runs"]
        if type(runs) is not list or len(runs) > 100:
            raise ValueError("Require at most 100 complete runs")
        if not runs:
            self.block("measurement_runs_missing")
        ids, held, development, seen = set(), set(), set(), set()
        inference, references, metrics = [], [], []
        for run in runs:
            keys(run, "run_id split condition_label specimen_id acquisition_start_utc acquisition_end_utc observation reference estimates reference_extra_uncertainty")
            run_id = text(run["run_id"])
            if run_id in ids or run["split"] not in ("development", "held_out"):
                raise ValueError("Duplicate run or invalid partition")
            ids.add(run_id)
            (held if run["split"] == "held_out" else development).add(run_id)
            text(run["condition_label"])
            text(run["specimen_id"])
            duration = (utc(run["acquisition_end_utc"])-utc(run["acquisition_start_utc"])).total_seconds()
            if not 0 < duration <= 86400:
                raise ValueError("Acquisition must span 0..86400 seconds")
            keys(run["reference_extra_uncertainty"], "placement timing environment other")
            for value in run["reference_extra_uncertainty"].values():
                number(value, 0)
            observed_raw = self.read(run["observation"])
            observed_hash = digest(observed_raw)
            if observed_hash in seen:
                raise ValueError("Observation bytes reused across independent runs")
            seen.add(observed_hash)
            obs = read_csv(observed_raw)
            if obs[-1]["timestamp_s"] > duration:
                raise ValueError("Observations exceed acquisition interval")
            interval = scope["sampling_interval_s"]
            if interval is not None:
                steps = duration/interval
                if not math.isclose(steps, round(steps), rel_tol=1e-10, abs_tol=1e-9) or not 1 <= round(steps) < MAX_ROWS:
                    raise ValueError("Acquisition duration must declare an endpoint-inclusive supported sample grid")
                if len(obs) != round(steps)+1 or any(not math.isclose(row["timestamp_s"], i*interval, rel_tol=1e-8, abs_tol=1e-9) for i,row in enumerate(obs)):
                    raise ValueError("Sample grid must cover both acquisition endpoints; retain explicit missing rows")
            observed_fraction = sum(r["quality"] == "valid" for r in obs)/len(obs)
            if observed_fraction == 0:
                self.block(run_id+":no_valid_observations")
            corrected = self.calibrate(obs, calibrations["observation"], run, "observation")
            inference.append({"run_id": run_id, "split": run["split"], "source_sha256": observed_hash, **corrected})
            if run["split"] == "development":
                if run["reference"] is not None or run["estimates"] is not None:
                    raise ValueError("Development targets must remain outside the held-out evaluator")
                continue
            if run["reference"] is None or run["estimates"] is None:
                self.block(run_id+":reference_or_estimates_missing")
                continue
            refraw = self.read(run["reference"])
            reference_hash = digest(refraw)
            if reference_hash in seen:
                raise ValueError("Reference bytes duplicate an observation or another reference")
            seen.add(reference_hash)
            ref = read_csv(refraw)
            predictions = read_csv(self.read(run["estimates"]), estimates=True)
            for prediction in predictions:
                normalize(self.profile_id, self.unit, prediction["estimate"], 0)
            times = [r["timestamp_s"] for r in obs]
            if times != [r["timestamp_s"] for r in ref] or times != [r["timestamp_s"] for r in predictions]:
                raise ValueError("Require exact timestamp alignment; interpolation is not implicit")
            corrected_ref = self.calibrate(ref, calibrations["reference"], run, "reference")
            references.append({"run_id": run_id, "source_sha256": reference_hash,
                               "reference": corrected_ref, "estimates": predictions, "estimate_unit": self.unit})
            if corrected_ref["calibration_status"] != "applied":
                continue
            pairs = [(p["estimate"]-r["value"], r["standard_uncertainty"], r["value"])
                     for p,r in zip(predictions,corrected_ref["rows"]) if r["quality"] == "valid"]
            if not pairs:
                self.block(run_id+":no_valid_reference")
                continue
            errors = [number(e) for e,_,_ in pairs]
            metrics.append({"run_id":run_id,"condition_label":run["condition_label"],
                            "sample_count":len(pairs),"observed_fraction":observed_fraction,
                            "reference_fraction":len(pairs)/len(ref),"bias":number(math.fsum(e/len(errors) for e in errors)),
                            "rmse":rms(errors),"max_abs_error":max(map(abs,errors)),
                            "reference_span":number(max(v for _,_,v in pairs)-min(v for _,_,v in pairs)),
                            "max_reference_standard_uncertainty":max(u for _,u,_ in pairs),"_pairs":pairs})
        for cal in calibrations.values():
            if cal is not None and identifiers(cal["fit_run_ids"]) & held:
                raise ValueError("Held-out run was used to fit calibration")
        self.policy(held,development,runs)
        result = self.assess(metrics,held)
        for metric in metrics:
            metric.pop("_pairs")
        report = {"schema":"ciw.physical-measurement-report.v1","campaign_id":m["campaign_id"],
                  "profile_id":self.profile_id,"quantity":self.profile["quantity"],"unit":self.unit,
                  "medium":scope["medium"],"manifest_sha256":digest(self.raw_manifest),
                  "evidence_kind":m["evidence_kind"],"processing_status":"valid",
                  "evidence_status":"blocked_missing_evidence" if self.blockers else "eligible_for_review",
                  "blockers":self.blockers,"criteria_result":result,"per_run_metrics":metrics,
                  "held_out_run_count":len(held),"evaluated_run_count":len(metrics),
                  "physical_validation":"not_established","release_status":"unchanged",
                  "evidence_authentication":"operator_assertions_and_local_hashes_only",
                  "uncertainty_scope":"pointwise affine readout; no confidence/coverage claim; shared calibration uncertainty is not reduced by row count",
                  "input_hashes":sorted(self.retained)}
        return report, {"schema":"ciw.physical-inference-input.v1","profile_id":self.profile_id,"runs":inference}, {"runs":references}

    def policy(self, held, development, runs):
        policy = self.manifest["held_out_policy"]
        if not held:
            self.block("held_out_runs_missing")
        if policy is None:
            self.block("held_out_policy_missing")
            return
        keys(policy,"split_unit development_run_ids reserved_run_ids model_artifact prediction_sha256_by_run criteria_sha256 split_frozen_at_utc model_frozen_at_utc predictions_frozen_at_utc reference_unsealed_at_utc reference_used_for_fit independence_statement reviewed_by")
        if policy["split_unit"] not in ("run", "specimen"):
            raise ValueError("Declare the run or specimen holdout unit")
        if policy["split_unit"] == "specimen":
            training = {r["specimen_id"] for r in runs if r["split"] == "development"}
            validation = {r["specimen_id"] for r in runs if r["split"] == "held_out"}
            if training & validation:
                raise ValueError("Specimen appears in both development and held-out partitions")
        if identifiers(policy["development_run_ids"]) != development or identifiers(policy["reserved_run_ids"]) != held:
            raise ValueError("Frozen partition differs from campaign whole runs")
        if policy["model_artifact"] is None:
            self.block("frozen_model_artifact_missing")
        else:
            self.read(policy["model_artifact"])
        expected = {r["run_id"]:r["estimates"]["sha256"] for r in runs if r["split"] == "held_out" and r["estimates"] is not None}
        if type(policy["prediction_sha256_by_run"]) is not dict or policy["prediction_sha256_by_run"] != expected:
            raise ValueError("Frozen predictions differ from supplied estimates")
        if policy["criteria_sha256"] is None:
            self.block("frozen_criteria_identity_missing")
        elif hash_id(policy["criteria_sha256"]) != digest(dump(self.manifest["criteria"])):
            raise ValueError("Frozen criteria differ from current decision rules")
        split, model, predictions, unsealed = [utc(policy[f]) for f in ("split_frozen_at_utc","model_frozen_at_utc","predictions_frozen_at_utc","reference_unsealed_at_utc")]
        if not split <= model <= predictions < unsealed:
            raise ValueError("Invalid freeze chronology")
        for run in runs:
            if run["split"] == "held_out" and (split >= utc(run["acquisition_start_utc"]) or model > utc(run["acquisition_start_utc"]) or predictions < utc(run["acquisition_end_utc"])):
                raise ValueError("Freezes do not bracket confirmatory acquisition")
        if policy["reference_used_for_fit"] is not False:
            raise ValueError("Held-out targets may not be used in model fitting")
        if policy["independence_statement"] is None or policy["reviewed_by"] is None:
            self.block("held_out_independence_review_missing")
        else:
            text(policy["independence_statement"])
            text(policy["reviewed_by"])

    def assess(self, metrics, held):
        c = self.manifest["criteria"]
        if c is None:
            self.block("agreed_criteria_missing")
            return "not_evaluated"
        keys(c,"status unit approved_by approved_at_utc decision_rule coverage_factor min_held_out_runs required_condition_labels min_observed_fraction min_reference_fraction min_reference_span max_abs_bias max_rmse max_abs_error max_reference_expanded_uncertainty")
        if c["status"] not in ("draft","agreed") or c["unit"] != self.unit:
            raise ValueError("Criteria state or quantity unit is invalid")
        if c["status"] == "draft":
            self.block("agreed_criteria_missing")
            return "not_evaluated"
        text(c["approved_by"])
        approved = utc(c["approved_at_utc"])
        if any(approved >= utc(r["acquisition_start_utc"]) for r in self.manifest["runs"] if r["split"] == "held_out"):
            raise ValueError("Criteria must precede confirmatory acquisition")
        if c["decision_rule"] != "conservative_pointwise_reference_guard":
            raise ValueError("Unsupported decision rule")
        k = number(c["coverage_factor"],1,10)
        minimum = integer(c["min_held_out_runs"],1,100)
        labels = identifiers(c["required_condition_labels"])
        if not labels:
            raise ValueError("Declare required qualification conditions")
        for key in ("min_observed_fraction","min_reference_fraction"):
            number(c[key],.01,1)
        for key in ("min_reference_span","max_abs_bias","max_rmse","max_abs_error","max_reference_expanded_uncertainty"):
            number(c[key],0)
        if len(metrics) != len(held) or len(metrics) < minimum or not labels <= {r["condition_label"] for r in metrics}:
            self.block("required_run_or_condition_coverage_missing")
        for r in metrics:
            pairs = r["_pairs"]
            guarded = [number(abs(e)+k*u) for e,u,_ in pairs]
            r["guarded_max_abs_error"] = max(guarded)
            r["guarded_rmse"] = rms(guarded)
            r["guarded_abs_bias"] = number(abs(r["bias"])+k*math.fsum(u/len(pairs) for _,u,_ in pairs))
            r["declared_limits_satisfied"] = (r["observed_fraction"] >= c["min_observed_fraction"] and
                r["reference_fraction"] >= c["min_reference_fraction"] and r["reference_span"] >= c["min_reference_span"] and
                r["guarded_abs_bias"] <= c["max_abs_bias"] and r["guarded_rmse"] <= c["max_rmse"] and
                r["guarded_max_abs_error"] <= c["max_abs_error"] and
                number(k*r["max_reference_standard_uncertainty"]) <= c["max_reference_expanded_uncertainty"])
        if self.blockers:
            return "not_evaluated"
        return "passed_declared_criteria" if all(r["declared_limits_satisfied"] for r in metrics) else "failed_declared_criteria"


def adapt_campaign(manifest_path, output_dir):
    campaign = MeasurementCampaign(manifest_path)
    report,inference,reference = campaign.evaluate()
    return campaign.retain_bundle(output_dir,report,inference,reference)


def write_templates(output_dir, profile_id, medium=None):
    profile = get_profile(profile_id)
    if medium is not None and medium not in profile["media"]:
        raise ValueError("Unsupported medium")
    root = Path(output_dir)
    root.mkdir(parents=True,exist_ok=False)
    channel = {**{f:None for f in CHAIN_FIELDS},"unit":None,"calibration":None}
    manifest = {"schema":SCHEMA,"campaign_id":"draft-"+profile_id,"profile_id":profile_id,"evidence_kind":"measured",
                "scope":{"medium":medium,"minimum":None,"maximum":None,"resolution":None,"sampling_interval_s":None,"bandwidth_hz":None,
                         "conditions":{f:None for f in profile["condition_fields"]},"instrument_specification":None},
                "channels":{"observation":channel.copy(),"reference":channel.copy()},"runs":[],"uncertainty_budget":None,"held_out_policy":None,"criteria":None}
    cal = {"schema":CALIBRATION_SCHEMA,"calibration_id":None,"profile_id":profile_id,**{f:None for f in CHAIN_FIELDS},
           "input_unit":None,"canonical_unit":profile["canonical_unit"],"scope_sha256":None,"model":"affine_canonical_readout",
           "input_min":None,"input_max":None,"scale":None,"offset":None,"coefficient_covariance":None,
           "residual_standard_uncertainty":None,"valid_from_utc":None,"valid_until_utc":None,"validity_policy":None,
           "certificate":None,"traceability_statement":None,"fit_run_ids":[]}
    run = {"run_id":None,"split":None,"condition_label":None,"specimen_id":None,"acquisition_start_utc":None,
           "acquisition_end_utc":None,"observation":None,"reference":None,"estimates":None,
           "reference_extra_uncertainty":{k:None for k in ("placement","timing","environment","other")}}
    criteria = {"status":"draft","unit":profile["canonical_unit"],"approved_by":None,"approved_at_utc":None,
                "decision_rule":"conservative_pointwise_reference_guard","coverage_factor":None,"min_held_out_runs":None,
                "required_condition_labels":[],"min_observed_fraction":None,"min_reference_fraction":None,
                "min_reference_span":None,"max_abs_bias":None,"max_rmse":None,"max_abs_error":None,"max_reference_expanded_uncertainty":None}
    policy = {"split_unit":None,"development_run_ids":[],"reserved_run_ids":[],"model_artifact":None,"prediction_sha256_by_run":{},"criteria_sha256":None,
              "split_frozen_at_utc":None,"model_frozen_at_utc":None,"predictions_frozen_at_utc":None,"reference_unsealed_at_utc":None,
              "reference_used_for_fit":None,"independence_statement":None,"reviewed_by":None}
    for filename,value in (("campaign.json",manifest),("profile.json",profile),("calibration.template.json",cal),
                           ("run.template.json",run),("criteria.template.json",criteria),("held-out-policy.template.json",policy),
                           ("uncertainty-budget.template.json",{"assumption":None,"reviewed_by":None,"evidence":None})):
        (root/filename).write_bytes(dump(value))
    (root/"readings.template.csv").write_text(",".join(READING_COLUMNS)+"\n",encoding="utf-8")
    (root/"estimates.template.csv").write_text(",".join(ESTIMATE_COLUMNS)+"\n",encoding="utf-8")
    return root/"campaign.json"



def write_specialized_template(output_dir, family_id):
    """Retain an unfilled specification request; no generic numeric evaluator."""
    if family_id not in DEFERRED:
        raise ValueError("Unknown specialized measurement family")
    root = Path(output_dir)
    root.mkdir(parents=True,exist_ok=False)
    request = {"schema":"ciw.specialized-measurement-intake-draft.v1","family_id":family_id,
               "status":"specialized_adapter_required","family":DEFERRED[family_id],
               "instrument":{"manufacturer":None,"model":None,"sensor_id":None,"readout_id":None,"channel_id":None},
               "specification":{"measurand":None,"unit":None,"medium":None,"operating_range":None,
                                "resolution":None,"sampling":None,"bandwidth":None,"reference_conditions":None,
                                "required_accuracy":None,"maximum_uncertainty":None},
               "raw_exports":[],"calibration_evidence":[],"independent_reference_evidence":[],
               "held_out_policy":None,"agreed_criteria":None,
               "physical_validation":"not_established","release_status":"unchanged"}
    (root/"intake-draft.json").write_bytes(dump(request))
    return root/"intake-draft.json"

def catalog():
    return {"schema":"ciw.physical-measurement-profile-catalog.v1","profiles":PROFILES,"deferred":DEFERRED,
            "hardware_inventory":"not_supplied","physical_validation":"not_established","release_status":"unchanged"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command",required=True)
    sub.add_parser("catalog")
    init = sub.add_parser("init")
    init.add_argument("--profile",required=True)
    init.add_argument("--medium")
    init.add_argument("--output-dir",required=True)
    all_init = sub.add_parser("init-all")
    all_init.add_argument("--output-dir",required=True)
    adapt = sub.add_parser("adapt")
    adapt.add_argument("--manifest",required=True)
    adapt.add_argument("--output-dir",required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "catalog":
            print(dump(catalog()).decode())
            return 0
        if args.command == "init":
            print(write_templates(args.output_dir,args.profile,args.medium))
            return 0
        if args.command == "init-all":
            root = Path(args.output_dir)
            root.mkdir(parents=True,exist_ok=False)
            for profile_id in PROFILES:
                write_templates(root/profile_id,profile_id)
            for family_id in DEFERRED:
                write_specialized_template(root/"specialized"/family_id,family_id)
            (root/"catalog.json").write_bytes(dump(catalog()))
            print(root)
            return 0
        report = adapt_campaign(args.manifest,args.output_dir)
        print(dump(report).decode())
        return 0 if report["criteria_result"] == "passed_declared_criteria" else 2
    except (ValueError,OSError,KeyError,TypeError,OverflowError,csv.Error) as error:
        print(json.dumps({"status":"refused","error":str(error)}),file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
