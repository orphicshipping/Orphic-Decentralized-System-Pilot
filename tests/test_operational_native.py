"""Real pinned native workflows through operational AgentHost and Session."""
from pathlib import Path
import asyncio
import json
import os
import runpy
import sys

import pytest

from ciw.agent_api import encode
from ciw import calibrated_window as window, residual_monitor as monitor, link_contract as c
from ciw.operational_agent import OperationalHost
from ciw.operational_cli import prepare, default_context, default_template
from companion_policy_helpers import policy_config_document, qualified_policies


@pytest.fixture(scope='module')
def stack():
    root=os.environ.get('CIW_ACQUIRED_STREAM_STACK_ROOT')
    if not root: pytest.skip('Requires exact native calibrated-window and FDIR/OIT checkouts')
    paths = {role:str(Path(root)/role) for role in window.ROLES|monitor.ROLES}
    return paths, qualified_policies(paths)


def run(host,alias,attempt):
    result=host.call('net_execute',{'source':alias,'graph':alias+'-graph','attempt':attempt})
    assert result['status']=='completed',result
    finding=host.call('oe_model_support',{'attempt':attempt})
    assert finding['data']['status']=='INSUFFICIENT_EVIDENCE'
    return result,finding


async def sdk_run(profile, policy_config):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    params=StdioServerParameters(command=sys.executable,args=['-I','-m','ciw.net','operational','serve',
        '--profile',str(profile),'--sdk','--resume','--companion-policies',str(policy_config)])
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=120) as client:
            await client.discover()
            async def call(name,args):
                result=await client.call_tool(name,args)
                assert not result.is_error,result
                assert json.loads(result.content[0].text)==result.structured_content
                return result.structured_content
            catalog=await call('oe_catalog',{})
            assert catalog['model_adapters']['assessment']['provider_status']=='operator_bound_pin_check_at_dispatch'
            first=await call('net_execute',{'source':'assessment','graph':'assessment-graph','attempt':'first'})
            assert first['status']=='completed',first
            finding=await call('oe_model_support',{'attempt':'first'})
            assert finding['data']['status']=='INSUFFICIENT_EVIDENCE'
            resource=await client.read_resource('ncp://operational-records/'+finding['record'][7:])
            assert json.loads(resource.contents[0].text)['record']==finding['record']
            return first,finding


def test_native_calibrated_window_preserves_covariance_and_resume_without_providers(tmp_path,stack,monkeypatch):
    stack, policies = stack
    selected = {'calibrated-window': {role: policies[role] for role in window.ROLES}}
    policy_config = tmp_path / 'companions.json'
    policy_config.write_bytes(encode(policy_config_document(selected)))
    raw=(Path(__file__).parents[1]/'examples/calibrated-window/source.json').read_bytes()
    native=window._source(raw)
    profile=prepare(tmp_path,investigation_id='native-window',sources={'window':{'raw':raw,
        'family':'calibrated-window','context':default_context(clock=native['device_clock']['clock_id']),
        'configurations':[]}},models={'assessment':{'source':'window','engine':'calibrated-window','request':None,
            'policy':{'unit':'m','coverage_factor':2,'max_abs_error':1,'max_standard_uncertainty':1}}},
        providers={'calibrated-window':{r:stack[r] for r in window.ROLES}},templates={'resolve':default_template()})
    with OperationalHost(profile, process_policies=selected): pass
    first,finding=asyncio.run(sdk_run(profile, policy_config))
    with OperationalHost(profile,resume=True, process_policies=selected) as host:
        summary=finding['data']['model']['native_results']
        assert len(summary['steps'])==4 and summary['verification']['outcome']=='passed'
        assert summary['steps'][1]['numerical_result']['data']['temporal_covariance']==[[5.5,3.5],[3.5,8.5]]
        proposal=host.call('oe_propose',{'finding':finding['record'],'template':'resolve'})
        assert proposal['data']['discrimination']['hypothesis_tests']
    monkeypatch.setattr(window,'create_session',lambda *a, **k:pytest.fail('Inspection called native providers'))
    with OperationalHost(profile,resume=True, process_policies=selected) as host:
        retry=host.call('net_execute',{'source':'assessment','graph':'assessment-graph','attempt':'first'})
        assert retry['reused_response'] and retry['result_ids']==first['result_ids']
        assert host.call('oe_model_support',{'attempt':'first'})==finding


def test_native_residual_monitor_binds_exact_upstream_windows_and_keeps_ambiguity(tmp_path,stack):
    stack, policies = stack
    selected = {'residual-monitor': {role: policies[role] for role in monitor.ROLES}}
    policy_config = tmp_path / 'companions.json'
    policy_config.write_bytes(encode(policy_config_document(selected)))
    templates=runpy.run_path(str(Path(__file__).parents[1]/'examples/acquired-stream/make_sequence.py'))['templates']()
    windows=[window.create_session(window.canonical(v),{r:stack[r] for r in window.ROLES},
        process_policies={role: policies[role] for role in window.ROLES}) for v in templates]
    upstreams={v['bundle_digest']:v for v in windows}
    source={'schema':monitor.SOURCE_SCHEMA,'experiment_id':'operational-residual',
        'window_bundle_ids':list(upstreams),'configuration':monitor.DEFAULT_CONFIGURATION}
    raw=encode(source)
    request=encode({'source':c.packed(raw,262144),'upstreams':upstreams})
    profile=prepare(tmp_path,investigation_id='native-monitor',sources={'monitor':{'raw':raw,
        'family':'residual-monitor','context':default_context(),'configurations':[]}},
        models={'assessment':{'source':'monitor','engine':'residual-monitor','request':request,
            'policy':{'unit':'m','coverage_factor':2,'max_abs_error':1,'max_standard_uncertainty':1}}},
        providers={'residual-monitor':{r:stack[r] for r in monitor.ROLES}})
    with OperationalHost(profile, process_policies=selected): pass
    _,finding=asyncio.run(sdk_run(profile, policy_config))
    with OperationalHost(profile,resume=True, process_policies=selected) as host:
        summary=finding['data']['model']['native_results']
        data=summary['steps'][0]['numerical_result']['data']
        assert [row['detection']['raw_residual'] for row in data['rows']]==[[0.0],[0.5],[10.0]]
        assert [row['isolability']['status'] for row in data['rows']]==['not_detected','not_detected','ambiguous']
        assert all(row['isolability']['isolated_fault'] is None for row in data['rows'])
        assert data['rows'][-1]['cusum']['status']=='statistical_anomaly'
        assert all(pair['residual_cross_covariance']=='unknown' for pair in data['overlaps'])
