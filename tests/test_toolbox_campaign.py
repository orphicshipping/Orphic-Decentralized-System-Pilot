"""Client recipes over actual campaign profiles and the existing audit boundary.

All captures, keys, principals and policies below are local synthetic fixtures.
These checks establish no hosted identity, live feed or native proof claim.
"""
from copy import deepcopy
from unittest.mock import patch

import pytest

from ciw.context_catalog import for_principal_facade, source_registry
from ciw.context_selector import StaleCatalog
from ciw.principal_policy import PrincipalPolicy
from ciw.toolbox_skills import SkillRegistry, SkillRunner


def policy(principal, names):
    return {'schema': 'ciw.principal-policy.v1', 'revision': 'toolbox-campaign',
            'principals': [{'principal': principal, 'enabled': True,
                'operations': [{'name': name, 'constraints': {}} for name in names],
                'scientific_operations': [], 'resources': [],
                'reference_scopes': [], 'handoff_recipients': []}]}


def recipe(calls):
    return {'schema': 'ncp.client-skill.v1', 'skill_id': 'campaign-inspection', 'version': '1.0.0',
            'description': 'Inspect host-bound local records; no source truth or execution grant.',
            'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'steps': [{'step_id': 'read-' + str(index), 'tool': name,
                       'arguments': {key: {'literal': value} for key, value in args.items()}}
                      for index, (name, args) in enumerate(calls)]}


def runner(facade, calls, *, registry=None, replay='campaign-inspection'):
    return SkillRunner(facade=facade,
        registry=registry or SkillRegistry([recipe(calls)], facade=facade), tenant='local',
        pins={'source': 'synthetic-capture'}, replay_identity=replay, admit_step=lambda _: True)


def run(active, attempt='first'):
    return active.run('campaign-inspection', '1.0.0', {}, attempt=attempt)


def binding(family, *, changed=False):
    if family == 'state':
        from test_state_read_profile import host, capture
        data = capture()
        if changed:
            data['captured_at'] = '2026-11-02T06:01:00Z'
        return host(data), 'test-owner', ('ncp_state_releases', {})
    if family == 'intake':
        from test_offline_intake import host, fixture
        data = fixture()
        if changed:
            data['records'][1]['observation']['value'] = 22.5
        return host(data), 'owner-a', ('ncp_intake_describe', {})
    if family == 'domain_read':
        from test_domain_read_profile import host, capture
        data = capture()
        if changed:
            data['records'][0]['data']['price'] = '2.00'
        return host(data), 'alice', ('ncp_domain_read_describe', {'domain': 'finance'})
    from test_proof_profile import host
    from test_proof_registry import registry_document
    from ciw.proof_registry import ProofKeyRegistry
    document = registry_document()
    if changed:
        document['version'] = 2
        document['entries'][0]['enabled'] = False
    return host(ProofKeyRegistry(document)), 'fixture-owner', ('ncp_proof_describe', {})


@pytest.mark.parametrize('family', ['state', 'intake', 'domain_read', 'proof'])
def test_capture_or_trust_rebind_invalidates_selection_and_retained_receipts(tmp_path, monkeypatch, family):
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path/'retained'))
    profile, principal, call = binding(family)
    document = policy(principal, [call[0]])
    facade = PrincipalPolicy(lambda: document).facade(principal, **{family: profile})
    selector = for_principal_facade(facade)
    with patch.object(profile, 'call', side_effect=AssertionError('discovery dispatched')):
        selected = selector.select([call[0]])
    assert selected['authorizes_execution'] is False
    active = runner(facade, [call])
    result = run(active)
    assert result['status'] == 'completed' and result['steps_completed'] == 1
    original = active.receipt(result['artifact_id'])
    assert original['receipt']['freshness_or_truth_established'] is False

    # The same immutable capture can reopen existing records, without replay.
    with facade.policy.lock:
        facade.hosts[family] = binding(family)[0]
    reopened = runner(facade, [call])
    assert reopened.receipt(result['artifact_id']) == original
    with pytest.raises(ValueError, match='attempt'):
        run(reopened)

    with facade.policy.lock:
        facade.hosts[family] = binding(family, changed=True)[0]
    with pytest.raises(StaleCatalog):
        selector.selected()
    assert selector.selected()['tools'] == []
    # Both the first stale refresh and later stable reads refuse old disclosure.
    for _ in range(2):
        with pytest.raises(ValueError):
            active.receipt(result['artifact_id'])
    with pytest.raises(ValueError, match='scope_changed'):
        runner(facade, [call]).receipt(result['artifact_id'])


def test_current_narrowed_schema_binds_without_widening_domain_grant(tmp_path, monkeypatch):
    from test_domain_read_profile import host
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path/'retained'))
    profile = host(allowed_domains=['finance'])
    call = ('ncp_domain_read_record', {'domain': 'finance', 'record_id': 'record:../inert/path'})
    document = policy('alice', [call[0]])
    facade = PrincipalPolicy(lambda: document).facade('alice', domain_read=profile)
    active = runner(facade, [call])
    assert run(active)['status'] == 'completed'
    assert profile.descriptions()[0]['inputSchema']['properties']['domain']['enum'] == ['finance']
    static = SkillRegistry([recipe([call])])
    assert run(runner(facade, [call], registry=static, replay='static-source'))['status'] == 'refused'
    with facade.policy.lock:
        facade.hosts['domain_read'] = host(allowed_domains=['finance', 'trade'])
    with pytest.raises(StaleCatalog):
        run(active, 'new-attempt')
    # Stable broader discovery still cannot satisfy the old exact recipe hash.
    assert run(active, 'new-attempt')['status'] == 'refused'


def test_registry_change_during_discovery_does_not_mix_snapshots():
    from test_proof_profile import host
    from test_proof_registry import registry_document
    profile = host()
    document = policy('fixture-owner', ['ncp_proof_describe'])
    facade = PrincipalPolicy(lambda: document).facade('fixture-owner', proof=profile)
    selector = for_principal_facade(facade)
    describe = profile.descriptions
    replacement = registry_document()
    replacement['version'] = 2
    replacement['entries'][0]['enabled'] = False
    def change():
        rows = describe()
        profile.registry.replace(replacement)
        return rows
    with patch.object(profile, 'descriptions', side_effect=change):
        with pytest.raises(ValueError, match='binding_changed_during_discovery'):
            selector.select(['ncp_proof_describe'])


def test_runner_fixed_owner_refuses_first_and_second_principal_rebind(tmp_path, monkeypatch):
    from reference_fixture_helpers import make_catalog
    from ciw.services import open_reference
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path/'retained'))
    corpus, _, _ = make_catalog(tmp_path/'reference', b'Synthetic source.\n')
    reference = open_reference(catalog_path=tmp_path/'reference/catalog-inputs/bundle', catalog_ref=corpus.ref)
    document = policy('alice', ['ncp_knowledge_overview'])
    document['principals'][0]['reference_scopes'] = [corpus.ref]
    bob = deepcopy(document['principals'][0]); bob['principal'] = 'bob'
    document['principals'].append(bob)
    facade = PrincipalPolicy(lambda: document).facade('alice', reference=reference)
    active = runner(facade, [('ncp_knowledge_overview', {})])
    result = run(active)
    assert result['status'] == 'completed'
    facade.principal = 'bob'
    for _ in range(2):
        with pytest.raises(ValueError, match='owner_binding_changed'):
            run(active, 'bob-attempt')
        with pytest.raises(ValueError, match='owner_binding_changed'):
            active.receipt(result['artifact_id'])
        with pytest.raises(ValueError, match='owner_binding_changed'):
            active.cancel('first')


def test_client_preserves_actual_audit_allowlist_and_dispatch(tmp_path, monkeypatch):
    from test_campaign_audited_host import bound
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path/'retained'))
    wrapped, raw, _, anchor = bound(tmp_path)
    selector = for_principal_facade(wrapped)
    names = {row['name'] for row in selector.list()['items']}
    assert names == wrapped._audit.chain.allowed
    assert 'ncp_domain_read_list' not in names
    with pytest.raises(ValueError, match='unknown_tool'):
        runner(wrapped, [('ncp_domain_read_list', {'domain': 'finance'})])
    calls = [('ncp_domain_read_describe', {'domain': 'finance'}),
             ('ncp_domain_read_record', {'domain': 'finance', 'record_id': 'record:../inert/path'})]
    with patch.object(raw, 'call', wraps=raw.call) as dispatch:
        assert run(runner(wrapped, calls))['status'] == 'completed'
    assert dispatch.call_count == 2
    journal = wrapped._audit.chain.verify(anchor.read())
    assert [row['event']['phase'] for row in journal] == ['admitted', 'succeeded'] * 2


def test_audit_unknown_stops_dependency_and_survives_new_client_attempt(tmp_path, monkeypatch):
    from test_campaign_audited_host import Anchor, bound
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path/'retained'))
    anchor = Anchor(tmp_path/'anchor.sqlite')
    writes = []
    def fail_terminal(before, after):
        writes.append(after)
        return anchor.cas(before, after) if len(writes) == 1 else False
    wrapped, raw, _, _ = bound(tmp_path, cas=fail_terminal)
    calls = [('ncp_domain_read_describe', {'domain': 'finance'})] * 2
    active = runner(wrapped, calls)
    with patch.object(raw, 'call', wraps=raw.call) as dispatch:
        result = run(active)
        assert result['status'] == 'outcome_unknown' and result['steps_completed'] == 0
        assert run(active, 'second')['status'] == 'outcome_unknown'
        assert run(runner(wrapped, calls, replay='new-client'))['status'] == 'outcome_unknown'
    assert dispatch.call_count == 1
    assert active.receipt(result['artifact_id'])['receipt']['status'] == 'outcome_unknown'


def test_source_registry_contains_exact_campaign_manifests():
    from ciw.offline_intake import profile_manifest as intake
    from ciw.proof_profile import profile_manifest as proof
    from ciw.domain_read_profile import profile_manifest as domain
    from ciw.mcp_toolbox import profile_manifest as toolbox
    from ciw.live_intake import profile_manifest as live
    from ciw.metrology_operations import profile_manifest as metrology
    from ciw.data_database import profile_manifest as data_database
    catalog = source_registry()
    assert catalog['intake'] == intake()['tools']
    assert catalog['proof'] == proof()['tools']
    assert catalog['domain_read'] == domain()['tools']
    assert catalog['toolbox'] == toolbox()['tools']
    assert catalog['live'] == live()['tools']
    assert catalog['metrology'] == metrology()['tools']
    assert catalog['data_database'] == data_database()['tools']
    assert len(catalog['data_database']) == 7
    assert sum(map(len, catalog.values())) == 111
    assert not {'finance', 'trade', 'gis'} & catalog.keys()


@pytest.mark.parametrize('family,variable', [
    ('recompiler', 'CIW_NSR_CHECKOUT'), ('schematic', 'CIW_SRA_CHECKOUT'),
    ('clocksync', 'CIW_CLOCKSYNC_CHECKOUT'), ('nise', 'CIW_NISE_CHECKOUT')])
def test_actual_native_binding_discovery_never_relaunches_provider(family, variable, monkeypatch):
    import os
    import sys
    from pathlib import Path
    from ciw import recompiler_profile, schematic_profile, clocksync_profile, nise_profile
    from test_campaign_native_policy import selected
    if not os.environ.get(variable):
        pytest.skip(variable + ' requires the approved pinned checkout')
    options = {'checkout': Path(os.environ[variable]), 'principal': 'native-owner'}
    if family == 'schematic':
        profile = schematic_profile.SchematicHost(**options, python=sys.executable)
    else:
        python = os.environ.get('CIW_NISE_PYTHON') if family == 'nise' else sys.executable
        if not python:
            pytest.skip('CIW_NISE_PYTHON requires the approved interpreter')
        factory = {'recompiler': recompiler_profile.RecompilerHost,
                   'clocksync': clocksync_profile.ClockSyncHost, 'nise': nise_profile.NiseHost}[family]
        profile = factory(**options, python_executable=python)
    try:
        names = [row['name'] for row in profile.descriptions()]
        document = policy('native-owner', names)
        facade = PrincipalPolicy(lambda: document).facade('native-owner', **{family: profile})
        selector = for_principal_facade(facade)
        def no_launch(*args, **kwargs):
            raise AssertionError('discovery relaunched native runtime')
        monkeypatch.setattr(profile._adapter, '_run', no_launch)
        monkeypatch.setattr(profile._adapter, 'runtime_identity', no_launch)
        assert selector.select(names)['tools'] == sorted(profile.descriptions(), key=lambda row: row['name'])
        with facade.policy.lock:
            profile._adapter.process_policy = selected()
        with pytest.raises(StaleCatalog):
            selector.selected()
        assert selector.selected()['tools'] == []
    finally:
        if family == 'recompiler':
            profile._lease_directory.cleanup()
        else:
            profile.close()
