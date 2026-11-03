"""Real native numerical runs and durable principal-bound workflow regressions."""
import asyncio
import base64
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import os
from io import StringIO
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ciw.agent_api import encode, parse
from ciw.agent_mcp import from_profile
from ciw import metrology_clearance as clearance
from ciw.link_store import Journal
from ciw.link_study import OPS
from ciw.link_study_cli import example
from ciw.metrology_operations import AUTHORITY, PROFILE, TOOLS, MetrologyHost, MetrologyJob, main, profile_manifest
from ciw.operational_agent import OperationalHost
from ciw.operational_cli import prepare, default_context
from ciw.operational_store import Records
from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
from ciw.services import invoke

ROOT = Path(__file__).resolve().parents[1]


def scalar_profile(root):
    request = example(); capture = request['capture']
    profile = prepare(root, investigation_id='native-scalar', sources={'capture': {
        'raw': encode(capture), 'family': 'link-capture', 'context': default_context(
            producer=capture['manifest']['device_id'], clock=capture['manifest']['clock']['id'],
            boot=capture['boot_id'], origin='synthetic'), 'configurations': []}},
        models={'scalar': {'source': 'capture', 'engine': 'scalar', 'request': encode(request),
            'policy': {'unit': 'N', 'coverage_factor': 2, 'max_abs_error': .1, 'max_standard_uncertainty': .1}}})
    return profile, request


def policy_document(operations):
    return {'schema': 'ciw.principal-policy.v1', 'revision': 'metrology-test', 'principals': [
        {'principal': p, 'enabled': True, 'operations': [{'name': n, 'constraints': {}} for n in TOOLS],
         'scientific_operations': list(operations), 'resources': [], 'reference_scopes': [], 'handoff_recipients': []}
        for p in ('alice', 'bob')]}


@pytest.fixture
def configured(tmp_path):
    profile, request = scalar_profile(tmp_path / 'native')
    with OperationalHost(profile) as native:
        job = MetrologyJob.operational(native, 'scalar')
        document = policy_document(job.operations)
        policy = PrincipalPolicy(lambda: document)
        host = MetrologyHost(policy=policy, principal='alice', tenant='lab', jobs={'scalar': job},
                             journal=tmp_path / 'workflow.sqlite', create=True)
        facade = policy.facade('alice', metrology=host)
        yield SimpleNamespace(host=host, facade=facade, native=native, job=job, profile=profile,
            policy=policy, document=document, grant=document['principals'][0], request=request, root=tmp_path)


def run(c, attempt='first'):
    return invoke('ncp_metrology_run', {'job': 'scalar', 'plan_ref': c.job.plan_ref, 'attempt': attempt}, host=c.facade)


def unknown_run(c, attempt='first'):
    response = c.facade.call('ncp_metrology_run', {'job': 'scalar', 'plan_ref': c.job.plan_ref, 'attempt': attempt})
    assert response['status'] == 'refused' and response['error']['code'] == 'outcome_unknown'
    assert response['data'] is None
    return invoke('ncp_metrology_status', {'job': 'scalar', 'attempt': attempt}, host=c.facade)


def read(c, artifact, attempt=None, **extra):
    raw = bytearray(); offset = 0
    while True:
        args = {'job': 'scalar', 'artifact': artifact, 'offset': offset, 'limit': 1000, **extra}
        if attempt is not None:
            args['attempt'] = attempt
        row = invoke('ncp_metrology_read', args, host=c.facade)
        raw.extend(base64.b64decode(row['bytes_b64'], validate=True))
        if row['next_offset'] is None:
            assert row['byte_count'] == len(raw)
            assert row['sha256'] == 'sha256:' + sha256(raw).hexdigest()
            return bytes(raw)
        offset = row['next_offset']


def test_real_scalar_facade_preserves_covariance_source_and_calibration_limits(configured):
    c = configured
    catalog = invoke('ncp_metrology_catalog', {}, host=c.facade)
    assert catalog['jobs'][0]['plan_ref'] == c.job.plan_ref
    assert catalog['jobs'][0]['currently_executable'] is True
    assert read(c, 'source') == encode(c.request)
    calibration = parse(read(c, 'calibration'))
    assert calibration['declaration'] == c.request['calibration']
    assert calibration['certificate_inspection']['parse']['status'] == 'FAILED'
    assert calibration['certificate_inspection']['correction_authorization']['status'] == 'NOT_GRANTED'
    result = run(c)
    assert result['status'] == 'completed' and result['interpretation'] == 'INSUFFICIENT_EVIDENCE'
    assert len(result['execution_ids']) == 2 and result['authority'] == AUTHORITY
    finding = parse(read(c, 'finding', 'first'))
    native = finding['data']['model']['native_results']
    assert native[OPS[2]]['mean'] == pytest.approx(2.03)
    assert native[OPS[2]]['variance'] == pytest.approx(.00190808)
    assert native[OPS[3]]['status'] == 'PASS'
    outcome = parse(read(c, 'outcome', 'first'))
    assert outcome['intent_ref'] == result['intent_ref']
    assert set(outcome['artifacts']) == {'workspace', 'graph-run', 'experiment', 'finding'}
    workspace = parse(read(c, 'workspace', 'first'))
    assert len(workspace['results']) == 2
    assert result['authority']['physical_qualification'] == 'not_established'


def test_restart_reuses_original_result_without_native_dispatch(configured, monkeypatch):
    c = configured; result = run(c); first = parse(read(c, 'outcome', 'first'))
    c.native.close()
    with OperationalHost(c.profile, resume=True) as native:
        job = MetrologyJob.operational(native, 'scalar')
        host = MetrologyHost(policy=c.policy, principal='alice', tenant='lab', jobs={'scalar': job}, journal=c.host.journal)
        reopened = SimpleNamespace(facade=c.policy.facade('alice', metrology=host), job=job)
        monkeypatch.setattr(job, 'execute', lambda *_: pytest.fail('retry executed native science'))
        retry = run(reopened)
        assert retry['reused_response'] is True
        assert retry['outcome_ref'] == result['outcome_ref']
        assert retry['execution_ids'] == result['execution_ids']
        assert parse(read(reopened, 'outcome', 'first')) == first


@pytest.mark.parametrize('removed', [0, 2, 4, 5])
def test_all_nested_native_scientific_grants_required_before_claim(configured, monkeypatch, removed):
    c = configured
    c.grant['scientific_operations'].pop(removed)
    monkeypatch.setattr(c.job, 'execute', lambda *_: pytest.fail('ungranted nested operation executed'))
    assert c.host.call('ncp_metrology_catalog', {})['data']['jobs'][0]['currently_executable'] is False
    with pytest.raises(PrincipalRefusal):
        c.host.call('ncp_metrology_run', {'job': 'scalar', 'plan_ref': c.job.plan_ref, 'attempt': 'blocked'})
    with Journal(c.host.journal) as database:
        assert database.state('metrology-attempts') is None


def test_source_plan_digest_identity_and_scope_cannot_be_selected_by_request(configured):
    c = configured
    args = {'job': 'scalar', 'plan_ref': c.job.plan_ref, 'attempt': 'first'}
    for changed in ({**args, 'plan_ref': 'sha256:'+'0'*64}, {**args, 'job': '../escape'},
                    {**args, 'principal': 'bob'}, {**args, 'provider': '/tmp/provider'},
                    {**args, 'attempt': True}):
        with pytest.raises((ValueError, KeyError)):
            c.host.call('ncp_metrology_run', changed)
    with Journal(c.host.journal) as database:
        assert database.state('metrology-attempts') is None
    with pytest.raises(ValueError):
        c.policy.facade('bob', metrology=c.host)
    with pytest.raises(ValueError):
        invoke('ncp_metrology_run', args, host=c.host)


def test_current_policy_revocation_and_constraints_stop_read_and_new_run(configured):
    c = configured; run(c)
    rule = next(v for v in c.grant['operations'] if v['name'] == 'ncp_metrology_read')
    rule['constraints'] = {'job': ['another-job']}
    assert c.facade.call('ncp_metrology_read', {'job': 'scalar', 'artifact': 'source'})['status'] == 'refused'
    c.grant['enabled'] = False
    for name, args in [('ncp_metrology_status', {'job': 'scalar', 'attempt': 'first'}),
                       ('ncp_metrology_run', {'job': 'scalar', 'attempt': 'new', 'plan_ref': c.job.plan_ref})]:
        assert c.facade.call(name, args)['status'] == 'refused'


def test_revocation_after_durable_claim_prevents_dispatch(configured, monkeypatch):
    c = configured; original = c.host._change
    def change(records, key, value, *, new):
        result = original(records, key, value, new=new)
        if new and result:
            c.grant['scientific_operations'] = []
        return result
    monkeypatch.setattr(c.host, '_change', change)
    monkeypatch.setattr(c.job, 'execute', lambda *_: pytest.fail('revoked claim executed'))
    row = run(c)
    assert row['status'] == 'refused' and row['execution_ids'] == []
    assert c.native.agent._attempts == {}


def test_review_is_separate_immutable_and_binds_exact_outcome(configured):
    c = configured; result = run(c)
    args = {'job': 'scalar', 'attempt': 'first', 'outcome_ref': result['outcome_ref'], 'review_id': 'review1',
            'disposition': 'requires_followup', 'note': 'Independent reference and authenticated calibration are still missing.'}
    review = invoke('ncp_metrology_review', args, host=c.facade)
    assert invoke('ncp_metrology_review', args, host=c.facade) == review
    assert parse(read(c, 'review', 'first', review_id='review1')) == review['review']
    assert review['review']['authority']['review_grants_execution'] is False
    assert parse(read(c, 'outcome', 'first'))['status'] == 'completed'
    for change in ({'note': 'different'}, {'outcome_ref': 'sha256:'+'0'*64}, {'disposition': 'approved'}):
        with pytest.raises(ValueError):
            c.host.call('ncp_metrology_review', {**args, **change})
    c.grant['scientific_operations'] = []
    assert c.host.call('ncp_metrology_catalog', {})['data']['jobs'][0]['currently_executable'] is False


def test_crash_claim_is_unknown_and_never_automatically_retried(configured, monkeypatch):
    c = configured
    class Crash(BaseException):
        pass
    monkeypatch.setattr(c.job, 'execute', lambda *_: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        run(c)
    monkeypatch.setattr(c.job, 'execute', lambda *_: pytest.fail('unknown attempt retried'))
    retry = unknown_run(c)
    assert retry['state'] == 'claimed' and retry['status'] == 'outcome_unknown'
    assert retry['outcome_ref'] is None and retry['reused_response'] is True


def test_post_execution_retention_failure_remains_unknown_without_reexecution(configured, monkeypatch):
    c = configured; original = Records.put
    def put(records, value):
        if value.get('schema') == 'ciw.metrology-outcome.v1':
            raise OSError('simulated exhausted storage after native work')
        return original(records, value)
    with monkeypatch.context() as selected:
        selected.setattr(Records, 'put', put)
        result = unknown_run(c)
    assert result['status'] == 'outcome_unknown' and len(c.native.agent._attempts) == 1
    monkeypatch.setattr(c.job, 'execute', lambda *_: pytest.fail('retention failure caused retry'))
    assert unknown_run(c)['status'] == 'outcome_unknown'


def test_native_unknown_outcome_is_unknown_in_existing_durable_audit(configured, monkeypatch):
    from ciw.audit_chain import AuditChain
    from ciw.audited_host import AuditedPrincipalHost
    from test_campaign_audited_host import Anchor
    c = configured; original = Records.put
    def put(records, value):
        if value.get('schema') == 'ciw.metrology-outcome.v1':
            raise OSError('simulated post-native outcome retention failure')
        return original(records, value)
    chain = AuditChain(c.root/'metrology-audit.jsonl', chain_id='audited-profile', allowed_operations=list(TOOLS))
    anchor = Anchor(c.root/'independent-anchor.sqlite')
    audited = AuditedPrincipalHost(c.facade, chain=chain, read_anchor=anchor.read, compare_and_set_anchor=anchor.cas)
    with monkeypatch.context() as selected:
        selected.setattr(Records, 'put', put)
        response = audited.call('ncp_metrology_run', {'job': 'scalar', 'plan_ref': c.job.plan_ref, 'attempt': 'first'})
    assert response['status'] == 'refused' and response['error']['code'] == 'outcome_unknown'
    assert len(c.native.agent._attempts) == 1
    assert [record['event']['phase'] for record in chain.verify(anchor.read())] == ['admitted', 'unknown']
    assert audited.call('ncp_metrology_catalog', {})['error']['code'] == 'audit_unavailable'
    monkeypatch.setattr(c.job, 'execute', lambda *_: pytest.fail('unknown audit outcome replayed'))
    assert unknown_run(c)['status'] == 'outcome_unknown'


def test_competing_workflow_hosts_claim_one_native_attempt(configured):
    c = configured
    other_policy = PrincipalPolicy(lambda: c.document)
    other = MetrologyHost(policy=other_policy, principal='alice', tenant='lab', jobs={'scalar': c.job}, journal=c.host.journal)
    args = {'job': 'scalar', 'attempt': 'first', 'plan_ref': c.job.plan_ref}
    with patch.object(c.job, 'execute', wraps=c.job.execute) as execute:
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda h: h.call('ncp_metrology_run', args), [c.host, other]))
    rows = [response['data'] for response in responses if response['status'] == 'ok']
    assert all(response['error']['code'] == 'outcome_unknown' for response in responses if response['status'] != 'ok')
    assert execute.call_count == 1
    assert any(row['status'] == 'completed' for row in rows)
    assert sum(row['reused_response'] is False for row in rows) == 1
    assert c.host.call('ncp_metrology_status', {'job': 'scalar', 'attempt': 'first'})['data']['status'] == 'completed'


@pytest.mark.parametrize('change', [{'principal': 'bob'}, {'tenant': 'otherlab'}])
def test_journal_cannot_be_rebound_to_another_owner(configured, change):
    c = configured
    with pytest.raises(ValueError, match='Immutable'):
        MetrologyHost(**{'policy': c.policy, 'principal': 'alice', 'tenant': 'lab',
                         'jobs': {'scalar': c.job}, 'journal': c.host.journal, **change})


def test_retained_outcome_cannot_be_substituted_across_attempts(configured):
    c = configured; first = run(c); second = run(c, 'second')
    with Journal(c.host.journal) as database:
        old = database.state('metrology-attempts'); value = old['value']
        value['attempts'][c.host._key('scalar', 'first')]['outcome_ref'] = second['outcome_ref']
        database.compare_and_set_state('metrology-attempts', old['revision'], value)
    with pytest.raises(ValueError, match='outcome_binding'):
        c.host.call('ncp_metrology_status', {'job': 'scalar', 'attempt': 'first'})
    assert first['outcome_ref'] != second['outcome_ref']


def test_profile_and_investigation_revocation_refuse_new_work(configured):
    c = configured
    with Journal(c.native.journal_path) as database:
        database.compare_and_set_state('oe-revoked:'+c.native.spec['investigation'], 0, {'revoked': True})
    with pytest.raises(PermissionError):
        c.host.call('ncp_metrology_read', {'job': 'scalar', 'artifact': 'source'})


def test_registry_scope_has_real_content_and_storage_identity_without_paths(configured):
    c = configured; scope = c.host.binding_scope()
    assert scope['jobs']['scalar']['plan_ref'] == c.job.plan_ref
    assert scope['jobs']['scalar']['runtime_catalog_ref'] == 'sha256:' + sha256(c.native.agent._catalog).hexdigest()
    assert str(c.root) not in encode(scope).decode()
    assert {row['name'] for row in profile_manifest()['tools']} == set(TOOLS)
    from ciw.context_catalog import for_principal_facade
    selector = for_principal_facade(c.facade)
    assert selector.describe('ncp_metrology_run')['tool']['inputSchema'] == TOOLS['ncp_metrology_run']['inputSchema']


@pytest.fixture(scope='module')
def native_clearance_provider(tmp_path_factory):
    from companion_policy_helpers import qualified_policies
    # Reconstitute only local git metadata for the unchanged imported native tree.
    root = tmp_path_factory.mktemp('metrology-cse') / 'cse'
    shutil.copytree(ROOT / 'instruments/domain/bim-estimator', root,
                   ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache', '.venv'))
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()
    git('init'); git('config', 'core.autocrlf', 'false'); git('add', '-f', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Exact imported native fixture')
    assert git('rev-parse', 'HEAD^{tree}') == clearance.NATIVE_TREE
    policies = qualified_policies({'cse': root})
    return clearance.Provider(root, git('rev-parse', 'HEAD'), process_policy=policies['cse'])


@pytest.mark.parametrize('fixture,expected', [('uncertain-inspection.json', 'assessed'), ('inspection.json', 'held')])
def test_pinned_native_clearance_retains_held_or_uncertain_results(tmp_path, native_clearance_provider, fixture, expected):
    raw = (ROOT/'examples/metrology-clearance'/fixture).read_bytes()
    profile = clearance.prepare(raw, tmp_path/'native')
    with from_profile(profile, instrument='bim-clearance', clearance_provider=native_clearance_provider) as native:
        job = MetrologyJob.clearance(native)
        document = policy_document(job.operations); policy = PrincipalPolicy(lambda: document)
        host = MetrologyHost(policy=policy, principal='alice', tenant='lab', jobs={'scalar': job},
                             journal=tmp_path/'workflow.sqlite', create=True)
        c = SimpleNamespace(job=job, facade=policy.facade('alice', metrology=host))
        result = run(c)
        assert result['status'] == 'completed'
        assert read(c, 'source') == raw
        workspace = parse(read(c, 'workspace', 'first'))
        data = workspace['results'][0]['data']
        assert data['native']['status'] == expected
        if expected == 'assessed':
            assert result['interpretation'] == 'UNRESOLVED'
            assert data['native']['posterior']['verdict'] == 'UNRESOLVED'
            assert data['native']['likelihood']['pose_sigma'] > data['native']['likelihood']['sampling_sigma']
        else:
            assert result['interpretation'] == 'held'
            assert data['native']['posterior'] is None
        assert result['authority'] == AUTHORITY


def test_unbound_native_provider_is_visible_and_refuses_before_claim(tmp_path):
    raw = (ROOT/'examples/calibrated-window/source.json').read_bytes()
    source = parse(raw)
    profile = prepare(tmp_path/'native', investigation_id='missing-window-provider', sources={'window': {
        'raw': raw, 'family': 'calibrated-window', 'context': default_context(clock=source['device_clock']['clock_id']),
        'configurations': []}}, models={'window': {'source': 'window', 'engine': 'calibrated-window', 'request': None,
            'policy': {'unit': 'm', 'coverage_factor': 2, 'max_abs_error': 1, 'max_standard_uncertainty': 1}}})
    with OperationalHost(profile) as native:
        job = MetrologyJob.operational(native, 'window')
        policy = PrincipalPolicy(lambda: policy_document(job.operations))
        host = MetrologyHost(policy=policy, principal='alice', tenant='lab', jobs={'window': job},
                             journal=tmp_path/'workflow.sqlite', create=True)
        row = host.call('ncp_metrology_catalog', {})['data']['jobs'][0]
        assert row['provider_status'] == 'provider_unavailable' and row['currently_executable'] is False
        with pytest.raises(ValueError, match='provider_unavailable'):
            host.call('ncp_metrology_run', {'job': 'window', 'plan_ref': job.plan_ref, 'attempt': 'missing'})
        with Journal(host.journal) as database:
            assert database.state('metrology-attempts') is None
        assert native.agent._attempts == {}


def test_official_sdk_stdio_launch_reconnect_and_current_policy(tmp_path):
    profile, _ = scalar_profile(tmp_path/'native')
    from ciw import operational_engineering as engineering
    document = policy_document([*OPS, engineering.ASSESS, engineering.VERIFY])
    policy = tmp_path/'policy.json'; policy.write_bytes(encode(document))
    config = tmp_path/'host.json'
    config.write_bytes(encode({'schema': PROFILE, 'principal': 'alice', 'tenant': 'lab',
        'policy': str(policy), 'journal': str(tmp_path/'workflow.sqlite'),
        'jobs': {'scalar': {'kind': 'operational', 'profile': str(profile), 'model': 'scalar'}}}))
    output = StringIO()
    with redirect_stdout(output):
        assert main(['--config', str(config), '--initialize', '--tool', 'ncp_metrology_catalog']) == 0
    catalog = parse(output.getvalue().encode())['data']; plan = catalog['jobs'][0]['plan_ref']
    async def client_once(revoke=False):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=sys.executable,
            args=['-m', 'ciw.metrology_operations', '--config', str(config)],
            env={'PYTHONPATH': str(ROOT/'src'), 'PYTHONDONTWRITEBYTECODE': '1'})
        async with stdio_client(params, errlog=sys.__stderr__) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=30) as client:
                await client.discover()
                listing = await client.list_tools()
                assert {t.name for t in listing.tools} == set(TOOLS)
                result = await client.call_tool('ncp_metrology_run', {'job': 'scalar', 'plan_ref': plan, 'attempt': 'sdk'})
                assert result.is_error is False
                value = result.structured_content['data']
                if revoke:
                    document['principals'][0]['enabled'] = False; policy.write_bytes(encode(document))
                    from mcp.shared.exceptions import MCPError
                    with pytest.raises(MCPError):
                        await client.list_tools()
                    denied = await client.call_tool('ncp_metrology_read', {'job': 'scalar', 'artifact': 'source'})
                    assert denied.is_error is True
                return value
    first = asyncio.run(client_once())
    second = asyncio.run(client_once(revoke=True))
    assert first['status'] == 'completed' and second['reused_response'] is True
    assert first['execution_ids'] == second['execution_ids']
    assert first['outcome_ref'] == second['outcome_ref']
