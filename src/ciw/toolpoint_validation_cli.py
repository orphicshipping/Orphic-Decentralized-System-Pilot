"""Retrospective detector validation against separately declared inspection outcomes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .manufacturing_contracts import load, save_new
from .toolpoint_validation import evaluate, verify


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='net polymer qc detector', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('example', 'demo'):
        p = commands.add_parser(name)
        p.add_argument('--profile', choices=('native', 'legacy'), default='native')
        p.add_argument('--output', type=Path, required=True)
    for name in ('evaluate', 'verify', 'summary', 'evaluate-store'):
        p = commands.add_parser(name)
        p.add_argument('input', type=Path)
        p.add_argument('--output', type=Path, required=name in ('evaluate', 'evaluate-store'))
        p.add_argument('--expected-protocol-ref')
        p.add_argument('--expected-manifest-ref')
        if name == 'evaluate-store':
            p.add_argument('--database', type=Path, required=True)
            p.add_argument('--bindings', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in ('example', 'demo'):
            from .toolpoint_validation_example import example_request
            request = example_request(profile=args.profile)
            result = request if args.command == 'example' else evaluate(request)
        else:
            source = load(args.input)
            anchors = {'expected_protocol_ref': args.expected_protocol_ref,
                       'expected_manifest_ref': args.expected_manifest_ref}
            if args.command == 'evaluate-store':
                from .toolpoint_validation_store import resolve
                source = resolve(source, args.database, load(args.bindings))
            if args.command in ('evaluate', 'evaluate-store'):
                result = evaluate(source, **anchors)
            else:
                checked = verify(source, **anchors)
                result = checked if args.command == 'verify' else {
                    'schema': 'ciw.toolpoint-validation-summary.v1',
                    'verification': checked, **{k: source['result'][k] for k in (
                        'status', 'criteria_status', 'reasons', 'part_metrics', 'group_inference', 'authority')}}
        if args.output is not None:
            save_new(args.output, result)
        payload = result.get('result', result)
        if args.command in ('demo', 'evaluate', 'evaluate-store'):
            display = {k: payload[k] for k in ('status', 'criteria_status', 'reasons', 'part_metrics', 'authority')}
            display['output'] = str(args.output)
        elif args.command == 'example':
            display = {'status': 'CREATED', 'output': str(args.output)}
        else:
            display = result
        print(json.dumps(display, indent=2, allow_nan=False))
        status = payload.get('status')
        return 0 if args.command in ('example', 'verify') or status == 'CRITERIA_MET_FOR_REVIEW' else 2
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError, ImportError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'REFUSE', 'reason': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
