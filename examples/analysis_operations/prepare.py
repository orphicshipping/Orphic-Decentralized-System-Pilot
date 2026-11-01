"""Prepare a builtin profile that grants statistics and the periodogram.

Requires an installed CIW package. Preparation does not execute an operation.
The destination must not exist; examples never overwrite retained work.
The builtin demo profile stays statistics-only. This is a separate directory.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ciw.control_contracts import save_new
from ciw.control_plane import experiment
from ciw.instruments import make_demo_run


def prepare(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    source = make_demo_run()
    graph = experiment('agent-oscillator-analysis', model_id='analytic-damped-oscillator.v1', nodes=[
        {'node_id': 'statistics', 'operation_id': 'statistics.v1', 'parameters': {'channel': 'q'},
         'inputs': {}, 'depends_on': []},
        {'node_id': 'spectrum', 'operation_id': 'spectrum.periodogram.v1', 'parameters': {'channel': 'q'},
         'inputs': {}, 'depends_on': []},
    ])
    save_new(destination / 'source.json', source)
    save_new(destination / 'experiment.json', graph)
    save_new(destination / 'profile.json', {
        'schema': 'ciw.agent-host-profile.v1',
        'inputs': {'source': 'source.json', 'baseline': 'experiment.json'},
        'output_dir': 'agent-output',
        'allow_operations': ['statistics.v1', 'spectrum.periodogram.v1'],
        'candidate_domains': {}, 'comparisons': {}, 'check_suites': {},
        'max_executions': 8, 'max_nodes': 4})
    return destination / 'profile.json'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    print(prepare(parser.parse_args().output_dir.resolve()))
