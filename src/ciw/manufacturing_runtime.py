"""Offline, bounded discrete-manufacturing replay and part genealogy.

This reducer records declarations. It neither contacts equipment nor makes a
production, assembly, certificate or shipment authorization. Generic part/BOM/
route/order objects are separate from the first polymer configuration profile.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal, localcontext

from .core.identities import new_identity, validate_identity
from .manufacturing_contracts import (bounded, content_identity, count, items, keys,
                                      number, ref, refs, scalar, text)
from .polymer_microfab import AUTHORITY, compile_configuration, validate_configuration

SCHEMA = "ciw.polymer-manufacturing-request.v1"
UNITS = {"m", "m2", "m3", "kg", "1", "Pa", "K", "N", "s"}
CONDITIONS = {"ejection", "conditioned", "assembled"}
MAX_EVENTS = 512
MAX_SERIALS = 512


def _catalog(request: dict) -> tuple[dict, dict, dict]:
    parts, routes, orders, names = {}, {}, {}, set()
    for part in items(request["parts"], minimum=1, maximum=128):
        keys(part, {"part_number", "revision", "requirement_refs", "bom", "inspection_plan"})
        identity = (text(part["part_number"]), text(part["revision"]))
        if identity in names:
            raise ValueError("Duplicate or conflicting part revision")
        names.add(identity)
        refs(part["requirement_refs"], minimum=1)
        seen = set()
        for line in items(part["bom"], maximum=64):
            keys(line, {"part_ref", "quantity"})
            component = ref(line["part_ref"])
            count(line["quantity"], maximum=MAX_SERIALS)
            if component in seen:
                raise ValueError("Duplicate BOM line")
            seen.add(component)
        seen = set()
        for spec in items(part["inspection_plan"], minimum=1, maximum=32):
            keys(spec, {"characteristic", "unit", "lower", "upper", "condition", "specification_ref"})
            characteristic = text(spec["characteristic"])
            if characteristic in seen or spec["unit"] not in UNITS or spec["condition"] not in CONDITIONS:
                raise ValueError("Repeated characteristic or unsupported inspection semantics")
            seen.add(characteristic)
            lo, hi = (number(spec[k], minimum=-1e50) for k in ("lower", "upper"))
            if lo >= hi or spec["unit"] == "K" and lo <= 0:
                raise ValueError("Invalid inspection interval")
            ref(spec["specification_ref"])
        parts[content_identity(part)] = part
    visiting, visited = set(), set()

    def visit(part_ref: str) -> None:
        if part_ref in visiting:
            raise ValueError("Cyclic BOM")
        if part_ref in visited:
            return
        if part_ref not in parts:
            raise ValueError("BOM references an absent part revision")
        visiting.add(part_ref)
        for line in parts[part_ref]["bom"]:
            visit(line["part_ref"])
        visiting.remove(part_ref)
        visited.add(part_ref)

    for part_ref in parts:
        visit(part_ref)
    names = set()
    for route in items(request["routes"], minimum=1, maximum=128):
        keys(route, {"route_id", "revision", "part_ref", "steps"})
        identity = (text(route["route_id"]), text(route["revision"]))
        if identity in names:
            raise ValueError("Duplicate route revision")
        names.add(identity)
        if ref(route["part_ref"]) not in parts:
            raise ValueError("Route references an absent part revision")
        first = "assemble" if parts[route["part_ref"]]["bom"] else "form"
        if route["steps"] != [first, "inspect"]:
            raise ValueError("V1 routes require form/inspect or assemble/inspect, in order")
        routes[content_identity(route)] = route
    for order in items(request["orders"], minimum=1, maximum=128):
        keys(order, {"work_order_id", "part_ref", "route_ref", "quantity", "max_starts", "configuration_ref"})
        oid = text(order["work_order_id"])
        if oid in orders:
            raise ValueError("Duplicate work order")
        part_ref, route_ref = ref(order["part_ref"]), ref(order["route_ref"])
        if part_ref not in parts or route_ref not in routes or routes[route_ref]["part_ref"] != part_ref:
            raise ValueError("Work order part/route revision mismatch")
        target = count(order["quantity"], maximum=MAX_SERIALS)
        count(order["max_starts"], minimum=target, maximum=MAX_SERIALS)
        if parts[part_ref]["bom"]:
            if order["configuration_ref"] is not None:
                raise ValueError("An assembly route does not use the polymer cell configuration")
        elif (ref(order["configuration_ref"]) != content_identity(request["configuration"])
              or part_ref != request["configuration"]["part_ref"]):
            raise ValueError("Forming order is bound to a different configuration or part revision")
        orders[oid] = order
    return parts, routes, orders


def _inspection(part: dict, data: dict) -> dict:
    keys(data, {"serial", "condition", "measurements"})
    text(data["serial"])
    if data["condition"] not in CONDITIONS:
        raise ValueError("Unsupported measurement condition")
    specs = {spec["characteristic"]: spec for spec in part["inspection_plan"]}
    readings = {}
    for row in items(data["measurements"], maximum=32):
        keys(row, {"characteristic", "unit", "value", "expanded_uncertainty", "evidence_ref", "calibration_ref"})
        name = text(row["characteristic"])
        if name not in specs or name in readings or row["unit"] != specs[name]["unit"]:
            raise ValueError("Unexpected, duplicate or unit-mismatched measurement")
        number(row["value"], minimum=1e-12 if row["unit"] == "K" else -1e50)
        if row["expanded_uncertainty"] is not None:
            number(row["expanded_uncertainty"])
        for field in ("evidence_ref", "calibration_ref"):
            if row[field] is not None:
                ref(row[field])
        readings[name] = row
    decisions = []
    for name, spec in specs.items():
        row = readings.get(name)
        if (row is None or row["expanded_uncertainty"] is None or row["evidence_ref"] is None
                or row["calibration_ref"] is None or data["condition"] != spec["condition"]):
            state = "INDETERMINATE"
        else:
            # Decimal arithmetic treats entered values as declared decimal data.
            value, uncertainty = Decimal(str(row["value"])), Decimal(str(row["expanded_uncertainty"]))
            lo, hi = Decimal(str(spec["lower"])), Decimal(str(spec["upper"]))
            state = ("PASS" if lo <= value - uncertainty and value + uncertainty <= hi else
                     "FAIL" if value + uncertainty < lo or value - uncertainty > hi else "INDETERMINATE")
        decisions.append({"characteristic": name, "status": state})
    states = {row["status"] for row in decisions}
    status = "FAIL" if "FAIL" in states else "INDETERMINATE" if "INDETERMINATE" in states else "PASS"
    return {"status": status, "characteristics": decisions, "measurement_ref": content_identity(data)}


def replay(request: dict) -> dict:
    """Pure replay: a refused event cannot partially mutate any external state."""
    bounded(request)
    keys(request, {"schema", "source_kind", "clock_id", "parts", "routes", "orders",
                   "configuration", "preflight", "inventory", "development_runs", "events"})
    if request["schema"] != SCHEMA:
        raise ValueError("Wrong manufacturing request schema")
    text(request["clock_id"])
    config = request["configuration"]
    validate_configuration(config)
    if request["source_kind"] != config["source_kind"]:
        raise ValueError("Mixed request and configuration evidence semantics")
    parts, routes, orders = _catalog(request)
    if config["part_ref"] not in parts or parts[config["part_ref"]]["bom"]:
        raise ValueError("The polymer configuration must bind a catalogued nonassembly part")
    inventory = request["inventory"]
    keys(inventory, {"lot_id", "mass", "evidence_ref"})
    if text(inventory["lot_id"]) != config["material"]["lot_id"]:
        raise ValueError("Inventory belongs to a different material lot")
    scalar(inventory["mass"], "kg")
    ref(inventory["evidence_ref"])
    studies, study_ids = [], set()
    for study in items(request["development_runs"], maximum=64):
        keys(study, {"study_id", "configuration_ref", "hypothesis_ref", "model_ref", "evidence_refs", "result_ref"})
        sid = text(study["study_id"])
        if sid in study_ids or ref(study["configuration_ref"]) != content_identity(config):
            raise ValueError("Duplicate study or stale experimental configuration")
        study_ids.add(sid)
        for field in ("hypothesis_ref", "model_ref", "result_ref"):
            ref(study[field])
        refs(study["evidence_refs"], minimum=1)
        studies.append({"study_ref": content_identity(study), "record": deepcopy(study),
                        "physical_qualification": "not_established"})
    events = items(request["events"], maximum=MAX_EVENTS)
    initial_time = number(events[0]["at_s"]) if events and type(events[0]) is dict and "at_s" in events[0] else 0
    planning = compile_configuration(config, request["preflight"], at_s=initial_time)
    instances, packages, receipts = {}, {}, []
    event_ids, cycle_ids = set(), set()
    starts = Counter()
    last_time, previous = -1.0, None
    with localcontext() as context:
        context.prec = 512
        stock = Decimal(str(inventory["mass"]["value"]))
        initial_stock, allocated_waste = stock, Decimal(0)
        for index, event in enumerate(events):
            keys(event, {"event_id", "at_s", "actor_id", "authorization_ref", "kind", "data"})
            eid, stamp = text(event["event_id"]), number(event["at_s"])
            if eid in event_ids or stamp < last_time:
                raise ValueError("Duplicate event identity or reversed event time")
            event_ids.add(eid)
            last_time = stamp
            text(event["actor_id"])
            ref(event["authorization_ref"])
            kind, data = text(event["kind"]), event["data"]
            event_ref = content_identity(event)
            if kind == "cycle":
                keys(data, {"cycle_id", "work_order_id", "configuration_ref", "material_lot_id",
                            "issued_mass", "waste_mass", "outputs", "cycle_evidence_ref", "model_prediction_ref"})
                cid, oid = text(data["cycle_id"]), text(data["work_order_id"])
                if cid in cycle_ids or oid not in orders:
                    raise ValueError("Duplicate cycle or unknown work order")
                order = orders[oid]
                if routes[order["route_ref"]]["steps"][0] != "form":
                    raise ValueError("A cycle requires a forming route")
                if (ref(data["configuration_ref"]) != order["configuration_ref"]
                        or text(data["material_lot_id"]) != inventory["lot_id"]):
                    raise ValueError("Cycle configuration or material lot substitution")
                gate = compile_configuration(config, request["preflight"], at_s=stamp)
                if gate["status"] != "CANDIDATE":
                    raise ValueError("Cycle recording refused: " + gate["status"])
                for field in ("cycle_evidence_ref", "model_prediction_ref"):
                    ref(data[field])
                scalar(data["issued_mass"], "kg", minimum=1e-12)
                scalar(data["waste_mass"], "kg")
                issued, waste = Decimal(str(data["issued_mass"]["value"])), Decimal(str(data["waste_mass"]["value"]))
                outputs = items(data["outputs"], minimum=1, maximum=len(config["tool"]["cavity_ids"]))
                new_serials, cavities, total = set(), set(), waste
                for output in outputs:
                    keys(output, {"serial", "cavity_id", "mass"})
                    serial, cavity = text(output["serial"]), text(output["cavity_id"])
                    scalar(output["mass"], "kg", minimum=1e-12)
                    if serial in instances or serial in new_serials or cavity in cavities or cavity not in config["tool"]["cavity_ids"]:
                        raise ValueError("Duplicate serial/cavity or undeclared cavity")
                    new_serials.add(serial)
                    cavities.add(cavity)
                    total += Decimal(str(output["mass"]["value"]))
                if total != issued or issued > stock:
                    raise ValueError("Declared mass allocation does not balance or stock would become negative")
                if starts[oid] + len(outputs) > order["max_starts"] or len(instances) + len(outputs) > MAX_SERIALS:
                    raise ValueError("Work-order start allowance or serial budget exceeded")
                stock -= issued
                allocated_waste += waste
                starts[oid] += len(outputs)
                cycle_ids.add(cid)
                for output in outputs:
                    instances[output["serial"]] = {"part_ref": order["part_ref"], "work_order_id": oid,
                        "quality": "UNINSPECTED", "disposition": "WIP", "parents": [], "children": [],
                        "birth_event_ref": event_ref, "inspection": None,
                        "genealogy": {"material_lot_id": inventory["lot_id"], "configuration_ref": order["configuration_ref"],
                            "machine_id": config["machine"]["machine_id"], "screw_id": config["machine"]["screw_id"],
                            "tool_id": config["tool"]["tool_id"], "tool_revision": config["tool"]["revision"],
                            "cavity_id": output["cavity_id"], "cycle_id": cid,
                            "drying_record_ref": config["material"]["drying_record_ref"],
                            "cycle_evidence_ref": data["cycle_evidence_ref"], "model_prediction_ref": data["model_prediction_ref"],
                            "declared_actor_id": event["actor_id"], "declared_authorization_ref": event["authorization_ref"]}}
            elif kind == "inspection":
                if type(data) is not dict or data.get("serial") not in instances:
                    raise ValueError("Inspection requires an existing serial")
                instance = instances[data["serial"]]
                if instance["disposition"] != "WIP" or instance["inspection"] is not None:
                    raise ValueError("Inspection cannot replace an existing disposition; rework is not implemented")
                result = _inspection(parts[instance["part_ref"]], data)
                result["event_ref"] = event_ref
                instance["inspection"] = result
                instance["quality"] = result["status"]
                instance["disposition"] = "AVAILABLE" if result["status"] == "PASS" else "QUARANTINED"
            elif kind == "scrap":
                keys(data, {"serial", "reason", "evidence_ref"})
                serial = text(data["serial"])
                text(data["reason"])
                ref(data["evidence_ref"])
                if serial not in instances or instances[serial]["disposition"] not in {"WIP", "AVAILABLE", "QUARANTINED"}:
                    raise ValueError("Cannot scrap an absent, packed, consumed or already scrapped serial")
                instances[serial]["disposition"] = "SCRAPPED"
            elif kind == "assemble":
                keys(data, {"serial", "work_order_id", "component_serials", "assembly_evidence_ref"})
                serial, oid = text(data["serial"]), text(data["work_order_id"])
                ref(data["assembly_evidence_ref"])
                if serial in instances or oid not in orders or len(instances) >= MAX_SERIALS:
                    raise ValueError("Duplicate assembly serial, unknown order or serial budget exceeded")
                order = orders[oid]
                if routes[order["route_ref"]]["steps"][0] != "assemble" or starts[oid] >= order["max_starts"]:
                    raise ValueError("Assembly route/start allowance mismatch")
                components = items(data["component_serials"], minimum=1, maximum=MAX_SERIALS)
                for component in components:
                    text(component)
                if len(set(components)) != len(components):
                    raise ValueError("A component cannot be consumed twice")
                actual = Counter()
                for component in components:
                    if component not in instances or instances[component]["disposition"] != "AVAILABLE" or instances[component]["quality"] != "PASS":
                        raise ValueError("Assembly requires available components passing every declared characteristic")
                    actual[instances[component]["part_ref"]] += 1
                expected = Counter({line["part_ref"]: line["quantity"] for line in parts[order["part_ref"]]["bom"]})
                if actual != expected:
                    raise ValueError("Assembly does not match its exact revision-bound BOM")
                for component in components:
                    instances[component]["disposition"] = "CONSUMED"
                    instances[component]["children"].append(serial)
                starts[oid] += 1
                instances[serial] = {"part_ref": order["part_ref"], "work_order_id": oid, "quality": "UNINSPECTED",
                    "disposition": "WIP", "parents": list(components), "children": [],
                    "birth_event_ref": event_ref, "inspection": None,
                    "genealogy": {"assembly_evidence_ref": data["assembly_evidence_ref"],
                        "declared_actor_id": event["actor_id"], "declared_authorization_ref": event["authorization_ref"]}}
            elif kind == "pack":
                keys(data, {"package_id", "serials", "packaging_ref"})
                pid = text(data["package_id"])
                ref(data["packaging_ref"])
                serials = items(data["serials"], minimum=1, maximum=MAX_SERIALS)
                for serial in serials:
                    text(serial)
                if pid in packages or len(set(serials)) != len(serials):
                    raise ValueError("Duplicate package or package contents")
                if any(serial not in instances or instances[serial]["disposition"] != "AVAILABLE" or instances[serial]["quality"] != "PASS" for serial in serials):
                    raise ValueError("Packaging requires available conforming records")
                for serial in serials:
                    instances[serial]["disposition"] = "PACKED"
                packages[pid] = {"serials": list(serials), "event_ref": event_ref, "shipment_status": "HELD"}
            else:
                raise ValueError("Unsupported event; hardware execution, rework and shipment release are not implemented")
            receipt = {"sequence": index, "event_id": eid, "event_ref": event_ref, "previous_ref": previous}
            previous = content_identity(receipt)
            receipts.append({**receipt, "record_ref": previous})
        summaries = {}
        for oid, order in orders.items():
            outputs = [value for value in instances.values() if value["work_order_id"] == oid]
            accepted = sum(value["quality"] == "PASS" and value["disposition"] != "SCRAPPED" for value in outputs)
            summaries[oid] = {"requested_good_quantity": order["quantity"], "started_quantity": starts[oid],
                "accepted_quantity": accepted, "scrapped_quantity": sum(value["disposition"] == "SCRAPPED" for value in outputs),
                "target_met_in_records": accepted >= order["quantity"]}
        result = {"schema": "ciw.manufacturing-replay.v1", "source_kind": request["source_kind"],
            "status": "RECORDED_NOT_RELEASED", "planning": planning, "orders": summaries,
            "instances": instances, "packages": packages, "event_receipts": receipts,
            "development_runs": studies,
            "inventory": {"lot_id": inventory["lot_id"], "initial_mass_kg_decimal": str(initial_stock),
                          "remaining_mass_kg_decimal": str(stock), "allocated_waste_kg_decimal": str(allocated_waste)},
            "shipment": {"status": "HELD", "reasons": ["Physical qualification is not established",
                "Operator and release authority are not authenticated", "No certificate or shipment adapter is implemented"]},
            "authority": dict(AUTHORITY)}
    bounded(result)
    return result


def evaluate(request: dict) -> dict:
    result = replay(request)
    report = {"schema": "ciw.polymer-manufacturing-report.v1", "evaluation_id": new_identity("execution"),
              "request": deepcopy(request), "request_ref": content_identity(request),
              "result": result, "result_ref": content_identity(result)}
    bounded(report)
    return report


def verify(report: dict) -> dict:
    """Fresh replay checks retained content and derived states, not authenticity."""
    bounded(report)
    keys(report, {"schema", "evaluation_id", "request", "request_ref", "result", "result_ref"})
    if report["schema"] != "ciw.polymer-manufacturing-report.v1":
        raise ValueError("Wrong retained manufacturing report schema")
    validate_identity(report["evaluation_id"], "execution")
    if report["request_ref"] != content_identity(report["request"]) or report["result_ref"] != content_identity(report["result"]):
        raise ValueError("Retained content identity mismatch")
    fresh = replay(report["request"])
    if content_identity(fresh) != report["result_ref"]:
        raise ValueError("Retained report disagrees with fresh replay")
    return {"schema": "ciw.manufacturing-replay-verification.v1", "status": "PASS",
            "verification_id": new_identity("verification"), "evaluation_id": report["evaluation_id"],
            "request_ref": report["request_ref"], "result_ref": report["result_ref"],
            "scope": "content integrity and same-implementation deterministic replay", "authority": dict(AUTHORITY)}


def trace(report: dict, serial: str) -> dict:
    verify(report)
    text(serial)
    instances = report["result"]["instances"]
    if serial not in instances:
        raise ValueError("Unknown serial")
    ancestors, descendants = set(), set()

    def walk(current: str, direction: str, seen: set) -> None:
        for target in instances[current][direction]:
            if target not in seen:
                seen.add(target)
                walk(target, direction, seen)

    walk(serial, "parents", ancestors)
    walk(serial, "children", descendants)
    relevant = sorted({serial} | ancestors | descendants)
    selected_events = []
    for event in report["request"]["events"]:
        data = event["data"]
        mentioned = set(data.get("component_serials", [])) | set(data.get("serials", []))
        mentioned.update(output["serial"] for output in data.get("outputs", []))
        if "serial" in data:
            mentioned.add(data["serial"])
        if mentioned.intersection(relevant):
            selected_events.append(deepcopy(event))
    result = {"schema": "ciw.manufacturing-genealogy.v1", "serial": serial,
            "ancestors": sorted(ancestors), "descendants": sorted(descendants),
            "records": {key: deepcopy(instances[key]) for key in relevant},
            "events": selected_events, "configuration": deepcopy(report["request"]["configuration"]),
            "authority": dict(AUTHORITY)}
    bounded(result)
    return result
