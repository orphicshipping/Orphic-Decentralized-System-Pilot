"""Bounded scalar investigations over retained Link bytes, through the existing DAG.

No serial port, driver loading, fitted calibration, physical acceptance or new
measurement store. Affine uncertainty is conditional first-order propagation of
an explicitly supplied full joint matrix, including shared coefficient errors.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha256
import math
from pathlib import Path
import platform
from threading import Lock

from . import link_contract as c, link_gateway as gateway, link_workflow
from .adapters.protocol import InstrumentManifest
from .control_contracts import observation, record
from .control_plane import CapabilityRegistry, Port, experiment
from .core.covariance import create_covariance_artifact, validate_covariance_artifact
from .core.identities import evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .operations.registry import Operation
from .operations.schemas import register_payload_validator, register_dependency_fields

SCHEMA = "ciw.link-scalar-study.v1"
OPS = ("link.decode-scalar.v1", "link.apply-affine.v1", "link.mean-model.v1", "link.audit-scalar.v1")
MAX_BYTES = 512 * 1024
MAX_LINES = 32
_FIELDS = (None, "decoded", "calibrated", "candidate")
_REGISTERED = False
_LOCK = Lock()


def _equal(left, right):
    return c.canonical_json(left) == c.canonical_json(right)


def calibration_basis(request):
    cal = request["calibration"]
    return c.content_identity({"capture_ref": c.content_identity(request["capture"]),
        "channel_id": request["channel_id"],
        "calibration": {k: v for k, v in cal.items() if k != "joint_covariance"}})


def validate_request(value):
    c.bounded(value, MAX_BYTES)
    c.keys(value, {"schema", "capture", "channel_id", "calibration", "model"})
    if value["schema"] != SCHEMA:
        raise ValueError("Unsupported Link scalar study")
    captured = gateway.validate_capture(value["capture"])
    device = value["capture"]["manifest"]
    channel = c.channel_for(device, value["channel_id"])
    if channel["encoding"] != "opaque.v1" or device["clock"]["timestamp_event"] != "instrument_report_receipt":
        raise ValueError("Scalar stream study requires opaque report-receipt chunks")
    if len(device["channels"]) != 1 or any(x["channel_id"] != channel["id"] for x in captured["observations"]):
        raise ValueError("V1 scalar stream requires one explicitly selected channel")
    if value["capture"]["joint_covariance"] is not None:
        raise ValueError("Chunk covariance cannot be reinterpreted as decoded-line covariance")
    cal = value["calibration"]
    c.keys(cal, {"gain", "offset", "input_range", "declaration", "joint_covariance"})
    c.finite(cal["gain"]); c.finite(cal["offset"])
    if type(cal["input_range"]) is not list or len(cal["input_range"]) != 2:
        raise ValueError("Declare the affine input range")
    lo, hi = map(c.finite, cal["input_range"])
    if lo >= hi:
        raise ValueError("Affine input range must increase")
    if cal["declaration"] is not None:
        d = gateway.calibration_declaration(cal["declaration"])
        if (d["manifest_ref"] != c.content_identity(device) or d["channel_id"] != channel["id"]
                or d["unit"] != channel["unit"]):
            raise ValueError("Calibration declaration does not bind this instrument/channel/unit")
    model = value["model"]
    c.keys(model, {"reference_kind", "expected_mean", "residual_limit"})
    if model["reference_kind"] != "declared_exact":
        raise ValueError("V1 reference is a declared exact mathematical value, not a reference measurement")
    c.finite(model["expected_mean"])
    if c.finite(model["residual_limit"]) < 0:
        raise ValueError("Residual limit must be nonnegative")
    return deepcopy(value)


def decode(request):
    request = validate_request(request)
    captured = gateway.validate_capture(request["capture"])
    items = captured["observations"]
    if not items or len(items) > 128 or [x["sequence"] for x in items] != list(range(len(items))):
        raise ValueError("Require an uninterrupted chunk sequence beginning at zero")
    if any(x["stage"] != "raw" for x in items):
        raise ValueError("Decoder consumes only original raw chunks")
    if any(x["context"] != items[0]["context"] for x in items):
        raise ValueError("Context changed within the finite scalar stream")
    if any(x["procedure_ref"] != items[0]["procedure_ref"] for x in items):
        raise ValueError("Procedure changed within the scalar stream")
    if any(x["event"] in {"overflow", "clock_reset"} for x in captured["events"]):
        raise ValueError("Overflow or clock reset prevents a complete scalar-study projection")
    device = request["capture"]["manifest"]
    channel = c.channel_for(device, request["channel_id"])
    raw, spans = bytearray(), []
    for item in items:
        chunk = c.unpack(item["raw"], c.MAX_RAW)
        if not chunk:
            raise ValueError("Empty raw acquisition chunk")
        start = len(raw); raw.extend(chunk)
        spans.append((start, len(raw), item))
    if len(raw) > 8192 or not raw.endswith(b"\r\n"):
        raise ValueError("Oversized stream or incomplete final CRLF record; original bytes retained")
    lines = bytes(raw).splitlines(keepends=True)
    if not 1 <= len(lines) <= MAX_LINES or any(not line.endswith(b"\r\n") or len(line) > 128 for line in lines):
        raise ValueError("Require 1..32 bounded, complete CRLF scalar lines")
    rows, start = [], 0
    for index, line in enumerate(lines):
        value = c.scalar(line, channel["unit"])
        stop = start + len(line)
        contributors = [(a, b, x) for a, b, x in spans if a < stop and b > start]
        last = contributors[-1][2]
        stamp = last["device_tick"]
        # Acquisition time is not inferred from a host receipt or a calendar date.
        acquired = {x["acquired_at"] for _, _, x in contributors}
        acquired_at = next(iter(acquired)) if len(acquired) == 1 else None
        identity = c.content_identity({"capture": c.content_identity(request["capture"]),
            "channel": channel["id"], "start": start, "stop": stop})
        rows.append({"id": identity, "value": value, "unit": channel["unit"],
            "byte_range": [start, stop], "raw": c.packed(line, 128),
            "chunks": [{"measurement_id": x["measurement_id"], "raw_ref": x["raw"]["sha256"],
                        "range": [max(start, a)-a, min(stop, b)-a]} for a,b,x in contributors],
            "receipt_tick": stamp, "acquired_at": acquired_at})
        start = stop
    return {"schema": "ciw.link-scalar-decoded.v1", "capture_ref": c.content_identity(request["capture"]),
        "decoder": "notation-ascii-scalar-crlf.v1", "stream": c.packed(bytes(raw), 8192),
        "rows": rows, "channel": channel, "context": items[0]["context"], "procedure_ref": items[0]["procedure_ref"],
        "origin": items[0]["origin"], "clock": device["clock"], "boot_id": request["capture"]["boot_id"],
        "timing": "last_contributing_chunk_receipt_not_sensor_acquisition", "limits": dict(c.LIMITS)}


def _float(value):
    result = float(value)
    if not math.isfinite(result) or (result == 0 and value != 0):
        raise ValueError("Calculation overflows or loses a nonzero value to underflow")
    return result


def _d(value):
    return Decimal(value) if type(value) is int else Decimal.from_float(value)


def _transport(matrix, jacobian):
    # Inputs are bounded binary64/100-digit integers. 4096 decimal digits retain
    # three-factor products and cancellation across that finite exponent range.
    # Conversion to binary64 occurs only at the output, never between terms.
    with localcontext() as ctx:
        ctx.prec = 4096
        return [[_float(sum((_d(a)*_d(matrix[i][j])*_d(b)
            for i,a in enumerate(row) for j,b in enumerate(col)), Decimal(0)))
            for col in jacobian] for row in jacobian]


def affine(request, decoded):
    if not _equal(decoded, decode(request)):
        raise ValueError("Decoded prerequisite differs from original capture")
    cal = request["calibration"]
    rows = decoded["rows"]; x = [r["value"] for r in rows]; n = len(x)
    if any(not cal["input_range"][0] <= v <= cal["input_range"][1] for v in x):
        raise ValueError("Reading outside declared calibration range")
    a,b = cal["gain"], cal["offset"]
    with localcontext() as ctx:
        ctx.prec = 4096
        y = [_float(_d(a)*_d(v)+_d(b)) for v in x]
    ids = [r["id"]+":corrected" for r in rows]
    covariance = cal["joint_covariance"]
    output_cov = None
    if covariance is not None:
        order = [r["id"]+":input" for r in rows]+["gain", "offset"]
        validate_covariance_artifact(covariance, expected_quantity_ids=order,
            expected_units=[decoded["channel"]["unit"]]*n+["1", decoded["channel"]["unit"]],
            expected_frame=decoded["channel"]["frame"])
        if (not _equal(covariance["reference_values"], x+[a,b])
                or covariance["basis"] != {"kind": "parameter", "id": calibration_basis(request)}
                or covariance["provenance"]["source_evidence_ids"] != [decoded["capture_ref"]]):
            raise ValueError("Joint covariance must bind the exact readings, coefficients and capture")
        jac = [[a if i == j else 0 for j in range(n)]+[v,1] for i,v in enumerate(x)]
        matrix = _transport(covariance["matrix"], jac)
        output_cov = create_covariance_artifact(matrix=matrix, quantity_ids=ids,
            units=[decoded["channel"]["unit"]]*n, frame=decoded["channel"]["frame"], reference_values=y,
            method="first_order_joint_affine_jacobian", basis={"kind":"calibrated_observation", "id":calibration_basis(request)},
            provenance={"provider":"ciw.link_study", "source_evidence_ids":[decoded["capture_ref"]],
                        "source_covariance_ids":[covariance["covariance_id"]]},
            assumptions=["conditional first-order approximation", "input/coefficient correlations supplied, not inferred",
                         "model discrepancy and timing uncertainty not included unless represented by supplied covariance"])
    applicability = []
    declaration = cal["declaration"]
    for row in rows:
        status = "MISSING_DECLARATION"
        if declaration is not None:
            from .calibration_status import _timestamp
            if row["acquired_at"] is None:
                status = "ACQUISITION_TIME_UNKNOWN"
            else:
                at = _timestamp(row["acquired_at"], "acquired_at", strict=True)
                lo = _timestamp(declaration["valid_from"], "valid_from", strict=True)
                hi = _timestamp(declaration["valid_until"], "valid_until", strict=True)
                status = "DECLARED_APPLICABLE" if lo <= at < hi else "OUTSIDE_DECLARED_VALIDITY"
        applicability.append(status)
    return {"schema":"ciw.link-scalar-affine.v1", "values":y, "quantity_ids":ids,
        "calibration_ref":calibration_basis(request), "unit":decoded["channel"]["unit"],
        "covariance":output_cov, "uncertainty_status":"NOT_SUPPLIED" if output_cov is None else "CONDITIONAL_FIRST_ORDER",
        "applicability":applicability, "scope":"declared_correction_calculation_not_physical_calibration",
        "limits":dict(c.LIMITS)}


def mean_model(request, corrected):
    expected = affine(request, decode(request))
    if not _equal(corrected, expected):
        raise ValueError("Calibration prerequisite differs from retained declarations")
    y = corrected["values"]; n=len(y)
    with localcontext() as ctx:
        ctx.prec=4096
        mean = _float(sum(map(_d,y), Decimal(0))/n)
        residual = _float(_d(mean)-_d(request["model"]["expected_mean"]))
        cov = corrected["covariance"]
        variance = None if cov is None else _float(sum((_d(v) for row in cov["matrix"] for v in row),Decimal(0))/(n*n))
    return {"schema":"ciw.link-scalar-model.v1", "model":"arithmetic_mean_with_shared_covariance.v1",
        "count":n, "mean":mean, "residual":residual, "unit":corrected["unit"], "variance":variance,
        "standard_uncertainty":None if variance is None else math.sqrt(variance),
        "nominal_comparison":"WITHIN" if abs(residual)<=request["model"]["residual_limit"] else "OUTSIDE",
        "comparison_scope":"declared_exact_reference_and_nominal_absolute_limit_only",
        "inspection_qualification":"HELD", "limits":dict(c.LIMITS)}


def audit(request, candidate):
    """Independent Fraction arithmetic; does not reuse affine/mean numeric kernels."""
    decoded = decode(request)  # Decoder integrity is independently tested by byte fixtures.
    x = [Fraction(r["value"]) for r in decoded["rows"]]; n=len(x)
    cal=request["calibration"]; a,b=Fraction(cal["gain"]),Fraction(cal["offset"])
    corrected = candidate["parameters"]["calibrated"]["data"]
    model = candidate["data"]
    checks=[]
    def check(name, actual, expected):
        expected = _float(expected)
        if type(actual) not in (int,float) or not math.isfinite(actual):
            ok=False
        elif expected == 0:
            ok=actual == 0
        else:
            ok=abs(actual-expected)<=8*math.ulp(expected)
        checks.append({"name":name,"passed":ok})
    y=[_float(a*v+b) for v in x]
    for i,v in enumerate(y): check(f"affine.{i}",corrected["values"][i], Fraction(v))
    mean=_float(sum(map(Fraction,y))/n)
    check("mean",model["mean"],Fraction(mean))
    check("residual",model["residual"],Fraction(mean)-Fraction(request["model"]["expected_mean"]))
    cov=cal["joint_covariance"]
    if cov is not None:
        matrix=[[Fraction(v) for v in row] for row in cov["matrix"]]
        jac=[[a if i==j else Fraction(0) for j in range(n)]+[v,Fraction(1)] for i,v in enumerate(x)]
        out=[[_float(sum(jac[r][i]*matrix[i][j]*jac[s][j] for i in range(n+2) for j in range(n+2)))
              for s in range(n)] for r in range(n)]
        for r in range(n):
            for s in range(n): check(f"covariance.{r}.{s}",corrected["covariance"]["matrix"][r][s],Fraction(out[r][s]))
        check("mean_variance",model["variance"],sum(Fraction(v) for row in out for v in row)/(n*n))
    else:
        checks.append({"name":"uncertainty_remains_unknown", "passed":corrected["covariance"] is None and model["variance"] is None})
    return {"schema":"ciw.link-scalar-audit.v1", "status":"PASS" if all(x["passed"] for x in checks) else "FAIL",
        "candidate_result_id":candidate["result_id"], "checks":checks,
        "scope":"independent_affine_first_order_covariance_and_mean_arithmetic_only", "limits":dict(c.LIMITS)}


def make_source(request, input_ref):
    c.bounded(request,MAX_BYTES); c.content_ref(input_ref)
    source=link_workflow.make_source(request.get("capture",{}), input_ref)
    source["instrument"]="link-scalar-study.v1"
    source["metadata"]["manifest"]["instrument_id"]=source["instrument"]
    source["metadata"]["manifest"]["supported_operations"]=list(OPS)
    source["metadata"]["link_study"]=deepcopy(request)
    source["evidence_id"]=evidence_id(source)
    return source


def source_request(source):
    validate_run_structure(source); validate_evidence_identity(source)
    request=source["metadata"]["link_study"]
    if not _equal(source,make_source(request,source["metadata"]["input_bytes_ref"])):
        raise ValueError("Scalar source differs from retained request")
    return request


def execute(index, source, parameters):
    request=source_request(source)
    c.keys(parameters, set() if index==0 else {_FIELDS[index]})
    if index==0: return decode(request)
    prior=parameters[_FIELDS[index]]
    if prior["operation_id"] != OPS[index-1] or prior["evidence_id"] != source["evidence_id"]:
        raise ValueError("Wrong prerequisite operation/source")
    if index==1: return affine(request,prior["data"])
    if index==2: return mean_model(request,prior["data"])
    return audit(request,prior)


def _validator(index):
    def check(op,data,source,parameters,selection):
        if op!=OPS[index] or selection["channel"]!=link_workflow.CHANNEL or selection["interval_s"]!=[0.0,1.0]:
            raise ValueError("Use the complete retained scalar declaration")
        if not _equal(data,execute(index,source,parameters)):
            raise ValueError("Retained scalar result disagrees with its source and prerequisites")
    return check


def register():
    global _REGISTERED
    with _LOCK:
        if not _REGISTERED:
            for i,op in enumerate(OPS):
                register_payload_validator(op,_validator(i),role=("backend","calibration","state_estimator","verification")[i])
                if i: register_dependency_fields(op,(_FIELDS[i],))
            _REGISTERED=True


def runtime_identity():
    from . import control_plane, control_contracts, link_runtime, link_serial, link_protocol, link_store
    from .core import covariance
    from .operations import schemas,runner
    modules=(c,gateway,link_workflow,link_runtime,link_serial,link_protocol,link_store,control_plane,control_contracts,covariance,schemas,runner)
    raw=Path(__file__).read_bytes().replace(b"\r\n",b"\n")+b"".join(Path(m.__file__).read_bytes().replace(b"\r\n",b"\n") for m in modules)
    return {"provider":"ciw.link_study","version":"1","code_sha256":sha256(raw).hexdigest(),
        "python":platform.python_version(),"scope":"bounded_retained_scalar_investigation"}


def registry(*,bind=True):
    register()
    result=CapabilityRegistry()
    manifest=InstrumentManifest(instrument_id="link-scalar-study.v1",role="retained_instrument_capture",
        units={link_workflow.CHANNEL:"1"},frames=(link_workflow.FRAME,),sampling={"kind":"one_declaration"},
        supported_operations=OPS,calibration_requirements={"scope":"no_physical_qualification"})
    result.advertise(manifest,runtime=runtime_identity(),capabilities={op:["link_scalar_study"] for op in OPS},
        inputs={OPS[i]:{_FIELDS[i]:Port("ciw.operation-result.v1").to_dict()} for i in range(1,4)})
    if bind:
        for i,op in enumerate(OPS):
            result.bind(Operation(op,("backend","calibration","state_estimator","verification")[i],
                                  lambda source,params,i=i: execute(i,source,params),runtime_identity))
    return result


def graph():
    names=("decode","calibration","model","audit")
    return experiment("link-scalar-study",model_id="link-scalar-study.v1",nodes=[
        {"node_id":name,"operation_id":OPS[i],"parameters":{},"depends_on":[],
         "inputs":{} if i==0 else {_FIELDS[i]:{"node_id":names[i-1],"port":"result"}}}
        for i,name in enumerate(names)])


def native(request, decoded, corrected, *, execution_id, source_evidence):
    """Conservative native projection; duplicate/unknown receipt timing is withheld."""
    stream=[]; withheld=[]; mappings=[]; last=None
    ticks=[r["receipt_tick"] for r in decoded["rows"] if r["receipt_tick"] is not None]
    origin=min(ticks) if ticks else None
    hz=decoded["clock"]["tick_hz"]
    clock=c.content_identity({"manifest":c.content_identity(request["capture"]["manifest"]),
        "boot":decoded["boot_id"],"origin":origin,"clock":decoded["clock"]})
    for i,row in enumerate(decoded["rows"]):
        tick=row["receipt_tick"]
        if hz is None or tick is None or (last is not None and tick<=last):
            withheld.append({"id":row["id"],"reason":"unknown_or_indistinguishable_receipt_time"});continue
        time=(tick-origin)/hz
        if round(time*hz)!=(tick-origin):
            withheld.append({"id":row["id"],"reason":"native_time_precision_insufficient"});continue
        last=tick
        marginal = None
        if corrected["covariance"] is not None:
            joint = corrected["covariance"]
            marginal = create_covariance_artifact(matrix=[[joint["matrix"][i][i]]],
                quantity_ids=[decoded["channel"]["quantity"]], units=[corrected["unit"]],
                frame=decoded["channel"]["frame"], reference_values=[corrected["values"][i]],
                method="explicit_single_row_marginal", basis={"kind":"calibrated_observation","id":row["id"]},
                provenance={"provider":"ciw.link_study","source_evidence_ids":[source_evidence],
                            "source_covariance_ids":[joint["covariance_id"]]},
                assumptions=["Marginal only; inter-row correlations remain in the accompanying full covariance"])
        stream.append(observation(identity={"model_id":"link-scalar-study.v1","entity_id":decoded["channel"]["transducer_id"],"execution_id":execution_id},
            clock={"id":clock,"time_s":time}, frame=decoded["channel"]["frame"],quantity=decoded["channel"]["quantity"],
            value=corrected["values"][i],unit=corrected["unit"],uncertainty=marginal,
            provenance={"provider":"ciw.link_study","sources":[source_evidence,row["id"],corrected["calibration_ref"]],
                        "semantics":"simulated" if decoded["origin"]=="synthetic" else "estimated"}))
        mappings.append({"line_id":row["id"], "native_ref":stream[-1]["record_digest"],
                         "full_covariance_index":i,"full_covariance_quantity_id":corrected["quantity_ids"][i]})
    return {"stream":record("observation-stream",observations=stream),"withheld":withheld,"mappings":mappings,
        "scope":"corrected_estimate_at_message_receipt_not_sensor_acquisition",
        "covariance":corrected["covariance"],"rows":decoded["rows"],"limits":dict(c.LIMITS)}
