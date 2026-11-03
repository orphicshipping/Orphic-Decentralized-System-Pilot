"""Explicit host launch binding for Rust numerical operations; no model-selected pins.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
from pathlib import Path
from ciw.agent_mcp import from_profile
from ciw.control_gateway import ControlHost
from ciw.mcp_server import serve_sdk
from ciw.process_policy import ProcessPolicy
from ciw.rust_numerical import RustNumericalBackend
from ciw.session import loads_json


def backend_from_binding(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Native host binding exceeds budget')
    value = loads_json(raw.decode('utf-8'))
    if (type(value) is not dict or set(value) != {'schema', 'binary', 'binary_sha256', 'lapack_libraries', 'process_policy'}
            or value['schema'] != 'odsp.rust-numerical-host-binding.v1'
            or type(value['lapack_libraries']) is not dict):
        raise ValueError('Invalid native host binding')
    return RustNumericalBackend(Path(value['binary']), value['binary_sha256'],
        process_policy=ProcessPolicy(**value['process_policy']),
        lapack_libraries={Path(path): digest for path, digest in value['lapack_libraries'].items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--binding', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    backend = backend_from_binding(args.binding)
    with from_profile(args.profile, resume=args.resume, numerical_backend=backend) as owner:
        with ControlHost(owner, resume=args.resume) as host:
            return serve_sdk(host)


if __name__ == '__main__':
    raise SystemExit(main())
