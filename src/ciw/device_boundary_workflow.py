"""Host-bound functional gates over the existing Session/OperationRegistry.

Saved gate results never grant permissions. Every perform call freshly evaluates
operator policy, signature, context, coherence, time and optional SP1 proof.
Only recorded, read-only processing is implemented; release/control always deny.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from fractions import Fraction
import struct
import threading
import time

from . import device_boundary as b
from .adapters.protocol import AdapterRefusal
from .core.identities import content_identity, new_identity
from .legibility import key_fingerprint
from .operations.registry import Operation
from .operations.schemas import register_payload_validator

REVIEW = "device.boundary.review.v1"
PERFORM = "device.boundary.perform.v1"
_REGISTERED = False
_SCHEMA_LOCK = threading.RLock()


def validate_policy(value):
    b.keys(value, {"schema", "epoch", "device_id", "boot_id", "device_public_key_b64",
        "firmware_id", "gateware_id", "frame_id", "clock_id", "approved_transform_ids",
        "allowed_functions", "require_sp1_functions", "valid_from_ns", "valid_until_ns",
        "max_sample_age_ns", "max_time_uncertainty_ns", "specification_nm", "procedure_id"})
    if value["schema"] != "ciw.device-boundary-policy.v1":
        raise ValueError("unsupported host policy")
    for key in ("epoch", "device_id", "boot_id", "frame_id", "clock_id"):
        b.identifier(value[key])
    if len(b.unb64(value["device_public_key_b64"], 32)) != 32:
        raise ValueError("invalid host trust anchor")
    b.hash_id(value["firmware_id"])
    if value["gateware_id"] is not None:
        b.hash_id(value["gateware_id"])
    for key, allowed in (("allowed_functions", set(b.FUNCTIONS)), ("require_sp1_functions", set(b.FUNCTIONS))):
        items = value[key]
        if type(items) is not list or any(type(s) is not str for s in items) or len(set(items)) != len(items) or not set(items) <= allowed:
            raise ValueError("invalid or duplicate host capability")
    transforms = value["approved_transform_ids"]
    if type(transforms) is not list or len(transforms) > 32 or len(set(transforms)) != len(transforms):
        raise ValueError("invalid transform allowlist")
    for item in transforms:
        b.hash_id(item)
    start=b.integer(value["valid_from_ns"],0,2**63-1)
    end=b.integer(value["valid_until_ns"],0,2**63-1)
    if end <= start:
        raise ValueError("empty authorization interval")
    b.integer(value["max_sample_age_ns"], 1, 10**18)
    b.integer(value["max_time_uncertainty_ns"], 0, 10**12)
    if value["specification_nm"] is not None:
        spec=value["specification_nm"]
        b.keys(spec,{"specification_id","lower","upper"})
        b.hash_id(spec["specification_id"])
        if b.ratio(spec["upper"]) < b.ratio(spec["lower"]):
            raise ValueError("invalid tolerance limits")
    if value["procedure_id"] is not None:
        b.hash_id(value["procedure_id"])
    b.encode(value)
    return deepcopy(value)


def proof_record(packet, payload, verifier):
    proof=packet["proof"]
    input_bytes=b.statement(payload); output_bytes=b.compute(input_bytes)
    base={"status":"unavailable", "reason":"proof_not_supplied", "verification_id":None,
          "input_id":b.raw_id(input_bytes), "output_id":b.raw_id(output_bytes), "runtime":None}
    if proof is None:
        return base
    try:
        b.keys(proof,{"schema","bytes_b64"})
        if proof["schema"] != "ciw.device-boundary-sp1.v1":
            raise ValueError("not an SP1 boundary proof envelope")
        raw=b.unb64(proof["bytes_b64"],8*1024*1024)
        if not raw:
            raise ValueError("empty proof")
        if verifier is None:
            return {**base,"reason":"registered_SP1_verifier_not_bound"}
        # A verifier is trusted operator configuration, never resolved from data.
        runtime=verifier.verify(input_bytes,output_bytes,raw)
        if type(runtime) is not dict or not runtime:
            raise ValueError("verifier supplied no runtime evidence")
        return {**base,"status":"verified","reason":None,"runtime":runtime,
                "verification_id":new_identity("verification")}
    except Exception as exc:
        return {**base,"status":"failed","reason":type(exc).__name__+": "+str(exc)[:300]}


def evaluate(raw_packet, policy, now_ns, *, previous_sequences=None, verifier=None, historical_proof=None):
    """No IO other than the explicitly bound verifier; no side effects or grants."""
    policy=validate_policy(policy); b.integer(now_ns,0,2**63-1)
    previous_sequences = {f: -1 for f in b.FUNCTIONS} if previous_sequences is None else deepcopy(previous_sequences)
    b.keys(previous_sequences,set(b.FUNCTIONS))
    for sequence in previous_sequences.values(): b.integer(sequence,-1,2**53-1)
    if type(raw_packet) is not bytes or not 0 < len(raw_packet) <= b.MAX_BYTES:
        raise ValueError("packet byte budget exceeded")
    checks={k:False for k in ("schema","identity","firmware_declaration","gateware_declaration",
        "frame","clock","calibration","coherence","uncertainty","freshness","authorization","filter_declaration")}
    checks["authorization"]=policy["valid_from_ns"] <= now_ns < policy["valid_until_ns"]
    review={"schema":b.REPORT, "review_id":new_identity("verification"),
        "packet_id":b.raw_id(raw_packet),"policy_id":content_identity(policy),
        "policy_snapshot":policy,"evaluated_at_ns":now_ns,"previous_sequences":previous_sequences,
        "device_id":None,"sample_id":None,"raw_id":None,"canonical_state_nm":None,
        "declared_state_nm":None,"transform_id":None,"checks":checks,"quarantine_reasons":[],
        "proof":None,"objects":None,"capabilities":{},"limits":deepcopy(b.LIMITS),
        "authorizes_execution":False,"mode":"historical_review_not_transferable_permission"}
    try:
        packet=b.parse(raw_packet)
        b.keys(packet,{"schema","payload","signature","proof"})
        p=b.validate_payload(packet["payload"])
        if packet["schema"] != b.PACKET:
            raise ValueError("unsupported packet")
        checks["schema"]=True
        review.update(device_id=p["device_id"],sample_id=p["sample_id"],raw_id=p["raw_id"],transform_id=content_identity(p["transform"]))
        key=b.unb64(policy["device_public_key_b64"],32)
        sig=b.authenticated(packet,{key_fingerprint(key):key})
        checks["identity"]=(sig["signature_valid"] is True and sig["issuer_trusted"] is True
                             and sig["content_intact"] is True and sig["artifact_status"]=="verified"
                             and sig["object_matches"] is True and sig["version_current"] is True
                             and p["device_id"]==policy["device_id"] and p["boot_id"]==policy["boot_id"])
        checks["firmware_declaration"]=p["firmware_id"]==policy["firmware_id"]
        checks["gateware_declaration"]=p["gateware_id"]==policy["gateware_id"]
        checks["frame"]=p["frame_id"]==policy["frame_id"]
        checks["filter_declaration"]=p["filter_id"]==b.FILTER_D
        u=p["time_uncertainty_ns"]; sample=p["captured_at_ns"]
        checks["clock"]=(p["clock_id"]==policy["clock_id"] and u is not None and u<=policy["max_time_uncertainty_ns"])
        cfg=p["transform"]; count=struct.unpack("<q",b.unb64(p["raw_b64"],8))[0]
        checks["calibration"]=(review["transform_id"] in policy["approved_transform_ids"]
            and checks["clock"] and cfg["valid_from_ns"] <= sample-u
            and sample+u < cfg["valid_until_ns"] and cfg["count_min"] <= count <= cfg["count_max"])
        y=b.reconstruct(p); claimed=b.ratio(p["device_state"]["value"])*b.UNITS_NM[p["device_state"]["unit"]]
        review.update(canonical_state_nm=b.rational(y),declared_state_nm=b.rational(claimed))
        computation={"schema":"ciw.device-boundary-computation.v1",
            "program_id":b.raw_id(b.PROGRAM),"input_id":b.raw_id(b.statement(p)),
            "output_id":b.raw_id(b.compute(b.statement(p))),"transform_id":review["transform_id"]}
        review["objects"]={
            "filters":{
                "device":{"profile_id":p["filter_id"],"status":"device_declared_not_attested",
                    "filter_id":content_identity({"profile":p["filter_id"],"raw_id":p["raw_id"]})},
                "net":{"profile_id":b.FILTER_N,"status":"executed_evidence_preserving_validation",
                    "filter_id":content_identity({"profile":b.FILTER_N,"packet_id":review["packet_id"],"raw_id":p["raw_id"]})}},
            "computation":{**computation,"computation_id":content_identity(computation),
                           "execution_id":new_identity("execution")}}
        checks["coherence"]=y==claimed
        checks["uncertainty"]=cfg["uncertainty_bound_nm"] is not None
        checks["freshness"]=(checks["clock"] and sample+u<=now_ns and now_ns-(sample-u)<=policy["max_sample_age_ns"])

        # Offline validation may inspect historical proof metadata, NEVER re-use it live.
        review["proof"]=deepcopy(historical_proof) if historical_proof is not None else proof_record(packet,p,verifier if checks["identity"] else None)
    except (ValueError,TypeError,KeyError,OverflowError,RecursionError) as exc:
        review["quarantine_reasons"].append(type(exc).__name__+": "+str(exc)[:300])
    required_base=("schema","identity","authorization")
    derived=required_base+("firmware_declaration","gateware_declaration","frame","clock","calibration","coherence","freshness","filter_declaration")
    for function in b.FUNCTIONS:
        reasons=[]
        if function not in b.SUPPORTED: reasons.append("unsupported_function_no_physical_authority")
        if function not in policy["allowed_functions"]: reasons.append("not_authorized_by_current_host")
        needs=required_base if function in ("display_raw","record_raw") else derived
        reasons.extend(key for key in needs if not checks[key])
        if checks["schema"] and function not in ("display_raw","record_raw") and p["sequence"] <= previous_sequences[function]:
            reasons.append("sequence_replay_or_out_of_order")
        if function not in ("display_raw","record_raw") and (review["proof"] or {}).get("status")=="failed":
            reasons.append("supplied_proof_failed")
        if function in ("compare_tolerance","generate_inspection_record"):
            if not checks["uncertainty"]: reasons.append("total_uncertainty_bound_unavailable")
            if policy["specification_nm"] is None: reasons.append("specification_unavailable")
        if function=="generate_inspection_record" and policy["procedure_id"] is None:
            reasons.append("procedure_unavailable")
        if function in policy["require_sp1_functions"] and (review["proof"] or {}).get("status")!="verified":
            reasons.append("fresh_registered_SP1_verification_required")
        review["capabilities"][function]={"gate_id":new_identity("decision"),"allow":not reasons,"reasons":reasons}
    review["quarantine_reasons"].extend(k for k in ("schema","identity","frame","coherence") if not checks[k])
    return review


def validate_report(report, raw_packet):
    """Saved proof reports are historical only; mathematical checks replay offline."""
    b.keys(report,{"schema","review_id","packet_id","policy_id","policy_snapshot","evaluated_at_ns",
        "previous_sequences","device_id","sample_id","raw_id","canonical_state_nm","declared_state_nm",
        "transform_id","checks","quarantine_reasons","proof","objects","capabilities","limits","authorizes_execution","mode"})
    from .core.identities import validate_identity
    validate_identity(report["review_id"],"verification")
    proof=report["proof"]
    if proof is not None:
        b.keys(proof,{"status","reason","verification_id","input_id","output_id","runtime"})
        if proof["status"] not in {"unavailable","failed","verified"}: raise ValueError("invalid proof status")
        p=b.parse(raw_packet)["payload"]
        if proof["input_id"]!=b.raw_id(b.statement(p)) or proof["output_id"]!=b.raw_id(b.compute(b.statement(p))):
            raise ValueError("historical proof statement mismatch")
        if proof["status"]=="verified":
            validate_identity(proof["verification_id"],"verification")
            if not isinstance(proof["runtime"],dict) or not proof["runtime"] or b.parse(raw_packet)["proof"] is None:
                raise ValueError("verified proof lacks retained runtime/artifact")
        elif proof["verification_id"] is not None or proof["runtime"] is not None:
            raise ValueError("unverified proof cannot carry verification authority")
    expected=evaluate(raw_packet,report["policy_snapshot"],report["evaluated_at_ns"],
                      previous_sequences=report["previous_sequences"],historical_proof=proof)
    expected["review_id"]=report["review_id"]
    b.keys(report["capabilities"],set(b.FUNCTIONS))
    for f,gate in report["capabilities"].items():
        b.keys(gate,{"gate_id","allow","reasons"})
        validate_identity(gate["gate_id"],"decision")
        expected["capabilities"][f]["gate_id"]=gate["gate_id"]
    if expected["objects"] is not None and report["objects"] is not None:
        occurrence=report["objects"]["computation"]["execution_id"]
        validate_identity(occurrence,"execution")
        expected["objects"]["computation"]["execution_id"]=occurrence
    if b.encode(expected)!=b.encode(report): raise ValueError("retained review differs from semantic replay")


def functional_result(review, raw_packet, function):
    if not review["capabilities"][function]["allow"]:
        raise AdapterRefusal("boundary_gate_closed",function+": "+", ".join(review["capabilities"][function]["reasons"]))
    p=b.parse(raw_packet)["payload"]
    value={"function":function,"sample_id":p["sample_id"],"packet_id":review["packet_id"],
           "physical_validation":"not_established","equipment_authority":False}
    if function in ("record_raw","display_raw"):
        value.update(raw_id=p["raw_id"],raw_b64=p["raw_b64"],label="raw_recorded_counts")
    else:
        value.update(value_nm=review["canonical_state_nm"],label="calibration_applied_not_physically_qualified")
    if function in ("compare_tolerance","generate_inspection_record"):
        y=b.ratio(review["canonical_state_nm"]); u=b.ratio(p["transform"]["uncertainty_bound_nm"])
        spec=review["policy_snapshot"]["specification_nm"]
        low,high=b.ratio(spec["lower"]),b.ratio(spec["upper"])
        state="inside_declared_interval" if low<=y-u and y+u<=high else (
            "outside_declared_interval" if y+u<low or high<y-u else "indeterminate")
        value.update(comparison=state,specification=deepcopy(spec),interval_nm=[b.rational(y-u),b.rational(y+u)],
                     procedure_id=review["policy_snapshot"]["procedure_id"],released=False)
    return value


def validate_operation(operation_id,data,run,parameters,selection):
    b.keys(parameters,{"packet_b64"} if operation_id==REVIEW else {"packet_b64","function"})
    b.keys(data,{"review"} if operation_id==REVIEW else {"review","function_result"})
    raw=b.unb64(parameters["packet_b64"])
    validate_report(data["review"],raw)
    if operation_id==PERFORM:
        if b.encode(functional_result(data["review"],raw,parameters["function"]))!=b.encode(data["function_result"]):
            raise ValueError("retained capability effect differs")


def register_schemas():
    global _REGISTERED
    with _SCHEMA_LOCK:
        if not _REGISTERED:
            register_payload_validator(REVIEW,validate_operation,role="backend")
            register_payload_validator(PERFORM,validate_operation,role="backend")
            _REGISTERED=True


class BoundaryHost:
    """One host per Session; no separate journal, gateway or global replay service."""
    def __init__(self,session,policy,*,clock=time.time_ns,verifier=None):
        register_schemas()
        if verifier is not None:
            from .device_boundary_sp1 import ExistingSP1Verifier
            if not isinstance(verifier,ExistingSP1Verifier):
                raise ValueError("only an operator-bound existing SCR SP1 verifier is accepted")
        self.session=session; self._lock=threading.RLock(); self._seen={}; self._last_now=None
        self._policy=validate_policy(policy); self._clock=clock; self._verifier=verifier
        for operation_id in (REVIEW,PERFORM):
            session.operations.register(Operation(operation_id,"backend",
                lambda run,params,op=operation_id:self._operate(op,params),
                lambda:{"provider":"ciw.device-boundary.v1",
                        "scope":"recorded_read_only_processing"}))

    def _now(self):
        now=b.integer(self._clock(),0,2**63-1)
        if self._last_now is not None and now < self._last_now:
            raise AdapterRefusal("boundary_clock_rollback","host clock moved backwards; execution refused")
        self._last_now=now
        return now

    def set_policy(self,policy):
        with self._lock: self._policy=validate_policy(policy)

    def _previous(self,p):
        key=(p["device_id"],p["boot_id"])
        previous={f:self._seen.get((*key,f),-1) for f in b.FUNCTIONS}
        for result in list(self.session.results.values()):
            if result.get("operation_id")!=PERFORM:continue
            prior=b.parse(b.unb64(result["parameters"]["packet_b64"]))["payload"]
            function=result["parameters"]["function"]
            if (prior["device_id"],prior["boot_id"])==key:
                previous[function]=max(previous[function],prior["sequence"])
        return previous

    def _operate(self,operation_id,params):
        with self._lock:
            b.keys(params,{"packet_b64"} if operation_id==REVIEW else {"packet_b64","function"})
            raw=b.unb64(params["packet_b64"])
            previous=None; p=None
            try:
                p=b.validate_payload(b.parse(raw)["payload"]); previous=self._previous(p)
            except (ValueError,TypeError,KeyError,RecursionError):pass
            review=evaluate(raw,self._policy,self._now(),previous_sequences=previous,verifier=self._verifier)
            if operation_id==REVIEW:return {"review":review}
            function=params["function"]
            if type(function) is not str or function not in b.FUNCTIONS:
                raise AdapterRefusal("unknown_boundary_capability","unsupported capability")
            # Proof verification can be slow: expiry/freshness is checked again
            # at the functional boundary while the host policy lock is held.
            completed_at=self._now()
            if completed_at!=review["evaluated_at_ns"]:
                review=evaluate(raw,self._policy,completed_at,previous_sequences=previous,
                                historical_proof=review["proof"])
            effect=functional_result(review,raw,function)
            if p is not None:
                self._seen[(p["device_id"],p["boot_id"],function)]=max(previous[function],p["sequence"])
            return {"review":review,"function_result":effect}

    def request(self,raw_packet,function=None):
        with self._lock:
            params={"packet_b64":base64.b64encode(raw_packet).decode()}
            if function is not None:params["function"]=function
            return self.session.handle({"protocol_version":1,"request_id":new_identity("execution"),
                "type":"operation.execute","payload":{"operation_id":REVIEW if function is None else PERFORM,"parameters":params}})
