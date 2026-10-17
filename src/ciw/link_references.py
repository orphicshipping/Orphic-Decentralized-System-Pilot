"""Two pinned published-data adapters, executed through the existing NET Session.

This is reference-data review, not gauge acquisition, calibration fitting,
transfer of another instrument's calibration, model training or state admission.
The source Run has a single declaration coordinate, never invented sample time.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha1, sha256
from pathlib import Path
import platform
import re
from threading import Lock

from . import link_contract as c, link_study
from .adapters.protocol import InstrumentManifest
from .control_contracts import artifact, validate_artifact
from .control_plane import CapabilityRegistry, Port, experiment
from .core.covariance import create_covariance_artifact, validate_covariance_artifact
from .core.identities import evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .operations.registry import Operation
from .operations.schemas import register_payload_validator, register_dependency_fields

SCHEMA = "ciw.link-reference-request.v1"
OPS = ("link.review-reference.v1", "link.audit-reference.v1")
CHANNEL = "reference_declaration"
FRAME = "published-reference-declaration"
MAX_BYTES = 65536
_REGISTERED = False
_LOCK = Lock()
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
LIMITS = {"own_device_acquisition": "not_performed", "physical_qualification": "not_established",
          "calibration_transfer": "not_authorized", "training_admission": "not_requested",
          "state_admission": "not_performed", "network_access": False}
# Trusted installed profiles, not executable names loaded from persisted data.
PROFILES = {
    "nist-pontius": {
        "title": "NIST Pontius load-cell data", "institution": "NIST",
        "upstream_revision": "d837c4af1282e51667ed75a02433f5e5590f1c6d",
        "upstream_blob": "62e5292ae7798d61e18da8ef6cc8b9e41337b4e5",
        "source_url": "https://github.com/usnistgov/dataplot/blob/d837c4af1282e51667ed75a02433f5e5590f1c6d/lib/data/PONTIUS.DAT",
        "reference_url": "https://www.itl.nist.gov/div898/strd/lls/data/LINKS/v-Pontius.shtml",
        "sha256": "7854389f4b3058fc1d1f03250843d2c2ef00e09162939339769e9cb9ab1e8d8c",
        "byte_count": 1778, "rows": 40, "source_scope": "complete_upstream_file",
        "upstream_lines": [1, 65], "whole_file_hash_checked": True,
        "origin": "publisher_reported_observed_data", "quantity": ["load", "deflection"],
        "units": [None, None], "acquisition_time": None, "joint_uncertainty": "not_supplied",
        "rights": "NIST-PD", "rights_url": "https://spdx.org/licenses/NIST-PD.html",
        "split_group": "nist-pontius-load-cell-experiment",
        "intended_use": "published_polynomial_reference_arithmetic_and_data_mapping",
    },
    "ptb-mh44-first17": {
        "title": "MH44 hydrophone calibration, first 17 frequency rows",
        "institution": "PTB / University of Helsinki authors",
        "upstream_revision": "2de078bd09a58ed4063d2a6b477a94310a6e1039",
        "upstream_blob": "6acabcdb3281bed7b93563915e36b28f0e6e4801",
        "source_url": "https://github.com/Ma-Weber/Tutorial-Deconvolution/blob/2de078bd09a58ed4063d2a6b477a94310a6e1039/HydrophoneCalibrationData/MW_MH44ReIm.csv",
        "reference_url": "https://doi.org/10.5281/zenodo.10079801",
        "sha256": "598b7ac55a7835ed646a5b7defa82f057fac542a259c67a78498fbf62ca743f1",
        "byte_count": 3151, "rows": 17, "source_scope": "connector_lines_reserialized_with_LF_and_final_LF",
        "upstream_lines": [1, 18], "whole_file_hash_checked": False,
        "origin": "published_calibration_reference_subset", "quantity": ["frequency", "complex_sensitivity"],
        "units": ["MHz", "V/Pa"], "acquisition_time": None,
        "joint_uncertainty": "pointwise_real_imag_covariance_only_cross_frequency_unknown",
        "rights": "repository_GPL-3.0_notice_data_specific_grant_not_separately_established",
        "rights_url": "https://github.com/Ma-Weber/Tutorial-Deconvolution/blob/2de078bd09a58ed4063d2a6b477a94310a6e1039/LICENSE",
        "split_group": "weber-wilkens-eichstaedt-hydrophone-reference",
        "intended_use": "pointwise_covariance_and_magnitude_uncertainty_benchmark_not_deconvolution",
    },
}
# Factual numerical transcription from the NIST certified-values page, not a
# calibration certificate, fit performed here, or joint coefficient covariance.
PONTIUS_BETA = ("0.673565789473684E-03", "0.732059160401003E-06", "-0.316081871345029E-14")
PONTIUS_RSS = "0.155761768796992E-05"
PONTIUS_RSS_ATOL = "5e-21"  # half of the final printed decimal place
HYDRO_RELATIVE_ALLOWANCE = "0.000002"  # declared arithmetic/printed-table comparison, not sensor accuracy


def catalog():
    return {"schema": "ciw.link-reference-catalog.v1", "profiles": deepcopy(PROFILES), "limits": dict(LIMITS)}


def request(profile_id, raw):
    if type(profile_id) is not str or profile_id not in PROFILES:
        raise ValueError("Unsupported reference profile")
    profile = PROFILES[profile_id]
    source = artifact(raw, media_type="text/plain" if profile_id == "nist-pontius" else "text/csv",
        producer="ciw.link_references", experiment_id=profile["split_group"], metadata=_artifact_metadata(profile))
    value = {"schema": SCHEMA, "profile_id": profile_id, "payload": c.packed(raw, 8192), "source_artifact": source}
    validate_request(value)
    return value


def validate_request(value):
    c.bounded(value, MAX_BYTES)
    c.keys(value, {"schema", "profile_id", "payload", "source_artifact"})
    if value["schema"] != SCHEMA or type(value["profile_id"]) is not str or value["profile_id"] not in PROFILES:
        raise ValueError("Require an explicitly supported published-data profile")
    profile = PROFILES[value["profile_id"]]
    raw = c.unpack(value["payload"], 8192)
    if len(raw) != profile["byte_count"] or sha256(raw).hexdigest() != profile["sha256"]:
        raise ValueError("Input differs from the reviewed exact reference-file/selection bytes")
    if profile["whole_file_hash_checked"] and sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() != profile["upstream_blob"]:
        raise ValueError("Complete source does not match the publisher Git blob")
    source = value["source_artifact"]
    validate_artifact(source, raw)
    if (source["producer"] != "ciw.link_references" or source["experiment_id"] != profile["split_group"]
            or source["media_type"] != ("text/plain" if value["profile_id"] == "nist-pontius" else "text/csv")
            or c.canonical_json(source["metadata"]) != c.canonical_json(_artifact_metadata(profile))):
        raise ValueError("Native reference artifact contradicts the reviewed source profile")
    return deepcopy(value)


def _artifact_metadata(profile):
    return {"source_url": profile["source_url"], "source_scope": profile["source_scope"],
            "upstream_blob": profile["upstream_blob"], "whole_file_hash_checked": profile["whole_file_hash_checked"],
            "created_at_meaning": "local_reference_retention_not_physical_acquisition"}


def _number(token):
    token = token.strip()
    if len(token) > 48 or not _NUMBER.fullmatch(token):
        raise ValueError("Unsupported reference numeric token")
    value = Decimal(token)
    if not value.is_finite() or (value and not Decimal("1e-300") <= abs(value) <= Decimal("1e300")):
        raise ValueError("Nonfinite or out-of-range reference number")
    return token, value


def _lines(raw, skip):
    offset = 0
    for index, line in enumerate(raw.splitlines(keepends=True), 1):
        start, offset = offset, offset + len(line)
        if index > skip:
            yield index, [start, offset], line.decode("ascii")


def _pontius(raw):
    rows = []
    with localcontext() as ctx:
        ctx.prec = 80
        beta = list(map(Decimal, PONTIUS_BETA))
        rss = Decimal(0)
        for line, span, text in _lines(raw, 25):
            tokens = text.split()
            if len(tokens) != 2:
                raise ValueError("Pontius row requires response then load")
            (yt, y), (xt, x) = map(_number, tokens)
            predicted = beta[0] + beta[1] * x + beta[2] * x * x
            residual = y - predicted
            rss += residual * residual
            rows.append({"line": line, "byte_range": span, "load": xt, "deflection": yt,
                         "set_index": len(rows) // 20, "predicted": str(predicted), "residual": str(residual)})
        if len(rows) != 40 or [Decimal(r["load"]) for r in rows] != [Decimal(150000 * (i % 20 + 1)) for i in range(40)]:
            raise ValueError("Pontius must retain both ordered 20-level sets")
        reference_match = abs(rss - Decimal(PONTIUS_RSS)) <= Decimal(PONTIUS_RSS_ATOL)
    return {"rows": rows, "coefficients": list(PONTIUS_BETA), "residual_sum_squares": str(rss),
            "published_residual_sum_squares": PONTIUS_RSS, "comparison_atol": PONTIUS_RSS_ATOL,
            "published_reference_match": reference_match,
            "fit_performed": False, "native_observation_projection": "withheld_units_and_time_unspecified",
            "covariance": None, "coefficient_standard_errors_are_not_joint_measurement_covariance": True}


def _hydrophone(raw):
    rows = []
    source_ref = c.bytes_ref(raw)
    header = next(csv.reader([raw.splitlines()[0].decode("ascii")]))
    if len(header) != 12:
        raise ValueError("Require all 12 publisher columns")
    for line, span, text in _lines(raw, 1):
        tokens = next(csv.reader([text]))
        if len(tokens) != 12:
            raise ValueError("Hydrophone row requires all original fields")
        pairs = list(map(_number, tokens)); original = [a for a, _ in pairs]
        with localcontext() as ctx:
            ctx.prec = 80
            f, r, i, vr, vi, cv = [v for _, v in pairs[:6]]
            m, u = pairs[7][1], pairs[8][1]
            if f <= 0 or vr < 0 or vi < 0 or vr * vi < cv * cv or m <= 0 or u < 0:
                raise ValueError("Invalid frequency, magnitude or pointwise covariance")
            mag2 = r*r + i*i
            mag = mag2.sqrt()
            # Existing NET full covariance transport, not a second UQ engine.
            matrix = [[float(vr), float(cv)], [float(cv), float(vi)]]
            jacobian = [[float(r / mag), float(i / mag)]]
            variance = link_study._transport(matrix, jacobian)[0][0]
            point_id = c.content_identity({"source_ref": source_ref, "line": line, "fields": original})
            covariance = create_covariance_artifact(matrix=matrix,
                quantity_ids=[point_id+":real", point_id+":imag"], units=["V/Pa", "V/Pa"],
                frame="complex-sensitivity-real-imag", reference_values=[float(r), float(i)],
                method="publisher_pointwise_covariance_columns_4_5_6",
                basis={"kind": "parameter", "id": point_id},
                provenance={"provider": "weber-wilkens-eichstaedt", "source_evidence_ids": [source_ref], "source_covariance_ids": []},
                assumptions=["Only the published 2x2 pointwise covariance; cross-frequency covariance is unknown",
                             "Binary64 projection; original decimal tokens and source bytes retained separately",
                             "Not calibration evidence for a different instrument"])
            propagated = create_covariance_artifact(matrix=[[variance]], quantity_ids=[point_id+":magnitude"], units=["V/Pa"],
                frame="sensitivity-magnitude", reference_values=[float(mag)], method="first_order_J_C_JT_existing_NET_transport",
                basis={"kind": "parameter", "id": point_id+":magnitude"},
                provenance={"provider": "ciw.link_study", "source_evidence_ids": [source_ref], "source_covariance_ids": [covariance["covariance_id"]]},
                assumptions=["Conditional first-order propagation, not nonlinear confidence coverage", "Cross-frequency covariance unknown"])
            rows.append({"line": line, "byte_range": span, "original_fields": original,
                "frequency_hz": str(f * Decimal(1000000)), "frequency_conversion": "exact_MHz_times_1000000",
                "point_id": point_id, "covariance": covariance, "magnitude_covariance": propagated,
                "magnitude": float(mag), "magnitude_variance": variance,
                "published_magnitude": original[7], "published_standard_uncertainty": original[8],
                "magnitude_table_agreement": abs(mag2-m*m) <= Decimal(HYDRO_RELATIVE_ALLOWANCE)*mag2,
                "uncertainty_table_agreement": abs(Decimal.from_float(variance)-u*u) <= Decimal(HYDRO_RELATIVE_ALLOWANCE)*u*u})
    if len(rows) != 17 or any(Decimal(rows[i]["frequency_hz"]) <= Decimal(rows[i-1]["frequency_hz"]) for i in range(1,len(rows))):
        raise ValueError("Require all 17 selected ordered frequencies")
    return {"rows": rows, "original_header": header, "comparison_relative_allowance": HYDRO_RELATIVE_ALLOWANCE,
            "joint_cross_frequency_covariance": None, "uncertainty_meaning": "pointwise_k1",
            "auxiliary_columns": "all_retained; columns_7_10_11_12_not_reinterpreted",
            "deconvolution": "not_performed", "native_observation_projection": "withheld_acquisition_time_unspecified"}


def review(value):
    value = validate_request(value)
    raw = c.unpack(value["payload"], 8192)
    profile = deepcopy(PROFILES[value["profile_id"]])
    data = (_pontius if value["profile_id"] == "nist-pontius" else _hydrophone)(raw)
    return {"schema": "ciw.link-reference-review.v1", "profile_id": value["profile_id"], "source": profile,
            "source_artifact": deepcopy(value["source_artifact"]),
            "data": data, "limits": dict(LIMITS)}


def audit(value, candidate):
    """Independent rational arithmetic over raw tokens, not the primary transport."""
    validate_request(value)
    raw = c.unpack(value["payload"], 8192)
    data = candidate["data"]
    checks = []
    def check(name, ok): checks.append({"name": name, "passed": bool(ok)})
    if value["profile_id"] == "nist-pontius":
        coefficients = list(map(Fraction, PONTIUS_BETA))
        rss = Fraction(0)
        for index, (_, _, text) in enumerate(_lines(raw, 25)):
            y, x = map(Fraction, text.split())
            p = sum(coef*x**power for power,coef in enumerate(coefficients))
            rss += (y-p)**2
            check(f"row.{index}.prediction", Fraction(data["rows"][index]["predicted"]) == p)
            check(f"row.{index}.residual", Fraction(data["rows"][index]["residual"]) == y-p)
        check("residual_sum_squares", Fraction(data["residual_sum_squares"]) == rss)
        check("published_rss", abs(rss-Fraction(PONTIUS_RSS)) <= Fraction(PONTIUS_RSS_ATOL))
        check("no_invented_covariance", data["covariance"] is None)
    else:
        for index, (_, _, text) in enumerate(_lines(raw, 1)):
            f,r,i,vr,vi,cv,_,m,u,*_ = map(lambda v: Fraction(v.strip()), next(csv.reader([text])))
            row=data["rows"][index]; magnitude2=r*r+i*i
            variance=(r*r*vr+i*i*vi+2*r*i*cv)/magnitude2
            actual=Fraction(row["magnitude_variance"])
            check(f"row.{index}.pointwise_psd",vr>=0 and vi>=0 and vr*vi>=cv*cv)
            check(f"row.{index}.frequency", Fraction(row["frequency_hz"])==f*1000000)
            check(f"row.{index}.magnitude_variance", abs(actual-variance) <= abs(variance)*Fraction("2e-14"))
            check(f"row.{index}.publisher_magnitude",abs(magnitude2-m*m)<=Fraction(HYDRO_RELATIVE_ALLOWANCE)*magnitude2)
            check(f"row.{index}.publisher_variance",abs(variance-u*u)<=Fraction(HYDRO_RELATIVE_ALLOWANCE)*u*u)
            validate_covariance_artifact(row["covariance"]); validate_covariance_artifact(row["magnitude_covariance"])
            check(f"row.{index}.cross_term_retained", row["covariance"]["matrix"][0][1] == float(cv))
        check("cross_frequency_unknown",data["joint_cross_frequency_covariance"] is None)
    return {"schema":"ciw.link-reference-audit.v1", "status":"PASS" if all(x["passed"] for x in checks) else "FAIL",
            "checks":checks, "source_profile":value["profile_id"], "scope":"numerical_reference_mapping_and_conditional_arithmetic_only", "limits":dict(LIMITS)}


def make_source(value, raw_ref):
    c.bounded(value, MAX_BYTES); c.content_ref(raw_ref)
    manifest=InstrumentManifest(instrument_id="published-reference.v1",role="published_reference_dataset",
        units={CHANNEL:"1"},frames=(FRAME,),sampling={"kind":"one_declaration", "time_semantics":"not_physical_time"},
        supported_operations=OPS,calibration_requirements={"scope":"no_calibration_transfer"})
    result={"run_schema":"run.v1", "run_id":"reference-"+c.content_identity({"request":value,"bytes":raw_ref})[7:23],
        "instrument":"published-reference.v1", "metadata":{"duration_s":1.0,"sample_count":1,"sample_rate_hz":None,
        "coordinate_frame":FRAME,"manifest":manifest.to_dict(),"reference_request":deepcopy(value),"input_bytes_ref":raw_ref,
        "provenance":{"source":"published_reference_declaration","generator":"ciw.link_references","generator_version":1}},
        "time_s":[0.0],"channels":{CHANNEL:{"unit":"1","values":[1.0]}},"render":{}}
    result["evidence_id"]=evidence_id(result)
    return result


def source_request(source):
    validate_run_structure(source); validate_evidence_identity(source)
    value=source["metadata"]["reference_request"]
    if c.canonical_json(make_source(value,source["metadata"]["input_bytes_ref"]))!=c.canonical_json(source):
        raise ValueError("Reference source binding mismatch")
    return value


def execute(index,source,params):
    value=source_request(source)
    c.keys(params,set() if index==0 else {"candidate"})
    if index==0: return review(value)
    prior=params["candidate"]
    if prior["operation_id"]!=OPS[0] or prior["evidence_id"]!=source["evidence_id"]:
        raise ValueError("Reference audit requires its same-source review")
    if c.canonical_json(prior["data"])!=c.canonical_json(review(value)):
        raise ValueError("Reference prerequisite is inconsistent")
    result=audit(value,prior["data"])
    result["candidate_result_id"]=prior["result_id"]
    return result


def _validator(index):
    def check(op,data,source,params,selection):
        if op!=OPS[index] or selection["channel"]!=CHANNEL or selection["interval_s"]!=[0.0,1.0]:
            raise ValueError("Reference review requires the complete declaration")
        if c.canonical_json(data)!=c.canonical_json(execute(index,source,params)):
            raise ValueError("Reference result does not match retained source and arithmetic")
    return check


def register():
    global _REGISTERED
    with _LOCK:
        if not _REGISTERED:
            register_payload_validator(OPS[0],_validator(0),role="backend")
            register_payload_validator(OPS[1],_validator(1),role="verification")
            register_dependency_fields(OPS[1],("candidate",))
            _REGISTERED=True


def runtime_identity():
    import numpy
    from .core import covariance
    from .operations import runner, schemas
    parts=(Path(__file__),Path(link_study.__file__),Path(c.__file__),Path(covariance.__file__),Path(runner.__file__),Path(schemas.__file__))
    raw=b"\0".join(p.name.encode()+b"\0"+p.read_bytes().replace(b"\r\n",b"\n") for p in parts)
    return {"provider":"ciw.link_references","version":"1","python":platform.python_version(),
            "code_sha256":sha256(raw).hexdigest(),"numpy":numpy.__version__,"scope":"pinned_published_reference_only"}


def registry(*, bind=True):
    register(); result=CapabilityRegistry()
    manifest=InstrumentManifest(instrument_id="published-reference.v1",role="published_reference_dataset",
        units={CHANNEL:"1"},frames=(FRAME,),sampling={"kind":"one_declaration"},supported_operations=OPS,
        calibration_requirements={"scope":"no_calibration_transfer"})
    result.advertise(manifest,runtime=runtime_identity(),capabilities={op:["published_reference"] for op in OPS},
        inputs={OPS[1]:{"candidate":Port("ciw.operation-result.v1").to_dict()}})
    if bind:
        for i,op in enumerate(OPS):
            result.bind(Operation(op,("backend","verification")[i],lambda source,params,i=i:execute(i,source,params),runtime_identity))
    return result


def graph():
    return experiment("link-published-reference",model_id="published-reference.v1",nodes=[
        {"node_id":"review","operation_id":OPS[0],"parameters":{},"depends_on":[],"inputs":{}},
        {"node_id":"audit","operation_id":OPS[1],"parameters":{},"depends_on":[],
         "inputs":{"candidate":{"node_id":"review","port":"result"}}}])
