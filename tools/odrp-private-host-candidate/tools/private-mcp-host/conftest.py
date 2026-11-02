"""Synthetic host bindings for local adapter qualification; no live credentials."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import uuid

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'tests'))
import adapter
from ciw.control_gateway import TOOLS
from ciw.numerical_contracts import OPERATIONS
from ciw.principal_policy import PrincipalPolicy
from ciw.reference_profile import REFERENCE_NAMES


RELEASE = {'schema': 'odsp.private-release-identity.v1', 'repository': 'synthetic/fixture',
           'source_commit': 'a' * 40, 'source_tree': 'b' * 40,
           'wheel_sha256': 'c' * 64, 'package_digest': 'd' * 64}


class Fixture:
    def __init__(self, root, monkeypatch):
        self.root = root
        self.policy_path = root / 'host-policy.json'
        self.policy_data = {'schema': 'ciw.principal-policy.v1', 'revision': 'fixture-1', 'principals': [
            {'principal': principal, 'enabled': True,
             'operations': [{'name': name, 'constraints': {}} for name in sorted(set(TOOLS) | set(REFERENCE_NAMES))],
             'scientific_operations': [*OPERATIONS, 'sequence.markov.v1'],
             'resources': ['ncp://plans/', 'ncp://runs/', 'ncp://artifacts/', 'ncp://retained/'],
             'reference_scopes': [], 'handoff_recipients': ['beta'] if principal == 'alpha' else []}
            for principal in ('alpha', 'beta')]}
        self.write_policy()
        self.policy = PrincipalPolicy.from_file(self.policy_path)
        self.config = adapter.Configuration('fixture-owner', 'fixture-generation', RELEASE, self.policy_path, self.policy)
        monkeypatch.setattr(adapter, 'STATE', root / 'runtime' / 'state')
        monkeypatch.setattr(adapter, 'configuration', lambda: self.config)
        monkeypatch.setattr(adapter, 'LOCK', asyncio.Lock())
        monkeypatch.setattr(adapter, 'LOCAL_FENCES', set())
        monkeypatch.setattr(adapter, 'ACTIVE', set())
        monkeypatch.setattr(adapter, 'UNCERTAIN_WORKERS', {})
        monkeypatch.setattr(adapter, 'INFLIGHT', 0)
        for name in ('ODRP_CATALOG', 'ODRP_CATALOG_REF', 'ODRP_EVIDENCE', 'ODRP_EVIDENCE_REF'):
            monkeypatch.delenv(name, raising=False)
        self.state, self.base_commit = None, None

    def write_policy(self):
        self.policy_path.write_text(json.dumps(self.policy_data), encoding='utf-8')

    def body(self, action='tools', payload=None, principal='alpha'):
        return {'state': self.state, 'action': action, 'payload': payload or {},
                'identity': self.policy.context(principal),
                'transaction': {'id': str(uuid.uuid4()), 'owner_id': self.config.owner_id,
                                'generation': self.config.generation, 'epoch': 1,
                                'base_commit': self.base_commit, 'release': deepcopy(RELEASE)}}

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=adapter.app), base_url='http://container')

    async def request(self, action='tools', payload=None, principal='alpha'):
        body = self.body(action, payload, principal)
        async with self.client() as client:
            result = await client.post('/transaction', json=body)
        if result.status_code == 200:
            value = result.json()
            assert value['transaction'] == body['transaction']
            assert value['identity'] == body['identity']
            self.state, self.base_commit = value['state'], body['transaction']['id']
        return result

    async def call(self, name, arguments, principal='alpha', confirmed=True):
        result = await self.request('call', {'name': name, 'arguments_json': json.dumps(arguments), 'confirmed': confirmed}, principal)
        assert result.status_code == 200, result.text
        envelope = result.json()['result']
        assert json.loads(envelope['raw_json']) == envelope['data']
        assert not envelope['data'].get('isError'), envelope
        return envelope['data']['structuredContent']['data']


@pytest.fixture
def host(tmp_path, monkeypatch):
    return Fixture(tmp_path, monkeypatch)
