"""Actual current-principal calls behind an optional durable audit barrier."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
from hashlib import sha256

import pytest
import ciw

from ciw.audit_chain import AuditChain, empty_anchor
from ciw.audited_host import AuditedPrincipalHost
from ciw.principal_policy import PrincipalPolicy
from ciw.services import invoke
from ciw.campaign_contracts import canonical
from test_domain_read_profile import capture, host, policy_document


class Anchor:
    def __init__(self,path):
        self.path=path
        with self.connect() as db:
            db.execute('create table if not exists head (id integer primary key, body text not null)')
            db.execute('insert or ignore into head values (1,?)',(json.dumps(empty_anchor('audited-profile')),))
    def connect(self):
        db=sqlite3.connect(self.path,timeout=2)
        db.execute('pragma synchronous=FULL')
        return db
    def read(self):
        with self.connect() as db:return json.loads(db.execute('select body from head where id=1').fetchone()[0])
    def cas(self,before,after):
        with self.connect() as db:
            db.execute('begin immediate')
            if json.loads(db.execute('select body from head where id=1').fetchone()[0])!=before:return False
            db.execute('update head set body=? where id=1',(json.dumps(after),))
        return True


def bound(root, *, policy=None, cas=None):
    value=policy_document() if policy is None else policy
    raw=host(); facade=PrincipalPolicy(lambda:value).facade('alice',domain_read=raw)
    chain=AuditChain(root/'audit.jsonl',chain_id='audited-profile',allowed_operations=[
        'ncp_domain_read_describe','ncp_domain_read_record','ncp_domain_read_verify'])
    anchor=Anchor(root/'anchor.sqlite')
    wrapped=AuditedPrincipalHost(facade,chain=chain,read_anchor=anchor.read,compare_and_set_anchor=cas or anchor.cas)
    return wrapped,raw,value,anchor


def test_sdk_exact_envelope_redaction_replay_and_live_revocation(tmp_path,monkeypatch):
    wrapped,raw,value,anchor=bound(tmp_path)
    calls=[];original=raw.call
    monkeypatch.setattr(raw,'call',lambda *args:(calls.append(args),original(*args))[1])
    args={'domain':'finance','record_id':'record:../inert/path'}
    first=wrapped.call('ncp_domain_read_record',args,call_id='trusted-attempt-1')
    assert first['data']==invoke('ncp_domain_read_record',args,host=host())
    replay=wrapped.call('ncp_domain_read_record',args,call_id='trusted-attempt-1')
    assert replay['error']['code']=='audit_call_already_recorded' and len(calls)==1
    journal=wrapped._audit.chain.verify(anchor.read())
    assert [r['event']['phase'] for r in journal]==['admitted','succeeded']
    assert all(r['event']['operation']=='ncp_domain_read_record' and r['event']['recorded_at'].endswith('Z') for r in journal)
    text=(tmp_path/'audit.jsonl').read_text()
    assert '0.00000001' not in text and 'record:../inert/path' not in text and 'source_identity' not in text
    value['principals'][0]['operations']=[]
    assert wrapped.descriptions()==[]
    assert wrapped.call('ncp_domain_read_record',args)['status']=='refused'
    assert len(calls)==1
    assert wrapped._audit.chain.verify(anchor.read())[-1]['event']['phase']=='refused'
    assert wrapped.resource_descriptions()==wrapped.resource_templates()==[]


def test_pre_effect_sink_error_prevents_dispatch_and_restart(tmp_path,monkeypatch):
    wrapped,raw,_,_=bound(tmp_path,cas=lambda before,after:False)
    calls=[];monkeypatch.setattr(raw,'call',lambda *args:calls.append(args))
    result=wrapped.call('ncp_domain_read_describe',{'domain':'finance'})
    assert result['error']=={'code':'audit_unavailable','effect_state':'not_started'}
    assert calls==[]
    reopened,newraw,_,_=bound(tmp_path)
    monkeypatch.setattr(newraw,'call',lambda *args:calls.append(args))
    assert reopened.call('ncp_domain_read_describe',{'domain':'finance'})['status']=='refused'
    assert calls==[]


def test_post_effect_ack_error_preserves_unknown_and_no_retry(tmp_path,monkeypatch):
    anchor=Anchor(tmp_path/'anchor.sqlite'); writes=[]
    def fail_second(before,after):
        writes.append(after)
        return anchor.cas(before,after) if len(writes)==1 else False
    wrapped,raw,_,_=bound(tmp_path,cas=fail_second)
    calls=[];original=raw.call
    monkeypatch.setattr(raw,'call',lambda *args:(calls.append(args),original(*args))[1])
    result=wrapped.call('ncp_domain_read_describe',{'domain':'finance'})
    assert result['error']=={'code':'outcome_unknown','effect_state':'returned'}
    assert len(calls)==1
    assert wrapped.call('ncp_domain_read_describe',{'domain':'finance'})['status']=='refused'
    reopened,_,_,_=bound(tmp_path)
    assert reopened.call('ncp_domain_read_describe',{'domain':'finance'})['status']=='refused'
    assert len(calls)==1


def test_unknown_and_payload_correlation_do_not_gain_authority(tmp_path):
    wrapped,_,_,anchor=bound(tmp_path)
    assert wrapped.call('ncp_domain_execute',{})['status']=='refused'
    assert wrapped._audit.chain.verify(anchor.read())==[]
    assert wrapped.call('ncp_domain_read_describe',{'domain':'finance','call_id':'grant'})['status']=='refused'
    assert all(r['event']['effect_state'] in ('not_started','returned') for r in wrapped._audit.chain.verify(anchor.read()))


@pytest.mark.parametrize('nested',[False,True])
def test_caller_mutation_during_durable_io_cannot_change_audited_request(tmp_path,monkeypatch,nested):
    wrapped,raw,value,anchor=bound(tmp_path)
    for operation in value['principals'][0]['operations']:
        operation['constraints']={'domain':['finance','trade']}
    arguments={'domain':'finance'}
    if nested:arguments['extra']={'list':[{'value':'before'}]}
    selected=deepcopy(arguments);observed=[];results=[]
    original=wrapped.facade.call
    monkeypatch.setattr(wrapped.facade,'call',lambda name,args:(observed.append(deepcopy(args)),original(name,args))[1])
    entered=threading.Event();release=threading.Event();reads=[]
    def delayed_read():
        reads.append(1)
        if len(reads)==1:
            entered.set()
            assert release.wait(5)
        return anchor.read()
    wrapped._audit.read_anchor=delayed_read
    worker=threading.Thread(target=lambda:results.append(wrapped.call('ncp_domain_read_describe',arguments)))
    worker.start()
    assert entered.wait(5)
    if nested:arguments['extra']['list'][0]['value']='after'
    else:arguments['domain']='trade'
    release.set();worker.join(5)
    assert not worker.is_alive() and len(results)==1
    assert observed==[selected]
    expected='sha256:'+sha256(canonical({'name':'ncp_domain_read_describe','arguments':selected})).hexdigest()
    assert all(row['event']['request_ref']==expected for row in wrapped._audit.chain.verify(anchor.read()))
    if not nested:assert results[0]['data']['domain']=='finance'
    else:assert results[0]['status']=='refused'


def test_actual_owned_resource_reads_do_not_create_unknown_holds(tmp_path):
    from ciw.agent_mcp import demo_config, from_profile
    from ciw.control_gateway import ControlHost
    from ciw.principal_policy import owner_for, PrincipalRefusal
    from test_principal_policy import policy_value
    value=policy_value()
    with from_profile(demo_config(tmp_path/'demo')) as owner, ControlHost(owner,retained_owner=owner_for('alice')) as control:
        facade=PrincipalPolicy(lambda:value).facade('alice',control=control)
        graph=invoke('ncp_plan_template',{'baseline':'baseline'},host=facade)['graph']
        plan=invoke('ncp_plan_create',{'source':'source','baseline':'baseline','graph':graph},host=facade)
        uri='ncp://plans/'+plan['plan_id']
        chain=AuditChain(tmp_path/'resource.jsonl',chain_id='audited-profile',allowed_operations=['resource_read'])
        anchor=Anchor(tmp_path/'anchor.sqlite')
        wrapper=AuditedPrincipalHost(facade,chain=chain,read_anchor=anchor.read,compare_and_set_anchor=anchor.cas)
        assert wrapper.read_resource(uri)==facade.read_resource(uri)
        assert wrapper.read_resource(uri)==facade.read_resource(uri)
        value['principals'][0]['resources']=[]
        with pytest.raises(PrincipalRefusal):wrapper.read_resource(uri)
        records=chain.verify(anchor.read())
        assert [row['event']['phase'] for row in records]==['admitted','succeeded','admitted','succeeded','admitted','refused']


def test_actual_mcp_uses_audited_sdk_contract(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    script=tmp_path/'server.py'
    # All helpers are test-only and shipped alongside installed test inputs.
    script.write_text('import sys,asyncio\n'+f'sys.path.insert(0,{str(Path(ciw.__file__).resolve().parents[1])!r})\n'+
        f'sys.path.insert(0,{str(Path(__file__).parent)!r})\n'+
        'from pathlib import Path\nfrom test_campaign_audited_host import bound\n'+
        'from ciw.knowledge_mcp import serve_stdio\n'+f'host,_,_,_=bound(Path({str(tmp_path)!r}))\n'+
        'asyncio.run(serve_stdio(host))\n')
    async def run():
        async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(script)],env=dict(os.environ))) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                assert len((await client.list_tools()).tools)==3
                args={'domain':'finance','record_id':'record:../inert/path'}
                result=await client.call_tool('ncp_domain_read_record',args)
                assert not result.is_error
                assert result.structured_content['data']==invoke('ncp_domain_read_record',args,host=host())
                assert (await client.call_tool('ncp_domain_read_record',{**args,'principal':'bob'})).is_error
                assert (await client.list_resources()).resources==[]
    asyncio.run(run())
    reopened,_,_,anchor=bound(tmp_path)
    assert len(reopened._audit.chain.verify(anchor.read()))==4
