"""Connect existing native workflows without replacing their execution contracts.

Provider paths are operator profile bindings. Retained requests cannot choose
imports, repositories or policies. Missing independent comparisons stay held.
"""
from copy import deepcopy
from pathlib import Path
import tempfile

from .agent_api import encode, parse
from .control_contracts import keys, record
from .core.identities import content_identity
from . import link_contract as c

ENGINES = frozenset({'scalar', 'calibrated-window', 'residual-monitor'})


def request(raw, engine):
    if engine == 'scalar':
        from .link_study import validate_request
        validate_request(parse(raw))
    elif engine == 'calibrated-window':
        from .calibrated_window import _source
        _source(raw)
    elif engine == 'residual-monitor':
        from .residual_monitor import _source, _request
        value=parse(raw); keys(value,{'source','upstreams'})
        source_raw=c.unpack(value['source'],262144)
        _request(_source(source_raw),value['upstreams'])
    else: raise ValueError('Unsupported native engineering adapter')


def unit(raw,engine):
    if engine=='scalar':
        value=parse(raw)
        return c.channel_for(value['capture']['manifest'],value['channel_id'])['unit']
    if engine=='calibrated-window': return parse(raw)['calibration_profile']['output_unit']
    value=parse(raw)
    from .residual_monitor import _source,_request
    return _request(_source(c.unpack(value['source'],262144)),value['upstreams'])['scope']['measurement_units'][0]


def binds(raw, engine, selected):
    request(raw,engine)
    if engine == 'scalar': return encode(parse(raw)['capture']) == selected
    if engine == 'calibrated-window': return raw == selected
    return c.unpack(parse(raw)['source'],262144) == selected


def roles(engine):
    if engine == 'calibrated-window':
        from .calibrated_window import ROLES
        return set(ROLES)
    if engine == 'residual-monitor':
        from .residual_monitor import ROLES
        return set(ROLES)
    return set()


def execute(raw, engine, providers, *, process_policies=None):
    request(raw,engine)
    if engine == 'scalar':
        from .link_study_io import run
        with tempfile.TemporaryDirectory(prefix='ncp-operational-scalar-') as temp:
            root=Path(temp)/'native'
            run(parse(raw),root,raw=raw)
            return {'workspace':parse((root/'workspace.json').read_bytes()),
                    'graph':parse((root/'graph.json').read_bytes())}
    selected=providers.get(engine,{})
    if set(selected) != roles(engine):
        raise ValueError('Native provider stack unavailable; bind exact role-named repositories in the operator profile')
    from .companion_policy_config import bind_process_policies, policy_set
    policies = bind_process_policies(selected, policy_set({} if process_policies is None else process_policies, engine))
    if engine == 'calibrated-window':
        from .calibrated_window import create_session
        return create_session(raw,selected,process_policies=policies)
    from .residual_monitor import ResidualMonitorWorkflow
    value=parse(raw)
    return ResidualMonitorWorkflow().create_session(c.unpack(value['source'],262144),value['upstreams'],selected,
                                                   process_policies=policies)


def validate(raw, engine, native):
    """Native offline validation; never invoke a provider or allocate execution."""
    if engine == 'scalar':
        from .link_study_io import read
        keys(native,{'workspace','graph'})
        with tempfile.TemporaryDirectory(prefix='ncp-operational-inspect-') as temp:
            root=Path(temp)
            (root/'request.json').write_bytes(raw)
            (root/'workspace.json').write_bytes(encode(native['workspace']))
            (root/'graph.json').write_bytes(encode(native['graph']))
            read(root,expected_request_ref=c.bytes_ref(raw))
    elif engine == 'calibrated-window':
        from .calibrated_window import _validate
        if _validate(native)!=raw: raise ValueError('Native calibrated-window source differs')
    else:
        from .residual_monitor import ResidualMonitorWorkflow
        value=parse(raw)
        if ResidualMonitorWorkflow()._validate(native)!=c.unpack(value['source'],262144):
            raise ValueError('Native monitor source differs')
        if native['upstream_windows']!=value['upstreams']: raise ValueError('Native monitor upstream evidence differs')


def metadata(raw, engine, native):
    if engine == 'scalar':
        from .link_study import OPS
        results={v['operation_id']:v for v in native['workspace']['results']}
        summary={op:deepcopy(results[op]['data']) for op in OPS}
        request_value=parse(raw)
        model=request_value['model']
        comparison_scope='declared exact mean reference; conditional shared covariance; physical qualification held'
    else:
        model=parse(raw).get('configuration',{}) if engine=='calibrated-window' else parse(c.unpack(parse(raw)['source'],262144))['configuration']
        summary={'steps':[{'operation_id':s['operation_id'],'execution_id':s['execution_id'],
            'result_id':s['result_id'],'numerical_result':deepcopy(s['numerical_result'])} for s in native['steps']],
            'runtimes':deepcopy(native['runtimes']),'verification':deepcopy(native.get('verification'))}
        comparison_scope='native conditional estimation/monitoring; independent held-out paired reference not supplied'
    return {'id':content_identity(model),'parameters':model,'validity_domain':comparison_scope,
        'prediction_mode':'native_retained_workflow','held_out':'not_established',
        'fitting_evidence':'native declarations; no independent fitting claim',
        'comparison_evidence':content_identity(native),'native_results':summary}


def comparison(raw, engine, native, policy):
    # Preserve native estimates/residuals above. They are not independent measured
    # references. Empty streams make that missing evidence explicit in the shared
    # guarded comparator rather than inventing a total covariance or observation.
    from .reference_comparison import compare, POLICY_SCHEMA
    left=record('observation-stream',observations=[])
    right=record('observation-stream',observations=[])
    p={'schema':POLICY_SCHEMA,'policy_id':content_identity([engine,policy]),'source_kind':'retained_observation',
       'left_ref':content_identity(left),'right_ref':content_identity(right),'unit':policy['unit'],
       'coverage_factor':policy['coverage_factor'],'max_abs_error':policy['max_abs_error'],
       'max_standard_uncertainty':policy['max_standard_uncertainty'],
       'uncertainty':{'status':'unknown','scope':'conditional_only','relationship':'unknown','joint_covariance':None,
           'justification':'Native estimates/residuals are retained; an independent paired reference and total joint comparison budget are not supplied.',
           'budget_ref':None}}
    return left,right,compare(left,right,p)
