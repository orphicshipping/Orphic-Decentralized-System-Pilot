"""Complete binding and recomputation tests, including resealed forgeries."""
from copy import deepcopy
from hashlib import sha256

import pytest

from ciw.campaign_contracts import ContractRefusal, canonical
from ciw.campaign_receipts import build_receipt, validate_receipt, verify_receipt
from ciw.principal_policy import owner_for
from ciw.private_release import IDENTITY_SCHEMA


def binding():
    return {'operation':'ncp_campaign_measure', 'execution_id':'occurrence-001',
        'identity':{'schema':'ciw.principal-context.v1','principal':'fixture-owner',
                    'owner':owner_for('fixture-owner'),'policy_revision':'fixture-1',
                    'policy_sha256':'sha256:' + 'a' * 64},
        'release':{'schema':IDENTITY_SCHEMA,
            'repository':'orphicsystems/Orphic-Decentralized-System-Protocol',
            'source_commit':'a'*40,'source_tree':'b'*40,'wheel_sha256':'c'*64,
            'package_digest':'d'*64},
        'runtime_digest':'sha256:' + 'e'*64,
        'comparison_policy_digest':'sha256:' + 'f'*64}


REQUEST = {'input': [1, 2, 3], 'context':'inert question'}
RESULT = {'sum': 6, 'scope':'synthetic fixture', 'authorizes_execution':False}


def receipt(outcome='returned'):
    return build_receipt(binding=binding(), request=REQUEST, outcome=outcome, result=RESULT)


def verify(value, **kwargs):
    return verify_receipt(canonical(value), expected_binding=binding(),
        expected_request=REQUEST,
        recompute=kwargs.get('recompute', lambda request: {'outcome':'returned','result':RESULT}))


def reseal(value):
    for name in ('request', 'result'):
        value[name + '_digest'] = 'sha256:' + sha256(canonical(value[name])).hexdigest()
    value['receipt_digest'] = 'sha256:' + sha256(canonical(
        {k:v for k,v in value.items() if k != 'receipt_digest'})).hexdigest()
    return value


def test_exact_replay_keeps_occurrence_identity_and_is_non_authorizing():
    calls = []
    def recompute(request):
        calls.append(request)
        return {'outcome':'returned','result':RESULT}
    raw = canonical(receipt())
    a = verify(receipt(), recompute=recompute)
    b = verify(receipt(), recompute=recompute)
    assert a == b and a['matched'] is True and a['execution_id'] == 'occurrence-001'
    assert canonical(receipt()) == raw
    assert len(calls) == 2
    assert a['claims']['authorizes_execution'] is False
    assert a['claims']['source_authentication'] == 'not_established'
    assert a['claims']['binding_authority'] == 'host_selected_unverified'


@pytest.mark.parametrize('resealed', [False, True])
def test_forged_full_result_refuses_even_when_attacker_recomputes_all_hashes(resealed):
    value = receipt()
    value['result']['sum'] = 99
    if resealed:
        reseal(value)
    calls = []
    with pytest.raises(ContractRefusal, match='result_content_mismatch|receipt_reproduction_mismatch'):
        verify(value, recompute=lambda request: calls.append(request) or {'outcome':'returned','result':RESULT})
    assert len(calls) == int(resealed)


@pytest.mark.parametrize('part,key,value', [
    ('binding','operation','ncp_other'), ('binding','execution_id','different-occurrence'),
    ('binding','runtime_digest','sha256:'+'0'*64),
    ('binding','comparison_policy_digest','sha256:'+'0'*64),
    ('identity','policy_revision','revoked-policy'),
    ('identity','policy_sha256','sha256:'+'0'*64),
    ('identity','principal','another-owner'), ('identity','owner',owner_for('another-owner')),
    ('release','repository','other/repository'), ('release','source_commit','0'*40),
    ('release','source_tree','0'*40), ('release','wheel_sha256','0'*64),
    ('release','package_digest','0'*64),
])
def test_all_host_binding_fields_checked_before_recomputation(part, key, value):
    forged = receipt()
    target = forged['binding'] if part == 'binding' else forged['binding'][part]
    target[key] = value
    reseal(forged)
    def forbidden(_):
        raise AssertionError('binding refusal must precede execution')
    with pytest.raises(ContractRefusal):
        verify(forged, recompute=forbidden)


def test_request_context_forgery_and_extra_verification_flags_refuse_before_call():
    forged = receipt()
    forged['request']['context'] = 'different question'
    reseal(forged)
    def forbidden(_):
        raise AssertionError('must refuse before execution')
    with pytest.raises(ContractRefusal, match='receipt_request_mismatch'):
        verify(forged, recompute=forbidden)
    forged = receipt()
    forged['verified'] = True
    reseal(forged)
    with pytest.raises(ContractRefusal, match='invalid_object_fields'):
        verify(forged, recompute=forbidden)


@pytest.mark.parametrize('value', [True, 0, 'false'])
def test_authority_claim_must_be_the_exact_boolean_false(value):
    forged = receipt()
    forged['claims']['authorizes_execution'] = value
    reseal(forged)
    with pytest.raises(ContractRefusal, match='receipt_authority_claim_refused'):
        verify(forged)


def test_raw_duplicate_key_cannot_hide_a_forged_record():
    raw = canonical(receipt())
    raw = b'{"schema":"forged",' + raw[1:]
    with pytest.raises(ContractRefusal, match='duplicate_json_key'):
        verify_receipt(raw, expected_binding=binding(), expected_request=REQUEST,
                       recompute=lambda _: pytest.fail('no launch'))


@pytest.mark.parametrize('outcome', ['returned', 'held', 'refused', 'invalid_contract'])
def test_outcome_is_bound_and_preserved(outcome):
    value = receipt(outcome)
    assert validate_receipt(value)['outcome'] == outcome
    assert verify(value, recompute=lambda _: {'outcome':outcome,'result':RESULT})['matched']
    if outcome != 'returned':
        with pytest.raises(ContractRefusal, match='receipt_reproduction_mismatch'):
            verify(value)


def test_recompute_receives_a_copy_and_no_callback_retry():
    expected = deepcopy(REQUEST)
    def mutating(request):
        request['context'] = 'only the private callback copy changed'
        return {'outcome':'returned','result':RESULT}
    assert verify(receipt(), recompute=mutating)['matched']
    assert REQUEST == expected
    for exception in (KeyboardInterrupt('cancelled'), SystemExit(4), OSError('cleanup failed')):
        calls = []
        def failing(request):
            calls.append(request)
            raise exception
        with pytest.raises(type(exception)) as caught:
            verify(receipt(), recompute=failing)
        assert caught.value is exception and len(calls) == 1


@pytest.mark.parametrize('observed', [None, {'result':RESULT},
    {'outcome':'success','result':RESULT}, {'outcome':'returned','result':[]},
    {'outcome':'returned','result':RESULT,'authorizes_execution':True}])
def test_invalid_recompute_output_never_becomes_success(observed):
    with pytest.raises(ContractRefusal):
        verify(receipt(), recompute=lambda _: observed)


def test_receipt_byte_and_depth_limits_apply_before_callback():
    forged = receipt()
    forged['result'] = {'oversized':'x' * 262144}
    with pytest.raises(ContractRefusal):
        build_receipt(binding=binding(), request=REQUEST, outcome='returned', result=forged['result'])
    with pytest.raises(ContractRefusal, match='host_recompute_required'):
        verify(receipt(), recompute='python -c untrusted')


def test_equal_python_numbers_are_not_equal_canonical_receipt_content():
    forged = receipt()
    forged['result']['sum'] = 6.0
    assert forged['result'] == RESULT
    reseal(forged)
    with pytest.raises(ContractRefusal, match='receipt_reproduction_mismatch'):
        verify(forged)


def test_unsupported_sdk_objects_are_refused_before_copy_hooks_can_execute():
    class Unsafe:
        def __deepcopy__(self, memo):
            pytest.fail('unsupported request objects must not execute copy hooks')
    for request, result in (({'value':Unsafe()}, RESULT), (REQUEST, {'value':Unsafe()})):
        with pytest.raises(ContractRefusal, match='json_type_refused'):
            build_receipt(binding=binding(), request=request, outcome='returned', result=result)
