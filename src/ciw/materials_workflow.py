"""Execute existing material profiles through NET Session and retained dependencies.

Trusted registration only. A declaration transport coordinate is not acquisition
 time. Source, operation, execution, result and numerical-check scopes stay distinct.
"""
from copy import deepcopy
from pathlib import Path
import hashlib
import platform
import threading
import uuid

from . import materials_bench as mb, thermochemical_benchmark as tc
from . import polymer_viscoelastic as pv, polymer_population as pp
from . import rheology_empirical as er, materials_uncertainty as mu
from .adapters.protocol import InstrumentManifest
from .control_contracts import keys, detached
from .core.identities import evidence_id, validate_evidence_identity, validate_identity, new_identity
from .core.records import validate_run_structure
from .operations.registry import Operation, OperationRegistry
from .operations.runner import digest, seal, check_seal

EVALUATE = 'materials.evaluate.v1'
INSPECT = 'materials.inspect.v1'
VERIFY = 'materials.verify.v1'
REPLAY = 'materials.replay.v1'
IDS = (EVALUATE, INSPECT, VERIFY, REPLAY)
AUTHORITY = {'physical_validation': False, 'calibration_authenticated': False,
             'state_admission': False, 'hardware_actuation': False, 'part_release': False}
_LOCK = threading.RLock()
MODULES = {tc.REQUEST_SCHEMA: tc, pv.SCHEMA: pv, pp.SCHEMA: pp,
           er.REQUEST_SCHEMA: er, mu.SCHEMA: mu}


def normalize(request):
    request = detached(request)
    if type(request) is not dict or type(request.get('schema')) is not str:
        raise ValueError('A declared supported material request is required')
    module = MODULES.get(request['schema'])
    if module is None: raise ValueError('No installed material profile; no arbitrary provider loading')
    return module, module.validate_request(request)


def runtime_identity():
    import ciw.core.covariance as covariance
    modules = [tc, pv, pp, mb, er, mu, covariance, __import__(__name__, fromlist=['*'])]
    return {'provider': 'ciw.materials-session', 'version': '1',
            'source_sha256': {Path(m.__file__).name: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                              for m in modules},
            'python': platform.python_version(), 'platform': platform.system(),
            'authenticated': False, 'scope': 'bounded-material-numerics-and-secondary-evidence'}


def make_source(request):
    module, p = normalize(request)
    kind = ('published-experimental-summary' if module is er else
            'declared-uncertainty-input' if module is mu else 'mathematical-model-configuration')
    manifest = InstrumentManifest(instrument_id='materials-investigation-declaration.v1',
        role='retained_investigation_declaration', units={'declaration': '1'}, frames=('materials-declaration',),
        sampling={'kind': 'single-declaration', 'time_semantics': 'synthetic-selection-not-acquisition-time'},
        supported_operations=IDS, calibration_requirements={'authority': 'no calibration authentication or actuation'})
    run = {'run_schema': 'run.v1', 'run_id': 'run-materials-'+digest(p)[7:23],
           'instrument': manifest.instrument_id,
           'metadata': {'duration_s': 1.0, 'sample_count': 1, 'sample_rate_hz': None,
                        'coordinate_frame': 'materials-declaration', 'manifest': manifest.to_dict(),
                        'materials_request': p,
                        'provenance': {'source': kind, 'generator': 'ciw.materials_workflow.make_source',
                                       'generator_version': 1, 'measurement_by_NET': False}},
           'time_s': [0.0], 'channels': {'declaration': {'unit': '1', 'values': [1.0]}}, 'render': {}}
    run['evidence_id'] = evidence_id(run)
    return run


def source_request(run):
    validate_run_structure(run); validate_evidence_identity(run)
    p = run['metadata'].get('materials_request')
    _, p = normalize(p)
    if run != make_source(p): raise ValueError('Source differs from the exact retained material declaration')
    return p


def _compute(p):
    module, p = normalize(p)
    if module is er: return er.run(p)
    if module is mu:
        return seal({'schema': 'ciw.materials-uncertainty-bundle.v1', 'request': p, 'result': mu.analyze(p)})
    return mb.run(p)


def _inspect_report(p, report):
    module, p = normalize(p)
    if type(report) is not dict or digest(report.get('request')) != digest(p):
        raise ValueError('Material report request differs from its Session source')
    if module is er:
        er.inspect_result(report)
    elif module is mu:
        check_seal(report); keys(report, {'schema', 'request', 'result', 'record_digest'})
        if report['schema'] != 'ciw.materials-uncertainty-bundle.v1': raise ValueError('Wrong uncertainty bundle')
        r = report['result']
        keys(r, {'schema', 'request_ref', 'observation_ref', 'analysis', 'source_kind', 'authority',
                 'source_refs_authenticated', 'status', 'reason', 'calculation'})
        if (r['schema'] != 'ciw.materials-uncertainty-result.v1' or r['request_ref'] != digest(p)
                or r['observation_ref'] != digest(p['observation']) or r['analysis'] != p['analysis']
                or r['source_kind'] != p['observation']['source_kind']):
            raise ValueError('Uncertainty result source binding differs')
        mb._equal(mu.AUTHORITY, r['authority'], rtol=0, atol=0)
        if r['source_refs_authenticated'] is not False: raise ValueError('Source authentication not established')
        if r['status'] not in ('CALCULATED', 'HELD'): raise ValueError('Unknown uncertainty result status')
        if r['status'] == 'HELD':
            if r['calculation'] is not None or r['reason'] not in {
                    'joint-covariance-not-supplied', 'zero-modulus-gradient-undefined',
                    'singular-joint-covariance', 'singular-or-ill-conditioned-joint-covariance',
                    'joint-covariance-scaling-not-representable'}:
                raise ValueError('Invalid held calculation')
        elif r['reason'] is not None or type(r['calculation']) is not dict:
            raise ValueError('Invalid calculated result')
        if p['covariance'] is None and (r['status'] != 'HELD' or r['reason'] != 'joint-covariance-not-supplied'):
            raise ValueError('Missing covariance cannot be promoted to a calculation')
        mu.inspect_calculation(p, r)
    else:
        mb.inspect(report)


def _candidate(run, parameters):
    keys(parameters, {'candidate'})
    candidate = parameters['candidate']
    check_seal(candidate)
    if (candidate.get('schema') != 'ciw.operation-result.v1' or candidate.get('operation_id') != EVALUATE
            or candidate.get('role') != 'backend' or candidate.get('evidence_id') != run['evidence_id']
            or candidate.get('run_id') != run['run_id'] or candidate.get('parameters') != {}):
        raise ValueError('Require an exact source-bound material evaluation occurrence')
    validate_identity(candidate['result_id'], 'result'); validate_identity(candidate['execution_id'], 'execution')
    validate_payload(EVALUATE, candidate['data'], run, {}, {})
    return candidate


def _payload(run, action, report, candidate=None):
    data = {'schema': 'ciw.materials-session-payload.v1', 'action': action,
            'source_ref': digest(source_request(run)), 'report': report, 'authority': deepcopy(AUTHORITY)}
    if candidate is not None:
        data.update(candidate_result_id=candidate['result_id'], candidate_record_digest=candidate['record_digest'])
    return data


def _evaluate(run, parameters):
    keys(parameters, set())
    return _payload(run, EVALUATE, _compute(source_request(run)))


def _inspect(run, parameters):
    c = _candidate(run, parameters)
    # Validators already checked normalized source and retained report; no solver invocation.
    return _payload(run, INSPECT, {'inspection_id': str(uuid.uuid4()),
        'status': 'CONTENT_CONSISTENT', 'scope': 'retained-integrity-and-binding-only',
        'numerical_reexecution': False}, c)


def _replay(run, parameters):
    c = _candidate(run, parameters)
    return _payload(run, REPLAY, _compute(source_request(run)), c)


def _verify(run, parameters):
    c = _candidate(run, parameters); p = source_request(run); module, _ = normalize(p)
    if module is er:
        proof = er.verify_result(c['data']['report'])
        scope = 'independent-decimal-arithmetic-audit'
    elif module is mu:
        fresh = _compute(p)
        mb._equal(c['data']['report']['result'], fresh['result'], rtol=2e-12, atol=1e-14)
        proof = {'fresh_report': fresh, 'original_report_ref': digest(c['data']['report'])}
        scope = 'current-kernel-reexecution-not-independent-verification'
    else:
        proof = mb.reproduce(c['data']['report'])
        scope = 'current-kernel-reexecution-not-independent-verification'
    return _payload(run, VERIFY, seal({'verification_id': new_identity('verification'),
        'status': 'PASS', 'scope': scope, 'proof': proof,
        'physical_validation': False}), c)


def validate_payload(operation_id, data, run, parameters, selection):
    """Offline structural and dependency binding; never execute a numerical solver."""
    p = source_request(run); module, _ = normalize(p)
    required = {'schema', 'action', 'source_ref', 'report', 'authority'}
    if operation_id != EVALUATE: required |= {'candidate_result_id', 'candidate_record_digest'}
    keys(data, required)
    if data['schema'] != 'ciw.materials-session-payload.v1' or data['action'] != operation_id or data['source_ref'] != digest(p):
        raise ValueError('Material payload operation/source binding differs')
    mb._equal(AUTHORITY, data['authority'], rtol=0, atol=0)
    if operation_id == EVALUATE:
        keys(parameters, set()); _inspect_report(p, data['report']); return
    c = _candidate(run, parameters)
    if data['candidate_result_id'] != c['result_id'] or data['candidate_record_digest'] != c['record_digest']:
        raise ValueError('Material dependency occurrence binding differs')
    r = data['report']
    if operation_id == INSPECT:
        keys(r, {'inspection_id', 'status', 'scope', 'numerical_reexecution'})
        er._uuid(r['inspection_id'])
        if r['status'] != 'CONTENT_CONSISTENT' or r['scope'] != 'retained-integrity-and-binding-only' or r['numerical_reexecution'] is not False:
            raise ValueError('Inspection scope was promoted')
    elif operation_id == REPLAY:
        _inspect_report(p, r)
        for key in ('execution_id', 'result_id'):
            if key in r and r[key] == c['data']['report'].get(key):
                raise ValueError('Replay reused a scientific occurrence')
    elif operation_id == VERIFY:
        keys(r, {'verification_id', 'status', 'scope', 'proof', 'physical_validation', 'record_digest'})
        check_seal(r); validate_identity(r['verification_id'], 'verification')
        scope = ('independent-decimal-arithmetic-audit' if module is er else
                 'current-kernel-reexecution-not-independent-verification')
        if r['scope'] != scope or r['status'] != 'PASS' or r['physical_validation'] is not False:
            raise ValueError('Verification scope was promoted')
        proof = r['proof']
        if module is er:
            expected = {'schema','operation_id','execution_id','verification_id','verified_at','result_id',
                        'result_record_sha256','evidence_id','status','method','observations_checked',
                        'marginal_comparisons_checked','joint_statistic','publisher_pdf_bytes_verified',
                        'source_review_authenticated','runtime','authority','record_sha256'}
            keys(proof, expected)
            if mb.digest({k:v for k,v in proof.items() if k != 'record_sha256'}) != proof['record_sha256']:
                raise ValueError('Empirical verification seal differs')
            for key in ('execution_id','verification_id'): er._uuid(proof[key])
            original = c['data']['report']
            if (proof['result_id'] != original['result_id'] or proof['result_record_sha256'] != original['record_sha256']
                    or proof['evidence_id'] != original['evidence']['evidence_id'] or proof['status'] != 'PASS'
                    or type(proof['observations_checked']) is not int or proof['observations_checked'] != 32
                    or type(proof['marginal_comparisons_checked']) is not int or proof['marginal_comparisons_checked'] != 16
                    or proof['joint_statistic'] is not None or proof['publisher_pdf_bytes_verified'] is not False
                    or proof['source_review_authenticated'] is not False):
                raise ValueError('Empirical verification dependency/coverage differs')
            if (proof['schema'] != 'ciw.rheology-empirical-verification.v1'
                    or proof['operation_id'] != 'polymer.rheology.empirical-audit.v1'
                    or proof['method'] != 'independent-50-digit-decimal-normalization-and-zero-storage-intervals'):
                raise ValueError('Empirical verification method differs')
            er._timestamp(proof['verified_at'])
            keys(proof['runtime'], {'python','platform','source_sha256','authenticated'})
            if proof['runtime']['authenticated'] is not False: raise ValueError('Runtime authentication is not established')
            keys(proof['runtime']['source_sha256'], {'rheology_empirical.py','materials_bench.py','thermochemical_benchmark.py'})
            for value in proof['runtime']['source_sha256'].values(): er._hash(value)
            mb._equal(er.AUTHORITY, proof['authority'], rtol=0, atol=0)
        elif module is mu:
            keys(proof, {'fresh_report', 'original_report_ref'})
            if proof['original_report_ref'] != digest(c['data']['report']): raise ValueError('Reexecution input differs')
            _inspect_report(p, proof['fresh_report'])
            # Recheck the retained comparison without reexecuting propagation.
            mb._equal(c['data']['report']['result'], proof['fresh_report']['result'],
                      rtol=2e-12, atol=1e-14)
        else:
            keys(proof, {'schema','verification_id','original_execution_id','original_complete_report_sha256',
                         'fresh_report','comparison','status','scope','physical_validation','state_admission'})
            original = c['data']['report']
            if (proof['schema'] != 'ciw.materials-benchmark-reproduction.v1'
                    or proof['original_execution_id'] != original['execution_id']
                    or proof['original_complete_report_sha256'] != mb.digest(original)
                    or proof['scope'] != scope or proof['status'] != 'passed'
                    or proof['physical_validation'] is not False or proof['state_admission'] is not False):
                raise ValueError('Reproduction source/scope differs')
            er._uuid(proof['verification_id'])
            _inspect_report(p, proof['fresh_report'])
            if proof['fresh_report']['execution_id'] == original['execution_id']:
                raise ValueError('Reproduction reused the scientific execution')
            if type(proof['comparison']) is not dict or proof['comparison'].get('within_policy') is not True:
                raise ValueError('Reproduction comparison did not pass')
            # Compare retained arrays/policies without launching a solver.
            expected = (tc.compare_trajectories(p, original['trajectory'], proof['fresh_report']['trajectory'])
                        if module is tc else mb._compare_material_results(module, original['result'], proof['fresh_report']['result']))
            mb._equal(expected, proof['comparison'], rtol=0, atol=0)
    else:
        raise ValueError('Unsupported material operation')


def register_schemas():
    from .operations import schemas
    if not hasattr(schemas, 'register_dependency_fields'):
        raise ValueError('Required retained-result extension boundary (PR184) is not installed; no fallback')
    with _LOCK:
        for name in IDS:
            prior = schemas._VALIDATORS.get(name)
            if prior is not None and prior is not validate_payload:
                raise ValueError('Material operation validator already owned by different trusted code')
        for name in IDS:
            role = 'verification' if name == VERIFY else 'backend'
            if name not in schemas._VALIDATORS:
                schemas.register_payload_validator(name, validate_payload, role=role)
                if name != EVALUATE: schemas.register_dependency_fields(name, ('candidate',))
            elif (schemas._VALIDATOR_ROLES.get(name) != role or
                  (name != EVALUATE and schemas._DEPENDENCY_FIELDS.get(name) != ('candidate',))):
                raise ValueError('Installed material role/dependency contract differs')


def registry():
    register_schemas()
    result = OperationRegistry()
    for name, fn in ((EVALUATE,_evaluate),(INSPECT,_inspect),(VERIFY,_verify),(REPLAY,_replay)):
        result.register(Operation(name, 'verification' if name == VERIFY else 'backend', fn, runtime_identity))
    return result


def execute(session, operation_id, parameters, *, request_id=None):
    reply = session.handle({'protocol_version': 1, 'request_id': request_id or uuid.uuid4().hex,
                            'type': 'operation.execute', 'payload': {'operation_id': operation_id, 'parameters': parameters}})
    if reply['type'] != 'response': raise ValueError(reply['payload']['message'])
    return reply['payload']


def create_session(request, directory):
    from .session import Session
    return Session(make_source(request), Path(directory), operations=registry())


def reopen(path, directory, *, bind=False):
    from .session import Session
    register_schemas()
    if Path(path).stat().st_size > 64*1024*1024:
        raise ValueError('Material workspace exceeds the 64 MiB read budget')
    session = Session.from_workspace(Path(path), Path(directory))
    source_request(session.run)
    # Offline reopen has no executable operations until an operator explicitly binds.
    session.operations = registry() if bind else OperationRegistry()
    return session
