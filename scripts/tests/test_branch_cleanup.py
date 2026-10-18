"""Exercise actual atomic archive/delete transactions against a local bare Git remote."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('cleanup', Path(__file__).resolve().parents[1] / 'cleanup_reconciled_branches.py')
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)


class Transactions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.local = self.root / 'local'
        self.remote = self.root / 'remote.git'
        self.run_git(self.root, 'init', '--quiet', '--bare', str(self.remote))
        self.run_git(self.root, 'init', '--quiet', str(self.local))
        self.run_git(self.local, 'config', 'user.name', 'Fixture')
        self.run_git(self.local, 'config', 'user.email', 'fixture@example.invalid')
        self.run_git(self.local, 'commit', '--quiet', '--allow-empty', '-m', 'baseline')
        self.sha = self.run_git(self.local, 'rev-parse', 'HEAD').strip()
        self.run_git(self.local, 'push', '--quiet', str(self.remote), 'HEAD:refs/heads/main', 'HEAD:refs/heads/retired-one', 'HEAD:refs/heads/retired-two')
        self.plan = {'schema': 'ncp.branch-cleanup-plan.v1', 'candidate_sha': self.sha,
                     'protected': ['main'], 'archive_tag_prefix': 'archive/fixture/', 'entry_count': 2,
                     'entries': [{'branch': n, 'expected_head': self.sha, 'archive_ref': 'refs/tags/archive/fixture/' + n,
                                  'archive_before_delete': True, 'action': 'eligible_after_confirmation'}
                                 for n in ['retired-one', 'retired-two']]}
        self.path = self.root / 'plan.json'
        self.save()

    def run_git(self, root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.DEVNULL)

    def save(self):
        self.path.write_text(json.dumps(self.plan))
        self.validated, self.identity = cleanup.read_plan(self.local, self.path)

    def refs(self):
        return self.run_git(self.local, 'ls-remote', '--refs', str(self.remote))

    def execute(self, receipt='receipt.json'):
        return cleanup.execute(self.local, str(self.remote), self.validated, self.identity, self.identity, self.root / receipt)

    def test_read_only_then_one_atomic_archive_and_delete_and_idempotent_resume(self):
        before = self.refs()
        report = cleanup.inspect(self.local, str(self.remote), self.validated, self.identity)
        self.assertEqual(report['status'], 'ready')
        self.assertEqual(self.refs(), before)
        done = self.execute()
        self.assertEqual(done['status'], 'complete')
        self.assertTrue(done['remote_mutated'])
        intent = json.loads((self.root / 'receipt.json.intent.json').read_text())
        self.assertEqual(intent['status'], 'intent_recorded')
        self.assertTrue(all(row['observed_head'] == self.sha for row in intent['entries']))
        self.assertNotIn('refs/heads/retired-', self.refs())
        self.assertEqual(self.refs().count('refs/tags/archive/fixture/'), 2)
        resumed = self.execute('resumed.json')
        self.assertEqual(resumed['status'], 'complete')
        self.assertFalse(resumed['remote_mutated'])

    def test_changed_head_refuses_the_whole_batch(self):
        self.run_git(self.local, 'commit', '--quiet', '--allow-empty', '-m', 'advanced')
        self.run_git(self.local, 'push', '--quiet', str(self.remote), 'HEAD:refs/heads/retired-one')
        before = self.refs()
        with self.assertRaises(ValueError): self.execute()
        self.assertEqual(self.refs(), before)
        self.assertFalse((self.root / 'receipt.json').exists())

    def test_changed_candidate_anchor_refuses_without_deleting_heads(self):
        self.run_git(self.local, 'commit', '--quiet', '--allow-empty', '-m', 'anchor changed')
        self.run_git(self.local, 'push', '--quiet', str(self.remote), 'HEAD:refs/heads/main')
        before = self.refs()
        with self.assertRaises(ValueError): self.execute()
        self.assertEqual(self.refs(), before)

    def test_head_race_between_inspection_and_push_is_caught_by_server_lease(self):
        self.run_git(self.local, 'commit', '--quiet', '--allow-empty', '-m', 'racing update')
        original = cleanup.git

        def race(root, *args):
            if args[0] == 'push':
                self.run_git(self.local, 'push', '--quiet', str(self.remote), 'HEAD:refs/heads/retired-one')
            return original(root, *args)

        with patch.object(cleanup, 'git', side_effect=race), self.assertRaises(ValueError): self.execute()
        refs = self.refs()
        self.assertIn('refs/heads/retired-two', refs)
        self.assertNotIn('refs/tags/archive/', refs)
        self.assertEqual(json.loads((self.root / 'receipt.json').read_text())['status'], 'refused_or_uncertain')

    def test_conflicting_archive_race_refuses_all_deletions(self):
        self.run_git(self.local, 'commit', '--quiet', '--allow-empty', '-m', 'conflicting archive')
        original = cleanup.git

        def race(root, *args):
            if args[0] == 'push':
                self.run_git(self.local, 'push', '--quiet', str(self.remote), 'HEAD:refs/tags/archive/fixture/retired-one')
            return original(root, *args)

        with patch.object(cleanup, 'git', side_effect=race), self.assertRaises(ValueError): self.execute()
        self.assertIn('refs/heads/retired-one', self.refs())
        self.assertIn('refs/heads/retired-two', self.refs())
        self.assertNotIn('refs/tags/archive/fixture/retired-two', self.refs())

    def test_concurrent_identical_archive_preserves_exact_source_and_completes(self):
        original = cleanup.git

        def race(root, *args):
            if args[0] == 'push':
                self.run_git(self.local, 'push', '--quiet', str(self.remote), 'HEAD:refs/tags/archive/fixture/retired-one')
            return original(root, *args)

        with patch.object(cleanup, 'git', side_effect=race):
            result = self.execute()
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(all(row['observed_archive'] == self.sha for row in result['entries']))
        self.assertNotIn('refs/heads/retired-', self.refs())

    def test_existing_archive_with_live_head_requires_review(self):
        self.run_git(self.local, 'push', '--quiet', str(self.remote), 'HEAD:refs/tags/archive/fixture/retired-one')
        before = self.refs()
        with self.assertRaises(ValueError): self.execute()
        self.assertEqual(self.refs(), before)

    def test_server_refusal_does_not_delete_unarchived_branches(self):
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        before = self.refs()
        with self.assertRaises(ValueError): self.execute()
        self.assertEqual(self.refs(), before)

    def test_wrong_confirmation_and_existing_receipt_refuse_without_mutation(self):
        before = self.refs()
        with self.assertRaises(ValueError):
            cleanup.execute(self.local, str(self.remote), self.validated, self.identity, '0' * 64, self.root / 'receipt.json')
        (self.root / 'receipt.json').write_text('prior evidence')
        with self.assertRaises(FileExistsError): self.execute()
        self.assertEqual(self.refs(), before)
        self.assertEqual((self.root / 'receipt.json').read_text(), 'prior evidence')

    def test_protected_duplicate_and_misdirected_archive_plans_refuse(self):
        for field, value in [('branch', 'main'), ('branch', 'release/frozen'), ('branch', 'retired-two'),
                             ('archive_ref', 'refs/heads/main'), ('expected_head', 'HEAD')]:
            with self.subTest(field=field, value=value):
                bad = deepcopy(self.plan)
                bad['entries'][0][field] = value
                self.path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError): cleanup.read_plan(self.local, self.path)


if __name__ == '__main__':
    unittest.main()
