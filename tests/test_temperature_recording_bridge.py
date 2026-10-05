"""Strict temperature handoff, covariance ancestry and real thermal consumption."""
import base64
from copy import deepcopy
import json
import math

import pytest

from ciw import measured_thermal as measured
from ciw import measured_thermal_workflow as workflow
from ciw import temperature_contract as temperature
from ciw.control_contracts import bytes_ref
from ciw.operations.registry import OperationRegistry
from ciw.operations.runner import digest, execute, seal
from ciw.session import Session, _recording_file
from ciw.temperature_processing import process_request
from ciw.temperature_workflow import PROCESS, make_source, operation
from ciw.temperature_recording_bridge import (
    APPLICATION_SCHEMA, UNCERTAINTY_SCOPE, bridge_temperature_recording,
)
from ciw.telemetry import canonical
from test_measured_thermal import _source, _session, _execute, _call, _rewrite_stream


def _retain(source, value):
    raw = canonical(value)
    ref = bytes_ref(raw)
    if not any(row["sha256"] == ref for row in source["artifacts"]):
        source["artifacts"].append({"sha256": ref, "bytes_b64": base64.b64encode(raw).decode("ascii")})
    return ref


def _inputs(channel="shell"):
    source = _source(shell_values=(300.0, 300.1), core_values=(301.0, 301.7),
                     heat_values=(100.0, 80.0), ambient_values=(290.0, 290.0))
    _retain(source, {"synthetic_temperature_fixture": 1})
    request = temperature.example_request()
    request["identity"]["sensor_id"] = source["channels"][channel]["device_id"]
    request["identity"]["clock_id"] = source["clock"]["id"]
    request["clock"].update(id=source["clock"]["id"], end_s=2.0)
    request["calibration"].update(sensor_id=request["identity"]["sensor_id"],
                                   clock_id=source["clock"]["id"], valid_time_s=[-10.0, 10.0])
    for i, row in enumerate(request["raw_observations"]["samples"]):
        row.update(time_s=float(i + 1), received_time_s=i + 1.1)
    return source, request, process_request(request)


def _artifacts(source):
    return {row["sha256"]: base64.b64decode(row["bytes_b64"]) for row in source["artifacts"]}


def test_bridge_retains_full_correlated_covariance_and_processing_lineage():
    source, request, report = _inputs()
    before = deepcopy((source, request, report))
    bridged = bridge_temperature_recording(source, request, report)
    assert (source, request, report) == before
    assert measured.validate_source(canonical(bridged)) == bridged
    assert bridged["request"] == source["request"]
    assert bridged["clock"] == source["clock"]
    assert bridged["frame"] == source["frame"]
    assert bridged["origin"] == "test_fixture"
    assert bridged["assumptions"] == measured.ASSUMPTIONS
    artifacts = _artifacts(bridged)
    channel = bridged["channels"]["shell"]
    application = json.loads(artifacts[channel["application_ref"]])
    assert application["schema"] == APPLICATION_SCHEMA
    assert application["request"] == request
    assert application["report"] == report
    assert application["numerical_audit"]["status"] == "PASS"
    assert application["authority"] == temperature.AUTHORITY
    assert application["report"]["acceptance"]["status"] == "indeterminate"
    assert "not acquisition" in application["raw_commitment_scope"].replace("_", " ")
    assert application["uncertainty_scope"] == UNCERTAINTY_SCOPE
    assert json.loads(artifacts[channel["raw_ref"]]) == request["raw_observations"]
    assert json.loads(artifacts[channel["calibration_ref"]]) == request["calibration"]
    full, = [value for raw in artifacts.values() if isinstance((value := _json_or_none(raw)), dict)
             and value.get("schema") == "covariance-artifact.v1"]
    assert full["matrix"] == report["calibrated_sensor_temperature"]["covariance_K2"]
    assert full["matrix"][0][1] > 0
    assert full["quantity_ids"] == ["shell_temperature:s0", "shell_temperature:s1"]
    assert full["provenance"]["metadata"]["time_s"] == [1.0, 2.0]
    rows = measured.streams(bridged)["shell"]["observations"]
    for i, row in enumerate(rows):
        assert row["value"] == report["calibrated_sensor_temperature"]["samples"][i]["value_K"]
        assert row["clock"] == {"id": source["clock"]["id"], "time_s": float(i + 1)}
        assert row["identity"]["execution_id"] is None
        assert row["quantity"] == "shell_temperature" and row["unit"] == "K"
        assert row["uncertainty"]["matrix"] == [[full["matrix"][i][i]]]
        assert row["uncertainty"]["provenance"]["source_covariance_ids"] == [full["covariance_id"]]
    for name in ("core_reference", "heat_power", "ambient"):
        assert bridged["channels"][name] == source["channels"][name]
        assert bridged["series"][name] == source["series"][name]


def _json_or_none(raw):
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        return None


def test_bridged_stream_is_consumed_by_native_session_and_replays(tmp_path):
    source, request, _ = _inputs()
    processing_source = make_source(request)
    registry = OperationRegistry()
    registry.register(operation())
    selection = {"revision": 0, "channel": "measurement_declaration", "interval_s": [0.0, 1.0]}
    execution, processing = execute(registry, processing_source, selection,
                                    _recording_file(processing_source), PROCESS, {})
    assert execution["status"] == "completed"
    bridged = bridge_temperature_recording(source, request, processing["data"])
    session, added, raw = _session(tmp_path / "original", bridged)
    bundle = _execute(session, added)
    step = bundle["steps"][0]
    assert base64.b64decode(bundle["source"]["evidence"][0]["bytes_b64"]) == raw
    data = step["result"]["data"]
    assert data["request"]["observations"] == [[None, 300.0], [None, 310.0]]
    assert data["request"]["observation_noise_covariance"] == source["request"]["observation_noise_covariance"]
    assert data["request"]["process_noise_covariance"] == source["request"]["process_noise_covariance"]
    assert data["recording"]["assumptions"]["input_model_clock_uncertainty"] == "retained_not_propagated_by_this_kernel"
    assert data["authority"] == workflow.AUTHORITY
    saved = session.save_workspace(tmp_path / "bridged.json")
    reopened = Session.from_workspace(saved, tmp_path / "reopened")
    assert _call(reopened, "bundle.get", {"bundle_id": bundle["bundle_digest"]}) == bundle
    replay = _call(reopened, "bundle.replay", {"bundle_id": bundle["bundle_digest"]})
    fresh = _call(reopened, "bundle.get", {"bundle_id": replay["bundle"]["bundle_id"]})
    assert fresh["steps"][0]["numerical_result_id"] == step["numerical_result_id"]
    assert fresh["steps"][0]["execution_id"] != step["execution_id"]


def test_changed_temperature_covariance_is_retained_but_does_not_change_thermal_posterior():
    source, request, report = _inputs()
    a = bridge_temperature_recording(source, request, report)
    changed = deepcopy(request)
    changed["joint_covariance"]["matrix"][0][0] *= 4
    b = bridge_temperature_recording(source, changed, process_request(changed))
    assert measured.streams(a)["shell"] != measured.streams(b)["shell"]
    assert measured.calculate(a)["observer"] == measured.calculate(b)["observer"]


@pytest.mark.parametrize("channel", ["shell", "core_reference", "ambient"])
def test_supported_temperature_roles_preserve_explicit_quantity(channel):
    source, request, report = _inputs(channel)
    bridged = bridge_temperature_recording(source, request, report, channel=channel)
    rows = measured.streams(bridged)[channel]["observations"]
    assert all(row["quantity"] == measured.CHANNELS[channel][0] for row in rows)
    assert measured.calculate(bridged)["authority"] == workflow.AUTHORITY


def test_held_out_bridge_cannot_condition_the_observer():
    source, request, report = _inputs("core_reference")
    before = measured.calculate(source)
    after = measured.calculate(bridge_temperature_recording(source, request, report, channel="core_reference"))
    assert before["observer"] == after["observer"]
    assert before["evaluation"]["errors_k"] != after["evaluation"]["errors_k"]


@pytest.mark.parametrize("mutation, message", [
    (lambda r: r["identity"].update(sensor_id="different"), "sensor"),
    (lambda r: (r["identity"].update(clock_id="different"), r["clock"].update(id="different"),
                r["calibration"].update(clock_id="different")), "clock"),
    (lambda r: r.update(source_kind="retained_observation"), "origins"),
    (lambda r: r["raw_observations"]["samples"][0].update(time_s=0.0), "timestamps"),
    (lambda r: r["raw_observations"]["samples"][0].update(received_time_s=20.0), "invalid"),
    (lambda r: r["raw_observations"]["samples"][0].update(status="missing", value=None), "invalid"),
    (lambda r: r["conditions"].update(ambient_temperature_K=400.0), "invalid"),
    (lambda r: r["conditions"].update(maximum_heating_rate_K_s=40.0), "invalid"),
    (lambda r: r["calibration"].update(sensor_id="wrong-calibration"), "invalid"),
])
def test_incompatible_or_inapplicable_declarations_refuse(mutation, message):
    source, request, _ = _inputs()
    mutation(request)
    with pytest.raises(ValueError, match=message):
        bridge_temperature_recording(source, request, process_request(request))


def test_wrong_count_refuses_without_truncation():
    source, request, report = _inputs()
    source = _source()
    _retain(source, {"synthetic_temperature_fixture": 1})
    with pytest.raises(ValueError, match="count"):
        bridge_temperature_recording(source, request, report)


@pytest.mark.parametrize("channel", ["heat_power", "polymer_temperature", "unknown"])
def test_cannot_relabel_temperature_as_unsupported_quantity(channel):
    source, request, report = _inputs()
    with pytest.raises(ValueError, match="requires shell"):
        bridge_temperature_recording(source, request, report, channel=channel)


def test_unresolved_temperature_evidence_is_not_laundered_into_retained_lineage():
    source, request, report = _inputs()
    evidence = request["joint_covariance"]["evidence_ref"]
    source["artifacts"] = [row for row in source["artifacts"] if row["sha256"] != evidence]
    with pytest.raises(ValueError, match="unresolved upstream evidence"):
        bridge_temperature_recording(source, request, report)


def test_resealed_wrong_arithmetic_fails_fresh_audit():
    source, request, report = _inputs()
    report["calibrated_sensor_temperature"]["samples"][0]["value_K"] += 1
    seal(report)
    with pytest.raises(ValueError, match="numerical audit failed"):
        bridge_temperature_recording(source, request, report)


@pytest.mark.parametrize("channel, limits", [("shell", [1.0, 10.0]), ("shell", [-10.0, 2.0]),
                                             ("ambient", [0.0, 10.0]), ("ambient", [0.5, 10.0])])
def test_calibration_covers_clock_radius_and_full_preceding_input_interval(channel, limits):
    source, request, _ = _inputs(channel)
    request["calibration"]["valid_time_s"] = limits
    report = process_request(request)
    assert report["applicability"]["calibration_status"] == "within_declared_applicability"
    with pytest.raises(ValueError, match="inapplicable over"):
        bridge_temperature_recording(source, request, report, channel=channel)


def test_unknown_clock_uncertainty_stays_unknown_and_never_becomes_zero():
    source, request, report = _inputs()
    source["clock"]["uncertainty_s"] = None
    bridged = bridge_temperature_recording(source, request, report)
    assert bridged["clock"]["uncertainty_s"] is None
    assert measured.calculate(bridged)["evaluation"]["clock_applicability"] == "declared_times_only"


def test_inward_timestamp_rounding_never_widens_closed_calibration():
    source, request, _ = _inputs()
    request["calibration"]["valid_time_s"] = [0.1234567, 9.1234567]
    bridged = bridge_temperature_recording(source, request, process_request(request))
    channel = bridged["channels"]["shell"]
    assert channel["valid_from"] == "2026-10-04T12:00:00.123457+00:00"
    assert channel["valid_until"] == "2026-10-04T12:00:09.123456+00:00"
    assert channel["range"] == [280.0, 350.0]


def test_declared_existing_range_cannot_be_widened_to_fit_new_temperature():
    source, request, report = _inputs()
    source["channels"]["shell"]["range"] = [290.0, 305.0]
    with pytest.raises(ValueError, match="intersection"):
        bridge_temperature_recording(source, request, report)


def test_upstream_reference_data_cannot_enter_inference_via_bridge():
    source, request, _ = _inputs()
    request["joint_covariance"]["evidence_ref"] = source["channels"]["core_reference"]["raw_ref"]
    with pytest.raises(ValueError, match="held-out"):
        bridge_temperature_recording(source, request, process_request(request))


def test_measured_origin_remains_observed_without_authentication_upgrade():
    source, request, _ = _inputs()
    source["origin"] = "measured"
    for channel in measured.CHANNELS:
        _rewrite_stream(source, channel, lambda rows: [row["provenance"].update(semantics="observed") for row in rows])
    request["source_kind"] = "retained_observation"
    bridged = bridge_temperature_recording(source, request, process_request(request))
    assert bridged["origin"] == "measured"
    assert measured.streams(bridged)["shell"]["observations"][0]["provenance"]["semantics"] == "observed"
    assert measured.calculate(bridged)["evaluation"]["calibration_authenticity"] == "not_established"


def test_native_artifact_budget_refuses_rather_than_pruning_retained_evidence():
    source, request, report = _inputs()
    while len(source["artifacts"]) < 32:
        _retain(source, {"extra_retained_declaration": len(source["artifacts"])})
    with pytest.raises(ValueError, match="bounded list"):
        bridge_temperature_recording(source, request, report)


def test_non_ascii_sample_and_calibration_names_preserve_original_commitment_hashes():
    source, request, _ = _inputs()
    request["raw_observations"]["samples"][0]["sample_id"] = "温度-α"
    request["joint_covariance"]["order"][0] = "raw:温度-α"
    request["calibration"]["calibration_id"] = "校准-β"
    report = process_request(request)
    bridged = bridge_temperature_recording(source, request, report)
    channel = bridged["channels"]["shell"]
    assert channel["raw_ref"] == report["identities"]["raw_observations_ref"]
    assert channel["calibration_ref"] == report["identities"]["calibration_ref"]
    assert measured.streams(bridged)["shell"]["observations"][0]["value"] == 300.0


def test_existing_tighter_validity_is_preserved_and_not_replaced_by_calibration():
    source, request, report = _inputs()
    source["channels"]["shell"].update(valid_from="2026-10-04T12:00:00.500000Z",
                                       valid_until="2026-10-04T12:00:02.500000Z")
    bridged = bridge_temperature_recording(source, request, report)
    channel = bridged["channels"]["shell"]
    assert channel["valid_from"] == "2026-10-04T12:00:00.500000+00:00"
    assert channel["valid_until"] == "2026-10-04T12:00:02.500000+00:00"


def test_temperature_acceptance_failure_is_retained_and_never_becomes_qualification():
    source, request, _ = _inputs()
    for item in request["budget"]:
        if item["status"] == "omitted":
            item.update(status="not_applicable", reason="Synthetic declaration only",
                        evidence_ref=request["joint_covariance"]["evidence_ref"])
    request["acceptance"].update(lower=320.0, upper=330.0)
    report = process_request(request)
    assert report["acceptance"]["status"] == "conditional_fail"
    bridged = bridge_temperature_recording(source, request, report)
    application = json.loads(_artifacts(bridged)[bridged["channels"]["shell"]["application_ref"]])
    assert application["report"]["acceptance"]["status"] == "conditional_fail"
    assert application["authority"]["physical_qualification"] == "not_established"
    assert measured.calculate(bridged)["authority"] == workflow.AUTHORITY


@pytest.mark.parametrize("path,replacement", [
    (("raw_observations", "samples", 0, "time_s"), True),
    (("raw_observations", "samples", 0, "time_s"), 1),
    (("raw_observations", "samples", 0, "value"), True),
    (("raw_observations", "samples", 0, "value"), 1),
    (("calibrated_sensor_temperature", "samples", 0, "time_s"), True),
    (("calibrated_sensor_temperature", "samples", 0, "time_s"), 1),
])
def test_python_equal_aliases_cannot_replace_exact_retained_source_copies(path, replacement):
    source, request, report = _inputs()
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    seal(report)
    with pytest.raises(ValueError, match="exact source copy"):
        bridge_temperature_recording(source, request, report)


def test_fixed_numerical_contract_cannot_be_redeclared_with_numeric_type_aliases():
    source, request, report = _inputs()
    report["numerical_contract"]["maximum_samples"] = float(report["numerical_contract"]["maximum_samples"])
    report["identities"]["procedure_ref"] = digest(report["numerical_contract"])
    seal(report)
    with pytest.raises(ValueError, match="exact declaration"):
        bridge_temperature_recording(source, request, report)


def test_exact_copy_checks_do_not_require_bitwise_identity_of_computed_numerical_values():
    source, request, report = _inputs()
    row = report["calibrated_sensor_temperature"]["samples"][0]
    row["value_K"] = math.nextafter(row["value_K"], math.inf)
    seal(report)
    bridged = bridge_temperature_recording(source, request, report)
    assert measured.streams(bridged)["shell"]["observations"][0]["value"] == row["value_K"]
    application = json.loads(_artifacts(bridged)[bridged["channels"]["shell"]["application_ref"]])
    assert application["numerical_audit"]["status"] == "PASS"



def test_boolean_alias_is_not_a_computed_acceptance_interval_endpoint():
    source, request, _ = _inputs()
    request["calibration"].update(gain=1.0, offset_K=0.0, coefficient_covariance=[[0.0, 0.0], [0.0, 0.0]],
                                   output_range_K=[0.1, 10.0])
    request["joint_covariance"]["matrix"] = [[0.0] * 4 for _ in range(4)]
    for item in request["budget"]:
        if item["status"] == "omitted":
            item.update(status="not_applicable", reason="Synthetic declaration only",
                        evidence_ref=request["joint_covariance"]["evidence_ref"])
    request["acceptance"].update(lower=0.5, upper=3.0)
    report = process_request(request)
    assert report["acceptance"]["sample_results"][0]["expanded_interval_K"] == [1.0, 1.0]
    report["acceptance"]["sample_results"][0]["expanded_interval_K"][0] = True
    seal(report)
    with pytest.raises(ValueError, match="not a boolean"):
        bridge_temperature_recording(source, request, report)
