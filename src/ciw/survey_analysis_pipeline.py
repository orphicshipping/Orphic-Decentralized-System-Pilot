"""One governed local Domain H SQL selection and held analysis workflow.

Trusted launcher API, not a SQL executor, MCP registration or evidence admission.
Existing capture, current principal/scientific grants, admission, covariance,
analysis, durable queue and owner retention contracts remain authoritative.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from hashlib import sha256
import math
import os
from pathlib import Path
import sqlite3
import stat

from .campaign_contracts import canonical, closed_object, parse
from .coordinator import _Lease
from .control_contracts import save_new
from .core.covariance import create_covariance_artifact
from .data_analysis import DataAnalysisHost, DatasetBinding, quantity_id, reference
from .data_instruments import content_ref, validate_table, version_ref
from .numerical_runtime import runtime_identity
from .offline_survey import OfflineSurveyFlow, SURVEY_TABLE, _points
from .retention import Store, _read_regular
from .store_queue import StoreQueue, QueueLimits

PIPELINE_ID = 'survey-observation-height-summary.v1'
OPERATION = 'pipeline.survey-analysis.v1'
SQL = 'SELECT payload FROM samples WHERE role = ? ORDER BY point_id COLLATE BINARY'
MODEL = {'schema': 'odsp.survey-analysis-model.v1',
    'model_id': 'independent-declared-height-errors.v1', 'unit': 'm',
    'standard_uncertainty_column': 'height_standard_uncertainty_m',
    'covariance_assumption': 'mutually_independent_declared_unverified',
    'sampling_uncertainty': 'not_established', 'physical_traceability': 'not_established'}
CLAIMS = {'authorizes_execution': False, 'authorizes_state_admission': False,
    'trusted_for_downstream': False, 'authenticated_time': False,
    'source_authenticated': False, 'nsl_admission': 'not_verified',
    'hosted_qualified': False, 'physical_calibration': 'not_established',
    'automatic_action': False}
FILES = ('survey_analysis_pipeline.py', 'offline_survey.py', 'data_instruments.py',
         'data_analysis.py', 'persistent_cache.py', 'principal_policy.py',
         'campaign_contracts.py', 'store_queue.py', 'coordinator.py', 'retention.py',
         'adapters/oscillator.py')
MAX_ARTIFACT = 131072


class PipelineRefusal(ValueError):
    """Bounded local refusal; no raw source values or provider error text."""


class PipelineOutcomeUnknown(PipelineRefusal):
    def __init__(self, job_id):
        super().__init__('pipeline_attempt_recorded_inspect_no_retry')
        self.job_id = job_id


def require(condition, reason):
    if not condition:
        raise PipelineRefusal(reason)


def implementation():
    root = Path(__file__).parent
    return {'source_refs': {name: content_ref((root / name).read_bytes()) for name in FILES},
            'numerical': runtime_identity(), 'sqlite': sqlite3.sqlite_version,
            'analysis_interface_ref': version_ref(DataAnalysisHost.tools),
            'normalization': 'ciw.python-json.v1', 'scope': 'local_supplied_model_only'}


def definition(model_ref):
    return {'pipeline_id': PIPELINE_ID, 'version': '1.0.0',
        'input_schema': SURVEY_TABLE, 'input_schema_ref': version_ref(SURVEY_TABLE),
        'output_schema': SURVEY_TABLE, 'output_schema_ref': version_ref(SURVEY_TABLE),
        'transform': {'engine': 'sqlite', 'sql': SQL, 'parameters': ['observation'],
                      'semantics': 'lossless_role_selection_no_conversion'},
        'analysis': {'operation': 'ncp_analysis_statistics', 'column': 'height_m',
                     'keys': 'exact_transform_output_point_ids'},
        'uncertainty_model_ref': model_ref, 'maximum_input_rows': 32,
        'maximum_input_bytes': 32768, 'maximum_output_rows': 31}


def select_rows(rows):
    """Actual fixed in-memory SQL; no caller SQL, extension, file or attach."""
    require(2 <= len(rows) <= 32, 'pipeline_row_budget')
    with sqlite3.connect(':memory:') as db:
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('CREATE TABLE samples(point_id TEXT PRIMARY KEY,role TEXT NOT NULL,payload BLOB NOT NULL)')
        db.executemany('INSERT INTO samples VALUES(?,?,?)',
            [(row['point_id'], row['role'], canonical(row)) for row in rows])
        db.execute('PRAGMA query_only=ON')
        selected = [parse(row[0]) for row in db.execute(SQL, ('observation',))]
    require(1 <= len(selected) <= 31, 'pipeline_observations_unavailable')
    validate_table(canonical(selected), SURVEY_TABLE)
    return selected


def covariance(rows, *, dataset_ref, raw_ref, model_ref):
    return create_covariance_artifact(
        quantity_ids=[quantity_id(row['point_id']) for row in rows], units=['m'] * len(rows),
        frame='dataset:' + dataset_ref, reference_values=[row['height_m'] for row in rows],
        matrix=[[row['height_standard_uncertainty_m'] ** 2 if i == j else 0.0
                 for j in range(len(rows))] for i, row in enumerate(rows)],
        method=MODEL['model_id'], basis={'kind': 'observation', 'id': dataset_ref},
        provenance={'provider': PIPELINE_ID, 'source_evidence_ids': [raw_ref, model_ref],
                    'source_covariance_ids': [], 'metadata': {'independence': 'host_declared_unverified'}},
        assumptions=['Source standard uncertainties and mutual independence are host declarations.',
                     'No source authentication, sampling uncertainty or physical traceability established.'])


def validate_artifact(value, *, expected_ref):
    """Pure retained-content validation; no source, authority, SQL or kernel opens."""
    raw = canonical(value)
    require(len(raw) <= MAX_ARTIFACT and content_ref(raw) == expected_ref, 'pipeline_artifact_pin_mismatch')
    closed_object(value, ('schema', 'status', 'review', 'attempt', 'pipeline', 'input', 'transform',
                          'analysis', 'reproducible_value', 'reproducible_value_ref', 'claims'))
    require(value['schema'] == 'odsp.survey-analysis-artifact.v1' and value['status'] == 'held'
            and value['review'] == {'status': 'pending_human_review', 'decision': None}
            and value['claims'] == CLAIMS, 'pipeline_artifact_contract')
    source = value['input']
    admission = value['attempt']['admission']
    closed_object(admission, ('context_ref', 'principal', 'required_scientific_operations', 'scope'))
    reference(admission['context_ref'])
    require(admission['principal'] == source['principal']
            and admission['required_scientific_operations'] == ['statistics.v1', 'uncertainty.propagate.v1']
            and admission['scope'] == 'fresh_host_context_record_not_a_grant_or_nsl_admission',
            'pipeline_admission_record')
    require(source['model'] == MODEL and version_ref(source['model']) == source['model_ref'], 'pipeline_model_changed')
    spec = definition(source['model_ref'])
    require(value['pipeline']['definition'] == spec, 'pipeline_definition_changed')
    plan = {'definition': spec, 'implementation': value['pipeline']['implementation'],
            'binding_ref': source['analysis_binding_ref'], 'survey_binding_ref': source['survey_binding_ref'],
            'tenant': source['tenant'], 'principal': source['principal']}
    require(version_ref(plan) == value['pipeline']['plan_ref'], 'pipeline_plan_pin_mismatch')
    original = base64.b64decode(source['raw_b64'], validate=True)
    require(len(original) <= 32768 and content_ref(original) == source['raw_ref'], 'pipeline_raw_pin_mismatch')
    indexed = validate_table(original, SURVEY_TABLE)
    rows = [indexed[key] for key in sorted(indexed)]
    receipt = source['capture_receipt']
    require(version_ref(receipt) == source['capture_ref']
            and receipt['tenant'] == source['tenant'] and receipt['principal'] == source['principal']
            and receipt['source_id'] == source['source_id'] and source['schema_ref'] == version_ref(SURVEY_TABLE)
            and receipt['body']['source_ref'] == source['source_ref']
            and receipt['body']['dataset_ref'] == source['dataset_ref']
            and receipt['body']['dataset']['content_ref'] == source['raw_ref']
            and receipt['body']['dataset']['table_contract_ref'] == version_ref(SURVEY_TABLE)
            and version_ref(receipt['body']['dataset']) == source['dataset_ref'], 'pipeline_capture_lineage')
    snapshot = {'raw': original, 'receipt': receipt}
    checked, issues = _points(snapshot, source['survey_binding'])
    require(not issues and len(checked) == len(rows)
            and version_ref(source['survey_binding']) == source['survey_binding_ref'], 'pipeline_survey_semantics')
    config = source['analysis_binding']
    require(version_ref(config) == source['analysis_binding_ref']
            and config['source_id'] == source['source_id'] and config['capture_ref'] == source['capture_ref']
            and config['dataset_ref'] == source['dataset_ref'] and config['table'] == SURVEY_TABLE
            and config['units'] == {'height_m': 'm'}, 'pipeline_analysis_binding_mismatch')
    selected = [row for row in rows if row['role'] == 'observation']
    transform = value['transform']
    output_version = {'schema': 'odsp.pipeline-dataset-version.v1', 'plan_ref': value['pipeline']['plan_ref'],
        'input_dataset_ref': source['dataset_ref'], 'content_ref': content_ref(canonical(selected)),
        'table_contract_ref': version_ref(SURVEY_TABLE)}
    require(1 <= len(selected) <= 31 and transform == {'rows': selected,
        'content_ref': output_version['content_ref'], 'schema_ref': version_ref(SURVEY_TABLE),
        'version': output_version, 'dataset_ref': version_ref(output_version)}, 'pipeline_transform_lineage')
    analysis = value['analysis']
    core = {k: v for k, v in analysis.items() if k not in ('receipt_ref', 'retention')}
    require(analysis['receipt_ref'] == version_ref(core)
            and analysis['profile'] == 'ciw.data-analysis.v1'
            and analysis['operation'] == 'ncp_analysis_statistics'
            and analysis['arguments'] == {'dataset': 'survey', 'dataset_ref': source['dataset_ref'],
                'column': 'height_m', 'keys': [row['point_id'] for row in selected]}
            and analysis['implementation']['datasets'] == {'survey': source['analysis_binding_ref']}
            and analysis['implementation']['tenant'] == source['tenant']
            and analysis['implementation']['principal'] == source['principal'],
            'pipeline_analysis_lineage')
    result = analysis['value']
    runtime = value['pipeline']['implementation']
    require(result['runtime'] == runtime['numerical']
            and result['statistics_source_ref'] == runtime['source_refs']['adapters/oscillator.py']
            and analysis['implementation']['implementation_ref'] == runtime['source_refs']['data_analysis.py']
            and analysis['implementation']['interface_ref'] == runtime['analysis_interface_ref'],
            'pipeline_analysis_runtime_mismatch')
    require(result['row_keys'] == [r['point_id'] for r in selected], 'pipeline_analysis_selection')
    summary = result['statistics']
    heights = [r['height_m'] for r in selected]
    require(summary['sample_count'] == len(selected) and summary['unit'] == 'm', 'pipeline_analysis_count_or_unit')
    expected = {'mean': math.fsum(heights) / len(heights), 'minimum': min(heights),
                'maximum': max(heights), 'rms': math.sqrt(math.fsum(h*h for h in heights) / len(heights))}
    require(all(math.isclose(summary[k], v, rel_tol=1e-12, abs_tol=1e-12) for k, v in expected.items()),
            'pipeline_statistics_relationship')
    variance = math.fsum(r['height_standard_uncertainty_m']**2 for r in selected) / len(selected)**2
    mean_uncertainty = result['uncertainty']['mean']
    require(mean_uncertainty['status'] == 'declared_model_propagated'
            and math.isclose(mean_uncertainty['standard_uncertainty'], math.sqrt(variance), rel_tol=1e-12, abs_tol=1e-12)
            and math.isclose(result['propagation']['result']['covariance']['matrix'][0][0], variance,
                             rel_tol=1e-12, abs_tol=1e-12), 'pipeline_uncertainty_relationship')
    for field in ('minimum', 'maximum', 'rms'):
        require(result['uncertainty'][field]['status'] == 'not_qualified'
                and result['uncertainty'][field]['standard_uncertainty'] is None, 'pipeline_unsupported_uncertainty_claim')
    reproducible = {'plan_ref': value['pipeline']['plan_ref'], 'input_dataset_ref': source['dataset_ref'],
                    'transform': transform, 'analysis_value': result}
    require(value['reproducible_value'] == reproducible
            and value['reproducible_value_ref'] == version_ref(reproducible), 'pipeline_reproducible_value_pin')
    return deepcopy(value)


class SurveyAnalysisPipeline:
    """One fixed input/model/implementation and at most 16 durable attempts.

    Host-only arguments. admit(action, detached_intent, current_context) must
    return exact True anew. No saved grant or artifact supplies authority.
    An unknown attempt holds fresh work; inspection never resends it.
    """
    def __init__(self, directory, *, survey, capture_ref, model, expected_model_ref, admit):
        require(type(survey) is OfflineSurveyFlow and callable(admit), 'pipeline_host_binding_required')
        require(model == MODEL and version_ref(model) == expected_model_ref, 'pipeline_model_pin_or_version_refused')
        self.survey, self.policy, self.admit = survey, survey.ledger.policy, admit
        self.directory = Path(directory)
        require(self.directory.is_absolute() and self.directory.resolve() == self.directory
                and self.directory.parent.resolve() == self.directory.parent, 'pipeline_private_path_required')
        self.directory.mkdir(mode=0o700, exist_ok=True)
        info = self.directory.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                and not info.st_mode & 0o077, 'pipeline_private_directory_required')
        self._root = Path(os.environ['NCP_RETAIN_DIR']).resolve()
        self._objects = (survey, survey.ledger, survey.ledger.db, self.policy, admit, self.directory,
                         survey.ledger.tenant, survey.ledger.principal)
        self._model, self.model_ref = parse(canonical(model)), expected_model_ref
        self.capture_ref = capture_ref
        snapshot = survey.ledger.inspect_capture(survey.source_id, capture_ref)
        require(snapshot['receipt']['body']['source_ref'] == survey.source_ref, 'pipeline_source_version_changed')
        require(snapshot['receipt']['body']['dataset']['table_contract_ref'] == version_ref(SURVEY_TABLE),
                'pipeline_breaking_schema_refused')
        checked, issues = _points(snapshot, parse(survey._binding_raw))
        require(not issues and 2 <= len(checked) <= 32 and len(snapshot['raw']) <= 32768,
                'pipeline_capture_semantics_or_budget_refused')
        indexed = validate_table(snapshot['raw'], SURVEY_TABLE)
        rows = [indexed[key] for key in sorted(indexed)]
        self.binding = DatasetBinding(ledger=survey.ledger, source_id=survey.source_id,
            capture_ref=capture_ref, dataset_ref=snapshot['receipt']['body']['dataset_ref'],
            table=SURVEY_TABLE, units={'height_m': 'm'}, covariances={'height_m': covariance(rows,
                dataset_ref=snapshot['receipt']['body']['dataset_ref'], raw_ref=content_ref(snapshot['raw']),
                model_ref=self.model_ref)})
        self._implementation = implementation()
        self._plan = {'definition': definition(self.model_ref), 'implementation': self._implementation,
            'binding_ref': self.binding.ref, 'survey_binding_ref': survey.binding_ref,
            'tenant': survey.ledger.tenant, 'principal': survey.ledger.principal}
        self.plan_ref = version_ref(self._plan)
        self._request = {'plan_ref': self.plan_ref, 'capture_ref': capture_ref,
                         'dataset_ref': self.binding.value()['dataset_ref']}
        self._fixed = canonical([self._plan, self._request, str(self._root), str(self.directory)])
        self._guard('open')
        self.analysis = DataAnalysisHost(policy=self.policy, principal=survey.ledger.principal,
            tenant=survey.ledger.tenant, datasets={'survey': self.binding},
            admit=lambda intent, context: self._analysis_admission(intent, context))
        self.store = Store('domain-h-pipeline:' + version_ref(self._plan)[7:], evict=False)
        self.queue = StoreQueue(self.directory / 'queue', owner='domain-h-pipeline',
            principal=survey.ledger.principal, generation=self.plan_ref, operations=[OPERATION],
            authorize=self._queue_admission, limits=QueueLimits(max_jobs=16, max_pending=1, max_running=1,
                max_request_bytes=32768, max_result_bytes=4096, max_state_bytes=524288))
        self._runtime_objects = (self.binding, self.analysis, self.store, self.queue,
                                 self.store.owner, self.queue.directory, canonical(self.queue.identity))
        self._execution_checks = ContextVar('survey_pipeline_execution_checks', default=())

    def seal_distributed_execution(self, binding):
        """Host-only, create-only binding to one coordinator; never a grant.

        A sealed directory cannot run through the local route without the
        matching current execution scope, including after a process restart.
        Historical inspection still needs its unchanged current source grants.
        """
        reference('sha256:' + binding)
        path = self.directory / 'distributed-execution.json'
        value = {'schema': 'odsp.survey-distributed-binding.v1', 'binding': binding}
        lease = _Lease(self.directory / 'pipeline.lock')
        require(lease.try_acquire(), 'pipeline_owned_worker_busy')
        try:
            StoreQueue._regular(lease)
            if not os.path.lexists(path):
                # Do not adopt an earlier local completion or uncertain attempt
                # as a new coordinator execution. Reopening an existing exact
                # seal is different from sealing a used native directory.
                with self.queue._guard():
                    require(not self.queue._read()['jobs'], 'pipeline_used_local_queue_not_adoptable')
                save_new(path, value)
                fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            require(parse(_read_regular(path, byte_limit=1024)) == value,
                    'pipeline_distributed_binding_changed')
            self._distributed_seal = binding
        finally:
            lease.release()

    @contextmanager
    def execution_scope(self, binding, check):
        """Invocation-local host consistency check, independent of admission."""
        require(callable(check), 'pipeline_execution_check_required')
        reference('sha256:' + binding)
        token = self._execution_checks.set(self._execution_checks.get() + ((binding, check),))
        try:
            self._check_execution(required=True)
            yield self
            self._check_execution(required=True)
        finally:
            self._execution_checks.reset(token)

    def _check_execution(self, *, required=False):
        checks = self._execution_checks.get() if hasattr(self, '_execution_checks') else ()
        path = self.directory / 'distributed-execution.json'
        sealed = getattr(self, '_distributed_seal', None)
        if sealed is not None:
            require(parse(_read_regular(path, byte_limit=1024)) == {
                'schema': 'odsp.survey-distributed-binding.v1', 'binding': sealed},
                'pipeline_distributed_binding_changed')
        if required and (sealed is not None or os.path.lexists(path)):
            require(bool(checks), 'pipeline_distributed_claim_required')
        for binding, check in checks:
            require(parse(_read_regular(path, byte_limit=1024)) == {
                'schema': 'odsp.survey-distributed-binding.v1', 'binding': binding},
                'pipeline_distributed_binding_changed')
            require(check() is True, 'pipeline_distributed_claim_refused')

    def _check_bindings(self):
        actual = (self.survey, self.survey.ledger, self.survey.ledger.db, self.policy, self.admit,
                  self.directory, self.survey.ledger.tenant, self.survey.ledger.principal)
        require(actual == self._objects and self.survey.ledger.policy is self.policy
                and self.capture_ref == self._request['capture_ref']
                and version_ref(self._model) == self.model_ref
                and Path(os.environ['NCP_RETAIN_DIR']).resolve() == self._root
                and implementation() == self._implementation
                and canonical([self._plan, self._request, str(self._root), str(self.directory)]) == self._fixed,
                'pipeline_host_or_code_binding_changed')
        if hasattr(self, '_runtime_objects'):
            require((self.binding, self.analysis, self.store, self.queue, self.store.owner,
                     self.queue.directory, canonical(self.queue.identity)) == self._runtime_objects,
                    'pipeline_runtime_binding_changed')
        self.survey._check_binding()

    def _guard(self, action, context=None):
        self._check_execution()
        self._check_bindings()
        current = self.policy.context(self.survey.ledger.principal)
        if context is not None:
            self.policy.validate_context(context)
        required = {'statistics.v1', 'uncertainty.propagate.v1'}
        require(required <= set(self.policy.check_principal(self.survey.ledger.principal)['scientific_operations']),
                'pipeline_scientific_grant_required')
        intent = {'pipeline_id': PIPELINE_ID, 'plan_ref': self.plan_ref, **self._request,
                  'tenant': self.survey.ledger.tenant, 'principal': self.survey.ledger.principal}
        try:
            allowed = self.admit(action, deepcopy(intent), deepcopy(current)) is True
        except Exception:
            allowed = False
        require(allowed, 'pipeline_fresh_admission_required')
        self._check_bindings()
        self.policy.validate_context(current)
        self.binding.ready()
        snapshot = self.survey.ledger.inspect_capture(self.survey.source_id, self.capture_ref)
        _, issues = _points(snapshot, parse(self.survey._binding_raw))
        require(not issues, 'pipeline_capture_semantics_refused')
        self.policy.validate_context(current)
        self._check_bindings()
        self._check_execution()
        return current

    def _analysis_admission(self, intent, context):
        self._guard('analysis', context)
        return True

    def _queue_admission(self, principal, operation, arguments):
        require(principal == self.survey.ledger.principal and operation == OPERATION
                and arguments == self._request, 'pipeline_saved_request_mismatch')
        self._guard('queue')
        return True

    def _produce(self, job, operation, arguments, fence, context):
        require(operation == OPERATION and arguments == self._request, 'pipeline_dispatch_mismatch')
        self._guard('transform', context)
        snapshot = self.survey.ledger.inspect_capture(self.survey.source_id, self.capture_ref)
        rows = self.binding.ready()['rows']
        selected = select_rows(rows)
        self._guard('analysis', context)
        args = {'dataset': 'survey', 'dataset_ref': self._request['dataset_ref'], 'column': 'height_m',
                'keys': [row['point_id'] for row in selected]}
        response = self.analysis.call('ncp_analysis_statistics', args)
        require(response.get('status') == 'ok', 'pipeline_analysis_refused')
        self._guard('retain', context)
        output_version = {'schema': 'odsp.pipeline-dataset-version.v1', 'plan_ref': self.plan_ref,
            'input_dataset_ref': self._request['dataset_ref'], 'content_ref': content_ref(canonical(selected)),
            'table_contract_ref': version_ref(SURVEY_TABLE)}
        transform = {'rows': selected, 'content_ref': output_version['content_ref'],
            'schema_ref': version_ref(SURVEY_TABLE), 'version': output_version,
            'dataset_ref': version_ref(output_version)}
        reproducible = {'plan_ref': self.plan_ref, 'input_dataset_ref': self._request['dataset_ref'],
                        'transform': transform, 'analysis_value': response['data']['value']}
        artifact = {'schema': 'odsp.survey-analysis-artifact.v1', 'status': 'held',
            'review': {'status': 'pending_human_review', 'decision': None},
            'attempt': {'attempt': job['attempt'], 'job_id': job['job_id'],
                        'request_sha256': job['request_sha256'], 'fence': fence,
                        'admission': {'context_ref': version_ref(context),
                            'principal': self.survey.ledger.principal,
                            'required_scientific_operations': ['statistics.v1', 'uncertainty.propagate.v1'],
                            'scope': 'fresh_host_context_record_not_a_grant_or_nsl_admission'}},
            'pipeline': {**{k: self._plan[k] for k in ('definition', 'implementation')}, 'plan_ref': self.plan_ref},
            'input': {'tenant': self.survey.ledger.tenant, 'principal': self.survey.ledger.principal,
                'source_id': self.survey.source_id, 'source_ref': self.survey.source_ref,
                'capture_ref': self.capture_ref, 'capture_receipt': snapshot['receipt'],
                'dataset_ref': self._request['dataset_ref'], 'raw_ref': content_ref(snapshot['raw']),
                'raw_b64': base64.b64encode(snapshot['raw']).decode(), 'schema_ref': version_ref(SURVEY_TABLE),
                'analysis_binding_ref': self.binding.ref, 'survey_binding_ref': self.survey.binding_ref,
                'analysis_binding': self.binding.value(),
                'survey_binding': parse(self.survey._binding_raw), 'model': self._model, 'model_ref': self.model_ref},
            'transform': transform, 'analysis': response['data'], 'reproducible_value': reproducible,
            'reproducible_value_ref': version_ref(reproducible), 'claims': deepcopy(CLAIMS)}
        artifact_ref = content_ref(canonical(artifact))
        validate_artifact(artifact, expected_ref=artifact_ref)
        self._guard('retain', context)
        artifact_id = self.store.keep(artifact, kind='domain-h-held-analysis',
            catalog={'schema': artifact['schema'], 'status': 'held', 'plan_ref': self.plan_ref,
                     'artifact_ref': artifact_ref, 'reproducible_value_ref': artifact['reproducible_value_ref']})
        self._guard('complete', context)
        return {'artifact_id': artifact_id, 'artifact_ref': artifact_ref,
                'reproducible_value_ref': artifact['reproducible_value_ref'], 'status': 'held'}

    def _inspect_job(self, job, context):
        result = {'status': job['status'], 'job_id': job['job_id'], 'attempt': job['attempt'],
                  'automatic_retry': False, 'claims': deepcopy(CLAIMS)}
        if job['status'] == 'completed':
            artifact = self.store.native(job['result']['artifact_id'])
            validate_artifact(artifact, expected_ref=job['result']['artifact_ref'])
            require(artifact['pipeline']['plan_ref'] == self.plan_ref and artifact['attempt']['job_id'] == job['job_id']
                    and artifact['attempt']['request_sha256'] == job['request_sha256'], 'pipeline_retention_binding_changed')
            result.update(status='held', **{k: job['result'][k] for k in ('artifact_id', 'artifact_ref', 'reproducible_value_ref')},
                          artifact=artifact)
        self._guard('disclose', context)
        return result

    def inspect(self, attempt):
        context = self._guard('read')
        # Queue validates the identifier and derives the immutable attempt id.
        self.queue._request(OPERATION, self._request, attempt)
        return self._inspect_job(self.queue.inspect(self.queue._job_id(attempt)), context)

    def run(self, attempt):
        self._check_execution(required=True)
        context = self._guard('run')
        self.queue._request(OPERATION, self._request, attempt)
        lease = _Lease(self.directory / 'pipeline.lock')
        require(lease.try_acquire(), 'pipeline_owned_worker_busy')
        try:
            StoreQueue._regular(lease)
            # A previously unsealed run may have waited while a coordinator
            # acquired the lease and sealed this directory. Recheck inside it.
            self._check_execution(required=True)
            self.queue.recover()  # Lost worker -> unknown; never resumes a calculation.
            with self.queue._guard():
                jobs = self.queue._read()['jobs']
            job_id = self.queue._job_id(attempt)
            prior = next((job for job in jobs if job['job_id'] == job_id), None)
            if prior is not None and prior['status'] in ('completed', 'refused', 'outcome_unknown'):
                return self._inspect_job(self.queue.inspect(job_id), context)
            require(not any(job['status'] == 'outcome_unknown' for job in jobs), 'pipeline_unknown_outcome_review_required')
            job = self.queue.enqueue(OPERATION, self._request, attempt)
            self._guard('dispatch', context)
            try:
                settled = self.queue.execute_one('fixed-pipeline-host',
                    lambda op, args, fence: self._produce(job, op, args, fence, context))
            except Exception:
                raise PipelineOutcomeUnknown(job['job_id']) from None
            require(settled is not None and settled['job_id'] == job['job_id'], 'pipeline_custody_unavailable')
            return self._inspect_job(self.queue.inspect(job['job_id']), context)
        finally:
            lease.release()
