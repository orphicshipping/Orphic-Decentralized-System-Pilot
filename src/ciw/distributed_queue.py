"""PostgreSQL job custody, renewable leases and durable dispatch boundaries.

SPDX-License-Identifier: AGPL-3.0-or-later
Trusted host API only. A lease is coordination, never principal authorization.
No network service, database, credential or grant is provisioned on import.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
import re
from threading import Event, Lock, Thread
from uuid import uuid4

from .store_queue import QueueLimits, QueueRefusal, _identifier, _wire

_SAFE = 2**53 - 1
_TERMINAL = frozenset({"completed", "refused", "outcome_unknown"})
_JOB = re.compile(r"job-[0-9a-f]{64}\Z")

# One explicit host migration. SQL identifiers and statements are fixed, not
# selected by queued arguments. psycopg binds every data value separately.
_DDL = """
CREATE SCHEMA IF NOT EXISTS odsp_distributed_v1;
CREATE TABLE IF NOT EXISTS odsp_distributed_v1.format (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), digest text NOT NULL);
CREATE TABLE IF NOT EXISTS odsp_distributed_v1.queues (
 queue_id text PRIMARY KEY, identity bytea NOT NULL CHECK(octet_length(identity)<=2048),
 limits bytea NOT NULL CHECK(octet_length(limits)<=2048),
 next_fence bigint NOT NULL DEFAULT 0 CHECK(next_fence BETWEEN 0 AND 9007199254740991));
CREATE TABLE IF NOT EXISTS odsp_distributed_v1.jobs (
 queue_id text NOT NULL REFERENCES odsp_distributed_v1.queues(queue_id),
 job_id text NOT NULL, attempt text NOT NULL, request bytea NOT NULL CHECK(octet_length(request) BETWEEN 1 AND 65536),
 request_sha256 text NOT NULL, reservation integer NOT NULL CHECK(reservation > 0),
 status text NOT NULL CHECK(status IN ('queued','claimed','dispatching','completed','refused','outcome_unknown')),
 fence bigint NOT NULL DEFAULT 0 CHECK(fence BETWEEN 0 AND 9007199254740991),
 worker text, claim_id uuid, lease_until timestamptz, dispatch_intent bytea CHECK(octet_length(dispatch_intent)<=2048),
 result bytea CHECK(octet_length(result) BETWEEN 1 AND 65536), error text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(queue_id,job_id), UNIQUE(queue_id,attempt),
 CHECK((status='queued' AND fence=0 AND worker IS NULL AND claim_id IS NULL AND lease_until IS NULL)
    OR (status<>'queued' AND fence>0 AND worker IS NOT NULL AND claim_id IS NOT NULL)),
 CHECK((status='completed' AND result IS NOT NULL AND error IS NULL AND dispatch_intent IS NOT NULL)
    OR (status<>'completed' AND result IS NULL)),
 CHECK(status<>'dispatching' OR dispatch_intent IS NOT NULL));
CREATE INDEX IF NOT EXISTS jobs_claimable ON odsp_distributed_v1.jobs(queue_id,status,created_at,job_id);
CREATE TABLE IF NOT EXISTS odsp_distributed_v1.events (
 queue_id text NOT NULL, job_id text NOT NULL, ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 4),
 event bytea NOT NULL CHECK(octet_length(event) BETWEEN 1 AND 2048), event_sha256 text NOT NULL,
 PRIMARY KEY(queue_id,job_id,ordinal),
 FOREIGN KEY(queue_id,job_id) REFERENCES odsp_distributed_v1.jobs(queue_id,job_id));
CREATE TABLE IF NOT EXISTS odsp_distributed_v1.outbox (
 queue_id text NOT NULL, job_id text NOT NULL, event_id text NOT NULL UNIQUE,
 payload bytea NOT NULL CHECK(octet_length(payload) BETWEEN 1 AND 1024), published boolean NOT NULL DEFAULT false,
 stream text, stream_sequence bigint,
 PRIMARY KEY(queue_id,job_id),
 FOREIGN KEY(queue_id,job_id) REFERENCES odsp_distributed_v1.jobs(queue_id,job_id));
"""
_FORMAT = sha256(_DDL.encode()).hexdigest()


def _require(condition, reason):
    if not condition:
        raise QueueRefusal(reason)


def _canonical(value, maximum=65536):
    return _wire(value, maximum)


@contextmanager
def _connection(dsn):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("distributed_psycopg_dependency_required") from exc
    # Never log DSN or exception details at an agent boundary: libpq errors may
    # identify a private endpoint. All values are supplied by the host launcher.
    # Transaction poolers may reject libpq startup options and assign a different
    # backend after every commit. Keep all runtime state in this transaction and
    # require no prepared-statement/session tracking from the proxy. Explicit
    # transactions also prevent a host callback from committing custody early.
    with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row,
                         prepare_threshold=None) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE")
            conn.execute("SET LOCAL statement_timeout=5000; SET LOCAL lock_timeout=2000; "
                         "SET LOCAL idle_in_transaction_session_timeout=5000; "
                         "SET LOCAL synchronous_commit=on; SET LOCAL search_path=pg_catalog",
                         prepare=False)
            settings = conn.execute(
                "SELECT current_setting('statement_timeout') AS statement_timeout, "
                "current_setting('lock_timeout') AS lock_timeout, "
                "current_setting('idle_in_transaction_session_timeout') AS idle_timeout, "
                "current_setting('synchronous_commit') AS synchronous_commit, "
                "current_setting('search_path') AS search_path, "
                "current_setting('transaction_isolation') AS isolation, "
                "current_setting('transaction_read_only') AS read_only").fetchone()
            _require(settings == {"statement_timeout": "5s", "lock_timeout": "2s",
                                  "idle_timeout": "5s", "synchronous_commit": "on",
                                  "search_path": "pg_catalog", "isolation": "read committed",
                                  "read_only": "off"}, "distributed_transaction_settings")
            yield conn


def install_schema(dsn: str) -> None:
    """Explicit trusted provisioning on an already authorized database.

    The deployment role needs CREATE only for this action. Runtime roles need
    SELECT/INSERT/UPDATE on the four custody tables and INSERT on events; they
    do not need superuser, database creation, or schema ownership.
    """
    with _connection(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(678044119137)")
        _require(conn.info.server_version >= 140000, "distributed_postgresql_version")
        for setting in ("fsync", "full_page_writes"):
            value = conn.execute("SELECT current_setting(%s) AS value", (setting,)).fetchone()["value"]
            _require(value == "on", "distributed_durability_setting")
        conn.execute(_DDL, prepare=False)
        conn.execute("INSERT INTO odsp_distributed_v1.format(singleton,digest) VALUES(true,%s) "
                     "ON CONFLICT DO NOTHING", (_FORMAT,))
        value = conn.execute("SELECT digest FROM odsp_distributed_v1.format").fetchone()
        _require(value["digest"] == _FORMAT, "distributed_schema_version_mismatch")


@dataclass(frozen=True)
class Fence:
    """Detached identity for a downstream fence-aware adapter, not a grant."""
    queue_id: str
    job_id: str
    request_sha256: str
    generation: str
    number: int


class Claim:
    """A process-owned receipt for one committed database claim."""
    def __init__(self, queue, row):
        self._queue, self._pid, self._row = queue, os.getpid(), deepcopy(row)

    @property
    def fence(self):
        row = self._row
        return Fence(self._queue.queue_id, row["job_id"], row["request_sha256"],
                     self._queue.identity["generation"], row["fence"])

    @property
    def job(self):
        return self._queue._public(self._row)


class PostgresQueue:
    """One immutable host-owned principal/generation namespace.

    Authorization returns exactly True or None on success and must check the
    canonical current principal/tool/scientific/resource policy. It is called
    again immediately before durable dispatch intent. Dispatcher code must use
    the canonical service boundary, and external effects need downstream fencing.
    """
    def __init__(self, dsn, *, owner, principal, generation, operations, authorize,
                 limits=None, lease_seconds=15):
        _require(type(dsn) is str and 0 < len(dsn) <= 4096, "distributed_dsn_required")
        for value in (owner, principal, generation):
            _identifier(value)
        _require(type(operations) in (tuple, list, frozenset, set) and 0 < len(operations) <= 128,
                 "distributed_operations_required")
        for operation in operations:
            _identifier(operation)
        _require(callable(authorize), "distributed_authorizer_required")
        _require(type(lease_seconds) in (int, float) and math.isfinite(lease_seconds)
                 and 0.2 <= lease_seconds <= 300, "distributed_lease_bounds")
        self._dsn = dsn
        self._identity_document = {"owner": owner, "principal": principal, "generation": generation}
        self._identity = _canonical(self._identity_document)
        self.queue_id = "q-" + sha256(self._identity).hexdigest()
        self.operations, self.authorize = frozenset(operations), authorize
        self.limits, self.lease_seconds = limits or QueueLimits(), float(lease_seconds)
        _require(type(self.limits) is QueueLimits, "distributed_limits_invalid")
        self._limits = _canonical(self.limits.persisted())
        self._claims, self._local_lock = {}, Lock()
        with self._transaction() as conn:
            conn.execute("INSERT INTO odsp_distributed_v1.queues(queue_id,identity,limits) VALUES(%s,%s,%s) "
                         "ON CONFLICT DO NOTHING", (self.queue_id, self._identity, self._limits))
            self._queue_row(conn)

    @property
    def identity(self):
        return deepcopy(self._identity_document)

    @contextmanager
    def _transaction(self):
        with _connection(self._dsn) as conn:
            version = conn.execute("SELECT digest FROM odsp_distributed_v1.format").fetchone()
            _require(version is not None and version["digest"] == _FORMAT,
                     "distributed_schema_version_mismatch")
            yield conn

    def _queue_row(self, conn, *, lock=False):
        # All state transitions take this row before the job. It serializes
        # capacity and fencing without a global database/advisory runtime lock.
        sql = "SELECT * FROM odsp_distributed_v1.queues WHERE queue_id=%s"
        row = conn.execute(sql + (" FOR UPDATE" if lock else ""), (self.queue_id,)).fetchone()
        _require(row is not None and bytes(row["identity"]) == self._identity
                 and bytes(row["limits"]) == self._limits, "distributed_queue_binding_mismatch")
        return row

    def _job(self, conn, job_id, *, lock=False):
        _require(type(job_id) is str and _JOB.fullmatch(job_id), "distributed_job_invalid")
        row = conn.execute("SELECT * FROM odsp_distributed_v1.jobs WHERE queue_id=%s AND job_id=%s"
                           + (" FOR UPDATE" if lock else ""), (self.queue_id, job_id)).fetchone()
        _require(row is not None, "distributed_job_unknown")
        raw = bytes(row["request"])
        request = json.loads(raw)
        _require(type(request) is dict and set(request) == {"identity", "operation", "arguments", "attempt"}
                 and type(request["arguments"]) is dict
                 and sha256(raw).hexdigest() == row["request_sha256"]
                 and _canonical(request, self.limits.max_request_bytes) == raw
                 and request["identity"] == self.identity and request["attempt"] == row["attempt"]
                 and row["job_id"] == "job-" + sha256(_canonical([self.identity, row["attempt"]])).hexdigest(),
                 "distributed_request_integrity")
        if row["result"] is not None:
            result = json.loads(bytes(row["result"]))
            _require(type(result) is dict and _canonical(result, self.limits.max_result_bytes) == bytes(row["result"]),
                     "distributed_result_integrity")
        row["decoded"] = request
        return row

    def _authorize(self, row):
        request = row.get("decoded", row)
        _require(request["operation"] in self.operations, "distributed_operation_refused")
        answer = self.authorize(self.identity["principal"], request["operation"], deepcopy(request["arguments"]))
        _require(answer is None or answer is True, "distributed_authorization_refused")

    def _public(self, row, *, replayed=False):
        return {"schema": "ciw.distributed-job.v1", "queue_id": self.queue_id,
                "job_id": row["job_id"], "attempt": row["attempt"],
                "request_sha256": row["request_sha256"], "status": row["status"],
                "fence": row["fence"], "worker": row["worker"],
                "result": json.loads(bytes(row["result"])) if row["result"] is not None else None,
                "error": row["error"], "replayed": replayed, "authorizes_execution": False}

    def _event(self, conn, row, state):
        previous = conn.execute("SELECT ordinal,event_sha256 FROM odsp_distributed_v1.events "
                                "WHERE queue_id=%s AND job_id=%s ORDER BY ordinal DESC LIMIT 1",
                                (self.queue_id, row["job_id"])).fetchone()
        ordinal = previous["ordinal"] + 1 if previous else 1
        payload = {"schema": "ciw.distributed-transition.v1", "queue_id": self.queue_id,
                   "job_id": row["job_id"], "request_sha256": row["request_sha256"],
                   "ordinal": ordinal, "previous_sha256": previous["event_sha256"] if previous else None,
                   "state": state, "fence": row["fence"],
                   "result_sha256": sha256(bytes(row["result"])).hexdigest() if row["result"] else None,
                   "intent_sha256": sha256(bytes(row["dispatch_intent"])).hexdigest() if row["dispatch_intent"] else None,
                   "error": row["error"], "authorizes_execution": False}
        raw = _canonical(payload, 2048)
        conn.execute("INSERT INTO odsp_distributed_v1.events VALUES(%s,%s,%s,%s,%s)",
                     (self.queue_id, row["job_id"], ordinal, raw, sha256(raw).hexdigest()))

    def enqueue(self, operation, arguments, attempt):
        _identifier(operation); _identifier(attempt)
        _require(type(arguments) is dict, "distributed_arguments_invalid")
        request = {"identity": self.identity, "operation": operation, "arguments": arguments, "attempt": attempt}
        raw = _canonical(request, self.limits.max_request_bytes)
        request = json.loads(raw)
        digest = sha256(raw).hexdigest()
        job_id = "job-" + sha256(_canonical([self.identity, attempt])).hexdigest()
        self._authorize(request)
        with self._transaction() as conn:
            self._queue_row(conn, lock=True)
            existing = conn.execute("SELECT job_id,request_sha256 FROM odsp_distributed_v1.jobs "
                                    "WHERE queue_id=%s AND attempt=%s", (self.queue_id, attempt)).fetchone()
            if existing:
                _require(existing["request_sha256"] == digest, "distributed_attempt_conflict")
                row = self._job(conn, job_id)
                self._history(conn, row)
                return self._public(row, replayed=True)
            size = len(raw) + self.limits.max_result_bytes + 12288
            counts = conn.execute("SELECT count(*) AS total, count(*) FILTER(WHERE status IN "
                                  "('queued','claimed','dispatching')) AS pending, "
                                  "coalesce(sum(reservation),0) AS bytes FROM odsp_distributed_v1.jobs "
                                  "WHERE queue_id=%s", (self.queue_id,)).fetchone()
            _require(counts["total"] < self.limits.max_jobs and counts["pending"] < self.limits.max_pending
                     and counts["bytes"] + size + 2048 <= self.limits.max_state_bytes,
                     "distributed_capacity_exhausted")
            conn.execute("INSERT INTO odsp_distributed_v1.jobs(queue_id,job_id,attempt,request,request_sha256,"
                         "reservation,status) VALUES(%s,%s,%s,%s,%s,%s,'queued')",
                         (self.queue_id, job_id, attempt, raw, digest, size))
            row = self._job(conn, job_id)
            self._event(conn, row, "queued")
            payload = _canonical({"schema": "ciw.distributed-wakeup.v1", "queue_id": self.queue_id,
                                  "job_id": job_id, "request_sha256": digest}, 1024)
            event_id = sha256(payload).hexdigest()
            conn.execute("INSERT INTO odsp_distributed_v1.outbox(queue_id,job_id,event_id,payload) "
                         "VALUES(%s,%s,%s,%s)", (self.queue_id, job_id, event_id, payload))
            result = self._public(row)
        return result

    def inspect(self, job_id):
        with self._transaction() as conn:
            self._queue_row(conn, lock=True)
            row = self._job(conn, job_id)
            self._authorize(row)
            self._history(conn, row)
            return self._public(row, replayed=True)

    def _next_fence(self, conn):
        row = conn.execute("UPDATE odsp_distributed_v1.queues SET next_fence=next_fence+1 "
                           "WHERE queue_id=%s AND next_fence<%s RETURNING next_fence",
                           (self.queue_id, _SAFE)).fetchone()
        _require(row is not None, "distributed_fence_exhausted")
        return row["next_fence"]

    def claim_next(self, worker, *, job_id=None):
        _identifier(worker)
        with self._transaction() as conn:
            self._queue_row(conn, lock=True)
            count = conn.execute("SELECT count(*) AS n FROM odsp_distributed_v1.jobs WHERE queue_id=%s "
                                 "AND status IN ('claimed','dispatching')", (self.queue_id,)).fetchone()["n"]
            if count >= self.limits.max_running:
                return None
            if job_id is None:
                selected = conn.execute("SELECT job_id FROM odsp_distributed_v1.jobs WHERE queue_id=%s "
                                        "AND status='queued' ORDER BY created_at,job_id "
                                        "LIMIT 1 FOR UPDATE SKIP LOCKED", (self.queue_id,)).fetchone()
                if selected is None:
                    return None
                job_id = selected["job_id"]
            row = self._job(conn, job_id, lock=True)
            self._history(conn, row)
            if row["status"] != "queued":
                return None
            self._authorize(row)
            fence, claim_id = self._next_fence(conn), uuid4()
            conn.execute("UPDATE odsp_distributed_v1.jobs SET status='claimed',fence=%s,worker=%s,claim_id=%s,"
                         "lease_until=clock_timestamp()+(%s * interval '1 second') WHERE queue_id=%s AND job_id=%s",
                         (fence, worker, claim_id, self.lease_seconds, self.queue_id, job_id))
            row = self._job(conn, job_id)
            self._event(conn, row, "claimed")
        # An ambiguous commit raises before any capability is handed to dispatch.
        claim = Claim(self, row)
        with self._local_lock:
            self._claims[job_id] = claim
        return claim

    def _owned(self, claim):
        _require(type(claim) is Claim and claim._queue is self and claim._pid == os.getpid()
                 and self._claims.get(claim._row["job_id"]) is claim, "distributed_claim_not_owned")

    def _current(self, conn, claim, states=("claimed", "dispatching")):
        self._owned(claim)
        row = self._job(conn, claim._row["job_id"], lock=True)
        self._history(conn, row)
        _require(row["status"] in states and row["fence"] == claim.fence.number
                 and row["claim_id"] == claim._row["claim_id"] and row["worker"] == claim._row["worker"]
                 and row["request_sha256"] == claim.fence.request_sha256, "distributed_stale_claim")
        valid = conn.execute("SELECT %s::timestamptz > clock_timestamp() AS valid", (row["lease_until"],)).fetchone()
        _require(valid["valid"], "distributed_lease_expired")
        return row

    def renew(self, claim):
        """Database clock and conditional current claim; an expired lease never revives."""
        with self._transaction() as conn:
            self._queue_row(conn, lock=True)
            row = self._current(conn, claim)
            self._authorize(row)
            # Authorization may take time; recheck after it before extending.
            self._current(conn, claim)
            conn.execute("UPDATE odsp_distributed_v1.jobs SET lease_until=clock_timestamp()+"
                         "(%s * interval '1 second') WHERE queue_id=%s AND job_id=%s",
                         (self.lease_seconds, self.queue_id, row["job_id"]))

    def begin_dispatch(self, claim):
        """Commit an intent before invoking anything, with fresh current authority."""
        with self._transaction() as conn:
            self._queue_row(conn, lock=True)
            row = self._current(conn, claim, ("claimed",))
            self._authorize(row)
            self._current(conn, claim, ("claimed",))
            intent = _canonical({"schema": "ciw.distributed-dispatch-intent.v1", "queue_id": self.queue_id,
                                 "job_id": row["job_id"], "request_sha256": row["request_sha256"],
                                 "fence": row["fence"], "worker": row["worker"],
                                 "generation": self.identity["generation"], "authorizes_execution": False}, 2048)
            conn.execute("UPDATE odsp_distributed_v1.jobs SET status='dispatching',dispatch_intent=%s "
                         "WHERE queue_id=%s AND job_id=%s", (intent, self.queue_id, row["job_id"]))
            self._event(conn, self._job(conn, row["job_id"]), "dispatching")

    @contextmanager
    def fenced_transaction(self, claim):
        """Same-database downstream effects, fenced before AND after host SQL.

        The yielded transaction holds the queue/job locks. Only database effects
        committed in this transaction are protected. It does not fence a broker,
        device, subprocess or a network call performed by the caller.
        """
        with self._transaction() as conn:
            self._queue_row(conn, lock=True)
            row = self._current(conn, claim, ("dispatching",))
            self._authorize(row)
            self._current(conn, claim, ("dispatching",))
            # The explicit psycopg transaction context rejects accidental
            # conn.commit()/rollback() inside host code before the final check.
            # Transaction-control SQL remains forbidden by the trusted-host API.
            with conn.transaction():
                yield conn
                self._current(conn, claim, ("dispatching",))

    def _settle(self, claim, status, *, result=None, error=None):
        self._owned(claim)
        raw = None
        if status == "completed":
            _require(type(result) is dict, "distributed_result_invalid")
            raw = _canonical(result, self.limits.max_result_bytes)
        with self._transaction() as conn:
            self._queue_row(conn, lock=True)
            row = self._current(conn, claim, ("dispatching",) if status == "completed" else ("claimed", "dispatching"))
            if status == "completed":
                # A callback returning is not publication authority. Recheck
                # current grants inside the same transaction that publishes the
                # terminal result, then recheck the database-clock lease.
                self._authorize_completion(row, result)
                self._current(conn, claim, ("dispatching",))
            conn.execute("UPDATE odsp_distributed_v1.jobs SET status=%s,result=%s,error=%s,lease_until=NULL "
                         "WHERE queue_id=%s AND job_id=%s", (status, raw, error, self.queue_id, row["job_id"]))
            row = self._job(conn, row["job_id"])
            self._event(conn, row, status)
        with self._local_lock:
            self._claims.pop(row["job_id"], None)
        return self._public(row)

    def complete(self, claim, result):
        return self._settle(claim, "completed", result=result)

    def _authorize_completion(self, row, result):
        """Fixed host adapters may require additional result disclosure grants."""
        self._authorize(row)

    def abandon(self, claim):
        return self._settle(claim, "outcome_unknown", error="dispatch_interrupted")

    def recover(self):
        """Fence every expired claim permanently; never requeue or re-dispatch."""
        results = []
        with self._transaction() as conn:
            self._queue_row(conn, lock=True)
            rows = conn.execute("SELECT job_id FROM odsp_distributed_v1.jobs WHERE queue_id=%s "
                                "AND status IN ('claimed','dispatching') AND lease_until<=clock_timestamp() "
                                "ORDER BY created_at,job_id FOR UPDATE", (self.queue_id,)).fetchall()
            for row in rows:
                self._history(conn, self._job(conn, row["job_id"]))
                fence = self._next_fence(conn)
                conn.execute("UPDATE odsp_distributed_v1.jobs SET status='outcome_unknown',error='worker_lost',"
                             "fence=%s,lease_until=NULL WHERE queue_id=%s AND job_id=%s",
                             (fence, self.queue_id, row["job_id"]))
                job = self._job(conn, row["job_id"])
                self._event(conn, job, "outcome_unknown")
                results.append(self._public(job))
        return results

    def history(self, job_id):
        """Read and verify complete hash-linked transitions with current authorization."""
        with self._transaction() as conn:
            self._queue_row(conn, lock=True)
            row = self._job(conn, job_id)
            self._authorize(row)
            return self._history(conn, row)

    def _history(self, conn, row):
        records = conn.execute("SELECT * FROM odsp_distributed_v1.events WHERE queue_id=%s AND job_id=%s "
                               "ORDER BY ordinal", (self.queue_id, row["job_id"])).fetchall()
        previous, events = None, []
        for number, record in enumerate(records, 1):
            raw = bytes(record["event"]); event = json.loads(raw)
            _require(record["ordinal"] == number and event["ordinal"] == number
                     and event["previous_sha256"] == previous and event["queue_id"] == self.queue_id
                     and event["job_id"] == row["job_id"] and event["request_sha256"] == row["request_sha256"]
                     and sha256(raw).hexdigest() == record["event_sha256"] and _canonical(event, 2048) == raw,
                     "distributed_history_integrity")
            previous = record["event_sha256"]; events.append(event)
        _require(events and events[-1]["state"] == row["status"] and events[-1]["fence"] == row["fence"],
                 "distributed_history_incomplete")
        states = [event["state"] for event in events]
        _require(states[0] == "queued" and (len(states) < 2 or states[1] == "claimed")
                 and (len(states) < 3 or states[2] in {"dispatching", "refused", "outcome_unknown"})
                 and (len(states) < 4 or states[2] == "dispatching" and states[3] in _TERMINAL),
                 "distributed_history_transitions")
        _require(events[-1]["result_sha256"] == (sha256(bytes(row["result"])).hexdigest() if row["result"] else None)
                 and events[-1]["intent_sha256"] == (sha256(bytes(row["dispatch_intent"])).hexdigest() if row["dispatch_intent"] else None)
                 and events[-1]["error"] == row["error"], "distributed_history_outcome_mismatch")
        return events

    def reconcile_notifications(self):
        """Reopen notifications for still-queued jobs after broker loss or drift.

        This only republishes an immutable wake-up. Claimed/dispatching/terminal
        jobs are never requeued. Use periodically even when broker PubAcks were
        observed: an acknowledgement is not a disaster-recovery guarantee.
        """
        with self._transaction() as conn:
            self._queue_row(conn, lock=True)
            rows = conn.execute("UPDATE odsp_distributed_v1.outbox AS o SET published=false "
                                "FROM odsp_distributed_v1.jobs AS j WHERE o.queue_id=%s AND "
                                "j.queue_id=o.queue_id AND j.job_id=o.job_id AND j.status='queued' "
                                "AND o.published RETURNING o.event_id", (self.queue_id,)).fetchall()
            return len(rows)

    def pending_notifications(self, *, limit=16):
        """Host-only outbox; no request arguments, credentials or grants are published."""
        _require(type(limit) is int and 1 <= limit <= 64, "distributed_outbox_limit")
        with self._transaction() as conn:
            self._queue_row(conn)
            rows = conn.execute("SELECT event_id,payload FROM odsp_distributed_v1.outbox WHERE queue_id=%s "
                                "AND NOT published ORDER BY job_id LIMIT %s", (self.queue_id, limit)).fetchall()
            return [{"event_id": row["event_id"], "payload": bytes(row["payload"])} for row in rows]

    def notification_published(self, event_id, *, stream, sequence):
        _require(type(event_id) is str and re.fullmatch(r"[0-9a-f]{64}", event_id), "distributed_event_invalid")
        _identifier(stream)
        _require(type(sequence) is int and 0 < sequence <= _SAFE, "distributed_stream_sequence_invalid")
        with self._transaction() as conn:
            self._queue_row(conn)
            row = conn.execute("UPDATE odsp_distributed_v1.outbox SET published=true,stream=%s,stream_sequence=%s "
                               "WHERE queue_id=%s AND event_id=%s RETURNING event_id",
                               (stream, sequence, self.queue_id, event_id)).fetchone()
            _require(row is not None, "distributed_event_unknown")

    def notification_job(self, raw):
        """Validate a wake-up against database custody; broker data cannot select arguments."""
        _require(type(raw) is bytes and 0 < len(raw) <= 1024, "distributed_event_invalid")
        value = json.loads(raw)
        _require(type(value) is dict and set(value) == {"schema", "queue_id", "job_id", "request_sha256"}
                 and value["schema"] == "ciw.distributed-wakeup.v1" and value["queue_id"] == self.queue_id
                 and _canonical(value, 1024) == raw, "distributed_event_invalid")
        with self._transaction() as conn:
            self._queue_row(conn, lock=True)
            row = self._job(conn, value["job_id"])
            _require(row["request_sha256"] == value["request_sha256"], "distributed_event_conflict")
            self._authorize(row)
            self._history(conn, row)
            return self._public(row)

    def execute_one(self, worker, dispatch, *, job_id=None):
        """Run one canonical bounded host callback and renew its lease in the background.

        Callback signature is (operation, detached_arguments, Claim). Use
        claim.fence for an external fencing adapter or fenced_transaction for
        same-database effects. A lost lease cannot terminate arbitrary host code;
        settlement fails closed and recovery never retries that occurrence.
        """
        _require(callable(dispatch), "distributed_dispatch_required")
        claim = self.claim_next(worker, job_id=job_id)
        if claim is None:
            return None
        try:
            self.begin_dispatch(claim)
        except BaseException:
            # No callback has been entered. Record refusal only while the claim
            # is still owned and valid; ambiguous DB failures require readback.
            try:
                self._settle(claim, "refused", error="authorization_or_intent_refused")
            except Exception:
                pass
            with self._local_lock:
                self._claims.pop(claim._row["job_id"], None)
            raise
        stop, lost = Event(), Event()

        def heartbeat():
            while not stop.wait(self.lease_seconds / 3):
                try:
                    self.renew(claim)
                except Exception:
                    lost.set()
                    return

        thread = Thread(target=heartbeat, name="odsp-lease-renewal", daemon=True)
        started = False
        try:
            try:
                thread.start()
                started = True
                request = claim._row["decoded"]
                value = dispatch(request["operation"], deepcopy(request["arguments"]), claim)
                _require(not lost.is_set(), "distributed_renewal_lost")
                # Validate before settlement. Oversized results after dispatch
                # are uncertain; no fallback may invoke the provider again.
                _require(type(value) is dict, "distributed_result_invalid")
                _canonical(value, self.limits.max_result_bytes)
            except BaseException:
                try:
                    self.abandon(claim)
                except Exception:
                    pass
                raise
            # Do not overwrite a possible committed completion after a lost
            # acknowledgement. inspect/history/recover are the reconciliation API.
            return self.complete(claim, value)
        finally:
            stop.set()
            if started:
                thread.join(timeout=12)
            with self._local_lock:
                self._claims.pop(claim._row["job_id"], None)
