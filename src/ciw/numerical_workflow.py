"""Seven built-in operations on the existing Session/graph/MCP execution path."""
from copy import deepcopy
import numpy as np

from .adapters.protocol import AdapterRefusal, InstrumentManifest
from .core.identities import content_identity
from .core.records import finite_tree
from .numerical_contracts import OPERATIONS, parameters_request
from .numerical_runtime import runtime_identity


def manifest():
    return InstrumentManifest(instrument_id='ciw.numerical',role='backend',
        inputs=('declared_numerical_request',),outputs=('ciw.numerical-result.v1',),
        supported_operations=tuple(OPERATIONS),
        normalization={'implicit_unit_conversion':False,'covariance_repair':False},
        determinism={'method':'binary64_bounded_linear_algebra'},
        tolerance_policy={'covariance':'existing covariance-artifact.v1 validation',
                          'rank':'caller-declared rcond in [1e-15,1e-2]'},
        calibration_requirements={'physical_qualification':'not_established'})


def execute(operation, run, parameters, *, backend=None):
    if backend is not None:
        from .rust_numerical import RustNumericalBackend
        if type(backend) is not RustNumericalBackend:
            raise TypeError('host_selected_numerical_backend_required')
        return backend.execute(operation, run, parameters)
    from .numerical_kernels import calculate
    from .numerical_records import validate_payload
    request = deepcopy(parameters_request(operation,run,parameters))
    try:
        result = calculate(operation,request)
        data = {'schema':'ciw.numerical-result.v1','operation_id':operation,
                'request_digest':content_identity(request),'source_evidence_id':run['evidence_id'],
                'scope':'declared_model_only','result':result}
        finite_tree(data,'numerical result')
        validate_payload(operation,data,run,parameters,None)
        return data
    except (FloatingPointError,OverflowError,np.linalg.LinAlgError) as exc:
        raise AdapterRefusal('numerical_domain_error', 'Numerical computation exceeded its valid finite domain; no repair applied') from exc


def operations(*, backend=None):
    if backend is not None:
        from .rust_numerical import RustNumericalBackend
        if type(backend) is not RustNumericalBackend:
            raise TypeError('host_selected_numerical_backend_required')
        return backend.operations()
    from .operations.registry import Operation
    return [Operation(name,'calibration' if name == 'calibration.fit.v1' else 'backend',
        lambda run,parameters,op=name: execute(op,run,parameters),runtime_identity) for name in OPERATIONS]
