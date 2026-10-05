"""Real opt-in academic sources; unavailable bytes never become fake fixtures."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import shutil

import pytest

SPEC = importlib.util.spec_from_file_location("academic_qualification",
    Path(__file__).resolve().parents[1] / "scripts/qualify_academic_consumers.py")
academic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(academic)
STAMP = "2026-10-05T21:00:00+00:00"


@pytest.fixture(scope="module")
def acquired():
    value = os.environ.get("NET_ACADEMIC_ACQUISITION_ROOT")
    if not value:
        pytest.skip("Pinned academic acquisition absent; no synthetic substitution")
    root = Path(value)
    return root, academic.check_acquisition(root)


@pytest.mark.integration
@pytest.mark.parametrize("case", sorted(academic.CASES))
def test_published_sbml_table_runs_exact_native_consumers(acquired, case, tmp_path):
    root, receipt = acquired
    result = academic.run_sbml(root, receipt, case, tmp_path / "run", STAMP)
    assert result["status"] == "PASS"
    assert result["native_executions"] == 4
    assert result["reference_samples"] == 51 and result["reference_scalar_values"] == 102
    assert all(row["status"] == "PASS" and row["unit"] == "mol" for row in result["comparisons"])
    assert max(row["metrics"]["max_abs_error"] for row in result["comparisons"]) < 1e-16
    workspace = json.loads(Path(result["workspace"]).read_text())
    assert workspace["run"]["metadata"]["sequence_request"]["source_kind"] == "declared_distribution"
    assert result["authority"]["physical_validation"] == "not_established"
    assert result["authority"]["upstream_native_execution"] == "not_performed"
    projection = json.loads((tmp_path / "run/projection.json").read_text())
    assert projection["stoichiometric_factor"] == (2 if case == 3 else 1)
    for row in projection["source_files"]:
        assert sha256((tmp_path / "run" / Path(row["path"]).name).read_bytes()).hexdigest() == row["sha256"]


@pytest.mark.parametrize("case", sorted(academic.UNSUPPORTED_SBML))
def test_incompatible_kinetics_are_not_substituted(case, tmp_path):
    with pytest.raises(ValueError, match=academic.UNSUPPORTED_SBML[case].replace(".", r"\.")):
        academic.run_sbml(tmp_path, {}, case, tmp_path / "not-created", STAMP)
    assert not (tmp_path / "not-created").exists()


@pytest.mark.integration
@pytest.mark.parametrize("suffix", ["results.csv", "model.m", "settings.txt", "sbml-l3v2.xml"])
def test_mutation_after_procurement_refuses_before_execution(acquired, suffix, tmp_path):
    root, receipt = acquired
    relative = Path("sources/sbml/cases/semantic/00001")
    shutil.copytree(root / relative, tmp_path / relative)
    path = tmp_path / relative / ("00001-" + suffix)
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Source SHA256 mismatch"):
        academic.run_sbml(tmp_path, receipt, 1, tmp_path / "not-created", STAMP)
    assert not (tmp_path / "not-created").exists()


@pytest.mark.integration
def test_real_tclab_prefix_preserves_all_columns_and_absent_uncertainty(tmp_path):
    value = os.environ.get("NET_TCLAB_CSV")
    if not value:
        pytest.skip("Original Tclab prefix absent; no synthetic substitution")
    result = academic.retain_tclab(Path(value), tmp_path / "tclab", STAMP)
    assert result["rows"] == 201 and result["observations"] == 804
    assert result["status"] == "TYPED_RETENTION_ONLY" and result["native_executions"] == 0
    assert result["synthetic_values_used"] is False


def test_no_byte_identity_or_clock_semantics_are_guessed(tmp_path):
    source = tmp_path / "changed.csv"
    source.write_text("time,x\n0,1\n")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        academic.retain_tclab(source, tmp_path / "not-created", STAMP)
    assert not (tmp_path / "not-created").exists()
    assert academic.mapping(1, "reference")["clock"]["unit"] == "s"
    assert all(row["unit"] == "mol" for row in academic.mapping(1, "reference")["quantities"])
