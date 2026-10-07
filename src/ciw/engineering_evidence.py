"""Retain opaque supporting files against an existing engineering follow-up.

This is local evidence assembly, not a qualified empirical importer. Source
findings, scientific occurrences and authority are never changed. Files are
neither executed nor interpreted, and the original thread remains immutable.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from . import engineering_thread as e, engineering_thread_io as thread, link_contract as c
from .engineering_thread_io import _write_raw, _json_raw
from .hardware_workflow import read_bytes
from .link_store import safe_path
from .operations.runner import seal, check_seal

MAX_ATTACHMENTS = 16
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_PACKET_BYTES = 65536
_POLICY = {
    'schema': 'ciw.engineering-support-evidence.v1',
    'status': 'RETAINED_NOT_ADMITTED',
    'content_interpretation': 'opaque_bytes_not_executed_or_parsed',
    'evidence_admitted': False, 'requirement_satisfied': False,
    'authorizes_execution': False, 'new_scientific_executions': 0,
    'active_authority_restored': False,
}


def _selected_action(follow_up: dict, action_id: str) -> dict:
    c.content_ref(action_id)
    matches = [a for a in follow_up['actions'] if a['action_id'] == action_id]
    if len(matches) != 1:
        raise ValueError('Select one original unmet action from this exact retained thread')
    return matches[0]


def _validate_packet(packet: dict, follow_up: dict) -> None:
    """Validate claims against the actual thread, not against the packet itself."""
    c.bounded(packet, MAX_PACKET_BYTES)
    c.keys(packet, set(_POLICY) | {'basis', 'action', 'attachments', 'note',
                                 'retention_method_ref', 'scope', 'record_digest'})
    check_seal(packet)
    if any(not e._same(packet[key], value) for key, value in _POLICY.items()):
        raise ValueError('Supporting material cannot claim admission, qualification or authority')
    if not e._same(packet['scope'], e.SCOPE):
        raise ValueError('Supporting material changes the original qualification scope')
    if not e._same(packet['basis'], follow_up['basis']):
        raise ValueError('Supporting material belongs to a different source thread or occurrence')
    action = _selected_action(follow_up, packet['action']['action_id'])
    if not e._same(packet['action'], action):
        raise ValueError('Supporting material changes its original action or finding')
    c.content_ref(packet['retention_method_ref'])  # Recorded method identity, not trusted authority.
    if packet['note'] is not None:
        c.text(packet['note'], maximum=1024)
    rows = packet['attachments']
    if type(rows) is not list or not 1 <= len(rows) <= MAX_ATTACHMENTS:
        raise ValueError('Require a bounded nonempty supporting-file selection')
    seen = set(); total = 0
    for i, row in enumerate(rows):
        c.keys(row, {'path', 'label', 'bytes', 'sha256'})
        # A retained document name never selects a path to read or execute.
        if row['path'] != f'artifacts/{i:04d}.bin':
            raise ValueError('Supporting artifact must use its exact fixed local slot')
        c.text(row['label'], maximum=256)
        if '/' in row['label'] or '\\' in row['label']:
            raise ValueError('Artifact label cannot be a path')
        c.integer(row['bytes'], minimum=1, maximum=MAX_ARTIFACT_BYTES)
        c.content_ref(row['sha256'])
        if row['sha256'] in seen:
            raise ValueError('Duplicate supporting bytes are not separate evidence items')
        seen.add(row['sha256']); total += row['bytes']
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Supporting artifacts exceed their total-byte budget')


def _read_packet(directory: Path | str, expected_packet_ref: str, follow_up: dict) -> dict:
    """Check bounded exact inventory; never follow paths supplied by attachments."""
    c.content_ref(expected_packet_ref)
    root = safe_path(directory)
    raw = read_bytes(safe_path(root/'packet.json'), limit=MAX_PACKET_BYTES)
    if c.bytes_ref(raw) != expected_packet_ref:
        raise ValueError('Evidence packet differs from the externally selected bytes')
    packet = c.loads(raw, MAX_PACKET_BYTES)
    _validate_packet(packet, follow_up)
    # Do not recursively walk or parse arbitrary directories/files from a document.
    children = set()
    for count, p in enumerate(root.iterdir()):
        if count >= 3:
            raise ValueError('Additional evidence packet artifacts')
        safe_path(p); children.add(p.name)
    if children != {'packet.json', 'artifacts'}:
        raise ValueError('Evidence packet has omitted or additional artifacts')
    folder = safe_path(root/'artifacts')
    if not folder.is_dir():
        raise ValueError('Evidence artifact slots are not a directory')
    names = set()
    for count, p in enumerate(folder.iterdir()):
        if count >= MAX_ATTACHMENTS:
            raise ValueError('Too many supporting files')
        safe_path(p)
        if not p.is_file():
            raise ValueError('Only original regular files belong in evidence slots')
        names.add(p.name)
    expected_names = {f'{i:04d}.bin' for i in range(len(packet['attachments']))}
    if names != expected_names:
        raise ValueError('Evidence packet has omitted or additional supporting files')
    for row in packet['attachments']:
        data = read_bytes(safe_path(root/row['path']), limit=row['bytes'])
        if len(data) != row['bytes'] or c.bytes_ref(data) != row['sha256']:
            raise ValueError('Supporting artifact differs from its selected original bytes')
    return packet


def _review(packet: dict, packet_ref: str, follow_up: dict, status: str) -> dict:
    action = packet['action']
    return {
        'schema': 'ciw.engineering-support-review.v1', 'status': status,
        'packet_ref': packet_ref, 'basis': deepcopy(packet['basis']),
        'action_id': action['action_id'], 'requirement_id': action['requirement_id'],
        'finding_ref': action['finding_ref'], 'finding_status': action['finding_status'],
        'assessment_status': follow_up['assessment_status'],
        'sampled_points_to_review': deepcopy(action['sampled_points_to_review']),
        'attachment_count': len(packet['attachments']),
        'attachment_bytes': sum(x['bytes'] for x in packet['attachments']),
        'attachments': deepcopy(packet['attachments']),
        'evidence_admitted': False, 'requirement_satisfied': False,
        'new_scientific_executions': 0, 'authorizes_execution': False,
        'active_authority_restored': False, 'content_authenticity': 'NOT_ESTABLISHED',
        'reviewer_authority': 'NOT_ESTABLISHED', 'scope': deepcopy(e.SCOPE),
    }


def prepare_evidence(directory: Path | str, output: Path | str, *, expected_thread_ref: str,
                     host_profile: str, action_id: str,
                     attachments: list[tuple[Path | str, str]], note: str | None = None) -> dict:
    """Create a new local packet for one unmet action; preserve original bytes.

    Source records are read through the existing frozen-thread inspector. Input
    files are host-selected and independently hash-bound, not paths discovered
    inside a report. No file parsing, source edits or evidence admission occurs.
    A partial write is retained without a completion manifest or success claim.
    """
    source, root = safe_path(directory), safe_path(output)
    if root.resolve().is_relative_to(source.resolve()):
        raise ValueError('Supporting evidence output must remain outside the original thread')
    if type(attachments) is not list or not 1 <= len(attachments) <= MAX_ATTACHMENTS:
        raise ValueError('Require 1 to 16 explicitly selected supporting files')
    if note is not None:
        c.text(note, maximum=1024)
    follow_up = thread.next_actions(source, expected_thread_ref=expected_thread_ref, host_profile=host_profile)
    action = _selected_action(follow_up, action_id)
    frozen = {}; rows = []; seen = set(); total = 0
    for i, pair in enumerate(attachments):
        if type(pair) not in (list, tuple) or len(pair) != 2:
            raise ValueError('Each supporting file requires an explicit path and SHA-256 reference')
        path, expected = pair
        c.content_ref(expected); path = safe_path(path)
        c.text(path.name, maximum=256)
        remaining = MAX_TOTAL_BYTES - total
        if remaining <= 0:
            raise ValueError('Supporting artifacts exceed their total-byte budget')
        data = read_bytes(path, limit=min(MAX_ARTIFACT_BYTES, remaining))
        if c.bytes_ref(data) != expected:
            raise ValueError('Supporting artifact differs from externally selected bytes')
        if expected in seen:
            raise ValueError('Duplicate supporting bytes are not separate evidence items')
        seen.add(expected); total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Supporting artifacts exceed their total-byte budget')
        slot = f'artifacts/{i:04d}.bin'
        frozen[slot] = data
        rows.append({'path': slot, 'label': path.name, 'bytes': len(data), 'sha256': expected})
    packet = seal({**_POLICY, 'basis': deepcopy(follow_up['basis']), 'action': deepcopy(action),
        'attachments': rows, 'note': note, 'scope': deepcopy(e.SCOPE),
        'retention_method_ref': c.bytes_ref(Path(__file__).read_bytes().replace(b'\r\n', b'\n'))})
    _validate_packet(packet, follow_up)
    raw = _json_raw(packet)
    if len(raw) > MAX_PACKET_BYTES:
        raise ValueError('Supporting packet exceeds its serialized metadata bound')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root/'artifacts').mkdir(mode=0o700)
    for slot, data in frozen.items():
        _write_raw(root/slot, data)
    _write_raw(root/'packet.json', raw)  # Last: not a power-loss-atomic directory transaction.
    ref = c.bytes_ref(raw)
    _read_packet(root, ref, follow_up)  # Reuse the same validated source snapshot.
    return _review(packet, ref, follow_up, 'RETAINED_NOT_ADMITTED')


def inspect_evidence(directory: Path | str, *, expected_packet_ref: str,
                     thread_directory: Path | str, expected_thread_ref: str,
                     host_profile: str) -> dict:
    """Check custody/bindings against original history; never reopen input reports."""
    follow_up = thread.next_actions(thread_directory, expected_thread_ref=expected_thread_ref,
                                    host_profile=host_profile)
    packet = _read_packet(directory, expected_packet_ref, follow_up)
    return _review(packet, expected_packet_ref, follow_up, 'INTEGRITY_CHECKED_NOT_ADMITTED')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='ncp production engineering evidence', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'inspect'):
        p = commands.add_parser(name)
        p.add_argument('directory', type=Path)
        p.add_argument('--thread-ref', required=True)
        p.add_argument('--profile', choices=e.PROFILES, required=True)
        if name == 'prepare':
            p.add_argument('--action-id', required=True)
            p.add_argument('--attach', nargs=2, action='append', required=True, metavar=('PATH', 'SHA256_REF'))
            p.add_argument('--note', help='Unreviewed context, not a qualification or reviewer assertion')
            p.add_argument('--output-dir', type=Path, required=True)
        else:
            p.add_argument('--thread', type=Path, required=True)
            p.add_argument('--packet-ref', required=True)
    a = parser.parse_args(argv)
    try:
        if a.command == 'prepare':
            result = prepare_evidence(a.directory, a.output_dir, expected_thread_ref=a.thread_ref,
                host_profile=a.profile, action_id=a.action_id, attachments=a.attach, note=a.note)
        else:
            result = inspect_evidence(a.directory, expected_packet_ref=a.packet_ref,
                thread_directory=a.thread, expected_thread_ref=a.thread_ref, host_profile=a.profile)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0  # Completed local retention/review, NEVER successful qualification.
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, OverflowError, RecursionError) as exc:
        print(json.dumps({'status': 'REFUSED', 'reason': str(exc), 'scope': e.SCOPE}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
