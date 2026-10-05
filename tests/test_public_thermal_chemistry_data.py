"""Opt-in original academic data consumption, without inventing calibration.

Set NET_PUBLIC_THERMAL_DATA to original files fetched and hash-checked by
scripts/inspect_public_thermal_data.py. Nothing downloads or executes author
code here. This qualifies byte identity and the chemistry quantity consumer;
it does not run a physically qualified thermal model or promote missing
uncertainty to zero. Source and licensing limits: docs/PUBLIC_THERMAL_DATA.md.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def original_data():
    selected = os.environ.get("NET_PUBLIC_THERMAL_DATA")
    if not selected:
        pytest.skip("Original pinned academic files not supplied; no synthetic substitution")
    path = Path(selected)
    expected = runpy.run_path(str(ROOT / "scripts/inspect_public_thermal_data.py"))["EXPECTED"]
    for name, digest in expected.items():
        assert sha256((path / name).read_bytes()).hexdigest() == digest, name
    return path


@pytest.mark.integration
def test_original_academic_bytes_and_declared_channels(original_data):
    scipy_io = pytest.importorskip("scipy.io")
    arrays = scipy_io.loadmat(original_data / "Temperature_data.mat")
    assert arrays["T_data_1"].shape == (5439, 4)
    assert arrays["T_data_2"].shape == (3222, 5)
    # The pinned author program identifies columns 2/3/4; column 5 is unused.
    code = (original_data / "MainScript.m").read_text()
    assert "RAW.TEMP.T_surf = T_data(:,2)" in code
    assert "RAW.TEMP.T_core = T_data(:,3)" in code
    assert "RAW.TEMP.T_inf  = T_data(:,4)" in code
    # This dependent heat estimate is not an independent driving measurement.
    assert "Q_rev           = INTERP.I.*INTERP.T_core*P.dUdT" in code


@pytest.mark.integration
def test_real_temperature_records_preserve_absent_uncertainty_in_chemistry_consumer(original_data):
    pytest.importorskip("scipy.io")
    # Isolate the imported runtime's top-level package names from Terminal tests.
    program = r'''
import json
import sys
from scipy.io import loadmat
from structures.quantity import assert_property_context

arrays = loadmat(sys.argv[1])
counts = {}
for cycle in (1, 2):
    count = 0
    for sample in arrays["T_data_" + str(cycle)]:
        for column, quantity in enumerate(("surface_temperature", "core_temperature", "coolant_temperature"), 1):
            result = assert_property_context({
                "property": quantity, "method": "author-declared thermocouple recording",
                "conditions": {"cycle": cycle, "time_s": float(sample[0]), "column": column + 1},
                "value": float(sample[column]), "unit": "degC", "uncertainty_kind": "absent",
            })
            assert result.value == sample[column]
            assert result.unit == "degC"
            assert result.uncertainty_kind == "absent" and result.uncertainty is None
            count += 1
    counts[str(cycle)] = count
print(json.dumps({"counts": counts, "scope": "quantity_contract_only_uncertainty_absent"}))
'''
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "instruments/execution/compute-runtime")}
    completed = subprocess.run([sys.executable, "-c", program, str(original_data / "Temperature_data.mat")],
        check=True, capture_output=True, text=True, env=environment, timeout=60)
    assert json.loads(completed.stdout) == {
        "counts": {"1": 16317, "2": 9666}, "scope": "quantity_contract_only_uncertainty_absent"}
