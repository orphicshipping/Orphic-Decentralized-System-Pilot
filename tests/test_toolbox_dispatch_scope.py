"""Independent adversarial regression probes over actual local service boundaries."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import threading

from ciw import context_catalog, toolbox_skills, audited_host

def implementation_hashes():
    return {module.__name__: sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (context_catalog, toolbox_skills, audited_host)}

import pytest
from ciw.agent_tools import _shape
from ciw.campaign_contracts import canonical
from ciw.context_catalog import for_principal_facade
from ciw.context_selector import StaleCatalog
from ciw.logistics_profile import TOOLS, selector
from test_campaign_audited_host import bound
from test_domain_read_profile import capture, host
from test_toolbox_campaign import runner, run


def accepted(call):
    try:
        call()
        return True
    except (ValueError, TypeError, KeyError):
        return False


def test_schema_selector_repair_preserves_independent_closed_selector_contract():
    """Differential check against the actual OLM selector, not a copied schema."""
    valid = [
        {'kind': 'workflow', 'mode': 'plan', 'planId': 'p'},
        {'kind': 'workflow', 'mode': 'run', 'planId': 'p', 'runId': 'r'},
        {'kind': 'acquisition', 'planId': 'p'},
        {'kind': 'compute', 'jobId': 'j', 'include': 'result'},
        {'kind': 'observation', 'observationId': 'o', 'observationDigest': 'sha256:' + 'A' * 64},
        {'kind': 'catalog'},
        {'kind': 'state', 'entityId': 'e', 'validAt': '2026-01-01', 'knownAt': '2026-01-02'},
        {'kind': 'twin', 'projectionId': 'p'},
        {'kind': 'package', 'mode': 'manifest', 'packageId': 'p', 'version': 'v'},
    ]
    cases = []
    for original in valid:
        cases.extend((original, {**original, 'extra': True}))
        for key in original:
            cases.append({k: v for k, v in original.items() if k != key})
            for value in (None, True, 0, [], {}, ''):
                cases.append({**original, key: value})
    schema = TOOLS['ncp_logistics_inspect']['inputSchema']['properties']['source']
    for value in cases:
        before = deepcopy(value)
        schema_accepts = accepted(lambda: _shape(value, schema))
        selector_accepts = accepted(lambda: selector(value))
        assert schema_accepts == selector_accepts, (value, schema_accepts, selector_accepts)
        assert value == before
    assert len(cases) == 200


@pytest.mark.parametrize('value,schema', [
    ({'nested': {'x': True}}, {'const': {'nested': {'x': 1}}}),
    ({'nested': [1.0]}, {'enum': [{'nested': [1]}]}),
    ('ok', {'anyOf': [{'const': 'ok'}, {'$ref': 'https://unresolved.invalid/schema'}]}),
    ('sha256:' + 'a' * 64 + '\n', {'pattern': '^sha256:[0-9a-f]{64}$'}),
    ({'x': 1}, {'oneOf': [{'required': ['x']}, {'properties': {'x': {}}}]}),
    ({'x': 1, 'y': 2}, {'anyOf': [{'type': 'object'}], 'additionalProperties': False}),
])
def test_adversarial_schema_constraints_fail_closed(value, schema):
    with pytest.raises(ValueError):
        _shape(value, schema)


def test_schema_shared_subtrees_are_not_cycles_or_mutated():
    child = {'v': 3}
    value = [child, child]
    spec = {'type': 'array', 'minItems': 2, 'maxItems': 2,
            'items': {'type': 'object', 'properties': {'v': {'const': 3}}}}
    before = deepcopy((value, spec))
    _shape(value, spec)
    assert (value, spec) == before
    assert value[0] is value[1]


def test_audited_allowlist_change_invalidates_selected_context(tmp_path):
    wrapped, _, _, _ = bound(tmp_path)
    selected = for_principal_facade(wrapped)
    selected.select(['ncp_domain_read_record'])
    wrapped._audit.chain.allowed = frozenset({'ncp_domain_read_describe'})
    with pytest.raises(StaleCatalog):
        selected.selected()
    assert selected.selected()['tools'] == []
    with pytest.raises(ValueError):
        selected.select(['ncp_domain_read_record'])


def test_audit_io_rebind_does_not_complete_under_old_recipe_scope(tmp_path, monkeypatch):
    """A real host replacement while durable I/O waits must not mix identities."""
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'retained'))
    wrapped, original_host, _, anchor = bound(tmp_path)
    changed = capture()
    changed['records'][0]['data']['price'] = '2.00'
    replacement = host(changed)
    active = runner(wrapped, [
        ('ncp_domain_read_record', {'domain': 'finance', 'record_id': 'record:../inert/path'})
    ])
    initial_scope = active._scope()
    entered, release = threading.Event(), threading.Event()
    read_anchor = wrapped._audit.read_anchor
    reads = []
    results, errors, calls = [], [], []
    original_call = replacement.call

    def call(*args):
        calls.append(deepcopy(args))
        return original_call(*args)

    def blocked_anchor():
        reads.append(1)
        if len(reads) == 1:
            entered.set()
            assert release.wait(5), 'bounded probe did not release anchor read'
        return read_anchor()

    def execute():
        try:
            results.append(run(active))
        except BaseException as exc:
            errors.append(type(exc).__name__ + ': ' + str(exc))

    monkeypatch.setattr(replacement, 'call', call)
    monkeypatch.setattr(wrapped._audit, 'read_anchor', blocked_anchor)
    thread = threading.Thread(target=execute)
    thread.start()
    try:
        assert entered.wait(5), 'recipe did not reach actual audit anchor read'
        # The trusted launcher obeys the documented facade policy lock.
        with wrapped.facade.policy.lock:
            wrapped.facade.hosts['domain_read'] = replacement
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors, errors
    assert len(results) == 1
    result = results[0]
    final = active._store.native(result['artifact_id'])
    saved = [active._store.native(step['artifact_id']) for step in final['steps']]
    report = {
        'summary': result, 'initial_scope': initial_scope,
        'final_scope': final['scope_sha256'],
        'completed_step_results': [record['result'] for record in saved],
        'original_capture': original_host._capture_digest,
        'replacement_capture': replacement._capture_digest,
        'replacement_dispatch_count': len(calls),
        'audit_phases': [row['event']['phase'] for row in wrapped._audit.chain.verify(anchor.read())],
        'source_sha256': implementation_hashes(),
    }
    (tmp_path / 'audit-io-rebind.json').write_text(json.dumps(report, indent=2) + '\n')
    assert result['status'] != 'completed', report
    assert result['steps_completed'] == 0, report


@pytest.mark.parametrize('boundary', ['terminal_anchor_read', 'terminal_anchor_ack'])
def test_terminal_audit_rebind_preserves_effects_without_completed_skill_receipt(
        tmp_path, monkeypatch, boundary):
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'retained'))
    wrapped, original_host, _, anchor = bound(tmp_path)
    replacement_value = capture()
    replacement_value['records'][0]['data']['price'] = '2.00'
    replacement = host(replacement_value)
    active = runner(wrapped, [
        ('ncp_domain_read_record', {'domain': 'finance', 'record_id': 'record:../inert/path'})
    ])
    initial_scope = active._scope()
    service_results, replacement_calls = [], []
    results, errors = [], []
    entered, release = threading.Event(), threading.Event()
    original_call = original_host.call
    original_replacement_call = replacement.call
    original_read = wrapped._audit.read_anchor
    original_cas = wrapped._audit.compare_and_set_anchor
    paused = []

    def call(*args):
        result = original_call(*args)
        service_results.append(deepcopy(result))
        return result

    def replacement_call(*args):
        replacement_calls.append(deepcopy(args))
        return original_replacement_call(*args)

    def pause_once():
        if service_results and not paused:
            paused.append(1)
            entered.set()
            assert release.wait(5), 'terminal audit probe was not released'

    def read():
        if boundary == 'terminal_anchor_read':
            pause_once()
        return original_read()

    def cas(before, after):
        result = original_cas(before, after)
        if boundary == 'terminal_anchor_ack':
            pause_once()  # The durable CAS has already committed its effect.
        return result

    def execute():
        try:
            results.append(run(active))
        except BaseException as exc:
            errors.append(type(exc).__name__ + ': ' + str(exc))

    monkeypatch.setattr(original_host, 'call', call)
    monkeypatch.setattr(replacement, 'call', replacement_call)
    monkeypatch.setattr(wrapped._audit, 'read_anchor', read)
    monkeypatch.setattr(wrapped._audit, 'compare_and_set_anchor', cas)
    thread = threading.Thread(target=execute)
    thread.start()
    try:
        assert entered.wait(5), 'operation did not reach the terminal audit boundary'
        assert len(service_results) == 1
        with wrapped.facade.policy.lock:
            wrapped.facade.hosts['domain_read'] = replacement
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors, errors
    assert len(results) == 1
    result = results[0]
    final = active._store.native(result['artifact_id'])
    journal = wrapped._audit.chain.verify(anchor.read())
    report = {
        'boundary': boundary, 'summary': result, 'initial_scope': initial_scope,
        'final_receipt': final, 'service_results': service_results,
        'replacement_dispatch_count': len(replacement_calls),
        'audit_phases': [row['event']['phase'] for row in journal],
        'terminal_event': journal[-1]['event'],
        'source_sha256': implementation_hashes(),
    }
    (tmp_path / 'terminal-rebind.json').write_text(json.dumps(report, indent=2) + '\n')
    assert result['status'] == 'outcome_unknown' and result['steps_completed'] == 0, report
    assert final['status'] == 'outcome_unknown' and final['steps'] == []
    assert replacement_calls == []
    assert service_results[0]['data']['capture_digest'] == original_host._capture_digest
    assert [row['event']['phase'] for row in journal] == ['admitted', 'succeeded']
    terminal = journal[-1]['event']
    assert terminal['effect_state'] == 'returned'
    assert terminal['result_ref'] == 'sha256:' + sha256(canonical(service_results[0])).hexdigest()
    from ciw.retention import _owner_dir
    records = [active._store.native(path.stem) for path in
               _owner_dir(active._store.owner).glob('retained-*.json')]
    assert not any(record['status'] == 'step_completed' for record in records)
    assert not any('result' in record for record in records)


@pytest.mark.parametrize('audited', [False, True])
def test_binding_change_at_service_return_withholds_data_without_undoing_call(
        tmp_path, monkeypatch, audited):
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'retained'))
    wrapped, original_host, _, anchor = bound(tmp_path)
    replacement_value = capture()
    replacement_value['records'][0]['data']['price'] = '2.00'
    replacement = host(replacement_value)
    authority = wrapped if audited else wrapped.facade
    active = runner(authority, [
        ('ncp_domain_read_record', {'domain': 'finance', 'record_id': 'record:../inert/path'})
    ])
    original_call = original_host.call
    actual_results = []

    def call(*args):
        result = original_call(*args)
        actual_results.append(deepcopy(result))
        # Same-thread host callbacks may legally re-enter the policy RLock.
        with wrapped.facade.policy.lock:
            wrapped.facade.hosts['domain_read'] = replacement
        return result

    monkeypatch.setattr(original_host, 'call', call)
    result = run(active)
    final = active._store.native(result['artifact_id'])
    assert len(actual_results) == 1
    assert actual_results[0]['data']['capture_digest'] == original_host._capture_digest
    assert result['status'] == 'outcome_unknown' and result['steps_completed'] == 0
    assert final['status'] == 'outcome_unknown' and final['steps'] == []
    if audited:
        journal = wrapped._audit.chain.verify(anchor.read())
        assert [row['event']['phase'] for row in journal] == ['admitted', 'unknown']
        assert journal[-1]['event']['effect_state'] == 'unknown'
    else:
        assert wrapped._audit.chain.verify(anchor.read()) == []


def test_context_scope_guard_nests_and_resets_without_changing_audit_state(tmp_path, monkeypatch):
    wrapped, profile, _, anchor = bound(tmp_path)
    checks, dispatched = [], []
    original_call = profile.call

    def call(*args):
        dispatched.append(deepcopy(args))
        return original_call(*args)

    def outer():
        checks.append('outer')
        return True

    def deny():
        checks.append('deny')
        return False

    monkeypatch.setattr(profile, 'call', call)
    with wrapped.scope_guard(outer):
        with wrapped.scope_guard(deny):
            denied = wrapped.call('ncp_domain_read_describe', {'domain': 'finance'}, call_id='nested-denial')
        assert denied['status'] == 'refused' and dispatched == []
        assert wrapped.call('ncp_domain_read_describe', {'domain': 'finance'}, call_id='outer-success')['status'] == 'ok'
        assert len(dispatched) == 1
    after = list(checks)
    assert wrapped.call('ncp_domain_read_describe', {'domain': 'finance'}, call_id='unguarded-success')['status'] == 'ok'
    assert len(dispatched) == 2 and checks == after
    assert checks[0:2] == ['outer', 'deny']
    assert checks.count('deny') == 1 and checks.count('outer') >= 3
    assert [row['event']['phase'] for row in wrapped._audit.chain.verify(anchor.read())] == [
        'admitted', 'refused', 'admitted', 'succeeded', 'admitted', 'succeeded',
    ]


def test_context_scope_guard_is_isolated_between_threads(tmp_path, monkeypatch):
    wrapped, profile, _, anchor = bound(tmp_path)
    entered, release = threading.Event(), threading.Event()
    denied_results, errors, dispatched = [], [], []
    original_call = profile.call

    def call(*args):
        dispatched.append((threading.get_ident(), deepcopy(args)))
        return original_call(*args)

    def denied_context():
        try:
            with wrapped.scope_guard(lambda: False):
                entered.set()
                assert release.wait(5)
                denied_results.append(wrapped.call(
                    'ncp_domain_read_describe', {'domain': 'finance'}, call_id='other-thread-denied'))
        except BaseException as exc:
            errors.append(type(exc).__name__ + ': ' + str(exc))

    monkeypatch.setattr(profile, 'call', call)
    thread = threading.Thread(target=denied_context)
    thread.start()
    try:
        assert entered.wait(5)
        allowed = wrapped.call('ncp_domain_read_describe', {'domain': 'finance'}, call_id='main-thread-allowed')
        assert allowed['status'] == 'ok'
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and errors == []
    assert len(denied_results) == 1 and denied_results[0]['status'] == 'refused'
    assert len(dispatched) == 1 and dispatched[0][0] == threading.get_ident()
    assert [row['event']['phase'] for row in wrapped._audit.chain.verify(anchor.read())] == [
        'admitted', 'succeeded', 'admitted', 'refused',
    ]
