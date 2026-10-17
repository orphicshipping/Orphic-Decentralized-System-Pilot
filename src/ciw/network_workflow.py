"""NET network lifecycle on the existing Session, with no network transport.

The only live credentials enter NetworkHost.request, never operation parameters.
Session reopening validates retained artifacts but cannot restore a human grant,
a private key, a live issuer binding or an enrollment challenge.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import secrets
import threading
import time

from . import device_boundary as b
from .core.identities import content_identity, new_identity, validate_identity
from .adapters.protocol import AdapterRefusal
from .operations.registry import Operation
from .operations.schemas import register_payload_validator
from .network_trust import AccountVerifier, public_bytes, signed_document, verify_document, issue_certificate, verify_certificate
from .network_data import prepare_contribution, residual_source, review_release

OPS = {'enroll':'network.device-enroll.v1', 'contribute':'network.contribution-prepare.v1',
       'release':'network.release-review.v1', 'revoke':'network.consent-revoke.v1'}
SCOPES = {'enroll':'device.enroll','contribute':'data.contribution.prepare',
          'release':'release.review','revoke':'data.consent.revoke'}
FIELDS = {'enroll':{'challenge','proof'}, 'contribute':{'source_b64','consent','recipient','purpose'},
          'release':{'artifact_b64','manifest'}, 'revoke':{'revocation'}}
LIMITS = {'physical_authority':False,'transmitted':False,'installed':False,'training_admission':False,
          'login_ceremony_performed':False,'hardware_attestation':'not_established',
          'scientific_state_admission':False,'scope':'local_cryptographic_lifecycle_and_preparation'}
_SCHEMA_LOCK = threading.RLock()
_REGISTERED = False


def pem(cert):
    from cryptography.hazmat.primitives.serialization import Encoding
    return cert.public_bytes(Encoding.PEM).decode()


def cert(text):
    from cryptography import x509
    if type(text) is not str or not 0<len(text)<=16384:raise ValueError('bounded certificate PEM required')
    return x509.load_pem_x509_certificate(text.encode())


def _identity_audit(value):
    b.keys(value,{'tenant','device','node','ca_certificate','node_certificate','device_certificate',
                 'consent_key_b64','publisher_key_b64','publisher','device_family','runtime_profile',
                 'approved_transforms','minimum_versions','device_revocation_expires','revoked_serials','observation_policy'})
    for name in ('tenant','device','node','publisher','device_family','runtime_profile'):b.identifier(value[name])
    now=b.integer(value['device_revocation_expires'],1,2**53-1)
    for name in ('consent_key_b64','publisher_key_b64'):
        if len(b.unb64(value[name],32))!=32:raise ValueError('invalid audit public key')
    if value['observation_policy'] is not None:
        from .device_boundary_workflow import validate_policy
        validate_policy(value['observation_policy'])
        if set(value['observation_policy']['approved_transform_ids'])!=set(value['approved_transforms']):
            raise ValueError('network allowlist differs from existing boundary policy')
    return deepcopy(value)


def validate_result(operation_id,data,run,parameters,selection):
    b.keys(parameters,{'request_id','inputs'})
    b.keys(data,{'schema','action','authorization','authorization_receipt','audit','evaluated_at','result','limits'})
    action=data['action']
    if action not in OPS or OPS[action]!=operation_id or data['schema']!='ciw.network-result.v1' or data['limits']!=LIMITS:
        raise ValueError('network result identity or authority changed')
    b.keys(parameters['inputs'],FIELDS[action])
    auth=data['authorization']
    b.keys(auth,{'schema','verification_id','token_id','issuer','issuer_key','account_id','tenant_id','node_id',
        'device_id','scope','roles','acr','auth_time','expires','evaluated_at','configuration_id','resource_id',
        'assurance','bearer_token_retained','device_identity_granted'})
    validate_identity(auth['verification_id'],'verification');b.hash_id(auth['token_id']);b.hash_id(auth['configuration_id'])
    audit=_identity_audit(data['audit']);now=b.integer(data['evaluated_at'],0,2**53-1)
    if (auth['schema']!='ciw.human-authorization-evidence.v1' or auth['bearer_token_retained'] is not False
            or auth['device_identity_granted'] is not False or auth['scope']!=SCOPES[action]
            or (auth['tenant_id'],auth['node_id'],auth['device_id'])!=(audit['tenant'],audit['node'],audit['device'])
            or not auth['auth_time']<=auth['evaluated_at']<=now<auth['expires']):
        raise ValueError('historical authorization context mismatch; never a fresh grant')
    ca=cert(audit['ca_certificate']);node_cert=cert(audit['node_certificate'])
    verify_certificate(node_cert,ca_certificate=ca,kind='node',subject=audit['node'],tenant=audit['tenant'],now=now)
    attested=verify_document(data['authorization_receipt'],kind='authorization',subject=audit['node'],public_key=public_bytes(node_cert.public_key()))
    if attested!={'authorization':auth,'inputs_id':content_identity(parameters['inputs']),'action':action}:
        raise ValueError('signed authorization evidence receipt mismatch')
    inputs=parameters['inputs'];result=data['result']
    resource=inputs['challenge'] if action=='enroll' else inputs['consent'] if action=='contribute' else inputs['manifest'] if action=='release' else inputs['revocation']
    if auth['resource_id']!=content_identity(resource):raise ValueError('resource-scoped authorization mismatch')
    if action=='enroll':
        b.keys(result,{'certificate','challenge_id','account_id','device_identity','hardware_attestation'})
        leaf=cert(result['certificate'])
        identity=verify_certificate(leaf,ca_certificate=ca,kind='device',subject=audit['device'],tenant=audit['tenant'],now=now)
        challenge=verify_document(inputs['challenge'],kind='enrollment',subject=audit['node'],public_key=public_bytes(node_cert.public_key()))
        proof=verify_document(inputs['proof'],kind='enrollment',subject=audit['device'],public_key=public_bytes(leaf.public_key()))
        if (content_identity(inputs['challenge'])!=result['challenge_id'] or proof!=challenge
                or challenge['device_key_id']!=identity['public_key_id'] or result['device_identity']!=identity
                or result['account_id']!=auth['account_id'] or auth['resource_id']!=result['challenge_id']):
            raise ValueError('certificate, challenge or human binding mismatch')
    elif action=='contribute':
        b.keys(result,{'export','local_lineage','status','transmitted','training_admission','source_bytes_unchanged','raw_commitment_exported'})
        if (result['status']!='PREPARED_LOCAL_ONLY' or result['source_bytes_unchanged'] is not True
                or result['raw_commitment_exported'] is not False):raise ValueError('invalid local preparation result')
        raw=b.unb64(inputs['source_b64'],65536)
        leaf=cert(audit['device_certificate']);verify_certificate(leaf,ca_certificate=ca,kind='device',subject=audit['device'],tenant=audit['tenant'],now=now)
        source,y,pred=residual_source(raw,tenant=audit['tenant'],device=audit['device'],device_public_key=public_bytes(leaf.public_key()),approved_transforms=frozenset(audit['approved_transforms']),observation_policy=audit['observation_policy'],now=now)
        grant=verify_document(inputs['consent'],kind='consent',subject=audit['tenant'],public_key=b.unb64(audit['consent_key_b64'],32))
        exported=verify_document(result['export'],kind='contribution',subject=audit['node'],public_key=public_bytes(node_cert.public_key()))
        if (auth['resource_id']!=content_identity(inputs['consent']) or grant['evidence_id']!=b.raw_id(raw)
                or exported['value']!=b.rational(y-pred) or exported['unit']!='m'
                or exported['recipient']!=inputs['recipient'] or exported['purpose']!=inputs['purpose']
                or exported['sharing_class']!=grant['output_class'] or exported['training_admission'] is not False
                or result['transmitted'] is not False or result['training_admission'] is not False
                or result['local_lineage']['evidence_id']!=b.raw_id(raw)):
            raise ValueError('contribution lineage, permission or scientific result mismatch')
    elif action=='release':
        raw=b.unb64(inputs['artifact_b64'],8*1024*1024)
        expected=review_release(raw,inputs['manifest'],publisher=audit['publisher'],publisher_key=b.unb64(audit['publisher_key_b64'],32),
            device_family=audit['device_family'],runtime_profile=audit['runtime_profile'],now=now,minimum_versions=audit['minimum_versions'])
        if result!=expected or auth['resource_id']!=content_identity(inputs['manifest']):raise ValueError('release review replay mismatch')
    else:
        revoke=verify_document(inputs['revocation'],kind='revocation',subject=audit['tenant'],public_key=b.unb64(audit['consent_key_b64'],32))
        if result!={'consent_id':revoke['consent_id'],'status':'FUTURE_PREPARATION_DENIED','past_artifacts_deleted':False,'trained_models_unlearned':False}:
            raise ValueError('revocation is not a claim of deletion or unlearning')
    # Account token itself is deliberately absent. Offline inspection checks
    # retained consistency/signatures, NOT current account authentication.


def register_schemas():
    global _REGISTERED
    with _SCHEMA_LOCK:
        if not _REGISTERED:
            for operation_id in OPS.values():register_payload_validator(operation_id,validate_result,role='backend')
            _REGISTERED=True


class NetworkHost:
    """Operator-bound one-node/one-device pilot over the existing Session."""
    def __init__(self,session,*,accounts:AccountVerifier,device_id,node_certificate,node_key,ca_certificate,
                 ca_key,consent_key,publisher_key,publisher,device_family,runtime_profile,
                 approved_transforms,lineage_key,device_revocation_expires,device_certificate=None,
                 revoked_serials=(),minimum_versions=None,observation_policy=None,clock=time.time):
        from .legibility import key_fingerprint
        register_schemas()
        b.identifier(device_id);b.identifier(publisher);b.identifier(device_family);b.identifier(runtime_profile)
        self.session,self.accounts=session,accounts;self._clock=clock;self._last_time=None
        self._lock=threading.RLock();self._active=None;self._challenges={};self._consumed=set()
        self._node_key,self._ca_key,self._lineage_key=node_key,ca_key,lineage_key
        if public_bytes(node_key.public_key())!=public_bytes(node_certificate.public_key()):raise ValueError('node key mismatch')
        if public_bytes(ca_key.public_key())!=public_bytes(ca_certificate.public_key()):raise ValueError('CA key mismatch')
        if type(lineage_key) is not bytes or len(lineage_key)!=32:raise ValueError('explicit private lineage key required')
        role_keys=[public_bytes(node_key.public_key()),public_bytes(ca_key.public_key()),consent_key,publisher_key]
        role_keys += [public_bytes(key) for key in accounts.keys.values()]
        if len(set(role_keys))!=len(role_keys):raise ValueError('human, node, CA, consent and release signing roles must use separate keys')
        self._audit={'tenant':accounts.tenant_id,'device':device_id,'node':accounts.node_id,
            'ca_certificate':pem(ca_certificate),'node_certificate':pem(node_certificate),
            'device_certificate':None if device_certificate is None else pem(device_certificate),
            'consent_key_b64':base64.b64encode(consent_key).decode(),'publisher_key_b64':base64.b64encode(publisher_key).decode(),
            'publisher':publisher,'device_family':device_family,'runtime_profile':runtime_profile,
            'approved_transforms':sorted(approved_transforms),'minimum_versions':dict(minimum_versions or {'model':1,'firmware':1,'gateware':1}),
            'device_revocation_expires':device_revocation_expires,'revoked_serials':[str(x) for x in revoked_serials],
            'observation_policy':deepcopy(observation_policy)}
        _identity_audit(self._audit)
        verify_certificate(node_certificate,ca_certificate=ca_certificate,kind='node',subject=accounts.node_id,tenant=accounts.tenant_id,now=self._now())
        for action,operation_id in OPS.items():
            session.operations.register(Operation(operation_id,'backend',lambda run,p,a=action:self._execute(a,p),
                lambda:{'provider':'ciw.network-lifecycle.v1','scope':'local_preparation_only'}))

    def _now(self):
        now=int(self._clock());b.integer(now,0,2**53-1)
        if self._last_time is not None and now<self._last_time:raise ValueError('network host clock rollback')
        self._last_time=now
        return now

    def _device(self,now):
        if now>=self._audit['device_revocation_expires']:raise ValueError('device revocation snapshot stale')
        if self._audit['device_certificate'] is None:raise ValueError('device not enrolled')
        leaf=cert(self._audit['device_certificate'])
        identity=verify_certificate(leaf,ca_certificate=cert(self._audit['ca_certificate']),kind='device',
            subject=self._audit['device'],tenant=self._audit['tenant'],now=now,revoked_serials={int(x) for x in self._audit['revoked_serials']})
        return leaf,identity

    def verify_device_packet(self,packet):
        """Independent from the human token/phone, within cert/revocation validity."""
        from .legibility import key_fingerprint
        with self._lock:
            leaf,identity=self._device(self._now());key=public_bytes(leaf.public_key())
            p=b.validate_payload(packet['payload'])
            if p['device_id']!=self._audit['device']:raise ValueError('wrong device')
            check=b.authenticated(packet,{key_fingerprint(key):key})
            if not(check['signature_valid'] is True and check['issuer_trusted'] is True):raise ValueError('device signature failed')
            return {'identity':identity,'packet_id':content_identity(packet),'account_token_required':False,
                    'physical_validation':'not_established'}

    def enrollment_challenge(self,device_public_key):
        from .legibility import key_fingerprint
        with self._lock:
            now=self._now()
            if len(self._challenges)>=64:raise ValueError('bounded commissioning challenge pool exhausted')
            raw=public_bytes(device_public_key)
            if raw in [public_bytes(self._node_key.public_key()),public_bytes(self._ca_key.public_key())]+[public_bytes(k) for k in self.accounts.keys.values()]+[b.unb64(self._audit[n],32) for n in ('consent_key_b64','publisher_key_b64')]:
                raise ValueError('device key must be independent of node, account, CA, consent and release keys')
            body={'schema':'ciw.device-enrollment-challenge.v1','tenant_id':self._audit['tenant'],
                'node_id':self._audit['node'],'device_id':self._audit['device'],
                'device_key_b64':base64.b64encode(raw).decode(),'device_key_id':key_fingerprint(raw),
                'nonce':secrets.token_hex(32),'issued_at':now,'expires':now+120}
            challenge=signed_document('enrollment',self._audit['node'],body,self._node_key)
            self._challenges[content_identity(challenge)]=challenge
            return deepcopy(challenge)

    def request(self,action,token,inputs):
        """No token is passed to Session, including refused/unknown requests."""
        with self._lock:
            if action not in OPS:raise ValueError('unsupported network action')
            try:
                b.keys(inputs,FIELDS[action]);clean=b.parse(b.encode(inputs));invalid=None
            except Exception:
                clean={};invalid='invalid action input fields; omitted from retention'
            self._active=(token,invalid)
            try:
                response=self.session.handle({'protocol_version':1,'request_id':new_identity('execution'),
                    'type':'operation.execute','payload':{'operation_id':OPS[action],
                    'parameters':{'request_id':new_identity('execution'),'inputs':clean}}})
                if action=='enroll' and response.get('payload',{}).get('status')=='completed':
                    self._audit['device_certificate']=response['payload']['result']['data']['result']['certificate']
                return response
            finally:self._active=None

    def _execute(self,action,parameters):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        with self._lock:
            try:
                b.keys(parameters,{'request_id','inputs'})
                if self._active is None:raise ValueError('fresh out-of-band human access token required')
                token,invalid=self._active
                if invalid:raise ValueError(invalid)
                inp=parameters['inputs'];b.keys(inp,FIELDS[action])
                resource=inp['challenge'] if action=='enroll' else inp['consent'] if action=='contribute' else inp['manifest'] if action=='release' else inp['revocation']
                now=self._now()
                authorization=self.accounts.verify(token,device_id=self._audit['device'],scope=SCOPES[action],now=now,resource_id=content_identity(resource))
                audit=deepcopy(self._audit)
                if action=='enroll':
                    cid=content_identity(inp['challenge'])
                    if cid not in self._challenges or cid in self._consumed:raise ValueError('unknown or consumed enrollment challenge')
                    body=verify_document(inp['challenge'],kind='enrollment',subject=audit['node'],public_key=public_bytes(self._node_key.public_key()))
                    if not body['issued_at']<=now<body['expires']:raise ValueError('enrollment challenge expired')
                    key=Ed25519PublicKey.from_public_bytes(b.unb64(body['device_key_b64'],32))
                    proof=verify_document(inp['proof'],kind='enrollment',subject=audit['device'],public_key=public_bytes(key))
                    if proof!=body:raise ValueError('device proof of possession does not bind the complete challenge')
                    now=self._now();authorization=self.accounts.verify(token,device_id=audit['device'],scope=SCOPES[action],now=now,resource_id=cid)
                    if now>=body['expires']:raise ValueError('challenge expired before issuance')
                    self._consumed.add(cid)
                    leaf=issue_certificate(kind='device',subject=audit['device'],tenant=audit['tenant'],public_key=key,
                        ca_certificate=cert(audit['ca_certificate']),ca_key=self._ca_key,now=now)
                    identity=verify_certificate(leaf,ca_certificate=cert(audit['ca_certificate']),kind='device',subject=audit['device'],tenant=audit['tenant'],now=now)
                    result={'certificate':pem(leaf),'challenge_id':cid,'account_id':authorization['account_id'],
                            'device_identity':identity,'hardware_attestation':'not_established'}
                elif action=='contribute':
                    leaf,_=self._device(now)
                    revoked={r['data']['result']['consent_id'] for r in list(self.session.results.values()) if r.get('operation_id')==OPS['revoke']}
                    result=prepare_contribution(b.unb64(inp['source_b64'],65536),inp['consent'],tenant=audit['tenant'],device=audit['device'],
                        node=audit['node'],recipient=inp['recipient'],purpose=inp['purpose'],now=now,
                        consent_key=b.unb64(audit['consent_key_b64'],32),device_key=public_bytes(leaf.public_key()),
                        node_signing_key=self._node_key,lineage_key=self._lineage_key,
                        approved_transforms=frozenset(audit['approved_transforms']),observation_policy=audit['observation_policy'],revoked_consents=revoked)
                elif action=='release':
                    floors=dict(audit['minimum_versions'])
                    for r in list(self.session.results.values()):
                        if r.get('operation_id')==OPS['release']:
                            prior=r['data']['result']['release'];floors[prior['kind']]=max(floors[prior['kind']],prior['version'])
                    audit['minimum_versions']=floors
                    result=review_release(b.unb64(inp['artifact_b64'],8*1024*1024),inp['manifest'],publisher=audit['publisher'],
                        publisher_key=b.unb64(audit['publisher_key_b64'],32),device_family=audit['device_family'],
                        runtime_profile=audit['runtime_profile'],now=now,minimum_versions=floors)
                else:
                    doc=verify_document(inp['revocation'],kind='revocation',subject=audit['tenant'],public_key=b.unb64(audit['consent_key_b64'],32))
                    b.keys(doc,{'schema','consent_id','tenant_id','issued_at','reason'})
                    if doc['schema']!='ciw.network-consent-revocation.v1' or doc['tenant_id']!=audit['tenant'] or not 0<=doc['issued_at']<=now:
                        raise ValueError('invalid revocation context')
                    b.identifier(doc['consent_id'])
                    if doc['reason'] not in ('customer_request','policy_change','suspected_exposure'):raise ValueError('unsupported revocation reason')
                    result={'consent_id':doc['consent_id'],'status':'FUTURE_PREPARATION_DENIED','past_artifacts_deleted':False,'trained_models_unlearned':False}
                # A result never carries a reusable bearer credential or install grant.
                now=self._now();authorization=self.accounts.verify(token,device_id=audit['device'],scope=SCOPES[action],now=now,resource_id=content_identity(resource))
                return {'schema':'ciw.network-result.v1','action':action,'authorization':authorization,
                        'authorization_receipt':signed_document('authorization',audit['node'],
                            {'authorization':authorization,'inputs_id':content_identity(inp),'action':action},self._node_key),
                        'audit':audit,'evaluated_at':now,'result':result,'limits':deepcopy(LIMITS)}
            except Exception as exc:
                raise AdapterRefusal('network_gate_closed',type(exc).__name__+': '+str(exc)[:400]) from exc
