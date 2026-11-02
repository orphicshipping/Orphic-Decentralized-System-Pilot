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


def domain_run(domain, name=None, example=False, request=None, store=None):
    from .domain_surface import run
    return run(domain, name=name, example=example, request=request, store=store)


def workbench_list():
    from .workbench_surface import listing
    return listing()


def workbench_run(workflow, source_b64=None, upstream_artifact=None, upstream_artifacts=None, store=None):
    from .workbench_surface import run
    return run(workflow, source_b64=source_b64, upstream_artifact=upstream_artifact,
               upstream_artifacts=upstream_artifacts, store=store)


def retained_read(artifact_id, offset=None, limit=None, store=None):
    from .retention import _store, page
    target = store if store is not None else _store()
    if offset is None and limit is None:
        return target.bounded(artifact_id)
    return target.page(artifact_id, 0 if offset is None else offset, 32768 if limit is None else limit)


def retained_release(artifact_id, store=None):
    from .retention import _store
    target = store if store is not None else _store()
    return target.release(artifact_id)


def coordinate(action, idempotency_key=None, operation=None, arguments=None, exclusive=None,
               artifact_id=None, recipient=None, grant_id=None, store=None):
    from .coordinator import coordinate as run
    return run(action, store=store, idempotency_key=idempotency_key, operation=operation,
               arguments=arguments, exclusive=exclusive, artifact_id=artifact_id,
               recipient=recipient, grant_id=grant_id)


def specialist_list():
    from .specialist_surface import listing
    return listing()


def specialist_describe(host):
    from .specialist_surface import describe
    return describe(host)


def specialist_run(host):
    from .specialist_surface import run
    return run(host)


def host_preflight():
    from .host_context import preflight
    return preflight()


def ledger_read(capability_id):
    import json
    rows = json.loads(_packaged_text("capability-ledger.json"))["capabilities"]
    for item in rows:
        if item["id"] == capability_id:
            return {
                "record": item,
                "authorizes_execution": False,
                "note": "Release-ledger row. Reading it does not execute or qualify the capability.",
            }
    raise ValueError("Unknown release-ledger id")


def doctor(diagnostic="core"):
    from .doctor import diagnose
    if diagnostic not in {"core", "legibility"}:
        raise ValueError("Doctor on this host accepts core or legibility only")
    report = diagnose(diagnostic)
    report["qualification"] = "not_performed"
    report["authorizes_execution"] = False
    return report


def demo():
    from copy import deepcopy
    from .adapters.oscillator import make_demo_run
    run = make_demo_run()
    return {"schema": "ciw.synthetic-oscillator-demo.v1", "run_id": run["run_id"],
            "evidence_id": run["evidence_id"], "instrument": run["instrument"],
            "samples": len(run["time_s"]), "channels": sorted(run["channels"]),
            "provenance": deepcopy(run["metadata"].get("provenance")),
            "sensor": False, "physical_qualification": "not_established",
            "authorizes_execution": False}


def linear_preview(model, base_point, delta):
    from .linear_response import PROFILES, make_request, preview
    if model not in PROFILES:
        raise ValueError("Unknown linear-response model")

    def numbers(text):
        values = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                raise ValueError("Linear-response coordinate is empty")
            value = float(part)
            if value != value or value in {float("inf"), float("-inf")}:
                raise ValueError("Linear-response coordinate must be finite")
            values.append(value)
        return values

    source = None
    if model.startswith("oscillator-"):
        source = {"schema": "ciw.julia-oscillator-source.v1",
                  "experiment_id": "linear-response-oscillator-v1",
                  "operation_id": "ciw.julia-oscillator.v1",
                  "model": {"omega_0_rad_s": 2.0, "gamma_s_inv": 0.1, "mass_kg": 2.0},
                  "initial_state": {"q0_m": numbers(base_point)[0], "v0_m_s": numbers(base_point)[1]},
                  "time_s": [0.0, 0.1, 0.2],
                  "solver": {"abstol": 1e-10, "reltol": 1e-10, "maxiters": 100000},
                  "claim_scope": "simulated_numerical_trajectory_against_independent_analytic_oracle"}
    record = preview(make_request(model, numbers(base_point), numbers(delta), source=source))
    record["authorizes_execution"] = False
    return record


def lesson(mode="learn", level="structural"):
    import shutil
    import tempfile
    from pathlib import Path
    from .learning import TOPIC, lesson as load, work
    if mode not in {"learn", "work"}:
        raise ValueError("Lesson mode must be learn or work")
    body = load(TOPIC, level)
    if mode == "learn":
        return {"mode": "learn", "lesson": body, "physical_qualification": "not_established",
                "authorizes_execution": False}
    parent = Path(tempfile.mkdtemp(prefix="ncp-lesson-"))
    try:
        reply = work(parent / "work", TOPIC)
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    public = {key: value for key, value in reply.items() if key != "workspace_file"}
    return {"mode": "work", "topic": TOPIC, "level": level, "result": public,
            "physical_qualification": "not_established", "authorizes_execution": False}


def hypergraph():
    from .hypergraph_rewrite import example_request, run
    record = run(example_request("thermal"))
    return {"schema": record["schema"], "status": record["status"], "mode": record["mode"],
            "attempt_count": record["attempt_count"], "record_digest": record["record_digest"],
            "authority": record["authority"], "physical_qualification": "not_established",
            "authorizes_execution": False}


def fluid_field(solver):
    from .fluid_contract import simulate, verify
    from .fluid_workflow import bounded_request
    if solver not in {"wave", "sph"}:
        raise ValueError("ncp_fluid_field accepts wave or sph. Reservoir, molecular, and FSI are plan profiles.")
    request = bounded_request(solver)
    result = simulate(request)
    report = verify(request, result)
    qualification = report["qualification"]
    return {
        "profile": solver,
        "status": report["status"],
        "checks": report["checks"],
        "qualification_action": qualification.get("action"),
        "qualification_reason": qualification.get("reason"),
        "request_digest": report["request_digest"],
        "result_digest": report["result_digest"],
        "report_digest": report["record_digest"],
        "field_retained": False,
        "retention": "The field exceeds the control document budget and is not retained.",
        "physical_validation": "not_established",
        "authorizes_execution": False,
    }


def polymer_cycle():
    import base64
    import json
    from .data_compute_bridge import _polymer
    from .data_sources import retain_record
    from .polymer_contract import example_request
    from .workbench import Workbench
    request = example_request()
    workbench = Workbench()
    raw = json.dumps(retain_record(
        request, dataset_id="cycle", revision=1, parent_source_id=None,
        recorded_at="2026-10-05T21:00:00+00:00", data_family="polymer-cycle-request"), indent=2).encode()
    descriptor = workbench.add_source({"kind": "data-record", "label": "cycle",
                                        "bytes_b64": base64.b64encode(raw).decode()})
    result = _polymer({"source": workbench.get_source(descriptor["source_id"])})
    assessment = result["assessment"]
    return {"schema": result["schema"], "source_id": result["source_id"],
            "conservation": result["conservation"],
            "authority": assessment.get("authority"),
            "physical_qualification": "not_established", "authorizes_execution": False}


def toolpoint():
    import shutil
    import tempfile
    from pathlib import Path
    from .data_manufacturing_bridge import prepare_toolpoint, review
    from .workbench import Workbench
    destination = Path(tempfile.mkdtemp(prefix="ncp-toolpoint-"))
    try:
        parameters = prepare_toolpoint(Workbench(), destination)
        result = review(parameters)
    finally:
        shutil.rmtree(destination, ignore_errors=True)
    return {"schema": result["schema"], "scope": result["scope"],
            "storage_write": result["storage_write"], "hold_release": result["hold_release"],
            "authority": result["authority"], "containment_result_ref": result["containment_result_ref"],
            "containment": result["containment"], "physical_qualification": "not_established",
            "authorizes_execution": False}


CONTRACTS = {
    "ncp://contract/plug-in": "plug-in.json",
    "ncp://contract/capability-map": "capability-map.json",
    "ncp://contract/verification": "verification-report.json",
}


def _packaged_text(name: str) -> str:
    from pathlib import Path
    packaged = Path(__file__).resolve().parent / name
    checkout = {
        "plug-in.json": Path(__file__).resolve().parents[2] / "mcp" / "plug-in.json",
        "capability-map.json": Path(__file__).resolve().parents[2] / "mcp" / "capability-map.json",
        "verification-report.json": Path(__file__).resolve().parents[2] / "mcp" / "verification-report.json",
        "capability-ledger.json": Path(__file__).resolve().parents[2] / "release" / "capability-ledger.json",
    }[name]
    if packaged.is_file() and checkout.is_file() and packaged.read_bytes() != checkout.read_bytes():
        raise RuntimeError(name + " drifted from the packaged copy")
    path = packaged if packaged.is_file() else checkout
    return path.read_text(encoding="utf-8")


def list_contracts():
    return [{"uri": uri, "name": name, "mimeType": "application/json"}
            for uri, name in CONTRACTS.items()]


def read_contract(uri):
    import json
    if uri not in CONTRACTS:
        raise ValueError("Resource unavailable")
    value = json.loads(_packaged_text(CONTRACTS[uri]))
    if isinstance(value, dict):
        value["authorizes_execution"] = False
    return value


def open_demo(directory):
    """Open the synthetic demo session used for plan, run, and artifact tools.

    Not a physical instrument and not a pin selection. Close the host when finished.
    """
    from pathlib import Path
    from .agent_mcp import demo_config, from_profile
    from .control_gateway import ControlHost
    profile = demo_config(Path(directory))
    owner = from_profile(profile)
    host = ControlHost(owner)
    host._service_owner = owner
    return host


OPERATIONS = {
    "ncp_plug_in": plug_in,
    "ncp_domain_list": domain_list,
    "ncp_domain_run": domain_run,
    "ncp_workbench_list": workbench_list,
    "ncp_workbench_run": workbench_run,
    "ncp_retained_read": retained_read,
    "ncp_retained_release": retained_release,
    "ncp_coordinate": coordinate,
    "ncp_specialist_list": specialist_list,
    "ncp_specialist_describe": specialist_describe,
    "ncp_specialist_run": specialist_run,
    "ncp_host_preflight": host_preflight,
    "ncp_ledger_read": ledger_read,
    "ncp_doctor": doctor,
    "ncp_demo": demo,
    "ncp_linear_preview": linear_preview,
    "ncp_lesson": lesson,
    "ncp_hypergraph": hypergraph,
    "ncp_fluid_field": fluid_field,
    "ncp_polymer_cycle": polymer_cycle,
    "ncp_toolpoint": toolpoint,
}

SESSION = frozenset({
    "ncp_integrations_list",
    "ncp_integration_describe",
    "ncp_workflows_list",
    "ncp_capabilities_list",
    "ncp_index_list",
    "ncp_capability_describe",
    "ncp_plan_template",
    "ncp_plan_create",
    "ncp_plan_get",
    "ncp_plan_admit",
    "ncp_run_submit",
    "ncp_run_get",
    "ncp_run_cancel",
    "ncp_artifact_read",
    "ncp_observe",
    "ncp_compare",
    "ncp_qualify",
})


def invoke(name, arguments=None, host=None):
    """Call one named service. Unknown names refuse. Arguments cannot add a pin."""
    if not isinstance(name, str) or (name not in OPERATIONS and name not in SESSION):
        raise ValueError("Unknown service. Call a named operation. This SDK is not a shell.")
    arguments = {} if arguments is None else arguments
    if type(arguments) is not dict:
        raise ValueError("Service arguments must be an object")
    forbidden = {"checkout", "python", "python_executable", "provider_revision", "pin", "repository", "configuration"}
    if forbidden & set(arguments):
        raise ValueError("Service arguments cannot select a checkout or a pin")
    from .agent_api import encode
    from .agent_tools import _shape
    from .control_gateway import TOOLS
    if name not in TOOLS or len(encode(arguments)) > 65536:
        raise ValueError("Request exceeds the argument budget")
    _shape(arguments, TOOLS[name]["inputSchema"])
    if name in SESSION:
        if host is None:
            raise ValueError("This operation needs open_demo. A folder is not a session.")
        response = host.call(name, arguments)
        if response["status"] != "ok":
            raise ValueError("request_refused")
        return response["data"]
    store = None
    if host is not None:
        store = getattr(host, "retained", None)
        if store is None:
            raise ValueError("This host has no retained store")
    if name in {"ncp_domain_run", "ncp_workbench_run", "ncp_retained_read", "ncp_retained_release", "ncp_coordinate"}:
        return OPERATIONS[name](**arguments, store=store)
    if host is not None:
        raise ValueError("This operation does not take a session")
    return OPERATIONS[name](**arguments)


def capability_map():
    """Advertised tools and indexed capabilities. Reading this does not run them."""
    from .capabilities import catalog
    from .control_gateway import TOOLS
    from .domain_surface import tool_grants

    tools = []
    for name, spec in sorted(TOOLS.items()):
        schema = spec["inputSchema"]
        if name in OPERATIONS:
            route = "ciw.services.invoke"
        else:
            route = "ciw.services.open_demo"
        tools.append({
            "name": name,
            "service": route,
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
        "note": "Host-free tools use ciw.services.invoke. Plan and run tools use the host from ciw.services.open_demo. A row is not a grant.",
    }
