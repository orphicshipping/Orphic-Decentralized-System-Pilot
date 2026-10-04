"""Measurand-specific normalization for declared scalar instrument readouts.

These profiles describe software support, not available hardware, calibrated
operating ranges, metrological traceability, or physical qualification. Unit
conversion does not calibrate a sensor or change a measurand's reference basis.
Profile order has no chronological meaning; chronology requires retained source
evidence and belongs to the instrument register.
"""
from __future__ import annotations

from copy import deepcopy
import math


def _profile(quantity, canonical_unit, media, units, minimum, maximum, conditions,
             description, sources, *, minimum_inclusive=True, allowed=None):
    result = {
        "quantity": quantity,
        "canonical_unit": canonical_unit,
        "media": list(media),
        "units": {unit: list(transform) for unit, transform in units.items()},
        "physical_min": minimum,
        "physical_max": maximum,
        "physical_min_inclusive": minimum_inclusive,
        "condition_fields": list(conditions),
        "description": description,
        "source_paths": list(sources),
        "status": "scalar_readout_adapter",
    }
    if allowed:
        result["condition_allowed_values"] = deepcopy(allowed)
    return result


_LENGTH = {"m": (1.0, 0.0), "mm": (1e-3, 0.0), "um": (1e-6, 0.0)}
_PRESSURE = {"Pa": (1.0, 0.0), "kPa": (1e3, 0.0), "MPa": (1e6, 0.0), "bar": (1e5, 0.0)}
_POLYMER = ["src/ciw/polymer_contract.py", "src/ciw/polymer_metrology.py"]
_IRRIGATION = ["src/ciw/irrigation_contract.py", "src/ciw/irrigation_metrology.py"]


PROFILES = {
    "displacement.axial.v1": _profile(
        "axial_displacement", "m", ["solid", "machine"], _LENGTH, None, None,
        ["axis_definition", "datum_definition", "mounting_description"],
        "Signed scalar displacement along a declared axis relative to a declared datum; not absolute length.",
        ["src/ciw/adapters/oscillator.py", *_POLYMER]),
    "mass.static.v1": _profile(
        "static_mass", "kg", ["solid", "liquid"],
        {"kg": (1.0, 0.0), "g": (1e-3, 0.0), "mg": (1e-6, 0.0)}, 0.0, None,
        ["mass_convention", "buoyancy_correction_status", "tare_definition", "loading_condition"],
        "Nonnegative static mass. Declare true_mass or conventional_mass consistently for observation, "
        "prediction and reference. Unit normalization never converts between those conventions or applies buoyancy corrections.",
        ["docs/ADAPTERS.md", "src/ciw/adapters/fsrt_records.py"],
        allowed={"mass_convention": ["true_mass", "conventional_mass"]}),
    "level.liquid.v1": _profile(
        "liquid_level", "m", ["liquid"],
        {"m": (1.0, 0.0), "cm": (1e-2, 0.0), "mm": (1e-3, 0.0)}, 0.0, None,
        ["datum_definition", "installation_geometry", "surface_condition"],
        "Nonnegative liquid height above the declared lower datum; hydrostatic pressure conversion and tank volume require separate models.",
        ["docs/CALIBRATED_WINDOW.md", "docs/ACQUIRED_STREAM.md"]),
    "temperature.contact.v1": _profile(
        "contact_temperature", "K", ["solid", "liquid", "gas"],
        {"K": (1.0, 0.0), "degC": (1.0, 273.15)}, 0.0, None,
        ["temperature_scale", "contact_location", "contact_method", "immersion_or_attachment", "equilibration_condition"],
        "Positive temperature on the declared scale from a contact readout; ITS-90 and thermodynamic conventions remain distinct, with no implicit conversion. "
        "Installation and dynamic effects remain in the uncertainty budget. "
        "Radiometric or infrared inference requires its own adapter.",
        ["src/ciw/fluid_thermal_measurements.py", *_POLYMER], minimum_inclusive=False,
        allowed={"temperature_scale": ["ITS-90", "thermodynamic"]}),
    "pressure.absolute.v1": _profile(
        "absolute_pressure", "Pa", ["liquid", "gas", "polymer_melt"], _PRESSURE, 0.0, None,
        ["pressure_type", "vacuum_reference_definition", "measurement_port", "pressure_tap_elevation", "temperature_condition"],
        "Nonnegative pressure relative to vacuum. No automatic conversion from gauge or differential pressure.",
        ["docs/ATMOSPHERIC_ENGINE.md", "src/ciw/fluid_thermal_measurements.py"], allowed={"pressure_type": ["absolute"]}),
    "pressure.gauge.v1": _profile(
        "gauge_pressure", "Pa", ["liquid", "gas", "polymer_melt"], _PRESSURE, None, None,
        ["pressure_type", "gauge_reference_definition", "measurement_port", "pressure_tap_elevation", "temperature_condition"],
        "Signed pressure relative to a declared gauge reference. Ambient/reference pressure is not inferred. "
        "This new profile supplies explicit semantics; source family pressure channels do not establish that reference basis.",
        [*_IRRIGATION, *_POLYMER], allowed={"pressure_type": ["gauge"]}),
    "pressure.differential.v1": _profile(
        "differential_pressure", "Pa", ["liquid", "gas", "polymer_melt"], _PRESSURE, None, None,
        ["pressure_type", "positive_port_definition", "negative_port_definition", "port_elevation_difference", "temperature_condition"],
        "Signed positive-port minus negative-port pressure. Port order and common-mode conditions require declared evidence. "
        "This new profile supplies explicit semantics; source family pressure channels do not establish that reference basis.",
        [*_IRRIGATION, *_POLYMER], allowed={"pressure_type": ["differential"]}),
    "dimension.length.v1": _profile(
        "length", "m", ["solid", "polymer"], _LENGTH, 0.0, None,
        ["feature_definition", "reference_temperature", "temperature_correction_status", "measurement_force"],
        "Nonnegative scalar feature length at a declared temperature convention; no thermal expansion or image geometry correction is inferred.",
        [*_POLYMER, "src/ciw/bim_quantity.py"]),
    "dimension.thickness.v1": _profile(
        "thickness", "m", ["solid", "polymer"], _LENGTH, 0.0, None,
        ["feature_definition", "reference_temperature", "temperature_correction_status", "measurement_force"],
        "Nonnegative scalar thickness with defined opposing surfaces and contact force; acoustic transit-time conversion requires a separate material model.",
        _POLYMER),
    "flow.volume_actual.v1": _profile(
        "actual_volumetric_flow_rate", "m3/s", ["liquid", "gas"],
        {"m3/s": (1.0, 0.0), "m3/h": (1.0 / 3600.0, 0.0),
         "L/min": (1e-3 / 60.0, 0.0), "mL/min": (1e-6 / 60.0, 0.0)}, 0.0, None,
        ["flow_direction", "measurement_section", "actual_pressure", "actual_temperature", "fluid_composition"],
        "Nonnegative flow magnitude in the declared direction, at actual measurement conditions. "
        "Standard or normalized gas volume is a different measurand and is not converted here.",
        _IRRIGATION),
    "strain.axial.v1": _profile(
        "axial_strain", "1", ["solid"], {"1": (1.0, 0.0), "microstrain": (1e-6, 0.0)}, None, None,
        ["axis_definition", "gauge_length", "zero_load_reference", "temperature_compensation_status", "strain_definition"],
        "Signed scalar axial strain with a declared strain definition and reference length; bridge voltage and gauge-factor conversion are not supplied.",
        _POLYMER),
    "water_content.volumetric.v1": _profile(
        "volumetric_water_content", "m3/m3", ["soil"],
        {"m3/m3": (1.0, 0.0), "%vol": (0.01, 0.0)}, 0.0, 1.0,
        ["sampling_volume", "soil_description", "temperature_condition", "salinity_condition"],
        "Water volume divided by bulk soil volume. Soil-specific dielectric calibration and spatial representativeness must be evidenced separately.",
        _IRRIGATION),
    "moisture.mass_fraction.v1": _profile(
        "water_mass_fraction", "kg/kg", ["solid", "polymer"],
        {"kg/kg": (1.0, 0.0), "%mass": (0.01, 0.0)}, 0.0, 1.0,
        ["mass_basis", "sample_conditioning", "water_selectivity", "sampling_method"],
        "Wet-basis water mass divided by total sample mass, bounded by [0,1]. Dry-basis water/dry-solid ratios are a different measurand; "
        "loss on drying is not automatically water content.",
        _POLYMER, allowed={"mass_basis": ["wet_basis"]}),
}


def _deferred(reason, sources=()):
    return {"status": "specialized_adapter_required", "required_model": reason,
            "source_paths": list(sources),
            "evidence_status": "source_grounded_family_scope" if sources else "proposed_no_source_adapter"}


DEFERRED = {
    "vibration.vector_rms": _deferred(
        "Axis/frame mapping, sample rate, anti-aliasing, frequency band, filtering, window and RMS definition, plus dynamic calibration.",
        ["src/ciw/polymer_contract.py", "src/ciw/dsp.py"]),
    "optical.spectral": _deferred(
        "Wavelength response, radiometric calibration, geometry, dark/background correction and spectral bandwidth."),
    "temperature.infrared": _deferred(
        "Emissivity, spectral response, reflected/background radiation, field of view and radiometric transfer model.",
        _POLYMER),
    "electrical.dc": _deferred(
        "Explicit electrical quantity, circuit loading, lead compensation, polarity, grounding and instrument transfer model."),
    "electrical.ac": _deferred(
        "Explicit electrical quantity, frequency response, bandwidth, phase, waveform and RMS/peak semantics."),
    "acoustic": _deferred(
        "Reference pressure, frequency weighting, time weighting, bandwidth, microphone response and acoustic geometry."),
    "force.raw_sensor": _deferred(
        "Force direction and load case, raw-channel excitation, bridge/sensitivity model, hysteresis, creep and mounting effects."),
    "flow.volume_standard_gas": _deferred(
        "Declared reference temperature and pressure, gas composition, compressibility and actual-to-standard volume model.",
        _IRRIGATION),
    "energy.gpu_counter": _deferred(
        "Counter identity and units, reset/wraparound behavior, clock alignment, integration window and independent external energy reference.",
        ["src/ciw/energy_nvml.py", "src/ciw/energy_records.py"]),
    "vision.geometry": _deferred(
        "Camera intrinsics/extrinsics, distortion, scale reference, segmentation, coordinate frames and spatial uncertainty model.",
        ["src/ciw/polymer_contract.py", "src/ciw/geometry_translation_contract.py"]),
}


def get_profile(profile_id: str) -> dict:
    """Return detached profile metadata; unknown/deferred identifiers fail closed."""
    if not isinstance(profile_id, str) or profile_id not in PROFILES:
        raise ValueError(f"Unknown scalar measurement profile: {profile_id!r}")
    return deepcopy(PROFILES[profile_id])


def _finite_number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(converted):
        raise ValueError(f"{label} must be a finite number")
    return converted


def normalize(profile_id: str, unit: str, value, uncertainty) -> tuple[float, float]:
    """Convert a scalar readout and its standard uncertainty to canonical units.

    Uncertainty must already be a nonnegative standard uncertainty (not an
    expanded uncertainty, tolerance or resolution). Affine unit offsets affect
    the value only. Physical bounds apply to the central value; uncertainty
    intervals are not clipped at those bounds. No calibration is performed.
    """
    profile = get_profile(profile_id)
    if not isinstance(unit, str) or unit not in profile["units"]:
        raise ValueError(f"Unsupported unit {unit!r} for {profile_id}")
    scalar = _finite_number(value, "value")
    standard_uncertainty = _finite_number(uncertainty, "uncertainty")
    if standard_uncertainty < 0:
        raise ValueError("uncertainty must be nonnegative")
    scale, offset = profile["units"][unit]
    canonical_value = scale * scalar + offset
    canonical_uncertainty = abs(scale) * standard_uncertainty
    if not math.isfinite(canonical_value) or not math.isfinite(canonical_uncertainty):
        raise ValueError("Unit conversion exceeds the finite numerical domain")
    if scalar != 0.0 and canonical_value == 0.0 and offset == 0.0:
        raise ValueError("Unit conversion loses a nonzero value to underflow")
    if standard_uncertainty != 0.0 and canonical_uncertainty == 0.0:
        raise ValueError("Unit conversion loses nonzero uncertainty to underflow")
    lower, upper = profile["physical_min"], profile["physical_max"]
    if lower is not None and (canonical_value < lower or (
            canonical_value == lower and not profile["physical_min_inclusive"])):
        raise ValueError(f"value is below the physical domain of {profile_id}")
    if upper is not None and canonical_value > upper:
        raise ValueError(f"value is above the physical domain of {profile_id}")
    return canonical_value, canonical_uncertainty
