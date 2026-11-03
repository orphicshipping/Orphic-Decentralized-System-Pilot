"""Shared schema checks remain separate from current grants and real OLM calls."""
from copy import deepcopy
import math
import shutil

import pytest

from ciw.agent_tools import _shape
from ciw.logistics_profile import KINDS, LogisticsHost
from ciw.principal_policy import PrincipalPolicy
from ciw.services import invoke
from logistics_fixture_helpers import Fixture


@pytest.fixture
def logistics(tmp_path, monkeypatch):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Real OLM canonical response verification requires host-selected Node')
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'retained'))
    grant = {'principal': 'alice', 'enabled': True,
             'operations': [{'name': name, 'constraints': {}} for name in
                            ('ncp_logistics_job', 'ncp_logistics_inspect')],
             'scientific_operations': [], 'resources': [],
             'reference_scopes': [], 'handoff_recipients': []}
    document = {'schema': 'ciw.principal-policy.v1', 'revision': 'schema-test-1',
                'principals': [grant]}
    transport = Fixture()
    host = LogisticsHost(transport=transport, principal='alice', tenant='synthetic-test',
                         roles=['pilot:agent-read', 'pilot:compute-read',
                                'pilot:observation-read', 'pilot:state-read', 'pilot:twin-read'],
                         allowed_kinds=sorted(KINDS), node=node, synthetic=True)
    facade = PrincipalPolicy(lambda: document).facade('alice', logistics=host)
    return grant, facade, host, transport


@pytest.mark.parametrize('include', ['status', 'result'])
def test_olm_enum_only_job_reaches_real_host_through_principal_facade(logistics, include):
    _, facade, _, transport = logistics
    result = invoke('ncp_logistics_job', {'job_id': 'j1', 'include': include}, host=facade)
    source = {'kind': 'compute', 'jobId': 'j1', 'include': include}
    assert result['bundle']['source'] == source
    assert result['bundle']['whole_rpc_digest_verified'] is True
    assert result['authorizes_execution'] is False and result['synthetic'] is True
    assert len(transport.calls) == 1
    assert transport.calls[0]['params'] == {'name': 'inspect_retained', 'arguments': {'source': source}}


SELECTORS = [
    {'kind': 'workflow', 'mode': 'plan', 'planId': 'p'},
    {'kind': 'workflow', 'mode': 'run', 'planId': 'p', 'runId': 'r'},
    {'kind': 'acquisition', 'planId': 'p'},
    {'kind': 'compute', 'jobId': 'j', 'include': 'result'},
    {'kind': 'observation', 'observationId': 'o', 'observationDigest': 'sha256:' + 'a' * 64},
    {'kind': 'catalog'},
    {'kind': 'state', 'entityId': 'e', 'validAt': '2026-01-01', 'knownAt': '2026-01-02'},
    {'kind': 'twin', 'projectionId': 'p'},
    {'kind': 'package', 'mode': 'manifest', 'packageId': 'p', 'version': '1'},
]


@pytest.mark.parametrize('source', SELECTORS, ids=[str(i) + '-' + s['kind'] for i, s in enumerate(SELECTORS)])
def test_olm_oneof_inspect_all_canonical_selectors(logistics, source):
    _, facade, _, transport = logistics
    original = deepcopy(source)
    result = invoke('ncp_logistics_inspect', {'source': source}, host=facade)
    assert source == original
    assert result['bundle']['source'] == source
    assert result['bundle']['whole_rpc_digest_verified'] is True
    assert len(transport.calls) == 1


@pytest.mark.parametrize('name,args', [
    ('ncp_logistics_job', {'job_id': 'j', 'include': 'submit'}),
    ('ncp_logistics_job', {'job_id': 'j', 'include': True}),
    ('ncp_logistics_job', {'job_id': 'j', 'include': ['result']}),
    ('ncp_logistics_job', {'job_id': 'j'}),
    ('ncp_logistics_job', {'job_id': 'j', 'include': 'result', 'extra': 1}),
    ('ncp_logistics_inspect', {'source': {'kind': 'catalog', 'planId': 'extra'}}),
    ('ncp_logistics_inspect', {'source': {'kind': 'workflow', 'mode': 'run', 'planId': 'p'}}),
    ('ncp_logistics_inspect', {'source': {'kind': 'package', 'mode': 'delivery', 'packageId': 'p', 'version': '1'}}),
    ('ncp_logistics_inspect', {'source': {'kind': True}}),
    ('ncp_logistics_inspect', {'source': {'kind': 'compute', 'jobId': 'j', 'include': 'submit'}}),
    ('ncp_logistics_inspect', {'source': {'kind': 'observation', 'observationId': 'o', 'observationDigest': 'sha256:' + 'g' * 64}}),
    ('ncp_logistics_inspect', {'source': {'kind': 'observation', 'observationId': 'o', 'observationDigest': 'sha256:' + 'a' * 64 + '\n'}}),
    ('ncp_logistics_inspect', {'source': {'kind': 'twin', 'projectionId': 'x' * 257}}),
])
def test_olm_invalid_shapes_never_reach_backend(logistics, monkeypatch, name, args):
    _, facade, host, transport = logistics
    entered = []
    original = host.call
    def record(*values):
        entered.append(values)
        return original(*values)
    monkeypatch.setattr(host, 'call', record)
    assert facade.call(name, args)['status'] == 'refused'
    assert entered == [] and transport.calls == []


def test_valid_schema_does_not_replace_current_tool_or_selector_grants(logistics):
    grant, facade, _, transport = logistics
    args = {'job_id': 'j', 'include': 'status'}
    rule = grant['operations'][0]
    rule['constraints'] = {'include': ['status']}
    assert facade.call('ncp_logistics_job', args)['status'] == 'ok'
    count = len(transport.calls)
    assert facade.call('ncp_logistics_job', {**args, 'include': 'result'})['status'] == 'refused'
    grant['enabled'] = False
    assert facade.call('ncp_logistics_job', args)['status'] == 'refused'
    grant['enabled'] = True
    grant['operations'] = []
    assert facade.call('ncp_logistics_job', args)['status'] == 'refused'
    assert facade.call('ncp_logistics_unknown', {})['status'] == 'refused'
    assert len(transport.calls) == count


@pytest.mark.parametrize('value,schema', [
    ('status', {'enum': ['status', 'result']}),
    ('catalog', {'const': 'catalog'}),
    (None, {'type': 'null'}),
    (1.25, {'type': 'number', 'minimum': 1, 'maximum': 2}),
    (2, {'type': 'number', 'minimum': 1, 'maximum': 2}),
    (['a'], {'type': 'array', 'minItems': 1, 'maxItems': 1, 'items': {'type': 'string'}}),
    ({'x': 1}, {'type': 'object'}),
    ({'x': 1}, {'type': 'object', 'properties': {}, 'additionalProperties': True}),
    ({'x': 1}, {'required': ['x']}),
    ({}, {}),
])
def test_supported_shapes_accept_exact_values_without_mutation(value, schema):
    before = deepcopy((value, schema))
    assert _shape(value, schema) is None
    assert (value, schema) == before


@pytest.mark.parametrize('value,schema', [
    (True, {'type': 'integer'}), (1.0, {'type': 'integer'}), (1, {'type': 'boolean'}),
    (True, {'type': 'number'}), ('1', {'type': 'number'}), (0, {'type': 'null'}),
    (True, {'enum': [1]}), (1, {'enum': [True]}), (1.0, {'enum': [1]}),
    ({'x': [True]}, {'const': {'x': [1]}}),
    ({'x': [1]}, {'enum': [{'x': [1.0]}]}),
    ('', {'type': 'string', 'minLength': 1}), ('abc', {'type': 'string', 'maxLength': 2}),
    (0, {'type': 'integer', 'minimum': 1}), (3, {'type': 'integer', 'maximum': 2}),
    (2**53 + 1, {'type': 'integer'}),
    ([], {'type': 'array', 'minItems': 1, 'items': {}}),
    ([1, 2], {'type': 'array', 'maxItems': 1, 'items': {}}),
    ({}, {'type': 'object', 'required': ['x']}),
    ({'x': 1}, {'type': 'object', 'additionalProperties': False}),
    ({'x': 1}, {'type': 'object', 'properties': {}}),
    ({'x': 1, 'extra': 2}, {'type': 'object', 'properties': {'x': {'type': 'integer'}}}),
])
def test_exact_types_and_all_declared_constraints_refuse(value, schema):
    with pytest.raises(ValueError):
        _shape(value, schema)


@pytest.mark.parametrize('composition', ['anyOf', 'oneOf'])
def test_compositions_apply_sibling_constraints_and_all_branches(composition):
    spec = {composition: [{'type': 'string'}, {'type': 'integer'}],
            'enum': ['ok', 2], 'maxLength': 2, 'minimum': 1}
    _shape('ok', spec)
    _shape(2, spec)
    for value in ('no', 'long', 0, True):
        with pytest.raises(ValueError):
            _shape(value, spec)
    with pytest.raises(ValueError):
        _shape('ok', {composition: [{'const': 'ok'}, {'not': {}}]})
    with pytest.raises(ValueError):
        _shape({}, {composition: [{'type': 'object'}], 'required': ['x']})
    with pytest.raises(ValueError):
        _shape(1, {composition: [{'const': 1}], 'type': 'string'})


@pytest.mark.parametrize('composition', ['anyOf', 'oneOf'])
@pytest.mark.parametrize('rule,invalid,valid', [
    ({'minLength': 2}, 'x', 'xx'),
    ({'maxLength': 2}, 'xxx', 'xx'),
    ({'minimum': 2}, 1, 2),
    ({'maximum': 2}, 3, 2),
    ({'minItems': 1}, [], [1]),
    ({'maxItems': 1}, [1, 2], [1]),
    ({'const': 'ok'}, 'bad', 'ok'),
    ({'properties': {'x': {'type': 'integer'}}, 'additionalProperties': False},
     {'x': 1, 'extra': 2}, {'x': 1}),
    ({'items': {'type': 'integer'}}, [True], [1]),
    ({'pattern': '^sha256:[0-9a-f]{64}$'}, 'sha256:' + 'g' * 64, 'sha256:' + 'a' * 64),
])
def test_each_composition_sibling_constraint_independently(composition, rule, invalid, valid):
    schema = {composition: [{}], **rule}
    _shape(valid, schema)
    with pytest.raises(ValueError):
        _shape(invalid, schema)


def test_oneof_requires_exactly_one_matching_branch():
    _shape(1, {'anyOf': [{'type': 'integer'}, {'const': 1}]})
    with pytest.raises(ValueError):
        _shape(1, {'oneOf': [{'type': 'integer'}, {'const': 1}]})
    with pytest.raises(ValueError):
        _shape('other', {'oneOf': [{'const': 1}, {'const': 'x'}]})


@pytest.mark.parametrize('pattern', ['^sha256:[0-9a-f]{64}$', '^sha256:[a-fA-F0-9]{64}$'])
def test_canonical_patterns_are_bounded_exact_digest_checks(pattern):
    spec = {'type': 'string', 'pattern': pattern}
    _shape('sha256:' + 'a' * 64, spec)
    for value in ('sha256:' + 'g' * 64, 'prefixsha256:' + 'a' * 64,
                  'sha256:' + 'a' * 64 + '\n', 'sha256:' + 'a' * 63):
        with pytest.raises(ValueError):
            _shape(value, spec)
    if 'A-F' in pattern:
        _shape('sha256:' + 'A' * 64, spec)
    else:
        with pytest.raises(ValueError):
            _shape('sha256:' + 'A' * 64, spec)


@pytest.mark.parametrize('schema', [
    {'type': 'object', 'format': 'invented'}, {'$ref': '#/anything'},
    {'type': ['string', 'integer']}, {'type': 'invented'},
    {'type': 'string', 'pattern': '(a+)+$'}, {'type': 'string', 'pattern': '['},
    {'type': 'integer', 'minimum': True}, {'type': 'string', 'maxLength': False},
    {'type': 'array', 'minItems': -1}, {'type': 'array', 'items': []},
    {'type': 'object', 'additionalProperties': 'false'},
    {'type': 'object', 'required': 'x'}, {'type': 'object', 'required': ['x', 'x']},
    {'type': 'object', 'properties': []}, {'enum': []}, {'enum': 'yes'},
    {'oneOf': []}, {'anyOf': {}}, {'type': 'string', 'minLength': 3, 'maxLength': 2},
    {'type': 'integer', 'minimum': math.nan}, True,
])
def test_unsupported_or_malformed_schema_fails_closed(schema):
    with pytest.raises(ValueError):
        _shape('x', schema)


@pytest.mark.parametrize('value', [math.inf, -math.inf, math.nan, 10**400, '\ud800',
                                 {'x': math.nan}, {'x': object()}, {True: 'bad'},
                                 {'x': 'a' * 65537}])
def test_open_payload_is_still_finite_plain_bounded_json(value):
    with pytest.raises(ValueError):
        _shape(value, {})


def test_cycles_depth_and_total_traversal_refuse_before_recursion_error():
    cycle = []
    cycle.append(cycle)
    schema_cycle = {'anyOf': []}
    schema_cycle['anyOf'].append(schema_cycle)
    deep = {}
    for _ in range(25):
        deep = {'x': deep}
    for value, spec in ((cycle, {}), ('x', schema_cycle), (deep, {'type': 'object'}),
                        ([list(range(65536))] * 5, {'type': 'array', 'items': {}})):
        with pytest.raises(ValueError):
            _shape(value, spec)


def test_combination_work_and_aggregate_text_have_independent_bounds():
    # Small schema and valid data; repeated alternative evaluation is separately
    # bounded instead of silently ignoring siblings or accepting early.
    alternative = {'type': 'array', 'items': {'enum': list(range(256))}}
    with pytest.raises(ValueError, match='work exceeds bounds'):
        _shape([255] * 5000, {'anyOf': [alternative] * 3})
    with pytest.raises(ValueError, match='text exceeds bounds'):
        _shape(['x' * 65536] * 129, {'type': 'array'})


def test_all_current_discovery_schemas_fit_supported_vocabulary():
    from ciw.agent_tools import TOOLS
    from ciw.principal_policy import _specs
    from ciw import simulation_agent_tools, external_work_mcp
    specs = {**TOOLS, **_specs(), **simulation_agent_tools.TOOLS, **external_work_mcp.TOOLS}
    for name, row in specs.items():
        try:
            _shape({}, row['inputSchema'])
        except ValueError as exc:
            # Missing required arguments are legitimate; unsupported schema is not.
            assert 'schema' not in str(exc).lower(), (name, str(exc))
