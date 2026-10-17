"""Bounded numerical request contracts; supplied models are not acquired evidence."""
from __future__ import annotations

import math
from .core.covariance import validate_covariance_artifact
from .core.identities import canonical_json

OPERATIONS = {
    'uncertainty.propagate.v1': 'Propagate a supplied Jacobian and full covariance at a declared operating point.',
    'uncertainty.budget.v1': 'Separate diagonal variance terms from signed pairwise covariance contributions.',
    'calibration.fit.v1': 'Fit an affine exact-x model with known absolute correlated y covariance.',
    'calibration.residuals.v1': 'Assess held-out affine residuals with coefficient and observation uncertainty.',
    'sensitivity.local.v1': 'Scale an explicitly supplied local Jacobian without inventing derivatives.',
    'observability.rank.v1': 'Inspect scaled finite-horizon discrete linear observability by SVD.',
    'model.discrepancy.v1': 'Compare aligned predictions and observations using their full joint covariance.',
}
MAX_DIM = 32
MAX_SAMPLES = 256
MAX_BYTES = 512 * 1024
COMMON = {'source_evidence_id', 'assumptions'}


def keys(value, required):
    if type(value) is not dict or set(value) != set(required):
        raise ValueError('Require exactly the declared numerical contract fields')


def text(value):
    if type(value) is not str or not value.strip() or len(value) > 256:
        raise ValueError('Require nonempty bounded text')
    return value


def scalar(value, *, positive=False):
    if type(value) not in (int, float):
        raise ValueError('Require a finite JSON number, not a boolean')
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError('Numerical input exceeds binary64') from exc
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError('Require finite numbers and strictly positive scales')
    return value


def vector(value, length, *, positive=False):
    if type(value) is not list or len(value) != length:
        raise ValueError('Vector does not match its declared axis')
    for item in value:
        scalar(item, positive=positive)


def matrix(value, rows, columns):
    if type(value) is not list or len(value) != rows:
        raise ValueError('Matrix does not match its declared axes')
    for row in value:
        vector(row, columns)


def labels(value, *, limit=MAX_DIM, unique=True):
    if type(value) is not list or not 1 <= len(value) <= limit:
        raise ValueError('Axis exceeds bounds or is empty')
    for item in value:
        text(item)
    if unique and len(set(value)) != len(value):
        raise ValueError('Axis labels must be unique and ordered')
    return len(value)


def axis(value, *, limit=MAX_DIM):
    keys(value, {'quantity_ids', 'units', 'frame', 'reference_values'})
    size = labels(value['quantity_ids'], limit=limit)
    if labels(value['units'], limit=limit, unique=False) != size:
        raise ValueError('Units must match axis order')
    vector(value['reference_values'], size)
    text(value['frame'])
    return size


def covariance(value, expected, *, evidence=None):
    # Bound allocations before invoking the shared covariance validator.
    size = axis(expected, limit=2*MAX_SAMPLES)
    if type(value) is not dict:
        raise ValueError('Require a covariance artifact')
    matrix(value.get('matrix'), size, size)
    validate_covariance_artifact(value, expected_quantity_ids=expected['quantity_ids'],
        expected_units=expected['units'], expected_frame=expected['frame'])
    if value['reference_values'] != expected['reference_values']:
        raise ValueError('Covariance operating point differs from the declared model')
    if evidence is not None and evidence not in value['provenance']['source_evidence_ids']:
        raise ValueError('Covariance does not declare the source evidence')


def tolerance(value):
    if not 1e-15 <= scalar(value) <= 1e-2:
        raise ValueError('rcond must lie in [1e-15, 1e-2]')


def sample_axis(request, values):
    return dict(quantity_ids=request['sample_ids'], units=[request['y_unit']]*len(values),
                frame=request['frame'], reference_values=values)


def coefficient_axis(request, values):
    return dict(quantity_ids=['offset', 'slope'], units=[request['y_unit'], request['y_unit']+'/'+request['x_unit']],
                frame=request['frame'], reference_values=values)


def validate_request(operation, request, evidence):
    if operation not in OPERATIONS or type(request) is not dict:
        raise ValueError('Unknown numerical operation or invalid request')
    if len(canonical_json(request).encode()) > MAX_BYTES:
        raise ValueError('Numerical request exceeds 512 KiB')
    if request.get('source_evidence_id') != evidence:
        raise ValueError('Request source evidence does not match the retained run')
    labels(request.get('assumptions'), unique=False)
    if operation.startswith(('uncertainty.', 'sensitivity.')):
        extra = {'covariance'} if operation.startswith('uncertainty.') else {'input_scales', 'output_scales'}
        keys(request, COMMON | {'input','output','jacobian'} | extra)
        n, m = axis(request['input']), axis(request['output'])
        matrix(request['jacobian'], m, n)
        if 'covariance' in extra:
            covariance(request['covariance'], request['input'], evidence=evidence)
        else:
            vector(request['input_scales'], n, positive=True)
            vector(request['output_scales'], m, positive=True)
    elif operation.startswith('calibration.'):
        extra = {'rcond'} if operation == 'calibration.fit.v1' else {'training_sample_ids','independence','coefficients'}
        keys(request, COMMON | {'model','x','y','sample_ids','x_unit','y_unit','frame','observation_covariance'} | extra)
        n = labels(request['sample_ids'], limit=MAX_SAMPLES)
        vector(request['x'], n)
        vector(request['y'], n)
        for key in ('x_unit','y_unit','frame'): text(request[key])
        if request['model'] != 'affine_exact_x':
            raise ValueError('v1 calibration supports only an affine model with exact x')
        covariance(request['observation_covariance'], sample_axis(request, request['y']), evidence=evidence)
        if operation == 'calibration.fit.v1':
            if n < 3 or min(request['x']) == max(request['x']):
                raise ValueError('Affine fit requires at least three samples and nonconstant x')
            tolerance(request['rcond'])
        else:
            labels(request['training_sample_ids'], limit=MAX_SAMPLES)
            if set(request['training_sample_ids']) & set(request['sample_ids']):
                raise ValueError('Held-out samples overlap the declared training identities')
            if request['independence'] != 'held_out_observations_independent_of_coefficients':
                raise ValueError('Declare held-out observation/coefficient independence explicitly')
            coeff = request['coefficients']
            if type(coeff) is not dict:
                raise ValueError('Require a coefficient covariance artifact')
            covariance(coeff, coefficient_axis(request, coeff.get('reference_values')))
            metadata = coeff['provenance'].get('metadata', {})
            if metadata.get('training_sample_ids') != request['training_sample_ids']:
                raise ValueError('Coefficient provenance differs from declared training sample identities')
    elif operation == 'observability.rank.v1':
        keys(request, COMMON | {'states','outputs','transition','observation','state_scales','output_scales',
                               'horizon','sample_period_s','rcond'})
        n, m = axis(request['states']), axis(request['outputs'])
        matrix(request['transition'], n, n)
        matrix(request['observation'], m, n)
        vector(request['state_scales'], n, positive=True)
        vector(request['output_scales'], m, positive=True)
        if type(request['horizon']) is not int or not 1 <= request['horizon'] <= 32:
            raise ValueError('Observability horizon must be an integer in [1,32]')
        scalar(request['sample_period_s'], positive=True)
        tolerance(request['rcond'])
    else:
        keys(request, COMMON | {'sample_ids','observed','predicted','unit','frame','joint_covariance'})
        n = labels(request['sample_ids'], limit=MAX_SAMPLES//2)
        vector(request['observed'], n)
        vector(request['predicted'], n)
        text(request['unit']); text(request['frame'])
        expected = dict(quantity_ids=[prefix+name for prefix in ('observed:','predicted:') for name in request['sample_ids']],
            units=[request['unit']]*(2*n), frame=request['frame'],
            reference_values=request['observed']+request['predicted'])
        covariance(request['joint_covariance'], expected, evidence=evidence)


def parameters_request(operation, run, parameters):
    keys(parameters, {'request'})
    request = parameters['request']
    validate_request(operation, request, run['evidence_id'])
    return request
