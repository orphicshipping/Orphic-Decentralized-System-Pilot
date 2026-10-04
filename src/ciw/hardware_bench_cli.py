"""Local bench imports and maintenance planning; no destructive execution."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import hardware_bench as bench
from .control_contracts import save_new


def _read(path):
    with path.open('rb') as stream: raw = stream.read(bench.MAX_RAW + 1)
    if len(raw) > bench.MAX_RAW: raise ValueError('Input exceeds 32768-byte limit')
    return raw


def _record(path):
    # Retained evidence includes base64 and may be larger than the original raw input.
    with path.open('rb') as stream: raw = stream.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024: raise ValueError('Record exceeds 256 KiB limit')
    from .session import loads_json
    return loads_json(raw.decode('utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='net bench', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    example = commands.add_parser('example', help='Write an offline public-fixture-derived demonstration')
    example.add_argument('--case', choices=['public', 'pass', 'fail', 'missing', 'stale'], default='pass')
    example.add_argument('--output-dir', type=Path, required=True)
    inventory = commands.add_parser('inventory', help='Read a bounded local system inventory; identifiers stay in the output file')
    inventory.add_argument('--asset-id', required=True); inventory.add_argument('--operator', required=True)
    inventory.add_argument('--output', type=Path, required=True)
    imported = commands.add_parser('import', help='Import exact SMART or strict diagnostic JSON bytes')
    imported.add_argument('raw', type=Path)
    imported.add_argument('--format', choices=['smartctl-json', 'diagnostic-json'], default='smartctl-json')
    imported.add_argument('--captured-at', required=True); imported.add_argument('--procedure-id', required=True)
    imported.add_argument('--duration-s', type=float, default=0)
    capture = commands.add_parser('smart', help='Read an explicitly selected device with installed trusted smartctl; no self-test')
    capture.add_argument('--device', required=True)
    capture.add_argument('--evidence-output', type=Path, help='Optional separate diagnostic evidence for hardware assessment')
    for command in (imported, capture):
        command.add_argument('--configuration', type=Path, required=True)
        command.add_argument('--slot', required=True); command.add_argument('--operator', required=True)
        command.add_argument('--output', type=Path, required=True)
    request = commands.add_parser('request', help='Map retained evidence into an existing hardware assessment request')
    request.add_argument('--configuration', type=Path, required=True); request.add_argument('--plan', type=Path, required=True)
    request.add_argument('--evidence', type=Path, action='append', required=True)
    request.add_argument('--mappings', type=Path, required=True); request.add_argument('--output', type=Path, required=True)
    plan = commands.add_parser('plan', help='Create a data-only sanitization/provisioning plan with pending preconditions')
    plan.add_argument('--configuration', type=Path, required=True); plan.add_argument('--expected-configuration', type=Path, required=True)
    plan.add_argument('--slot', required=True); plan.add_argument('--action', choices=['sanitize', 'provision'], required=True)
    plan.add_argument('--procedure-id', required=True); plan.add_argument('--operator', required=True)
    plan.add_argument('--source-kind', choices=['operator_import', 'synthetic'], default='operator_import')
    plan.add_argument('--output', type=Path, required=True)
    receipt = commands.add_parser('import-maintenance', help='Check an imported completion declaration against a retained plan')
    receipt.add_argument('raw', type=Path); receipt.add_argument('--plan', type=Path, required=True)
    receipt.add_argument('--output', type=Path, required=True)
    verify = commands.add_parser('verify', help='Recompute retained record bindings without invoking collectors')
    verify.add_argument('record', type=Path); verify.add_argument('--configuration', type=Path)
    verify.add_argument('--plan', type=Path)
    args = parser.parse_args(argv)
    try:
        if getattr(args, 'output', None) is not None and args.output.exists():
            raise ValueError('Output already exists; use a new evidence path')
        if getattr(args, 'evidence_output', None) is not None:
            if args.evidence_output.exists() or args.evidence_output.resolve() == args.output.resolve():
                raise ValueError('Evidence output must be a distinct new path')
        if args.command == 'example':
            bundle = bench.example_bundle(args.case)
            args.output_dir.mkdir(parents=True, exist_ok=False)
            for name in ('configuration', 'evidence', 'plan', 'mappings', 'request'):
                save_new(args.output_dir / (name + '.json'), bundle[name])
            print(json.dumps({'status': 'created', 'source_kind': bundle['evidence']['source_kind'],
                              'assessment_source_kind': 'synthetic', 'directory': str(args.output_dir)}))
            return 0
        if args.command == 'inventory':
            record = bench.capture_inventory(args.asset_id, args.operator)
        elif args.command == 'import':
            record = bench.import_diagnostic(_read(args.raw), _record(args.configuration), args.slot,
                format=args.format, operator=args.operator, captured_at=args.captured_at,
                procedure_id=args.procedure_id, duration_s=args.duration_s)
        elif args.command == 'smart':
            record = bench.capture_smart(_record(args.configuration), args.slot, args.device, args.operator)
        elif args.command == 'request':
            record = bench.assessment_request(_record(args.configuration), _record(args.plan),
                [_record(path) for path in args.evidence], _record(args.mappings))
        elif args.command == 'plan':
            record = bench.maintenance_plan(_record(args.configuration), args.slot, action=args.action,
                expected_configuration=_record(args.expected_configuration), procedure_id=args.procedure_id, operator=args.operator,
                source_kind=args.source_kind)
        elif args.command == 'import-maintenance':
            record = bench.import_maintenance_result(_read(args.raw), _record(args.plan))
        else:
            record = _record(args.record)
            if record.get('schema') == bench.EVIDENCE_SCHEMA:
                if args.configuration is None: raise ValueError('Evidence verification requires --configuration')
                bench.validate_evidence(record, _record(args.configuration))
            elif record.get('schema') == bench.MAINTENANCE_SCHEMA:
                bench.validate_maintenance_plan(record)
            elif record.get('schema') == bench.RECEIPT_SCHEMA:
                if args.plan is None: raise ValueError('Maintenance evidence verification requires --plan')
                bench.validate_maintenance_result(record, _record(args.plan))
            else: raise ValueError('Unsupported retained record schema')
            print(json.dumps({'status': 'bindings_consistent', 'record_digest': record['record_digest'],
                              'source_authenticity': 'not_established'}))
            return 0
        save_new(args.output, record)
        if args.command == 'smart' and args.evidence_output is not None and record['evidence'] is not None:
            save_new(args.evidence_output, record['evidence'])
        print(json.dumps({'status': record.get('status', 'created'), 'schema': record['schema'],
                          'path': str(args.output), 'record_digest': record.get('record_digest'),
                          'sensitive_identifiers': 'local_only'}))
        return 3 if record.get('status') == 'unavailable' else 1 if record.get('status') == 'error' else 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as error:
        print(json.dumps({'status': 'REFUSED', 'reason': str(error)}), file=sys.stderr)
        return 1
