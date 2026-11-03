"""Actual modern and legacy MCP journeys with a host-selected native backend."""
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import pytest
import ciw
from test_builtin_numerical import NAMES
from test_builtin_numerical_mcp import profile_files
from test_rust_numerical import backend, binary, lapack_libraries


def test_rust_mcp_all_operations_admission_retry_and_resume(backend, tmp_path):
    pytest.importorskip('mcp')
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    import jsonschema
    root = tmp_path/'profile'; profile = profile_files(root)
    original = sha256((root/'source.json').read_bytes()).hexdigest()
    binding = tmp_path/'native-binding.json'
    policy = {key:getattr(backend.process_policy,key) for key in backend.process_policy.__dataclass_fields__}
    policy['environment'] = dict(policy['environment'])
    binding.write_text(json.dumps({'schema':'odsp.rust-numerical-host-binding.v1',
        'binary':str(backend.binary),'binary_sha256':backend.expected_sha256,
        'lapack_libraries':{str(path):digest for path,digest in backend._libraries},'process_policy':policy}))
    launcher = Path(__file__).resolve().parents[1]/'native/odsp-core/mcp_launcher.py'
    # This wrapper proves that the real child never calls the Python compute backend.
    trusted = tmp_path/'trusted-launcher.py'
    trusted.write_text("from unittest.mock import patch\nimport runpy\n"
        "with patch('ciw.numerical_kernels.calculate', side_effect=AssertionError('Python fallback')):\n"
        "    runpy.run_path("+repr(str(launcher))+", run_name='__main__')\n")

    async def journey():
        done = None
        for modern in (True,False):
            params = StdioServerParameters(command=sys.executable,
                args=[str(trusted),'--profile',str(profile),'--binding',str(binding)] + ([] if modern else ['--resume']),
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
                            runtime = (await call('ncp_artifact_read',{'artifact':artifact,
                                'selector':['nodes','operation-'+str(i),'result','runtime']}))['data']
                            assert runtime['provider'] == 'ciw.numerical.rust'
                            assert runtime['binary_sha256'] == backend.expected_sha256
                            assert runtime['native_libraries_sha256'] == backend.runtime_identity()['native_libraries_sha256']
                        resource = await client.read_resource(done['uri'])
                        assert json.loads(resource.contents[0].text) == done
        return done
    asyncio.run(journey())
    assert sha256((root/'source.json').read_bytes()).hexdigest() == original
