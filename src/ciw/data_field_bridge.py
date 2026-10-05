"""Exact sampled-field projection into NET's existing observation consumer.

This bounded bridge accepts the native periodic fluid-wave request/result only.
It preserves the complete source, field association, staggered location, clock,
units and chosen trace. It neither interpolates nor establishes numerical or
physical validity. Retained inspection never calls the solver or verifier.
"""
from __future__ import annotations

from copy import deepcopy

from .control_checks import inspect_record
from .control_contracts import detached, keys, text
from .control_plane import ObservationBus
from .core.identities import canonical_json
from .fluid_wave_contract import (
    FIELD_NAMES, PROVIDER_ID, TRACE_LABELS, UNITS, validate_request, validate_result,
)
from .operations.runner import check_seal, digest, seal

SCHEMA = "ciw.field-observation-mapping.v1"
MAX_BYTES = 32 * 1024 * 1024
FRAME = "fluid-wave.periodic-x-positive.v1"
AUTHORITY = {
    "mapping": "exact_index_projection",
    "numerical_verification": "not_performed_by_mapping",
    "physical_validation": "not_established",
    "state_admission": "not_performed",
}
_FIELD_GRIDS = {
    "free_surface_elevation_m": "cell_x_m",
    "bottom_gauge_pressure_pa": "cell_x_m",
    "depth_averaged_velocity_m_per_s": "face_x_m",
    "volume_flux_m3_per_s": "face_x_m",
}


def _bounded(value: dict) -> None:
    detached(value)
    if len(canonical_json(value).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Field mapping exceeds its 32 MiB retained-source budget")


def _projection(request: dict, result: dict, selection: dict) -> tuple[dict, dict]:
    validate_request(request)
    validate_result(request, result)
    keys(selection, {"field", "trace", "location_index", "model_id", "entity_id", "execution_id"})
    field, trace, index = selection["field"], selection["trace"], selection["location_index"]
    if type(field) is not str or field not in FIELD_NAMES:
        raise ValueError("Choose one native wave field; scalar histories are not grid fields")
    if type(trace) is not str or trace not in TRACE_LABELS:
        raise ValueError("Choose an exact retained wave trace")
    native = result[trace]
    coordinate = _FIELD_GRIDS[field]
    if type(index) is not int or not 0 <= index < len(native[coordinate]):
        raise ValueError("location_index must select one retained grid location")
    model_id, entity_id = text(selection["model_id"]), text(selection["entity_id"])
    execution_id = selection["execution_id"]
    if execution_id is not None:
        text(execution_id)
    location = {
        "coordinate_array": coordinate,
        "association": "cell" if coordinate == "cell_x_m" else "face",
        "location_index": index,
        "x_m": native[coordinate][index],
        "coordinate_unit": "m",
        "domain_length_m": request["model"]["length_m"],
        "boundary": request["model"]["boundary"],
        "frame": FRAME,
    }
    # A caller's label cannot make two different staggered sites the same entity.
    # Resolution/index are intentionally excluded: equal exact physical sites
    # on refined grids may be compared when their clock grids also match.
    site = {name: location[name] for name in (
        "association", "x_m", "coordinate_unit", "domain_length_m", "boundary", "frame")}
    scoped_entity = "field-site:" + digest({"entity_id": entity_id, "site": site})
    mapping = {
        "source_request_ref": digest(request),
        "source_result_ref": result["record_digest"],
        "selection_ref": digest(selection),
        "location": location,
        "observation_entity_id": scoped_entity,
        "quantity": field,
        "unit": UNITS[field],
        "clock": deepcopy(result["clock"]),
        "sample_count": len(native["time_s"]),
        "interpolation": "none",
        "missingness": "native_wave_contract_requires_complete_finite_fields",
        "uncertainty": "not_declared_by_native_wave_profile",
    }
    bus = ObservationBus(capacity=max(1, len(native["time_s"])))
    for time_s, row in zip(native["time_s"], native[field]):
        bus.observe(
            identity={"model_id": model_id, "entity_id": scoped_entity, "execution_id": execution_id},
            clock={"id": result["clock"]["clock_id"], "time_s": time_s},
            frame=FRAME, quantity=field, value=row[index], unit=UNITS[field],
            provenance={"provider": PROVIDER_ID, "semantics": "simulated", "sources": [
                mapping["source_request_ref"], mapping["source_result_ref"], mapping["selection_ref"]]},
        )
    return mapping, bus.snapshot()


def wave_observations(request: dict, result: dict, *, field: str, location_index: int,
                      model_id: str, entity_id: str, execution_id: str | None = None,
                      trace: str = "primary") -> dict:
    """Project one explicitly selected cell/face history, retaining all inputs.

    ``execution_id`` is a caller-supplied occurrence label, not authenticated by
    a result seal. Omit it when the source occurrence is unknown. The generated
    observation entity includes the exact physical site, so comparisons refuse
    to silently conflate different grid positions or cell/face associations.
    """
    request, result = detached(request), detached(result)
    selection = {"field": field, "trace": trace, "location_index": location_index,
                 "model_id": model_id, "entity_id": entity_id, "execution_id": execution_id}
    mapping, stream = _projection(request, result, selection)
    value = seal({"schema": SCHEMA, "source_family": "fluid-wave",
                  "native_request": request, "native_result": result,
                  "selection": selection, "mapping": mapping,
                  "observation_stream": stream, "authority": deepcopy(AUTHORITY)})
    _bounded(value)
    return value


def validate(value: dict) -> dict:
    """Validate exact retained correspondence without re-running any physics."""
    _bounded(value)
    keys(value, {"schema", "source_family", "native_request", "native_result", "selection",
                 "mapping", "observation_stream", "authority", "record_digest"})
    check_seal(value)
    if (value["schema"] != SCHEMA or value["source_family"] != "fluid-wave"
            or value["authority"] != AUTHORITY):
        raise ValueError("Unsupported field mapping family, schema or authority")
    mapping, stream = _projection(value["native_request"], value["native_result"], value["selection"])
    inspect_record(value["observation_stream"])
    if (canonical_json(value["mapping"]) != canonical_json(mapping)
            or canonical_json(value["observation_stream"]) != canonical_json(stream)):
        raise ValueError("Field mapping contradicts the retained native source or selection")
    return deepcopy(value)


def observation_stream(value: dict) -> dict:
    """Return the existing typed observation record for numerical comparison."""
    return validate(value)["observation_stream"]
