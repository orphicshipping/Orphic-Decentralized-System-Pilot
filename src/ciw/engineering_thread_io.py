"""Intent-to-evidence front door over the existing NET production controller.

Compilation has no execution permission. Running requires an explicit host choice
and grant. Review freezes individually hashed records before the existing offline
reader; it is not an atomic filesystem snapshot or a new scientific ledger.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import html
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile

from . import engineering_thread as e, link_contract as c, production
from .control_contracts import save_new
from .hardware_workflow import read_bytes
from .link_store import safe_path as _link_safe_path
from .filesystem_paths import filesystem_path
from .operations.runner import seal, check_seal

MAX_FILE = 8 * 1024 * 1024  # Preserve the existing production-reader boundary.
MAX_TOTAL = 64 * 1024 * 1024
CORE = {'intent.json', 'request.json', 'compilation.json'}


def safe_path(path):
    # Check the original components before normalization can remove a parent
    # traversal, then recheck the IO spelling so long symlinks cannot be missed.
    guarded = _link_safe_path(path)
    if os.name == 'nt':
        # A long original component can look absent to ordinary Win32 stat.
        # Check each lexical parent before removing any '..' components.
        for item in (guarded, *guarded.parents):
            if filesystem_path(item).is_symlink():
                raise ValueError('Link storage paths must not traverse symlinks')
        return _link_safe_path(filesystem_path(guarded))
    return guarded


def _write_raw(path, raw):
    path = safe_path(path)
    with path.open('xb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())


def _json_raw(value):
    return (json.dumps(value, indent=2, allow_nan=False)+'\n').encode('utf-8')


def compile_packet(intent_raw, request_raw, output, *, host_profile, continuation=None):
    compiled = e.compile_intent(intent_raw, request_raw, host_profile=host_profile, continuation=continuation)
    raw = _json_raw(compiled)
    root = safe_path(output); root.mkdir(parents=True, exist_ok=False)
    for name, data in [('intent.json', intent_raw), ('request.json', request_raw), ('compilation.json', raw)]:
        _write_raw(root/name, data)
    return {'status': 'COMPILED_NOT_AUTHORIZED', 'compiled_ref': c.bytes_ref(raw),
        'intent_ref': compiled['intent_ref'], 'request_ref': compiled['request_ref'],
        'operation_order': compiled['typed_plan'], 'reserved_operations': compiled['bindings']['reserved_operations'],
        'scope': deepcopy(e.SCOPE), 'authorizes_execution': False}


def _packet(directory, *, expected_compiled_ref, host_profile, current_runtime=False):
    c.content_ref(expected_compiled_ref)
    root = safe_path(directory)
    files = {name: read_bytes(safe_path(root/name), limit=MAX_FILE) for name in sorted(CORE)}
    if c.bytes_ref(files['compilation.json']) != expected_compiled_ref:
        raise ValueError('Compilation differs from externally selected bytes')
    compiled = c.loads(files['compilation.json'], MAX_FILE)
    intent, source = e.check_compilation(compiled, files['intent.json'], files['request.json'],
                                        host_profile=host_profile, current_runtime=current_runtime)
    return files, compiled, intent, source


def _check_transition(prior, new, old_basis, new_basis):
    """The evidence may change; a continuation cannot silently redefine success."""
    if new['intent_id'] != prior['intent_id'] or new['revision'] != prior['revision']+1:
        raise ValueError('Revision must retain intent identity and increment exactly once')
    if not e._same(new['requirements'], prior['requirements']) or not e._same(new['constraints'], prior['constraints']):
        raise ValueError('A continuation cannot weaken/change objectives or constraints; separate design review required')
    if not e._same(old_basis, new_basis):
        raise ValueError('Source plan changed the acceptance basis; separate design review required')


def _check_predecessor(directory, compiled, intent, *, host_profile):
    """Check the immediate predecessor from a host-selected path, never a saved URI.

    Typed continuation references alone are not evidence that it was reviewed.
    This is bounded content/occurrence validation, not authentication, execution
    permission, transitive-chain verification or a physical qualification.
    """
    continuation = compiled['continuation']
    if continuation is None:
        if directory is not None:
            raise ValueError('An initial revision has no predecessor to authorize or reuse')
        return
    if directory is None:
        raise PermissionError('A continued run requires its explicit --predecessor for fresh evidence review')
    with _frozen_thread(directory, expected_thread_ref=continuation['previous_thread_ref'],
                        host_profile=host_profile) as state:
        _, old, prior, _, session = state
        _check_transition(prior, intent, old['decision_basis'], compiled['decision_basis'])
        expected = {'previous_intent_ref': old['intent_ref'],
                    'previous_execution_ids': list(session.executions),
                    'previous_result_ids': list(session.results)}
        if any(not e._same(continuation[name], value) for name, value in expected.items()):
            raise ValueError('Continuation lineage differs from the original predecessor occurrences')


def run_packet(directory, output, *, expected_compiled_ref, host_profile, allow_compute=False,
               predecessor=None):
    if allow_compute is not True:
        raise PermissionError('Explicit host --allow-compute is required; a plan grants no authority')
    files, compiled, intent, source = _packet(directory, expected_compiled_ref=expected_compiled_ref,
                                            host_profile=host_profile, current_runtime=True)
    module = e.profile(host_profile)[0]
    registry = module.registry()
    workers = (production.Worker('retained-analysis', tuple(n['operation_id'] for n in module.graph()['nodes'])),)
    _check_predecessor(predecessor, compiled, intent, host_profile=host_profile)
    # Recheck separately supplied bindings immediately before any execution.
    bindings = production.preflight(compiled['plan'], registry, workers, e.gate_bindings(),
                                    max_operations=intent['constraints']['max_operations'])
    if not e._same(bindings, compiled['bindings']):
        raise ValueError('Execution bindings changed after compilation')
    root = safe_path(output); root.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items(): _write_raw(root/name, raw)
    production.run_production(source, compiled['plan'], registry, workers, e.gate_bindings(), root/'run',
                              max_operations=intent['constraints']['max_operations'])
    entries = []
    total = 0
    for path in sorted(root.rglob('*')):
        if path.is_dir(): continue
        raw = read_bytes(safe_path(path), limit=MAX_FILE); total += len(raw)
        entries.append({'path': path.relative_to(root).as_posix(), 'bytes': len(raw), 'sha256': c.bytes_ref(raw)})
    if len(entries) > 64 or total > MAX_TOTAL:
        raise ValueError('Retained thread exceeds review bounds; existing history remains unpromoted')
    receipt = seal({'schema': 'ciw.engineering-thread.v1', 'compiled_ref': expected_compiled_ref,
                    'files': entries, 'scope': deepcopy(e.SCOPE), 'active_authority_restored': False})
    raw = _json_raw(receipt); _write_raw(root/'thread.json', raw)  # Last, not evidence of atomic directory commit.
    return inspect_thread(root, expected_thread_ref=c.bytes_ref(raw), host_profile=host_profile)


def _relative(name):
    if type(name) is not str or not name or len(name) > 256 or '\\' in name:
        raise ValueError('Invalid retained relative path')
    p = PurePosixPath(name)
    if p.is_absolute() or name != p.as_posix() or any(x in {'..', '.', ''} for x in p.parts):
        raise ValueError('Retained path cannot escape the frozen work order')
    if name not in CORE and (len(p.parts) not in (2, 3) or p.parts[0] != 'run' or
                            (len(p.parts) == 3 and p.parts[1] != 'session')):
        raise ValueError('Not an existing production/session artifact')
    if p.suffix != '.json': raise ValueError('Only retained JSON artifacts belong to this bounded work order')
    return p


@contextmanager
def _frozen_thread(directory, *, expected_thread_ref, host_profile, include_manifest=False):
    c.content_ref(expected_thread_ref); root = safe_path(directory)
    raw = read_bytes(safe_path(root/'thread.json'), limit=65536)
    if c.bytes_ref(raw) != expected_thread_ref:
        raise ValueError('Thread differs from externally retained selection')
    manifest = c.loads(raw, 65536); check_seal(manifest)
    c.keys(manifest, {'schema', 'compiled_ref', 'files', 'scope', 'active_authority_restored', 'record_digest'})
    if (manifest['schema'] != 'ciw.engineering-thread.v1' or not e._same(manifest['scope'], e.SCOPE)
            or manifest['active_authority_restored'] is not False):
        raise ValueError('Thread changes review scope or claims restored authority')
    entries = manifest['files']
    if type(entries) is not list or not 1 <= len(entries) <= 64:
        raise ValueError('Unbounded retained manifest')
    captured = {}; total = 0
    for entry in entries:
        c.keys(entry, {'path', 'bytes', 'sha256'}); relative = _relative(entry['path'])
        c.integer(entry['bytes'], minimum=1, maximum=MAX_FILE); c.content_ref(entry['sha256'])
        if entry['path'] in captured: raise ValueError('Duplicate retained path')
        total += entry['bytes']
        if total > MAX_TOTAL: raise ValueError('Retained thread exceeds total-byte bound')
        data = read_bytes(safe_path(root/relative), limit=entry['bytes'])
        if len(data) != entry['bytes'] or c.bytes_ref(data) != entry['sha256']:
            raise ValueError('Retained artifact differs: '+entry['path'])
        captured[entry['path']] = data
    if not CORE <= set(captured): raise ValueError('Incomplete thread inputs')
    # Reject omitted or injected records. Do not read extra file contents.
    actual = set()
    for i, path in enumerate(root.rglob('*')):
        if i >= 128: raise ValueError('Too many paths in retained review directory')
        safe_path(path)
        if path.is_file() and path != root/'thread.json': actual.add(path.relative_to(root).as_posix())
    if actual != set(captured): raise ValueError('Thread has omitted or additional artifacts')
    with tempfile.TemporaryDirectory(prefix='net-engineering-review-') as d:
        temp = Path(d)
        for name, data in captured.items():
            dest = temp/name; dest.parent.mkdir(parents=True, exist_ok=True); _write_raw(dest, data)
        files, compiled, intent, source = _packet(temp, expected_compiled_ref=manifest['compiled_ref'],
                                                host_profile=host_profile)
        # Compilation and existing controller must agree, not merely be separately well formed.
        actual_plan = c.loads(captured['run/plan.json'], MAX_FILE)
        actual_bindings = c.loads(captured['run/bindings.json'], MAX_FILE)
        if not e._same(actual_plan, compiled['plan']) or not e._same(actual_bindings, compiled['bindings']):
            raise ValueError('Executed plan or bindings differ from the selected compilation')
        module = e.profile(host_profile)[0]; module.register()
        reviewed = production.inspect_production(temp/'run', e.inspection_gate_bindings(compiled['compiler']))
        # Bind original source bytes as well as their content identity.
        from .session import Session
        with tempfile.TemporaryDirectory(prefix='net-engineering-source-review-') as s:
            session = Session.from_workspace(temp/'run/session/workspace.json', Path(s))
        if not e._same(session.run, source):
            raise ValueError('Controller source differs from original engineering request')
        # Restore receives the same externally anchored manifest bytes, not a
        # second path read or a reconstruction with different formatting.
        if include_manifest:
            captured = {**captured, 'thread.json': raw}
        yield captured, compiled, intent, reviewed, session


def inspect_thread(directory, *, expected_thread_ref, host_profile):
    with _frozen_thread(directory, expected_thread_ref=expected_thread_ref, host_profile=host_profile) as state:
        files, compiled, intent, reviewed, session = state
        checks = c.loads(files['run/attempt-0001-checks.json'], MAX_FILE)['checks']
        numerical = [c for c in checks if not c['check']['check_id'].startswith('industrial.')]
        if any(c['status'] == 'FAIL' for c in numerical):
            status = 'REQUIREMENTS_NOT_MET'
        elif any(c['status'] != 'PASS' for c in numerical):
            status = 'ANALYSIS_UNRESOLVED'
        else:
            status = 'EVIDENCE_REQUIRED'
        todo = [{'requirement_id': check['check']['check_id'], 'status': check['status'],
                 'next': 'Review source, method and discrepancy; no physical cause inferred.'
                 if not check['check']['check_id'].startswith('industrial.') else
                 'Obtain independently reviewed evidence; declarations and successful computation are insufficient.'}
                for check in checks if check['status'] != 'PASS']
        return {'schema': 'ciw.engineering-review.v1', 'status': status,
            'intent_id': intent['intent_id'], 'revision': intent['revision'], 'profile': host_profile,
            'thread_ref': expected_thread_ref, 'compiled_ref': c.bytes_ref(files['compilation.json']),
            'intent_ref': c.bytes_ref(files['intent.json']), 'request_ref': c.bytes_ref(files['request.json']),
            'evidence_id': session.run['evidence_id'], 'session_id': c.loads(files['run/production.json'], MAX_FILE)['session_id'],
            'execution_ids': list(session.executions), 'result_ids': list(session.results),
            'checks': checks, 'unresolved': todo, 'controller': reviewed,
            'workflow_observations': {
                'reserved_operations': compiled['bindings']['reserved_operations'],
                'executed_operations': reviewed['execution_count'], 'retained_results': reviewed['result_count'],
                'requirements_passed': sum(x['status'] == 'PASS' for x in checks),
                'requirements_failed': sum(x['status'] == 'FAIL' for x in checks),
                'requirements_unresolved': sum(x['status'] == 'INDETERMINATE' for x in checks),
                'queue_time_s': None, 'monetary_cost': None, 'information_gain': None},
            'next_action': 'STOP_AND_REVIEW_EVIDENCE', 'automatic_retry': False,
            'cost_or_information_gain_estimated': False, 'active_authority_restored': False,
            'continuation': deepcopy(compiled['continuation']),
            'scope': deepcopy(e.SCOPE)}



# Bounded internal evidence guidance, not standards adjudication or provider discovery.
_FOLLOW_UP_EVIDENCE = {
    'reference_traceability': (
        'Assemble applicable reference-chain evidence',
        ['Identified reference and calibration records, with range, units and validity.',
         'Applicability to the exact DUT, reference, configuration and installation.']),
    'timing_qualification': (
        'Assemble timing and phase evidence',
        ['Timestamp-event definitions and measured clock, skew and drift evidence.',
         'Applicable interval, sampling configuration and timing uncertainty.']),
    'uncertainty_budget': (
        'Assemble the measurement uncertainty budget',
        ['Contribution definitions, units, covariance and propagation method.',
         'Coverage convention and supporting measurement or validation evidence.']),
    'independent_validation': (
        'Assemble independent validation evidence',
        ['Original independent reference observations and identified procedure.',
         'Comparison against the unchanged acceptance criteria and applicability limits.']),
    'product_safety_emc': (
        'Assemble product-specific safety and EMC evidence',
        ['Reviewed applicable requirements for the exact product configuration.',
         'Applicable reports, test conditions, limitations and review authority.']),
}


def next_actions(directory, *, expected_thread_ref, host_profile):
    """Propose bounded follow-up work from an existing validated investigation.

    This is a read-only projection, not another execution graph or authority
    token. It names unresolved evidence without pretending that an unavailable
    importer, laboratory, solver or qualification decision has been supplied.
    The existing reader freezes and validates the source once; no saved status
    file or model-generated assertion determines the findings.
    """
    view = inspect_thread(directory, expected_thread_ref=expected_thread_ref, host_profile=host_profile)
    actions = []
    for finding in view['checks']:
        state = finding['status']
        if state == 'PASS':
            continue
        if state not in {'FAIL', 'INDETERMINATE'}:
            raise ValueError('Unsupported finding status for an engineering follow-up')
        check = finding['check']; policy = check['policy']; predicate = policy['predicate']
        if (check['gate_id'] != e.GATE or policy['profile'] != host_profile
                or policy['request_ref'] != view['request_ref']):
            raise ValueError('Follow-up finding differs from the original evidence boundary')
        if predicate in _FOLLOW_UP_EVIDENCE:
            title, needed = _FOLLOW_UP_EVIDENCE[predicate]
            kind, admission = 'ASSEMBLE_EMPIRICAL_EVIDENCE', 'QUALIFIED_IMPORTER_NOT_IMPLEMENTED'
        else:
            title = ('Review the numerical discrepancy' if state == 'FAIL'
                     else 'Review source and analysis prerequisites')
            needed = ['Original flagged, missing or discrepant records and applicable method.',
                      'Reviewed explanation or new evidence; keep the acceptance basis unchanged.']
            kind = 'REVIEW_NUMERICAL_DISCREPANCY' if state == 'FAIL' else 'REVIEW_ANALYSIS_PREREQUISITES'
            admission = 'REVIEW_REQUIRED_BEFORE_RECOMPILATION'
        finding_ref = c.content_identity(finding)
        points = []
        if predicate == 'sampled_points' and type(finding['detail']) is dict:
            points = [{'frequency_hz': row['frequency_hz'], 'status': row['status']}
                      for row in finding['detail'].get('sampled_points', []) if row['status'] != 'PASS']
        action = {
            'requirement_id': check['check_id'], 'predicate': predicate, 'finding_status': state,
            'finding_ref': finding_ref, 'source_result_id': finding['source_result_id'],
            'source_record_digest': finding['source_record_digest'], 'action_kind': kind,
            'title': title, 'required_evidence': list(needed), 'sampled_points_to_review': points,
            'cause_identified': False, 'requested_capability': None, 'executable': False,
            'admission_status': admission}
        action['action_id'] = c.content_identity({'thread_ref': expected_thread_ref, 'action': action})
        actions.append(action)
    observations = view['workflow_observations']
    return seal({
        'schema': 'ciw.engineering-next-actions.v1', 'status': 'FOLLOW_UP_PROPOSED_NOT_AUTHORIZED',
        'assessment_status': view['status'],
        'method': {'id': 'ciw.engineering-next-actions.v1',
                   'source_ref': c.bytes_ref(Path(__file__).read_bytes().replace(b'\r\n', b'\n')),
                   'selection': 'unmet_existing_predicates_not_optimization_or_causal_inference'},
        'basis': {key: deepcopy(view[key]) for key in ('thread_ref', 'compiled_ref', 'intent_ref',
            'request_ref', 'intent_id', 'revision', 'profile', 'evidence_id', 'session_id',
            'execution_ids', 'result_ids')},
        'counts': {'satisfied': observations['requirements_passed'],
                   'failed': observations['requirements_failed'],
                   'indeterminate': observations['requirements_unresolved'], 'proposed': len(actions)},
        'actions': actions, 'order_basis': 'original_check_order_not_cost_or_safety_priority',
        'metrics': {'estimated_cost': None, 'expected_information_gain': None},
        'execution_plan_generated': False, 'authorizes_execution': False, 'automatic_retry': False,
        'new_scientific_executions': 0, 'active_authority_restored': False,
        'scope': deepcopy(e.SCOPE)})


def revise(directory, intent_raw, request_raw, output, *, expected_thread_ref, host_profile, reason):
    """Recompile a proposed next work order; never resume or erase old history."""
    c.text(reason, maximum=1024)
    with _frozen_thread(directory, expected_thread_ref=expected_thread_ref, host_profile=host_profile) as state:
        files, old, prior, reviewed, session = state
        new = e.validate_intent(c.loads(intent_raw, e.MAX_INTENT), host_profile)
        continuation = {'previous_thread_ref': expected_thread_ref, 'previous_intent_ref': old['intent_ref'],
            'previous_execution_ids': list(session.executions), 'previous_result_ids': list(session.results),
            'reason': reason, 'basis': 'locally_reviewed_predecessor_not_signed_authority'}
        replacement = e.compile_intent(intent_raw, request_raw, host_profile=host_profile, continuation=continuation)
        _check_transition(prior, new, old['decision_basis'], replacement['decision_basis'])
    result = compile_packet(intent_raw, request_raw, output, host_profile=host_profile, continuation=continuation)
    return {'status': 'REVISION_PROPOSED_NOT_EXECUTED', 'compilation': result,
            'continuation': continuation, 'computations_reused': False, 'execution_authorized': False}



def restore_thread(directory, output, *, expected_thread_ref, host_profile):
    """Recover exact history into a NEW directory without rerunning the graph.

    Validate/freeze first, write original thread manifest last, then inspect the
    destination. A failed copy remains partial; neither source nor existing
    destination is overwritten. This is not a power-loss-atomic filesystem
    transaction or an authenticated backup service. Credentials are not restored.
    """
    source_root, root = safe_path(directory), safe_path(output)
    if root.resolve().is_relative_to(source_root.resolve()):
        raise ValueError('Recovery destination must be outside the original retained thread')
    with _frozen_thread(source_root, expected_thread_ref=expected_thread_ref,
                        host_profile=host_profile, include_manifest=True) as state:
        files, _, _, _, session = state
        expected_executions, expected_results = list(session.executions), list(session.results)
        # Keep byte references in memory after the temporary verification closes.
        frozen = dict(files)
    root.mkdir(parents=True, exist_ok=False)
    for name, data in sorted(frozen.items()):
        if name == 'thread.json':
            continue
        target = root / _relative(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_raw(target, data)
    _write_raw(root/'thread.json', frozen['thread.json'])
    checked = inspect_thread(root, expected_thread_ref=expected_thread_ref, host_profile=host_profile)
    if checked['execution_ids'] != expected_executions or checked['result_ids'] != expected_results:
        raise ValueError('Restored computation occurrences differ; no recovery success')
    return {'status': 'RESTORED_HISTORICAL_EVIDENCE', 'thread_ref': expected_thread_ref,
            'files_restored': len(frozen), 'bytes_restored': sum(map(len, frozen.values())),
            'execution_ids': expected_executions, 'result_ids': expected_results,
            'new_scientific_executions': 0, 'active_authority_restored': False,
            'predecessor_chain_revalidated': False, 'scope': deepcopy(e.SCOPE)}


def report(directory, output, *, expected_thread_ref, host_profile):
    view = inspect_thread(directory, expected_thread_ref=expected_thread_ref, host_profile=host_profile)
    esc = lambda value: html.escape(str(value), quote=True)
    rows = ''.join('<tr><td>'+esc(x['check']['check_id'])+'</td><td>'+esc(x['status'])+'</td></tr>' for x in view['checks'])
    text = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>NET / Engineering readiness</title><style>body{{font:17px/1.5 system-ui;background:#101813;color:#d8e3bb;margin:0}}main{{max-width:900px;margin:auto;padding:20px}}h1,p,td{{overflow-wrap:anywhere}}table{{width:100%;table-layout:fixed}}td{{padding:10px;border-bottom:1px solid #627563}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}summary{{cursor:pointer;padding:12px}}</style>
<main><p>LINK / NET · RETAINED ENGINEERING REVIEW · NOT LIVE</p><h1>{esc(view['status'].replace('_',' '))}</h1>
<p>{esc(view['intent_id'])}, revision {view['revision']}</p><p><strong>Industrial qualification: HELD.</strong> No conformance or release decision has been made.</p>
<p>Next: stop and review unmet requirements and evidence. No automatic hardware action or retry.</p>
<table><thead><tr><th>Requirement</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table>
<details><summary>Advanced: original occurrences, predicates and evidence</summary><pre>{esc(json.dumps(view,indent=2,allow_nan=False))}</pre></details></main></html>'''
    raw = text.encode('utf-8'); _write_raw(output, raw)
    return {'path': str(output), 'sha256': c.bytes_ref(raw), 'scope': deepcopy(e.SCOPE)}


def demo(output, *, host_profile):
    e.profile(host_profile)
    logical_root = _link_safe_path(output)
    root = safe_path(output); root.mkdir(parents=True, exist_ok=False)
    source_fixture = None
    if host_profile == 'installation':
        from .link_installation_example import demo as make
        source_fixture = make(logical_root/'source', compliant=True)
        raw = (root/'source/investigation/request.json').read_bytes()
    elif host_profile == 'scalar':
        from .link_study_cli import example
        raw = c.bounded(example(), e.profile(host_profile)[3])
    else:
        raise ValueError('Unknown explicit profile')
    intent_raw = _json_raw(e.example_intent(host_profile, c.bytes_ref(raw)))
    planned = compile_packet(intent_raw, raw, root/'compiled', host_profile=host_profile)
    result = run_packet(root/'compiled', root/'execution', expected_compiled_ref=planned['compiled_ref'],
                        host_profile=host_profile, allow_compute=True)
    page = report(root/'execution', logical_root/'engineering.html', expected_thread_ref=result['thread_ref'], host_profile=host_profile)
    summary = {'origin': 'synthetic', 'plan': planned, 'review': result, 'report': page,
               'source_fixture': source_fixture,
               'source_fixture_execution_is_separate': source_fixture is not None}
    save_new(root/'summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(prog='net production engineering', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ('demo', 'example', 'compile', 'run', 'inspect', 'report', 'revise', 'restore', 'next'):
        p = sub.add_parser(cmd); p.add_argument('--profile', choices=e.PROFILES, required=True)
        if cmd in {'demo', 'compile', 'run', 'revise', 'restore'}: p.add_argument('--output-dir', type=Path, required=True)
        if cmd in {'example', 'report'}: p.add_argument('--output', type=Path, required=True)
        if cmd == 'next': p.add_argument('--output', type=Path, help='Optional new review file outside the original thread')
        if cmd == 'example': p.add_argument('--request-ref', required=True)
        if cmd in {'compile', 'revise'}:
            p.add_argument('--intent', type=Path, required=True); p.add_argument('--request', type=Path, required=True)
        if cmd in {'run', 'inspect', 'report', 'revise', 'restore', 'next'}:
            p.add_argument('directory', type=Path)
        if cmd == 'run': p.add_argument('--compiled-ref', required=True); p.add_argument('--allow-compute', action='store_true')
        if cmd in {'inspect', 'report', 'revise', 'restore', 'next'}: p.add_argument('--thread-ref', required=True)
        if cmd == 'revise': p.add_argument('--reason', required=True)
        if cmd == 'run': p.add_argument('--predecessor', type=Path, help='Host-selected immediate predecessor; required for continued revisions')
    a = parser.parse_args(argv)
    try:
        if a.command == 'demo': result = demo(a.output_dir, host_profile=a.profile)
        elif a.command == 'example':
            value = e.example_intent(a.profile, a.request_ref); save_new(safe_path(a.output), value)
            result = {'status': 'EXAMPLE_NOT_AUTHORIZED', 'intent': value}
        elif a.command in {'compile', 'revise'}:
            intent = read_bytes(safe_path(a.intent), limit=e.MAX_INTENT)
            raw = read_bytes(safe_path(a.request), limit=e.profile(a.profile)[3])
            if a.command == 'compile': result = compile_packet(intent, raw, a.output_dir, host_profile=a.profile)
            else: result = revise(a.directory, intent, raw, a.output_dir, expected_thread_ref=a.thread_ref,
                                   host_profile=a.profile, reason=a.reason)
        elif a.command == 'run': result = run_packet(a.directory, a.output_dir, expected_compiled_ref=a.compiled_ref,
                                                     host_profile=a.profile, allow_compute=a.allow_compute, predecessor=a.predecessor)
        elif a.command == 'restore': result = restore_thread(a.directory, a.output_dir, expected_thread_ref=a.thread_ref, host_profile=a.profile)
        elif a.command == 'next':
            if a.output is not None and safe_path(a.output).resolve().is_relative_to(safe_path(a.directory).resolve()):
                raise ValueError('Follow-up output must remain outside the original retained thread')
            result = next_actions(a.directory, expected_thread_ref=a.thread_ref, host_profile=a.profile)
            if a.output is not None: save_new(safe_path(a.output), result)
        elif a.command == 'inspect': result = inspect_thread(a.directory, expected_thread_ref=a.thread_ref, host_profile=a.profile)
        else: result = report(a.directory, a.output, expected_thread_ref=a.thread_ref, host_profile=a.profile)
        print(json.dumps(result, indent=2, allow_nan=False))
        # Exit 2 is a retained but held/failed assessment; it is not a tool crash.
        status = result.get('review', result).get('status')
        return 2 if status in {'REQUIREMENTS_NOT_MET', 'ANALYSIS_UNRESOLVED', 'EVIDENCE_REQUIRED'} else 0
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, OverflowError, RecursionError) as exc:
        print(json.dumps({'status': 'REFUSED', 'reason': str(exc), 'scope': e.SCOPE}), file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
