"""Operator-declared refurbishment records; no physical or certification authority.

Commands are the import boundary. ``apply_command`` projects an already verified
command history; callers must not treat an arbitrary supplied snapshot as proof.
Money uses the asset's declared currency and minor-unit exponent, never floats.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import re

from . import hardware_contract as hardware
from .control_contracts import content_ref, detached, keys
from .operations.runner import digest

COMMAND_SCHEMA = "ciw.refurbishment-command.v1"
ASSET_SCHEMA = "ciw.refurbishment-asset.v1"
SOURCE_KINDS = {"operator_import", "external_public", "synthetic"}
MAX_MONEY = 10**15
AUTHORITY = {
    "scope": "operator_declared_asset_and_work_order_records",
    "operator_identity": "not_authenticated",
    "physical_testing": "not_performed_by_registry",
    "certification": "not_granted",
    "device_execution": "not_performed",
    "sanitization": "not_established",
}
PAYLOAD_FIELDS = {
    "intake": {"intake_id", "category", "location", "condition", "configuration", "source_kind",
               "currency", "minor_unit_exponent", "acquisition_cost_minor", "notes"},
    "relocate": {"location", "notes"},
    "open_order": {"work_order_id", "summary"},
    "diagnose": {"condition", "findings", "evidence_ids"},
    "record_repair": {"repair_id", "description", "parts", "labor_cost_minor", "other_cost_minor", "evidence_ids"},
    "replace_configuration": {"configuration", "reason"},
    "record_evidence": {"evidence_id", "kind", "configuration_ref", "source_kind", "content_ref", "summary", "record"},
    "reassess": {"assessment_id", "request", "evidence_ids"},
    "review": {"review_id", "assessment_id", "decision", "notes"},
    "release": {"release_id", "review_id", "disposition", "customer_id", "notes"},
    "record_customer": {"customer_id", "name", "contact", "notes"},
    "record_warranty": {"warranty_id", "release_id", "starts_on", "ends_on", "terms", "notes"},
    "record_return": {"return_id", "release_id", "received_on", "reason", "condition", "notes"},
}


def _text(value, maximum=4096, *, empty=False):
    if (type(value) is not str or len(value) > maximum or "\x00" in value
            or not empty and not value.strip()):
        raise ValueError("Require bounded text")
    return value


def identifier(value):
    _text(value, 128)
    if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Require a bounded opaque identifier without control characters")
    return value


def _integer(value, maximum=MAX_MONEY, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Require a bounded integer, not a boolean or floating-point amount")
    return value


def _ids(value, *, nonempty=False):
    if type(value) is not list or len(value) > 128 or nonempty and not value:
        raise ValueError("Require a bounded list of evidence identities")
    for item in value:
        identifier(item)
    if len(set(value)) != len(value):
        raise ValueError("Duplicate evidence identity")


def _date(value):
    _text(value, 10)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError("Require a YYYY-MM-DD date")
    return date.fromisoformat(value)


def _configuration(value, asset_id):
    result = hardware.validate_configuration(value)
    if result["asset_id"] != asset_id:
        raise ValueError("Configuration belongs to a different asset")
    identifier(result["revision"])
    return result


def validate_command(value):
    command = detached(value)
    keys(command, {"schema", "command_id", "asset_id", "expected_revision", "actor", "action", "payload"})
    if command["schema"] != COMMAND_SCHEMA:
        raise ValueError("Unsupported refurbishment command schema")
    for name in ("command_id", "asset_id", "actor"):
        identifier(command[name])
    _integer(command["expected_revision"], 10000)
    action = command["action"]
    if type(action) is not str or action not in PAYLOAD_FIELDS:
        raise ValueError("Unsupported refurbishment action")
    payload = command["payload"]
    keys(payload, PAYLOAD_FIELDS[action])
    for name in ("notes", "contact"):
        if name in payload:
            _text(payload[name], empty=True)
    for name in ("intake_id", "work_order_id", "repair_id", "evidence_id", "assessment_id",
                 "review_id", "release_id", "warranty_id", "return_id"):
        if name in payload:
            identifier(payload[name])
    if "customer_id" in payload and payload["customer_id"] is not None:
        identifier(payload["customer_id"])
    if action == "record_customer" and payload["customer_id"] is None:
        raise ValueError("Customer identity is required")
    for name in ("category", "location", "condition", "summary", "findings", "description", "reason", "name", "terms"):
        if name in payload:
            _text(payload[name])
    if "configuration" in payload:
        _configuration(payload["configuration"], command["asset_id"])
    if "evidence_ids" in payload:
        _ids(payload["evidence_ids"], nonempty=action == "reassess")
    if "source_kind" in payload and (type(payload["source_kind"]) is not str or payload["source_kind"] not in SOURCE_KINDS):
        raise ValueError("Declare operator_import, external_public or synthetic origin")
    if action == "intake":
        if type(payload["currency"]) is not str or re.fullmatch(r"[A-Z]{3}", payload["currency"]) is None:
            raise ValueError("Require a declared three-letter currency")
        _integer(payload["minor_unit_exponent"], 6)
        _integer(payload["acquisition_cost_minor"])
    elif action == "record_repair":
        _integer(payload["labor_cost_minor"])
        _integer(payload["other_cost_minor"])
        if type(payload["parts"]) is not list or len(payload["parts"]) > 128:
            raise ValueError("Require a bounded parts list")
        seen = set()
        for part in payload["parts"]:
            keys(part, {"part_id", "description", "quantity", "unit_cost_minor"})
            identifier(part["part_id"]); _text(part["description"])
            _integer(part["quantity"], 10000, 1); _integer(part["unit_cost_minor"])
            if part["part_id"] in seen:
                raise ValueError("Duplicate part identity within repair")
            seen.add(part["part_id"])
        _repair_cost(payload)
    elif action == "record_evidence":
        if type(payload["kind"]) is not str or payload["kind"] not in {"diagnostic", "sanitization", "provisioning", "document"}:
            raise ValueError("Unsupported data-only evidence kind")
        content_ref(payload["configuration_ref"]); content_ref(payload["content_ref"])
        if type(payload["record"]) is not dict or digest(payload["record"]) != payload["content_ref"]:
            raise ValueError("Evidence content identity differs from retained record")
    elif action == "reassess":
        hardware.validate_request(payload["request"])
    elif action == "review" and (type(payload["decision"]) is not str or payload["decision"] not in {"approve", "reject"}):
        raise ValueError("Require an explicit approve or reject disposition")
    elif action == "release" and (type(payload["disposition"]) is not str or payload["disposition"] not in {"operator_release", "demo_release"}):
        raise ValueError("Require explicit operator_release or demo_release disposition")
    elif action == "record_warranty":
        if _date(payload["ends_on"]) < _date(payload["starts_on"]):
            raise ValueError("Warranty end precedes start")
    elif action == "record_return":
        _date(payload["received_on"])
    return command


def _repair_cost(payload):
    total = payload["labor_cost_minor"] + payload["other_cost_minor"]
    total += sum(p["quantity"] * p["unit_cost_minor"] for p in payload["parts"])
    return _integer(total)


def _entry(command, payload=None):
    return {"command_id": command["command_id"], "revision": command["expected_revision"] + 1,
            "actor": command["actor"], **deepcopy(command["payload"] if payload is None else payload)}


def _order(state):
    order_id = state["active_work_order_id"]
    if order_id is None:
        raise ValueError("An active work order is required")
    return state["work_orders"][order_id]


def _invalidate(state, stage, *, material=False):
    order = _order(state)
    order["generation"] += 1
    if material:
        order["evidence_epoch"] += 1
    order["current_assessment_id"] = None
    order["current_review_id"] = None
    order["status"] = stage
    state["stage"] = stage
    state["current_release_id"] = None


def _evidence(state, evidence_ids, *, current=True):
    order = _order(state)
    records = []
    for evidence_id in evidence_ids:
        if evidence_id not in order["evidence_ids"]:
            raise ValueError("Evidence is not attached to the active work order")
        value = state["evidence"][evidence_id]
        if current and (value["configuration_ref"] != state["configuration_ref"] or value["evidence_epoch"] != order["evidence_epoch"]):
            raise ValueError("Evidence belongs to a stale configuration or repair epoch")
        records.append(value)
    return records


def _unique(entries, name, value):
    if any(entry[name] == value for entry in entries):
        raise ValueError("Identity already exists: " + value)


def _find(entries, name, value):
    for entry in entries:
        if entry[name] == value:
            return entry
    raise ValueError("Unknown retained identity: " + value)


def _check_evidence_record(state, payload):
    if payload["configuration_ref"] != state["configuration_ref"]:
        raise ValueError("Evidence belongs to a stale configuration")
    record = payload["record"]
    for name, expected in (("asset_id", state["asset_id"]), ("configuration_ref", state["configuration_ref"])):
        if name in record and record[name] != expected:
            raise ValueError("Retained evidence binding differs: " + name)
    if "source_kind" in record and record["source_kind"] != payload["source_kind"]:
        # The older hardware observation contract distinguishes imported from
        # synthetic data; the registry additionally retains public-data origin.
        public_import = (payload["source_kind"] == "external_public" and record["source_kind"] == "operator_import"
                         and record.get("schema") == hardware.OBSERVATIONS_SCHEMA)
        if not public_import:
            raise ValueError("Evidence origin cannot be relabeled")
    if record.get("schema") == "ciw.hardware-bench-evidence.v1":
        from .hardware_bench import validate_evidence
        validate_evidence(record, state["configuration"])


def _assessment_binding(request, evidence):
    bench = [e["record"] for e in evidence if e["record"].get("schema") == "ciw.hardware-bench-evidence.v1"]
    if bench:
        from .hardware_bench import validate_assessment_binding
        validate_assessment_binding(request, bench)
    elif not any(e["record"].get("schema") == hardware.OBSERVATIONS_SCHEMA
                 and digest(e["record"]) == digest(request["observations"]) for e in evidence):
        raise ValueError("Assessment requires its exact retained observations or bound bench evidence")


def apply_command(previous, value):
    """Pure projection of a validated command onto verified prior history."""
    command = validate_command(value); p = command["payload"]; action = command["action"]
    if previous is None:
        if action != "intake" or command["expected_revision"] != 0:
            raise ValueError("First command must be intake at revision zero")
        configuration = _configuration(p["configuration"], command["asset_id"])
        ref = hardware.configuration_ref(configuration)
        return {"schema": ASSET_SCHEMA, "asset_id": command["asset_id"], "revision": 1,
                **{k:deepcopy(p[k]) for k in ("intake_id", "category", "location", "condition", "source_kind",
                    "currency", "minor_unit_exponent", "acquisition_cost_minor")},
                "configuration": configuration, "configuration_ref": ref,
                "configuration_history": [_entry(command, {"configuration": configuration, "configuration_ref": ref,
                                                           "reason": p["notes"]})],
                "stage": "intake", "active_work_order_id": None, "work_orders": {}, "evidence": {},
                "customer_history": [], "warranties": [], "returns": [], "releases": [],
                "current_release_id": None, "total_repair_cost_minor": 0,
                "total_cost_minor": p["acquisition_cost_minor"], "authority": dict(AUTHORITY)}
    if previous["asset_id"] != command["asset_id"] or previous["revision"] != command["expected_revision"]:
        raise ValueError("Asset identity or expected revision differs")
    if action == "intake":
        raise ValueError("Asset already exists")
    state = deepcopy(previous)
    if action == "relocate":
        state["location"] = p["location"]
    elif action == "open_order":
        if state["active_work_order_id"] is not None or p["work_order_id"] in state["work_orders"]:
            raise ValueError("A work order is already active or this identity was used")
        state["active_work_order_id"] = p["work_order_id"]
        state["current_release_id"] = None
        state["stage"] = "diagnosis"
        state["work_orders"][p["work_order_id"]] = {
            "work_order_id":p["work_order_id"], "summary":p["summary"], "status":"diagnosis", "generation":0, "evidence_epoch":0,
            "diagnoses":[], "repairs":[], "evidence_ids":[], "assessments":[], "reviews":[],
            "current_assessment_id":None, "current_review_id":None, "total_cost_minor":0}
    elif action == "diagnose":
        order = _order(state); _evidence(state, p["evidence_ids"])
        order["diagnoses"].append(_entry(command)); state["condition"] = p["condition"]
        _invalidate(state, "repair", material=True)
    elif action == "record_repair":
        order = _order(state)
        if not order["diagnoses"]:
            raise ValueError("Record a diagnosis before repair")
        _unique(order["repairs"], "repair_id", p["repair_id"]); _evidence(state, p["evidence_ids"])
        cost = _repair_cost(p)
        order["total_cost_minor"] = _integer(order["total_cost_minor"] + cost)
        state["total_repair_cost_minor"] = _integer(state["total_repair_cost_minor"] + cost)
        state["total_cost_minor"] = _integer(state["acquisition_cost_minor"] + state["total_repair_cost_minor"])
        order["repairs"].append(_entry(command, {**p, "total_cost_minor":cost}))
        _invalidate(state, "repair", material=True)
    elif action == "replace_configuration":
        if state["stage"] != "intake":
            _order(state)
        configuration = _configuration(p["configuration"], state["asset_id"])
        if any(x["configuration"]["revision"] == configuration["revision"] for x in state["configuration_history"]):
            raise ValueError("Configuration revision identity cannot be reused")
        ref = hardware.configuration_ref(configuration)
        state["configuration"] = configuration; state["configuration_ref"] = ref
        state["configuration_history"].append(_entry(command, {"configuration":configuration,
                                                              "configuration_ref":ref, "reason":p["reason"]}))
        if state["active_work_order_id"] is not None:
            _invalidate(state, "repair" if _order(state)["diagnoses"] else "diagnosis", material=True)
    elif action == "record_evidence":
        order = _order(state)
        if p["evidence_id"] in state["evidence"]:
            raise ValueError("Evidence identity already exists")
        _check_evidence_record(state, p)
        if any(state["evidence"][item]["content_ref"] == p["content_ref"]
               and state["evidence"][item]["evidence_epoch"] != order["evidence_epoch"]
               for item in order["evidence_ids"]):
            raise ValueError("Unchanged evidence from before a repair cannot be reattached as fresh evidence")
        state["evidence"][p["evidence_id"]] = _entry(command, {**p, "evidence_epoch":order["evidence_epoch"]})
        order["evidence_ids"].append(p["evidence_id"])
        _invalidate(state, "reassessment" if order["diagnoses"] else "diagnosis")
    elif action == "reassess":
        order = _order(state)
        if not order["diagnoses"]:
            raise ValueError("Record a diagnosis before reassessment")
        _unique(order["assessments"], "assessment_id", p["assessment_id"])
        evidence = _evidence(state, p["evidence_ids"])
        current = {e for e in order["evidence_ids"] if state["evidence"][e]["configuration_ref"] == state["configuration_ref"]
                   and state["evidence"][e]["evidence_epoch"] == order["evidence_epoch"]}
        if set(p["evidence_ids"]) != current:
            raise ValueError("Reassessment must bind all evidence for the current configuration")
        request = hardware.validate_request(p["request"])
        if digest(request["configuration"]) != state["configuration_ref"]:
            raise ValueError("Assessment configuration is stale")
        _assessment_binding(request, evidence)
        assessment = hardware.assess(request)
        record = _entry(command, {**p, "assessment":assessment, "assessment_ref":digest(assessment),
                                  "request_ref":digest(request), "configuration_ref":state["configuration_ref"],
                                  "evidence_refs":[state["evidence"][e]["content_ref"] for e in p["evidence_ids"]],
                                  "generation":order["generation"]})
        order["assessments"].append(record); order["current_assessment_id"] = p["assessment_id"]
        order["current_review_id"] = None; order["status"] = state["stage"] = "review"
    elif action == "review":
        order = _order(state); _unique(order["reviews"], "review_id", p["review_id"])
        if order["current_assessment_id"] != p["assessment_id"]:
            raise ValueError("Review requires the current assessment")
        assessment = _find(order["assessments"], "assessment_id", p["assessment_id"])
        if p["decision"] == "approve" and assessment["assessment"]["status"] != "PASS":
            raise ValueError("Approval requires passing declared evidence")
        order["reviews"].append(_entry(command, {**p, "configuration_ref":state["configuration_ref"],
            "assessment_ref":assessment["assessment_ref"], "request_ref":assessment["request_ref"],
            "evidence_refs":assessment["evidence_refs"], "generation":order["generation"]}))
        order["current_review_id"] = p["review_id"]
        order["status"] = state["stage"] = "ready_for_release" if p["decision"] == "approve" else "repair"
    elif action == "release":
        order = _order(state); _unique(state["releases"], "release_id", p["release_id"])
        if order["current_review_id"] != p["review_id"] or state["stage"] != "ready_for_release":
            raise ValueError("Release requires a current explicit approval")
        review = _find(order["reviews"], "review_id", p["review_id"])
        assessment = _find(order["assessments"], "assessment_id", review["assessment_id"])
        if (review["decision"] != "approve" or review["configuration_ref"] != state["configuration_ref"]
                or review["generation"] != order["generation"] or assessment["assessment"]["status"] != "PASS"):
            raise ValueError("Release review is stale or does not pass")
        origins = {state["source_kind"], assessment["request"]["observations"]["source_kind"]}
        origins.update(state["evidence"][e]["source_kind"] for e in assessment["evidence_ids"])
        if p["disposition"] == "operator_release" and origins != {"operator_import"}:
            raise ValueError("Synthetic or public-data evidence permits demo release only")
        customer = None
        if p["customer_id"] is not None:
            customer = _find(list(reversed(state["customer_history"])), "customer_id", p["customer_id"])
        release = _entry(command, {**p, "work_order_id":order["work_order_id"],
            "configuration_ref":state["configuration_ref"], "assessment_ref":assessment["assessment_ref"],
            "request_ref":assessment["request_ref"], "evidence_refs":assessment["evidence_refs"],
            "source_kinds":sorted(origins), "customer":customer,
            "scope":"synthetic_or_public_demo_only" if p["disposition"] == "demo_release" else "operator_declared_disposition_only",
            "certification_authority":False})
        state["releases"].append(release); state["current_release_id"] = p["release_id"]
        order["status"] = "closed"; state["active_work_order_id"] = None; state["stage"] = "released"
    elif action == "record_customer":
        state["customer_history"].append(_entry(command))
    elif action == "record_warranty":
        _unique(state["warranties"], "warranty_id", p["warranty_id"])
        release = _find(state["releases"], "release_id", p["release_id"])
        state["warranties"].append(_entry(command, {**p, "configuration_ref":release["configuration_ref"],
                                                   "release_scope":release["scope"]}))
    elif action == "record_return":
        _unique(state["returns"], "return_id", p["return_id"])
        if state["current_release_id"] != p["release_id"] or state["active_work_order_id"] is not None:
            raise ValueError("Return must reference the active released disposition")
        release = _find(state["releases"], "release_id", p["release_id"])
        state["returns"].append(_entry(command, {**p, "configuration_ref":release["configuration_ref"]}))
        old_order = state["work_orders"][release["work_order_id"]]
        old_order["current_review_id"] = None; old_order["current_assessment_id"] = None
        old_order["generation"] += 1
        state["current_release_id"] = None; state["condition"] = p["condition"]; state["stage"] = "returned"
    state["revision"] += 1
    return state


def example_commands(asset_id="NS-DEMO-0001", actor="Synthetic fixture"):
    """Synthetic example commands; applying them records no physical approval."""
    identifier(asset_id); identifier(actor)
    example_id = "demo-" + digest(asset_id)[7:39]
    request = hardware.example_request(); request["configuration"]["asset_id"] = asset_id
    request["observations"]["asset_id"] = asset_id
    configuration = deepcopy(request["configuration"])
    updated = deepcopy(configuration); updated["revision"] = "2"
    updated["components"][1]["firmware"] = "synthetic-example-2"
    request["configuration"] = updated
    request["observations"]["configuration_ref"] = hardware.configuration_ref(updated)
    observations = request["observations"]
    entries = [
        ("intake", {"intake_id":example_id+":intake", "category":"computing", "location":"Demo shelf", "condition":"Synthetic intake",
                    "configuration":configuration, "source_kind":"synthetic", "currency":"CAD", "minor_unit_exponent":2,
                    "acquisition_cost_minor":10000, "notes":"Synthetic example; no hardware received"}),
        ("open_order", {"work_order_id":"WO-DEMO-1", "summary":"Synthetic refurbishment"}),
        ("diagnose", {"condition":"Synthetic repair required", "findings":"Simulated firmware revision", "evidence_ids":[]}),
        ("record_repair", {"repair_id":"repair-1", "description":"Simulated service", "parts":[{"part_id":"part-1", "description":"Synthetic part", "quantity":2, "unit_cost_minor":1250}], "labor_cost_minor":3000, "other_cost_minor":0, "evidence_ids":[]}),
        ("replace_configuration", {"configuration":updated, "reason":"Synthetic firmware revision"}),
        ("record_evidence", {"evidence_id":"evidence-1", "kind":"diagnostic", "configuration_ref":hardware.configuration_ref(updated),
                             "source_kind":"synthetic", "content_ref":digest(observations), "summary":"Synthetic observations", "record":observations}),
        ("reassess", {"assessment_id":"assessment-1", "request":request, "evidence_ids":["evidence-1"]}),
        ("review", {"review_id":"review-1", "assessment_id":"assessment-1", "decision":"approve", "notes":"Synthetic review only"}),
        ("record_customer", {"customer_id":"customer-demo", "name":"Synthetic customer", "contact":"", "notes":"No real customer"}),
        ("release", {"release_id":"release-1", "review_id":"review-1", "disposition":"demo_release", "customer_id":"customer-demo", "notes":"Demo only"}),
        ("record_warranty", {"warranty_id":"warranty-1", "release_id":"release-1", "starts_on":"2026-01-01", "ends_on":"2026-04-01", "terms":"Synthetic terms, no actual warranty issued", "notes":""}),
    ]
    return [{"schema":COMMAND_SCHEMA, "command_id":example_id+":"+str(i+1), "asset_id":asset_id,
             "expected_revision":i, "actor":actor, "action":action, "payload":payload}
            for i,(action,payload) in enumerate(entries)]
