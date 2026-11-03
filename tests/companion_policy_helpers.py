"""Test-only operator policies; these do not qualify a production host.

Expected hashes here identify this test machine's fixed launchers. Production
configuration must contain independently reviewed operator pins. Positive tests
skip only after the actual gate reports a held host; no gate is patched out.
"""
from pathlib import Path
import os
import stat
import subprocess
import sys
import tempfile

import pytest

from ciw.process_policy import ProcessPolicy, ProcessPolicyHeld, executable_sha256, minimal_environment


def _roots(repositories):
    roots = {Path('/usr'), Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
             Path(sys.executable).resolve().parent.parent}
    for repository in repositories.values():
        repo = Path(repository).resolve(strict=True)
        roots.add(repo)
        # A linked worktree needs its explicit Git metadata mounts as well.
        if (repo / '.git').exists():
            probe = subprocess.run(['/usr/bin/git', '-C', str(repo), 'rev-parse', '--absolute-git-dir', '--git-common-dir'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=True, env=minimal_environment())
            if len(probe.stdout) > 16384:
                raise ValueError('Test Git metadata exceeds its budget')
            metadata = []
            for line in probe.stdout.decode().splitlines():
                path = Path(line)
                path = (path if path.is_absolute() else repo / path).resolve(strict=True)
                roots.add(path)
                metadata.append(path)
            # Prepared, test-owned clones use an existing shared object store.
            # Mount only its exact object directory, never the surrounding user
            # workspace. Production configuration does no such discovery.
            pending = [path / 'objects' for path in metadata if (path / 'objects').is_dir()]
            seen = set()
            while pending:
                objects = pending.pop().resolve(strict=True)
                if objects in seen:
                    continue
                seen.add(objects)
                if len(seen) > 32:
                    raise ValueError('Test Git alternates exceed their directory budget')
                alternate = objects / 'info/alternates'
                if not alternate.exists():
                    continue
                fd = os.open(alternate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
                        raise ValueError('Test Git alternates exceed their byte budget')
                    raw = os.read(fd, 8193)
                    if len(raw) > 8192:
                        raise ValueError('Test Git alternates exceed their byte budget')
                finally:
                    os.close(fd)
                for line in raw.decode().splitlines():
                    path = Path(line)
                    path = (path if path.is_absolute() else objects / path).resolve(strict=True)
                    if not path.is_dir():
                        raise ValueError('Test Git alternate is not an object directory')
                    roots.add(path)
                    pending.append(path)
    return tuple(sorted(str(root) for root in roots))


def test_policy(repo, *, repositories=None):
    """Construct a real strict policy without claiming namespace availability."""
    return ProcessPolicy(mode='bubblewrap',
        expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'),
        expected_bwrap_sha256=executable_sha256('/usr/bin/bwrap'),
        read_only_roots=_roots(repositories or {'repository': repo}),
        max_input_bytes=4*1024*1024, max_output_bytes=4*1024*1024)


test_policy.__test__ = False


def qualified_policies(repositories):
    """Probe the actual namespace gate; absence is recorded as skipped, not pass."""
    if not Path('/usr/bin/bwrap').is_file() or not Path('/usr/bin/prlimit').is_file():
        pytest.skip('Companion qualification held: required Bubblewrap/prlimit launcher is unavailable')
    selected = {role: test_policy(repo, repositories=repositories) for role, repo in repositories.items()}
    with tempfile.TemporaryDirectory(prefix='ciw-policy-test-') as directory:
        for policy in selected.values():
            try:
                policy.qualify(Path(directory))
            except ProcessPolicyHeld as exc:
                pytest.skip('Companion qualification held: ' + exc.reason)
    return selected


def policy_config_document(workflows):
    """Serialize only the explicit operator fields accepted by the real loader."""
    return {'schema': 'ciw.companion-policies.v1', 'workflows': {
        workflow: {role: {name: list(value) if name == 'read_only_roots' else value
                         for name in policy.__dataclass_fields__ if name not in {'mode', 'environment'}
                         for value in [getattr(policy, name)]}
                   for role, policy in policies.items()}
        for workflow, policies in workflows.items()}}
