"""Bounded hardware configuration and imported-test comparison contracts.

Assessments compare caller-declared observations with a declared test plan.
Neither imported records nor content identities authenticate hardware acquisition,
authorize release, establish physical qualification, or perform sanitization.
Synthetic examples exercise the software contract only.
"""
from __future__ import annotations

from datetime import datetime

from .control_contracts import content_ref, detached, keys, number, text
from .operations.runner import digest


OPERATION = "hardware.assess.v1"
REQUEST_SCHEMA = "ciw.hardware-request.v1"
CONFIGURATION_SCHEMA = "ciw.hardware-configuration.v1"
PLAN_SCHEMA = "ciw.hardware-plan.v1"
OBSERVATIONS_SCHEMA = "ciw.hardware-observations.v1"
ASSESSMENT_SCHEMA = "ciw.hardware-assessment.v1"
AUTHORITY = {
    "physical_validation": "not_established",
    "release_authorization": "not_granted",
    "hardware_actuation": "not_performed",
    "data_sanitization": "not_performed",
    "source_authenticity": "not_established",
}


def _list(value, maximum, *, nonempty=False):
    if type(value) is not list or len(value) > maximum or nonempty and not value:
        raise ValueError("Require a bounded list" + (" with at least one entry" if nonempty else ""))


def _optional_text(value):
    if value is not None:
        text(value)


def _nonnegative(value):
    if number(value) < 0:
        raise ValueError("Duration must be nonnegative")


def validate_configuration(value: dict) -> dict:
    """Validate and detach the entire configuration covered by its content ID."""
    configuration = detached(value)
    keys(configuration, {"schema", "asset_id", "revision", "manufacturer", "model",
                         "serial_number", "components", "software"})
    if configuration["schema"] != CONFIGURATION_SCHEMA:
        raise ValueError("Unsupported hardware configuration schema")
    for name in ("asset_id", "revision", "manufacturer", "model"):
        text(configuration[name])
    _optional_text(configuration["serial_number"])
    _list(configuration["components"], 128, nonempty=True)
    slots = set()
    for component in configuration["components"]:
        keys(component, {"slot", "kind", "manufacturer", "model", "serial_number", "firmware"})
        for name in ("slot", "kind", "manufacturer", "model"):
            text(component[name])
        for name in ("serial_number", "firmware"):
            _optional_text(component[name])
        if component["slot"] in slots:
            raise ValueError("Duplicate hardware component slot")
        slots.add(component["slot"])
    _list(configuration["software"], 128)
    names = set()
    for software in configuration["software"]:
        keys(software, {"name", "version"})
        text(software["name"])
        text(software["version"])
        if software["name"] in names:
            raise ValueError("Duplicate installed software name")
        names.add(software["name"])
    return configuration


def configuration_ref(configuration: dict) -> str:
    return digest(validate_configuration(configuration))


def _validate_plan(plan):
    keys(plan, {"schema", "plan_id", "revision", "purpose", "checks"})
    if plan["schema"] != PLAN_SCHEMA:
        raise ValueError("Unsupported hardware test plan schema")
    for name in ("plan_id", "revision", "purpose"):
        text(plan[name])
    _list(plan["checks"], 64, nonempty=True)
    test_ids = set()
    for check in plan["checks"]:
        keys(check, {"test_id", "procedure_id", "unit", "minimum", "maximum", "minimum_duration_s"})
        for name in ("test_id", "procedure_id", "unit"):
            text(check[name])
        if check["test_id"] in test_ids:
            raise ValueError("Duplicate hardware plan test identity")
        test_ids.add(check["test_id"])
        lower, upper = check["minimum"], check["maximum"]
        if lower is None and upper is None:
            raise ValueError("Every hardware check needs at least one numerical bound")
        for bound in (lower, upper):
            if bound is not None:
                number(bound)
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("Hardware check minimum exceeds maximum")
        _nonnegative(check["minimum_duration_s"])
    return test_ids


def _validate_observations(observations, configuration, test_ids):
    keys(observations, {"schema", "asset_id", "configuration_ref", "source_kind", "captured_at",
                        "operator", "tool", "environment", "records"})
    if observations["schema"] != OBSERVATIONS_SCHEMA:
        raise ValueError("Unsupported hardware observations schema")
    text(observations["asset_id"])
    content_ref(observations["configuration_ref"])
    if observations["asset_id"] != configuration["asset_id"]:
        raise ValueError("Hardware observations belong to a different asset")
    if observations["configuration_ref"] != digest(configuration):
        raise ValueError("Hardware observations belong to a different configuration")
    if observations["source_kind"] not in {"synthetic", "operator_import"}:
        raise ValueError("Declare synthetic or operator_import observation source")
    for name in ("captured_at", "operator", "environment"):
        text(observations[name])
    try:
        timestamp = datetime.fromisoformat(observations["captured_at"])
    except ValueError as exc:
        raise ValueError("Require an ISO observation timestamp with timezone") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Require an ISO observation timestamp with timezone")
    keys(observations["tool"], {"name", "version"})
    for value in observations["tool"].values():
        text(value)
    _list(observations["records"], 64)
    observed_ids = set()
    for observation in observations["records"]:
        keys(observation, {"test_id", "procedure_id", "unit", "value", "duration_s", "status", "notes"})
        for name in ("test_id", "procedure_id", "unit"):
            text(observation[name])
        test_id = observation["test_id"]
        if test_id in observed_ids:
            raise ValueError("Duplicate hardware observation test identity")
        if test_id not in test_ids:
            raise ValueError("Observation test identity is absent from the hardware plan")
        observed_ids.add(test_id)
        _nonnegative(observation["duration_s"])
        if observation["status"] not in {"completed", "error", "not_run"}:
            raise ValueError("Unsupported hardware observation status")
        if observation["status"] == "completed":
            number(observation["value"])
        elif observation["value"] is not None:
            raise ValueError("Incomplete hardware observations must have a null value")
        if observation["notes"] != "":
            text(observation["notes"])


def validate_request(value: dict) -> dict:
    """Require exact configuration binding; retain missing tests as missing."""
    request = detached(value)
    keys(request, {"schema", "configuration", "plan", "observations"})
    if request["schema"] != REQUEST_SCHEMA:
        raise ValueError("Unsupported hardware request schema")
    configuration = validate_configuration(request["configuration"])
    test_ids = _validate_plan(request["plan"])
    _validate_observations(request["observations"], configuration, test_ids)
    return request


def _decision(check, observation):
    if observation is None:
        return "INDETERMINATE", "missing_observation"
    if observation["procedure_id"] != check["procedure_id"]:
        return "INDETERMINATE", "procedure_mismatch"
    if observation["unit"] != check["unit"]:
        return "INDETERMINATE", "unit_mismatch"
    if observation["status"] != "completed":
        return "INDETERMINATE", "observation_" + observation["status"]
    if check["minimum"] is not None and observation["value"] < check["minimum"]:
        return "FAIL", "below_minimum"
    if check["maximum"] is not None and observation["value"] > check["maximum"]:
        return "FAIL", "above_maximum"
    if observation["duration_s"] < check["minimum_duration_s"]:
        return "INDETERMINATE", "insufficient_duration"
    return "PASS", "within_declared_limits"


def assess(value: dict) -> dict:
    """Compare imported declarations only; a PASS grants no release authority."""
    request = validate_request(value)
    observations = request["observations"]
    records = {entry["test_id"]: entry for entry in observations["records"]}
    checks = []
    for check in request["plan"]["checks"]:
        observation = records.get(check["test_id"])
        status, reason = _decision(check, observation)
        checks.append({
            "test_id": check["test_id"], "procedure_id": check["procedure_id"],
            "observed_procedure_id": None if observation is None else observation["procedure_id"],
            "expected_unit": check["unit"],
            "value": None if observation is None else observation["value"],
            "unit": None if observation is None else observation["unit"],
            "duration_s": None if observation is None else observation["duration_s"],
            "minimum": check["minimum"], "maximum": check["maximum"],
            "minimum_duration_s": check["minimum_duration_s"],
            "status": status, "reason": reason,
        })
    statuses = {entry["status"] for entry in checks}
    status = "FAIL" if "FAIL" in statuses else "INDETERMINATE" if "INDETERMINATE" in statuses else "PASS"
    return {
        "schema": ASSESSMENT_SCHEMA, "asset_id": request["configuration"]["asset_id"],
        "configuration_ref": digest(request["configuration"]), "plan_ref": digest(request["plan"]),
        "observations_ref": digest(observations), "source_kind": observations["source_kind"],
        "status": status, "checks": checks, "authority": dict(AUTHORITY),
    }


def validate_assessment(request: dict, data: dict) -> None:
    """Check the complete deterministic comparison, without running hardware."""
    data = detached(data)
    expected = assess(request)
    # Canonical JSON comparison distinguishes bool from int and int from float.
    if digest(data) != digest(expected):
        raise ValueError("Hardware assessment differs from its exact retained request")


def example_request() -> dict:
    """Return synthetic workstation observations, not physical test evidence."""
    configuration = {
        "schema": CONFIGURATION_SCHEMA, "asset_id": "NS-DEMO-0001", "revision": "1",
        "manufacturer": "Synthetic", "model": "Workstation example", "serial_number": None,
        "components": [
            {"slot": "memory-0", "kind": "memory", "manufacturer": "Synthetic", "model": "32 GiB",
             "serial_number": None, "firmware": None},
            {"slot": "storage-0", "kind": "storage", "manufacturer": "Synthetic", "model": "1 TB SSD",
             "serial_number": None, "firmware": "example-1"},
        ],
        "software": [{"name": "bench-environment", "version": "example-1"}],
    }
    return {
        "schema": REQUEST_SCHEMA, "configuration": configuration,
        "plan": {"schema": PLAN_SCHEMA, "plan_id": "workstation-example", "revision": "1",
                 "purpose": "Synthetic demonstration of declared workstation acceptance criteria",
                 "checks": [
                     {"test_id": "memory-errors", "procedure_id": "example.memory-check.v1", "unit": "count",
                      "minimum": 0, "maximum": 0, "minimum_duration_s": 600},
                     {"test_id": "storage-health", "procedure_id": "example.storage-health.v1", "unit": "%",
                      "minimum": 90, "maximum": 100, "minimum_duration_s": 0},
                 ]},
        "observations": {"schema": OBSERVATIONS_SCHEMA, "asset_id": configuration["asset_id"],
                         "configuration_ref": configuration_ref(configuration), "source_kind": "synthetic",
                         "captured_at": "2026-01-01T12:00:00+00:00", "operator": "Synthetic fixture",
                         "tool": {"name": "NET hardware example", "version": "1"},
                         "environment": "Synthetic values; no physical device was tested",
                         "records": [
                             {"test_id": "memory-errors", "procedure_id": "example.memory-check.v1", "unit": "count",
                              "value": 0, "duration_s": 600, "status": "completed", "notes": "Synthetic observation"},
                             {"test_id": "storage-health", "procedure_id": "example.storage-health.v1", "unit": "%",
                              "value": 98, "duration_s": 1, "status": "completed", "notes": "Synthetic observation"},
                         ]},
    }
