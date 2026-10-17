# Copyright 2025 Google LLC (reference algorithms)
# Modifications and bounded NET adaptation Copyright 2026 Notation Systems Inc.
# SPDX-License-Identifier: Apache-2.0
# Adapted from T. Fischbacher, framework/markov_tapes.py. Attribution and scope:
# docs/SEQUENCE_KINETICS.md and third_party/chemical-kinetics-and-program-execution/.
"""Bounded sequence probabilities and independent binary-site kinetics.

This is not the upstream Scheme interaction engine. No native library loading,
program evaluation, source acquisition, physical qualification or machine I/O.
Zero-mass contexts stay undefined. No probability clipping or normalization.
"""
from __future__ import annotations

from copy import deepcopy
import itertools
import math
import re
from typing import Any

from .core.identities import canonical_json, content_identity

SCHEMA = "ciw.sequence-kinetics-request.v1"
MAX_BYTES = 256 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_STATES = 4096
MAX_TRAJECTORY_VALUES = 16384
TOLERANCE = 1e-12
AUTHORITY = {"physical_validation": "not_established", "source_authentication": "not_established",
             "thermodynamic_entropy": "not_established", "state_admission": "not_performed",
             "hardware_actuation": "not_performed", "upstream_native_execution": "not_performed"}


def exact_keys(value: Any, expected: set[str]) -> None:
    if type(value) is not dict or set(value) != expected:
        raise ValueError("Unexpected or missing sequence contract fields")


def bounded_json(value: Any, max_bytes: int = MAX_BYTES) -> None:
    count = 0

    def visit(x: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if depth > 16 or count > 150000:
            raise ValueError("Sequence JSON complexity bound exceeded")
        if x is None or type(x) is bool:
            return
        if type(x) is str:
            if len(x) > 512:
                raise ValueError("Sequence text bound exceeded")
        elif type(x) in (int, float):
            if abs(x) > 1e100 or not math.isfinite(x):
                raise ValueError("Require finite bounded JSON numbers")
        elif type(x) is list and len(x) <= MAX_TRAJECTORY_VALUES:
            for item in x:
                visit(item, depth + 1)
        elif type(x) is dict and len(x) <= 64:
            for key, item in x.items():
                if type(key) is not str or not key or len(key) > 128:
                    raise ValueError("Require bounded text keys")
                visit(item, depth + 1)
        else:
            raise ValueError("Require a bounded JSON tree")

    visit(value, 0)
    if len(canonical_json(value).encode()) > max_bytes:
        raise ValueError("Sequence JSON byte bound exceeded")


def finite_number(value: Any, lower: float, upper: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError("Require a finite bounded number, not a boolean")
    return float(value)


def validate_request(value: dict) -> dict:
    bounded_json(value)
    exact_keys(value, {"schema", "source_kind", "source_ref", "alphabet", "block_length",
                       "probabilities", "unit", "queries", "allow_markov_closure", "evolution"})
    if value["schema"] != SCHEMA or value["source_kind"] not in ("synthetic", "declared_distribution"):
        raise ValueError("Require sequence schema and explicit distribution provenance")
    if type(value["source_ref"]) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value["source_ref"]) is None:
        raise ValueError("Require source content reference; this does not authenticate the source")
    alphabet = value["alphabet"]
    if type(alphabet) is not list or not 1 <= len(alphabet) <= 8:
        raise ValueError("Require 1..8 ordered alphabet symbols")
    for symbol in alphabet:
        if (type(symbol) is not str or not symbol or len(symbol) > 32 or symbol != symbol.strip()
                or any(ord(c) < 32 for c in symbol)):
            raise ValueError("Require bounded distinct symbol names")
    if len(set(alphabet)) != len(alphabet):
        raise ValueError("Duplicate alphabet symbol")
    k, n = value["block_length"], len(alphabet)
    if type(k) is not int or not 1 <= k <= 8 or n ** k > MAX_STATES:
        raise ValueError("Require block length 1..8 and at most 4096 states")
    probs = value["probabilities"]
    if value["unit"] != "1" or type(probs) is not list or len(probs) != n ** k:
        raise ValueError("Require a dimensionless, lexicographically ordered full block table")
    for p in probs:
        finite_number(p, 0, 1)
    if abs(math.fsum(probs) - 1.0) > TOLERANCE:
        raise ValueError("Block probabilities must sum to one; no implicit normalization")
    if type(value["allow_markov_closure"]) is not bool:
        raise ValueError("Markov closure must be an explicit boolean declaration")
    queries = value["queries"]
    if type(queries) is not list or len(queries) > 32:
        raise ValueError("At most 32 sequence queries are supported")
    for seq in queries:
        if type(seq) is not list or not 1 <= len(seq) <= 64:
            raise ValueError("Query length must be 1..64")
        if any(type(s) is not str or s not in alphabet for s in seq):
            raise ValueError("Unknown query symbol")
    evolution = value["evolution"]
    if evolution is not None:
        exact_keys(evolution, {"model", "rate_0_to_1_per_s", "rate_1_to_0_per_s", "time_s"})
        if n != 2 or evolution["model"] != "independent_binary_sites":
            raise ValueError("Only the explicit independent binary-site model is supported")
        a = finite_number(evolution["rate_0_to_1_per_s"], 0, 1e6)
        b = finite_number(evolution["rate_1_to_0_per_s"], 0, 1e6)
        ts = evolution["time_s"]
        if type(ts) is not list or not 1 <= len(ts) <= 64 or len(ts) * n ** k > MAX_TRAJECTORY_VALUES:
            raise ValueError("Bound trajectory to 64 samples and 16384 scalar values")
        previous = None
        for t in ts:
            finite_number(t, 0, 1e6)
            if previous is not None and t <= previous:
                raise ValueError("Times must be strictly increasing")
            previous = t
        if ts[0] != 0 or (a + b) * ts[-1] > 700:
            raise ValueError("Start at time zero and bound the rate-time product to 700")
    return deepcopy(value)


def _marginals(probs: list, n: int, k: int) -> tuple[list[float], list[float]]:
    contexts = n ** (k - 1)
    prefix = [math.fsum(probs[i * n:(i + 1) * n]) for i in range(contexts)]
    suffix = [math.fsum(probs[i::contexts]) for i in range(contexts)]
    return prefix, suffix


def _index(symbols: list[int], n: int) -> int:
    index = 0
    for symbol in symbols:
        index = index * n + symbol
    return index


def _query(request: dict, seq: list[str], prefix: list[float]) -> dict:
    n, k, ps = len(request["alphabet"]), request["block_length"], request["probabilities"]
    xs = [request["alphabet"].index(s) for s in seq]
    longer = len(xs) > k
    result = {"sequence": list(seq), "scope": "finite_order_markov_closure" if longer else "retained_block_marginal",
              "status": "PASS", "probability": None, "log_probability_nats": None}
    if longer and not request["allow_markov_closure"]:
        result["status"] = "EXPAND"
        return result
    if not longer:
        width = n ** (k - len(xs))
        start = _index(xs, n) * width
        p = math.fsum(ps[start:start + width])
        result.update(probability=p, log_probability_nats=math.log(p) if p else None)
        return result
    p = ps[_index(xs[:k], n)]
    if not p:
        result["probability"] = 0.0
        return result
    logarithms = [math.log(p)]
    underflow = False
    for stop in range(k, len(xs)):
        window = xs[stop - k + 1:stop + 1]
        numerator = ps[_index(window, n)]
        denominator = prefix[_index(window[:-1], n)]
        if denominator == 0:
            result["status"] = "UNDEFINED_CONTEXT"
            return result
        if numerator == 0:
            result["probability"] = 0.0
            return result
        factor = numerator / denominator
        logarithms.append(math.log(numerator) - math.log(denominator))
        next_p = p * factor
        underflow = underflow or next_p == 0.0
        p = next_p
    result.update(status="UNDERFLOW" if underflow else "PASS",
                  probability=None if underflow else p,
                  log_probability_nats=math.fsum(logarithms))
    return result


def analyze(value: dict) -> dict:
    request = validate_request(value)
    n, k, probs = len(request["alphabet"]), request["block_length"], request["probabilities"]
    prefix, suffix = _marginals(probs, n, k)
    delta = max(abs(a - b) for a, b in zip(prefix, suffix))
    compatible = delta <= TOLERANCE
    contexts = list(itertools.product(range(n), repeat=k - 1))
    conditional, edges = [], []
    for i, mass in enumerate(prefix):
        row = None if mass == 0 else [p / mass for p in probs[i * n:(i + 1) * n]]
        conditional.append(row)
        if row is not None:
            for symbol, p in enumerate(row):
                if p:
                    target = (i * n + symbol) % len(prefix)
                    edges.append({"from_context": i, "to_context": target, "symbol": request["alphabet"][symbol], "probability": p})
    conditional_entropy = -math.fsum(p * (math.log(p) - math.log(prefix[i // n]))
                                    for i, p in enumerate(probs) if p)
    return {"schema": "ciw.sequence-markov-analysis.v1", "request_ref": content_identity(request),
            "source_kind": request["source_kind"], "status": "PASS" if compatible else "REFUSE",
            "alphabet": request["alphabet"], "block_length": k, "state_count": len(probs),
            "probability_sum": math.fsum(probs), "marginal_tolerance": TOLERANCE,
            "max_prefix_suffix_difference": delta, "prefix_masses": prefix, "suffix_masses": suffix,
            "contexts": [[request["alphabet"][i] for i in ctx] for ctx in contexts],
            "conditional_probabilities": conditional,
            "zero_mass_context_indices": [i for i, p in enumerate(prefix) if p == 0],
            "transfer_orientation": "edges_from_context_to_context; upstream_matrix_is_target_source",
            "transfer_edges": edges, "block_entropy_nats": -math.fsum(p * math.log(p) for p in probs if p),
            "conditional_entropy_nats_per_symbol": conditional_entropy,
            "markov_entropy_rate_nats_per_symbol": conditional_entropy if compatible else None,
            "queries": [_query(request, seq, prefix) for seq in request["queries"]] if compatible else [],
            "closure": {"order": k - 1, "longer_sequences_authorized": request["allow_markov_closure"],
                        "status": "model_assumption_not_experimental_validation"},
            "authority": deepcopy(AUTHORITY)}


def _product(a: float, b: float) -> float:
    value = a * b
    if a and b and value == 0:
        raise ValueError("Positive kinetic contribution underflow; no silent support loss")
    return value


def _binary_rhs(probs: list[float], k: int, a: float, b: float) -> list[float]:
    terms: list[list[float]] = [[] for _ in probs]
    for index, p in enumerate(probs):
        for position in range(k):
            bit = 1 << position
            rate = b if index & bit else a
            flux = _product(p, rate)
            terms[index].append(-flux)
            terms[index ^ bit].append(flux)
    return [math.fsum(xs) for xs in terms]


def evolve_binary(value: dict) -> dict:
    request = validate_request(value)
    model = request["evolution"]
    if model is None:
        raise ValueError("Explicit independent binary-site model is required")
    k, initial = request["block_length"], request["probabilities"]
    prefix, suffix = _marginals(initial, 2, k)
    if max(abs(a - b) for a, b in zip(prefix, suffix)) > TOLERANCE:
        raise ValueError("Initial window marginals are incompatible with a stationary tape")
    a, b = float(model["rate_0_to_1_per_s"]), float(model["rate_1_to_0_per_s"])
    total = a + b
    samples = []
    for t in model["time_s"]:
        if total == 0 or t == 0:
            state = list(initial)
        else:
            exponent = _product(total, t)
            decay = math.exp(-exponent)
            changed = -math.expm1(-exponent)
            fractions = (a / total, b / total)
            if any(rate > 0 and fraction == 0 for rate, fraction in zip((a, b), fractions)):
                raise ValueError("Positive transition ratio underflow")
            p01, p10 = _product(fractions[0], changed), _product(fractions[1], changed)
            p00, p11 = decay + p10, decay + p01
            state = list(initial)
            for position in range(k):
                bit = 1 << position
                for index in range(len(state)):
                    if index & bit:
                        continue
                    p0, p1 = state[index], state[index | bit]
                    state[index] = math.fsum((_product(p0, p00), _product(p1, p10)))
                    state[index | bit] = math.fsum((_product(p0, p01), _product(p1, p11)))
        mass = math.fsum(state)
        left, right = _marginals(state, 2, k)
        overlap = max(abs(x - y) for x, y in zip(left, right))
        if abs(mass - 1.0) > TOLERANCE or min(state) < 0 or overlap > TOLERANCE:
            raise ValueError("Independent-site result violates probability or marginal invariant")
        samples.append({"time_s": t, "probabilities": state, "probability_sum": mass,
                        "max_prefix_suffix_difference": overlap})
    return {"schema": "ciw.sequence-binary-evolution.v1", "request_ref": content_identity(request),
            "source_kind": request["source_kind"], "status": "PASS", "model": deepcopy(model),
            "method": "analytic_two_state_transition_tensor_product_no_time_stepping",
            "initial_derivative_per_s": _binary_rhs(initial, k, a, b), "samples": samples,
            "scope": "independent_sites_only; no_interacting_tapes_or_copolymerization_prediction",
            "authority": deepcopy(AUTHORITY)}


def example_request() -> dict:
    source = {"kind": "authored_reference", "problem": "upstream_binary_decay_canary", "block_length": 3}
    return {"schema": SCHEMA, "source_kind": "synthetic", "source_ref": content_identity(source),
            "alphabet": ["A", "B"], "block_length": 3, "probabilities": [0.125] * 8,
            "unit": "1", "queries": [["B"], ["A", "B"], ["A", "B", "B", "A"]],
            "allow_markov_closure": False,
            "evolution": {"model": "independent_binary_sites", "rate_0_to_1_per_s": 0.0,
                          "rate_1_to_0_per_s": 1.0, "time_s": [0.0, 0.1, 1.0, 5.0]}}
