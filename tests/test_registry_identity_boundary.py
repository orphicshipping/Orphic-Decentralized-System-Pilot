"""Regression proposal for the native CapabilityRegistry binding boundary.

These are contract-only callback fixtures, not scientific provider qualification.
Run from the NET source root with:
    python -m pytest -q -c pyproject.toml /absolute/path/to/this/file.py

The pairs below are equal under Python equality but distinct under CIW's native
v1 canonical JSON encoding. No new encoding or scientific semantics are assumed.
"""

from copy import deepcopy

import pytest

from ciw.adapters.protocol import AdapterRefusal, InstrumentManifest
from ciw.control_plane import CapabilityRegistry
from ciw.core.identities import canonical_json
from ciw.operations.registry import Operation


OPERATION_ID = "identity-boundary-fixture.inspect.v1"
PROVIDER_ID = "identity-boundary-fixture"
PAYLOAD = {"source_class": "contract_fixture", "value": "returned"}

DISTINCT_PAIRS = [
    pytest.param(True, 1, id="bool-int"),
    pytest.param(False, 0, id="false-int-zero"),
    pytest.param(True, 1.0, id="bool-float"),
    pytest.param(1, 1.0, id="int-float"),
    pytest.param(0, 0.0, id="int-float-zero"),
    pytest.param(-0.0, 0.0, id="signed-zero"),
]


def runtime(value, nested=False):
    variant = {"build": [{"feature": value}]} if nested else value
    return {"provider": PROVIDER_ID, "variant": deepcopy(variant)}


def bound_operation(expected, live, execute):
    registry = CapabilityRegistry()
    manifest = InstrumentManifest(
        PROVIDER_ID,
        role="operation_provider",
        supported_operations=(OPERATION_ID,),
    )
    registry.advertise(
        manifest,
        runtime=expected,
        capabilities={OPERATION_ID: ["fixture.identity-boundary"]},
    )
    registry.bind(Operation(OPERATION_ID, "backend", execute, lambda: live))
    return registry.operations.get(OPERATION_ID)


@pytest.mark.parametrize("declared,changed", DISTINCT_PAIRS)
@pytest.mark.parametrize("nested", [False, True], ids=["scalar", "nested-list-object"])
def test_native_distinction_refuses_before_any_provider_call(declared, changed, nested):
    expected, live = runtime(declared, nested), runtime(changed, nested)
    # Independent case preconditions establish the Python/native disagreement.
    assert expected == live
    assert canonical_json(expected) != canonical_json(live)
    calls = []

    def execute(run, parameters):
        calls.append("provider")
        return deepcopy(PAYLOAD)

    operation = bound_operation(expected, live, execute)
    refusal = None
    try:
        operation.execute({}, {})
    except AdapterRefusal as exc:
        refusal = exc

    assert calls == [], "Mismatched runtime reached the provider callback"
    assert refusal is not None, "A distinct native runtime identity was accepted"
    assert refusal.code == "runtime_mismatch"


@pytest.mark.parametrize("declared,changed", DISTINCT_PAIRS)
@pytest.mark.parametrize("nested", [False, True], ids=["scalar", "nested-list-object"])
def test_native_distinction_refuses_returned_result_after_provider_mutation(declared, changed, nested):
    expected, live = runtime(declared, nested), runtime(declared, nested)
    changed_runtime = runtime(changed, nested)
    assert expected == changed_runtime
    assert canonical_json(expected) != canonical_json(changed_runtime)
    calls = []

    def execute(run, parameters):
        calls.append("provider")
        # The callback owns this live dict, and mutates it during execution.
        live.clear()
        live.update(deepcopy(changed_runtime))
        return deepcopy(PAYLOAD)

    operation = bound_operation(expected, live, execute)
    returned, refusal = None, None
    try:
        returned = operation.execute({}, {})
    except AdapterRefusal as exc:
        refusal = exc

    assert calls == ["provider"]
    assert refusal is not None, "A result escaped after the native runtime identity changed"
    assert refusal.code == "runtime_mismatch"
    assert returned is None


@pytest.mark.parametrize("value", [True, False, 1, 1.0, 0, 0.0, -0.0])
@pytest.mark.parametrize("nested", [False, True], ids=["scalar", "nested-list-object"])
def test_exact_native_identity_executes_and_returns_payload(value, nested):
    expected, live = runtime(value, nested), runtime(value, nested)
    calls = []

    def execute(run, parameters):
        calls.append("provider")
        return deepcopy(PAYLOAD)

    operation = bound_operation(expected, live, execute)
    assert canonical_json(operation.runtime_identity()) == canonical_json(expected)
    assert calls == []
    assert operation.execute({}, {}) == PAYLOAD
    assert calls == ["provider"]


def test_native_object_key_order_is_not_an_identity_change():
    expected = runtime({"a": 1, "b": [True, -0.0]})
    live = {"variant": {"b": [True, -0.0], "a": 1}, "provider": PROVIDER_ID}
    assert list(expected) != list(live)
    assert canonical_json(expected) == canonical_json(live)
    calls = []

    def execute(run, parameters):
        calls.append("provider")
        # Dictionary insertion order can also change during execution.
        variant = live["variant"].pop("b")
        live["variant"]["b"] = variant
        return deepcopy(PAYLOAD)

    operation = bound_operation(expected, live, execute)
    assert operation.execute({}, {}) == PAYLOAD
    assert calls == ["provider"]
    assert canonical_json(operation.runtime_identity()) == canonical_json(expected)


@pytest.mark.parametrize("phase", ["before", "during"])
def test_ordinary_runtime_revision_change_still_refuses(phase):
    expected, live = runtime("revision-one"), runtime("revision-one")
    calls = []

    def execute(run, parameters):
        calls.append("provider")
        live["variant"] = "revision-two"
        return deepcopy(PAYLOAD)

    operation = bound_operation(expected, live, execute)
    if phase == "before":
        live["variant"] = "revision-two"
    with pytest.raises(AdapterRefusal) as caught:
        operation.execute({}, {})
    assert caught.value.code == "runtime_mismatch"
    assert calls == ([] if phase == "before" else ["provider"])
