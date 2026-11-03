"""Seeded boundary properties for raw data, not a new scientific serializer."""
from dataclasses import replace
import json
import random

import pytest

from ciw.agent_api import encode
from ciw.campaign_contracts import (CANONICAL_FORMAT, ContractRefusal, Limits,
    canonical, closed_object, integer, mcp_envelope, parse)


def test_canonical_preserves_existing_ciw_bytes_for_supported_values():
    value = {'z': -0.0, 'snow': '\u2603', 'non_bmp': '\U00010000', 'n': 1e-7,
             'array': [None, True, False, 9007199254740991], 'empty': {}}
    assert canonical(value) == encode(value)
    assert CANONICAL_FORMAT == 'ciw.python-json.v1'
    assert b'"z":-0.0' in canonical(value)


def generated(rng, depth=0):
    leaves = [None, True, False, rng.randint(-100000, 100000),
              rng.uniform(-1e6, 1e6), 'q"\\\n\u2603\U0001f40b'[:rng.randrange(10)]]
    if depth == 3 or rng.randrange(3) == 0:
        return rng.choice(leaves)
    if rng.randrange(2):
        return [generated(rng, depth + 1) for _ in range(rng.randrange(5))]
    return {'field_' + str(i): generated(rng, depth + 1) for i in range(rng.randrange(5))}


def test_seeded_roundtrip_and_key_order_properties():
    rng = random.Random(20261103)
    for _ in range(400):
        value = generated(rng)
        raw = canonical(value)
        assert canonical(parse(raw)) == raw == encode(value)
        if isinstance(value, dict):
            assert canonical(dict(reversed(list(value.items())))) == raw


@pytest.mark.parametrize('raw,code', [
    (b'{"x":1,"x":2}', 'duplicate_json_key'),
    (b'{"x":1,"\\u0078":2}', 'duplicate_json_key'),
    (b'{"inside":{"a":1,"a":2}}', 'duplicate_json_key'),
    (b'NaN', 'json_number_limit'), (b'Infinity', 'json_number_limit'),
    (b'-Infinity', 'json_number_limit'), (b'1e999', 'json_number_limit'),
    (b'1e151', 'json_number_limit'), (b'1e-999', 'json_number_underflow'),
    (b'-0.000001e-999', 'json_number_underflow'),
    (b'9007199254740992', 'json_integer_limit'),
    (b'-9007199254740992', 'json_integer_limit'),
    (b'9' * 10000, 'json_integer_limit'),
    (b'"\xff"', 'invalid_unicode'), (b'"\\ud800"', 'invalid_unicode'),
    (b'"\\udfff"', 'invalid_unicode'),
    (b'{} {}', 'invalid_json'), (b'{"x":1,}', 'invalid_json'),
])
def test_raw_refusals_before_loss(raw, code):
    with pytest.raises(ContractRefusal, match='^' + code + '$'):
        parse(raw)


def test_floating_zero_and_unicode_have_explicit_semantics():
    assert parse(b'-0e-999') == -0.0
    assert parse(b'"\\ud800\\udc00"') == '\U00010000'
    assert parse('"\U0001f40b"'.encode()) == '\U0001f40b'
    assert parse(b'"[\\\"{}]"') == '["{}]'
    assert parse(b'9007199254740991') == 9007199254740991


def test_depth_nodes_collection_and_escaped_byte_boundaries():
    assert parse(b'[[0]]', limits=Limits(max_depth=2)) == [[0]]
    with pytest.raises(ContractRefusal, match='json_complexity_limit'):
        parse(b'[[[0]]]', limits=Limits(max_depth=2))
    assert parse(b'{"a":1}', limits=Limits(max_nodes=3)) == {'a': 1}
    with pytest.raises(ContractRefusal, match='json_complexity_limit'):
        parse(b'{"a":1}', limits=Limits(max_nodes=2))
    with pytest.raises(ContractRefusal, match='json_array_limit'):
        parse(b'[0,1]', limits=Limits(max_array=1))
    with pytest.raises(ContractRefusal, match='json_object_limit'):
        parse(b'{"a":0,"b":1}', limits=Limits(max_object=1))
    # Input is three UTF-8 bytes plus quotes; canonical escaping is eight bytes.
    assert canonical('\u2603', limits=Limits(max_bytes=8)) == b'"\\u2603"'
    with pytest.raises(ContractRefusal, match='json_byte_limit'):
        parse('"\u2603"'.encode(), limits=Limits(max_bytes=7))


def test_in_process_types_cycles_and_boolean_integer_are_refused():
    for value in (float('nan'), float('inf'), -float('inf'), 1e151):
        with pytest.raises(ContractRefusal, match='json_number_limit'):
            canonical(value)
    for value in ((), {1: 'not a string key'}, {1}, object()):
        with pytest.raises(ContractRefusal):
            canonical(value)
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ContractRefusal, match='json_complexity_limit'):
        canonical(cycle)
    with pytest.raises(ContractRefusal, match='invalid_integer'):
        integer(True, 0, 1)
    with pytest.raises(ContractRefusal, match='invalid_object_fields'):
        closed_object({'allowed': 1, 'authority': True}, {'allowed'})


def test_wire_limit_includes_escaped_text_and_structured_copy():
    small = {'value': '\u2603' * 1000}
    envelope = mcp_envelope(small)
    measured = len(json.dumps(envelope, ensure_ascii=True, sort_keys=True,
                              separators=(',', ':')).encode())
    assert mcp_envelope(small, limits=Limits(max_bytes=measured)) == envelope
    with pytest.raises(ContractRefusal, match='json_byte_limit'):
        mcp_envelope(small, limits=Limits(max_bytes=measured - 1))
    # Generated text may span several individually valid application strings.
    assert mcp_envelope({'values': ['x' * 40000, 'y' * 40000]})['isError'] is False
    with pytest.raises(ContractRefusal):
        mcp_envelope({'value': '\u2603' * 21000})


@pytest.mark.parametrize('value', [None, True, 1, 'text', []])
def test_mcp_structured_content_is_an_object(value):
    with pytest.raises(ContractRefusal, match='mcp_structured_object_required'):
        mcp_envelope(value)


def test_seeded_raw_mutations_remain_bounded_and_canonical_or_typed_refusal():
    rng = random.Random(3917)
    for _ in range(500):
        raw = canonical(generated(rng))
        position = rng.randrange(len(raw) + 1)
        raw = raw[:position] + rng.choice((b'\xff', b'NaN', b']', b'"', b'\\', b'\0')) + raw[position:]
        try:
            value = parse(raw)
        except ContractRefusal:
            continue
        assert parse(canonical(value)) == value


@pytest.mark.parametrize('field,value', [('max_bytes', 0), ('max_depth', 65),
    ('max_nodes', True), ('max_integer', 2**53), ('max_number', float('nan'))])
def test_request_cannot_select_unbounded_host_limits(field, value):
    with pytest.raises(ContractRefusal, match='invalid_host_json_limits'):
        replace(Limits(), **{field: value})
