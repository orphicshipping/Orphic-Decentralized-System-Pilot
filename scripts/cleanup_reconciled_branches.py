"""Archive and remove only confirmed exact heads in the retained cleanup plan.

Default mode is read-only. Execution requires the exact plan SHA-256 and uses
one atomic push for the archive creations and exact-head deletions.
Missing Git credentials or server atomic support is a refusal, never a fallback.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def git(root, *args):
    process = subprocess.run(
        ['git', '--no-replace-objects', '-c', 'core.fsmonitor=false', '-C', str(root), *args],
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}, capture_output=True,
        text=True, timeout=180, check=False)
    if process.returncode:
        # Git diagnostics may contain credential-bearing remote URLs. Retain
        # only the operation and exit code, never command text or raw stderr.
        raise ValueError(f'Git {args[0]} refused (exit {process.returncode}); no non-atomic retry performed')
    return process.stdout


def read_plan(root, path):
    raw = Path(path).read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Cleanup plan exceeds byte budget')

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate cleanup-plan key')
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError('Cleanup plan must be an object')
    entries = value.get('entries')
    if (value.get('schema') != 'ncp.branch-cleanup-plan.v1'
            or not isinstance(entries, list) or not 1 <= len(entries) <= 500
            or value.get('entry_count') != len(entries)
            or not re.fullmatch(r'[0-9a-f]{40}', value.get('candidate_sha', ''))):
        raise ValueError('Unsupported or incomplete cleanup plan')
    prefix = value.get('archive_tag_prefix', '')
    if isinstance(prefix, str) and prefix.startswith('archive/'):
        prefix = 'refs/tags/' + prefix
    if not isinstance(prefix, str) or not prefix.startswith('refs/tags/archive/'):
        raise ValueError('Require the retained archive tag prefix')
    protected = value.get('protected')
    if not isinstance(protected, list) or 'main' not in protected:
        raise ValueError('Require explicit protected branches including main')
    names = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Cleanup entry must be an object')
        name = entry.get('branch', '')
        sha = entry.get('expected_head', '')
        archive = entry.get('archive_ref', '')
        if (not isinstance(name, str) or name in names or name in protected
                or name.startswith('release/') or name == 'main'
                or entry.get('action') != 'eligible_after_confirmation'
                or entry.get('archive_before_delete') is not True
                or not re.fullmatch(r'[0-9a-f]{40}', sha)
                or archive != prefix.rstrip('/') + '/' + name):
            raise ValueError('Invalid, duplicate, protected or unconfirmed cleanup entry')
        git(root, 'check-ref-format', 'refs/heads/' + name)
        git(root, 'check-ref-format', archive)
        names.add(name)
    return value, hashlib.sha256(raw).hexdigest()


def inspect(root, remote, plan, plan_hash):
    if not isinstance(remote, str) or not remote or remote.startswith('-'):
        raise ValueError('Require an explicit Git remote without option syntax')
    # Request exact names, avoiding wildcard selection or a mutable branch glob.
    anchor_name = ('release/research-baseline-20261017'
                   if 'release/research-baseline-20261017' in plan['protected'] else 'main')
    anchor_ref = 'refs/heads/' + anchor_name
    refs = [anchor_ref] + [ref for row in plan['entries'] for ref in ('refs/heads/' + row['branch'], row['archive_ref'])]
    observed = {}
    for line in git(root, 'ls-remote', '--refs', remote, *refs).splitlines():
        sha, ref = line.split('\t', 1)
        if ref in observed or ref not in refs or not re.fullmatch(r'[0-9a-f]{40}', sha):
            raise ValueError('Unexpected or ambiguous remote reference response')
        observed[ref] = sha
    rows = []
    for entry in plan['entries']:
        sha = entry['expected_head']
        head = observed.get('refs/heads/' + entry['branch'])
        archive = observed.get(entry['archive_ref'])
        if head is None and archive == sha:
            state = 'already_archived_and_removed'
        elif head != sha:
            state = 'missing_or_advanced_head'
        elif archive is not None:
            # A no-op update would not participate in Git's atomic transaction.
            # Refuse pre-existing archives while a branch is still present.
            state = 'archive_exists_requires_review'
        else:
            state = 'ready'
        rows.append({**entry, 'observed_head': head, 'observed_archive': archive, 'state': state})
    anchor_matches = observed.get(anchor_ref) == plan['candidate_sha']
    return {'schema': 'ncp.branch-cleanup-receipt.v1', 'plan_sha256': plan_hash,
            'candidate_sha': plan['candidate_sha'], 'observed_at': datetime.now(timezone.utc).isoformat(),
            'candidate_anchor': {'ref': anchor_ref, 'observed': observed.get(anchor_ref), 'matches': anchor_matches},
            'status': 'blocked' if not anchor_matches or any(r['state'] not in {'ready', 'already_archived_and_removed'} for r in rows) else 'ready',
            'entries': rows, 'remote_mutated': False}


def execute(root, remote, plan, plan_hash, confirmation, receipt_path):
    if confirmation != plan_hash:
        raise ValueError('Execution requires the exact reviewed --plan-sha256')
    report = inspect(root, remote, plan, plan_hash)
    if report['status'] != 'ready':
        raise ValueError('Cleanup refused: a head/archive changed; review the read-only report')
    pending = [row for row in report['entries'] if row['state'] == 'ready']
    for row in pending:
        git(root, 'cat-file', '-e', row['expected_head'] + '^{commit}')
    leases = []
    updates = []
    for row in pending:
        head = 'refs/heads/' + row['branch']
        leases.extend(['--force-with-lease=' + head + ':' + row['expected_head'],
                       '--force-with-lease=' + row['archive_ref'] + ':'])
        updates.extend([row['expected_head'] + ':' + row['archive_ref'], ':' + head])
    # Publish a new intent receipt before mutation. Never overwrite prior evidence.
    report['status'] = 'intent_recorded' if pending else 'already_complete'
    path = Path(receipt_path)
    intent = Path(str(path) + '.intent.json')
    if path.exists() or intent.exists():
        raise FileExistsError('Preserve existing cleanup intent/result receipts; select fresh paths')
    report['intent_receipt'] = str(intent)
    # Reserve the result path before mutation; keep immutable pre-push intent
    # separately so an interrupted result update cannot erase the initial facts.
    with path.open('x', encoding='utf-8') as result:
        with intent.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        result.write(json.dumps(report, indent=2) + '\n')
        result.flush()
        os.fsync(result.fileno())
    try:
        if pending:
            git(root, 'push', '--atomic', '--porcelain', *leases, remote, *updates)
        observed = inspect(root, remote, plan, plan_hash)
        if not observed['candidate_anchor']['matches'] or any(row['state'] != 'already_archived_and_removed' for row in observed['entries']):
            raise ValueError('Remote verification changed after push; inspect before any retry')
        report.update(status='complete', remote_mutated=bool(pending), entries=observed['entries'])
    except (ValueError, OSError, subprocess.TimeoutExpired):
        # A timeout is uncertain. Preserve every intent and let a fresh read-only
        # inspection establish the actual remote state before another invocation.
        report.update(status='refused_or_uncertain', remote_mutated='unknown')
        path.write_text(json.dumps(report, indent=2) + '\n')
        raise
    path.write_text(json.dumps(report, indent=2) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=Path.cwd())
    parser.add_argument('--remote', required=True, help='Explicit authenticated Git remote; no network default')
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--plan-sha256')
    parser.add_argument('--receipt', type=Path, help='New result JSON; immutable sibling intent also retained; required for execution')
    args = parser.parse_args(argv)
    try:
        plan, identity = read_plan(args.repository, args.plan)
        if args.execute:
            if args.receipt is None:
                raise ValueError('Execution requires a new --receipt path')
            report = execute(args.repository, args.remote, plan, identity, args.plan_sha256, args.receipt)
        else:
            if args.plan_sha256 is not None or args.receipt is not None:
                raise ValueError('Execution flags require --execute')
            report = inspect(args.repository, args.remote, plan, identity)
        print(json.dumps(report, indent=2))
        return 0 if report['status'] in {'ready', 'complete'} else 1
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
