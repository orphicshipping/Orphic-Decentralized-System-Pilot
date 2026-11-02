"""ODSP logistics segment.

This is an operating path, not a Git branch and not a second logistics engine.
The attached logistics system exposes one read-only MCP tool, inspect_retained,
on protocol 2025-06-18. Dispatch, booking and package delivery are not that tool.
A model argument cannot name the logistics endpoint or a credential.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

ARCHIVE = "217a09016116123a5be95544b870e990c895e142"
PROTOCOL = "2025-06-18"
SEGMENT = "odsp-logistics"

_READS = {
    "catalog": ("catalog", ()),
    "workflow-plan": ("workflow", ("plan_id",)),
    "workflow-run": ("workflow", ("plan_id", "run_id")),
    "acquisition": ("acquisition", ("plan_id",)),
    "compute-status": ("compute", ("job_id",)),
    "compute-result": ("compute", ("job_id",)),
    "observation": ("observation", ("observation_id", "observation_digest")),
    "state": ("state", ("entity_id", "valid_at", "known_at")),
    "twin": ("twin", ("projection_id",)),
    "package-manifest": ("package", ("package_id", "version")),
}
_COMPUTE = frozenset({"vehicle-routing", "terminal-events", "instrumentation"})
_REFUSED = frozenset({"dispatch", "booking", "package-delivery"})
INTENTS = tuple(sorted([*_READS, *_COMPUTE, *_REFUSED]))


def listing() -> dict:
    return {
        "schema": "ncp.logistics-pathway.v1",
        "segment": SEGMENT,
        "archive": ARCHIVE,
        "protocol": PROTOCOL,
        "tool": "inspect_retained",
        "reads": sorted(_READS),
        "compute_on_logistics_host": sorted(_COMPUTE),
        "refused_here": sorted(_REFUSED),
        "grants_shared": False,
        "authorizes_execution": False,
        "physical_qualification": "not_established",
    }


def route(intent: str) -> dict:
    if intent in _REFUSED:
        disposition = "refused"
    elif intent in _COMPUTE:
        disposition = "logistics_compute"
    elif intent in _READS:
        disposition = "logistics_read"
    else:
        raise ValueError("unknown_logistics_intent")
    return {"schema": "ncp.logistics-route.v1", "segment": SEGMENT, "intent": intent,
            "disposition": disposition, "grants_shared": False, "authorizes_execution": False}


def _source(intent: str, fields: dict) -> dict:
    kind, required = _READS[intent]
    missing = [name for name in required if type(fields.get(name)) is not str]
    if missing:
        raise ValueError("logistics_source_incomplete")
    if kind == "catalog":
        return {"kind": "catalog"}
    if kind == "workflow":
        source = {"kind": "workflow", "mode": "plan" if intent == "workflow-plan" else "run", "planId": fields["plan_id"]}
        if intent == "workflow-run":
            source["runId"] = fields["run_id"]
        return source
    if kind == "acquisition":
        return {"kind": "acquisition", "planId": fields["plan_id"]}
    if kind == "compute":
        return {"kind": "compute", "jobId": fields["job_id"], "include": "status" if intent == "compute-status" else "result"}
    if kind == "observation":
        return {"kind": "observation", "observationId": fields["observation_id"], "observationDigest": fields["observation_digest"]}
    if kind == "state":
        return {"kind": "state", "entityId": fields["entity_id"], "validAt": fields["valid_at"], "knownAt": fields["known_at"]}
    if kind == "twin":
        return {"kind": "twin", "projectionId": fields["projection_id"]}
    return {"kind": "package", "mode": "manifest", "packageId": fields["package_id"], "version": fields["version"]}


def _endpoint() -> str:
    raw = os.environ.get("NCP_LOGISTICS_ENDPOINT", "")
    if type(raw) is not str or not raw:
        raise ValueError("logistics_host_unbound")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("logistics_endpoint_refused")
    return raw


def _post(url: str, payload: dict) -> dict:
    import http.client
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection = http.client.HTTPSConnection(parsed.hostname, port, timeout=5) if parsed.scheme == "https" else http.client.HTTPConnection(parsed.hostname, port, timeout=5)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    token = os.environ.get("NCP_LOGISTICS_BEARER", "")
    headers = {"content-type": "application/json", "mcp-protocol-version": PROTOCOL, "x-read-purpose": "read-surface:mcp"}
    if token:
        headers["authorization"] = "Bearer " + token
    try:
        connection.request("POST", path, json.dumps(payload).encode(), headers)
        response = connection.getresponse()
        raw = response.read(65537)
    finally:
        connection.close()
    if len(raw) > 65536:
        raise ValueError("logistics_response_exceeds_budget")
    if response.status != 200:
        raise ValueError("logistics_read_refused")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("logistics_read_refused") from exc
    if type(body) is not dict or body.get("jsonrpc") != "2.0":
        raise ValueError("logistics_read_refused")
    return body


def call(intent: str, store=None, **fields) -> dict:
    decision = route(intent)
    if decision["disposition"] == "refused":
        raise ValueError("logistics_effect_refused")
    if decision["disposition"] == "logistics_compute":
        raise ValueError("logistics_compute_not_this_host")
    source = _source(intent, fields)
    url = _endpoint()
    body = _post(url, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "inspect_retained", "arguments": {"source": source}}})
    if body.get("error") or (body.get("result") or {}).get("isError") is True:
        raise ValueError("logistics_read_refused")
    record = {"schema": "ncp.logistics-inspection.v1", "segment": SEGMENT, "intent": intent,
              "source": source, "result": body.get("result"), "authorizes_execution": False,
              "physical_qualification": "not_established"}
    from .retention import _store
    holder = store if store is not None else _store()
    artifact_id = holder.keep(record, kind="logistics-read", catalog={"schema": "ncp.logistics-inspection.v1", "segment": SEGMENT})
    return {"schema": "ncp.logistics-inspection.v1", "artifact_id": artifact_id, "intent": intent,
            "executed_on": "logistics-read", "authorizes_execution": False,
            "physical_qualification": "not_established"}
