"""A fresh MCP process reopens native Link results through trusted offline readers."""
import json

from test_corpus_retrieval import setup, built
from test_ncp_corpus_integration import command, profile, request
from ciw.corpus_retrieval import encode


def test_fresh_process_inspects_link_workspace_without_restoring_grants(tmp_path, profile, built):
    from ciw import engineering_thread as engineering
    from ciw import engineering_thread_io as thread
    from ciw import link_contract as contract
    from ciw.link_study_cli import example

    raw = encode(example())
    intent = encode(engineering.example_intent('scalar', contract.bytes_ref(raw)))
    compiled = thread.compile_packet(intent, raw, tmp_path/'compiled', host_profile='scalar')
    review = thread.run_packet(tmp_path/'compiled', tmp_path/'executed', host_profile='scalar',
                               expected_compiled_ref=compiled['compiled_ref'], allow_compute=True)
    workspace = tmp_path/'executed/run/session/workspace.json'
    original = workspace.read_bytes()
    value = json.loads(profile.read_bytes())
    value['inputs'] = {'workspace': str(workspace)}
    profile.write_bytes(encode(value))
    rows = [request(1, 'initialize', dict(protocolVersion='2025-11-25', capabilities={},
              clientInfo=dict(name='fresh-workspace-reader', version='1'))),
            request(None, 'notifications/initialized', {}),
            request(2, 'tools/call', dict(name='net_inspect', arguments={'artifact': 'workspace'})),
            request(3, 'tools/call', dict(name='net_capabilities', arguments={}))]
    result = command('agent', 'serve', '--profile', profile, '--corpus-bundle', built[0],
                     '--corpus-ref', built[1], input=b'\n'.join(encode(r) for r in rows)+b'\n')
    assert result.returncode == 0, result.stderr
    answers = {r['id']: r['result'] for r in map(json.loads, result.stdout.splitlines())}
    assert answers[2]['isError'] is False
    data = answers[2]['structuredContent']['data']
    assert set(data['executions']) == set(review['execution_ids'])
    assert set(data['results']) == set(review['result_ids'])
    caps = answers[3]['structuredContent']
    assert caps['execution_budget']['used'] == 0 and caps['output_enabled'] is False
    assert caps['host_policy']['allowed_operations'] == []
    assert workspace.read_bytes() == original
