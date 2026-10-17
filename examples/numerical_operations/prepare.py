"""Prepare seven synthetic built-in requests and an explicit MCP profile.

Requires an installed CIW package. Preparation does not execute an operation.
The destination must not exist; examples never overwrite retained work.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ciw.control_contracts import save_new
from ciw.control_plane import experiment
from ciw.core.covariance import create_covariance_artifact
from ciw.instruments import make_demo_run
from ciw.numerical_contracts import OPERATIONS


def axis(ids, units, values, frame='declared-frame'):
    return dict(quantity_ids=ids, units=units, reference_values=values, frame=frame)


def prepare(destination):
    destination.mkdir(parents=True, exist_ok=False)
    run = make_demo_run()
    evidence = run['evidence_id']
    assumptions = ['Synthetic operator-supplied model; no physical traceability claimed.']

    def cov(axes, matrix, metadata=None):
        return create_covariance_artifact(
            **axes, matrix=matrix, method='declared_synthetic_covariance',
            basis={'kind': 'parameter', 'id': 'numerical-demo-v1'},
            provenance={'provider': 'numerical-demo', 'source_evidence_ids': [evidence],
                        'source_covariance_ids': [], 'metadata': metadata or {}},
            assumptions=assumptions)

    def common():
        return dict(source_evidence_id=evidence, assumptions=list(assumptions))

    inp = axis(['a', 'b'], ['m', 's'], [2., 3.])
    out = axis(['z'], ['kg'], [7.])
    linear = dict(input=inp, output=out, jacobian=[[2., -1.]])
    uncertainty = dict(**common(), **linear, covariance=cov(inp, [[4., 1.], [1., 9.]]))
    samples = dict(sample_ids=['s0', 's1', 's2'], x=[0., 1., 2.], y=[1., 3., 5.],
                   x_unit='count', y_unit='V', frame='sensor', model='affine_exact_x')
    observation = cov(axis(samples['sample_ids'], ['V']*3, samples['y'], 'sensor'),
                      [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]])
    training = ['train0', 'train1']
    coefficients = cov(axis(['offset', 'slope'], ['V', 'V/count'], [1., 2.], 'sensor'),
                       [[.25, .05], [.05, .04]], {'training_sample_ids': training})
    joint = cov(axis(['observed:s0', 'observed:s1', 'predicted:s0', 'predicted:s1'],
                     ['m']*4, [2., 4., 1., 2.], 'sensor'),
                [[1., 0., .5, 0.], [0., 1., 0., .5],
                 [.5, 0., 1., 0.], [0., .5, 0., 1.]])
    requests = {
        'uncertainty.propagate.v1': uncertainty,
        'uncertainty.budget.v1': uncertainty,
        'calibration.fit.v1': dict(**common(), **samples, observation_covariance=observation, rcond=1e-12),
        'calibration.residuals.v1': dict(
            **common(), **samples, observation_covariance=observation, coefficients=coefficients,
            training_sample_ids=training,
            independence='held_out_observations_independent_of_coefficients'),
        'sensitivity.local.v1': dict(**common(), **linear, input_scales=[2., 3.], output_scales=[7.]),
        'observability.rank.v1': dict(
            **common(), states=axis(['position', 'velocity'], ['m', 'm/s'], [0., 0.]),
            outputs=axis(['position_readout'], ['m'], [0.]),
            transition=[[1., 1.], [0., 1.]], observation=[[1., 0.]],
            state_scales=[1., 1.], output_scales=[1.], horizon=2, sample_period_s=1., rcond=1e-12),
        'model.discrepancy.v1': dict(
            **common(), sample_ids=['s0', 's1'], observed=[2., 4.], predicted=[1., 2.],
            unit='m', frame='sensor', joint_covariance=joint),
    }
    graph = experiment('numerical-builtins', model_id='supplied-linear-models.v1', nodes=[
        {'node_id': 'operation-'+str(i), 'operation_id': name,
         'parameters': {'request': requests[name]}, 'inputs': {}, 'depends_on': []}
        for i, name in enumerate(OPERATIONS)])
    save_new(destination/'source.json', run)
    save_new(destination/'experiment.json', graph)
    save_new(destination/'profile.json', {
        'schema': 'ciw.agent-host-profile.v1',
        'inputs': {'source': 'source.json', 'baseline': 'experiment.json'},
        'output_dir': 'agent-output', 'allow_operations': list(OPERATIONS),
        'candidate_domains': {}, 'comparisons': {}, 'check_suites': {},
        'max_executions': 4, 'max_nodes': 8})
    return destination/'profile.json'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    print(prepare(parser.parse_args().output_dir.resolve()))
