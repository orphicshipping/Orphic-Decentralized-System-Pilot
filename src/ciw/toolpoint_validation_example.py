"""Authored synthetic detector-validation cohort; not measured molding outcomes."""
from copy import deepcopy
from decimal import Decimal

from .manufacturing_contracts import content_identity as identity
from .toolpoint_stream import CaptureJournal, make_spec
from .toolpoint_stream_example import fixture, prefix
from .toolpoint_validation import REQUEST_SCHEMA


def example_request(*, profile='native'):
    base_plan, _ = fixture(profile=profile, case='nominal')
    protocol = {'schema': 'ciw.toolpoint-validation-protocol.v1', 'study_id': 'synthetic.qc-study',
        'revision': 'A', 'source_kind': 'synthetic', 'monitoring_plan': base_plan,
        'characteristic_id': 'synthetic.critical-feature-incomplete-fill',
        'inspection_method_ref': identity({'synthetic_independent_inspection': 'optical feature check'}),
        'inspection_condition': 'conditioned', 'frozen_at_s': 0,
        'development_group_ids': ['synthetic.development-run'],
        'development_cycle_refs': [identity({'synthetic_development_cycle': 0})],
        'target_rule_ids': [r['rule_id'] for r in base_plan['rules']],
        'sampling_design': {'mode': 'independent_exchangeable_groups',
                            'evidence_ref': identity({'synthetic_sampling_design': 1})},
        'confidence': 0.95, 'minimum_defect_groups': 3, 'minimum_nondefect_groups': 3,
        'minimum_timely_detection': 0.9, 'maximum_false_hold': 0.1,
        'decision_deadline_s': 0.7, 'maximum_receipt_uncertainty_s': 0.02}
    samples, streams, outcomes = [], [], []
    scenarios = ['late-unknown', 'nominal', 'health-recovery', 'high-nozzle',
                 'nominal', 'late-unknown', 'nominal', 'nominal']
    for index, scenario in enumerate(scenarios):
        plan, observation = fixture(profile=profile, case=scenario)
        cid, start = 'synthetic.validation.cycle.'+str(index), 10+index*2
        cref = identity({'synthetic_cycle': index})
        observation['cycle_id'] = cid
        mapping_ref = identity({'synthetic_mapping': index})
        if profile == 'native':
            observation.update(started_at_s=start, completed_at_s=start+0.8, cycle_evidence_ref=cref)
            observation['part_mapping'] = {'status': 'cavity_resolved', 'evidence_ref': mapping_ref,
                'items': [{'serial': f's{index}.{i}', 'cavity_id': f'cavity.{i}'} for i in (1, 2)]}
        else:
            observation.update(cycle_start_s=start, cycle_event_ref=cref)
        journal = CaptureJournal(make_spec(plan, observation))
        for n, through in enumerate((0.2, 0.6, 0.8)):
            capture = prefix(plan, observation, through, final=n == 2)
            if scenario == 'health-recovery' and n == 0:
                capture['signals' if profile == 'native' else 'streams'][1]['health'] = 'fault'
            delay = Decimal('0.31') if index == 5 and n == 1 else Decimal('0.12') if index == 5 and n == 2 else Decimal('0.01')
            received = float(Decimal(start)+Decimal(str(through))+delay)
            journal.append(journal.update(capture, received_at_s=received, final=n == 2))
        report = journal.report()
        if index != 6:  # Explicitly enrolled but missing capture; cannot silently drop it.
            streams.append({'report': report, 'receipt_time_uncertainty_s': 0.005})
        for cavity in (1, 2):
            sample = {'sample_id': f'case.{index}.{cavity}', 'group_id': f'synthetic.run.{index}',
                'cycle_id': cid, 'cycle_ref': cref, 'cavity_id': f'cavity.{cavity}', 'serial': f's{index}.{cavity}',
                'started_at_s': start, 'stream_spec_ref': report['spec_ref'],
                'stream_head_ref': report['receipts'][-1]['receipt_ref'], 'mapping_evidence_ref': mapping_ref}
            samples.append(sample)
            defective = (index in (0, 2, 5) and cavity == 2) or (index == 4 and cavity == 1)
            outcome = {'schema': 'ciw.toolpoint-independent-outcome.v1', 'sample_id': sample['sample_id'],
                'sample_ref': identity(sample), 'characteristic_id': protocol['characteristic_id'],
                'source_kind': 'synthetic', 'method_ref': protocol['inspection_method_ref'],
                'condition': 'conditioned', 'label': 'DEFECT_PRESENT' if defective else 'DEFECT_ABSENT',
                'independence': 'independent_measurement',
                'evidence_ref': identity({'synthetic_inspection': sample['sample_id']}),
                'calibration_ref': identity({'synthetic_optical_calibration': 1}),
                'assessor_id': 'synthetic.independent-inspector', 'measured_at_s': start+1}
            if index == 7 and cavity == 2:
                outcome['label'] = 'INDETERMINATE'
            outcomes.append(outcome)
    manifest = {'schema': 'ciw.toolpoint-validation-manifest.v1', 'manifest_id': 'synthetic.enrollment.A',
                'protocol_ref': identity(protocol), 'sampling_frame_ref': identity({'synthetic_frame': 16}), 'samples': samples}
    return {'schema': REQUEST_SCHEMA, 'protocol': protocol, 'manifest': manifest, 'streams': streams,
            'outcomes': outcomes, 'assessed_at_s': 30}
