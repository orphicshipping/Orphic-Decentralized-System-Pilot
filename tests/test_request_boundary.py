"""Installed opt-in shadow hook: Python remains the sole request authority."""
import asyncio
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

import ciw
import pytest

from ciw import request_boundary as boundary
from ciw import request_boundary_transport as transport
from ciw.agent_mcp import Server, serve, MAX_MESSAGE, MAX_REQUESTS


RAW = b'{"jsonrpc":"2.0","id":1,"method":"ping"}'


class Host:
    def __init__(self):
        self.calls = []
    def descriptions(self):
        return []
    def call(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        if name != 'allowed' or arguments != {}:
            return {'status':'refused', 'authorizes_execution':False}
        return {'status':'ok', 'data':{'value':1}, 'authorizes_execution':False}


class Observation:
    def __init__(self, failure=None):
        self.seen = []
        self.counts = []
        self.failure = failure
    def prefix(self, raw, seen):
        self.seen.append(seen)
        assert type(seen) is frozenset
        if self.failure is not None:
            raise self.failure
        return {'decision':'candidate', 'execution_authorized':True}
    def wire_output(self, count):
        self.counts.append(count)
        if self.failure is not None:
            raise self.failure
        return {'decision':'output_candidate', 'execution_authorized':True}


def fixture_binary(tmp_path, *, result=None, body=None):
    path = tmp_path / 'shadow-fixture'
    if body is None:
        result = result or {'decision':'candidate', 'code':None, 'response_id':1,
                            'consume_id':True, 'execution_authorized':False}
        reply = {'schema':'odsp.request-boundary.reply.v1', 'status':'ok', 'result':result}
        body = 'import sys,json\njson.load(sys.stdin)\nprint(' + repr(json.dumps(reply)) + ')\n'
    path.write_text('#!' + sys.executable + '\n' + body, encoding='utf-8')
    path.chmod(0o700)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def active(tmp_path, **kwargs):
    path, digest = fixture_binary(tmp_path, **kwargs)
    return boundary.RequestBoundary(enabled=True, binary=path, binary_sha256=digest)


def test_all_reviewed_source_hashes_match_actual_package():
    for module in (boundary.agent_api, boundary.agent_mcp, boundary.control_contracts,
                   boundary.session, boundary.request_boundary_contracts,
                   boundary.request_boundary_transport, boundary.runtime):
        assert boundary.digest_file(Path(module.__file__)) == boundary.SOURCE_SHA256[module.__name__.rsplit('.',1)[-1]]


@pytest.mark.parametrize('raw', [b'', b'{', b'[]', b'null', b'NaN', b'\xff',
    b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}',
    b'{"jsonrpc":"2.0","id":true,"method":"ping"}',
    b'{"jsonrpc":"2.0","method":"tools/call"}', RAW])
@pytest.mark.parametrize('failure', [None, OSError('synthetic observer error')])
def test_observer_cannot_override_python_prefix_or_consume_ids(raw, failure):
    observer = Observation(failure)
    baseline, observed = Server(Host()), Server(Host(), request_boundary=observer)
    baseline.seen = {(str,'1')}
    observed.seen = {(str,'1')}
    assert observed.handle(raw) == baseline.handle(raw)
    assert observed.seen == baseline.seen
    assert observer.seen == [frozenset({(str,'1')})]
    assert observed.host.calls == baseline.host.calls == []


def test_duplicate_id_and_connection_budget_are_not_reopened_by_observer():
    observer = Observation()
    server = Server(Host(), request_boundary=observer)
    assert server.handle(RAW)['result'] == {}
    assert server.handle(RAW)['error']['code'] == -32600
    server.seen = {(int,i) for i in range(MAX_REQUESTS)}
    assert server.handle(b'{"jsonrpc":"2.0","id":99999,"method":"ping"}')['error']['code'] == -32000
    assert len(server.seen) == MAX_REQUESTS and server.host.calls == []


def test_denials_and_serialized_output_limit_remain_authoritative():
    observer = Observation()
    host = Host()
    server = Server(host, request_boundary=observer)
    server.initialized = True
    result = server.handle(b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"forbidden","arguments":{}}}')
    assert result['result']['isError'] is True
    assert host.calls == [('forbidden',{})]
    # This controlled output is serialized exactly as the live server would.
    host.mcp_result = lambda *_: {'content':[{'type':'text','text':'x'*MAX_MESSAGE}]}
    result = server.handle(b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"allowed","arguments":{}}}')
    assert result['result']['isError'] is True
    assert observer.counts[-1] > MAX_MESSAGE - 1024
    assert 'wire budget' in result['result']['structuredContent']['reason']
    assert (int,2) in server.seen  # Output refusal does not undo the operation.


def test_plain_cancellation_is_not_diagnostic_fallback():
    primary = KeyboardInterrupt('synthetic cancellation')
    server = Server(Host(), request_boundary=Observation(primary))
    with pytest.raises(KeyboardInterrupt) as caught:
        server.handle(RAW)
    assert caught.value is primary and server.seen == set() and server.host.calls == []


@pytest.mark.parametrize('signal_type', [KeyboardInterrupt, SystemExit, asyncio.CancelledError, GeneratorExit])
@pytest.mark.parametrize('chain', ['cause', 'context', 'group'])
def test_cleanup_cannot_turn_cancellation_into_request_admission(tmp_path, monkeypatch, signal_type, chain):
    observer = active(tmp_path)
    primary = signal_type('current request interrupted')
    cleanup = OSError('after-effect cleanup failure')
    def interrupted(_):
        try:
            raise primary
        except BaseException:
            if chain == 'cause':
                raise cleanup from primary
            if chain == 'group':
                raise cleanup from BaseExceptionGroup('retained diagnostics', [primary])
            raise cleanup
    monkeypatch.setattr(observer, '_run', interrupted)
    server = Server(Host(), request_boundary=observer)
    with pytest.raises(BaseExceptionGroup) as caught:
        server.handle(RAW)
    assert primary in caught.value.exceptions and cleanup in caught.value.exceptions
    assert server.seen == set() and server.host.calls == []
    assert observer._cleanup_uncertain is None  # Diagnostics alone do not imply lost custody.


def test_previously_handled_cancellation_does_not_cancel_current_request(tmp_path, monkeypatch):
    observer = active(tmp_path)
    def diagnostic(_):
        raise OSError('independent observation failure')
    monkeypatch.setattr(observer, '_run', diagnostic)
    server = Server(Host(), request_boundary=observer)
    try:
        raise asyncio.CancelledError('already handled by caller')
    except asyncio.CancelledError:
        assert server.handle(RAW)['result'] == {}
    assert server.seen == {(int, 1)} and observer.last_observation['status'] == 'fallback'


def test_stdio_entry_point_accepts_only_host_supplied_observer():
    observer = Observation(OSError('diagnostic failed'))
    output = io.BytesIO()
    assert serve(Host(), io.BytesIO(RAW+b'\n'), output, request_boundary=observer) == 0
    assert json.loads(output.getvalue()) == {'jsonrpc':'2.0','id':1,'result':{}}


def test_default_and_raw_rejection_never_launch(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('no child may launch')
    monkeypatch.setattr(boundary, '_exchange', forbidden)
    disabled = boundary.RequestBoundary()
    assert disabled.prefix(RAW)['execution_authorized'] is False
    assert disabled.last_observation['status'] == 'disabled'
    enabled = boundary.RequestBoundary(enabled=True)
    assert enabled.prefix(b'{"id":1,"id":2}')['code'] == -32700
    assert enabled.last_observation['status'] == 'python_only'


def test_actual_fixed_reply_matches_and_forged_authority_cannot_escape(tmp_path):
    observer = active(tmp_path)
    assert observer.prefix(RAW)['execution_authorized'] is False
    assert observer.last_observation['status'] == 'matched'
    path, digest = fixture_binary(tmp_path, result={'execution_authorized':True})
    forged = boundary.RequestBoundary(enabled=True, binary=path, binary_sha256=digest)
    assert forged.prefix(RAW)['execution_authorized'] is False
    assert forged.last_observation == {'status':'mismatch','reason':'result_fields','execution_authorized':False}


def test_source_drift_refuses_observation_before_launch(tmp_path, monkeypatch):
    observer = active(tmp_path)
    monkeypatch.setitem(boundary.SOURCE_SHA256, 'agent_mcp', '0'*64)
    monkeypatch.setattr(boundary, '_exchange', lambda *a: pytest.fail('drift must not launch'))
    observer.prefix(RAW)
    assert observer.last_observation['reason'] == 'source_binding'


@pytest.mark.parametrize('kind', ['symlink', 'parent_symlink', 'fifo', 'directory'])
def test_nonregular_or_link_executable_refuses_without_blocking(tmp_path, monkeypatch, kind):
    path, digest = fixture_binary(tmp_path)
    if kind == 'symlink':
        selected=tmp_path/'selected';selected.symlink_to(path)
    elif kind == 'parent_symlink':
        parent=tmp_path/'linked';parent.symlink_to(tmp_path,target_is_directory=True)
        selected=parent/path.name
    elif kind == 'fifo':
        selected=tmp_path/'pipe';os.mkfifo(selected)
    else:
        selected=tmp_path
    monkeypatch.setattr(boundary, '_exchange', lambda *a: pytest.fail('invalid file must not launch'))
    observer=boundary.RequestBoundary(enabled=True,binary=selected,binary_sha256=digest,timeout=0.05)
    started=time.monotonic();observer.prefix(RAW)
    assert time.monotonic()-started < 1
    assert observer.last_observation['status']=='fallback'


def test_sealed_snapshot_survives_path_replacement_and_rejects_writes(tmp_path):
    original, digest = fixture_binary(tmp_path, body='print("original")\n')
    with transport.executable_snapshot(original, digest) as snapshot:
        original.write_text('#!'+sys.executable+'\nprint("replacement")\n')
        with pytest.raises(OSError):
            with snapshot.open('wb') as target:
                target.write(b'replacement')
        assert transport._exchange(snapshot,'boundary-v1',b'{}',1)==b'original\n'
    assert not snapshot.exists()


def test_second_read_must_match_expected_digest_before_exec(tmp_path, monkeypatch):
    observer=active(tmp_path)
    original=boundary.digest_file
    def checked(path):
        value=original(path)
        if path==observer.binary:
            path.write_text('#!'+sys.executable+'\nprint("different")\n')
        return value
    monkeypatch.setattr(boundary,'digest_file',checked)
    monkeypatch.setattr(boundary,'_exchange',lambda *a:pytest.fail('changed snapshot must not launch'))
    observer.prefix(RAW)
    assert observer.last_observation['reason']=='binary_binding'


def test_real_official_client_uses_packaged_hook_without_added_grants(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    binary,digest=fixture_binary(tmp_path,result={'execution_authorized':True})
    summary=tmp_path/'observation.json'
    module_parent=str(Path(ciw.__file__).resolve().parent.parent)
    program='''import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from ciw.agent_mcp import demo_config,from_profile,serve
from ciw.control_gateway import ControlHost
from ciw.request_boundary import RequestBoundary
observer=RequestBoundary(enabled=True,binary=Path(sys.argv[2]),binary_sha256=sys.argv[3])
with from_profile(demo_config(Path.cwd()/'profile')) as owner,ControlHost(owner) as host:
    serve(host,sys.stdin.buffer,sys.stdout.buffer,request_boundary=observer)
Path(sys.argv[4]).write_text(json.dumps(observer.last_observation))
'''
    async def run():
        params=StdioServerParameters(command=sys.executable,args=['-I','-B','-c',program,
            module_parent,str(binary),digest,str(summary)],cwd=str(tmp_path))
        async with stdio_client(params) as (reader,writer):
            async with ClientSession(reader,writer,read_timeout_seconds=30) as client:
                await client.initialize()
                listed=await client.list_tools()
                assert 'ncp_capabilities_list' in {tool.name for tool in listed.tools}
                allowed=await client.call_tool('ncp_capabilities_list',{})
                assert not allowed.is_error
                denied=await client.call_tool('arbitrary_shell',{'command':'not executable'})
                assert denied.is_error
    asyncio.run(run())
    assert json.loads(summary.read_text())=={'status':'mismatch','reason':'result_fields','execution_authorized':False}
