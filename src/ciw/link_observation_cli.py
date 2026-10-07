"""Retained Link measurement views, recovery, export and existing MCP transport."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

from . import link_measurements as m, link_measurement_io as io, link_study_io
from .link_store import Journal, safe_path


def main(argv=None):
    parser=argparse.ArgumentParser(prog="net observation",description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    ingest=sub.add_parser("ingest");ingest.add_argument("investigation");ingest.add_argument("--output",required=True)
    listing=sub.add_parser("list");listing.add_argument("bundle");listing.add_argument("--device-id");listing.add_argument("--channel-id");listing.add_argument("--stage",choices=["indicated","corrected"])
    inspect=sub.add_parser("inspect");inspect.add_argument("bundle");inspect.add_argument("measurement_id")
    for name in ("restore","replay","export","report"):
        action=sub.add_parser(name);action.add_argument("bundle");action.add_argument("--output",required=True)
        if name=="export":action.add_argument("--parquet",action="store_true")
    review=sub.add_parser("review-export");review.add_argument("directory")
    recovery=sub.add_parser("restore-export");recovery.add_argument("directory");recovery.add_argument("--output",required=True)
    attach=sub.add_parser("workbench");attach.add_argument("bundle");attach.add_argument("--output",required=True)
    attach.add_argument("--dataset-id",required=True);attach.add_argument("--recorded-at",required=True)
    mcp=sub.add_parser("mcp");mcp.add_argument("bundle");mcp.add_argument("--journal")
    args=parser.parse_args(argv)
    try:
        if args.command=="ingest":result=io.save(m.build(args.investigation),args.output)
        elif args.command=="restore-export":
            value=io.review_objects(args.directory,verify_parquet=False)
            result=io.restore(value,args.output)
            result["parquet_semantic_review"]="NOT_PERFORMED_PRIMARY_EVIDENCE_RECOVERED"
        elif args.command=="review-export":
            value=io.review_objects(args.directory);result={"status":"CHECKED","bundle_id":value["bundle_id"],"measurements":len(value["measurements"])}
        else:
            value=io.load(args.bundle)
            if args.command=="list":result={"measurements":m.query(value,device_id=args.device_id,channel_id=args.channel_id,stage=args.stage)}
            elif args.command=="inspect":
                rows=m.query(value,measurement_id=args.measurement_id)
                if len(rows)!=1:raise ValueError("Measurement is not in this bundle")
                result={"measurement":rows[0]}
            elif args.command=="restore":result=io.restore(value,args.output)
            elif args.command=="export":result=io.export_objects(value,args.output,parquet=args.parquet)
            elif args.command=="mcp":
                from .agent_mcp import serve
                from .link_measurement_agent import MeasurementHost
                if args.journal:
                    with Journal(args.journal,read_only=True) as journal:host=MeasurementHost(value,journal)
                else:host=MeasurementHost(value)
                return serve(host,sys.stdin.buffer,sys.stdout.buffer)
            else:
                with tempfile.TemporaryDirectory(prefix="net-observation-source-") as temp:
                    source=Path(temp)/"original";io.restore(value,source)
                    if args.command=="replay":result=link_study_io.replay(source,args.output)
                    elif args.command=="report":result=link_study_io.report(source,args.output)
                    else:
                        _,_,session,_=link_study_io.read(source)
                        result=io.attach_workbench(value,session,dataset_id=args.dataset_id,recorded_at=args.recorded_at)
                        output=safe_path(args.output)
                        if output.exists():raise FileExistsError(output)
                        # Stage the saved workspace and publish create-only.
                        stage=Path(temp)/"attached.json";session.save_workspace(stage)
                        io._write(output,stage.read_bytes())
        print(json.dumps(result,allow_nan=False,indent=2));return 0
    except (OSError,ValueError,TypeError,KeyError,RuntimeError) as exc:
        print(json.dumps({"status":"REFUSE","reason":str(exc)}),file=sys.stderr);return 2


if __name__=="__main__":raise SystemExit(main())
