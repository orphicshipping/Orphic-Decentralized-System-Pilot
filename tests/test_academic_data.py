"""Import/replay boundary checks; the real-data suite runs separately on pinned MAT files."""
import importlib.util
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("academic_check", ROOT / "scripts/check_academic_data.py")
academic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(academic)


def test_html_http_success_is_not_a_dataset(tmp_path):
    path = tmp_path / "download.zip"
    path.write_text("<html>Javascript challenge</html>")
    with pytest.raises(ValueError, match="not a valid ZIP"):
        academic.reject_nonarchive(path)


def test_valid_zip_can_advance_to_schema_review(tmp_path):
    path = tmp_path / "download.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metadata.txt", "schema review is still required")
    academic.reject_nonarchive(path)


def test_window_selection_preserves_complete_nonoverlapping_source_support():
    starts = academic.window_starts(121265, 128)
    assert len(starts) == 128 and starts[0] == 0
    assert starts[-1]+32 <= 121265
    assert all((right-left) >= 32 for left, right in zip(starts, starts[1:]))


def test_existing_evidence_is_never_overwritten(tmp_path):
    output, work = tmp_path / 'results', tmp_path / 'work'
    output.mkdir()
    marker = output / 'retained.json'
    marker.write_text('original retained evidence')
    with pytest.raises(FileExistsError, match='fresh'):
        academic.create_run_directories(output, work)
    assert marker.read_text() == 'original retained evidence'
    assert not work.exists()


def test_raw_sample_receipts_cannot_enter_output_directory(tmp_path):
    output = tmp_path / 'results'
    with pytest.raises(ValueError, match='outside'):
        academic.create_run_directories(output, output / 'raw-receipts')
    assert not output.exists()


def test_fresh_output_and_work_directories_are_created_once(tmp_path):
    output, work = tmp_path / 'results', tmp_path / 'private-work'
    academic.create_run_directories(output, work)
    assert output.is_dir() and work.is_dir()
    with pytest.raises(FileExistsError):
        academic.create_run_directories(output, work)
