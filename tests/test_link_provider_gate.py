"""A required profile gate must not report success after skipped/missing cases."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def gate():
    path = Path(__file__).resolve().parents[1] / 'scripts/check_link_provider_profiles.py'
    spec = importlib.util.spec_from_file_location('link_provider_gate_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report(tmp_path, cases, **attributes):
    attrs = ' '.join(f'{key}="{value}"' for key, value in attributes.items())
    path = tmp_path / 'results.xml'
    path.write_text(f'<testsuites><testsuite {attrs}>{cases}</testsuite></testsuites>')
    return path


def test_required_modules_report_actual_passing_cases(gate, tmp_path):
    xml = report(tmp_path, '<testcase classname="tests.test_owner" name="native"/>'
                          '<testcase classname="tests.test_link" name="integration"/>')
    result = gate.validate_report(xml, ('test_owner', 'test_link'))
    assert result['passed'] == 2 and result['skipped'] == 0
    assert result['by_module'] == {'test_owner': 1, 'test_link': 1}


@pytest.mark.parametrize('outcome', ['skipped', 'failure', 'error'])
def test_nonpassing_case_cannot_qualify_profile(gate, tmp_path, outcome):
    xml = report(tmp_path, f'<testcase classname="tests.test_owner" name="native"><{outcome}/></testcase>')
    with pytest.raises(ValueError, match='did not pass'):
        gate.validate_report(xml, ('test_owner',))


def test_absent_module_and_empty_report_are_refused(gate, tmp_path):
    with pytest.raises(ValueError, match='No test cases'):
        gate.validate_report(report(tmp_path, ''), ('test_owner',))
    xml = report(tmp_path, '<testcase classname="tests.test_other" name="irrelevant"/>')
    with pytest.raises(ValueError, match='Missing profile'):
        gate.validate_report(xml, ('test_owner',))


def test_aggregate_failure_cannot_be_hidden_by_passing_case(gate, tmp_path):
    xml = report(tmp_path, '<testcase classname="tests.test_owner" name="native"/>', errors='1')
    with pytest.raises(ValueError, match='suite reports'):
        gate.validate_report(xml, ('test_owner',))


def test_partial_native_measurement_case_set_cannot_qualify(gate, tmp_path):
    cases = ''.join(f'<testcase classname="tests.test_link_measurement_workbench" name="case{i}"/>'
                    for i in range(7))
    with pytest.raises(ValueError, match='Incomplete required profile'):
        gate.validate_report(report(tmp_path, cases), ('test_link_measurement_workbench',))
    cases += '<testcase classname="tests.test_link_measurement_workbench" name="case7"/>'
    assert gate.validate_report(report(tmp_path, cases), ('test_link_measurement_workbench',))['passed'] == 8
