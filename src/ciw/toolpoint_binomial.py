"""Bounded exact-tail binomial confidence brackets, using integer arithmetic.

The binomial model assumes independent, exchangeable Bernoulli trials. This
module verifies arithmetic, not that a manufacturing sample satisfies that model.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction
from functools import lru_cache
from math import comb

from .manufacturing_contracts import count

MAX_TRIALS = 256
GRID_BITS = 48


def _probability(value: str) -> Fraction:
    if type(value) is not str or not 1 <= len(value) <= 80:
        raise ValueError("Require a bounded decimal probability string")
    try:
        d = Decimal(value)
    except Exception as exc:
        raise ValueError("Invalid decimal probability") from exc
    if not d.is_finite() or not Decimal('0.000001') <= d <= Decimal('0.5'):
        raise ValueError("Tail probability must lie in [0.000001, 0.5]")
    return Fraction(d)


def _tail_at_least(k: int, n: int, a: int, b: int, alpha: Fraction) -> bool:
    """Compare P[Bin(n,a/b)>=k] with alpha exactly, without float tails."""
    pa, pq = [1], [1]
    for _ in range(n):
        pa.append(pa[-1] * a)
        pq.append(pq[-1] * (b - a))
    numerator = sum(comb(n, j) * pa[j] * pq[n-j] for j in range(k, n+1))
    return numerator * alpha.denominator >= alpha.numerator * b**n


@lru_cache(maxsize=1024)
def _lower_bracket(k: int, n: int, alpha: Fraction) -> tuple[int, int]:
    denominator = 1 << GRID_BITS
    if k == 0:
        return 0, 0
    left, right = 0, denominator
    while right - left > 1:
        middle = (left + right) // 2
        if _tail_at_least(k, n, middle, denominator, alpha):
            right = middle
        else:
            left = middle
    return left, right


def _decimal(numerator: int) -> str:
    # A dyadic with denominator 2**48 has an exact terminating decimal.
    with localcontext() as context:
        context.prec = 100
        return str(Decimal(numerator) / Decimal(1 << GRID_BITS))


def confidence_bounds(successes: int, trials: int, *, tail_probability: str = '0.025') -> dict:
    """Outward brackets for Clopper-Pearson lower/upper one-sided bounds.

Each side has nominal coverage 1-alpha. Using both gives an at-least
1-2*alpha interval under the binomial model. Zero trials returns unknown, not 0.
The numerical bracket is <=2**-48 wide; comparisons use exact integer tails.
"""
    count(trials, minimum=0, maximum=MAX_TRIALS)
    count(successes, minimum=0, maximum=trials)
    alpha = _probability(tail_probability)
    if trials == 0:
        return {'successes': 0, 'trials': 0, 'estimate': None,
                'lower_decimal': None, 'upper_decimal': None,
                'lower_root_bracket': None, 'upper_root_bracket': None,
                'tail_probability': tail_probability, 'status': 'NO_TRIALS'}
    lower = _lower_bracket(successes, trials, alpha)
    inverse = _lower_bracket(trials-successes, trials, alpha)
    d = 1 << GRID_BITS
    upper = (d-inverse[1], d-inverse[0])
    return {'successes': successes, 'trials': trials, 'estimate': successes/trials,
            'lower_decimal': _decimal(lower[0]), 'upper_decimal': _decimal(upper[1]),
            'lower_root_bracket': [_decimal(v) for v in lower],
            'upper_root_bracket': [_decimal(v) for v in upper],
            'tail_probability': tail_probability, 'status': 'CONDITIONAL_BINOMIAL_BOUNDS'}
