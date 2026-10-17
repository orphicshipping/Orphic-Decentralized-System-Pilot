"""Focused offline contracts and integration tests; no physical validation."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ciw import manufacturing_contracts as contracts
from ciw.core.identities import content_identity
from ciw.manufacturing_example import example_request
from ciw.manufacturing_runtime import evaluate, replay, trace, verify
from ciw.polymer_microfab import AUTHORITY, CHECKS, compile_configuration
from ciw.polymer_manufacturing_cli import main


@pytest.fixture
def request_data():
    return example_request()


def rebind(value):
    reference = content_identity(value["configuration"])
    value["preflight"]["configuration_ref"] = reference
    for order in value["orders"]:
        if order["configuration_ref"] is not None:
            order["configuration_ref"] = reference
    for event in value["events"]:
        if event["kind"] == "cycle":
            event["data"]["configuration_ref"] = reference
    for study in value["development_runs"]:
        study["configuration_ref"] = reference


def plan(value, at_s=1):
    return compile_configuration(value["configuration"], value["preflight"], at_s=at_s)


def renumber(value):
    for index, event in enumerate(value["events"]):
        event["event_id"] = f"mutation.event.{index}"
        event["at_s"] = index + 1


def set_path(value, path, replacement):
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement


def test_synthetic_end_to_end(request_data):
    before = deepcopy(request_data)
    report = evaluate(request_data)
    result = report["result"]
    assert request_data == before
    assert result["planning"]["status"] == "CANDIDATE"
    assert result["orders"]["WO-COMPONENT"] == {"requested_good_quantity": 3, "started_quantity": 4,
        "accepted_quantity": 3, "scrapped_quantity": 1, "target_met_in_records": True}
    assert result["orders"]["WO-ASSEMBLY"]["accepted_quantity"] == 1
    assert len(result["instances"]) == 5
    assert result["instances"]["p004"]["disposition"] == "SCRAPPED"
    assert result["instances"]["p001"]["children"] == ["a001"]
    assert Decimal(result["inventory"]["remaining_mass_kg_decimal"]) == Decimal("1")
    assert Decimal(result["inventory"]["allocated_waste_kg_decimal"]) == Decimal("0.6")
    assert result["shipment"]["status"] == "HELD"
    assert result["authority"] == AUTHORITY
    assert verify(report)["status"] == "PASS"


def test_known_planning_arithmetic(request_data):
    result = plan(request_data)
    envelope = result["declared_envelope"]
    assert envelope["melt_temperature"] == {"lower": 460, "upper": 540, "unit": "K"}
    assert envelope["mold_temperature"] == {"lower": 300, "upper": 380, "unit": "K"}
    assert envelope["required_clamp_force"]["value"] == 30000
    # Independent arithmetic uses cavity pressure, not the 40 MPa injection pressure.
    assert Decimal(envelope["required_clamp_force"]["decimal_value"]) == Decimal("0.001") * Decimal("20000000") * Decimal("1.5")
    assert all(row["status"] == "PASS" for row in result["diagnostics"])


@pytest.mark.parametrize("owner,field", [
    ("material", "melt_temperature"), ("material", "mold_temperature"),
    ("material", "max_residence_time"), ("material", "moisture"), ("material", "moisture_max"),
    ("material", "drying_record_ref"), ("machine", "melt_temperature"), ("machine", "mold_temperature"),
    ("machine", "shot_volume"), ("machine", "max_injection_pressure"), ("machine", "max_clamp_force"),
    ("machine", "capability_ref"), ("tool", "melt_temperature"), ("tool", "mold_temperature"),
    ("tool", "max_injection_pressure"), ("tool", "projected_area")])
def test_missing_evidence_is_not_approval(request_data, owner, field):
    request_data["configuration"][owner][field] = None
    if owner == "tool":
        request_data["configuration"]["machine"]["compatible_tool_refs"] = [content_identity(request_data["configuration"]["tool"])]
    rebind(request_data)
    assert plan(request_data)["status"] == "INSUFFICIENT_EVIDENCE"
    with pytest.raises(ValueError, match="INSUFFICIENT_EVIDENCE"):
        replay(request_data)


@pytest.mark.parametrize("owner,field", [("material", "characterization_refs"), ("machine", "compatible_tool_refs"),
                                         ("machine", "compatible_grade_ids")])
def test_empty_qualification_sets_are_unknown(request_data, owner, field):
    request_data["configuration"][owner][field] = []
    rebind(request_data)
    assert plan(request_data)["status"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("field,value", [("melt_temperature", 600), ("mold_temperature", 450),
    ("injection_pressure", 90000000), ("shot_volume", 0.002), ("residence_time", 1000),
    ("cavity_pressure_bound", 50000000), ("clamp_factor", 100)])
def test_outside_declared_envelope(request_data, field, value):
    request_data["configuration"]["recipe"][field]["value"] = value
    rebind(request_data)
    assert plan(request_data)["status"] == "INFEASIBLE"
    with pytest.raises(ValueError, match="INFEASIBLE"):
        replay(request_data)


@pytest.mark.parametrize("field,value", [("melt_temperature", 460), ("melt_temperature", 540),
    ("mold_temperature", 300), ("mold_temperature", 380), ("shot_volume", 0.000001),
    ("shot_volume", 0.001), ("residence_time", 600), ("clamp_factor", 50)])
def test_hard_declared_boundaries_inclusive(request_data, field, value):
    request_data["configuration"]["recipe"][field]["value"] = value
    rebind(request_data)
    assert plan(request_data)["status"] == "CANDIDATE"


@pytest.mark.parametrize("value,uncertainty,expected", [(0.0001, 0.00001, "CANDIDATE"),
    (0.00021, 0.00002, "INSUFFICIENT_EVIDENCE"), (0.0003, 0.00001, "INFEASIBLE"),
    (0.0002, 0, "CANDIDATE"), (0.0002, 5e-324, "INSUFFICIENT_EVIDENCE"),
    (0.0001, None, "INSUFFICIENT_EVIDENCE")])
def test_moisture_uncertainty_is_not_erased(request_data, value, uncertainty, expected):
    moisture = request_data["configuration"]["material"]["moisture"]
    moisture.update(value=value, expanded_uncertainty=uncertainty)
    rebind(request_data)
    assert plan(request_data)["status"] == expected


@pytest.mark.parametrize("check", sorted(CHECKS))
def test_each_preflight_check_required(request_data, check):
    request_data["preflight"]["checks"] = [row for row in request_data["preflight"]["checks"] if row["check"] != check]
    assert plan(request_data)["status"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("field,value,expected", [("status", "FAIL", "INFEASIBLE"),
    ("status", "UNKNOWN", "INSUFFICIENT_EVIDENCE"), ("evidence_ref", None, "INSUFFICIENT_EVIDENCE"),
    ("valid_until_s", 0, "INSUFFICIENT_EVIDENCE"), ("observed_at_s", 10, "INSUFFICIENT_EVIDENCE")])
def test_preflight_evidence_validity(request_data, field, value, expected):
    request_data["preflight"]["checks"][0][field] = value
    assert plan(request_data)["status"] == expected


def test_each_cycle_rechecks_freshness_and_refusal_has_no_mutation(request_data):
    request_data["preflight"]["checks"][0]["valid_until_s"] = 1.5
    original = deepcopy(request_data)
    assert plan(request_data)["status"] == "CANDIDATE"
    with pytest.raises(ValueError, match="INSUFFICIENT_EVIDENCE"):
        replay(request_data)
    assert request_data == original


@pytest.mark.parametrize("path,value", [
    (("recipe", "melt_temperature", "value"), 501), (("material", "lot_id"), "different.lot"),
    (("tool", "revision"), "B"), (("machine", "screw_id"), "different.screw"),
    (("configuration_id",), "different.configuration")])
def test_changes_invalidate_preflight(request_data, path, value):
    set_path(request_data["configuration"], path, value)
    with pytest.raises(ValueError, match="Preflight"):
        plan(request_data)


@pytest.mark.parametrize("invalid", [True, False, None, "500", -1, 0, float("nan"), float("inf"), float("-inf"), 10**400])
def test_invalid_numeric_inputs_refused(request_data, invalid):
    request_data["configuration"]["recipe"]["melt_temperature"]["value"] = invalid
    with pytest.raises(ValueError):
        plan(request_data)


@pytest.mark.parametrize("unit", ["C", "degC", "kelvin", "k", "MPa", None, ""])
def test_implicit_units_refused(request_data, unit):
    request_data["configuration"]["recipe"]["melt_temperature"]["unit"] = unit
    with pytest.raises(ValueError):
        plan(request_data)


@pytest.mark.parametrize("key", ["configuration", "material", "machine", "tool", "recipe"])
def test_unknown_configuration_fields_refused(request_data, key):
    target = request_data["configuration"] if key == "configuration" else request_data["configuration"][key]
    target["grant_execute"] = True
    with pytest.raises(ValueError):
        plan(request_data)


def test_invalid_window_refused(request_data):
    request_data["configuration"]["tool"]["melt_temperature"]["lower"] = 550
    with pytest.raises(ValueError):
        plan(request_data)


def test_disjoint_windows_are_infeasible(request_data):
    request_data["configuration"]["material"]["melt_temperature"].update(lower=600, upper=650)
    rebind(request_data)
    assert plan(request_data)["status"] == "INFEASIBLE"


@pytest.mark.parametrize("mutation", ["part_duplicate", "part_conflict", "bom_missing", "bom_duplicate", "route_duplicate",
    "route_wrong_revision", "route_reordered", "route_without_inspection", "order_duplicate", "order_wrong_revision",
    "order_bool_count", "order_negative", "order_too_many", "order_start_limit", "assembly_config", "inventory_lot",
    "study_duplicate", "study_stale", "part_unknown_unit", "part_no_requirements"])
def test_catalog_and_revision_contracts(request_data, mutation):
    q = request_data
    if mutation == "part_duplicate": q["parts"].append(deepcopy(q["parts"][0]))
    elif mutation == "part_conflict":
        other = deepcopy(q["parts"][0]); other["requirement_refs"] = [content_identity("other")]; q["parts"].append(other)
    elif mutation == "bom_missing": q["parts"][1]["bom"][0]["part_ref"] = content_identity("absent")
    elif mutation == "bom_duplicate": q["parts"][1]["bom"].append(deepcopy(q["parts"][1]["bom"][0]))
    elif mutation == "route_duplicate": q["routes"].append(deepcopy(q["routes"][0]))
    elif mutation == "route_wrong_revision": q["routes"][0]["part_ref"] = content_identity("absent")
    elif mutation == "route_reordered": q["routes"][0]["steps"].reverse()
    elif mutation == "route_without_inspection": q["routes"][0]["steps"] = ["form"]
    elif mutation == "order_duplicate": q["orders"].append(deepcopy(q["orders"][0]))
    elif mutation == "order_wrong_revision": q["orders"][0]["part_ref"] = q["orders"][1]["part_ref"]
    elif mutation == "order_bool_count": q["orders"][0]["quantity"] = True
    elif mutation == "order_negative": q["orders"][0]["quantity"] = -1
    elif mutation == "order_too_many": q["orders"][0]["max_starts"] = 513
    elif mutation == "order_start_limit": q["orders"][0]["max_starts"] = 2
    elif mutation == "assembly_config": q["orders"][1]["configuration_ref"] = q["orders"][0]["configuration_ref"]
    elif mutation == "inventory_lot": q["inventory"]["lot_id"] = "wrong.lot"
    elif mutation == "study_duplicate": q["development_runs"].append(deepcopy(q["development_runs"][0]))
    elif mutation == "study_stale": q["development_runs"][0]["configuration_ref"] = content_identity("stale")
    elif mutation == "part_unknown_unit": q["parts"][0]["inspection_plan"][0]["unit"] = "mm"
    elif mutation == "part_no_requirements": q["parts"][0]["requirement_refs"] = []
    with pytest.raises(ValueError): replay(q)


@pytest.mark.parametrize("mutation", ["duplicate_event", "reverse_time", "duplicate_cycle", "wrong_order", "assembly_order",
    "wrong_configuration", "wrong_lot", "duplicate_serial", "duplicate_cavity", "unknown_cavity", "unbalanced_mass",
    "negative_stock", "negative_mass", "negative_waste", "too_many_starts", "bad_evidence", "unknown_field", "too_many_outputs"])
def test_cycle_integrity_and_inventory(request_data, mutation):
    q = request_data
    d = q["events"][0]["data"]
    if mutation == "duplicate_event": q["events"][1]["event_id"] = q["events"][0]["event_id"]
    elif mutation == "reverse_time": q["events"][1]["at_s"] = 0
    elif mutation == "duplicate_cycle": q["events"][1]["data"]["cycle_id"] = d["cycle_id"]
    elif mutation == "wrong_order": d["work_order_id"] = "absent"
    elif mutation == "assembly_order": d["work_order_id"] = "WO-ASSEMBLY"
    elif mutation == "wrong_configuration": d["configuration_ref"] = content_identity("wrong")
    elif mutation == "wrong_lot": d["material_lot_id"] = "wrong"
    elif mutation == "duplicate_serial": d["outputs"][1]["serial"] = d["outputs"][0]["serial"]
    elif mutation == "duplicate_cavity": d["outputs"][1]["cavity_id"] = d["outputs"][0]["cavity_id"]
    elif mutation == "unknown_cavity": d["outputs"][0]["cavity_id"] = "absent"
    elif mutation == "unbalanced_mass": d["issued_mass"]["value"] = 0.6
    elif mutation == "negative_stock": q["inventory"]["mass"]["value"] = 0.49
    elif mutation == "negative_mass": d["outputs"][0]["mass"]["value"] = -0.1
    elif mutation == "negative_waste": d["waste_mass"]["value"] = -0.1
    elif mutation == "too_many_starts": q["orders"][0]["max_starts"] = 3
    elif mutation == "bad_evidence": d["cycle_evidence_ref"] = "unbound"
    elif mutation == "unknown_field": d["execute"] = True
    elif mutation == "too_many_outputs": d["outputs"].append(deepcopy(d["outputs"][0]))
    with pytest.raises(ValueError): replay(q)


def test_decimal_stock_does_not_erase_small_consumption(request_data):
    request_data["inventory"]["mass"]["value"] = 1e50
    result = replay(request_data)
    assert result["inventory"]["remaining_mass_kg_decimal"].replace(".0", "") == "9" * 50


@pytest.mark.parametrize("value,uncertainty,expected", [(0.0200, 0.00001, "PASS"),
    (0.02009, 0.00001, "PASS"), (0.0201, 0.00001, "INDETERMINATE"),
    (0.0202, 0.00001, "FAIL"), (0.0198, 0.00001, "FAIL"), (0.0199, 0, "PASS"),
    (0.0199, 5e-324, "INDETERMINATE"), (0.02, None, "INDETERMINATE")])
def test_guard_banded_inspection(request_data, value, uncertainty, expected):
    request_data["events"] = request_data["events"][:3]
    row = request_data["events"][2]["data"]["measurements"][0]
    row.update(value=value, expanded_uncertainty=uncertainty)
    result = replay(request_data)
    assert result["instances"]["p001"]["quality"] == expected


@pytest.mark.parametrize("mutation", ["missing_measurements", "missing_evidence", "missing_calibration", "wrong_condition"])
def test_missing_inspection_evidence_quarantines(request_data, mutation):
    request_data["events"] = request_data["events"][:3]
    data = request_data["events"][2]["data"]
    if mutation == "missing_measurements": data["measurements"] = []
    elif mutation == "missing_evidence": data["measurements"][0]["evidence_ref"] = None
    elif mutation == "missing_calibration": data["measurements"][0]["calibration_ref"] = None
    elif mutation == "wrong_condition": data["condition"] = "ejection"
    assert replay(request_data)["instances"]["p001"]["disposition"] == "QUARANTINED"


@pytest.mark.parametrize("mutation", ["before_cycle", "duplicate_inspection", "unknown_characteristic", "wrong_unit", "duplicate_measurement",
    "negative_uncertainty", "unknown_serial", "changed_bom", "duplicate_component", "scrapped_component", "missing_component",
    "already_consumed", "pack_scrap", "pack_consumed", "pack_duplicate", "pack_uninspected", "ship", "rework"])
def test_discrete_route_quality_and_consumption_gates(request_data, mutation):
    q = request_data
    data = q["events"][2]["data"]
    if mutation == "before_cycle": q["events"].insert(0, deepcopy(q["events"][2])); renumber(q)
    elif mutation == "duplicate_inspection": q["events"].insert(3, deepcopy(q["events"][2])); renumber(q)
    elif mutation == "unknown_characteristic": data["measurements"][0]["characteristic"] = "absent"
    elif mutation == "wrong_unit": data["measurements"][0]["unit"] = "mm"
    elif mutation == "duplicate_measurement": data["measurements"].append(deepcopy(data["measurements"][0]))
    elif mutation == "negative_uncertainty": data["measurements"][0]["expanded_uncertainty"] = -0.1
    elif mutation == "unknown_serial": data["serial"] = "absent"
    elif mutation == "changed_bom": q["events"][7]["data"]["component_serials"] = ["p001", "p002", "p003"]
    elif mutation == "duplicate_component": q["events"][7]["data"]["component_serials"] = ["p001", "p001"]
    elif mutation == "scrapped_component": q["events"][7]["data"]["component_serials"] = ["p001", "p004"]
    elif mutation == "missing_component": q["events"][7]["data"]["component_serials"] = ["p001", "absent"]
    elif mutation == "already_consumed":
        q["orders"][1]["max_starts"] = 2
        other = deepcopy(q["events"][7]); other["data"]["serial"] = "a002"; q["events"].insert(8, other); renumber(q)
    elif mutation == "pack_scrap": q["events"][9]["data"]["serials"] = ["p004"]
    elif mutation == "pack_consumed": q["events"][9]["data"]["serials"] = ["p001"]
    elif mutation == "pack_duplicate": q["events"][9]["data"]["serials"] = ["a001", "a001"]
    elif mutation == "pack_uninspected": del q["events"][8]
    elif mutation in {"ship", "rework"}: q["events"][-1]["kind"] = mutation
    with pytest.raises(ValueError): replay(q)


def test_genealogy_both_directions_retains_process_and_actor(request_data):
    report = evaluate(request_data)
    assembly = trace(report, "a001")
    component = trace(report, "p001")
    assert assembly["ancestors"] == ["p001", "p002"]
    assert component["descendants"] == ["a001"]
    assert assembly["records"]["p001"]["genealogy"]["material_lot_id"] == "synthetic.lot.1"
    assert assembly["records"]["p001"]["genealogy"]["cavity_id"] == "cavity.1"
    assert {event["kind"] for event in assembly["events"]} == {"cycle", "inspection", "assemble", "pack"}
    assembly["records"]["p001"]["quality"] = "forged"
    assert report["result"]["instances"]["p001"]["quality"] == "PASS"


@pytest.mark.parametrize("mutation", ["result", "resealed_result", "request", "receipt_chain", "authority", "evaluation_id", "extra_field"])
def test_retained_tampering_and_coherent_resealing(request_data, mutation):
    report = evaluate(request_data)
    if mutation in {"result", "resealed_result"}: report["result"]["orders"]["WO-COMPONENT"]["accepted_quantity"] = 4
    elif mutation == "request": report["request"]["configuration"]["material"]["lot_id"] = "other"
    elif mutation == "receipt_chain": report["result"]["event_receipts"][1]["previous_ref"] = None
    elif mutation == "authority": report["result"]["authority"]["hardware_actuation"] = "authorized"
    elif mutation == "evaluation_id": report["evaluation_id"] = report["request_ref"]
    elif mutation == "extra_field": report["grant"] = "execute"
    if mutation in {"resealed_result", "receipt_chain", "authority"}:
        report["result_ref"] = content_identity(report["result"])
    with pytest.raises(ValueError): verify(report)


def test_replay_and_verification_occurrences_stay_distinct(request_data):
    a, b = evaluate(request_data), evaluate(request_data)
    assert a["evaluation_id"] != b["evaluation_id"]
    assert a["request_ref"] == b["request_ref"] and a["result_ref"] == b["result_ref"]
    first, second = verify(a), verify(a)
    assert first["verification_id"] != second["verification_id"]
    assert first["evaluation_id"] == a["evaluation_id"]


def test_declaring_observations_does_not_grant_authenticity(request_data):
    request_data["source_kind"] = request_data["configuration"]["source_kind"] = "retained_observation"
    for row in request_data["preflight"]["checks"]: row["source_kind"] = "retained_observation"
    rebind(request_data)
    report = evaluate(request_data)
    assert verify(report)["authority"] == AUTHORITY
    assert report["result"]["shipment"]["status"] == "HELD"


def test_dictionary_order_does_not_change_result(request_data):
    rearranged = json.loads(json.dumps(request_data, sort_keys=True))
    assert evaluate(rearranged)["result_ref"] == evaluate(request_data)["result_ref"]


@pytest.mark.parametrize("bad", ['{"a":1,"a":2}', '{"value":NaN}', '{"value":Infinity}',
                                '{"value":1e999}', '{"value":1e-999}', '{"value":-1e-999}'])
def test_strict_json_ingress(tmp_path, bad):
    path = tmp_path / "bad.json"; path.write_text(bad)
    with pytest.raises(ValueError): contracts.load(path)


def test_zero_exponents_valid(tmp_path):
    path = tmp_path / "zero.json"; path.write_text('{"value":0e-999}')
    assert contracts.load(path) == {"value": 0.0}


def test_atomic_create_only_and_injected_failure(tmp_path, monkeypatch):
    destination = tmp_path / "report.json"
    contracts.save_new(destination, {"record": 1})
    before = destination.read_bytes()
    with pytest.raises(FileExistsError): contracts.save_new(destination, {"record": 2})
    assert destination.read_bytes() == before
    def fail(*args): raise OSError("injected publication failure")
    monkeypatch.setattr(contracts.os, "link", fail)
    with pytest.raises(OSError): contracts.save_new(tmp_path / "failed.json", {"record": 3})
    assert list(tmp_path.iterdir()) == [destination]


def test_symlink_ingress_and_publication_refused(tmp_path):
    original = tmp_path / "source.json"; original.write_text('{}')
    link = tmp_path / "link.json"
    try: link.symlink_to(original)
    except OSError: pytest.skip("Symlink creation unavailable on this host")
    with pytest.raises(ValueError): contracts.load(link)
    with pytest.raises(FileExistsError): contracts.save_new(link, {"changed": True})
    assert original.read_text() == '{}'


def test_input_output_and_structure_budgets(tmp_path):
    giant = tmp_path / "giant.json"; giant.write_bytes(b' ' * (contracts.MAX_BYTES + 1))
    with pytest.raises(ValueError): contracts.load(giant)
    with pytest.raises(ValueError): contracts.bounded([0] * (contracts.MAX_ITEMS + 1))
    nested = None
    for _ in range(22): nested = [nested]
    with pytest.raises(ValueError): contracts.bounded(nested)
    with pytest.raises(ValueError): contracts.save_new(tmp_path / "bad.json", {"x": float("nan")})
    assert not (tmp_path / "bad.json").exists()


def test_cli_instantiated_lifecycle(tmp_path, capsys):
    request_path, report_path = tmp_path / "request.json", tmp_path / "report.json"
    receipt_path, trace_path = tmp_path / "receipt.json", tmp_path / "trace.json"
    assert main(["example", "--output", str(request_path)]) == 0
    assert main(["plan", str(request_path)]) == 0
    assert main(["run", str(request_path), "--output", str(report_path)]) == 0
    assert main(["verify", str(report_path), "--output", str(receipt_path)]) == 0
    assert main(["trace", str(report_path), "--serial", "a001", "--output", str(trace_path)]) == 0
    assert contracts.load(receipt_path)["status"] == "PASS"
    assert contracts.load(trace_path)["ancestors"] == ["p001", "p002"]
    before = report_path.read_bytes()
    assert main(["run", str(request_path), "--output", str(report_path)]) == 1
    assert report_path.read_bytes() == before
    assert "REFUSE" in capsys.readouterr().err


def test_cli_refusal_does_not_publish_partial_artifact(tmp_path, request_data):
    request_data["events"][-1]["kind"] = "ship"
    path, output = tmp_path / "bad.json", tmp_path / "out.json"
    contracts.save_new(path, request_data)
    assert main(["run", str(path), "--output", str(output)]) == 1
    assert not output.exists()


def test_real_module_cli_subprocess(tmp_path):
    output = tmp_path / "demo.json"
    first = subprocess.run([sys.executable, "-m", "ciw.polymer_manufacturing_cli", "demo", "--output", str(output)],
                           capture_output=True, text=True, timeout=30)
    assert first.returncode == 0, first.stderr
    second = subprocess.run([sys.executable, "-m", "ciw.polymer_manufacturing_cli", "verify", str(output)],
                            capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["status"] == "PASS"


def test_full_repository_polymer_dispatch(tmp_path):
    # The focused connector snapshot deliberately lacks unrelated NET modules.
    # In a full checkout this must exercise the real, unmocked existing dispatcher.
    import importlib.util
    if importlib.util.find_spec("ciw.control_contracts") is None:
        pytest.skip("Full-repository dispatcher dependency absent from focused snapshot")
    from ciw.polymer_cli import main as polymer_main
    output = tmp_path / "dispatched.json"
    assert polymer_main(["manufacture", "demo", "--output", str(output)]) == 0
    assert verify(contracts.load(output))["status"] == "PASS"


def test_clamp_guard_uses_exact_decimal_product(request_data):
    cfg = request_data["configuration"]
    cfg["tool"]["projected_area"]["value"] = 0.10000000000000002
    cfg["recipe"]["cavity_pressure_bound"]["value"] = 3
    cfg["recipe"]["clamp_factor"]["value"] = 1
    cfg["machine"]["max_clamp_force"]["value"] = 0.30000000000000004
    cfg["machine"]["compatible_tool_refs"] = [content_identity(cfg["tool"])]
    rebind(request_data)
    result = plan(request_data)
    assert result["status"] == "INFEASIBLE"
    assert Decimal(result["declared_envelope"]["required_clamp_force"]["decimal_value"]) == Decimal("0.30000000000000006")


def test_model_and_cycle_evidence_are_references_not_computed_results(request_data):
    result = replay(request_data)
    genealogy = result["instances"]["p001"]["genealogy"]
    assert genealogy["cycle_evidence_ref"] == request_data["events"][0]["data"]["cycle_evidence_ref"]
    assert genealogy["model_prediction_ref"] == request_data["events"][0]["data"]["model_prediction_ref"]
    assert "simulation_residual" not in genealogy
    assert result["development_runs"][0]["physical_qualification"] == "not_established"
