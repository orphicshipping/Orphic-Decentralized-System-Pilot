"""Conditional rheology uncertainty calculations on the existing covariance contract.

No acquisition, calibration authentication, inferred independence or p-value.
Missing covariance produces HELD, never a diagonal replacement.
"""
from copy import deepcopy
import math
import numpy as np
from .control_contracts import keys, text, number, content_ref, detached
from .core.identities import content_identity
from .core.covariance import validate_covariance_artifact, create_covariance_artifact

SCHEMA = 'ciw.materials-uncertainty-request.v1'
AUTHORITY = {'physical_validation': False, 'calibration_authenticated': False,
             'coverage_validated': False, 'state_admission': False,
             'hardware_actuation': False, 'part_release': False}


def validate_request(request):
    p = detached(request)
    keys(p, {'schema', 'analysis', 'observation', 'covariance', 'reference', 'criterion'})
    if p['schema'] != SCHEMA or p['analysis'] not in ('complex-viscosity', 'joint-residual'):
        raise ValueError('Unsupported uncertainty profile')
    o = p['observation']
    keys(o, {'source_kind', 'subject', 'conditions', 'quantity_ids', 'units', 'values', 'source_refs'})
    if o['source_kind'] not in ('synthetic-fixture', 'operator-declared'):
        raise ValueError('This input is a declaration, not authenticated native measurement')
    text(o['subject']); text(o['conditions'])
    n = len(o['values']) if type(o['values']) is list else 0
    if not 1 <= n <= 32:
        raise ValueError('Require 1..32 declared quantities')
    for field in ('quantity_ids', 'units'):
        if type(o[field]) is not list or len(o[field]) != n:
            raise ValueError('Quantity axes and units must match values')
        for value in o[field]: text(value)
    if len(set(o['quantity_ids'])) != n:
        raise ValueError('Quantity identities must be unique')
    for value in o['values']: number(value)
    if type(o['source_refs']) is not list or not o['source_refs'] or len(o['source_refs']) > 32:
        raise ValueError('Explicit source content references are required')
    for ref in o['source_refs']: content_ref(ref)
    if len(set(o['source_refs'])) != len(o['source_refs']):
        raise ValueError('Duplicate source references')
    if p['analysis'] == 'complex-viscosity':
        if (o['quantity_ids'] != ['shear-storage', 'shear-loss', 'angular-frequency']
                or o['units'] != ['Pa', 'Pa', 'rad/s']
                or any(x < 0 for x in o['values'][:2]) or o['values'][2] <= 0
                or p['reference'] is not None or p['criterion'] is not None):
            raise ValueError('Complex viscosity requires nonnegative Gprime/Gdoubleprime and positive omega, in Pa/Pa/rad/s')
    else:
        ref = p['reference']
        keys(ref, {'basis', 'values'})
        if ref['basis'] != 'exact-declared-reference' or type(ref['values']) is not list or len(ref['values']) != n:
            raise ValueError('This profile compares to an exact declared reference, not an uncertain fitted prediction')
        for value in ref['values']: number(value)
        if number(p['criterion']) <= 0:
            raise ValueError('A positive declared quadratic-form threshold is required')
    c = p['covariance']
    if c is not None:
        validate_covariance_artifact(c, expected_quantity_ids=o['quantity_ids'],
                                    expected_units=o['units'], expected_frame=o['conditions'])
        if c['reference_values'] != o['values'] or any(type(x) not in (int, float) for x in c['reference_values']):
            raise ValueError('Covariance reference values differ')
        if c['basis'] != {'kind': 'observation', 'id': content_identity(o)}:
            raise ValueError('Covariance must bind this exact observation declaration')
        if set(c['provenance']['source_evidence_ids']) != set(o['source_refs']):
            raise ValueError('Covariance ancestry differs from the supplied sources')
    return p


def analyze(request):
    p = validate_request(request)
    o, c = p['observation'], p['covariance']
    common = {'schema': 'ciw.materials-uncertainty-result.v1',
              'request_ref': content_identity(p), 'observation_ref': content_identity(o),
              'analysis': p['analysis'], 'source_kind': o['source_kind'],
              'authority': deepcopy(AUTHORITY), 'source_refs_authenticated': False}
    if c is None:
        return {**common, 'status': 'HELD', 'reason': 'joint-covariance-not-supplied', 'calculation': None}
    matrix = np.asarray(c['matrix'], dtype=float)
    if p['analysis'] == 'complex-viscosity':
        gp, gpp, omega = o['values']
        magnitude = math.hypot(gp, gpp)
        if magnitude == 0:
            return {**common, 'status': 'HELD', 'reason': 'zero-modulus-gradient-undefined', 'calculation': None}
        value = magnitude / omega
        gradient = [gp / magnitude / omega, gpp / magnitude / omega, -value / omega]
        variance = math.fsum(gradient[i] * c['matrix'][i][j] * gradient[j]
                             for i in range(3) for j in range(3))
        if not math.isfinite(value) or not math.isfinite(variance) or variance < 0:
            raise ValueError('Invalid propagated result; covariance is not repaired or clipped')
        output = create_covariance_artifact(matrix=[[variance]], quantity_ids=['complex-viscosity-magnitude'],
            units=['Pa*s'], frame=o['conditions'], reference_values=[value],
            method='first-order-joint-covariance-propagation',
            basis={'kind': 'estimated_state', 'id': content_identity(p)},
            provenance={'provider': 'ciw.materials_uncertainty', 'source_evidence_ids': o['source_refs'],
                        'source_covariance_ids': [c['covariance_id']]},
            assumptions=['First-order Taylor approximation; nonlinear coverage not established',
                         'Input covariance and source links are caller declarations, not authenticated calibration'])
        calculation = {'value': value, 'unit': 'Pa*s', 'standard_uncertainty': math.sqrt(variance),
                       'sensitivity': gradient, 'output_covariance': output,
                       'nonlinear_coverage': 'not-established'}
    else:
        # Normalize before factorization: mixed units cannot set rank tolerances.
        sigma = np.sqrt(np.diag(matrix))
        if np.any(sigma == 0):
            return {**common, 'status': 'HELD', 'reason': 'singular-joint-covariance', 'calculation': None}
        # Use the same symmetric scaling on both triangles, never average entries.
        denominator = np.outer(sigma, sigma)
        if not np.array_equal(matrix, matrix.T):
            raise ValueError('Joint solve requires exactly symmetric covariance; no repair')
        if np.any(denominator == 0) or not np.isfinite(denominator).all():
            return {**common, 'status': 'HELD', 'reason': 'joint-covariance-scaling-not-representable', 'calculation': None}
        corr = matrix / denominator
        try:
            if float(np.linalg.eigvalsh(corr).min()) <= 1e-12:
                return {**common, 'status': 'HELD', 'reason': 'singular-or-ill-conditioned-joint-covariance', 'calculation': None}
            factor = np.linalg.cholesky(corr)
            residual = np.asarray(o['values']) - np.asarray(p['reference']['values'])
            whitened = np.linalg.solve(factor, residual / sigma)
            score = float(whitened @ whitened)
        except np.linalg.LinAlgError as exc:
            raise ValueError('Joint covariance factorization failed') from exc
        if not math.isfinite(score): raise ValueError('Nonfinite joint residual score')
        calculation = {'residual': residual.tolist(), 'quantity_ids': o['quantity_ids'], 'units': o['units'],
                       'squared_mahalanobis': score, 'criterion': p['criterion'],
                       'outcome': 'WITHIN_DECLARED_THRESHOLD' if score <= p['criterion'] else 'OUTSIDE_DECLARED_THRESHOLD',
                       'p_value': None, 'statistical_coverage': 'not-asserted',
                       'input_covariance_id': c['covariance_id']}
    return {**common, 'status': 'CALCULATED', 'reason': None, 'calculation': calculation}


def inspect_calculation(request, result):
    """Check retained calculation metadata and covariance bindings, without a solve.

    This is not a rerun of propagation or a statistical coverage validation.
    """
    p = validate_request(request)
    if result['status'] == 'HELD': return
    calc = result['calculation']; o = p['observation']; c = p['covariance']
    if c is None: raise ValueError('A calculation requires the declared input covariance')
    if p['analysis'] == 'complex-viscosity':
        keys(calc, {'value','unit','standard_uncertainty','sensitivity','output_covariance','nonlinear_coverage'})
        if calc['unit'] != 'Pa*s' or calc['nonlinear_coverage'] != 'not-established':
            raise ValueError('Viscosity units or nonlinear uncertainty scope differ')
        if number(calc['value']) < 0 or number(calc['standard_uncertainty']) < 0:
            raise ValueError('Negative viscosity magnitude or uncertainty')
        if type(calc['sensitivity']) is not list or len(calc['sensitivity']) != 3:
            raise ValueError('Sensitivity axes differ')
        for x in calc['sensitivity']: number(x)
        out = calc['output_covariance']
        validate_covariance_artifact(out, expected_quantity_ids=['complex-viscosity-magnitude'],
                                    expected_units=['Pa*s'], expected_frame=o['conditions'])
        if (out['reference_values'] != [calc['value']]
                or out['basis'] != {'kind':'estimated_state', 'id':content_identity(p)}
                or out['method'] != 'first-order-joint-covariance-propagation'
                or out['provenance'] != {'provider':'ciw.materials_uncertainty',
                    'source_evidence_ids':o['source_refs'], 'source_covariance_ids':[c['covariance_id']]}
                or out['assumptions'] != ['First-order Taylor approximation; nonlinear coverage not established',
                    'Input covariance and source links are caller declarations, not authenticated calibration']):
            raise ValueError('Output covariance ancestry or propagation scope differs')
        if not math.isclose(out['matrix'][0][0], calc['standard_uncertainty']**2, rel_tol=2e-12, abs_tol=0):
            raise ValueError('Standard uncertainty differs from retained variance')
    else:
        keys(calc, {'residual','quantity_ids','units','squared_mahalanobis','criterion','outcome',
                    'p_value','statistical_coverage','input_covariance_id'})
        if (calc['quantity_ids'] != o['quantity_ids'] or calc['units'] != o['units']
                or calc['input_covariance_id'] != c['covariance_id']
                or calc['p_value'] is not None or calc['statistical_coverage'] != 'not-asserted'
                or number(calc['criterion']) != p['criterion']):
            raise ValueError('Joint residual metadata or statistical scope differs')
        if type(calc['residual']) is not list or len(calc['residual']) != len(o['values']):
            raise ValueError('Residual axes differ')
        for x in calc['residual']: number(x)
        score=number(calc['squared_mahalanobis'])
        if score < 0: raise ValueError('Negative joint residual quadratic form')
        expected='WITHIN_DECLARED_THRESHOLD' if score <= p['criterion'] else 'OUTSIDE_DECLARED_THRESHOLD'
        if calc['outcome'] != expected: raise ValueError('Joint residual decision differs from declared rule')
