"""Reference disclosure remains read-only and shares current principal policy."""
import asyncio
import base64
import io
import json
import tarfile

import pytest
import adapter
from reference_fixture_helpers import make_catalog
from test_adapter import archive, mock_bridge


def test_historical_and_bad_archive_preserve_existing_state(host):
    old = adapter.STATE / 'transactions' / 'old' / 'state'
    old.mkdir(parents=True)
    (old / 'sentinel').write_text('preserve')
    historical = archive([('operator.json', b'{}', tarfile.REGTYPE)])
    with pytest.raises(adapter.Refused, match='historical_checkpoint'):
        adapter.restore(historical, body=host.body(), config=host.config)
    with pytest.raises(adapter.Refused):
        adapter.restore('not-base64', body=host.body(), config=host.config)
    assert (old / 'sentinel').read_text() == 'preserve'


def bind_reference(host, monkeypatch):
    corpus, record, path = make_catalog(host.root / 'input', b'Synthetic calibration reference.\n')
    monkeypatch.setenv('ODRP_CATALOG', str(host.root / 'input/catalog-inputs/bundle'))
    monkeypatch.setenv('ODRP_CATALOG_REF', corpus.ref)
    host.policy_data['principals'][0]['reference_scopes'] = [corpus.ref]
    host.policy_data['principals'][0]['resources'].append('ncp://proto-corpus/' + corpus.ref[7:] + '/')
    host.write_policy()
    return corpus


def test_reference_binding_is_host_only_and_scope_is_current(host, monkeypatch):
    monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: mock_bridge())
    bind_reference(host, monkeypatch)
    async def run():
        listed = await host.request()
        assert listed.status_code == 200, listed.text
        assert 'ncp_knowledge_search' in {row['name'] for row in listed.json()['result']['data']}
        await host.call('ncp_knowledge_search', {'query': 'calibration'})
        invalid = await host.request('call', {'name': 'ncp_knowledge_search', 'arguments': {'query': 'calibration', 'path': '/other'}})
        assert invalid.status_code == 403
        # A different enabled principal has no reference scope. Discovery and
        # calls disclose no corpus even though the global checkpoint is shared.
        other = await host.request('call', {'name': 'ncp_knowledge_search', 'arguments': {'query': 'calibration'}}, principal='beta')
        assert other.status_code == 200
        assert other.json()['result']['data']['isError']
        assert 'Synthetic' not in other.text
        host.policy_data['principals'][0]['reference_scopes'] = []
        host.policy_data['revision'] = 'reduced-reference'
        host.write_policy()
        reduced = await host.request('call', {'name': 'ncp_knowledge_search', 'arguments': {'query': 'calibration'}})
        assert reduced.status_code == 200
        assert reduced.json()['result']['data']['isError']
    asyncio.run(run())


def test_no_binding_refuses_without_opening_scientific_host(host, monkeypatch):
    bridge = mock_bridge()
    monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: bridge)
    async def run():
        result = await host.request('call', {'name': 'ncp_knowledge_search', 'arguments': {'query': 'calibration'}})
        assert result.status_code == 409
        assert result.json()['error']['code'] == 'reference_not_configured'
        assert not bridge.open.called
        assert not adapter.STATE.exists()
    asyncio.run(run())


def test_checkpoint_cannot_rebind_frozen_profile_or_carry_policy(host, monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: mock_bridge())
    async def run():
        result = await host.request()
        assert result.status_code == 200, result.text
    asyncio.run(run())
    original = host.state
    for transform in ('profile', 'policy'):
        root = tmp_path / transform
        root.mkdir()
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(original)), mode='r:gz') as stream:
            stream.extractall(root, filter='data')
        if transform == 'profile':
            profile = next((root / 'principals').glob('*/profile/profile.json'))
            value = json.loads(profile.read_text())
            value['inputs']['source'] = '/private/source'
            profile.write_text(json.dumps(value))
        else:
            (root / 'policy.json').write_text('{}')
        altered = adapter.checkpoint(root)
        with pytest.raises(adapter.Refused, match='frozen_profile_mismatch|unsafe_checkpoint_entry'):
            adapter.restore(altered, body=host.body(), config=host.config)


def test_checkpoint_owner_and_generation_cannot_be_transferred(host, monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, 'PrincipalBridge', lambda *a: mock_bridge())
    async def run():
        result = await host.request()
        assert result.status_code == 200, result.text
    asyncio.run(run())
    original = host.state
    for key, value in (('owner_id', 'other-deployment'), ('generation', 'historical')):
        root = tmp_path / key
        root.mkdir()
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(original)), mode='r:gz') as stream:
            stream.extractall(root, filter='data')
        path = root / 'deployment-generation.json'
        marker = json.loads(path.read_text())
        marker[key] = value
        path.write_text(json.dumps(marker))
        with pytest.raises(adapter.Refused, match='checkpoint_binding_refused'):
            adapter.restore(adapter.checkpoint(root), body=host.body(), config=host.config)
