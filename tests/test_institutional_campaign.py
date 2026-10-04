"""Campaign failure handling only; no institutional dataset is executed here."""
from argparse import Namespace
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('institutional_campaign_review', ROOT / 'scripts/run_institutional_campaign.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def run_fake_lane(tmp_path, monkeypatch, child):
    script = tmp_path / 'harness.py'
    script.write_text('# execution is mocked; this file binds harness identity\n')
    destination = tmp_path / 'campaign'
    destination.mkdir()
    monkeypatch.setattr(driver.subprocess, 'run', child)
    result = driver.run_lane('academic', script, [], 'academic-checks.json', destination, {})
    saved = json.loads((destination / 'academic/outcome.json').read_text())
    assert saved == result
    return result, destination


def test_nonzero_child_exit_retains_failure_and_log(tmp_path, monkeypatch):
    def child(command, **kwargs):
        kwargs['stdout'].write(b'input hash mismatch\n')
        return SimpleNamespace(returncode=7)
    result, destination = run_fake_lane(tmp_path, monkeypatch, child)
    assert result['status'] == 'FAIL' and result['exit_code'] == 7
    assert result['log_sha256'] == driver.digest(destination / 'academic/process.log')
    assert 'summary' not in result


def test_timeout_is_distinct_and_retains_partial_log(tmp_path, monkeypatch):
    def child(command, **kwargs):
        kwargs['stdout'].write(b'partial execution\n')
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    result, destination = run_fake_lane(tmp_path, monkeypatch, child)
    assert result['status'] == 'TIMEOUT'
    assert result['error_type'] == 'TimeoutExpired'
    assert result['log_sha256'] == driver.digest(destination / 'academic/process.log')


def test_zero_exit_without_report_is_failure(tmp_path, monkeypatch):
    result, _ = run_fake_lane(tmp_path, monkeypatch, lambda command, **kwargs: SimpleNamespace(returncode=0))
    assert result['status'] == 'FAIL'
    assert result['exit_code'] == 0 and result['error_type'] == 'FileNotFoundError'


def test_zero_exit_with_invalid_json_is_failure(tmp_path, monkeypatch):
    def child(command, **kwargs):
        output = Path(command[command.index('--output-dir') + 1])
        output.mkdir()
        (output / 'academic-checks.json').write_text('{truncated')
        return SimpleNamespace(returncode=0)
    result, _ = run_fake_lane(tmp_path, monkeypatch, child)
    assert result['status'] == 'FAIL'
    assert result['error_type'] == 'JSONDecodeError'


def test_incomplete_academic_success_report_is_rejected(tmp_path):
    report = tmp_path / 'report.json'
    report.write_text(json.dumps({'schema': 'net.academic-real-data-check.v1', 'status': 'PASS'}))
    with pytest.raises(ValueError):
        driver.lane_summary('academic', report)


def test_epa_success_with_no_measurements_is_rejected(tmp_path):
    report = tmp_path / 'report.json'
    report.write_text(json.dumps({'schema': 'net.institutional-epa-campaign.v1',
        'software_test_status': 'PASS', 'counts': {'rows': 0, 'groups': 0, 'sites': 0,
        'complete_pairs': 0, 'missing_pairs': 0}, 'diagnostic_policy': {}, 'limits': []}))
    with pytest.raises(ValueError):
        driver.lane_summary('epa', report)


def campaign_args(tmp_path):
    return Namespace(output_dir=tmp_path / 'output', wheel=tmp_path / 'candidate.whl',
        work_dir=tmp_path / 'private-academic', corpus_root=tmp_path / 'corpus',
        thermal_dir=tmp_path / 'thermal', cwru_dir=tmp_path / 'cwru',
        rwth_response=None, stfe_source_root=None, stfe_revision=None)


def test_failed_lane_does_not_suppress_independent_lanes(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, 'sys', SimpleNamespace(flags=SimpleNamespace(isolated=1, utf8_mode=1),
        version='test runtime', executable='test-python'))
    monkeypatch.setattr(driver, 'installed_binding', lambda wheel: {'wheel_sha256': 'a' * 64})
    called = []
    def lane(name, *args):
        called.append(name)
        return {'name': name, 'status': 'FAIL' if name == 'numerics' else 'PASS'}
    monkeypatch.setattr(driver, 'run_lane', lane)
    report = driver.campaign(campaign_args(tmp_path))
    assert called == ['numerics', 'academic', 'epa']
    assert report['status'] == 'FAIL'
    assert report['counts'] == {'passed': 2, 'failed': 1, 'not_run': 0}


def test_existing_campaign_directory_is_never_overwritten(tmp_path):
    args = campaign_args(tmp_path)
    args.output_dir.mkdir()
    marker = args.output_dir / 'campaign.json'
    marker.write_text('previous immutable evidence')
    with pytest.raises(FileExistsError):
        driver.campaign(args)
    assert marker.read_text() == 'previous immutable evidence'


def test_installed_binding_failure_prevents_all_dataset_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, 'sys', SimpleNamespace(flags=SimpleNamespace(isolated=1, utf8_mode=1),
        version='test runtime', executable='test-python'))
    def refuse(wheel):
        raise ValueError('installed bytes differ from candidate wheel')
    monkeypatch.setattr(driver, 'installed_binding', refuse)
    monkeypatch.setattr(driver, 'run_lane', lambda *args: pytest.fail('Dataset lane must not execute after binding failure'))
    report = driver.campaign(campaign_args(tmp_path))
    assert report['status'] == 'FAIL'
    assert report['counts'] == {'passed': 0, 'failed': 0, 'not_run': 3}
    assert report['binding_after']['status'] == 'FAIL'


def test_changed_installed_binding_invalidates_passing_lanes(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, 'sys', SimpleNamespace(flags=SimpleNamespace(isolated=1, utf8_mode=1),
        version='test runtime', executable='test-python'))
    bindings = iter([{'wheel_sha256': 'a' * 64}, {'wheel_sha256': 'b' * 64}])
    monkeypatch.setattr(driver, 'installed_binding', lambda wheel: next(bindings))
    monkeypatch.setattr(driver, 'run_lane', lambda name, *args: {'name': name, 'status': 'PASS'})
    report = driver.campaign(campaign_args(tmp_path))
    assert report['counts'] == {'passed': 3, 'failed': 0, 'not_run': 0}
    assert report['status'] == 'FAIL' and report['binding_after']['status'] == 'FAIL'

@pytest.mark.parametrize('field,value', [('passed', True), ('passed', 1.0), ('failed', False), ('failed', 0.0)])
def test_numerical_evidence_counts_must_be_integers(tmp_path, field, value):
    data = {'schema': 'net.institutional-numerical-tests.v1',
            'status': 'PASS_WITH_UNTESTED_PHYSICAL_SCOPE',
            'counts': {'passed': 1, 'failed': 0}, 'checks': [{'status': 'PASS'}],
            'real_data_counts': {'observations': 1}, 'scope': 'software only', 'blocked': []}
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(data))
    assert driver.lane_summary('numerics', path)['counts']['passed'] == 1
    data['counts'][field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='consistent passing evidence'):
        driver.lane_summary('numerics', path)


@pytest.mark.parametrize('value', [True, 1.0])
def test_epa_complete_pair_row_count_must_be_integer(tmp_path, value):
    data = {'schema': 'net.institutional-epa-campaign.v1', 'software_test_status': 'PASS',
            'counts': {'rows': 1, 'groups': 1, 'sites': 1, 'complete_pairs': 1, 'missing_pairs': 0},
            'diagnostic_policy': {'atol': 0}, 'limits': ['software only'],
            'groups': [{'rows': 1, 'missing_pairs': 0, 'group': {'site': 'retained-site'},
                        'complete_pairs_only': {'rows': 1, 'independent_decimal_metrics':
                                                {'rmse': 0, 'max_abs_error': 0, 'max_tolerance_excess': 0}}}]}
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(data))
    assert driver.lane_summary('epa', path)['counts']['rows'] == 1
    data['groups'][0]['complete_pairs_only']['rows'] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='complete-pair counts'):
        driver.lane_summary('epa', path)


def test_progress_identifies_all_lanes_and_failure_without_stopping(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, 'sys', SimpleNamespace(flags=SimpleNamespace(isolated=1, utf8_mode=1),
        version='test runtime', executable='test-python'))
    monkeypatch.setattr(driver, 'installed_binding', lambda wheel: {'wheel_sha256': 'a' * 64})
    monkeypatch.setattr(driver, 'run_lane', lambda name, *args:
        {'name': name, 'status': 'FAIL' if name == 'academic' else 'PASS',
         **({'message': 'source hash mismatch'} if name == 'academic' else {})})
    progress = []
    report = driver.campaign(campaign_args(tmp_path), progress=progress.append)
    assert report['counts'] == {'passed': 2, 'failed': 1, 'not_run': 0}
    assert any('[2/3] academic: FAIL - source hash mismatch' in line for line in progress)
    assert any('[3/3] epa: PASS' in line for line in progress)
    assert all(any(f'Running {name}; log:' in line for line in progress)
               for name in ('numerics', 'academic', 'epa'))
    assert 'after the run' in progress[-1]


@pytest.mark.parametrize('quiet', [False, True])
def test_cli_keeps_json_stdout_and_points_to_failed_lane_log(tmp_path, monkeypatch, capsys, quiet):
    def failed_campaign(args, progress=None):
        if progress is not None:
            progress('Running academic lane')
        return {'status': 'FAIL', 'counts': {'passed': 2, 'failed': 1, 'not_run': 0},
                'binding_after': {'status': 'PASS'},
                'lanes': [{'name': 'academic', 'status': 'FAIL',
                           'message': 'source hash mismatch', 'log': 'academic/process.log'}]}
    monkeypatch.setattr(driver, 'campaign', failed_campaign)
    argv = []
    for name in ('corpus-root', 'thermal-dir', 'cwru-dir', 'output-dir', 'wheel'):
        argv.extend(['--' + name, str(tmp_path / name)])
    assert driver.main(argv + (['--quiet'] if quiet else [])) == 1
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary['failed_lanes'][0]['message'] == 'source hash mismatch'
    assert summary['failed_lanes'][0]['log'] == str((tmp_path / 'output-dir/academic/process.log').resolve())
    assert captured.err == ('' if quiet else 'Running academic lane\n')


def test_closed_progress_stream_does_not_interrupt_or_lose_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, 'sys', SimpleNamespace(flags=SimpleNamespace(isolated=1, utf8_mode=1),
        version='test runtime', executable='test-python'))
    monkeypatch.setattr(driver, 'installed_binding', lambda wheel: {'wheel_sha256': 'a' * 64})
    lanes = []
    def lane(name, *args):
        lanes.append(name)
        return {'name': name, 'status': 'PASS'}
    monkeypatch.setattr(driver, 'run_lane', lane)
    attempts = []
    def closed_stream(message):
        attempts.append(message)
        raise BrokenPipeError('progress consumer closed')
    args = campaign_args(tmp_path)
    report = driver.campaign(args, progress=closed_stream)
    assert report['status'] == 'PASS'
    assert lanes == ['numerics', 'academic', 'epa']
    assert len(attempts) == 1
    assert json.loads((args.output_dir / 'campaign.json').read_text()) == report
