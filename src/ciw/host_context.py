"""Operator-owned workflow bindings.

The manifest path comes from the process environment or the control-serve
launch flag. Tool arguments cannot set it, and a model cannot supply a
checkout, a pin, or an executable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SCHEMA = "ncp.host-bindings.v1"
ENV = "NCP_HOST_MANIFEST"


def manifest_path() -> Path | None:
    raw = os.environ.get(ENV)
    if raw is None or not str(raw).strip():
        return None
    path = Path(raw)
    if not path.is_file():
        raise ValueError("Host manifest is not a file")
    return path


def load() -> dict:
    path = manifest_path()
    if path is None:
        return {"schema": SCHEMA, "authorization": "absent", "workflows": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict or value.get("schema") != SCHEMA:
        raise ValueError("Host manifest schema refused")
    if value.get("authorization") != "operator":
        raise ValueError("Host manifest is not an operator authorization")
    workflows = value.get("workflows")
    if type(workflows) is not dict:
        raise ValueError("Host manifest workflows must be an object")
    cleaned = {}
    for name, item in workflows.items():
        if type(name) is not str or not name or type(item) is not dict:
            raise ValueError("Host manifest workflow entry is malformed")
        roles = item.get("roles")
        if type(roles) is not dict or not roles:
            raise ValueError("Host manifest roles must be an object")
        bound = {}
        for role, raw_path in roles.items():
            if type(role) is not str or type(raw_path) is not str or not raw_path.strip():
                raise ValueError("Host manifest role path is empty")
            path = Path(raw_path)
            if not path.is_absolute():
                raise ValueError("Host manifest paths must be absolute")
            bound[role] = path
        cleaned[name] = {"roles": bound}
        inputs = item.get("inputs")
        if inputs is not None:
            if type(inputs) is not str or not Path(inputs).is_absolute():
                raise ValueError("Host manifest input must be an absolute path")
            cleaned[name]["inputs"] = Path(inputs)
        if "configuration" in item:
            if type(item["configuration"]) is not dict:
                raise ValueError("Host manifest configuration must be an object")
            cleaned[name]["configuration"] = item["configuration"]
    return {"schema": SCHEMA, "authorization": "operator", "workflows": cleaned}


def declared(workflow: str) -> bool:
    return workflow in load()["workflows"]


def bindings_for(workflow: str) -> dict | None:
    item = load()["workflows"].get(workflow)
    if item is None:
        return None
    return dict(item["roles"])


def input_path(workflow: str) -> Path | None:
    item = load()["workflows"].get(workflow)
    if item is None:
        return None
    return item.get("inputs")


def configuration_for(workflow: str):
    item = load()["workflows"].get(workflow)
    if item is None:
        return None
    return item.get("configuration")


def preflight() -> dict:
    """Record what the operator manifest declares. This does not run a provider."""
    document = load()
    rows = []
    for name, item in sorted(document["workflows"].items()):
        roles = []
        for role, path in sorted(item["roles"].items()):
            roles.append({
                "role": role,
                "exists": path.exists(),
                "kind": "directory" if path.is_dir() else "file" if path.is_file() else "missing",
            })
        source = item.get("inputs")
        rows.append({
            "workflow": name,
            "roles": roles,
            "input_declared": source is not None,
            "input_exists": bool(source and source.is_file()),
            "runtime_ready": False,
            "pin_checked": False,
            "source_checked": False,
            "readiness": "declared_only",
        })
    return {
        "schema": "ncp.host-preflight.v1",
        "authorization": document["authorization"],
        "workflows": rows,
        "authorizes_execution": False,
        "note": "exists is a declaration. runtime_ready, pin_checked and source_checked stay false here. Preflight does not run the provider.",
    }
