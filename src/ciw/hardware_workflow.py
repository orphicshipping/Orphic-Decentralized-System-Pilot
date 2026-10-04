"""Imported hardware assessment using the existing NET Session and identities.

No acquisition, sanitization, provisioning or equipment control is performed.
Inspection checks retained decisions; explicit replay creates a new occurrence.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import html
import json
import os
from pathlib import Path
import platform
import stat
import tempfile

from . import hardware_contract as contract
from .adapters.protocol import InstrumentManifest
from .control_contracts import bytes_ref, content_ref, keys, save_new, text
from .core.identities import evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .operations.registry import Operation, OperationRegistry
from .operations.runner import digest

INSTRUMENT = "hardware-observation-declaration.v1"
CHANNEL = "hardware_declaration"
FRAME = "hardware-declaration"
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_WORKSPACE_BYTES = 32 * 1024 * 1024
SCOPE = "numeric_comparison_of_imported_hardware_observations"


def runtime_identity() -> dict:
    from . import control_contracts
    from .core import identities
    from .operations import runner
    modules = [contract, control_contracts, identities, runner,
               __import__(__name__, fromlist=["*"])]
    raw = b"\0".join(Path(module.__file__).name.encode() + b"\0" +
                     Path(module.__file__).read_text(encoding="utf-8").replace("\r\n", "\n").encode()
                     for module in modules)
    return {"provider": "ciw.hardware", "version": "1", "code_sha256": sha256(raw).hexdigest(),
            "source_normalization": "utf8_lf", "scope": SCOPE,
            "environment": {"python": platform.python_version()}}


def validate_runtime(runtime: dict | None, *, allow_absent: bool = False) -> None:
    if runtime is None and allow_absent:
        return
    keys(runtime, {"provider", "version", "code_sha256", "source_normalization", "scope", "environment"})
    if (runtime["provider"] != "ciw.hardware" or runtime["version"] != "1"
            or runtime["source_normalization"] != "utf8_lf" or runtime["scope"] != SCOPE):
        raise ValueError("Hardware runtime identity or scope differs")
    content_ref("sha256:" + runtime["code_sha256"] if type(runtime["code_sha256"]) is str else None)
    keys(runtime["environment"], {"python"})
    text(runtime["environment"]["python"])


def make_source(request: dict, input_bytes_ref: str) -> dict:
    request = contract.validate_request(request)
    content_ref(input_bytes_ref)
    manifest = InstrumentManifest(
        instrument_id=INSTRUMENT, role="retained_hardware_observation_declaration",
        units={CHANNEL: "1"}, frames=(FRAME,),
        sampling={"kind": "one_imported_declaration", "time_semantics": "selection_envelope_only"},
        supported_operations=(contract.OPERATION,),
        calibration_requirements={"authority": "operator declarations; physical qualification separate"})
    source = {"run_schema": "run.v1",
              "run_id": "run-hardware-" + digest({"request": request, "input_bytes_ref": input_bytes_ref})[7:23],
              "instrument": INSTRUMENT,
              "metadata": {"duration_s": 1.0, "sample_count": 1, "sample_rate_hz": None,
                           "coordinate_frame": FRAME, "manifest": manifest.to_dict(),
                           "hardware_request": request, "input_bytes_ref": input_bytes_ref,
                           "provenance": {"source": request["observations"]["source_kind"],
                                          "generator": "ciw.hardware_workflow.make_source", "generator_version": 1}},
              "time_s": [0.0], "channels": {CHANNEL: {"unit": "1", "values": [1.0]}}, "render": {}}
    source["evidence_id"] = evidence_id(source)
    return source


def source_request(source: dict) -> dict:
    validate_run_structure(source)
    validate_evidence_identity(source)
    request = contract.validate_request(source["metadata"]["hardware_request"])
    if digest(source) != digest(make_source(request, source["metadata"]["input_bytes_ref"])):
        raise ValueError("Hardware source differs from its exact retained declaration")
    return request


def _assess(source: dict, parameters: dict) -> dict:
    keys(parameters, set())
    return contract.assess(source_request(source))


def operation() -> Operation:
    return Operation(contract.OPERATION, "decision", _assess, runtime_identity)


def registry() -> OperationRegistry:
    value = OperationRegistry()
    value.register(operation())
    return value


def capability_registry(*, bind: bool = False):
    from .control_plane import CapabilityRegistry
    value = CapabilityRegistry()
    manifest = InstrumentManifest(
        instrument_id=contract.OPERATION, role="decision", units={}, frames=(),
        sampling={"kind": "retained_hardware_observation_declaration"},
        supported_operations=(contract.OPERATION,),
        calibration_requirements={"authority": "numeric criteria only; no release authorization"})
    value.advertise(manifest, runtime=runtime_identity(),
                    capabilities={contract.OPERATION: ["hardware.assess"]}, inputs={contract.OPERATION: {}})
    if bind:
        value.bind(operation())
    return value


def validate_payload(operation_id: str, data: dict, source: dict, parameters: dict, selection: dict) -> None:
    if operation_id != contract.OPERATION:
        raise ValueError("Unsupported hardware operation")
    keys(parameters, set())
    if selection["channel"] != CHANNEL or selection["interval_s"] != [0.0, 1.0]:
        raise ValueError("Hardware assessment requires the complete imported declaration")
    contract.validate_assessment(source_request(source), data)


def read_bytes(path: Path, *, limit: int = MAX_REQUEST_BYTES) -> bytes:
    """Read one bounded regular file without following a final symlink."""
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Hardware inputs must be regular files without symlinks")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        actual = os.fstat(descriptor)
        if not stat.S_ISREG(actual.st_mode) or not 0 < actual.st_size <= limit:
            raise ValueError("Hardware input is empty, non-regular, or exceeds byte budget")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        if not raw or len(raw) > limit:
            raise ValueError("Hardware input is empty or exceeds byte budget")
        return raw
    finally:
        os.close(descriptor)


def parse_request(raw: bytes) -> dict:
    from .session import loads_json
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_REQUEST_BYTES:
        raise ValueError("Hardware request exceeds byte budget or is empty")
    return contract.validate_request(loads_json(raw.decode("utf-8")))


def run(request: dict, destination: Path, *, raw: bytes | None = None) -> dict:
    """Retain exact input bytes and execute one explicit decision occurrence."""
    from .session import Session
    request = contract.validate_request(request)
    if raw is None:
        raw = (json.dumps(request, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if digest(parse_request(raw)) != digest(request):
        raise ValueError("Raw request bytes differ from supplied request")
    source = make_source(request, bytes_ref(raw))
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "request.json").open("xb") as stream:
        stream.write(raw)
    session = Session(source, destination, operations=registry())
    response = session.handle({"protocol_version": 1, "request_id": "hardware-assessment",
                               "type": "operation.execute",
                               "payload": {"operation_id": contract.OPERATION, "parameters": {}}})
    session.save_workspace(destination / "workspace.json")
    if response["type"] != "response":
        raise ValueError(response["payload"]["message"])
    return inspect(destination)


def _read(destination: Path):
    from .session import Session, loads_json
    destination = Path(destination)
    raw = read_bytes(destination / "request.json")
    request = parse_request(raw)
    workspace = loads_json(read_bytes(destination / "workspace.json", limit=MAX_WORKSPACE_BYTES).decode("utf-8"))
    keys(workspace, {"workspace_version", "saved_at", "run", "selection", "results", "view_settings", "executions"})
    if type(workspace["workspace_version"]) is not int or workspace["workspace_version"] != 2:
        raise ValueError("Hardware bundle requires workspace version 2 without unreviewed corrections or native artifacts")
    # Restore a frozen, bounded snapshot only in scratch; saved data binds no code.
    with tempfile.TemporaryDirectory(prefix="net-hardware-inspect-") as temporary:
        root = Path(temporary)
        save_new(root / "workspace.json", workspace)
        session = Session.from_workspace(root / "workspace.json", root / "restored")
    if digest(session.run) != digest(make_source(request, bytes_ref(raw))):
        raise ValueError("Retained request bytes or declaration differ from workspace evidence")
    executions = list(session.executions.values())
    if len(executions) != 1 or executions[0]["operation_id"] != contract.OPERATION:
        raise ValueError("Hardware bundle requires exactly one assessment occurrence")
    execution = executions[0]
    validate_runtime(execution["runtime"], allow_absent=execution["status"] == "refused")
    if execution["status"] == "refused":
        if session.results:
            raise ValueError("Refused hardware assessment cannot contain a result")
        result = None
    else:
        if len(session.results) != 1:
            raise ValueError("Hardware bundle requires exactly one assessment result")
        result = next(iter(session.results.values()))
        if result["operation_id"] != contract.OPERATION:
            raise ValueError("Hardware result operation differs")
        validate_runtime(result["runtime"])
    return request, raw, session, execution, result


def _summary(request, session, execution, result) -> dict:
    return {"schema": "ciw.hardware-inspection.v1",
            "status": "REFUSED" if result is None else result["data"]["status"],
            "asset_id": request["configuration"]["asset_id"],
            "configuration_ref": contract.configuration_ref(request["configuration"]),
            "source_kind": request["observations"]["source_kind"],
            "evidence_id": session.run["evidence_id"], "input_bytes_ref": session.run["metadata"]["input_bytes_ref"],
            "operation_id": contract.OPERATION, "execution_id": execution["execution_id"],
            "result_id": None if result is None else result["result_id"],
            "verification_status": "not_verified", "provider_executed_by_inspection": False,
            "assessment": None if result is None else deepcopy(result["data"]),
            "refusal": deepcopy(execution.get("refusal"))}


def inspect(destination: Path) -> dict:
    request, _raw, session, execution, result = _read(destination)
    return _summary(request, session, execution, result)


def replay(destination: Path, output_dir: Path) -> dict:
    request, raw, _session, _execution, _result = _read(destination)
    return run(request, output_dir, raw=raw)


def export_report(destination: Path, output: Path) -> dict:
    request, _raw, session, execution, result = _read(destination)
    summary = _summary(request, session, execution, result)
    escape = lambda value: html.escape("Not recorded" if value is None else str(value), quote=True)
    config, plan, observations = request["configuration"], request["plan"], request["observations"]
    synthetic = observations["source_kind"] == "synthetic"
    source_label = "SYNTHETIC EXAMPLE — no equipment tested" if synthetic else "Operator-imported observations — acquisition not authenticated"
    fields = [("Asset", config["asset_id"]), ("Equipment", config["manufacturer"] + " " + config["model"]),
              ("Serial number", config["serial_number"]), ("Configuration revision", config["revision"]),
              ("Configuration identity", summary["configuration_ref"]),
              ("Plan", plan["plan_id"] + " / " + plan["revision"]), ("Purpose", plan["purpose"]),
              ("Plan identity", digest(plan)), ("Declared capture time", observations["captured_at"]),
              ("Operator", observations["operator"]), ("Tool", observations["tool"]["name"] + " / " + observations["tool"]["version"]),
              ("Environment", observations["environment"]), ("Evidence identity", summary["evidence_id"]),
              ("Imported request bytes", summary["input_bytes_ref"]), ("Execution", summary["execution_id"]),
              ("Result", summary["result_id"]), ("Runtime code SHA256", execution["runtime"]["code_sha256"] if execution["runtime"] else "unavailable")]
    metadata = "".join(f"<dt>{escape(name)}</dt><dd>{escape(value)}</dd>" for name, value in fields)
    rows = ""
    if result is not None:
        observed = {row["test_id"]: row for row in observations["records"]}
        checks = {row["test_id"]: row for row in result["data"]["checks"]}
        for criterion in plan["checks"]:
            check = checks[criterion["test_id"]]
            record = observed.get(criterion["test_id"])
            measured = "Missing" if record is None else f"{record['value']} {record['unit']} ({record['status']}; {record['duration_s']} s; procedure: {record['procedure_id']})"
            lower, upper = criterion["minimum"], criterion["maximum"]
            if lower is None:
                bounds = f"value ≤ {upper}"
            elif upper is None:
                bounds = f"value ≥ {lower}"
            else:
                bounds = f"{lower} ≤ value ≤ {upper}"
            limits = f"{bounds} {criterion['unit']}; duration ≥ {criterion['minimum_duration_s']} s"
            values = [criterion["test_id"], criterion["procedure_id"], measured, limits, check["status"], check["reason"],
                      "" if record is None else record["notes"]]
            rows += "<tr>" + "".join("<td>" + escape(value) + "</td>" for value in values) + "</tr>"
    else:
        rows = '<tr><td colspan="7">' + escape(summary["refusal"]) + "</td></tr>"
    components = "".join("<tr>" + "".join("<td>" + escape(component[key]) + "</td>"
                        for key in ("slot", "kind", "manufacturer", "model", "serial_number", "firmware")) + "</tr>"
                         for component in config["components"])
    software = "".join("<li>" + escape(item["name"]) + " — " + escape(item["version"]) + "</li>" for item in config["software"])
    content = f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hardware assessment — {escape(config['asset_id'])}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;color:#182a35;max-width:1200px;margin:40px auto;padding:0 24px}}h1{{margin-bottom:8px}}.notice{{padding:16px;background:#fff3ce;border-left:4px solid #b07700}}dt{{font-weight:650;margin-top:8px}}dd{{margin:0;overflow-wrap:anywhere}}table{{width:100%;border-collapse:collapse;margin:20px 0}}th,td{{border:1px solid #d5dee4;text-align:left;padding:8px;overflow-wrap:anywhere}}th{{background:#edf2f5}}.status{{font-size:24px;font-weight:700}}@media print{{body{{margin:0;font-size:10pt}}thead{{display:table-header-group}}tr{{break-inside:avoid}}}}</style>
<h1>Hardware assessment</h1><p>Notation Systems · NET</p>
<p class="notice">{escape(source_label)}</p><p class="status">Declared criteria: {escape(summary['status'])}</p>
<p>PASS means the imported values satisfy this declared numeric plan. It does not certify the equipment,
authenticate its source, establish physical qualification, or authorize sale or deployment.
No sanitization, firmware changes, provisioning, or equipment control was performed by this workflow.</p>
<h2>Equipment and evidence</h2><dl>{metadata}</dl>
<h2>Installed components</h2><table><thead><tr><th>Slot</th><th>Kind</th><th>Manufacturer</th><th>Model</th><th>Serial</th><th>Firmware</th></tr></thead><tbody>{components}</tbody></table>
<h2>Software configuration</h2><ul>{software}</ul>
<h2>Recorded checks</h2>
<table><thead><tr><th>Test</th><th>Required procedure</th><th>Observation</th><th>Requirement</th><th>Outcome</th><th>Reason</th><th>Notes</th></tr></thead><tbody>{rows}</tbody></table>
<p>Verification status: not_verified. Evidence hashes establish content consistency, not physical truth.
Inspection and this report do not rerun acquisition or the assessment provider.</p></html>'''
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return {**summary, "report": str(output), "report_sha256": bytes_ref(content.encode("utf-8"))}
