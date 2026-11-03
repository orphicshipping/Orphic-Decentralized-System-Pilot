"""Actual MCP dispatch, persisted server loading and signed canonical workflows.

Fixtures use actual local reference/evidence services, Ed25519 verification,
SQLite CAS audit anchors and hash-chained audit journals. No hosted or device
qualification is inferred from these local protocol checks.
"""
from copy import deepcopy
import json
import multiprocessing
from pathlib import Path
import sqlite3

import pytest

from ciw.agent_mcp import Server, serve
from ciw.audit_chain import AuditChain, empty_anchor
from ciw.audited_host import AuditedPrincipalHost
from ciw.mcp_toolbox import MCPToolboxHost, TOOLS
from ciw.services import invoke
from ciw.toolbox_recipes import builtin_skills
from ciw.workflow_registry import WorkflowRegistry
from test_toolbox_skills import configured, OPERATIONS
from test_workflow_registry import signed


class DurableAnchor:
    def __init__(self, path, chain_id):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS anchor (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO anchor VALUES (1,?)', (json.dumps(empty_anchor(chain_id)),))

    def read(self):
        with sqlite3.connect(self.path) as db:
            return json.loads(db.execute('SELECT body FROM anchor WHERE id=1').fetchone()[0])

    def cas(self, before, after):
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            if json.loads(db.execute('SELECT body FROM anchor WHERE id=1').fetchone()[0]) != before:
                return False
            db.execute('UPDATE anchor SET body=? WHERE id=1', (json.dumps(after),))
        return True


def audited(facade, folder, chain_id, names):
    anchor = DurableAnchor(folder / (chain_id + '.sqlite'), chain_id)
    chain = AuditChain(folder / (chain_id + '.jsonl'), chain_id=chain_id, allowed_operations=names)
    return AuditedPrincipalHost(facade, chain=chain, read_anchor=anchor.read,
                               compare_and_set_anchor=anchor.cas), anchor


@pytest.fixture
def toolbox(configured, tmp_path):
    c = configured
    tmp_path.chmod(0o700)
    c.grant['operations'].extend({'name': n, 'constraints': {}} for n in TOOLS)
    definition = builtin_skills().describe('reference-citation', '1.0.0')['definition']
    for step in definition['steps']:
        step.pop('schema_sha256')
    envelope, trust, _ = signed(definition, tenant='synthetic-tenant')
    registry = WorkflowRegistry(tmp_path / 'workflows.sqlite', lambda: trust)
    registry.publish(envelope)
    inner, inner_anchor = audited(c.facade, tmp_path, 'canonical-inner', OPERATIONS)
    def new_host(**kwargs):
        return MCPToolboxHost(path=tmp_path / 'toolbox.sqlite', registry=registry,
            policy=c.policy, principal='alice', tenant='synthetic-tenant', pins={'source': 'fixture-v1'},
            replay_identity='server-fixture', components={'reference': OPERATIONS[:3]},
            admit_step=kwargs.pop('admit_step', lambda intent: c.admissions.append(deepcopy(intent)) or True),
            **kwargs).bind_dispatch(inner)
    host = new_host()
    outer_facade = c.policy.facade('alice', toolbox=host)
    outer, outer_anchor = audited(outer_facade, tmp_path, 'toolbox-outer', TOOLS)
    return c, host, outer, inner, registry, trust, outer_anchor, inner_anchor, new_host


class RPCClient:
    def __init__(self, send):
        self.send, self.sequence = send, 0
        result = self.request('initialize', {'protocolVersion': '2025-11-25',
            'capabilities': {}, 'clientInfo': {'name': 'fixture-client', 'version': '1'}})
        assert result['result']['capabilities']['tools']['listChanged'] is False
        self.send(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}).encode(), False)

    def request(self, method, params):
        self.sequence += 1
        return self.send(json.dumps({'jsonrpc': '2.0', 'id': self.sequence,
            'method': method, 'params': params}).encode(), True)

    def call(self, name, arguments):
        reply = self.request('tools/call', {'name': name, 'arguments': arguments})
        assert 'error' not in reply, reply
        return reply['result']['structuredContent']


def local_client(host):
    server = Server(host)
    return RPCClient(lambda raw, reply: server.handle(raw))


def run_args(attempt='one'):
    return {'workflow_id': 'reference-citation', 'version': '1.0.0',
            'inputs': {'source_id': 'gdrive:Original', 'query': 'calibration'}, 'attempt': attempt}


def test_registered_mcp_load_invoke_unload_and_discovery_unchanged(toolbox):
    c, host, outer, inner, registry, trust, outer_anchor, inner_anchor, reopen = toolbox
    client = local_client(outer)
    names_before = client.request('tools/list', {})['result']['tools']
    assert {r['name'] for r in names_before} == set(TOOLS)
    description = client.call('ncp_toolbox_describe', {'component_id': 'reference'})
    checksum = description['data']['digest']
    loaded = client.call('ncp_toolbox_load', {'component_id': 'reference', 'expected_digest': checksum})
    assert loaded['status'] == 'ok' and loaded['data']['active']
    assert client.request('tools/list', {})['result']['tools'] == names_before
    result = client.call('ncp_toolbox_call', {'component_id': 'reference',
        'operation': 'ncp_knowledge_resolve', 'arguments': {'source_id': 'gdrive:Original'}})
    assert result['status'] == 'ok'
    assert result['data']['result']['record']['record_id'] == c.record_id
    # A reconstructed host shares persisted activation, not connection memory.
    assert reopen().call('ncp_toolbox_list', {})['data']['items'][0]['active']
    assert client.call('ncp_toolbox_unload', {'component_id': 'reference'})['data']['active'] is False
    assert client.request('tools/list', {})['result']['tools'] == names_before
    assert not reopen().call('ncp_toolbox_list', {})['data']['items'][0]['active']
    journal = inner._audit.chain.verify(inner_anchor.read())
    assert [r['event']['phase'] for r in journal] == ['admitted', 'succeeded']
    assert journal[0]['event']['operation'] == 'ncp_knowledge_resolve'
    outer._audit.chain.verify(outer_anchor.read())


def test_signed_workflow_mcp_runs_actual_services_and_receipts_reopen(toolbox):
    c, host, outer, inner, _, _, outer_anchor, inner_anchor, reopen = toolbox
    client = local_client(outer)
    assert client.call('ncp_skill_list', {})['data']['items'][0]['workflow_id'] == 'reference-citation'
    value = client.call('ncp_skill_run', run_args())
    assert value['status'] == 'ok', value
    result = value['data']
    assert result['status'] == 'completed' and result['steps_completed'] == 3, result
    receipt_args = {'attempt': 'one', 'artifact_id': result['artifact_id']}
    final = client.call('ncp_skill_receipt', receipt_args)['data']['receipt']
    assert final['status'] == 'completed'
    passage = client.call('ncp_skill_receipt', {'attempt': 'one',
        'artifact_id': final['steps'][-1]['artifact_id']})['data']['receipt']
    assert passage['result']['span']['text'].encode() == c.text
    assert reopen().call('ncp_skill_receipt', receipt_args)['data']['receipt'] == final
    assert reopen().call('ncp_skill_receipt', {'attempt': 'one'})['data']['receipt'] == final
    assert len(c.admissions) == 3
    assert [r['event']['operation'] for r in inner._audit.chain.verify(inner_anchor.read())][::2] == OPERATIONS[:3]
    assert [r['event']['phase'] for r in outer._audit.chain.verify(outer_anchor.read())] == ['admitted', 'succeeded'] * 4


class PipeStream:
    def __init__(self, connection):
        self.connection = connection

    def readline(self, maximum):
        try:
            return self.connection.recv_bytes(maxlength=maximum)
        except EOFError:
            return b''

    def write(self, raw):
        self.connection.send_bytes(raw)

    def flush(self):
        pass


def _serve_process(host, pipe):
    stream = PipeStream(pipe)
    serve(host, stream, stream)


def test_real_child_server_and_parent_jsonrpc_client(toolbox):
    _, _, outer, _, _, _, _, _, _ = toolbox
    context = multiprocessing.get_context('fork')
    parent, child = context.Pipe()
    process = context.Process(target=_serve_process, args=(outer, child))
    process.start()
    child.close()
    def send(raw, reply):
        parent.send_bytes(raw + b'\n')
        if reply:
            assert parent.poll(20), 'MCP server did not reply'
            return json.loads(parent.recv_bytes())
    try:
        client = RPCClient(send)
        assert len(client.request('tools/list', {})['result']['tools']) == len(TOOLS)
        result = client.call('ncp_skill_run', run_args('process-attempt'))
        assert result['status'] == 'ok' and result['data']['status'] == 'completed', result
        receipt = client.call('ncp_skill_receipt', {'attempt': 'process-attempt',
            'artifact_id': result['data']['artifact_id']})
        assert receipt['data']['receipt']['status'] == 'completed'
    finally:
        # Explicit bounded worker termination after successful protocol checks;
        # neither a native provider nor a deployment is running in this child.
        process.terminate()
        process.join(5)
        parent.close()


def test_management_grant_and_inner_operation_grant_are_independent(toolbox):
    c, host, _, inner, _, _, _, inner_anchor, _ = toolbox
    c.grant['operations'] = [r for r in c.grant['operations'] if r['name'] != 'ncp_toolbox_load']
    checksum = host.call('ncp_toolbox_describe', {'component_id': 'reference'})['data']['digest']
    assert host.call('ncp_toolbox_load', {'component_id': 'reference', 'expected_digest': checksum})['status'] == 'refused'
    c.grant['operations'].append({'name': 'ncp_toolbox_load', 'constraints': {}})
    assert host.call('ncp_toolbox_load', {'component_id': 'reference', 'expected_digest': checksum})['status'] == 'ok'
    c.grant['operations'] = [r for r in c.grant['operations'] if r['name'] != 'ncp_knowledge_resolve']
    assert host.call('ncp_toolbox_call', {'component_id': 'reference', 'operation': 'ncp_knowledge_resolve',
        'arguments': {'source_id': 'gdrive:Original'}})['status'] == 'refused'
    assert inner._audit.chain.verify(inner_anchor.read()) == []


def test_signature_revocation_during_admission_prevents_canonical_dispatch(toolbox):
    c, _, _, inner, _, trust, _, inner_anchor, reopen = toolbox
    def revoke(intent):
        trust['generation'] += 1
        trust['keys'][0]['enabled'] = False
        return True
    value = reopen(admit_step=revoke).call('ncp_skill_run', run_args())
    # The durable attempt was written before admission; a later revoked signer
    # withholds its data and never reclassifies the write as not started.
    assert value['status'] == 'refused' and value['data'] is None, value
    assert value['error']['code'] == 'outcome_unknown' and value['effect_state'] == 'unknown'
    assert inner._audit.chain.verify(inner_anchor.read()) == []


def test_outer_audit_admission_revocation_prevents_first_workflow_dispatch(toolbox, monkeypatch):
    _, _, outer, inner, _, trust, outer_anchor, inner_anchor, _ = toolbox
    original = outer._audit.compare_and_set_anchor
    def admitted(before, after):
        committed = original(before, after)
        if after['sequence'] == 1:
            trust['generation'] += 1
            trust['keys'][0]['enabled'] = False
        return committed
    monkeypatch.setattr(outer._audit, 'compare_and_set_anchor', admitted)
    result = local_client(outer).call('ncp_skill_run', run_args('audit-admission-revoked'))
    assert result['status'] == 'refused' and result['data'] is None
    assert inner._audit.chain.verify(inner_anchor.read()) == []
    assert [r['event']['phase'] for r in outer._audit.chain.verify(outer_anchor.read())] == ['admitted', 'refused']


def test_workflow_expiry_during_terminal_audit_withholds_receipt_without_erasing_event(toolbox, monkeypatch):
    c, host, outer, _, registry, _, outer_anchor, _, _ = toolbox
    clock = [10]
    monkeypatch.setattr(registry, '_clock', lambda: clock[0])
    assert host.call('ncp_skill_run', run_args('expiry'))['data']['status'] == 'completed'
    original = outer._audit.compare_and_set_anchor
    def terminal(before, after):
        committed = original(before, after)
        if after['sequence'] == 2:
            clock[0] = 4102444800
        return committed
    monkeypatch.setattr(outer._audit, 'compare_and_set_anchor', terminal)
    result = local_client(outer).call('ncp_skill_receipt', {'attempt': 'expiry'})
    assert result['status'] == 'refused' and result['data'] is None
    assert result['error']['code'] == 'outcome_unknown'
    assert c.text.decode() not in json.dumps(result)
    assert [r['event']['phase'] for r in outer._audit.chain.verify(outer_anchor.read())] == ['admitted', 'succeeded']


def test_selected_policy_authority_is_never_replaced_by_python_snapshot(toolbox, tmp_path):
    from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
    c, _, _, _, registry, _, _, _, _ = toolbox
    checked = []
    class DenyingPolicy(PrincipalPolicy):
        def authorize_context(self, principal, name, arguments):
            checked.append((principal, name, deepcopy(arguments)))
            raise PrincipalRefusal()
    policy = DenyingPolicy(c.policy._reader)
    inner, _ = audited(policy.facade('alice', reference=c.reference, logistics=c.logistics),
                       tmp_path, 'selected-policy', OPERATIONS)
    host = MCPToolboxHost(path=tmp_path / 'selected-policy-toolbox.sqlite', registry=registry,
        policy=policy, principal='alice', tenant='synthetic-tenant', pins={'source': 'fixture-v1'},
        replay_identity='selected-policy', components={'reference': OPERATIONS[:3]}).bind_dispatch(inner)
    result = host.call('ncp_toolbox_list', {})
    assert result['status'] == 'refused' and result['data'] is None
    assert checked == [('alice', 'ncp_toolbox_list', {})]


def test_underlying_grant_revoked_at_outer_terminal_withholds_component_result(toolbox, monkeypatch):
    c, host, outer, inner, _, _, outer_anchor, inner_anchor, _ = toolbox
    checksum = host.call('ncp_toolbox_describe', {'component_id': 'reference'})['data']['digest']
    assert host.call('ncp_toolbox_load', {'component_id': 'reference', 'expected_digest': checksum})['status'] == 'ok'
    original = outer._audit.compare_and_set_anchor
    def terminal(before, after):
        committed = original(before, after)
        if after['sequence'] == 2:
            c.grant['operations'] = [r for r in c.grant['operations'] if r['name'] != 'ncp_knowledge_resolve']
        return committed
    monkeypatch.setattr(outer._audit, 'compare_and_set_anchor', terminal)
    result = local_client(outer).call('ncp_toolbox_call', {'component_id': 'reference',
        'operation': 'ncp_knowledge_resolve', 'arguments': {'source_id': 'gdrive:Original'}})
    assert result['status'] == 'refused' and result['data'] is None
    assert result['error']['code'] == 'outcome_unknown'
    for audited_host, anchor in ((inner, inner_anchor), (outer, outer_anchor)):
        assert [r['event']['phase'] for r in audited_host._audit.chain.verify(anchor.read())] == ['admitted', 'succeeded']


def test_list_guard_rechecks_expiry_after_its_final_trust_read(toolbox, monkeypatch):
    from ciw.workflow_registry import WorkflowRefusal
    _, host, _, _, registry, _, _, _, _ = toolbox
    clock = [10]
    monkeypatch.setattr(registry, '_clock', lambda: clock[0])
    guard = host.request_scope_guard('ncp_skill_list', {})
    original_list, original_read = registry.list, registry._reader
    listed = [False]
    def listing(*args, **kwargs):
        result = original_list(*args, **kwargs)
        listed[0] = True
        return result
    def delayed_trust():
        if listed[0]:
            clock[0] = 4102444800
        return original_read()
    monkeypatch.setattr(registry, 'list', listing)
    monkeypatch.setattr(registry, '_reader', delayed_trust)
    with pytest.raises(WorkflowRefusal, match='validity_window'):
        guard()
    assert listed[0] and clock[0] == 4102444800


def test_duplicate_attempt_reopen_never_replays_and_trust_revokes_receipt(toolbox):
    _, host, _, inner, _, trust, _, inner_anchor, reopen = toolbox
    result = host.call('ncp_skill_run', run_args())['data']
    before = inner._audit.chain.verify(inner_anchor.read())
    assert reopen().call('ncp_skill_run', run_args())['status'] == 'refused'
    assert inner._audit.chain.verify(inner_anchor.read()) == before
    trust['generation'] += 1
    trust['keys'][0]['enabled'] = False
    assert host.call('ncp_skill_receipt', {'attempt': 'one', 'artifact_id': result['artifact_id']})['status'] == 'refused'


def test_cancel_from_separate_host_during_admission_prevents_dispatch(toolbox):
    _, _, _, inner, _, _, _, inner_anchor, reopen = toolbox
    other = reopen()
    def cancel(intent):
        assert other.call('ncp_skill_cancel', {'attempt': intent['attempt']})['data']['cancel_requested']
        return True
    result = reopen(admit_step=cancel).call('ncp_skill_run', run_args())
    assert result['status'] == 'ok' and result['data']['status'] in {'refused', 'cancelled'}, result
    assert inner._audit.chain.verify(inner_anchor.read()) == []


def test_raw_service_bypass_and_arbitrary_arguments_refused(toolbox):
    _, host, outer, _, _, _, _, _, _ = toolbox
    with pytest.raises(ValueError):
        invoke('ncp_skill_run', run_args(), host=host)
    client = local_client(outer)
    for extra in ({'principal': 'bob'}, {'path': '/tmp/whatever'}, {'token': 'fixture'}):
        assert client.call('ncp_skill_run', {**run_args(), **extra})['status'] == 'refused'
    assert host.call('ncp_toolbox_load', {'component_id': 'unregistered', 'expected_digest': 'a' * 64})['status'] == 'refused'


def test_inner_chain_cannot_include_management_admission(toolbox, tmp_path):
    c, host, outer, _, registry, _, _, _, _ = toolbox
    fresh = MCPToolboxHost(path=tmp_path / 'unbound.sqlite', registry=registry, policy=c.policy,
        principal='alice', tenant='synthetic-tenant', pins={'source': 'fixture-v1'},
        replay_identity='new', components={'reference': OPERATIONS[:3]})
    with pytest.raises(ValueError, match='separate_canonical_audit'):
        fresh.bind_dispatch(outer)
    assert fresh.call('ncp_skill_list', {})['status'] == 'refused'


def _run_with_paused_admission(make_host, ready, release, result):
    def admission(intent):
        ready.set()
        if not release.wait(10):
            raise TimeoutError('test admission handshake')
        return True
    result.put(make_host(admit_step=admission).call('ncp_skill_run', run_args('cancel-process')))


def test_actual_cross_process_cancellation_before_first_dispatch(toolbox):
    _, host, _, inner, _, _, _, anchor, reopen = toolbox
    context = multiprocessing.get_context('fork')
    ready, release, result = context.Event(), context.Event(), context.Queue()
    child = context.Process(target=_run_with_paused_admission, args=(reopen, ready, release, result))
    child.start()
    try:
        assert ready.wait(10)
        cancellation = host.call('ncp_skill_cancel', {'attempt': 'cancel-process'})
        assert cancellation['status'] == 'ok' and cancellation['data']['cancel_requested']
        release.set()
        reply = result.get(timeout=15)
        assert reply['status'] == 'ok' and reply['data']['status'] in {'refused', 'cancelled'}, reply
        child.join(10)
        assert child.exitcode == 0
        assert inner._audit.chain.verify(anchor.read()) == []
    finally:
        release.set()
        if child.is_alive():
            child.terminate()
            child.join(5)


def test_interrupted_attempt_is_inspectable_and_never_replayed(toolbox):
    _, host, _, inner, registry, _, _, anchor, reopen = toolbox
    args = run_args('interrupted')
    checked = registry.get('synthetic-tenant', args['workflow_id'], args['version'])
    from ciw.campaign_contracts import canonical
    # This is the exact durable pre-dispatch state left by a terminated host.
    with host.db.transaction() as db:
        db.execute('INSERT INTO toolbox_attempts(owner,attempt,request,workflow_digest) VALUES (?,?,?,?)',
            (host._owner, args['attempt'], canonical(args), checked['workflow_sha256']))
    fresh = reopen()
    status = fresh.call('ncp_skill_receipt', {'attempt': 'interrupted'})
    assert status['status'] == 'ok'
    assert status['data']['status'] == 'outcome_unknown' and status['data']['retry_authorized'] is False
    assert fresh.call('ncp_skill_run', args)['status'] == 'refused'
    assert inner._audit.chain.verify(anchor.read()) == []


def test_receipt_cannot_cross_attempt_or_principal(toolbox):
    c, host, _, _, _, _, _, _, _ = toolbox
    first = host.call('ncp_skill_run', run_args('first'))['data']
    host.call('ncp_skill_run', run_args('second'))
    assert host.call('ncp_skill_receipt', {'attempt': 'second', 'artifact_id': first['artifact_id']})['status'] == 'refused'
    c.grant['enabled'] = False
    assert host.call('ncp_skill_receipt', {'attempt': 'first'})['status'] == 'refused'


def test_policy_identity_snapshot_cannot_mix_a_b_a(toolbox):
    c, host, _, _, _, _, _, _, _ = toolbox
    allowed = deepcopy(c.value)
    denied = deepcopy(c.value)
    denied['revision'] = 'deny-management'
    denied['principals'][0]['operations'] = []
    sequence = [allowed, denied, allowed]
    original = c.policy._reader
    reads = []
    def read():
        item = sequence.pop(0) if sequence else denied
        reads.append(item['revision'])
        return item
    c.policy._reader = read
    try:
        context = host._authorize('ncp_toolbox_describe', {'component_id': 'reference'})
        assert context['policy_revision'] == allowed['revision']
        assert reads == [allowed['revision']]
    finally:
        c.policy._reader = original


def test_terminal_audit_failure_propagates_unknown_and_prevents_next_execution(toolbox):
    _, host, outer, inner, _, _, outer_anchor, inner_anchor, reopen = toolbox
    real_cas = inner._audit.compare_and_set_anchor
    writes = []
    def fail_terminal(before, after):
        writes.append(1)
        return real_cas(before, after) if len(writes) == 1 else False
    inner._audit.compare_and_set_anchor = fail_terminal
    value = local_client(outer).call('ncp_skill_run', run_args('uncertain'))
    assert value['status'] == 'refused' and value['error']['code'] == 'outcome_unknown', value
    assert value['data']['status'] == 'outcome_unknown'
    assert outer._audit.chain.verify(outer_anchor.read())[-1]['event']['phase'] == 'unknown'
    retained = host.call('ncp_skill_receipt', {'attempt': 'uncertain'})
    assert retained['data']['receipt']['status'] == 'outcome_unknown'
    after = inner._audit.chain.inspect_unanchored()['records']
    assert len(after) == 2
    refused = reopen().call('ncp_skill_run', run_args('another'))
    assert refused['status'] == 'refused' and refused['error']['code'] == 'outcome_unknown'
    assert inner._audit.chain.inspect_unanchored()['records'] == after


def test_invalid_mutation_is_distinct_from_unknown_execution(toolbox):
    _, host, outer, _, _, _, outer_anchor, _, _ = toolbox
    result = host.call('ncp_toolbox_load', {'component_id': 'reference', 'expected_digest': '0' * 64})
    assert result['status'] == 'refused' and result['effect_state'] == 'not_started'
    assert result['error']['code'] == 'toolbox_refused'
    assert host.call('ncp_toolbox_list', {})['data']['items'][0]['active'] is False
    client = local_client(outer)
    result = client.call('ncp_toolbox_load', {'component_id': 'reference', 'expected_digest': '0' * 64})
    assert result['status'] == 'refused' and result['error']['code'] == 'toolbox_refused'
    journal = outer._audit.chain.verify(outer_anchor.read())
    assert [r['event']['phase'] for r in journal] == ['admitted', 'refused']
    checksum = client.call('ncp_toolbox_describe', {'component_id': 'reference'})['data']['digest']
    assert client.call('ncp_toolbox_load', {'component_id': 'reference', 'expected_digest': checksum})['status'] == 'ok'


@pytest.mark.parametrize('derived', [False, True])
def test_raw_or_subclass_host_cannot_forge_pre_effect_custody(toolbox, derived):
    c, _, _, _, _, _, _, _, _ = toolbox
    class Forged(MCPToolboxHost if derived else object):
        _bound_principal = 'alice'
        def __init__(self):
            pass
        def call(self, name, args):
            return {'status': 'refused', 'data': None, 'effect_state': 'not_started',
                    'error': {'code': 'toolbox_refused'}}
    facade = c.policy.facade('alice', toolbox=Forged())
    result = facade.call('ncp_toolbox_load', {'component_id': 'reference', 'expected_digest': '0' * 64})
    assert result['error']['code'] == 'outcome_unknown'


def test_other_family_cannot_forge_pre_effect_custody(toolbox):
    from types import SimpleNamespace
    from ciw.principal_policy import owner_for
    c, _, _, _, _, _, _, _, _ = toolbox
    c.grant['operations'].append({'name': 'ncp_retained_release', 'constraints': {}})
    c.grant['resources'].append('ncp://retained/')
    invoked = []
    class Forged:
        retained = SimpleNamespace(owner=owner_for('alice'))
        def call(self, name, arguments):
            invoked.append(name)
            return {'status': 'refused', 'data': None, 'effect_state': 'not_started',
                    'error': {'code': 'toolbox_refused'}}
    result = c.policy.facade('alice', control=Forged()).call(
        'ncp_retained_release', {'artifact_id': 'retained-' + '0' * 32})
    assert invoked == ['ncp_retained_release']
    assert result['error']['code'] == 'outcome_unknown'
