"""Opt-in bounded local job custody; not a tool grant or distributed broker.

The host selects the directory, principal, operation allowlist and current
authorization callback. Kernel leases protect live workers. An interrupted or
lost claim becomes outcome_unknown, never an automatically retried execution.
Existing coordinator jobs and retained artifacts are not migrated or changed.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from .agent_api import encode
from .control_contracts import json_tree
from .coordinator import _Lease
from .retention import _publish, _read_regular

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}\Z")
_JOB = re.compile(r"job-[0-9a-f]{64}\Z")
_SAFE = 2**53 - 1
_TERMINAL = {"completed", "refused", "outcome_unknown"}


class QueueRefusal(ValueError):
    """A custody or host-policy refusal; no rollback claim is implied."""


def _require(condition, reason):
    if not condition:
        raise QueueRefusal(reason)


def _identifier(value):
    _require(type(value) is str and _ID.fullmatch(value), "queue_identity_invalid")
    return value


def _wire(value, maximum, *, wrapper_depth=0, node_budget=32768):
    # Bound traversal before the established canonical encoder, and reject lone
    # surrogates before JSON escaping could hide malformed Unicode.
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        _require(nodes <= node_budget and depth <= 24 + wrapper_depth, "queue_document_complexity")
        if type(item) is str:
            try:
                item.encode("utf-8", "strict")
            except UnicodeError as exc:
                raise QueueRefusal("queue_unicode_invalid") from exc
        elif type(item) is dict:
            _require(len(item) <= 1024, "queue_document_complexity")
            pending.extend((v, depth + 1) for pair in item.items() for v in pair)
        elif type(item) is list:
            _require(len(item) <= 32768, "queue_document_complexity")
            pending.extend((v, depth + 1) for v in item)
    if wrapper_depth:
        # The individual request/result still has its unchanged depth-24
        # contract. Queue-owned containers add only these explicit levels.
        json_tree(value, depth=-wrapper_depth)
        raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    else:
        raw = encode(value)
    _require(len(raw) <= maximum, "queue_byte_budget")
    return raw


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "queue_duplicate_key")
        result[key] = value
    return result


@dataclass(frozen=True)
class QueueLimits:
    max_jobs: int = 128
    max_pending: int = 16
    max_running: int = 1
    max_request_bytes: int = 65536
    max_result_bytes: int = 65536
    max_state_bytes: int = 4_000_000
    lock_timeout: float = 2.0

    def validate(self):
        for name, maximum in (("max_jobs", 1024), ("max_pending", 128), ("max_running", 16),
                              ("max_request_bytes", 65536), ("max_result_bytes", 65536),
                              ("max_state_bytes", 4_000_000)):
            value = getattr(self, name)
            _require(type(value) is int and 1 <= value <= maximum, "queue_limit_invalid")
        _require(self.max_running <= self.max_pending <= self.max_jobs and self.max_state_bytes >= 4096,
                 "queue_limit_invalid")
        _require(type(self.lock_timeout) in (int, float) and math.isfinite(self.lock_timeout)
                 and 0 < self.lock_timeout <= 10, "queue_limit_invalid")

    def persisted(self):
        return {key: value for key, value in vars(self).items() if key != "lock_timeout"}


class Claim:
    """A process-local owned lease, not a serializable execution capability."""
    def __init__(self, queue, job, lease):
        self._queue, self._job, self._lease = queue, deepcopy(job), lease
        self._pid = os.getpid()

    @property
    def job(self):
        return deepcopy(self._job)

    @property
    def fence(self):
        return self._job["fence"]

    def __enter__(self):
        self._queue._owned(self)
        return self

    def __exit__(self, *error):
        if self._queue._claims.get(self._job["job_id"]) is self:
            self._queue.abandon(self)
        return False


class StoreQueue:
    """Single-owner POSIX queue with bounded, durable attempt history.

    `authorize(principal, operation, arguments)` must check current host grants
    and raise on refusal. `dispatch(operation, arguments, fence)` is supplied
    only by trusted host code, and must provide its own execution/resource bound.
    The queue's timeout bounds lock admission, not arbitrary Python callbacks.
    """
    def __init__(self, directory, *, owner, principal, generation, operations, authorize,
                 limits=None):
        _require(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "queue_platform_unsupported")
        self.identity = {"owner": _identifier(owner), "principal": _identifier(principal),
                         "generation": _identifier(generation)}
        _require(type(operations) in (list, tuple, set, frozenset) and 1 <= len(operations) <= 128,
                 "queue_operations_invalid")
        self.operations = frozenset(_identifier(name) for name in operations)
        _require(callable(authorize), "queue_authorization_required")
        self.authorize = authorize
        self.limits = QueueLimits() if limits is None else limits
        _require(type(self.limits) is QueueLimits, "queue_limit_invalid")
        self.limits.validate()
        self.directory = Path(directory)
        _require(self.directory.is_absolute() and ".." not in self.directory.parts
                 and not any(p.is_symlink() for p in (self.directory, *self.directory.parents)),
                 "queue_path_invalid")
        self.directory.mkdir(mode=0o700, exist_ok=True)
        info = self.directory.stat()
        _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                 and stat.S_IMODE(info.st_mode) & 0o077 == 0, "queue_directory_not_private")
        self._claims = {}
        with self._guard():
            if not (self.directory / "queue.json").exists():
                _require(set(p.name for p in self.directory.iterdir()) <= {"queue.lock"},
                         "queue_history_missing")
                self._write({"schema": "ciw.store-queue.v1", "identity": self.identity,
                             "limits": self.limits.persisted(), "next_fence": 0, "jobs": []})
            else:
                self._read()

    def _lease(self, name):
        return _Lease(self.directory / name)

    @staticmethod
    def _regular(lease):
        info = os.fstat(lease.fd)
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "queue_lock_file_invalid")

    @contextmanager
    def _guard(self):
        lease = self._lease("queue.lock")
        try:
            deadline = time.monotonic() + self.limits.lock_timeout
            while not lease.try_acquire():
                _require(time.monotonic() < deadline, "queue_busy")
                time.sleep(0.005)
            self._regular(lease)
            (self.directory / "queue.json.partial").unlink(missing_ok=True)
            yield
        finally:
            lease.release()

    def _request(self, operation, arguments, attempt):
        _identifier(operation)
        _identifier(attempt)
        _require(type(arguments) is dict, "queue_arguments_invalid")
        request = {"identity": self.identity, "operation": operation,
                   "arguments": arguments, "attempt": attempt}
        raw = _wire(request, self.limits.max_request_bytes)
        return deepcopy(request), sha256(raw).hexdigest(), len(raw)

    def _job_id(self, attempt):
        return "job-" + sha256(_wire({"identity": self.identity, "attempt": attempt}, 2048)).hexdigest()

    def _read(self):
        raw = _read_regular(self.directory / "queue.json", byte_limit=self.limits.max_state_bytes)
        try:
            value = json.loads(raw, object_pairs_hook=_unique)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise QueueRefusal("queue_record_invalid") from exc
        _require(type(value) is dict and set(value) == {"schema", "identity", "limits", "next_fence", "jobs"}
                 and value["schema"] == "ciw.store-queue.v1" and value["identity"] == self.identity
                 and value["limits"] == self.limits.persisted(), "queue_binding_mismatch")
        _require(type(value["limits"]) is dict and all(type(v) is int for v in value["limits"].values()),
                 "queue_record_invalid")
        _require(type(value["next_fence"]) is int and 0 <= value["next_fence"] <= _SAFE
                 and type(value["jobs"]) is list and len(value["jobs"]) <= self.limits.max_jobs,
                 "queue_record_invalid")
        seen = set()
        for job in value["jobs"]:
            _require(type(job) is dict and set(job) == {"job_id", "attempt", "operation", "arguments",
                "request_sha256", "status", "fence", "worker", "result", "error", "reserved_bytes"},
                "queue_record_invalid")
            _request, digest, size = self._request(job["operation"], job["arguments"], job["attempt"])
            _require(job["job_id"] == self._job_id(job["attempt"]) and job["job_id"] not in seen
                     and job["request_sha256"] == digest
                     and type(job["reserved_bytes"]) is int
                     and job["reserved_bytes"] == size + self.limits.max_result_bytes + 2048,
                     "queue_request_binding_mismatch")
            seen.add(job["job_id"])
            _require(type(job["status"]) is str and job["status"] in _TERMINAL | {"queued", "running"}
                     and type(job["fence"]) is int and 0 <= job["fence"] <= value["next_fence"],
                     "queue_record_invalid")
            if job["status"] == "queued":
                _require(job["fence"] == 0 and job["worker"] is None and job["result"] is None
                         and job["error"] is None, "queue_record_invalid")
            else:
                _identifier(job["worker"])
                _require(job["fence"] > 0, "queue_record_invalid")
            if job["status"] == "completed":
                _require(type(job["result"]) is dict and job["error"] is None, "queue_record_invalid")
                _wire(job["result"], self.limits.max_result_bytes)
            elif job["status"] == "running":
                _require(job["result"] is None and job["error"] is None, "queue_record_invalid")
            elif job["status"] == "refused":
                _require(job["result"] is None and job["error"] == "authorization_refused",
                         "queue_record_invalid")
            elif job["status"] == "outcome_unknown":
                _require(job["result"] is None and type(job["error"]) is str and job["error"] in {
                    "abandoned", "worker_lost", "dispatch_interrupted"}, "queue_record_invalid")
        _require(sum(j["reserved_bytes"] for j in value["jobs"]) + 2048 <= self.limits.max_state_bytes,
                 "queue_capacity_inconsistent")
        _require(sum(j["status"] in {"queued", "running"} for j in value["jobs"]) <= self.limits.max_pending
                 and sum(j["status"] == "running" for j in value["jobs"]) <= self.limits.max_running,
                 "queue_capacity_inconsistent")
        _require(self._state_wire(value) == raw, "queue_record_not_canonical")
        return value

    def _state_wire(self, state):
        # Every JSON node occupies at least one serialized byte. The fixed
        # byte reservation therefore also bounds aggregate nodes; imposing the
        # per-result 32768-node cap again on all jobs would exhaust custody only
        # after a valid dispatch. Three queue-owned levels enclose each result.
        return _wire(state, self.limits.max_state_bytes, wrapper_depth=3,
                     node_budget=self.limits.max_state_bytes)

    def _write(self, state):
        _publish(self.directory, "queue.json", self._state_wire(state))

    @staticmethod
    def _find(state, job_id):
        _require(type(job_id) is str and _JOB.fullmatch(job_id), "queue_job_invalid")
        job = next((j for j in state["jobs"] if j["job_id"] == job_id), None)
        _require(job is not None, "queue_job_unknown")
        return job

    def _public(self, job, replayed=False):
        value = {"schema": "ciw.store-queue-job.v1", **deepcopy({k: v for k, v in job.items()
                if k not in {"arguments", "reserved_bytes"}}), "replayed": replayed,
                "authorizes_execution": False}
        # One queue-owned response level and bounded metadata surround a result.
        _wire(value, self.limits.max_result_bytes + 4096, wrapper_depth=1, node_budget=32768 + 128)
        return value

    def _authorize(self, job):
        _require(job["operation"] in self.operations, "queue_operation_refused")
        decision = self.authorize(self.identity["principal"], job["operation"], deepcopy(job["arguments"]))
        _require(decision is None or decision is True, "queue_authorization_refused")

    def enqueue(self, operation, arguments, attempt):
        request, digest, size = self._request(operation, arguments, attempt)
        self._authorize(request)
        job_id = self._job_id(attempt)
        with self._guard():
            state = self._read()
            existing = next((job for job in state["jobs"] if job["job_id"] == job_id), None)
            if existing:
                _require(existing["request_sha256"] == digest, "queue_attempt_conflict")
                result = self._public(existing, True)
            else:
                reservation = size + self.limits.max_result_bytes + 2048
                _require(len(state["jobs"]) < self.limits.max_jobs
                         and sum(j["status"] in {"queued", "running"} for j in state["jobs"]) < self.limits.max_pending
                         and sum(j["reserved_bytes"] for j in state["jobs"]) + reservation + 2048 <= self.limits.max_state_bytes,
                         "queue_capacity_exhausted")
                job = {"job_id": job_id, "attempt": attempt, "operation": operation,
                       "arguments": request["arguments"], "request_sha256": digest, "status": "queued",
                       "fence": 0, "worker": None, "result": None, "error": None,
                       "reserved_bytes": reservation}
                state["jobs"].append(job)
                self._write(state)
                result = self._public(job)
        return result

    def inspect(self, job_id):
        with self._guard():
            job = self._find(self._read(), job_id)
            self._authorize(job)
            result = self._public(job, True)
        return result

    def claim_next(self, worker):
        _identifier(worker)
        lease = claim = None
        try:
            with self._guard():
                state = self._read()
                if sum(j["status"] == "running" for j in state["jobs"]) >= self.limits.max_running:
                    return None
                job = next((j for j in state["jobs"] if j["status"] == "queued"), None)
                if job is None:
                    return None
                self._authorize(job)
                lease = self._lease(job["job_id"] + ".lease")
                _require(lease.try_acquire(), "queue_lease_conflict")
                self._regular(lease)
                _require(state["next_fence"] < _SAFE, "queue_fence_exhausted")
                state["next_fence"] += 1
                job.update(status="running", worker=worker, fence=state["next_fence"])
                self._write(state)
            # The lease transfers only after publication AND enclosing guard exit.
            claim = Claim(self, job, lease)
            self._claims[job["job_id"]] = claim
            return claim
        except BaseException:
            if claim is not None and self._claims.get(claim._job["job_id"]) is claim:
                self._claims.pop(claim._job["job_id"])
            if lease is not None:
                lease.release()
            raise

    def _owned(self, claim):
        _require(type(claim) is Claim and claim._queue is self and claim._pid == os.getpid()
                 and self._claims.get(claim._job["job_id"]) is claim and claim._lease.fd is not None,
                 "queue_claim_not_owned")

    def _release(self, claim):
        self._claims.pop(claim._job["job_id"], None)
        claim._lease.release()

    def _settle(self, claim, status, result=None, error=None):
        self._owned(claim)
        try:
            if status == "completed":
                _require(type(result) is dict, "queue_result_invalid")
                result = json.loads(_wire(result, self.limits.max_result_bytes))
            with self._guard():
                state = self._read()
                job = self._find(state, claim._job["job_id"])
                _require(job["status"] == "running" and job["fence"] == claim.fence
                         and job["worker"] == claim._job["worker"]
                         and job["request_sha256"] == claim._job["request_sha256"], "queue_stale_claim")
                job.update(status=status, result=result, error=error)
                self._write(state)
                settled = self._public(job)
            return settled
        finally:
            # A lost acknowledgement/cleanup never rolls back a committed outcome.
            self._release(claim)

    def complete(self, claim, result):
        return self._settle(claim, "completed", result=result)

    def abandon(self, claim):
        return self._settle(claim, "outcome_unknown", error="abandoned")

    def recover(self):
        recovered = []
        with self._guard():
            state = self._read()
            for job in state["jobs"]:
                if job["status"] != "running":
                    continue
                lease = self._lease(job["job_id"] + ".lease")
                try:
                    if not lease.try_acquire():
                        continue
                    self._regular(lease)
                    _require(state["next_fence"] < _SAFE, "queue_fence_exhausted")
                    state["next_fence"] += 1
                    job.update(status="outcome_unknown", error="worker_lost", fence=state["next_fence"])
                    # Publish each settlement while its recovered job lease is held.
                    self._write(state)
                    recovered.append(self._public(job))
                finally:
                    lease.release()
        return {"schema": "ciw.store-queue-recovery.v1", "jobs": recovered, "authorizes_execution": False}

    def execute_one(self, worker, dispatch):
        """Run one host-selected bounded dispatcher; never retry an occurrence.

        Dispatch exceptions/cancellation record uncertainty, not rollback. A
        completion publication or cleanup exception propagates and readback is
        required: the completed record may already be durable.
        """
        _require(callable(dispatch), "queue_dispatch_required")
        claim = self.claim_next(worker)
        if claim is None:
            return None
        try:
            try:
                self._authorize(claim._job)
            except Exception:
                self._settle(claim, "refused", error="authorization_refused")
                raise
            try:
                value = dispatch(claim._job["operation"], deepcopy(claim._job["arguments"]), claim.fence)
            except BaseException:
                self._settle(claim, "outcome_unknown", error="dispatch_interrupted")
                raise
            return self.complete(claim, value)
        finally:
            # Includes cancellation during the final authorization check.
            if self._claims.get(claim._job["job_id"]) is claim:
                self._release(claim)
