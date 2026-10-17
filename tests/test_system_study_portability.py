"""Tail-only portability allowance must not weaken retained study contracts."""
import copy
import math

import pytest

from ciw.operations.runner import check_seal, seal
from ciw.system_spec import demo_spec
from ciw.system_study import TAIL_LIMIT_K, run_temporal_study, validate_temporal_study


def _source(**thermal_values):
    source = demo_spec(cells=16)
    source["clock"].update(duration=20.0, dt=5.0)
    for coupling in source["couplings"]:
        coupling["timing"]["interval_s"] = 5.0
    for name, value in thermal_values.items():
        node = next(node for node in source["nodes"] if name in node["parameters"])
        node["parameters"][name]["value"] = value
    return source


@pytest.fixture(scope="module")
def baseline():
    source = _source()
    return source, run_temporal_study(source)


@pytest.mark.parametrize("direction", [math.inf, -math.inf], ids=["up", "down"])
def test_temporal_reference_accepts_cross_platform_roundoff(baseline, direction):
    source, report = baseline
    altered = copy.deepcopy(report)
    tail = altered["analytic_reference"]["samples"][1]["tail_bound_k"]
    assert 0 < tail < TAIL_LIMIT_K
    altered["analytic_reference"]["samples"][1]["tail_bound_k"] = math.nextafter(tail, direction)
    seal(altered)
    untouched = copy.deepcopy(altered)
    assert validate_temporal_study(altered, source) is altered
    assert altered == untouched
    check_seal(altered)


@pytest.mark.parametrize("factor", [0.0, 0.99, 1.01, 2.0])
def test_material_tail_changes_below_hard_limit_are_rejected(baseline, factor):
    source, report = baseline
    altered = copy.deepcopy(report)
    sample = altered["analytic_reference"]["samples"][1]
    sample["tail_bound_k"] *= factor
    assert sample["tail_bound_k"] < TAIL_LIMIT_K
    seal(altered)
    with pytest.raises(ValueError, match="scalar contract"):
        validate_temporal_study(altered, source)


def test_tail_roundoff_allowance_never_relaxes_hard_limit(baseline, monkeypatch):
    source, report = baseline
    altered = copy.deepcopy(report)
    expected = copy.deepcopy(report["analytic_reference"]["samples"])
    expected[1]["tail_bound_k"] = TAIL_LIMIT_K
    altered["analytic_reference"]["samples"][1]["tail_bound_k"] = math.nextafter(TAIL_LIMIT_K, math.inf)
    monkeypatch.setattr("ciw.system_study._reference_schedule", lambda *args: (
        expected, report["work_budget"]["reference_cell_modes"]))
    seal(altered)
    with pytest.raises(ValueError, match="tail exceeds the declared bound"):
        validate_temporal_study(altered, source)


@pytest.mark.parametrize("field,value", [
    ("status", "NOT_ASSESSED"),
    ("series_terms", 8),
    ("reason", "different diagnostic"),
    ("time_s", math.nextafter(5.0, math.inf)),
])
def test_tail_allowance_keeps_other_sample_fields_exact(baseline, field, value):
    source, report = baseline
    altered = copy.deepcopy(report)
    sample = altered["analytic_reference"]["samples"][1]
    assert sample[field] != value
    sample[field] = value
    seal(altered)
    with pytest.raises(ValueError):
        validate_temporal_study(altered, source)


def test_tail_allowance_keeps_sample_order_exact(baseline):
    source, report = baseline
    altered = copy.deepcopy(report)
    samples = altered["analytic_reference"]["samples"]
    samples[1], samples[2] = samples[2], samples[1]
    seal(altered)
    with pytest.raises(ValueError):
        validate_temporal_study(altered, source)


@pytest.mark.parametrize("field", ["solver_cell_steps", "reference_cell_modes"])
def test_tail_allowance_keeps_work_accounting_exact(baseline, field):
    source, report = baseline
    altered = copy.deepcopy(report)
    altered["work_budget"][field] += 1
    seal(altered)
    with pytest.raises(ValueError, match="work accounting"):
        validate_temporal_study(altered, source)


@pytest.mark.parametrize("value", [-1e-30, True, "0", None])
def test_tail_allowance_rejects_invalid_tail_values(baseline, value):
    source, report = baseline
    altered = copy.deepcopy(report)
    altered["analytic_reference"]["samples"][1]["tail_bound_k"] = value
    seal(altered)
    with pytest.raises(ValueError, match="Fourier tail"):
        validate_temporal_study(altered, source)


@pytest.mark.parametrize("index", [0, 1])
def test_exact_initial_and_equilibrium_tails_remain_zero(index):
    source = _source(left_temperature_k=293.15, right_temperature_k=293.15)
    report = run_temporal_study(source)
    report["analytic_reference"]["samples"][index]["tail_bound_k"] = math.nextafter(0.0, math.inf)
    seal(report)
    with pytest.raises(ValueError, match="scalar contract"):
        validate_temporal_study(report, source)


def test_unassessed_tail_remains_none():
    source = _source(k_w_per_m_k=1e-12)
    report = run_temporal_study(source)
    assert report["analytic_reference"]["samples"][1]["status"] == "NOT_ASSESSED"
    report["analytic_reference"]["samples"][1]["tail_bound_k"] = 0.0
    seal(report)
    with pytest.raises(ValueError, match="unassessed reference"):
        validate_temporal_study(report, source)


def test_roundoff_change_still_requires_valid_content_seal(baseline):
    source, report = baseline
    altered = copy.deepcopy(report)
    sample = altered["analytic_reference"]["samples"][1]
    sample["tail_bound_k"] = math.nextafter(sample["tail_bound_k"], math.inf)
    with pytest.raises(ValueError, match="integrity"):
        validate_temporal_study(altered, source)
