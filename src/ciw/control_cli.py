"""NCP MCP control: discover, compile, admit, submit, inspect and compare."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
from .agent_mcp import INSTRUMENTS, from_profile, demo_config, polymer_config, temperature_config, leakage_config


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ncp control", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo-config", help="Prepare synthetic inputs without execution")
    demo.add_argument("--instrument", choices=["builtin", "polymer", "temperature", "leakage", "link-scalar"], default="builtin")
    demo.add_argument("--output-dir", type=Path, required=True)
    prepare = commands.add_parser("prepare-link", help="Bind retained Link request bytes without acquisition")
    prepare.add_argument("--request", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    serve = commands.add_parser("serve", help="Serve modern and legacy MCP using the optional SDK")
    serve.add_argument("--profile", type=Path, required=True)
    serve.add_argument("--instrument", choices=INSTRUMENTS, default="builtin")
    serve.add_argument("--provider-checkout", type=Path)
    serve.add_argument("--python", type=Path)
    serve.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
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
        backend = None
        if args.instrument == "leakage":
            if args.provider_checkout is None or not args.provider_checkout.is_absolute():
                raise ValueError("Leakage requires an absolute host-selected provider checkout")
            if args.python is not None and not args.python.is_absolute():
                raise ValueError("Provider interpreter must be absolute")
            from .leakage_native import NativeLeakageBackend
            backend = NativeLeakageBackend(args.provider_checkout, python=args.python)
        elif args.provider_checkout is not None or args.python is not None:
            raise ValueError("Provider options require leakage")
        with from_profile(args.profile, instrument=args.instrument, leakage_backend=backend, resume=args.resume) as owner:
            with ControlHost(owner, resume=args.resume) as host:
                return serve_sdk(host)
    except (ImportError, OSError, ValueError, TypeError, KeyError) as exc:
        print("NCP control startup refused: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
