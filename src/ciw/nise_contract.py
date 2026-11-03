"""Bounded ODSP validation of inert NISE data, not a second selection engine.

The pinned subprocess owns selection and recomputation. These checks reject
malformed input before that boundary and strengthen the legacy data-only handoff.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from copy import deepcopy
from hashlib import sha256
import json
import math
import re

MAX_BYTES = 262144
MAX_DEPTH = 24
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")
_REF = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CAP = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+\.v[1-9][0-9]*\Z")
_KINDS = {'ENTITY', 'STATE_VARIABLE', 'GEOMETRY', 'MODEL', 'CONSTRAINT', 'OBSERVATION', 'OPERATION'}
_STATUS = {'OBSERVED', 'DERIVED', 'DECLARED', 'HYPOTHESIZED'}
CLAIMS = {'candidate_structure': True, 'physical_truth_established': False,
          'execution_authority': False, 'state_admission': False, 'causal_proof': False}


def _require(value, reason='invalid_nise_contract'):
    if not value:
        raise ValueError(reason)


def canonical(value, *, max_depth=MAX_DEPTH):
    """Bound traversal, integers and output before returning canonical JSON bytes."""
    nodes = 0
    def visit(item, depth=0):
        nonlocal nodes
        nodes += 1
        _require(depth <= max_depth and nodes <= 32768, 'nise_json_complexity_limit')
        if item is None or type(item) is bool:
            return
        if type(item) is int:
            _require(item.bit_length() <= 500 and abs(item) <= 1e150, 'nise_number_limit')
        elif type(item) is float:
            _require(math.isfinite(item) and abs(item) <= 1e150, 'nise_number_limit')
        elif type(item) is str:
            _require(len(item) <= MAX_BYTES, 'nise_string_limit')
            item.encode('utf-8', errors='strict')
        elif type(item) is list:
            _require(len(item) <= 4096, 'nise_array_limit')
            for child in item:
                visit(child, depth + 1)
        elif type(item) is dict:
            _require(len(item) <= 2048, 'nise_object_limit')
            for key, child in item.items():
                _require(type(key) is str and bool(key.strip()) and len(key) <= 512, 'nise_key_limit')
                visit(key, depth + 1)
                visit(child, depth + 1)
        else:
            raise ValueError('nise_json_type')
    visit(value)
    chunks, size = [], 0
    for part in json.JSONEncoder(sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                                 allow_nan=False).iterencode(value):
        raw = part.encode('ascii')
        size += len(raw)
        _require(size <= MAX_BYTES, 'nise_serialized_byte_limit')
        chunks.append(raw)
    return b''.join(chunks)


def parse(raw, limit=65536, *, max_depth=MAX_DEPTH):
    """Reject duplicate keys and deep/oversized numeric tokens before conversion."""
    _require(type(raw) is bytes and 0 < len(raw) <= limit <= MAX_BYTES, 'nise_input_byte_limit')
    text = raw.decode('utf-8', errors='strict')
    quoted, escaped, depth = False, False, 0
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            _require(depth <= max_depth + 1, 'nise_json_complexity_limit')
        elif char in ']}':
            depth -= 1
    def pairs(items):
        result = {}
        for key, item in items:
            _require(key not in result, 'nise_duplicate_json_key')
            result[key] = item
        return result
    def integer(token):
        _require(len(token.lstrip('-')) <= 151, 'nise_number_limit')
        number = int(token)
        _require(abs(number) <= 1e150, 'nise_number_limit')
        return number
    def floating(token):
        _require(len(token) <= 512, 'nise_number_limit')
        number = float(token)
        _require(math.isfinite(number) and abs(number) <= 1e150, 'nise_number_limit')
        return number
    def constant(_):
        raise ValueError('nise_number_limit')
    value = json.loads(text, object_pairs_hook=pairs, parse_int=integer,
                       parse_float=floating, parse_constant=constant)
    canonical(value, max_depth=max_depth)
    return value


def _keys(value, fields):
    _require(type(value) is dict and set(value) == set(fields))


def _text(value, maximum=512):
    _require(type(value) is str and bool(value.strip()) and len(value) <= maximum)


def _pattern(value, pattern):
    _require(type(value) is str and pattern.fullmatch(value) is not None)


def _references(value):
    _require(type(value) is list and len(value) <= 64)
    for ref in value:
        _pattern(ref, _REF)
    _require(len(value) == len(set(value)))


def validate_query(value):
    canonical(value)
    _keys(value, {'schema', 'query_id', 'question', 'focus_node_ids', 'requested_capabilities',
                  'max_hops', 'node_budget', 'include_hypotheses'})
    _require(value['schema'] == 'nise.query.v1')
    _pattern(value['query_id'], _ID)
    _text(value['question'], 8192)
    for field, minimum, pattern in (('focus_node_ids', 1, _ID), ('requested_capabilities', 0, _CAP)):
        items = value[field]
        _require(type(items) is list and minimum <= len(items) <= 64)
        for item in items:
            _pattern(item, pattern)
        _require(len(items) == len(set(items)))
    _require(type(value['max_hops']) is int and 0 <= value['max_hops'] <= 6)
    _require(type(value['node_budget']) is int and 1 <= value['node_budget'] <= 256)
    _require(type(value['include_hypotheses']) is bool)
    return deepcopy(value)


def _node(value, kinds):
    _keys(value, {'node_id', 'kind', 'label', 'status', 'attributes', 'evidence_refs'})
    _pattern(value['node_id'], _ID)
    _require(type(value['kind']) is str and value['kind'] in kinds)
    _text(value['label'])
    _require(type(value['status']) is str and value['status'] in _STATUS)
    _require(type(value['attributes']) is dict)
    _references(value['evidence_refs'])
    if value['kind'] == 'OPERATION':
        _pattern(value['attributes'].get('semantic_capability'), _CAP)


def validate_schematic(value):
    """Validate the complete data contract; does not authenticate or recompile."""
    canonical(value)
    _keys(value, {'schema', 'schematic_id', 'query', 'catalog_id', 'catalog_digest', 'V_q', 'E_q',
                  'M_q', 'C_q', 'O_q', 'P_q', 'selection_trace', 'unresolved_capabilities', 'frontier', 'claims'})
    _require(value['schema'] == 'nise.inference-schematic.v1')
    query = validate_query(value['query'])
    _pattern(value['catalog_id'], _ID)
    _pattern(value['catalog_digest'], _REF)
    _pattern(value['schematic_id'], _REF)
    all_nodes = []
    for key, kinds in (('V_q', {'ENTITY', 'STATE_VARIABLE', 'GEOMETRY'}), ('M_q', {'MODEL'}),
                       ('C_q', {'CONSTRAINT'}), ('O_q', {'OBSERVATION'}), ('P_q', {'OPERATION'})):
        _require(type(value[key]) is list and len(value[key]) <= query['node_budget'])
        for item in value[key]:
            _node(item, kinds)
            all_nodes.append(item)
    ids = [node['node_id'] for node in all_nodes]
    _require(len(ids) <= query['node_budget'] and len(ids) == len(set(ids)))
    _require(type(value['E_q']) is list and len(value['E_q']) <= 4096)
    edge_ids = set()
    for edge in value['E_q']:
        _keys(edge, {'edge_id', 'source', 'target', 'relation', 'status', 'directional', 'evidence_refs', 'attributes'})
        for field in ('edge_id', 'source', 'target'):
            _pattern(edge[field], _ID)
        _require(edge['edge_id'] not in edge_ids)
        edge_ids.add(edge['edge_id'])
        _require(edge['source'] != edge['target'] and edge['source'] in ids and edge['target'] in ids)
        _text(edge['relation'])
        _require(type(edge['status']) is str and edge['status'] in _STATUS)
        _require(type(edge['directional']) is bool and type(edge['attributes']) is dict)
        _references(edge['evidence_refs'])
    _require(type(value['selection_trace']) is list and len(value['selection_trace']) == len(ids),
             'NISE selection trace must cover every selected node')
    traced = set()
    for row in value['selection_trace']:
        _keys(row, {'node_id', 'depth', 'reason'})
        _pattern(row['node_id'], _ID)
        _require(row['node_id'] in ids and row['node_id'] not in traced)
        traced.add(row['node_id'])
        _require(type(row['depth']) is int and 0 <= row['depth'] <= query['max_hops'])
        _require(type(row['reason']) is str and row['reason'] in {'focus', 'requested_capability', 'reachable'})
    _require(type(value['unresolved_capabilities']) is list and len(value['unresolved_capabilities']) <= 64)
    for item in value['unresolved_capabilities']:
        _pattern(item, _CAP)
        _require(item in query['requested_capabilities'])
    _require(len(value['unresolved_capabilities']) == len(set(value['unresolved_capabilities'])))
    _require(type(value['frontier']) is list and len(value['frontier']) <= 4096)
    seen = set()
    for row in value['frontier']:
        _keys(row, {'node_id', 'blocked_by'})
        _pattern(row['node_id'], _ID)
        _require(type(row['blocked_by']) is str and row['blocked_by'] in {'hop_limit', 'node_budget', 'hypothesis_policy'})
        pair = (row['node_id'], row['blocked_by'])
        _require(pair not in seen)
        seen.add(pair)
    _keys(value['claims'], CLAIMS)
    _require(all(type(value['claims'][key]) is bool and value['claims'][key] is expected
                 for key, expected in CLAIMS.items()), 'NISE authority claims exceed contract')
    expected = 'sha256:' + sha256(canonical({k: v for k, v in value.items() if k != 'schematic_id'})).hexdigest()
    _require(value['schematic_id'] == expected, 'NISE schematic content identity mismatch')
    return deepcopy(value)
