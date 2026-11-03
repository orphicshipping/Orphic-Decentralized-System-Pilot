"""Real synthetic reference/domain hosts, with observed identity drift refused.

No credentials, remote identity or hosted deployment. An observed rebind is a
permanent refusal for that wrapper, even after the original object is restored.
"""
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
import threading

import pytest

from ciw.audit_chain import AuditChain, AuditOutcomeUncertain
from ciw.audited_host import AuditedPrincipalHost
from ciw.principal_policy import PrincipalFacade, PrincipalRefusal
from ciw.reference_profile import open_reference
from ciw.services import invoke
from reference_fixture_helpers import make_catalog
from test_campaign_audited_host import Anchor
from test_domain_read_profile import capture, host, policy_document


@pytest.fixture(scope='module')
def references(tmp_path_factory):
    root = tmp_path_factory.mktemp('audited-identity-reference')
    result = {}
    for principal in ('alice', 'bob'):
        path = root / principal
        corpus, record, _ = make_catalog(path, ('Synthetic ' + principal + ' private reference.').encode(),
                                       filename=principal + '-fixture.md')
        raw = open_reference(catalog_path=path/'catalog-inputs/bundle', catalog_ref=corpus.ref)
        result[principal] = (raw, record)
    return result


@pytest.fixture
def bound(tmp_path, references, monkeypatch):
    from ciw.principal_policy import PrincipalPolicy
    domain = {}
    value = policy_document()
    value['principals'].append(deepcopy(value['principals'][0]))
    for principal, row in zip(('alice', 'bob'), value['principals']):
        captured = capture()
        captured['principal'] = principal
        captured['records'][0]['data']['instrument'] = 'synthetic:' + principal + '-private'
        domain[principal] = host(captured, principal=principal)
        row['principal'] = principal
        row['operations'] += [{'name': name, 'constraints': {}}
                              for name in ('ncp_knowledge_overview', 'ncp_knowledge_source')]
        row['reference_scopes'] = [references[principal][0].corpus_ref]
        row['resources'] = [references[principal][0].base_uri + '/']
    policy = PrincipalPolicy(lambda: value)
    facades = {principal: policy.facade(principal, domain_read=domain[principal],
                                      reference=references[principal][0]) for principal in domain}
    chain = AuditChain(tmp_path/'identity.jsonl', chain_id='audited-profile', allowed_operations=[
        'ncp_domain_read_record', 'ncp_knowledge_overview', 'ncp_knowledge_source', 'resource_read'])
    anchor = Anchor(tmp_path/'anchor.sqlite')
    wrapper = AuditedPrincipalHost(facades['alice'], chain=chain, read_anchor=anchor.read,
                                  compare_and_set_anchor=anchor.cas)
    calls = []
    for principal, raw in domain.items():
        original = raw.call
        def tracked(*args, principal=principal, original=original):
            calls.append(principal)
            return original(*args)
        monkeypatch.setattr(raw, 'call', tracked)
    return SimpleNamespace(wrapper=wrapper, facades=facades, policy=policy, value=value,
                           domain=domain, references=references, chain=chain, anchor=anchor, calls=calls,
                           uri=references['alice'][0].base_uri + '/source/' + references['alice'][1][7:])


ARGUMENTS = {'domain': 'finance', 'record_id': 'record:../inert/path'}
OPERATION = 'ncp_domain_read_record'


def replace(fixture, mode):
    original = fixture.facades['alice']
    if mode == 'other_facade':
        fixture.wrapper.facade = fixture.facades['bob']
    elif mode == 'same_principal_facade':
        fixture.wrapper.facade = fixture.policy.facade('alice', domain_read=fixture.domain['alice'],
                                                      reference=fixture.references['alice'][0])
    elif mode == 'principal':
        original.principal = 'bob'
    elif mode == 'missing':
        del fixture.wrapper.facade
    elif mode == 'invalid_principal':
        original.principal = None


def restored(fixture):
    fixture.wrapper.facade = fixture.facades['alice']
    fixture.facades['alice'].principal = 'alice'


def fresh(fixture):
    return AuditedPrincipalHost(fixture.facades['alice'], chain=fixture.chain,
                               read_anchor=fixture.anchor.read, compare_and_set_anchor=fixture.anchor.cas)


def assert_private_refusal(value):
    assert value == PrincipalFacade.refusal()
    assert 'alice' not in repr(value) and 'bob' not in repr(value)
    assert 'sha256:' not in repr(value) and 'private' not in repr(value)


def test_real_two_principal_fixtures_and_canonical_sdk_are_distinct(bound):
    assert bound.facades['alice'].invoke(OPERATION, ARGUMENTS)['record']['data']['instrument'] == 'synthetic:alice-private'
    assert bound.facades['bob'].invoke(OPERATION, ARGUMENTS)['record']['data']['instrument'] == 'synthetic:bob-private'
    expected = bound.facades['alice'].read_resource(bound.uri)
    assert bound.wrapper.read_resource(bound.uri) == expected
    assert invoke(OPERATION, ARGUMENTS, host=bound.wrapper)['record']['data']['instrument'] == 'synthetic:alice-private'
    assert bound.wrapper.facade is bound.facades['alice']
    assert bound.wrapper.resource_descriptions()
    assert bound.wrapper.resource_templates() == []
    rows = bound.chain.verify(bound.anchor.read())
    identity = 'sha256:' + sha256(b'alice').hexdigest()
    assert [r['event']['phase'] for r in rows] == ['admitted', 'succeeded', 'admitted', 'succeeded']
    assert all(r['event']['principal_ref'] == identity for r in rows)


def test_other_principal_real_resource_never_disclosed_under_original_audit_identity(bound):
    bob, record = bound.references['bob']
    uri = bob.base_uri + '/source/' + record[7:]
    assert bound.facades['bob'].read_resource(uri)
    replace(bound, 'other_facade')
    with pytest.raises(PrincipalRefusal, match='^principal_policy_refused$'):
        bound.wrapper.read_resource(uri)
    assert bound.chain.verify(bound.anchor.read()) == []


@pytest.mark.parametrize('mode', ['other_facade', 'same_principal_facade', 'principal', 'missing', 'invalid_principal'])
@pytest.mark.parametrize('entry', ['call', 'invoke', 'descriptions', 'resource_descriptions', 'resource_templates', 'read_resource'])
def test_rebound_identity_refuses_every_entry_before_audit_or_disclosure(bound, monkeypatch, mode, entry):
    reads = []
    monkeypatch.setattr(bound.wrapper._audit, 'read_anchor', lambda: (reads.append(1), bound.anchor.read())[1])
    replace(bound, mode)
    if entry == 'call':
        assert_private_refusal(bound.wrapper.call(OPERATION, ARGUMENTS))
    else:
        with pytest.raises(PrincipalRefusal, match='^principal_policy_refused$'):
            if entry == 'invoke':
                invoke(OPERATION, ARGUMENTS, host=bound.wrapper)
            elif entry == 'read_resource':
                bound.wrapper.read_resource(bound.uri)
            else:
                getattr(bound.wrapper, entry)()
    assert reads == [] and bound.calls == []
    assert bound.chain.verify(bound.anchor.read()) == []


@pytest.mark.parametrize('mode', ['other_facade', 'same_principal_facade', 'principal'])
def test_observed_rebinding_stays_refused_when_exact_original_is_restored(bound, mode):
    replace(bound, mode)
    assert_private_refusal(bound.wrapper.call(OPERATION, ARGUMENTS))
    restored(bound)
    assert_private_refusal(bound.wrapper.call(OPERATION, ARGUMENTS))
    with pytest.raises(PrincipalRefusal):
        bound.wrapper._check_identity()
    assert bound.calls == [] and bound.chain.verify(bound.anchor.read()) == []
    # A new trusted wrapper may bind the original identity; it gets no grant
    # from this act and must still verify the original chain/independent anchor.
    assert fresh(bound).call(OPERATION, ARGUMENTS)['status'] == 'ok'
    assert bound.calls == ['alice']


@pytest.mark.parametrize('mode', ['other_facade', 'principal'])
@pytest.mark.parametrize('resource', [False, True])
def test_rebinding_during_durable_admission_prevents_actual_dispatch(bound, mode, resource):
    def rebind_after_admission(before, after):
        answer = bound.anchor.cas(before, after)
        if after['sequence'] == 1:
            replace(bound, mode)
        return answer
    bound.wrapper._audit.compare_and_set_anchor = rebind_after_admission
    if resource:
        with pytest.raises(PrincipalRefusal):
            bound.wrapper.read_resource(bound.uri)
    else:
        assert_private_refusal(bound.wrapper.call(OPERATION, ARGUMENTS))
    assert bound.calls == []
    rows = bound.chain.verify(bound.anchor.read())
    assert [r['event']['phase'] for r in rows] == ['admitted', 'refused']
    assert all(r['event']['principal_ref'] == 'sha256:' + sha256(b'alice').hexdigest() for r in rows)


@pytest.mark.parametrize('resource', [False, True])
def test_policy_lock_wait_rechecks_identity_before_current_grants(bound, monkeypatch, resource):
    admitted = threading.Event()
    results, errors, authorizations = [], [], []
    method = 'authorize_resource' if resource else 'authorize'
    original = getattr(bound.policy, method)
    def authorize(*args):
        authorizations.append(args[0])
        return original(*args)
    monkeypatch.setattr(bound.policy, method, authorize)
    def cas(before, after):
        answer = bound.anchor.cas(before, after)
        if after['sequence'] == 1:
            admitted.set()
        return answer
    bound.wrapper._audit.compare_and_set_anchor = cas
    def work():
        try:
            results.append(bound.wrapper.read_resource(bound.uri) if resource
                           else bound.wrapper.call(OPERATION, ARGUMENTS))
        except BaseException as exc:
            errors.append(exc)
    with bound.policy.lock:
        worker = threading.Thread(target=work)
        worker.start()
        assert admitted.wait(5)
        replace(bound, 'principal')
    worker.join(5)
    assert not worker.is_alive()
    assert authorizations == [] and bound.calls == []
    if resource:
        assert results == [] and len(errors) == 1 and type(errors[0]) is PrincipalRefusal
    else:
        assert errors == [] and results == [PrincipalFacade.refusal()]
    assert [r['event']['phase'] for r in bound.chain.verify(bound.anchor.read())] == ['admitted', 'refused']


@pytest.mark.parametrize('entry', ['descriptions', 'resource_descriptions', 'resource_templates'])
def test_discovery_rechecks_identity_before_releasing_rows(bound, monkeypatch, entry):
    facade = bound.facades['alice']
    if entry == 'resource_templates':
        original = type(facade).resource_templates.fget
        def changed(self):
            result = original(self)
            replace(bound, 'other_facade')
            return result
        monkeypatch.setattr(type(facade), 'resource_templates', property(changed))
    else:
        method = 'list_resources' if entry == 'resource_descriptions' else entry
        original = getattr(facade, method)
        def changed():
            result = original()
            replace(bound, 'other_facade')
            return result
        monkeypatch.setattr(facade, method, changed)
    with pytest.raises(PrincipalRefusal):
        getattr(bound.wrapper, entry)()
    assert bound.calls == [] and bound.chain.verify(bound.anchor.read()) == []


@pytest.mark.parametrize('resource', [False, True])
def test_post_dispatch_rebinding_withholds_data_and_preserves_unknown(bound, monkeypatch, resource):
    target = bound.references['alice'][0] if resource else bound.domain['alice']
    method = 'read_resource' if resource else 'call'
    original = getattr(target, method)
    def change_after_real_read(*args):
        result = original(*args)
        replace(bound, 'other_facade')
        return result
    monkeypatch.setattr(target, method, change_after_real_read)
    if resource:
        with pytest.raises(AuditOutcomeUncertain):
            bound.wrapper.read_resource(bound.uri)
    else:
        result = bound.wrapper.call(OPERATION, ARGUMENTS)
        assert result['data'] is None and result['error']['code'] == 'outcome_unknown'
        assert 'alice' not in repr(result) and 'bob' not in repr(result)
        assert bound.calls == ['alice']
    rows = bound.chain.verify(bound.anchor.read())
    assert [r['event']['phase'] for r in rows] == ['admitted', 'unknown']
    assert rows[-1]['event']['effect_state'] == 'unknown'
    restored(bound)
    assert_private_refusal(bound.wrapper.call(OPERATION, ARGUMENTS))
    assert fresh(bound).call(OPERATION, ARGUMENTS)['status'] == 'refused'
    assert bound.chain.verify(bound.anchor.read()) == rows


def test_rebinding_during_terminal_audit_withholds_result_without_rewriting_success(bound):
    def change_after_terminal(before, after):
        answer = bound.anchor.cas(before, after)
        if after['sequence'] == 2:
            replace(bound, 'other_facade')
        return answer
    bound.wrapper._audit.compare_and_set_anchor = change_after_terminal
    result = bound.wrapper.call(OPERATION, ARGUMENTS, call_id='terminal-boundary')
    assert result['data'] is None and result['error'] == {'code': 'outcome_unknown', 'effect_state': 'returned'}
    assert bound.calls == ['alice']
    rows = bound.chain.verify(bound.anchor.read())
    assert [r['event']['phase'] for r in rows] == ['admitted', 'succeeded']
    restored(bound)
    assert_private_refusal(bound.wrapper.call(OPERATION, ARGUMENTS))
    assert fresh(bound).call(OPERATION, ARGUMENTS, call_id='terminal-boundary')['error']['code'] == 'audit_call_already_recorded'
    assert bound.calls == ['alice'] and bound.chain.verify(bound.anchor.read()) == rows


def test_existing_unknown_hold_and_stale_anchor_survive_identity_restoration(bound):
    bound.wrapper._audit.compare_and_set_anchor = lambda before, after: bound.anchor.cas(before, after) if after['sequence'] == 1 else False
    assert bound.wrapper.call(OPERATION, ARGUMENTS)['error']['code'] == 'outcome_unknown'
    held = bound.wrapper._audit._held
    assert held is not None and bound.calls == ['alice']
    replace(bound, 'other_facade')
    assert_private_refusal(bound.wrapper.call(OPERATION, ARGUMENTS))
    restored(bound)
    assert_private_refusal(bound.wrapper.call(OPERATION, ARGUMENTS))
    assert bound.wrapper._audit._held is held
    assert fresh(bound).call(OPERATION, ARGUMENTS)['status'] == 'refused'
    assert bound.calls == ['alice']
