"""Actual CLI and SDK stdio access to the same retained GaugeLink candidate."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
import ciw
from ciw import link_refit_cli as cli


def command(*args):
    value = subprocess.run([sys.executable, '-m', 'ciw.link_refit_cli', *map(str,args)],
        env={**os.environ, 'PYTHONPATH':str(Path(ciw.__file__).resolve().parents[1])},
        capture_output=True, text=True, timeout=60)
    assert value.returncode == 0, value.stderr
    return json.loads(value.stdout)


def call_cli(tmp_path, profile, tool, args):
    path = tmp_path / 'arguments.json'
    path.write_text(json.dumps(args), encoding='utf-8')
    return command('call','--profile',profile,'--tool',tool,'--arguments',path)


def test_real_mcp_and_cli_journey(tmp_path, record_property):
    anyio = pytest.importorskip('anyio')
    pytest.importorskip('mcp')
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    configured = command('demo','--output-dir',tmp_path/'demo')
    profile = Path(configured['profile'])
    assert configured['synthetic'] and not configured['acquisition_performed']
    config = json.loads(Path(configured['mcp_config']).read_text())
    params = StdioServerParameters(**config['mcpServers']['ncp-gaugelink-refit'])
    original = {p.name:p.read_bytes() for p in profile.parent.glob('*.json')}
    preview_args = json.loads((profile.parent/'example-preview.json').read_text())
    receipt = {'process_sessions':0,'protocol_versions':[],'tool_calls':0}
    async def call(client, name, args):
        result = await client.call_tool(name, args)
        assert not result.is_error, result
        assert json.loads(result.content[0].text) == result.structured_content
        receipt['tool_calls'] += 1
        return result.structured_content
    async def run():
        with anyio.fail_after(120):
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    init = await client.initialize()
                    receipt['process_sessions'] += 1
                    receipt['protocol_versions'].append(init.protocol_version)
                    assert len((await client.list_tools()).tools) == 7
                    status = await call(client,'ncp_board_status',{})
                    preview = await call(client,'ncp_board_preview',preview_args)
                    validation = await call(client,'ncp_board_validate',{'preview_ref':preview['preview_ref']})
                    baseline = await call(client,'ncp_board_baseline',{'attempt_id':'sdk-baseline'})
            args = {'preview_ref':preview['preview_ref'],'validation_ref':validation['validation_ref'],
                    'baseline_ref':baseline['baseline_ref'],'attempt_id':'sdk-candidate'}
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    init = await client.initialize()
                    receipt['process_sessions'] += 1
                    receipt['protocol_versions'].append(init.protocol_version)
                    candidate = await call(client,'ncp_board_recompute',args)
                    assert candidate['status'] == 'completed'
                    assert candidate['reused_nodes'] == ['basis']
                    assert set(candidate['rerun_nodes']) == {'hypothesis','audit'}
                    compare = await call(client,'ncp_board_compare',{'candidate_ref':candidate['candidate_ref']})
                    assert not compare['accepted']
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    init = await client.initialize()
                    receipt['process_sessions'] += 1
                    receipt['protocol_versions'].append(init.protocol_version)
                    assert await call(client,'ncp_board_recompute',args) == candidate
                    assert await call(client,'ncp_board_candidate',{'candidate_ref':candidate['candidate_ref']}) == candidate
                    assert await call(client,'ncp_board_baseline',{'attempt_id':'sdk-baseline'}) == baseline
                    final = await call(client,'ncp_board_status',{})
                    assert final['budgets']['candidate_runs_used'] == 1
                    assert final['execution_profile']['original_result_refs'] == status['execution_profile']['original_result_refs']
            return candidate,args
    candidate,args = anyio.run(run)
    assert call_cli(tmp_path, profile, 'ncp_board_recompute',args) == candidate
    assert call_cli(tmp_path, profile,'ncp_board_candidate',{'candidate_ref':candidate['candidate_ref']}) == candidate
    assert {p.name:p.read_bytes() for p in profile.parent.glob('*.json')} == original
    assert candidate['accepted'] is False and candidate['review_status'] == 'unreviewed'
    record_property('negotiated_mcp_protocol', receipt['protocol_versions'][0])
    record_property('stdio_process_sessions', receipt['process_sessions'])
    (tmp_path/'journey-receipt.json').write_text(json.dumps({**receipt, 'candidate':candidate},indent=2))


def test_grant_and_source_revocation(tmp_path):
    configured = cli.demo(tmp_path/'demo')
    path = Path(configured['profile'])
    grant = tmp_path/'demo'/'grant.json'
    with cli.open_host(path) as host:
        raw = grant.read_bytes()
        grant.unlink()
        from ciw.board_refit import BoardRefitRefusal
        with pytest.raises(BoardRefitRefusal, match='authorization_refused'):
            host.call('ncp_board_baseline', {'attempt_id':'revoked'})
        grant.write_bytes(raw)
        assert host.call('ncp_board_status', {})['attempts'] == []
        bundle = path.parent/'bundle.json'
        bundle.write_bytes(bundle.read_bytes() + b' ')
        with pytest.raises(BoardRefitRefusal, match='authorization_refused'):
            host.descriptions()
    with pytest.raises(ValueError):
        cli.open_host(path)
