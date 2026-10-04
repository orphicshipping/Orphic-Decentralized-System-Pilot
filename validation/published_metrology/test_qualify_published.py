"""Integrity and numerical-acceptance checks for the published-data runner.

Run with the pinned installed NET Python:
  python -m unittest discover -s validation/published_metrology -p test_qualify_published.py -v
Set NET_QUALIFICATION_CORPUS, NET_QUALIFICATION_WHEEL and
NET_QUALIFICATION_WHEEL_SHA256 explicitly before running.
Only manifest-declared files are copied into disposable workspace directories.
"""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('net_published_qualification_under_test', HERE / 'qualify_published.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
CORPUS = Path(os.environ['NET_QUALIFICATION_CORPUS']).resolve()
WHEEL = Path(os.environ['NET_QUALIFICATION_WHEEL']).resolve()
WHEEL_SHA256 = os.environ['NET_QUALIFICATION_WHEEL_SHA256']


class QualificationIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Missing evidence is a test failure, not an optional-dependency skip.
        cls.manifest = runner.preflight(CORPUS)
        cls.kriss = runner.read_json(CORPUS / 'qualification' / 'kriss-kernel-oracles.json')

    @contextmanager
    def corpus_copy(self):
        with tempfile.TemporaryDirectory(prefix='qualification-test-') as name:
            root = Path(name)
            for item in self.manifest['files']:
                source = runner.checked_path(CORPUS, item['path'])
                target = runner.checked_path(root, item['path'])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            (root / 'qualification').mkdir(exist_ok=True)
            self.write_manifest(root, self.manifest)
            yield root

    @staticmethod
    def write_manifest(root, manifest):
        (root / 'qualification' / 'inputs.json').write_text(json.dumps(manifest), encoding='utf-8')

    def test_nominal_executes_all_published_cases(self):
        report = runner.qualify(CORPUS, WHEEL, WHEEL_SHA256)
        self.assertEqual(report['status'], 'passed_bounded_kernel_reproduction')
        self.assertEqual(report['summary']['cases'], 147)
        self.assertEqual(report['summary']['passed'], 147)
        self.assertEqual(report['summary']['failed'], 0)
        self.assertTrue(all(case['passed'] for case in report['cases']))
        self.assertEqual(len({case['id'] for case in report['cases']}), 147)

    def test_changed_workbook_bytes_are_refused(self):
        with self.corpus_copy() as root:
            path = runner.checked_path(root, self.kriss['source']['asset_path'])
            raw = bytearray(path.read_bytes())
            raw[-1] ^= 1  # Same length: requires a content digest check.
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                runner.qualify(root, WHEEL, WHEEL_SHA256)

    def test_changed_original_reference_pdf_is_refused(self):
        reference = next(item['path'] for item in self.manifest['files'] if item['path'].endswith('.pdf'))
        with self.corpus_copy() as root:
            path = runner.checked_path(root, reference)
            path.write_bytes(path.read_bytes() + b'\nchanged-source')
            with self.assertRaises(ValueError):
                runner.qualify(root, WHEEL, WHEEL_SHA256)

    def test_changed_fixture_is_refused(self):
        with self.corpus_copy() as root:
            path = root / 'qualification' / 'helium-table12-rss.json'
            data = runner.read_json(path)
            data['rows'][0]['expected_combined_u'] *= 2
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                runner.qualify(root, WHEEL, WHEEL_SHA256)

    def test_resealing_changed_fixture_does_not_authorize_it(self):
        with self.corpus_copy() as root:
            relative = 'qualification/helium-table12-rss.json'
            path = runner.checked_path(root, relative)
            data = runner.read_json(path)
            data['rows'][0]['expected_combined_u'] *= 2
            # A fabricated broad tolerance must not become an authorized oracle
            # merely because the attacker updates the colocated manifest.
            data['rows'][0]['source_rounding_absolute_tolerance'] = 1.0
            path.write_text(json.dumps(data), encoding='utf-8')
            manifest = runner.read_json(root / 'qualification' / 'inputs.json')
            item = next(item for item in manifest['files'] if item['path'] == relative)
            item.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            self.write_manifest(root, manifest)
            with self.assertRaises(ValueError):
                runner.qualify(root, WHEEL, WHEEL_SHA256)

    def test_every_required_manifest_entry_is_enforced(self):
        for omitted in self.manifest['files']:
            with self.subTest(omitted=omitted['path']), self.corpus_copy() as root:
                manifest = runner.read_json(root / 'qualification' / 'inputs.json')
                manifest['files'] = [row for row in manifest['files'] if row['path'] != omitted['path']]
                self.write_manifest(root, manifest)
                with self.assertRaises(ValueError):
                    runner.qualify(root, WHEEL, WHEEL_SHA256)

    def test_empty_manifest_is_refused(self):
        with self.corpus_copy() as root:
            self.write_manifest(root, {'files': []})
            with self.assertRaises(ValueError):
                runner.qualify(root, WHEEL, WHEEL_SHA256)

    def test_wrong_explicit_wheel_digest_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'Wheel differs'):
            runner.qualify(CORPUS, WHEEL, '0' * 64)

    def test_malformed_explicit_wheel_digest_is_refused(self):
        for digest in ('', 'not-a-digest', 'g' * 64):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                runner.qualify(CORPUS, WHEEL, digest)

    def test_resealed_changed_wheel_is_not_the_installed_package(self):
        import zipfile
        with tempfile.TemporaryDirectory(prefix='qualification-wheel-') as name:
            altered = Path(name) / WHEEL.name
            changed = False
            with zipfile.ZipFile(WHEEL) as original, zipfile.ZipFile(altered, 'w') as output:
                for entry in original.infolist():
                    raw = original.read(entry.filename)
                    if entry.filename == 'ciw/temperature_processing.py':
                        raw += b'\n# altered artifact\n'
                        changed = True
                    output.writestr(entry, raw)
            self.assertTrue(changed)
            with self.assertRaisesRegex(ValueError, 'Installed package differs'):
                runner.qualify(CORPUS, altered, runner.digest(altered))


class NumericalAcceptanceTests(unittest.TestCase):
    def test_wrong_observed_result_fails_without_changing_expected(self):
        result = runner.check_value(3.0, 1.0, 0.01, 0.01)
        self.assertFalse(result['passed'])
        self.assertEqual(result['actual'], 3.0)
        self.assertEqual(result['expected'], 1.0)
        self.assertEqual(result['absolute_error'], 2.0)

    def test_nonfinite_results_and_tolerances_are_rejected(self):
        for index in range(4):
            for value in (math.nan, math.inf, -math.inf):
                args = [1.0, 1.0, 0.01, 0.01]
                args[index] = value
                with self.subTest(argument=index, value=value), self.assertRaises(ValueError):
                    runner.check_value(*args)

    def test_nonfinite_json_numbers_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='qualification-json-test-') as name:
            path = Path(name) / 'fixture.json'
            for literal in ('NaN', 'Infinity', '-Infinity'):
                path.write_text('{"result":' + literal + '}', encoding='utf-8')
                with self.subTest(literal=literal), self.assertRaises(ValueError):
                    runner.read_json(path)


if __name__ == '__main__':
    unittest.main()
