"""One local synthetic survey intake and retained, held observation inspection.

Launcher-only composition of the existing data-instrument ledger and owner
store. No MCP registration, network, coordinate conversion, scientific provider,
authenticated time, physical calibration or state admission is supplied here.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re

from .campaign_contracts import ContractRefusal, canonical, closed_object, parse, text
from .data_instruments import (DataInstrumentLedger, InstrumentRefusal, TABLE_LIMITS,
                               content_ref, validate_table, version_ref)
from .retention import Store

SURVEY_TABLE = {'key': 'point_id', 'columns': {
    'point_id': 'string', 'role': 'string', 'easting_m': 'number', 'northing_m': 'number',
    'height_m': 'number', 'height_standard_uncertainty_m': 'number', 'frame_id': 'string',
    'unit': 'string', 'instrument_ref': 'string', 'calibration_ref': 'string',
    'sample_time_s': 'number', 'clock_id': 'string', 'time_scale': 'string'}, 'nullable': []}
CLAIMS = {'authorizes_execution': False, 'authorizes_state_admission': False,
          'trusted_for_downstream': False, 'source_authenticated': False,
          'authenticated_time': False, 'nsl_admission': 'not_verified',
          'physical_calibration_established': False, 'automatic_action': False,
          'live_source_read': False}
IMPLEMENTATION_REF = content_ref(Path(__file__).read_bytes())
_REF = re.compile(r'sha256:[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,79}\Z')


class SurveyRefusal(ValueError):
    """Bounded local diagnostic without captured values or paths."""


class SurveyOutcomeUnknown(SurveyRefusal):
    """Result retention completed but current authority withholds disclosure."""
    def __init__(self, artifact_id, capture_ref):
        super().__init__('survey_retained_disclosure_withheld')
        self.artifact_id, self.capture_ref = artifact_id, capture_ref


def _require(condition, reason):
    if not condition:
        raise SurveyRefusal(reason)


def _reference(value):
    _require(type(value) is str and _REF.fullmatch(value) is not None,
             'survey_reference_required')
    return value


def _binding(value):
    value = parse(canonical(value))
    closed_object(value, ('schema', 'origin', 'frame_id', 'linear_unit', 'axis_order',
                          'instrument_ref', 'calibration_ref', 'clock_frame'))
    _require(value['schema'] == 'odsp.offline-survey.binding.v1'
             and value['origin'] == 'synthetic', 'synthetic_survey_binding_required')
    text(value['frame_id'], 128)
    _require(value['linear_unit'] == 'm'
             and value['axis_order'] == ['easting', 'northing', 'height'],
             'survey_metre_axes_required')
    _reference(value['instrument_ref']); _reference(value['calibration_ref'])
    frame = value['clock_frame']
    closed_object(frame, ('clock_id', 'time_scale', 'unit'))
    text(frame['clock_id'], 256); text(frame['time_scale'], 128)
    _require(frame['unit'] == 's', 'survey_seconds_required')
    return canonical(value)


def _points(snapshot, binding):
    try:
        indexed = validate_table(snapshot['raw'], SURVEY_TABLE)
    except (ContractRefusal, InstrumentRefusal):
        return [], ['survey_table_contract_failed']
    rows = [indexed[key] for key in sorted(indexed)]
    issues = set()
    if not 2 <= len(rows) <= 64 or {row['role'] for row in rows} != {'control', 'observation'}:
        issues.add('survey_point_support_refused')
    observed = snapshot['receipt']['body']['observed']
    for row in rows:
        if not _ID.fullmatch(row['point_id']):
            issues.add('survey_point_identifier_refused')
        if row['frame_id'] != binding['frame_id']:
            issues.add('survey_frame_mismatch')
        if row['unit'] != binding['linear_unit']:
            issues.add('survey_unit_mismatch')
        if any(abs(row[name]) > 1e7 for name in ('easting_m', 'northing_m', 'height_m')):
            issues.add('survey_coordinate_budget')
        if not 0 <= row['height_standard_uncertainty_m'] <= 100:
            issues.add('survey_uncertainty_refused')
        if (row['instrument_ref'] != binding['instrument_ref']
                or row['calibration_ref'] != binding['calibration_ref']):
            issues.add('survey_instrument_or_calibration_mismatch')
        if (observed['frame'] != binding['clock_frame']
                or row['clock_id'] != binding['clock_frame']['clock_id']
                or row['time_scale'] != binding['clock_frame']['time_scale']
                or row['sample_time_s'] != observed['value']):
            issues.add('survey_clock_mismatch')
    return ([] if issues else rows), sorted(issues)


class OfflineSurveyFlow:
    """Fixed host bindings and fresh ledger guards around every retained read.

    The capture attempt is idempotent in the source ledger. Each call records a
    separate assessment/result occurrence. Saved results remain immutable;
    reopening reports a separate current assessment and never releases a hold.
    """
    def __init__(self, *, ledger, source_id, source_ref, binding):
        if not isinstance(ledger, DataInstrumentLedger):
            raise TypeError('data_instrument_ledger_required')
        _require(type(source_id) is str and _ID.fullmatch(source_id) is not None,
                 'survey_source_identifier_required')
        self.ledger, self.source_id, self.source_ref = ledger, source_id, _reference(source_ref)
        self._binding_raw = _binding(binding)
        self.binding_ref = content_ref(self._binding_raw)
        self._identity = (ledger, ledger.db, ledger.policy, ledger.tenant, ledger.principal,
                          source_id, source_ref, self.binding_ref)
        self._owner = 'offline-survey:' + sha256(canonical(
            [ledger.tenant, ledger.principal, source_id, source_ref, self.binding_ref])).hexdigest()
        self.store = Store(self._owner, evict=False)

    def _check_binding(self):
        ledger = self.ledger
        _require((ledger, ledger.db, ledger.policy, ledger.tenant, ledger.principal,
                  self.source_id, self.source_ref, self.binding_ref) == self._identity
                 and content_ref(self._binding_raw) == self.binding_ref
                 and self.store.owner == self._owner, 'survey_host_binding_changed')

    def _inspect(self, capture_ref):
        self._check_binding()
        ledger = self.ledger
        snapshot = ledger.inspect_capture(self.source_id, _reference(capture_ref))
        body = snapshot['receipt']['body']
        _require(body['source_ref'] == self.source_ref
                 and body['dataset']['table_contract_ref'] == version_ref(SURVEY_TABLE)
                 and body['dataset_ref'] == version_ref(body['dataset'])
                 and body['dataset']['content_ref'] == content_ref(snapshot['raw']),
                 'survey_dataset_binding_mismatch')
        assessment = snapshot['assessment']
        if 'out_of_tolerance' in assessment['checks'].values() and assessment['hold_ref'] is None:
            ledger.assess(self.source_id, capture_ref)
            snapshot = ledger.inspect_capture(self.source_id, capture_ref)
        return snapshot

    def _report(self, capture_ref, assessment_ref):
        snapshot = self._inspect(capture_ref)
        assessment = self.ledger.receipt(self.source_id, _reference(assessment_ref))
        _require(assessment['kind'] == 'assessment'
                 and assessment['body']['capture_ref'] == capture_ref
                 and assessment['body']['source_ref'] == self.source_ref,
                 'survey_assessment_binding_mismatch')
        binding = parse(self._binding_raw)
        points, issues = _points(snapshot, binding)
        value = {'schema': 'odsp.offline-survey.held-result.v1', 'status': 'held',
            'origin': 'synthetic', 'source_id': self.source_id, 'source_ref': self.source_ref,
            'capture_ref': capture_ref, 'dataset_ref': snapshot['receipt']['body']['dataset_ref'],
            'raw_ref': content_ref(snapshot['raw']), 'assessment_ref': assessment_ref,
            'assessment_receipt': assessment, 'binding_ref': self.binding_ref, 'binding': binding,
            'implementation_ref': IMPLEMENTATION_REF, 'reported_points': points,
            'semantic_issues': issues, 'coordinate_transformation': 'not_performed',
            'uncertainty_interpretation': 'source_declared_standard_uncertainty_not_verified',
            'calibration_status': 'synthetic_control_comparison_only',
            'timestamp_status': 'host_observation_only',
            'hold_reasons': ['authenticated_source_time_missing', 'verified_nsl_admission_missing',
                             'physical_calibration_not_established', 'human_review_required'],
            'claims': deepcopy(CLAIMS)}
        return {**value, 'result_ref': content_ref(canonical(value))}

    def capture(self, raw, *, expected_ref, attempt, observed):
        """Host supplies exact approved bytes/pin; malformed bytes remain evidence."""
        self._check_binding()
        _require(type(raw) is bytes and 0 < len(raw) <= TABLE_LIMITS.max_bytes,
                 'survey_capture_byte_budget')
        _require(content_ref(raw) == _reference(expected_ref), 'survey_capture_pin_mismatch')
        capture_ref = self.ledger.capture(self.source_id, self.source_ref, raw,
                                          attempt=attempt, observed=observed)
        self._inspect(capture_ref)
        assessment = self.ledger.assess(self.source_id, capture_ref)
        report = self._report(capture_ref, assessment['assessment_ref'])
        self._inspect(capture_ref)
        artifact = self.store.keep(report, kind='offline-survey-held-result', catalog={
            'schema': 'odsp.offline-survey.catalog.v1', 'status': 'held',
            'capture_ref': capture_ref, 'result_ref': report['result_ref'],
            'authorizes_state_admission': False})
        try:
            self._inspect(capture_ref)
        except Exception:
            raise SurveyOutcomeUnknown(artifact, capture_ref) from None
        return {'status': 'held', 'artifact_id': artifact, 'capture_ref': capture_ref,
                'dataset_ref': report['dataset_ref'], 'result_ref': report['result_ref']}

    def read(self, artifact_id, *, capture_ref):
        """Inspect retained provenance under current source/principal authority."""
        self._inspect(capture_ref)
        value = self.store.native(artifact_id)
        _require(type(value) is dict and 'assessment_ref' in value,
                 'retained_survey_mismatch')
        expected = self._report(capture_ref, value['assessment_ref'])
        _require(canonical(value) == canonical(expected), 'retained_survey_mismatch')
        current = self._inspect(capture_ref)['assessment']
        return {'status': 'held', 'artifact_id': artifact_id, 'retained_result': value,
                'current_assessment': current, 'claims': deepcopy(CLAIMS)}
