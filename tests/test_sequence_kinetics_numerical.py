"""Independent numerical checks for ciw.sequence_kinetics.

The references here do not reuse the module's arithmetic: the evolution is
checked against a dense matrix exponential of the full 2**k generator, the
Markov closure against a directly enumerated stationary Markov chain, and the
entropies against closed forms.
"""
import itertools
import math

import numpy as np
import pytest

from ciw.sequence_kinetics import analyze, evolve_binary, example_request

REF = "sha256:" + "0" * 64


def _request(probs, k, n=2, queries=(), closure=False, evolution=None):
    return {"schema": example_request()["schema"], "source_kind": "synthetic", "source_ref": REF,
            "alphabet": ["A", "B", "C", "D"][:n], "block_length": k, "probabilities": list(probs),
            "unit": "1", "queries": [list(q) for q in queries], "allow_markov_closure": closure,
            "evolution": evolution}


def _generator(k, a, b):
    size = 2 ** k
    q = np.zeros((size, size))
    for index in range(size):
        for position in range(k):
            bit = 1 << position
            rate = b if index & bit else a
            q[index ^ bit, index] += rate
            q[index, index] -= rate
    return q


def _expm(m):
    # Scaling and squaring with a 30-term Taylor series: independent of the
    # module's closed-form two-state product.
    norm = max(1.0, np.abs(m).sum(axis=0).max())
    s = int(math.ceil(math.log2(norm))) + 1
    scaled = m / 2 ** s
    term, total = np.eye(len(m)), np.eye(len(m))
    for i in range(1, 30):
        term = term @ scaled / i
        total = total + term
    for _ in range(s):
        total = total @ total
    return total


def _stationary_window_table(k, rng):
    # A random stationary block table: the k-window law of a two-state Markov chain.
    p01, p10 = rng.uniform(0.05, 0.95, 2)
    pi = np.array([p10, p01]) / (p01 + p10)
    t = np.array([[1 - p01, p01], [p10, 1 - p10]])
    table = []
    for word in itertools.product((0, 1), repeat=k):
        p = pi[word[0]]
        for x, y in zip(word, word[1:]):
            p *= t[x, y]
        table.append(float(p))
    total = math.fsum(table)
    return [p / total for p in table], pi, t


@pytest.mark.parametrize("k, a, b", [(1, 0.3, 0.7), (2, 1.0, 0.0), (3, 0.0, 1.0), (3, 2.5, 0.4), (4, 0.9, 1.3)])
def test_evolution_matches_dense_matrix_exponential(k, a, b):
    rng = np.random.default_rng(k)
    initial, _, _ = _stationary_window_table(k, rng)
    times = [0.0, 0.05, 0.5, 2.0, 7.5]
    result = evolve_binary(_request(initial, k, evolution={
        "model": "independent_binary_sites", "rate_0_to_1_per_s": a, "rate_1_to_0_per_s": b, "time_s": times}))
    q = _generator(k, a, b)
    for sample, t in zip(result["samples"], times):
        expected = _expm(q * t) @ np.array(initial)
        assert np.allclose(sample["probabilities"], expected, rtol=1e-10, atol=1e-13)
    assert np.allclose(result["initial_derivative_per_s"], q @ np.array(initial), atol=1e-14)


def test_long_time_limit_is_the_product_of_site_equilibria():
    a, b, k = 0.8, 0.2, 3
    result = evolve_binary(_request([0.125] * 8, k, evolution={
        "model": "independent_binary_sites", "rate_0_to_1_per_s": a, "rate_1_to_0_per_s": b,
        "time_s": [0.0, 600.0]}))
    p1 = a / (a + b)
    expected = [math.prod(p1 if index & (1 << pos) else 1 - p1 for pos in range(k)) for index in range(8)]
    assert np.allclose(result["samples"][-1]["probabilities"], expected, atol=1e-14)


def test_pure_decay_matches_closed_form():
    # All mass starts on "BBB" (index 7); with only 1->0 transitions at rate b
    # each site survives as B with probability exp(-b t).
    b, t = 1.0, 0.7
    initial = [0.0] * 7 + [1.0]
    out = evolve_binary(_request(initial, 3, evolution={
        "model": "independent_binary_sites", "rate_0_to_1_per_s": 0.0, "rate_1_to_0_per_s": b,
        "time_s": [0.0, t]}))
    survive = math.exp(-b * t)
    assert out["samples"][1]["probabilities"][7] == pytest.approx(survive ** 3, rel=1e-14)
    assert out["samples"][1]["probabilities"][0] == pytest.approx((1 - survive) ** 3, rel=1e-12)


@pytest.mark.parametrize("p", [0.5, 0.2, 0.93])
def test_iid_block_entropy_and_rate_match_bernoulli_entropy(p):
    k = 3
    table = [math.prod(p if bit else 1 - p for bit in word) for word in itertools.product((0, 1), repeat=k)]
    out = analyze(_request(table, k))
    h = -(p * math.log(p) + (1 - p) * math.log(1 - p))
    assert out["status"] == "PASS"
    assert out["block_entropy_nats"] == pytest.approx(k * h, rel=1e-12)
    assert out["markov_entropy_rate_nats_per_symbol"] == pytest.approx(h, rel=1e-12)


def test_markov_closure_reproduces_the_generating_chain():
    rng = np.random.default_rng(7)
    table, pi, t = _stationary_window_table(2, rng)
    seq = [0, 1, 1, 0, 1, 0, 0, 1, 1, 1, 0]  # x1 != x_last, so a wrong context denominator cannot telescope away
    out = analyze(_request(table, 2, queries=[["AB"[x] for x in seq]], closure=True))
    exact = pi[seq[0]] * math.prod(t[x, y] for x, y in zip(seq, seq[1:]))
    query = out["queries"][0]
    assert query["scope"] == "finite_order_markov_closure" and query["status"] == "PASS"
    assert query["probability"] == pytest.approx(exact, rel=1e-12)
    assert query["log_probability_nats"] == pytest.approx(math.log(exact), rel=1e-12)
    rate = -sum(pi[i] * t[i, j] * math.log(t[i, j]) for i in range(2) for j in range(2))
    assert out["markov_entropy_rate_nats_per_symbol"] == pytest.approx(rate, rel=1e-12)


def test_non_stationary_table_is_refused_and_longer_queries_need_closure():
    # P(AA)=0.5, P(AB)=0.5: prefix masses (1, 0) differ from suffix masses (0.5, 0.5).
    assert analyze(_request([0.5, 0.5, 0.0, 0.0], 2))["status"] == "REFUSE"
    out = analyze(_request([0.25] * 4, 2, queries=[["A", "B", "A"]], closure=False))
    assert out["queries"][0]["status"] == "EXPAND" and out["queries"][0]["probability"] is None


def test_probabilities_are_not_silently_normalized():
    with pytest.raises(ValueError, match="sum to one"):
        analyze(_request([0.25, 0.25, 0.25, 0.2], 2))
