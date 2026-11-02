"""Bounded local coordinator.

A job payload is data, not a grant. Naming another principal does not impersonate
that host. Handoff is a one-time grant file that only the named recipient host
can claim, including from another process that shares the retain directory.
This is not a live model swarm and it does not contact an external account.
"""
from __future__ import annotations

import json
import os
import re
import time
from hashlib import sha256
from pathlib import Path

from .retention import (_LOCK, _lock_release, _owner_dir, _publish, _root, _transaction,
                        _try_lock, _allocate, _canonical, _read_regular, _BYTE_LIMIT)

_EXECUTIONS = 0
_CANCELLED = (KeyboardInterrupt, SystemExit)
# Legacy grants use default JSON spacing, adding at most one byte for each
# compact delimiter. Allow that expansion plus escaped owner/recipient metadata;
# the recipient artifact itself still has the unchanged retention byte budget.
_GRANT_BYTE_LIMIT = 2 * _BYTE_LIMIT + 12 * (4096 + 500) + 1024


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
        try:
            if _try_lock(fd):
                self.fd = fd
                return True
            return False
        except BaseException:
            # A signal can arrive after the kernel acquired the lock. Until a
            # successful return, this attempt must not leave an unowned fd.
            if self.fd == fd:
                self.fd = None
            raise
        finally:
            if self.fd != fd:
                # Closing also releases a possibly acquired lock; do not try
                # to unlock a failed acquisition (not valid on every platform).
                os.close(fd)

    def release(self) -> None:
        fd, self.fd = self.fd, None
        if fd is not None:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                _lock_release(fd)
            finally:
                # Detach before I/O: a repeated cleanup must not close an fd
                # number which the OS may have reused after a close error.
                os.close(fd)


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
    try:
        if not lease.try_acquire():
            return job
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
    lease = None
    try:
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
                _store_job(store, name, job)
        # Ownership transfers only after both context exits succeed. If exit
        # fails, keep any committed job for recovery, but not an orphaned lease.
        return job, lease
    except BaseException:
        if lease is not None:
            lease.release()
        raise


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
    try:
        while not lease.try_acquire():
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.02)
        record = json.dumps({"pid": os.getpid(), "owner": store.owner, "liveness": "file-lease"}).encode()
        os.ftruncate(lease.fd, 0)
        os.lseek(lease.fd, 0, os.SEEK_SET)
        os.write(lease.fd, record)
        return lease
    except BaseException:
        # The caller cannot clean up a lease which this function never returned.
        # Leave the stable lock-file inode in place and release our local owner.
        lease.release()
        raise


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
        try:
            if exclusive is not None:
                held = _hold_exclusive(store, exclusive, exclusive_wait)
                if held is None:
                    failure = store.keep_failure({"error": "exclusive_resource_busy", "operation": operation})
                    raise RetainedFailure("exclusive_resource_busy", failure)
            _EXECUTIONS += 1
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
        try:
            _release_exclusive(held)
        finally:
            # A resource-cleanup failure must not strand the job lease. Terminal
            # publication precedes cleanup; never overwrite completed on a
            # cleanup cancellation or permit a replay to execute the job again.
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


def _claim_record(path: Path, *, byte_limit: int | None = None) -> dict | None:
    try:
        raw = _read_regular(path, byte_limit=byte_limit)
    except FileNotFoundError:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("handoff_record_invalid") from exc
    if type(value) is not dict or not value:
        raise ValueError("handoff_record_invalid")
    return value


def _valid_artifact_id(value) -> bool:
    return type(value) is str and re.fullmatch(r"(?:retained|failure)-[0-9a-f]{32}", value) is not None


def _grant_identity(owner: str, recipient: str, source: str) -> str:
    return "grant-" + sha256((owner + "\0" + recipient + "\0" + source).encode()).hexdigest()[:32]


def _check_receipt(receipt: dict, grant_id: str, recipient: str) -> None:
    if (receipt.get("schema") != "ncp.coordinator-claim-receipt.v1" or
            receipt.get("recipient") != recipient or
            not _valid_artifact_id(receipt.get("artifact_id")) or
            not _valid_artifact_id(receipt.get("source_artifact_id"))):
        raise ValueError("handoff_receipt_invalid")
    # Old valid v1 receipts lack these fields. Do not require a retained artifact
    # to exist: release/eviction must not authorize another execution or copy.
    if "grant_id" in receipt or "source_owner" in receipt:
        owner = receipt.get("source_owner")
        if (type(owner) is not str or not owner or receipt.get("grant_id") != grant_id or
                _grant_identity(owner, recipient, receipt["source_artifact_id"]) != grant_id):
            raise ValueError("handoff_receipt_invalid")


def claim(store, grant_id: str) -> dict:
    """Recover a single grant-specific publication, including after process loss.

    The grants lock serializes claims. A durable intent fixes the recipient's
    sequence before publishing its artifact; retries reconstruct the same bytes
    and identity, rather than allocating a new sequence. The owner lock also
    excludes concurrent release/eviction until the receipt is committed.
    """
    if type(grant_id) is not str or re.fullmatch(r"grant-[0-9a-f]{32}", grant_id) is None:
        raise ValueError("handoff_refused")
    directory, guard = _grants_lock()
    with guard:
        receipt = _claim_record(directory / (grant_id + ".claimed.json"))
        if receipt is not None:
            if receipt.get("recipient") != store.owner:
                raise ValueError("handoff_refused")
            _check_receipt(receipt, grant_id, store.owner)
            return {"schema": "ncp.coordinator-claim.v1", "artifact_id": receipt.get("artifact_id"),
                    "source_artifact_id": receipt.get("source_artifact_id"), "replayed": True,
                    "authorizes_execution": False}
        path = directory / (grant_id + ".json")
        grant = _claim_record(path, byte_limit=_GRANT_BYTE_LIMIT)
        if not grant or grant.get("recipient") != store.owner or grant.get("grant_id") != grant_id:
            raise ValueError("handoff_refused")
        if (grant.get("schema") != "ncp.handoff-grant.v1" or
                type(grant.get("owner")) is not str or not grant["owner"] or
                not _valid_artifact_id(grant.get("source_artifact_id")) or
                type(grant.get("native")) is not dict or type(grant.get("catalog")) is not dict or
                _grant_identity(grant["owner"], store.owner, grant["source_artifact_id"]) != grant_id):
            raise ValueError("handoff_grant_invalid")
        intent_name = grant_id + ".intent.json"
        intent = _claim_record(directory / intent_name)
        replayed = intent is not None
        digest = sha256(_canonical(grant)).hexdigest()
        with _LOCK:
            owner_directory = _owner_dir(store.owner)
            with _transaction(owner_directory):
                if intent is None:
                    intent = {"schema": "ncp.coordinator-claim-intent.v1", "recipient": store.owner,
                              "grant_digest": digest, "sequence": _allocate(owner_directory)}
                    _publish(directory, intent_name, _canonical(intent))
                elif (intent.get("schema") != "ncp.coordinator-claim-intent.v1" or
                      intent.get("recipient") != store.owner or intent.get("grant_digest") != digest or
                      type(intent.get("sequence")) is not int or intent["sequence"] < 1):
                    raise ValueError("handoff_intent_conflict")
                envelope = {"schema": "ncp.retained-bundle.v1", "owner": store.owner,
                            "kind": "handoff", "sequence": intent["sequence"],
                            "native": grant["native"], "catalog": grant["catalog"]}
                copied = "retained-" + sha256(_canonical(envelope)).hexdigest()[:32]
                target = store._path(copied)
                if target.is_symlink():
                    raise ValueError("handoff_artifact_conflict")
                if target.exists():
                    if store._read(copied) != envelope:
                        raise ValueError("handoff_artifact_conflict")
                    # Publication may have survived a crash before eviction ran.
                    store._evict(owner_directory, "retained-", target)
                else:
                    if store._write(envelope) != copied:
                        raise RuntimeError("handoff_artifact_identity_mismatch")
                _publish(directory, grant_id + ".claimed.json", _canonical(
                    {"schema": "ncp.coordinator-claim-receipt.v1", "recipient": store.owner,
                     "artifact_id": copied, "source_artifact_id": grant.get("source_artifact_id"),
                     "grant_id": grant_id, "source_owner": grant["owner"]}))
        # Receipt is durable before cleanup. A crash during cleanup just replays it.
        path.unlink(missing_ok=True)
        (directory / intent_name).unlink(missing_ok=True)
    return {"schema": "ncp.coordinator-claim.v1", "artifact_id": copied,
            "source_artifact_id": grant.get("source_artifact_id"), "replayed": replayed,
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
            try:
                if lease.try_acquire():
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
