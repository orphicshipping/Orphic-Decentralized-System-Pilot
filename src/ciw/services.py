"""Transport-independent application services.

MCP tools and ordinary Python callers use these functions. There is no second
copy of the domain logic. A name that is not listed here is refused. This
module does not select a checkout, an interpreter, or a pin.
"""
from __future__ import annotations


def plug_in():
    from .plug_in import document
    return document()


def domain_list():
    from .domain_surface import listing
    return listing()


def domain_run(domain, name=None):
    from .domain_surface import run
    return run(domain, name)


def workbench_list():
    from .workbench_surface import listing
    return listing()


def workbench_run(workflow):
    from .workbench_surface import run
    return run(workflow)


def ledger_read(capability_id):
    import json
    from pathlib import Path
    ledger_path = Path(__file__).resolve().parents[2] / "release" / "capability-ledger.json"
    rows = json.loads(ledger_path.read_text(encoding="utf-8"))["capabilities"]
    for item in rows:
        if item["id"] == capability_id:
            return {
                "record": item,
                "authorizes_execution": False,
                "note": "Release-ledger row. Reading it does not execute or qualify the capability.",
            }
    raise ValueError("Unknown release-ledger id")


# Explicit operations. Not a catch-all over the filesystem or a shell.
OPERATIONS = {
    "ncp_plug_in": plug_in,
    "ncp_domain_list": domain_list,
    "ncp_domain_run": domain_run,
    "ncp_workbench_list": workbench_list,
    "ncp_workbench_run": workbench_run,
    "ncp_ledger_read": ledger_read,
}


def invoke(name, arguments=None):
    """Call one named service. Unknown names refuse. Arguments cannot add a pin."""
    if not isinstance(name, str) or name not in OPERATIONS:
        raise ValueError("Unknown service. Call a named operation. This SDK is not a shell.")
    arguments = {} if arguments is None else arguments
    if type(arguments) is not dict:
        raise ValueError("Service arguments must be an object")
    forbidden = {"checkout", "python", "python_executable", "provider_revision", "pin", "repository"}
    if forbidden & set(arguments):
        raise ValueError("Service arguments cannot select a checkout or a pin")
    return OPERATIONS[name](**arguments)


def capability_map():
    """Advertised tools and indexed capabilities. Reading this does not run them."""
    from .capabilities import catalog
    from .control_gateway import TOOLS
    from .domain_surface import tool_grants

    tools = []
    for name, spec in sorted(TOOLS.items()):
        schema = spec["inputSchema"]
        tools.append({
            "name": name,
            "service": "ciw.services.invoke" if name in OPERATIONS else "ciw.control_gateway.ControlHost.call",
            "read_only": bool(spec["annotations"]["readOnlyHint"]),
            "required": list(schema.get("required") or []),
            "description": spec["description"],
            "authorizes_execution": False,
        })
    indexed = []
    grants = tool_grants()
    for item in catalog()["capabilities"]:
        capability_id = item["capability_id"]
        indexed.append({
            "capability_id": capability_id,
            "module": item.get("module"),
            "kind": item.get("kind"),
            "mcp_tool": grants.get(capability_id),
            "authorizes_execution": False,
            "notes": item.get("notes"),
        })
    return {
        "schema": "ncp.capability-map.v1",
        "authorizes_execution": False,
        "qualification": "not_performed_by_this_file",
        "tools": tools,
        "indexed": indexed,
        "note": "Same domain functions back the MCP tools and ciw.services.invoke. A row is not a grant.",
    }
