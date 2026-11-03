"""Bounded redacted audit chain with an independently retained head anchor.

A hash chain alone is not immutable storage or an authenticated external log.
The host owns anchor persistence, operation names and event construction. No
request bodies, response bodies, credentials or exception text are accepted.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import threading
import time

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_REF = re.compile(r"sha256:[0-9a-f]{64}\Z")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
_MAX_RECORD = 4096
_LOCAL = threading.RLock()
_EVENT_FIELDS = {"call_id", "operation", "principal_ref", "request_ref", "result_ref", "phase", "effect_state", "recorded_at", "error_code", "reconciliation"}
_PHASES = {"admitted", "succeeded", "failed", "refused", "unknown", "observed", "reconciled"}
_EFFECTS = {"not_started", "may_have_effects", "returned", "committed", "unknown"}


class AuditRefusal(ValueError):
    pass


class AuditOutcomeUncertain(AuditRefusal):
    def __init__(self, effect_state="unknown", *, result_ref=None):
        self.effect_state = effect_state
        self.result_ref = result_ref
        super().__init__("audit_outcome_uncertain")


class AuditReplay(AuditRefusal):
    """An existing admission must be reconciled/retrieved, never executed again."""
    def __init__(self, event):
        self.event = deepcopy(event)
        super().__init__("audit_call_already_recorded")


def standard_result_outcome(value):
    """Host envelope classification; returned refusal does not imply rollback."""
    status = value.get("status") if type(value) is dict else None
    if type(value) is dict:
        metadata = [value]
        if type(value.get("error")) is dict:
            metadata.append(value["error"])
        for item in metadata:
            for key in ("code","reason"):
                code = item.get(key)
                if type(code) is str and code.lower() in {
                        "outcome_unknown","runtime_cleanup_uncertain","audit_outcome_uncertain"}:
                    return {"phase":"unknown", "effect_state":"unknown"}
            if item.get("uncertain") is True or item.get("outcome_unknown") is True:
                return {"phase":"unknown", "effect_state":"unknown"}
        effects = [item.get("effect_state") for item in metadata]
        if any(type(e) is str and e == "unknown" for e in effects):
            return {"phase":"unknown", "effect_state":"unknown"}
        if any(type(e) is str and e == "may_have_effects" for e in effects):
            return {"phase":"unknown", "effect_state":"may_have_effects"}
    if type(status) is not str:
        return {"phase":"unknown", "effect_state":"returned"}
    phases = {"ok":"succeeded", "success":"succeeded", "refused":"refused", "denied":"refused",
              "failed":"failed", "error":"failed", "unknown":"unknown", "partial":"unknown"}
    effect = "unknown" if status in {"unknown","partial"} else "returned"
    if effect == "returned" and type(value) is dict and any(
            item.get("effect_state") == "committed" for item in metadata):
        effect = "committed"
    return {"phase":phases.get(status, "unknown"), "effect_state":effect}


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def _digest(value):
    return "sha256:" + sha256(_encode(value)).hexdigest()


def empty_anchor(chain_id):
    if type(chain_id) is not str or not _ID.fullmatch(chain_id):
        raise AuditRefusal("invalid_audit_chain")
    return {"schema":"ciw.audit-anchor.v1", "chain_id":chain_id, "sequence":0,
            "head_hash":_digest({"schema":"ciw.audit-genesis.v1","chain_id":chain_id})}


def _anchor(value, chain_id):
    if (type(value) is not dict or set(value) != {"schema","chain_id","sequence","head_hash"}
            or value["schema"] != "ciw.audit-anchor.v1" or value["chain_id"] != chain_id
            or type(value["sequence"]) is not int or not 0 <= value["sequence"] <= 1000000
            or type(value["head_hash"]) is not str or not _REF.fullmatch(value["head_hash"])):
        raise AuditRefusal("invalid_audit_anchor")
    if value["sequence"] == 0 and value != empty_anchor(chain_id):
        raise AuditRefusal("invalid_audit_anchor")
    return value


def _event(value, allowed):
    if type(value) is not dict or set(value) != _EVENT_FIELDS:
        raise AuditRefusal("invalid_audit_event")
    if (type(value["call_id"]) is not str or not _ID.fullmatch(value["call_id"])
            or type(value["operation"]) is not str or value["operation"] not in allowed
            or type(value["phase"]) is not str or value["phase"] not in _PHASES
            or type(value["effect_state"]) is not str or value["effect_state"] not in _EFFECTS):
        raise AuditRefusal("invalid_audit_event")
    for key in ("principal_ref", "request_ref"):
        if type(value[key]) is not str or not _REF.fullmatch(value[key]):
            raise AuditRefusal("invalid_audit_event")
    if value["result_ref"] is not None and (type(value["result_ref"]) is not str or not _REF.fullmatch(value["result_ref"])):
        raise AuditRefusal("invalid_audit_event")
    if value["error_code"] is not None and (type(value["error_code"]) is not str or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", value["error_code"])):
        raise AuditRefusal("invalid_audit_event")
    if type(value["recorded_at"]) is not str or not _STAMP.fullmatch(value["recorded_at"]):
        raise AuditRefusal("invalid_audit_timestamp")
    try:
        datetime.fromisoformat(value["recorded_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuditRefusal("invalid_audit_timestamp") from exc
    if value["phase"] == "admitted" and (value["effect_state"] != "not_started" or value["result_ref"] is not None or value["error_code"] is not None):
        raise AuditRefusal("invalid_audit_admission")
    if value["phase"] == "reconciled":
        resolution = _resolution(value["reconciliation"])
        if value["effect_state"] != resolution["effect_state"] or value["recorded_at"] != resolution["reviewed_at"]:
            raise AuditRefusal("invalid_audit_reconciliation")
    elif value["reconciliation"] is not None:
        raise AuditRefusal("unexpected_audit_reconciliation")
    if len(_encode(value)) > 2048:
        raise AuditRefusal("audit_event_budget")
    return deepcopy(value)


def _resolution(value):
    fields = {"schema","review_id","record_hash","decision","custody","effect_state","evidence_ref","reviewed_at"}
    if (type(value) is not dict or set(value) != fields or value["schema"] != "ciw.audit-reconciliation.v1"
            or type(value["review_id"]) is not str or not _ID.fullmatch(value["review_id"])
            or value["decision"] != "preserve_effects_no_retry"
            or type(value["custody"]) is not str or value["custody"] not in {"confirmed_stopped","transferred"}
            or type(value["effect_state"]) is not str or value["effect_state"] not in {"returned","committed","unknown"}
            or type(value["reviewed_at"]) is not str or not _STAMP.fullmatch(value["reviewed_at"])):
        raise AuditRefusal("invalid_audit_reconciliation")
    for key in ("record_hash","evidence_ref"):
        if type(value[key]) is not str or not _REF.fullmatch(value[key]):
            raise AuditRefusal("invalid_audit_reconciliation")
    try:
        datetime.fromisoformat(value["reviewed_at"].replace("Z","+00:00"))
    except ValueError as exc:
        raise AuditRefusal("invalid_audit_reconciliation") from exc
    return deepcopy(value)


def _transition(previous, event):
    if previous is None:
        if event["phase"] not in {"admitted","observed"}:
            raise AuditRefusal("audit_terminal_without_admission")
        return
    before = previous["event"]
    if (any(before[k] != event[k] for k in ("operation","principal_ref","request_ref"))
            or (before["phase"] != "admitted" and event["phase"] != "reconciled")
            or before["phase"] == "reconciled"
            or event["phase"] in {"admitted","observed"}):
        raise AuditRefusal("audit_call_binding_mismatch")
    if event["phase"] == "reconciled" and event["reconciliation"]["record_hash"] != previous["hash"]:
        raise AuditRefusal("audit_reconciliation_binding_mismatch")


def _unresolved(records):
    latest = {r["event"]["call_id"]:r for r in records}
    return [r for r in latest.values() if _pending_event(r["event"])]


def _pending_event(event):
    return event["phase"] != "reconciled" and (event["phase"] in {"admitted","unknown"}
        or event["effect_state"] in {"unknown","may_have_effects"})


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise AuditRefusal("duplicate_audit_key")
        out[key] = value
    return out


def _regular(fd):
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise AuditRefusal("audit_record_not_regular")


class AuditChain:
    def __init__(self, path, *, chain_id, allowed_operations, max_records=4096,
                 max_bytes=2*1024*1024, lock_timeout=1.0):
        empty_anchor(chain_id)
        self.path = Path(path)
        if (not self.path.is_absolute() or not self.path.parent.is_dir()
                or any(p.is_symlink() for p in (self.path, *self.path.parents))):
            raise AuditRefusal("unsafe_audit_path")
        if (type(max_records) is not int or not 1 <= max_records <= 1000000
                or type(max_bytes) is not int or not _MAX_RECORD <= max_bytes <= 64*1024*1024
                or type(lock_timeout) not in (int,float) or not 0 < lock_timeout <= 10):
            raise AuditRefusal("invalid_audit_budget")
        allowed = tuple(allowed_operations)
        if not allowed or len(allowed) > 512 or any(type(n) is not str or not _ID.fullmatch(n) for n in allowed):
            raise AuditRefusal("invalid_audit_operations")
        self.chain_id, self.allowed = chain_id, frozenset(allowed)
        self.max_records, self.max_bytes, self.lock_timeout = max_records, max_bytes, lock_timeout
        self._uncertain = None

    @contextmanager
    def _guard(self):
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise AuditRefusal("audit_posix_lock_required")
        import fcntl
        deadline = time.monotonic()+self.lock_timeout
        acquired = _LOCAL.acquire(timeout=self.lock_timeout)
        if not acquired:
            raise AuditRefusal("audit_busy")
        fd = None
        locked = False
        try:
            lock_path = self.path.with_name(self.path.name+".lock")
            fd = os.open(lock_path, os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
            _regular(fd)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise AuditRefusal("audit_busy")
                    time.sleep(0.005)
            yield
        finally:
            try:
                handle, fd = fd, None
                if handle is not None:
                    try:
                        if locked:
                            fcntl.flock(handle, fcntl.LOCK_UN)
                    finally:
                        # One close only: after-effect close errors may reuse the fd.
                        os.close(handle)
            finally:
                _LOCAL.release()

    def _read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        except FileNotFoundError:
            return [], b"", empty_anchor(self.chain_id)
        try:
            _regular(fd)
            if os.fstat(fd).st_size > self.max_bytes:
                raise AuditRefusal("audit_capacity_exceeded")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(self.max_bytes+1)
        finally:
            os.close(fd)
        if len(raw) > self.max_bytes or (raw and not raw.endswith(b"\n")):
            raise AuditRefusal("audit_incomplete_or_oversized")
        lines = raw.splitlines(keepends=True)
        if len(lines) > self.max_records:
            raise AuditRefusal("audit_capacity_exceeded")
        records, calls = [], {}
        anchor = empty_anchor(self.chain_id)
        for line in lines:
            if len(line) > _MAX_RECORD:
                raise AuditRefusal("audit_record_budget")
            try:
                record = json.loads(line, object_pairs_hook=_unique)
                if (type(record) is not dict or set(record) != {"schema","chain_id","sequence","previous_hash","event","hash"}
                        or record["schema"] != "ciw.audit-record.v1" or record["chain_id"] != self.chain_id
                        or type(record["sequence"]) is not int or record["sequence"] != anchor["sequence"]+1
                        or record["previous_hash"] != anchor["head_hash"] or _encode(record)+b"\n" != line):
                    raise AuditRefusal("invalid_audit_record")
                event = _event(record["event"], self.allowed)
                body = {k:v for k,v in record.items() if k != "hash"}
                if record["hash"] != _digest(body):
                    raise AuditRefusal("audit_hash_mismatch")
                _transition(calls.get(event["call_id"]), event)
                calls[event["call_id"]] = record
            except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
                if isinstance(exc, AuditRefusal):
                    raise
                raise AuditRefusal("invalid_audit_record") from exc
            records.append(record)
            anchor = {**anchor, "sequence":record["sequence"], "head_hash":record["hash"]}
        return records, raw, anchor

    def verify(self, anchor):
        _anchor(anchor, self.chain_id)
        with self._guard():
            records, _, actual = self._read()
            if actual != anchor:
                raise AuditRefusal("audit_anchor_mismatch")
        return deepcopy(records)

    def inspect_unanchored(self):
        """Diagnostic only; the returned head is not an independently trusted anchor."""
        with self._guard():
            records, _, anchor = self._read()
        return {"anchor":anchor, "records":records, "externally_anchored":False}

    def append(self, expected_anchor, event):
        if self._uncertain is not None:
            raise AuditOutcomeUncertain() from self._uncertain
        _anchor(expected_anchor, self.chain_id)
        event = _event(event, self.allowed)
        started = False
        try:
            with self._guard():
                records, raw, actual = self._read()
                matches = [r for r in records if r["event"]["call_id"] == event["call_id"]]
                matching = next((r for r in matches if r["event"] == event), None)
                if matching is not None:
                    prior = {**actual,"sequence":matching["sequence"]-1,"head_hash":matching["previous_hash"]}
                    if expected_anchor != actual and not (expected_anchor == prior and matching == records[-1]):
                        raise AuditRefusal("audit_anchor_conflict")
                    result = {"anchor":actual, "record":matching, "replayed":True}
                else:
                    if actual != expected_anchor:
                        raise AuditRefusal("audit_anchor_conflict")
                    _transition(matches[-1] if matches else None, event)
                    # Check under the same file lock as publication. A fresh
                    # random correlation ID is not a way around crash fencing.
                    if event["phase"] == "admitted" and _unresolved(records):
                        raise AuditRefusal("audit_unresolved_calls")
                    body = {"schema":"ciw.audit-record.v1", "chain_id":self.chain_id,
                            "sequence":actual["sequence"]+1,"previous_hash":actual["head_hash"],"event":event}
                    record = {**body,"hash":_digest(body)}
                    line = _encode(record)+b"\n"
                    latest = {r["event"]["call_id"]:r["event"] for r in records}
                    latest[event["call_id"]] = event
                    # Admission reserves a terminal record and a possible
                    # reconciliation; an unknown terminal keeps that last slot.
                    reserved = sum(2 if e["phase"] == "admitted" else int(_pending_event(e)) for e in latest.values())
                    if (len(line) > _MAX_RECORD or len(records)+1+reserved > self.max_records
                            or len(raw)+len(line)+reserved*_MAX_RECORD > self.max_bytes):
                        raise AuditRefusal("audit_capacity_exceeded")
                    fd = os.open(self.path, os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW, 0o600)
                    try:
                        _regular(fd)
                        if os.fstat(fd).st_size != len(raw):
                            raise AuditRefusal("audit_changed_outside_lock")
                        pending = memoryview(line)
                        started = True
                        while pending:
                            count = os.write(fd, pending)
                            if count <= 0:
                                raise OSError("audit publication made no progress")
                            pending = pending[count:]
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    directory = os.open(self.path.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                    result = {"anchor":{**actual,"sequence":record["sequence"],"head_hash":record["hash"]},
                              "record":record,"replayed":False}
            # Ownership/result transfer occurs only after the guard exits.
            return deepcopy(result)
        except BaseException as exc:
            if started:
                failure = AuditOutcomeUncertain(event["effect_state"], result_ref=event["result_ref"])
                self._uncertain = failure
                raise failure from exc
            raise

    def reconciled(self, trusted_anchor):
        """Only a host that has reconciled its independent anchor may clear this hold."""
        self.verify(trusted_anchor)
        self._uncertain = None


class AuditedCall:
    """Host-only barrier around an operation; callbacks must persist a durable CAS.

    The independent anchor is never synthesized or cached as restart-safe state.
    A pre-dispatch failure prevents execution. A post-effect sink failure fences
    this wrapper and reports uncertainty; it never claims provider rollback.
    """
    def __init__(self, chain: AuditChain, *, read_anchor: Callable, compare_and_set_anchor: Callable,
                 principal_ref: str, result_outcome: Callable | None = None):
        if not isinstance(chain, AuditChain) or not callable(read_anchor) or not callable(compare_and_set_anchor):
            raise ValueError("trusted audit chain and durable anchor callbacks required")
        if type(principal_ref) is not str or not _REF.fullmatch(principal_ref):
            raise ValueError("principal reference must be a digest")
        if result_outcome is not None and not callable(result_outcome):
            raise ValueError("result outcome must be a trusted host callback")
        self.chain, self.read_anchor, self.compare_and_set_anchor = chain, read_anchor, compare_and_set_anchor
        self.result_outcome = result_outcome
        self.principal_ref, self._held = principal_ref, None
        self._mutex = threading.RLock()

    def _publish(self, event):
        before = self.read_anchor()
        result = self.chain.append(before, event)
        if event["phase"] == "admitted" and result["replayed"]:
            # A replaying contender cannot race the actual publisher's anchor
            # CAS or acquire dispatch ownership by acknowledging its record.
            raise AuditReplay(result["record"]["event"])
        if result["anchor"] != before and self.compare_and_set_anchor(before, result["anchor"]) is not True:
            raise AuditOutcomeUncertain(event["effect_state"], result_ref=event["result_ref"])
        return result

    def invoke(self, *, operation, call_id, request_ref, call: Callable, result_ref: Callable):
        if not callable(call) or not callable(result_ref):
            raise ValueError("trusted operation and result digest callbacks required")
        with self._mutex:
            if self._held is not None:
                raise AuditOutcomeUncertain() from self._held
            try:
                anchor = self.read_anchor()
                records = self.chain.verify(anchor)
            except AuditRefusal:
                raise
            except BaseException as exc:
                self._held = exc
                raise AuditOutcomeUncertain("not_started") from exc
            previous = [r["event"] for r in records if r["event"]["call_id"] == call_id]
            if previous:
                event = previous[-1]
                if any(event[k] != value for k,value in (("operation",operation),("principal_ref",self.principal_ref),("request_ref",request_ref))):
                    raise AuditRefusal("audit_call_binding_mismatch")
                raise AuditReplay(event)
            if _unresolved(records):
                raise AuditRefusal("audit_unresolved_calls")
            base = {"call_id":call_id,"operation":operation,"principal_ref":self.principal_ref,
                    "request_ref":request_ref,"result_ref":None,"phase":"admitted", "effect_state":"not_started",
                    "recorded_at":datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z"),
                    "error_code":None, "reconciliation":None}
            try:
                admission = self._publish(base)
                if admission["replayed"]:
                    # Another process may have published exactly these bytes
                    # between verify and append. Replay is never ownership.
                    raise AuditReplay(admission["record"]["event"])
            except AuditReplay:
                raise
            except BaseException as exc:
                self._held = exc
                raise AuditOutcomeUncertain("not_started") from exc
            try:
                value = call()
            except BaseException:
                event = {**base,"phase":"unknown","effect_state":"unknown","error_code":"operation_raised",
                         "recorded_at":datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")}
                try:
                    self._publish(event)
                except BaseException as audit_error:
                    self._held = audit_error
                    raise AuditOutcomeUncertain("unknown") from audit_error
                raise
            try:
                digest = result_ref(value)
                outcome = ({"phase":"succeeded","effect_state":"returned"} if self.result_outcome is None
                           else self.result_outcome(value))
                if (type(outcome) is not dict or set(outcome) != {"phase","effect_state"}
                        or type(outcome["phase"]) is not str or outcome["phase"] not in {"succeeded","failed","refused","unknown"}
                        or type(outcome["effect_state"]) is not str
                        or outcome["effect_state"] not in {"returned","may_have_effects","committed","unknown"}):
                    raise AuditRefusal("invalid_audit_returned_outcome")
                event = {**base,**outcome,"result_ref":digest,
                         "recorded_at":datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")}
                self._publish(event)
            except BaseException as exc:
                self._held = exc
                raise AuditOutcomeUncertain(event["effect_state"] if 'event' in locals() else "returned",
                    result_ref=event.get("result_ref") if 'event' in locals() else None) from exc
            return value

    def resume_after_reconciliation(self):
        with self._mutex:
            anchor = self.read_anchor()
            if _unresolved(self.chain.verify(anchor)):
                raise AuditRefusal("audit_unresolved_calls")
            self.chain.reconciled(anchor)
            self._held = None

    def reconcile_call(self, call_id, *, resolution, validate: Callable):
        """Host-only custody review, never a request/tool-call recovery shortcut.

        The validator must establish custody using independent retained evidence.
        A matching digest alone is not that evidence. The complete reviewed
        context is supplied to it and the closed resolution is hash-chained.
        Unknown outcomes remain unknown and the original call cannot run again.
        """
        if not callable(validate):
            raise ValueError("trusted independent reconciliation validator required")
        resolution = _resolution(resolution)
        with self._mutex:
            before = _anchor(self.read_anchor(), self.chain.chain_id)
            observed = self.chain.inspect_unanchored()
            rows, actual = observed["records"], observed["anchor"]
            sequence = before["sequence"]
            prefix = (empty_anchor(self.chain.chain_id) if sequence == 0 else
                      {**before,"head_hash":rows[sequence-1]["hash"]} if sequence <= len(rows) else None)
            if prefix != before:
                raise AuditRefusal("audit_reconciliation_anchor_not_prefix")
            matches = [r for r in rows if r["event"]["call_id"] == call_id]
            replay = (bool(matches) and matches[-1]["event"]["phase"] == "reconciled"
                      and matches[-1]["event"]["reconciliation"] == resolution)
            previous = matches[-2] if replay and len(matches) >= 2 else matches[-1] if matches else None
            if previous is None or previous["hash"] != resolution["record_hash"]:
                raise AuditRefusal("audit_reconciliation_binding_mismatch")
            if previous["event"]["principal_ref"] != self.principal_ref or previous["event"]["phase"] == "reconciled":
                raise AuditRefusal("audit_reconciliation_binding_mismatch")
            context = {"schema":"ciw.audit-reconciliation-review.v1", "stored_anchor":deepcopy(before),
                       "observed_anchor":deepcopy(actual),"record":deepcopy(previous),
                       "unanchored_records":deepcopy(rows[sequence:]),"resolution":deepcopy(resolution)}
            if validate(context) is not True:
                raise AuditRefusal("audit_reconciliation_not_validated")
            try:
                if before != actual and self.compare_and_set_anchor(before, actual) is not True:
                    raise AuditOutcomeUncertain()
                self.chain.reconciled(actual)
                event = {**previous["event"],"phase":"reconciled","effect_state":resolution["effect_state"],
                         "recorded_at":resolution["reviewed_at"],"error_code":None,"reconciliation":resolution}
                recorded = ({"anchor":actual,"record":deepcopy(matches[-1]),"replayed":True}
                            if replay else self._publish(event))
                if _unresolved(self.chain.verify(self.read_anchor())):
                    raise AuditRefusal("audit_unresolved_calls")
            except BaseException as exc:
                self._held = exc
                raise AuditOutcomeUncertain(resolution["effect_state"]) from exc
            self._held = None
            return recorded
