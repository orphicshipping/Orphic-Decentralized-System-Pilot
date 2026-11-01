"""NCP MCP control: discover, compile, admit, submit, inspect and compare."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys
from .agent_mcp import INSTRUMENTS, from_profile, native_bindings, demo_config, polymer_config, temperature_config, leakage_config


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ncp control", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo-config", help="Prepare synthetic inputs without execution")
    demo.add_argument("--instrument", choices=["builtin", "polymer", "temperature", "leakage", "link-scalar"], default="builtin")
    demo.add_argument("--output-dir", type=Path, required=True)
    prepare = commands.add_parser("prepare-link", help="Bind retained Link request bytes without acquisition")
    prepare.add_argument("--request", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    bim = commands.add_parser("prepare-bim", help="Freeze an existing bounded BIM inspection without execution")
    bim.add_argument("--inspection", type=Path, required=True)
    bim.add_argument("--output-dir", type=Path, required=True)
    serve = commands.add_parser("serve", help="Serve modern and legacy MCP using the optional SDK")
    serve.add_argument("--profile", type=Path, required=True)
    serve.add_argument("--instrument", choices=INSTRUMENTS, default="builtin")
    serve.add_argument("--provider-checkout", type=Path)
    serve.add_argument("--provider-revision", help="Full original CSE revision; BIM clearance only")
    serve.add_argument("--python", type=Path)
    serve.add_argument("--host-manifest", type=Path, help="Operator binding manifest. Not a tool argument.")
    serve.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-bim":
            from .metrology_clearance import prepare, MAX_BYTES
            from .hardware_workflow import read_bytes
            print(prepare(read_bytes(args.inspection, limit=MAX_BYTES), args.output_dir))
            return 0
        if args.command == "prepare-link":
            from .link_study_agent import prepare
            from .hardware_workflow import read_bytes
            from .link_study import MAX_BYTES
            print(prepare(read_bytes(args.request, limit=MAX_BYTES), args.output_dir))
            return 0
        if args.command == "demo-config":
            from .link_study_agent import demo_config as link_config
            factory = {"builtin": demo_config, "polymer": polymer_config,
                       "temperature": temperature_config, "leakage": leakage_config,
                       "link-scalar": link_config}[args.instrument]
            print(factory(args.output_dir))
            return 0
        import mcp  # Fail before creating a host if the optional SDK is absent.
        from .mcp_server import serve_sdk
        from .control_gateway import ControlHost
        if args.host_manifest is not None:
            os.environ["NCP_HOST_MANIFEST"] = str(args.host_manifest.resolve())
        bindings = native_bindings(args.instrument, checkout=args.provider_checkout,
                                   revision=args.provider_revision, python=args.python)
        with from_profile(args.profile, instrument=args.instrument, resume=args.resume, **bindings) as owner:
            with ControlHost(owner, resume=args.resume) as host:
                return serve_sdk(host)
    except (ImportError, OSError, ValueError, TypeError, KeyError) as exc:
        print("NCP control startup refused: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
