"""Inspect selected retained scalar inputs before scientific computation.

This view contains existing Link records and declared analysis inputs. It does
not decode the scalar stream, apply calibration, evaluate readiness or create
an execution. Hash selection establishes byte identity, not physical origin.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from . import engineering_thread as engineering, link_contract as c, link_study as study
from .hardware_workflow import read_bytes
from .link_store import safe_path


def inspect_source(raw: bytes, *, expected_request_ref: str, host_profile: str) -> dict:
    """Return a detached, non-executable view using the native producer contract.

    Envelope faults refuse. Opaque scalar bytes and quality flags remain visible
    even when the separately authorized scientific graph would refuse them.
    Unknown context and timestamps remain null; covariance is only a declaration
    here, not a newly evaluated uncertainty budget.
    """
    if host_profile != 'scalar':
        raise ValueError('Source inspection supports only the existing scalar profile')
    c.content_ref(expected_request_ref)
    value = c.loads(raw, study.MAX_BYTES)
    if c.bytes_ref(raw) != expected_request_ref:
        raise ValueError('Source differs from externally selected request bytes')
    request = study.validate_request(value)
    capture_review = study.gateway.validate_capture(request['capture'])
    return {
        'schema': 'ciw.engineering-source-review.v1',
        'status': 'SOURCE_INSPECTED_NOT_EXECUTED',
        'profile': host_profile, 'request_ref': expected_request_ref,
        'request': request, 'capture_review': capture_review,
        'origins': sorted({row['origin'] for row in capture_review['observations']}),
        'computation_readiness': 'NOT_EVALUATED',
        'authorizes_execution': False, 'new_scientific_executions': 0,
        'scope': deepcopy(engineering.SCOPE),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog='net production engineering source', description=__doc__)
    parser.add_argument('request', type=Path)
    parser.add_argument('--profile', choices=('scalar',), required=True)
    parser.add_argument('--request-ref', required=True)
    args = parser.parse_args(argv)
    try:
        data = read_bytes(safe_path(args.request), limit=study.MAX_BYTES)
        result = inspect_source(data, expected_request_ref=args.request_ref, host_profile=args.profile)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0  # Source inspection succeeded, not analysis or empirical admission.
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, OverflowError, RecursionError) as exc:
        print(json.dumps({'status': 'REFUSED', 'reason': str(exc), 'scope': engineering.SCOPE}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
