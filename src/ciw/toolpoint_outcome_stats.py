"""Finite binomial upper bounds using exact rational CDF comparisons.

Conditional statistical model only: independent, identically distributed declared
lot trials. No per-part independence assumption and no material qualification.
"""
from fractions import Fraction
from decimal import Decimal, InvalidOperation
from math import comb

from .manufacturing_contracts import count, number


def _decimal_up(value: Fraction, digits: int = 24) -> str:
    scale = 10 ** digits
    rounded = (value.numerator * scale + value.denominator - 1) // value.denominator
    return f"{rounded // scale}.{rounded % scale:0{digits}d}"


def _cdf_numerator(k: int, n: int, numerator: int, denominator: int) -> int:
    return sum(comb(n, i) * numerator ** i * (denominator - numerator) ** (n-i)
               for i in range(k+1))


def binomial_upper(events: int, trials: int, *, alpha=0.05) -> dict:
    """Conservative endpoint of a 72-bisection Clopper-Pearson upper bracket.

The CDF/root comparisons are integer-exact. The returned decimal upper endpoint
is rounded OUTWARD. 'Exact' names the binomial construction, not the unknown rate.
Zero trials has no estimate or bound. At most 128 lot trials are supported.
"""
    count(trials, minimum=0, maximum=128)
    count(events, minimum=0, maximum=trials)
    if type(alpha) is str:
        if len(alpha) > 64:
            raise ValueError("Tail probability text exceeds bound")
        try:
            value = Decimal(alpha)
        except InvalidOperation as exc:
            raise ValueError("Invalid tail probability") from exc
        if not value.is_finite() or not 0 < value < 1 or value.adjusted() < -50:
            raise ValueError("Invalid bounded tail probability")
    else:
        number(alpha)
    a = Fraction(str(alpha))
    if not 0 < a < 1:
        raise ValueError("Require a tail probability strictly between zero and one")
    result = {"events": events, "trials": trials, "alpha_decimal": str(alpha),
              "estimate": events/trials if trials else None,
              "upper_decimal": None, "bracket_width_at_most": "2^-72",
              "method": "one-sided Clopper-Pearson; exact rational CDF inversion; outward decimal bound"}
    if not trials:
        return result
    if events == trials:
        result["upper_decimal"] = "1.000000000000000000000000"
        return result
    lo, hi = Fraction(0), Fraction(1)
    for _ in range(72):
        mid = (lo + hi) / 2
        lhs = _cdf_numerator(events, trials, mid.numerator, mid.denominator) * a.denominator
        rhs = a.numerator * mid.denominator ** trials
        if lhs > rhs:  # The CDF decreases with p: the root is to the right.
            lo = mid
        else:
            hi = mid
    result["upper_decimal"] = _decimal_up(hi)
    return result
