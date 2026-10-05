"""A bounded production binding over existing polymer cycles, not a new state owner."""
from __future__ import annotations

from copy import deepcopy
import json

from .control_contracts import content_ref, detached, keys, number, text
from .core.identities import canonical_json
from .operations.runner import digest
from .polymer_contract import validate_request as validate_cycle

SCHEMA = "ciw.polymer-microfab-request.v1"
MAX_PARTS = 16
MAX_BYTES = 4 * 1024 * 1024
MAX_SAMPLES = 16384
MAX_SIMULATED_CYCLES = 4096
AUTHORITY = {
    "scope": "retained_production_evidence_and_engineering_review_only",
    "physical_controller_authority": "external_machine_controller",
    "safety_controller": False,
    "opc_ua_write": False,
    "hardware_actuation": "not_performed",
    "authorization": "not_granted",
    "state_admission": "not_performed",
    "physical_validation": "not_established",
    "calibration_traceability": "not_established",
    "identity_authentication": "not_established",
    "product_release": "not_authorized",
    "shipment_release": "not_authorized",
}


def _identifier(value):
    text(value)
    if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Require an opaque identifier without surrounding space or control characters")
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Require a bounded integer, not a boolean")
    return value


def _followup(value, cycle):
    if value is None:
        return
    keys(value, {"sensor_id", "quantity", "unit", "frame", "clock_id", "time_s", "value",
                 "standard_uncertainty", "source_ref", "calibration_ref", "clock_ref", "origin"})
    for name in ("sensor_id", "frame", "clock_id"):
        _identifier(value[name])
    for name in ("source_ref", "calibration_ref", "clock_ref"):
        content_ref(value[name])
    if value["quantity"] != "part_temperature" or value["unit"] != "K":
        raise ValueError("Cooling residual requires part temperature in exact kelvin")
    if value["frame"] != cycle["frame"] or value["clock_id"] != cycle["clock"]["id"]:
        raise ValueError("Follow-up frame and clock must match explicitly; no implicit mapping")
    if value["origin"] != ("synthetic" if cycle["source_kind"] == "synthetic" else "observed"):
        raise ValueError("Follow-up origin contradicts cycle evidence semantics")
    if number(value["value"]) <= 0 or number(value["standard_uncertainty"]) < 0:
        raise ValueError("Require positive absolute temperature and nonnegative known uncertainty")
    number(value["time_s"])
    cooling = cycle["model"]["cooling"]
    target = cycle["clock"]["end_s"] + cooling["forecast_horizon_s"]
    if cooling["forecast_horizon_s"] <= 0 or value["time_s"] != target:
        raise ValueError("Follow-up must be at the strictly future nominal forecast time; no interpolation")
    sensors = [s for s in cycle["sensors"] if s["sensor_id"] == cooling["temperature_sensor_id"]]
    if len(sensors) != 1:
        raise ValueError("Follow-up requires the explicitly bound model temperature sensor")
    sensor = sensors[0]
    if any(value[k] != sensor[k] for k in ("sensor_id", "quantity", "unit", "calibration_ref", "clock_ref")):
        raise ValueError("Follow-up sensor/calibration/clock binding differs")
    # Distinct evidence is required, but is not a statistical independence claim.
    refs = {s[k] for s in cycle["sensors"] for k in ("source_ref", "calibration_ref", "clock_ref")}
    refs.update(cooling[k]["source_ref"] for k in cooling if type(cooling[k]) is dict)
    if value["source_ref"] in refs:
        raise ValueError("Follow-up cannot reuse a model-input evidence reference")


def validate_request(value: dict) -> dict:
    value = detached(value)
    keys(value, {"schema", "part", "order", "material", "cell", "residual_policy", "entries"})
    if value["schema"] != SCHEMA:
        raise ValueError("Unsupported polymer microfab schema")
    if max(len(canonical_json(value).encode("utf-8")),
           len((json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8"))) > MAX_BYTES:
        raise ValueError("Microfab request exceeds the 4 MiB budget")
    part, order, material, cell = (value[k] for k in ("part", "order", "material", "cell"))
    keys(part, {"part_number", "revision", "drawing_ref", "tolerances"})
    _identifier(part["part_number"])
    _identifier(part["revision"])
    content_ref(part["drawing_ref"])
    keys(order, {"work_order_id", "quantity", "requirement_ref", "bom_ref", "route_ref"})
    _identifier(order["work_order_id"])
    _integer(order["quantity"], 1, MAX_PARTS)
    for name in ("requirement_ref", "bom_ref", "route_ref"):
        content_ref(order[name])
    keys(material, {"material_id", "revision", "material_lot_id", "specification_ref"})
    for name in ("material_id", "revision", "material_lot_id"):
        _identifier(material[name])
    content_ref(material["specification_ref"])
    keys(cell, {"cell_id", "configuration_ref", "facility_id", "machine_id", "tool_id",
                "cavity_ids", "controller_id", "process", "frame", "clock_id", "model_ref"})
    for name in ("cell_id", "facility_id", "machine_id", "tool_id", "controller_id", "frame", "clock_id"):
        _identifier(cell[name])
    for name in ("configuration_ref", "model_ref"):
        content_ref(cell[name])
    if cell["process"] != "injection_molding":
        raise ValueError("Microfab v1 is a single-material injection-molding evidence workload")
    cavities = cell["cavity_ids"]
    if type(cavities) is not list or not 1 <= len(cavities) <= MAX_PARTS:
        raise ValueError("Require 1..16 declared mold cavities")
    for cavity in cavities:
        _identifier(cavity)
    if len(set(cavities)) != len(cavities):
        raise ValueError("Duplicate cell cavity identity")
    policy = value["residual_policy"]
    keys(policy, {"coverage_factor", "absolute_tolerance_k", "policy_ref"})
    content_ref(policy["policy_ref"])
    if not 1 <= number(policy["coverage_factor"]) <= 6:
        raise ValueError("Residual multiplier must be in [1,6]")
    if not 0 < number(policy["absolute_tolerance_k"]) <= 2000:
        raise ValueError("Residual tolerance must be in (0,2000] kelvin")
    entries = value["entries"]
    if type(entries) is not list or not 1 <= len(entries) <= order["quantity"]:
        raise ValueError("Require 1..ordered-quantity retained parts; overproduction is not silently admitted")
    serials, slots, shots, observations = set(), set(), {}, set()
    source_kinds, sample_count, simulation_count = set(), 0, 0
    for entry in entries:
        keys(entry, {"serial_id", "cavity_id", "cycle", "ingress", "followup"})
        serial, cavity = _identifier(entry["serial_id"]), _identifier(entry["cavity_id"])
        cycle = validate_cycle(entry["cycle"])
        identity = cycle["identity"]
        shot = identity["cycle_id"]
        if serial in serials or (shot, cavity) in slots:
            raise ValueError("Duplicate serial or cycle/cavity production slot")
        if cavity not in cavities or serial != identity["part_id"]:
            raise ValueError("Serial or cavity differs from its declared production binding")
        if any(identity[k] != cell[k] for k in ("facility_id", "machine_id", "tool_id")):
            raise ValueError("Cycle physical identity differs from the configured cell")
        if identity["material_lot_id"] != material["material_lot_id"]:
            raise ValueError("Cycle resin lot differs from the work-order material")
        if cycle["tolerances"] != part["tolerances"]:
            raise ValueError("Cycle tolerances differ from the exact part-revision specification")
        if (cycle["process"] != cell["process"] or cycle["frame"] != cell["frame"]
                or cycle["clock"]["id"] != cell["clock_id"] or digest(cycle["model"]) != cell["model_ref"]):
            raise ValueError("Cycle process/frame/clock/model differs from the configured cell")
        shared = digest({k: cycle[k] for k in ("process", "source_kind", "frame", "clock", "model", "control")})
        if shot in shots and shots[shot] != shared:
            raise ValueError("Parts attributed to one shot disagree on clock, model or control configuration")
        if entry["ingress"] is not None:
            from . import polymer_ingress
            envelope = polymer_ingress.validate(entry["ingress"])
            if digest(polymer_ingress.derive(envelope)) != digest(cycle):
                raise ValueError("Native ingress differs from the exact retained cycle")
        _followup(entry["followup"], cycle)
        # A quantitative observation channel cannot be silently reused as a new part's evidence.
        for sensor in cycle["sensors"]:
            if sensor["quantity"] in {"part_temperature", "part_dimension", "wall_thickness", "surface_defect_score"}:
                key = (sensor["sensor_id"], sensor["source_ref"])
                if key in observations:
                    raise ValueError("Part-specific observation evidence reused across serialized parts")
                observations.add(key)
        if entry["followup"] is not None:
            key = (entry["followup"]["sensor_id"], entry["followup"]["source_ref"])
            if key in observations:
                raise ValueError("Follow-up evidence reused across serialized parts")
            observations.add(key)
        serials.add(serial)
        slots.add((shot, cavity))
        shots[shot] = shared
        source_kinds.add(cycle["source_kind"])
        sample_count += sum(len(s["samples"]) for s in cycle["sensors"])
        simulation_count += cycle["control"]["toy_plant"]["cycles"]
    if len(source_kinds) != 1:
        raise ValueError("Synthetic and observed parts must not share one production dossier")
    if sample_count > MAX_SAMPLES or simulation_count > MAX_SIMULATED_CYCLES:
        raise ValueError("Microfab aggregate sample/simulation work budget exceeded")
    return value


def example_request() -> dict:
    from .polymer_contract import example_request as example_cycle
    from .polymer_copilot import example_knowledge
    cycle = example_cycle()
    serial = "synthetic.serial.001"
    cycle["identity"]["part_id"] = serial
    cycle["knowledge"] = example_knowledge(cycle["identity"])
    for sensor in cycle["sensors"]:
        if sensor["quantity"] == "part_dimension":
            for sample in sensor["samples"]:
                sample["value"] = 0.0200
        sensor["source_ref"] = digest({"serial": serial, "sensor": sensor["sensor_id"], "samples": sensor["samples"]})
    temperature = next(s for s in cycle["sensors"] if s["sensor_id"] == cycle["model"]["cooling"]["temperature_sensor_id"])
    followup = {k: deepcopy(temperature[k]) for k in ("sensor_id", "quantity", "unit", "frame", "clock_id",
                                                      "calibration_ref", "clock_ref", "origin")}
    followup.update(time_s=cycle["clock"]["end_s"] + cycle["model"]["cooling"]["forecast_horizon_s"],
                    value=323.0, standard_uncertainty=0.5,
                    source_ref=digest({"synthetic_followup": serial, "value_k": 323.0}))
    identity = cycle["identity"]
    value = {"schema": SCHEMA,
             "part": {"part_number": "synthetic.clip", "revision": "A", "drawing_ref": digest({"drawing": "demo"}),
                      "tolerances": deepcopy(cycle["tolerances"])},
             "order": {"work_order_id": "synthetic.WO.001", "quantity": 1,
                       **{k: digest({k: "synthetic"}) for k in ("requirement_ref", "bom_ref", "route_ref")}},
             "material": {"material_id": "synthetic.polymer", "revision": "A",
                          "material_lot_id": identity["material_lot_id"], "specification_ref": digest({"material": "synthetic"})},
             "cell": {"cell_id": "synthetic.cell.001", "configuration_ref": digest({"configuration": "demo"}),
                      **{k: identity[k] for k in ("facility_id", "machine_id", "tool_id")},
                      "cavity_ids": ["cavity.1"], "controller_id": "external.PLC.demo", "process": cycle["process"],
                      "frame": cycle["frame"], "clock_id": cycle["clock"]["id"], "model_ref": digest(cycle["model"])},
             "residual_policy": {"coverage_factor": 2.0, "absolute_tolerance_k": 8.0,
                                 "policy_ref": digest({"residual_policy": "synthetic_demo_not_part_release"})},
             "entries": [{"serial_id": serial, "cavity_id": "cavity.1", "cycle": cycle,
                          "ingress": None, "followup": followup}]}
    return validate_request(value)
