"""Operator-bound material execution on the existing NET Session lifecycle."""
import argparse
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
import json
import tempfile

from . import materials_workflow as mw, materials_bench as mb
from .control_contracts import load, save_new
from .operations.runner import digest

PROFILES = ('thermochemistry', 'viscoelasticity', 'population', 'empirical-rheology',
            'complex-viscosity', 'joint-residual')


def example(profile):
    if profile not in PROFILES: raise ValueError('Unknown material profile')
    raw = files('ciw').joinpath('operator_examples', 'materials-'+profile+'.json').read_bytes()
    return json.loads(raw)


def summary(session):
    return {'schema': 'ciw.materials-session-summary.v1', 'evidence_id': session.run['evidence_id'],
            'request_ref': digest(mw.source_request(session.run)),
            'executions': [{'operation_id': e['operation_id'], 'execution_id': e['execution_id'],
                            'result_id': e['result_id'], 'status': e['status'],
                            **({'refusal': e['refusal']} if 'refusal' in e else {})}
                           for e in session.executions.values()],
            'results': [{'result_id': r['result_id'], 'operation_id': r['operation_id'],
                         'record_digest': r['record_digest'], 'verification_status': r['verification_status']}
                        for r in session.results.values()],
            'authority': deepcopy(mw.AUTHORITY)}


def run(request, output):
    mw.normalize(request)
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    save_new(output/'request.json', request)
    session = mw.create_session(request, output/'records')
    try:
        first = mw.execute(session, mw.EVALUATE, {})
        if first['status'] == 'completed':
            candidate = first['result']
            mw.execute(session, mw.INSPECT, {'candidate': candidate})
            mw.execute(session, mw.VERIFY, {'candidate': candidate})
    finally:
        session.save_workspace(output/'workspace.json')
    result = summary(session)
    save_new(output/'summary.json', result)
    return result


def inspect(path):
    with tempfile.TemporaryDirectory(prefix='net-materials-inspect-') as temporary:
        session = mw.reopen(Path(path), Path(temporary), bind=False)
        result = summary(session)
        result.update(scope='offline-retained-inspection', numerical_execution=False)
        return result


def extend(path, output, action):
    if action not in ('verify','replay'): raise ValueError('Unsupported extension action')
    # Resolve the original before any new output is published. Reopen never changes it.
    with tempfile.TemporaryDirectory(prefix='net-materials-preflight-') as temporary:
        mw.reopen(Path(path), Path(temporary), bind=False)
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    session = mw.reopen(Path(path), output/'records', bind=True)
    candidate = next((r for r in reversed(list(session.results.values())) if r['operation_id'] == mw.EVALUATE), None)
    if candidate is None: raise ValueError('No retained material evaluation to extend')
    before = set(session.executions)
    try:
        mw.execute(session, mw.VERIFY if action == 'verify' else mw.REPLAY, {'candidate': candidate})
    finally:
        session.save_workspace(output/'workspace.json')
    result = summary(session)
    result['new_execution_ids'] = sorted(set(session.executions)-before)
    result['parent_workspace_sha256'] = __import__('hashlib').sha256(Path(path).read_bytes()).hexdigest()
    save_new(output/'summary.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog='net materials', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('inventory')
    p = sub.add_parser('example'); p.add_argument('profile', choices=PROFILES); p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('run'); p.add_argument('request', type=Path); p.add_argument('--output-dir', type=Path, required=True)
    p = sub.add_parser('inspect'); p.add_argument('workspace', type=Path)
    for action in ('verify','replay'):
        p = sub.add_parser(action); p.add_argument('workspace', type=Path); p.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inventory':
            result = {'profiles': list(PROFILES), 'operations': list(mw.IDS),
                      'session_execution': True, 'evidence_mutation': False,
                      'calibration_and_equipment_authority': False,
                      'unknown_covariance': 'HELD, not zero or independence'}
        elif args.command == 'example':
            save_new(args.output, example(args.profile)); result = {'request': str(args.output), 'profile': args.profile}
        elif args.command == 'run':
            result = run(load(args.request), args.output_dir)
        elif args.command == 'inspect':
            result = inspect(args.workspace)
        else:
            result = extend(args.workspace, args.output_dir, args.command)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if any(e['status'] != 'completed' for e in result.get('executions', [])) else 0
    except (ValueError, OSError, TypeError, KeyError, OverflowError) as exc:
        import sys
        print(json.dumps({'status': 'REFUSE', 'reason': str(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
