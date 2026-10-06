"""Durable bounded Link outbox/ingress cache, not another scientific state ledger.

SQLite FULL/WAL transactions commit received bytes before a delivery receipt.
A retry returns the original receipt. No deletion, automatic resend of actions,
network discovery, driver loading, or physical exactly-once claim is provided.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3
import time
import uuid

from . import link_contract as c
from .link_protocol import MAX_FRAME, decode_one

MAX_STORE_BYTES = 64 * 1024 * 1024
MAX_MESSAGES = 4096
_SCHEMA = {
    "meta": "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "messages": "CREATE TABLE messages (ordinal INTEGER PRIMARY KEY AUTOINCREMENT, message_key TEXT UNIQUE NOT NULL, manifest_ref TEXT NOT NULL, wire BLOB NOT NULL, wire_ref TEXT NOT NULL, received_ns TEXT NOT NULL, host_clock TEXT NOT NULL, authentication TEXT NOT NULL)",
    "acks": "CREATE TABLE acks (message_key TEXT PRIMARY KEY REFERENCES messages(message_key), receiver_id TEXT NOT NULL, wire_ref TEXT NOT NULL, receipt BLOB NOT NULL)",
    "state": "CREATE TABLE state (namespace TEXT PRIMARY KEY, revision INTEGER NOT NULL, value BLOB NOT NULL)",
}


def safe_path(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError("Link storage paths must not traverse symlinks")
    return path


def validate_event(value, device):
    c.bounded(value, 65536)
    c.keys(value, {"schema", "device_id", "manifest_ref", "boot_id", "event_sequence", "event", "device_tick", "detail"})
    if (value["schema"] != c.EVENT_SCHEMA or value["device_id"] != device["device_id"]
            or value["manifest_ref"] != c.content_identity(device)):
        raise ValueError("Event device binding mismatch")
    c.text(value["boot_id"]); c.integer(value["event_sequence"]); c.text(value["detail"], maximum=512)
    if value["device_tick"] is not None:
        c.integer(value["device_tick"])
    if value["event"] not in {"overflow", "disconnect", "recording_started", "recording_stopped", "clock_reset"}:
        raise ValueError("Unsupported declared device event")
    return value


def decode_bound(wire, expected_manifest, *, key=None, require_auth=False, offline=False):
    device = c.manifest(expected_manifest)
    frame = decode_one(wire, key=key, require_auth=require_auth, allow_unverified=offline)
    value = c.loads(frame.payload, 65536)
    manifest_ref = c.content_identity(device)
    if frame.kind == "manifest":
        c.manifest(value)
        if c.canonical_json(value) != c.canonical_json(device):
            raise ValueError("Discovered declaration differs from the operator-bound manifest")
        message_key = "manifest:" + manifest_ref
    elif frame.kind == "observation":
        c.validate_observation(value, device)
        message_key = "observation:" + value["measurement_id"]
    else:
        validate_event(value, device)
        message_key = "event:" + c.content_identity({k: value[k] for k in ("device_id", "boot_id", "event_sequence")})
    return frame, value, message_key, manifest_ref


class Journal:
    def __init__(self, path, *, create=False, capacity_bytes=MAX_STORE_BYTES, capacity_messages=MAX_MESSAGES, read_only=False):
        if type(read_only) is not bool or (read_only and create):
            raise ValueError("Read-only inspection requires an existing journal")
        self.read_only = read_only
        self.path = safe_path(path)
        c.integer(capacity_bytes, minimum=MAX_FRAME, maximum=MAX_STORE_BYTES)
        c.integer(capacity_messages, minimum=1, maximum=MAX_MESSAGES)
        self.capacity_bytes = capacity_bytes
        self.capacity_messages = capacity_messages
        if not self.path.parent.is_dir():
            raise ValueError("Create the storage parent directory explicitly")
        for suffix in ("-wal", "-shm", "-journal"):
            safe_path(str(self.path) + suffix)
        if create:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        elif not self.path.is_file() or self.path.stat().st_size > MAX_STORE_BYTES * 2:
            raise ValueError("Require an existing bounded Link journal")
        self.db = sqlite3.connect(
            self.path.as_uri() + "?mode=ro" if read_only else self.path,
            timeout=5, isolation_level=None, uri=read_only)
        try:
            self.db.execute("PRAGMA trusted_schema=OFF")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA busy_timeout=5000")
            if create:
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA synchronous=FULL")
                with self.transaction():
                    for sql in _SCHEMA.values():
                        self.db.execute(sql)
                    self.db.execute("INSERT INTO meta VALUES (?, ?)", ("store_id", "link-store-" + uuid.uuid4().hex))
                    self.db.execute("PRAGMA user_version=1")
            if self.db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("Unknown Link journal version")
            found = dict(self.db.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
            if found != _SCHEMA:
                raise ValueError("Link journal schema changed; no triggers/views or migrations allowed")
            if self.db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Link journal integrity failure")
            if not read_only:
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA synchronous=FULL")
            else:
                self.db.execute("PRAGMA query_only=ON")
            self.store_id = self.db.execute("SELECT value FROM meta WHERE key='store_id'").fetchone()[0]
            c.text(self.store_id)
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def transaction(self):
        if self.read_only:
            raise PermissionError("Read-only Link journal cannot accept writes")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _bounded_rows(self):
        count, size = self.db.execute("SELECT count(*),coalesce(sum(length(wire)),0) FROM messages").fetchone()
        if count > MAX_MESSAGES or size > MAX_STORE_BYTES:
            raise ValueError("Journal content exceeds inspection bound")
        return count, size

    def append(self, wire, *, expected_manifest, key=None, require_auth=False, received_ns=None,
               host_clock="host.unix.wall-clock", runtime_device_id=None):
        frame, value, message_key, manifest_ref = decode_bound(wire, expected_manifest, key=key, require_auth=require_auth)
        if received_ns is None:
            received_ns = time.time_ns()
        c.integer(received_ns); c.text(host_clock)
        wire_ref = c.bytes_ref(wire)
        with self.transaction():
            if runtime_device_id is not None:
                if runtime_device_id != expected_manifest["device_id"]:
                    raise ValueError("Producer runtime device binding differs")
                current = self.state("config:" + runtime_device_id)
                if current is not None and current["value"]["manifest_ref"] != manifest_ref:
                    raise ValueError("Producer runtime configuration became stale before commit")
            count, size = self._bounded_rows()
            previous = self.db.execute("SELECT wire,wire_ref FROM messages WHERE message_key=?", (message_key,)).fetchone()
            if previous:
                if previous != (wire, wire_ref):
                    raise ValueError("Same device/boot/sequence changed bytes; retry conflict")
            else:
                if count >= self.capacity_messages or size + len(wire) > self.capacity_bytes:
                    raise ValueError("Link buffer full; input NOT committed and NOT acknowledged")
                if frame.kind != "manifest" and self.db.execute("SELECT 1 FROM messages WHERE message_key=?", ("manifest:" + manifest_ref,)).fetchone() is None:
                    raise ValueError("Explicitly bound manifest must be retained before observations")
                self.db.execute("INSERT INTO messages(message_key,manifest_ref,wire,wire_ref,received_ns,host_clock,authentication) VALUES(?,?,?,?,?,?,?)",
                                (message_key, manifest_ref, wire, wire_ref, str(received_ns), host_clock, frame.authentication))
        # Only return a receipt after transaction commit and readback.
        stored = self.db.execute("SELECT wire,wire_ref FROM messages WHERE message_key=?", (message_key,)).fetchone()
        if stored != (wire, wire_ref):
            raise ValueError("Committed Link bytes failed readback")
        return self.receipt(message_key)

    def receipt(self, message_key):
        c.text(message_key, maximum=512)
        row = self.db.execute("SELECT wire,wire_ref FROM messages WHERE message_key=?", (message_key,)).fetchone()
        if row is None or c.bytes_ref(row[0]) != row[1]:
            raise ValueError("Cannot acknowledge absent/corrupt bytes")
        value = {"schema": "ciw.link-delivery-receipt.v1", "receiver_id": self.store_id,
                 "message_key": message_key, "wire_ref": row[1], "scope": "durable_local_buffer_only",
                 "state_admission": "not_performed", "physical_verification": "not_performed"}
        return {**value, "receipt_ref": c.content_identity(value)}

    def rows(self):
        self._bounded_rows()
        result = []
        for row in self.db.execute("SELECT ordinal,message_key,manifest_ref,wire,wire_ref,received_ns,host_clock,authentication FROM messages ORDER BY ordinal"):
            ordinal, message_key, manifest_ref, wire, wire_ref, stamp, clock, auth = row
            if type(wire) is not bytes or len(wire) > MAX_FRAME or c.bytes_ref(wire) != wire_ref:
                raise ValueError("Stored wire bytes differ from their identity")
            c.content_ref(manifest_ref); c.text(clock); c.integer(int(stamp))
            if stamp != str(int(stamp)) or auth not in {"not_authenticated", "shared_key_possession_verified"}:
                raise ValueError("Invalid retained gateway receipt metadata")
            result.append({"ordinal": ordinal, "message_key": message_key, "manifest_ref": manifest_ref,
                           "wire": wire, "wire_ref": wire_ref, "received_ns": int(stamp),
                           "host_clock": clock, "authentication_at_capture": auth})
        return result

    def acknowledge_local(self, message_key, receiver):
        """Trusted local sync only; never accept a caller-authored ACK file."""
        receipt = receiver.receipt(message_key)
        ours = self.receipt(message_key)
        if receipt["wire_ref"] != ours["wire_ref"]:
            raise ValueError("Receiver acknowledged different bytes")
        raw = c.bounded(receipt)
        with self.transaction():
            previous = self.db.execute("SELECT receipt FROM acks WHERE message_key=?", (message_key,)).fetchone()
            if previous is not None and previous[0] != raw:
                raise ValueError("Delivery already acknowledged by a different receiver or receipt")
            self.db.execute("INSERT OR IGNORE INTO acks VALUES(?,?,?,?)", (message_key, receiver.store_id, receipt["wire_ref"], raw))
        return receipt

    def sync_to(self, receiver, *, manifests, key=None, require_auth=False, after_receive=None):
        """Explicit local store-to-store copy with crash-safe submission retries.

        ``after_receive`` is a trusted fault-injection hook, never selected by
        device content. No telemetry action or physical acquisition is retried.
        """
        if receiver.store_id == self.store_id:
            raise ValueError("Source and receiver must be distinct stores")
        allowed = {c.content_identity(c.manifest(m)): m for m in manifests}
        copied, retried, receipts = 0, 0, []
        for row in self.rows():
            if row["manifest_ref"] not in allowed:
                raise ValueError("A saved manifest cannot authorize its own receiver registration")
            previous = self.db.execute("SELECT receiver_id,receipt FROM acks WHERE message_key=?", (row["message_key"],)).fetchone()
            if previous is not None:
                if previous[0] != receiver.store_id or c.canonical_json(c.loads(previous[1])) != c.canonical_json(receiver.receipt(row["message_key"])):
                    raise ValueError("Acknowledged destination changed or lost its original evidence")
                retried += 1
                continue
            receipt = receiver.append(row["wire"], expected_manifest=allowed[row["manifest_ref"]], key=key, require_auth=require_auth)
            if after_receive is not None:
                after_receive(receipt)
            receipts.append(self.acknowledge_local(row["message_key"], receiver))
            copied += 1
        return {"schema": "ciw.link-sync.v1", "source_id": self.store_id, "receiver_id": receiver.store_id,
                "new_acknowledgements": copied, "existing_acknowledgements": retried,
                "receipts": receipts, "scope": "local_file_transport_no_network_or_state_admission"}

    def state(self, namespace):
        c.text(namespace)
        row = self.db.execute("SELECT revision,value FROM state WHERE namespace=?", (namespace,)).fetchone()
        if row is None:
            return None
        c.integer(row[0], minimum=1, maximum=2**53 - 1)
        return {"revision": row[0], "value": c.loads(row[1], 65536)}

    def compare_and_set_state(self, namespace, previous_revision, value):
        """Local simulation/update-review state, never authoritative plant state."""
        c.text(namespace); c.integer(previous_revision, maximum=2**53 - 2)
        raw = c.bounded(value, 65536)
        with self.transaction():
            row = self.db.execute("SELECT revision FROM state WHERE namespace=?", (namespace,)).fetchone()
            actual = 0 if row is None else row[0]
            c.integer(actual, maximum=2**53 - 2)
            state_count, state_bytes = self.db.execute("SELECT count(*),coalesce(sum(length(value)),0) FROM state").fetchone()
            if state_count >= MAX_MESSAGES and row is None or state_bytes + len(raw) > MAX_STORE_BYTES:
                raise ValueError("Local review/configuration state budget exceeded")
            if actual != previous_revision:
                raise ValueError("Stale simulation/configuration revision")
            self.db.execute("INSERT INTO state VALUES(?,?,?) ON CONFLICT(namespace) DO UPDATE SET revision=excluded.revision,value=excluded.value",
                            (namespace, actual + 1, raw))
        return actual + 1
