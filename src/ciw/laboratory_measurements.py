"""Development-only comparison of already reduced laboratory method results.

No raw-signal calibration, correction, method approval or release qualification
occurs here. Original reports and evidence are retained byte for byte.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
from pathlib import Path
import sys

from .laboratory_profiles import PROFILES, DEFERRED, get_profile, normalize
from .measurement_evidence import (EvidenceStore, keys, text, number, integer,
    utc, identifiers, digest, hash_id, dump, decimal_float)

SCHEMA = "ciw.laboratory-measurements.v1"
COLUMNS = ("result_id", "sample_id", "parent_sample_id", "lot_id", "preparation_id",
           "run_id", "replicate_id", "measured_at_utc", "value", "standard_uncertainty",
           "qualifier", "detection_limit", "quantification_limit", "reference_material_id")
ROLE_FIELDS = ("evidence_type laboratory_id method_id method_revision instrument_ids unit scope_sha256 "
               "results raw_evidence calibration_evidence method_validation_evidence qc_evidence "
               "preparation_evidence reporting_stage valid_from_utc valid_until_utc "
               "calibration_material_ids calibration_sample_ids calibration_lot_ids reference_certificate")
QUALIFIERS = {"quantified", "below_detection", "below_quantification", "missing", "invalid", "out_of_range"}
CRITERIA_FIELDS = ("status unit approved_by approved_at_utc decision_rule coverage_factor min_held_out_groups "
                   "min_quantified_fraction min_reference_span max_abs_bias max_rmse max_abs_error "
                   "max_combined_expanded_uncertainty")
POLICY_FIELDS = ("split_unit development_group_ids reserved_group_ids method_artifact candidate_results_sha256 "
                 "reference_results_sha256 criteria_sha256 split_frozen_at_utc method_frozen_at_utc "
                 "candidate_results_frozen_at_utc reference_unsealed_at_utc reference_used_for_development "
                 "independence_evidence reviewed_by")


class LaboratoryCampaign(EvidenceStore):
    def required_evidence(self, descriptor, label):
        if descriptor is None:
            self.block(label + " is missing")
        else:
            self.read(descriptor)

    def required_text(self, value, label):
        if value is None:
            self.block(label + " is missing")
        else:
            text(value)

    def scope(self):
        scope = self.manifest["scope"]
        keys(scope, "target_identity matrix reporting_basis minimum maximum conditions method_scope_evidence")
        for field in ("target_identity", "matrix", "reporting_basis"):
            self.required_text(scope[field], "scope." + field)
        keys(scope["conditions"], " ".join(self.profile["condition_fields"]))
        for field, value in scope["conditions"].items():
            self.required_text(value, "scope.conditions." + field)
            allowed = self.profile.get("condition_allowed_values", {}).get(field)
            if value is not None and allowed and value not in allowed:
                raise ValueError("Unsupported condition: " + field)
        self.required_evidence(scope["method_scope_evidence"], "method scope evidence")
        self.bounds = None
        if scope["minimum"] is None or scope["maximum"] is None:
            self.block("Method reporting range is missing")
        else:
            low, _ = normalize(self.profile_id, self.unit, scope["minimum"], 0)
            high, _ = normalize(self.profile_id, self.unit, scope["maximum"], 0)
            if high <= low:
                raise ValueError("Method reporting range must have positive span")
            self.bounds = low, high
        return digest(dump(scope))

    def role(self, name, scope_hash):
        role = self.manifest[name]
        keys(role, ROLE_FIELDS)
        if role["evidence_type"] not in ("method_report", "crm_assigned_value"):
            raise ValueError("Unsupported evidence type")
        crm = role["evidence_type"] == "crm_assigned_value"
        if name == "candidate" and crm:
            raise ValueError("Candidate must be a measured method report")
        for field in ("laboratory_id", "method_id", "method_revision"):
            self.required_text(role[field], name + "." + field)
        instruments = identifiers(role["instrument_ids"])
        if not instruments and not crm:
            self.block(name + " instrument identities are missing")
        for field in ("calibration_material_ids", "calibration_sample_ids", "calibration_lot_ids"):
            identifiers(role[field])
        if role["unit"] is None:
            self.block(name + " reporting unit is missing")
        elif role["unit"] not in self.profile["units"]:
            raise ValueError("Unsupported reporting unit")
        if role["scope_sha256"] is None:
            self.block(name + " scope binding is missing")
        elif hash_id(role["scope_sha256"]) != scope_hash:
            raise ValueError(name + " scope binding mismatch")
        expected_stage = "certified_assigned_value" if crm else "final_corrected_method_result"
        if role["reporting_stage"] != expected_stage:
            raise ValueError("Upstream corrections or certificate interpretation must already be complete")
        for field in ("raw_evidence", "calibration_evidence"):
            if type(role[field]) is not list:
                raise ValueError("Evidence collections must be lists")
            for item in role[field]:
                self.read(item)
            if not role[field] and not crm:
                self.block(name + " " + field + " is missing")
        for field in ("method_validation_evidence", "qc_evidence", "preparation_evidence"):
            if not crm or role[field] is not None:
                self.required_evidence(role[field], name + " " + field)
        if crm or role["reference_certificate"] is not None:
            self.required_evidence(role["reference_certificate"], name + " reference certificate")
        interval = None
        if role["valid_from_utc"] is None or role["valid_until_utc"] is None:
            self.block(name + " method/certificate applicability dates are missing")
        else:
            interval = utc(role["valid_from_utc"]), utc(role["valid_until_utc"])
            if interval[0] > interval[1]:
                raise ValueError("Reversed applicability interval")
        if role["results"] is None:
            self.block(name + " results are missing")
            return {}
        raw = self.read(role["results"])
        if role["unit"] is None:
            raise ValueError("Results require a declared unit")
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        if reader.fieldnames != list(COLUMNS):
            raise ValueError("Results require exact ordered CSV headers")
        rows = {}
        for source in reader:
            if len(rows) >= 100000 or None in source or None in source.values():
                raise ValueError("Malformed or oversized result table")
            for field in COLUMNS[:8]:
                text(source[field])
            result_id = source["result_id"]
            if result_id in rows:
                raise ValueError("Repeated result identity")
            timestamp = utc(source["measured_at_utc"])
            if interval and not interval[0] <= timestamp <= interval[1]:
                raise ValueError("Result is outside declared method/certificate applicability")
            qualifier = source["qualifier"]
            if qualifier not in QUALIFIERS:
                raise ValueError("Unknown result qualifier")
            if crm and not source["reference_material_id"]:
                raise ValueError("CRM result requires a material identity")
            if source["reference_material_id"]:
                text(source["reference_material_id"])
            row = dict(source)
            for field in ("detection_limit", "quantification_limit"):
                token = source[field]
                if token and not self.profile["censoring_supported"]:
                    raise ValueError("Detection-limit semantics require a concentration or fraction profile")
                row[field] = normalize(self.profile_id, role["unit"], decimal_float(token), 0)[0] if token else None
                if row[field] is not None and row[field] <= 0:
                    raise ValueError("Detection and quantification limits must be positive")
            lod, loq = row["detection_limit"], row["quantification_limit"]
            if lod is not None and loq is not None and lod > loq:
                raise ValueError("Detection limit exceeds quantification limit")
            if qualifier == "quantified":
                value, uncertainty = normalize(self.profile_id, role["unit"],
                    decimal_float(source["value"]), decimal_float(source["standard_uncertainty"]))
                if self.bounds and not self.bounds[0] <= value <= self.bounds[1]:
                    raise ValueError("Quantified value exceeds method reporting range")
                if (loq is not None and value < loq) or (lod is not None and value < lod):
                    raise ValueError("Quantified value is below its declared limit")
                row["value"], row["standard_uncertainty"] = value, uncertainty
            else:
                if source["value"] or source["standard_uncertainty"]:
                    raise ValueError("Unquantified rows must not substitute numeric values")
                if qualifier.startswith("below_"):
                    if not self.profile["censoring_supported"]:
                        raise ValueError("Censoring is unsupported for this profile")
                    if qualifier == "below_detection" and lod is None:
                        raise ValueError("Below-detection result requires a detection limit")
                    if qualifier == "below_quantification" and loq is None:
                        raise ValueError("Below-quantification result requires a quantification limit")
                row["value"], row["standard_uncertainty"] = None, None
            rows[result_id] = row
        if not rows:
            self.block(name + " result table is empty")
        return rows

    def reviews(self):
        review = self.manifest["comparability_review"]
        if review is None:
            self.block("Method comparability and specimen pairing review is missing")
        else:
            keys(review, "reviewed_by evidence same_measurand compatible_methods valid_specimen_pairing")
            text(review["reviewed_by"])
            self.read(review["evidence"])
            if any(review[f] is not True for f in ("same_measurand", "compatible_methods", "valid_specimen_pairing")):
                raise ValueError("Methods and paired specimens must be reviewed as comparable")
        budget = self.manifest["uncertainty_budget"]
        if budget is None:
            self.block("Reviewed independent candidate/reference uncertainty budget is missing")
        else:
            keys(budget, "assumption reviewed_by evidence")
            if budget["assumption"] != "independent_candidate_reference":
                raise ValueError("Correlated or shared uncertainty needs a specialized covariance model")
            text(budget["reviewed_by"])
            self.read(budget["evidence"])
        a, b = self.manifest["candidate"], self.manifest["reference"]
        if a["results"] and b["results"] and a["results"]["sha256"] == b["results"]["sha256"]:
            raise ValueError("Candidate and independent reference cannot reuse identical result evidence")
        for field in ("raw_evidence", "calibration_evidence"):
            if {d["sha256"] for d in a[field]} & {d["sha256"] for d in b[field]}:
                raise ValueError("Shared raw or calibration evidence requires a specialized independence/covariance model")
        if set(a["instrument_ids"]) & set(b["instrument_ids"]):
            raise ValueError("Shared instruments require a specialized covariance model")
        if set(a["calibration_material_ids"]) & set(b["calibration_material_ids"]):
            raise ValueError("Shared calibration materials require a specialized covariance model")
        if a["laboratory_id"] and a["method_id"] and (a["laboratory_id"], a["method_id"]) == (b["laboratory_id"], b["method_id"]):
            raise ValueError("Same laboratory and method do not provide an independent reference")

    def comparisons(self, candidate, reference):
        records = self.manifest["comparisons"]
        if type(records) is not list:
            raise ValueError("Comparisons must be a list")
        pairs, ids, used_a, used_b = [], set(), set(), set()
        split_by_lineage, sample_lineage, parent_lots, material_lineage = {}, {}, {}, {}
        for record in records:
            keys(record, "comparison_id candidate_result_id reference_result_id split")
            for field in ("comparison_id", "candidate_result_id", "reference_result_id"):
                text(record[field])
            ident, aid, bid, split = (record[f] for f in ("comparison_id", "candidate_result_id", "reference_result_id", "split"))
            if ident in ids or aid in used_a:
                raise ValueError("Repeated comparison or candidate result")
            if aid not in candidate or bid not in reference or split not in ("development", "held_out"):
                raise ValueError("Comparison references an absent result or unsupported split")
            a, b = candidate[aid], reference[bid]
            if bid in used_b and self.manifest["reference"]["evidence_type"] != "crm_assigned_value":
                raise ValueError("Only certified assigned values may be reused across comparisons")
            ids.add(ident); used_a.add(aid); used_b.add(bid)
            # Both lots and original samples remain disjoint even when the
            # narrower parent_sample grouping is selected for group counting.
            for name, row in (("candidate", a), ("reference", b)):
                lineage = (row["parent_sample_id"], row["lot_id"])
                for material_id in (row["sample_id"], row["parent_sample_id"]):
                    if material_lineage.setdefault(material_id, lineage) != lineage:
                        raise ValueError("Global sample/parent identity has inconsistent original lineage")
                if sample_lineage.setdefault(row["sample_id"], lineage) != lineage:
                    raise ValueError("Sample identity has inconsistent original lineage")
                if parent_lots.setdefault(row["parent_sample_id"], row["lot_id"]) != row["lot_id"]:
                    raise ValueError("Parent sample identity has inconsistent lot lineage")
                for field in ("sample_id", "parent_sample_id", "lot_id"):
                    key = ("sample" if field != "lot_id" else "lot"), row[field]
                    previous = split_by_lineage.setdefault(key, split)
                    if previous != split:
                        raise ValueError("Development/held-out sample or lot leakage")
            if split == "held_out":
                for name, row in (("candidate", a), ("reference", b)):
                    for role in (self.manifest["candidate"], self.manifest["reference"]):
                        if {row["sample_id"], row["parent_sample_id"]} & set(role["calibration_sample_ids"]):
                            raise ValueError("Held-out sample was used for calibration")
                        if row["lot_id"] in role["calibration_lot_ids"]:
                            raise ValueError("Held-out lot was used for calibration")
                for row in (a, b):
                    for role in (self.manifest["candidate"], self.manifest["reference"]):
                        if row["reference_material_id"] and row["reference_material_id"] in role["calibration_material_ids"]:
                            raise ValueError("Calibration material cannot also be held-out validation material")
            pair = dict(record, candidate=a, reference=b)
            pair["quantified"] = a["qualifier"] == b["qualifier"] == "quantified"
            if pair["quantified"]:
                pair["difference"] = number(a["value"] - b["value"])
                pair["combined_standard_uncertainty"] = number(math.hypot(a["standard_uncertainty"], b["standard_uncertainty"]))
            pairs.append(pair)
        if used_a != set(candidate) or used_b != set(reference):
            raise ValueError("Every imported result must be accounted for in comparisons")
        if not any(p["split"] == "held_out" for p in pairs):
            self.block("No held-out comparisons supplied")
        return pairs

    def policy(self, pairs):
        policy = self.manifest["held_out_policy"]
        if policy is None:
            self.block("Frozen held-out policy is missing")
            return None
        keys(policy, POLICY_FIELDS)
        if policy["split_unit"] not in ("lot", "parent_sample"):
            raise ValueError("Laboratory holdouts must be grouped by lot or parent_sample")
        field = policy["split_unit"] + "_id"
        development, held = (identifiers(policy[f]) for f in ("development_group_ids", "reserved_group_ids"))
        actual = {split: {p["candidate"][field] for p in pairs if p["split"] == split} for split in ("development", "held_out")}
        if development & held or development != actual["development"] or held != actual["held_out"]:
            raise ValueError("Frozen groups do not match declared comparison partitions")
        if policy["reference_used_for_development"] is not False:
            raise ValueError("Held-out reference must remain excluded from development")
        text(policy["reviewed_by"])
        self.read(policy["method_artifact"])
        self.read(policy["independence_evidence"])
        for name in ("candidate", "reference"):
            descriptor = self.manifest[name]["results"]
            if descriptor is None or hash_id(policy[name + "_results_sha256"]) != descriptor["sha256"]:
                raise ValueError("Frozen result identity mismatch")
        if hash_id(policy["criteria_sha256"]) != digest(dump(self.manifest["criteria"])):
            raise ValueError("Frozen criteria identity mismatch")
        split, method, frozen, unsealed = (utc(policy[f]) for f in ("split_frozen_at_utc", "method_frozen_at_utc", "candidate_results_frozen_at_utc", "reference_unsealed_at_utc"))
        held_rows = [p["candidate"] for p in pairs if p["split"] == "held_out"]
        if held_rows:
            start, end = min(utc(r["measured_at_utc"]) for r in held_rows), max(utc(r["measured_at_utc"]) for r in held_rows)
            if not (split < start and method < start and end <= frozen < unsealed):
                raise ValueError("Held-out method/split/results/reference freeze chronology is invalid")
        if any(utc(p["reference"]["measured_at_utc"]) > unsealed for p in pairs if p["split"] == "held_out"):
            raise ValueError("Reference cannot be unsealed before its result exists")
        if any(utc(p[role]["measured_at_utc"]) >= method for p in pairs if p["split"] == "development" for role in ("candidate", "reference")):
            raise ValueError("Development results must precede the method freeze")
        return field

    def criteria(self, pairs):
        criteria = self.manifest["criteria"]
        if criteria is None:
            self.block("Agreed acceptance criteria are missing")
            return None
        keys(criteria, CRITERIA_FIELDS)
        if criteria["status"] == "draft":
            self.block("Acceptance criteria remain draft")
            return None
        if criteria["status"] != "agreed" or criteria["unit"] != self.unit:
            raise ValueError("Agreed criteria require the profile canonical unit")
        if criteria["decision_rule"] != "independent_measurement_difference_guard":
            raise ValueError("Unsupported decision rule; only absolute endpoint differences are evaluated")
        text(criteria["approved_by"])
        approved = utc(criteria["approved_at_utc"])
        if any(approved >= utc(p["candidate"]["measured_at_utc"]) for p in pairs if p["split"] == "held_out"):
            raise ValueError("Acceptance criteria must be agreed before held-out measurements")
        number(criteria["coverage_factor"], 1, 10)
        integer(criteria["min_held_out_groups"], 1, 100000)
        if number(criteria["min_quantified_fraction"], 0, 1) == 0:
            raise ValueError("Require a positive quantified fraction")
        for field in ("min_reference_span", "max_abs_bias", "max_rmse", "max_abs_error", "max_combined_expanded_uncertainty"):
            number(criteria[field], 0)
        return criteria

    def evaluate(self):
        m = self.manifest
        keys(m, "schema campaign_id profile_id evidence_kind scope candidate reference comparisons comparability_review uncertainty_budget held_out_policy criteria")
        if m["schema"] != SCHEMA or m["evidence_kind"] not in ("measured", "synthetic"):
            raise ValueError("Unsupported laboratory campaign schema or evidence kind")
        text(m["campaign_id"])
        self.profile_id = m["profile_id"]
        self.profile = get_profile(self.profile_id)
        self.unit = self.profile["canonical_unit"]
        if m["evidence_kind"] == "synthetic":
            self.block("Synthetic evidence cannot establish measured qualification")
        scope_hash = self.scope()
        candidate, reference = self.role("candidate", scope_hash), self.role("reference", scope_hash)
        self.reviews()
        pairs = self.comparisons(candidate, reference)
        group_field = self.policy(pairs)
        criteria = self.criteria(pairs)
        held = [p for p in pairs if p["split"] == "held_out"]
        quantitative = [p for p in held if p["quantified"]]
        metrics, failures = None, []
        if criteria and group_field and not self.blockers:
            grouped = {}
            for pair in held:
                grouped.setdefault(pair["candidate"][group_field], []).append(pair)
            groups = set(grouped)
            if len(groups) < criteria["min_held_out_groups"]:
                failures.append("min_held_out_groups")
            fraction = len(quantitative) / len(held) if held else 0
            if fraction < criteria["min_quantified_fraction"]:
                failures.append("min_quantified_fraction")
            if not quantitative:
                failures.append("no_quantified_held_out_comparisons")
            span = max((p["reference"]["value"] for p in quantitative), default=0) - min((p["reference"]["value"] for p in quantitative), default=0)
            span = number(span, 0)
            if span < criteria["min_reference_span"]:
                failures.append("min_reference_span")
            by_group = {}
            for group in sorted(groups):
                all_rows = grouped[group]
                rows = [p for p in all_rows if p["quantified"]]
                if not rows or len(rows) / len(all_rows) < criteria["min_quantified_fraction"]:
                    failures.append(group + ":quantified_fraction")
                if not rows:
                    continue
                k = criteria["coverage_factor"]
                errors = [p["difference"] for p in rows]
                expanded = [number(k * p["combined_standard_uncertainty"], 0) for p in rows]
                # Divide before summation to avoid avoidable overflow. No
                # reduction in uncertainty is inferred from repeat injections.
                bias = number(math.fsum(e / len(rows) for e in errors))
                guard_bias = number(abs(bias) + math.fsum(u / len(rows) for u in expanded), 0)
                guards = [number(abs(e) + u, 0) for e, u in zip(errors, expanded)]
                rmse = number(math.hypot(*(e / math.sqrt(len(rows)) for e in errors)), 0)
                guarded_rmse = number(math.hypot(*(e / math.sqrt(len(rows)) for e in guards)), 0)
                values = {"abs_bias_guard": guard_bias, "rmse_guard": guarded_rmse,
                          "max_abs_error_guard": max(guards), "max_combined_expanded_uncertainty": max(expanded)}
                for metric, limit in (("abs_bias_guard", "max_abs_bias"), ("rmse_guard", "max_rmse"),
                                      ("max_abs_error_guard", "max_abs_error"), ("max_combined_expanded_uncertainty", "max_combined_expanded_uncertainty")):
                    if values[metric] > criteria[limit]:
                        failures.append(group + ":" + limit)
                by_group[group] = dict(values, bias=bias, rmse=rmse, quantified_comparisons=len(rows), total_comparisons=len(all_rows))
            metrics = {"held_out_groups": len(groups), "quantified_fraction": fraction,
                       "reference_span": span, "groups": by_group}
        status = "blocked_missing_evidence" if self.blockers else ("failed_declared_criteria" if failures else "passed_declared_criteria")
        report = {"schema": "ciw.laboratory-comparison-report.v1", "campaign_id": m["campaign_id"],
                  "profile_id": self.profile_id, "unit": self.unit, "criteria_result": status,
                  "blockers": self.blockers, "failed_checks": failures, "metrics": metrics,
                  "candidate_result_count": len(candidate), "reference_result_count": len(reference),
                  "held_out_comparison_count": len(held), "quantified_held_out_comparison_count": len(quantitative),
                  "held_out_parent_sample_count": len({p["candidate"]["parent_sample_id"] for p in held}),
                  "held_out_lot_count": len({p["candidate"]["lot_id"] for p in held}),
                  "evidence_kind": m["evidence_kind"], "manifest_sha256": digest(self.raw_manifest),
                  "retained_evidence_sha256": sorted(self.retained),
                  "physical_validation": "not_established", "release_status": "unchanged",
                  "limitations": ["Method results and standard uncertainties must already include applicable corrections.",
                      "Metadata and hashes establish internal consistency, not authenticity or physical independence.",
                      "Independent uncertainty is operator-reviewed; shared effects require a separate covariance model.",
                      "Criteria checks are descriptive guards, not confidence intervals or method accreditation."]}
        candidate_output = {"schema": "ciw.laboratory-candidate-input.v1", "profile_id": self.profile_id,
                            "unit": self.unit, "results": list(candidate.values())}
        reference_output = {"schema": "ciw.laboratory-reference-evaluation.v1", "profile_id": self.profile_id,
                            "unit": self.unit, "comparisons": pairs}
        return report, candidate_output, reference_output


def adapt_campaign(manifest_path, output_dir):
    campaign = LaboratoryCampaign(manifest_path)
    report, candidate, reference = campaign.evaluate()
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    (target / "raw").mkdir()
    (target / "campaign.json").write_bytes(campaign.raw_manifest)
    for identity, raw in campaign.retained.items():
        (target / "raw" / (identity.split(":")[1] + ".bin")).write_bytes(raw)
    (target / "candidate-input.json").write_bytes(dump(candidate))
    (target / "reference-evaluation.json").write_bytes(dump(reference))
    (target / "report.json").write_bytes(dump(report))
    return report


def write_templates(output_dir, profile_id):
    profile = get_profile(profile_id)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=False)
    role = {field: None for field in ROLE_FIELDS.split()}
    role.update(evidence_type="method_report", reporting_stage="final_corrected_method_result",
                instrument_ids=[], raw_evidence=[], calibration_evidence=[], calibration_material_ids=[],
                calibration_sample_ids=[], calibration_lot_ids=[])
    manifest = {"schema": SCHEMA, "campaign_id": "draft-" + profile_id, "profile_id": profile_id,
                "evidence_kind": "measured", "scope": {"target_identity": None, "matrix": None,
                    "reporting_basis": None, "minimum": None, "maximum": None,
                    "conditions": {f: None for f in profile["condition_fields"]}, "method_scope_evidence": None},
                "candidate": dict(role), "reference": dict(role), "comparisons": [], "comparability_review": None,
                "uncertainty_budget": None, "held_out_policy": None, "criteria": None}
    criteria = {f: None for f in CRITERIA_FIELDS.split()}
    criteria.update(status="draft", unit=profile["canonical_unit"], decision_rule="independent_measurement_difference_guard")
    policy = {f: None for f in POLICY_FIELDS.split()}
    policy.update(development_group_ids=[], reserved_group_ids=[])
    files = {"campaign.json": manifest, "profile.json": profile, "criteria.template.json": criteria,
             "held-out-policy.template.json": policy,
             "comparison.template.json": {f: None for f in ("comparison_id", "candidate_result_id", "reference_result_id", "split")},
             "comparability-review.template.json": {f: None for f in ("reviewed_by", "evidence", "same_measurand", "compatible_methods", "valid_specimen_pairing")},
             "uncertainty-budget.template.json": {"assumption": None, "reviewed_by": None, "evidence": None}}
    for filename, value in files.items():
        (root / filename).write_bytes(dump(value))
    (root / "results.template.csv").write_text(",".join(COLUMNS) + "\n", encoding="utf-8")
    return root / "campaign.json"


def write_specialized_template(output_dir, family_id):
    if family_id not in DEFERRED:
        raise ValueError("Unknown specialized laboratory family")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=False)
    value = {"schema": "ciw.specialized-laboratory-intake-draft.v1", "family_id": family_id,
             "family": DEFERRED[family_id], "status": "specialized_adapter_required",
             "instrument": {"manufacturer": None, "model": None, "serial_number": None},
             "measurand": None, "matrix": None, "method_revision": None,
             "specification": {"range": None, "resolution": None, "accuracy": None, "uncertainty": None},
             "raw_exports": [], "calibration_evidence": [], "independent_reference_evidence": [],
             "held_out_policy": None, "agreed_criteria": None,
             "physical_validation": "not_established", "release_status": "unchanged"}
    (root / "intake-draft.json").write_bytes(dump(value))
    return root / "intake-draft.json"


def catalog():
    return {"schema": "ciw.laboratory-profile-catalog.v1", "profiles": PROFILES, "deferred": DEFERRED,
            "hardware_inventory": "not_supplied", "physical_validation": "not_established", "release_status": "unchanged"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog")
    init = sub.add_parser("init")
    init.add_argument("--profile", required=True)
    init.add_argument("--output-dir", required=True)
    all_init = sub.add_parser("init-all")
    all_init.add_argument("--output-dir", required=True)
    adapt = sub.add_parser("adapt")
    adapt.add_argument("--manifest", required=True)
    adapt.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "catalog":
            print(dump(catalog()).decode())
            return 0
        if args.command == "init":
            print(write_templates(args.output_dir, args.profile))
            return 0
        if args.command == "init-all":
            root = Path(args.output_dir)
            root.mkdir(parents=True, exist_ok=False)
            for profile_id in PROFILES:
                write_templates(root / profile_id, profile_id)
            for family_id in DEFERRED:
                write_specialized_template(root / "specialized" / family_id, family_id)
            (root / "catalog.json").write_bytes(dump(catalog()))
            print(root)
            return 0
        report = adapt_campaign(args.manifest, args.output_dir)
        print(dump(report).decode())
        return 0 if report["criteria_result"] == "passed_declared_criteria" else 2
    except (ValueError, OSError, KeyError, TypeError, OverflowError, csv.Error) as error:
        print(json.dumps({"status": "refused", "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
