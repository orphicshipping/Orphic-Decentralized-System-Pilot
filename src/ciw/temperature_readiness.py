"""Data-only inventory of a polymer-temperature pilot plan.

Filled declarations and document references are not experimental evidence,
traceability, product conformity, permission to operate, or physical qualification.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from .control_contracts import detached, load, number, save_new
from .operations.runner import digest, seal

SCHEMA = "ciw.temperature-pilot-plan.v1"
REPORT_SCHEMA = "ciw.temperature-pilot-readiness.v1"
MAX_BYTES = 256 * 1024
FIELDS = {
    "product_conformity_assessment": (
        "record", "status", "product_conformity", "independent_of_processing_acceptance"),
    "protocol": ("version", "owner", "technical_reviewer", "freeze_date", "amendment_record"),
    "measurand": (
        "quantity", "reported_unit", "target_location_and_coordinate_frame",
        "spatial_averaging_convention", "time_origin_and_averaging_convention",
        "instrumented_or_undisturbed_polymer_target", "polymer_grade_and_lot",
        "specimen_geometry_and_conditioning", "material_state_and_excluded_transitions",
        "sensor_observation_label", "calibrated_output_label", "installed_output_label",
        "optional_model_output_target"),
    "measurement_chain": (
        "sensor_type_model_serial", "readout_model_serial_firmware", "raw_quantity_and_unit",
        "installation_method_and_drawing", "position_tolerance",
        "thermal_contact_and_insertion_assessment", "calibration_record",
        "reference_instrument_and_calibration_chain", "reference_response_time_and_placement",
        "acquisition_clock_and_alignment", "original_acquisition_record_retention",
        "authentication_contract_if_required"),
    "operating_envelope": (
        "temperature_range_K", "heating_and_cooling_rate_range_K_per_s",
        "cycle_profiles_and_duration_s", "ambient_conditions", "sampling_interval_s",
        "allowed_timing_uncertainty_s", "allowed_installations_devices_and_operators",
        "missing_and_saturation_policy", "unsupported_conditions_policy"),
    "acceptance_criteria": (
        "customer_decision_and_specification", "required_uncertainty_K",
        "coverage_convention_probability_and_factor", "bias_and_error_limits_K",
        "reproducibility_limit_K", "reference_comparison_rule",
        "operational_accept_reject_indeterminate_rule",
        "pinn_gpinn_improvement_or_noninferiority_margin",
        "evidence_completeness_review", "physical_qualification_review"),
    "experiment_design": (
        "independent_unit", "static_comparison_points_and_sequences",
        "dynamic_profiles_and_conditions", "replication_across_days_installations_operators_devices",
        "randomization_and_blocking", "preliminary_variance_estimates",
        "confirmatory_sample_size_and_precision_power_rationale",
        "development_tuning_and_confirmatory_split_manifest", "split_constraint",
        "additional_held_out_day_lot_installation_groups", "within_envelope_validation_conditions",
        "separate_ood_stress_conditions", "independent_instrument_or_laboratory_comparison"),
    "uncertainty_evidence": (
        "measurement_model_and_source_ledger", "joint_raw_gain_offset_covariance_record",
        "reference_and_fit_double_counting_review", "resolution_noise_drift_hysteresis",
        "installed_lag_gradients_contact_insertion_timing", "correction_cross_covariance_assessment",
        "model_discrepancy_and_condition_dependence", "numerical_error_and_coverage_checks",
        "excluded_effects_and_claim_limitations"),
    "maintenance": (
        "check_standards_and_schedule", "control_limits_and_recalibration_triggers",
        "version_and_installation_change_policy", "as_found_as_left_records",
        "affected_result_withdrawal_and_reprocessing_policy", "responsible_owner"),
    "optional_sp1": (
        "status", "temperature_guest_identity", "numerical_contract",
        "verifier_public_output_binding", "genuine_build_prove_verify_evidence",
        "numerical_equivalence_and_negative_tests", "workload_performance_and_cost",
        "claim", "physical_qualification_inferred"),
}
META = {"schema", "status", "purpose", "evidence_origin", "physical_qualification", "source_selection"}
UNSPECIFIED = {"", "pending", "tbd", "todo", "unknown", "unassessed", "not_supplied",
               "not_implemented", "no_experimental_data_supplied"}
AUTHORITY = {
    "physical_qualification": "not_established",
    "calibration_traceability": "not_established",
    "evidence_authentication": "not_performed",
    "product_conformity": "not_established",
    "proof": "not_established",
    "authorization_to_operate": "not_granted",
}


def _paths(section, exclude=()):
    return [section + "." + field for field in FIELDS[section] if field not in exclude]


EVIDENCE_CHAIN = ("thermal_contact_and_insertion_assessment", "calibration_record",
                  "reference_instrument_and_calibration_chain", "reference_response_time_and_placement")
STAGES = {
    "setup_definition": (
        _paths("protocol", ("amendment_record",))
        + _paths("measurand", ("optional_model_output_target",))
        + _paths("measurement_chain", EVIDENCE_CHAIN + ("authentication_contract_if_required",))
        + _paths("operating_envelope")
        + _paths("acceptance_criteria", ("pinn_gpinn_improvement_or_noninferiority_margin",
                                         "evidence_completeness_review", "physical_qualification_review"))),
    "confirmatory_design": _paths("experiment_design"),
    "qualification_evidence_inventory": (
        ["measurement_chain." + key for key in EVIDENCE_CHAIN]
        + _paths("uncertainty_evidence")
        + ["acceptance_criteria.evidence_completeness_review",
           "acceptance_criteria.physical_qualification_review"]),
    "maintenance_definition": _paths("maintenance"),
}
MODEL_FIELDS = [
    "measurand.optional_model_output_target",
    "acceptance_criteria.pinn_gpinn_improvement_or_noninferiority_margin",
    "experiment_design.development_tuning_and_confirmatory_split_manifest",
    "experiment_design.separate_ood_stress_conditions",
    "uncertainty_evidence.model_discrepancy_and_condition_dependence",
]
SP1_FIELDS = _paths("optional_sp1", ("status", "claim", "physical_qualification_inferred"))


def _get(plan, path):
    section, field = path.split(".", 1)
    return plan.get(section, {}).get(field)


def _specified(value):
    if value is None:
        return False
    if type(value) is str:
        return value.strip().lower() not in UNSPECIFIED
    if type(value) is dict:
        return bool(value) and all(_specified(item) for item in value.values())
    if type(value) is list:
        return bool(value) and all(_specified(item) for item in value)
    return True


def _format_issue(path, value):
    """Check only concrete numeric semantics the current plan names."""
    positive = {
        "operating_envelope.sampling_interval_s",
        "acceptance_criteria.required_uncertainty_K",
        "acceptance_criteria.reproducibility_limit_K",
    }
    try:
        if path in positive:
            return "Require a positive finite number in the named unit" if number(value) <= 0 else None
        if path == "operating_envelope.allowed_timing_uncertainty_s":
            return "Require a nonnegative finite number in seconds" if number(value) < 0 else None
        if path in {"operating_envelope.temperature_range_K",
                    "operating_envelope.heating_and_cooling_rate_range_K_per_s"}:
            if type(value) is not list or len(value) != 2:
                return "Require [lower, upper] numbers in the named unit"
            low, high = map(number, value)
            if low >= high or path.endswith("temperature_range_K") and low <= 0:
                return "Require ordered bounds; absolute temperatures must be positive"
    except (ValueError, TypeError):
        return "Require finite numeric values, not booleans or unit-bearing text"
    if type(value) not in (str, dict, list):
        return "Require descriptive text or a structured record"
    return None


def _stage(plan, paths, *, requested=True):
    missing = []
    invalid = []
    present = []
    for path in paths:
        value = _get(plan, path)
        if not _specified(value):
            missing.append(path)
        elif issue := _format_issue(path, value):
            invalid.append({"field": path, "reason": issue})
        else:
            present.append(path)
    return {
        "status": ("not_requested" if not requested else
                   "incomplete" if missing or invalid else "declarations_complete_for_review"),
        "requested": requested,
        "required_fields": list(paths), "specified_fields": present,
        "missing_fields": missing, "invalid_fields": invalid,
    }


def assess_plan(value: dict) -> dict:
    """Inventory bounded declarations without opening evidence paths or URLs."""
    plan = detached(value)
    if type(plan) is not dict or plan.get("schema") != SCHEMA:
        raise ValueError("Require ciw.temperature-pilot-plan.v1")
    if set(plan) - META - set(FIELDS):
        raise ValueError("Unknown pilot-plan fields")
    if len(json.dumps(plan, allow_nan=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Pilot plan exceeds 256 KiB")
    for section, fields in FIELDS.items():
        if section in plan and (type(plan[section]) is not dict or set(plan[section]) - set(fields)):
            raise ValueError("Invalid or unknown fields in pilot section " + section)
    if plan.get("physical_qualification") != "not_established":
        raise ValueError("This planning profile cannot assert physical qualification")
    if type(plan.get("evidence_origin")) is not str or not plan["evidence_origin"].strip():
        raise ValueError("Require explicit evidence origin")
    measurand = plan.get("measurand", {})
    if measurand.get("reported_unit", "K") != "K":
        raise ValueError("Pilot temperature unit must be K; no unit conversion is performed")
    sp1 = plan.get("optional_sp1", {})
    if sp1.get("physical_qualification_inferred", False) is not False:
        raise ValueError("A computation proof cannot infer physical qualification")
    stages = {name: _stage(plan, paths) for name, paths in STAGES.items()}
    model_requested = _specified(_get(plan, "measurand.optional_model_output_target"))
    proof_requested = _specified(sp1.get("status")) or any(
        _specified(_get(plan, path)) for path in SP1_FIELDS)
    optional = {
        "model_comparison": _stage(plan, MODEL_FIELDS, requested=model_requested),
        "temperature_sp1": _stage(plan, SP1_FIELDS, requested=proof_requested),
    }
    required = list(stages.values()) + [row for row in optional.values() if row["requested"]]
    status = ("declarations_complete_for_review"
              if all(row["status"] == "declarations_complete_for_review" for row in required)
              else "incomplete")
    return seal({
        "schema": REPORT_SCHEMA, "plan_ref": digest(plan), "status": status,
        "scope": "Field presence and selected numeric formats only; no evidence adequacy, instrument or experimental review",
        "evidence_origin": plan["evidence_origin"], "stages": stages, "optional": optional,
        "authority": deepcopy(AUTHORITY),
        "limitations": [
            "Document references are not opened, authenticated or evaluated",
            "Complete synthetic or published declarations remain declarations, not an installed measurement chain",
            "A populated review field does not make this checker its reviewer",
            "Some fields accept structured records; completeness checks their leaves, not their scientific adequacy",
            "Optional acquisition authentication and product conformity require separate applicability review",
        ],
    })


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", type=Path, required=True, help="Existing pilot-plan JSON")
    parser.add_argument("--output", type=Path, help="Optional new report JSON; existing files are refused")
    args = parser.parse_args(argv)
    try:
        report = assess_plan(load(args.setup))
        if args.output is not None:
            save_new(args.output, report)
        print(json.dumps(report, indent=2, allow_nan=False))
        return 0 if report["status"] == "declarations_complete_for_review" else 1
    except (OSError, ValueError, TypeError) as error:
        print("Temperature readiness refused: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
