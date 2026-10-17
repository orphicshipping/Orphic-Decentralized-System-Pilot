"""Offline-verifiable identity and authorization boundaries, not an account server.

Human access tokens are ephemeral. Device/node X.509 credentials and publisher /
consent signing keys are independent. No discovery URL, key or executable comes
from a retained record. Production IdP, hardware attestation and TLS deployment
are separate qualifications.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import re
from urllib.parse import urlsplit

from . import device_boundary as b
from .core.identities import content_identity, new_identity, validate_identity
from .legibility import compile_bundle, sign_bundle, verify_bundle, key_fingerprint

MAX_TOKEN = 16384
SCOPES = frozenset({'device.enroll', 'device.read', 'device.configure.prepare',
                    'data.contribution.prepare', 'data.consent.revoke', 'release.review'})
KINDS = {'enrollment': 'network.device-enroll.v1', 'consent': 'network.consent.v1',
         'contribution': 'network.contribution-prepare.v1', 'release': 'network.release-review.v1',
         'revocation': 'network.consent-revoke.v1', 'authorization': 'network.authorization-evidence.v1'}


def public_bytes(key) -> bytes:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError('the bounded profile requires Ed25519 public keys')
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def signed_document(kind: str, subject: str, payload: dict, private_key) -> dict:
    if kind not in KINDS:
        raise ValueError('unsupported signed-document role')
    execution = new_identity('execution')
    bundle = _bundle(kind, subject, payload, execution)
    return {'kind': kind, 'subject': subject, 'execution_id': execution,
            'payload': deepcopy(payload), 'signature': sign_bundle(bundle, private_key)}


def _bundle(kind, subject, payload, execution):
    b.identifier(subject)
    validate_identity(execution, 'execution')
    # Extend existing Legibility rather than inventing another signature format.
    base = b.signature_bundle(payload, object_id=subject, execution_id=execution)['source']
    base['bindings']['operation_id'] = KINDS[kind]
    base['object']['kind'] = 'notation-network-' + kind
    base['object']['label'] = 'Notation network ' + kind
    return compile_bundle(base, {'payload': b.encode(payload)})


def verify_document(document: dict, *, kind: str, subject: str, public_key: bytes) -> dict:
    b.keys(document, {'kind', 'subject', 'execution_id', 'payload', 'signature'})
    if document['kind'] != kind or document['subject'] != subject or kind not in KINDS:
        raise ValueError('signature role or principal mismatch')
    bundle = _bundle(kind, subject, document['payload'], document['execution_id'])
    result = verify_bundle(bundle, document['signature'], artifacts={'payload': b.encode(document['payload'])},
        trusted_keys={key_fingerprint(public_key): public_key}, expected_object_id=subject,
        expected_version=content_identity(document['payload']))
    if not (result['signature_valid'] is True and result['issuer_trusted'] is True
            and result['content_intact'] is True and result['artifact_status'] == 'verified'):
        raise ValueError('invalid or untrusted role-specific signature')
    return deepcopy(document['payload'])


def _jwt_segment(value: str) -> bytes:
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('invalid JWT compact encoding')
    raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    if base64.urlsafe_b64encode(raw).decode().rstrip('=') != value:
        raise ValueError('noncanonical JWT compact encoding')
    return raw


class AccountVerifier:
    """Pinned issuer/audience/algorithm and an explicit, finite revocation snapshot.

    This verifies an issuer's assertion. It does not run WebAuthn, SMS or TOTP,
    and never equates a client-provided MFA flag with a verified login ceremony.
    """
    def __init__(self, *, issuer: str, node_id: str, tenant_id: str, keys: dict,
                 role_scopes: dict, step_up_acrs: tuple[str, ...], snapshot_expires: int,
                 revoked_tokens=(), revoked_subjects=()):
        url = urlsplit(issuer)
        if url.scheme != 'https' or not url.netloc or url.username or url.password or url.fragment:
            raise ValueError('configure an exact HTTPS issuer')
        b.identifier(node_id); b.identifier(tenant_id)
        if type(keys) is not dict or not 1 <= len(keys) <= 8:
            raise ValueError('explicit bounded issuer key set required')
        for kid, key in keys.items():
            b.identifier(kid); public_bytes(key)
        if not role_scopes or any(not set(scopes) <= SCOPES for scopes in role_scopes.values()):
            raise ValueError('roles cannot grant unimplemented capabilities')
        if not step_up_acrs or any(type(x) is not str or not x for x in step_up_acrs):
            raise ValueError('configure issuer-specific step-up assurance values')
        self.issuer, self.node_id, self.tenant_id = issuer, node_id, tenant_id
        self.keys = dict(keys)
        self.role_scopes = {b.identifier(k): frozenset(v) for k, v in role_scopes.items()}
        self.step_up_acrs = frozenset(step_up_acrs)
        self.snapshot_expires = b.integer(snapshot_expires, 1, 2**53-1)
        self.revoked_tokens, self.revoked_subjects = frozenset(revoked_tokens), frozenset(revoked_subjects)
        self._configuration = content_identity(self.configuration())

    def configuration(self):
        return {'issuer': self.issuer, 'node_id': self.node_id, 'tenant_id': self.tenant_id,
            'keys': {kid: key_fingerprint(public_bytes(key)) for kid, key in self.keys.items()},
            'role_scopes': {k: sorted(v) for k, v in self.role_scopes.items()},
            'step_up_acrs': sorted(self.step_up_acrs), 'snapshot_expires': self.snapshot_expires,
            'revoked_tokens': sorted(self.revoked_tokens), 'revoked_subjects': sorted(self.revoked_subjects)}

    def verify(self, token: str, *, device_id: str, scope: str, now: int, resource_id=None) -> dict:
        import jwt
        b.identifier(device_id); b.integer(now, 0, 2**53-1)
        if content_identity(self.configuration()) != self._configuration:
            raise ValueError('account-verifier configuration mutated; explicitly rebind it')
        if now >= self.snapshot_expires:
            raise ValueError('authorization/revocation snapshot is stale')
        if type(token) is not str or not 0 < len(token) <= MAX_TOKEN or scope not in SCOPES:
            raise ValueError('invalid access token or capability')
        parts = token.split('.')
        if len(parts) != 3:
            raise ValueError('require a compact signed access token')
        header = b.parse(_jwt_segment(parts[0]))
        raw_claims = b.parse(_jwt_segment(parts[1]))  # Also rejects duplicate JSON keys.
        _jwt_segment(parts[2])
        b.keys(header, {'alg', 'kid', 'typ'})
        if header['alg'] != 'EdDSA' or header['typ'] != 'at+jwt' or header['kid'] not in self.keys:
            raise ValueError('unapproved algorithm, token type or issuer key')
        required = {'iss','sub','aud','iat','nbf','exp','auth_time','jti','acr','amr',
                    'org','device','scope','roles','token_use','resource_id'}
        b.keys(raw_claims, required)
        # Signature/issuer/audience checks use PyJWT. Time is checked below against
        # the HOST clock, with exact integer types; callers cannot send now in data.
        try:
            claims = jwt.decode(token, self.keys[header['kid']], algorithms=['EdDSA'],
                issuer=self.issuer, audience='urn:notation:node:' + self.node_id,
                options={'require': sorted(required), 'strict_aud': True,
                         'verify_exp': False, 'verify_iat': False, 'verify_nbf': False})
        except jwt.InvalidTokenError as exc:
            raise ValueError('access-token signature/issuer/audience validation failed') from exc
        if claims != raw_claims or claims['iss'] != self.issuer or claims['aud'] != 'urn:notation:node:' + self.node_id:
            raise ValueError('token interpretation or exact issuer/audience mismatch')
        for name in ('iat','nbf','exp','auth_time'):
            b.integer(claims[name], 0, 2**53-1)
        if not (claims['auth_time'] <= claims['iat'] <= now and claims['nbf'] <= now < claims['exp']
                and claims['iat'] <= claims['nbf'] < claims['exp']
                and 0 < claims['exp']-claims['iat'] <= 900):
            raise ValueError('token expired, future-dated or outside the 15-minute envelope')
        for name in ('sub','jti','org','device'):
            b.identifier(claims[name])
        if claims['org'] != self.tenant_id or claims['device'] != device_id or claims['token_use'] != 'access':
            raise ValueError('tenant, device or access-token purpose mismatch')
        if claims['jti'] in self.revoked_tokens or claims['sub'] in self.revoked_subjects:
            raise ValueError('human authorization revoked')
        roles, methods = claims['roles'], claims['amr']
        if (type(roles) is not list or not 1 <= len(roles) <= 16 or any(type(x) is not str for x in roles)
                or len(set(roles)) != len(roles) or not set(roles) <= self.role_scopes.keys()
                or type(methods) is not list or any(type(x) is not str for x in methods)):
            raise ValueError('unrecognized roles or authentication-method claims')
        if type(claims['scope']) is not str:
            raise ValueError('scope must be an explicit bounded string')
        scopes = claims['scope'].split(' ')
        if len(scopes) > 16 or len(set(scopes)) != len(scopes) or not set(scopes) <= SCOPES:
            raise ValueError('unrecognized or duplicate token scope')
        grants = set().union(*(self.role_scopes[role] for role in roles))
        if scope not in scopes or scope not in grants:
            raise ValueError('role and token scope do not grant the requested capability')
        if scope != 'device.read' and (claims['acr'] not in self.step_up_acrs or now-claims['auth_time'] > 300):
            raise ValueError('fresh issuer-attested step-up authentication required')
        if resource_id is not None and claims['resource_id'] != resource_id:
            raise ValueError('authorization is not bound to this exact resource')
        return {'schema':'ciw.human-authorization-evidence.v1', 'verification_id':new_identity('verification'),
            'token_id': b.raw_id(token.encode()), 'issuer':self.issuer, 'issuer_key':header['kid'],
            'account_id':claims['sub'], 'tenant_id':self.tenant_id, 'node_id':self.node_id,
            'device_id':device_id, 'scope':scope, 'roles':list(roles), 'acr':claims['acr'],
            'auth_time':claims['auth_time'], 'expires':claims['exp'], 'evaluated_at':now,
            'configuration_id':self._configuration, 'resource_id':claims['resource_id'], 'assurance':'issuer_assertion_not_local_login_ceremony',
            'bearer_token_retained':False, 'device_identity_granted':False}


def issue_certificate(*, kind, subject, tenant, public_key, ca_certificate, ca_key, now, lifetime=30*86400):
    from cryptography import x509
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    if kind not in ('device','node'):
        raise ValueError('humans do not use instrument certificates')
    b.identifier(subject); b.identifier(tenant); public_bytes(public_key)
    b.integer(now, 0, 2**53-1); b.integer(lifetime, 1, 365*86400)
    if public_bytes(ca_key.public_key()) != public_bytes(ca_certificate.public_key()):
        raise ValueError('CA key/certificate mismatch')
    if (not ca_certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            or not ca_certificate.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign):
        raise ValueError('certificate issuer is not a CA')
    if not ca_certificate.not_valid_before_utc.timestamp() <= now < now+lifetime <= ca_certificate.not_valid_after_utc.timestamp():
        raise ValueError('requested certificate lifetime exceeds CA validity')
    usage = ExtendedKeyUsageOID.CLIENT_AUTH if kind == 'device' else ExtendedKeyUsageOID.SERVER_AUTH
    return (x509.CertificateBuilder().subject_name(x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, subject),x509.NameAttribute(NameOID.ORGANIZATION_NAME,tenant)]))
        .issuer_name(ca_certificate.subject).public_key(public_key).serial_number(x509.random_serial_number())
        .not_valid_before(datetime.fromtimestamp(now,timezone.utc))
        .not_valid_after(datetime.fromtimestamp(now+lifetime,timezone.utc))
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier('urn:notation:'+kind+':'+tenant+':'+subject)]),critical=False)
        .add_extension(x509.ExtendedKeyUsage([usage]),critical=False)
        .add_extension(x509.KeyUsage(True,False,False,False,False,False,False,False,False),critical=True)
        .sign(ca_key,algorithm=None))


def verify_certificate(certificate, *, ca_certificate, kind, subject, tenant, now, revoked_serials=()):
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID, ExtendedKeyUsageOID, SignatureAlgorithmOID, NameOID
    b.integer(now,0,2**53-1)
    if kind not in ('device','node'):
        raise ValueError('incorrect certificate role')
    public_bytes(certificate.public_key()); root_key=ca_certificate.public_key(); public_bytes(root_key)
    if (certificate.issuer != ca_certificate.subject or certificate.signature_algorithm_oid != SignatureAlgorithmOID.ED25519
            or not ca_certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            or not ca_certificate.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign
            or certificate.serial_number in revoked_serials):
        raise ValueError('certificate issuer, algorithm or revocation failure')
    for cert in (certificate,ca_certificate):
        if not cert.not_valid_before_utc.timestamp() <= now < cert.not_valid_after_utc.timestamp():
            raise ValueError('certificate or trust-anchor validity interval expired')
    root_key.verify(certificate.signature,certificate.tbs_certificate_bytes)
    allowed={ExtensionOID.BASIC_CONSTRAINTS,ExtensionOID.SUBJECT_ALTERNATIVE_NAME,
             ExtensionOID.EXTENDED_KEY_USAGE,ExtensionOID.KEY_USAGE}
    if {ext.oid for ext in certificate.extensions} != allowed:
        raise ValueError('unsupported certificate extension profile')
    if certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise ValueError('instrument/node credential cannot be a CA')
    expected='urn:notation:'+kind+':'+b.identifier(tenant)+':'+b.identifier(subject)
    san=certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if list(san) != [x509.UniformResourceIdentifier(expected)]:
        raise ValueError('certificate identity role or tenant mismatch')
    if (certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value != subject
            or certificate.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value != tenant):
        raise ValueError('certificate subject mismatch')
    usage=ExtendedKeyUsageOID.CLIENT_AUTH if kind=='device' else ExtendedKeyUsageOID.SERVER_AUTH
    if list(certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) != [usage]:
        raise ValueError('certificate purpose mismatch')
    ku=certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    if not ku.digital_signature or ku.key_cert_sign or ku.crl_sign or ku.key_agreement or ku.key_encipherment:
        raise ValueError('certificate key usage mismatch')
    return {'kind':kind,'subject':subject,'tenant':tenant,'public_key_id':key_fingerprint(public_bytes(certificate.public_key())),
            'serial':str(certificate.serial_number),'hardware_binding':'not_attested','physical_validity':'not_established'}
