"""Synthetic fixture generator. No real login, customer data or private-key export."""
from __future__ import annotations
import base64
from datetime import datetime,timezone
from fractions import Fraction
from pathlib import Path
import secrets
import struct
import time

from . import device_boundary as b
from .core.identities import content_identity,new_identity
from .network_trust import public_bytes,issue_certificate,signed_document,AccountVerifier,SCOPES
from .network_workflow import NetworkHost
from .instruments import make_demo_run
from .session import Session


def fixture(output:Path,*,now=None):
    import jwt
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.x509.oid import NameOID
    now=int(time.time()) if now is None else now
    keys={role:Ed25519PrivateKey.generate() for role in ('idp','ca','node','device','consent','publisher')}
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'SYNTHETIC-NOTATION-CA')])
    ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(keys['ca'].public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.fromtimestamp(now-86400,timezone.utc))
        .not_valid_after(datetime.fromtimestamp(now+366*86400,timezone.utc))
        .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
        .add_extension(x509.KeyUsage(True,False,False,False,False,True,True,False,False),critical=True)
        .sign(keys['ca'],algorithm=None))
    node=issue_certificate(kind='node',subject='node-demo',tenant='tenant-demo',public_key=keys['node'].public_key(),ca_certificate=ca,ca_key=keys['ca'],now=now)
    accounts=AccountVerifier(issuer='https://accounts.notation.invalid',node_id='node-demo',tenant_id='tenant-demo',
        keys={'idp-demo':keys['idp'].public_key()},role_scopes={'administrator':set(SCOPES),'auditor':{'device.read'}},
        step_up_acrs=('urn:notation:test:passkey-uv',),snapshot_expires=now+3600)
    config={'schema':b.TRANSFORM,'calibration_id':'synthetic-calibration','gain':1000,'offset':0,'denominator':1,
        'valid_from_ns':(now-86400)*10**9,'valid_until_ns':(now+86400)*10**9,'count_min':0,'count_max':100000,
        'uncertainty_bound_nm':None,'uncertainty_basis':'unknown'}
    raw=struct.pack('<q',24982)
    payload={'schema':b.PAYLOAD,'device_id':'gauge-demo','boot_id':'boot-demo','sequence':1,'sample_id':'sample-demo',
        'execution_id':new_identity('execution'),'frame_id':'gauge-axis','clock_id':'declared-fixture-clock',
        'captured_at_ns':now*10**9,'time_uncertainty_ns':100,'firmware_id':b.raw_id(b'synthetic firmware'),
        'gateware_id':None,'raw_b64':base64.b64encode(raw).decode(),'raw_id':b.raw_id(raw),'transform':config,
        'device_state':{'quantity':'length','unit':'mm','value':b.rational(Fraction(12491,500)),'frame_id':'gauge-axis'},
        'origin':'synthetic','filter_id':b.FILTER_D}
    packet=b.signed_packet(payload,keys['device'])
    source={'schema':'ciw.network-residual-source.v1','tenant_id':'tenant-demo','device_id':'gauge-demo',
        'observation':packet,'prediction':{'model_id':b.raw_id(b'public synthetic model'),'quantity':'length','frame_id':'gauge-axis',
            'unit':'m','value':b.rational(Fraction(25,1000))},
        'joint_covariance_m2':[[b.rational(Fraction(4,10**12)),b.rational(Fraction(1,10**12))],
                              [b.rational(Fraction(1,10**12)),b.rational(Fraction(9,10**12))]],
        'conditions':{'temperature_K':b.rational(300),'pressure_Pa':b.rational(101325)},
        'private_context':{'part_id':'CONFIDENTIAL-PART-17','facility':'PRIVATE-FACILITY','recipe':'PRIVATE-RECIPE'}}
    source_raw=b.encode(source)
    consent=signed_document('consent','tenant-demo',{
        'schema':'ciw.network-consent.v1','consent_id':'consent-demo','tenant_id':'tenant-demo','device_id':'gauge-demo',
        'evidence_id':b.raw_id(source_raw),'recipients':['notation-research'],'purposes':['research'],
        'source_fields':['observed_length','predicted_length','model_id','joint_covariance_m2','metrology_summary'],
        'output_conditions':['temperature_K'],'output_class':'pseudonymized','transform_id':'notation.length-residual.v1',
        'not_before':now-1,'expires':now+600,'retain_until':now+86400,'terms_id':b.raw_id(b'synthetic consent terms'),
        'allow_cross_tenant_training':False,'proof_policy':'independent_reconstruction'},keys['consent'])
    observation_policy={'schema':'ciw.device-boundary-policy.v1','epoch':'network-fixture-1',
        'device_id':'gauge-demo','boot_id':'boot-demo','device_public_key_b64':base64.b64encode(public_bytes(keys['device'].public_key())).decode(),
        'firmware_id':payload['firmware_id'],'gateware_id':None,'frame_id':payload['frame_id'],'clock_id':payload['clock_id'],
        'approved_transform_ids':[content_identity(config)],'allowed_functions':['record_raw','record_calibrated'],
        'require_sp1_functions':[],'valid_from_ns':(now-86400)*10**9,'valid_until_ns':(now+86400)*10**9,
        'max_sample_age_ns':86400*10**9,'max_time_uncertainty_ns':1000,'specification_nm':None,'procedure_id':None}
    session=Session(make_demo_run(),output/'session')
    host=NetworkHost(session,accounts=accounts,device_id='gauge-demo',node_certificate=node,node_key=keys['node'],
        ca_certificate=ca,ca_key=keys['ca'],consent_key=public_bytes(keys['consent'].public_key()),
        publisher_key=public_bytes(keys['publisher'].public_key()),publisher='notation-publisher',device_family='gauge-fixture',
        runtime_profile='opaque-reference-v1',approved_transforms={content_identity(config)},lineage_key=secrets.token_bytes(32),
        device_revocation_expires=now+30*86400,observation_policy=observation_policy,clock=lambda:now)
    return {'now':now,'keys':keys,'ca':ca,'node':node,'accounts':accounts,'packet':packet,'source':source_raw,
            'consent':consent,'session':session,'host':host}


def access_token(f,requested_scope,resource,**changes):
    import jwt
    now=f['now']
    claims={'iss':'https://accounts.notation.invalid','sub':'human-demo','aud':'urn:notation:node:node-demo',
        'iat':now,'nbf':now,'exp':now+900,'auth_time':now,'jti':secrets.token_hex(16),
        'acr':'urn:notation:test:passkey-uv','amr':['test-only'],'org':'tenant-demo','device':'gauge-demo',
        'scope':requested_scope,'roles':['administrator'],'token_use':'access','resource_id':content_identity(resource)}
    claims.update(changes)
    return jwt.encode(claims,f['keys']['idp'],algorithm='EdDSA',headers={'kid':'idp-demo','typ':'at+jwt'})


def enroll(f):
    from .network_trust import signed_document
    challenge=f['host'].enrollment_challenge(f['keys']['device'].public_key())
    proof=signed_document('enrollment','gauge-demo',challenge['payload'],f['keys']['device'])
    token=access_token(f,'device.enroll',challenge)
    return f['host'].request('enroll',token,{'challenge':challenge,'proof':proof})
