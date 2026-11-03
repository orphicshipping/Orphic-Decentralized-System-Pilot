"""Actual Ed25519 signatures and real MCP transport over synthetic statements.

No SP1 host/guest is fabricated or qualified by these tests.
"""
import asyncio
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import struct
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ciw import proof_profile as profile
from ciw.campaign_contracts import canonical
from ciw.device_boundary import PROGRAM, compute
from ciw.legibility import sign_bundle
from ciw.proof_profile import ProofHost, receipt_bundle, invoke_proof
from ciw.proof_registry import ProofKeyRegistry, ProofRefusal
from test_proof_registry import registry_document


def example():
    row = registry_document()['entries'][0]
    statement = {'schema': profile.STATEMENT, 'system': 'ed25519-attribution',
                 'execution_id': 'execution-' + 'a' * 32, 'subject': 'fixture-owner',
                 **{name: row[name] for name in ('program_id', 'circuit_id', 'source_revision', 'policy_id')},
                 'public_inputs': {'fixture_only': True, 'sample': [1, 2, 3], 'unit': 'dimensionless'}}
    return statement


def fixture(value=None, private=None):
    value = deepcopy(example() if value is None else value)
    bundle = receipt_bundle(value)
    private = private or Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    return {'schema': profile.RECEIPT, 'statement': value, 'bundle': bundle,
            'signature': sign_bundle(bundle, private)}


def arguments(receipt=None, request=None):
    receipt = fixture() if receipt is None else receipt
    request = receipt['statement'] if request is None else request
    return {'request_json': json.dumps(request), 'receipt_json': json.dumps(receipt)}


def host(registry=None, principal='fixture-owner'):
    return ProofHost(registry=registry or ProofKeyRegistry(registry_document()), principal=principal)


def reason(value):
    return value['data']['reason']


def test_actual_signature_full_content_sdk_mcp_and_replay_identity():
    h = host()
    first = h.call('ncp_proof_verify', arguments())
    assert first['status'] == 'ok', first
    data = first['data']
    assert data['outcome'] == 'accepted'
    assert data['verification_scope'] == 'ed25519_signature_attribution_only'
    assert data['signature_verified'] is True
    assert data['native_verification_performed'] is False
    assert data['authorizes_execution'] is data['authorizes_state_admission'] is False
    second = invoke_proof(h, 'ncp_proof_verify', arguments())
    for field in ('request_digest', 'receipt_digest', 'execution_id', 'trust'):
        assert data[field] == second[field]
    assert data['verification_id'] != second['verification_id']
    assert h.mcp_result('ncp_proof_verify', first)['structuredContent'] == first
    assert h.mcp_result('ncp_proof_verify', first)['isError'] is False


def test_describe_does_not_claim_native_proof_or_source_authentication():
    response = host().call('ncp_proof_describe', {})['data']
    assert response['verification_performed'] is False
    assert response['native_verification'] == 'held_missing_trusted_sp1_runtime'
    assert response['registry']['source_authentication'] == 'not_established'
    assert response['unsupported_native'] == ['proved_heat', 'ordinary_f2_checker']
    assert len(host().descriptions()) == 2
    assert host().resource_descriptions() == host().resource_templates() == []
    with pytest.raises(ProofRefusal):
        host().read_resource('ncp://proof/anything')


@pytest.mark.parametrize('mutation', ['source', 'projection', 'manifest', 'bundle_id', 'signature', 'extra'])
def test_unchanged_id_does_not_validate_forged_full_content(mutation):
    receipt = fixture()
    if mutation == 'source':
        receipt['bundle']['source']['object']['label'] = 'forged content'
    if mutation == 'projection':
        receipt['bundle']['representations']['human']['label'] = 'forged content'
    if mutation == 'manifest':
        receipt['bundle']['manifest']['compiler'] = 'forged compiler'
    if mutation == 'bundle_id':
        receipt['bundle']['bundle_id'] = 'sha256:' + '0' * 64
    if mutation == 'signature':
        receipt['signature']['signature_b64'] = base64.b64encode(b'\0' * 64).decode()
    if mutation == 'extra':
        receipt['verified'] = True
    assert host().call('ncp_proof_verify', arguments(receipt))['status'] == 'refused'


def test_changed_content_recomputed_bundle_cannot_reuse_signature():
    receipt = fixture()
    receipt['statement']['public_inputs']['sample'][0] = 77
    receipt['bundle'] = receipt_bundle(receipt['statement'])
    assert host().call('ncp_proof_verify', arguments(receipt))['status'] == 'refused'


def test_genuine_signature_wrong_key_cannot_self_establish_trust():
    private = Ed25519PrivateKey.from_private_bytes(b'\x99' * 32)
    receipt = fixture(private=private)
    assert reason(host().call('ncp_proof_verify', arguments(receipt))) == 'trust_binding_refused'


@pytest.mark.parametrize('field,value', [
    ('program_id', 'sha256:' + '9' * 64), ('circuit_id', 'other.v1'),
    ('policy_id', 'sha256:' + '8' * 64), ('source_revision', '7' * 40),
    ('subject', 'someone-else'),
])
def test_signed_wrong_binding_is_refused(field, value):
    request = example()
    request[field] = value
    response = host().call('ncp_proof_verify', arguments(fixture(request)))
    assert response['status'] == 'refused'


def test_complete_expected_request_not_caller_digest_is_compared():
    receipt = fixture()
    request = deepcopy(receipt['statement'])
    request['public_inputs']['sample'][0] = 9
    assert reason(host().call('ncp_proof_verify', arguments(receipt, request))) == 'receipt_request_mismatch'


def test_owner_isolation_and_reduced_registry_recheck():
    assert host(principal='intruder').call('ncp_proof_verify', arguments())['status'] == 'refused'
    document = registry_document()
    registry = ProofKeyRegistry(document)
    h = host(registry)
    assert h.call('ncp_proof_verify', arguments())['status'] == 'ok'
    document['version'] += 1
    document['entries'][0]['enabled'] = False
    registry.replace(document)
    assert reason(h.call('ncp_proof_verify', arguments())) == 'trust_key_disabled'


def test_registry_revocation_during_verification_cannot_accept(monkeypatch):
    document = registry_document()
    registry = ProofKeyRegistry(document)
    real = profile.verify_bundle
    def verify(*args, **kwargs):
        result = real(*args, **kwargs)
        document['version'] = 2
        document['entries'][0]['enabled'] = False
        registry.replace(document)
        return result
    monkeypatch.setattr(profile, 'verify_bundle', verify)
    assert reason(host(registry).call('ncp_proof_verify', arguments())) == 'trust_snapshot_changed'


@pytest.mark.parametrize('raw', [
    '{"schema":1,"schema":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e99999}',
    '{"x":9007199254740992}', '{"x":1e-99999}', '{"x":"\\ud800"}',
    '[' * 32 + '0' + ']' * 32, 'x' * 50000,
])
def test_raw_duplicate_numeric_unicode_and_complexity_limits_before_crypto(raw, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('malformed input reached signature verification')
    monkeypatch.setattr(profile, 'verify_bundle', forbidden)
    args = arguments()
    args['receipt_json'] = raw
    assert host().call('ncp_proof_verify', args)['status'] == 'refused'


@pytest.mark.parametrize('args', [
    {'request_json': True, 'receipt_json': '{}'}, {}, {'principal': 'other'},
    {'request_json': '{}', 'receipt_json': '{}', 'registry': {}},
])
def test_closed_argument_contract(args):
    assert host().call('ncp_proof_verify', args)['status'] == 'refused'


def test_unknown_operation_and_backend_are_distinct():
    assert reason(host().call('ncp_proof_prove', {})) == 'unknown_proof_operation'
    request = example()
    request['system'] = 'unimplemented-zk-system'
    response = host().call('ncp_proof_verify', arguments(fixture(request)))
    assert response['status'] == 'unsupported'
    assert host().mcp_result('ncp_proof_verify', response)['isError'] is True


def native_receipt():
    request = example()
    request['system'] = 'sp1-device-affine'
    request['program_id'] = profile.digest(PROGRAM)
    wire = b'DBI1' + b'c' * 32 + struct.pack('<qqqQ', 12, 3, -2, 10)
    proof = b'not-an-SP1-proof'
    request['public_inputs'] = {'input_b64': base64.b64encode(wire).decode(),
                                'output_b64': base64.b64encode(compute(wire)).decode(),
                                'proof_digest': profile.digest(proof)}
    receipt = fixture(request)
    receipt['proof_b64'] = base64.b64encode(proof).decode()
    document = registry_document()
    document['entries'][0]['program_id'] = request['program_id']
    return receipt, document


def test_no_native_binary_is_a_hold_never_fake_proof_acceptance():
    receipt, document = native_receipt()
    response = host(ProofKeyRegistry(document)).call('ncp_proof_verify', arguments(receipt))
    assert response['status'] == 'held'
    assert response['data']['signature_verified'] is True
    assert response['data']['native_verification_performed'] is False
    assert response['data']['authorizes_execution'] is False
    assert reason(response) == 'trusted_sp1_runtime_unavailable'


def test_native_proof_transport_budget_is_not_silently_raised():
    receipt, document = native_receipt()
    receipt['proof_b64'] = base64.b64encode(b'x' * (profile.MAX_PROOF_BYTES + 1)).decode()
    assert host(ProofKeyRegistry(document)).call('ncp_proof_verify', arguments(receipt))['status'] == 'refused'


def test_cancellation_cannot_be_translated_into_accepted_signature(monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(profile, 'verify_bundle', interrupted)
    response = host().call('ncp_proof_verify', arguments())
    assert response['status'] == 'held'
    assert reason(response) == 'verification_interrupted'


def test_serialized_unicode_and_duplicated_mcp_envelope_are_bounded():
    args = arguments()
    args['receipt_json'] = '\u2603' * 30000
    assert host().call('ncp_proof_verify', args)['status'] == 'refused'
    response = {'status': 'ok', 'data': {'blob': '\u2603' * 22000}}
    envelope = host().mcp_result('ncp_proof_verify', response)
    assert envelope['isError'] is True
    assert reason(envelope['structuredContent']) == 'proof_response_too_large'
    assert len(canonical(envelope, limits=profile.OUTPUT_LIMITS)) <= 262144


def test_real_mcp_external_process_signature_denial_and_restart(tmp_path):
    pytest.importorskip('mcp')
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    launcher = tmp_path / 'proof_host.py'
    launcher.write_text('import asyncio,json\nfrom ciw.proof_registry import ProofKeyRegistry\n'
        'from ciw.proof_profile import ProofHost\nfrom ciw.knowledge_mcp import serve_stdio\n'
        + 'host=ProofHost(registry=ProofKeyRegistry(json.loads(' + repr(json.dumps(registry_document()))
        + ')),principal="fixture-owner")\nasyncio.run(serve_stdio(host))\n')
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(profile.__file__).resolve().parents[1])
    async def run():
        digests = []
        for _ in range(2):
            params = StdioServerParameters(command=sys.executable, args=[str(launcher)], env=env)
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    assert {t.name for t in (await client.list_tools()).tools} == set(profile.TOOLS)
                    assert (await client.list_resources()).resources == []
                    assert (await client.list_resource_templates()).resource_templates == []
                    response = await client.call_tool('ncp_proof_verify', arguments())
                    assert not response.is_error
                    data = response.structured_content['data']
                    assert data['verification_scope'] == 'ed25519_signature_attribution_only'
                    digests.append(data['receipt_digest'])
                    denied = await client.call_tool('ncp_proof_verify', {'principal': 'attacker'})
                    assert denied.is_error
                    unknown = await client.call_tool('ncp_proof_prove', {})
                    assert unknown.is_error
        assert digests[0] == digests[1]
    asyncio.run(run())


def boundary_probe(tmp_path, script, *, wall=0.3):
    """Custody test only: this subprocess is NOT an SP1 proof verifier."""
    from threading import Lock
    from ciw.adapters.subprocess import _bounded_process
    from ciw.process_policy import ProcessPolicy, executable_sha256
    from ciw.proof_registry import scope
    policy = ProcessPolicy(wall_seconds=wall, max_input_bytes=65536, max_output_bytes=1024,
                           cpu_seconds=2, memory_bytes=256 * 1024 * 1024, file_bytes=1048576,
                           expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'))
    receipt, document = native_receipt()
    wrapper = profile.NativeSP1.__new__(profile.NativeSP1)
    wrapper.scope = scope(receipt['statement'])
    wrapper._cleanup_uncertain = None
    wrapper._lock = Lock()
    class CustodyProbe:
        calls = 0
        def verify(self, raw_input, raw_output, proof):
            self.calls += 1
            _bounded_process([sys.executable, '-c', script, str(tmp_path / 'child.pid')],
                             cwd=tmp_path, timeout=wall, limit=1024, process_policy=policy)
            raise AssertionError('a custody fixture must never be accepted as native proof')
    wrapper._verifier = CustodyProbe()
    h = ProofHost(registry=ProofKeyRegistry(document), principal='fixture-owner', native=wrapper)
    return h, receipt, wrapper


def assert_child_reaped(pid):
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.skipif(sys.platform != 'linux' or not Path('/usr/bin/prlimit').exists(),
                    reason='real Linux pinned-prlimit custody qualification only')
@pytest.mark.parametrize('mode', ['timeout', 'stdout', 'stderr'])
def test_real_native_boundary_timeout_and_pipe_flood_reap_owned_child(tmp_path, mode):
    script = ('import os,sys,time\nfrom pathlib import Path\n'
              'Path(sys.argv[1]).write_text(str(os.getpid()))\n')
    script += ('time.sleep(30)\n' if mode == 'timeout' else
               'while True: os.write(' + ('1' if mode == 'stdout' else '2') + ', b"x"*4096)\n')
    h, receipt, wrapper = boundary_probe(tmp_path, script)
    response = h.call('ncp_proof_verify', arguments(receipt))
    assert response['status'] == 'held', response
    assert response['data']['native_verification_completed'] is False
    assert 'native_verification_performed' not in response['data']
    pid = int((tmp_path / 'child.pid').read_text())
    assert_child_reaped(pid)
    assert wrapper._cleanup_uncertain is None


@pytest.mark.skipif(sys.platform != 'linux' or not Path('/usr/bin/prlimit').exists(),
                    reason='real Linux signal/prlimit custody qualification only')
def test_real_interruption_reaps_child_while_worker_remains_alive(tmp_path):
    import signal
    script = ('import os,sys,time\nfrom pathlib import Path\n'
              'Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(30)\n')
    h, receipt, wrapper = boundary_probe(tmp_path, script, wall=3)
    def cancelled(signum, frame):
        raise KeyboardInterrupt()
    old = signal.signal(signal.SIGALRM, cancelled)
    signal.setitimer(signal.ITIMER_REAL, 0.3)
    try:
        response = h.call('ncp_proof_verify', arguments(receipt))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    assert response['status'] == 'held'
    assert reason(response) == 'verification_interrupted'
    assert_child_reaped(int((tmp_path / 'child.pid').read_text()))
    assert wrapper._cleanup_uncertain is None


def test_cleanup_failure_exception_context_is_sticky_and_blocks_second_native_launch(tmp_path):
    from ciw.adapters.subprocess import RuntimeCleanupUncertain
    h, receipt, wrapper = boundary_probe(tmp_path, 'raise SystemExit(0)')
    class Uncertain:
        calls = 0
        def verify(self, *args):
            self.calls += 1
            try:
                raise RuntimeCleanupUncertain()
            except RuntimeCleanupUncertain as original:
                raise OSError('synthetic cleanup diagnostic with secret-like details') from original
    probe = Uncertain()
    wrapper._verifier = probe
    first = h.call('ncp_proof_verify', arguments(receipt))
    assert first['status'] == 'held'
    assert reason(first) == 'native_cleanup_uncertain'
    assert 'secret-like' not in str(first)
    assert isinstance(wrapper._cleanup_uncertain, RuntimeCleanupUncertain)
    assert isinstance(wrapper._cleanup_uncertain.__cause__, OSError)
    second = h.call('ncp_proof_verify', arguments(receipt))
    assert second['status'] == 'held'
    assert probe.calls == 1


def test_native_output_hash_and_reference_are_checked_before_process(tmp_path):
    h, receipt, wrapper = boundary_probe(tmp_path, 'raise AssertionError("must not launch")')
    receipt['statement']['public_inputs']['output_b64'] = base64.b64encode(b'x' * 60).decode()
    unsigned = fixture(receipt['statement'])
    unsigned['proof_b64'] = receipt['proof_b64']
    response = h.call('ncp_proof_verify', arguments(unsigned))
    assert response['status'] == 'refused'
    assert reason(response) == 'native_statement_mismatch'
    assert wrapper._verifier.calls == 0


def policy_document():
    return {'schema': 'ciw.principal-policy.v1', 'revision': 'proof-fixture-1', 'principals': [
        {'principal': 'fixture-owner', 'enabled': True,
         'operations': [{'name': name, 'constraints': {}} for name in profile.TOOLS],
         'scientific_operations': [], 'resources': [], 'reference_scopes': [], 'handoff_recipients': []}]}


def test_actual_services_principal_grants_revoke_and_no_launch(monkeypatch):
    from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
    from ciw.services import invoke
    policy = policy_document()
    facade = PrincipalPolicy(lambda: deepcopy(policy)).facade('fixture-owner', proof=host())
    data = invoke('ncp_proof_verify', arguments(), host=facade)
    assert data['outcome'] == 'accepted'
    assert facade.list_resources() == facade.resource_templates == []
    real = profile.verify_bundle
    launches = []
    def checked(*args, **kwargs):
        launches.append(1)
        return real(*args, **kwargs)
    monkeypatch.setattr(profile, 'verify_bundle', checked)
    policy['principals'][0]['operations'] = [{'name': 'ncp_proof_describe', 'constraints': {}}]
    assert {v['name'] for v in facade.descriptions()} == {'ncp_proof_describe'}
    assert facade.call('ncp_proof_verify', arguments())['status'] == 'refused'
    with pytest.raises(PrincipalRefusal):
        invoke('ncp_proof_verify', arguments(), host=facade)
    policy['principals'][0]['enabled'] = False
    assert facade.call('ncp_proof_describe', {})['status'] == 'refused'
    assert launches == []


def test_actual_mcp_principal_revocation_and_reopen(tmp_path):
    pytest.importorskip('mcp')
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    policy_path = tmp_path / 'policy.json'
    policy = policy_document()
    policy_path.write_text(json.dumps(policy))
    launcher = tmp_path / 'principal_proof.py'
    launcher.write_text('import asyncio,json\nfrom ciw.proof_registry import ProofKeyRegistry\n'
        'from ciw.proof_profile import ProofHost\nfrom ciw.principal_policy import PrincipalPolicy\n'
        'from ciw.knowledge_mcp import serve_stdio\n'
        + 'h=ProofHost(registry=ProofKeyRegistry(json.loads(' + repr(json.dumps(registry_document()))
        + ')),principal="fixture-owner")\n'
        + 'host=PrincipalPolicy.from_file(' + repr(str(policy_path))
        + ').facade("fixture-owner",proof=h)\nasyncio.run(serve_stdio(host))\n')
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(profile.__file__).resolve().parents[1])
    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(launcher)], env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                assert not (await client.call_tool('ncp_proof_verify', arguments())).is_error
                policy['principals'][0]['operations'] = [{'name': 'ncp_proof_describe', 'constraints': {}}]
                policy['revision'] = 'proof-fixture-2'
                policy_path.write_text(json.dumps(policy))
                assert (await client.call_tool('ncp_proof_verify', arguments())).is_error
                assert {v.name for v in (await client.list_tools()).tools} == {'ncp_proof_describe'}
                assert not (await client.call_tool('ncp_proof_describe', {})).is_error
                assert (await client.list_resources()).resources == []
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                assert (await client.call_tool('ncp_proof_verify', arguments())).is_error
    asyncio.run(run())


def test_native_binding_applies_policy_before_any_runtime_probe(tmp_path, monkeypatch):
    from ciw.process_policy import ProcessPolicy, executable_sha256
    from hashlib import sha256
    policy = ProcessPolicy(max_input_bytes=65536, max_output_bytes=65536,
                           expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'))
    binary = tmp_path / 'synthetic-host.bin'
    guest = tmp_path / 'synthetic-guest.bin'
    binary.write_bytes(b'constructor-only-not-an-executable')
    guest.write_bytes(b'constructor-only-not-an-ELF')
    binding = {'schema': 'ciw.device-boundary-sp1-binding.v1', 'scr_root': str(tmp_path),
               'scr_revision': '2' * 40, 'source_root': '.', 'python': sys.executable,
               'python_sha256': 'a' * 64, 'python_version': '3.12.14', 'dependencies': {},
               'host': str(binary), 'host_sha256': sha256(binary.read_bytes()).hexdigest(),
               'guest': str(guest), 'guest_sha256': sha256(guest.read_bytes()).hexdigest()}
    calls = []
    class ConstructorProbe:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)
            assert kwargs['process_policy'] is policy
            assert kwargs['timeout_seconds'] <= 30
            assert kwargs['max_output_bytes'] <= 65536
    monkeypatch.setattr(profile, 'PinnedSubprocessAdapter', ConstructorProbe)
    native = profile.NativeSP1(binding=binding, process_policy=policy,
        program_id=profile.digest(PROGRAM), circuit_id='device-affine.v1', source_revision='2' * 40,
        policy_id='sha256:' + '3' * 64, guest_sha256=binding['guest_sha256'])
    assert len(calls) == 1
    assert native._verifier.binding == binding
    assert native._cleanup_uncertain is None
    # Reject symlinked native bytes before a second adapter can be created.
    real_guest = tmp_path / 'guest.saved'
    guest.rename(real_guest)
    guest.symlink_to(real_guest)
    with pytest.raises(ValueError, match='symlinked'):
        profile.NativeSP1(binding=binding, process_policy=policy,
            program_id=profile.digest(PROGRAM), circuit_id='device-affine.v1', source_revision='2' * 40,
            policy_id='sha256:' + '3' * 64, guest_sha256=binding['guest_sha256'])
    assert len(calls) == 1


def test_wrong_proof_bytes_refuse_before_missing_native_runtime_hold():
    receipt, document = native_receipt()
    receipt['proof_b64'] = base64.b64encode(b'different-not-a-proof').decode()
    response = host(ProofKeyRegistry(document)).call('ncp_proof_verify', arguments(receipt))
    assert response['status'] == 'refused'
    assert reason(response) == 'native_statement_mismatch'
