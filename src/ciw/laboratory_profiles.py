"""Endpoint measurands for already reduced chemistry and materials reports.

These new method profiles normalize a reported result and its standard
uncertainty. They are not device drivers, raw signal reducers, calibration
models, method validation, or physical qualification. Related source paths
identify existing computational context only, not implemented laboratory
hardware. In particular, REACTION_BENCHMARK.md describes mathematical species
and numerical comparisons, not qualified experimental chemistry.

Bounds below define a measurand's central-value domain, never an instrument's
working range or an acceptance criterion. Each campaign must separately retain
its method, matrix, conditions, calibrated measurements, independent held-out
reference, uncertainty evidence and agreed limits. Registration order does not
establish development chronology.
"""
from __future__ import annotations

from copy import deepcopy
import math


def _profile(quantity, canonical_unit, units, minimum, maximum, conditions,
             description, sources=(), *, minimum_inclusive=True,
             censoring_supported=False, allowed=None):
    result = {
        "quantity": quantity,
        "canonical_unit": canonical_unit,
        "units": {unit: list(transform) for unit, transform in units.items()},
        "physical_min": minimum,
        "physical_max": maximum,
        "physical_min_inclusive": minimum_inclusive,
        "condition_fields": list(conditions),
        "censoring_supported": censoring_supported,
        "description": description,
        "status": "method_report_endpoint_comparison",
        "evidence_status": "new_method_profile_not_device_driver",
        "source_paths": list(sources),
    }
    if allowed:
        result["condition_allowed_values"] = deepcopy(allowed)
    return result


_CHEMISTRY_CONTEXT = ["docs/REACTION_BENCHMARK.md", "src/ciw/reaction_contract.py"]
_POLYMER_CONTEXT = ["docs/POLYMER_PROCESSING.md", "src/ciw/polymer_metrology.py"]
_TEMPERATURE = {"K": (1.0, 0.0), "degC": (1.0, 273.15)}
_STRESS = {"Pa": (1.0, 0.0), "MPa": (1e6, 0.0), "GPa": (1e9, 0.0)}
_CONCENTRATION_CONTEXT = [
    "analyte_identity", "matrix", "preparation_method", "dilution_definition",
    "temperature_condition",
]


PROFILES = {
    "chemistry.amount_concentration.v1": _profile(
        "amount_concentration", "mol/m3",
        {"mol/m3": (1.0, 0.0), "mol/L": (1e3, 0.0), "mmol/L": (1.0, 0.0)},
        0.0, None, _CONCENTRATION_CONTEXT,
        "Amount of the identified analyte per volume of the declared solution. "
        "Preparation and dilution basis must match; no mass-to-amount conversion "
        "or detector-response calibration is inferred. The related reaction "
        "benchmark is mathematical, not a qualified chemical method.",
        _CHEMISTRY_CONTEXT, censoring_supported=True),
    "chemistry.mass_concentration.v1": _profile(
        "mass_concentration", "kg/m3",
        {"kg/m3": (1.0, 0.0), "g/L": (1.0, 0.0), "mg/L": (1e-3, 0.0),
         "ug/L": (1e-6, 0.0)},
        0.0, None, _CONCENTRATION_CONTEXT,
        "Mass of the identified analyte per volume of the declared solution. "
        "No implicit density, molar mass, recovery or dilution correction. "
        "The related reaction benchmark supplies computational context only.",
        _CHEMISTRY_CONTEXT, censoring_supported=True),
    "chemistry.mass_fraction.v1": _profile(
        "analyte_mass_fraction", "kg/kg",
        {"kg/kg": (1.0, 0.0), "%mass": (0.01, 0.0), "mg/kg": (1e-6, 0.0),
         "ug/kg": (1e-9, 0.0)},
        0.0, 1.0, ["analyte_identity", "matrix", "preparation_method", "basis"],
        "Mass of an analyte constituent divided by total sample mass on the "
        "declared as_received, dry or wet basis. The numerator must be a "
        "constituent of that denominator. A water-to-dry-solid moisture ratio "
        "is a different quantity. Basis conversions are never inferred.",
        _CHEMISTRY_CONTEXT, censoring_supported=True,
        allowed={"basis": ["as_received", "dry", "wet"]}),
    "chemistry.ph.v1": _profile(
        "pH", "pH", {"pH": (1.0, 0.0)}, None, None,
        ["temperature_condition", "solvent_composition", "electrode_method", "buffer_calibration"],
        "Reported pH under the declared operational measurement convention. "
        "Finite pH outside 0 to 14 is permitted; that interval is not a physical "
        "bound. Compare absolute pH differences only, never percent or ratio "
        "errors. Hydrogen-ion concentration is not an interchangeable unit.",
        _CHEMISTRY_CONTEXT),
    "materials.bulk_density.v1": _profile(
        "bulk_density", "kg/m3",
        {"kg/m3": (1.0, 0.0), "g/cm3": (1e3, 0.0), "g/mL": (1e3, 0.0)},
        0.0, None,
        ["bulk_volume_definition", "porosity_treatment", "temperature_condition", "density_method"],
        "Mass divided by explicitly defined bulk volume. Void inclusion, "
        "packing or envelope convention and conditioning must be retained. "
        "Skeletal, true, tapped and loose bulk density are not interchangeable "
        "through unit conversion.",
        ["src/ciw/polymer_models.py"], minimum_inclusive=False),
    "polymer.apparent_shear_viscosity.v1": _profile(
        "apparent_shear_viscosity", "Pa.s",
        {"Pa.s": (1.0, 0.0), "mPa.s": (1e-3, 0.0), "cP": (1e-3, 0.0)},
        0.0, None, ["temperature_condition", "shear_rate", "geometry", "sample_prehistory"],
        "Reported apparent dynamic shear viscosity at a specified temperature, "
        "shear rate, geometry and sample prehistory. No Newtonian assumption, "
        "sweep reduction, complex-viscosity mapping or kinematic-viscosity "
        "conversion is supplied.",
        _POLYMER_CONTEXT),
    "polymer.melt_mass_flow_rate.v1": _profile(
        "melt_mass_flow_rate", "kg/s",
        {"kg/s": (1.0, 0.0), "g/10min": (1e-3 / 600.0, 0.0)},
        0.0, None,
        ["temperature_condition", "applied_load", "die_geometry", "sample_conditioning", "cut_method"],
        "Reported melt mass flow rate with temperature, load, die, specimen "
        "conditioning and cut method declared. Melt volume flow rate requires "
        "a separate measurand and density evidence; no MVR-to-MFR conversion.",
        _POLYMER_CONTEXT),
    "polymer.dsc_tg_midpoint.v1": _profile(
        "dsc_glass_transition_midpoint_temperature", "K", _TEMPERATURE,
        0.0, None,
        ["scan_rate", "thermal_history", "cycle", "atmosphere", "baseline_method", "midpoint_definition"],
        "DSC glass-transition midpoint endpoint from an already reduced method "
        "report. The midpoint construction, baseline, scan and thermal history "
        "must be identical across compared records. Onset, inflection and "
        "midpoint temperatures cannot be relabelled as each other.",
        _POLYMER_CONTEXT, minimum_inclusive=False),
    "polymer.dsc_melting_peak.v1": _profile(
        "dsc_melting_peak_temperature", "K", _TEMPERATURE,
        0.0, None,
        ["peak_definition", "baseline_method", "heating_rate", "thermal_history", "cycle"],
        "DSC melting-peak temperature with the selected peak and reduction "
        "method explicit. Multiple peaks, baseline choice and thermal history "
        "require retained method evidence. Onset temperature and enthalpy "
        "are different measurands.",
        _POLYMER_CONTEXT, minimum_inclusive=False),
    "materials.tensile_strength_engineering.v1": _profile(
        "engineering_tensile_strength", "Pa", _STRESS, 0.0, None,
        ["original_cross_section", "specimen_geometry", "orientation", "strain_rate",
         "sample_conditioning", "temperature_condition"],
        "Maximum reported tensile force divided by the original cross-sectional "
        "area under a declared specimen and test method. True stress, fracture "
        "stress and yield stress are distinct endpoints and are not relabelled.",
        _POLYMER_CONTEXT),
    "materials.tensile_modulus_chord.v1": _profile(
        "engineering_tensile_chord_modulus", "Pa", _STRESS, 0.0, None,
        ["strain_endpoints", "strain_method", "compliance_correction", "specimen_geometry",
         "sample_conditioning", "temperature_condition"],
        "Reported chord modulus between explicit strain endpoints using the "
        "method's engineering-stress and strain conventions. Strain measurement "
        "and compliance correction must be declared. A tangent, secant, dynamic "
        "or flexural modulus is not an interchangeable endpoint.",
        _POLYMER_CONTEXT, minimum_inclusive=False),
}


def _deferred(required_model, sources=()):
    return {
        "status": "specialized_method_adapter_required",
        "evidence_status": "new_method_family_not_device_driver",
        "required_model": required_model,
        "required_evidence": ["method_specific_reducer", "calibration_records",
                              "independent_reference_records", "agreed_acceptance_criteria"],
        "source_paths": list(sources),
    }


DEFERRED = {
    "chemistry.lc_ms_raw": _deferred(
        "Chromatographic integration, analyte identity, ion transitions, internal "
        "standards, response model, extraction recovery and matrix effects; "
        "retain blanks, detection/quantification limits and independent reference material."),
    "chemistry.gc_ms_raw": _deferred(
        "Inlet and column conditions, retention and mass-spectral identification, "
        "peak integration, response factors, volatile-sample preparation and "
        "independent reference material; library matches alone do not quantify an analyte."),
    "chemistry.uvvis_ir_raman_raw": _deferred(
        "Technique-specific wavelength/wavenumber and intensity calibration, "
        "background/baseline, optical geometry, path length, spectral preprocessing "
        "and frozen chemometric model with independent spectra and reference concentrations."),
    "chemistry.titration_raw": _deferred(
        "Titrant standardization, blank and endpoint construction, reaction "
        "stoichiometry, sample preparation, delivered-volume calibration and "
        "independent reference material."),
    "polymer.dsc_tga_dma_raw_curves": _deferred(
        "Separate DSC, TGA and DMA reducers with their own temperature, time, "
        "mass, heat-flow or force/displacement calibration; atmosphere, thermal "
        "history, baseline and endpoint algorithms with method-specific references.",
        _POLYMER_CONTEXT),
    "polymer.rheometer_sweeps": _deferred(
        "Geometry and gap, torque/force/angle calibration, temperature and shear "
        "history, wall slip/inertia/compliance treatment, steady or oscillatory "
        "mode and mode-specific reference materials.", _POLYMER_CONTEXT),
    "materials.tensile_curves_fracture": _deferred(
        "Synchronized force, strain and original geometry, calibration and machine "
        "compliance, yield/strength/modulus reducers or separate fracture-mechanics "
        "geometry and crack-length models with independent specimen references.",
        _POLYMER_CONTEXT),
    "materials.hardness_scales": _deferred(
        "Scale-specific indenter, load, dwell and surface/thickness requirements, "
        "machine verification and certified reference blocks; no generic conversion "
        "between Shore, Rockwell, Vickers or other hardness scales."),
    "polymer.sec_gpc_distributions": _deferred(
        "Detector response and molar-mass calibration or absolute detector model, "
        "solvent/column conditions, integration and distribution moments, recovery "
        "and polymer-appropriate independent reference standards."),
    "materials.xrd_microscopy": _deferred(
        "Separate diffraction and microscopy models: wavelength/angle and phase "
        "analysis for XRD; calibrated spatial geometry, preparation, segmentation "
        "and stereology for microscopy, each with independent reference specimens."),
}


def get_profile(profile_id: str) -> dict:
    """Return detached endpoint metadata; raw-method and unknown IDs fail closed."""
    if not isinstance(profile_id, str) or profile_id not in PROFILES:
        raise ValueError(f"Unknown laboratory endpoint profile: {profile_id!r}")
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
    """Normalize an already processed endpoint and its standard uncertainty.

    Uncertainty must be a nonnegative standard uncertainty in the same unit as
    the value, not a tolerance, resolution or expanded uncertainty. Offsets
    convert temperature values only. Bounds apply to central values without
    clipping uncertainty. No calibration or signal-to-property fit occurs here.
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
