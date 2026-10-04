"""Software normalization checks; none of these values are physical evidence."""
from copy import deepcopy
import math
from pathlib import Path

import pytest

from ciw.physical_measurement_profiles import DEFERRED, PROFILES, get_profile, normalize


@pytest.mark.parametrize("profile,unit,value,uncertainty,expected_value,expected_uncertainty", [
    ("displacement.axial.v1", "mm", -12, 0.2, -0.012, 0.0002),
    ("displacement.axial.v1", "um", 1000, 20, 0.001, 0.00002),
    ("mass.static.v1", "g", 1500, 2, 1.5, 0.002),
    ("mass.static.v1", "mg", 1500, 2, 0.0015, 0.000002),
    ("level.liquid.v1", "cm", 125, 0.5, 1.25, 0.005),
    ("temperature.contact.v1", "degC", 20, 0.05, 293.15, 0.05),
    ("temperature.contact.v1", "degC", -20, 0, 253.15, 0),
    ("temperature.contact.v1", "K", 20, 0.1, 20, 0.1),
    ("pressure.absolute.v1", "kPa", 101.325, 0.01, 101325, 10),
    ("pressure.gauge.v1", "bar", -0.5, 0.002, -50000, 200),
    ("pressure.differential.v1", "MPa", -0.25, 0.001, -250000, 1000),
    ("dimension.length.v1", "mm", 12, 0.2, 0.012, 0.0002),
    ("dimension.thickness.v1", "um", 250, 2, 0.00025, 0.000002),
    ("flow.volume_actual.v1", "m3/h", 3600, 3.6, 1, 0.001),
    ("flow.volume_actual.v1", "L/min", 60, 0.06, 0.001, 0.000001),
    ("flow.volume_actual.v1", "mL/min", 60000, 60, 0.001, 0.000001),
    ("strain.axial.v1", "microstrain", -200, 2, -0.0002, 0.000002),
    ("water_content.volumetric.v1", "%vol", 35, 0.5, 0.35, 0.005),
    ("moisture.mass_fraction.v1", "%mass", 2, 0.1, 0.02, 0.001),
])
def test_declared_measurand_unit_conversions(profile, unit, value, uncertainty, expected_value, expected_uncertainty):
    converted, converted_uncertainty = normalize(profile, unit, value, uncertainty)
    assert converted == pytest.approx(expected_value)
    assert converted_uncertainty == pytest.approx(expected_uncertainty)


def test_temperature_offset_does_not_shift_uncertainty():
    celsius = normalize("temperature.contact.v1", "degC", 0, 0.25)
    kelvin = normalize("temperature.contact.v1", "K", 273.15, 0.25)
    assert celsius == kelvin == (273.15, 0.25)


@pytest.mark.parametrize("basis", ["absolute", "gauge", "differential"])
def test_pressure_reference_basis_is_explicit_and_not_a_unit_alias(basis):
    profile_id = f"pressure.{basis}.v1"
    profile = get_profile(profile_id)
    assert profile["quantity"] == f"{basis}_pressure"
    assert profile["condition_allowed_values"]["pressure_type"] == [basis]
    assert "pressure_type" in profile["condition_fields"]
    assert normalize(profile_id, "Pa", 120, 1) == (120, 1)
    for ambiguous in ("Pa(g)", "Pa(a)", "gauge", "absolute", "psi", "pressure"):
        with pytest.raises(ValueError, match="Unsupported unit"):
            normalize(profile_id, ambiguous, 120, 1)


@pytest.mark.parametrize("profile,unit,value", [
    ("temperature.contact.v1", "K", 0),
    ("temperature.contact.v1", "degC", -273.15),
    ("temperature.contact.v1", "degC", -274),
    ("mass.static.v1", "kg", -1),
    ("level.liquid.v1", "m", -1),
    ("pressure.absolute.v1", "Pa", -1),
    ("dimension.length.v1", "m", -1),
    ("dimension.thickness.v1", "m", -1),
    ("flow.volume_actual.v1", "m3/s", -1),
    ("water_content.volumetric.v1", "%vol", 100.01),
    ("water_content.volumetric.v1", "m3/m3", -0.1),
    ("moisture.mass_fraction.v1", "%mass", 101),
    ("moisture.mass_fraction.v1", "kg/kg", -0.1),
])
def test_nonphysical_central_values_are_rejected(profile, unit, value):
    with pytest.raises(ValueError, match="physical domain"):
        normalize(profile, unit, value, 0.1)


@pytest.mark.parametrize("profile,unit", [
    ("mass.static.v1", "kg"), ("level.liquid.v1", "m"),
    ("pressure.absolute.v1", "Pa"), ("dimension.length.v1", "m"),
    ("dimension.thickness.v1", "m"), ("flow.volume_actual.v1", "m3/s"),
    ("water_content.volumetric.v1", "m3/m3"), ("moisture.mass_fraction.v1", "kg/kg"),
])
def test_zero_boundary_is_admissible_and_uncertainty_is_not_clipped(profile, unit):
    assert normalize(profile, unit, 0, 0.25) == (0, 0.25)


@pytest.mark.parametrize("profile,unit", [
    ("water_content.volumetric.v1", "%vol"), ("moisture.mass_fraction.v1", "%mass"),
])
def test_fraction_upper_boundary_is_inclusive(profile, unit):
    assert normalize(profile, unit, 100, 2) == (1, 0.02)


@pytest.mark.parametrize("profile", [
    "displacement.axial.v1", "strain.axial.v1", "pressure.gauge.v1", "pressure.differential.v1",
])
def test_signed_measurands_retain_negative_readings(profile):
    assert normalize(profile, PROFILES[profile]["canonical_unit"], -0.5, 0.1) == (-0.5, 0.1)


@pytest.mark.parametrize("bad", [True, False, "1", None, 1j, [], {}, math.nan, math.inf, -math.inf, 10 ** 1000])
@pytest.mark.parametrize("field", ["value", "uncertainty"])
def test_non_numbers_and_nonfinite_values_are_rejected(bad, field):
    args = {"value": 1.0, "uncertainty": 0.1, field: bad}
    with pytest.raises(ValueError, match="finite number"):
        normalize("mass.static.v1", "kg", **args)


def test_negative_uncertainty_is_rejected():
    with pytest.raises(ValueError, match="nonnegative"):
        normalize("mass.static.v1", "kg", 1, -0.01)


@pytest.mark.parametrize("value,uncertainty", [(1e308, 0.1), (1, 1e308)])
def test_normalization_overflow_is_rejected(value, uncertainty):
    with pytest.raises(ValueError, match="finite numerical domain"):
        normalize("pressure.absolute.v1", "MPa", value, uncertainty)


@pytest.mark.parametrize("value,uncertainty", [(5e-324, 0.1), (1, 5e-324)])
def test_normalization_underflow_does_not_hide_nonzero_values(value, uncertainty):
    with pytest.raises(ValueError, match="underflow"):
        normalize("mass.static.v1", "mg", value, uncertainty)


@pytest.mark.parametrize("profile,unit", [
    ("mass.static.v1", "mV"), ("mass.static.v1", "N"),
    ("mass.static.v1", "dB"), ("temperature.contact.v1", "mV"),
    ("temperature.contact.v1", "C"), ("temperature.contact.v1", "degF"),
    ("flow.volume_actual.v1", "sccm"), ("flow.volume_actual.v1", "Nm3/h"),
    ("moisture.mass_fraction.v1", "%vol"), ("water_content.volumetric.v1", "%mass"),
    ("dimension.length.v1", "microstrain"), ("strain.axial.v1", "um"),
    ("level.liquid.v1", "um"), ("pressure.absolute.v1", None),
    ("pressure.absolute.v1", []),
])
def test_unsupported_or_semantically_different_units_are_rejected(profile, unit):
    with pytest.raises(ValueError, match="Unsupported unit"):
        normalize(profile, unit, 1, 0.1)


@pytest.mark.parametrize("bad", ["pressure.v1", "temperature.infrared", "flow.volume_standard_gas", "mass.true.v1", "", None, []])
def test_unknown_and_deferred_profiles_do_not_fall_back_to_generic_conversion(bad):
    with pytest.raises(ValueError, match="Unknown scalar"):
        get_profile(bad)
    with pytest.raises(ValueError, match="Unknown scalar"):
        normalize(bad, "1", 1, 0.1)


def test_profile_metadata_is_a_deep_copy():
    original = deepcopy(PROFILES["mass.static.v1"])
    detached = get_profile("mass.static.v1")
    detached["units"]["kg"][0] = 500
    detached["media"].clear()
    detached["condition_allowed_values"]["mass_convention"].append("invented")
    assert PROFILES["mass.static.v1"] == original
    assert normalize("mass.static.v1", "kg", 1, 0.1) == (1, 0.1)


def test_reference_conventions_and_different_fractions_remain_explicit():
    mass = get_profile("mass.static.v1")
    assert mass["condition_allowed_values"]["mass_convention"] == ["true_mass", "conventional_mass"]
    assert "buoyancy_correction_status" in mass["condition_fields"]
    assert get_profile("moisture.mass_fraction.v1")["condition_allowed_values"]["mass_basis"] == ["wet_basis"]
    assert PROFILES["water_content.volumetric.v1"]["quantity"] != PROFILES["moisture.mass_fraction.v1"]["quantity"]
    assert PROFILES["dimension.length.v1"]["quantity"] != PROFILES["dimension.thickness.v1"]["quantity"]
    for key in ("dimension.length.v1", "dimension.thickness.v1"):
        assert {"reference_temperature", "temperature_correction_status"} <= set(PROFILES[key]["condition_fields"])


def test_metadata_grounding_and_scope_are_available_without_hardware_claims():
    root = Path(__file__).resolve().parents[1]
    for profile in PROFILES.values():
        assert profile["status"] == "scalar_readout_adapter"
        assert profile["canonical_unit"] in profile["units"]
        assert profile["units"][profile["canonical_unit"]] == [1, 0]
        assert profile["condition_fields"]
        assert profile["source_paths"]
        assert all((root / path).is_file() for path in profile["source_paths"])
        assert "calibrated_range" not in profile
        assert "accuracy" not in profile
        for field in profile.get("condition_allowed_values", {}):
            assert field in profile["condition_fields"]
    assert not set(PROFILES).intersection(DEFERRED)
    for deferred in DEFERRED.values():
        assert deferred["status"] == "specialized_adapter_required"
        assert deferred["required_model"]
        assert all((root / path).is_file() for path in deferred["source_paths"])

def test_contact_temperature_scale_is_explicit_without_implicit_scale_conversion():
    profile = get_profile("temperature.contact.v1")
    assert "temperature_scale" in profile["condition_fields"]
    assert profile["condition_allowed_values"]["temperature_scale"] == ["ITS-90", "thermodynamic"]
    assert "no implicit conversion" in profile["description"]
    assert normalize("temperature.contact.v1", "degC", 20, 0.1) == (293.15, 0.1)


def test_pressure_source_paths_do_not_claim_native_reference_basis():
    for profile_id in ("pressure.gauge.v1", "pressure.differential.v1"):
        assert "do not establish that reference basis" in get_profile(profile_id)["description"]