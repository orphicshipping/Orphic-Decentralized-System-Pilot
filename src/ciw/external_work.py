"""Bounded external candidate work, retained alongside native NCP references.

Only the trusted host calls the administration methods. Model clients receive
ExternalWorkHost, never this store or database credentials. No provider registry,
Session, scientific admission, model runtime, or background scheduler is added.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .control_contracts import bytes_ref, content_ref, json_tree, keys
from .core.identities import canonical_json, content_identity, new_identity


class WorkRefusal(ValueError):
    """Stable public refusal code, without credentials or source contents."""


def require(condition, code):
    if not condition:
        raise WorkRefusal(code)


def text(value, maximum=512):
    require(type(value) is str and 0 < len(value) <= maximum and bool(value.strip()),
            "invalid_text")
    return value


def count(value, maximum):
    require(type(value) is int and 1 <= value <= maximum, "invalid_limit")
    return value


def timestamp(value):
    require(type(value) in (int, float) and 0 < value < 2**53, "invalid_time")
    return value


def bounded(value, maximum=65536):
    json_tree(value)
    raw = canonical_json(value)
    require(len(raw.encode()) <= maximum, "document_too_large")
    return raw


class ExternalWorkStore:
    """Transactional local adapter store; filesystem access is host authority.

    Put this database on a local disk owned by the service, not a cloud-synced
    folder. Every operation opens a transaction; separate processes can share it.
    Clients/contexts are provisioned by the operator and cannot be created by MCP.
    """

    def __init__(self, path: Path, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        with self._transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            require(version in (0, 1), "unsupported_store_version")
            db.execute("CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, role TEXT, parent TEXT, enabled INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS contexts (id TEXT PRIMARY KEY, parent TEXT, document TEXT, enabled INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS tasks (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, requester TEXT, request_key TEXT, document TEXT, UNIQUE(requester, request_key))")
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _now(self):
        return timestamp(self.clock())

    # Trusted host administration. None of these are model-facing tools.
    def provision_client(self, client_id, *, role, parent_ref):
        text(client_id, 128)
        text(parent_ref)
        require(role in ("requester", "worker"), "invalid_role")
        with self._transaction() as db:
            old = db.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
            if old:
                require((old["role"], old["parent"]) == (role, parent_ref), "client_binding_conflict")
            else:
                db.execute("INSERT INTO clients VALUES (?,?,?,1)", (client_id, role, parent_ref))

    def revoke_client(self, client_id):
        with self._transaction() as db:
            db.execute("UPDATE clients SET enabled=0 WHERE id=?", (text(client_id, 128),))

    def retain_context(self, *, parent_ref, snapshot_ref, source_id, version_ref,
                       source_bytes, expected_source_hash, start, end, provenance=None):
        """Retain one approved UTF-8 excerpt with exact full-source byte binding.

        The host supplies the approved source version/hash and disclosure scope.
        Hash equality checks bytes, not publication rights or scientific validity.
        """
        for value in (parent_ref, snapshot_ref, source_id, version_ref):
            text(value)
        content_ref(expected_source_hash)
        require(type(source_bytes) is bytes and 0 < len(source_bytes) <= 8 * 1024 * 1024,
                "invalid_source_bytes")
        require(bytes_ref(source_bytes) == expected_source_hash, "source_hash_mismatch")
        require(type(start) is int and type(end) is int and 0 <= start < end <= len(source_bytes)
                and end - start <= 8192, "invalid_source_window")
        excerpt = source_bytes[start:end].decode("utf-8")
        context = {"schema": "ciw.external-work-context.v1", "parent_ref": parent_ref,
                   "snapshot_ref": snapshot_ref, "source_id": source_id,
                   "version_ref": version_ref, "source_hash": expected_source_hash,
                   "locator": {"kind": "utf8_byte_window", "start": start, "end": end},
                   "text": excerpt, "excerpt_hash": bytes_ref(source_bytes[start:end])}
        if provenance is not None:
            require(type(provenance) is dict, "invalid_provenance")
            context["provenance"] = json.loads(bounded(provenance, 16384))
        raw = bounded(context)
        context_id = content_identity(context)
        with self._transaction() as db:
            db.execute("INSERT OR IGNORE INTO contexts VALUES (?,?,?,1)",
                       (context_id, parent_ref, raw))
        return context_id

    def revoke_context(self, context_id):
        with self._transaction() as db:
            db.execute("UPDATE contexts SET enabled=0 WHERE id=?", (content_ref(context_id),))

    def authorize(self, task_id, *, expected_hash, approval_ref, worker_id,
                  expires_at, lease_seconds=300, max_attempts=3, max_result_bytes=16384):
        """Record a host-verified approval of this exact task and worker scope."""
        grant = {"task_hash": content_ref(expected_hash), "approval_ref": text(approval_ref),
                 "worker_id": text(worker_id, 128), "expires_at": timestamp(expires_at),
                 "lease_seconds": count(lease_seconds, 3600),
                 "max_attempts": count(max_attempts, 8),
                 "max_result_bytes": count(max_result_bytes, 32768),
                 "allowed_operations": ["context.read", "candidate.submit"],
                 "destination": "retained_task_submission"}
        with self._transaction() as db:
            task = self._load(db, task_id)
            require(task["task_hash"] == expected_hash, "task_hash_mismatch")
            worker = self._client(db, worker_id, "worker")
            require(worker["parent"] == task["body"]["parent_ref"], "scope_refused")
            self._contexts(db, task)
            require(expires_at > self._now(), "authorization_expired")
            if task["authorization"] is not None:
                require(task["authorization"] == grant, "authorization_conflict")
                return task
            require(task["execution_status"] == "proposed", "task_not_proposed")
            task["authorization"] = grant
            task["execution_status"] = "authorized"
            self._save(db, task)
            return task

    def record_review(self, task_id, *, result_id, decision, reviewer_ref, scope, findings):
        """Retain a trusted review decision, never a scientific verification ID."""
        require(decision in ("accepted", "rejected", "needs_review"), "invalid_decision")
        review = {"result_id": text(result_id, 128), "decision": decision,
                  "reviewer_ref": text(reviewer_ref), "scope": text(scope),
                  "findings": text(findings, 8192)}
        with self._transaction() as db:
            task = self._load(db, task_id)
            require(task["submission"] is not None and
                    task["submission"]["result_id"] == result_id, "result_mismatch")
            require(task["execution_status"] == "submitted", "task_not_submitted")
            if task["review"]:
                require(all(task["review"][k] == v for k, v in review.items()), "review_conflict")
            else:
                task["review"] = {**review, "decision_id": new_identity("decision"),
                                  "recorded_at": self._now()}
                self._save(db, task)
            return task["review"]

    def _client(self, db, client_id, role=None):
        client = db.execute("SELECT * FROM clients WHERE id=?", (text(client_id, 128),)).fetchone()
        require(client is not None and client["enabled"] == 1, "client_refused")
        require(role is None or client["role"] == role, "role_refused")
        return client

    def _load(self, db, task_id):
        row = db.execute("SELECT document FROM tasks WHERE id=?", (text(task_id, 128),)).fetchone()
        require(row is not None, "task_unavailable")
        task = json.loads(row["document"])
        require(task["task_id"] == task_id and content_identity(task["body"]) == task["task_hash"],
                "task_integrity_failure")
        if task["submission"] is not None:
            require(content_identity(task["submission"]["artifact"]) == task["submission"]["artifact_hash"],
                    "submission_integrity_failure")
        return task

    def _save(self, db, task):
        db.execute("UPDATE tasks SET document=? WHERE id=?", (bounded(task, 262144), task["task_id"]))

    def _contexts(self, db, task):
        contexts = {}
        for context_id in task["body"]["context_ids"]:
            row = db.execute("SELECT * FROM contexts WHERE id=?", (context_id,)).fetchone()
            require(row is not None and row["enabled"] == 1 and
                    row["parent"] == task["body"]["parent_ref"], "context_unavailable")
            context = json.loads(row["document"])
            require(content_identity(context) == context_id and
                    bytes_ref(context["text"].encode()) == context["excerpt_hash"],
                    "context_integrity_failure")
            contexts[context_id] = context
        return contexts

    def _access(self, db, client_id, task):
        client = self._client(db, client_id)
        require(client["parent"] == task["body"]["parent_ref"], "scope_refused")
        if client["role"] == "requester":
            require(task["requester"] == client_id, "scope_refused")
        else:
            grant = task["authorization"]
            require(grant is not None and grant["worker_id"] == client_id, "scope_refused")
            require(grant["expires_at"] > self._now(), "authorization_expired")
            require(task["execution_status"] != "cancelled", "task_cancelled")
        return client

    def _eligible(self, db, task):
        grant = task["authorization"]
        require(grant is not None and grant["expires_at"] > self._now(), "authorization_expired")
        self._client(db, grant["worker_id"], "worker")
        self._contexts(db, task)
        require(task["execution_status"] in ("authorized", "claimed"), "task_not_eligible")
        if task["execution_status"] == "claimed":
            require(task["attempts"][-1]["expires_at"] <= self._now(), "task_already_claimed")
        require(len(task["attempts"]) < grant["max_attempts"], "attempt_budget_exhausted")

    # Data-plane methods; client_id must come from a trusted transport binding.
    def propose(self, client_id, *, request_key, task):
        text(request_key, 128)
        keys(task, {"parent_ref", "instruction", "expected_output", "context_ids"})
        text(task["parent_ref"])
        text(task["instruction"], 8192)
        text(task["expected_output"], 4096)
        ids = task["context_ids"]
        require(type(ids) is list and 1 <= len(ids) <= 4, "invalid_contexts")
        for context_id in ids:
            content_ref(context_id)
        require(len(set(ids)) == len(ids), "duplicate_contexts")
        body = json.loads(bounded({**task, "revision": 1, "operation": "candidate_extraction"}))
        task_hash = content_identity(body)
        with self._transaction() as db:
            client = self._client(db, client_id, "requester")
            require(client["parent"] == body["parent_ref"], "scope_refused")
            self._contexts(db, {"body": body})
            row = db.execute("SELECT id FROM tasks WHERE requester=? AND request_key=?",
                             (client_id, request_key)).fetchone()
            if row:
                existing = self._load(db, row["id"])
                require(existing["task_hash"] == task_hash, "idempotency_conflict")
                return existing
            total = db.execute("SELECT COUNT(*) FROM tasks WHERE requester=?", (client_id,)).fetchone()[0]
            require(total < 1024, "task_budget_exhausted")
            retained = {"schema": "ciw.external-work-task.v1", "task_id": "external-task-" + uuid.uuid4().hex,
                        "requester": client_id, "body": body, "task_hash": task_hash,
                        "created_at": self._now(), "authorization": None,
                        "execution_status": "proposed", "attempts": [], "submission": None,
                        "review": None, "deliveries": [], "cancellation": None}
            db.execute("INSERT INTO tasks(id,requester,request_key,document) VALUES (?,?,?,?)",
                       (retained["task_id"], client_id, request_key, bounded(retained)))
            return retained

    def read(self, client_id, *, task_id):
        with self._transaction() as db:
            task = self._load(db, task_id)
            self._access(db, client_id, task)
            self._contexts(db, task)
            return task

    def context(self, client_id, *, task_id, context_id):
        with self._transaction() as db:
            task = self._load(db, task_id)
            self._access(db, client_id, task)
            contexts = self._contexts(db, task)
            require(context_id in contexts, "context_unavailable")
            return {"context_id": context_id, **contexts[context_id]}

    def discover(self, client_id, *, after=0, limit=20):
        require(type(after) is int and 0 <= after <= 2**53 - 1, "invalid_cursor")
        count(limit, 50)
        with self._transaction() as db:
            client = self._client(db, client_id)
            rows = db.execute("SELECT seq,id FROM tasks WHERE seq>? ORDER BY seq LIMIT 100", (after,)).fetchall()
            items = []
            scanned = after
            for row in rows:
                scanned = row["seq"]
                try:
                    task = self._load(db, row["id"])
                    self._access(db, client_id, task)
                    self._contexts(db, task)
                    if client["role"] == "worker":
                        self._eligible(db, task)
                except WorkRefusal:
                    continue
                items.append({k: task[k] for k in ("task_id", "task_hash", "execution_status")})
                if len(items) == limit:
                    break
            more = db.execute("SELECT 1 FROM tasks WHERE seq>? LIMIT 1", (scanned,)).fetchone() is not None
            return {"items": items, "next_after": scanned if more else None,
                    "poll_again_from": 0}

    def claim(self, client_id, *, task_id, expected_hash, attempt_id, bot_label):
        text(attempt_id, 128)
        text(bot_label, 128)
        with self._transaction() as db:
            self._client(db, client_id, "worker")
            task = self._load(db, task_id)
            self._access(db, client_id, task)
            self._contexts(db, task)
            require(task["task_hash"] == expected_hash, "task_hash_mismatch")
            for attempt in task["attempts"]:
                if attempt["attempt_id"] == attempt_id:
                    require(attempt["worker_id"] == client_id and attempt["bot_label"] == bot_label,
                            "idempotency_conflict")
                    # Never extend a lease on retry; report its original expiry.
                    return {**attempt, "active": task["execution_status"] == "claimed" and
                            task["attempts"][-1]["attempt_id"] == attempt_id and
                            attempt["expires_at"] > self._now()}
            self._eligible(db, task)
            now = self._now()
            grant = task["authorization"]
            attempt = {"attempt_id": attempt_id, "worker_id": client_id,
                       "bot_label": bot_label, "claimed_at": now,
                       "expires_at": min(now + grant["lease_seconds"], grant["expires_at"])}
            task["attempts"].append(attempt)
            task["execution_status"] = "claimed"
            self._save(db, task)
            return {**attempt, "active": True}

    def submit(self, client_id, *, task_id, expected_hash, attempt_id, result):
        keys(result, {"outcome", "candidate", "citations", "limitations"})
        require(result["outcome"] in ("completed", "blocked"), "invalid_outcome")
        text(result["candidate"], 16384)
        require(type(result["limitations"]) is list and 1 <= len(result["limitations"]) <= 16,
                "limitations_required")
        for limitation in result["limitations"]:
            text(limitation, 1024)
        citations = result["citations"]
        require(type(citations) is list and len(citations) <= 16 and
                (result["outcome"] == "blocked" or len(citations) > 0), "citations_required")
        for citation in citations:
            keys(citation, {"context_id", "quote"})
            content_ref(citation["context_id"])
            text(citation["quote"], 4096)
        with self._transaction() as db:
            self._client(db, client_id, "worker")
            task = self._load(db, task_id)
            self._access(db, client_id, task)
            require(task["task_hash"] == expected_hash, "task_hash_mismatch")
            contexts = self._contexts(db, task)
            bounded(result, task["authorization"]["max_result_bytes"])
            for citation in citations:
                require(citation["context_id"] in contexts and
                        citation["quote"] in contexts[citation["context_id"]]["text"],
                        "citation_mismatch")
            artifact_hash = content_identity(result)
            if task["submission"]:
                submission = task["submission"]
                require(submission["attempt_id"] == attempt_id and
                        submission["worker_id"] == client_id and
                        submission["artifact_hash"] == artifact_hash, "submission_conflict")
                return submission
            require(task["execution_status"] == "claimed", "task_not_claimed")
            attempt = task["attempts"][-1]
            require(attempt["attempt_id"] == attempt_id and attempt["worker_id"] == client_id and
                    attempt["expires_at"] > self._now(), "stale_attempt")
            task["submission"] = {"schema": "ciw.external-work-submission.v1",
                                  "result_id": new_identity("result"), "attempt_id": attempt_id,
                                  "worker_id": client_id, "artifact_hash": artifact_hash,
                                  "artifact": json.loads(canonical_json(result)),
                                  "submitted_at": self._now(),
                                  "structural_status": "passed", "semantic_status": "unreviewed",
                                  "verification_id": None, "physical_qualification": "not_established"}
            task["execution_status"] = "submitted"
            self._save(db, task)
            return task["submission"]

    def cancel(self, client_id, *, task_id, reason):
        text(reason, 1024)
        with self._transaction() as db:
            self._client(db, client_id, "requester")
            task = self._load(db, task_id)
            self._access(db, client_id, task)
            if task["cancellation"]:
                require(task["cancellation"]["reason"] == reason, "idempotency_conflict")
                return task
            require(task["execution_status"] != "submitted", "already_submitted")
            task["execution_status"] = "cancelled"
            task["cancellation"] = {"reason": reason, "client_id": client_id, "at": self._now()}
            self._save(db, task)
            return task

    def wake(self, task_id, *, expected_hash, delivery_id, adapter=None):
        """Host-only optional signal. Unknown deliveries are never auto-retried.

        Commit the delivery intent before I/O. Cancellation during I/O can still
        wake a routine; every later worker call rechecks retained authorization.
        """
        text(delivery_id, 128)
        with self._transaction() as db:
            task = self._load(db, task_id)
            require(task["task_hash"] == expected_hash, "task_hash_mismatch")
            for delivery in task["deliveries"]:
                if delivery["delivery_id"] == delivery_id:
                    return delivery
            self._eligible(db, task)
            if adapter is None:
                return {"delivery_id": delivery_id, "status": "disabled", "polling_available": True}
            require(len(task["deliveries"]) < 4, "delivery_budget_exhausted")
            delivery = {"delivery_id": delivery_id, "status": "unknown", "prepared_at": self._now()}
            task["deliveries"].append(delivery)
            self._save(db, task)
        payload = {"task_id": task_id, "task_hash": expected_hash, "delivery_id": delivery_id}
        try:
            code = adapter.send(payload)
            status = "acknowledged" if type(code) is int and code == 200 else "failed"
        except Exception:
            # Never retain exception text: it can contain URLs, keys or source data.
            status = "unknown"
        with self._transaction() as db:
            task = self._load(db, task_id)
            delivery = next(d for d in task["deliveries"] if d["delivery_id"] == delivery_id)
            delivery["status"] = status
            delivery["recorded_at"] = self._now()
            self._save(db, task)
            return delivery
