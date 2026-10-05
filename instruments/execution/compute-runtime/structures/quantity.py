"""Typed quantities and property context: a bare scalar is not science.

A property value is a function of method and conditions -- the same
quantity measured by two methods, or at two rates, is TWO facts. And a
numeric value without a unit and an explicit uncertainty posture
invites comparisons the data cannot support. These guards are the
chemistry vertical's ingest gate for canonical scientific properties;
they extend the open observation content mapping (no core schema is
widened) and every refusal is fail-closed.

`uncertainty_kind: "absent"` is EXPLICIT and load-bearing: it records
"the source reported no uncertainty," which is a different fact from
"the uncertainty was lost during ingestion" -- the latter simply cannot
happen here, because a quantity without a declared kind is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Optional

UNCERTAINTY_KINDS = ("stated", "estimated", "propagated", "absent")


class QuantityError(ValueError):
    """A context-free or untyped quantity is refused."""


def _finite_number(value: object, field: str) -> float:
    # Keep numeric-text ingestion compatible, but never coerce booleans, nulls
    # or arbitrary objects into scientific values. All failures belong to the
    # quantity gate so the acquisition path can retain and quarantine them.
    if type(value) not in (int, float, str):
        raise QuantityError(f"quantity {field} requires a finite non-boolean number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise QuantityError(f"quantity {field} requires a finite non-boolean number") from exc
    if not math.isfinite(result):
        raise QuantityError(f"quantity {field} requires a finite non-boolean number")
    return result


@dataclass(frozen=True)
class TypedQuantity:
    """value + unit + uncertainty posture. `uncertainty` must be None
    exactly when `uncertainty_kind` is "absent", and present otherwise."""

    value: float
    unit: str
    uncertainty_kind: str
    uncertainty: Optional[float] = None

    def __post_init__(self):
        if type(self.unit) is not str or not self.unit.strip():
            raise QuantityError("a quantity without a unit is untyped; refused")
        if self.uncertainty_kind not in UNCERTAINTY_KINDS:
            raise QuantityError(
                f"uncertainty_kind {self.uncertainty_kind!r} is not one of "
                f"{UNCERTAINTY_KINDS} -- absent must be EXPLICIT, never implied"
            )
        if self.uncertainty_kind == "absent" and self.uncertainty is not None:
            raise QuantityError(
                "uncertainty_kind 'absent' contradicts a supplied uncertainty"
            )
        if self.uncertainty_kind != "absent" and self.uncertainty is None:
            raise QuantityError(
                f"uncertainty_kind {self.uncertainty_kind!r} requires the "
                f"uncertainty value it claims"
            )
        object.__setattr__(self, "value", _finite_number(self.value, "value"))
        if self.uncertainty is not None:
            uncertainty = _finite_number(self.uncertainty, "uncertainty")
            if uncertainty < 0:
                raise QuantityError("quantity uncertainty must be nonnegative")
            object.__setattr__(self, "uncertainty", uncertainty)


def assert_quantity_type(payload: Mapping[str, object]) -> TypedQuantity:
    """The ingest guard (`chem.assert_quantity_type`): a numeric value
    carries unit, uncertainty and uncertainty_kind, or is refused."""
    missing = {"value", "unit", "uncertainty_kind"} - set(payload)
    if missing:
        raise QuantityError(
            f"quantity is missing {sorted(missing)}; bare scalars are refused"
        )
    return TypedQuantity(
        value=payload["value"],  # type: ignore[arg-type]
        unit=payload["unit"],  # type: ignore[arg-type]
        uncertainty_kind=payload["uncertainty_kind"],  # type: ignore[arg-type]
        uncertainty=payload.get("uncertainty"),  # type: ignore[arg-type]
    )


def assert_property_context(content: Mapping[str, object]) -> TypedQuantity:
    """The ingest guard (`chem.assert_property_context`): a canonical
    scientific property requires property name, method, conditions, and
    a typed quantity. Anything less is refused at ingest, never
    silently compared later."""
    missing = {"property", "method", "conditions"} - set(content)
    if missing:
        raise QuantityError(
            f"property is missing {sorted(missing)}; a value without method "
            f"and conditions is a different fact than it appears to be"
        )
    for field in ("property", "method"):
        if type(content[field]) is not str or not content[field].strip():
            raise QuantityError(f"property {field} must be a non-empty string")
    conditions = content["conditions"]
    if not isinstance(conditions, Mapping) or not conditions:
        raise QuantityError("conditions must be a non-empty mapping")
    return assert_quantity_type(content)
