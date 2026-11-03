"""Full-content reproduction receipts, not signatures, grants or source authentication.

The host supplies the identity, release/runtime/comparison-policy bindings and
trusted recomputation callable. This helper never resolves a callable, path or
executable from a submitted receipt. The caller must bound recomputation time and
resources at the existing subprocess/host boundary. It mints no occurrence IDs.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from copy import deepcopy
from hashlib import sha256
import re

from .campaign_contracts import (CANONICAL_FORMAT, ContractRefusal, OUTPUT_LIMITS,
                                 canonical, closed_object, parse)
from .principal_policy import owner_for
from .private_release import validate_identity

SCHEMA = 'odsp.campaign-receipt.v1'
_REF = re.compile(r'sha256:[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}\Z')
_OUTCOMES = frozenset(('returned', 'held', 'refused', 'invalid_contract'))
CLAIMS = {'authorizes_execution':False, 'authorizes_state_admission':False,
          'source_authentication':'not_established',
          'binding_authority':'host_selected_unverified',
          'verification_scope':'full_content_recomputation'}


def _require(value, reason):
    if not value:
        raise ContractRefusal(reason)


def _ref(value):
    _require(type(value) is str and _REF.fullmatch(value) is not None, 'invalid_content_reference')


def _identifier(value):
    _require(type(value) is str and _ID.fullmatch(value) is not None, 'invalid_binding_identifier')


def validate_binding(value):
    canonical(value)
    closed_object(value, {'operation','execution_id','identity','release',
                          'runtime_digest','comparison_policy_digest'})
    _identifier(value['operation'])
    _identifier(value['execution_id'])
    _ref(value['runtime_digest'])
    _ref(value['comparison_policy_digest'])
    identity = value['identity']
    closed_object(identity, {'schema','principal','owner','policy_revision','policy_sha256'})
    _require(identity['schema'] == 'ciw.principal-context.v1', 'invalid_principal_context')
    _identifier(identity['principal'])
    _identifier(identity['policy_revision'])
    _ref(identity['policy_sha256'])
    _require(identity['owner'] == owner_for(identity['principal']), 'principal_owner_mismatch')
    try:
        validate_identity(value['release'])
    except (ValueError, TypeError, KeyError):
        raise ContractRefusal('invalid_release_binding') from None
    return deepcopy(value)


def _digest(value):
    return 'sha256:' + sha256(canonical(value)).hexdigest()


def _content(value):
    return {key:item for key,item in value.items() if key != 'receipt_digest'}


def build_receipt(*, binding, request, outcome, result):
    """Bind exact supplied content and host occurrence identity, without minting one."""
    checked = validate_binding(binding)
    _require(type(request) is dict and type(result) is dict, 'receipt_records_required')
    _require(type(outcome) is str and outcome in _OUTCOMES, 'invalid_receipt_outcome')
    # Reject arbitrary Python objects before deepcopy could invoke user-defined
    # hooks. Raw JSON is already primitive, but trusted SDK calls share this API.
    canonical(request)
    canonical(result)
    receipt = {'schema':SCHEMA,'canonical_format':CANONICAL_FORMAT,'binding':checked,
               'request':deepcopy(request),'request_digest':_digest(request),
               'outcome':outcome,'result':deepcopy(result),'result_digest':_digest(result),
               'claims':dict(CLAIMS)}
    receipt['receipt_digest'] = _digest(receipt)
    canonical(receipt)
    return receipt


def validate_receipt(value):
    """Validate all content and claims; this alone does not establish reproduction."""
    canonical(value)
    closed_object(value, {'schema','canonical_format','binding','request','request_digest',
                          'outcome','result','result_digest','claims','receipt_digest'})
    _require(value['schema'] == SCHEMA and value['canonical_format'] == CANONICAL_FORMAT,
             'unsupported_receipt_contract')
    validate_binding(value['binding'])
    _require(type(value['request']) is dict and type(value['result']) is dict,
             'receipt_records_required')
    _require(type(value['outcome']) is str and value['outcome'] in _OUTCOMES,
             'invalid_receipt_outcome')
    closed_object(value['claims'], CLAIMS)
    _require(canonical(value['claims']) == canonical(CLAIMS), 'receipt_authority_claim_refused')
    for field in ('request_digest','result_digest','receipt_digest'):
        _ref(value[field])
    _require(value['request_digest'] == _digest(value['request']), 'request_content_mismatch')
    _require(value['result_digest'] == _digest(value['result']), 'result_content_mismatch')
    _require(value['receipt_digest'] == _digest(_content(value)), 'receipt_content_mismatch')
    return deepcopy(value)


def verify_receipt(raw, *, expected_binding, expected_request, recompute):
    """Recompute and compare complete canonical content, never just submitted IDs.

The callable returns exactly ``{'outcome': ..., 'result': {...}}``. Exceptions,
timeouts, cancellation and cleanup uncertainty propagate: none mean mismatch or
successful verification, and this function never retries a recomputation.
"""
    receipt = validate_receipt(parse(raw, limits=OUTPUT_LIMITS))
    binding = validate_binding(expected_binding)
    _require(type(expected_request) is dict, 'receipt_records_required')
    _require(canonical(receipt['binding']) == canonical(binding), 'receipt_binding_mismatch')
    _require(canonical(receipt['request']) == canonical(expected_request), 'receipt_request_mismatch')
    _require(callable(recompute), 'host_recompute_required')
    # Freeze the expected request before the host function can mutate its copy.
    request = deepcopy(expected_request)
    observed = recompute(deepcopy(request))
    canonical(observed)
    closed_object(observed, {'outcome','result'})
    expected = build_receipt(binding=binding, request=request,
                             outcome=observed['outcome'], result=observed['result'])
    _require(canonical(receipt) == canonical(expected), 'receipt_reproduction_mismatch')
    return {'schema':'odsp.campaign-receipt-verification.v1','matched':True,
            'receipt_digest':receipt['receipt_digest'], 'execution_id':binding['execution_id'],
            'canonical_format':CANONICAL_FORMAT, 'claims':dict(CLAIMS)}
