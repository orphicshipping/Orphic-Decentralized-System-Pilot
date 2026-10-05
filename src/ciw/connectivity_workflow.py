"""Connectivity observations through existing Session/OperationRegistry records.

Offline inspection recomputes this bounded projection, never an external core
or an HTTP request. Replaying allocates a fresh execution and result occurrence.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import html
import json
from pathlib import Path
import platform
import tempfile
from threading import Lock

from . import connectivity as contract
from .adapters.protocol import InstrumentManifest
from .control_contracts import bytes_ref, content_ref, keys, save_new
from .core.identities import canonical_json, evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .hardware_workflow import read_bytes
from .operations.registry import Operation, OperationRegistry
from .operations.runner import digest
from .operations.schemas import register_payload_validator

INSTRUMENT = "connectivity-export.v1"
CHANNEL = "connectivity_declaration"
FRAME = "connectivity-declaration"
MAX_WORKSPACE_BYTES = 4 * 1024 * 1024
_REGISTERED = False
_LOCK = Lock()


def register_schema() -> None:
    global _REGISTERED
    with _LOCK:
        if not _REGISTERED:
            register_payload_validator(contract.OPERATION, validate_payload, role="backend")
            _REGISTERED = True


def runtime_identity() -> dict:
    from . import control_contracts
    from .core import identities
    from .operations import runner
    modules = (contract, control_contracts, identities, runner,
               __import__(__name__, fromlist=["*"]))
    raw = b"\0".join(Path(m.__file__).name.encode() + b"\0" +
                     Path(m.__file__).read_text(encoding="utf-8").replace("\r\n", "\n").encode()
                     for m in modules)
    return {"provider": "ciw.connectivity", "version": "1", "code_sha256": sha256(raw).hexdigest(),
            "source_normalization": "utf8_lf", "python": platform.python_version(),
            "scope": "aggregate_projection_not_native_core_execution"}


def validate_runtime(value: dict) -> None:
    keys(value, {"provider", "version", "code_sha256", "source_normalization", "python", "scope"})
    if (value["provider"] != "ciw.connectivity" or value["version"] != "1"
            or value["source_normalization"] != "utf8_lf"
            or value["scope"] != "aggregate_projection_not_native_core_execution"):
        raise ValueError("Connectivity runtime identity or scope differs")
    content_ref("sha256:" + value["code_sha256"])
    if type(value["python"]) is not str or len(value["python"]) > 64:
        raise ValueError("Invalid retained Python identity")


def make_source(request: dict, raw_ref: str) -> dict:
    request = contract.validate_request(request)
    content_ref(raw_ref)
    manifest = InstrumentManifest(instrument_id=INSTRUMENT, role="retained_connectivity_export",
        units={CHANNEL: "1"}, frames=(FRAME,),
        sampling={"kind": "one_declaration", "time_semantics": "selection_envelope_not_network_or_sensor_time"},
        supported_operations=(contract.OPERATION,),
        calibration_requirements={"authority": "no_calibration_or_clock_qualification"})
    source = {"run_schema": "run.v1", "run_id": "run-connectivity-" + digest({"request": request, "raw_ref": raw_ref})[7:23],
        "instrument": INSTRUMENT,
        "metadata": {"duration_s": 1.0, "sample_count": 1, "sample_rate_hz": None,
                     "coordinate_frame": FRAME, "manifest": manifest.to_dict(),
                     "connectivity_request": request, "input_bytes_ref": raw_ref,
                     "provenance": {"source": request["source_kind"], "generator": "ciw.connectivity_workflow.make_source",
                                    "generator_version": 1}},
        "time_s": [0.0], "channels": {CHANNEL: {"unit": "1", "values": [1.0]}}, "render": {}}
    source["evidence_id"] = evidence_id(source)
    return source


def source_request(source: dict) -> dict:
    validate_run_structure(source)
    validate_evidence_identity(source)
    request = contract.validate_request(source["metadata"]["connectivity_request"])
    if canonical_json(source) != canonical_json(make_source(request, source["metadata"]["input_bytes_ref"])):
        raise ValueError("Connectivity source differs from its complete retained declaration")
    return request


def _execute(source: dict, parameters: dict) -> dict:
    keys(parameters, set())
    return contract.observe(source_request(source))


def registry() -> OperationRegistry:
    register_schema()
    result = OperationRegistry()
    result.register(Operation(contract.OPERATION, "backend", _execute, runtime_identity))
    return result


def validate_payload(operation_id: str, data: dict, source: dict, parameters: dict, selection: dict) -> None:
    keys(parameters, set())
    if (operation_id != contract.OPERATION or selection["channel"] != CHANNEL
            or selection["interval_s"] != [0.0, 1.0]):
        raise ValueError("Connectivity requires the full declaration selection")
    if canonical_json(data) != canonical_json(contract.observe(source_request(source))):
        raise ValueError("Connectivity payload differs from retained source projection")


def parse_request(raw: bytes) -> dict:
    from .session import loads_json
    if type(raw) is not bytes or not 0 < len(raw) <= contract.MAX_REQUEST_BYTES:
        raise ValueError("Require a nonempty connectivity request within 256 KiB")
    return contract.validate_request(loads_json(raw.decode("utf-8")))


def run(request: dict, destination: Path, *, raw: bytes | None = None) -> dict:
    from .session import Session
    request = contract.validate_request(request)
    if raw is None:
        raw = (json.dumps(request, indent=2, allow_nan=False) + "\n").encode()
    if canonical_json(parse_request(raw)) != canonical_json(request):
        raise ValueError("Request and original input bytes disagree")
    source = make_source(request, bytes_ref(raw))
    bound = registry()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "request.json").open("xb") as stream:
        stream.write(raw)
    session = Session(source, destination, operations=bound)
    response = session.handle({"protocol_version": 1, "request_id": "connectivity-observation",
        "type": "operation.execute", "payload": {"operation_id": contract.OPERATION, "parameters": {}}})
    session.save_workspace(destination / "workspace.json")
    if response["type"] != "response":
        raise ValueError(response["payload"]["message"])
    return inspect(destination)


def _read(destination: Path):
    from .session import Session, loads_json
    register_schema()
    destination = Path(destination)
    raw = read_bytes(destination / "request.json", limit=contract.MAX_REQUEST_BYTES)
    request = parse_request(raw)
    workspace = loads_json(read_bytes(destination / "workspace.json", limit=MAX_WORKSPACE_BYTES).decode("utf-8"))
    keys(workspace, {"workspace_version", "saved_at", "run", "selection", "results", "view_settings", "executions"})
    if type(workspace["workspace_version"]) is not int or workspace["workspace_version"] != 2:
        raise ValueError("Connectivity bundle requires the bounded V2 recording workspace")
    with tempfile.TemporaryDirectory(prefix="net-connectivity-inspect-") as temporary:
        root = Path(temporary)
        save_new(root / "workspace.json", workspace)
        session = Session.from_workspace(root / "workspace.json", root / "restored")
    if canonical_json(session.run) != canonical_json(make_source(request, bytes_ref(raw))):
        raise ValueError("Request bytes differ from retained connectivity evidence")
    executions = list(session.executions.values())
    if len(executions) != 1 or executions[0]["operation_id"] != contract.OPERATION:
        raise ValueError("Expected one connectivity execution occurrence")
    execution = executions[0]
    if execution["runtime"] is not None:
        validate_runtime(execution["runtime"])
    result = None
    if execution["status"] == "completed":
        if len(session.results) != 1:
            raise ValueError("Expected one completed connectivity result")
        result = next(iter(session.results.values()))
        validate_runtime(result["runtime"])
        if canonical_json(result["runtime"]) != canonical_json(execution["runtime"]):
            raise ValueError("Execution and result runtime differ")
    elif session.results:
        raise ValueError("Refused connectivity execution cannot contain a result")
    return request, raw, session, execution, result


def inspect(destination: Path) -> dict:
    request, _raw, session, execution, result = _read(destination)
    return {"schema": "ciw.connectivity-inspection.v1", "status": "REFUSED" if result is None else result["data"]["status"],
        "source_kind": request["source_kind"], "evidence_id": session.run["evidence_id"],
        "input_bytes_ref": session.run["metadata"]["input_bytes_ref"], "operation_id": contract.OPERATION,
        "execution_id": execution["execution_id"], "result_id": None if result is None else result["result_id"],
        "verification_status": "not_verified", "network_access_by_inspection": False,
        "observation": None if result is None else deepcopy(result["data"]), "refusal": deepcopy(execution.get("refusal"))}


def replay(destination: Path, output_dir: Path) -> dict:
    request, raw, _session, _execution, _result = _read(destination)
    return run(request, output_dir, raw=raw)


def report(destination: Path, output: Path) -> dict:
    summary = inspect(destination)
    esc = lambda value: html.escape(str(value), quote=True)
    observation = summary["observation"]
    rows = "" if observation is None else "".join(
        "<tr>" + "".join("<td>" + esc(value) + "</td>" for value in
        (item["name"], canonical_json(item["labels"]), item["value"], item["unit"])) + "</tr>"
        for item in observation["metrics"])
    missing = "" if observation is None else esc(canonical_json(observation["missing_metrics"]))
    body = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Notation connectivity record</title><style>
body{{background:#182319;color:#e6efd5;font:16px/1.6 ui-monospace,monospace;max-width:1000px;margin:2rem auto;padding:1rem}}
section{{border:3px solid #b9ce9a;padding:1rem;margin:1rem 0;box-shadow:5px 5px 0 #081209}}
h1{{letter-spacing:.08em;font-size:1.6rem}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;border-bottom:1px solid #728765;padding:.5rem}}
pre,code,td{{overflow-wrap:anywhere;white-space:pre-wrap}}.notice{{font-weight:bold}}@media(max-width:600px){{body{{font-size:14px}}table{{display:block;overflow-x:auto}}}}
</style></head><body><h1>NOTATION / CONNECTIVITY</h1>
<section><p class="notice">RETAINED RECORD / {esc(summary['source_kind'])}</p>
<p>Capture result: {esc(summary['status'])}. This is not a live network-status display.</p>
<p>Core execution: NOT PERFORMED. Radio authority: NONE. Verification: NOT VERIFIED.</p></section>
<section><h2>Aggregate gauges</h2><table><thead><tr><th>Metric</th><th>Labels</th><th>Value</th><th>Unit</th></tr></thead><tbody>{rows}</tbody></table>
<p>Missing expected aggregates: {missing}</p></section>
<section><h2>Retained evidence</h2><p>Evidence: <code>{esc(summary['evidence_id'])}</code></p>
<p>Execution: <code>{esc(summary['execution_id'])}</code></p><p>Result: <code>{esc(summary['result_id'])}</code></p>
<p>Counts do not establish per-device connectivity, end-to-end delivery, calibration or clock synchronization.</p>
<details><summary>Full scoped observation</summary><pre>{esc(json.dumps(observation, indent=2, allow_nan=False))}</pre></details></section>
</body></html>'''
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(body)
    return {"status": "created", "report": str(output), "report_ref": bytes_ref(body.encode()),
            "evidence_id": summary["evidence_id"], "network_access": False}
