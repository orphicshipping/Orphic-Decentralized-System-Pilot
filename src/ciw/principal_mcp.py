"""Trusted launcher for current-policy principal-bound MCP stdio.

Files, identity, native providers and deployment retention root are launcher
inputs. They are never MCP arguments. Use an installed wheel with Python -I.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main(argv=None):
    from .agent_mcp import INSTRUMENTS, from_profile, native_bindings
    from .control_gateway import ControlHost
    from .mcp_server import serve_sdk
    from .principal_policy import PrincipalPolicy, owner_for

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--instrument", choices=INSTRUMENTS, default="builtin")
    parser.add_argument("--provider-checkout", type=Path)
    parser.add_argument("--provider-revision")
    parser.add_argument("--python", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--catalog-ref")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--evidence-ref")
    args = parser.parse_args(argv)
    try:
        policy = PrincipalPolicy.from_file(args.policy)
        policy.check_principal(args.principal)
        if ((args.catalog is None) != (args.catalog_ref is None) or
                (args.evidence is None) != (args.evidence_ref is None) or
                args.evidence is not None and args.catalog is None):
            raise ValueError("reference_binding_required")
        reference = None
        if args.catalog is not None:
            from .reference_profile import open_reference
            reference = open_reference(catalog_path=args.catalog, catalog_ref=args.catalog_ref,
                                       evidence_path=args.evidence, evidence_ref=args.evidence_ref)
        bindings = native_bindings(args.instrument, checkout=args.provider_checkout,
                                   revision=args.provider_revision, python=args.python)
        with from_profile(args.profile, instrument=args.instrument, resume=args.resume, **bindings) as owner:
            with ControlHost(owner, resume=args.resume, retained_owner=owner_for(args.principal)) as control:
                facade = policy.facade(args.principal, control=control, reference=reference)
                return serve_sdk(facade, name="odsp-principal")
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        # No policy paths, token values or provider diagnostics cross stdio.
        print("ODSP principal startup refused", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
