"""Canonical audited services composed with PostgreSQL/NATS custody.

SPDX-License-Identifier: AGPL-3.0-or-later
Trusted launcher API. No MCP tool, connection, credential or grant is created.
Only explicitly reviewed read operations are supported. No canonical mutation
adapter has been qualified for downstream fencing, so mutations always refuse
before queue insertion or dispatch intent. A metadata readOnlyHint is not enough.
"""
from hashlib import sha256

from .audited_host import AuditedPrincipalHost
from .campaign_contracts import canonical
from .distributed_nats import JetStreamBridge
from .distributed_queue import PostgresQueue, _connection, _FORMAT
from .principal_policy import PrincipalFacade, PrincipalRefusal, _specs, owner_for
from .store_queue import QueueRefusal


# This fixed adapter contains no generic caller callback or provider escape.
# Extending it requires source review and actual downstream qualification.
READ_OPERATIONS = frozenset({
    "ncp_capabilities_list", "ncp_plan_template",
    "ncp_knowledge_overview", "ncp_knowledge_source", "ncp_knowledge_search",
    "ncp_knowledge_resolve", "ncp_evidence_search", "ncp_evidence_span",
    "ncp_domain_read_describe", "ncp_domain_read_list", "ncp_domain_read_record",
    "ncp_domain_read_verify",
})


def _refuse(condition):
    if not condition:
        raise PrincipalRefusal()


class DistributedServices:
    """Bind one exact audited principal and immutable service/queue namespace.

    Current authorization is re-evaluated on enqueue, claim, heartbeat, intent,
    canonical invocation and result disclosure. Queue recovery never retries an
    unknown occurrence. Queue outcomes and the host's independent audit remain
    separate durable records; neither substitutes for the other.
    """
    def __init__(self, dsn, *, host, generation, operations, limits=None, lease_seconds=15):
        _refuse(type(host) is AuditedPrincipalHost)
        facade = host._check_identity()
        _refuse(type(facade) is PrincipalFacade)
        _refuse(type(operations) in (tuple, list, frozenset, set)
                and 0 < len(operations) <= len(READ_OPERATIONS)
                and all(type(name) is str for name in operations))
        selected = frozenset(operations)
        _refuse(len(selected) == len(operations) and selected <= READ_OPERATIONS)
        specs = _specs()
        _refuse(selected <= host._audit.chain.allowed)
        for name in selected:
            _refuse(name in specs and specs[name].get("annotations", {}).get("readOnlyHint") is True)
            facade._host(name)  # Every selected operation has an existing bound provider.
        self.host = self._host = host
        self._facade, self._policy, self._principal = facade, facade.policy, facade.principal
        self._audit, self._chain = host._audit, host._audit.chain
        self._hosts = dict(facade.hosts)
        self._scope = self._host_scope()
        self._operations = selected
        self._specification = {name: canonical(specs[name]) for name in selected}
        self._identity_refused = False
        self._authorizer = self._authorize
        self._authorize_identity()
        self.queue = PostgresQueue(dsn, owner=owner_for(self._principal), principal=self._principal,
                                   generation=generation, operations=selected, authorize=self._authorizer,
                                   limits=limits, lease_seconds=lease_seconds)
        self._queue = self.queue
        self._queue_identity = self.queue.identity

    def _host_scope(self):
        return canonical({family: {name: getattr(raw, name) for name in (
            "_bound_principal", "_bound_tenant", "corpus_ref", "evidence_ref") if hasattr(raw, name)}
            for family, raw in self._facade.hosts.items() if raw is not None})

    def _authorize_identity(self):
        try:
            _refuse(not self._identity_refused and self.host is self._host
                    and self._host._check_identity() is self._facade
                    and self._facade.policy is self._policy and self._facade.principal == self._principal
                    and self._host._audit is self._audit and self._audit.chain is self._chain
                    and self._operations <= self._chain.allowed
                    and set(self._facade.hosts) == set(self._hosts)
                    and all(self._facade.hosts[key] is value for key, value in self._hosts.items())
                    and self._host_scope() == self._scope)
            if hasattr(self, "_queue"):
                _refuse(self.queue is self._queue and self.queue.identity == self._queue_identity
                        and self.queue.operations == self._operations
                        and self.queue.authorize is self._authorizer)
        except Exception:
            self._identity_refused = True
            raise PrincipalRefusal() from None
        return True

    def _authorize(self, principal, operation, arguments):
        self._authorize_identity()
        _refuse(principal == self._principal and operation in self._operations)
        with self._policy.lock:
            self._authorize_identity()
            _refuse(canonical(_specs()[operation]) == self._specification[operation])
            self._policy.authorize(self._principal, operation, arguments)
        return True

    def enqueue(self, operation, arguments, *, attempt):
        self._authorize_identity()
        # Unsupported mutations refuse here even if the principal has a grant.
        _refuse(operation in self._operations)
        return self.queue.enqueue(operation, arguments, attempt)

    def inspect(self, job_id):
        self._authorize_identity()
        return self.queue.inspect(job_id)

    def history(self, job_id):
        self._authorize_identity()
        return self.queue.history(job_id)

    def _scope_guard(self, operation, arguments, claim):
        self._authorize(self._principal, operation, arguments)
        self.queue._owned(claim)
        # A nonlocking MVCC read is intentional: this check runs inside the
        # canonical policy lock after audit I/O. Heartbeats take the queue lock
        # before that policy lock. Taking a queue/job lock here would invert the
        # order and could turn a safe read into a lease timeout/deadlock.
        with _connection(self.queue._dsn) as db:
            row = db.execute("SELECT j.status,j.fence,j.claim_id,j.worker,j.request,j.request_sha256,"
                             "j.lease_until>clock_timestamp() AS active,q.identity,f.digest "
                             "FROM odsp_distributed_v1.jobs j "
                             "JOIN odsp_distributed_v1.queues q ON q.queue_id=j.queue_id "
                             "CROSS JOIN odsp_distributed_v1.format f "
                             "WHERE j.queue_id=%s AND j.job_id=%s AND f.singleton",
                             (self.queue.queue_id, claim.fence.job_id)).fetchone()
        if (row is None or row["digest"] != _FORMAT or bytes(row["identity"]) != self.queue._identity
                or row["status"] != "dispatching" or row["active"] is not True
                or row["fence"] != claim.fence.number or row["claim_id"] != claim._row["claim_id"]
                or row["worker"] != claim._row["worker"]
                or row["request_sha256"] != claim.fence.request_sha256
                or sha256(bytes(row["request"])).hexdigest() != claim.fence.request_sha256):
            raise QueueRefusal("distributed_service_claim_no_longer_current")
        self._authorize(self._principal, operation, arguments)
        return True

    def _dispatch(self, operation, arguments, claim):
        from .services import invoke
        self._scope_guard(operation, arguments, claim)
        with self._host.scope_guard(lambda: self._scope_guard(operation, arguments, claim)):
            value = invoke(operation, arguments, host=self._host)
        self._scope_guard(operation, arguments, claim)
        return value

    def execute_one(self, worker):
        self._authorize_identity()
        return self.queue.execute_one(worker, self._dispatch)

    def bridge(self, jetstream, *, stream, durable, replicas=1, ack_wait=30):
        """Bind an existing authenticated context; provisioning remains explicit."""
        self._authorize_identity()
        return JetStreamBridge(self.queue, jetstream, stream=stream, durable=durable,
                               replicas=replicas, ack_wait=ack_wait)

    async def cycle(self, bridge, worker, *, timeout=1):
        self._authorize_identity()
        _refuse(type(bridge) is JetStreamBridge and bridge.queue is self.queue)
        return await bridge.cycle(worker, self._dispatch, timeout=timeout)
