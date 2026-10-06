"""NET Link scalar-study entry point; no hardware commands or private providers."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import tempfile

from . import link_contract as c, link_study as study, link_study_io as io
from .control_contracts import save_new
from .hardware_workflow import read_bytes


def example():
    from .link_profiles import example_manifest
    from .link_runtime import DeviceRuntime
    from .link_store import Journal
    from .link_gateway import capture
    from .core.covariance import create_covariance_artifact
    device=example_manifest("GaugeLink")
    device["channels"]=[{"id":"CH0","transducer_id":"synthetic.force-transducer",
        "quantity":"force","unit":"N","frame":"synthetic.instrument.frame","encoding":"opaque.v1"}]
    device["clock"].update(id="link.host.monotonic_ns",tick_hz=1000000000)
    with tempfile.TemporaryDirectory() as tmp:
        with Journal(Path(tmp)/"source.sqlite",create=True) as journal:
            runtime=DeviceRuntime(device,journal,origin="synthetic",boot_id="synthetic.scalar.boot")
            for i,raw in enumerate((b"1.000 N\r\n",b"2.000 N\r",b"\n3.000 N\r\n")):
                runtime.emit("CH0",raw,tick=1000000+i*1000000)
            captured=capture(journal,expected_manifest=device,boot_id=runtime.boot_id)
    declaration={"schema":"ciw.link-calibration-declaration.v1","calibration_id":"synthetic.affine.1",
        "manifest_ref":c.content_identity(device),"channel_id":"CH0","unit":"N",
        "valid_from":"2026-01-01T00:00:00Z","valid_until":"2027-01-01T00:00:00Z",
        "certificate":c.packed(b"Synthetic calibration declaration. Not a certificate.",32768),"reference_standard_ref":None}
    request={"schema":study.SCHEMA,"capture":captured,"channel_id":"CH0",
        "calibration":{"gain":1.02,"offset":-0.01,"input_range":[0,10],"declaration":declaration,"joint_covariance":None},
        "model":{"reference_kind":"declared_exact","expected_mean":2.0,"residual_limit":0.1}}
    decoded=study.decode(request)
    matrix=[[0.0004,0.0001,0.0001,0,0],[0.0001,0.0004,0.0001,0,0],
        [0.0001,0.0001,0.0004,0,0],[0,0,0,0.0001,0.0001],[0,0,0,0.0001,0.0009]]
    request["calibration"]["joint_covariance"]=create_covariance_artifact(matrix=matrix,
        quantity_ids=[r["id"]+":input" for r in decoded["rows"]]+["gain","offset"],
        units=["N","N","N","1","N"],frame=device["channels"][0]["frame"],reference_values=[1.0,2.0,3.0,1.02,-0.01],
        method="authored_synthetic_joint_covariance",basis={"kind":"parameter","id":study.calibration_basis(request)},
        provenance={"provider":"synthetic.example","source_evidence_ids":[c.content_identity(captured)],"source_covariance_ids":[]},
        assumptions=["All entries are authored synthetic fixture values, not a calibration uncertainty budget"])
    return request


def main(argv=None):
    parser=argparse.ArgumentParser(prog="net link study",description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("example");p.add_argument("--output",type=Path,required=True)
    p=sub.add_parser("run");p.add_argument("request",type=Path);p.add_argument("--output-dir",type=Path,required=True)
    for name in ("inspect","replay","export","report","mcp"):
        p=sub.add_parser(name);p.add_argument("directory",type=Path)
        p.add_argument("--request-ref", help="Optional externally retained exact request-byte reference; no authority grant")
        if name in {"replay","export"}:p.add_argument("--output-dir",type=Path,required=True)
        if name=="report":p.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=="example":
            request=example();save_new(args.output,request);value={"status":"CREATED_SYNTHETIC","path":str(args.output)}
        elif args.command=="run":
            raw=read_bytes(args.request,limit=study.MAX_BYTES);value=io.run(c.loads(raw,study.MAX_BYTES),args.output_dir,raw=raw)
        elif args.command=="inspect":value=io.inspect(args.directory,expected_request_ref=args.request_ref)
        elif args.command=="replay":value=io.replay(args.directory,args.output_dir,expected_request_ref=args.request_ref)
        elif args.command=="export":value=io.export(args.directory,args.output_dir,expected_request_ref=args.request_ref)
        elif args.command=="report":value=io.report(args.directory,args.output,expected_request_ref=args.request_ref)
        else:
            from .agent_mcp import serve
            with io.agent(args.directory,expected_request_ref=args.request_ref) as host:return serve(host,sys.stdin.buffer,sys.stdout.buffer)
        print(json.dumps(value,indent=2,allow_nan=False))
        return 2 if value.get("status")=="incomplete" else 0
    except (ValueError,KeyError,TypeError,OSError,RuntimeError,OverflowError,RecursionError) as exc:
        print(json.dumps({"status":"REFUSED","reason":str(exc),"physical_qualification":"not_established"}),file=sys.stderr)
        return 2

if __name__=="__main__":raise SystemExit(main())
