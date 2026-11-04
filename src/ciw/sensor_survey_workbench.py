"""Host-only synthetic survey/sensor reconciliation in the existing workbench.

Exact coincident support only: no clock/coordinate/unit conversion, source
selection by a model, physical state admission, or device access. The optional
Atelier journal implementation must be supplied by the embedding launcher.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import html
import os
from pathlib import Path
import stat

from .adapters.protocol import InstrumentManifest
from .campaign_contracts import canonical, closed_object, parse
from .control_contracts import artifact, content_ref, number, observation
from .core.covariance import create_covariance_artifact, validate_covariance_artifact
from .core.identities import evidence_id
from .data_instruments import content_ref as byte_ref, version_ref
from .data_sources import retain_record
from .offline_survey import OfflineSurveyFlow, SURVEY_TABLE, CLAIMS as SURVEY_CLAIMS, _points
from .session import Session

OPERATION = 'model.discrepancy.v1'
MODEL = 'coincident-height-discrepancy.v1'
SCHEMA = 'odsp.sensor-survey-model.v1'
MAX_FILE = 1024 * 1024
IMPLEMENTATION_REF = byte_ref(Path(__file__).read_bytes())
CLAIMS = {'authenticated_time': False, 'source_authenticated': False,
          'nsl_admission': 'not_verified', 'physical_validation': 'not_performed',
          'physical_calibration': 'not_established', 'state_admission': 'not_performed',
          'equipment_authority': False, 'automatic_action': False,
          'hosted_readiness': 'not_established'}


class ReconciliationRefusal(ValueError):
    """No calculation/disclosure is authorized by a failed input check."""


class ReconciliationOutcomeUnknown(ReconciliationRefusal):
    """A local execution may exist; preserve its directory for operator recovery."""
    def __init__(self, output):
        super().__init__('local_reconciliation_incomplete_or_disclosure_withheld')
        self.output = output


def require(condition, reason):
    if not condition:
        raise ReconciliationRefusal(reason)


def _model(value, expected_ref):
    raw = canonical(value)
    require(byte_ref(raw) == content_ref(expected_ref), 'model_pin_mismatch')
    value = parse(raw)
    closed_object(value, ('schema', 'model_id', 'survey', 'sensor',
                          'sensor_uncertainty', 'shared_component', 'assumptions'))
    require(value['schema'] == SCHEMA and value['model_id'] == MODEL, 'model_version_required')
    closed_object(value['survey'], ('artifact_id', 'capture_ref', 'result_ref', 'point_id'))
    closed_object(value['sensor'], ('job_id', 'export_ref', 'sample_ref'))
    for ref in (value['survey']['capture_ref'], value['survey']['result_ref'],
                value['sensor']['export_ref'], value['sensor']['sample_ref']):
        content_ref(ref)
    covariance = value['sensor_uncertainty']
    validate_covariance_artifact(covariance, expected_quantity_ids=['height'], expected_units=['m'])
    require(0 < covariance['matrix'][0][0] <= 10000
            and covariance['provenance'].get('metadata', {}).get('status') == 'declared_unverified',
            'sensor_uncertainty_declaration_required')
    item = value['shared_component']
    closed_object(item, ('schema', 'unit', 'frame', 'standard_uncertainty_m', 'status'))
    require(item['schema'] == 'odsp.shared-height-component.v1' and item['unit'] == 'm'
            and item['status'] == 'declared_unverified', 'uncertainty_declaration_required')
    require(0 < number(item['standard_uncertainty_m']) <= 100
            and item['standard_uncertainty_m'] ** 2 > 0, 'uncertainty_range_refused')
    require(type(value['assumptions']) is list and 1 <= len(value['assumptions']) <= 8
            and all(type(s) is str and 1 <= len(s) <= 512 for s in value['assumptions']),
            'explicit_model_assumptions_required')
    return raw


def _survey(view, receipt, raw, model, *, require_current_ready=True):
    """Reconstruct the same report/table bindings offline, without an issuer."""
    s = model['survey']
    report = view['retained_result']
    require(view['status'] == report['status'] == 'held' and report['origin'] == 'synthetic'
            and view['artifact_id'] == s['artifact_id']
            and report['capture_ref'] == s['capture_ref']
            and report['result_ref'] == s['result_ref']
            == byte_ref(canonical({k: v for k, v in report.items() if k != 'result_ref'})),
            'survey_result_binding_mismatch')
    require(report['binding_ref'] == byte_ref(canonical(report['binding']))
            and report['raw_ref'] == byte_ref(raw) and not report['semantic_issues']
            and report['claims'] == view['claims'] == SURVEY_CLAIMS
            and report['coordinate_transformation'] == 'not_performed'
            and report['timestamp_status'] == 'host_observation_only',
            'survey_semantics_refused')
    body = receipt['body']
    require(version_ref(receipt) == s['capture_ref'] and receipt['kind'] == 'capture'
            and body['source_ref'] == report['source_ref']
            and version_ref(body['dataset']) == body['dataset_ref'] == report['dataset_ref']
            and body['dataset']['content_ref'] == byte_ref(raw)
            and body['dataset']['table_contract_ref'] == version_ref(SURVEY_TABLE),
            'survey_capture_binding_mismatch')
    assessment = report['assessment_receipt']
    require(version_ref(assessment) == report['assessment_ref']
            and assessment['kind'] == 'assessment'
            and assessment['body']['capture_ref'] == s['capture_ref'],
            'survey_assessment_binding_mismatch')
    checks = [assessment['body']]
    if require_current_ready:
        checks.append(view['current_assessment'])
    for checked in checks:
        require(checked['capture_ref'] == s['capture_ref']
                and checked['hold_ref'] is None
                and checked['checks'] == {'schema': 'within_contract',
                    'calibration': 'within_tolerance', 'freshness': 'within_nominal_window'},
                'survey_drift_or_freshness_hold')
    points, issues = _points({'raw': raw, 'receipt': receipt}, report['binding'])
    require(not issues and points == report['reported_points'], 'survey_point_reconstruction_failed')
    found = [p for p in points if p['point_id'] == s['point_id'] and p['role'] == 'observation']
    require(len(found) == 1, 'one_survey_observation_required')
    return found[0]


def _pair(view, receipt, raw, exported, model, readiness, *, implementation_ref=None):
    from atelier_mcp.observation_gateway import inspect_export
    require(len(exported) <= 49152 and len(canonical({'view': view, 'capture_receipt': receipt,
            'raw_b64': base64.b64encode(raw).decode('ascii')})) <= 49152,
            'one_pair_source_retention_budget')
    point = _survey(view, receipt, raw, model)
    journal = inspect_export(exported, expected_ref=model['sensor']['export_ref'])
    closed_object(readiness, ('schema', 'binding_ref', 'job_id', 'outcome',
                             'binding_hold_job_id', 'ready_for_held_projection', 'claims'))
    require(readiness == {'schema': 'atelier.observation-readiness.v1',
        'binding_ref': journal['binding_ref'], 'job_id': journal['job_id'], 'outcome': 'complete',
        'binding_hold_job_id': None, 'ready_for_held_projection': True, 'claims': journal['claims']},
        'current_journal_binding_hold_or_incomplete_job')
    require(journal['job_id'] == model['sensor']['job_id']
            and journal['outcome'] == 'complete' and not journal['stop_requested'],
            'completed_observation_job_required')
    bound, summary = journal['binding'], journal['summary']
    require(bound['origin'] == 'synthetic_recording' and bound['effects'] == 'observe_only'
            and summary['current_calibration_serving'] == 'declared_current',
            'synthetic_current_calibration_declaration_required')
    found = [r for r in summary['samples'] if r['sample_ref'] == model['sensor']['sample_ref']]
    require(len(found) == 1, 'one_sensor_measurement_required')
    row, sample = found[0], found[0]['sample']
    require(row['calibration_at_acquisition'] == 'declared_applicable'
            and sample['quality'] == 'reported_good' and sample['missing_reason'] is None
            and sample['value'] is not None, 'missing_or_uncertain_sensor_sample')
    number(sample['value'])
    channel = [c for c in bound['channels'] if c['channel_id'] == sample['channel_id']]
    require(len(channel) == 1 and channel[0]['quantity'] == 'height'
            and sample['unit'] == point['unit'] == 'm'
            and sample['frame'] == point['frame_id'], 'quantity_frame_or_unit_mismatch')
    clock = view['retained_result']['binding']['clock_frame']
    require(bound['source_clock'] == {'clock_id': clock['clock_id'], 'scale': clock['time_scale'], 'unit': 's'}
            and sample['source_stamp'] == sample['acquired_at']
            == {'clock_id': point['clock_id'], 'seconds': point['sample_time_s']},
            'exact_coincident_declared_time_required')
    configuration = bound['configuration']
    require(all(configuration.get(k) == point[k] for k in ('point_id', 'easting_m', 'northing_m')),
            'declared_point_support_mismatch')
    uncertainty, shared = model['sensor_uncertainty'], model['shared_component']
    require(uncertainty['frame'] == shared['frame'] == point['frame_id']
            and bound['uncertainty'] == {'status': 'declared_unverified',
                'covariance_ref': uncertainty['covariance_id'],
                'dependency_refs': [byte_ref(canonical(shared))]},
            'uncertainty_or_dependency_binding_mismatch')
    require(uncertainty['reference_values'] == [sample['value']], 'sensor_covariance_reference_mismatch')
    sensor_variance, survey_u = uncertainty['matrix'][0][0], point['height_standard_uncertainty_m']
    common_u = shared['standard_uncertainty_m']
    require(survey_u > 0 and survey_u ** 2 > 0 and common_u ** 2 <= min(sensor_variance, survey_u ** 2),
            'shared_variance_exceeds_marginals')
    lineage = {'survey_result_ref': model['survey']['result_ref'],
        'survey_capture_ref': model['survey']['capture_ref'], 'survey_raw_ref': byte_ref(raw),
        'survey_dataset_ref': view['retained_result']['dataset_ref'],
        'sensor_export_ref': model['sensor']['export_ref'], 'sensor_record_ref': journal['record_ref'],
        'sensor_binding_ref': journal['binding_ref'], 'sensor_sample_ref': row['sample_ref'],
        'sensor_measurement_identity_ref': row['identity_ref'], 'model_ref': byte_ref(canonical(model)),
        'bridge_implementation_ref': IMPLEMENTATION_REF if implementation_ref is None else content_ref(implementation_ref),
        'journal_readiness_ref': byte_ref(canonical(readiness))}
    frame, unit, model_ref = point['frame_id'], point['unit'], lineage['model_ref']
    manifest = InstrumentManifest(instrument_id='odsp.sensor-survey-pair.v1', role='recording',
        units={'sensor_height': unit, 'survey_height': unit}, frames=(frame,),
        sampling={'kind': 'single_declared_coincident_support',
            'relative_origin_s': point['sample_time_s'], 'duration_semantics': 'selection_container_only'},
        normalization={'clock_mapping': 'not_performed', 'coordinate_transform': 'not_performed',
                       'unit_conversion': 'not_performed'},
        calibration_requirements={'physical_traceability': 'not_established'})
    run = {'run_schema': 'run.v1', 'run_id': 'pair-' + model_ref[7:39],
        'instrument': manifest.instrument_id, 'metadata': {'duration_s': 1, 'sample_count': 1,
            'coordinate_frame': frame, 'manifest': manifest.to_dict(),
            'provenance': lineage, 'model': model,
            'source_clock': clock, 'source_time_s': point['sample_time_s'],
            'duration_semantics': 'selection_container_only_no_sampled_interval'},
        'time_s': [0], 'channels': {'sensor_height': {'unit': unit, 'values': [sample['value']]},
                                  'survey_height': {'unit': unit, 'values': [point['height_m']]}}, 'render': {}}
    run['evidence_id'] = evidence_id(run)
    provenance = {'provider': MODEL, 'source_evidence_ids': sorted(set(lineage.values()) | {run['evidence_id']}),
                  'source_covariance_ids': [uncertainty['covariance_id']],
                  'metadata': {'shared_additive_component_sensitivities': [1, 1],
                    'shared_component_ref': byte_ref(canonical(shared)), 'physical_traceability': 'not_established'}}
    def covariance(ids, values, matrix):
        return create_covariance_artifact(quantity_ids=ids, units=[unit] * len(ids), frame=frame,
            reference_values=values, matrix=matrix, method=MODEL,
            basis={'kind': 'observation', 'id': model_ref}, provenance=provenance,
            assumptions=model['assumptions'])
    sensor_cov = covariance(['height'], [sample['value']], [[sensor_variance]])
    survey_cov = covariance(['height'], [point['height_m']], [[survey_u ** 2]])
    joint = covariance(['observed:' + point['point_id'], 'predicted:' + point['point_id']],
        [sample['value'], point['height_m']],
        [[sensor_variance, common_u ** 2], [common_u ** 2, survey_u ** 2]])
    observations = []
    for name, value, cov, refs in (
        ('sensor', sample['value'], sensor_cov, [lineage['sensor_export_ref'], row['sample_ref']]),
        ('survey', point['height_m'], survey_cov, [lineage['survey_result_ref'], lineage['survey_raw_ref']])):
        observations.append(observation(identity={'model_id': MODEL, 'entity_id': point['point_id'], 'execution_id': None},
            clock={'id': point['clock_id'], 'time_s': point['sample_time_s']}, frame=frame,
            quantity='height', value=value, unit=unit, uncertainty=cov,
            provenance={'provider': 'odsp.' + name + '-record-adapter.v1', 'sources': refs,
                        'semantics': 'simulated'}))
    request = {'source_evidence_id': run['evidence_id'], 'assumptions': model['assumptions'],
        'sample_ids': [point['point_id']], 'observed': [sample['value']], 'predicted': [point['height_m']],
        'unit': unit, 'frame': frame, 'joint_covariance': joint}
    return run, request, observations, point, journal, lineage


def _add_sources(session, native_inputs):
    sources = []
    for index, (name, native, family) in enumerate(native_inputs):
        envelope = retain_record(native, dataset_id='sensor-survey-' + name, revision=1,
            parent_source_id=None, recorded_at=datetime.now(timezone.utc).isoformat(), data_family=family)
        payload = {'kind': 'data-record', 'label': name,
                   'bytes_b64': base64.b64encode(canonical(envelope)).decode('ascii')}
        if index in (2, 3):
            payload['input_source_ids'] = [sources[index - 2]['source_id']]
        elif index == 4:
            payload['input_source_ids'] = [s['source_id'] for s in sources[2:4]]
        sources.append(session.workbench.add_source(payload))
    return sources


def _projection(outcome, run, point, lineage, sources):
    result = outcome['result']['data']['result']
    return {'schema': 'odsp.sensor-survey-held-projection.v1', 'status': 'held',
        'review': {'status': 'pending_human_review', 'decision': None},
        'operation_id': OPERATION, 'model_id': MODEL, 'model_ref': lineage['model_ref'],
        'execution_id': outcome['execution']['execution_id'], 'result_id': outcome['result']['result_id'],
        'run_evidence_id': run['evidence_id'], 'source_ids': [s['source_id'] for s in sources],
        'lineage': lineage, 'point': point,
        'sensor_height_m': run['channels']['sensor_height']['values'][0],
        'survey_height_m': point['height_m'], 'sensor_minus_survey_m': result['residuals'][0],
        'residual_standard_uncertainty_m': result['standard_uncertainty'][0],
        'residual_covariance': result['covariance'],
        'interpretation': 'one declared coincident scalar discrepancy; no fused physical state or acceptance verdict',
        'hold_reasons': ['synthetic_inputs_only', 'source_and_time_not_authenticated',
            'uncertainty_and_shared_component_declared_unverified', 'nsl_admission_not_verified',
            'physical_calibration_not_established', 'human_review_required'], 'claims': deepcopy(CLAIMS)}


def _html(projection):
    p = projection
    pairs = [('Point', p['point']['point_id']), ('Declared local frame', p['point']['frame_id']),
        ('Easting / northing (m)', f"{p['point']['easting_m']} / {p['point']['northing_m']}"),
        ('Source clock / time (s)', f"{p['point']['clock_id']} / {p['point']['sample_time_s']}"),
        ('Sensor height (m)', p['sensor_height_m']), ('Survey height (m)', p['survey_height_m']),
        ('Sensor minus survey (m)', p['sensor_minus_survey_m']),
        ('Residual standard uncertainty, k=1 (m)', p['residual_standard_uncertainty_m'])]
    rows = ''.join('<tr><th>' + html.escape(k) + '</th><td>' + html.escape(str(v)) + '</td></tr>' for k, v in pairs)
    refs = ''.join('<li>' + html.escape(k) + ': <code>' + html.escape(v) + '</code></li>' for k, v in p['lineage'].items())
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
        '<title>Held synthetic height reconciliation</title><style>body{font:18px system-ui;max-width:65rem;margin:2rem auto;padding:1rem}'
        'th,td{text-align:left;padding:.5rem;border-bottom:1px solid #ccc}code{overflow-wrap:anywhere}</style>'
        '<h1>Held synthetic height reconciliation</h1><p>Pending human review. Synthetic observations and declared uncertainties; '
        'no authenticated time, NSL admission, physical validation or equipment authority.</p><table>' + rows + '</table>'
        '<p>The full joint covariance includes a shared additive component with sensitivity +1 for both inputs. '
        'The discrepancy variance is C(sensor,sensor) + C(survey,survey) − 2 C(sensor,survey). '
        'A standard uncertainty is not a confidence probability or an acceptance verdict. No physical state is fused.</p>'
        '<p><a href="projection.json">Exact projection</a> · <a href="workspace.json">Workbench</a> · '
        '<a href="model.json">Declared model</a> · <a href="journal-export.json">Journal</a> · '
        '<a href="survey-context.json">Survey provenance</a></p><ul>' + refs + '</ul></html>').encode('utf-8')


def _write(root, name, value):
    raw = value if type(value) is bytes else canonical(value)
    require(0 < len(raw) <= MAX_FILE, 'reconciliation_file_budget')
    with (root / name).open('xb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def _read(root, name):
    path = root / name
    require(not path.is_symlink() and path.parent in (root, root / 'session')
            and not path.parent.is_symlink(), 'owned_local_artifact_required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), 'owned_regular_artifact_required')
        raw = source.read(MAX_FILE + 1)
    require(0 < len(raw) <= MAX_FILE, 'reconciliation_file_budget')
    return raw


class SensorSurveyWorkbench:
    """Bind current host authorities once; saved files never restore permission."""
    def __init__(self, *, survey, journal, model, expected_model_ref, authorize):
        from atelier_mcp.observation_gateway import ObservationJournal
        require(isinstance(survey, OfflineSurveyFlow) and type(journal) is ObservationJournal
                and callable(authorize), 'bound_survey_journal_and_host_admission_required')
        require((survey.ledger.tenant, survey.ledger.principal) == (journal.tenant, journal.principal),
                'source_owner_scope_mismatch')
        self.survey, self.journal, self._authorize = survey, journal, authorize
        self._model = _model(model, expected_model_ref)
        self.model_ref = expected_model_ref
        self._fixed = (survey, journal, survey.ledger.tenant, survey.ledger.principal, journal.tenant, journal.principal)

    def _guard(self, action):
        require((self.survey, self.journal, self.survey.ledger.tenant, self.survey.ledger.principal,
                 self.journal.tenant, self.journal.principal) == self._fixed
                and byte_ref(self._model) == self.model_ref, 'reconciliation_host_binding_changed')
        request = {'model_ref': self.model_ref, 'operation_id': OPERATION,
                   'tenant': self._fixed[2], 'principal': self._fixed[3]}
        try:
            allowed = self._authorize(action, deepcopy(request)) is True
        except Exception:
            allowed = False
        require(allowed, 'host_reconciliation_not_admitted')

    def _inputs(self):
        self._guard('read')
        model = parse(self._model)
        s = model['survey']
        view = self.survey.read(s['artifact_id'], capture_ref=s['capture_ref'])
        snapshot = self.survey.ledger.inspect_capture(self.survey.source_id, s['capture_ref'])
        exported = self.journal.export(model['sensor']['job_id'])
        readiness = self.journal.readiness(model['sensor']['job_id'])
        paired = _pair(view, snapshot['receipt'], snapshot['raw'], exported, model, readiness)
        self._guard('read')
        return view, snapshot, exported, paired, readiness

    def execute(self, output):
        """Fresh numerical occurrence, create-only local retention, always held."""
        view, snapshot, exported, paired, readiness = self._inputs()
        run, request, observations, point, journal, lineage = paired
        self._guard('compute')
        output = Path(output).absolute()
        require(not output.exists() and not output.is_symlink(), 'new_local_output_required')
        output.mkdir(mode=0o700, parents=False, exist_ok=False)
        try:
            _write(output, 'model.json', self._model)
            _write(output, 'survey-context.json', {'view': view, 'capture_receipt': snapshot['receipt']})
            _write(output, 'survey-raw.json', snapshot['raw'])
            _write(output, 'journal-export.json', exported)
            _write(output, 'journal-readiness.json', readiness)
            session = Session(run, output / 'session')
            def raw_artifact(raw, producer):
                return artifact(raw, media_type='application/json', producer=producer,
                    experiment_id=self.model_ref,
                    metadata={'exact_bytes_b64': base64.b64encode(raw).decode('ascii'),
                              'time_status': 'host_wall_clock_only_not_authenticated'})
            inputs = [('sensor-journal', raw_artifact(exported, 'atelier.observation-journal'), 'artifact'),
                      ('survey-context', raw_artifact(canonical({'view': view,
                          'capture_receipt': snapshot['receipt'],
                          'raw_b64': base64.b64encode(snapshot['raw']).decode('ascii')}),
                          'odsp.offline-survey'), 'artifact'),
                      ('sensor-observation', observations[0], 'observation'),
                      ('survey-observation', observations[1], 'observation'),
                      ('joint-covariance', request['joint_covariance'], 'covariance')]
            sources = _add_sources(session, inputs)
            # Filesystem work may block: recheck both source guards and the exact
            # immutable inputs immediately before the numerical occurrence.
            current = self._inputs()
            require(canonical(current[0]['retained_result']) == canonical(view['retained_result'])
                    and current[2] == exported,
                    'source_readiness_changed')
            self._guard('compute')
            reply = session.handle({'protocol_version': 1, 'request_id': 'sensor-survey-reconcile',
                'type': 'operation.execute', 'payload': {'operation_id': OPERATION,
                    'parameters': {'request': request}}})
            require(reply['type'] == 'response' and reply['payload']['status'] == 'completed',
                    'numerical_reconciliation_refused')
            outcome = reply['payload']
            self._guard('retain')
            session.save_workspace(output / 'workspace.json')
            projection = _projection(outcome, run, point, lineage, sources)
            _write(output, 'projection.json', projection)
            _write(output, 'projection.html', _html(projection))
            files = {str(p.relative_to(output)): byte_ref(_read(output, str(p.relative_to(output))))
                     for p in sorted(output.rglob('*')) if p.is_file()}
            manifest = {'schema': 'odsp.sensor-survey-workbench.v1', 'status': 'held',
                'model_ref': self.model_ref, 'files': files, 'claims': deepcopy(CLAIMS)}
            self._guard('retain')
            _write(output, 'manifest.json', manifest)
            fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            current = self._inputs()
            require(canonical(current[0]['retained_result']) == canonical(view['retained_result'])
                    and current[2] == exported,
                    'source_readiness_changed')
            self._guard('disclose')
            return {'status': 'held', 'output': str(output), 'manifest_ref': byte_ref(canonical(manifest)),
                'execution_id': projection['execution_id'], 'result_id': projection['result_id'],
                'review': deepcopy(projection['review']), 'claims': deepcopy(CLAIMS)}
        except Exception:
            raise ReconciliationOutcomeUnknown(str(output)) from None


def inspect_workbench(root, *, expected_manifest_ref, playback_output=None):
    """Pinned offline playback only. No current-source readiness or permission claim."""
    root = Path(root).absolute()
    require(not root.is_symlink() and root.is_dir() and root.stat().st_uid == os.getuid()
            and not root.stat().st_mode & 0o077, 'owned_private_workbench_required')
    raw = _read(root, 'manifest.json')
    require(byte_ref(raw) == content_ref(expected_manifest_ref), 'workbench_manifest_pin_mismatch')
    manifest = parse(raw)
    closed_object(manifest, ('schema', 'status', 'model_ref', 'files', 'claims'))
    require(manifest['schema'] == 'odsp.sensor-survey-workbench.v1'
            and manifest['status'] == 'held' and manifest['claims'] == CLAIMS,
            'held_workbench_required')
    names = manifest['files']
    required = {'model.json', 'survey-context.json', 'survey-raw.json', 'journal-export.json', 'journal-readiness.json',
                'workspace.json', 'projection.json', 'projection.html'}
    require(type(names) is dict and required <= names.keys() and len(names) <= 16,
            'workbench_artifact_set_mismatch')
    for name, pin in names.items():
        require(type(name) is str and len(name) < 100 and '..' not in name and '\\' not in name
                and (name in required or name.startswith('session/') and name.count('/') == 1)
                and byte_ref(_read(root, name)) == content_ref(pin), 'workbench_artifact_pin_mismatch')
    model = parse(_model(parse(_read(root, 'model.json')), manifest['model_ref']))
    context = parse(_read(root, 'survey-context.json'))
    saved = Session.inspect_workspace(root / 'workspace.json')
    run, request, observations, point, journal, lineage = _pair(context['view'], context['capture_receipt'],
        _read(root, 'survey-raw.json'), _read(root, 'journal-export.json'), model,
        parse(_read(root, 'journal-readiness.json')),
        implementation_ref=saved['run']['metadata']['provenance']['bridge_implementation_ref'])
    require(canonical(saved['run']) == canonical(run) and len(saved['results']) == len(saved['executions']) == 1,
            'retained_pair_or_occurrence_mismatch')
    result = next(iter(saved['results'].values()))
    execution = next(iter(saved['executions'].values()))
    require(execution['operation_id'] == OPERATION and execution['parameters'] == {'request': request},
            'retained_operation_binding_mismatch')
    retained_sources = saved['workbench']['sources']
    sources = [{k: v for k, v in source.items() if k != 'bytes_b64'} for source in retained_sources]
    require(len(sources) == 5, 'retained_sources_mismatch')
    from .data_sources import native_record
    from .control_contracts import validate_artifact
    original_bytes = [_read(root, 'journal-export.json'), canonical({**context,
        'raw_b64': base64.b64encode(_read(root, 'survey-raw.json')).decode('ascii')})]
    for source, payload in zip(retained_sources[:2], original_bytes):
        native = native_record(source)
        validate_artifact(native, payload)
        require(native['metadata'] == {'exact_bytes_b64': base64.b64encode(payload).decode('ascii'),
                                      'time_status': 'host_wall_clock_only_not_authenticated'},
                'original_source_bytes_mismatch')
    for source, expected in zip(retained_sources[2:], [*observations, request['joint_covariance']]):
        require(canonical(native_record(source)) == canonical(expected),
                'retained_native_observation_mismatch')
    expected = _projection({'result': result, 'execution': execution}, run, point, lineage, sources)
    require(canonical(expected) == _read(root, 'projection.json')
            and _html(expected) == _read(root, 'projection.html'), 'retained_projection_mismatch')
    if playback_output is not None:
        playback_output = Path(playback_output).absolute()
        require(not playback_output.exists() and not playback_output.is_symlink()
                and root.resolve() not in playback_output.resolve().parents
                and root.resolve() != playback_output.resolve(), 'new_separate_playback_output_required')
        Session.from_workspace(root / 'workspace.json', playback_output)
    return {'status': 'held', 'projection': expected, 'claims': deepcopy(CLAIMS),
            'source_bindings': {'survey': deepcopy(model['survey']),
                'sensor': {'job_id': journal['job_id'], 'binding_ref': journal['binding_ref']}},
            'playback': 'retained_history_only_no_source_read_or_new_execution'}
