"""Data-only Model-Reality Contract; never a state owner or an actuation API.

The six roles are distinct slots. This first slice reserves ``verified`` as
null: neither a numerical fit nor a caller-supplied hash can grant qualification.
"""
from __future__ import annotations

from copy import deepcopy
import math

from .control_contracts import content_ref, detached, keys, number, text
from .operations.runner import check_seal, seal

SCHEMA = "ciw.model-reality.v1"
ROLES = ("specified", "predicted", "commanded", "observed", "estimated", "verified")
ORIGINS = {
    "specified": {"specification"},
    "predicted": {"model", "synthetic"},
    "commanded": {"retained_command"},
    "observed": {"retained_observation"},
    "estimated": {"estimation", "synthetic"},
}
AUTHORITY = {"physical_validation": "not_established", "state_admission": "not_performed",
             "hardware_actuation": "not_performed", "verification": "not_performed"}


def validate(value: dict) -> dict:
    keys(value, {"schema", "entity_id", "quantity", "unit", "frame", "clock_id",
                 "states", "authority", "record_digest"})
    detached(value)
    check_seal(value)
    if value["schema"] != SCHEMA or value["authority"] != AUTHORITY:
        raise ValueError("Model-Reality schema or authority differs")
    for field in ("entity_id", "quantity", "unit", "frame", "clock_id"):
        text(value[field])
    keys(value["states"], set(ROLES))
    if value["states"]["verified"] is not None:
        raise ValueError("Verified state promotion is unavailable in this slice")
    for role, item in value["states"].items():
        if item is None:
            continue
        keys(item, {"value", "standard_uncertainty", "time_s", "source_ref", "origin", "uncertainty_scope"})
        number(item["value"])
        if number(item["time_s"]) < 0:
            raise ValueError("Require nonnegative time in the declared clock")
        if item["standard_uncertainty"] is not None and number(item["standard_uncertainty"]) < 0:
            raise ValueError("Unknown uncertainty is null, never a negative number")
        scope = item["uncertainty_scope"]
        if item["standard_uncertainty"] is None:
            if scope != "unknown":
                raise ValueError("Missing uncertainty requires an explicit unknown scope")
        elif type(scope) is not str or scope not in {"declared_marginal", "conditional_model"}:
            raise ValueError("Declare marginal or conditional-model uncertainty scope")
        if role == "observed" and scope == "conditional_model":
            raise ValueError("Model uncertainty is not observation uncertainty")
        content_ref(item["source_ref"])
        if type(item["origin"]) is not str or item["origin"] not in ORIGINS[role]:
            raise ValueError("State role contradicts its evidence origin")
    return deepcopy(value)


def make(*, entity_id: str, quantity: str, unit: str, frame: str,
         clock_id: str, states: dict) -> dict:
    """Create a detached view. A retained command is not command authorization."""
    if type(states) is not dict or set(states) - set(ROLES):
        raise ValueError("Unknown Model-Reality state role")
    return validate(seal({"schema": SCHEMA, "entity_id": entity_id, "quantity": quantity,
        "unit": unit, "frame": frame, "clock_id": clock_id,
        "states": {role: deepcopy(states.get(role)) for role in ROLES},
        "authority": deepcopy(AUTHORITY)}))


def residual(value: dict, *, cross_covariance: float | None = None) -> dict:
    """Observed minus predicted, at exactly the same declared instant.

A missing cross-covariance does NOT imply independence. A caller can explicitly
supply zero only when an independence assumption is appropriate. This is an
arithmetic diagnostic, not a pass/fail or physical-verification decision.
"""
    value = validate(value)
    predicted, observed = (value["states"][role] for role in ("predicted", "observed"))
    if predicted is None or observed is None:
        raise ValueError("Residual requires distinct predicted and observed states")
    if predicted["origin"] == "synthetic":
        raise ValueError("A synthetic fixture prediction cannot assess physical observations")
    if predicted["time_s"] != observed["time_s"]:
        raise ValueError("Explicit time alignment is required; no silent interpolation")
    up, uo = (item["standard_uncertainty"] for item in (predicted, observed))
    uncertainty = None
    if cross_covariance is not None:
        cross_covariance = number(cross_covariance)
        if up is None or uo is None:
            raise ValueError("Cross-covariance requires both marginal uncertainties")
        if abs(cross_covariance) > up * uo:
            raise ValueError("Cross-covariance violates the marginal uncertainty bound")
        # Nonnegative decomposition avoids catastrophic cancellation at rho=1.
        variance = (up - uo) ** 2 + 2 * (up * uo - cross_covariance)
        uncertainty = math.sqrt(variance)
    return seal({"schema": "ciw.model-reality-residual.v1", "contract_ref": value["record_digest"],
        "quantity": value["quantity"], "unit": value["unit"],
        "value": observed["value"] - predicted["value"],
        "standard_uncertainty": uncertainty, "cross_covariance": cross_covariance,
        "uncertainty_scope": "unknown" if uncertainty is None else "conditional_on_declared_joint_second_moments",
        "authority": deepcopy(AUTHORITY)})
