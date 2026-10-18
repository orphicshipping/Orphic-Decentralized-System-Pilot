"""Counterexamples to treating lexical test names as interchangeable CI runs."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('workflow_inventory', Path(__file__).resolve().parents[1] / 'audit_workflow_inventory.py')
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


class Declarations(unittest.TestCase):
    def test_copy_and_dependency_mentions_are_not_executions(self):
        command = 'python -m pip install pytest\ncp tests/test_fixture.py .\npython -m pytest -q tests/test_actual.py'
        calls, opaque = inventory.pytest_declarations(command)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['pytest_arguments'], ['-q', 'tests/test_actual.py'])
        self.assertEqual(opaque, [])

    def test_shell_continuations_and_isolated_installed_invocation(self):
        command = 'env -u PYTHONPATH python -I -m pytest -q \\\n test_installed.py --junitxml=installed.xml'
        calls, _ = inventory.pytest_declarations(command)
        self.assertIn('-I', calls[0]['argv'])
        self.assertIn('test_installed.py', calls[0]['pytest_arguments'])

    def test_inline_environment_assignment_is_not_a_different_executable(self):
        calls, opaque = inventory.pytest_declarations('PROVIDER_ROOT="$RUNNER_TEMP/provider" python -m pytest tests/test_shared.py')
        self.assertEqual(len(calls), 1)
        self.assertEqual(opaque, [])

    def test_computed_python_path_preserves_its_mapping_limit(self):
        calls, _ = inventory.pytest_declarations("import subprocess\nsubprocess.run(['python','-m','pytest',str(temp/'test_copy.py')])", 'python')
        self.assertEqual(calls[0]['pytest_arguments'], ['{path}/test_copy.py'])
        self.assertFalse(calls[0]['selection_complete'])

    def test_python_here_document_does_not_count_copied_fixtures(self):
        command = '''python - <<'PY'
import shutil, subprocess, sys
shutil.copy2('tests/test_fixture.py', 'test_fixture.py')
subprocess.run([sys.executable, '-I', '-m', 'pytest', 'test_actual.py', '--junitxml='+str(report)])
PY'''
        calls, opaque = inventory.pytest_declarations(command)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['pytest_arguments'], ['test_actual.py', '--junitxml={dynamic}'])
        self.assertTrue(calls[0]['selection_complete'])
        self.assertEqual(opaque, [])

    def test_computed_selectors_remain_explicitly_incomplete(self):
        command = '''python - <<'PY'
import subprocess, sys
subprocess.run([sys.executable, '-m', 'pytest', *computed_names])
PY'''
        calls, _ = inventory.pytest_declarations(command)
        self.assertEqual(calls[0]['pytest_arguments'], [None])
        self.assertFalse(calls[0]['selection_complete'])

    def test_a_literal_call_does_not_hide_a_second_computed_command(self):
        command = "import subprocess\nsubprocess.run(['python','-m','pytest','test_a.py'])\nsubprocess.run(other_command)"
        calls, opaque = inventory.pytest_declarations(command, 'python')
        self.assertEqual(len(calls), 1)
        self.assertEqual(opaque[0]['reason'], 'computed_subprocess_command_in_pytest_step')

    def test_python_arrays_are_never_executed(self):
        command = '''python - <<'PY'
raise RuntimeError('audit must not execute this')
import subprocess
subprocess.run(['python', '-m', 'pytest', 'test_safe.py'])
PY'''
        calls, _ = inventory.pytest_declarations(command)
        self.assertEqual(calls[0]['pytest_arguments'], ['test_safe.py'])

    def test_permission_provider_and_platform_differences_preserve_lanes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'tests').mkdir()
            (root / 'tests/test_shared.py').write_text('')
            common = {'permissions': {'contents': 'read'}, 'on': {'pull_request': None},
                      'jobs': {'check': {'runs-on': 'ubuntu-latest', 'steps': [{'run': 'python -m pytest tests/test_shared.py'}]}}}
            other = json.loads(json.dumps(common))
            other['permissions']['contents'] = 'write'
            other['jobs']['check'].update({'runs-on': 'windows-latest', 'env': {'PROVIDER_PIN': 'a' * 40}})
            review = inventory.test_lane_review(root, [('one.yml', common), ('two.yml', other)])
            self.assertEqual(review['overlapping_module_count'], 1)
            row = review['overlapping_modules'][0]
            self.assertTrue({'permissions', 'environment', 'platform_matrix'} <= set(row['differing_dimensions']))
            self.assertEqual(review['automatic_removals'], [])

    def test_basenames_and_globs_keep_source_mapping_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'tests').mkdir()
            for name in ['test_a.py', 'test_b.py']: (root / 'tests' / name).write_text('')
            selected, other = inventory._selectors(root, ['test_*.py'])
            self.assertEqual(len(selected), 2)
            self.assertTrue(all(x['mapping'] == 'basename_reference_not_content_attestation' for x in selected.values()))
            self.assertEqual(other, [])
            (root / 'outside.py').write_text('')
            selected, other = inventory._selectors(root, ['tests/../outside.py'])
            self.assertEqual(selected, {})
            self.assertEqual(other, ['tests/../outside.py'])


if __name__ == '__main__':
    unittest.main()
