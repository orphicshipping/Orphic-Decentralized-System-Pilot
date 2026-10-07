"""Adversarial semantic names: distinct operations must not collapse in discovery."""
from types import SimpleNamespace

import pytest

from ciw.adapters.protocol import InstrumentManifest
from ciw.control_gateway import semantic_registry
from ciw.control_plane import CapabilityRegistry


def mapped(labels):
    concrete = CapabilityRegistry()
    manifest = InstrumentManifest(instrument_id="review.names.v1", role="record_only", units={"q": "1"},
        frames=("local",), sampling={"kind": "declared"}, supported_operations=tuple(labels), calibration_requirements={})
    concrete.advertise(manifest, runtime={"fixture": "no execution"},
                       capabilities={operation: [label] for operation, label in labels.items()})
    registry, names = semantic_registry(SimpleNamespace(_registry=concrete))
    assert len(names) == len(set(names.values())) == len(labels)
    for operation, name in names.items():
        lowerings = registry.catalog()["lowerings"][name]
        assert len(lowerings) == 1 and lowerings[0]["operation_id"] == operation
        assert lowerings[0]["bound"] is False
    return names


@pytest.mark.parametrize("labels", [
    {"review.first.v1": "sensor.filter", "review.second.v1": "sensor.filter.v1"},
    {"review.first.v1": "invalid_label", "review.second.v1": "review.first.v1"},
])
def test_normalized_and_fallback_names_remain_distinct_independent_of_insertion(labels):
    forward = mapped(labels)
    backward = mapped(dict(reversed(list(labels.items()))))
    assert forward == backward


def test_builtin_alias_is_preserved_when_another_preference_collides():
    labels = {"statistics.v1": "statistics", "review.extra.v1": "analysis.statistics.v1"}
    names = mapped(labels)
    assert names["statistics.v1"] == "analysis.statistics.v1"
    assert mapped(dict(reversed(list(labels.items())))) == names
