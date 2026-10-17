"""One offline installation workflow; existing Session/DAG/MCP own execution."""
from __future__ import annotations

import argparse
from copy import deepcopy
import html
import json
import os
from pathlib import Path
import sys
import tempfile

from . import link_contract as c, link_installation as inst
from .control_contracts import save_new
from .control_plane import run_graph
from .hardware_workflow import read_bytes
from .link_store import safe_path
from .session import Session, loads_json


def run(request, output, *, raw=None):
    raw = c.bounded(request, inst.MAX_BYTES) if raw is None else raw
    if not inst.equal(c.loads(raw, inst.MAX_BYTES), request):
        raise ValueError('Request and original bytes disagree')
    inst.unpack_request(request)  # Typed plan first; semantic capture refusal stays in Session.
    source = inst.make_source(request, c.bytes_ref(raw))
    root = safe_path(output); root.mkdir(parents=True, exist_ok=False)
    with (root/'request.json').open('xb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())
    registry = inst.registry()
    session = Session(source, root, operations=registry.operations)
    outcome = run_graph(session, inst.graph(), registry)
    session.save_workspace(root/'workspace.json'); save_new(root/'graph.json', outcome)
    return inspect(root, expected_request_ref=c.bytes_ref(raw))


def _read(directory, *, expected_request_ref):
    c.content_ref(expected_request_ref); inst.register()
    root = safe_path(directory)
    raw = read_bytes(root/'request.json', limit=inst.MAX_BYTES)
    if c.bytes_ref(raw) != expected_request_ref:
        raise ValueError('Installation request differs from external selection')
    request = c.loads(raw, inst.MAX_BYTES); inst.unpack_request(request)
    workspace_raw = read_bytes(root/'workspace.json', limit=32*1024*1024)
    workspace = loads_json(workspace_raw.decode('utf-8'))
    with tempfile.TemporaryDirectory(prefix='net-installation-review-') as d:
        temp = Path(d); save_new(temp/'workspace.json', workspace)
        session = Session.from_workspace(temp/'workspace.json', temp/'review')
    if not inst.equal(session.run, inst.make_source(request, expected_request_ref)):
        raise ValueError('Installation workspace and exact source differ')
    executions = list(session.executions.values())
    if len(executions) != 1 or executions[0]['operation_id'] != inst.OPERATION:
        raise ValueError('Require exactly the original installation computation')
    execution = executions[0]
    result = next(iter(session.results.values()), None)
    if (execution['status'] == 'completed' and len(session.results) != 1
            or execution['status'] != 'completed' and session.results):
        raise ValueError('Installation execution/result cardinality mismatch')
    g = loads_json(read_bytes(root/'graph.json', limit=32*1024*1024).decode('utf-8'))
    from .control_checks import inspect_record
    inspect_record(g)
    if (not inst.equal(g['experiment'], inst.graph()) or g['source_evidence_id'] != session.run['evidence_id']
            or set(g['nodes']) != {'response'}):
        raise ValueError('Installation graph differs from fixed operation/source')
    node = g['nodes']['response']
    if not inst.equal(node['execution'], execution) or not inst.equal(node.get('result'), result):
        raise ValueError('Graph substituted a different computation occurrence')
    return request, raw, session, g, workspace_raw


def inspect(directory, *, expected_request_ref):
    req, raw, session, g, _ = _read(directory, expected_request_ref=expected_request_ref)
    plan, cap, _, _ = inst.unpack_request(req)
    try:
        from .link_gateway import validate_capture
        origins = {v['origin'] for v in validate_capture(cap)['observations']}
        origin = next(iter(origins)) if len(origins) == 1 else 'UNRESOLVED'
    except (ValueError, TypeError, KeyError):
        origin = 'UNRESOLVED'
    ex = next(iter(session.executions.values())); res = next(iter(session.results.values()), None)
    return {'schema': 'ciw.link-installation-inspection.v1', 'application': 'Link',
        'capability': 'Installation Response', 'origin': origin, 'status': 'REFUSED' if res is None else res['data']['status'],
        'request_ref': c.bytes_ref(raw), 'evidence_id': session.run['evidence_id'],
        'operation_id': inst.OPERATION, 'execution_id': ex['execution_id'],
        'result_id': None if res is None else res['result_id'], 'verification_id': None,
        'installation_occurrence': plan['installation']['occurrence_id'],
        'plan_ref': req['plan']['sha256'], 'capture_bytes_ref': req['capture']['sha256'],
        'result': deepcopy(res), 'execution': deepcopy(ex), 'scope': deepcopy(inst.SCOPE)}


def replay(directory, output, *, expected_request_ref):
    request, raw, _, _, _ = _read(directory, expected_request_ref=expected_request_ref)
    return run(request, output, raw=raw)


def restore(directory, output, *, expected_request_ref):
    request, raw, _, _, _ = _read(directory, expected_request_ref=expected_request_ref)
    _, _, pr, cr = inst.unpack_request(request)
    root = safe_path(output); root.mkdir(parents=True, exist_ok=False)
    for name, data in [('plan.json', pr), ('capture.json', cr), ('request.json', raw)]:
        with (root/name).open('xb') as f: f.write(data)
    return {'status': 'ORIGINAL_BYTES_RESTORED', 'request_ref': c.bytes_ref(raw),
            'plan_ref': c.bytes_ref(pr), 'capture_ref': c.bytes_ref(cr), 'scope': deepcopy(inst.SCOPE)}


def applicability(directory, plan_raw, *, expected_request_ref, expected_plan_ref):
    """Review a proposed plan against history, never transfer qualification."""
    c.content_ref(expected_plan_ref)
    if c.bytes_ref(plan_raw) != expected_plan_ref:
        raise ValueError('Proposed plan differs from external selection')
    proposed = inst.validate_plan(c.loads(plan_raw, inst.MAX_PLAN))
    request, _, session, _, _ = _read(directory, expected_request_ref=expected_request_ref)
    original, _, old_raw, _ = inst.unpack_request(request)
    changed = sorted(k for k in original if not inst.equal(original[k], proposed[k]))
    # Even an unchanged declaration is not evidence of unchanged physical setup.
    return {'schema': 'ciw.link-installation-applicability.v1',
        'status': 'RECHARACTERIZATION_REQUIRED' if changed else 'DECLARATIONS_UNCHANGED',
        'changed_fields': changed, 'original_plan_ref': c.bytes_ref(old_raw),
        'proposed_plan_ref': expected_plan_ref, 'source_evidence_id': session.run['evidence_id'],
        'source_execution_id': next(iter(session.executions)),
        'same_declared_physical_unit': original['installation']['dut']['unit_id'] == proposed['installation']['dut']['unit_id'],
        'same_declared_installation': original['installation']['occurrence_id'] == proposed['installation']['occurrence_id'],
        'qualification_transferred': False, 'new_scientific_execution': False, 'scope': deepcopy(inst.SCOPE)}


def agent(directory, *, expected_request_ref):
    *_, workspace_raw = _read(directory, expected_request_ref=expected_request_ref)
    from .agent_api import AgentHost, MAX_DOCUMENT
    if len(workspace_raw) > MAX_DOCUMENT: raise ValueError('Workspace exceeds existing MCP bound')
    return AgentHost(registry=inst.registry(bind=False), inputs={'investigation': workspace_raw}, allow_operations=())


def report(directory, output, *, expected_request_ref):
    view = inspect(directory, expected_request_ref=expected_request_ref)
    data = (view['result'] or {}).get('data', {}); response = data.get('response') or {}
    esc = lambda v: html.escape(str(v), quote=True)
    def number(v): return 'UNKNOWN' if v is None else format(v, '.7g')
    rows = ''.join('<tr>' + ''.join('<td>'+esc(v)+'</td>' for v in (
        number(p['frequency_hz']), number(p['observations']['relative_gain']['value']),
        number(p['observations']['relative_phase']['value']), number(p['observations']['coherence']['value']),
        p['status'])) + '</tr>' for p in response.get('frequencies', []))
    body = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Link / Installation Response</title><style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{background:#0f1611;color:#d8e3bb;font:16px/1.5 system-ui;margin:0}}
main{{max-width:1050px;margin:auto;padding:24px}}section,details{{border:1px solid #57705c;padding:18px;margin:18px 0}}
.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #57705c}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}summary{{cursor:pointer;min-height:44px}}p,h1,h2{{overflow-wrap:anywhere}}
</style><main><p>LINK / NET / RETAINED OFFLINE INVESTIGATION</p><h1>Installation Response</h1>
<section><h2>{esc(view['status'])}</h2><p>Origin: {esc(view['origin'])}</p>
<p>Installation: {esc(view['installation_occurrence'])}</p><p><strong>PHYSICAL QUALIFICATION HELD</strong></p>
<p>{len(response.get('passing_sampled_frequencies_hz', []))} sampled frequencies within declared point limits.</p>
<p>No continuous qualified band. No isolated mount diagnosis or automatic correction.</p>
<p>Next: review excitation, reference-chain evidence, sample timing and uncertainty before physical acceptance.</p></section>
<section><h2>Relative installed-chain response</h2><div class="scroll"><table><thead><tr><th>Hz</th><th>Gain</th><th>Phase °</th><th>Coherence</th><th>Point review</th></tr></thead><tbody>{rows}</tbody></table></div>
<p>Coherence is not calibrated uncertainty. All source flags, refusals and raw bytes remain retained.</p></section>
<details><summary>Advanced: evidence, conditions, criteria and execution</summary><pre>{esc(json.dumps(view, indent=2, allow_nan=False))}</pre></details></main></html>'''
    path = safe_path(output)
    with path.open('xb') as f: f.write(body.encode())
    return {'path': str(path), 'sha256': c.bytes_ref(body.encode()), 'scope': deepcopy(inst.SCOPE)}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='net link installation', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('demo'); p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--compliant', action='store_true')
    p = sub.add_parser('prepare'); p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--plan-ref', required=True); p.add_argument('--capture', type=Path, required=True)
    p.add_argument('--capture-ref', required=True); p.add_argument('--output', type=Path, required=True)
    for cmd in ('run', 'inspect', 'replay', 'restore', 'report', 'mcp', 'applicability'):
        p = sub.add_parser(cmd); p.add_argument('input', type=Path); p.add_argument('--request-ref', required=True)
        if cmd in {'run', 'replay', 'restore'}: p.add_argument('--output-dir', type=Path, required=True)
        if cmd == 'report': p.add_argument('--output', type=Path, required=True)
        if cmd == 'applicability':
            p.add_argument('--plan', type=Path, required=True); p.add_argument('--plan-ref', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'demo':
            from .link_installation_example import demo
            result = demo(args.output_dir, compliant=args.compliant)
        elif args.command == 'prepare':
            result = inst.prepare(read_bytes(safe_path(args.plan), limit=inst.MAX_PLAN),
                read_bytes(safe_path(args.capture), limit=c.MAX_CAPTURE_BYTES),
                expected_plan_ref=args.plan_ref, expected_capture_ref=args.capture_ref)
            prepared_raw = (json.dumps(result, indent=2, allow_nan=False)+'\n').encode()
            save_new(safe_path(args.output), result)
            result = {'status': 'PREPARED', 'request_ref': c.bytes_ref(prepared_raw),
                      'scope': deepcopy(inst.SCOPE)}
        elif args.command == 'run':
            raw = read_bytes(safe_path(args.input), limit=inst.MAX_BYTES)
            if c.bytes_ref(raw) != args.request_ref: raise ValueError('Request differs from external selection')
            result = run(c.loads(raw, inst.MAX_BYTES), args.output_dir, raw=raw)
        elif args.command == 'applicability':
            result = applicability(args.input, read_bytes(safe_path(args.plan), limit=inst.MAX_PLAN),
                expected_request_ref=args.request_ref, expected_plan_ref=args.plan_ref)
        elif args.command == 'mcp':
            from .agent_mcp import serve
            with agent(args.input, expected_request_ref=args.request_ref) as host:
                return serve(host, sys.stdin.buffer, sys.stdout.buffer)
        else:
            fn = {'inspect': inspect, 'replay': replay, 'restore': restore, 'report': report}[args.command]
            result = fn(args.input, *([args.output_dir] if args.command in {'replay', 'restore'} else
                [args.output] if args.command == 'report' else []), expected_request_ref=args.request_ref)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2 if result.get('status') in {'REFUSED', 'SOURCE_REVIEW_REQUIRED'} else 0
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, OverflowError) as exc:
        print(json.dumps({'status': 'REFUSED', 'reason': str(exc), 'scope': inst.SCOPE}), file=sys.stderr)
        return 2


if __name__ == '__main__': raise SystemExit(main())
