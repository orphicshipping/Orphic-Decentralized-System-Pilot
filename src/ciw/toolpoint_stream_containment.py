"""Read-only stream-hold projection over verified manufacturing genealogy.

Does not replace a manufacturing reducer or execute a reject/hold/ship operation.
Every recorded forming cycle requires stream evidence; missing evidence is held.
"""
from copy import deepcopy
from decimal import Decimal, localcontext

from .core.identities import new_identity, validate_identity
from .manufacturing_contracts import bounded, content_identity, items, keys, number, text
from .manufacturing_runtime import verify as verify_manufacturing
from .toolpoint_stream import AUTHORITY, LEGACY, verify as verify_stream

SCHEMA = "ciw.toolpoint-stream-containment-request.v1"
OPERATION = "polymer.toolpoint-stream.containment.v1"


def project(request):
    bounded(request)
    keys(request, {"schema", "manufacturing_report", "streams", "assessed_at_s"})
    if request["schema"] != SCHEMA:
        raise ValueError("Wrong stream containment schema")
    number(request["assessed_at_s"])
    manufacturing = request["manufacturing_report"]
    verify_manufacturing(manufacturing)
    source, baseline = manufacturing["request"], manufacturing["result"]
    config = source["configuration"]
    if any(e["at_s"] > request["assessed_at_s"] for e in source["events"]):
        raise ValueError("Manufacturing events occur after the assessment")
    cycles = {e["data"]["cycle_id"]: e for e in source["events"] if e["kind"] == "cycle"}
    streams = {}
    for stream in items(request["streams"], maximum=64):
        verify_stream(stream)
        spec = stream["spec"]
        plan, binding = spec["plan"], spec["binding"]
        cid = binding["cycle_id"]
        if cid not in cycles or cid in streams:
            raise ValueError("Unknown or duplicate cycle stream")
        event = cycles[cid]
        legacy = plan["schema"] == LEGACY
        expected_ref = content_identity(event) if legacy else event["data"]["cycle_evidence_ref"]
        if (binding["cycle_ref"] != expected_ref or binding["source_kind"] != source["source_kind"]
                or plan["configuration_ref"] != content_identity(config) or plan["part_ref"] != config["part_ref"]
                or plan["clock_id"] != source["clock_id"]):
            raise ValueError("Stream does not bind the manufacturing cycle/configuration/part/clock")
        actual_cavities = set(stream["result"]["cavities"])
        if actual_cavities != set(config["tool"]["cavity_ids"]):
            raise ValueError("Stream must cover exactly the configured cavities")
        if legacy and plan["tool_ref"] != content_identity(config["tool"]):
            raise ValueError("Stream tool binding differs from the physical configuration")
        with localcontext() as context:
            context.prec = 512
            if Decimal(str(binding["started_at_s"])) + Decimal(stream["result"]["captured_through_s_decimal"]) > Decimal(str(event["at_s"])):
                raise ValueError("Stream capture extends past its manufacturing cycle")
        if stream["updates"][-1]["received_at_s"] > request["assessed_at_s"]:
            raise ValueError("Stream receipt occurs after the assessment")
        if not legacy:
            expected = [{"serial": p["serial"], "cavity_id": p["cavity_id"]} for p in event["data"]["outputs"]]
            mapping = stream["updates"][-1]["observation"]["part_mapping"]
            if any(p not in expected for p in mapping["items"]):
                raise ValueError("Stream mapping contradicts manufactured serials/cavities")
            if mapping["status"] == "cavity_resolved" and sorted(mapping["items"], key=lambda p:p["serial"]) != sorted(expected, key=lambda p:p["serial"]):
                raise ValueError("Resolved mapping does not cover the exact cycle outputs")
        streams[cid] = stream
    instances = baseline["instances"]
    reasons = {s: [] for s in instances}
    findings = []

    def add(roots, code, evidence):
        seen, todo = set(), list(roots)
        while todo:
            serial = todo.pop()
            if serial not in seen:
                seen.add(serial)
                todo.extend(instances[serial]["children"])
        body = {"code": code, "root_serials": sorted(roots), "affected_serials": sorted(seen), "evidence_ref": evidence}
        finding = {**body, "finding_ref": content_identity(body)}
        findings.append(finding)
        for serial in seen:
            reasons[serial].append(finding["finding_ref"])

    for serial, instance in instances.items():
        if instance["quality"] != "PASS" or instance["disposition"] in {"QUARANTINED", "SCRAPPED"}:
            add([serial], "EXISTING_INSPECTION_OR_DISPOSITION_HOLD", manufacturing["result_ref"])
        # Superset integration: retain the current manufacturing-v2 reducer's holds.
        if instance.get("quality_holds") or instance.get("process_quality", {}).get("status") not in {
                None, "PROCESS_SIGNATURE_ACCEPTABLE", "NOT_APPLICABLE_ASSEMBLY"}:
            add([serial], "EXISTING_MANUFACTURING_QC_HOLD", manufacturing["result_ref"])
    for cid, event in cycles.items():
        stream = streams.get(cid)
        for output in event["data"]["outputs"]:
            cavity = output["cavity_id"]
            if stream is None:
                add([output["serial"]], "MISSING_CYCLE_STREAM", content_identity(event))
            elif not stream["result"]["finalized"] or stream["result"]["cavities"][cavity]["state"] != "NO_PROCESS_HOLD_IDENTIFIED":
                add([output["serial"]], "STREAM_QUALITY_HOLD", stream["result_ref"])
    quality = {s: {"state": "SCRAPPED" if i["disposition"] == "SCRAPPED" else "HELD" if reasons[s] else "ELIGIBLE_FOR_REVIEW",
                    "finding_refs": reasons[s], "recorded_inspection": i["quality"], "recorded_disposition": i["disposition"]}
               for s, i in instances.items()}
    packages = {pid: {"state": "HELD" if any(quality[s]["state"] != "ELIGIBLE_FOR_REVIEW" for s in p["serials"]) else "ELIGIBLE_FOR_REVIEW",
                     "serials": deepcopy(p["serials"]), "shipment_release": "not_authorized"} for pid, p in baseline["packages"].items()}
    result = {"schema": "ciw.toolpoint-stream-containment.v1", "status": "RECORDED_NOT_RELEASED",
        "manufacturing_result_ref": manufacturing["result_ref"], "stream_result_refs": [s["result_ref"] for s in request["streams"]],
        "quality": quality, "packages": packages, "findings": findings, "authority": dict(AUTHORITY)}
    bounded(result)
    return result


def evaluate(request):
    result = project(request)
    report = {"schema": "ciw.toolpoint-stream-containment-report.v1", "operation_ref": content_identity({"operation": OPERATION}),
        "execution_id": new_identity("execution"), "request": deepcopy(request), "request_ref": content_identity(request),
        "result": result, "result_ref": content_identity(result)}
    bounded(report)
    return report


def verify(report):
    bounded(report)
    keys(report, {"schema", "operation_ref", "execution_id", "request", "request_ref", "result", "result_ref"})
    if report["schema"] != "ciw.toolpoint-stream-containment-report.v1" or report["operation_ref"] != content_identity({"operation": OPERATION}):
        raise ValueError("Wrong stream containment report")
    validate_identity(report["execution_id"], "execution")
    if report["request_ref"] != content_identity(report["request"]) or report["result_ref"] != content_identity(report["result"]):
        raise ValueError("Containment content identity mismatch")
    if content_identity(project(report["request"])) != report["result_ref"]:
        raise ValueError("Containment report differs from fresh replay")
    return {"schema": "ciw.toolpoint-stream-containment-verification.v1", "status": "PASS",
            "execution_id": report["execution_id"], "verification_id": new_identity("verification"),
            "result_ref": report["result_ref"], "scope": "same-implementation replay, not physical verification", "authority": dict(AUTHORITY)}


def gate(report, *, serials, action):
    verify(report)
    if action not in {"assemble", "pack", "release_review"}:
        raise ValueError("No physical actions; only assembly/packaging/release review")
    items(serials, minimum=1, maximum=512)
    for serial in serials:
        text(serial)
    if len(set(serials)) != len(serials):
        raise ValueError("Duplicate review target")
    blocked = {}
    allowed = {"AVAILABLE", "PACKED"} if action == "release_review" else {"AVAILABLE"}
    for serial in serials:
        if serial not in report["result"]["quality"]:
            raise ValueError("Unknown review target")
        q = report["result"]["quality"][serial]
        if q["state"] != "ELIGIBLE_FOR_REVIEW" or q["recorded_disposition"] not in allowed:
            blocked[serial] = deepcopy(q)
    return {"schema": "ciw.toolpoint-stream-review-gate.v1", "status": "HELD" if blocked else "ELIGIBLE_FOR_REVIEW",
            "action": action, "blocked": blocked, "result_ref": report["result_ref"], "authority": dict(AUTHORITY)}
