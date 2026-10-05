"""Production bindings must not promote numerical success into physical authority."""
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from ciw import polymer_microfab as microfab
from ciw import polymer_microfab_contract as contract
from ciw import polymer_workflow as workflow
from ciw.control_contracts import save_new
from ciw.operations.runner import digest, seal
from ciw.polymer_copilot import example_knowledge


@pytest.fixture
def request_data():
    return contract.example_request()


@pytest.fixture
def retained(request_data, tmp_path):
    path = tmp_path / "retained"
    dossier = microfab.run(request_data, path)
    return path, dossier


def second_part(request_data, *, same_shot=False):
    data = deepcopy(request_data)
    data["order"]["quantity"] = 2
    entry = deepcopy(data["entries"][0])
    entry["serial_id"] = "synthetic.serial.002"
    entry["cycle"]["identity"]["part_id"] = entry["serial_id"]
    if same_shot:
        entry["cavity_id"] = "cavity.2"
        data["cell"]["cavity_ids"].append("cavity.2")
    else:
        entry["cycle"]["identity"]["cycle_id"] = "synthetic.cycle.11"
    entry["cycle"]["knowledge"] = example_knowledge(entry["cycle"]["identity"])
    for sensor in entry["cycle"]["sensors"]:
        sensor["source_ref"] = digest({"part": entry["serial_id"], "sensor": sensor["sensor_id"]})
    entry["followup"]["source_ref"] = digest({"followup": entry["serial_id"]})
    data["entries"].append(entry)
    return data


def at(value, path):
    for key in path:
        value = value[key]
    return value


@pytest.mark.parametrize("path,value", [
    (("order", "quantity"), True),
    (("order", "quantity"), 0),
    (("order", "quantity"), 17),
    (("order", "work_order_id"), "bad\nidentifier"),
    (("part", "revision"), ""),
    (("part", "drawing_ref"), "unhashed"),
    (("cell", "cavity_ids"), ["cavity.1", "cavity.1"]),
    (("cell", "process"), "extrusion_blow_molding"),
    (("cell", "model_ref"), "sha256:" + "0" * 64),
    (("entries", 0, "serial_id"), "wrong.part"),
    (("entries", 0, "cavity_id"), "missing.cavity"),
    (("entries", 0, "cycle", "identity", "material_lot_id"), "different.lot"),
    (("entries", 0, "cycle", "identity", "machine_id"), "different.machine"),
    (("entries", 0, "cycle", "identity", "tool_id"), "different.tool"),
    (("entries", 0, "cycle", "tolerances", 0, "upper"), 0.5),
    (("entries", 0, "cycle", "sensors", 0, "unit"), "degC"),
    (("entries", 0, "cycle", "control", "mode"), "live"),
    (("entries", 0, "followup", "time_s"), 10),
    (("entries", 0, "followup", "time_s"), 15.0001),
    (("entries", 0, "followup", "frame"), "wrong.frame"),
    (("entries", 0, "followup", "clock_id"), "wrong.clock"),
    (("entries", 0, "followup", "unit"), "degC"),
    (("entries", 0, "followup", "quantity"), "mold_temperature"),
    (("entries", 0, "followup", "origin"), "observed"),
    (("entries", 0, "followup", "value"), float("nan")),
    (("entries", 0, "followup", "value"), 0),
    (("entries", 0, "followup", "standard_uncertainty"), None),
    (("entries", 0, "followup", "standard_uncertainty"), -1),
    (("entries", 0, "followup", "standard_uncertainty"), True),
    (("entries", 0, "followup", "calibration_ref"), "sha256:" + "0" * 64),
    (("entries", 0, "followup", "clock_ref"), "sha256:" + "0" * 64),
    (("entries", 0, "ingress"), {}),
    (("residual_policy", "coverage_factor"), 0),
    (("residual_policy", "absolute_tolerance_k"), 0),
])
def test_invalid_input_is_refused_before_output(request_data, tmp_path, path, value):
    at(request_data, path[:-1])[path[-1]] = value
    out = tmp_path / "must-not-exist"
    with pytest.raises((ValueError, KeyError, TypeError)):
        microfab.run(request_data, out)
    assert not out.exists()


def test_unknown_fields_cannot_enable_machine_authority(request_data):
    request_data["cell"]["opc_ua_write"] = True
    with pytest.raises(ValueError):
        contract.validate_request(request_data)


def test_input_detached(request_data):
    validated = contract.validate_request(request_data)
    validated["part"]["revision"] = "B"
    assert request_data["part"]["revision"] == "A"


def test_followup_cannot_reuse_model_evidence(request_data):
    request_data["entries"][0]["followup"]["source_ref"] = request_data["entries"][0]["cycle"]["sensors"][0]["source_ref"]
    with pytest.raises(ValueError, match="model-input"):
        contract.validate_request(request_data)


def test_duplicate_serial(request_data):
    request_data["order"]["quantity"] = 2
    request_data["entries"].append(deepcopy(request_data["entries"][0]))
    with pytest.raises(ValueError, match="Duplicate serial"):
        contract.validate_request(request_data)


def test_duplicate_cycle_cavity_slot(request_data):
    data = second_part(request_data)
    data["entries"][1]["cycle"]["identity"]["cycle_id"] = data["entries"][0]["cycle"]["identity"]["cycle_id"]
    with pytest.raises(ValueError, match="production slot"):
        contract.validate_request(data)


def test_same_shot_can_bind_distinct_cavities(request_data, tmp_path):
    data = second_part(request_data, same_shot=True)
    result = microfab.run(data, tmp_path / "multi")
    assert result["retained_quantity"] == 2
    assert len({x["cavity_id"] for x in result["parts"]}) == 2
    assert result["engineering_review"] == "READY_FOR_ENGINEERING_REVIEW"


def test_same_shot_inconsistent_control_refused(request_data):
    data = second_part(request_data, same_shot=True)
    data["entries"][1]["cycle"]["control"]["parameter"]["current"] += 1
    with pytest.raises(ValueError, match="one shot disagree"):
        contract.validate_request(data)


def test_reused_part_evidence_refused(request_data):
    data = second_part(request_data)
    data["entries"][1]["cycle"]["sensors"][0]["source_ref"] = data["entries"][0]["cycle"]["sensors"][0]["source_ref"]
    with pytest.raises(ValueError, match="reused"):
        contract.validate_request(data)


def test_reused_followup_refused(request_data):
    data = second_part(request_data)
    data["entries"][1]["followup"]["source_ref"] = data["entries"][0]["followup"]["source_ref"]
    with pytest.raises(ValueError, match="reused"):
        contract.validate_request(data)


def test_no_mixing_observed_and_synthetic(request_data):
    data = second_part(request_data)
    entry = data["entries"][1]
    entry["cycle"]["source_kind"] = "retained_observation"
    for sensor in entry["cycle"]["sensors"]:
        sensor["origin"] = "observed"
    entry["followup"]["origin"] = "observed"
    with pytest.raises(ValueError, match="Synthetic and observed"):
        contract.validate_request(data)


def test_budget_checked_before_execution(request_data, monkeypatch):
    monkeypatch.setattr(contract, "MAX_SAMPLES", 1)
    with pytest.raises(ValueError, match="work budget"):
        contract.validate_request(request_data)


def test_aggregate_simulation_budget(request_data, monkeypatch):
    monkeypatch.setattr(contract, "MAX_SIMULATED_CYCLES", 1)
    with pytest.raises(ValueError, match="work budget"):
        contract.validate_request(request_data)


def test_byte_budget(request_data, monkeypatch):
    monkeypatch.setattr(contract, "MAX_BYTES", 100)
    with pytest.raises(ValueError, match="4 MiB"):
        contract.validate_request(request_data)


def test_run_reuses_four_existing_session_operations(retained):
    directory, dossier = retained
    assert dossier["workflow_status"] == "PASS"
    assert dossier["engineering_review"] == "READY_FOR_ENGINEERING_REVIEW"
    assert dossier["source_kind"] == "synthetic"
    row = dossier["parts"][0]
    assert {o["operation_id"] for o in row["occurrences"]} == workflow.OPERATIONS
    assert len({o["execution_id"] for o in row["occurrences"]}) == 4
    assert row["proposal_execution"] == "NOT_AUTHORIZED"
    assert row["supervisory_proposal"]["authority"]["plc_write"] is False
    assert dossier["authority"] == contract.AUTHORITY
    assert dossier["authority"]["product_release"] == "not_authorized"
    assert microfab.inspect(directory) == dossier


def test_inspect_never_executes_provider(retained, monkeypatch):
    directory, dossier = retained
    def forbidden(*args, **kwargs):
        pytest.fail("read-only inspection attempted numerical execution")
    monkeypatch.setattr(workflow, "run", forbidden)
    monkeypatch.setattr(workflow, "verify_retained", forbidden)
    assert microfab.inspect(directory) == dossier


def test_replay_preserves_evidence_but_allocates_new_occurrences(retained, tmp_path):
    directory, first = retained
    second = microfab.replay(directory, tmp_path / "replay")
    assert first["request_ref"] == second["request_ref"]
    assert first["parts"][0]["source_evidence_id"] == second["parts"][0]["source_evidence_id"]
    assert first["parts"][0]["occurrences"] != second["parts"][0]["occurrences"]
    assert first["parts"][0]["residual"] == second["parts"][0]["residual"]


def test_fresh_verification_does_not_rewrite_workspace(retained):
    directory, _ = retained
    before = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    audit = microfab.verify_retained(directory)
    assert audit["status"] == "PASS"
    assert audit["fresh_numerical_verification"] is True
    assert len(audit["cycle_audits"]) == 1
    assert audit["authority"]["physical_validation"] == "not_established"
    after = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    assert before == after


def test_residual_independent_decimal_interval(request_data):
    entry = request_data["entries"][0]
    entry["followup"].update(value=323.0, standard_uncertainty=0.5)
    cooling = {"status": "ESTIMATED", "temperature_k": 322.0, "standard_uncertainty_k": 0.75,
               "timing_temperature_bound_k": 0.125}
    policy = {"coverage_factor": 2.0, "absolute_tolerance_k": 8.0}
    result = microfab._residual(entry, {"engineering": {"cooling": cooling}}, policy)
    expected_radius = Decimal(2) * (Decimal("0.5") + Decimal("0.75")) + Decimal("0.125")
    assert result["interval_k"] == [float(Decimal(1) - expected_radius), float(Decimal(1) + expected_radius)]
    assert result["standard_uncertainty_upper_bound_k"] == 1.25
    assert result["status"] == "CONFORMING"
    assert result["coverage_probability"] == "not_validated"


@pytest.mark.parametrize("reading,status", [(10.0, "CONFORMING"), (11.0, "INDETERMINATE"),
                                           (12.0, "INDETERMINATE"), (12.1, "NONCONFORMING")])
def test_residual_boundary_policy(request_data, reading, status):
    entry = request_data["entries"][0]
    entry["followup"].update(value=reading, standard_uncertainty=0.5)
    cooling = {"status": "ESTIMATED", "temperature_k": 10., "standard_uncertainty_k": 0.0,
               "timing_temperature_bound_k": 0.0}
    result = microfab._residual(entry, {"engineering": {"cooling": cooling}},
                              {"coverage_factor": 2.0, "absolute_tolerance_k": 1.0})
    # [0,2] overlaps; [1,3] touches the tolerance boundary and is not wholly outside.
    assert result["status"] == status


def test_missing_followup_holds_order(request_data, tmp_path):
    request_data["entries"][0]["followup"] = None
    result = microfab.run(request_data, tmp_path / "missing")
    assert result["workflow_status"] == "PASS"
    assert result["engineering_review"] == "HOLD_INDETERMINATE_EVIDENCE"
    assert result["parts"][0]["residual"]["reason"] == "MISSING_FOLLOWUP"


def test_missing_quality_sensor_holds_order(request_data, tmp_path):
    for sensor in request_data["entries"][0]["cycle"]["sensors"]:
        if sensor["quantity"] == "part_dimension":
            sensor["samples"] = []
    result = microfab.run(request_data, tmp_path / "missing-sensor")
    assert result["parts"][0]["part_conformity"] == "INDETERMINATE"
    assert result["engineering_review"] == "HOLD_INDETERMINATE_EVIDENCE"


def test_stale_quality_holds_order(request_data, tmp_path):
    for sensor in request_data["entries"][0]["cycle"]["sensors"]:
        if sensor["quantity"] == "part_dimension":
            sensor["samples"][-1]["time_s"] = 8
    result = microfab.run(request_data, tmp_path / "stale")
    assert result["parts"][0]["part_conformity"] == "INDETERMINATE"


def test_model_abstention_holds_order(request_data, tmp_path):
    request_data["entries"][0]["cycle"]["model"]["cooling"]["conductivity_w_m_k"]["value"] = 0.01
    request_data["entries"][0]["cycle"]["model"]["cooling"]["conductivity_w_m_k"]["standard_uncertainty"] = 0.0
    request_data["cell"]["model_ref"] = digest(request_data["entries"][0]["cycle"]["model"])
    result = microfab.run(request_data, tmp_path / "abstain")
    assert result["parts"][0]["residual"]["reason"] == "MODEL_ABSTAINED"
    assert result["engineering_review"] == "HOLD_INDETERMINATE_EVIDENCE"


def test_order_shortfall_never_silently_succeeds(request_data, tmp_path):
    request_data["order"]["quantity"] = 2
    result = microfab.run(request_data, tmp_path / "shortfall")
    assert result["workflow_status"] == "PASS"
    assert result["engineering_review"] == "HOLD_INCOMPLETE_ORDER"


def test_nonconforming_residual_is_retained(request_data, tmp_path):
    request_data["entries"][0]["followup"]["value"] = 400
    result = microfab.run(request_data, tmp_path / "nonconforming")
    assert result["parts"][0]["residual"]["status"] == "NONCONFORMING"
    assert result["engineering_review"] == "HOLD_NONCONFORMING"
    assert result["workflow_status"] == "PASS"


def test_partial_failure_preserves_child_evidence(request_data, tmp_path, monkeypatch):
    data = second_part(request_data)
    original = workflow.run
    calls = 0
    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(workflow, "run", fail_second)
    path = tmp_path / "partial"
    with pytest.raises(RuntimeError):
        microfab.run(data, path)
    assert (path / "part-0000" / "workspace.json").is_file()
    assert (path / "failure.json").is_file()
    assert not (path / "dossier.json").exists()
    with pytest.raises(ValueError, match="inventory"):
        microfab.inspect(path)


def test_create_only_does_not_overwrite(retained, request_data):
    path, _ = retained
    before = (path / "dossier.json").read_bytes()
    with pytest.raises(FileExistsError):
        microfab.run(request_data, path)
    assert (path / "dossier.json").read_bytes() == before


@pytest.mark.parametrize("path,value", [
    (("authority", "safety_controller"), True),
    (("authority", "product_release"), "authorized"),
    (("parts", 0, "serial_id"), "forged.serial"),
    (("parts", 0, "residual", "residual_k"), 0),
    (("parts", 0, "proposal_execution"), "AUTHORIZED"),
    (("engineering_review",), "RELEASED"),
])
def test_resealed_forged_dossier_refused(retained, path, value):
    directory, dossier = retained
    at(dossier, path[:-1])[path[-1]] = value
    (directory / "dossier.json").write_text(json.dumps(seal(dossier)))
    with pytest.raises(ValueError, match="Dossier differs"):
        microfab.inspect(directory)


def test_replaced_child_request_refused(retained):
    directory, _ = retained
    path = directory / "part-0000" / "request.json"
    request = json.loads(path.read_text())
    request["identity"]["cycle_id"] = "different.cycle"
    path.write_text(json.dumps(request))
    with pytest.raises(ValueError, match="genealogy"):
        microfab.inspect(directory)


def test_extra_inventory_refused(retained):
    directory, _ = retained
    (directory / "unbound.json").write_text("{}")
    with pytest.raises(ValueError, match="inventory"):
        microfab.inspect(directory)


def test_symlink_request_refused(request_data, tmp_path):
    real = tmp_path / "real.json"
    save_new(real, request_data)
    link = tmp_path / "link.json"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="symlink"):
        microfab.load_request(link)


def test_duplicate_json_keys_refused(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"schema":"one","schema":"two"}')
    with pytest.raises(ValueError):
        microfab.load_request(path)


def test_cli_all_commands_and_no_machine_command(tmp_path):
    from ciw.polymer_cli import main
    example = tmp_path / "example.json"
    directory, replay = tmp_path / "cli-run", tmp_path / "cli-replay"
    assert main(["microfab", "example", "--output", str(example)]) == 0
    assert main(["microfab", "run", str(example), "--output-dir", str(directory)]) == 0
    assert main(["microfab", "inspect", str(directory)]) == 0
    assert main(["microfab", "verify", str(directory), "--output", str(tmp_path / "audit.json")]) == 0
    assert main(["microfab", "replay", str(directory), "--output-dir", str(replay)]) == 0
    assert main(["microfab", "verify", str(directory), "--output", str(directory / "audit.json")]) == 2
    with pytest.raises(SystemExit):
        main(["microfab", "execute-plc"])
