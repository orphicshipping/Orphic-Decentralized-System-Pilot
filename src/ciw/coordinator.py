"""Bounded local coordinator.

A job payload is data, not a grant. Naming another principal does not impersonate
that host. Handoff is a one-time grant file that only the named recipient host
can claim, including from another process that shares the retain directory.
This is not a live model swarm and it does not contact an external account.
"""
from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path

from .retention import _LOCK, _owner_dir, _publish, _root, _transaction

_EXECUTIONS = 0


def _alive(pid) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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


def _fail_lost(store, name: str, job: dict) -> dict:
    failed = {k: v for k, v in job.items() if k != "replayed"}
    failed["status"] = "failed"
    failed["error"] = "worker_lost"
    artifact = store.keep_failure({"error": "worker_lost", "operation": job.get("operation")})
    failed["artifact_id"] = artifact
    directory = _owner_dir(store.owner)
    with _LOCK:
        with _transaction(directory):
            _store_job(store, name, failed)
    failed["replayed"] = True
    failed["authorizes_execution"] = False
    return failed


def _admit(store, key: str, digest: str, operation: str) -> tuple[dict, bool]:
    """Create the running record or return the existing one. One lock covers both."""
    directory = _owner_dir(store.owner)
    path = directory / _idem_name(key)
    lost = None
    with _LOCK:
        with _transaction(directory):
            name = _idem_name(key)
            path = directory / name
            existing = _read_json(path)
            if existing is not None:
                if existing.get("request_digest") != digest:
                    raise ValueError("idempotency_key_conflict")
                if existing.get("status") == "running" and not _alive(existing.get("pid")):
                    lost = (name, existing)
                else:
                    replay = dict(existing)
                    replay["replayed"] = True
                    replay["authorizes_execution"] = False
                    return replay, True
            else:
                job = {
                    "schema": "ncp.coordinator-job.v1",
                    "operation": operation,
                    "executions": 1,
                    "status": "running",
                    "pid": os.getpid(),
                    "request_digest": digest,
                    "authorizes_execution": False,
                }
                _store_job(store, name, job)
                return job, False
    if lost is not None:
        return _fail_lost(store, lost[0], lost[1]), True
    raise RuntimeError("coordinator admission did not resolve")


def _hold_exclusive(store, name: str) -> Path | None:
    path = _exclusive_path(name)
    record = json.dumps({"pid": os.getpid(), "owner": store.owner}).encode()
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        current = _read_json(path) or {}
        if _alive(current.get("pid")):
            return None
        path.unlink(missing_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return None
    try:
        os.write(fd, record)
    finally:
        os.close(fd)
    return path


def _release_exclusive(path: Path) -> None:
    current = _read_json(path) or {}
    if current.get("pid") == os.getpid():
        path.unlink(missing_ok=True)


def _checked_arguments(arguments: dict) -> None:
    from .agent_tools import _shape
    from .control_gateway import TOOLS
    if "owner" in arguments or "principal" in arguments or "agent_id" in arguments:
        raise ValueError("coordinator_impersonation_refused")
    _shape(arguments, TOOLS["ncp_domain_run"]["inputSchema"])


def submit(store, operation: str, arguments: dict, idempotency_key: str, exclusive: str | None = None) -> dict:
    global _EXECUTIONS
    from .retention import RetainedFailure
    from .services import domain_run
    if operation != "ncp_domain_run":
        raise ValueError("coordinator_operation_refused")
    if type(arguments) is not dict:
        raise ValueError("coordinator arguments must be an object")
    _checked_arguments(arguments)
    digest = _digest(operation, arguments, exclusive)
    existing, replay = _admit(store, idempotency_key, digest, operation)
    if replay:
        return existing
    held = None
    if exclusive is not None:
        held = _hold_exclusive(store, exclusive)
        if held is None:
            failure = store.keep_failure({"error": "exclusive_resource_busy", "operation": operation})
            failed = {**existing, "status": "failed", "error": "exclusive_resource_busy", "artifact_id": failure}
            directory = _owner_dir(store.owner)
            with _LOCK:
                with _transaction(directory):
                    _store_job(store, _idem_name(idempotency_key), {k: v for k, v in failed.items() if k != "replayed"})
            raise RetainedFailure("exclusive_resource_busy", failure)
    try:
        _EXECUTIONS += 1
        result = domain_run(store=store, **arguments)
    except Exception as exc:
        failure = getattr(exc, "artifact_id", None)
        failed = {**existing, "status": "failed", "artifact_id": failure, "error": str(exc)[:300]}
        directory = _owner_dir(store.owner)
        with _LOCK:
            with _transaction(directory):
                _store_job(store, _idem_name(idempotency_key), failed)
        raise
    else:
        done = {**existing, "status": "completed", "artifact_id": result.get("artifact_id"), "example": result.get("example")}
        directory = _owner_dir(store.owner)
        with _LOCK:
            with _transaction(directory):
                _store_job(store, _idem_name(idempotency_key), done)
        done["replayed"] = False
        done["authorizes_execution"] = False
        return done
    finally:
        if held is not None:
            _release_exclusive(held)


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
    directory = _root() / "grants"
    directory.mkdir(parents=True, exist_ok=True)
    _publish(directory, grant_id + ".json", json.dumps(body, sort_keys=True).encode())
    return {"schema": "ncp.coordinator-handoff.v1", "grant_id": grant_id, "authorizes_execution": False}


def claim(store, grant_id: str) -> dict:
    if type(grant_id) is not str or not grant_id.startswith("grant-") or "/" in grant_id or ".." in grant_id:
        raise ValueError("handoff_refused")
    path = _root() / "grants" / (grant_id + ".json")
    grant = _read_json(path)
    if grant is None or grant.get("recipient") != store.owner:
        raise ValueError("handoff_refused")
    copied = store.keep(grant["native"], kind="handoff", catalog=grant["catalog"])
    if path.is_file() and not path.is_symlink():
        path.unlink()
    return {"schema": "ncp.coordinator-claim.v1", "artifact_id": copied,
            "source_artifact_id": grant.get("source_artifact_id"), "authorizes_execution": False}


def recover(store) -> dict:
    directory = _owner_dir(store.owner)
    recovered = []
    if directory.is_dir():
        for path in sorted(directory.glob("idem-*.json")):
            job = _read_json(path)
            if job is None or job.get("status") != "running" or _alive(job.get("pid")):
                continue
            failed = _fail_lost(store, path.name, job)
            recovered.append({"job": key_hint, "artifact_id": failed.get("artifact_id")})
    exclusive = _root() / "exclusive"
    released = []
    if exclusive.is_dir():
        for path in sorted(exclusive.iterdir()):
            current = _read_json(path) or {}
            if current.get("owner") == store.owner and not _alive(current.get("pid")):
                path.unlink(missing_ok=True)
                released.append(path.name)
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
        if found.get("status") == "running" and not _alive(found.get("pid")):
            return _fail_lost(holder, _idem_name(idempotency_key or ""), found)
        found["authorizes_execution"] = False
        return found
    if action == "handoff":
        return handoff(holder, artifact_id or "", recipient or "")
    if action == "claim":
        return claim(holder, grant_id or "")
    if action == "recover":
        return recover(holder)
    raise ValueError("unknown_coordinator_action")
