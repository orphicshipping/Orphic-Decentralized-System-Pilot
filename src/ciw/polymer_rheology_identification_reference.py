"""Independent Decimal80 response and constrained-GLS stationarity checks.

No import/call to the production design, prediction, fit, or optimizer. This
checks a frozen candidate on its declared fixed grid, not a material truth.
"""
from decimal import Decimal, localcontext
import math

import numpy as np


def _d(x):
    return Decimal.from_float(float(x))


def _design(p):
    result = []
    for row in p["samples"]:
        q, x = row["quantity"], _d(row["coordinate"])
        basis = [Decimal(0) if q == "loss_modulus_pa" else Decimal(1)]
        for value in p["relaxation_times_s"]:
            tau = _d(value)
            if q == "relaxation_modulus_pa":
                z = x / tau
                basis.append(Decimal(0) if z > 1000 else (-z).exp())
            else:
                z = x * tau
                basis.append((z*z if q == "storage_modulus_pa" else z) / (1 + z*z))
        result.append(basis)
    return result


def _cholesky(matrix):
    n = len(matrix)
    lower = [[Decimal(0)] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            remainder = matrix[i][j] - sum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                if remainder <= 0:
                    raise ValueError("Decimal covariance is not positive definite")
                lower[i][j] = remainder.sqrt()
            else:
                lower[i][j] = remainder / lower[j][j]
    return lower


def _whiten(lower, vector):
    result = []
    for i, value in enumerate(vector):
        result.append((value - sum(lower[i][j] * result[j] for j in range(i))) / lower[i][i])
    return result


def audit_solution(result, kkt_tolerance):
    failures = []
    with localcontext() as context:
        context.prec = 80
        p, coefficients = result["request"], list(map(_d, result["coefficients_pa"]))
        basis = _design(p)
        target = [sum(a*b for a, b in zip(row, coefficients)) for row in basis]
        max_relative = 0.0
        for actual, reference in zip(result["predicted_modulus_pa"], target):
            expected = float(reference)
            error = abs(actual - expected)
            limit = max(2e-12 * abs(expected), 8 * math.ulp(expected))
            if error > limit or (reference == 0 and actual != 0):
                failures.append("prediction")
            if actual != 0 or expected != 0:
                max_relative = max(max_relative, error / max(abs(actual), abs(expected)))
        train = [i for i, row in enumerate(p["samples"]) if row["partition"] == "train"]
        covariance = [[_d(p["covariance"]["matrix"][i][j]) for j in train] for i in train]
        lower = _cholesky(covariance)
        columns = [_whiten(lower, [basis[i][j] for i in train]) for j in range(len(coefficients))]
        norms = [sum(x*x for x in col).sqrt() for col in columns]
        rhs = _whiten(lower, [_d(p["samples"][i]["value_pa"]) for i in train])
        scale = max(map(abs, rhs)) or Decimal(1)
        rhs = [x / scale for x in rhs]
        columns = [[x / norm for x in column] for column, norm in zip(columns, norms)]
        z = [e * norm / scale for e, norm in zip(coefficients, norms)]
        residual = [sum(column[i] * value for column, value in zip(columns, z)) - rhs[i] for i in range(len(train))]
        gradient = [sum(a*b for a, b in zip(column, residual)) for column in columns]
        violation = max(abs(g) if e > 0 else max(Decimal(0), -g) for e, g in zip(coefficients, gradient))
        complementarity = max(abs(a*b) for a, b in zip(z, gradient)) / max(Decimal(1), max(map(abs, z)))
        if violation > _d(kkt_tolerance) or complementarity > _d(kkt_tolerance):
            failures.append("training-kkt")
        # SVD is not a second independent LAPACK implementation; its design and
        # whitening inputs were recomputed independently in Decimal arithmetic.
        singular = np.linalg.svd(np.asarray([[float(column[i]) for column in columns]
                                            for i in range(len(train))]), compute_uv=False)
        condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else math.inf
        if (not math.isfinite(condition) or condition > 1e8
                or not math.isclose(condition, result["solver"]["scaled_design_condition"], rel_tol=2e-8, abs_tol=0)):
            failures.append("design-condition")
        return {"kkt_max_violation": float(violation), "normalized_complementarity": float(complementarity),
                "prediction_max_symmetric_relative_error": max_relative}, sorted(set(failures))
