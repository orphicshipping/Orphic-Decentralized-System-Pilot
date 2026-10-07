"""NET device declarations in the existing Link journal. Never connect or enroll."""
import argparse
import json
import sys

from . import link_devices as devices
from .hardware_workflow import read_bytes
from .link_store import Journal, safe_path


def main(argv=None):
    parser=argparse.ArgumentParser(prog="net device",description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    reg=sub.add_parser("register");reg.add_argument("manifest");reg.add_argument("--journal",required=True)
    reg.add_argument("--create-journal",action="store_true");reg.add_argument("--approve-declaration",action="store_true")
    listing=sub.add_parser("list");listing.add_argument("--journal",required=True)
    inspect=sub.add_parser("inspect");inspect.add_argument("device_id");inspect.add_argument("--manifest-ref");inspect.add_argument("--journal",required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=="register":
            # Consent and manifest validation precede any journal creation.
            if not args.approve_declaration:raise PermissionError("Explicit --approve-declaration is required")
            raw=read_bytes(safe_path(args.manifest),limit=65536)
            from . import link_contract as c
            c.manifest(c.loads(raw,65536))
            with Journal(args.journal,create=args.create_journal) as journal:
                result=devices.register(journal,raw,approved=True)
        else:
            with Journal(args.journal,read_only=True) as journal:
                result={"devices":devices.list_devices(journal)} if args.command=="list" else devices.inspect_device(journal,args.device_id,manifest_ref=args.manifest_ref)
        print(json.dumps(result,allow_nan=False,indent=2));return 0
    except (OSError,ValueError,TypeError,KeyError,RuntimeError) as exc:
        print(json.dumps({"status":"REFUSE","reason":str(exc)}),file=sys.stderr);return 2


if __name__=="__main__":raise SystemExit(main())
