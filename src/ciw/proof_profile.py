"""Bounded receipt signature verification, with a distinct existing-SP1 path.

This profile does not prove a computation, admit evidence, authorize execution,
validate physical truth, identify a person, or attest a source checkout by itself.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from threading import Lock
from pathlib import Path
import re

from .adapters.subprocess import PinnedSubprocessAdapter, _guard_cleanup

from .campaign_contracts import ContractRefusal, Limits, canonical, closed_object, mcp_envelope, parse, text
from .core.identities import content_identity, validate_identity
from .legibility import compile_bundle, verify_bundle
from .proof_registry import ProofKeyRegistry, ProofRefusal, hash_id, scope, unbase64

PROFILE = 'odsp.proof-verification.v1'
STATEMENT = 'odsp.proof-statement.v1'
RECEIPT = 'odsp.proof-receipt.v1'
REQUEST_LIMITS = Limits(max_bytes=65536, max_depth=24, max_nodes=16384, max_string=49152)
RECEIPT_LIMITS = Limits(max_bytes=49152, max_depth=20, max_nodes=12288, max_string=32768)
STATEMENT_LIMITS = Limits(max_bytes=8192, max_depth=12, max_nodes=2048, max_string=4096)
OUTPUT_LIMITS = Limits(max_bytes=262144, max_depth=24)
SYSTEMS = ('ed25519-attribution', 'sp1-device-affine')
MAX_PROOF_BYTES = 16384


def digest(raw):
    return 'sha256:' + sha256(raw).hexdigest()


def statement(value):
    canonical(value, limits=STATEMENT_LIMITS)
    closed_object(value, ('schema', 'system', 'execution_id', 'subject', 'program_id',
                          'circuit_id', 'source_revision', 'policy_id', 'public_inputs'))
    if value['schema'] != STATEMENT:
        raise ProofRefusal('statement_schema_refused')
    text(value['system'], 64)
    validate_identity(value['execution_id'], 'execution')
    text(value['subject'], 512)
    scope(value)
    if type(value['public_inputs']) is not dict:
        raise ProofRefusal('public_inputs_refused')
    return value


def receipt_bundle(value):
    """Build the existing Legibility contract; callers cannot supply projections.

    This helper is deterministic serialization only. It is not verification or
    signing. Consumers must use ProofHost with a current host trust registry.
    """
    value = statement(value)
    raw = canonical(value, limits=STATEMENT_LIMITS)
    object_id = 'proof:' + sha256(value['subject'].encode('utf-8')).hexdigest()
    contract = {
        'schema': 'ciw.legibility-contract.v1',
        'object': {'object_id': object_id, 'version': content_identity(value),
                   'label': 'Bounded proof receipt', 'kind': 'attributed-proof-statement'},
        'bindings': {'evidence_id': digest(raw), 'operation_id': 'proof.receipt.verify.v1',
                     'execution_id': value['execution_id'], 'verification_id': None},
        'semantics': {'coordinate_frame': 'receipt-statement', 'properties': [], 'relationships': [],
                      'assumptions': ['Signature is attribution, not proof of computation or physical validity']},
        'claims': [],
        'qualification': {'status': 'unqualified', 'calibration_refs': [], 'verification_refs': [],
                          'canonical_admission': False},
        'vision': {'image_artifact_id': None, 'annotations': []},
        'artifacts': [{'artifact_id': 'statement', 'media_type': 'application/json',
                       'sha256': digest(raw), 'size_bytes': len(raw)}],
    }
    return compile_bundle(contract, {'statement': raw})


def _native_statement(value, proof):
    from .device_boundary import PROGRAM, compute
    public = value['public_inputs']
    closed_object(public, ('input_b64', 'output_b64', 'proof_digest'))
    raw_input = unbase64(public['input_b64'], size=68, maximum=68)
    raw_output = unbase64(public['output_b64'], size=60, maximum=60)
    hash_id(public['proof_digest'])
    if (value['program_id'] != digest(PROGRAM) or digest(proof) != public['proof_digest']
            or compute(raw_input) != raw_output):
        raise ProofRefusal('native_statement_mismatch')
    return raw_input, raw_output


class NativeSP1:
    """Host-only binding to the existing registered SCR affine verifier.

    No arbitrary callback, command, ELF, path or resource policy comes from a
    request. The limits policy must use the existing pinned subprocess boundary.
    No proving or verification-key generation endpoint is exposed by this class.
    """
    def __init__(self, *, binding, process_policy, program_id, circuit_id, source_revision,
                 policy_id, guest_sha256):
        from .device_boundary import PROGRAM
        from .device_boundary_sp1 import ExistingSP1Verifier
        from .process_policy import ProcessPolicy
        if type(process_policy) is not ProcessPolicy:
            raise ProofRefusal('native_process_policy_required')
        if (process_policy.wall_seconds > 30 or process_policy.max_output_bytes > 65536
                or process_policy.max_input_bytes > 65536 or process_policy.cpu_seconds > 30
                or process_policy.memory_bytes > 2 * 1024 * 1024 * 1024
                or process_policy.file_bytes > 16 * 1024 * 1024 or process_policy.max_open_files > 128):
            raise ProofRefusal('native_process_limits_refused')
        self.scope = scope({'program_id': program_id, 'circuit_id': circuit_id,
                            'source_revision': source_revision, 'policy_id': policy_id})
        if (program_id != digest(PROGRAM) or source_revision != binding['scr_revision']
                or guest_sha256 != binding['guest_sha256']):
            raise ProofRefusal('native_binding_mismatch')
        # ExistingSP1Verifier's historical constructor probes before a policy can
        # be attached. Construct its data binding with the same closed checks,
        # then attach the existing adapter with policy BEFORE any subprocess.
        closed_object(binding, ('schema', 'scr_root', 'scr_revision', 'source_root', 'python',
                                'python_sha256', 'python_version', 'dependencies', 'host',
                                'host_sha256', 'guest', 'guest_sha256'))
        if binding['schema'] != 'ciw.device-boundary-sp1-binding.v1':
            raise ProofRefusal('native_binding_mismatch')
        for name in ('scr_root', 'python', 'host', 'guest'):
            if type(binding[name]) is not str or not Path(binding[name]).is_absolute():
                raise ProofRefusal('native_binding_mismatch')
        for name in ('python_sha256', 'host_sha256', 'guest_sha256'):
            if type(binding[name]) is not str or re.fullmatch(r'[a-f0-9]{64}', binding[name]) is None:
                raise ProofRefusal('native_binding_mismatch')
        if binding['source_root'] not in ('.', 'instruments/execution/compute-runtime'):
            raise ProofRefusal('native_binding_mismatch')
        verifier = ExistingSP1Verifier.__new__(ExistingSP1Verifier)
        verifier.binding = parse(canonical(binding))
        verifier._binding_id = digest(canonical(verifier.binding))
        verifier._files()
        verifier.adapter = PinnedSubprocessAdapter(
            binding['scr_root'], binding['scr_revision'], 'execution.proving',
            source_root=binding['source_root'], python_executable=binding['python'],
            timeout_seconds=process_policy.wall_seconds, max_output_bytes=process_policy.max_output_bytes,
            expected_python_sha256=binding['python_sha256'], expected_python_version=binding['python_version'],
            expected_dependencies=binding['dependencies'], process_policy=process_policy)
        self._verifier = verifier
        self._cleanup_uncertain = None
        self._lock = Lock()

    @_guard_cleanup
    def verify(self, value, proof):
        if scope(value) != self.scope:
            raise ProofRefusal('native_binding_mismatch')
        raw_input, raw_output = _native_statement(value, proof)
        if not self._lock.acquire(blocking=False):
            raise ProofRefusal('native_verification_busy')
        try:
            return self._verifier.verify(raw_input, raw_output, proof)
        finally:
            self._lock.release()


def _result(outcome, reason, **data):
    return {'status': 'ok' if outcome == 'accepted' else outcome,
            'data': {'profile': PROFILE, 'outcome': outcome, 'reason': reason,
                     'authorizes_execution': False, 'authorizes_state_admission': False,
                     'physical_validity': 'not_assessed', **data}}


class ProofHost:
    strict_mcp_input = True
    instructions = ('Verify bounded complete receipts against current host-selected trust anchors. '
                    'Ed25519 attribution is separate from native SP1 proof verification. '
                    'No execution, admission, retention, hardware or physical-validity authority.')

    def __init__(self, *, registry, principal, native=None):
        if type(registry) is not ProofKeyRegistry or (native is not None and type(native) is not NativeSP1):
            raise ProofRefusal('proof_host_binding_required')
        text(principal, 512)
        self._bound_principal = principal
        self.registry, self.native = registry, native

    def descriptions(self):
        return deepcopy([{'name': name, **description} for name, description in TOOLS.items()])

    def call(self, name, arguments):
        try:
            canonical(arguments, limits=REQUEST_LIMITS)
            if name == 'ncp_proof_describe':
                closed_object(arguments, ())
                return _result('accepted', 'description_only',
                               verification_performed=False, registry=self.registry.describe(),
                               signature_verification='existing_legibility_ed25519',
                               native_verification=('host_bound_not_qualified' if self.native else
                                                    'held_missing_trusted_sp1_runtime'),
                               unsupported_native=['proved_heat', 'ordinary_f2_checker'],
                               limits={'request_bytes': 65536, 'receipt_bytes': 49152,
                                       'statement_bytes': 8192, 'proof_bytes': MAX_PROOF_BYTES,
                                       'mcp_envelope_bytes': 262144})
            if name != 'ncp_proof_verify':
                raise ProofRefusal('unknown_proof_operation')
            closed_object(arguments, ('request_json', 'receipt_json'))
            request_raw = text(arguments['request_json'], 8192).encode('utf-8')
            receipt_raw = text(arguments['receipt_json'], 49152).encode('utf-8')
            request = statement(parse(request_raw, limits=STATEMENT_LIMITS))
            receipt = parse(receipt_raw, limits=RECEIPT_LIMITS)
            closed_object(receipt, ('schema', 'statement', 'bundle', 'signature'), ('proof_b64',))
            if receipt['schema'] != RECEIPT:
                raise ProofRefusal('receipt_schema_refused')
            supplied = statement(receipt['statement'])
            if canonical(supplied, limits=STATEMENT_LIMITS) != canonical(request, limits=STATEMENT_LIMITS):
                raise ProofRefusal('receipt_request_mismatch')
            if request['subject'] != self._bound_principal:
                raise ProofRefusal('proof_principal_refused')
            if request['system'] not in SYSTEMS:
                return _result('unsupported', 'proof_system_unsupported', verification_performed=False)
            if type(receipt['signature']) is not dict:
                raise ProofRefusal('signature_refused')
            selected = self.registry.resolve(request, receipt['signature'].get('key_id'), self._bound_principal)
            expected = receipt_bundle(request)
            if canonical(receipt['bundle'], limits=RECEIPT_LIMITS) != canonical(expected, limits=RECEIPT_LIMITS):
                raise ProofRefusal('receipt_content_mismatch')
            # Verify the full signed manifest, every projection, and actual bytes.
            checked = verify_bundle(receipt['bundle'], receipt['signature'],
                                    artifacts={'statement': canonical(request, limits=STATEMENT_LIMITS)},
                                    trusted_keys={selected.key_id: selected.public_key},
                                    expected_object_id=expected['source']['object']['object_id'],
                                    expected_version=content_identity(request),
                                    expected_source_digest=expected['manifest']['source_digest'])
            if not (checked['signature_valid'] is True and checked['issuer_trusted'] is True
                    and checked['content_intact'] is True and checked['artifact_status'] == 'verified'
                    and checked['object_matches'] is True and checked['version_current'] is True
                    and checked['source_matches'] is True):
                raise ProofRefusal('receipt_verification_refused')
            native = None
            if request['system'] == 'sp1-device-affine':
                if 'proof_b64' not in receipt:
                    raise ProofRefusal('native_proof_required')
                proof = unbase64(receipt['proof_b64'], maximum=MAX_PROOF_BYTES)
                _native_statement(request, proof)
                if self.native is None:
                    return _result('held', 'trusted_sp1_runtime_unavailable',
                                   signature_verified=True, native_verification_performed=False)
                native = self.native.verify(request, proof)
            elif 'proof_b64' in receipt:
                raise ProofRefusal('unexpected_native_proof')
            self.registry.assert_current(selected)
            data = {'verification_scope': ('existing_registered_sp1_device_affine' if native else
                                           'ed25519_signature_attribution_only'),
                    'signature_verified': True, 'native_verification_performed': native is not None,
                    'request_digest': digest(canonical(request, limits=STATEMENT_LIMITS)),
                    'receipt_digest': digest(canonical(receipt, limits=RECEIPT_LIMITS)),
                    'verification_id': checked['verification_id'], 'execution_id': request['execution_id'],
                    'trust': {'registry_id': selected.registry_id, 'version': selected.version,
                              'digest': selected.digest, 'entry_id': selected.entry_id, 'key_id': selected.key_id},
                    'source_revision': request['source_revision'], 'source_authentication': 'not_established',
                    'freshness': 'caller_statement_and_current_host_trust_snapshot_only'}
            if native is not None:
                data['native_receipt_digest'] = digest(canonical(native, limits=OUTPUT_LIMITS))
            result = _result('accepted', 'verification_succeeded', **data)
            self.mcp_result(name, result)
            return result
        except (KeyboardInterrupt, SystemExit):
            # Native adapter owns process cleanup. An interruption is never success.
            return _result('held', 'verification_interrupted', verification_completed=False)
        except ProofRefusal as exc:
            reason = str(exc)
            return _result('held' if reason in {'native_verification_busy', 'native_cleanup_uncertain'} else 'refused', reason)
        except ContractRefusal as exc:
            return _result('refused', str(exc))
        except Exception as exc:
            from .adapters.subprocess import RuntimeCleanupUncertain, AdapterRefusal
            if isinstance(exc, RuntimeCleanupUncertain):
                return _result('held', 'native_cleanup_uncertain', native_verification_completed=False,
                               native_verification_outcome='unknown')
            if isinstance(exc, AdapterRefusal):
                reason = {'TIMEOUT': 'native_verification_timeout', 'OUTPUT_LIMIT': 'native_output_limit',
                          'PROCESS_POLICY_HELD': 'native_process_policy_unavailable'}.get(
                              exc.code, 'native_runtime_refused')
                return _result('held', reason, native_verification_completed=False)
            if isinstance(exc, (ValueError, TypeError, KeyError, UnicodeError, OSError, RuntimeError)):
                return _result('refused', 'proof_verification_refused')
            raise

    @staticmethod
    def mcp_result(name, value):
        try:
            return mcp_envelope(value, is_error=value.get('status') != 'ok', limits=OUTPUT_LIMITS)
        except (ContractRefusal, TypeError, ValueError):
            return mcp_envelope(_result('refused', 'proof_response_too_large'), is_error=True, limits=OUTPUT_LIMITS)

    def resource_descriptions(self):
        return []

    def resource_templates(self):
        return []

    def read_resource(self, uri):
        raise ProofRefusal('proof_resource_unavailable')


def invoke_proof(host, name, arguments):
    if type(host) is not ProofHost:
        raise ProofRefusal('proof_host_required')
    response = host.call(name, arguments)
    if response['status'] != 'ok':
        raise ProofRefusal(response['data']['reason'])
    return response['data']


_ANNOTATIONS = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}
TOOLS = {
    'ncp_proof_describe': {'description': 'Describe host-bound proof receipt verification and explicit native prerequisites.',
                         'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                         'outputSchema': {'type': 'object'}, 'annotations': deepcopy(_ANNOTATIONS)},
    'ncp_proof_verify': {'description': 'Verify the full supplied receipt against an expected statement and current host-selected keys; never proves, executes, or admits.',
                       'inputSchema': {'type': 'object', 'properties': {
                           'request_json': {'type': 'string', 'minLength': 1, 'maxLength': 8192},
                           'receipt_json': {'type': 'string', 'minLength': 1, 'maxLength': 49152}},
                           'required': ['request_json', 'receipt_json'], 'additionalProperties': False},
                       'outputSchema': {'type': 'object'}, 'annotations': deepcopy(_ANNOTATIONS)},
}


def profile_manifest():
    return {'profile_id': PROFILE, 'service': 'ciw.services.invoke',
            'host_factory': 'ciw.proof_profile.ProofHost',
            'tools': [{'name': name, **deepcopy(value)} for name, value in TOOLS.items()],
            'authorizes_execution': False, 'authorizes_state_admission': False,
            'qualification': 'local_signature_fixture_only_native_sp1_not_qualified'}
