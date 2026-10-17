"""Retain source-cited review declarations without adjudicating physical evidence.

An exact byte citation proves where the declared support points, not that the
citation supports the reasoning. No identity, competence or authorization of a
reviewer is established here. Original findings and gates are never changed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from . import engineering_evidence as evidence, engineering_thread as e, link_contract as c
from .engineering_thread_io import _json_raw, _write_raw
from .hardware_workflow import read_bytes
from .link_store import safe_path
from .operations.runner import seal, check_seal

# Internal review questions, not a normative standard or sufficiency checklist.
DIMENSIONS = ('authenticity', 'applicability', 'method_conditions', 'timing', 'uncertainty', 'independence')
STATES = ('SUPPORTED', 'CONTRADICTED', 'NOT_ASSESSED')
INPUT_SCHEMA = 'ciw.engineering-evidence-assessment-input.v1'
MAX_INPUT = 65536
MAX_ASSESSMENT = 131072
MAX_CITATIONS = 4
MAX_SPAN = 65536


def _basis(*, packet_directory, expected_packet_ref, thread_directory, expected_thread_ref, host_profile):
    follow_up = evidence.thread.next_actions(thread_directory, expected_thread_ref=expected_thread_ref,
                                             host_profile=host_profile)
    packet = evidence._read_packet(packet_directory, expected_packet_ref, follow_up)
    return packet, follow_up


def template_assessment(**context):
    """Read original evidence and start every review question unassessed."""
    packet, _ = _basis(**context)
    return {'schema': INPUT_SCHEMA, 'packet_ref': context['expected_packet_ref'],
        'action_id': packet['action']['action_id'],
        'checks': {key: {'status': 'NOT_ASSESSED', 'rationale': 'Not assessed.', 'citations': []}
                   for key in DIMENSIONS}}


def _validate_input(raw, packet, context):
    """Recheck cited artifact bytes and evaluate only structural review rules.

    Packet inspection already checks full custody. Each cited file is reread
    under its bound hash before use, then cached for this call so repeated
    citations cannot see different file versions. No text extraction is used.
    """
    value = c.loads(raw, MAX_INPUT)
    c.keys(value, {'schema', 'packet_ref', 'action_id', 'checks'})
    if (value['schema'] != INPUT_SCHEMA or value['packet_ref'] != context['expected_packet_ref']
            or value['action_id'] != packet['action']['action_id']):
        raise ValueError('Assessment input must bind this exact evidence packet and action')
    checks = value['checks']
    c.keys(checks, set(DIMENSIONS))
    sources = {row['sha256']: row for row in packet['attachments']}
    frozen = {}
    for dimension in DIMENSIONS:
        item = checks[dimension]
        c.keys(item, {'status', 'rationale', 'citations'})
        if type(item['status']) is not str or item['status'] not in STATES:
            raise ValueError('Review status must be SUPPORTED, CONTRADICTED or NOT_ASSESSED')
        c.text(item['rationale'], maximum=1024)
        citations = item['citations']
        if type(citations) is not list or len(citations) > MAX_CITATIONS:
            raise ValueError('A review question supports at most four source citations')
        if item['status'] != 'NOT_ASSESSED' and not citations:
            raise ValueError('An assessed declaration requires an original-source citation')
        seen = set()
        for citation in citations:
            c.keys(citation, {'artifact_ref', 'start_byte', 'end_byte', 'span_ref'})
            ref = c.content_ref(citation['artifact_ref'])
            c.content_ref(citation['span_ref'])
            if ref not in sources:
                raise ValueError('Citation names bytes outside the selected evidence packet')
            row = sources[ref]
            start, end = citation['start_byte'], citation['end_byte']
            c.integer(start, maximum=row['bytes']-1)
            c.integer(end, minimum=start+1, maximum=min(row['bytes'], start+MAX_SPAN))
            key = (ref, start, end)
            if key in seen:
                raise ValueError('Duplicate citation within a review question')
            seen.add(key)
            if ref not in frozen:
                data = read_bytes(safe_path(Path(context['packet_directory'])/row['path']), limit=row['bytes'])
                if len(data) != row['bytes'] or c.bytes_ref(data) != ref:
                    raise ValueError('Supporting artifact changed before citation review')
                frozen[ref] = data
            if c.bytes_ref(frozen[ref][start:end]) != citation['span_ref']:
                raise ValueError('Citation span does not match the selected source bytes')
    return value, len(frozen)


def _compile(raw, packet, follow_up, context, method_ref):
    value, cited = _validate_input(raw, packet, context)
    c.content_ref(method_ref)
    counts = {state.lower(): sum(x['status'] == state for x in value['checks'].values()) for state in STATES}
    status = ('CONTRADICTIONS_DECLARED' if counts['contradicted'] else
              'REVIEW_INCOMPLETE' if counts['not_assessed'] else 'DECLARATIONS_COMPLETE_NOT_ADMITTED')
    action = packet['action']
    return seal({'schema': 'ciw.engineering-evidence-assessment.v1', 'status': status,
        'input_ref': c.bytes_ref(raw), 'packet_ref': context['expected_packet_ref'],
        'basis': deepcopy(packet['basis']), 'action_id': action['action_id'],
        'requirement_id': action['requirement_id'], 'finding_ref': action['finding_ref'],
        'finding_status': action['finding_status'], 'assessment_status': follow_up['assessment_status'],
        'checks': deepcopy(value['checks']), 'counts': counts, 'cited_artifact_count': cited,
        'unresolved_dimensions': [d for d in DIMENSIONS if value['checks'][d]['status'] == 'NOT_ASSESSED'],
        'contradicted_dimensions': [d for d in DIMENSIONS if value['checks'][d]['status'] == 'CONTRADICTED'],
        'assessment_method_ref': method_ref, 'semantic_support_checked': False,
        'reviewer_identity': 'NOT_AUTHENTICATED', 'reviewer_authority': 'NOT_ESTABLISHED',
        'evidence_admitted': False, 'requirement_satisfied': False,
        'authorizes_execution': False, 'new_scientific_executions': 0,
        'active_authority_restored': False, 'scope': deepcopy(e.SCOPE)})


def _summary(value, ref):
    return {**deepcopy(value), 'assessment_ref': ref}


def record_assessment(input_raw, output, *, expected_input_ref, **context):
    """Create an auditable review record; no acceptance/authority mutation."""
    c.content_ref(expected_input_ref)
    if type(input_raw) is not bytes or c.bytes_ref(input_raw) != expected_input_ref:
        raise ValueError('Assessment input differs from its external reference')
    # Bound parsing before any source lookup or directory creation.
    c.loads(input_raw, MAX_INPUT)
    root = safe_path(output)
    for source in (context['packet_directory'], context['thread_directory']):
        if root.resolve().is_relative_to(safe_path(source).resolve()):
            raise ValueError('Assessment output must be outside original evidence and thread')
    packet, follow_up = _basis(**context)
    method = c.bytes_ref(Path(__file__).read_bytes().replace(b'\r\n', b'\n'))
    result = _compile(input_raw, packet, follow_up, context, method)
    output_raw = _json_raw(result)
    c.bounded(result, MAX_ASSESSMENT)
    if len(output_raw) > MAX_ASSESSMENT:
        raise ValueError('Assessment record exceeds its byte budget')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    _write_raw(root/'assessment-input.json', input_raw)
    _write_raw(root/'assessment.json', output_raw)  # Completion record last.
    if (read_bytes(root/'assessment-input.json', limit=MAX_INPUT) != input_raw
            or read_bytes(root/'assessment.json', limit=MAX_ASSESSMENT) != output_raw):
        raise ValueError('Assessment retention readback differs')
    return _summary(result, c.bytes_ref(output_raw))


def inspect_assessment(directory, *, expected_assessment_ref, **context):
    """Recompute the review rules from retained input and original cited bytes."""
    c.content_ref(expected_assessment_ref)
    root = safe_path(directory)
    names = set()
    for i, path in enumerate(root.iterdir()):
        if i >= 2:
            raise ValueError('Additional assessment artifacts')
        safe_path(path); names.add(path.name)
    if names != {'assessment-input.json', 'assessment.json'}:
        raise ValueError('Incomplete assessment inventory')
    output_raw = read_bytes(safe_path(root/'assessment.json'), limit=MAX_ASSESSMENT)
    if c.bytes_ref(output_raw) != expected_assessment_ref:
        raise ValueError('Assessment differs from the externally selected bytes')
    value = c.loads(output_raw, MAX_ASSESSMENT)
    check_seal(value)
    input_raw = read_bytes(safe_path(root/'assessment-input.json'), limit=MAX_INPUT)
    packet, follow_up = _basis(**context)
    # Historical implementation reference remains recorded, not self-authenticating.
    expected = _compile(input_raw, packet, follow_up, context, value['assessment_method_ref'])
    if not e._same(value, expected):
        raise ValueError('Assessment summary differs from original input, findings or source citations')
    return _summary(value, expected_assessment_ref)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ncp production engineering evidence review', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('template', 'record', 'inspect'):
        p = sub.add_parser(name)
        p.add_argument('packet_directory', type=Path)
        p.add_argument('--packet-ref', required=True)
        p.add_argument('--thread', type=Path, required=True)
        p.add_argument('--thread-ref', required=True)
        p.add_argument('--profile', choices=e.PROFILES, required=True)
        if name == 'record':
            p.add_argument('--input', type=Path, required=True)
            p.add_argument('--input-ref', required=True)
            p.add_argument('--output-dir', type=Path, required=True)
        elif name == 'inspect':
            p.add_argument('--assessment', type=Path, required=True)
            p.add_argument('--assessment-ref', required=True)
    args = parser.parse_args(argv)
    context = dict(packet_directory=args.packet_directory, expected_packet_ref=args.packet_ref,
        thread_directory=args.thread, expected_thread_ref=args.thread_ref, host_profile=args.profile)
    try:
        if args.command == 'template':
            result = template_assessment(**context)
        elif args.command == 'record':
            data = read_bytes(safe_path(args.input), limit=MAX_INPUT)
            result = record_assessment(data, args.output_dir, expected_input_ref=args.input_ref, **context)
        else:
            result = inspect_assessment(args.assessment, expected_assessment_ref=args.assessment_ref, **context)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0  # Completed local review handling, not empirical admission.
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, OverflowError, RecursionError) as exc:
        print(json.dumps({'status':'REFUSED', 'reason':str(exc), 'scope':e.SCOPE}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
