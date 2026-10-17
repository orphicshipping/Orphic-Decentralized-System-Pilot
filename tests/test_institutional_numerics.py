"""Bounded regression checks for source-neutral numerical kernels.

These small edge cases are software fixtures; institutional measurements are
exercised by scripts/check_institutional_numerics.py with pinned original files.
"""
from copy import deepcopy

import numpy as np
import pytest

from ciw.adapters.oscillator import (
    compute_spectrum, compute_statistics, make_demo_run,
    periodogram_values, summarize_values,
)


def test_summary_keeps_existing_oscillator_selection_and_numeric_results():
    run = make_demo_run()
    expected = compute_statistics(run, "q", [0, 12])
    assert summarize_values(run["channels"]["q"]["values"], "m") == expected
    damaged = deepcopy(run)
    damaged["channels"]["energy"]["values"][0] = None
    with pytest.raises(ValueError):
        compute_statistics(damaged, "q", [0, 12])


def test_periodogram_keeps_existing_oscillator_selection_and_numeric_results():
    run = make_demo_run()
    assert periodogram_values(run["channels"]["q"]["values"], "m", 64) == compute_spectrum(run, "q", [0, 12])


@pytest.mark.parametrize("values", [[], [True, 1], [np.nan], [np.inf], [[1, 2]], ["1"], [1 + 2j]])
def test_summary_refuses_empty_malformed_or_nonfinite_samples(values):
    with pytest.raises(ValueError):
        summarize_values(values, "source-unit")


@pytest.mark.parametrize("unit", ["", " ", None, 1])
def test_summary_requires_explicit_nonempty_unit(unit):
    with pytest.raises(ValueError):
        summarize_values([1.0], unit)


@pytest.mark.parametrize("rate", [0, -1, np.nan, np.inf, True, "12"])
def test_periodogram_requires_positive_finite_numeric_sample_rate(rate):
    with pytest.raises(ValueError):
        periodogram_values([1., 2., 3., 4.], "source-unit", rate)


def test_scaled_summary_preserves_large_values_and_zero():
    assert summarize_values([1e308, -1e308], "source-unit")["rms"] == 1e308
    assert summarize_values([0, 0], "source-unit")["mean"] == 0
    with pytest.raises(ValueError, match="at least four"):
        periodogram_values([1, 2, 3], "source-unit", 1)


@pytest.mark.parametrize("rate,message", [(1e-320, "finite positive spacing"), (1e-308, "finite sample support")])
def test_periodogram_refuses_unrepresentable_timing_instead_of_zero_frequency_grid(rate, message):
    # A zero-amplitude signal must not hide reciprocal/support overflow in the
    # frequency grid. Nonzero bins are mathematically representable at both rates.
    with pytest.raises(ValueError, match=message):
        periodogram_values([0., 0., 0., 0.], "source-unit", rate)


def test_numerics_runner_refuses_existing_results_before_input_or_dependency_loading(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    import sys
    script = Path(__file__).resolve().parents[1] / "scripts/check_institutional_numerics.py"
    spec = importlib.util.spec_from_file_location("numerics_immutable_review", script)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    output = tmp_path / "retained-results"
    output.mkdir()
    previous = output / "institutional-results.json"
    retained = '{"status":"PASS_WITH_UNTESTED_PHYSICAL_SCOPE","previous_run":true}\n'
    previous.write_text(retained)
    monkeypatch.setattr(sys, "argv", [str(script), "--nist-dir", str(tmp_path / "absent-nist"),
        "--thermal-dir", str(tmp_path / "absent-thermal"), "--output-dir", str(output)])
    with pytest.raises(FileExistsError):
        runner.main()
    assert previous.read_text() == retained
    assert list(output.iterdir()) == [previous]
