"""Native authoritative policy differentials against the actual Python oracle.

Set ODSP_NATIVE_POLICY_BINARY to an independently built release executable.
These tests launch the real native process; a missing binary is a visible skip.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

from ciw import principal_policy as oracle
from ciw import rust_principal_policy as native
from ciw.agent_api import encode
from ciw.process_policy import ProcessPolicy, executable_sha256


def limits(**changes):
    value = dict(environment={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
        wall_seconds=3, cpu_seconds=3, memory_bytes=256 * 1024 * 1024,
        file_bytes=1048576, max_input_bytes=native.MAX_INPUT, max_output_bytes=native.MAX_OUTPUT,
        expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'))
    value.update(changes)
    return ProcessPolicy(**value)


@pytest.fixture(scope='session')
def binding():
    path = os.environ.get('ODSP_NATIVE_POLICY_BINARY')
    if not path:
        pytest.skip('ODSP_NATIVE_POLICY_BINARY is required for actual native qualification')
    binary = Path(path)
    assert binary.is_absolute() and binary.is_file() and not binary.is_symlink()
    identity = json.loads(subprocess.check_output([str(binary), 'identity-v1'], timeout=3))
    root = Path(__file__).resolve().parents[1]
    for name, expected in identity['native_sources_sha256'].items():
        assert sha256((root / name).read_bytes()).hexdigest() == expected, name
    return dict(binary=binary, expected_sha256=sha256(binary.read_bytes()).hexdigest(), process_policy=limits())


@pytest.fixture(scope='session')
def differential_evidence():
    rows = []
    yield rows
    folder = os.environ.get('ODSP_NATIVE_POLICY_EVIDENCE')
    if folder:
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        report = {'schema': 'ciw.native-policy-differential.v1', 'cases': rows,
            'total': len(rows), 'accepted': sum(r['accepted'] for r in rows),
            'refused': sum(not r['accepted'] for r in rows),
            'full_control_engine_qualified': False, 'formal_proof_established': False}
        (target / 'differential.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')


def full_policy():
    catalog = native._catalog()
    direct = catalog['direct']
    science = {r[0] for r in direct['domain']} | set(direct['workbench'].values()) | {'statistics.v1'}
    for mapping in (direct['fluid'], direct['fixed']):
        for values in mapping.values():
            science.update(values)
    return {'schema': 'ciw.principal-policy.v1', 'revision': 'test-native-1', 'principals': [{
        'principal': 'alice', 'enabled': True,
        'operations': [{'name': n, 'constraints': {}} for n in sorted(catalog['specs'])],
        'scientific_operations': sorted(science),
        'resources': ['ncp://plans/', 'ncp://runs/', 'ncp://artifacts/', 'ncp://retained/',
            'ncp://proto-corpus/' + 'a' * 64 + '/', 'ncp://evidence/' + 'a' * 64 + '/'],
        'reference_scopes': ['sha256:' + 'a' * 64], 'handoff_recipients': []}]}


def outcome(policy, method, args):
    try:
        return True, native._transport(getattr(policy, method)(*args), native.MAX_OUTPUT)
    except (ValueError, TypeError, OverflowError):
        return False, None


def compare(value, rust, method, args, evidence, label=''):
    expected = outcome(oracle.PrincipalPolicy(lambda: value), method, args)
    actual = outcome(rust, method, args)
    assert actual == expected, (label, method, args, expected, actual)
    evidence.append({'method': method, 'case': label, 'accepted': actual[0],
                     'result_sha256': sha256(actual[1].encode()).hexdigest() if actual[0] else None})
    return actual[0]


def minimum(schema):
    if 'const' in schema:
        return deepcopy(schema['const'])
    if 'enum' in schema:
        return deepcopy(schema['enum'][0])
    for key in ('anyOf', 'oneOf'):
        if key in schema:
            return minimum(schema[key][0])
    kind = schema.get('type')
    if kind == 'object':
        return {k: minimum(schema['properties'][k]) for k in schema.get('required', [])}
    if kind == 'array':
        return [minimum(schema.get('items', {})) for _ in range(schema.get('minItems', 0))]
    if kind == 'string':
        if schema.get('pattern'):
            return 'sha256:' + 'a' * 64
        return 'x' * schema.get('minLength', 0)
    if kind == 'integer':
        return max(schema.get('minimum', 0), 0)
    if kind == 'number':
        return float(max(schema.get('minimum', 0), 0))
    if kind == 'boolean':
        return False
    return None


def valid_arguments(name, schema):
    value = minimum(schema)
    if name == 'ncp_domain_run':
        row = native._catalog()['direct']['domain'][0]
        value = {'domain': row[1]}
        if row[2] is not None:
            value['name'] = row[2]
    if name == 'ncp_coordinate':
        value = {'action': 'recall'}
    return value


def test_all_registered_schemas_acceptance_and_rejection(binding, differential_evidence):
    policy = full_policy()
    rust = native.RustPrincipalPolicy(lambda: policy, **binding)
    accepted = set()
    for name, row in oracle._specs().items():
        schema = row['inputSchema']
        args = valid_arguments(name, schema)
        assert compare(policy, rust, 'authorize', ('alice', name, args), differential_evidence, name)
        accepted.add(name)
        for field in schema.get('required', []):
            changed = deepcopy(args)
            changed.pop(field, None)
            assert not compare(policy, rust, 'authorize', ('alice', name, changed), differential_evidence, name + ':missing:' + field)
        for field, shape in schema.get('properties', {}).items():
            changed = deepcopy(args)
            changed[field] = minimum(shape)
            compare(policy, rust, 'authorize', ('alice', name, changed), differential_evidence, name + ':present:' + field)
            changed[field] = {'unexpected': True} if shape.get('type') != 'object' else ['invalid']
            compare(policy, rust, 'authorize', ('alice', name, changed), differential_evidence, name + ':wrongtype:' + field)
        for changed in ({**args, 'principal': 'bob'}, {**args, 'unexpected_argument': True}, [], None):
            assert not compare(policy, rust, 'authorize', ('alice', name, changed), differential_evidence, name + ':override')
    assert accepted == set(oracle._specs())


def test_current_policy_closed_validation_and_unicode(binding, differential_evidence):
    policy = full_policy()
    rust = native.RustPrincipalPolicy(lambda: policy, **binding)
    original = deepcopy(policy)
    mutations = [lambda p: None, lambda p: p.update(extra=True), lambda p: p.update(schema='wrong'),
        lambda p: p.update(revision='../bad'), lambda p: p.update(principals=[]),
        lambda p: p['principals'].append(deepcopy(p['principals'][0])),
        lambda p: p['principals'][0].update(enabled=1),
        lambda p: p['principals'][0].update(enabled=False),
        lambda p: p['principals'][0].update(principal='bob'),
        lambda p: p['principals'][0].update(handoff_recipients=['unbound']),
        lambda p: p['principals'][0].update(reference_scopes=['sha256:' + 'A'*64]),
        lambda p: p['principals'][0]['operations'].append({'name': 'shell', 'constraints': {}}),
        lambda p: p['principals'][0]['operations'].append(deepcopy(p['principals'][0]['operations'][0])),
        lambda p: p['principals'][0]['operations'][0].update(constraints={'a..b': [1]}),
        lambda p: p['principals'][0]['operations'][0].update(constraints={'a': []}),
        lambda p: p['principals'][0]['operations'][0].update(constraints={'a': [float('nan')]}),
        lambda p: p['principals'][0].update(scientific_operations=['\ud800', '界🌍']),
        lambda p: p['principals'][0].update(resources=['ncp://\ud800']),
        lambda p: p['principals'][0].update(resources=['ncp://hello/world']),
        lambda p: p['principals'][0].update(resources=['ncp://runs/%2E']),
        lambda p: p['principals'][0].update(resources=['ncp://runs/../']),
        lambda p: p['principals'][0].update(resources=['ncp://runs/\x00']),
        lambda p: p['principals'][0].update(resources=['ncp://runs/#x'])]
    for index, mutate in enumerate(mutations):
        policy.clear(); policy.update(deepcopy(original)); mutate(policy)
        for method, args in [('snapshot', ()), ('check_principal', ('alice',)), ('context', ('alice',))]:
            compare(policy, rust, method, args, differential_evidence, 'policy:' + str(index))


def test_typed_constraints_and_open_json(binding, differential_evidence):
    policy = full_policy()
    rust = native.RustPrincipalPolicy(lambda: policy, **binding)
    rule = next(r for r in policy['principals'][0]['operations'] if r['name'] == 'ncp_domain_run')
    rule['constraints'] = {'request.value': []}
    values = [1, 1.0, True, False, 0, -0.0, 0.0, 2**53, 2**100, 1e150, math.nextafter(1e150, 0),
              '界🌍', '\ud800', {'b': 2, 'a': 1}, {'a': 1.0, 'b': 2}, [1, True, 1.0], None]
    for expected in values:
        rule['constraints']['request.value'] = [expected]
        for supplied in values:
            compare(policy, rust, 'authorize', ('alice', 'ncp_domain_run',
                {'domain': 'sequence', 'example': True, 'request': {'value': supplied}}),
                differential_evidence, 'typed:' + repr(expected) + ':' + repr(supplied))
    rule['constraints'] = {}
    for payload in [float('inf'), float('nan'), 10**151, {'token': 'injection'}, ('tuple',), {' ': 1}, {1: 1}]:
        assert not compare(policy, rust, 'authorize', ('alice', 'ncp_domain_run',
            {'domain': 'sequence', 'request': {'value': payload}}), differential_evidence, 'open-json-negative')
    # Each prefix keeps its original integer contract; no coercion to binary64.
    for name, field in [('ncp_domain_run', 'request'), ('ncp_nise_validate', 'request'), ('ncp_skill_run', 'inputs')]:
        args = valid_arguments(name, oracle._specs()[name]['inputSchema'])
        if field not in oracle._specs()[name]['inputSchema']['properties']:
            field = next(k for k, v in oracle._specs()[name]['inputSchema']['properties'].items() if v.get('type') == 'object')
        for number in [2**53-1, 2**53, 10**150, 1e150, math.nextafter(1e150, math.inf), -0.0]:
            args[field] = {'x': number}
            compare(policy, rust, 'authorize', ('alice', name, args), differential_evidence, name + ':number')


def test_exact_text_transport_and_constraint_parity(binding, differential_evidence):
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **binding)
    rule = next(r for r in value['principals'][0]['operations'] if r['name'] == 'ncp_domain_run')
    strings = ['🌍', '\ud83c\udf0d', '\ud800', '~ud800;', '~~', '~',
               '\\ud800', 'a\ud83c\udf0db', 'a🌍b', '~ud83c;~udf0d;']
    for permitted in strings:
        rule['constraints'] = {'request.value': [permitted]}
        for supplied in strings:
            compare(value, rust, 'authorize', ('alice', 'ncp_domain_run',
                {'domain': 'sequence', 'request': {'value': supplied}}),
                differential_evidence, 'exact-text:' + repr(permitted) + ':' + repr(supplied))
    # Two distinct Python keys can have identical ordinary JSON escapes. Their
    # values and ordering still matter to the existing policy hash and snapshot.
    rule['constraints'] = {'request.value': [{key: i for i, key in enumerate(strings)}]}
    value['principals'][0]['scientific_operations'].extend(strings)
    value['principals'][0]['resources'].extend('ncp://' + s for s in strings if '\\' not in s)
    for method, args in [('snapshot', ()), ('check_principal', ('alice',)), ('context', ('alice',))]:
        assert compare(value, rust, method, args, differential_evidence, 'distinct-code-points:' + method)
    assert rust.snapshot() == value
    assert rust.snapshot()['principals'][0]['operations'] == value['principals'][0]['operations']


def test_direct_science_nested_operations_and_schematic_exception(binding, differential_evidence):
    policy = full_policy()
    rust = native.RustPrincipalPolicy(lambda: policy, **binding)
    grant = policy['principals'][0]
    allowed = grant['scientific_operations'][:]
    direct = native._catalog()['direct']
    calls = []
    for operation, domain, name in direct['domain']:
        args = {'domain': domain}
        if name is not None:
            args['name'] = name
        calls.append(('ncp_domain_run', args))
        calls.append(('ncp_coordinate', {'action': 'submit', 'operation': 'ncp_domain_run', 'arguments': args}))
    calls += [('ncp_workbench_run', {'workflow': w}) for w in direct['workbench']]
    calls += [('ncp_fluid_field', {'solver': s}) for s in direct['fluid']]
    calls += [('ncp_lesson', {'mode': 'work'})]
    calls += [(name, valid_arguments(name, oracle._specs()[name]['inputSchema'])) for name in direct['fixed']]
    for name, args in calls:
        grant['scientific_operations'] = allowed
        accepted = compare(policy, rust, 'authorize', ('alice', name, args), differential_evidence, name + ':science-allowed')
        # The fluid catalog also names kernels deliberately absent from this tool's enum.
        if name != 'ncp_fluid_field' or args['solver'] in ('wave', 'sph'):
            assert accepted
        grant['scientific_operations'] = []
        assert not compare(policy, rust, 'authorize', ('alice', name, args), differential_evidence, name + ':science-revoked')
    grant['scientific_operations'] = allowed
    args = {'action': 'submit', 'operation': 'ncp_domain_run', 'arguments': {'domain': 'sequence'}}
    grant['operations'] = [r for r in grant['operations'] if r['name'] != 'ncp_domain_run']
    assert not compare(policy, rust, 'authorize', ('alice', 'ncp_coordinate', args), differential_evidence, 'inner-grant-missing')
    for kind, owner in [('certificate', 'jspt'), ('certificate', 'caller'), ('other', 'jspt')]:
        args = {'schematic': {'nodes': [{'kind': kind, 'attrs': {'owner': owner}}]}}
        compare(policy, rust, 'authorize', ('alice', 'ncp_schematic_validate', args), differential_evidence, 'inert-schematic-owner')
    for config in [native._catalog()['assessment_policy'], {'principal': 'caller'}]:
        args = {'receipt': {'bundle': {'configuration': config}}}
        compare(policy, rust, 'authorize', ('alice', 'ncp_schematic_verify_receipt', args), differential_evidence, 'inert-receipt-configuration')


def test_resource_prefix_scope_and_refusal_parity(binding, differential_evidence):
    policy = full_policy()
    rust = native.RustPrincipalPolicy(lambda: policy, **binding)
    good = ['ncp://plans/a', 'ncp://runs/a', 'ncp://artifacts/a', 'ncp://retained/retained-' + 'b'*32,
        'ncp://proto-corpus/' + 'a'*64 + '/overview', 'ncp://proto-corpus/' + 'a'*64 + '/source/' + 'b'*64,
        'ncp://evidence/' + 'a'*64 + '/overview', 'ncp://evidence/' + 'a'*64 + '/span/' + 'b'*64]
    bad = ['ncp://runs/a/b', 'ncp://runs/a?x', 'ncp://runs/%2E', 'ncp://runs/../a', 'ncp://runs/界',
           'ncp://plans/', 'https://example.test', 'ncp://runs/\ud800', [], None, True]
    for uri in good:
        assert compare(policy, rust, 'authorize_resource', ('alice', uri), differential_evidence, 'resource-positive')
    for uri in bad:
        assert not compare(policy, rust, 'authorize_resource', ('alice', uri), differential_evidence, 'resource-negative')
    policy['principals'][0]['reference_scopes'] = []
    for uri in good[4:]:
        assert not compare(policy, rust, 'authorize_resource', ('alice', uri), differential_evidence, 'resource-scope-revoked')
    policy['principals'][0]['resources'] = ['ncp://runs/a']
    for uri in ['ncp://runs/a', 'ncp://runs/ab', 'ncp://artifacts/a']:
        compare(policy, rust, 'authorize_resource', ('alice', uri), differential_evidence, 'resource-exact')


def test_seeded_request_mutations(binding, differential_evidence):
    rng = random.Random(0x235ee45a)
    policy = full_policy()
    rust = native.RustPrincipalPolicy(lambda: policy, **binding)
    specs = oracle._specs()
    values = [False, 0, 1.0, None, '', '界🌍', '\ud800', [], {}, {'configuration': {}}, 2**53, 1e151]
    for i in range(200):
        name = rng.choice(sorted(specs))
        args = valid_arguments(name, specs[name]['inputSchema'])
        keys = list(specs[name]['inputSchema'].get('properties', {})) + ['extra', 'principal']
        args[rng.choice(keys)] = deepcopy(rng.choice(values))
        compare(policy, rust, 'authorize', (rng.choice(['alice', 'bob', True]), name, args), differential_evidence, 'seeded:' + str(i))


def disable_python(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Python policy authorization fallback was called')
    for method in ('snapshot', 'check_principal', 'authorize', 'authorize_context', 'authorize_resource', 'context'):
        monkeypatch.setattr(oracle.PrincipalPolicy, method, forbidden)
    for function in ('validate_policy', '_request', '_rule', '_direct_science'):
        monkeypatch.setattr(oracle, function, forbidden)


def test_atomic_fresh_authorize_context_and_no_python_fallback(binding, monkeypatch):
    value = full_policy()
    reads = []
    def read():
        reads.append(value['revision'])
        return value
    rust = native.RustPrincipalPolicy(read, **binding)
    expected = oracle.PrincipalPolicy(lambda: value).context('alice')
    disable_python(monkeypatch)
    assert rust.authorize_context('alice', 'ncp_capabilities_list', {}) == expected
    assert reads == [value['revision']]
    assert rust.snapshot() == value
    assert rust.check_principal('alice')['enabled'] is True
    assert rust.authorize_resource('alice', 'ncp://runs/a')['principal'] == 'alice'
    value['principals'][0]['operations'] = []
    with pytest.raises(oracle.PrincipalRefusal):
        rust.authorize_context('alice', 'ncp_capabilities_list', {})
    value['principals'][0]['enabled'] = False
    with pytest.raises(oracle.PrincipalRefusal):
        rust.context('alice')


def test_malformed_child_results_fail_closed(binding, monkeypatch):
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **binding)
    disable_python(monkeypatch)
    for result in [None, True, 1, [], {}, {'principal': 'alice', 'enabled': True},
                   {**value['principals'][0], 'enabled': 1}, {**value['principals'][0], 'operations': True}]:
        def reply(mode, raw):
            encoded = json.dumps(result, sort_keys=True, separators=(',', ':'))
            return {'schema': 'odsp.rust-policy.reply.v1', 'status': 'ok', 'proof_established': False,
                'input_sha256': sha256(raw).hexdigest(), 'result_sha256': sha256(encoded.encode()).hexdigest(),
                'result_json': encoded}
        monkeypatch.setattr(rust, '_exchange', reply)
        with pytest.raises(oracle.PrincipalRefusal):
            rust.authorize('alice', 'ncp_capabilities_list', {})
    for reply in [None, {}, [], True, {'status': 'ok'}]:
        monkeypatch.setattr(rust, '_exchange', lambda *a: reply)
        with pytest.raises(oracle.PrincipalRefusal):
            rust.authorize_context('alice', 'ncp_capabilities_list', {})


def test_source_binary_protocol_and_reader_failure_refuse(binding, monkeypatch, tmp_path):
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **binding)
    source = rust._source_hashes
    monkeypatch.setattr(native, '_sources', lambda: {**source, 'principal_policy.py': '0'*64})
    with pytest.raises(oracle.PrincipalRefusal):
        rust.authorize('alice', 'ncp_capabilities_list', {})
    monkeypatch.setattr(native, '_sources', lambda: source)
    symlink = tmp_path / 'policy-link'
    symlink.symlink_to(binding['binary'])
    rust.binary = symlink
    with pytest.raises(oracle.PrincipalRefusal):
        rust.authorize('alice', 'ncp_capabilities_list', {})
    rust.binary = binding['binary']
    rust.expected_sha256 = '0'*64
    with pytest.raises(oracle.PrincipalRefusal):
        rust.snapshot()
    rust.expected_sha256 = binding['expected_sha256']
    monkeypatch.setattr(native, '_bounded_process', lambda *a, **k: (0, b'{"status":"ok","status":"ok"}'))
    with pytest.raises(oracle.PrincipalRefusal):
        rust.snapshot()
    rust._reader = lambda: (_ for _ in ()).throw(OSError('reader unavailable'))
    with pytest.raises(oracle.PrincipalRefusal):
        rust.authorize('alice', 'ncp_capabilities_list', {})


def test_native_transport_actual_timeout_output_and_no_inherited_secrets(binding, monkeypatch, tmp_path):
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **binding)
    disable_python(monkeypatch)
    monkeypatch.setenv('ODSP_TEST_SECRET_MUST_NOT_INHERIT', 'fixture-only')
    rust.process_policy = limits(wall_seconds=.1)
    for name, program in [('timeout', 'import time; time.sleep(5)'),
                          ('output', 'import os; os.write(1,b"x"*1100000)'),
                          ('environment', 'import os,json; print(json.dumps(dict(os.environ)))')]:
        path = tmp_path / name
        path.write_text('#!' + sys.executable + '\n' + program + '\n')
        path.chmod(0o700)
        rust.binary, rust.expected_sha256 = path, sha256(path.read_bytes()).hexdigest()
        if name == 'environment':
            rust.process_policy = limits()
            reply = rust._exchange('policy-v1', b'{}')
            assert 'ODSP_TEST_SECRET_MUST_NOT_INHERIT' not in reply
            assert set(reply) <= {'PATH', 'LANG', 'LC_CTYPE'}
        else:
            with pytest.raises(oracle.PrincipalRefusal):
                rust.authorize('alice', 'ncp_capabilities_list', {})


def test_policy_file_duplicate_keys_symlinks_and_source_identity(binding, tmp_path):
    path = tmp_path / 'policy.json'
    value = full_policy()
    path.write_text(json.dumps(value))
    rust = native.RustPrincipalPolicy.from_file(path, **binding)
    assert rust.snapshot() == value
    identity = rust.runtime_identity()
    assert identity['proof_established'] is False
    assert identity['native_build']['profile'] == 'release'
    assert len(identity['source_files_sha256']) == len(native.SOURCE_FILES)
    path.write_text('{"schema":"x","schema":"y"}')
    with pytest.raises(oracle.PrincipalRefusal):
        rust.snapshot()
    link = tmp_path / 'link.json'
    link.symlink_to(path)
    other = native.RustPrincipalPolicy.from_file(link, **binding)
    with pytest.raises(oracle.PrincipalRefusal):
        other.snapshot()


def test_actual_mcp_facade_and_current_grant_revocation(binding, tmp_path, monkeypatch):
    import sys
    from ciw.agent_mcp import Server, demo_config, from_profile
    from ciw.control_gateway import ControlHost
    from ciw.services import admit, invoke
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path / 'retained'))
    value = full_policy()
    rust = native.RustPrincipalPolicy(lambda: value, **binding)
    request_validator = oracle._request
    disable_python(monkeypatch)
    shared_validations = []
    def shared_request_validation(name, arguments):
        # Raw service request validation remains a distinct Python boundary.
        # Native authorization must never use it as a decision fallback.
        assert sys._getframe(1).f_code is admit.__code__
        shared_validations.append((name, deepcopy(arguments)))
        return request_validator(name, arguments)
    monkeypatch.setattr(oracle, '_request', shared_request_validation)
    assert rust.authorize('alice', 'ncp_capabilities_list', {})['enabled'] is True
    assert shared_validations == []
    with from_profile(demo_config(tmp_path / 'demo')) as owner:
        with ControlHost(owner, retained_owner=oracle.owner_for('alice')) as control:
            facade = rust.facade('alice', control=control)
            server = Server(facade)
            response = server.handle(encode({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'native-test', 'version': '1'}}}))
            assert 'error' not in response
            assert server.handle(encode({'jsonrpc': '2.0', 'method': 'notifications/initialized'})) is None
            response = server.handle(encode({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                'params': {'name': 'ncp_capabilities_list', 'arguments': {}}}))
            assert response['result']['structuredContent']['status'] == 'ok'
            assert response['result']['structuredContent']['data']['items']
            assert shared_validations == [('ncp_capabilities_list', {})]
            artifact = control.retained.keep({'value': 'native-selected'}, kind='test', catalog={})
            assert facade.read_resource('ncp://retained/' + artifact)['bundle'] == {'value': 'native-selected'}
            value['principals'][0]['operations'] = []
            assert facade.call('ncp_capabilities_list', {})['status'] == 'refused'
            assert facade.descriptions() == []
            with pytest.raises(ValueError):
                invoke('ncp_capabilities_list', {}, host=facade)
            assert owner.capabilities()['execution_budget']['used'] == 0
