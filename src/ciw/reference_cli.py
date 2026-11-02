"""ODRP read-only host launcher; existing ncp knowledge/evidence commands remain.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
import json
from pathlib import Path
from .reference_profile import open_reference, reference_tools

def main(argv=None):
    p = argparse.ArgumentParser(prog='ncp reference', description=__doc__)
    p.add_argument('command', choices=('status', 'tools', 'serve'))
    p.add_argument('--catalog', type=Path, required=True)
    p.add_argument('--catalog-ref', required=True)
    p.add_argument('--evidence', type=Path)
    p.add_argument('--evidence-ref')
    a = p.parse_args(argv)
    try:
        host = open_reference(catalog_path=a.catalog, catalog_ref=a.catalog_ref,
                              evidence_path=a.evidence, evidence_ref=a.evidence_ref)
        if a.command == 'serve':
            import anyio
            from .reference_mcp import serve
            anyio.run(serve, host)
        else:
            value = reference_tools(host) if a.command == 'tools' else host.call('ncp_system_status', {})
            print(json.dumps(value, ensure_ascii=True, allow_nan=False))
        return 0
    except (ValueError, OSError, TypeError, KeyError):
        # Never expose source paths or native exception details to clients.
        print(json.dumps({'status': 'refused', 'reason': 'reference_binding_refused'}))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
