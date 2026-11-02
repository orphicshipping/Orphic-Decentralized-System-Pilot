"""Bounded local coordinator.

A job payload is data, not a grant. Naming another principal does not impersonate
that host. Handoff is a one-time grant file that only the named recipient host
can claim, including from another process that shares the retain directory.
This is not a live model swarm and it does not contact an external account.
"""
from __future__ import annotations

import json
import os
import time
from hashlib import sha256
from pathlib import Path

from .retention import (_LOCK, _lock_release, _owner_dir, _publish, _root, _transaction,
                        _try_lock)

_EXECUTIONS = 0
_CANCELLED = (KeyboardInterrupt, SystemExit)


# Liveness is a kernel file lock (a lease), not a PID. A worker holds the lease on
# idem-<hash>.lease for as long as it runs. The kernel drops it when the process
# exits for any reason, so a reused PID cannot make a dead job look alive and a
# live job cannot be stolen. The PID stays in the record for diagnosis only.
class _Lease:
    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def try_acquire(self) -> bool:
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        if _try_lock(fd):
            self.fd = fd
            return True
        os.close(fd)
        return False

    def release(self) -> None:
        if self.fd is not None:
            try:
                os.lseek(self.fd, 0, os.SEEK_SET)
                _lock_release(self.fd)
            finally:
                os.close(self.fd)
                self.fd = None


def _lease_path(directory: Path, name: str) -> Path:
    return directory / (name[: -len(".json")] + ".lease")


def _exclusive_path(name: str) -> Path:
    if type(name) is not str or not name.isidentifier():
        raise ValueError("exclusive resource name is not usable")
    directory = _root() / "exclusive"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / name


def _idem_name(key: str) -> str:
    if type(key) is not str or not key or len(key) > 200:
        raise ValueError("idempotency_key is not usable")
    return "idem-" + sha256(key.encode()).hexdigest()[:32] + ".json"


def _digest(operation: str, arguments: dict, exclusive: str | None) -> str:
    raw = json.dumps(
        {"operation": operation, "arguments": arguments, "exclusive": exclusive},
        sort_keys=True, separators=(",", ":")).encode()
    return sha256(raw).hexdigest()


def _read_json(path: Path):
    try:
        if path.is_symlink() or not path.is_file():
            return None
        raw = path.read_bytes()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}


def _store_job(store, name: str, job: dict) -> None:
    directory = _owner_dir(store.owner)
    _publish(directory, name, json.dumps(job, sort_keys=True, separators=(",", ":")).encode())


def _replay(job: dict) -> dict:
    replay = dict(job)
    replay["replayed"] = True
    replay["authorizes_execution"] = False
    return replay


def _settle_locked(store, directory: Path, name: str) -> dict | None:
    """Resolve one job record. The caller holds _transaction(directory).

    A running record whose lease nobody holds is a lost worker. It is marked
    failed here, inside the same owner lock as the read, so two recoverers
    cannot both write it and a finishing worker cannot be overwritten.
    """
    job = _read_json(directory / name)
    if job is None or job.get("status") != "running":
        return job
    lease = _Lease(_lease_path(directory, name))
    if not lease.try_acquire():
        return job
    try:
        failed = {k: v for k, v in job.items() if k != "replayed"}
        failed["status"] = "failed"
        failed["error"] = "worker_lost"
        failed["artifact_id"] = store._keep_failure_locked(
            {"error": "worker_lost", "operation": job.get("operation")})
        _store_job(store, name, failed)
        return failed
    finally:
        lease.release()


def _fail_lost(store, name: str, job: dict | None = None) -> dict:
    directory = _owner_dir(store.owner)
    with _LOCK:
        with _transaction(directory):
            settled = _settle_locked(store, directory, name)
    if settled is None:
        raise ValueError("unknown_job")
    return _replay(settled)


def _admit(store, key: str, digest: str, operation: str):
    """Create the running record and take its lease, or return the existing record."""
    directory = _owner_dir(store.owner)
    name = _idem_name(key)
    with _LOCK:
        with _transaction(directory):
            existing = _read_json(directory / name)
            if existing is not None:
                if existing.get("request_digest") != digest:
                    raise ValueError("idempotency_key_conflict")
                return _replay(_settle_locked(store, directory, name)), None
            lease = _Lease(_lease_path(directory, name))
            if not lease.try_acquire():
                raise RuntimeError("coordinator lease is held without a job record")
            job = {
                "schema": "ncp.coordinator-job.v1",
                "operation": operation,
                "executions": 1,
                "status": "running",
                "pid": os.getpid(),
                "liveness": "file-lease",
                "request_digest": digest,
                "authorizes_execution": False,
            }
            try:
                _store_job(store, name, job)
            except BaseException:
                lease.release()
                raise
            return job, lease


def _finish(store, key: str, record: dict) -> None:
    directory = _owner_dir(store.owner)
    with _LOCK:
        with _transaction(directory):
            _store_job(store, _idem_name(key), {k: v for k, v in record.items() if k != "replayed"})


# An exclusive resource is held by a kernel lock on exclusive/<name>. The file is
# never unlinked: unlinking a locked file would let a second process lock a new
# inode under the same name while the first still holds the old one.
def _hold_exclusive(store, name: str, wait: float = 0.0):
    path = _exclusive_path(name)
    deadline = time.monotonic() + max(0.0, float(wait))
    lease = _Lease(path)
    while not lease.try_acquire():
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.02)
    record = json.dumps({"pid": os.getpid(), "owner": store.owner, "liveness": "file-lease"}).encode()
    os.ftruncate(lease.fd, 0)
    os.lseek(lease.fd, 0, os.SEEK_SET)
    os.write(lease.fd, record)
    return lease


def _release_exclusive(lease) -> None:
    if lease is None or lease.fd is None:
        return
    try:
        os.ftruncate(lease.fd, 0)
    finally:
        lease.release()


def _checked_arguments(arguments: dict) -> None:
    # The inner operation passes the same policy as a direct MCP or SDK call, so
    # the coordinator cannot be used to bypass a check the host would enforce.
    from .services import admit
    admit("ncp_domain_run", arguments)


def submit(store, operation: str, arguments: dict, idempotency_key: str, exclusive: str | None = None,
           exclusive_wait: float = 0.0) -> dict:
    global _EXECUTIONS
    from .retention import RetainedFailure
    from .services import domain_run
    if operation != "ncp_domain_run":
        raise ValueError("coordinator_operation_refused")
    if type(arguments) is not dict:
        raise ValueError("coordinator arguments must be an object")
    _checked_arguments(arguments)
    digest = _digest(operation, arguments, exclusive)
    existing, lease = _admit(store, idempotency_key, digest, operation)
    if lease is None:
        return existing
    held = None
    try:
        if exclusive is not None:
            held = _hold_exclusive(store, exclusive, exclusive_wait)
            if held is None:
                failure = store.keep_failure({"error": "exclusive_resource_busy", "operation": operation})
                _finish(store, idempotency_key, {**existing, "status": "failed",
                                                 "error": "exclusive_resource_busy", "artifact_id": failure})
                raise RetainedFailure("exclusive_resource_busy", failure)
        _EXECUTIONS += 1
        try:
            result = domain_run(store=store, **arguments)
        except _CANCELLED:
            _finish(store, idempotency_key, {**existing, "status": "cancelled", "error": "cancelled"})
            raise
        except Exception as exc:
            failure = getattr(exc, "artifact_id", None)
            _finish(store, idempotency_key, {**existing, "status": "failed", "artifact_id": failure,
                                             "error": str(exc)[:300]})
            raise
        done = {**existing, "status": "completed", "artifact_id": result.get("artifact_id"),
                "example": result.get("example")}
        _finish(store, idempotency_key, done)
        done["replayed"] = False
        done["authorizes_execution"] = False
        return done
    finally:
        _release_exclusive(held)
        # The terminal record is written before the lease drops, so no recoverer
        # can see a running record with a free lease while this worker is alive.
        lease.release()


def _grants_lock():
    directory = _root() / "grants"
    directory.mkdir(parents=True, exist_ok=True)
    return directory, _transaction(directory)


def handoff(store, artifact_id: str, recipient: str) -> dict:
    if type(recipient) is not str or not recipient or recipient == store.owner or len(recipient) > 500:
        raise ValueError("handoff_refused")
    native = store.native(artifact_id)
    catalog = store.catalog(artifact_id)
    grant_id = "grant-" + sha256((store.owner + "\0" + recipient + "\0" + artifact_id).encode()).hexdigest()[:32]
    body = {
        "schema": "ncp.handoff-grant.v1",
        "grant_id": grant_id,
        "owner": store.owner,
        "recipient": recipient,
        "source_artifact_id": artifact_id,
        "native": native,
        "catalog": catalog,
    }
    directory, guard = _grants_lock()
    with guard:
        claimed = _read_json(directory / (grant_id + ".claimed.json"))
        if not claimed:
            _publish(directory, grant_id + ".json", json.dumps(body, sort_keys=True).encode())
    return {"schema": "ncp.coordinator-handoff.v1", "grant_id": grant_id,
            "already_claimed": bool(claimed), "authorizes_execution": False}


def claim(store, grant_id: str) -> dict:
    """Claim a grant exactly once across processes.

    The read, the copy and the consumption happen under one cross-process file
    lock. A repeated delivery of the same claim by the recipient returns the
    same artifact instead of a second copy or a refusal.
    """
    if type(grant_id) is not str or not grant_id.startswith("grant-") or "/" in grant_id or ".." in grant_id:
        raise ValueError("handoff_refused")
    directory, guard = _grants_lock()
    with guard:
        receipt = _read_json(directory / (grant_id + ".claimed.json"))
        if receipt:
            if receipt.get("recipient") != store.owner:
                raise ValueError("handoff_refused")
            return {"schema": "ncp.coordinator-claim.v1", "artifact_id": receipt.get("artifact_id"),
                    "source_artifact_id": receipt.get("source_artifact_id"), "replayed": True,
                    "authorizes_execution": False}
        path = directory / (grant_id + ".json")
        grant = _read_json(path)
        if not grant or grant.get("recipient") != store.owner:
            raise ValueError("handoff_refused")
        copied = store.keep(grant["native"], kind="handoff", catalog=grant["catalog"])
        _publish(directory, grant_id + ".claimed.json", json.dumps(
            {"schema": "ncp.coordinator-claim-receipt.v1", "recipient": store.owner, "artifact_id": copied,
             "source_artifact_id": grant.get("source_artifact_id")}, sort_keys=True).encode())
        path.unlink(missing_ok=True)
    return {"schema": "ncp.coordinator-claim.v1", "artifact_id": copied,
            "source_artifact_id": grant.get("source_artifact_id"), "replayed": False,
            "authorizes_execution": False}


def recover(store) -> dict:
    directory = _owner_dir(store.owner)
    recovered = []
    if directory.is_dir():
        for path in sorted(directory.glob("idem-*.json")):
            with _LOCK:
                with _transaction(directory):
                    before = _read_json(path)
                    if before is None or before.get("status") != "running":
                        continue
                    settled = _settle_locked(store, directory, path.name)
            if settled is not None and settled.get("error") == "worker_lost":
                recovered.append({"job": path.name[: -len(".json")], "artifact_id": settled.get("artifact_id")})
    exclusive = _root() / "exclusive"
    released = []
    if exclusive.is_dir():
        for path in sorted(exclusive.iterdir()):
            current = _read_json(path) or {}
            if current.get("owner") != store.owner:
                continue
            lease = _Lease(path)
            if lease.try_acquire():
                try:
                    os.ftruncate(lease.fd, 0)
                    released.append(path.name)
                finally:
                    lease.release()
    return {"schema": "ncp.coordinator-recovery.v1", "jobs": recovered, "released": released,
            "authorizes_execution": False}


def coordinate(action: str, store=None, idempotency_key: str | None = None, operation: str | None = None,
               arguments: dict | None = None, exclusive: str | None = None, artifact_id: str | None = None,
               recipient: str | None = None, grant_id: str | None = None) -> dict:
    from .retention import _store
    holder = store if store is not None else _store()
    if action == "submit":
        return submit(holder, operation or "", arguments or {}, idempotency_key or "", exclusive)
    if action == "recall":
        path = _owner_dir(holder.owner) / _idem_name(idempotency_key or "")
        found = _read_json(path)
        if found is None:
            raise ValueError("unknown_job")
        if found.get("status") == "running":
            return _fail_lost(holder, _idem_name(idempotency_key or ""))
        found["authorizes_execution"] = False
        return found
    if action == "handoff":
        return handoff(holder, artifact_id or "", recipient or "")
    if action == "claim":
        return claim(holder, grant_id or "")
    if action == "recover":
        return recover(holder)
    raise ValueError("unknown_coordinator_action")
