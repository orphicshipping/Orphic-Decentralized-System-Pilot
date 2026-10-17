"""Trusted GaugeLink covariance-scenario profile over Board, Session and Needle.

Original measurement occurrences are restored, never acquired or recomputed.
The three adapter operations retain a basis, calculate a scenario using the
existing improvement kernel, and audit it using the independent rational kernel.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import Lock

from . import link_contract as c, link_study as study, link_measurements as measurements
from . import link_pinwheel as pinwheel, link_improvement as improvement
from .link_improvement_audit import audit_calculation
from .adapters.protocol import InstrumentManifest
from .control_plane import Port
from .control_contracts import save_new
from .core.identities import content_identity
from .operations.registry import Operation
from .operations.schemas import register_payload_validator, register_dependency_fields
from .semantic_capabilities import SemanticRegistry, SemanticMorphism, EngineLowering
from .session import Session, loads_json
from .system_board import board_from_spec, validate_board, compile_board

PROFILE = 'gaugelink-covariance-refit.v1'
OPS = ('link.refit-basis.v1', 'link.refit-scenario.v1', 'link.refit-audit.v1')
CAPS = ('metrology.retained-basis.v1', 'metrology.covariance-scenario.v1', 'metrology.scenario-audit.v1')
_REGISTERED = False
_LOCK = Lock()


def runtime_identity():
    return {'provider': 'ciw.link_refit', 'version': '1',
            'code_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
            'calculation': improvement.runtime_identity()}


def validate_basis(source, basis):
    c.keys(basis, {'schema', 'model', 'audit', 'plan'})
    if basis['schema'] != 'ciw.link-refit-basis.v1':
        raise ValueError('Unsupported retained basis')
    improvement.validate_plan(basis['plan'], source=source, model=basis['model'], audit=basis['audit'])
    covariance = study.source_request(source)['calibration']['joint_covariance']
    if covariance is None:
        raise ValueError('GaugeLink refit requires declared full covariance')
    candidates = basis['plan']['candidates']
    n = len(covariance['matrix'])
    if (len(candidates) != 1 or candidates[0]['id'] != 'offset-scenario'
            or candidates[0]['scales'] != [1] * (n - 1) + [0.5]):
        raise ValueError('Profile binds one offset-amplitude scenario at fixed input means')
    return basis


def scenario_parameters(source, parameters):
    c.keys(parameters, {'basis', 'offset_scale'})
    retained = parameters['basis']
    if retained['operation_id'] != OPS[0] or retained['evidence_id'] != source['evidence_id']:
        raise ValueError('Scenario requires its exact retained basis occurrence')
    basis = deepcopy(validate_basis(source, retained['data']))
    scale = c.finite(parameters['offset_scale'])
    if not 0 <= scale <= 0.99:
        raise ValueError('Offset amplitude scale must be in [0,0.99]')
    basis['plan']['candidates'][0]['scales'][-1] = scale
    return {key: basis[key] for key in ('model', 'audit', 'plan')}


def calculate(index, source, parameters):
    if index == 1:
        return improvement.review(source, scenario_parameters(source, parameters))
    c.keys(parameters, {'candidate'})
    candidate = parameters['candidate']
    if candidate['operation_id'] != OPS[1] or candidate['evidence_id'] != source['evidence_id']:
        raise ValueError('Audit requires the matching scenario occurrence')
    scientific = scenario_parameters(source, candidate['parameters'])
    return audit_calculation(source, scientific, candidate['data'], candidate['result_id'])


def _validator(index):
    def validate(operation_id, data, source, parameters, selection):
        from . import link_workflow
        if (operation_id != OPS[index] or selection['channel'] != link_workflow.CHANNEL
                or selection['interval_s'] != [0.0, 1.0]):
            raise ValueError('Refit must use the complete retained declaration')
        if index == 0:
            c.keys(parameters, {'basis_ref'})
            validate_basis(source, data)
            if content_identity(data) != parameters['basis_ref']:
                raise ValueError('Retained basis differs from the selected identity')
        elif not study._equal(data, calculate(index, source, parameters)):
            raise ValueError('Scenario or audit differs from its exact prerequisites')
    return validate


def register():
    global _REGISTERED
    improvement.register()
    with _LOCK:
        if not _REGISTERED:
            for i, op in enumerate(OPS):
                register_payload_validator(op, _validator(i), role=('decision', 'decision', 'verification')[i])
                if i:
                    register_dependency_fields(op, ((), ('basis',), ('candidate',))[i])
            _REGISTERED = True


class GaugeLinkRefitProfile:
    instructions = ('Inspect status, preview hypothesis.offset_scale, validate, establish the baseline under '
        'the existing grant, recompute and compare using stable attempt IDs. The original capture and four '
        'scalar-study occurrences are retained; the evidence-basis node is reused. Scaled covariance is '
        'hypothetical. No acquisition, calibration change, physical control or acceptance is available. '
        'Reconnect with the same operator profile; never retry unresolved work under a new ID.')

    def __init__(self, bundle, plan):
        self.bundle = measurements.validate(bundle)
        request, _, original, _ = pinwheel._original(self.bundle)
        old = {r['operation_id']: r for r in original.results.values()}
        self.source = deepcopy(original.run)
        if plan['bundle_id'] != self.bundle['bundle_id']:
            raise ValueError('Plan and selected measurement bundle differ')
        self.basis = validate_basis(self.source, {'schema': 'ciw.link-refit-basis.v1',
            'model': old[study.OPS[2]], 'audit': old[study.OPS[3]], 'plan': deepcopy(plan)})
        self.original = loads_json(measurements._unpack(self.bundle['source_files']['workspace.json']).decode())
        self._runtime = runtime_identity()
        register()

    def identity(self):
        return {'profile': PROFILE, 'bundle_ref': content_identity(self.bundle),
                'basis_ref': content_identity(self.basis), 'runtime': runtime_identity()}

    def summary(self):
        return {'profile': PROFILE, 'profile_ref': content_identity(self.identity()),
                'bundle_id': self.bundle['bundle_id'], 'basis_ref': content_identity(self.basis),
                'original_result_refs': {r['operation_id']: content_identity(r) for r in self.original['results']},
                'covariance_ref': content_identity(study.source_request(self.source)['calibration']['joint_covariance']),
                'scenario_meaning': 'hypothetical D Sigma D at fixed means and correlations',
                'acquisition_performed': False, 'physical_qualification': 'not_established'}

    def registry(self):
        from . import link_workflow
        result = improvement.registry()
        manifest = InstrumentManifest(instrument_id=PROFILE, role='retained_instrument_capture',
            units={link_workflow.CHANNEL: '1'}, frames=(link_workflow.FRAME,),
            sampling={'kind': 'one_declaration'}, supported_operations=OPS,
            calibration_requirements={'scope': 'hypothetical_uncertainty_only'})
        port = Port('ciw.operation-result.v1').to_dict()
        identity = self.identity()
        result.advertise(manifest, runtime=identity, capabilities={op: [CAPS[i]] for i, op in enumerate(OPS)},
                         inputs={OPS[1]: {'basis': port}, OPS[2]: {'candidate': port}})
        def execute(index, source, parameters):
            if source != self.source or runtime_identity() != self._runtime:
                raise ValueError('Selected source or provider changed')
            if index == 0:
                c.keys(parameters, {'basis_ref'})
                if parameters['basis_ref'] != content_identity(self.basis):
                    raise ValueError('Basis identity mismatch')
                return deepcopy(self.basis)
            return calculate(index, source, parameters)
        for i, op in enumerate(OPS):
            result.bind(Operation(op, ('decision', 'decision', 'verification')[i],
                                  lambda source, parameters, i=i: execute(i, source, parameters), self.identity))
        return result

    def semantic(self, concrete):
        result = SemanticRegistry(concrete)
        port = Port('ciw.operation-result.v1').to_dict()
        for i, capability in enumerate(CAPS):
            inputs = ({}, {'basis': port}, {'candidate': port})[i]
            result.declare(SemanticMorphism(capability, 'compute', inputs, {'result': port},
                effects=('read:recording',), description=('Retain exact original model and audit',
                'Declare a covariance amplitude counterfactual', 'Independently audit counterfactual arithmetic')[i]))
            result.implement(EngineLowering(capability, OPS[i], 'ciw.link_refit', 'python',
                                          'scientific', 'headless', resources=('cpu',)))
        return result

    def board(self, offset_scale=0.5):
        scale = {'length_m': None, 'time_s': None, 'energy_j': None, 'label': 'retained investigation'}
        def socket(name):
            return {'socket_id': name, 'port': Port('ciw.operation-result.v1').to_dict(),
                    'scale': deepcopy(scale), 'uncertainty_semantics': 'UNKNOWN', 'provenance_required': True}
        def node(i, name, inputs, parameters):
            return {'node_id': name, 'label': name, 'kind': 'OPERATION', 'semantic_capability': CAPS[i],
                'sockets': {'inputs': [socket(x) for x in inputs], 'outputs': [socket('result')]},
                'parameters': parameters, 'resources': ['cpu'], 'inspection': False, 'scale': deepcopy(scale),
                'provenance_refs': [content_identity(self.basis)], 'authority_requirements': ['read:recording'],
                'invariants': ['Original capture and covariance remain unchanged'],
                'validity_conditions': ['Hypothetical scenario; physical improvement is not established']}
        fixed = {'kind': 'FIXED', 'minimum': None, 'maximum': None, 'values': None, 'log_base': None}
        nodes = [node(0, 'basis', [], {'basis_ref': {'type': 'STRING', 'value': content_identity(self.basis),
                 'unit': None, 'exposed': False, 'domain': fixed}}),
                 node(1, 'hypothesis', ['basis'], {'offset_scale': {'type': 'NUMBER', 'value': offset_scale,
                 'unit': '1', 'exposed': True, 'domain': {'kind': 'RANGE', 'minimum': 0,
                     'maximum': 0.99, 'values': None, 'log_base': None}}}), node(2, 'audit', ['candidate'], {})]
        edges = [{'edge_id': a + '-' + b, 'kind': 'DATAFLOW', 'source_node_id': a, 'target_node_id': b,
                  'source_socket': 'result', 'target_socket': port, 'relation': 'retained_prerequisite', 'metadata': {}}
                 for a, b, port in [('basis', 'hypothesis', 'basis'), ('hypothesis', 'audit', 'candidate')]]
        return board_from_spec({'board_id': PROFILE, 'title': 'GaugeLink uncertainty scenario',
            'model_id': self.source['instrument'], 'nodes': nodes, 'edges': edges, 'groups': [],
            'board_invariants': ['Original evidence and acceptance remain independent of this proposal'],
            'notes': 'Retained Station scalar investigation; no acquisition or instrument commands'})

    def preflight(self, board, compilation, source):
        if source != self.source or runtime_identity() != self._runtime:
            raise ValueError('GaugeLink source or runtime binding differs')
        validate_basis(source, self.basis)
        checked = validate_board(board)
        nodes = {n['node_id']: n for n in checked['nodes']}
        value = nodes['hypothesis']['parameters']['offset_scale']['value']
        expected = self.board()
        if value != 0.5:
            from .visual_board import apply_parameter_edit
            expected = apply_parameter_edit(expected, {'schema': 'ciw.board-parameter-edit.v1',
                'base_board_ref': expected['record_digest'], 'node_id': 'hypothesis',
                'parameter': 'offset_scale', 'replacement': value}, self.semantic(self.registry()))['candidate_board']
        if checked != expected:
            raise ValueError('GaugeLink profile permits only its bounded offset scale')
        if compilation != compile_board(checked, self.semantic(self.registry())):
            raise ValueError('GaugeLink compilation differs from the pinned provider')

    def session(self, output, registry, workspace=None):
        # Restore exact old occurrences; Session validation is not provider execution.
        workspace = deepcopy(self.original if workspace is None else workspace)
        self.check_workspace(workspace, None)
        path = Path(output).parent / 'session-input.json'
        save_new(path, workspace)
        session = Session.from_workspace(path, output)
        session.operations = registry.operations
        return session

    def check_workspace(self, workspace, run):
        if workspace.get('run') != self.source:
            raise ValueError('Workspace source differs from the retained investigation')
        for field, key in [('results', 'result_id'), ('executions', 'execution_id')]:
            retained = {item[key]: item for item in workspace[field]}
            if len(retained) != len(workspace[field]):
                raise ValueError('Duplicate retained occurrence')
            for original in self.original[field]:
                if retained.get(original[key]) != original:
                    raise ValueError('Original scalar occurrence was changed or omitted')
        if run is not None:
            for outcome in run['nodes'].values():
                if outcome.get('status') == 'completed':
                    result = outcome['result']
                    if result['runtime'] != self.identity():
                        raise ValueError('Scenario provider identity differs')
                    if result['operation_id'] == OPS[0] and result['data'] != self.basis:
                        raise ValueError('Retained evidence basis differs')


def plan_for(bundle, *, instance_id, max_standard_uncertainty):
    request, _, original, _ = pinwheel._original(measurements.validate(bundle))
    n = len(study.decode(request)['rows'])
    return pinwheel.plan_for(bundle, instance_id=instance_id, max_standard_uncertainty=max_standard_uncertainty,
        candidates=[{'id': 'offset-scenario', 'label': 'Declared offset uncertainty scenario',
                     'kind': 'covariance_scale_counterfactual', 'scales': [1] * (n + 1) + [0.5],
                     'rationale': 'Hypothetical amplitude reduction; requires independent experimental evidence'}])
