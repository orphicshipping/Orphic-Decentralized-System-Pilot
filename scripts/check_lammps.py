#!/usr/bin/env python3
"""NET's bounded LAMMPS tool: example / run / verify / replay / qualify.

Native physics remains in LAMMPS. No binary discovery or automatic installation.
No operation registry, data bridge or evidence-admission path is introduced.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4


def _module():
    root=Path(__file__).resolve().parents[1]/"instruments/execution/compute-runtime"
    sys.path.insert(0,str(root))
    from execution import lammps
    return lammps


def _write(path,value):
    with path.open("x",encoding="utf-8") as stream:
        json.dump(value,stream,indent=2,allow_nan=False);stream.write("\n")


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("example");p.add_argument("--output",required=True,type=Path)
    for name in ("run","qualify","replay"):
        p=sub.add_parser(name)
        p.add_argument("--lmp",type=Path)
        p.add_argument("--expected-sha256")
        p.add_argument("--output",required=True,type=Path)
        if name=="run": p.add_argument("--input",required=True,type=Path)
        if name=="replay":
            p.add_argument("--capture",required=True,type=Path)
            p.add_argument("--manifest-sha256",required=True)
    p=sub.add_parser("verify");p.add_argument("--capture",required=True,type=Path)
    p.add_argument("--manifest-sha256",required=True)
    args=parser.parse_args(argv)
    if args.command=="example":
        mod=_module();_write(args.output,mod.example());return 0
    if args.command=="verify":
        try:
            checked=_module().verify_capture(args.capture,args.manifest_sha256)
            print(json.dumps(checked,allow_nan=False))
            return 0 if checked["comparison"]["status"]=="PASS" else 1
        except Exception as exc:
            print(json.dumps({"status":"FAIL","error":str(exc)}));return 1
    root=Path(__file__).resolve().parents[1]
    dest=args.output.absolute()
    if dest.resolve().is_relative_to(root): parser.error("output must be outside the NET checkout")
    dest.mkdir(parents=True,exist_ok=False)
    report={"schema":"net.lammps-gate.v1","attempt_id":"attempt-"+uuid4().hex,
        "created_at":datetime.now(timezone.utc).isoformat(),"status":"NOT_RUN","cases":[],
        "runtime":None,"physical_validation":"not_performed","equipment_authority":False,
        "qualification_scope":"bounded_synthetic_LJ_CPU_profile_only_not_full_NET_Session",
        "uncertainty":"unavailable"}
    code=2
    try:
        if args.lmp is None or args.expected_sha256 is None:
            report["blocker"]="supply both --lmp and --expected-sha256; nothing was executed"
        elif not args.lmp.is_absolute() or not args.lmp.is_file():
            report["blocker"]="explicit absolute LAMMPS executable is unavailable"
        else:
            mod=_module()
            # Validate a supplied scientific input before launching even a help probe.
            if args.command=="run":
                inputs=[mod.validate_input(mod.load(mod.read_regular(args.input)))]
            elif args.command=="qualify":
                inputs=[mod.example(r) for r in (3.0,4.0,8.0,10.0,4.0)]
                inputs.append(mod.validate_input({"schema":mod.INPUT_SCHEMA,"origin":"synthetic",
                    "configuration_id":"lj-triangle","frame_id":"cartesian-lab",
                    "atom_ids":["Ar-a","Ar-b","Ar-c"],
                    "positions_A":[[0.,0.,0.],[4.,0.,0.],[2.,3.5,0.]]}))
            else:
                old=mod.verify_capture(args.capture,args.manifest_sha256)
                if old["comparison"]["status"]!="PASS": raise ValueError("cannot replay a failed comparison")
                original=mod.load(mod.read_regular(args.capture/"manifest.json"))
                if args.expected_sha256!=original["runtime"]["executable_sha256"]:
                    raise ValueError("replay requires the original binary; changed providers need a new run")
                report["replay_of"]={"manifest_sha256":args.manifest_sha256,"capture_id":original["capture_id"]}
                inputs=[mod.validate_input(mod.load(mod.read_regular(args.capture/"input.json")))]
            version=mod.probe(args.lmp,args.expected_sha256)
            report["runtime"]={"version_line":version,"executable_sha256":args.expected_sha256}
            for i,value in enumerate(inputs):
                capture=dest/f"case-{i}"
                spec=mod.specification(value,version,args.expected_sha256)
                row={"case_id":"case-"+uuid4().hex,"directory":capture.name,"specification_identity":spec.identity()}
                report["cases"].append(row)
                result=mod.run_lammps_specification(spec,args.lmp,artifact_directory=capture)
                anchor=mod.digest(mod.read_regular(capture/"manifest.json"))
                row.update(status=result.status,manifest_sha256=anchor,computation_identity=result.computation_identity)
                if result.status!="completed": raise RuntimeError(result.detail)
                check=mod.verify_capture(capture,anchor)
                row["check"]=check
                _write(capture/"numerical-check.json",check)
                if check["comparison"]["status"]!="PASS": raise ValueError("independent numerical comparison failed")
            if args.command=="qualify":
                a,b=report["cases"][1],report["cases"][4]
                if a["computation_identity"]!=b["computation_identity"]:
                    raise ValueError("same-host repeated calculation changed the numerical result")
            if args.command=="replay" and report["cases"][0]["computation_identity"]!=old["computation_identity"]:
                raise ValueError("replay changed the numerical result")
            report["status"]="PASS";code=0
    except Exception as exc:
        report.update(status="FAIL",error={"type":type(exc).__name__,"message":str(exc)});code=1
    finally:
        report["finished_at"]=datetime.now(timezone.utc).isoformat()
        _write(dest/"gate.json",report)
    print(json.dumps({"status":report["status"],"report":str(dest/"gate.json")}))
    return code


if __name__=="__main__":
    raise SystemExit(main())
