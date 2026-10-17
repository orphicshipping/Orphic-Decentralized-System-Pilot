"""Real cryptography on synthetic identities/data; no production login ceremony."""
from __future__ import annotations
import base64
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ciw import device_boundary as b
from ciw.core.identities import content_identity
from ciw.network_example import fixture,access_token,enroll
from ciw.network_workflow import NetworkHost,OPS,cert,register_schemas
from ciw.network_trust import signed_document,verify_document,verify_certificate,public_bytes,AccountVerifier
from ciw.network_data import prepare_contribution,review_release
from ciw.session import Session

NOW=1791244800

@pytest.fixture
def f(tmp_path):return fixture(tmp_path,now=NOW)

def payload(response):
    assert response['type']=='response',response
    return response['payload']

def contribute(f,**changes):
    args={'source_b64':base64.b64encode(f['source']).decode(),'consent':f['consent'],'recipient':'notation-research','purpose':'research'}
    args.update(changes)
    token=access_token(f,'data.contribution.prepare',args['consent'])
    return payload(f['host'].request('contribute',token,args))

def altered_consent(f,**changes):
    value=deepcopy(f['consent']['payload']);value.update(changes)
    return signed_document('consent','tenant-demo',value,f['keys']['consent'])

def test_enrollment_three_identities_and_no_token_retention(f,tmp_path):
    challenge=f['host'].enrollment_challenge(f['keys']['device'].public_key())
    proof=signed_document('enrollment','gauge-demo',challenge['payload'],f['keys']['device'])
    token=access_token(f,'device.enroll',challenge)
    response=payload(f['host'].request('enroll',token,{'challenge':challenge,'proof':proof}))
    assert response['status']=='completed',response
    result=response['result']['data']['result']
    assert result['account_id']=='human-demo'
    assert result['device_identity']['subject']=='gauge-demo'
    assert result['device_identity']['kind']=='device'
    assert result['hardware_attestation']=='not_established'
    assert public_bytes(cert(result['certificate']).public_key())==public_bytes(f['keys']['device'].public_key())
    assert public_bytes(f['node'].public_key())!=public_bytes(f['keys']['device'].public_key())
    path=f['session'].save_workspace(tmp_path/'workspace.json')
    assert token not in path.read_text()
    assert response['result']['verification_status']=='not_verified'
    assert f['host'].verify_device_packet(f['packet'])['account_token_required'] is False


def test_enrollment_replay_is_denied(f):
    challenge=f['host'].enrollment_challenge(f['keys']['device'].public_key())
    proof=signed_document('enrollment','gauge-demo',challenge['payload'],f['keys']['device'])
    token=access_token(f,'device.enroll',challenge);args={'challenge':challenge,'proof':proof}
    assert payload(f['host'].request('enroll',token,args))['status']=='completed'
    assert payload(f['host'].request('enroll',token,args))['status']=='refused'


@pytest.mark.parametrize('change',[
    {'org':'other-tenant'},{'device':'other-device'},{'aud':'urn:notation:node:other'},
    {'iss':'https://attacker.invalid'},{'token_use':'id'},{'roles':['auditor']},
    {'acr':'self-asserted-mfa'},{'auth_time':NOW-301},{'exp':NOW},{'iat':NOW+1},
    {'nbf':NOW+1},{'exp':NOW+901},{'scope':'device.read'},{'resource_id':'wrong'},
    {'roles':['administrator','administrator']},{'scope':'device.enroll device.enroll'},
    {'iat':True},{'sub':False},
])
def test_access_token_restrictions(f,change):
    challenge=f['host'].enrollment_challenge(f['keys']['device'].public_key())
    token=access_token(f,'device.enroll',challenge,**change)
    with pytest.raises((ValueError,TypeError)):
        f['accounts'].verify(token,device_id='gauge-demo',scope='device.enroll',now=NOW,resource_id=content_identity(challenge))


def test_algorithm_none_and_wrong_signature_rejected(f):
    token=access_token(f,'device.read',{})
    claims=jwt.decode(token,options={'verify_signature':False})
    for bad in (jwt.encode(claims,'',algorithm='none',headers={'typ':'at+jwt','kid':'idp-demo'}),
                jwt.encode(claims,Ed25519PrivateKey.generate(),algorithm='EdDSA',headers={'typ':'at+jwt','kid':'idp-demo'})):
        with pytest.raises(ValueError):f['accounts'].verify(bad,device_id='gauge-demo',scope='device.read',now=NOW)


def test_duplicate_jwt_keys_rejected_before_crypto(f):
    token=access_token(f,'device.read',{})
    h,p,s=token.split('.')
    decoded=base64.urlsafe_b64decode(p+'='*(-len(p)%4))
    duplicate=decoded[:-1]+b',"sub":"other"}'
    bad=h+'.'+base64.urlsafe_b64encode(duplicate).decode().rstrip('=')+'.'+s
    with pytest.raises(ValueError):f['accounts'].verify(bad,device_id='gauge-demo',scope='device.read',now=NOW)


def test_human_revocation_does_not_rekey_device(f):
    assert payload(enroll(f))['status']=='completed'
    cfg=f['accounts']
    f['host'].accounts=AccountVerifier(issuer=cfg.issuer,node_id=cfg.node_id,tenant_id=cfg.tenant_id,keys=cfg.keys,
        role_scopes=cfg.role_scopes,step_up_acrs=tuple(cfg.step_up_acrs),snapshot_expires=NOW+3600,revoked_subjects=['human-demo'])
    assert contribute(f)['status']=='refused'
    assert f['host'].verify_device_packet(f['packet'])['account_token_required'] is False


def test_wrong_device_key_possession_and_roles_refuse(f):
    challenge=f['host'].enrollment_challenge(f['keys']['device'].public_key())
    proof=signed_document('enrollment','gauge-demo',challenge['payload'],Ed25519PrivateKey.generate())
    token=access_token(f,'device.enroll',challenge)
    response=payload(f['host'].request('enroll',token,{'challenge':challenge,'proof':proof}))
    assert response['status']=='refused'
    with pytest.raises(ValueError):f['host'].verify_device_packet(f['packet'])
    with pytest.raises(ValueError):f['host'].enrollment_challenge(f['keys']['node'].public_key())


def test_expired_human_token_not_installed_as_device_key(f):
    challenge=f['host'].enrollment_challenge(f['keys']['device'].public_key())
    proof=signed_document('enrollment','gauge-demo',challenge['payload'],f['keys']['device'])
    token=access_token(f,'device.enroll',challenge,exp=NOW+1)
    times=iter([NOW,NOW,NOW+1]);f['host']._clock=lambda:next(times)
    response=payload(f['host'].request('enroll',token,{'challenge':challenge,'proof':proof}))
    assert response['status']=='refused'
    assert f['host']._audit['device_certificate'] is None


def test_certificate_roles_tenants_and_revocation(f):
    result=payload(enroll(f))['result']['data']['result'];leaf=cert(result['certificate'])
    args=dict(ca_certificate=f['ca'],kind='device',subject='gauge-demo',tenant='tenant-demo',now=NOW)
    assert verify_certificate(leaf,**args)['kind']=='device'
    for changed in ({'kind':'node'},{'tenant':'other'},{'subject':'other'},{'now':NOW+31*86400},{'revoked_serials':{leaf.serial_number}}):
        with pytest.raises(ValueError):verify_certificate(leaf,**{**args,**changed})


def test_contribution_filters_private_identifiers_and_preserves_joint_uncertainty(f):
    assert payload(enroll(f))['status']=='completed'
    before=f['source'];r=contribute(f)
    assert r['status']=='completed',r
    result=r['result']['data']['result'];exported=result['export']['payload']
    assert result['transmitted'] is False and result['training_admission'] is False
    assert exported['value']==b.rational(Fraction(-18,10**6))
    assert exported['variance_m2']==b.rational(Fraction(11,10**12))
    assert set(exported['conditions'])=={'temperature_K'}
    raw=json.dumps(result['export'])
    for sensitive in ('CONFIDENTIAL-PART','PRIVATE-FACILITY','PRIVATE-RECIPE','private_context',b.raw_id(before)):
        assert sensitive not in raw
    assert result['local_lineage']['evidence_id']==b.raw_id(before)
    assert f['source']==before
    second=contribute(f)
    assert second['result']['data']['result']['export']['payload']['source_group']==exported['source_group']


@pytest.mark.parametrize('changes',[
    {'output_class':'private'}, {'recipients':['different']},{'purposes':['benchmarking']},
    {'expires':NOW},{'evidence_id':b.raw_id(b'other')},{'tenant_id':'other'},
    {'source_fields':['private_context']},{'output_conditions':['facility']},
    {'transform_id':'arbitrary.python.v1'}, {'output_class':'anonymized'},
])
def test_consent_deny_cases_keep_source_local(f,changes):
    assert payload(enroll(f))['status']=='completed'
    before=f['source'];result=contribute(f,consent=altered_consent(f,**changes))
    assert result['status']=='refused' and result['result'] is None
    assert before==f['source']


def test_scope_alone_does_not_override_consent_signature(f):
    assert payload(enroll(f))['status']=='completed'
    bad=signed_document('consent','tenant-demo',f['consent']['payload'],f['keys']['idp'])
    assert contribute(f,consent=bad)['status']=='refused'


def test_model_training_requires_explicit_right_and_is_still_not_admitted(f):
    assert payload(enroll(f))['status']=='completed'
    c=altered_consent(f,purposes=['model_training'])
    assert contribute(f,consent=c,purpose='model_training')['status']=='refused'
    c=altered_consent(f,purposes=['model_training'],allow_cross_tenant_training=True)
    r=contribute(f,consent=c,purpose='model_training')
    assert r['status']=='completed'
    assert r['result']['data']['result']['export']['payload']['training_admission'] is False


def test_covariance_unknown_is_not_zero_and_no_independence_assumption(f):
    assert payload(enroll(f))['status']=='completed'
    source=b.parse(f['source']);source['joint_covariance_m2']=None;f['source']=b.encode(source)
    c=altered_consent(f,evidence_id=b.raw_id(f['source']))
    r=contribute(f,consent=c)
    assert r['status']=='completed'
    assert r['result']['data']['result']['export']['payload']['variance_m2'] is None


def test_covariance_inconsistent_refuses_instead_of_repairing(f):
    assert payload(enroll(f))['status']=='completed'
    source=b.parse(f['source']);source['joint_covariance_m2'][0][1]=b.rational(5);f['source']=b.encode(source)
    c=altered_consent(f,evidence_id=b.raw_id(f['source']))
    assert contribute(f,consent=c)['status']=='refused'


def test_consent_retraction_stops_future_preparation_not_history(f):
    assert payload(enroll(f))['status']=='completed'
    first=contribute(f);assert first['status']=='completed'
    rev=signed_document('revocation','tenant-demo',{'schema':'ciw.network-consent-revocation.v1',
        'consent_id':'consent-demo','tenant_id':'tenant-demo','issued_at':NOW,'reason':'customer_request'},f['keys']['consent'])
    token=access_token(f,'data.consent.revoke',rev)
    r=payload(f['host'].request('revoke',token,{'revocation':rev}))
    assert r['status']=='completed',r
    assert r['result']['data']['result']['trained_models_unlearned'] is False
    assert contribute(f)['status']=='refused'
    assert first['result']['result_id'] in f['session'].results


def release(f,version=1,kind='model',**changes):
    raw=b'opaque synthetic release, never executable'
    value={'schema':'ciw.network-release.v1','release_id':'release-demo','kind':kind,'version':version,
        'device_family':'gauge-fixture','runtime_profile':'opaque-reference-v1','artifact_id':b.raw_id(raw),
        'size_bytes':len(raw),'issued_at':NOW,'expires':NOW+300,'validation_record_id':None}
    value.update(changes)
    manifest=signed_document('release','notation-publisher',value,f['keys']['publisher'])
    token=access_token(f,'release.review',manifest)
    return payload(f['host'].request('release',token,{'artifact_b64':base64.b64encode(raw).decode(),'manifest':manifest}))


@pytest.mark.parametrize('kind',['model','firmware','gateware'])
def test_signed_release_review_does_not_install_execute_or_qualify(f,kind):
    r=release(f,kind=kind);assert r['status']=='completed',r
    d=r['result']['data']['result']
    assert d['installed'] is False and d['install_authorized'] is False
    assert d['model_executed'] is False and d['empirical_validation']=='not_established'
    assert release(f,version=3,kind=kind)['status']=='completed'
    assert release(f,version=2,kind=kind)['status']=='refused'


@pytest.mark.parametrize('changes',[{'device_family':'other'},{'runtime_profile':'other'},
    {'artifact_id':b.raw_id(b'changed')},{'size_bytes':True},{'expires':NOW}])
def test_release_invalid_binding_refuses(f,changes):assert release(f,**changes)['status']=='refused'


def test_reopen_offline_restores_no_token_key_or_capability(f,tmp_path):
    assert payload(enroll(f))['status']=='completed'
    assert contribute(f)['status']=='completed'
    assert release(f)['status']=='completed'
    path=f['session'].save_workspace(tmp_path/'workspace.json')
    with patch('socket.socket',side_effect=AssertionError('offline means no networking')):
        reopened=Session.from_workspace(path,tmp_path/'reopened')
    assert reopened.results==f['session'].results
    r=reopened.handle({'protocol_version':1,'request_id':'stale-grant','type':'operation.execute',
        'payload':{'operation_id':OPS['enroll'],'parameters':{'request_id':'replay','inputs':{}}}})
    assert r['payload']['status']=='refused'
    assert r['payload']['execution']['refusal']['code']=='operation_unavailable'


def test_direct_session_execution_without_ephemeral_token_is_denied(f):
    r=f['session'].handle({'protocol_version':1,'request_id':'no-token','type':'operation.execute',
        'payload':{'operation_id':OPS['enroll'],'parameters':{'request_id':'raw','inputs':{}}}})
    assert r['payload']['status']=='refused'


def test_unknown_sensitive_parameter_not_retained(f,tmp_path):
    token=access_token(f,'device.read',{})
    r=payload(f['host'].request('release',token,{'access_token':token}))
    assert r['status']=='refused'
    path=f['session'].save_workspace(tmp_path/'failed.json')
    assert token not in path.read_text()


@pytest.mark.parametrize('role',['consent','publisher','ca','idp'])
def test_instrument_key_cannot_be_a_different_signing_authority(f,role):
    with pytest.raises(ValueError,match='independent'):
        f['host'].enrollment_challenge(f['keys'][role].public_key())


def test_sp1_required_contribution_never_falls_back_to_reconstruction(f):
    assert payload(enroll(f))['status']=='completed'
    result=contribute(f,consent=altered_consent(f,proof_policy='sp1_required'))
    assert result['status']=='refused' and 'SP1' in result['execution']['refusal']['message']


def test_same_sample_group_different_derivative_identity(f):
    assert payload(enroll(f))['status']=='completed'
    first=contribute(f)['result']['data']['result']['export']['payload']
    source=b.parse(f['source']);source['prediction']['value']=b.rational(Fraction(26,1000))
    f['source']=b.encode(source)
    changed=altered_consent(f,evidence_id=b.raw_id(f['source']))
    second=contribute(f,consent=changed)['result']['data']['result']['export']['payload']
    assert first['source_group']==second['source_group']
    assert first['contribution_id']!=second['contribution_id']
    other=altered_consent(f,evidence_id=b.raw_id(f['source']),recipients=['another-consortium'])
    third=contribute(f,consent=other,recipient='another-consortium')['result']['data']['result']['export']['payload']
    assert third['source_group']!=second['source_group']


def test_failed_session_retention_does_not_activate_enrollment(f):
    challenge=f['host'].enrollment_challenge(f['keys']['device'].public_key())
    proof=signed_document('enrollment','gauge-demo',challenge['payload'],f['keys']['device'])
    token=access_token(f,'device.enroll',challenge)
    with patch.object(f['session'],'handle',side_effect=OSError('simulated disk failure')):
        with pytest.raises(OSError):f['host'].request('enroll',token,{'challenge':challenge,'proof':proof})
    with pytest.raises(ValueError,match='not enrolled'):f['host'].verify_device_packet(f['packet'])


def test_cli_local_journey_and_reopen(tmp_path,capsys):
    from ciw.network_cli import main
    target=tmp_path/'network-demo'
    assert main(['demo','--output',str(target)])==0
    summary=json.loads(capsys.readouterr().out)
    assert summary['transmitted'] is False and summary['actual_login_ceremony'] is False
    assert main(['inspect','--workspace',str(target/'workspace.json'),
                 '--workspace-sha256',summary['workspace_sha256']])==0
    inspected=json.loads(capsys.readouterr().out)
    assert inspected['active_grants_restored'] is False
    assert main(['demo','--output',str(target)])==2
