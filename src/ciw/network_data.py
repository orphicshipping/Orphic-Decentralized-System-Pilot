"""Consent-bound local contribution and release preparation, never transmission.

A sharing class is not proof of de-identification, ownership or scientific
qualification. Raw sources and lineage stay local; the fixed projection cannot
select arbitrary fields, URLs, code, executable paths or transformation plugins.
"""
from __future__ import annotations
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
import hmac

from . import device_boundary as b
from .core.identities import content_identity
from .network_trust import verify_document, signed_document

SHARING_CLASSES = frozenset({'private','derived_shareable','pseudonymized','consortium','public'})
PURPOSES = frozenset({'research','model_training','benchmarking'})
TRANSFORM = 'notation.length-residual.v1'
CONDITIONS = frozenset({'temperature_K','pressure_Pa','humidity_fraction'})


def residual_source(raw: bytes, *, tenant: str, device: str, device_public_key: bytes,
                    approved_transforms: frozenset, observation_policy=None, now=None) -> tuple[dict, Fraction, Fraction]:
    from .legibility import key_fingerprint
    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        raise ValueError('bounded local evidence source required')
    source = b.parse(raw)
    expected = {'schema','tenant_id','device_id','observation','prediction','joint_covariance_m2',
                'conditions','private_context'}
    if 'sharing_class' in source:
        expected.add('sharing_class')
    b.keys(source,expected)
    if source['schema'] != 'ciw.network-residual-source.v1' or (source['tenant_id'],source['device_id']) != (tenant,device):
        raise ValueError('evidence schema, tenant or device mismatch')
    if source.get('sharing_class','private') not in SHARING_CLASSES:
        raise ValueError('unsupported source sharing class')
    packet=source['observation']; p=b.validate_payload(packet['payload'])
    sig=b.authenticated(packet,{key_fingerprint(device_public_key):device_public_key})
    if not (sig['signature_valid'] is True and sig['issuer_trusted'] is True and p['device_id']==device):
        raise ValueError('source observation not signed by the enrolled device')
    if p['filter_id'] != b.FILTER_D or content_identity(p['transform']) not in approved_transforms:
        raise ValueError('unapproved acquisition/filter/calibration transformation')
    if observation_policy is None or now is None:
        raise ValueError('operator-reviewed existing boundary policy required')
    from .device_boundary_workflow import evaluate
    boundary=evaluate(b.encode(packet),observation_policy,now*10**9)
    required_checks=('schema','identity','filter_declaration','firmware_declaration','gateware_declaration',
                     'frame','clock','calibration','coherence')
    if not all(boundary['checks'][name] for name in required_checks):
        raise ValueError('existing device/NET coherence or metrology-declaration gate closed')
    measured_nm=b.reconstruct(p)
    if measured_nm != b.ratio(p['device_state']['value'])*b.UNITS_NM[p['device_state']['unit']]:
        raise ValueError('device/NET observation coherence failed')
    prediction=source['prediction']
    b.keys(prediction,{'model_id','frame_id','quantity','value','unit'})
    b.hash_id(prediction['model_id'])
    if prediction['frame_id']!=p['frame_id'] or prediction['quantity']!='length' or prediction['unit'] not in b.UNITS_NM:
        raise ValueError('prediction/observation quantity or frame mismatch')
    predicted_nm=b.ratio(prediction['value'])*b.UNITS_NM[prediction['unit']]
    import struct
    cfg=p['transform'];count=struct.unpack('<q',b.unb64(p['raw_b64'],8))[0]
    u=p['time_uncertainty_ns'];t=p['captured_at_ns']
    if (u is None or not cfg['count_min']<=count<=cfg['count_max']
            or not cfg['valid_from_ns']<=t-u or not t+u<cfg['valid_until_ns']):
        raise ValueError('declared calibration applicability or time uncertainty insufficient')
    if type(source['conditions']) is not dict or not set(source['conditions']) <= CONDITIONS:
        raise ValueError('only the bounded physical-condition vocabulary is accepted')
    for name,value in source['conditions'].items():
        number=b.ratio(value)
        if number<0 or (name=='humidity_fraction' and number>1):
            raise ValueError('invalid physical-condition value')
    if type(source['private_context']) is not dict:
        raise ValueError('private context is a retained local object, never an export field')
    covariance=source['joint_covariance_m2']
    if covariance is not None:
        if (type(covariance) is not list or len(covariance)!=2 or any(type(row) is not list or len(row)!=2 for row in covariance)):
            raise ValueError('explicit joint 2x2 covariance required')
        a,c=map(b.ratio,covariance[0]);d,e=map(b.ratio,covariance[1])
        if a<0 or e<0 or c!=d or a*e-c*c<0:
            raise ValueError('joint covariance must be symmetric positive semidefinite; no repair')
    return source,measured_nm/Fraction(10**9),predicted_nm/Fraction(10**9)


def prepare_contribution(raw, consent, *, tenant, device, node, recipient, purpose, now,
                         consent_key, device_key, node_signing_key, lineage_key,
                         approved_transforms, observation_policy=None, revoked_consents=frozenset()):
    source,measured,predicted=residual_source(raw,tenant=tenant,device=device,
        device_public_key=device_key,approved_transforms=approved_transforms,observation_policy=observation_policy,now=now)
    grant=verify_document(consent,kind='consent',subject=tenant,public_key=consent_key)
    b.keys(grant,{'schema','consent_id','tenant_id','evidence_id','device_id','recipients','purposes',
                 'source_fields','output_conditions','output_class','transform_id','not_before','expires',
                 'retain_until','terms_id','allow_cross_tenant_training','proof_policy'})
    if grant['schema']!='ciw.network-consent.v1':raise ValueError('unsupported consent schema')
    if grant['proof_policy'] not in ('independent_reconstruction','sp1_required'):raise ValueError('unsupported contribution proof policy')
    if grant['proof_policy']=='sp1_required':raise ValueError('SP1-qualified residual transform not available; contribution gate closed')
    b.identifier(grant['consent_id']);b.hash_id(grant['terms_id'])
    for field in ('not_before','expires','retain_until'):b.integer(grant[field],0,2**53-1)
    if (grant['tenant_id'],grant['device_id'],grant['evidence_id']) != (tenant,device,b.raw_id(raw)):
        raise ValueError('consent is not bound to this customer, device and exact evidence')
    if grant['consent_id'] in revoked_consents or not grant['not_before']<=now<grant['expires']<=grant['retain_until']:
        raise ValueError('consent expired, revoked or has invalid retention bounds')
    for field in ('recipients','purposes','source_fields','output_conditions'):
        items=grant[field]
        if type(items) is not list or not len(items)<=32 or any(type(x) is not str for x in items) or len(set(items))!=len(items):
            raise ValueError('consent arrays must be bounded, typed and unique')
    if grant['output_class'] not in SHARING_CLASSES or grant['output_class']=='private':
        raise ValueError('private evidence has no contribution permission')
    if purpose not in PURPOSES or purpose not in grant['purposes'] or recipient not in grant['recipients']:
        raise ValueError('recipient or purpose not expressly permitted')
    if grant['output_class']=='public' and grant['recipients']!=['public']:
        raise ValueError('public classification requires an explicit public recipient')
    if grant['output_class']!='public' and recipient=='public':
        raise ValueError('non-public consent cannot authorize a public target')
    if type(grant['allow_cross_tenant_training']) is not bool:
        raise ValueError('cross-tenant training permission must be explicit')
    if purpose=='model_training' and recipient!=tenant and grant['allow_cross_tenant_training'] is not True:
        raise ValueError('cross-tenant model training not licensed by this consent')
    allowed={'observed_length','predicted_length','model_id','joint_covariance_m2','metrology_summary'}
    required={'observed_length','predicted_length','model_id','metrology_summary'}
    if grant['transform_id']!=TRANSFORM or not required<=set(grant['source_fields'])<=allowed:
        raise ValueError('unapproved transform or source-field use; arbitrary projections are forbidden')
    if not set(grant['output_conditions']) <= source['conditions'].keys():
        raise ValueError('consent cannot introduce an absent condition')
    if type(lineage_key) is not bytes or len(lineage_key)!=32:
        raise ValueError('operator-owned recipient-pseudonym key required')
    # Keyed recipient-scoped linkage supports source grouping without publishing a
    # guessable raw-evidence hash. Key rotation/versioning is the operator's duty.
    b.identifier(recipient)
    observation=source['observation']['payload']
    sample_lineage={name:observation[name] for name in ('device_id','boot_id','sample_id')}
    group=hmac.new(lineage_key,(recipient+'\0'+content_identity(sample_lineage)).encode(),sha256).hexdigest()
    variance=None; covariance=source['joint_covariance_m2']
    if covariance is not None and 'joint_covariance_m2' in grant['source_fields']:
        a,c=map(b.ratio,covariance[0]);_,d=map(b.ratio,covariance[1])
        variance=b.rational(a+d-2*c)
    payload={'schema':'ciw.network-contribution.v1','source_group':group,'recipient':recipient,'purpose':purpose,'sharing_class':grant['output_class'],
        'transform_id':TRANSFORM,'quantity':'length_residual','unit':'m','value':b.rational(measured-predicted),
        'variance_m2':variance,'uncertainty_status':'unknown' if variance is None else 'declared_joint_covariance_propagated',
        'model_id':source['prediction']['model_id'],
        'conditions':{name:deepcopy(source['conditions'][name]) for name in grant['output_conditions']},
        'origin':source['observation']['payload']['origin'],'retention_deadline':grant['retain_until'],
        'consent_id':grant['consent_id'],'terms_id':grant['terms_id'],'use_not_before':grant['not_before'],'use_expires':grant['expires'],
        'provenance':'observation signature and exact reconstruction; private lineage held by customer',
        'prediction_provenance':'customer_declared; model_execution_not_verified',
        'conditions_provenance':'consent_bound_customer_declarations',
        'metrology':{'quantity':'length','calibration_applicability':'declared_interval_and_count_domain_checked',
            'clock_alignment':'not_established','instrument_characterization':'not_established',
            'calibration_traceability':'private_reference_retained; not_independently_qualified'},
        'privacy_assessment':'not_established; pseudonymization_is_not_anonymization',
        'physical_qualification':'not_established','SP1':'NOT_RUN','training_admission':False}
    # Source grouping is stable; content identity must change with the actual result.
    payload['contribution_id']=content_identity(payload)
    export=signed_document('contribution',node,payload,node_signing_key)
    return {'export':export,'local_lineage':{'evidence_id':b.raw_id(raw),'consent_id':grant['consent_id'],
        'consent_document_id':content_identity(consent),'tenant_id':tenant,'device_id':device,
        'original_sharing_class':source.get('sharing_class','private'),'source_group':group},
        'status':'PREPARED_LOCAL_ONLY','transmitted':False,'training_admission':False,
        'source_bytes_unchanged':True,'raw_commitment_exported':False}


def review_release(raw: bytes, manifest, *, publisher, publisher_key, device_family,
                   runtime_profile, now, minimum_versions):
    value=verify_document(manifest,kind='release',subject=publisher,public_key=publisher_key)
    b.keys(value,{'schema','release_id','kind','version','device_family','runtime_profile','artifact_id',
                 'size_bytes','issued_at','expires','validation_record_id'})
    if value['schema']!='ciw.network-release.v1' or value['kind'] not in ('model','firmware','gateware'):
        raise ValueError('unsupported signed-release schema or artifact kind')
    b.identifier(value['release_id']);b.integer(value['version'],1,2**31-1)
    b.integer(value['issued_at'],0,2**53-1);b.integer(value['expires'],0,2**53-1)
    b.hash_id(value['artifact_id'])
    if value['validation_record_id'] is not None:b.hash_id(value['validation_record_id'])
    if type(raw) is not bytes or not 0<len(raw)<=8*1024*1024 or type(value['size_bytes']) is not int:
        raise ValueError('bounded nonempty opaque release bytes required')
    if b.raw_id(raw)!=value['artifact_id'] or len(raw)!=value['size_bytes']:
        raise ValueError('signed release byte identity mismatch')
    if value['device_family']!=device_family or value['runtime_profile']!=runtime_profile:
        raise ValueError('release target/compatibility mismatch')
    if not value['issued_at']<=now<value['expires']:
        raise ValueError('release metadata expired or future-dated')
    if value['version']<minimum_versions[value['kind']]:
        raise ValueError('rollback below the operator-bound version floor')
    return {'status':'SIGNATURE_AND_BYTES_REVIEWED','release':deepcopy(value),
        'artifact_id':b.raw_id(raw),'validation_reference_authenticated_only':value['validation_record_id'] is not None,
        'empirical_validation':'not_established','install_authorized':False,'installed':False,
        'model_executed':False,'equipment_authority':False,'format':'opaque_bytes_not_deserialized'}
