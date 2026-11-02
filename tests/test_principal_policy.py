"""Principal grants exercised through real hosts, retained records and SDK/MCP."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import threading
import time

import ciw
import pytest
from ciw.agent_api import encode
from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import ControlHost
from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal, owner_for, validate_policy, MAX_ENVELOPE
from ciw.services import invoke

TOOLS = ['ncp_capabilities_list','ncp_plan_template','ncp_plan_create','ncp_plan_get','ncp_plan_admit',
         'ncp_run_submit','ncp_run_get','ncp_run_cancel','ncp_artifact_read','ncp_retained_read',
         'ncp_retained_release','ncp_coordinate','ncp_domain_run']

def grant(principal='alice'):
    return {'principal':principal,'enabled':True,'operations':[{'name':n,'constraints':{}} for n in TOOLS],
            'scientific_operations':['statistics.v1'],'resources':['ncp://plans/','ncp://runs/','ncp://artifacts/','ncp://retained/'],
            'reference_scopes':[],'handoff_recipients':[]}

def policy_value(*grants):
    return {'schema':'ciw.principal-policy.v1','revision':'test-1','principals':list(grants or [grant()])}

@pytest.fixture
def configured(tmp_path,monkeypatch):
    monkeypatch.setenv('NCP_RETAIN_DIR',str(tmp_path/'retained'))
    value=policy_value()
    policy=PrincipalPolicy(lambda:value)
    profile=demo_config(tmp_path/'demo')
    with from_profile(profile) as owner:
        with ControlHost(owner,retained_owner=owner_for('alice')) as control:
            facade=policy.facade('alice',control=control)
            yield value,policy,profile,owner,control,facade

def planned(facade):
    graph=invoke('ncp_plan_template',{'baseline':'baseline'},host=facade)['graph']
    return invoke('ncp_plan_create',{'source':'source','baseline':'baseline','graph':graph},host=facade)

def wait(control,run_id):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        job=control.get_run(run_id)
        if job['state'] not in ('queued','running'):return job
        time.sleep(.01)
    pytest.fail('bounded queued execution did not settle')

@pytest.mark.parametrize('mutation',[
    lambda v:v.update(extra=True),lambda v:v['principals'][0].update(owner='someone'),
    lambda v:v['principals'][0].update(enabled='true'),lambda v:v['principals'][0].update(principal='../alice'),
    lambda v:v['principals'][0]['operations'].append({'name':'shell','constraints':{}}),
    lambda v:v['principals'].append(deepcopy(v['principals'][0])),
    lambda v:v['principals'][0].update(handoff_recipients=['unbound']),
    lambda v:v['principals'][0].pop('scientific_operations'),
])
def test_closed_host_policy(mutation):
    value=policy_value();mutation(value)
    with pytest.raises(ValueError):validate_policy(value)


def test_no_identity_grant_path_or_operation_override_and_zero_execution(configured):
    value,policy,_,owner,control,facade=configured
    for args in ({'principal':'bob'},{'configuration':{}},{'owner':'bob'},{'endpoint':'https://example.test'},
                 {'extra':{'grants':['admin']}},{'query':'界'*30000}):
        assert facade.call('ncp_capabilities_list',args)['status']=='refused'
    assert facade.call('shell',{})['status']=='refused'
    value['principals'][0]['operations']=[]
    assert facade.descriptions()==[]
    assert facade.call('ncp_plan_template',{'baseline':'baseline'})['status']=='refused'
    assert owner.capabilities()['execution_budget']['used']==0


def test_scientific_grant_separate_from_tool_and_reduced_after_admit(configured):
    value,policy,_,owner,control,facade=configured
    plan=planned(facade)
    invoke('ncp_plan_admit',{'plan_id':plan['plan_id'],'expected_digest':plan['digest']},host=facade)
    value['principals'][0]['scientific_operations']=[]
    caps=invoke('ncp_capabilities_list',host=facade)
    assert not any(row['authorized'] for row in caps['items'])
    for name,args in [('ncp_plan_template',{'baseline':'baseline'}),('ncp_plan_get',{'plan_id':plan['plan_id']}),
        ('ncp_run_submit',{'plan_id':plan['plan_id'],'expected_digest':plan['digest'],'attempt':'revoked'})]:
        assert facade.call(name,args)['status']=='refused'
    with pytest.raises(ValueError):facade.read_resource(plan['uri'])
    assert owner.capabilities()['execution_budget']['used']==0


@pytest.mark.parametrize('removed',['scientific_operations','resources','operations'])
def test_revocation_while_queued_refuses_before_provider_and_retains_outcome(configured,removed):
    value,policy,_,owner,control,facade=configured
    plan=planned(facade)
    invoke('ncp_plan_admit',{'plan_id':plan['plan_id'],'expected_digest':plan['digest']},host=facade)
    entered,release=threading.Event(),threading.Event()
    def hold():entered.set();assert release.wait(5)
    control._executor.submit(hold)
    assert entered.wait(2)
    try:
        run=invoke('ncp_run_submit',{'plan_id':plan['plan_id'],'expected_digest':plan['digest'],'attempt':'queued'},host=facade)
        value['principals'][0][removed]=[]
    finally:release.set()
    job=wait(control,run['run_id'])
    assert job['state']=='failed' and job['result']=={'status':'refused','reason':'principal_policy_refused','execution_outcome':'not_started'}
    assert owner.capabilities()['execution_budget']['used']==0
    assert control._store.load()[0]['jobs'][run['run_id']]['result']['execution_outcome']=='not_started'
    control._restore()
    assert control.get_run(run['run_id'])['result']['execution_outcome']=='not_started'


def test_resource_grant_is_current_for_tool_and_uri(configured):
    value,policy,_,owner,control,facade=configured
    artifact=control.retained.keep({'text':'retained'},kind='test',catalog={})
    assert invoke('ncp_retained_read',{'artifact_id':artifact},host=facade)['bundle']=={'text':'retained'}
    assert facade.read_resource('ncp://retained/'+artifact)['bundle']=={'text':'retained'}
    value['principals'][0]['resources']=[]
    assert facade.call('ncp_retained_read',{'artifact_id':artifact})['status']=='refused'
    with pytest.raises(ValueError):facade.read_resource('ncp://retained/'+artifact)
    value['principals'][0]['enabled']=False
    with pytest.raises(ValueError):policy.facade('alice',control=control)


def test_private_host_capacity_refuses_without_evicting_evidence(configured):
    from ciw.retention import _LIMIT
    _, _, _, owner, control, facade = configured
    artifacts = [control.retained.keep({'i': i}, kind='test', catalog={}) for i in range(_LIMIT)]
    with pytest.raises(ValueError, match='capacity exhausted'):
        control.retained.keep({'overflow': True}, kind='test', catalog={})
    for i, artifact in enumerate(artifacts):
        assert invoke('ncp_retained_read', {'artifact_id': artifact}, host=facade)['bundle'] == {'i': i}
    assert owner.capabilities()['execution_budget']['used'] == 0


def test_owner_isolation_and_revoked_handoff_claim(tmp_path,monkeypatch):
    monkeypatch.setenv('NCP_RETAIN_DIR',str(tmp_path/'retained'))
    alice,bob,eve=grant(),grant('bob'),grant('eve');alice['handoff_recipients']=['bob']
    value=policy_value(alice,bob,eve);policy=PrincipalPolicy(lambda:value)
    from contextlib import ExitStack
    with ExitStack() as stack:
        hosts={}
        for principal in ('alice','bob','eve'):
            owner=stack.enter_context(from_profile(demo_config(tmp_path/principal)))
            control=stack.enter_context(ControlHost(owner,retained_owner=owner_for(principal)))
            hosts[principal]=policy.facade(principal,control=control)
        artifact=hosts['alice'].retained.keep({'text':'private'},kind='test',catalog={})
        assert hosts['bob'].call('ncp_retained_read',{'artifact_id':artifact})['status']=='refused'
        handoff=invoke('ncp_coordinate',{'action':'handoff','artifact_id':artifact,'recipient':'bob'},host=hosts['alice'])
        assert hosts['eve'].call('ncp_coordinate',{'action':'claim','grant_id':handoff['grant_id']})['status']=='refused'
        alice['handoff_recipients']=[]
        assert hosts['bob'].call('ncp_coordinate',{'action':'claim','grant_id':handoff['grant_id']})['status']=='refused'
        alice['handoff_recipients']=['bob']
        claimed=invoke('ncp_coordinate',{'action':'claim','grant_id':handoff['grant_id']},host=hosts['bob'])
        assert invoke('ncp_retained_read',{'artifact_id':claimed['artifact_id']},host=hosts['bob'])['bundle']=={'text':'private'}
        assert invoke('ncp_coordinate',{'action':'claim','grant_id':handoff['grant_id']},host=hosts['bob'])['replayed']
        alice['enabled']=False
        assert hosts['bob'].call('ncp_coordinate',{'action':'claim','grant_id':handoff['grant_id']})['status']=='refused'


def test_coordinator_cannot_bypass_inner_grant(configured,monkeypatch):
    value,_,_,owner,control,facade=configured
    value['principals'][0]['operations']=[r for r in value['principals'][0]['operations'] if r['name']!='ncp_domain_run']
    calls=[]
    monkeypatch.setattr('ciw.services.domain_run',lambda **kwargs:calls.append(kwargs))
    response=facade.call('ncp_coordinate',{'action':'submit','operation':'ncp_domain_run','arguments':{'domain':'sequence','example':True},'idempotency_key':'no-permission'})
    assert response['status']=='refused' and calls==[]


def test_context_fails_closed_on_current_policy_change_or_payload_identity(configured):
    value,policy,_,_,_,_=configured
    context=policy.context('alice');assert policy.validate_context(context)=='alice'
    for key in ('owner','principal','policy_revision','policy_sha256'):
        forged=deepcopy(context);forged[key]='attacker'
        with pytest.raises(ValueError):policy.validate_context(forged)
    value['revision']='test-2'
    with pytest.raises(ValueError):policy.validate_context(context)


def test_policy_file_no_follow_closed_json_and_reload(tmp_path):
    path=tmp_path/'policy.json';path.write_text(json.dumps(policy_value()))
    policy=PrincipalPolicy.from_file(path)
    assert policy.check_principal('alice')['enabled']
    value=policy_value();value['principals'][0]['enabled']=False;path.write_text(json.dumps(value))
    with pytest.raises(ValueError):policy.check_principal('alice')
    path.write_text('{"schema":"bad","schema":"ciw.principal-policy.v1"}')
    with pytest.raises(ValueError):policy.check_principal('alice')
    if hasattr(os,'O_NOFOLLOW'):
        link=tmp_path/'link';link.symlink_to(path)
        with pytest.raises(ValueError):PrincipalPolicy.from_file(link).check_principal('alice')


def test_full_mcp_envelope_unicode_budget(configured,monkeypatch):
    _,_,_,_,control,facade=configured
    monkeypatch.setattr(control,'call',lambda name,args:{'status':'ok','data':{'text':'界'*20000}})
    assert facade.call('ncp_capabilities_list',{})['status']=='refused'
    assert len(encode(facade.mcp_result('ncp_capabilities_list',facade.refusal())))<=MAX_ENVELOPE


def test_reopen_never_restores_old_grants(tmp_path,monkeypatch):
    monkeypatch.setenv('NCP_RETAIN_DIR',str(tmp_path/'retained'))
    profile=demo_config(tmp_path/'demo');value=policy_value();policy=PrincipalPolicy(lambda:value)
    with from_profile(profile) as owner,ControlHost(owner,retained_owner=owner_for('alice')) as control:
        facade=policy.facade('alice',control=control);plan=planned(facade)
        invoke('ncp_plan_admit',{'plan_id':plan['plan_id'],'expected_digest':plan['digest']},host=facade)
    value['principals'][0]['scientific_operations']=[]
    with from_profile(profile,resume=True) as owner,ControlHost(owner,resume=True,retained_owner=owner_for('alice')) as control:
        facade=policy.facade('alice',control=control)
        assert facade.call('ncp_run_submit',{'plan_id':plan['plan_id'],'expected_digest':plan['digest'],'attempt':'reopened'})['status']=='refused'
        assert owner.capabilities()['execution_budget']['used']==0


def test_actual_mcp_subprocess_rechecks_policy_and_resource(tmp_path,monkeypatch):
    from ciw import coordinator,retention
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.shared.exceptions import MCPError
    monkeypatch.setenv('NCP_RETAIN_DIR',str(tmp_path/'retained'))
    from reference_fixture_helpers import make_catalog
    corpus,_,_=make_catalog(tmp_path/'catalog-input',b'Synthetic principal-bound reference.')
    catalog=tmp_path/'catalog-input/catalog-inputs/bundle'
    profile=demo_config(tmp_path/'demo');path=tmp_path/'policy.json';value=policy_value()
    value['principals'][0]['scientific_operations'].append('sequence.markov.v1')
    value['principals'][0]['operations'].append({'name':'ncp_knowledge_overview','constraints':{}})
    value['principals'][0]['reference_scopes']=[corpus.ref]
    reference_uri='ncp://proto-corpus/'+corpus.ref[7:]+'/overview'
    value['principals'][0]['resources'].append(reference_uri)
    path.write_text(json.dumps(value))
    env={**os.environ,'PYTHONPATH':str(Path(ciw.__file__).resolve().parents[1]),'NCP_RETAIN_DIR':str(tmp_path/'retained')}
    async def run():
        params=StdioServerParameters(command=sys.executable,args=['-m','ciw.principal_mcp','--profile',str(profile),'--policy',str(path),'--principal','alice','--catalog',str(catalog),'--catalog-ref',corpus.ref],env=env,cwd=str(tmp_path))
        with (tmp_path/'stderr.log').open('w') as err:
            async with stdio_client(params,errlog=err) as streams:
                async with ClientSession(*streams,read_timeout_seconds=15) as client:
                    await client.initialize()
                    names={t.name for t in (await client.list_tools()).tools}
                    assert 'ncp_plan_template' in names
                    assert not (await client.call_tool('ncp_knowledge_overview',{})).is_error
                    assert (await client.read_resource(reference_uri)).contents
                    held=coordinator._hold_exclusive(retention.Store('other-worker'),'mcp_exclusive')
                    assert held is not None
                    try:
                        failed=await client.call_tool('ncp_coordinate',{'action':'submit','operation':'ncp_domain_run',
                            'arguments':{'domain':'sequence','example':True},'exclusive':'mcp_exclusive','idempotency_key':'blocked'})
                        assert failed.is_error and failed.structured_content['error']['code']=='outcome_unknown'
                        locator=failed.structured_content['error']['artifact_id']
                        found=await client.call_tool('ncp_retained_read',{'artifact_id':locator})
                        assert not found.is_error and found.structured_content['data']['bundle']['error']=='exclusive_resource_busy'
                    finally:coordinator._release_exclusive(held)
                    value['principals'][0]['reference_scopes']=[];path.write_text(json.dumps(value))
                    assert (await client.call_tool('ncp_knowledge_overview',{})).is_error
                    with pytest.raises(MCPError):await client.read_resource(reference_uri)
                    result=await client.call_tool('ncp_plan_template',{'baseline':'baseline'});assert not result.is_error
                    graph=result.structured_content['data']['graph']
                    result=await client.call_tool('ncp_plan_create',{'source':'source','baseline':'baseline','graph':graph});assert not result.is_error
                    plan=result.structured_content['data']
                    resource=await client.read_resource(plan['uri']);assert json.loads(resource.contents[0].text)['plan_id']==plan['plan_id']
                    value['principals'][0]['scientific_operations']=[];path.write_text(json.dumps(value))
                    assert (await client.call_tool('ncp_plan_template',{'baseline':'baseline'})).is_error
                    with pytest.raises(MCPError):await client.read_resource(plan['uri'])
                    value['principals'][0]['enabled']=False;path.write_text(json.dumps(value))
                    assert (await client.call_tool('ncp_capabilities_list',{})).is_error
    asyncio.run(run())
    with from_profile(profile,resume=True) as owner:
        assert owner.capabilities()['execution_budget']['used']==0


def test_reference_scope_rechecked_on_tool_resource_and_discovery(tmp_path):
    from reference_fixture_helpers import make_catalog
    from ciw.reference_profile import open_reference
    corpus,record,_=make_catalog(tmp_path/'input',b'Synthetic reference only.')
    host=open_reference(catalog_path=tmp_path/'input/catalog-inputs/bundle',catalog_ref=corpus.ref)
    g=grant();g['operations']=[{'name':name,'constraints':{}} for name in ('ncp_knowledge_overview','ncp_knowledge_source')]
    g['reference_scopes']=[corpus.ref];g['resources']=[host.base_uri+'/']
    value=policy_value(g);facade=PrincipalPolicy(lambda:value).facade('alice',reference=host)
    assert invoke('ncp_knowledge_overview',{},host=facade)
    assert facade.read_resource(host.base_uri+'/overview')
    g['reference_scopes']=[]
    assert facade.descriptions()==[]
    assert facade.call('ncp_knowledge_overview',{})['status']=='refused'
    with pytest.raises(ValueError):facade.read_resource(host.base_uri+'/overview')


def test_nsl_principal_binding_and_reduced_operation_grant():
    from test_state_read_profile import capture,host
    g=grant();g['operations']=[{'name':'ncp_state_releases','constraints':{}}]
    value=policy_value(g);policy=PrincipalPolicy(lambda:value)
    with pytest.raises(ValueError):policy.facade('alice',state=host())
    facade=policy.facade('alice',state=host(capture(principal='alice'),principal='alice'))
    assert invoke('ncp_state_releases',{},host=facade)['provenance']['fresh_upstream_read'] is False
    g['operations']=[]
    assert facade.call('ncp_state_releases',{})['status']=='refused'


def test_olm_principal_binding_before_transport_and_reduced_retained_reads(tmp_path,monkeypatch):
    import shutil
    from logistics_fixture_helpers import Fixture
    from ciw.logistics_profile import LogisticsHost
    node=shutil.which('node')
    if node is None:pytest.skip('OLM canonical digest requires host-selected Node')
    monkeypatch.setenv('NCP_RETAIN_DIR',str(tmp_path/'retained'))
    g=grant();g['operations']=[{'name':n,'constraints':{}} for n in ('ncp_logistics_catalog','ncp_logistics_retained_read')]
    value=policy_value(g);policy=PrincipalPolicy(lambda:value);transport=Fixture()
    host=LogisticsHost(transport=transport,principal='alice',tenant='test-tenant',roles=['pilot:agent-read','pilot:compute-read'],allowed_kinds=['catalog'],node=node,synthetic=True)
    with pytest.raises(ValueError):PrincipalPolicy(lambda:policy_value(grant('bob'))).facade('bob',logistics=host)
    assert transport.calls==[]
    facade=policy.facade('alice',logistics=host)
    artifact=invoke('ncp_logistics_catalog',{},host=facade)['artifact_id']
    assert invoke('ncp_logistics_retained_read',{'artifact_id':artifact},host=facade)
    # Keeping the retained-read tool cannot resurrect removed selector scope.
    g['operations']=[r for r in g['operations'] if r['name']!='ncp_logistics_catalog']
    before=len(transport.calls)
    assert facade.call('ncp_logistics_retained_read',{'artifact_id':artifact})['status']=='refused'
    assert len(transport.calls)==before


@pytest.mark.parametrize('failure',['exception','oversize'])
def test_post_effect_failure_is_unknown_not_authorization_refusal(configured,monkeypatch,failure):
    from ciw.principal_policy import PrincipalOutcomeUnknown
    _,_,_,_,control,facade=configured
    calls=[]
    def applied(name,args):
        calls.append(name)
        if failure=='exception':raise RuntimeError('private exception after effect')
        return {'status':'ok','data':{'text':'界'*50000}}
    monkeypatch.setattr(control,'call',applied)
    args={'action':'recover'}
    response=facade.call('ncp_coordinate',args)
    assert response['error']['code']=='outcome_unknown' and calls==['ncp_coordinate']
    with pytest.raises(PrincipalOutcomeUnknown):invoke('ncp_coordinate',args,host=facade)


def test_principal_olm_capacity_refuses_without_evicting_then_explicit_release(tmp_path,monkeypatch):
    import shutil
    from logistics_fixture_helpers import Fixture
    from ciw.logistics_profile import LogisticsHost
    node=shutil.which('node')
    if node is None:pytest.skip('OLM canonical digest requires host-selected Node')
    monkeypatch.setenv('NCP_RETAIN_DIR',str(tmp_path/'retained'))
    host=LogisticsHost(transport=Fixture(),principal='alice',tenant='test-tenant',roles=['pilot:agent-read','pilot:compute-read'],allowed_kinds=['catalog'],node=node,synthetic=True)
    assert host.store._evict_existing is True  # Raw trusted-host default is unchanged.
    g=grant();g['operations']=[{'name':'ncp_logistics_catalog','constraints':{}}]
    facade=PrincipalPolicy(lambda:policy_value(g)).facade('alice',logistics=host)
    assert host.store._evict_existing is False
    artifacts=[host.store.keep({'index':i},kind='test',catalog={}) for i in range(32)]
    assert facade.call('ncp_logistics_catalog',{})['status']=='refused'
    assert [host.store.native(a)['index'] for a in artifacts]==list(range(32))
    host.store.release(artifacts[0])
    fresh=invoke('ncp_logistics_catalog',{},host=facade)['artifact_id']
    assert host.store.native(fresh)['profile']=='olm.logistics.v1'
    assert [host.store.native(a)['index'] for a in artifacts[1:]]==list(range(1,32))


def test_observe_compare_and_qualify_cannot_read_outside_current_artifact_scope(tmp_path,monkeypatch):
    from test_agent_protocol import observation_host
    monkeypatch.setenv('NCP_RETAIN_DIR',str(tmp_path/'retained'))
    g=grant();g['operations'] += [{'name':n,'constraints':{}} for n in ('ncp_observe','ncp_compare','ncp_qualify')]
    value=policy_value(g);policy=PrincipalPolicy(lambda:value)
    with observation_host(tmp_path) as owner,ControlHost(owner,retained_owner=owner_for('alice')) as control:
        facade=policy.facade('alice',control=control)
        assert len(invoke('ncp_observe',{'artifact':'left','limit':2},host=facade)['data'])==2
        comparison=invoke('ncp_compare',{'left':'left','right':'right','policy':'strict'},host=facade)
        assert comparison['outcome']['status']=='PASS'
        inputs={'difference':comparison['comparison']['artifact_id']}
        assert invoke('ncp_qualify',{'suite':'regression','inputs':inputs},host=facade)['summary']['status']=='PASS'
        g['resources']=[]
        calls=[];original=control.call
        monkeypatch.setattr(control,'call',lambda name,args:(calls.append(name),original(name,args))[1])
        for name,args in [('ncp_observe',{'artifact':'left'}),('ncp_compare',{'left':'left','right':'right','policy':'strict'}),
                          ('ncp_qualify',{'suite':'regression','inputs':inputs})]:
            assert facade.call(name,args)['status']=='refused'
        assert calls==[]


def test_workbench_references_require_current_retained_scope_before_dispatch(configured,monkeypatch):
    value,_,_,_,control,facade=configured
    value['principals'][0]['operations'].append({'name':'ncp_workbench_run','constraints':{}})
    value['principals'][0]['scientific_operations'].append('ciw.identified-design.v1')
    value['principals'][0]['resources']=[]
    calls=[];monkeypatch.setattr(control,'call',lambda *args:calls.append(args))
    for references in ({'upstream_artifact':'retained-'+'1'*32},{'upstream_artifacts':['retained-'+'1'*32]}):
        assert facade.call('ncp_workbench_run',{'workflow':'identified-design',**references})['status']=='refused'
    assert calls==[]


def test_retained_failure_locator_survives_sdk_and_mcp_unknown_envelope(configured):
    from ciw import coordinator,retention
    value,_,_,_,control,facade=configured
    value['principals'][0]['scientific_operations'].append('sequence.markov.v1')
    held=coordinator._hold_exclusive(retention.Store('other-worker'),'fixture_exclusive')
    assert held is not None
    before=coordinator._EXECUTIONS
    try:
        args={'action':'submit','operation':'ncp_domain_run','arguments':{'domain':'sequence','example':True},'exclusive':'fixture_exclusive','idempotency_key':'sdk-failure'}
        with pytest.raises(retention.RetainedFailure) as refused:
            invoke('ncp_coordinate',args,host=facade)
        assert str(refused.value)=='outcome_unknown'
        assert control.retained.native(refused.value.artifact_id)['error']=='exclusive_resource_busy'
        response=facade.call('ncp_coordinate',{**args,'idempotency_key':'mcp-failure'})
        assert response['error']['code']=='outcome_unknown'
        envelope=facade.mcp_result('ncp_coordinate',response)
        assert envelope['isError'] and envelope['structuredContent']['error']['artifact_id']==response['error']['artifact_id']
        assert control.retained.native(response['error']['artifact_id'])['error']=='exclusive_resource_busy'
        assert coordinator._EXECUTIONS==before
    finally:coordinator._release_exclusive(held)


@pytest.mark.parametrize('surface',['call','sdk'])
def test_real_unlink_then_cleanup_failure_remains_unknown(configured,monkeypatch,surface):
    from ciw import retention
    from ciw.principal_policy import PrincipalOutcomeUnknown
    _,_,_,_,control,facade=configured
    artifact=control.retained.keep({'fixture':True},kind='test',catalog={})
    target=control.retained._path(artifact);lock=retention._owner_dir(control.retained.owner)/'sequence.lock'
    original=retention._lock_release;fired=False
    def unlock(fd):
        nonlocal fired
        actual,expected=os.fstat(fd),lock.stat()
        matches=(actual.st_dev,actual.st_ino)==(expected.st_dev,expected.st_ino)
        original(fd)
        if matches and not fired:
            fired=True
            assert not target.exists()
            raise OSError('synthetic cleanup failure after applied unlink')
    monkeypatch.setattr(retention,'_lock_release',unlock)
    if surface=='call':
        assert facade.call('ncp_retained_release',{'artifact_id':artifact})['error']['code']=='outcome_unknown'
    else:
        with pytest.raises(PrincipalOutcomeUnknown):invoke('ncp_retained_release',{'artifact_id':artifact},host=facade)
    assert fired and not target.exists()
