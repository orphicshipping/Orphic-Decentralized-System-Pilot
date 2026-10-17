"""Link source -> existing Session -> native observations -> offline reopen.

The acquisition envelope is a recording declaration, not a synthetic substitute
for physical sample time. Actual device ticks and projected times remain inside
the retained Link payload and its native observation stream.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import platform
import tempfile
from threading import Lock

from . import link_contract as c, link_gateway as gateway, link_protocol, link_store
from .adapters.protocol import InstrumentManifest
from .control_contracts import save_new
from .core.identities import evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .hardware_workflow import read_bytes
from .operations.registry import Operation, OperationRegistry
from .operations.schemas import register_payload_validator

INSTRUMENT = "link-capture.v1"
CHANNEL = "link_declaration"
FRAME = "link-declaration-only"
_REGISTERED = False
_LOCK = Lock()
MAX_WORKSPACE_BYTES = 32 * 1024 * 1024


def runtime_identity():
    from . import control_contracts, control_plane
    from .core import identities, covariance
    modules = (c, gateway, link_protocol, link_store, control_contracts, control_plane, identities, covariance,
               __import__(__name__, fromlist=["*"]))
    raw = b"\0".join(Path(m.__file__).name.encode() + b"\0" + Path(m.__file__).read_bytes().replace(b"\r\n", b"\n") for m in modules)
    return {"provider": "ciw.link", "version": "1", "code_sha256": sha256(raw).hexdigest(),
            "source_normalization": "utf8_lf", "python": platform.python_version(),
            "scope": "retained_link_decode_and_native_projection_only"}


def register_schema():
    global _REGISTERED
    with _LOCK:
        if not _REGISTERED:
            register_payload_validator(c.OPERATION, validate_payload, role="backend")
            _REGISTERED = True


def make_source(request, input_ref):
    # Keep malformed but bounded declarations representable as refused Sessions.
    c.bounded(request)
    if type(request) is not dict:
        raise ValueError("Link request must be a JSON object")
    c.content_ref(input_ref)
    manifest = InstrumentManifest(instrument_id=INSTRUMENT, role="retained_instrument_capture",
        units={CHANNEL: "1"}, frames=(FRAME,), sampling={"kind": "one_declaration", "time_semantics": "not_device_acquisition_time"},
        supported_operations=(c.OPERATION,), calibration_requirements={"scope": "physical_calibration_not_established"})
    source = {"run_schema": "run.v1", "run_id": "run-link-" + c.content_identity({"request": request, "input_ref": input_ref})[7:23],
              "instrument": INSTRUMENT, "metadata": {"duration_s": 1.0, "sample_count": 1, "sample_rate_hz": None,
              "coordinate_frame": FRAME, "manifest": manifest.to_dict(), "link_capture": deepcopy(request), "input_bytes_ref": input_ref,
              "provenance": {"source": "retained_link_declaration", "generator": "ciw.link_workflow", "generator_version": 1}},
              "time_s": [0.0], "channels": {CHANNEL: {"unit": "1", "values": [1.0]}}, "render": {}}
    source["evidence_id"] = evidence_id(source)
    return source


def source_request(source):
    validate_run_structure(source); validate_evidence_identity(source)
    request = source["metadata"]["link_capture"]
    expected = make_source(request, source["metadata"]["input_bytes_ref"])
    if c.canonical_json(source) != c.canonical_json(expected):
        raise ValueError("Link source differs from the complete retained declaration")
    return deepcopy(request)


def _execute(source, parameters):
    c.keys(parameters, set())
    result = gateway.validate_capture(source_request(source))
    # Offline reprocessing never renews historical HMAC verification.
    result["scope"] = "record_integrity_and_explicit_observation_projection"
    result["limits"] = dict(c.LIMITS)
    return result


def validate_payload(operation_id, data, source, parameters, selection):
    if operation_id != c.OPERATION or selection["channel"] != CHANNEL or selection["interval_s"] != [0.0, 1.0]:
        raise ValueError("Link operation requires the complete declaration selection")
    if c.canonical_json(data) != c.canonical_json(_execute(source, parameters)):
        raise ValueError("Link retained output differs from original wire evidence")


def registry():
    register_schema()
    result = OperationRegistry()
    result.register(Operation(c.OPERATION, "backend", _execute, runtime_identity))
    return result


def run(request, output_dir, *, raw=None):
    from .session import Session
    if raw is None:
        raw = (json.dumps(request, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if c.canonical_json(c.loads(raw)) != c.canonical_json(request):
        raise ValueError("Capture differs from the original supplied request bytes")
    source = make_source(request, c.bytes_ref(raw))
    output_dir = link_store.safe_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    # Original formatting/bytes are retained rather than rewritten.
    with (output_dir / "capture.json").open("xb") as stream:
        stream.write(raw)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    session = Session(source, output_dir, operations=registry())
    session.handle({"protocol_version": 1, "request_id": "link-project", "type": "operation.execute",
                    "payload": {"operation_id": c.OPERATION, "parameters": {}}})
    session.save_workspace(output_dir / "workspace.json")
    return inspect(output_dir)


def _read(directory):
    from .session import Session, loads_json
    register_schema()
    directory = link_store.safe_path(directory)
    raw = read_bytes(directory / "capture.json", limit=c.MAX_CAPTURE_BYTES)
    request = c.loads(raw)
    workspace = loads_json(read_bytes(directory / "workspace.json", limit=MAX_WORKSPACE_BYTES).decode("utf-8"))
    with tempfile.TemporaryDirectory(prefix="net-link-inspect-") as temp:
        path = Path(temp)
        save_new(path / "workspace.json", workspace)
        session = Session.from_workspace(path / "workspace.json", path / "reopened")
    if c.canonical_json(session.run) != c.canonical_json(make_source(request, c.bytes_ref(raw))):
        raise ValueError("Retained capture bytes disagree with Session evidence")
    executions = list(session.executions.values())
    if len(executions) != 1 or executions[0]["operation_id"] != c.OPERATION:
        raise ValueError("Link bundle requires exactly one original projection occurrence")
    execution = executions[0]
    result = None
    if execution["status"] == "completed":
        if len(session.results) != 1:
            raise ValueError("Missing/extra Link projection result")
        result = next(iter(session.results.values()))
    elif session.results:
        raise ValueError("Refused Link capture cannot contain successful results")
    for runtime in [execution["runtime"]] + ([] if result is None else [result["runtime"]]):
        if runtime is not None:
            c.keys(runtime, {"provider", "version", "code_sha256", "source_normalization", "python", "scope"})
            if (runtime["provider"] != "ciw.link" or runtime["version"] != "1" or runtime["source_normalization"] != "utf8_lf"
                    or runtime["scope"] != "retained_link_decode_and_native_projection_only"):
                raise ValueError("Unexpected retained Link runtime scope")
            c.content_ref("sha256:" + runtime["code_sha256"]); c.text(runtime["python"])
    return request, raw, session, execution, result


def inspect(directory):
    request, _, session, execution, result = _read(directory)
    data = None if result is None else deepcopy(result["data"])
    return {"schema": "ciw.link-inspection.v1", "status": "REFUSED" if result is None else "RETAINED",
            "evidence_id": session.run["evidence_id"], "input_bytes_ref": session.run["metadata"]["input_bytes_ref"],
            "operation_id": c.OPERATION, "execution_id": execution["execution_id"],
            "result_id": None if result is None else result["result_id"], "verification_status": "not_verified",
            "manifest": deepcopy(request.get("manifest")), "data": data, "network_access": False,
            "refusal": deepcopy(execution.get("refusal")), "limits": dict(c.LIMITS)}


def replay(directory, output_dir):
    request, raw, _, _, _ = _read(directory)
    return run(request, output_dir, raw=raw)


def export_native(directory, output_dir):
    summary = inspect(directory)
    if summary["data"] is None:
        raise ValueError("No native observations for a refused capture")
    output_dir = link_store.safe_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    projection = summary["data"]["projection"]
    save_new(output_dir / "observations.json", projection["native_stream"])
    if projection["joint_covariance"] is not None:
        save_new(output_dir / "covariance.json", projection["joint_covariance"])
    save_new(output_dir / "mapping.json", {"source_evidence_id": summary["evidence_id"], "input_bytes_ref": summary["input_bytes_ref"],
        "source_result_id": summary["result_id"], "mappings": projection["mappings"], "withheld": projection["withheld"],
        "native_observation_ref": projection["native_stream"]["record_digest"], "limits": dict(c.LIMITS)})
    return {"status": "EXPORTED", "output_dir": str(output_dir), "source_evidence_id": summary["evidence_id"],
            "scope": "existing_native_observation_stream_and_covariance_not_new_data_family"}
