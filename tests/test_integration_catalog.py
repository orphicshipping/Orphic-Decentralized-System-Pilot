"""The source map is navigation, never a provider binding or qualification."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from ciw import integration_catalog as catalog
from ciw import operator_catalog
from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import ControlHost


def test_all_import_declarations_and_licences_match_authoritative_manifest():
    root = Path(__file__).resolve().parents[1]
    manifest = Path(os.environ.get('NCP_IMPORT_MANIFEST', root / 'instruments/manifest.json'))
    original = json.loads(manifest.read_bytes())['modules']
    rows = catalog.listing(limit=32)['items']
    assert len(rows) == 21
    for expected in original:
        row = next(r for r in rows if r['id'] == expected['id'])
        assert {key: row[key] for key in expected} == expected
        assert row['default_activation'] is False
        assert row['authorizes_execution'] is False
        assert row['qualification'] == catalog.QUALIFICATION
    assert catalog.summary()['source_files']['instruments/manifest.json'] == sha256(manifest.read_bytes()).hexdigest()
    for module in ('frame-mapper', 'real-time-globe'):
        row = catalog.describe('imports', module)['item']
        assert row['runtime_revision'] is None
        assert row['build_kind'] == 'npm'
        assert row['workflow_connections'] == []


def test_current_and_historical_profile_pins_remain_separate_without_probes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Discovery contacted a provider or Git process')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    from ciw.workbench import Workbench
    from ciw.control_plane import CapabilityRegistry
    monkeypatch.setattr(Workbench, 'bind_workflow', forbidden)
    monkeypatch.setattr(CapabilityRegistry, 'bind', forbidden)
    row = catalog.describe('imports', 'state-inference')['item']
    pins = {r['workflow']: r['runtime_pin']['revision'] for r in row['workflow_connections']}
    assert row['runtime_revision'] == '5241eee6dab434533bdf0cf0e824bc43b4a79831'
    assert pins['calibrated-observable'] == row['runtime_revision']
    assert pins['calibrated-window'] == 'de38873db1ba98744d395dfa7df1d3b5b7bb3059'
    assert row['runtime_revision_scope'].startswith('module_default_declaration')
    cse = catalog.describe('imports', 'bim-estimator')['item']
    assert cse['instrument_connections'][0]['source_revision'] == cse['import_revision']
    assert cse['instrument_connections'][0]['required_source_tree'] == cse['import_tree']
    assert cse['workflow_connections'][0]['runtime_pin']['revision'] != cse['import_revision']
    assert operator_catalog.catalog()['integration_sources']['counts'] == {'imports': 21, 'systems': 26, 'external': 19}


def test_detached_pages_have_unique_ids_and_preserve_pending_boundaries():
    for section, total in [('imports', 21), ('systems', 26), ('external', 19)]:
        rows = []
        offset = 0
        while True:
            page = catalog.listing(section, offset=offset, limit=5)
            rows.extend(page['items'])
            assert page['total'] == total
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
        assert len({row['id'] for row in rows}) == total
        assert all(re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', row['id']) for row in rows)
        assert all(row['authorizes_execution'] is False for row in rows)
    row = catalog.describe('external', 'openobserve')
    assert row['item']['declared_boundary'] == 'planning_contract_only'
    row['item']['declared_boundary'] = 'qualified'
    assert catalog.describe('external', 'openobserve')['item']['declared_boundary'] == 'planning_contract_only'


@pytest.mark.parametrize('section,offset,limit', [('other', 0, 1), ('imports', True, 1),
    ('imports', -1, 1), ('imports', 0, False), ('imports', 0, 33)])
def test_invalid_selectors_are_refused(section, offset, limit):
    with pytest.raises(ValueError):
        catalog.listing(section, offset=offset, limit=limit)


def test_mcp_catalog_preserves_grants_and_execution_budget(tmp_path):
    profile = demo_config(tmp_path / 'profile')
    with from_profile(profile) as owner, ControlHost(owner) as host:
        before = deepcopy(owner.capabilities())
        response = host.call('ncp_integrations_list', {'section': 'imports', 'limit': 32})
        assert response['status'] == 'ok' and response['data']['total'] == 21
        source = host.call('ncp_integration_describe', {'section': 'imports', 'source_id': 'calibration'})
        assert source['data']['item']['license'] == 'MPL-2.0'
        assert source['data']['item']['workflow_connections']
        for bad in ('../../secret', 'https://example.invalid/provider', 'unlisted'):
            assert host.call('ncp_integration_describe', {'section': 'imports', 'source_id': bad})['status'] == 'refused'
        assert owner.capabilities() == before
        for item in host.descriptions():
            if item['name'].startswith('ncp_integration'):
                assert item['annotations']['readOnlyHint'] is True
