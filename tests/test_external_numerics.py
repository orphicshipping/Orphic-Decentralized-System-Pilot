"""Third-party numerical comparisons; optional locally, required in its CI job."""
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path

import pytest

pytest.importorskip("scipy", reason="Install .[numerical-checks] to run the external numerical comparison suite")

from ciw.fluid_reservoir_solver import simulate
from ciw.polymer_models import engineering_estimate


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_external_numerics.py"
spec = importlib.util.spec_from_file_location("external_numerics", SCRIPT)
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


@pytest.fixture(scope="module")
def reservoir():
    request = comparison.reservoir_cases()["coupled_damped"]
    return request, simulate(request)


@pytest.fixture(scope="module")
def cooling():
    request = comparison.cooling_cases()["biot_0.08_elapsed_1_tau"]
    return request, engineering_estimate(request)


def test_full_comparison_passes_with_versions_and_explicit_limitations():
    report = comparison.run_comparisons()
    assert report["status"] == "PASS"
    assert len(report["reservoir"]) == 4
    assert len(report["polymer_cooling"]) == 9
    assert report["versions"]["scipy"]
    assert report["versions"]["numpy"]
    assert "not_physical_validation" in report["scope"]
    assert "no independent external publication" in report["limitations"][0]
    assert all(len(value) == 64 for value in report["source_sha256"].values())
    json.dumps(report, allow_nan=False)


def test_wrong_dynamics_fails_even_when_energy_balance_is_preserved(reservoir):
    request, _ = reservoir
    wrong_request = deepcopy(request)
    wrong_request["structure"]["stiffness_n_per_m"] *= 1.1
    report = comparison.compare_reservoir(request, simulate(wrong_request))
    assert report["status"] == "FAIL"
    assert report["checks"]["discrete_balances"]
    assert not report["checks"]["finer_state"]


@pytest.mark.parametrize("field", ["fluid_energy_j", "fluid_dissipation_j", "structure_interface_work_j"])
def test_altered_energy_and_work_numbers_fail(reservoir, field):
    request, result = reservoir
    corrupt = deepcopy(result)
    corrupt["resolutions"]["finer"]["trace"][field][-1] += 0.001
    report = comparison.compare_reservoir(request, corrupt)
    assert report["status"] == "FAIL"
    assert not report["checks"]["finer_energy_and_work"]


@pytest.mark.parametrize("field", ["temperature_k", "remaining_time_s"])
def test_altered_cooling_numbers_fail(cooling, field):
    request, result = cooling
    corrupt = deepcopy(result)
    destination = corrupt["cooling"] if field == "temperature_k" else corrupt["cooling"]["target"]
    destination[field] += 1.0
    report = comparison.compare_cooling(request, corrupt)
    assert report["status"] == "FAIL"
    assert not report["checks"]["ode_temperature_k" if field == "temperature_k" else "ode_target_time_s"]


def test_slab_discrepancy_is_distinct_from_arithmetic_error(cooling):
    request, result = cooling
    report = comparison.compare_cooling(request, result)
    assert report["errors"]["ode_temperature_k"] < 1e-8
    assert 0.005 < report["errors"]["slab_temperature_initial_gap_fraction"] < 0.01
    assert 0.02 < report["errors"]["slab_target_time_relative"] < 0.03
    assert report["temperature_k"]["convective_slab_volume_average"] > result["cooling"]["temperature_k"]


def test_slab_solution_approaches_lumped_limit_as_biot_decreases():
    differences = [abs(comparison.slab_average_ratio(biot, 1.0 / biot) - math.exp(-1.0))
                   for biot in (0.08, 0.02, 0.001)]
    assert differences[0] > differences[1] > differences[2] > 0
    assert differences[-1] < 1.3e-4


def test_cli_writes_failure_report_and_returns_nonzero(tmp_path, monkeypatch):
    def failed_comparison():
        raise RuntimeError("numerical comparator failed")

    monkeypatch.setattr(comparison, "run_comparisons", failed_comparison)
    destination = tmp_path / "comparison.json"
    assert comparison.main(["--output", str(destination)]) == 1
    report = json.loads(destination.read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert "numerical comparator failed" in report["error"]


def test_nonfinite_cooling_number_cannot_pass(cooling):
    request, result = cooling
    corrupt = deepcopy(result)
    corrupt["cooling"]["temperature_k"] = float("nan")
    assert comparison.compare_cooling(request, corrupt)["status"] == "FAIL"


@pytest.mark.parametrize("failure_kind", ["exception", "nonfinite"])
def test_one_failed_case_preserves_other_case_evidence(monkeypatch, failure_kind):
    original = comparison.compare_reservoir
    calls = 0

    def damaged_case(request, result):
        nonlocal calls
        calls += 1
        if calls == 2:
            if failure_kind == "exception":
                raise RuntimeError("reference integration failed")
            return {"status": "FAIL", "error_value": float("nan")}
        return original(request, result)

    monkeypatch.setattr(comparison, "compare_reservoir", damaged_case)
    report = comparison.run_comparisons()
    assert calls == 4
    assert report["status"] == "FAIL"
    assert [row["status"] for row in report["reservoir"].values()] == ["PASS", "FAIL", "PASS", "PASS"]
    failed = list(report["reservoir"].values())[1]
    assert "RuntimeError" in failed["error"] if failure_kind == "exception" else "ValueError" in failed["error"]
    assert len(report["polymer_cooling"]) == 9
    assert all(row["status"] == "PASS" for row in report["polymer_cooling"].values())
    assert report["versions"]["scipy"] and report["tolerances"] and report["source_sha256"]
    json.dumps(report, allow_nan=False)


def test_existing_report_is_refused_before_computation(tmp_path, monkeypatch, capsys):
    destination = tmp_path / "comparison.json"
    original = b'{"previous": "retained evidence"}\n'
    destination.write_bytes(original)

    def should_not_run():
        pytest.fail("Existing report must be refused before comparison")

    monkeypatch.setattr(comparison, "run_comparisons", should_not_run)
    assert comparison.main(["--output", str(destination)]) == 1
    assert destination.read_bytes() == original
    assert "Choose a new report path" in capsys.readouterr().err


def test_report_created_during_comparison_is_not_overwritten(tmp_path, monkeypatch, capsys):
    destination = tmp_path / "comparison.json"

    def racing_report():
        destination.write_text("other retained evidence", encoding="utf-8")
        return {"status": "PASS"}

    monkeypatch.setattr(comparison, "run_comparisons", racing_report)
    assert comparison.main(["--output", str(destination)]) == 1
    assert destination.read_text(encoding="utf-8") == "other retained evidence"
    assert "could not save report" in capsys.readouterr().err


def test_invalid_output_parent_has_clear_nonzero_diagnostic(tmp_path, monkeypatch, capsys):
    parent = tmp_path / "occupied"
    parent.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(comparison, "run_comparisons", lambda: {"status": "PASS"})
    assert comparison.main(["--output", str(parent / "report.json")]) == 1
    assert parent.read_text(encoding="utf-8") == "keep"
    assert "could not save report" in capsys.readouterr().err



def test_inaccessible_output_is_reported_before_computation(tmp_path, monkeypatch, capsys):
    def inaccessible(_path):
        raise PermissionError("output parent is inaccessible")

    def should_not_run():
        pytest.fail("Inaccessible output must be refused before comparison")

    monkeypatch.setattr(comparison.Path, "exists", inaccessible)
    monkeypatch.setattr(comparison, "run_comparisons", should_not_run)
    assert comparison.main(["--output", str(tmp_path / "report.json")]) == 1
    assert "could not inspect output" in capsys.readouterr().err


def test_report_publication_writes_complete_json_and_removes_staging(tmp_path, monkeypatch, capsys):
    report = {"status": "PASS", "evidence": {"value": 1.25, "units": "kelvin"}}
    monkeypatch.setattr(comparison, "run_comparisons", lambda: report)
    destination = tmp_path / "new-directory" / "comparison.json"

    assert comparison.main(["--output", str(destination)]) == 0
    assert destination.read_bytes() == (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    assert list(destination.parent.iterdir()) == [destination]
    captured = capsys.readouterr()
    assert "PASS; report:" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize("phase", ["write", "flush", "fsync", "close"])
def test_failed_staging_never_creates_final_report(tmp_path, monkeypatch, capsys, phase):
    from contextlib import contextmanager

    original_temporary_file = comparison.tempfile.NamedTemporaryFile

    @contextmanager
    def failing_temporary_file(**kwargs):
        with original_temporary_file(**kwargs) as stream:
            original_write, original_flush = stream.write, stream.flush

            def write_part_then_fail(value):
                original_write(value[:10])
                original_flush()
                raise OSError("injected partial write failure")

            def flush_then_fail():
                original_flush()
                raise OSError("injected flush failure")

            if phase == "write":
                stream.write = write_part_then_fail
            if phase == "flush":
                stream.flush = flush_then_fail
            yield stream
        if phase == "close":
            raise OSError("injected close failure")

    def failed_fsync(_descriptor):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(comparison.tempfile, "NamedTemporaryFile", failing_temporary_file)
    if phase == "fsync":
        monkeypatch.setattr(comparison.os, "fsync", failed_fsync)
    monkeypatch.setattr(comparison, "run_comparisons", lambda: {"status": "PASS", "evidence": "complete"})
    destination = tmp_path / "comparison.json"

    assert comparison.main(["--output", str(destination)]) == 1
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []
    captured = capsys.readouterr()
    assert "could not save report" in captured.err
    assert f"{phase} failure" in captured.err
    assert captured.out == ""


def test_unsupported_hard_links_fail_without_publication_or_staging(tmp_path, monkeypatch, capsys):
    def unsupported_link(_source, _destination):
        raise OSError("hard links unavailable on output filesystem")

    monkeypatch.setattr(comparison.os, "link", unsupported_link)
    monkeypatch.setattr(comparison, "run_comparisons", lambda: {"status": "PASS"})
    assert comparison.main(["--output", str(tmp_path / "comparison.json")]) == 1
    assert list(tmp_path.iterdir()) == []
    assert "hard links unavailable" in capsys.readouterr().err


def test_destination_created_at_publication_is_preserved(tmp_path, monkeypatch, capsys):
    original_link = comparison.os.link
    original = b"other complete retained evidence\n"
    destination = tmp_path / "comparison.json"

    def publish_competing_report(source, target):
        # The stage is complete, but the destination appears immediately before
        # the atomic publication operation. Its original bytes must survive.
        assert json.loads(Path(source).read_text(encoding="utf-8")) == {"status": "PASS"}
        Path(target).write_bytes(original)
        original_link(source, target)

    monkeypatch.setattr(comparison.os, "link", publish_competing_report)
    monkeypatch.setattr(comparison, "run_comparisons", lambda: {"status": "PASS"})

    assert comparison.main(["--output", str(destination)]) == 1
    assert destination.read_bytes() == original
    assert list(tmp_path.iterdir()) == [destination]
    assert "could not save report" in capsys.readouterr().err


def test_interruption_during_staging_cleans_up_without_publication(tmp_path, monkeypatch):
    def interrupted_fsync(_descriptor):
        raise KeyboardInterrupt("comparison publication interrupted")

    monkeypatch.setattr(comparison.os, "fsync", interrupted_fsync)
    monkeypatch.setattr(comparison, "run_comparisons", lambda: {"status": "PASS"})
    with pytest.raises(KeyboardInterrupt, match="publication interrupted"):
        comparison.main(["--output", str(tmp_path / "comparison.json")])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("published", [False, True])
def test_cleanup_failure_retains_primary_outcome_and_names_staged_file(tmp_path, monkeypatch, capsys, published):
    original_unlink = comparison.Path.unlink

    def fail_staging_cleanup(path, *args, **kwargs):
        if path.name.startswith(".external-numerics-"):
            raise PermissionError("injected staging cleanup refusal")
        return original_unlink(path, *args, **kwargs)

    def refuse_publication(_source, _destination):
        raise OSError("original publication failure")

    monkeypatch.setattr(comparison.Path, "unlink", fail_staging_cleanup)
    if not published:
        monkeypatch.setattr(comparison.os, "link", refuse_publication)
    monkeypatch.setattr(comparison, "run_comparisons", lambda: {"status": "PASS"})
    destination = tmp_path / "comparison.json"

    assert comparison.main(["--output", str(destination)]) == (0 if published else 1)
    staged = list(tmp_path.glob(".external-numerics-*.tmp"))
    assert len(staged) == 1
    captured = capsys.readouterr()
    assert str(staged[0]) in captured.err
    assert "injected staging cleanup refusal" in captured.err
    if published:
        assert json.loads(destination.read_text(encoding="utf-8")) == {"status": "PASS"}
        assert destination.read_bytes() == staged[0].read_bytes()
        assert "Published report is complete" in captured.err
    else:
        assert not destination.exists()
        assert "original publication failure" in captured.err
        assert "No new report was published" in captured.err
        assert captured.out == ""
    original_unlink(staged[0])


def test_staging_creation_failure_has_clear_nonzero_diagnostic(tmp_path, monkeypatch, capsys):
    def refuse_staging(**_kwargs):
        raise PermissionError("cannot create staging file")

    monkeypatch.setattr(comparison.tempfile, "NamedTemporaryFile", refuse_staging)
    monkeypatch.setattr(comparison, "run_comparisons", lambda: {"status": "PASS"})
    assert comparison.main(["--output", str(tmp_path / "comparison.json")]) == 1
    assert list(tmp_path.iterdir()) == []
    assert "could not save report: cannot create staging file" in capsys.readouterr().err
