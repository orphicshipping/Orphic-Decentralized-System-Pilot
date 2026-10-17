"""Real MCP and CLI integration of every numerical operation, not catalog-only tests."""
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import subprocess

import pytest
import ciw

from ciw.control_contracts import save_new
from ciw.control_plane import experiment
from ciw.instruments import make_demo_run
from test_builtin_numerical import NAMES, request_for


def profile_files(root):
    root.mkdir()
    run = make_demo_run()
    graph = experiment('numerical-builtins',model_id='supplied-linear-models.v1',nodes=[
        {'node_id': 'operation-'+str(i), 'operation_id':name,
         'parameters':{'request':request_for(name,run)},'inputs':{},'depends_on':[]}
        for i,name in enumerate(NAMES)])
    save_new(root/'source.json',run); save_new(root/'experiment.json',graph)
    save_new(root/'profile.json',{'schema':'ciw.agent-host-profile.v1',
        'inputs':{'source':'source.json','baseline':'experiment.json'},'output_dir':'agent-output',
        'allow_operations':list(NAMES),'candidate_domains':{},'comparisons':{},'check_suites':{},
        'max_executions':4,'max_nodes':8})
    return root/'profile.json'


def test_ci_exercises_source_and_installed_numerical_library():
    import yaml
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root/'.github/workflows/mcp-control.yml').read_text())
    job = workflow['jobs']['numerical-builtins']
    assert set(job['strategy']['matrix']['os']) == {'ubuntu-24.04', 'windows-latest'}
    commands = '\n'.join(step.get('run','') for step in job['steps'])
    assert 'tests/test_builtin_numerical.py tests/test_builtin_numerical_mcp.py' in commands
    assert '-I -m pytest -o pythonpath=' in commands
    assert 'site-packages' in commands
    assert 'skipped' in commands


def test_net_cli_runs_every_operation(tmp_path,capsys):
    from ciw.net import main
    root = tmp_path/'profile'; profile_files(root)
    assert main(['run',str(root/'experiment.json'),'--run',str(root/'source.json'),
                 '--output-dir',str(tmp_path/'output')]) == 0
    report = json.loads((tmp_path/'output'/'graph-run.json').read_text())
    assert len(report['nodes']) == 7
    assert all(node['status'] == 'completed' for node in report['nodes'].values())


def test_default_mcp_profile_does_not_inherit_new_execution_grants(tmp_path):
    from ciw.agent_mcp import demo_config,from_profile
    from ciw.control_gateway import ControlHost
    with from_profile(demo_config(tmp_path/'profile')) as owner:
        with ControlHost(owner) as host:
            rows = host.capabilities()['items']
            assert len(rows) == 9
            assert all(not row['authorized'] for row in rows if row['operation_id'] in NAMES)
            assert {row['operation_id'] for row in rows if row['authorized']} == {'statistics.v1'}


def test_documented_example_prepares_then_executes_without_overwriting(tmp_path):
    script = Path(__file__).resolve().parents[1]/'examples'/'numerical_operations'/'prepare.py'
    target = tmp_path/'example'
    env = dict(os.environ, PYTHONPATH=str(Path(ciw.__file__).parent.parent))
    prepared = subprocess.run([sys.executable, str(script), '--output-dir', str(target)],
                              env=env, capture_output=True, text=True)
    assert prepared.returncode == 0, prepared.stderr
    assert not (target/'agent-output').exists()  # Preparation never executes kernels.
    repeated = subprocess.run([sys.executable, str(script), '--output-dir', str(target)],
                              env=env, capture_output=True, text=True)
    assert repeated.returncode != 0
    from ciw.net import main
    assert main(['run',str(target/'experiment.json'),'--run',str(target/'source.json'),
                 '--output-dir',str(tmp_path/'executed')]) == 0
    report = json.loads((tmp_path/'executed'/'graph-run.json').read_text())
    assert len(report['nodes']) == 7
    assert all(node['status'] == 'completed' for node in report['nodes'].values())


def test_live_mcp_all_operations_admission_retry_and_resume(tmp_path):
    pytest.importorskip('mcp')
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    import jsonschema
    root = tmp_path/'profile'; profile = profile_files(root)
    original = sha256((root/'source.json').read_bytes()).hexdigest()

    async def journey():
        done = None
        for modern in (True,False):
            params = StdioServerParameters(command=sys.executable,
                args=['-m','ciw.net','control','serve','--profile',str(profile)] + ([] if modern else ['--resume']),
                env=dict(os.environ,PYTHONPATH=str(Path(ciw.__file__).parent.parent)),cwd=str(tmp_path))
            with (tmp_path/('modern.log' if modern else 'legacy.log')).open('w') as errors:
                async with stdio_client(params,errlog=errors) as (read,write):
                    async with ClientSession(read,write,read_timeout_seconds=30) as client:
                        await (client.discover() if modern else client.initialize())
                        assert client.protocol_version == ('2026-07-28' if modern else '2025-11-25')
                        schemas = {t.name:t.output_schema for t in (await client.list_tools()).tools}
                        async def call(name,args):
                            raw = await client.call_tool(name,args)
                            assert not raw.is_error,raw
                            jsonschema.validate(raw.structured_content,schemas[name])
                            return raw.structured_content['data']
                        capabilities = await call('ncp_capabilities_list',{})
                        assert {r['operation_id'] for r in capabilities['items'] if r['authorized']} == set(NAMES)
                        if not modern:
                            assert await call('ncp_run_get',{'run_id':done['run_id']}) == done
                            assert await call('ncp_run_submit',{'plan_id':done['plan_id'],
                                'expected_digest':done['plan_digest'],'attempt':'all-seven'}) == done
                            continue
                        template = await call('ncp_plan_template',{'baseline':'baseline'})
                        draft = await call('ncp_plan_create',{'source':'source','baseline':'baseline','graph':template['graph']})
                        assert draft['admitted'] is False
                        await call('ncp_plan_admit',{'plan_id':draft['plan_id'],'expected_digest':draft['digest']})
                        submit = await call('ncp_run_submit',{'plan_id':draft['plan_id'],
                            'expected_digest':draft['digest'],'attempt':'all-seven'})
                        async with asyncio.timeout(45):
                            while True:
                                done = await call('ncp_run_get',{'run_id':submit['run_id']})
                                if done['state'] not in {'queued','running'}: break
                                await asyncio.sleep(.05)
                        assert done['state'] == 'completed',done
                        assert len(done['result']['execution_ids']) == 7
                        artifact = done['result']['artifacts']['graph-run']['artifact_id']
                        for i,name in enumerate(NAMES):
                            data = (await call('ncp_artifact_read',{'artifact':artifact,
                                'selector':['nodes','operation-'+str(i),'result','data']}))['data']
                            assert data['operation_id'] == name
                            assert data['scope'] == 'declared_model_only'
                        resource = await client.read_resource(done['uri'])
                        assert json.loads(resource.contents[0].text) == done
        return done
    asyncio.run(journey())
    assert sha256((root/'source.json').read_bytes()).hexdigest() == original
