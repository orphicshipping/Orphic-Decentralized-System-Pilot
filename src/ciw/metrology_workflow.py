"""Metrology profiles use the existing evidence, operation, Session and AgentHost lifecycle."""
from __future__ import annotations

import base64
from hashlib import sha256
from pathlib import Path

from .adapters.protocol import InstrumentManifest
from .agent_api import AgentHost, encode, parse
from .control_contracts import keys, save_new
from .control_plane import CapabilityRegistry, experiment
from .core.identities import evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .operations.registry import Operation
from .operations.schemas import register_payload_validator
from .metrology_contract import MAX_BYTES, PROFILES, case, digest
from . import metrology_numeric as numeric

_registered = False


def make_source(raw):
    s = case(raw)
    op = PROFILES[s["profile"]]
    manifest = InstrumentManifest(instrument_id=s["profile"], role="retained_inspection",
        units={"inspection":"1"}, frames=(s["capture"]["frame"],),
        sampling={"kind":"declaration","time_semantics":"synthetic_transport_envelope"},
        supported_operations=(op,), calibration_requirements={"scope":"declared_applicability"})
    source = {"run_schema":"run.v1", "run_id":"metrology-"+sha256(raw).hexdigest()[:24],
        "instrument":s["profile"], "metadata":{"duration_s":1.,"sample_count":1,"sample_rate_hz":None,
            "coordinate_frame":s["capture"]["frame"],"manifest":manifest.to_dict(),
            "metrology_case":{"sha256":digest(raw),"bytes_b64":base64.b64encode(raw).decode()},
            "provenance":{"source":s["origin"],"generator":"ciw.metrology_workflow.make_source","generator_version":1}},
        "time_s":[0.],"channels":{"inspection":{"unit":"1","values":[1.]}},"render":{}}
    source["evidence_id"] = evidence_id(source)
    return source


def source_bytes(source):
    validate_run_structure(source); validate_evidence_identity(source)
    value = source["metadata"]["metrology_case"]
    keys(value, {"sha256","bytes_b64"})
    if type(value["bytes_b64"]) is not str or len(value["bytes_b64"]) > 4*((MAX_BYTES+2)//3):
        raise ValueError("Inspection envelope exceeds bounded evidence budget")
    raw = base64.b64decode(value["bytes_b64"], validate=True)
    if source != make_source(raw):
        raise ValueError("Inspection envelope differs from exact evidence")
    return raw


def graph(profile):
    if profile not in PROFILES:
        raise ValueError("Unknown metrology profile")
    return experiment("metrology-case", model_id=profile, nodes=[{
        "node_id":"assess","operation_id":PROFILES[profile],"parameters":{},"inputs":{},"depends_on":[]}])


def _execute(source, parameters):
    if parameters != {"channel":"inspection", "interval_s":[0.0,1.0]}:
        raise ValueError("Metrology uses its complete fixed transport marker")
    return numeric.assess(source_bytes(source))


def _validate(operation, data, source, parameters, selection):
    if parameters != {"channel":"inspection", "interval_s":[0.0,1.0]}:
        raise ValueError("Metrology uses its complete fixed transport marker")
    raw = source_bytes(source)
    if operation != PROFILES[case(raw)["profile"]]:
        raise ValueError("Profile/operation binding differs")
    from .metrology_audit import validate_retained
    validate_retained(data,raw)


def register():
    global _registered
    if not _registered:
        for operation in PROFILES.values():
            register_payload_validator(operation,_validate,role="analysis")
        _registered = True


def registry(profile, *, bind=True):
    if profile not in PROFILES or type(bind) is not bool:
        raise ValueError("Require a fixed host metrology profile")
    register()
    operation = PROFILES[profile]
    r = CapabilityRegistry()
    manifest = InstrumentManifest(instrument_id=profile,role="analysis",units={},frames=(),
        sampling={"kind":"bounded_sampled_geometry"}, supported_operations=(operation,),
        calibration_requirements={"physical_qualification":"not_established"})
    r.advertise(manifest,runtime=numeric.runtime_identity(), capabilities={operation:[profile]},inputs={operation:{}})
    if bind:
        r.bind(Operation(operation,"analysis",_execute,numeric.runtime_identity))
    return r


class MetrologyHost(AgentHost):
    def validate_plan(self, run, value):
        s = case(source_bytes(run))
        if value != graph(s["profile"]):
            raise ValueError("Inspection requires its fixed graph and policy")
        return super().validate_plan(run,value)


def prepare(raw, destination):
    s = case(raw);source = make_source(raw)
    root = Path(destination)
    if root.is_symlink():
        raise ValueError("Profile destination cannot be a symlink")
    root.mkdir(parents=False,exist_ok=False)
    (root/"inspection.json").write_bytes(raw)
    save_new(root/"source.json",source);save_new(root/"experiment.json",graph(s["profile"]))
    save_new(root/"profile.json",{"schema":"ciw.agent-host-profile.v1",
        "inputs":{"source":"source.json","baseline":"experiment.json"},"output_dir":"agent-output",
        "allow_operations":[PROFILES[s["profile"]]],"candidate_domains":{},"comparisons":{},"check_suites":{},
        "max_executions":8,"max_nodes":1})
    return root/"profile.json"


def from_profile(path, *, resume=False):
    # The existing loader owns file budgets, frozen grants and recovery. Only a
    # process-local instrument selector chooses this trusted registry.
    from .agent_mcp import from_profile as load
    return load(Path(path),instrument="metrology",resume=resume)


def main(argv=None):
    import argparse
    import sys
    from .metrology_contract import example
    from .metrology_ingress import read_case
    from .agent_mcp import serve
    from .agent_api import _validated
    p = argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest="command",required=True)
    e=sub.add_parser("example");e.add_argument("profile",choices=PROFILES);e.add_argument("output")
    e=sub.add_parser("prepare");e.add_argument("inspection");e.add_argument("destination")
    for name in ("run","serve"):
        e=sub.add_parser(name);e.add_argument("profile");e.add_argument("--resume",action="store_true")
        if name=="run":e.add_argument("--attempt",required=True)
        else:e.add_argument("--sdk",action="store_true")
    e=sub.add_parser("inspect");e.add_argument("workspace")
    a=p.parse_args(argv)
    try:
        if a.command=="example":save_new(Path(a.output),example(a.profile));return 0
        if a.command=="prepare":print(prepare(read_case(a.inspection),a.destination));return 0
        if a.command=="inspect":register();print(encode(_validated(parse(Path(a.workspace).read_bytes()))).decode());return 0
        with from_profile(a.profile,resume=a.resume) as host:
            if a.command=="serve":
                if a.sdk:
                    from .mcp_server import serve_sdk
                    return serve_sdk(host,name="ncp-metrology")
                return serve(host,sys.stdin.buffer,sys.stdout.buffer)
            print(encode(host.execute("source","baseline",a.attempt)).decode());return 0
    except (ValueError,OSError,KeyError) as error:
        print(str(error),file=sys.stderr);return 2


if __name__=="__main__":
    raise SystemExit(main())
