"""Retained Link observations -> existing comparison -> Session -> offline review.

No device access, calibration, clock fit, provider selection from data or actuation.
Source, result and verification authority stay with their existing contracts.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import platform
import tempfile
from threading import Lock

from . import control_checks as checks, link_contract as link, link_workflow
from .adapters.protocol import InstrumentManifest
from .control_contracts import bytes_ref, keys, number, text, save_new
from .control_plane import CapabilityRegistry
from .core.identities import evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .hardware_workflow import read_bytes
from .link_store import safe_path
from .operations.registry import Operation
from .operations.runner import validate_execution
from .operations.schemas import register_payload_validator

OPERATION = "device.compare-retained.v1"
INSTRUMENT = "device-retained-review.v1"
CHANNEL = "review_declaration"
FRAME = "review-declaration-not-acquisition"
MAX_INPUT = 2 * 1024 * 1024
MAX_WORKSPACE = 32 * 1024 * 1024
AUTHORITY = {
    "physical_validation": "not_established",
    "calibration": "not_performed",
    "clock_qualification": "not_performed",
    "statistical_confidence": "not_assessed",
    "verification_id": None,
    "verification_status": "not_verified",
    "state_admission": "not_performed",
    "equipment_control": False,
}
_LOCK = Lock()
_REGISTERED = False


def _same(a, b):
    return link.canonical_json(a) == link.canonical_json(b)


def _raw(value, supplied=None):
    raw = ((json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
           if supplied is None else supplied)
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_INPUT:
        raise ValueError("Review input exceeds its byte budget")
    if not _same(link.loads(raw, MAX_INPUT), value):
        raise ValueError("Review input differs from original bytes")
    return raw


def _request(value):
    keys(value, {"channel_id", "atol", "rtol", "purpose"})
    text(value["channel_id"]); text(value["purpose"])
    if number(value["atol"]) < 0 or number(value["rtol"]) < 0:
        raise ValueError("Numerical tolerances must be nonnegative")
    return deepcopy(value)


def _reference(value):
    if type(value) is not dict or value.get("schema") != "ciw.observation-stream.v1":
        raise ValueError("Reference must use the existing native observation stream")
    if type(value.get("observations")) is not list or len(value["observations"]) > link.MAX_RECORDS:
        raise ValueError("Reference exceeds the bounded Link review sample budget")
    checks.inspect_record(value)
    if any(v["provenance"]["semantics"] != "reference" for v in value["observations"]):
        raise ValueError("Right-hand observations must explicitly declare reference semantics")
    return deepcopy(value)


def _manifest():
    return InstrumentManifest(instrument_id=INSTRUMENT, role="retained_comparison", units={CHANNEL: "1"}, frames=(FRAME,),
        sampling={"kind": "one_declaration", "time_semantics": "not_device_acquisition_time"},
        supported_operations=(OPERATION,), calibration_requirements={"scope": "no_physical_qualification"})


def make_source(parent, reference, request, bindings):
    """A linked review declaration; the original Link run and occurrences survive."""
    keys(bindings, {"capture", "link_workspace", "reference", "request"})
    for ref in bindings.values():
        link.content_ref(ref)
    context = {"parent": deepcopy(parent), "reference": _reference(reference),
               "request": _request(request), "input_bytes": deepcopy(bindings)}
    run = {"run_schema": "run.v1", "run_id": "run-device-review-" + link.content_identity(context)[7:23],
        "instrument": INSTRUMENT, "metadata": {
            "duration_s": 1.0, "sample_count": 1, "sample_rate_hz": None,
            "coordinate_frame": FRAME, "manifest": _manifest().to_dict(), "device_review": context,
            "provenance": {"source": "retained_link_comparison_declaration",
                           "generator": "ciw.device_review", "generator_version": 1}},
        "time_s": [0.0], "channels": {CHANNEL: {"unit": "1", "values": [1.0]}}, "render": {}}
    run["evidence_id"] = evidence_id(run)
    # The existing control/data bounds apply before provider execution or retention.
    link.bounded(run, MAX_WORKSPACE)
    return run


def _context(source):
    from .session import _validate_saved_result, _recording_file
    validate_run_structure(source); validate_evidence_identity(source)
    info = source["metadata"]["device_review"]
    keys(info, {"parent", "reference", "request", "input_bytes"})
    p = info["parent"]
    keys(p, {"source", "execution", "result"})
    link_workflow.register_schema()
    capture = link_workflow.source_request(p["source"])
    if p["result"] is None or p["execution"]["status"] != "completed":
        raise ValueError("Comparison requires a retained successful Link projection")
    if p["execution"]["operation_id"] != link.OPERATION or p["result"]["operation_id"] != link.OPERATION:
        raise ValueError("Review parent is not a Link projection")
    _validate_saved_result(p["result"], p["source"], 0, _recording_file(p["source"]))
    validate_execution(p["execution"], p["source"], 0, {p["result"]["result_id"]: p["result"]})
    # Enforce type-exact equality as well as the parent's ordinary envelope checks.
    for key in ("execution_id", "operation_id", "runtime", "parameters", "created_at",
                "selection_revision", "channel", "interval_s"):
        if not _same(p["execution"][key], p["result"][key]):
            raise ValueError("Parent execution/result content differs")
    if info["input_bytes"]["capture"] != p["source"]["metadata"]["input_bytes_ref"]:
        raise ValueError("Original capture byte binding differs")
    expected = make_source(p, info["reference"], info["request"], info["input_bytes"])
    if not _same(source, expected):
        raise ValueError("Review source differs from its complete declaration")
    channel = info["request"]["channel_id"]
    link.channel_for(capture["manifest"], channel)
    data = p["result"]["data"]
    selected = [v["measurement_id"] for v in data["observations"] if v["channel_id"] == channel]
    native_refs = [m["observation_ref"] for m in data["projection"]["mappings"] if m["measurement_id"] in selected]
    native = {v["record_digest"]: v for v in data["projection"]["native_stream"]["observations"]}
    left = [deepcopy(native[ref]) for ref in native_refs]
    coverage = {"measurement_ids": selected, "observation_refs": native_refs,
        "withheld": [v for v in data["projection"]["withheld"] if v["measurement_id"] in selected],
        "capture_gaps": deepcopy(data["projection"]["gaps"]),
        "clock_relation_to_host": data["projection"]["clock_relation_to_host"],
        "calibrations": [v for v in data["calibrations"] if v["measurement_id"] in selected],
        "joint_covariance_ref": None if data["projection"]["joint_covariance"] is None
                                 else data["projection"]["joint_covariance"]["covariance_id"],
        "source_origin": sorted({v["origin"] for v in data["observations"]}),
        "scope": "selected_projected_samples_only; gaps_and_withheld_samples_not_qualified"}
    return info, left, coverage


def _fields(info, coverage):
    p = info["parent"]
    return {"schema": "ciw.device-review-result.v1", "source_evidence_id": p["source"]["evidence_id"],
            "source_execution_id": p["execution"]["execution_id"], "source_result_id": p["result"]["result_id"],
            "input_bytes": deepcopy(info["input_bytes"]), "request": deepcopy(info["request"]),
            "coverage": deepcopy(coverage), "authority": deepcopy(AUTHORITY)}


def _execute(source, parameters):
    keys(parameters, {"channel", "interval_s"})
    if not _same(parameters, {"channel": CHANNEL, "interval_s": [0.0, 1.0]}):
        raise ValueError("Review parameters must retain the complete declaration selection")
    info, left, coverage = _context(source)
    comparison = checks.compare(left, info["reference"]["observations"],
                                atol=info["request"]["atol"], rtol=info["request"]["rtol"])
    return {**_fields(info, coverage), "comparison": comparison}


def _validate_outcome_types(comparison):
    """Keep native result types exact without changing comparison arithmetic.

    Python equality admits False == 0 and 3.0 == 3. Those are not the same
    retained JSON types. The existing checker still owns values and decisions.
    """
    outcome = comparison["outcome"]
    keys(outcome, {"status", "sample_count", "component_count", "metrics", "unit", "reason"})
    for key in ("sample_count", "component_count"):
        if type(outcome[key]) is not int or outcome[key] < 0:
            raise ValueError("Retained comparison counts must be nonnegative integers")
    metrics = outcome["metrics"]
    if metrics is not None:
        keys(metrics, {"rmse", "max_abs_error", "max_tolerance_excess"})
        for value in metrics.values():
            if type(value) is not float:
                raise ValueError("Retained comparison metrics must retain their native float type")
            number(value)


def validate_payload(operation, data, source, parameters, selection):
    """Offline semantic consistency only; never invokes the executable operation."""
    keys(parameters, {"channel", "interval_s"})
    if not _same(parameters, {"channel": CHANNEL, "interval_s": [0.0, 1.0]}):
        raise ValueError("Review parameters must retain the complete declaration selection")
    if operation != OPERATION or not _same({k: selection[k] for k in ("channel", "interval_s")}, {"channel": CHANNEL, "interval_s": [0.0, 1.0]}):
        raise ValueError("Review requires its complete declaration selection")
    info, left, coverage = _context(source)
    expected = _fields(info, coverage)
    keys(data, set(expected) | {"comparison"})
    if not _same({k: data[k] for k in expected}, expected):
        raise ValueError("Retained review metadata, authority or source binding differs")
    comparison = data["comparison"]
    checks.validate_comparison(comparison)
    _validate_outcome_types(comparison)
    if (not _same(comparison["left"], left)
            or not _same(comparison["right"], info["reference"]["observations"])
            or not _same(comparison["policy"], {k: info["request"][k] for k in ("atol", "rtol")})):
        raise ValueError("Comparison differs from the retained observations or policy")


def register_schema():
    global _REGISTERED
    with _LOCK:
        if not _REGISTERED:
            register_payload_validator(OPERATION, validate_payload, role="analysis")
            _REGISTERED = True


def runtime_identity():
    from . import control_contracts, control_plane
    modules = (__import__(__name__, fromlist=["*"]), checks, control_contracts, control_plane)
    raw = b"\0".join(Path(m.__file__).name.encode() + b"\0" + Path(m.__file__).read_bytes().replace(b"\r\n", b"\n") for m in modules)
    return {"provider": "ciw.device-review", "version": "1", "code_sha256": sha256(raw).hexdigest(),
            "link": link_workflow.runtime_identity(), "python": platform.python_version(),
            "scope": "existing_numerical_comparison_only"}


def _validate_runtime(value, *, absent=False):
    if value is None and absent:
        return
    keys(value, {"provider", "version", "code_sha256", "link", "python", "scope"})
    if (value["provider"] != "ciw.device-review" or value["version"] != "1"
            or value["scope"] != "existing_numerical_comparison_only"):
        raise ValueError("Unsupported retained comparison runtime scope")
    link.content_ref("sha256:" + value["code_sha256"]); text(value["python"])
    nested = value["link"]
    keys(nested, {"provider", "version", "code_sha256", "source_normalization", "python", "scope"})
    if (nested["provider"] != "ciw.link" or nested["version"] != "1"
            or nested["source_normalization"] != "utf8_lf"
            or nested["scope"] != "retained_link_decode_and_native_projection_only"):
        raise ValueError("Unsupported retained projection runtime scope")
    link.content_ref("sha256:" + nested["code_sha256"]); text(nested["python"])


def capability_registry(*, bind=False):
    register_schema()
    registry = CapabilityRegistry()
    registry.advertise(_manifest(), runtime=runtime_identity(), capabilities={OPERATION: ["device.compare-retained"]})
    if bind:
        registry.bind(Operation(OPERATION, "analysis", _execute, runtime_identity))
    return registry


def _link_snapshot(directory):
    directory = safe_path(directory)
    raw = read_bytes(directory / "capture.json", limit=MAX_INPUT)
    workspace = read_bytes(directory / "workspace.json", limit=MAX_WORKSPACE)
    # Work from a fixed snapshot; later external changes cannot substitute inputs.
    with tempfile.TemporaryDirectory(prefix="net-device-source-") as tmp:
        tmp = Path(tmp)
        (tmp / "capture.json").write_bytes(raw)
        (tmp / "workspace.json").write_bytes(workspace)
        _, _, session, execution, result = link_workflow._read(tmp)
        parent = {"source": deepcopy(session.run), "execution": deepcopy(execution), "result": deepcopy(result)}
    return parent, raw, workspace


def _put(path, raw):
    with safe_path(path).open("xb") as stream:
        stream.write(raw)
        stream.flush()
        import os
        os.fsync(stream.fileno())


def run(link_directory, reference, request, output_dir, *, reference_raw=None, request_raw=None, _expected_bindings=None):
    from .session import Session
    parent, capture_raw, link_raw = _link_snapshot(link_directory)
    reference_raw, request_raw = _raw(reference, reference_raw), _raw(request, request_raw)
    bindings = dict(capture=bytes_ref(capture_raw), link_workspace=bytes_ref(link_raw),
                    reference=bytes_ref(reference_raw), request=bytes_ref(request_raw))
    if _expected_bindings is not None and not _same(bindings, _expected_bindings):
        raise ValueError("Retained input bytes changed during explicit replay")
    source = make_source(parent, reference, request, bindings)
    # Invalid parent/channel is refused before creating the destination. A legal
    # invocation that subsequently fails still uses normal retained Session refusal.
    _context(source)
    output = safe_path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    (output / "link").mkdir()
    for path, raw in (("link/capture.json", capture_raw), ("link/workspace.json", link_raw),
                      ("reference.json", reference_raw), ("request.json", request_raw)):
        _put(output / path, raw)
    registry = capability_registry(bind=True)
    session = Session(source, output, operations=registry.operations)
    response = session.handle({"protocol_version": 1, "request_id": "device-review", "type": "operation.execute",
                               "payload": {"operation_id": OPERATION, "parameters": {}}})
    if response["type"] == "error":
        raise ValueError("Review operation was not accepted: " + str(response["payload"]))
    session.save_workspace(output / "workspace.json")
    return inspect(output)


def _read(directory, *, expected_snapshot_ref=None):
    from .session import Session
    register_schema()
    directory = safe_path(directory)
    if expected_snapshot_ref is not None:
        link.content_ref(expected_snapshot_ref)
    workspace_raw = read_bytes(directory / "workspace.json", limit=MAX_WORKSPACE)
    if expected_snapshot_ref is not None and bytes_ref(workspace_raw) != expected_snapshot_ref:
        raise ValueError("Retained review snapshot does not match the operator's content anchor")
    parent, capture_raw, link_raw = _link_snapshot(directory / "link")
    ref_raw = read_bytes(directory / "reference.json", limit=MAX_INPUT)
    req_raw = read_bytes(directory / "request.json", limit=MAX_INPUT)
    ref, req = link.loads(ref_raw, MAX_INPUT), link.loads(req_raw, MAX_INPUT)
    bindings = dict(capture=bytes_ref(capture_raw), link_workspace=bytes_ref(link_raw),
                    reference=bytes_ref(ref_raw), request=bytes_ref(req_raw))
    expected = make_source(parent, ref, req, bindings)
    with tempfile.TemporaryDirectory(prefix="net-device-review-") as tmp:
        tmp = Path(tmp); (tmp / "workspace.json").write_bytes(workspace_raw)
        session = Session.from_workspace(tmp / "workspace.json", tmp / "reopened")
    if not _same(session.run, expected):
        raise ValueError("Review workspace differs from the exact retained input bytes")
    executions = list(session.executions.values())
    if len(executions) != 1 or executions[0]["operation_id"] != OPERATION:
        raise ValueError("Require exactly one retained device comparison occurrence")
    execution = executions[0]
    _validate_runtime(execution["runtime"], absent=execution["status"] == "refused")
    result = session.results.get(execution.get("result_id"))
    if len(session.results) != (1 if execution["status"] == "completed" else 0):
        raise ValueError("Review result count contradicts its execution")
    if result:
        for key in ("runtime", "parameters", "selection_revision", "channel", "interval_s"):
            if not _same(execution[key], result[key]):
                raise ValueError("Retained execution/result content differs")
    return session, execution, result, bindings, workspace_raw


def _inspection(session, execution, result, bindings, workspace_raw, *, expected_snapshot_ref=None):
    return {"schema": "ciw.device-review-inspection.v1", "status": "REFUSED" if result is None else "RETAINED",
        "snapshot_ref": bytes_ref(workspace_raw), "review_evidence_id": session.run["evidence_id"],
        "operation_id": OPERATION, "execution_id": execution["execution_id"],
        "result_id": None if result is None else result["result_id"], "input_bytes": bindings,
        "data": None if result is None else deepcopy(result["data"]), "refusal": deepcopy(execution.get("refusal")),
        "authority": deepcopy(AUTHORITY), "network_access": False,
        "content_anchor_check": {"status": "NOT_REQUESTED" if expected_snapshot_ref is None else "MATCHED",
                                 "expected": expected_snapshot_ref, "authenticates_origin": False}}


def inspect(directory, *, expected_snapshot_ref=None):
    """Inspect retained records, optionally pinned to an externally supplied hash."""
    loaded = _read(directory, expected_snapshot_ref=expected_snapshot_ref)
    return _inspection(*loaded, expected_snapshot_ref=expected_snapshot_ref)


def _review_snapshot(directory, *, expected_snapshot_ref=None):
    """Read one validated snapshot; source visibility never depends on success."""
    loaded = _read(directory, expected_snapshot_ref=expected_snapshot_ref)
    info, observations, _ = _context(loaded[0].run)
    return (_inspection(*loaded, expected_snapshot_ref=expected_snapshot_ref),
            observations, deepcopy(info["reference"]["observations"]))


def replay(directory, output_dir, *, expected_snapshot_ref=None):
    _, _, _, bindings, _ = _read(directory, expected_snapshot_ref=expected_snapshot_ref)
    directory = safe_path(directory)
    reference_raw = read_bytes(directory / "reference.json", limit=MAX_INPUT)
    request_raw = read_bytes(directory / "request.json", limit=MAX_INPUT)
    return run(directory / "link", link.loads(reference_raw, MAX_INPUT), link.loads(request_raw, MAX_INPUT), output_dir,
               reference_raw=reference_raw, request_raw=request_raw, _expected_bindings=bindings)
