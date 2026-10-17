"""Offline structural and algebraic checks, never dispatching numerical kernels.

These checks do not independently qualify fitted models or supplied derivatives.
"""
import math
from .core.records import finite_tree
from .core.identities import content_identity
from .numerical_contracts import (keys, vector, matrix, scalar, covariance,
    parameters_request, sample_axis, coefficient_axis)


def close(actual, expected):
    scalar(actual)
    if not math.isclose(actual,expected,rel_tol=1e-10,abs_tol=1e-12):
        raise ValueError('Saved numerical result contradicts its declared relationships')


def nonnegative(value):
    if scalar(value) < 0:
        raise ValueError('Result statistic must be nonnegative')


def output_covariance(value, axes, request, sources, method, metadata=None):
    covariance(value, axes)
    expected = {'provider':'ciw.numerical',
        'source_evidence_ids':sorted({request['source_evidence_id']} | {
            e for source in sources for e in source['provenance']['source_evidence_ids']}),
        'source_covariance_ids':list(dict.fromkeys(s['covariance_id'] for s in sources)),
        'metadata':metadata or {}}
    if (value['provenance'] != expected or value['method'] != method
            or value['assumptions'] != request['assumptions']
            or value['basis'] != {'kind':'parameter','id':content_identity(request)}):
        raise ValueError('Output covariance lost its source bindings or declarations')


def validate_payload(operation, data, run, parameters, selection):
    request = parameters_request(operation, run, parameters)
    keys(data, {'schema','operation_id','request_digest','source_evidence_id','scope','result'})
    if (data['schema'] != 'ciw.numerical-result.v1' or data['operation_id'] != operation
            or data['request_digest'] != content_identity(request)
            or data['source_evidence_id'] != run['evidence_id'] or data['scope'] != 'declared_model_only'):
        raise ValueError('Numerical result envelope does not bind the retained request')
    finite_tree(data, 'numerical result')
    result = data['result']
    if operation.startswith('uncertainty.'):
        n,m = len(request['input']['quantity_ids']),len(request['output']['quantity_ids'])
        extra = {'diagonal_terms','cross_terms'} if operation.endswith('budget.v1') else set()
        keys(result, {'covariance','standard_uncertainty'} | extra)
        output_covariance(result['covariance'],request['output'],request,[request['covariance']],
                          'first_order_supplied_jacobian')
        vector(result['standard_uncertainty'],m)
        for i,std in enumerate(result['standard_uncertainty']):
            nonnegative(std); close(std,math.sqrt(result['covariance']['matrix'][i][i]))
        if extra:
            matrix(result['diagonal_terms'],m,n)
            pairs = [(i,k) for i in range(n) for k in range(i+1,n)]
            if type(result['cross_terms']) is not list or len(result['cross_terms']) != len(pairs):
                raise ValueError('Budget must retain every signed covariance pair')
            for term,(i,k) in zip(result['cross_terms'],pairs):
                keys(term,{'pair','contributions'})
                if term['pair'] != [request['input']['quantity_ids'][i],request['input']['quantity_ids'][k]]:
                    raise ValueError('Budget pair order differs from input axis')
                vector(term['contributions'],m)
            for row in range(m):
                for diagonal in result['diagonal_terms'][row]: nonnegative(diagonal)
                close(math.fsum(result['diagonal_terms'][row]+[v['contributions'][row] for v in result['cross_terms']]),
                      result['covariance']['matrix'][row][row])
    elif operation == 'sensitivity.local.v1':
        keys(result,{'jacobian','scaled_jacobian','method','row_norms'})
        m,n = len(request['output']['quantity_ids']),len(request['input']['quantity_ids'])
        matrix(result['jacobian'],m,n)
        if result['jacobian'] != request['jacobian'] or result['method'] != 'supplied_jacobian':
            raise ValueError('Sensitivity must retain the supplied derivative')
        matrix(result['scaled_jacobian'],m,n); vector(result['row_norms'],m)
        for i,row in enumerate(result['scaled_jacobian']):
            for k,value in enumerate(row): close(value,request['jacobian'][i][k]*request['input_scales'][k]/request['output_scales'][i])
            close(result['row_norms'][i],math.hypot(*row))
    elif operation == 'calibration.fit.v1':
        keys(result,{'coefficients','predicted','residuals','degrees_of_freedom','chi_square','condition_number','covariance_scale'})
        n = len(request['x'])
        coeff = result['coefficients']
        output_covariance(coeff,coefficient_axis(request,coeff.get('reference_values')),request,
            [request['observation_covariance']],'affine_gls_known_absolute_covariance',
            {'training_sample_ids':request['sample_ids']})
        vector(result['predicted'],n); vector(result['residuals'],n)
        if type(result['degrees_of_freedom']) is not int or result['degrees_of_freedom'] != n-2:
            raise ValueError('Incorrect calibration degrees of freedom')
        if result['covariance_scale'] != 'known_absolute_not_residual_rescaled':
            raise ValueError('Fit cannot silently estimate or rescale supplied noise')
        nonnegative(result['chi_square']); scalar(result['condition_number'],positive=True)
        for y,pred,residual in zip(request['y'],result['predicted'],result['residuals']): close(y-pred,residual)
    elif operation == 'observability.rank.v1':
        keys(result,{'observability_matrix','singular_values','rank','threshold','full_rank_over_horizon','condition_number','method'})
        n,m = len(request['states']['quantity_ids']),len(request['outputs']['quantity_ids'])*request['horizon']
        matrix(result['observability_matrix'],m,n); vector(result['singular_values'],min(m,n))
        s = result['singular_values']
        for value in s: nonnegative(value)
        if s != sorted(s,reverse=True): raise ValueError('Singular values must be ordered')
        close(result['threshold'],request['rcond']*s[0])
        rank = sum(v > result['threshold'] for v in s)
        if type(result['rank']) is not int or result['rank'] != rank or result['full_rank_over_horizon'] is not (rank == n):
            raise ValueError('Observability rank flags disagree')
        if result['method'] != 'scaled_discrete_finite_horizon_svd': raise ValueError('Wrong observability method')
        if rank == n: close(result['condition_number'],s[0]/s[-1])
        elif result['condition_number'] is not None: raise ValueError('Rank deficient condition number must be absent')
    else:
        keys(result,{'predicted','residuals','bias','rmse','covariance','standard_uncertainty',
                     'standardized_residuals','chi_square','chi_square_status'})
        n = len(request['sample_ids'])
        vector(result['predicted'],n); vector(result['residuals'],n); vector(result['standard_uncertainty'],n)
        if operation == 'calibration.residuals.v1':
            observed = request['y']
            axes = sample_axis(request,result['residuals'])
            sources = [request['observation_covariance'],request['coefficients']]
            method = 'held_out_affine_independent_observations'
        else:
            observed = request['observed']
            if result['predicted'] != request['predicted']: raise ValueError('Predictions differ from declared values')
            axes = dict(quantity_ids=request['sample_ids'],units=[request['unit']]*n,
                        reference_values=result['residuals'],frame=request['frame'])
            sources,method = [request['joint_covariance']],'observed_minus_predicted_full_joint_covariance'
        output_covariance(result['covariance'],axes,request,sources,method)
        if type(result['standardized_residuals']) is not list or len(result['standardized_residuals']) != n:
            raise ValueError('Residual uncertainty shape mismatch')
        for i,(y,pred,residual) in enumerate(zip(observed,result['predicted'],result['residuals'])):
            close(y-pred,residual)
            std = result['standard_uncertainty'][i]
            nonnegative(std); close(std,math.sqrt(result['covariance']['matrix'][i][i]))
            z = result['standardized_residuals'][i]
            if std: close(z,residual/std)
            elif z is not None: raise ValueError('Zero uncertainty cannot produce a finite standardized residual')
        close(result['bias'],math.fsum(result['residuals'])/n)
        close(result['rmse'],math.hypot(*result['residuals'])/math.sqrt(n))
        if result['chi_square'] is None:
            if result['chi_square_status'] != 'singular_or_nonpositive_covariance': raise ValueError('Missing chi-square reason')
        else:
            nonnegative(result['chi_square'])
            if result['chi_square_status'] != 'computed_known_covariance_no_acceptance_threshold': raise ValueError('Wrong chi-square scope')
