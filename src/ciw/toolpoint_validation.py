"""Frozen-plan, manifest-complete retrospective QC detector evaluation.

A precautionary hold is not counted as a successfully detected physical defect.
This is a read-only study, not limit fitting, process control, release, or an
independent physical measurement. Inspection labels are supplied declarations.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext

from .core.identities import new_identity, validate_identity
from .manufacturing_contracts import bounded, content_identity, count, items, keys, number, ref, text
from .toolpoint_binomial import confidence_bounds
from .toolpoint_stream import AUTHORITY, CaptureJournal, LEGACY, NATIVE

REQUEST_SCHEMA = 'ciw.toolpoint-validation-request.v1'
OPERATION = 'polymer.toolpoint-detector-validation.v1'
MAX_CASES = 256
MAX_STREAMS = 128
KNOWN_LABELS = {'DEFECT_PRESENT', 'DEFECT_ABSENT'}
ALARM_KINDS = {'SAMPLED_LIMIT_VIOLATION', 'EVALUATOR_DEVIATION'}


def _d(value) -> Decimal:
    return Decimal(str(value))


def _names(value, maximum=256):
    entries = items(value, maximum=maximum)
    for entry in entries:
        text(entry)
    if len(entries) != len(set(entries)):
        raise ValueError('Duplicate declared identity')
    return entries


def _prob(value):
    number(value)
    if value > 1:
        raise ValueError('Probability exceeds one')


def _plan(plan):
    schema = plan.get('schema') if type(plan) is dict else None
    if schema == NATIVE:
        from .polymer_toolpoint_qc import validate_plan
        validate_plan(plan)
        cavities = plan['cavity_ids']
        end = max(p['end_s'] for p in plan['phases'])
    elif schema == LEGACY:
        from .toolpoint_quality import validate_plan
        validate_plan(plan)
        cavities = sorted({r['cavity_id'] for r in plan['rules'] if r['cavity_id'] is not None})
        end = max(r['end_s'] for r in plan['rules'])
    else:
        raise ValueError('Unsupported fixed QC profile')
    return cavities, end


def _validate_protocol(protocol):
    keys(protocol, {'schema', 'study_id', 'revision', 'source_kind', 'monitoring_plan',
        'characteristic_id', 'inspection_method_ref', 'inspection_condition', 'frozen_at_s',
        'development_group_ids', 'development_cycle_refs', 'target_rule_ids',
        'sampling_design', 'confidence', 'minimum_defect_groups', 'minimum_nondefect_groups',
        'minimum_timely_detection', 'maximum_false_hold', 'decision_deadline_s',
        'maximum_receipt_uncertainty_s'})
    if protocol['schema'] != 'ciw.toolpoint-validation-protocol.v1':
        raise ValueError('Wrong validation protocol schema')
    for k in ('study_id', 'revision', 'characteristic_id', 'inspection_condition'):
        text(protocol[k])
    if protocol['source_kind'] not in {'synthetic', 'retained_observation'}:
        raise ValueError('Declare validation source semantics')
    ref(protocol['inspection_method_ref'])
    plan = protocol['monitoring_plan']
    cavities, end = _plan(plan)
    if plan['schema'] == NATIVE and protocol['source_kind'] != plan['source_kind']:
        raise ValueError('Protocol and plan evidence semantics differ')
    for k in ('frozen_at_s', 'decision_deadline_s', 'maximum_receipt_uncertainty_s'):
        number(protocol[k])
    if protocol['decision_deadline_s'] <= 0:
        raise ValueError('Require a positive declared decision deadline')
    _names(protocol['development_group_ids'])
    _names(protocol['development_cycle_refs'])
    for r in protocol['development_cycle_refs']:
        ref(r)
    targets = _names(protocol['target_rule_ids'], 64)
    if not targets or not set(targets) <= {r['rule_id'] for r in plan['rules']}:
        raise ValueError('Target rules must resolve in the frozen plan')
    design = protocol['sampling_design']
    keys(design, {'mode', 'evidence_ref'})
    if design['mode'] not in {'descriptive_only', 'independent_exchangeable_groups'}:
        raise ValueError('Unknown sampling design declaration')
    if design['evidence_ref'] is not None:
        ref(design['evidence_ref'])
    for k in ('minimum_defect_groups', 'minimum_nondefect_groups'):
        count(protocol[k], maximum=MAX_CASES)
    for k in ('confidence', 'minimum_timely_detection', 'maximum_false_hold'):
        _prob(protocol[k])
    if not 0.5 <= protocol['confidence'] <= 0.999998:
        raise ValueError('Confidence outside the bounded profile')
    return cavities, end


def _validate_manifest(manifest, protocol, cavities):
    keys(manifest, {'schema', 'manifest_id', 'protocol_ref', 'sampling_frame_ref', 'samples'})
    if manifest['schema'] != 'ciw.toolpoint-validation-manifest.v1':
        raise ValueError('Wrong validation manifest schema')
    text(manifest['manifest_id'])
    if ref(manifest['protocol_ref']) != content_identity(protocol):
        raise ValueError('Manifest must bind the exact frozen protocol')
    ref(manifest['sampling_frame_ref'])
    samples, cycles, serials, positions = {}, {}, set(), set()
    for sample in items(manifest['samples'], minimum=1, maximum=MAX_CASES):
        keys(sample, {'sample_id', 'group_id', 'cycle_id', 'cycle_ref', 'cavity_id', 'serial',
                      'started_at_s', 'stream_spec_ref', 'stream_head_ref', 'mapping_evidence_ref'})
        for k in ('sample_id', 'group_id', 'cycle_id', 'cavity_id', 'serial'):
            text(sample[k])
        sid, cid = sample['sample_id'], sample['cycle_id']
        if sid in samples or sample['serial'] in serials or (cid, sample['cavity_id']) in positions:
            raise ValueError('Duplicate sample, serial, or cycle/cavity observation')
        if sample['cavity_id'] not in cavities:
            raise ValueError('Undeclared cavity in validation inventory')
        for k in ('cycle_ref', 'stream_spec_ref', 'stream_head_ref'):
            ref(sample[k])
        if sample['mapping_evidence_ref'] is not None:
            ref(sample['mapping_evidence_ref'])
        number(sample['started_at_s'])
        if _d(sample['started_at_s']) < _d(protocol['frozen_at_s']):
            raise ValueError('Evaluation capture predates the declared plan freeze')
        if (sample['group_id'] in protocol['development_group_ids'] or
                sample['cycle_ref'] in protocol['development_cycle_refs']):
            raise ValueError('Development/evaluation leakage in a group or cycle reference')
        cycle_fields = {k: sample[k] for k in ('group_id', 'cycle_ref', 'started_at_s',
                                              'stream_spec_ref', 'stream_head_ref', 'mapping_evidence_ref')}
        if cid in cycles and content_identity(cycles[cid]) != content_identity(cycle_fields):
            raise ValueError('A cycle cannot cross groups, starts, mapping or evidence anchors')
        if cid not in cycles and any(c['cycle_ref'] == sample['cycle_ref'] for c in cycles.values()):
            raise ValueError('Cycle evidence cannot be renamed into another trial')
        cycles[cid] = cycle_fields
        samples[sid] = sample
        serials.add(sample['serial'])
        positions.add((cid, sample['cavity_id']))
    # Every cavity in every enrolled cycle must remain visible, even with missing data.
    for cid in cycles:
        if {s['cavity_id'] for s in samples.values() if s['cycle_id'] == cid} != set(cavities):
            raise ValueError('Manifest must enroll every configured cavity for each cycle')
    return samples, cycles


def _load_streams(request, protocol, samples, cycles):
    streams = {}
    for envelope in items(request['streams'], maximum=MAX_STREAMS):
        keys(envelope, {'report', 'receipt_time_uncertainty_s'})
        report = envelope['report']
        # Full replay also verifies types/hashes of the report and every accepted prefix.
        journal = CaptureJournal.restore(report)
        replayed = journal.report()
        spec, result = replayed['spec'], replayed['result']
        cid = spec['binding']['cycle_id']
        if cid not in cycles or cid in streams:
            raise ValueError('Unknown or duplicate retained cycle report')
        cycle = cycles[cid]
        if (replayed['spec_ref'] != cycle['stream_spec_ref'] or
                replayed['receipts'][-1]['receipt_ref'] != cycle['stream_head_ref']):
            raise ValueError('Stream differs from the enrolled specification/head anchor')
        if content_identity(spec['plan']) != content_identity(protocol['monitoring_plan']):
            raise ValueError('Changed thresholds, references or plan revision in evaluation')
        binding = spec['binding']
        if (binding['cycle_ref'] != cycle['cycle_ref'] or binding['source_kind'] != protocol['source_kind'] or
                content_identity(binding['started_at_s']) != content_identity(cycle['started_at_s'])):
            raise ValueError('Capture does not bind the enrolled cycle/source/start')
        if report['updates'][-1]['received_at_s'] > request['assessed_at_s']:
            raise ValueError('Future capture receipt in a retrospective study')
        mapping_ok = cycle['mapping_evidence_ref'] is not None
        if spec['plan']['schema'] == NATIVE:
            mapping = report['updates'][-1]['observation']['part_mapping']
            expected = sorted([{'serial': s['serial'], 'cavity_id': s['cavity_id']}
                for s in samples.values() if s['cycle_id'] == cid], key=lambda r:r['serial'])
            if any(row not in expected for row in mapping['items']):
                raise ValueError('Cavity/serial mapping contradicts the enrolled inventory')
            if mapping['status'] == 'cavity_resolved':
                if sorted(mapping['items'], key=lambda r:r['serial']) != expected:
                    raise ValueError('Resolved mapping omits enrolled outputs')
                if mapping['evidence_ref'] != cycle['mapping_evidence_ref']:
                    raise ValueError('Mapping evidence differs from the enrolled binding')
            else:
                mapping_ok = False
        uncertainty = envelope['receipt_time_uncertainty_s']
        if uncertainty is not None:
            number(uncertainty)
        streams[cid] = {'report': report, 'result': result, 'mapping_ok': mapping_ok,
                        'receipt_uncertainty': uncertainty}
    return streams


def _load_outcomes(request, protocol, samples, streams):
    outcomes = {}
    for outcome in items(request['outcomes'], maximum=MAX_CASES):
        keys(outcome, {'schema', 'sample_id', 'sample_ref', 'characteristic_id', 'source_kind',
            'method_ref', 'condition', 'label', 'independence', 'evidence_ref', 'calibration_ref',
            'assessor_id', 'measured_at_s'})
        if outcome['schema'] != 'ciw.toolpoint-independent-outcome.v1':
            raise ValueError('Wrong independent-outcome schema')
        sid = text(outcome['sample_id'])
        if sid not in samples or sid in outcomes:
            raise ValueError('Duplicate or unenrolled inspection outcome')
        if ref(outcome['sample_ref']) != content_identity(samples[sid]):
            raise ValueError('Outcome labels a different sample/cycle/cavity/serial')
        for k, expected in (('characteristic_id', protocol['characteristic_id']),
                            ('source_kind', protocol['source_kind']),
                            ('method_ref', protocol['inspection_method_ref']),
                            ('condition', protocol['inspection_condition'])):
            if outcome[k] != expected:
                raise ValueError('Incompatible independent-inspection meaning: '+k)
        if outcome['label'] not in KNOWN_LABELS | {'INDETERMINATE'}:
            raise ValueError('Unknown independent inspection label')
        if outcome['independence'] not in {'independent_measurement', 'derived_from_detector', 'not_established'}:
            raise ValueError('Unknown label-independence declaration')
        for k in ('evidence_ref', 'calibration_ref'):
            if outcome[k] is not None:
                ref(outcome[k])
        text(outcome['assessor_id'])
        number(outcome['measured_at_s'])
        lower = _d(samples[sid]['started_at_s'])
        stream = streams.get(samples[sid]['cycle_id'])
        if stream:
            lower += _d(stream['result']['captured_through_s_decimal'])
        if not lower <= _d(outcome['measured_at_s']) <= _d(request['assessed_at_s']):
            raise ValueError('Inspection timestamp predates capture or lies in the future')
        circular_refs = set()
        if stream:
            report = stream['report']
            circular_refs.update((content_identity(report), report['result_ref'], report['spec_ref']))
            for receipt in report['receipts']:
                circular_refs.update(receipt[k] for k in ('receipt_ref', 'update_ref', 'assessment_ref', 'result_ref'))
            for update in report['updates']:
                circular_refs.add(content_identity(update['observation']))
                for channel in update['observation'].get('signals', update['observation'].get('streams', [])):
                    circular_refs.add(channel.get('source_ref'))
                    circular_refs.add(channel.get('samples_ref'))
        if outcome['label'] not in KNOWN_LABELS:
            reason = 'INDETERMINATE_LABEL'
        elif outcome['independence'] != 'independent_measurement':
            reason = 'INDEPENDENCE_NOT_DECLARED'
        elif outcome['evidence_ref'] is None or outcome['calibration_ref'] is None:
            reason = 'MISSING_OUTCOME_EVIDENCE'
        elif outcome['evidence_ref'] in circular_refs:
            reason = 'DETECTOR_ARTIFACT_USED_AS_GROUND_TRUTH'
        else:
            reason = 'DECLARED_INDEPENDENT_OUTCOME'
        outcomes[sid] = (outcome, outcome['label'] if reason == 'DECLARED_INDEPENDENT_OUTCOME' else 'UNRESOLVED', reason)
    return outcomes


def _case(sample, stream, outcome, protocol):
    label_record, label, label_reason = outcome if outcome is not None else (None, 'UNRESOLVED', 'MISSING_OUTCOME')
    row = {'sample_id': sample['sample_id'], 'group_id': sample['group_id'],
           'cycle_id': sample['cycle_id'], 'cavity_id': sample['cavity_id'], 'serial': sample['serial'],
           'sample_ref': content_identity(sample), 'label': label, 'label_reason': label_reason,
           'outcome_ref': None if label_record is None else content_identity(label_record),
           'prediction': 'ABSTAIN', 'timely_alarm': False, 'timing_status': 'NOT_DETECTED',
           'first_alarm_receipt_relative_interval': None, 'alarm_finding_refs': [],
           'stream_hold': True, 'capture_status': 'MISSING', 'reason': 'MISSING_CAPTURE'}
    if stream is None:
        return row
    result = stream['result']
    row['capture_status'] = 'FINAL' if result['finalized'] else 'OPEN'
    if not stream['mapping_ok']:
        row['reason'] = 'UNRESOLVED_CAVITY_MAPPING'
        return row
    cavity = sample['cavity_id']
    findings = [f for f in result['latched_findings'] if f['kind'] in ALARM_KINDS
                and f['rule_id'] in protocol['target_rule_ids'] and f['cavity_id'] in (None, cavity)]
    row['stream_hold'] = (not result['finalized'] or result['cavities'][cavity]['state'] != 'NO_PROCESS_HOLD_IDENTIFIED')
    row['alarm_finding_refs'] = [f['finding_ref'] for f in findings]
    if findings:
        row.update(prediction='ALARM', reason='RETAINED_TARGET_RULE_DEVIATION')
        first = min(_d(f['first_received_at_s']) for f in findings) - _d(sample['started_at_s'])
        u = stream['receipt_uncertainty']
        if u is None or _d(u) > _d(protocol['maximum_receipt_uncertainty_s']):
            row['timing_status'] = 'UNKNOWN'
        else:
            lo, hi = first-_d(u), first+_d(u)
            deadline = _d(protocol['decision_deadline_s'])
            row['first_alarm_receipt_relative_interval'] = {'lower_decimal': str(lo), 'upper_decimal': str(hi), 'unit': 's'}
            row['timing_status'] = 'TIMELY' if hi <= deadline else 'LATE' if lo > deadline else 'AMBIGUOUS'
            row['timely_alarm'] = hi <= deadline
    elif not row['stream_hold']:
        row.update(prediction='NO_ALARM', reason='FINAL_COVERED_NO_PROCESS_HOLD')
    else:
        row['reason'] = 'PRECAUTIONARY_HOLD_NOT_DEFECT_DETECTION'
    return row


def _rate(k, n):
    return {'numerator': k, 'denominator': n, 'value': k/n if n else None}


def _metrics(rows):
    positives = [r for r in rows if r['label'] == 'DEFECT_PRESENT']
    negatives = [r for r in rows if r['label'] == 'DEFECT_ABSENT']
    unknown = [r for r in rows if r['label'] == 'UNRESOLVED']
    def counts(selected):
        return {p: sum(r['prediction'] == p for r in selected) for p in ('ALARM', 'NO_ALARM', 'ABSTAIN')}
    p, n = counts(positives), counts(negatives)
    timely = sum(r['timely_alarm'] for r in positives)
    false_holds = sum(r['stream_hold'] for r in negatives)
    escapes = sum(not r['stream_hold'] for r in positives)
    return {'enrolled': len(rows), 'defect_present': p, 'defect_absent': n,
            'unresolved_labels': len(unknown), 'missing_captures': sum(r['capture_status'] == 'MISSING' for r in rows),
            'open_captures': sum(r['capture_status'] == 'OPEN' for r in rows),
            'alarm_sensitivity': _rate(p['ALARM'], len(positives)),
            'timely_alarm_sensitivity': _rate(timely, len(positives)),
            'nondefect_alarm_rate': _rate(n['ALARM'], len(negatives)),
            'nondefect_hold_rate': _rate(false_holds, len(negatives)),
            'defect_without_stream_hold_rate': _rate(escapes, len(positives)),
            'scope': 'descriptive enrolled part counts; abstentions remain in denominators; target characteristic only; stream holds are not physical containment'}


def assess(request, *, expected_protocol_ref=None, expected_manifest_ref=None):
    """Replay enrolled streams and compare with separately declared inspection labels."""
    bounded(request)
    keys(request, {'schema', 'protocol', 'manifest', 'streams', 'outcomes', 'assessed_at_s'})
    if request['schema'] != REQUEST_SCHEMA:
        raise ValueError('Wrong tool-point validation request schema')
    number(request['assessed_at_s'])
    protocol, manifest = request['protocol'], request['manifest']
    for anchor, record in ((expected_protocol_ref, protocol), (expected_manifest_ref, manifest)):
        if anchor is not None and ref(anchor) != content_identity(record):
            raise ValueError('Study differs from the independently retained anchor')
    cavities, _ = _validate_protocol(protocol)
    if request['assessed_at_s'] < protocol['frozen_at_s']:
        raise ValueError('Assessment predates the declared frozen protocol')
    samples, cycles = _validate_manifest(manifest, protocol, cavities)
    if any(s['started_at_s'] > request['assessed_at_s'] for s in samples.values()):
        raise ValueError('Enrolled cycle starts in the future')
    streams = _load_streams(request, protocol, samples, cycles)
    with localcontext() as context:
        context.prec = 512
        outcomes = _load_outcomes(request, protocol, samples, streams)
        rows = [_case(s, streams.get(s['cycle_id']), outcomes.get(sid), protocol)
                for sid, s in sorted(samples.items())]
        groups = []
        for gid in sorted({r['group_id'] for r in rows}):
            selected = [r for r in rows if r['group_id'] == gid]
            positive = [r for r in selected if r['label'] == 'DEFECT_PRESENT']
            all_known = all(r['label'] in KNOWN_LABELS for r in selected)
            groups.append({'group_id': gid, 'sample_ids': [r['sample_id'] for r in selected],
                'labels_complete': all_known, 'contains_known_defect': bool(positive),
                'all_defects_timely_detected': bool(positive) and all_known and all(r['timely_alarm'] for r in positive),
                'all_nondefective': all_known and not positive,
                'any_stream_hold': any(r['stream_hold'] for r in selected)})
        pg = [g for g in groups if g['contains_known_defect']]
        ng = [g for g in groups if g['all_nondefective']]
        # Two predeclared primary bounds; a Bonferroni split of the family tail.
        alpha = str((Decimal(1)-_d(protocol['confidence']))/2)
        inferential = (protocol['sampling_design']['mode'] == 'independent_exchangeable_groups'
                       and protocol['sampling_design']['evidence_ref'] is not None)
        detection = confidence_bounds(sum(g['all_defects_timely_detected'] for g in pg), len(pg), tail_probability=alpha) if inferential else None
        false_hold = confidence_bounds(sum(g['any_stream_hold'] for g in ng), len(ng), tail_probability=alpha) if inferential else None
        reasons = []
        if not inferential:
            reasons.append('SAMPLING_ASSUMPTIONS_NOT_DECLARED')
        if any(r['label'] == 'UNRESOLVED' for r in rows):
            reasons.append('UNRESOLVED_INDEPENDENT_OUTCOMES')
        if any(r['capture_status'] != 'FINAL' for r in rows):
            reasons.append('MISSING_OR_OPEN_CAPTURES')
        if len(pg) < protocol['minimum_defect_groups']:
            reasons.append('TOO_FEW_DEFECT_GROUPS')
        if len(ng) < protocol['minimum_nondefect_groups']:
            reasons.append('TOO_FEW_NONDEFECT_GROUPS')
        if detection and detection['lower_decimal'] is not None and _d(detection['lower_decimal']) < _d(protocol['minimum_timely_detection']):
            reasons.append('TIMELY_DETECTION_LOWER_BOUND_BELOW_TARGET')
        if false_hold and false_hold['upper_decimal'] is not None and _d(false_hold['upper_decimal']) > _d(protocol['maximum_false_hold']):
            reasons.append('FALSE_HOLD_UPPER_BOUND_ABOVE_TARGET')
        for c in cavities:
            labels = {r['label'] for r in rows if r['cavity_id'] == c}
            if not KNOWN_LABELS <= labels:
                reasons.append('CAVITY_CLASS_COVERAGE_INCOMPLETE:'+c)
    criterion = 'CRITERIA_NOT_MET' if reasons else 'CRITERIA_MET_FOR_REVIEW'
    result = {'schema': 'ciw.toolpoint-validation-result.v1', 'operation_ref': content_identity({'operation': OPERATION}),
        'protocol_ref': content_identity(protocol), 'manifest_ref': content_identity(manifest),
        'source_kind': protocol['source_kind'], 'characteristic_id': protocol['characteristic_id'],
        'criteria_status': criterion,
        'status': 'SYNTHETIC_ONLY' if protocol['source_kind'] == 'synthetic' else criterion,
        'reasons': reasons, 'cases': rows, 'groups': groups, 'part_metrics': _metrics(rows),
        'cavity_metrics': [{'cavity_id': c, 'metrics': _metrics([r for r in rows if r['cavity_id'] == c])} for c in cavities],
        'review_queue': [{'sample_id': r['sample_id'], 'serial': r['serial'], 'category':
            'UNRESOLVED_INSPECTION' if r['label'] == 'UNRESOLVED' else
            'DEFECT_WITHOUT_TIMELY_ALARM' if r['label'] == 'DEFECT_PRESENT' and not r['timely_alarm'] else
            'NONDEFECTIVE_TARGET_ON_HOLD',
            'prediction': r['prediction'], 'timing_status': r['timing_status'],
            'sample_ref': r['sample_ref'], 'outcome_ref': r['outcome_ref']}
            for r in rows if r['label'] == 'UNRESOLVED' or
                r['label'] == 'DEFECT_PRESENT' and not r['timely_alarm'] or
                r['label'] == 'DEFECT_ABSENT' and r['stream_hold']],
        'group_inference': {'unit': 'declared_independence_group_not_part',
            'defect_group_count': len(pg), 'nondefect_group_count': len(ng),
            'timely_detection': detection, 'nondefect_any_hold': false_hold,
            'primary_bound_count': 2, 'family_confidence': protocol['confidence'],
            'assumptions': 'conditional on independent exchangeable group trials, fixed sampling, correct labels and no undisclosed reuse'},
        'limitations': ['labels and source independence declared, not authenticated',
            'manifest completeness applies only to enrolled samples',
            'receipt timing is supplied clock arithmetic, not measured physical actuation latency',
            'no guarantee for unseen materials, tools, configurations, cavities or defect classes',
            'no calibration, threshold fitting, control action, hold clearance or qualification promotion'],
        'authority': dict(AUTHORITY)}
    bounded(result)
    return result


def evaluate(request, *, expected_protocol_ref=None, expected_manifest_ref=None):
    result = assess(request, expected_protocol_ref=expected_protocol_ref, expected_manifest_ref=expected_manifest_ref)
    report = {'schema': 'ciw.toolpoint-validation-report.v1', 'operation_ref': content_identity({'operation': OPERATION}),
              'execution_id': new_identity('execution'), 'request': deepcopy(request),
              'request_ref': content_identity(request), 'result': result, 'result_ref': content_identity(result)}
    bounded(report)
    return report


def verify(report, *, expected_protocol_ref=None, expected_manifest_ref=None):
    bounded(report)
    keys(report, {'schema', 'operation_ref', 'execution_id', 'request', 'request_ref', 'result', 'result_ref'})
    if report['schema'] != 'ciw.toolpoint-validation-report.v1' or report['operation_ref'] != content_identity({'operation': OPERATION}):
        raise ValueError('Wrong detector-validation report or operation')
    validate_identity(report['execution_id'], 'execution')
    if report['request_ref'] != content_identity(report['request']) or report['result_ref'] != content_identity(report['result']):
        raise ValueError('Detector-validation content identity mismatch')
    fresh = assess(report['request'], expected_protocol_ref=expected_protocol_ref, expected_manifest_ref=expected_manifest_ref)
    if content_identity(fresh) != report['result_ref']:
        raise ValueError('Detector-validation report differs from fresh replay')
    return {'schema': 'ciw.toolpoint-validation-verification.v1', 'status': 'PASS',
            'verification_id': new_identity('verification'), 'execution_id': report['execution_id'],
            'operation_ref': report['operation_ref'], 'request_ref': report['request_ref'],
            'result_ref': report['result_ref'], 'protocol_anchor_checked': expected_protocol_ref is not None,
            'manifest_anchor_checked': expected_manifest_ref is not None,
            'scope': 'full stream replay and same-implementation study recomputation; not label authenticity',
            'authority': dict(AUTHORITY)}
