"""Existing native CSE operations run through the common admitted-plan owner."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from ciw.agent_mcp import native_bindings, from_profile
from ciw.control_gateway import ControlHost
from ciw import metrology_clearance as clearance


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    source = Path(os.environ.get('NCP_CSE_SOURCE', root / 'instruments/domain/bim-estimator'))
    destination = tmp_path_factory.mktemp('native-control') / 'cse'
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache', '.venv'))
    def git(*args):
        return subprocess.check_output(['git', '-C', str(destination), *args], stderr=subprocess.PIPE).decode().strip()
    git('init', '--quiet'); git('config', 'core.autocrlf', 'false'); git('add', '-f', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Exact CSE tree fixture')
    assert git('rev-parse', 'HEAD^{tree}') == clearance.NATIVE_TREE
    return destination, git('rev-parse', 'HEAD')


@pytest.mark.parametrize('instrument,options', [
    ('unknown', {}),
    ('builtin', {'checkout': Path('/unapproved')}),
    ('bim-clearance', {}),
    ('bim-clearance', {'checkout': Path('relative'), 'revision': 'a' * 40}),
    ('bim-clearance', {'checkout': Path('/provider'), 'revision': 'main'}),
    ('bim-clearance', {'checkout': Path('/provider'), 'revision': 'a' * 40, 'python': Path('python')}),
    ('leakage', {'checkout': Path('/provider'), 'revision': 'a' * 40}),
])
def test_native_flags_fail_closed_before_any_provider_construction(monkeypatch, instrument, options):
    from ciw import leakage_native
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid host configuration reached a provider')
    monkeypatch.setattr(clearance, 'Provider', forbidden)
    monkeypatch.setattr(leakage_native, 'NativeLeakageBackend', forbidden)
    with pytest.raises(ValueError):
        native_bindings(instrument, **options)


def test_real_native_clearance_plan_retry_fresh_occurrence_and_resume(native, tmp_path):
    source = Path(os.environ.get('NCP_CLEARANCE_EXAMPLES', Path(__file__).resolve().parents[1] / 'examples/metrology-clearance'))
    profile = clearance.prepare((source / 'uncertain-inspection.json').read_bytes(), tmp_path / 'profile')
    kwargs = native_bindings('bim-clearance', checkout=native[0], revision=native[1], python=Path(sys.executable))
    def call(host, name, **arguments):
        response = host.call(name, arguments)
        assert response['status'] == 'ok', response
        return response['data']
    def finished(host, identity):
        end = time.monotonic() + 45
        while time.monotonic() < end:
            run = call(host, 'ncp_run_get', run_id=identity)
            if run['state'] not in {'queued', 'running'}:
                return run
            time.sleep(.02)
        pytest.fail('Bounded CSE inspection did not finish')
    with from_profile(profile, instrument='bim-clearance', **kwargs) as owner, ControlHost(owner) as host:
        capabilities = call(host, 'ncp_capabilities_list')['items']
        assert len(capabilities) == 1 and capabilities[0]['operation_id'] == clearance.OP
        template = call(host, 'ncp_plan_template', baseline='baseline')
        plan = call(host, 'ncp_plan_create', source='source', baseline='baseline', graph=template['graph'])
        args = {'plan_id': plan['plan_id'], 'expected_digest': plan['digest']}
        assert host.call('ncp_run_submit', {**args, 'attempt': 'unadmitted'})['status'] == 'refused'
        call(host, 'ncp_plan_admit', **args)
        submitted = call(host, 'ncp_run_submit', **args, attempt='native-first')
        run = finished(host, submitted['run_id'])
        assert run['state'] == 'completed', run
        same = call(host, 'ncp_run_submit', **args, attempt='native-first')
        assert same['run_id'] == run['run_id']
        fresh = finished(host, call(host, 'ncp_run_submit', **args, attempt='native-fresh')['run_id'])
        assert fresh['result']['execution_ids'] != run['result']['execution_ids']
        artifact = run['result']['artifacts']['graph-run']['artifact_id']
        payload = call(host, 'ncp_artifact_read', artifact=artifact,
                       selector=['nodes', 'assess', 'result', 'data'])['data']
        assert payload['authority'] == clearance.AUTHORITY
        assert payload['native']['posterior']['verdict'] == 'UNRESOLVED'
    with from_profile(profile, instrument='bim-clearance', resume=True, **kwargs) as owner, ControlHost(owner, resume=True) as host:
        recovered = call(host, 'ncp_run_get', run_id=run['run_id'])
        assert recovered == run
        assert call(host, 'ncp_run_submit', **args, attempt='native-first') == run
        assert owner.capabilities()['execution_budget']['used'] == 2
