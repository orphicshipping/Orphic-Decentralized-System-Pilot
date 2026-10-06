"""Bounded intent compiler for EXISTING NET production plans and Link graphs.

No scheduler, numerical model, driver loader, plant authority or claim of optimal
planning. Host selects one of two installed profiles. Saved intent is data, not
permission. Physical evidence obligations cannot be discharged by declarations.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from . import link_contract as c, production
from .control_contracts import bytes_ref
from .control_plane import plan_graph
from .operations.runner import seal, check_seal

INTENT = 'ciw.engineering-intent.v1'
COMPILATION = 'ciw.engineering-compilation.v1'
GATE = 'engineering.evidence-obligation.v1'
MAX_INTENT = 32768
PROFILES = ('installation', 'scalar')
OBLIGATIONS = ('reference_traceability', 'timing_qualification', 'uncertainty_budget',
               'independent_validation', 'product_safety_emc')
SCOPE = {'physical_qualification': 'HELD', 'standards_conformance': 'NOT_ASSESSED',
         'hardware_access': False, 'network_access': False, 'part_release': False,
         'model_promotion': False, 'training_admission': False, 'verification_id': None}


def _same(a, b):
    return c.canonical_json(a) == c.canonical_json(b)


def profile(name):
    """Explicit installed host choice; never imports a path from saved data."""
    if name == 'installation':
        from . import link_installation as module
        return module, ('reference_paired_installation',), ('response',), module.MAX_BYTES
    if name == 'scalar':
        from . import link_study as module
        return module, ('link_scalar_study',), ('decode', 'calibration', 'model', 'audit'), module.MAX_BYTES
    raise ValueError('Unsupported host-selected engineering profile')


def _validate_requirement(item, name):
    c.keys(item, {'requirement_id', 'predicate', 'parameters'})
    c.text(item['requirement_id'])
    pred, params = item['predicate'], item['parameters']
    c.text(pred)
    allowed = {'sampled_points'} if name == 'installation' else {'arithmetic_audit', 'nominal_comparison'}
    if pred not in allowed:
        raise ValueError('Requirement has no installed predicate for this profile')
    if pred == 'sampled_points':
        c.keys(params, {'frequencies_hz'})
        values = params['frequencies_hz']
        if type(values) is not list or not 1 <= len(values) <= 32:
            raise ValueError('Declare 1..32 distinct sampled-frequency requirements')
        for value in values:
            if c.finite(value) <= 0:
                raise ValueError('Required frequencies must be finite positive numbers')
        if len(set(values)) != len(values):
            raise ValueError('Duplicate frequency requirement')
    else:
        c.keys(params, set())


def validate_intent(value, name):
    profile(name)
    c.bounded(value, MAX_INTENT)
    c.keys(value, {'schema', 'intent_id', 'revision', 'profile', 'request_ref', 'requirements', 'constraints'})
    if value['schema'] != INTENT or value['profile'] != name:
        raise ValueError('Intent and explicitly selected host profile differ')
    c.text(value['intent_id']); c.integer(value['revision'], minimum=1, maximum=2**31-1)
    c.content_ref(value['request_ref'])
    constraints = value['constraints']
    c.keys(constraints, {'read_only', 'max_operations'})
    if constraints['read_only'] is not True:
        raise ValueError('Only retained-data computation is supported; no physical dispatch')
    c.integer(constraints['max_operations'], minimum=1, maximum=8)
    reqs = value['requirements']
    if type(reqs) is not list or not 1 <= len(reqs) <= 8:
        raise ValueError('Declare a bounded nonempty requirement set')
    for item in reqs:
        _validate_requirement(item, name)
    if len({r['requirement_id'] for r in reqs}) != len(reqs):
        raise ValueError('Duplicate requirement identity')
    if any(r['requirement_id'].startswith('industrial.') for r in reqs):
        raise ValueError('Industrial evidence requirement IDs are reserved')
    expected = {'sampled_points'} if name == 'installation' else {'arithmetic_audit', 'nominal_comparison'}
    if {r['predicate'] for r in reqs} != expected:
        raise ValueError('The profile baseline requirements may not be omitted')
    return deepcopy(value)


def example_intent(name, request_ref):
    """Authored example objectives, not Notation hardware specifications."""
    profile(name); c.content_ref(request_ref)
    reqs = ([{'requirement_id': 'sampled-response', 'predicate': 'sampled_points',
              'parameters': {'frequencies_hz': [32.0, 64.0, 384.0]}}]
            if name == 'installation' else
            [{'requirement_id': 'independent-arithmetic', 'predicate': 'arithmetic_audit', 'parameters': {}},
             {'requirement_id': 'nominal-limit', 'predicate': 'nominal_comparison', 'parameters': {}}])
    return validate_intent({'schema': INTENT, 'intent_id': 'synthetic.'+name+'.engineering',
        'revision': 1, 'profile': name, 'request_ref': request_ref, 'requirements': reqs,
        'constraints': {'read_only': True, 'max_operations': 1 if name == 'installation' else 4}}, name)


def _request(raw, name):
    module, _, _, limit = profile(name)
    value = c.loads(raw, limit)
    if name == 'installation':
        module.unpack_request(value)
    else:
        module.validate_request(value)
    return value


def decision_basis(request, name):
    """A revision may change evidence, not silently relax the decision problem."""
    module = profile(name)[0]
    if name == 'installation':
        plan = module.unpack_request(request)[0]
        return {'analysis': deepcopy(plan['analysis']), 'unit': module.mount.UNIT, 'frame': module.mount.FRAME}
    channel = c.channel_for(request['capture']['manifest'], request['channel_id'])
    return {'model': deepcopy(request['model']), 'quantity': channel['quantity'],
            'unit': channel['unit'], 'frame': channel['frame']}


def compiler_identity():
    # Function implementation identities, not a claim of full dependency attestation.
    from . import control_plane, control_contracts, production_gates
    modules = (Path(__file__), Path(production.__file__), Path(control_plane.__file__),
               Path(control_contracts.__file__), Path(production_gates.__file__))
    return {'provider': 'ciw.engineering_thread', 'version': '1',
            'files': {p.name: bytes_ref(p.read_bytes().replace(b'\r\n', b'\n')) for p in modules},
            'planning': 'finite_installed_recipe_not_optimal_search'}


def gate_bindings():
    def validate(policy):
        c.keys(policy, {'profile', 'request_ref', 'predicate', 'parameters'})
        profile(policy['profile']); c.content_ref(policy['request_ref'])
        if policy['predicate'] in OBLIGATIONS:
            c.keys(policy['parameters'], set())
        else:
            _validate_requirement({'requirement_id': 'bound-check', 'predicate': policy['predicate'],
                                   'parameters': policy['parameters']}, policy['profile'])

    def evaluate(result, policy):
        validate(policy)
        pred = policy['predicate']
        if result is None:
            return {'status': 'INDETERMINATE', 'detail': 'missing_retained_result'}
        # No arbitrary field selector, file lookup, code import or truth flag.
        if pred in OBLIGATIONS:
            return {'status': 'INDETERMINATE', 'detail': {
                'obligation': pred, 'reason': 'independent_evidence_not_admitted_by_this_profile',
                'declaration_is_not_qualification': True}}
        data = result['data']
        if pred == 'sampled_points':
            module = profile('installation')[0]
            if result['operation_id'] != module.OPERATION:
                raise ValueError('Frequency predicate must inspect the original response operation')
            if data['status'] != 'RETAINED_RESPONSE_PREVIEW' or data['response'] is None:
                return {'status': 'INDETERMINATE', 'detail': 'source_review_or_response_unavailable'}
            rows = data['response']['frequencies']
            evidence = []
            states = []
            for f in policy['parameters']['frequencies_hz']:
                matches = [r for r in rows if r['frequency_hz'] == f]
                if len(matches) != 1:
                    states.append('INDETERMINATE')
                    evidence.append({'frequency_hz': f, 'status': 'NOT_RESOLVED'})
                    continue
                r = matches[0]
                good = f in data['response']['passing_sampled_frequencies_hz']
                # A missing prerequisite is not a known failed engineering response.
                known = (r['observations']['relative_gain']['value'] is not None and
                         r['observations']['relative_phase']['value'] is not None and
                         not any(x in r['reasons'] for x in ('CHAIN_RESPONSE_UNCHARACTERIZED', 'TIMING_BOUND_UNKNOWN')))
                state = 'PASS' if good else 'FAIL' if known else 'INDETERMINATE'
                states.append(state)
                evidence.append({'frequency_hz': f, 'status': state, 'point': deepcopy(r)})
            status = 'FAIL' if 'FAIL' in states else 'PASS' if all(s == 'PASS' for s in states) else 'INDETERMINATE'
            return {'status': status, 'detail': {'sampled_points': evidence, 'continuous_band_claim': False}}
        from .production_gates import builtin_gates
        module = profile('scalar')[0]
        expected_op = module.OPS[3] if pred == 'arithmetic_audit' else module.OPS[2]
        if result['operation_id'] != expected_op:
            raise ValueError('Scalar predicate targets the wrong operation')
        selected = {'path': ['status'], 'expected': 'PASS'} if pred == 'arithmetic_audit' else {
            'path': ['nominal_comparison'], 'expected': 'WITHIN'}
        return builtin_gates()['result.equals.v1'].evaluate(result, selected)

    return {GATE: production.Gate(GATE, compiler_identity(), validate, evaluate)}


def _recipe(intent, source):
    name = intent['profile']; module, capabilities, nodes, _ = profile(name)
    checks = []
    for r in intent['requirements']:
        node = 'model' if r['predicate'] == 'nominal_comparison' else nodes[-1]
        checks.append({'check_id': r['requirement_id'], 'node_id': node, 'gate_id': GATE,
            'policy': {'profile': name, 'request_ref': intent['request_ref'],
                       'predicate': r['predicate'], 'parameters': deepcopy(r['parameters'])}})
    for obligation in OBLIGATIONS:
        checks.append({'check_id': 'industrial.'+obligation, 'node_id': nodes[-1], 'gate_id': GATE,
            'policy': {'profile': name, 'request_ref': intent['request_ref'],
                       'predicate': obligation, 'parameters': {}}})
    return production.plan('engineering.'+c.content_identity(intent)[7:31],
        project_id=intent['intent_id'], source_evidence_id=source['evidence_id'], jobs=[{
            'job_id': 'assess', 'worker_id': 'retained-analysis', 'requires': list(capabilities),
            'depends_on': [], 'attempts': [module.graph()], 'checks': checks}])


def _continuation(value, revision):
    if value is None:
        if revision != 1:
            raise ValueError('Later intent revisions require a reviewed predecessor proposal')
        return None
    c.keys(value, {'previous_thread_ref', 'previous_intent_ref', 'previous_execution_ids',
                   'previous_result_ids', 'reason', 'basis'})
    if revision <= 1 or value['basis'] != 'locally_reviewed_predecessor_not_signed_authority':
        raise ValueError('Invalid continuation provenance')
    for name in ('previous_thread_ref', 'previous_intent_ref'): c.content_ref(value[name])
    c.text(value['reason'], maximum=1024)
    from .core.identities import validate_identity
    for name, kind in (('previous_execution_ids', 'execution'), ('previous_result_ids', 'result')):
        ids = value[name]
        if type(ids) is not list or not 0 <= len(ids) <= 8 or len(set(ids)) != len(ids):
            raise ValueError('Invalid bounded prior occurrence references')
        for identity in ids: validate_identity(identity, kind)
    return deepcopy(value)


def compile_intent(intent_raw, request_raw, *, host_profile, registry=None, continuation=None):
    """Lower intent to the original plan/DAG; never execute an operation."""
    intent = validate_intent(c.loads(intent_raw, MAX_INTENT), host_profile)
    continuation = _continuation(continuation, intent['revision'])
    if c.bytes_ref(request_raw) != intent['request_ref']:
        raise ValueError('Intent names different original request bytes')
    request = _request(request_raw, host_profile)
    module, _, _, _ = profile(host_profile)
    source = module.make_source(request, intent['request_ref'])
    reg = module.registry() if registry is None else registry
    graph = module.graph()
    planning = plan_graph(graph, reg)
    workers = (production.Worker('retained-analysis', tuple(n['operation_id'] for n in graph['nodes'])),)
    value = _recipe(intent, source)
    bindings = production.preflight(value, reg, workers, gate_bindings(),
                                    max_operations=intent['constraints']['max_operations'])
    return seal({'schema': COMPILATION, 'intent_ref': c.bytes_ref(intent_raw),
        'request_ref': intent['request_ref'], 'profile': host_profile, 'source_evidence_id': source['evidence_id'],
        'intent_id': intent['intent_id'], 'revision': intent['revision'], 'plan': value, 'bindings': bindings,
        'typed_plan': planning, 'decision_basis': decision_basis(request, host_profile),
        'compiler': compiler_identity(), 'continuation': continuation,
        'stop_policy': 'no_automatic_retry_or_physical_dispatch',
        'scope': deepcopy(SCOPE), 'authorizes_execution': False})


def check_compilation(value, intent_raw, request_raw, *, host_profile, current_runtime=False):
    """Historical data check; only execution requires today's identical bindings."""
    check_seal(value)
    c.keys(value, {'schema', 'intent_ref', 'request_ref', 'profile', 'source_evidence_id', 'intent_id',
        'revision', 'plan', 'bindings', 'typed_plan', 'decision_basis', 'compiler', 'continuation', 'stop_policy',
        'scope', 'authorizes_execution', 'record_digest'})
    intent = validate_intent(c.loads(intent_raw, MAX_INTENT), host_profile)
    continuation = _continuation(value['continuation'], intent['revision'])
    req = _request(request_raw, host_profile)
    module = profile(host_profile)[0]
    source = profile(host_profile)[0].make_source(req, c.bytes_ref(request_raw))
    expected = {'schema': COMPILATION, 'intent_ref': c.bytes_ref(intent_raw), 'request_ref': c.bytes_ref(request_raw),
        'profile': host_profile, 'source_evidence_id': source['evidence_id'], 'intent_id': intent['intent_id'],
        'revision': intent['revision'], 'plan': _recipe(intent, source), 'decision_basis': decision_basis(req, host_profile),
        'compiler': compiler_identity(), 'continuation': continuation,
        'typed_plan': plan_graph(module.graph(), module.registry(bind=False)),
        'stop_policy': 'no_automatic_retry_or_physical_dispatch',
        'scope': SCOPE, 'authorizes_execution': False}
    if intent['request_ref'] != c.bytes_ref(request_raw) or any(not _same(value[k], v) for k, v in expected.items()):
        raise ValueError('Compiled plan differs from exact intent, source, obligations or compiler')
    # Bind historical planning limits to the actual intent as well. The original
    # production reader separately checks the full execution/contract history.
    bindings = value['bindings']; check_seal(bindings)
    c.keys(bindings, {'schema', 'workers', 'contracts', 'gates', 'order', 'reserved_operations',
                      'max_operations', 'authorizes_execution', 'record_digest'})
    graph = module.graph()
    fixed = {'schema': 'ciw.production-bindings.v1', 'workers': {'retained-analysis':
                 [node['operation_id'] for node in graph['nodes']]},
             'gates': {GATE: compiler_identity()}, 'order': ['assess'],
             'reserved_operations': len(graph['nodes']),
             'max_operations': intent['constraints']['max_operations'], 'authorizes_execution': False}
    if any(not _same(bindings[k], v) for k, v in fixed.items()):
        raise ValueError('Retained binding exceeds the intent budget, worker or gate contract')
    advertised = module.registry(bind=False)
    c.keys(bindings['contracts'], set(fixed['workers']['retained-analysis']))
    for operation, contract in bindings['contracts'].items():
        expected_contract = advertised.contract(operation)
        c.keys(contract, set(expected_contract))
        if any(not _same(contract[k], v) for k, v in expected_contract.items() if k != 'runtime'):
            raise ValueError('Retained capability signature differs from the installed recipe')
        if type(contract['runtime']) is not dict or not contract['runtime']:
            raise ValueError('Historical runtime binding is missing')
    # The original production reader validates historical contracts and gates.
    # Do not mistake an old runtime binding for an executable one on this host.
    if current_runtime and not _same(value, compile_intent(intent_raw, request_raw, host_profile=host_profile, continuation=continuation)):
        raise ValueError('Current capabilities or runtime differ; compile a new reviewed plan')
    return intent, source
