"""Independent rational audit of the local covariance-counterfactual calculation.

Does not call the review's Decimal numeric kernel or recalculate a calibration.
Structural/decoding validation is shared; all new variance arithmetic is separate.
"""
from fractions import Fraction
import math

from . import link_contract as c, link_study as study


def audit(source, candidate):
    from .link_improvement import validate_plan, LIMITS
    parameters = candidate["parameters"]
    plan = validate_plan(parameters["plan"], source=source, model=parameters["model"], audit=parameters["audit"])
    request = study.source_request(source)
    result = candidate["data"]
    data = result["feedback"]
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})

    def number(name, actual, expected):
        value = float(expected)
        ok = (type(actual) in (int, float) and math.isfinite(actual) and math.isfinite(value)
              and (actual == 0 if expected == 0 else
                   value != 0 and abs(actual - value) <= 8 * math.ulp(value)))
        check(name, ok)

    check("proposal_not_authority", result["status"] == "PROPOSAL_ONLY" and result["limits"] == LIMITS)
    check("physical_hub_unestablished", result["hub"]["status"] == "NOT_ESTABLISHED")
    check("source_and_occurrences", result["source_evidence_id"] == source["evidence_id"]
          and result["model_result_id"] == parameters["model"]["result_id"]
          and result["audit_result_id"] == parameters["audit"]["result_id"])
    cov = request["calibration"]["joint_covariance"]
    if cov is None:
        check("unknown_not_zero", data["status"] == "UNCERTAINTY_UNKNOWN" and data["linearized_variance"] is None
              and not data["ranking"] and not data["candidates"] and data["proposed_candidate_id"] is None)
    else:
        x = [Fraction(r["value"]) for r in study.decode(request)["rows"]]
        n = len(x); size = n + 2
        g = [Fraction(request["calibration"]["gain"]) / n] * n + [sum(x) / n, Fraction(1)]
        sigma = [[Fraction(v) for v in row] for row in cov["matrix"]]
        variance = sum(g[i] * sigma[i][j] * g[j] for i in range(size) for j in range(size))
        number("variance", data["linearized_variance"], variance)
        number("original_variance", data["retained_model_variance"], Fraction(parameters["model"]["data"]["variance"]))
        number("projection_rounding_difference", data["rounding_difference"], variance - Fraction(parameters["model"]["data"]["variance"]))
        check("covariance_identity", data["input_covariance_id"] == cov["covariance_id"])
        check("sensitivity_count", len(data["sensitivities"]) == size)
        for i, item in enumerate(data["sensitivities"]):
            number(f"gradient.{i}", item["gradient"], g[i])
            number(f"diagonal.{i}", item["diagonal_variance_term"], g[i]**2 * sigma[i][i])
            expected_scale = math.sqrt(float(g[i]**2 * sigma[i][i]))
            number(f"marginal_scale.{i}", item["marginal_scale"], Fraction(expected_scale))
            check(f"axis.{i}", item["index"] == i and item["quantity_id"] == cov["quantity_ids"][i]
                  and item["input_unit"] == cov["units"][i])
        pairs = [(i, j) for i in range(size) for j in range(i + 1, size)]
        check("cross_term_count", len(data["cross_terms"]) == len(pairs))
        for item, (i, j) in zip(data["cross_terms"], pairs):
            check(f"pair.{i}.{j}", item["left_index"] == i and item["right_index"] == j)
            number(f"cross.{i}.{j}", item["signed_variance_term"], g[i] * (sigma[i][j] + sigma[j][i]) * g[j])
        threshold = Fraction(plan["objective"]["max_standard_uncertainty"])**2
        check("objective", data["objective_met"] == (variance <= threshold))
        ranking = []
        check("candidate_count", len(data["candidates"]) == len(plan["candidates"]))
        for actual, planned in zip(data["candidates"], plan["candidates"]):
            d = list(map(Fraction, planned["scales"]))
            matrix = [[d[i] * sigma[i][j] * d[j] for j in range(size)] for i in range(size)]
            v = sum(g[i] * matrix[i][j] * g[j] for i in range(size) for j in range(size))
            identity = planned["id"]
            check(identity + ".identity", actual["candidate_id"] == identity)
            number(identity + ".variance", actual["variance"], v)
            number(identity + ".uncertainty", actual["standard_uncertainty"], Fraction(math.sqrt(float(v))))
            number(identity + ".reduction", actual["variance_reduction"], variance - v)
            for i in range(size):
                for j in range(size):
                    number(f"{identity}.matrix.{i}.{j}", actual["counterfactual_matrix"][i][j], matrix[i][j])
            expected_status = "REDUCES_DECLARED_VARIANCE" if v < variance else "INCREASES_DECLARED_VARIANCE" if v > variance else "UNCHANGED"
            check(identity + ".status", actual["status"] == expected_status and actual["objective_met"] == (v <= threshold))
            ranking.append((v, identity, variance - v))
        ranking.sort(key=lambda row: (row[0], row[1]))
        check("ranking", data["ranking"] == [identity for _, identity, _ in ranking])
        check("proposal", data["proposed_candidate_id"] == (None if variance <= threshold else next((identity for _, identity, delta in ranking if delta > 0), None)))
    return {"schema": "ciw.link-improvement-audit.v1", "status": "PASS" if all(v["passed"] for v in checks) else "FAIL",
            "candidate_result_id": candidate["result_id"], "checks": checks,
            "scope": "independent_rational_counterfactual_arithmetic_not_empirical_improvement", "limits": dict(LIMITS)}
