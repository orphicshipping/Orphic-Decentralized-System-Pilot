"""Data-boundary regressions; fixtures here are synthetic, not provenance proof."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import io
import json

import pytest
from ciw.nise_contract import canonical, parse, validate_query, validate_schematic
from ciw.nise_stdio import StrictInput, MAX_FRAME


def schematic():
    value = {'schema': 'nise.inference-schematic.v1', 'schematic_id': 'sha256:' + '0' * 64,
             'query': {'schema': 'nise.query.v1', 'query_id': 'q', 'question': 'Inert context',
                       'focus_node_ids': ['entity'], 'requested_capabilities': [], 'max_hops': 0,
                       'node_budget': 1, 'include_hypotheses': False},
             'catalog_id': 'catalog', 'catalog_digest': 'sha256:' + '1' * 64,
             'V_q': [{'node_id': 'entity', 'kind': 'ENTITY', 'label': 'Synthetic entity',
                      'status': 'DECLARED', 'attributes': {}, 'evidence_refs': []}],
             'M_q': [], 'C_q': [], 'O_q': [], 'P_q': [], 'E_q': [],
             'selection_trace': [{'node_id': 'entity', 'depth': 0, 'reason': 'focus'}],
             'unresolved_capabilities': [], 'frontier': [],
             'claims': {'candidate_structure': True, 'physical_truth_established': False,
                        'execution_authority': False, 'state_admission': False, 'causal_proof': False}}
    reseal(value)
    return value


def reseal(value):
    value['schematic_id'] = 'sha256:' + sha256(canonical({k: v for k, v in value.items()
                                                        if k != 'schematic_id'})).hexdigest()


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e9999}',
    b'{"x":' + b'9' * 5000 + b'}', b'{"x":"\\ud800"}', b'{"x":"\xff"}', b'[' * 100 + b'0' + b']' * 100])
def test_raw_invalid_data_refuses_before_information_loss(raw):
    with pytest.raises(ValueError):
        parse(raw)


@pytest.mark.parametrize('value', [10**10000, float('inf'), float('nan'), '\udfff', {'x': b'x'}],
                         ids=['huge-int', 'infinity', 'nan', 'surrogate', 'bytes'])
def test_object_data_refuses_before_provider(value):
    with pytest.raises(ValueError):
        canonical(value)


@pytest.mark.parametrize('field,value', [('node_budget', True), ('max_hops', False),
    ('include_hypotheses', 1), ('requested_capabilities', ['signal']), ('focus_node_ids', ['x', 'x'])])
def test_query_types_and_bounds(field, value):
    query = schematic()['query']; query[field] = value
    with pytest.raises(ValueError):
        validate_query(query)


@pytest.mark.parametrize('mutation', ['empty_trace', 'duplicate_trace', 'wrong_partition', 'extra',
    'false_authority_as_zero', 'true_claim_as_one', 'dangling_edge', 'wrong_catalog_ref', 'bad_capability'])
def test_full_structure_rejects_rehashed_invalid_content(mutation):
    value = schematic()
    if mutation == 'empty_trace': value['selection_trace'] = []
    elif mutation == 'duplicate_trace': value['selection_trace'] *= 2
    elif mutation == 'wrong_partition': value['P_q'] = value.pop('V_q'); value['V_q'] = []
    elif mutation == 'extra': value['verified'] = True
    elif mutation == 'false_authority_as_zero': value['claims']['execution_authority'] = 0
    elif mutation == 'true_claim_as_one': value['claims']['candidate_structure'] = 1
    elif mutation == 'wrong_catalog_ref': value['catalog_digest'] = 'bad'
    elif mutation == 'bad_capability': value['unresolved_capabilities'] = ['analysis.bad']
    else:
        value['E_q'] = [{'edge_id': 'edge', 'source': 'entity', 'target': 'absent',
            'relation': 'synthetic', 'status': 'DECLARED', 'directional': False,
            'attributes': {}, 'evidence_refs': []}]
    reseal(value)
    with pytest.raises(ValueError): validate_schematic(value)


def test_frontier_overlap_and_focus_displacement_are_not_silently_rewritten():
    value = schematic()
    value['query']['focus_node_ids'] = ['displaced']
    value['selection_trace'][0]['reason'] = 'requested_capability'
    value['frontier'] = [{'node_id': 'entity', 'blocked_by': 'hypothesis_policy'},
                         {'node_id': 'displaced', 'blocked_by': 'node_budget'}]
    reseal(value)
    assert validate_schematic(value) == value
    detached = validate_schematic(value); detached['frontier'].clear()
    assert value['frontier']


def test_strict_frame_rejects_duplicate_and_invalid_unicode_and_closes():
    pytest.importorskip('mcp', reason='Optional official MCP transport dependency')
    async def run():
        for raw in (b'{"id":1,"id":2}\n', b'{"id":3,"x":"\\udfff"}\n'):
            stream = StrictInput(io.BytesIO(raw + b'{"id":4}\n'))
            with pytest.raises(ValueError, match='invalid_mcp_frame'): await anext(stream)
            with pytest.raises(StopAsyncIteration): await anext(stream)
        valid = {'jsonrpc': '2.0', 'id': 4, 'method': 'ping'}
        assert json.loads(await anext(StrictInput(io.BytesIO(json.dumps(valid).encode() + b'\n')))) == valid
    asyncio.run(run())


@pytest.mark.parametrize('value', [{}, {'jsonrpc': '1.0', 'id': 1, 'method': 'ping'},
    {'jsonrpc': '2.0', 'id': 1, 'method': 123},
    {'jsonrpc': '2.0', 'id': 1, 'method': 'ping', 'params': []}])
def test_strict_frame_closes_on_sdk_envelope_rejection(value):
    pytest.importorskip('mcp', reason='Optional official MCP transport dependency')
    async def run():
        stream = StrictInput(io.BytesIO(json.dumps(value).encode() + b'\n'))
        with pytest.raises(ValueError, match='invalid_mcp_frame'): await anext(stream)
        with pytest.raises(StopAsyncIteration): await anext(stream)
    asyncio.run(run())


def test_overlong_frame_closes_without_decoding_suffix():
    stream = StrictInput(io.BytesIO(b' ' * (MAX_FRAME + 1) + b'{"id":1}\n'))
    async def run():
        with pytest.raises(ValueError, match='frame_budget'): await anext(stream)
    asyncio.run(run())


def test_strict_transport_retains_sdk_descriptor_claim_diversion_and_restoration(tmp_path):
    pytest.importorskip('mcp', reason='Optional official MCP transport dependency')
    import os
    import subprocess
    import sys
    from pathlib import Path
    import ciw
    if os.name != 'posix':
        pytest.skip('Local descriptor custody qualification requires POSIX')
    program = '''import asyncio,json,os,subprocess,sys
sys.path.insert(0, PACKAGE)
from ciw.nise_stdio import strict_stdio_server
from mcp.server.stdio import _claims
import anyio
before=os.fstat(0)
async def run():
    async with strict_stdio_server() as (reader,writer):
        assert 0 in _claims and 1 in _claims
        assert os.read(0,1)==b''
        assert sys.stdin.buffer.read(1)==b''
        child=subprocess.run([sys.executable,'-I','-c','import os;assert os.read(0,1)==b""'],
                             capture_output=True,timeout=5)
        assert child.returncode==0,child.stderr
        try:
            async with strict_stdio_server():
                raise AssertionError('duplicate standard-stream claim accepted')
        except RuntimeError as exc:
            assert 'already claimed fd 0' in str(exc)
        assert 0 in _claims and 1 in _claims
        try: await reader.receive()
        except anyio.EndOfStream: pass
        else: raise AssertionError('expected actual input EOF')
        await writer.aclose()
    assert not _claims
    after=os.fstat(0)
    assert (before.st_dev,before.st_ino)==(after.st_dev,after.st_ino)
asyncio.run(run())
print(json.dumps({'claims_restored':True,'handler_and_inherited_child_read_eof':True}))
'''.replace('PACKAGE', repr(str(Path(ciw.__file__).resolve().parents[1])))
    result = subprocess.run([sys.executable, '-I', '-B', '-c', program], input=b'',
        capture_output=True, cwd=tmp_path, timeout=15)
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout) == {'claims_restored': True, 'handler_and_inherited_child_read_eof': True}
