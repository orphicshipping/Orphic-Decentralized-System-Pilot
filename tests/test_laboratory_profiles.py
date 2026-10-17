"""Endpoint unit and semantic boundary checks; fixtures are not physical evidence."""
import math
from pathlib import Path

import pytest

from ciw.laboratory_profiles import DEFERRED, PROFILES, get_profile, normalize


@pytest.mark.parametrize("profile_id,unit,value,uncertainty,expected,expected_u", [
    ("chemistry.amount_concentration.v1", "mol/m3", 4, 0.2, 4, 0.2),
    ("chemistry.amount_concentration.v1", "mol/L", 0.01, 0.0002, 10, 0.2),
    ("chemistry.amount_concentration.v1", "mmol/L", 10, 0.2, 10, 0.2),
    ("chemistry.mass_concentration.v1", "kg/m3", 0.25, 0.001, 0.25, 0.001),
    ("chemistry.mass_concentration.v1", "g/L", 0.25, 0.001, 0.25, 0.001),
    ("chemistry.mass_concentration.v1", "mg/L", 250, 1, 0.25, 0.001),
    ("chemistry.mass_concentration.v1", "ug/L", 250000, 1000, 0.25, 0.001),
    ("chemistry.mass_fraction.v1", "kg/kg", 0.5, 0.01, 0.5, 0.01),
    ("chemistry.mass_fraction.v1", "%mass", 50, 1, 0.5, 0.01),
    ("chemistry.mass_fraction.v1", "mg/kg", 500000, 10000, 0.5, 0.01),
    ("chemistry.mass_fraction.v1", "ug/kg", 500000000, 10000000, 0.5, 0.01),
    ("chemistry.ph.v1", "pH", 7.23, 0.02, 7.23, 0.02),
    ("materials.bulk_density.v1", "kg/m3", 1300, 2, 1300, 2),
    ("materials.bulk_density.v1", "g/cm3", 1.3, 0.002, 1300, 2),
    ("materials.bulk_density.v1", "g/mL", 1.3, 0.002, 1300, 2),
    ("polymer.apparent_shear_viscosity.v1", "Pa.s", 12.5, 0.3, 12.5, 0.3),
    ("polymer.apparent_shear_viscosity.v1", "mPa.s", 12500, 300, 12.5, 0.3),
    ("polymer.apparent_shear_viscosity.v1", "cP", 12500, 300, 12.5, 0.3),
    ("polymer.melt_mass_flow_rate.v1", "kg/s", 2e-5, 1e-6, 2e-5, 1e-6),
    ("polymer.melt_mass_flow_rate.v1", "g/10min", 12, 0.6, 2e-5, 1e-6),
    ("polymer.dsc_tg_midpoint.v1", "degC", -30, 0.4, 243.15, 0.4),
    ("polymer.dsc_tg_midpoint.v1", "K", 243.15, 0.4, 243.15, 0.4),
    ("polymer.dsc_melting_peak.v1", "degC", 170, 0.8, 443.15, 0.8),
    ("polymer.dsc_melting_peak.v1", "K", 443.15, 0.8, 443.15, 0.8),
    ("materials.tensile_strength_engineering.v1", "Pa", 20e6, 1e6, 20e6, 1e6),
    ("materials.tensile_strength_engineering.v1", "MPa", 20, 1, 20e6, 1e6),
    ("materials.tensile_strength_engineering.v1", "GPa", 0.02, 0.001, 20e6, 1e6),
    ("materials.tensile_modulus_chord.v1", "Pa", 2e9, 1e8, 2e9, 1e8),
    ("materials.tensile_modulus_chord.v1", "MPa", 2000, 100, 2e9, 1e8),
    ("materials.tensile_modulus_chord.v1", "GPa", 2, 0.1, 2e9, 1e8),
])
def test_endpoint_value_and_standard_uncertainty_unit_conversion(
        profile_id, unit, value, uncertainty, expected, expected_u):
    result, result_u = normalize(profile_id, unit, value, uncertainty)
    assert result == pytest.approx(expected)
    assert result_u == pytest.approx(expected_u)


@pytest.mark.parametrize("profile_id", [
    "polymer.dsc_tg_midpoint.v1", "polymer.dsc_melting_peak.v1",
])
def test_celsius_offset_does_not_shift_standard_uncertainty(profile_id):
    assert normalize(profile_id, "degC", 0, 0.35) == (273.15, 0.35)
    assert normalize(profile_id, "degC", 0, 0) == (273.15, 0)
    assert normalize(profile_id, "degC", 0, 0.35) == normalize(profile_id, "K", 273.15, 0.35)


@pytest.mark.parametrize("value", [-2.3, 0.0, 14.0, 15.7])
def test_ph_is_not_arbitrarily_clipped_to_zero_through_fourteen(value):
    assert normalize("chemistry.ph.v1", "pH", value, 0.02) == (value, 0.02)
    assert "percent or ratio" in get_profile("chemistry.ph.v1")["description"]


@pytest.mark.parametrize("profile_id,unit,value", [
    ("chemistry.amount_concentration.v1", "mol/L", -1e-8),
    ("chemistry.mass_concentration.v1", "mg/L", -0.1),
    ("chemistry.mass_fraction.v1", "%mass", -0.001),
    ("chemistry.mass_fraction.v1", "%mass", 100.001),
    ("chemistry.mass_fraction.v1", "kg/kg", 1.1),
    ("materials.bulk_density.v1", "kg/m3", 0),
    ("materials.bulk_density.v1", "g/mL", -1),
    ("polymer.apparent_shear_viscosity.v1", "cP", -0.1),
    ("polymer.melt_mass_flow_rate.v1", "g/10min", -0.1),
    ("polymer.dsc_tg_midpoint.v1", "K", 0),
    ("polymer.dsc_tg_midpoint.v1", "degC", -273.15),
    ("polymer.dsc_melting_peak.v1", "K", -1),
    ("polymer.dsc_melting_peak.v1", "degC", -273.15),
    ("materials.tensile_strength_engineering.v1", "MPa", -1),
    ("materials.tensile_modulus_chord.v1", "GPa", 0),
    ("materials.tensile_modulus_chord.v1", "GPa", -1),
])
def test_nonphysical_central_values_fail_closed(profile_id, unit, value):
    with pytest.raises(ValueError, match="physical domain"):
        normalize(profile_id, unit, value, 0.1)


@pytest.mark.parametrize("profile_id", [
    "chemistry.amount_concentration.v1", "chemistry.mass_concentration.v1",
    "chemistry.mass_fraction.v1", "polymer.apparent_shear_viscosity.v1",
    "polymer.melt_mass_flow_rate.v1", "materials.tensile_strength_engineering.v1",
])
def test_allowed_zero_central_value_does_not_clip_uncertainty(profile_id):
    assert normalize(profile_id, PROFILES[profile_id]["canonical_unit"], 0, 0.3) == (0, 0.3)


def test_mass_fraction_upper_endpoint_is_inclusive_without_clipping_uncertainty():
    assert normalize("chemistry.mass_fraction.v1", "%mass", 100, 3) == (1, 0.03)


@pytest.mark.parametrize("profile_id,unit", [
    ("chemistry.amount_concentration.v1", "mg/L"),
    ("chemistry.amount_concentration.v1", "M"),
    ("chemistry.mass_concentration.v1", "mol/L"),
    ("chemistry.mass_concentration.v1", "ppm"),
    ("chemistry.mass_fraction.v1", "ppm"),
    ("chemistry.mass_fraction.v1", "ppb"),
    ("chemistry.mass_fraction.v1", "wt%"),
    ("chemistry.mass_fraction.v1", "%"),
    ("chemistry.mass_fraction.v1", "mg/L"),
    ("chemistry.ph.v1", "mol/L"),
    ("chemistry.ph.v1", "1"),
    ("materials.bulk_density.v1", "specific_gravity"),
    ("polymer.apparent_shear_viscosity.v1", "cSt"),
    ("polymer.apparent_shear_viscosity.v1", "m2/s"),
    ("polymer.apparent_shear_viscosity.v1", "Pa"),
    ("polymer.melt_mass_flow_rate.v1", "cm3/10min"),
    ("polymer.melt_mass_flow_rate.v1", "MVR"),
    ("polymer.dsc_tg_midpoint.v1", "degC_onset"),
    ("polymer.dsc_melting_peak.v1", "J/g"),
    ("materials.tensile_strength_engineering.v1", "MPa_true"),
    ("materials.tensile_modulus_chord.v1", "N"),
    ("chemistry.mass_concentration.v1", " mg/L"),
    ("chemistry.mass_concentration.v1", "mg/L "),
    ("chemistry.mass_concentration.v1", "MG/L"),
    ("chemistry.mass_concentration.v1", []),
    ("chemistry.mass_concentration.v1", None),
])
def test_ambiguous_aliases_and_cross_quantity_units_are_rejected(profile_id, unit):
    with pytest.raises(ValueError, match="Unsupported unit"):
        normalize(profile_id, unit, 1, 0.1)


@pytest.mark.parametrize("bad", [True, False, "1", None, 1j, [], {}, math.nan, math.inf, -math.inf, 10 ** 1000])
@pytest.mark.parametrize("field", ["value", "uncertainty"])
def test_strict_finite_numerical_inputs(bad, field):
    args = {"value": 1.0, "uncertainty": 0.1, field: bad}
    with pytest.raises(ValueError, match="finite number"):
        normalize("chemistry.mass_concentration.v1", "mg/L", **args)


def test_negative_standard_uncertainty_is_rejected():
    with pytest.raises(ValueError, match="nonnegative"):
        normalize("chemistry.ph.v1", "pH", 7, -0.1)


@pytest.mark.parametrize("value,uncertainty", [(1e308, 0.1), (1, 1e308)])
def test_conversion_overflow_is_rejected(value, uncertainty):
    with pytest.raises(ValueError, match="finite numerical domain"):
        normalize("materials.tensile_strength_engineering.v1", "GPa", value, uncertainty)


@pytest.mark.parametrize("profile_id,unit", [
    ("chemistry.mass_concentration.v1", "ug/L"),
    ("chemistry.mass_fraction.v1", "ug/kg"),
    ("polymer.apparent_shear_viscosity.v1", "mPa.s"),
    ("polymer.melt_mass_flow_rate.v1", "g/10min"),
])
@pytest.mark.parametrize("value,uncertainty", [(5e-324, 0.1), (0.1, 5e-324)])
def test_conversion_underflow_never_changes_a_nonzero_quantity_to_zero(
        profile_id, unit, value, uncertainty):
    with pytest.raises(ValueError, match="underflow"):
        normalize(profile_id, unit, value, uncertainty)


@pytest.mark.parametrize("profile_id", list(PROFILES))
def test_profile_copy_cannot_mutate_shared_nested_metadata(profile_id):
    detached = get_profile(profile_id)
    detached["units"][detached["canonical_unit"]][0] = 999
    detached["condition_fields"].clear()
    detached["source_paths"].clear()
    assert get_profile(profile_id)["units"][detached["canonical_unit"]] == [1.0, 0.0]
    assert get_profile(profile_id)["condition_fields"]
    if "condition_allowed_values" in detached:
        detached["condition_allowed_values"]["basis"].clear()
        assert get_profile(profile_id)["condition_allowed_values"]["basis"] == ["as_received", "dry", "wet"]


@pytest.mark.parametrize("unknown", [None, [], {}, "chemistry.ph", "chemistry.pH.v1",
                                     "polymer.melt_volume_flow_rate.v1", *DEFERRED])
def test_unknown_and_deferred_profile_ids_cannot_enter_endpoint_normalization(unknown):
    with pytest.raises(ValueError, match="Unknown laboratory endpoint profile"):
        get_profile(unknown)


EXPECTED_CONTEXT = {
    "chemistry.amount_concentration.v1": ["analyte_identity", "matrix", "preparation_method", "dilution_definition", "temperature_condition"],
    "chemistry.mass_concentration.v1": ["analyte_identity", "matrix", "preparation_method", "dilution_definition", "temperature_condition"],
    "chemistry.mass_fraction.v1": ["analyte_identity", "matrix", "preparation_method", "basis"],
    "chemistry.ph.v1": ["temperature_condition", "solvent_composition", "electrode_method", "buffer_calibration"],
    "materials.bulk_density.v1": ["bulk_volume_definition", "porosity_treatment", "temperature_condition", "density_method"],
    "polymer.apparent_shear_viscosity.v1": ["temperature_condition", "shear_rate", "geometry", "sample_prehistory"],
    "polymer.melt_mass_flow_rate.v1": ["temperature_condition", "applied_load", "die_geometry", "sample_conditioning", "cut_method"],
    "polymer.dsc_tg_midpoint.v1": ["scan_rate", "thermal_history", "cycle", "atmosphere", "baseline_method", "midpoint_definition"],
    "polymer.dsc_melting_peak.v1": ["peak_definition", "baseline_method", "heating_rate", "thermal_history", "cycle"],
    "materials.tensile_strength_engineering.v1": ["original_cross_section", "specimen_geometry", "orientation", "strain_rate", "sample_conditioning", "temperature_condition"],
    "materials.tensile_modulus_chord.v1": ["strain_endpoints", "strain_method", "compliance_correction", "specimen_geometry", "sample_conditioning", "temperature_condition"],
}


@pytest.mark.parametrize("profile_id,context", EXPECTED_CONTEXT.items())
def test_measurand_context_cannot_be_replaced_by_a_generic_instrument_condition(profile_id, context):
    assert get_profile(profile_id)["condition_fields"] == context


def test_mass_fraction_basis_is_explicit_and_not_automatically_converted():
    profile = get_profile("chemistry.mass_fraction.v1")
    assert profile["condition_allowed_values"] == {"basis": ["as_received", "dry", "wet"]}
    assert "water-to-dry-solid" in profile["description"]


@pytest.mark.parametrize("profile_id", list(PROFILES))
def test_only_concentration_and_fraction_endpoints_allow_censoring(profile_id):
    allowed = {"chemistry.amount_concentration.v1", "chemistry.mass_concentration.v1",
               "chemistry.mass_fraction.v1"}
    assert get_profile(profile_id)["censoring_supported"] is (profile_id in allowed)


def test_registry_distinguishes_endpoint_support_from_hardware_or_qualification():
    root = Path(__file__).resolve().parents[1]
    assert set(PROFILES) == set(EXPECTED_CONTEXT)
    assert len(DEFERRED) == 10
    for profile in PROFILES.values():
        assert profile["status"] == "method_report_endpoint_comparison"
        assert profile["evidence_status"] == "new_method_profile_not_device_driver"
        assert "measurement_range" not in profile
        assert "acceptance_criteria" not in profile
        for source in profile["source_paths"]:
            assert (root / source).is_file()
    for family in DEFERRED.values():
        assert family["status"] == "specialized_method_adapter_required"
        assert family["required_model"]
        assert "calibration_records" in family["required_evidence"]
        assert "independent_reference_records" in family["required_evidence"]
        for source in family["source_paths"]:
            assert (root / source).is_file()


def test_existing_computational_model_bounds_do_not_become_laboratory_ranges():
    assert normalize("materials.bulk_density.v1", "kg/m3", 20000, 10) == (20000, 10)
    assert normalize("chemistry.amount_concentration.v1", "mol/m3", 2000, 5) == (2000, 5)
    for profile_id, profile in PROFILES.items():
        if profile_id != "chemistry.mass_fraction.v1":
            assert profile["physical_max"] is None
