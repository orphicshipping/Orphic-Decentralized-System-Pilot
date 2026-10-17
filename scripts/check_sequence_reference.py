"""Small independent CTMC matrix-exponential cross-check; never a physical proof."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import platform
import random
import sys

import numpy as np
import scipy
from scipy.linalg import expm

from ciw import sequence_kinetics as k
from ciw.core.identities import content_identity, new_identity


def run_checks():
    rows = []
    for length in range(1, 6):
        words = list(itertools.product((0, 1), repeat=length))
        lookup = {word: i for i, word in enumerate(words)}
        for seed in range(7):
            rng = random.Random(1907 + seed)
            tape = [rng.randrange(2) for _ in range(127)]
            counts = [0] * len(words)
            for start in range(len(tape)):
                word = tuple(tape[(start+j) % len(tape)] for j in range(length))
                counts[lookup[word]] += 1
            probs = [count / len(tape) for count in counts]
            for a, b in ((0., 1.), (.37, .82), (1., 0.)):
                request = k.example_request()
                request.update(block_length=length, probabilities=probs, queries=[])
                request['evolution'].update(rate_0_to_1_per_s=a, rate_1_to_0_per_s=b,
                                            time_s=[0., .03, .7, 3.])
                result = k.evolve_binary(request)
                # Independent tuple-based full generator, NOT tensor transition code.
                generator = np.zeros((len(words), len(words)))
                for column, word in enumerate(words):
                    for position, symbol in enumerate(word):
                        next_word = list(word)
                        next_word[position] = 1-symbol
                        rate = b if symbol else a
                        generator[lookup[tuple(next_word)], column] += rate
                        generator[column, column] -= rate
                error = 0.
                rhs_error = float(np.max(np.abs(generator @ probs - result['initial_derivative_per_s'])))
                for sample in result['samples']:
                    ref = expm(sample['time_s'] * generator) @ probs
                    error = max(error, float(np.max(np.abs(ref - sample['probabilities']))))
                if error > 2e-12 or rhs_error > 2e-12:
                    raise AssertionError(f'Independent CTMC mismatch: {length}, {seed}, {a}, {b}, {error}')
                rows.append({'request_ref': content_identity(request), 'block_length': length,
                             'seed': seed, 'rates_per_s': [a,b], 'max_probability_error': error,
                             'max_rhs_error': rhs_error})
    expected = [.375,.125,.125,-.125,.125,-.125,-.125,-.375]
    if k.evolve_binary(k.example_request())['initial_derivative_per_s'] != expected:
        raise AssertionError('Published upstream canary mismatch')
    return {'schema':'ciw.sequence-independent-reference-audit.v1', 'status':'PASS',
            'execution_id':new_identity('execution'), 'verification_id':new_identity('verification'),
            'scope':'independent_small_matrix_ctmc_arithmetic_not_native_session_or_physical_validation',
            'cases':len(rows), 'time_points':4*len(rows), 'max_probability_error':max(x['max_probability_error'] for x in rows),
            'max_rhs_error':max(x['max_rhs_error'] for x in rows), 'absolute_tolerance':2e-12,
            'published_canary':'exact_match', 'case_results':rows,
            'runtime':{'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__,
                       'kernel_sha256':hashlib.sha256(Path(k.__file__).read_bytes()).hexdigest(),
                       'checker_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            'authority':k.AUTHORITY.copy()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=run_checks()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:
        json.dump(report,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({name:report[name] for name in ('status','cases','time_points','max_probability_error','max_rhs_error')}))
