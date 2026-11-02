"""Separate profile grants, retained-read scope and shared public refusals."""
import json
import shutil
from types import SimpleNamespace

import pytest

from ciw import services
from ciw.control_gateway import ControlHost
from ciw.logistics_profile import LogisticsHost, MAX_OUTPUT, raw
from logistics_fixture_helpers import Fixture
from test_state_read_profile import host as state_host


@pytest.fixture
def logistics_factory(tmp_path, monkeypatch):
    node = shutil.which('node')
    if node is None:
        pytest.skip('OLM canonical digest requires a host-selected Node executable')
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path))
    def factory(**overrides):
        bindings = dict(transport=Fixture(), principal='review-owner', tenant='review-tenant',
                        roles=['pilot:agent-read', 'pilot:compute-read'], allowed_kinds=['catalog','compute'],
                        node=node, synthetic=True)
        bindings.update(overrides)
        return LogisticsHost(**bindings)
    return factory


@pytest.mark.parametrize('overrides', [
    {'roles':[]}, {'roles':['pilot:agent-read']},
    {'roles':['pilot:compute-read']}, {'allowed_kinds':['compute']},
])
def test_retained_read_rechecks_current_host_scope(logistics_factory, overrides):
    artifact = services.invoke('ncp_logistics_catalog', {}, host=logistics_factory())['artifact_id']
    narrowed = logistics_factory(**overrides)
    assert narrowed.call('ncp_logistics_catalog', {})['status'] == 'refused'
    assert narrowed.call('ncp_logistics_retained_read', {'artifact_id':artifact})['status'] == 'refused'
    with pytest.raises(ValueError, match='^logistics_read_refused$'):
        services.invoke('ncp_logistics_retained_read', {'artifact_id':artifact}, host=narrowed)
    assert narrowed.transport.calls == []


def test_tenant_isolation_and_invalid_saved_selector(logistics_factory):
    owner = logistics_factory()
    artifact = services.invoke('ncp_logistics_catalog', {}, host=owner)['artifact_id']
    with pytest.raises(ValueError):
        services.invoke('ncp_logistics_retained_read', {'artifact_id':artifact},
                        host=logistics_factory(tenant='another-tenant'))
    saved = owner.store.native(artifact)
    saved['source'] = {'kind':'catalog', 'principal':'injected'}
    tampered = owner.store.keep(saved, kind='olm-retained-read', catalog={})
    assert owner.call('ncp_logistics_retained_read', {'artifact_id':tampered})['status'] == 'refused'


def test_sdk_and_mcp_hide_transport_exception_details(logistics_factory):
    def broken(_):
        raise OSError('synthetic transport token must not enter a public refusal')
    host = logistics_factory(transport=broken)
    response = host.call('ncp_logistics_catalog', {})
    assert response == {'status':'refused','reason':'logistics_read_refused','authorizes_execution':False}
    with pytest.raises(ValueError, match='^logistics_read_refused$'):
        services.invoke('ncp_logistics_catalog', {}, host=host)
    assert 'synthetic transport token' not in json.dumps(host.mcp_result('ncp_logistics_catalog', response))


def test_profiles_are_discovered_without_composing_control_grants(logistics_factory):
    manifests = {p['profile_id']:p for p in services.capability_map()['reference_profiles']}
    assert set(manifests) == {'odrp.reference.v1','nsl.state-ledger.v1','olm.logistics.v1'}
    for profile in manifests.values():
        assert profile['authorizes_execution'] is False
        assert all(tool['annotations']['readOnlyHint'] for tool in profile['tools'])
    control = object.__new__(ControlHost)
    control._closed = False
    control._store = SimpleNamespace(check_current=lambda: None)
    for name in ['ncp_state_releases','ncp_logistics_catalog']:
        assert control.call(name, {})['status'] == 'refused'
    with pytest.raises(ValueError, match='state_host_required'):
        services.invoke('ncp_state_releases', {}, host=logistics_factory())
    with pytest.raises(ValueError, match='logistics_host_required'):
        services.invoke('ncp_logistics_catalog', {}, host=state_host())
    with pytest.raises(ValueError, match='reference_host_required'):
        services.invoke('ncp_knowledge_overview', {}, host=logistics_factory())


@pytest.mark.parametrize('payload', ['\\' * 50000, '\u2603' * 10000, 'x' * 300000],
                         ids=['escaped-backslashes', 'unicode', 'retained-large-value'])
def test_logistics_sdk_and_complete_mcp_envelope_are_bounded(logistics_factory, payload):
    host = logistics_factory(transport=Fixture(payload={'blob':payload}))
    data = services.invoke('ncp_logistics_catalog', {}, host=host)
    response = {'status':'ok', 'data':data}
    assert len(raw(data)) <= MAX_OUTPUT
    assert len(raw(host.mcp_result('ncp_logistics_catalog', response))) <= MAX_OUTPUT
    page = services.invoke('ncp_logistics_retained_read', {'artifact_id':data['artifact_id']}, host=host)
    assert len(raw(host.mcp_result('ncp_logistics_retained_read', {'status':'ok','data':page}))) <= MAX_OUTPUT


def test_discovery_schema_mutation_cannot_change_later_discovery(logistics_factory):
    host = logistics_factory()
    first = host.descriptions()
    first[0]['inputSchema']['additionalProperties'] = True
    first[0]['annotations']['readOnlyHint'] = False
    assert host.descriptions()[0]['inputSchema']['additionalProperties'] is False
    assert host.descriptions()[0]['annotations']['readOnlyHint'] is True
