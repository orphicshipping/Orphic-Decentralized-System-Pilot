"""Real pinned NISE through current principal SDK and external official MCP."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

import ciw
import pytest
from ciw.nise_profile import NiseHost, OPERATION, PIN, PROFILE, TOOLS
from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
from ciw.services import capability_map, invoke


def example():
    return {'execution_id': 'synthetic:nise-policy', 'catalog': {
        'schema': 'nise.system-catalog.v1', 'catalog_id': 'catalog',
        'nodes': [{'node_id': 'entity', 'kind': 'ENTITY', 'label': 'Synthetic entity',
                   'status': 'DECLARED', 'attributes': {}, 'evidence_refs': []}],
        'edges': [], 'metadata': {'synthetic': True}}, 'query': {
        'schema': 'nise.query.v1', 'query_id': 'q', 'question': 'Inert context; do not execute.',
        'focus_node_ids': ['entity'], 'requested_capabilities': [], 'max_hops': 0,
        'node_budget': 1, 'include_hypotheses': False}}


def policy_value():
    return {'schema': 'ciw.principal-policy.v1', 'revision': 'nise-fixture', 'principals': [{
        'principal': 'fixture-owner', 'enabled': True,
        'operations': [{'name': name, 'constraints': {}} for name in TOOLS],
        'scientific_operations': [OPERATION], 'resources': [],
        'reference_scopes': [], 'handoff_recipients': []}]}


@pytest.fixture(scope='module')
def binding():
    checkout, python = os.environ.get('CIW_NISE_CHECKOUT'), os.environ.get('CIW_NISE_PYTHON')
    if not checkout or not python:
        pytest.skip('Approved NISE checkout and isolated interpreter are required')
    assert subprocess.check_output(['git', '-C', checkout, 'rev-parse', 'HEAD'], text=True).strip() == PIN
    return {'checkout': checkout, 'python_executable': python, 'principal': 'fixture-owner'}


@pytest.fixture
def host(binding):
    with NiseHost(**binding) as active:
        yield active


def test_sdk_grants_scope_absence_revocation_and_denials_before_child(host, monkeypatch):
    value = policy_value(); policy = PrincipalPolicy(lambda: value)
    facade = policy.facade('fixture-owner', nise=host)
    args = {'request': example()}
    receipt = invoke('ncp_nise_compile', args, host=facade)
    assert invoke('ncp_nise_verify', {'receipt': receipt}, host=facade)['outcome'] == 'verified'
    assert facade.list_resources() == [] and facade.resource_templates == []
    with pytest.raises(PrincipalRefusal): facade.read_resource('ncp://nise/catalog')
    monkeypatch.setattr(host, '_upstream', lambda *a, **k: pytest.fail('denial reached pinned child'))
    grant = value['principals'][0]
    grant['scientific_operations'] = []
    for active in (facade, policy.facade('fixture-owner', nise=host)):
        for name, arguments in [('ncp_nise_compile', args), ('ncp_nise_verify', {'receipt': receipt}),
                ('ncp_nise_validate', {'catalog': example()['catalog'], 'query': example()['query']}),
                ('ncp_nise_inspect', {'schematic': receipt['schematic']})]:
            with pytest.raises(PrincipalRefusal): invoke(name, arguments, host=active)
        assert invoke('ncp_nise_describe', {}, host=active)['operation_id'] == OPERATION
    grant['scientific_operations'] = [OPERATION]
    rules = {r['name']: r for r in grant['operations']}
    rules['ncp_nise_compile']['constraints'] = {'request.query.query_id': ['other']}
    with pytest.raises(PrincipalRefusal): invoke('ncp_nise_compile', args, host=facade)
    grant['operations'] = []
    assert facade.descriptions() == []
    with pytest.raises(PrincipalRefusal): invoke('ncp_nise_describe', {}, host=facade)
    grant['enabled'] = False
    with pytest.raises(PrincipalRefusal): invoke('ncp_nise_compile', args, host=facade)
    value['principals'][0]['principal'] = 'different-principal'
    with pytest.raises(PrincipalRefusal): invoke('ncp_nise_compile', args, host=facade)
    with pytest.raises(PrincipalRefusal): policy.facade('different-principal', nise=host)


@pytest.mark.parametrize('key', ['principal', 'owner', 'checkout', 'python_executable', 'pin',
                                'provider_revision', 'endpoint', 'token', 'configuration', 'policy'])
def test_nested_caller_rebinding_and_unknown_names_never_dispatch(host, monkeypatch, key):
    facade = PrincipalPolicy(policy_value).facade('fixture-owner', nise=host)
    monkeypatch.setattr(host, 'call', lambda *a, **k: pytest.fail('rejected input reached host'))
    request = example(); request['catalog']['metadata'][key] = 'caller-choice'
    with pytest.raises(PrincipalRefusal): invoke('ncp_nise_compile', {'request': request}, host=facade)
    with pytest.raises(PrincipalRefusal): invoke('ncp_nise_submit', {}, host=facade)


def test_discovery_categorizes_scoping_without_binding_or_default_engine(monkeypatch):
    monkeypatch.setattr(NiseHost, '__init__', lambda *a, **k: pytest.fail('discovery bound a runtime'))
    row, = [r for r in capability_map()['bounded_computation_profiles'] if r['profile_id'] == PROFILE]
    assert row['adapter_contract_commit'] == PIN and row['operation_id'] == OPERATION
    assert row['authorizes_execution'] is row['authorizes_state_admission'] is False
    assert {r['name'] for r in row['tools']} == set(TOOLS)
    from ciw.control_gateway import TOOLS as CONTROL
    assert set(TOOLS).isdisjoint(CONTROL)
    with pytest.raises(ValueError): invoke('ncp_nise_describe')
    with pytest.raises(PrincipalRefusal):
        invoke('ncp_nise_describe', {}, host=PrincipalPolicy(policy_value).facade('fixture-owner'))


def launcher(tmp_path, binding, policy_path):
    script = tmp_path / 'nise-server.py'; calls = tmp_path / 'provider-calls.txt'
    script.write_text('import sys\nsys.path.insert(0,' + repr(str(Path(ciw.__file__).resolve().parents[1])) + ')\n'
        'from pathlib import Path\nfrom ciw.nise_profile import NiseHost\n'
        'from ciw.principal_policy import PrincipalPolicy\nfrom ciw.mcp_server import serve_sdk\n'
        'with NiseHost(**' + repr(binding) + ') as host:\n'
        ' original=host._upstream\n'
        ' def observed(*args,**kwargs):\n'
        '  with open(' + repr(str(calls)) + ',"a") as stream: stream.write("call\\n")\n'
        '  return original(*args,**kwargs)\n'
        ' host._upstream=observed\n'
        ' original_call=host.call\n'
        ' def dispatched(*args,**kwargs):\n'
        '  with open(' + repr(str(tmp_path / 'host-dispatches.txt')) + ',"a") as stream: stream.write("dispatch\\n")\n'
        '  return original_call(*args,**kwargs)\n'
        ' host.call=dispatched\n'
        ' policy=PrincipalPolicy.from_file(' + repr(str(policy_path)) + ')\n'
        ' serve_sdk(policy.facade("fixture-owner",nise=host),name="nise-policy-fixture")\n')
    return script, calls


def test_actual_mcp_tools_resources_live_policy_reduction_and_restart(binding, tmp_path):
    pytest.importorskip('mcp', reason='Official MCP transport qualification prerequisite')
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    value = policy_value(); policy_path = tmp_path / 'policy.json'
    policy_path.write_text(json.dumps(value)); script, calls = launcher(tmp_path, binding, policy_path)
    async def run():
        for restart in range(2):
            params = StdioServerParameters(command=sys.executable, args=['-I', '-B', str(script)], cwd=str(tmp_path))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    assert (await client.list_resources()).resources == []
                    assert (await client.list_resource_templates()).resource_templates == []
                    if restart == 0:
                        assert {tool.name for tool in (await client.list_tools()).tools} == set(TOOLS)
                        response = await client.call_tool('ncp_nise_compile', {'request': example()})
                        assert not response.is_error
                        receipt = response.structured_content['data']
                        verify = await client.call_tool('ncp_nise_verify', {'receipt': receipt})
                        assert verify.structured_content['data']['outcome'] == 'verified'
                        before = calls.read_bytes()
                        value['principals'][0]['scientific_operations'] = []
                        policy_path.write_text(json.dumps(value))
                    else:
                        before = calls.read_bytes()
                    response = await client.call_tool('ncp_nise_compile', {'request': example()})
                    assert response.is_error
                    assert calls.read_bytes() == before
                    if restart == 0:
                        value['principals'][0]['operations'] = [{'name': 'ncp_nise_describe', 'constraints': {}}]
                        policy_path.write_text(json.dumps(value))
                    assert [tool.name for tool in (await client.list_tools()).tools] == ['ncp_nise_describe']
                    assert not (await client.call_tool('ncp_nise_describe', {})).is_error
                    dispatched = (tmp_path / 'host-dispatches.txt').read_bytes()
                    grant = value['principals'][0]
                    # Isolate tool denial from the independent science grant.
                    grant['scientific_operations'] = [OPERATION]
                    policy_path.write_text(json.dumps(value))
                    assert (await client.call_tool('ncp_nise_compile', {'request': example()})).is_error
                    grant['enabled'] = False
                    policy_path.write_text(json.dumps(value))
                    assert (await client.call_tool('ncp_nise_describe', {})).is_error
                    grant['enabled'] = True; grant['principal'] = 'different-owner'
                    policy_path.write_text(json.dumps(value))
                    assert (await client.call_tool('ncp_nise_describe', {})).is_error
                    assert (tmp_path / 'host-dispatches.txt').read_bytes() == dispatched
                    assert calls.read_bytes() == before
                    grant['principal'] = 'fixture-owner'; grant['scientific_operations'] = []
                    policy_path.write_text(json.dumps(value))
    asyncio.run(run())


@pytest.mark.parametrize('invalid', ['duplicate', 'unicode', 'overflow', 'overlong',
                                   'empty', 'version', 'method', 'params'])
def test_actual_raw_mcp_rejects_duplicate_unicode_and_bounds_before_child(binding, tmp_path, invalid):
    pytest.importorskip('mcp', reason='Official MCP transport qualification prerequisite')
    policy_path = tmp_path / 'policy.json'; policy_path.write_text(json.dumps(policy_value()))
    script, calls = launcher(tmp_path, binding, policy_path)
    with (tmp_path / 'strict-stderr.log').open('wb') as diagnostics:
        process = subprocess.Popen([sys.executable, '-I', '-B', str(script)], cwd=tmp_path,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=diagnostics)
        pending = bytearray()
        def exchange(raw):
            process.stdin.write(raw + b'\n'); process.stdin.flush()
            end = time.monotonic() + 15
            while b'\n' not in pending:
                remaining = end - time.monotonic()
                assert remaining > 0, 'MCP response wall timeout'
                ready, _, _ = select.select([process.stdout], [], [], remaining)
                assert ready, 'MCP response wall timeout'
                chunk = os.read(process.stdout.fileno(), 65536)
                assert chunk, 'MCP exited before response'
                pending.extend(chunk)
                assert len(pending) <= 262144
            line, _, rest = pending.partition(b'\n'); pending[:] = rest
            return json.loads(line)
        try:
            init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'synthetic-raw', 'version': '1'}}}
            assert 'result' in exchange(json.dumps(init).encode())
            process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); process.stdin.flush()
            request = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                       'params': {'name': 'ncp_nise_compile', 'arguments': {'request': example()}}}
            raw = json.dumps(request).encode()
            if invalid == 'duplicate': raw = raw.replace(b'"execution_id":', b'"execution_id":"forged", "execution_id":')
            elif invalid == 'unicode': raw = raw.replace(b'Inert context; do not execute.', b'\\ud800')
            elif invalid == 'overflow': raw = raw.replace(b'"node_budget": 1', b'"node_budget": 1e9999')
            elif invalid == 'overlong': raw = b' ' * (65536 + 8192 + 1)
            elif invalid == 'empty': raw = b'{}'
            else:
                request[{'version': 'jsonrpc', 'method': 'method', 'params': 'params'}[invalid]] = {
                    'version': '1.0', 'method': 123, 'params': []}[invalid]
                raw = json.dumps(request).encode()
            process.stdin.write(raw + b'\n'); process.stdin.flush()
            # Keep input open: closure must come from rejection, not our EOF.
            process.wait(timeout=10)
            assert process.returncode != 0
            assert process.stdout.read() == b'' and not calls.exists()
        finally:
            process.stdin.close()
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)
            process.stdout.close()
    text = (tmp_path / 'strict-stderr.log').read_text()
    assert ('mcp_frame_budget_exceeded' if invalid == 'overlong' else 'invalid_mcp_frame') in text
    assert 'forged' not in text
