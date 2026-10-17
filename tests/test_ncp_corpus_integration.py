"""Installed CLI and mixed MCP inspection; synthetic fixtures, no providers."""
import io
import json
import subprocess
import sys

import pytest

from test_corpus_retrieval import setup, built, hashes
from ciw.agent_mcp import demo_config, serve
from ciw.corpus_retrieval import encode


@pytest.fixture
def profile(tmp_path):
    path = demo_config(tmp_path / 'native')
    value = json.loads(path.read_bytes())
    value.update(output_dir=None, allow_operations=[], candidate_domains={}, comparisons={}, check_suites={})
    path.write_bytes(encode(value))
    return path


def combined(profile, built):
    from ciw.corpus_agent import combined_from_profile
    return combined_from_profile(profile, built[0], expected_ref=built[1])


def command(*args, input=b''):
    return subprocess.run([sys.executable, '-m', 'ciw.net', *map(str, args)],
                          input=input, capture_output=True, timeout=30)


def request(i, method, params):
    value = dict(jsonrpc='2.0', method=method, params=params)
    if i is not None:
        value['id'] = i
    return value


def messages():
    rows = [request(1, 'initialize', dict(protocolVersion='2025-11-25', capabilities={},
            clientInfo=dict(name='synthetic-integration-check', version='1'))),
            request(None, 'notifications/initialized', {}), request(2, 'tools/list', {})]
    calls = [('net_capabilities', {}), ('net_inspect', {'artifact': 'source'}),
             ('ncp_corpus_overview', {}), ('ncp_corpus_search', {'query': 'calibration'}),
             ('ncp_corpus_reported_field', {'selection_id': 'NCP-CS-002', 'pointer': '/qualification'}),
             ('net_execute', {'source': 'source', 'graph': 'baseline', 'attempt': 'from-paper'}),
             ('net_inspect', {'artifact': 'NCP-CS-001'}),
             ('ncp_corpus_source', {'selection_id': 'source'}), ('net_capabilities', {})]
    rows += [request(i, 'tools/call', dict(name=n, arguments=a)) for i, (n, a) in enumerate(calls, 3)]
    return b'\n'.join(encode(row) for row in rows) + b'\n'


def assert_mixed(raw):
    replies = {r['id']: r['result'] for r in map(json.loads, raw.splitlines())}
    tools = replies[2]['tools']
    names = {t['name'] for t in tools}
    assert len(names) == len(tools) == 11
    assert all(t['annotations']['readOnlyHint'] for t in tools)
    assert 'net_execute' not in names and 'net_extract' not in names
    assert {'net_inspect', 'net_observe', 'ncp_corpus_search', 'ncp_corpus_span'} <= names
    assert 'untrusted' in replies[1]['instructions']
    assert replies[3]['structuredContent']['output_enabled'] is False
    assert replies[3]['structuredContent']['host_policy']['allowed_operations'] == []
    assert replies[4]['isError'] is False
    assert replies[5]['structuredContent']['sources'] == 3
    assert replies[6]['structuredContent']['hits']
    assert replies[7]['structuredContent']['value'] is False
    for i in (8, 9, 10):
        assert replies[i]['isError'] is True
        assert replies[i]['structuredContent']['status'] == 'refused'
    assert replies[11]['structuredContent'] == replies[3]['structuredContent']


def test_combined_mcp_native_and_corpus_same_connection(profile, built):
    before = hashes(profile.parent), hashes(built[0])
    host = combined(profile, built)
    try:
        wire = io.BytesIO()
        assert serve(host, io.BytesIO(messages()), wire) == 0
        assert_mixed(wire.getvalue())
    finally:
        host.close()
    assert before == (hashes(profile.parent), hashes(built[0]))


def test_installed_command_combines_existing_transport(profile, built):
    result = command('agent', 'serve', '--profile', profile,
                     '--corpus-bundle', built[0], '--corpus-ref', built[1], input=messages())
    assert result.returncode == 0, result.stderr
    assert result.stderr == b''
    assert_mixed(result.stdout)
    assert not (profile.parent / 'agent-output').exists()


def test_operator_command_exposes_corpus(built):
    result = command('corpus', 'inspect', '--bundle', built[0], '--expected-ref', built[1])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['sources'] == 3
    from ciw.operator_commands import COMMANDS
    assert [item[1] for item in COMMANDS if item[0] == 'corpus'] == ['corpus_agent']


@pytest.mark.parametrize('key,value', [('output_dir', 'must-not-exist'),
    ('allow_operations', ['statistics.v1']), ('candidate_domains', {'x': {}}),
    ('comparisons', {'x': {}}), ('check_suites', {'x': {}})])
def test_combined_rejects_grants_before_output_creation(profile, built, key, value):
    document = json.loads(profile.read_bytes()); document[key] = value
    profile.write_bytes(encode(document))
    before = hashes(profile.parent)
    with pytest.raises(ValueError, match='read-only'):
        combined(profile, built)
    assert hashes(profile.parent) == before


@pytest.mark.parametrize('name', ['net_execute', 'net_extract', 'net_candidate', 'net_replay',
                                  'net_compare', 'net_qualify', 'shell', 'ncp_corpus_fetch'])
def test_direct_calls_cannot_escape_inspection(profile, built, name):
    host = combined(profile, built)
    try:
        with pytest.raises(ValueError, match='read-only'):
            host.call(name, {})
        assert host.call('net_capabilities', {})['execution_budget']['used'] == 0
    finally:
        host.close()


@pytest.mark.parametrize('extra', [[], ['--instrument', 'polymer'], ['--resume'],
    ['--provider-checkout', 'missing-provider'], ['--python', 'missing-python']])
def test_invalid_launch_fails_cleanly(profile, built, extra):
    # The empty variant supplies only one of the required pair.
    args = ['agent', 'serve', '--profile', profile, '--corpus-bundle', built[0]]
    if extra: args += ['--corpus-ref', built[1], *extra]
    result = command(*args)
    assert result.returncode != 0 and result.stdout == b''
    assert b'Traceback' not in result.stderr
    assert b'corpus' in result.stderr.lower()
    assert not (profile.parent / 'agent-output').exists()


def test_wrong_corpus_pin_refused_before_native_load(profile, built):
    from ciw.corpus_agent import combined_from_profile
    profile.unlink()
    with pytest.raises(ValueError, match='[Pp]in|[Rr]oot|[Cc]atalog|[Rr]eference'):
        combined_from_profile(profile, built[0], expected_ref='sha256:'+'0'*64)


def test_combined_freezes_both_input_sets(profile, built):
    host = combined(profile, built)
    try:
        source = host.call('net_inspect', {'artifact': 'source'})
        overview = host.call('ncp_corpus_overview', {})
        (profile.parent/'source.json').write_text('tampered')
        (built[0]/'catalog.json').write_text('tampered')
        assert host.call('net_inspect', {'artifact': 'source'}) == source
        assert host.call('ncp_corpus_overview', {}) == overview
        with pytest.raises(ValueError): combined(profile, built)
    finally:
        host.close()


@pytest.mark.parametrize('raw', [b'[]', b'\xff', b'['*1100+b'0'+b']'*1100])
def test_malformed_pinned_catalog_startup_refuses_without_traceback(profile, built, raw):
    from hashlib import sha256
    (built[0]/'catalog.json').write_bytes(raw)
    ref = 'sha256:'+sha256(raw).hexdigest()
    result = command('agent', 'serve', '--profile', profile,
                     '--corpus-bundle', built[0], '--corpus-ref', ref)
    assert result.returncode == 1 and result.stdout == b''
    assert b'startup refused' in result.stderr and b'Traceback' not in result.stderr
    assert not (profile.parent/'agent-output').exists()
