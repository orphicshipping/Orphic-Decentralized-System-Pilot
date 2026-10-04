"""Measured thermal ingress, held-out isolation and retained Session regressions."""
from copy import deepcopy
import base64
from importlib.resources import files
import json

import pytest

from ciw import measured_thermal as measured
from ciw import measured_thermal_workflow as workflow
from ciw import thermal_contract as thermal
from ciw import thermal_workflow
from ciw.control_checks import inspect_record
from ciw.control_contracts import bytes_ref, observation
from ciw.core.covariance import create_covariance_artifact
from ciw.core.identities import content_identity
from ciw.instruments import make_demo_run
from ciw.operations.runner import seal
from ciw.session import Session
from ciw.telemetry import canonical, digest


def _source(*, shell_values=(300.0, 300.1, 300.2),
            core_values=(301.0, 301.7, 302.0),
            heat_values=(100.0, 80.0, 60.0),
            ambient_values=(290.0, 290.0, 290.0)):
    """A fully retained simulated fixture; never a claim of physical acquisition."""
    request = {
        "schema": thermal.REQUEST_SCHEMA, "operation_id": thermal.OPERATION_ID,
        "model": {"state_order": thermal.STATE_ORDER, "state_units": ["K", "K"],
                  "input_order": thermal.INPUT_ORDER, "input_units": ["W", "K"],
                  "sensor_order": thermal.SENSOR_ORDER, "sensor_units": ["K", "K"],
                  "capacities_j_per_k": [100.0, 200.0],
                  "conductances_w_per_k": [10.0, 5.0],
                  "observation_matrix": [[1.0, 0.0], [0.0, 1.0]]},
        "sample_interval_s": 1.0,
        "prior": {"mean": [300.0, 300.0], "covariance": [[4.0, 0.2], [0.2, 9.0]]},
        "process_noise_covariance": [[0.1, 0.0], [0.0, 0.2]],
        "observation_noise_covariance": [[0.5, 0.0], [0.0, 0.75]],
        "selection": {"costs": [1.0, 2.0], "budget": 2.0, "minimum_sensors": 1,
                      "tie_tolerance_nats": 1e-10,
                      "tie_policy": "lowest_mask_within_tolerance"},
    }
    artifacts = []

    def retain(raw):
        ref = bytes_ref(raw)
        artifacts.append({"sha256": ref, "bytes_b64": base64.b64encode(raw).decode("ascii")})
        return ref

    model_ref = retain(b"Simulated test fixture: declared RC model, prior and noise; not fitted to reference.\n")
    held_ref = retain(b"Simulated test protocol: core reference withheld from fitting, tuning and conditioning.\n")
    criteria_ref = retain(b"Simulated test criterion: absolute core error at most 5 K; no physical validation.\n")
    model_id = content_identity(request["model"])
    series, channels = {}, {}
    values = {"shell": shell_values, "core_reference": core_values,
              "heat_power": heat_values, "ambient": ambient_values}
    for name, (quantity, unit) in measured.CHANNELS.items():
        raw_ref = retain(canonical({"fixture_channel": name, "raw_values": list(values[name])}))
        calibration_ref = retain((name + ": simulated calibration declaration, unit " + unit + "\n").encode())
        application_ref = retain((name + ": simulated values already calibrated; application not recomputed\n").encode())
        refs = [raw_ref, calibration_ref, application_ref]
        device = "fixture-device:" + name
        channels[name] = {"device_id": device, "raw_ref": raw_ref,
                          "calibration_ref": calibration_ref, "application_ref": application_ref,
                          "valid_from": "2026-10-04T00:00:00Z",
                          "valid_until": "2026-10-05T00:00:00Z",
                          "range": [-1000.0, 1000.0] if name == "heat_power" else [0.0, 1000.0],
                          "quantity": quantity, "unit": unit}
        rows = []
        for i, value in enumerate(values[name]):
            uncertainty = None if value is None else create_covariance_artifact(
                matrix=[[0.25]], quantity_ids=[quantity], units=[unit],
                frame="frame:thermal-fixture", reference_values=[value],
                method="declared_fixture_calibrated_marginal",
                basis={"kind": "calibrated_observation", "id": name + ":" + str(i)},
                provenance={"provider": device, "source_evidence_ids": refs,
                            "source_covariance_ids": []},
                assumptions=["Simulated fixture marginal; no physical calibration authenticity"])
            rows.append(observation(
                identity={"model_id": model_id, "entity_id": "asset:thermal-fixture",
                          "execution_id": "fixture-acquisition:" + name},
                clock={"id": "clock:thermal-fixture", "time_s": float(i + 1)},
                frame="frame:thermal-fixture", quantity=quantity, value=value, unit=unit,
                uncertainty=uncertainty,
                provenance={"provider": device, "sources": refs, "semantics": "simulated"}))
        series[name] = retain(canonical(seal({"schema": "ciw.observation-stream.v1", "observations": rows})))
    return {"schema": measured.SOURCE_SCHEMA, "experiment_id": "measured-thermal:test-fixture",
            "configuration": deepcopy(measured.POLICY), "origin": "test_fixture",
            "asset_id": "asset:thermal-fixture", "configuration_id": "configuration:thermal-fixture",
            "clock": {"id": "clock:thermal-fixture", "epoch_utc": "2026-10-04T12:00:00Z",
                      "uncertainty_s": 0.01},
            "frame": "frame:thermal-fixture", "request": request, "series": series,
            "channels": channels, "artifacts": artifacts, "model_evidence_refs": [model_ref],
            "assumptions": deepcopy(measured.ASSUMPTIONS),
            "held_out": {"excluded_from": ["model_fit", "prior", "noise_tuning", "conditioning", "acceptance_selection"],
                         "evidence_refs": [held_ref]},
            "acceptance": {"atol_k": 5.0, "rtol": 0.0, "criteria_ref": criteria_ref}}


def _rewrite_stream(source, name, mutate):
    """Make a coherently sealed but semantically challenged stream."""
    old_ref = source["series"][name]
    artifact = next(row for row in source["artifacts"] if row["sha256"] == old_ref)
    stream = json.loads(base64.b64decode(artifact["bytes_b64"]))
    mutate(stream["observations"])
    stream["observations"] = [seal(row) for row in stream["observations"]]
    raw = canonical(seal(stream))
    artifact.update(sha256=bytes_ref(raw), bytes_b64=base64.b64encode(raw).decode("ascii"))
    source["series"][name] = artifact["sha256"]


def _request(session, kind, payload=None):
    return session.handle({"protocol_version": 1, "request_id": "test-" + kind,
                           "type": kind, "payload": {} if payload is None else payload})


def _call(session, kind, payload=None):
    response = _request(session, kind, payload)
    assert response["type"] == "response", response
    return response["payload"]


def _session(tmp_path, source=None):
    session = Session(make_demo_run(), tmp_path)
    raw = canonical(_source() if source is None else source) + b"\n"
    added = _call(session, "source.add", {"kind": workflow.KIND, "label": "Simulated measured-ingress fixture",
                 "bytes_b64": base64.b64encode(raw).decode("ascii")})
    return session, added, raw


def _execute(session, source):
    completed = _call(session, "operation.execute", {"operation_id": workflow.OPERATION,
                      "parameters": {"source_id": source["source_id"]}})
    return _call(session, "bundle.get", {"bundle_id": completed["bundle_id"]})


def test_fixture_retains_exact_typed_streams_and_covariance():
    source = _source()
    assert measured.validate_source(canonical(source)) == source
    retained = measured.streams(source)
    assert set(retained) == {"shell", "core_reference", "heat_power", "ambient"}
    for name, stream in retained.items():
        inspect_record(stream)
        row = stream["observations"][0]
        assert row["quantity"] == measured.CHANNELS[name][0]
        assert row["uncertainty"]["matrix"] == [[0.25]]
        assert row["provenance"]["semantics"] == "simulated"
    request = measured.execution_request(source)
    assert request["observations"] == [[None, 300.0], [None, 300.1], [None, 300.2]]
    assert request["inputs"] == [[100.0, 290.0], [80.0, 290.0], [60.0, 290.0]]


def test_reference_values_and_thresholds_never_condition_estimator():
    original = _source()
    changed_reference = _source(core_values=(311.0, 311.7, 312.0))
    changed_criterion = deepcopy(original)
    changed_criterion["acceptance"]["atol_k"] = 0.0
    values = [measured.calculate(x) for x in (original, changed_reference, changed_criterion)]
    for value in values[1:]:
        for field in ("request", "model", "observer", "selection"):
            assert value[field] == values[0][field]
    assert values[0]["evaluation"]["errors_k"] != values[1]["evaluation"]["errors_k"]
    assert values[0]["evaluation"]["status"] == "PASS"
    assert values[1]["evaluation"]["status"] == "FAIL"
    assert values[2]["evaluation"]["status"] == "FAIL"
    for value in values:
        assert value["authority"] == workflow.AUTHORITY
        assert value["evaluation"]["calibration_authenticity"] == "not_established"
        assert value["evaluation"]["uncertainty_coverage"] == "not_established"


def test_all_missing_shell_is_explicit_prediction_only():
    source = _source(shell_values=(None, None, None))
    data = measured.calculate(source)
    assert data["evaluation"]["estimation_mode"] == "prediction_only"
    for row in data["observer"]["trace"]:
        assert row["available_mask"] == 0
        assert row["posterior_mean"] == row["predicted_mean"]
        assert row["posterior_covariance"] == row["predicted_covariance"]
        assert row["nis"] is None
    assert data["authority"]["physical_validation"] == "not_established"


def test_missing_reference_and_undeclared_criteria_stay_indeterminate():
    missing = measured.calculate(_source(core_values=(None, None, None)))
    assert missing["evaluation"]["status"] == "INDETERMINATE"
    assert missing["evaluation"]["reference_sample_count"] == 0
    assert missing["evaluation"]["rmse_k"] is None
    source = _source()
    source["acceptance"] = None
    source["clock"]["uncertainty_s"] = None
    data = measured.calculate(source)
    assert data["evaluation"]["status"] == "INDETERMINATE"
    assert data["evaluation"]["reason"] == "criteria_not_declared"
    assert data["recording"]["clock"]["uncertainty_s"] is None
    assert data["evaluation"]["clock_applicability"] == "declared_times_only"


@pytest.mark.parametrize("channel", ["heat_power", "ambient"])
def test_missing_driving_input_refuses_instead_of_imputing(channel):
    source = _source(**{("heat_values" if channel == "heat_power" else "ambient_values"): (None, 290.0, 290.0)})
    with pytest.raises(ValueError):
        measured.validate_source(canonical(source))


@pytest.mark.parametrize("field,value", [("origin", "measured"), ("asset_id", "asset:other"), ("frame", "frame:other")])
def test_origin_identity_or_frame_contradiction_refuses(field, value):
    source = _source()
    source[field] = value
    with pytest.raises(ValueError, match="identity, clock, frame, unit or origin"):
        measured.validate_source(canonical(source))


def test_irregular_clock_refuses_even_with_coherent_stream_seals():
    source = _source()
    _rewrite_stream(source, "shell", lambda rows: rows[1]["clock"].update(time_s=2.25))
    with pytest.raises(ValueError, match="clock"):
        measured.validate_source(canonical(source))


@pytest.mark.parametrize("channel,field,value", [
    ("heat_power", "valid_from", "2026-10-04T12:00:00Z"),
    ("ambient", "valid_until", "2026-10-04T12:00:03Z"),
    ("shell", "valid_from", "2026-10-04T12:00:01Z"),
])
def test_calibration_covers_full_input_interval_and_clock_uncertainty(channel, field, value):
    source = _source()
    source["channels"][channel][field] = value
    with pytest.raises(ValueError, match="Calibration is inapplicable"):
        measured.validate_source(canonical(source))


def test_artifact_hash_and_dangling_reference_refuse():
    source = _source()
    changed = deepcopy(source)
    changed["artifacts"][0]["bytes_b64"] = base64.b64encode(b"changed raw evidence").decode()
    with pytest.raises(ValueError, match="artifact bytes differ"):
        measured.validate_source(canonical(changed))
    removed = source["channels"]["ambient"]["calibration_ref"]
    source["artifacts"] = [x for x in source["artifacts"] if x["sha256"] != removed]
    with pytest.raises(ValueError, match="dangling evidence"):
        measured.validate_source(canonical(source))


@pytest.mark.parametrize("field", ["raw_ref", "application_ref", "device_id"])
def test_shared_reference_lineage_refuses_even_when_declared_consistently(field):
    source = _source()
    old = source["channels"]["core_reference"][field]
    new = source["channels"]["shell"][field]
    source["channels"]["core_reference"][field] = new
    def replace(rows):
        for row in rows:
            if field == "device_id":
                row["provenance"]["provider"] = new
            else:
                row["provenance"]["sources"] = [new if ref == old else ref for ref in row["provenance"]["sources"]]
    _rewrite_stream(source, "core_reference", replace)
    with pytest.raises(ValueError, match="distinct device and data lineage|Inference recording references held-out data"):
        measured.validate_source(canonical(source))


@pytest.mark.parametrize("reference_kind", ["stream", "raw"])
def test_reference_cannot_be_declared_model_fit_evidence(reference_kind):
    source = _source()
    ref = (source["series"]["core_reference"] if reference_kind == "stream"
           else source["channels"]["core_reference"]["raw_ref"])
    source["model_evidence_refs"].append(ref)
    with pytest.raises(ValueError, match="cannot justify fitted"):
        measured.validate_source(canonical(source))


def test_request_arrays_cannot_replace_bound_observations():
    source = _source()
    source["request"]["observations"] = [[301.0, 300.0]] * 3
    with pytest.raises(ValueError, match="Unexpected or missing contract fields"):
        measured.validate_source(canonical(source))


def test_posterior_export_keeps_reference_out_of_conditioning_provenance():
    source = _source()
    data = measured.calculate(source)
    exported = measured.observation_stream(source, data, stage="posterior", execution_id="execution:test-export")
    inspect_record(exported)
    for row in exported["observations"]:
        assert row["quantity"] == "core_temperature"
        assert source["series"]["core_reference"] not in row["provenance"]["sources"]
        assert row["identity"]["execution_id"] == "execution:test-export"
        assert row["uncertainty"]["quantity_ids"] == ["core_temperature"]


def test_session_save_reopen_and_replay_keep_exact_bytes_and_fresh_identities(tmp_path, monkeypatch):
    session, added, raw = _session(tmp_path / "original")
    original = _execute(session, added)
    step = original["steps"][0]
    assert base64.b64decode(original["source"]["evidence"][0]["bytes_b64"]) == raw
    assert step["input_refs"] == [bytes_ref(raw)]
    assert original["verification"]["independent"] is False
    assert original["verification"]["authority"] == workflow.AUTHORITY
    assert original["verification"]["reproduction"]["execution_id"] != step["execution_id"]
    saved = session.save_workspace(tmp_path / "saved.json")
    saved_bytes = saved.read_bytes()
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            pytest.fail("Read-only restore entered measured thermal execution")
        patch.setattr(measured, "calculate", forbidden)
        patch.setattr(workflow.MeasuredThermalWorkflow, "create_session", forbidden)
        patch.setattr(workflow.MeasuredThermalWorkflow, "replay_session", forbidden)
        reopened = Session.from_workspace(saved, tmp_path / "reopened")
        assert _call(reopened, "bundle.get", {"bundle_id": original["bundle_digest"]}) == original
        assert _call(reopened, "result.get", {"result_id": step["result_id"]}) == step["result"]
        assert _call(reopened, "dependency.inspect")["state_admission"] == "not_performed"
    assert saved.read_bytes() == saved_bytes
    replay = _call(reopened, "bundle.replay", {"bundle_id": original["bundle_digest"]})
    fresh = _call(reopened, "bundle.get", {"bundle_id": replay["bundle"]["bundle_id"]})
    assert fresh["bundle_digest"] != original["bundle_digest"]
    fresh_step = fresh["steps"][0]
    assert fresh_step["execution_id"] != step["execution_id"]
    assert fresh_step["result_id"] != step["result_id"]
    assert fresh_step["numerical_result_id"] == step["numerical_result_id"]
    assert fresh_step["numerical_result"] == step["numerical_result"]
    assert replay["replay_receipt"]["verification"]["reproduction"] == fresh_step
    assert replay["replay_receipt"]["admission"] == "not_performed"


def test_changed_runtime_refuses_replay_before_calculation(tmp_path, monkeypatch):
    session, added, _ = _session(tmp_path)
    original = _execute(session, added)
    identity = workflow.runtime_identity()
    identity["algorithm"]["code_sha256"] = "0" * 64
    monkeypatch.setattr(workflow, "runtime_identity", lambda: deepcopy(identity))
    monkeypatch.setattr(measured, "calculate", lambda *args: pytest.fail("Runtime mismatch executed calculation"))
    response = _request(session, "bundle.replay", {"bundle_id": original["bundle_digest"]})
    assert response["type"] == "error"
    assert "runtime identity differs" in response["payload"]["message"]
    assert _call(session, "bundle.list")["bundles"][0]["bundle_id"] == original["bundle_digest"]


def test_failed_calculation_is_retained_without_result_then_retry_is_fresh(tmp_path, monkeypatch):
    session, added, _ = _session(tmp_path / "original")
    with monkeypatch.context() as patch:
        def fail(*args):
            raise ValueError("declared measured fixture calculation failure")
        patch.setattr(measured, "calculate", fail)
        response = _request(session, "operation.execute", {"operation_id": workflow.OPERATION,
                            "parameters": {"source_id": added["source_id"]}})
        assert response["type"] == "error"
    failed, = session.workbench.list_failed_executions()
    assert failed["schema"] == "ciw.workflow-failure.v1"
    assert failed["operation_id"] == workflow.OPERATION
    assert failed["result_id"] is None and failed["bundle_id"] is None
    assert not _call(session, "bundle.list")["bundles"]
    saved = session.save_workspace(tmp_path / "failed.json")
    reopened = Session.from_workspace(saved, tmp_path / "reopened")
    assert reopened.workbench.list_failed_executions() == [failed]
    fresh = _execute(reopened, added)
    assert fresh["steps"][0]["execution_id"] != failed["execution_id"]
    assert reopened.workbench.list_failed_executions() == [failed]


def test_coherently_resealed_data_and_replay_runtime_tampering_refuse():
    engine = workflow.MeasuredThermalWorkflow()
    original = engine.create_session(canonical(_source()), {})
    data = deepcopy(original["steps"][0]["result"]["data"])
    data["observer"]["trace"][0]["posterior_mean"][0] += 1.0
    with pytest.raises(ValueError, match="differs from retained inputs|differs from the independent analytical reference"):
        measured.validate_data(_source(), data)
    fresh = engine.replay_session(original, {})["session"]
    receipt = fresh["replay_receipts"][0]
    verification = receipt["verification"]
    verification["runtime_digest"] = "sha256:" + "0" * 64
    from ciw.telemetry import byte_digest
    verification["verification_id"] = byte_digest(workflow.VERIFY_SCHEMA.encode() + b"\0" + canonical(
        {k: v for k, v in verification.items() if k != "verification_id"}))
    receipt["replay_id"] = digest({k: v for k, v in receipt.items() if k != "replay_id"})
    with pytest.raises(ValueError, match="replay verification scope"):
        engine._validate(fresh)


def test_legacy_synthetic_workflow_stays_separate(monkeypatch):
    raw = files("ciw").joinpath("operator_examples", "thermal-source.json").read_bytes()
    source = thermal.validate_source(raw)
    assert source["schema"] != measured.SOURCE_SCHEMA
    monkeypatch.setattr(measured, "calculate", lambda *args: pytest.fail("Synthetic path used measured ingress"))
    bundle = thermal_workflow.ThermalWorkflow().create_session(raw, {})
    assert bundle["schema"] == "ciw.thermal-observer-session.v1"
    assert bundle["steps"][0]["operation_id"] == "ciw.thermal-observer.v1"
    assert base64.b64decode(bundle["source"]["evidence"][0]["bytes_b64"]) == raw
