"""Bounded local coordinator.

A job payload is data, not a grant. Naming another principal does not impersonate
that host. Handoff copies a record only when the owning host asks. This is not a
live model swarm and it does not contact an external account.
"""
from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

from .retention import _owner_dir, _publish, _root, _transaction

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


def _recall(store, key: str):
    import json
    path = _owner_dir(store.owner) / _idem_name(key)
    if path.is_symlink() or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _commit(store, key: str, job: dict) -> None:
    import json
    directory = _owner_dir(store.owner)
    raw = json.dumps(job, sort_keys=True, separators=(",", ":")).encode()
    with _transaction(directory):
        _publish(directory, _idem_name(key), raw)


def submit(store, operation: str, arguments: dict, idempotency_key: str, exclusive: str | None = None) -> dict:
    from .retention import RetainedFailure
    from .services import domain_run
    if operation != "ncp_domain_run":
        raise ValueError("coordinator_operation_refused")
    if type(arguments) is not dict:
        raise ValueError("coordinator arguments must be an object")
    if "owner" in arguments or "principal" in arguments or "agent_id" in arguments:
        raise ValueError("coordinator_impersonation_refused")
    existing = _recall(store, idempotency_key)
    if existing is not None:
        replay = dict(existing)
        replay["replayed"] = True
        replay["authorizes_execution"] = False
        return replay
    held = None
    if exclusive is not None:
        path = _exclusive_path(exclusive)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            held = path
        except FileExistsError:
            failure = store.keep_failure({"error": "exclusive_resource_busy", "operation": operation})
            raise RetainedFailure("exclusive_resource_busy", failure)
    job = {"schema": "ncp.coordinator-job.v1", "operation": operation, "executions": 1,
           "status": "running", "authorizes_execution": False}
    _commit(store, idempotency_key, job)
    try:
        result = domain_run(store=store, **arguments)
    except Exception as exc:
        failure = getattr(exc, "artifact_id", None)
        job = {**job, "status": "failed", "artifact_id": failure, "error": str(exc)[:300]}
        _commit(store, idempotency_key, job)
        raise
    else:
        job = {**job, "status": "completed", "artifact_id": result.get("artifact_id"), "example": result.get("example")}
        _commit(store, idempotency_key, job)
        job["replayed"] = False
        return job
    finally:
        if held is not None:
            held.unlink(missing_ok=True)


def handoff(store, artifact_id: str, recipient) -> dict:
    if recipient is store or getattr(recipient, "owner", None) == store.owner:
        raise ValueError("handoff_refused")
    native = store.native(artifact_id)
    catalog = store.catalog(artifact_id)
    copied = recipient.keep(native, kind="handoff", catalog=catalog)
    return {"schema": "ncp.coordinator-handoff.v1", "artifact_id": copied,
            "source_artifact_id": artifact_id, "authorizes_execution": False}


def coordinate(action: str, store=None, idempotency_key: str | None = None, operation: str | None = None,
               arguments: dict | None = None, exclusive: str | None = None, artifact_id: str | None = None,
               recipient_owner: str | None = None) -> dict:
    """Public fold. recipient_owner cannot select a store the caller does not hold."""
    from .retention import _store
    holder = store if store is not None else _store()
    if action == "submit":
        return submit(holder, operation or "", arguments or {}, idempotency_key or "", exclusive)
    if action == "recall":
        found = _recall(holder, idempotency_key or "")
        if found is None:
            raise ValueError("unknown_job")
        found["authorizes_execution"] = False
        return found
    raise ValueError("unknown_coordinator_action")
