"""Offline contract inspection, never live market data or a domain execution."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

import pytest
import ciw

from ciw.campaign_contracts import canonical, parse
from ciw.domain_read_profile import (CAPTURE_LIMITS, CAPTURE_SCHEMA, DOMAINS, FORMATS,
    TOOLS, DomainReadHost, digest)
from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
from ciw.services import invoke


def finance():
    return {'schema':FORMATS['finance'],'instrument':'synthetic:asset','currency':'USD',
            'quantity':'123.00000001','price':'0.00000001','as_of':'2026-11-03T00:00:00Z',
            'source_ref':'synthetic:observation'}


def capture(records=None):
    if records is None:
        records = [{'record_id':'../inert/path','domain':'finance',
                    'format':FORMATS['finance'],'data':finance()}]
        records[0]['record_id'] = 'record:../inert/path'
    return {'schema':CAPTURE_SCHEMA,'principal':'alice','source_identity':{'system':'fixture','revision':'1'},
            'origin':'synthetic','captured_at':'2026-11-03T00:00:00Z','records':records}


def host(value=None, **overrides):
    value = capture() if value is None else value
    raw = canonical(value, limits=CAPTURE_LIMITS)
    bindings = dict(capture_bytes=raw,expected_digest=digest(raw),source_identity=value['source_identity'],
                    principal='alice',allowed_domains=list(DOMAINS))
    bindings.update(overrides)
    return DomainReadHost(**bindings)


def policy_document():
    return {'schema':'ciw.principal-policy.v1','revision':'test-1','principals':[
        {'principal':'alice','enabled':True,'operations':[{'name':n,'constraints':{'domain':['finance']}} for n in TOOLS],
         'scientific_operations':[],'resources':[],'reference_scopes':[],'handoff_recipients':[]}]}


def test_exact_domain_record_and_receipt_do_not_execute(monkeypatch):
    import net_geomatics.catalog as catalog
    from ciw.geomatics_workflow import example
    from ciw.metrology_contract import example as metrology_example
    request = example(next(iter(catalog.OPERATIONS)))
    output = catalog.execute(request['operation_id'], request['parameters'])
    trade = {'schema':FORMATS['trade'],'trade_id':'synthetic:trade','instrument':'synthetic:asset',
             'side':'buy','quantity':'1.25','price':'2.00','currency':'USD',
             'event_time':'2026-11-03T00:00:00Z','source_ref':'synthetic:trade-observation'}
    data = {'metrology':metrology_example(), 'gis':{'request':request,'output':output},
            'finance':finance(), 'trade':trade}
    def forbidden(*args, **kwargs):
        raise AssertionError('read-only profile executed an engine or wrote evidence')
    monkeypatch.setattr(catalog, 'execute', forbidden)
    from ciw import coordinator, retention
    monkeypatch.setattr(coordinator,'submit',forbidden)
    monkeypatch.setattr(retention.Store,'keep',forbidden)
    value = capture([{'record_id':'fixture:'+domain,'domain':domain,'format':FORMATS[domain],'data':row}
                     for domain,row in data.items()])
    h = host(value)
    for domain in DOMAINS:
        args = {'domain':domain,'record_id':'fixture:'+domain}
        receipt = invoke('ncp_domain_read_record',args,host=h)
        assert receipt['record']['data'] == data[domain]
        verified = invoke('ncp_domain_read_verify',{**args,'receipt_json':canonical(receipt).decode()},host=h)
        assert verified['verified'] is True
        assert not verified['authorizes_execution']
        assert not verified['authorizes_state_admission']
    assert h.resource_descriptions() == h.resource_templates() == []
    with pytest.raises(ValueError): h.read_resource('ncp://records/fixture')


def test_record_copy_paging_restart_and_inert_identifier():
    h = host()
    args = {'domain':'finance','record_id':'record:../inert/path'}
    first = invoke('ncp_domain_read_record',args,host=h)
    assert invoke('ncp_domain_read_record',args,host=host()) == first
    first['record']['data']['price'] = '999'
    assert invoke('ncp_domain_read_record',args,host=h)['record']['data']['price'] == '0.00000001'
    page = invoke('ncp_domain_read_list',{'domain':'finance','limit':1},host=h)
    assert page['total'] == 1 and page['next_offset'] is None
    assert invoke('ncp_domain_read_list',{'domain':'finance','offset':1},host=h)['items'] == []
    assert invoke('ncp_domain_read_describe',{'domain':'finance'},host=h)['fresh_upstream_read'] is False


@pytest.mark.parametrize('mutation',[
    lambda r:r['record']['data'].update(price='9'),
    lambda r:r.update(principal='bob'),lambda r:r.update(authorizes_execution=True),
    lambda r:r.update(source_authentication='verified'),lambda r:r.update(captured_at='2026-11-02T00:00:00Z'),
    lambda r:r['source_identity'].update(revision='2'),lambda r:r.update(extra=True),
])
@pytest.mark.parametrize('reseal',[False,True])
def test_full_receipt_forgery_is_not_hidden_by_ids(mutation,reseal):
    h=host();args={'domain':'finance','record_id':'record:../inert/path'}
    receipt=invoke('ncp_domain_read_record',args,host=h)
    mutation(receipt)
    if reseal:
        receipt['record_digest']=digest(canonical(receipt['record']))
        receipt['receipt_digest']=digest(canonical({k:v for k,v in receipt.items() if k!='receipt_digest'}))
    with pytest.raises(ValueError,match='receipt_content_mismatch'):
        invoke('ncp_domain_read_verify',{**args,'receipt_json':canonical(receipt).decode()},host=h)


@pytest.mark.parametrize('field,value',[
    ('price',0.1),('quantity',True),('price','NaN'),('price','1e999'),('price','-1'),
    ('quantity','1.123456789'),('quantity','1'*19),('currency','US'),('as_of','2026-02-30T00:00:00Z'),
])
def test_observation_contract_refuses_coercions(field,value):
    document=capture(); document['records'][0]['data'][field]=value
    with pytest.raises(ValueError):host(document)


def test_raw_duplicate_unicode_depth_pin_and_capture_budget(monkeypatch):
    for raw in (b'{"schema":1,"schema":2}', b'{"x":NaN}', b'{"x":1e999}',
                b'{"x":"\\ud800"}', b'['*100+b'0'+b']'*100):
        with pytest.raises(ValueError):host(capture_bytes=raw,expected_digest=digest(raw))
    with pytest.raises(ValueError):host(principal='bob')
    with pytest.raises(ValueError):host(source_identity={'system':'fixture','revision':'2'})
    import ciw.domain_read_profile as profile
    monkeypatch.setattr(profile,'digest',lambda raw: (_ for _ in ()).throw(AssertionError('oversized hash')))
    with pytest.raises(ValueError,match='capture_byte_limit'):
        host(capture_bytes=b'x'*(CAPTURE_LIMITS.max_bytes+1),expected_digest='sha256:'+'0'*64)


def test_raw_receipt_duplicates_and_caller_grant_fields_refuse():
    h=host(); args={'domain':'finance','record_id':'record:../inert/path'}
    receipt=invoke('ncp_domain_read_record',args,host=h)
    raw=canonical(receipt).decode(); duplicate='{"principal":"mallory",'+raw[1:]
    assert h.call('ncp_domain_read_verify',{**args,'receipt_json':duplicate})['status']=='refused'
    for extra in ({'principal':'alice'},{'verified':True},{'endpoint':'https://example.invalid'}):
        refused=h.call('ncp_domain_read_record',{**args,**extra})
        assert refused['status']=='refused'
        assert h.mcp_result('ncp_domain_read_record',refused)['isError'] is True


def test_current_policy_tool_constraints_and_bound_principal(monkeypatch):
    value=policy_document(); p=PrincipalPolicy(lambda:value); h=host()
    facade=p.facade('alice',domain_read=h)
    assert facade.strict_mcp_input
    assert invoke('ncp_domain_read_describe',{'domain':'finance'},host=facade)['domain']=='finance'
    assert facade.call('ncp_domain_read_describe',{'domain':'trade'})['status']=='refused'
    with pytest.raises(PrincipalRefusal):p.facade('alice',domain_read=host(capture_bytes=canonical({**capture(),'principal':'bob'}),
        expected_digest=digest(canonical({**capture(),'principal':'bob'})),principal='bob'))
    calls=[]; original=h.call
    monkeypatch.setattr(h,'call',lambda *args:(calls.append(args),original(*args))[1])
    value['principals'][0]['operations']=[]
    assert facade.descriptions()==[]
    assert facade.call('ncp_domain_read_describe',{'domain':'finance'})['status']=='refused'
    assert calls==[]
    value['principals'][0]['enabled']=False
    assert facade.call('ncp_domain_read_describe',{'domain':'finance'})['status']=='refused'
    assert calls==[]


def test_actual_mcp_restart_and_live_policy_denials(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    policy_path=tmp_path/'policy.json'; policy_path.write_bytes(canonical(policy_document()))
    capture_path=tmp_path/'capture.json'; raw=canonical(capture()); capture_path.write_bytes(raw)
    script=tmp_path/'host.py'
    # Select the same source or installed package as this test, without relying
    # on PYTHONPATH inheritance through the bounded qualification launcher.
    script.write_text('import sys\n'+f'sys.path.insert(0,{str(Path(ciw.__file__).resolve().parents[1])!r})\n'+'''import asyncio
from pathlib import Path
from ciw.domain_read_profile import DomainReadHost
from ciw.knowledge_mcp import serve_stdio
from ciw.principal_policy import PrincipalPolicy
import json
''' + f'''raw=Path({str(capture_path)!r}).read_bytes()
host=DomainReadHost(capture_bytes=raw,expected_digest={digest(raw)!r},source_identity={{'system':'fixture','revision':'1'}},principal='alice',allowed_domains=['finance'])
policy=PrincipalPolicy.from_file({str(policy_path)!r})
asyncio.run(serve_stdio(policy.facade('alice',domain_read=host)))
''')
    async def run():
        before=None
        for turn in range(2):
            policy_path.write_bytes(canonical(policy_document()))
            async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(script)],env=dict(os.environ))) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    assert len((await client.list_tools()).tools)==4
                    assert (await client.list_resources()).resources==[]
                    assert (await client.list_resource_templates()).resource_templates==[]
                    args={'domain':'finance','record_id':'record:../inert/path'}
                    result=await client.call_tool('ncp_domain_read_record',args)
                    assert not result.is_error
                    receipt=result.structured_content['data']
                    assert receipt==invoke('ncp_domain_read_record',args,host=host())
                    if before is not None: assert receipt==before
                    before=receipt
                    verify=await client.call_tool('ncp_domain_read_verify',{**args,'receipt_json':canonical(receipt).decode()})
                    assert not verify.is_error
                    assert (await client.call_tool('ncp_domain_read_describe',{'domain':'trade'})).is_error
                    assert (await client.call_tool('ncp_domain_read_execute',{})).is_error
                    value=policy_document();value['principals'][0]['operations']=[]
                    policy_path.write_bytes(canonical(value))
                    assert (await client.list_tools()).tools==[]
                    assert (await client.call_tool('ncp_domain_read_record',args)).is_error
    asyncio.run(run())
