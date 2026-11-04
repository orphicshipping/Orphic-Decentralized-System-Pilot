"""One fixed canonical survey pipeline under PostgreSQL/NATS custody.

Host-only composition, never a generic SQL/workflow/provider dispatcher. Current
principal, scientific, source and both host admissions stay authoritative. A
lease can withhold the PG result; it cannot roll back local evidence writes.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock

from .audited_host import AuditedPrincipalHost
from .campaign_contracts import canonical
from .distributed_queue import PostgresQueue, QueueLimits, QueueRefusal, _connection, _FORMAT
from .distributed_services import DistributedServices, _refuse
from .principal_policy import PrincipalFacade, PrincipalOutcomeUnknown, _specs
from .survey_analysis_pipeline import CLAIMS, SurveyAnalysisPipeline
from .survey_pipeline_profile import SurveyPipelineHost, TOOLS

RUN, INSPECT = 'ncp_pipeline_run', 'ncp_pipeline_inspect'


class _SurveyQueue(PostgresQueue):
    def __init__(self, *args, adapter, **kwargs):
        self._adapter = adapter
        super().__init__(*args, **kwargs)

    def _authorize(self, row):
        super()._authorize(row)
        request = row.get('decoded', row)
        _refuse(request['attempt'] == request['arguments']['attempt'])
        if row.get('status') in (None, 'queued'):
            # During claim this runs while the queue row is locked. An unknown
            # earlier occurrence holds queued work as well as new insertion.
            self._adapter._unknown_hold(request['attempt'])

    def _authorize_completion(self, row, result):
        super()._authorize_completion(row, result)
        self._adapter._authorize_result(result, row)

    def _public(self, row, *, replayed=False):
        if row.get('result') is not None:
            self._adapter._authorize_result(json.loads(bytes(row['result'])), row)
        return super()._public(row, replayed=replayed)


class DistributedSurveyPipeline(DistributedServices):
    """One audited source host, fixed plan and durable coordinator namespace.

    Requests contain only the already bound plan and an attempt identifier.
    Same-attempt replay discloses an existing PG outcome under fresh read grants;
    no result or permission is taken from a broker notification. An unknown PG
    occurrence holds fresh execution in this namespace. Native local unknown
    and audit holds independently remain in force.
    """
    def __init__(self, dsn, *, host, generation, lease_seconds=15):
        _refuse(type(host) is AuditedPrincipalHost)
        facade = host._check_identity()
        _refuse(type(facade) is PrincipalFacade)
        family, profile = facade._host(RUN)
        _refuse(family == 'pipeline' and type(profile) is SurveyPipelineHost
                and type(profile.pipeline) is SurveyAnalysisPipeline
                and {RUN, INSPECT} <= host._audit.chain.allowed)
        self.host = self._host = host
        self._facade, self._policy, self._principal = facade, facade.policy, facade.principal
        self._audit, self._chain = host._audit, host._audit.chain
        self._hosts, self._scope = dict(facade.hosts), self._host_scope()
        self._profile, self._pipeline = profile, profile.pipeline
        self._plan_ref = self._pipeline.plan_ref
        self._code_ref = 'sha256:' + sha256(Path(__file__).read_bytes()).hexdigest()
        self._operations = frozenset({RUN})
        self._specification = {name: canonical(_specs()[name]) for name in (RUN, INSPECT)}
        self._identity_refused = False
        self._authorizer = self._authorize
        self._renewal_checks, self._checks_lock = {}, Lock()
        self._authorize_identity()
        namespace = 'survey-' + sha256(canonical([self._scope.decode(), self._plan_ref])).hexdigest()
        # Fixed limits match the native attempt bound. Two queued notifications
        # may wait; only one worker can own execution at a time.
        self.queue = _SurveyQueue(dsn, adapter=self, owner=namespace, principal=self._principal,
            generation=generation, operations=self._operations, authorize=self._authorizer,
            limits=QueueLimits(max_jobs=16, max_pending=16, max_running=1,
                max_request_bytes=32768, max_result_bytes=16384, max_state_bytes=1048576),
            lease_seconds=lease_seconds)
        self._queue, self._queue_identity = self.queue, self.queue.identity
        self._binding = sha256(canonical({'queue': self.queue.identity,
            'plan_ref': self._plan_ref, 'adapter_ref': self._code_ref})).hexdigest()
        self._pipeline.seal_distributed_execution(self._binding)
        self._authorize_identity()

    def _authorize_identity(self):
        super()._authorize_identity()
        try:
            _refuse(self._facade.hosts['pipeline'] is self._profile
                and self._profile.pipeline is self._pipeline and self._pipeline.plan_ref == self._plan_ref
                and type(self._profile) is SurveyPipelineHost and type(self._pipeline) is SurveyAnalysisPipeline
                and 'sha256:' + sha256(Path(__file__).read_bytes()).hexdigest() == self._code_ref)
            self._profile._check_bindings()
        except Exception:
            self._identity_refused = True
            raise
        return True

    def _authorize(self, principal, operation, arguments):
        self._authorize_identity()
        _refuse(principal == self._principal and operation == RUN
                and all(canonical(_specs()[name]) == spec for name, spec in self._specification.items()))
        # Canonical synchronous calls hold the policy mutex through computation.
        # A guard constructed BEFORE dispatch already pins one coherent context
        # and revalidates live policy, both admissions and source holds on EVERY
        # call. Renewals reuse that check, never a cached permission decision or
        # the mutex needed by the foreground call. A changed context refuses.
        key = canonical(arguments)
        with self._checks_lock:
            check = self._renewal_checks.get(key)
        if check is None:
            check = self._profile.request_scope_guard(operation, arguments)
        check()
        return True

    def _unknown_hold(self, attempt):
        with _connection(self.queue._dsn) as db:
            row = db.execute("SELECT count(*) AS n FROM odsp_distributed_v1.jobs WHERE queue_id=%s "
                "AND status='outcome_unknown' AND attempt<>%s", (self.queue.queue_id, attempt)).fetchone()
        if row['n']:
            raise QueueRefusal('distributed_survey_unknown_outcome_review_required')

    def enqueue(self, *, attempt):
        return self.queue.enqueue(RUN, {'plan_ref': self._plan_ref, 'attempt': attempt}, attempt)

    def _claim_guard(self, arguments, claim):
        # Nonlocking MVCC lease check avoids lock inversion with heartbeats.
        # Deliberately does not call native admission: it is also called FROM
        # that admission boundary. Current authority is checked independently.
        self._authorize_identity()
        self.queue._owned(claim)
        _refuse(arguments == claim._row['decoded']['arguments']
                and arguments['attempt'] == claim._row['attempt'])
        with _connection(self.queue._dsn) as db:
            row = db.execute("SELECT j.status,j.fence,j.claim_id,j.worker,j.request,j.request_sha256,"
                "j.lease_until>clock_timestamp() AS active,q.identity,q.limits,f.digest "
                "FROM odsp_distributed_v1.jobs j JOIN odsp_distributed_v1.queues q ON q.queue_id=j.queue_id "
                "CROSS JOIN odsp_distributed_v1.format f WHERE j.queue_id=%s AND j.job_id=%s AND f.singleton",
                (self.queue.queue_id, claim.fence.job_id)).fetchone()
        if (row is None or row['status'] != 'dispatching' or row['active'] is not True
                or row['digest'] != _FORMAT or bytes(row['identity']) != self.queue._identity
                or bytes(row['limits']) != self.queue._limits
                or row['fence'] != claim.fence.number or row['claim_id'] != claim._row['claim_id']
                or row['worker'] != claim._row['worker']
                or row['request_sha256'] != claim.fence.request_sha256
                or sha256(bytes(row['request'])).hexdigest() != claim.fence.request_sha256):
            raise QueueRefusal('distributed_survey_claim_no_longer_current')
        return True

    def _scope_guard(self, operation, arguments, claim):
        self._claim_guard(arguments, claim)
        self._authorize(self._principal, operation, arguments)
        self._unknown_hold(arguments['attempt'])
        return self._claim_guard(arguments, claim)

    def _authorize_result(self, value, row):
        self._authorize_identity()
        _refuse(type(value) is dict and set(value) == {
            'schema', 'queue_id', 'plan_ref', 'adapter_ref', 'fence', 'service_result', 'claims'}
            and value['schema'] == 'odsp.distributed-survey-result.v1'
            and value['queue_id'] == self.queue.queue_id and value['plan_ref'] == self._plan_ref
            and value['adapter_ref'] == self._code_ref and value['claims'] == CLAIMS)
        fence = value['fence']
        _refuse(type(fence) is dict and fence == {
            'queue_id': self.queue.queue_id, 'job_id': row['job_id'],
            'request_sha256': row['request_sha256'], 'number': row['fence'],
            'generation': self.queue.identity['generation']})
        request = row['decoded']['arguments']
        self._profile.request_scope_guard(INSPECT, request)()
        actual = self._profile._summary(self._pipeline.inspect(request['attempt']))
        actual['service'] = {'profile_id': 'ciw.survey-pipeline.v1',
            'implementation_ref': self._profile._code_ref, 'interface_ref': self._profile._interface_ref}
        _refuse(actual['status'] == 'held' and value['service_result'] == actual)
        return True

    def _dispatch(self, operation, arguments, claim):
        from .services import invoke
        check = self._profile.request_scope_guard(operation, arguments)
        key = canonical(arguments)
        with self._checks_lock:
            _refuse(key not in self._renewal_checks)
            self._renewal_checks[key] = check
        try:
            self._scope_guard(operation, arguments, claim)
            guard = lambda: self._claim_guard(arguments, claim)
            with self._pipeline.execution_scope(self._binding, guard), \
                    self._host.scope_guard(lambda: self._scope_guard(operation, arguments, claim)):
                result = invoke(operation, arguments, host=self._host)
                if result.get('status') != 'held':
                    raise PrincipalOutcomeUnknown()
                value = {'schema': 'odsp.distributed-survey-result.v1', 'queue_id': self.queue.queue_id,
                    'plan_ref': self._plan_ref, 'adapter_ref': self._code_ref,
                    'fence': vars(claim.fence), 'service_result': result, 'claims': dict(CLAIMS)}
                self._authorize_result(value, claim._row)
                self._scope_guard(operation, arguments, claim)
            return value
        finally:
            with self._checks_lock:
                self._renewal_checks.pop(key, None)
