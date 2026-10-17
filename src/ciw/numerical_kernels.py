"""Explicitly invoked binary64 kernels; no provider lookup or source acquisition."""
from __future__ import annotations
import math
import numpy as np

from .core.covariance import create_covariance_artifact
from .core.identities import content_identity
from .numerical_contracts import sample_axis, coefficient_axis


def artifact(request, axes, matrix, method, sources, *, metadata=None):
    evidence = sorted({request['source_evidence_id']} | {
        evidence for source in sources for evidence in source['provenance']['source_evidence_ids']})
    return create_covariance_artifact(**axes, matrix=np.asarray(matrix).tolist(), method=method,
        basis={'kind':'parameter','id':content_identity(request)},
        provenance={'provider':'ciw.numerical','source_evidence_ids':evidence,
                    'source_covariance_ids':list(dict.fromkeys(source['covariance_id'] for source in sources)),
                    'metadata':metadata or {}}, assumptions=request['assumptions'])


def standard(c):
    # Never hide a negative variance by clipping it to zero.
    if np.any(np.diag(c) < 0):
        raise ValueError('Floating-point result has negative variance; no repair applied')
    return np.sqrt(np.diag(c)).tolist()


def product(*values):
    """Avoid intermediate exponent loss; refuse unrepresentable nonzero terms."""
    if any(value == 0 for value in values):
        return 0.
    mantissa, exponent = 1., 0
    for value in values:
        part, shift = math.frexp(float(value))
        mantissa *= part
        exponent += shift
    result = math.ldexp(mantissa, exponent)
    if result == 0:
        raise FloatingPointError('Nonzero covariance contribution underflows binary64')
    return result


def propagate(j, c):
    # fsum is deliberate: retain signed cross terms without BLAS reduction-order
    # dependence, and produce symmetric outputs for exactly symmetric inputs.
    rows = len(j)
    return np.array([[math.fsum(product(j[a][i],c[i][k],j[b][k])
        for i in range(len(c)) for k in range(len(c))) for b in range(rows)] for a in range(rows)])


def positive_cholesky(c):
    scales = np.sqrt(np.diag(c))
    if np.any(scales <= 0) or not np.array_equal(c, c.T):
        raise ValueError('GLS requires strictly positive, exactly symmetric observation covariance')
    correlation = (c/scales[:,None])/scales[None,:]
    return scales, np.linalg.cholesky(correlation)


def discrepancy_summary(request, predicted, residuals, c, axes, sources, method):
    std = standard(c)
    chi_square = None
    chi_status = 'singular_or_nonpositive_covariance'
    try:
        scales, chol = positive_cholesky(c)
        whitened = np.linalg.solve(chol, residuals/scales)
        chi_square = float(whitened @ whitened)
        chi_status = 'computed_known_covariance_no_acceptance_threshold'
    except (ValueError, np.linalg.LinAlgError):
        pass
    return {'predicted':predicted.tolist(), 'residuals':residuals.tolist(),
        'bias':math.fsum(residuals)/len(residuals),
        'rmse':math.hypot(*residuals)/math.sqrt(len(residuals)),
        'covariance':artifact(request, axes, c, method, sources),
        'standard_uncertainty':std,
        'standardized_residuals':[float(value)/sigma if sigma else None for value,sigma in zip(residuals,std)],
        'chi_square':chi_square, 'chi_square_status':chi_status}


def calculate(operation, request):
    """Request validation belongs to the installed workflow boundary."""
    with np.errstate(over='raise', invalid='raise', divide='raise', under='raise'):
        if operation.startswith('uncertainty.'):
            j, c = request['jacobian'], request['covariance']['matrix']
            out = propagate(j, c)
            result = {'covariance':artifact(request, request['output'], out,
                'first_order_supplied_jacobian', [request['covariance']]),
                'standard_uncertainty':standard(out)}
            if operation == 'uncertainty.budget.v1':
                result['diagonal_terms'] = [[product(row[i],row[i],c[i][i]) for i in range(len(c))] for row in j]
                result['cross_terms'] = [{'pair':[request['input']['quantity_ids'][i],request['input']['quantity_ids'][k]],
                    'contributions':[math.fsum((product(row[i],row[k],c[i][k]),product(row[i],row[k],c[k][i]))) for row in j]}
                    for i in range(len(c)) for k in range(i+1,len(c))]
            return result
        if operation == 'sensitivity.local.v1':
            j = np.asarray(request['jacobian'], dtype=float)
            scaled = (j*np.array(request['input_scales']))/np.array(request['output_scales'])[:,None]
            return {'jacobian':request['jacobian'], 'scaled_jacobian':scaled.tolist(),
                    'method':'supplied_jacobian', 'row_norms':[math.hypot(*row) for row in scaled]}
        if operation == 'calibration.fit.v1':
            x,y = np.array(request['x'],dtype=float),np.array(request['y'],dtype=float)
            c = np.array(request['observation_covariance']['matrix'],dtype=float)
            scales, chol = positive_cholesky(c)
            center = math.fsum(x/len(x))
            xscale = float(np.max(np.abs(x-center)))
            design = np.column_stack((np.ones(len(x)), (x-center)/xscale))
            whitened = np.linalg.solve(chol, design/scales[:,None])
            target = np.linalg.solve(chol, y/scales)
            u,s,vh = np.linalg.svd(whitened,full_matrices=False)
            if s[-1] <= request['rcond']*s[0]:
                raise ValueError('Calibration design is rank deficient at declared rcond')
            coeff = vh.T @ ((u.T @ target)/s)
            transform = np.array([[1., -center/xscale],[0., 1./xscale]])
            physical = transform @ coeff
            inverse = transform @ (vh.T/s)
            coeff_cov = inverse @ inverse.T
            predicted = design @ coeff
            residual = y-predicted
            wr = np.linalg.solve(chol,residual/scales)
            return {'coefficients':artifact(request, coefficient_axis(request,physical.tolist()), coeff_cov,
                        'affine_gls_known_absolute_covariance', [request['observation_covariance']],
                        metadata={'training_sample_ids':request['sample_ids']}),
                    'predicted':predicted.tolist(), 'residuals':residual.tolist(),
                    'degrees_of_freedom':len(x)-2, 'chi_square':float(wr@wr),
                    'condition_number':float(s[0]/s[-1]), 'covariance_scale':'known_absolute_not_residual_rescaled'}
        if operation == 'calibration.residuals.v1':
            design = np.column_stack((np.ones(len(request['x'])),request['x']))
            coeff = request['coefficients']
            predicted = design @ coeff['reference_values']
            residual = np.array(request['y'])-predicted
            c = np.array(request['observation_covariance']['matrix']) + propagate(design,coeff['matrix'])
            return discrepancy_summary(request,predicted,residual,c,sample_axis(request,residual.tolist()),
                [request['observation_covariance'],coeff], 'held_out_affine_independent_observations')
        if operation == 'observability.rank.v1':
            scales = np.array(request['state_scales'])
            a = (np.array(request['transition'])*scales)/scales[:,None]
            block = (np.array(request['observation'])*scales)/np.array(request['output_scales'])[:,None]
            blocks = []
            for k in range(request['horizon']):
                blocks.append(block)
                if k+1 < request['horizon']: block = block@a
            o = np.vstack(blocks)
            s = np.linalg.svd(o,compute_uv=False)
            threshold = float(request['rcond']*s[0])
            rank = int(np.count_nonzero(s>threshold))
            full = rank == len(scales)
            return {'observability_matrix':o.tolist(),'singular_values':s.tolist(),'rank':rank,
                    'threshold':threshold,'full_rank_over_horizon':full,
                    'condition_number':float(s[0]/s[-1]) if full else None,
                    'method':'scaled_discrete_finite_horizon_svd'}
        n = len(request['sample_ids'])
        joint = np.array(request['joint_covariance']['matrix'])
        c = joint[:n,:n]+joint[n:,n:]-joint[:n,n:]-joint[n:,:n]
        predicted = np.array(request['predicted'],dtype=float)
        residual = np.array(request['observed'])-predicted
        axes = dict(quantity_ids=request['sample_ids'],units=[request['unit']]*n,
                    reference_values=residual.tolist(),frame=request['frame'])
        return discrepancy_summary(request,predicted,residual,c,axes,[request['joint_covariance']],
                                   'observed_minus_predicted_full_joint_covariance')
