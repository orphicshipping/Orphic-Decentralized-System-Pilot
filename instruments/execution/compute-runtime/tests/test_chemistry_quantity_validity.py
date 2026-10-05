"""Scientific value validity through direct guards and retained acquisition.

These adversarial records test software contracts, not physical calibration or
an empirical chemistry rejection rate. Original values survive quarantine.
"""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from evidence.pool import EvidencePool
from evidence.quarantine import Quarantine
from scout.property_extraction import PropertyExtractor
from scout.interface import RawDocument
from structures.ingest import PROPERTY_CONTEXT, QUANTITY_TYPED, chemistry_content_gate, ingest_documents
from structures.quantity import QuantityError, TypedQuantity, assert_property_context, assert_quantity_type

CLEAN = ("PROPERTY: glass_transition | method=DSC | conditions=heating_rate:10 "
         "| value=373 | unit=K | uncertainty_kind=absent")


def _source(*lines):
    class Source:
        def fetch(self):
            return (RawDocument(source_name="quantity-contract-regression", source_kind="paper",
                content="\n".join(lines), locator="test://chemistry/quantity-contract",
                retrieval_method="manual_entry", retrieved_at="2026-10-05T00:00:00Z"),)
    return Source()

BASE = {"property": "glass_transition", "method": "DSC", "conditions": {"heating_rate": 10},
        "value": 373, "unit": "K", "uncertainty_kind": "stated", "uncertainty": 1}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, False,
                                   None, [], {}, "not-numeric", "", "1e999", 10 ** 400])
def test_invalid_value_is_a_quantity_refusal(value):
    payload = {**BASE, "value": value}
    with pytest.raises(QuantityError, match="quantity value"):
        assert_property_context(payload)
    assert chemistry_content_gate(payload) == (QUANTITY_TYPED,)


@pytest.mark.parametrize("uncertainty", [-1, "-0.1", float("nan"), float("inf"),
                                        True, False, [], {}, "not-numeric", "1e999"])
def test_invalid_uncertainty_is_a_quantity_refusal(uncertainty):
    payload = {**BASE, "uncertainty": uncertainty}
    with pytest.raises(QuantityError, match="quantity uncertainty"):
        assert_property_context(payload)
    assert chemistry_content_gate(payload) == (QUANTITY_TYPED,)


@pytest.mark.parametrize("unit", [None, True, 1, [], {}, "", " \t"])
def test_unit_is_never_synthesized_by_string_coercion(unit):
    payload = {**BASE, "unit": unit}
    with pytest.raises(QuantityError, match="without a unit"):
        assert_property_context(payload)
    assert chemistry_content_gate(payload) == (QUANTITY_TYPED,)


@pytest.mark.parametrize("field", ["property", "method"])
@pytest.mark.parametrize("value", [None, False, 0, [], {}, "", " \t"])
def test_empty_or_nontext_property_context_refuses(field, value):
    payload = {**BASE, field: value}
    with pytest.raises(QuantityError, match="non-empty string"):
        assert_property_context(payload)
    assert chemistry_content_gate(payload) == (PROPERTY_CONTEXT,)


@pytest.mark.parametrize("kind", ["stated", "estimated", "propagated"])
@pytest.mark.parametrize("value,uncertainty", [(0, 0), (-2.5, 0.3), (1e-300, 1e-310), ("373", "0.1")])
def test_valid_numbers_and_explicit_uncertainty_postures_are_preserved(kind, value, uncertainty):
    quantity = assert_quantity_type({"value": value, "unit": "K", "uncertainty_kind": kind,
                                     "uncertainty": uncertainty})
    assert quantity.value == float(value)
    assert quantity.uncertainty == float(uncertainty)
    assert quantity.uncertainty_kind == kind


def test_absent_uncertainty_is_not_zero_and_direct_constructor_checks_too():
    assert TypedQuantity(373, "K", "absent").uncertainty is None
    with pytest.raises(QuantityError):
        TypedQuantity(float("nan"), "K", "absent")
    with pytest.raises(QuantityError):
        TypedQuantity(373, "K", "stated", -1)


@pytest.mark.parametrize("field,value", [("value", "nan"), ("value", "inf"),
    ("value", "not-numeric"), ("uncertainty", "-1"), ("uncertainty", "nan"),
    ("uncertainty", "not-numeric")])
def test_invalid_values_arrive_and_are_quarantined_without_losing_valid_neighbor(field, value):
    invalid = ("PROPERTY: glass_transition | method=DSC | conditions=heating_rate:10 "
               "| value=373 | unit=K | uncertainty_kind=stated | uncertainty=1")
    invalid = invalid.replace(field + "=" + ("373" if field == "value" else "1"), field + "=" + value)
    quarantine = Quarantine()
    findings, failures = ingest_documents(_source(invalid, CLEAN), PropertyExtractor(), EvidencePool(),
                                          quarantine=quarantine)
    assert len(findings) == len(failures) == len(quarantine.records) == 1
    assert failures[0].stage == "content_gate"
    assert {error.code for error in failures[0].errors} == {QUANTITY_TYPED}
    held = quarantine.records[0]
    assert held.failing_invariant_ids == (QUANTITY_TYPED,)
    assert held.source_ref
    if value == "nan":
        assert math.isnan(held.payload[field])
    else:
        assert str(held.payload[field]) == ("-1" if value == "-1" else value)
    assert findings[0].observation.content["uncertainty_kind"] == "absent"


@pytest.mark.parametrize("field", ["property", "method"])
def test_empty_context_arrives_and_is_quarantined(field):
    text = CLEAN.replace("glass_transition" if field == "property" else "method=DSC",
                         "" if field == "property" else "method=")
    quarantine = Quarantine()
    findings, failures = ingest_documents(_source(text), PropertyExtractor(), EvidencePool(), quarantine=quarantine)
    assert not findings
    assert len(failures) == 1
    assert quarantine.records[0].failing_invariant_ids == (PROPERTY_CONTEXT,)
