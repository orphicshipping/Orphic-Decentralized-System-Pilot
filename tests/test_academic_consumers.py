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


@pytest.mark.integration
def test_tclab_uses_native_fir_with_real_predecessors_and_independent_oracle(tmp_path):
    value, library = os.environ.get("NET_TCLAB_CSV"), os.environ.get("SCR_DSP_LIBRARY")
    if not value or not library:
        pytest.skip("Original TCLab bytes and native SCR DSP library are both required")
    library = Path(library)
    digest = "sha256:" + sha256(library.read_bytes()).hexdigest()
    result = academic.run_tclab_fir(Path(value), library, digest, tmp_path / "fir", STAMP)
    assert result["native_executions"] == 8 and result["native_filter_executions"] == 4
    assert result["filtered_scalar_values"] == 796
    assert result["padding"] == "none" and result["source_predecessors_s"] == [0, 1]
    assert all(row["sample_count"] == 199 and row["status"] == "PASS" for row in result["comparisons"])
    assert max(row["metrics"]["max_abs_error"] for row in result["comparisons"]) < 2e-14
    workspace = json.loads(Path(result["workspace"]).read_text())
    assert workspace["run"]["time_s"] == list(range(2, 201))
    assert workspace["run"]["metadata"]["provenance"]["uncertainty"] == "absent"
    assert result["runtime"]["library_sha256"] == digest


@pytest.mark.integration
def test_native_fir_never_substitutes_an_unmatched_library(tmp_path):
    value, library = os.environ.get("NET_TCLAB_CSV"), os.environ.get("SCR_DSP_LIBRARY")
    if not value or not library:
        pytest.skip("Original TCLab bytes and native library are required for the binding test")
    with pytest.raises(ValueError, match="digest|hash|SHA256|sha256|identity"):
        academic.run_tclab_fir(Path(value), Path(library), "sha256:" + "0" * 64,
                               tmp_path / "not-created", STAMP)
    assert not (tmp_path / "not-created").exists()


@pytest.mark.integration
def test_fao56_worked_example_uses_existing_endpoint_without_farm_invention(tmp_path):
    value = os.environ.get("NET_FAO56_REFERENCE")
    if not value:
        pytest.skip("Original pinned FAO56 worked reference is required")
    result = academic.run_fao56(Path(value), tmp_path / "fao56", STAMP)
    assert result["status"] == "BOUNDED_EXISTING_ENDPOINT_PASS"
    assert result["value"] == pytest.approx(3.88, abs=0.02)
    assert result["matched_input_discrepancy"] <= 1e-12
    assert result["reported_deficit_kpa"] == 0.589
    assert result["actual_reported_es_minus_ea_kpa"] == pytest.approx(0.588)
    assert result["unit"] == "mm/day" and result["uncertainty"] == "absent"
    assert result["session_executions"] == 0 and result["native_endpoint_evaluations"] == 3
    assert "zones" not in result["weather"] and "date" not in result["weather"]


def test_changed_fao56_reference_is_refused(tmp_path):
    path = tmp_path / "fao.json"
    path.write_text('{}')
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        academic.run_fao56(path, tmp_path / "not-created", STAMP)
    assert not (tmp_path / "not-created").exists()
