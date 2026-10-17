"""Fixed adapters into existing thermal and joint-reference calculations.

Session, OperationRegistry, the graph runner and AgentHost own dispatch and retry.
No selected data can name a Python import, provider, device or acceptance policy.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import platform

from . import link_contract as c
from .agent_api import encode, parse
from .adapters.protocol import InstrumentManifest
from .control_contracts import keys
from .core.identities import evidence_id, validate_evidence_identity, content_identity
from .operations.registry import Operation

ASSESS = 'operational.engineering.assess.v1'
VERIFY = 'operational.engineering.verify.v1'
SCHEMA = 'ncp.operational-engineering-result.v1'


def make_source(raw, *, engine, binding_ref, policy):
    c.content_ref(binding_ref)
    if engine == 'thermal':
        from .measured_thermal import validate_source
        validate_source(raw)
        keys(policy, {'coverage_factor', 'max_abs_error', 'max_standard_uncertainty', 'uncertainty'})
    elif engine == 'reference':
        from .reference_comparison import _stream, _policy
        v = parse(raw); keys(v, {'left', 'right', 'policy'})
        _stream(v['left']); _stream(v['right']); _policy(v['policy'], v['left'], v['right'])
        keys(policy, set())
    elif engine in {'scalar','calibrated-window','residual-monitor'}:
        from .operational_native import request,unit
        request(raw,engine)
        keys(policy,{'unit','coverage_factor','max_abs_error','max_standard_uncertainty'})
        from .control_contracts import number,text
        text(policy['unit'])
        if policy['unit']!=unit(raw,engine): raise ValueError('Native comparison unit differs from the declared quantity')
        if not 1<=number(policy['coverage_factor'])<=6 or any(number(policy[k])<0 for k in ('max_abs_error','max_standard_uncertainty')):
            raise ValueError('Native comparison limits must be predeclared and nonnegative')
    else: raise ValueError('Unsupported model/source adapter')
    chunks = [c.packed(raw[i:i+24576], 24576) for i in range(0, len(raw), 24576)]
    manifest = InstrumentManifest(instrument_id='operational-engineering-evidence.v1', role='retained_evidence',
        units={'declaration': '1'}, frames=('declaration',), supported_operations=(ASSESS,),
        sampling={'kind': 'frozen_native_source'}, calibration_requirements={'scope': 'native_applicability_only'})
    source = {'run_schema': 'run.v1', 'run_id': 'run-operational-' + sha256(encode([binding_ref, engine, policy])).hexdigest()[:24],
        'instrument': manifest.instrument_id, 'metadata': {'duration_s': 1.0, 'sample_count': 1, 'sample_rate_hz': None,
            'coordinate_frame': 'declaration', 'manifest': manifest.to_dict(),
            'operational': {'engine': engine, 'binding_ref': binding_ref, 'policy': deepcopy(policy), 'chunks': chunks},
            'provenance': {'source': 'retained_declaration', 'generator': 'ciw.operational_engineering', 'generator_version': 1}},
        'time_s': [0.0], 'channels': {'declaration': {'unit': '1', 'values': [1.0]}}, 'render': {}}
    source['evidence_id'] = evidence_id(source)
    return source


def source_request(source):
    from .instruments import validate_run
    validate_run(source); validate_evidence_identity(source)
    v = source['metadata']['operational']
    keys(v, {'engine', 'binding_ref', 'policy', 'chunks'})
    if type(v['chunks']) is not list or not 1 <= len(v['chunks']) <= 22:
        raise ValueError('Bounded native source chunks required')
    raw = b''.join(c.unpack(p, 24576) for p in v['chunks'])
    if source != make_source(raw, engine=v['engine'], binding_ref=v['binding_ref'], policy=v['policy']):
        raise ValueError('Engineering envelope differs from its exact native inputs')
    return raw, v


def runtime_identity():
    from . import measured_thermal_workflow, reference_comparison, operational_native, link_study, calibrated_window, residual_monitor
    files = [Path(__file__), *(Path(module.__file__) for module in
        (reference_comparison,operational_native,link_study,calibrated_window,residual_monitor))]
    return {'provider': 'ciw.operational_engineering', 'version': '1', 'python': platform.python_version(),
        'adapter_sha256': sha256(b'\0'.join(p.read_bytes().replace(b'\r\n', b'\n') for p in files)).hexdigest(),
        'thermal': measured_thermal_workflow.runtime_identity(), 'scope': 'retained_data_only_no_hardware'}


def _thermal_streams(raw, bundle):
    from . import measured_thermal as t
    source = t.validate_source(raw)
    data = bundle['steps'][0]['result']['data']
    predicted = t.observation_stream(source, data, stage='posterior', execution_id=None)
    observed = t.observation_stream(source, data, stage='reference', execution_id=None)
    return source, predicted, observed


def _thermal_policy(source, p, left, right):
    from .reference_comparison import POLICY_SCHEMA
    uncertainty = deepcopy(p['uncertainty'])
    keys(uncertainty, {'status', 'scope', 'relationship', 'matrix', 'justification', 'budget_ref'})
    matrix = uncertainty.pop('matrix')
    joint = None
    if matrix is not None:
        from .core.covariance import create_covariance_artifact
        n = len(left['observations'])
        joint = create_covariance_artifact(matrix=matrix,
            quantity_ids=[f'{side}:{i}' for side in ('left', 'right') for i in range(n)],
            units=['K']*(2*n), frame=source['frame'],
            reference_values=[r['value'] for s in (left, right) for r in s['observations']],
            method='operator_declared_total_joint_comparison', basis={'kind': 'observation', 'id': content_identity(p)},
            provenance={'provider': 'operator.declaration', 'source_evidence_ids': [content_identity(left), content_identity(right)],
                        'source_covariance_ids': []}, assumptions=[uncertainty['justification']])
    uncertainty['joint_covariance'] = joint
    return {'schema': POLICY_SCHEMA, 'policy_id': content_identity(p),
        'source_kind': 'synthetic' if source['origin'] == 'test_fixture' else 'retained_observation',
        'left_ref': content_identity(left), 'right_ref': content_identity(right), 'unit': 'K',
        'coverage_factor': p['coverage_factor'], 'max_abs_error': p['max_abs_error'],
        'max_standard_uncertainty': p['max_standard_uncertainty'], 'uncertainty': uncertainty}


def assess(source, parameters, *, providers=None):
    from .reference_comparison import compare
    keys(parameters, set()); raw, v = source_request(source)
    native = None
    if v['engine'] == 'thermal':
        from .measured_thermal_workflow import MeasuredThermalWorkflow
        native = MeasuredThermalWorkflow().create_session(raw, {})
        thermal, left, right = _thermal_streams(raw, native)
        policy = _thermal_policy(thermal, v['policy'], left, right)
    elif v['engine']=='reference':
        request = parse(raw); left, right, policy = request['left'], request['right'], request['policy']
    else:
        from . import operational_native
        native=operational_native.execute(raw,v['engine'],providers or {})
        left,right,report=operational_native.comparison(raw,v['engine'],native,v['policy'])
        return {'schema':SCHEMA,'binding_ref':v['binding_ref'],'engine':v['engine'],'native_bundle':native,
            'prediction':left,'observation':right,'comparison':report,'model':model_metadata(raw,v['engine'],native),
            'authority':deepcopy(report['authority'])}
    model = model_metadata(raw,v['engine'],native)
    report = compare(left, right, policy)
    return {'schema': SCHEMA, 'binding_ref': v['binding_ref'], 'engine': v['engine'],
        'native_bundle': native, 'prediction': left, 'observation': right, 'comparison': report,
        'model': model, 'authority': deepcopy(report['authority'])}


def validate_assessment(operation, data, source, parameters, selection):
    """Offline integrity checks; never enter create_session or provider dispatch."""
    from .reference_comparison import validate_report
    keys(parameters, set()); raw, v = source_request(source)
    keys(data, {'schema', 'binding_ref', 'engine', 'native_bundle', 'prediction', 'observation', 'comparison', 'model', 'authority'})
    if operation != ASSESS or data['schema'] != SCHEMA or data['binding_ref'] != v['binding_ref'] or data['engine'] != v['engine']:
        raise ValueError('Engineering result binding differs')
    if selection['channel'] != 'declaration' or selection['interval_s'] != [0.0, 1.0]:
        raise ValueError('Engineering operation requires its complete frozen declaration')
    report = data['comparison']; validate_report(report)
    if (report['request']['left'] != data['prediction'] or report['request']['right'] != data['observation']
            or report['authority'] != data['authority']): raise ValueError('Comparison lineage differs')
    if v['engine'] == 'thermal':
        from .measured_thermal_workflow import MeasuredThermalWorkflow
        retained = MeasuredThermalWorkflow()._validate(data['native_bundle'])
        if retained != raw: raise ValueError('Native thermal source changed')
        thermal, left, right = _thermal_streams(raw, data['native_bundle'])
        if left != data['prediction'] or right != data['observation'] or report['request']['policy'] != _thermal_policy(thermal, v['policy'], left, right):
            raise ValueError('Thermal prediction or frozen policy differs')
    elif v['engine']=='reference':
        if report['request'] != parse(raw) or data['native_bundle'] is not None:
            raise ValueError('Retained reference request differs')
    else:
        from . import operational_native
        operational_native.validate(raw,v['engine'],data['native_bundle'])
        left,right,expected=operational_native.comparison(raw,v['engine'],data['native_bundle'],v['policy'])
        if (left,right,expected)!=(data['prediction'],data['observation'],report):
            raise ValueError('Native comparison projection differs')
    if data['model'] != model_metadata(raw, v['engine'], data['native_bundle']):
        raise ValueError('Retained model applicability or held-out declaration differs')


def model_metadata(raw, engine, native):
    if engine in {'scalar','calibrated-window','residual-monitor'}:
        from .operational_native import metadata
        return metadata(raw,engine,native)
    if engine=='thermal':
        from .measured_thermal import validate_source
        thermal=validate_source(raw)
        return {'id':content_identity(thermal['request']['model']),'parameters':thermal['request']['model'],
            'validity_domain':'two-capacity linear thermal model; native declared applicability',
            'fitting_evidence':thermal['model_evidence_refs'],'comparison_evidence':thermal['series']['core_reference'],
            'held_out':thermal['held_out'],'assumptions':thermal['assumptions'],
            'prediction_mode':native['steps'][0]['result']['data']['evaluation']['estimation_mode']}
    request=parse(raw);left,right=request['left'],request['right']
    return {'id':left['observations'][0]['identity']['model_id'] if left['observations'] else 'unknown',
        'validity_domain':'retained paired predictions only; no new model inference','fitting_evidence':'unknown',
        'comparison_evidence':content_identity(right),'held_out':'operator_declaration_only','prediction_mode':'retained_prediction'}


def verify(source, parameters):
    from .core.identities import new_identity
    from .reference_comparison import validate_report
    keys(parameters,{'assessment'})
    candidate=parameters['assessment']
    if candidate.get('operation_id')!=ASSESS or candidate.get('evidence_id')!=source['evidence_id']:
        raise ValueError('Verification requires the source-bound assessment occurrence')
    validate_report(candidate['data']['comparison'])
    return verification_record(candidate,new_identity('verification'))


def verification_record(candidate,verification_id):
    return {'schema':'ncp.operational-engineering-verification.v1','verification_id':verification_id,
        'assessment_result_id':candidate['result_id'],'assessment_execution_id':candidate['execution_id'],
        'assessment_digest':candidate['record_digest'],'status':'PASS',
        'scope':'offline_numerical_report_and_binding_consistency','independent':False,'physical_validation':'not_established'}


def validate_payload(operation,data,source,parameters,selection):
    if operation==ASSESS:return validate_assessment(operation,data,source,parameters,selection)
    if operation!=VERIFY:raise ValueError('Unsupported engineering operation')
    from .core.identities import validate_identity
    keys(data,{'schema','verification_id','assessment_result_id','assessment_execution_id','assessment_digest','status',
               'scope','independent','physical_validation'})
    validate_identity(data['verification_id'],'verification')
    keys(parameters,{'assessment'})
    candidate=parameters['assessment']
    if candidate.get('operation_id')!=ASSESS or candidate.get('evidence_id')!=source['evidence_id']:raise ValueError('Verification source differs')
    from .reference_comparison import validate_report
    validate_report(candidate['data']['comparison'])
    expected=verification_record(candidate,data['verification_id'])
    if data!=expected:raise ValueError('Verification differs from its exact assessment')


_REGISTERED = False


def registry(*, bind=True, providers=None):
    global _REGISTERED
    from .operations.schemas import register_payload_validator
    from .control_plane import CapabilityRegistry
    if not _REGISTERED:
        from .operations.schemas import register_dependency_fields
        register_payload_validator(ASSESS, validate_payload, role='backend')
        register_payload_validator(VERIFY, validate_payload, role='verification')
        register_dependency_fields(VERIFY,('assessment',)); _REGISTERED=True
    from .control_plane import Port
    reg = CapabilityRegistry()
    implementation=assess if not providers else lambda source,parameters:assess(source,parameters,providers=providers)
    for op in (Operation(ASSESS,'backend',implementation,runtime_identity),Operation(VERIFY,'verification',verify,runtime_identity)):
        manifest=InstrumentManifest(instrument_id=op.operation_id,role=op.role,supported_operations=(op.operation_id,),
            sampling={'kind':'frozen_retained_data'},calibration_requirements={'scope':'native_checks_and_declared_joint_budget'})
        reg.advertise(manifest,runtime=runtime_identity(),capabilities={op.operation_id:[op.operation_id]},
            inputs={op.operation_id:{'assessment':Port('ciw.operation-result.v1').to_dict()} if op.operation_id==VERIFY else {}})
        if bind:reg.bind(op)
    return reg
