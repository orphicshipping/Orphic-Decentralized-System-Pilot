"""Source-independent unit tests; the acquired NIST table remains opt-in."""
from copy import deepcopy
from decimal import Decimal
import importlib.util
import os
from pathlib import Path
import py_compile
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("norris_qualification", ROOT / "scripts/qualify_norris_affine_consumer.py")
norris = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(norris)
NATIVE = ROOT / "instruments/mathematics/sensitivity/src"


def test_native_affine_evaluates_declared_numbers_without_units_or_clock():
    runtime = norris.AffineRuntime(NATIVE)
    source = {"b0": "-0.25", "b1": "1.5", "rows": [{"x": "2.0", "y": "3"}, {"x": "0", "y": "-0.2"}]}
    native = runtime.execute(source)
    assert native["rows"] == [{"prediction": 2.75, "derivative": 1.5}, {"prediction": -0.25, "derivative": 1.5}]
    assert norris.POLICY["coefficient_fitting"] == "not_performed"
    assert norris.POLICY["time_or_clock"] == "not_required_not_invented"


@pytest.mark.parametrize("raw", [b"", b"not Norris", b"\xff", b"a" * 16385])
def test_invalid_source_refuses(raw):
    with pytest.raises(ValueError):
        norris.parse_norris(raw)


def test_source_hash_refuses_before_native_execution(tmp_path, monkeypatch):
    path = tmp_path / "Norris.dat"
    path.write_text("changed")
    monkeypatch.setattr(norris.AffineRuntime, "execute", lambda *a: pytest.fail("Unexpected execution"))
    with pytest.raises(ValueError, match="SHA256"):
        norris.qualify(path, NATIVE, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_explicit_binding_refuses_missing_source(tmp_path):
    with pytest.raises(ValueError, match="Missing"):
        norris.AffineRuntime(tmp_path)


@pytest.mark.parametrize("attack", ["forged_unchecked_bytecode", "numpy_shadow"])
def test_only_hashed_source_bytes_can_execute(tmp_path, attack):
    root = tmp_path / "source"
    shutil.copytree(NATIVE / "sensitivity", root / "sensitivity", ignore=shutil.ignore_patterns("__pycache__"))
    if attack == "forged_unchecked_bytecode":
        target = root / "sensitivity/reference_models.py"
        cache = Path(importlib.util.cache_from_source(str(target)))
        cache.parent.mkdir(parents=True, exist_ok=True)
        forged = tmp_path / "forged.py"
        forged.write_text("raise RuntimeError('UNHASHED BYTECODE EXECUTED')\n")
        py_compile.compile(str(forged), cfile=str(cache), dfile=str(target), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    else:
        (root / "numpy.py").write_text("raise RuntimeError('SOURCE ROOT SHADOW EXECUTED')\n")
    runtime = norris.AffineRuntime(root)
    source = {"b0": "-0.25", "b1": "1.5", "rows": [{"x": "2.0", "y": "3"}]}
    assert runtime.execute(source)["rows"] == [{"prediction": 2.75, "derivative": 1.5}]
    assert runtime.identity()["binding"] == "explicit_local_source_bytes_copied_without_bytecode_or_siblings"


def test_changed_source_after_binding_refuses(tmp_path):
    root = tmp_path / "source"
    shutil.copytree(NATIVE / "sensitivity", root / "sensitivity", ignore=shutil.ignore_patterns("__pycache__"))
    runtime = norris.AffineRuntime(root)
    with (root / "sensitivity/reference_models.py").open("a") as stream:
        stream.write("\n# Changed after binding\n")
    with pytest.raises(ValueError, match="changed after binding"):
        runtime.execute({})


def test_decimal_oracle_detects_wrong_prediction_and_derivative():
    source = {"b0": "0", "b1": "1", "rows": [{"x": "1", "y": "2"}],
        "degrees_of_freedom": 1, "certified_rss": "1", "certified_residual_sd": "1"}
    native = {"rows": [{"prediction": 1.0, "derivative": 1.0}]}
    assert norris.check_reference(source, native)["status"] == "PASS"
    for key in ("prediction", "derivative"):
        changed = deepcopy(native)
        changed["rows"][0][key] += 0.01
        with pytest.raises(ValueError, match="decimal reference"):
            norris.check_reference(source, changed)


@pytest.mark.integration
def test_acquired_norris_native_reference(tmp_path):
    path = os.environ.get("NET_NORRIS_DAT")
    if not path:
        pytest.skip("Original acquired Norris source absent; no data substitution")
    report = norris.qualify(Path(path), NATIVE, tmp_path / "norris")
    assert report["status"] == "PASS" and len(report["source"]["rows"]) == 36
    assert report["source"]["units"] == {"x": "not_declared_in_retained_source", "y": "not_declared_in_retained_source"}
    assert report["native_evaluations"] == report["native_analytical_derivatives"] == 72
    assert report["comparison"]["max_prediction_absolute_error"] < 1e-12
    assert abs(Decimal(report["comparison"]["decimal_evaluated_rss"]) - Decimal(report["source"]["certified_rss"])) < Decimal("1e-10")
