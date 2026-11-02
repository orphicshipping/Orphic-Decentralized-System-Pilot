"""Synthetic local Linux subprocess qualification, not native Windows or hosted CI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ciw import coordinator, retention

CHILD = r'''
import json, os, sys
from ciw import coordinator, retention
stage, grant = sys.argv[1:]
original = retention._publish
def publish(directory, name, raw):
    kind = ('intent' if name.endswith('.intent.json') else
            'receipt' if name.endswith('.claimed.json') else
            'artifact' if name.startswith('retained-') else 'other')
    if stage == 'before_' + kind:
        os._exit(91)
    original(directory, name, raw)
    if stage == 'after_' + kind:
        os._exit(91)
retention._publish = coordinator._publish = publish
print(json.dumps(coordinator.claim(retention.Store('recipient'), grant)), flush=True)
'''

@unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"),
                     "Recovery filesystem qualification requires POSIX no-follow support")
class ClaimRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'NCP_RETAIN_DIR': self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.sender, self.recipient = retention.Store('sender'), retention.Store('recipient')
        self.bundle = {'synthetic': True, 'values': [1, 2, 3]}
        self.source = self.sender.keep(self.bundle, kind='fixture', catalog={'source': 'test'})
        self.grant = coordinator.handoff(self.sender, self.source, 'recipient')['grant_id']

    def child_env(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join(filter(None, (
            str(Path(retention.__file__).resolve().parents[1]), env.get('PYTHONPATH'))))
        return env

    def child(self, stage):
        return subprocess.run([sys.executable, '-c', CHILD, stage, self.grant], capture_output=True, text=True, timeout=20, env=self.child_env())

    def artifacts(self):
        return list(retention._owner_dir('recipient').glob('retained-*.json'))

    def test_crash_boundaries_reconnect_and_replay(self):
        for stage in ('before_intent', 'after_intent', 'before_artifact', 'after_artifact', 'before_receipt', 'after_receipt'):
            with self.subTest(stage=stage):
                # Each boundary has independent persisted state and real process exit.
                with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'NCP_RETAIN_DIR': root}):
                    source = self.sender.keep(self.bundle, kind='fixture', catalog={})
                    self.grant = coordinator.handoff(self.sender, source, 'recipient')['grant_id']
                    crashed = self.child(stage)
                    self.assertEqual(crashed.returncode, 91, crashed.stderr)
                    before = self.artifacts()
                    retry = self.child('none')
                    self.assertEqual(retry.returncode, 0, retry.stderr)
                    receipt = json.loads(retry.stdout)
                    self.assertEqual(len(self.artifacts()), 1)
                    self.assertEqual(receipt['replayed'], stage != 'before_intent')
                    if before:
                        self.assertEqual(before[0].stem, receipt['artifact_id'])
                    self.assertEqual(self.recipient.native(receipt['artifact_id']), self.bundle)
                    again = json.loads(self.child('none').stdout)
                    self.assertTrue(again['replayed'])
                    self.assertEqual(again['artifact_id'], receipt['artifact_id'])

    def test_concurrent_processes_after_crash_have_one_identity(self):
        self.assertEqual(self.child('after_artifact').returncode, 91)
        workers = [subprocess.Popen([sys.executable, '-c', CHILD, 'none', self.grant],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.child_env()) for _ in range(6)]
        results = []
        for child in workers:
            out, err = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, err)
            results.append(json.loads(out))
        self.assertEqual(len({r['artifact_id'] for r in results}), 1)
        self.assertTrue(all(r['replayed'] for r in results))
        self.assertEqual(len(self.artifacts()), 1)

    def test_recovery_finishes_eviction_after_artifact_publication(self):
        # Fill the recipient budget; the child dies before _write calls _evict.
        for i in range(retention._LIMIT):
            self.recipient.keep({'i': i}, kind='fixture', catalog={})
        self.assertEqual(self.child('after_artifact').returncode, 91)
        self.assertEqual(len(self.artifacts()), retention._LIMIT + 1)
        result = coordinator.claim(self.recipient, self.grant)
        self.assertEqual(len(self.artifacts()), retention._LIMIT)
        self.assertEqual(self.recipient.native(result['artifact_id']), self.bundle)

    def test_recovery_after_newer_publications_preserves_count_budget(self):
        self.assertEqual(self.child('after_intent').returncode, 91)
        for i in range(retention._LIMIT):
            self.recipient.keep({'i': i}, kind='fixture', catalog={})
        result = coordinator.claim(self.recipient, self.grant)
        self.assertEqual(len(self.artifacts()), retention._LIMIT)
        self.assertEqual(self.recipient.native(result['artifact_id']), self.bundle)

    def test_recovery_after_newer_publications_preserves_byte_budget(self):
        self.assertEqual(self.child('after_intent').returncode, 91)
        native = {'blob': ''}
        envelope = {'schema': 'ncp.retained-bundle.v1', 'owner': 'recipient',
                    'kind': 'fixture', 'sequence': 2, 'native': native, 'catalog': {}}
        native['blob'] = 'x' * (retention._BYTE_LIMIT // 2 - len(retention._canonical(envelope)))
        for _ in range(2):
            self.recipient.keep(native, kind='fixture', catalog={})
        self.assertEqual(sum(path.stat().st_size for path in self.artifacts()), retention._BYTE_LIMIT)
        result = coordinator.claim(self.recipient, self.grant)
        self.assertLessEqual(sum(path.stat().st_size for path in self.artifacts()), retention._BYTE_LIMIT)
        self.assertEqual(self.recipient.native(result['artifact_id']), self.bundle)

    def test_full_size_artifacts_allow_grant_envelope_and_legacy_spacing(self):
        for native in ({'padding': ''}, {'values': [0] * 1_400_000, 'padding': ''}):
            with self.subTest(has_many_values='values' in native):
                with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'NCP_RETAIN_DIR': root}):
                    sender, recipient = retention.Store('sender'), retention.Store('reader')
                    envelope = {'schema': 'ncp.retained-bundle.v1', 'owner': 'sender',
                                'kind': 'fixture', 'sequence': 1, 'native': native, 'catalog': {}}
                    native['padding'] = 'x' * (retention._BYTE_LIMIT - len(retention._canonical(envelope)))
                    source = sender.keep(native, kind='fixture', catalog={})
                    self.assertEqual(sender._path(source).stat().st_size, retention._BYTE_LIMIT)
                    grant = coordinator.handoff(sender, source, 'reader')['grant_id']
                    grant_path = Path(root) / 'grants' / (grant + '.json')
                    self.assertGreater(grant_path.stat().st_size, retention._BYTE_LIMIT)
                    result = coordinator.claim(recipient, grant)
                    self.assertEqual(recipient.native(result['artifact_id']), native)
                    self.assertEqual(recipient._path(result['artifact_id']).stat().st_size, retention._BYTE_LIMIT)
                    self.assertTrue(coordinator.claim(recipient, grant)['replayed'])

    def test_grant_and_artifact_read_budgets_remain_bounded(self):
        grant_path = Path(self.temp.name) / 'grants' / (self.grant + '.json')
        grant_path.write_bytes(b' ' * (coordinator._GRANT_BYTE_LIMIT + 1))
        with self.assertRaisesRegex(ValueError, 'exceeds the byte budget'):
            coordinator.claim(self.recipient, self.grant)
        self.sender._path(self.source).write_bytes(b' ' * (retention._BYTE_LIMIT + 1))
        with self.assertRaisesRegex(ValueError, 'exceeds the byte budget'):
            self.sender.native(self.source)

    def test_wrong_owner_cannot_claim_before_or_after_crash(self):
        with self.assertRaisesRegex(ValueError, 'handoff_refused'):
            coordinator.claim(retention.Store('intruder'), self.grant)
        self.assertEqual(self.child('after_artifact').returncode, 91)
        with self.assertRaisesRegex(ValueError, 'handoff_refused'):
            coordinator.claim(retention.Store('intruder'), self.grant)
        result = coordinator.claim(self.recipient, self.grant)
        with self.assertRaisesRegex(ValueError, 'another host'):
            self.sender.native(result['artifact_id'])
        with self.assertRaisesRegex(ValueError, 'handoff_refused'):
            coordinator.claim(retention.Store('intruder'), self.grant)

    def test_changed_grant_rejected_after_intent(self):
        self.assertEqual(self.child('after_intent').returncode, 91)
        path = Path(self.temp.name) / 'grants' / (self.grant + '.json')
        value = json.loads(path.read_text())
        value['native'] = {'changed': True}
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'handoff_intent_conflict'):
            coordinator.claim(self.recipient, self.grant)
        self.assertEqual(self.artifacts(), [])

    def test_symlink_records_and_artifact_are_refused(self):
        self.assertEqual(self.child('after_artifact').returncode, 91)
        paths = [Path(self.temp.name) / 'grants' / (self.grant + suffix)
                 for suffix in ('.json', '.intent.json', '.claimed.json')]
        paths.append(self.artifacts()[0])
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes() if path.exists() else None
                target = path.with_suffix('.saved')
                target.write_bytes(original or b'{}')
                path.unlink(missing_ok=True)
                path.symlink_to(target)
                with self.assertRaises(ValueError):
                    coordinator.claim(self.recipient, self.grant)
                path.unlink()
                if original is not None:
                    path.write_bytes(original)
        self.assertTrue(coordinator.claim(self.recipient, self.grant)['replayed'])

    def test_missing_nofollow_is_explicit_refusal_not_attribute_error(self):
        flag = os.O_NOFOLLOW
        del os.O_NOFOLLOW
        try:
            with self.assertRaisesRegex(ValueError, 'no-follow reads are unsupported'):
                self.sender.native(self.source)
            with self.assertRaisesRegex(ValueError, 'no-follow reads are unsupported'):
                coordinator.claim(self.recipient, self.grant)
        finally:
            os.O_NOFOLLOW = flag

    def test_malformed_or_mismatched_receipt_refuses(self):
        path = Path(self.temp.name) / 'grants' / (self.grant + '.claimed.json')
        result = coordinator.claim(self.recipient, self.grant)
        valid = json.loads(path.read_text())
        for invalid in ({'recipient': 'recipient'}, {**valid, 'artifact_id': None},
                        {**valid, 'source_artifact_id': ''}, {**valid, 'schema': 'wrong'},
                        {**valid, 'grant_id': 'grant-' + '0' * 32},
                        {**valid, 'source_owner': 'someone-else'}):
            path.write_text(json.dumps(invalid))
            with self.assertRaisesRegex(ValueError, 'handoff_receipt_invalid'):
                coordinator.claim(self.recipient, self.grant)
        # Valid legacy receipts remain replayable even after explicit release.
        legacy = {k: v for k, v in valid.items() if k not in {'grant_id', 'source_owner'}}
        path.write_text(json.dumps(legacy))
        self.recipient.release(result['artifact_id'])
        self.assertEqual(coordinator.claim(self.recipient, self.grant)['artifact_id'], result['artifact_id'])
        self.assertEqual(self.artifacts(), [])

    def test_grant_identity_must_match_its_source(self):
        path = Path(self.temp.name) / 'grants' / (self.grant + '.json')
        original = json.loads(path.read_text())
        for mutation in ({'owner': 'other'}, {'source_artifact_id': 'retained-' + '0' * 32},
                         {'schema': 'wrong'}, {'native': []}):
            path.write_text(json.dumps({**original, **mutation}))
            with self.assertRaisesRegex(ValueError, 'handoff_grant_invalid'):
                coordinator.claim(self.recipient, self.grant)
        self.assertEqual(self.artifacts(), [])

    def test_partial_writes_publish_complete_record(self):
        original = os.write
        with patch.object(retention.os, 'write', side_effect=lambda fd, data: original(fd, data[:3])):
            result = coordinator.claim(self.recipient, self.grant)
        self.assertEqual(self.recipient.native(result['artifact_id']), self.bundle)

if __name__ == '__main__':
    unittest.main(verbosity=2)
