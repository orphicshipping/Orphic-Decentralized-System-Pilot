from copy import deepcopy
import math

import pytest

from ciw.hardware_contract import (
    AUTHORITY, assess, configuration_ref, example_request, validate_assessment,
    validate_configuration, validate_request,
)
from ciw.operations.runner import digest


def test_example_is_deterministic_detached_and_grants_no_authority():
    request = example_request()
    report = assess(request)
    assert report == assess(request)
    assert report["status"] == "PASS"
    assert report["source_kind"] == "synthetic"
    assert report["authority"] == AUTHORITY
    assert report["configuration_ref"] == configuration_ref(request["configuration"])
    assert report["plan_ref"] == digest(request["plan"])
    assert report["observations_ref"] == digest(request["observations"])
    validate_assessment(request, report)
    report["authority"]["physical_validation"] = "changed"
    request["observations"]["records"][0]["value"] = 2
    assert AUTHORITY["physical_validation"] == "not_established"
    assert assess(request)["status"] == "FAIL"


def test_validation_returns_detached_data():
    request = example_request()
    validated = validate_request(request)
    validated["configuration"]["components"][0]["model"] = "changed"
    assert validated != request
    configuration = validate_configuration(request["configuration"])
    configuration["software"][0]["version"] = "changed"
    assert configuration != request["configuration"]


@pytest.mark.parametrize("field", ["firmware", "model", "serial_number"])
def test_component_changes_invalidate_observation_binding(field):
    request = example_request()
    original = configuration_ref(request["configuration"])
    request["configuration"]["components"][0][field] = "changed"
    assert configuration_ref(request["configuration"]) != original
    with pytest.raises(ValueError, match="different configuration"):
        assess(request)


@pytest.mark.parametrize("field", ["revision", "manufacturer", "model", "serial_number", "software"])
def test_all_configuration_fields_participate_in_identity(field):
    request = example_request()
    original = configuration_ref(request["configuration"])
    if field == "software":
        request["configuration"][field][0]["version"] = "next"
    else:
        request["configuration"][field] = "next"
    assert configuration_ref(request["configuration"]) != original
    with pytest.raises(ValueError, match="different configuration"):
        validate_request(request)


def test_wrong_asset_refused_even_when_configuration_digest_is_unchanged():
    request = example_request()
    request["observations"]["asset_id"] = "OTHER"
    with pytest.raises(ValueError, match="different asset"):
        assess(request)


@pytest.mark.parametrize("kind", ["components", "software"])
def test_duplicate_configuration_entries_rejected(kind):
    request = example_request()
    configuration = request["configuration"]
    configuration[kind].append(deepcopy(configuration[kind][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        validate_configuration(configuration)


def test_configuration_requires_components_but_accepts_explicit_unknown_identifiers():
    configuration = example_request()["configuration"]
    assert validate_configuration(configuration)["serial_number"] is None
    configuration["software"] = []
    validate_configuration(configuration)
    configuration["components"] = []
    with pytest.raises(ValueError):
        validate_configuration(configuration)


@pytest.mark.parametrize("target", ["configuration", "component", "software", "request", "plan", "check", "observations", "tool", "record"])
def test_unknown_fields_are_rejected(target):
    request = example_request()
    locations = {
        "configuration": request["configuration"], "component": request["configuration"]["components"][0],
        "software": request["configuration"]["software"][0], "request": request,
        "plan": request["plan"], "check": request["plan"]["checks"][0],
        "observations": request["observations"], "tool": request["observations"]["tool"],
        "record": request["observations"]["records"][0],
    }
    locations[target]["extra"] = "unsupported"
    with pytest.raises(ValueError):
        validate_request(request)


@pytest.mark.parametrize("value", [True, False, math.nan, math.inf, -math.inf, "0", 1e151])
@pytest.mark.parametrize("field", ["minimum", "maximum", "minimum_duration_s", "value", "duration_s"])
def test_invalid_numbers_are_rejected(value, field):
    request = example_request()
    target = request["plan"]["checks"][0] if field in {"minimum", "maximum", "minimum_duration_s"} else request["observations"]["records"][0]
    target[field] = value
    with pytest.raises(ValueError):
        assess(request)


@pytest.mark.parametrize("field", ["duration_s", "minimum_duration_s"])
def test_negative_duration_is_rejected(field):
    request = example_request()
    target = request["plan"]["checks"][0] if field == "minimum_duration_s" else request["observations"]["records"][0]
    target[field] = -1
    with pytest.raises(ValueError, match="nonnegative"):
        assess(request)


@pytest.mark.parametrize("mode", ["empty", "no_bounds", "inverted", "duplicate"])
def test_invalid_plans_are_rejected(mode):
    request = example_request()
    checks = request["plan"]["checks"]
    if mode == "empty":
        checks.clear()
    elif mode == "no_bounds":
        checks[0].update(minimum=None, maximum=None)
    elif mode == "inverted":
        checks[0].update(minimum=1, maximum=0)
    else:
        checks.append(deepcopy(checks[0]))
    with pytest.raises(ValueError):
        assess(request)


@pytest.mark.parametrize("mode", ["duplicate", "unknown", "completed_null", "error_number", "unknown_status"])
def test_invalid_observations_are_rejected(mode):
    request = example_request()
    records = request["observations"]["records"]
    if mode == "duplicate":
        records.append(deepcopy(records[0]))
    elif mode == "unknown":
        records[0]["test_id"] = "not-in-plan"
    elif mode == "completed_null":
        records[0]["value"] = None
    elif mode == "error_number":
        records[0]["status"] = "error"
    else:
        records[0]["status"] = "passed"
    with pytest.raises(ValueError):
        assess(request)


@pytest.mark.parametrize("timestamp", ["2026-01-01", "2026-01-01T12:00:00", "not-a-date", "2026-99-99T00:00:00Z"])
def test_timestamp_requires_timezone_and_valid_iso_date(timestamp):
    request = example_request()
    request["observations"]["captured_at"] = timestamp
    with pytest.raises(ValueError):
        assess(request)


def test_operator_import_is_explicit_and_does_not_authenticate_source():
    request = example_request()
    request["observations"].update(source_kind="operator_import", captured_at="2026-01-01T07:00:00-05:00")
    report = assess(request)
    assert report["source_kind"] == "operator_import"
    assert report["authority"]["source_authenticity"] == "not_established"
    request["observations"]["source_kind"] = "authenticated"
    with pytest.raises(ValueError):
        assess(request)


@pytest.mark.parametrize("mode,reason", [
    ("missing", "missing_observation"), ("error", "observation_error"),
    ("not_run", "observation_not_run"), ("procedure", "procedure_mismatch"),
    ("unit", "unit_mismatch"), ("duration", "insufficient_duration"),
])
def test_incomplete_or_incompatible_evidence_is_indeterminate(mode, reason):
    request = example_request()
    observation = request["observations"]["records"][0]
    if mode == "missing":
        request["observations"]["records"].pop(0)
    elif mode in {"error", "not_run"}:
        observation.update(status=mode, value=None)
    elif mode == "procedure":
        observation.update(procedure_id="different-procedure", value=999)
    elif mode == "unit":
        observation.update(unit="different-unit", value=999)
    else:
        observation["duration_s"] = 599
    report = assess(request)
    assert report["status"] == "INDETERMINATE"
    assert report["checks"][0]["status"] == "INDETERMINATE"
    assert report["checks"][0]["reason"] == reason
    if mode == "missing":
        for field in ("value", "unit", "duration_s", "observed_procedure_id"):
            assert report["checks"][0][field] is None


def test_no_records_retains_every_required_check_in_order():
    request = example_request()
    request["observations"]["records"] = []
    report = assess(request)
    assert report["status"] == "INDETERMINATE"
    assert [entry["test_id"] for entry in report["checks"]] == [entry["test_id"] for entry in request["plan"]["checks"]]
    assert {entry["reason"] for entry in report["checks"]} == {"missing_observation"}


def test_observation_order_does_not_change_check_order_or_decisions():
    request = example_request()
    first = assess(request)
    request["observations"]["records"].reverse()
    second = assess(request)
    assert first["checks"] == second["checks"]
    assert first["observations_ref"] != second["observations_ref"]


def test_failure_dominates_missing_evidence_and_insufficient_duration():
    request = example_request()
    request["observations"]["records"] = request["observations"]["records"][:1]
    request["observations"]["records"][0].update(value=1, duration_s=0)
    report = assess(request)
    assert report["status"] == "FAIL"
    assert [entry["status"] for entry in report["checks"]] == ["FAIL", "INDETERMINATE"]
    assert report["checks"][0]["reason"] == "above_maximum"


@pytest.mark.parametrize("value,status,reason", [(89, "FAIL", "below_minimum"), (90, "PASS", "within_declared_limits"), (100, "PASS", "within_declared_limits"), (101, "FAIL", "above_maximum")])
def test_acceptance_bounds_are_inclusive(value, status, reason):
    request = example_request()
    request["observations"]["records"][1]["value"] = value
    result = assess(request)["checks"][1]
    assert (result["status"], result["reason"]) == (status, reason)


@pytest.mark.parametrize("bound", ["minimum", "maximum"])
def test_one_sided_limits_are_supported(bound):
    request = example_request()
    request["plan"]["checks"][1][bound] = None
    assert assess(request)["status"] == "PASS"


def test_policy_drift_cannot_reuse_old_report():
    request = example_request()
    report = assess(request)
    request["plan"]["checks"][1]["minimum"] = 99
    changed = assess(request)
    assert changed["status"] == "FAIL"
    assert changed["configuration_ref"] == report["configuration_ref"]
    assert changed["plan_ref"] != report["plan_ref"]
    with pytest.raises(ValueError, match="exact retained request"):
        validate_assessment(request, report)


@pytest.mark.parametrize("mode", ["status", "reason", "value", "bool_value", "authority", "drop_check", "identity", "extra"])
def test_forged_assessment_is_rejected(mode):
    request = example_request()
    report = assess(request)
    if mode == "status":
        report["status"] = "FAIL"
    elif mode == "reason":
        report["checks"][0]["reason"] = "certified"
    elif mode == "value":
        report["checks"][0]["value"] = 1
    elif mode == "bool_value":
        report["checks"][0]["value"] = False
    elif mode == "authority":
        report["authority"]["physical_validation"] = "established"
    elif mode == "drop_check":
        report["checks"].pop()
    elif mode == "identity":
        report["configuration_ref"] = digest({})
    else:
        report["release_authorized"] = True
    with pytest.raises(ValueError):
        validate_assessment(request, report)


@pytest.mark.parametrize("target,maximum", [("components", 128), ("software", 128), ("checks", 64), ("records", 64)])
def test_list_capacity_is_bounded(target, maximum):
    request = example_request()
    container = request["configuration"] if target in {"components", "software"} else request["plan"] if target == "checks" else request["observations"]
    container[target] = [deepcopy(container[target][0]) for _ in range(maximum + 1)]
    with pytest.raises(ValueError, match="bounded list"):
        validate_request(request)
