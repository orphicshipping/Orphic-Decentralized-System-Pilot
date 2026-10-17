"""Connected J1-J5 check, run with an installed interpreter and -I.

No PYTHONPATH or checkout imports. Retains SDK receipts, identity comparisons,
client configuration, environment and actual installed module/asset hashes.
"""
from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
from importlib.metadata import version
from importlib.resources import files
import json
import math
from pathlib import Path
import platform
import subprocess
import sys

import ciw
from ciw.agent_api import encode, parse
from ciw.operational_agent import OperationalHost
from ciw.operational_cli import prepare,default_context,configuration,default_template
from ciw.reference_comparison import example_request
from ciw.operational_view import snapshot,render_html


def run(output):
    package=Path(ciw.__file__).resolve()
    if not package.is_relative_to(Path(sys.prefix).resolve()):raise RuntimeError('Installed test refuses source fallback')
    output.mkdir(parents=True,exist_ok=False)
    req=example_request();ctx=default_context(clock='synthetic.clock',origin='synthetic')
    from ciw.link_study_cli import example as scalar_example
    scalar=scalar_example();capture=scalar['capture']
    configs=[configuration('before',{'power':10,'fan':1},clock='synthetic.clock'),
             configuration('after',{'power':20,'fan':2},effective=(0.5,0.5),clock='synthetic.clock')]
    profile=prepare(output/'profile',investigation_id='installed-operational',sources={'recorded':{
        'raw':encode(req['right']),'family':'observations','context':ctx,'configurations':configs},
        'scalar':{'raw':encode(capture),'family':'link-capture','configurations':[],
            'context':default_context(producer=capture['manifest']['device_id'],clock=capture['manifest']['clock']['id'],
                boot=capture['boot_id'],origin='synthetic')}},
        models={'assessment':{'source':'recorded','engine':'reference','policy':{},'request':encode(req)},
                'scalar-model':{'source':'scalar','engine':'scalar','request':encode(scalar),
                    'policy':{'unit':'N','coverage_factor':2,'max_abs_error':.1,'max_standard_uncertainty':.1}}},
        templates={'resolve':default_template()})
    with OperationalHost(profile):pass
    config_path=next((output/'profile').glob('mcp-*.json'))
    config=parse(config_path.read_bytes())['mcpServers']['ncp-operational']
    if config['args'][:3]!=['-I','-m','ciw.net']:raise RuntimeError('Generated launch must isolate installed imports')
    shadow=output/'unrelated-client-cwd';(shadow/'ciw').mkdir(parents=True)
    (shadow/'ciw/__init__.py').write_text('')
    (shadow/'ciw/net.py').write_text("raise RuntimeError('Working-directory package shadowed installed NCP')\n")
    receipts={}
    async def journey(modern,reconnect):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        params=StdioServerParameters(command=config['command'],args=config['args'],cwd=str(shadow))
        with (output/'sdk-stderr.log').open('a') as errors:
            async with stdio_client(params,errlog=errors) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=30) as client:
                    await (client.discover() if modern else client.initialize())
                    async def call(name,args={}):
                        r=await client.call_tool(name,args)
                        if r.is_error:raise RuntimeError((name,r))
                        v=r.structured_content
                        if json.loads(r.content[0].text)!=v:raise RuntimeError('MCP content mismatch')
                        return v
                    catalog=await call('oe_catalog')
                    assert len((await call('oe_rows',{'source':'recorded'}))['rows'])==2
                    sel={'source':'recorded','clock':'synthetic.clock','boot':'boot-1','channel':'temperature','start':0,'end':0.5}
                    a=await call('oe_query',sel);b=await call('oe_query',{**sel,'start':0.5,'end':2})
                    changed=await call('oe_change',{'baseline':a['record'],'changed':b['record']})
                    run=await call('net_execute',{'source':'assessment','graph':'assessment-graph','attempt':'installed-first'})
                    finding=await call('oe_model_support',{'attempt':'installed-first'})
                    scalar_run=await call('net_execute',{'source':'scalar-model','graph':'scalar-model-graph','attempt':'installed-scalar'})
                    scalar_finding=await call('oe_model_support',{'attempt':'installed-scalar'})
                    assert scalar_finding['data']['status']=='INSUFFICIENT_EVIDENCE'
                    assert math.isclose(scalar_finding['data']['model']['native_results']['link.mean-model.v1']['mean'],2.03,rel_tol=1e-14)
                    unplaced=await call('oe_rows',{'source':'scalar','unplaced_only':True})
                    assert unplaced['rows'] and all(row['time'] is None for row in unplaced['rows'])
                    proposal=await call('oe_propose',{'finding':finding['record'],'template':'resolve'})
                    assert changed['data']['status']=='RECORDED_ASSOCIATION'
                    assert finding['data']['status']=='AGREEMENT'
                    assert proposal['data']['approval']=='not_granted' and not proposal['data']['executed']
                    templates=await client.list_resource_templates();assert len(templates.resource_templates)==2
                    resource=await client.read_resource('ncp://operational-records/'+finding['record'][7:])
                    assert json.loads(resource.contents[0].text)['record']==finding['record']
                    if reconnect:
                        assert run['reused_response'] and run['result_ids']==receipts['J3']['result_ids']
                        assert finding['record']==receipts['finding']['record']
                    else:receipts.update(J1=a,J2=changed,J3=run,finding=finding,J4=proposal,native_scalar=scalar_run,native_finding=scalar_finding)
                    return {'protocol':client.protocol_version,'reconnect':reconnect,'results':run['result_ids']}
    sessions=[asyncio.run(journey(True,False)),asyncio.run(journey(False,True)),asyncio.run(journey(True,True))]
    with OperationalHost(profile,resume=True) as host:
        fresh=host.call('net_replay',{'original_attempt':'installed-first','new_attempt':'installed-fresh'})
        assert set(fresh['execution_ids']).isdisjoint(receipts['J3']['execution_ids'])
        view=snapshot(host);(output/'investigation.html').write_bytes(render_html(view))
        (output/'representative-investigation.json').write_bytes(encode(view))
    cli=subprocess.run([sys.executable,'-I','-m','ciw.net','operational','call','--profile',str(profile),'--resume',
        'oe_inspect','--arguments',json.dumps({'record':receipts['finding']['record']})],cwd=output,
        capture_output=True,text=True,check=True)
    assert json.loads(cli.stdout)['data']==receipts['finding']['data']
    (output/'journey-receipts.json').write_bytes(encode({'sessions':sessions,**receipts,'J5':fresh}))
    identities={}
    for name in ('operational_context.py','operational_agent.py','operational_engineering.py','operational_collection.py',
                 'operational_supervision.py','web/operational.html','web/operational.js','web/operational.css'):
        raw=files('ciw').joinpath(name).read_bytes();identities[name]=sha256(raw).hexdigest()
    report={'status':'PASS','scope':'installed synthetic connected software journeys J1-J5',
        'import_path':str(package),'prefix':sys.prefix,'python':platform.python_version(),
        'generated_client_configuration':json.loads(config_path.read_bytes()),'working_directory_shadowing_blocked':True,
        'dependencies':{name:version(name) for name in ('numpy','mcp','websockets','PyJWT','uvicorn')},
        'module_asset_sha256':identities,'MCP_sessions':sessions,'CLI_MCP_UI_same_finding':receipts['finding']['record'],
        'original_execution_ids':receipts['J3']['execution_ids'],'fresh_execution_ids':fresh['execution_ids'],
        'physical_acquisition':'not_performed','remote_deployment':'not_performed','new_workload_launches':0}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,required=True)
    run(parser.parse_args().output_dir.absolute())
