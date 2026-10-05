"""Transactional local persistence for the existing NET capture journal.

SQLite serializes local writers; it does not become the canonical State Ledger.
Every load fully replays original updates and checks their unchanged QC receipts.
Acknowledgements leave this API only after commit. No sensor or machine IO.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

from .core.identities import content_identity, new_identity, validate_identity
from .manufacturing_contracts import bounded, ref, text
from .toolpoint_stream import AUTHORITY, CaptureJournal, UPDATE_SCHEMA, validate_spec
from . import toolpoint_stream_codec as codec

APPLICATION_ID = 0x4E515343  # NQSC: Notations QC stream cache
FORMAT_VERSION = 1
MAX_DATABASE_BYTES = 128 * 1024 * 1024
MAX_STREAMS = 128
MAX_IMPORTS_PER_STREAM = 16
OPERATION = "polymer.toolpoint-stream.local-commit.v1"

_TABLES = {
    "metadata": """CREATE TABLE metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        format_version INTEGER NOT NULL CHECK (format_version = 1),
        store_id TEXT NOT NULL)""",
    "streams": """CREATE TABLE streams (
        stream_id TEXT PRIMARY KEY NOT NULL,
        stream_key TEXT NOT NULL UNIQUE,
        spec_ref TEXT NOT NULL,
        spec_json TEXT NOT NULL CHECK (length(spec_json) <= 2097152),
        registration_id TEXT NOT NULL)""",
    "imports": """CREATE TABLE imports (
        stream_id TEXT NOT NULL REFERENCES streams(stream_id),
        source_report_ref TEXT NOT NULL,
        record_json TEXT NOT NULL CHECK (length(record_json) <= 2097152),
        import_ref TEXT NOT NULL UNIQUE,
        PRIMARY KEY (stream_id, source_report_ref))""",
    "entries": """CREATE TABLE entries (
        stream_id TEXT NOT NULL REFERENCES streams(stream_id),
        sequence INTEGER NOT NULL CHECK (sequence >= 0 AND sequence < 64),
        delta_json TEXT NOT NULL CHECK (length(delta_json) <= 2097152),
        receipt_ref TEXT NOT NULL UNIQUE,
        acknowledgement_json TEXT NOT NULL CHECK (length(acknowledgement_json) <= 2097152),
        commit_ref TEXT NOT NULL UNIQUE,
        PRIMARY KEY (stream_id, sequence))""",
}
_TRIGGERS = {
    f"immutable_{table}_{verb.lower()}":
    f"CREATE TRIGGER immutable_{table}_{verb.lower()} BEFORE {verb} ON {table} "
    "BEGIN SELECT RAISE(ABORT, 'Immutable QC evidence; updates/deletions refused'); END"
    for table in _TABLES for verb in ("UPDATE", "DELETE")
}


def _identifier(value: str) -> str:
    text(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Control characters are not valid local stream identifiers")
    return value


def _path(value: str | Path, *, existing: bool) -> Path:
    p = Path(os.path.abspath(os.fspath(value)))
    for part in [p, *p.parents]:
        if part.is_symlink():
            raise ValueError("Local QC storage rejects symlink paths")
    if not p.parent.is_dir():
        raise ValueError("Require an existing trusted local parent directory")
    if existing:
        info = p.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_DATABASE_BYTES:
            raise ValueError("Require a single-link bounded regular QC database")
        for suffix in ("-journal", "-wal", "-shm"):
            side = Path(str(p) + suffix)
            if side.is_symlink() or (side.exists() and not side.is_file()):
                raise ValueError("Invalid QC database journal sidecar")
            if suffix != "-journal" and side.exists():
                raise ValueError("WAL sidecars are outside this rollback-journal profile")
    elif p.exists():
        raise FileExistsError("Refusing to replace an existing database path")
    return p


def _connect(path: Path, timeout_s: float) -> sqlite3.Connection:
    # mode=rw never silently creates a missing store. No extension loading or user SQL.
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None,
                         timeout=timeout_s)
    try:
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024 * 1024)
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=EXTRA")
        db.execute("PRAGMA busy_timeout=" + str(int(timeout_s * 1000)))
        if (db.execute("PRAGMA foreign_keys").fetchone()[0] != 1
                or db.execute("PRAGMA synchronous").fetchone()[0] != 3
                or db.execute("PRAGMA journal_mode").fetchone()[0] != "delete"):
            raise ValueError("Required rollback-journal durability settings unavailable")
        pages = MAX_DATABASE_BYTES // db.execute("PRAGMA page_size").fetchone()[0]
        if db.execute(f"PRAGMA max_page_count={pages}").fetchone()[0] > pages:
            raise ValueError("QC database exceeds the configured page budget")
        return db
    except BaseException:
        db.close()
        raise


def _normal(sql: str) -> str:
    return " ".join(sql.split())


def _check_schema(db: sqlite3.Connection) -> str:
    if (db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or db.execute("PRAGMA user_version").fetchone()[0] != FORMAT_VERSION):
        raise ValueError("Not a supported NET QC stream database")
    rows = db.execute("SELECT name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchmany(20)
    expected = {k: _normal(v) for k, v in {**_TABLES, **_TRIGGERS}.items()}
    if {name: _normal(sql or "") for name, sql in rows} != expected:
        raise ValueError("QC database schema or immutability guards changed")
    rows = db.execute("SELECT singleton, format_version, store_id FROM metadata").fetchmany(2)
    if len(rows) != 1 or rows[0][:2] != (1, FORMAT_VERSION):
        raise ValueError("Invalid QC store metadata")
    validate_identity(rows[0][2], "session")
    return rows[0][2]


def _commit(db: sqlite3.Connection) -> None:
    db.execute("COMMIT")


@contextmanager
def _transaction(path: Path, *, timeout_s: float, write: bool):
    _path(path, existing=True)
    db = _connect(path, timeout_s)
    try:
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        store_id = _check_schema(db)
        yield db, store_id
        _commit(db)
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def initialize(path: str | Path) -> dict:
    """Publish a new database create-only; failed initialization never replaces data."""
    target = _path(path, existing=False)
    fd, temporary = tempfile.mkstemp(prefix=".net-qc-init-", dir=target.parent)
    os.close(fd)
    temp = Path(temporary)
    db = None
    store_id = new_identity("session")
    try:
        db = _connect(temp, 1.0)
        db.execute("BEGIN IMMEDIATE")
        for statement in [*_TABLES.values(), *_TRIGGERS.values()]:
            db.execute(statement)
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute(f"PRAGMA user_version={FORMAT_VERSION}")
        db.execute("INSERT INTO metadata VALUES(1, ?, ?)", (FORMAT_VERSION, store_id))
        _check_schema(db)
        _commit(db)
        db.close()
        db = None
        with temp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.link(temp, target)  # Exclusive publication, never overwrite.
        # Flush directory publication where supported. A post-publication failure
        # may leave a valid DB; never delete it or claim it was not created.
        if os.name == "posix":
            directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if db is not None:
            db.close()
        temp.unlink(missing_ok=True)
        Path(str(temp) + "-journal").unlink(missing_ok=True)
    return {"schema": "ciw.toolpoint-store-created.v1", "store_id": store_id,
            "storage_status": "CREATED", "authority": dict(AUTHORITY)}


def _stream_key(spec: dict) -> str:
    # Different plan revisions may not silently create clean parallel histories
    # for the same declared configuration/clock/cycle/source slot.
    binding, plan = spec["binding"], spec["plan"]
    return content_identity({"configuration_ref": plan["configuration_ref"],
        "clock_id": plan["clock_id"], "cycle_id": binding["cycle_id"],
        "source_kind": binding["source_kind"]})


def _ack(store_id, stream_id, spec_ref, update, delta, receipt, execution_id):
    body = {"schema": "ciw.toolpoint-store-acknowledgement.v1", "operation_id": OPERATION,
        "store_id": store_id, "stream_id": stream_id, "spec_ref": spec_ref,
        "sequence": update["sequence"], "update_ref": content_identity(update),
        "delta_ref": content_identity(delta), "receipt": deepcopy(receipt),
        "write_execution_id": execution_id, "finalized": update["final"],
        "storage_status": "COMMITTED", "quality_status": receipt["status"],
        "authority": dict(AUTHORITY)}
    return {**body, "commit_ref": content_identity(body)}


def _replay(db, store_id, stream_id, expected_spec_ref=None):
    row = db.execute("SELECT stream_key, spec_ref, spec_json, registration_id FROM streams WHERE stream_id=?",
                     (stream_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown QC stream; implicit registration is forbidden")
    stream_key, spec_ref, raw, registration_id = row
    spec = codec.loads(raw)
    validate_spec(spec)
    validate_identity(registration_id, "execution")
    if spec_ref != content_identity(spec) or stream_key != _stream_key(spec):
        raise ValueError("QC stream specification content mismatch")
    if expected_spec_ref is not None and ref(expected_spec_ref) != spec_ref:
        raise ValueError("QC stream differs from the caller's specification anchor")
    journal = CaptureJournal(spec)
    previous, acknowledgements, stats = None, [], {"stored_sample_count": 0, "delta_json_bytes": 0,
                                                   "cumulative_update_json_bytes": 0, "imported_checkpoint_count": 0}
    rows = db.execute("SELECT sequence, delta_json, receipt_ref, acknowledgement_json, commit_ref "
                      "FROM entries WHERE stream_id=? ORDER BY sequence", (stream_id,)).fetchmany(65)
    if len(rows) > spec["limits"]["max_updates"]:
        raise ValueError("Stored stream exceeds its update budget")
    for index, (sequence, delta_raw, receipt_ref, ack_raw, commit_ref) in enumerate(rows):
        if type(sequence) is not int or sequence != index:
            raise ValueError("Stored stream sequence gap")
        delta = codec.loads(delta_raw)
        update = codec.decode(previous, delta, profile=spec["plan"]["schema"])
        if update["sequence"] != sequence:
            raise ValueError("Delta and storage sequence differ")
        receipt = journal.append(update)
        ack = codec.loads(ack_raw)
        validate_identity(ack.get("write_execution_id"), "execution")
        expected = _ack(store_id, stream_id, spec_ref, update, delta, receipt, ack["write_execution_id"])
        if (content_identity(ack) != content_identity(expected) or commit_ref != expected["commit_ref"]
                or receipt_ref != receipt["receipt_ref"]):
            raise ValueError("Stored acknowledgement differs from complete QC replay")
        acknowledgements.append(ack)
        stats["stored_sample_count"] += sum(len(c["samples"]) for c in delta["channels"])
        stats["delta_json_bytes"] += len(delta_raw.encode("utf-8"))
        stats["cumulative_update_json_bytes"] += len(codec.dumps(update).encode("utf-8"))
        previous = update
    stats["imported_checkpoint_count"] = _check_imports(db, store_id, stream_id, spec_ref, journal, acknowledgements)
    return journal, acknowledgements, stats


def _check_imports(db, store_id, stream_id, spec_ref, journal, acknowledgements):
    rows = db.execute("SELECT source_report_ref, record_json, import_ref FROM imports WHERE stream_id=? "
                      "ORDER BY source_report_ref", (stream_id,)).fetchmany(MAX_IMPORTS_PER_STREAM + 1)
    if len(rows) > MAX_IMPORTS_PER_STREAM:
        raise ValueError("Imported checkpoint provenance budget exceeded")
    if not rows:
        return 0
    report = journal.report()
    heads = [ack["receipt"]["receipt_ref"] for ack in acknowledgements]
    for source_ref, raw, import_ref in rows:
        record = codec.loads(raw)
        from .manufacturing_contracts import keys
        keys(record, {"schema", "store_id", "stream_id", "spec_ref", "source_report_ref",
            "source_execution_id", "source_head_ref", "source_result_ref", "local_head_after_import",
            "import_execution_id", "authority", "import_ref"})
        if (record["schema"] != "ciw.toolpoint-store-import-record.v1" or record["store_id"] != store_id
                or record["stream_id"] != stream_id or record["spec_ref"] != spec_ref
                or record["source_report_ref"] != source_ref or record["import_ref"] != import_ref
                or content_identity(record["authority"]) != content_identity(AUTHORITY)
                or identity_without(record, "import_ref") != import_ref):
            raise ValueError("Imported checkpoint provenance content mismatch")
        validate_identity(record["source_execution_id"], "execution")
        validate_identity(record["import_execution_id"], "execution")
        if (record["source_head_ref"] not in heads or record["local_head_after_import"] not in heads
                or heads.index(record["source_head_ref"]) > heads.index(record["local_head_after_import"])):
            raise ValueError("Imported checkpoint provenance refers to absent or reversed history")
        last = heads.index(record["source_head_ref"])
        source = CaptureJournal(report["spec"])
        for update in report["updates"][:last + 1]:
            source.append(update)
        reconstructed = source.report()
        # Recover original occurrence identity only to check its retained source hash.
        # The actual replay execution remains separately identified by its journal.
        reconstructed["execution_id"] = record["source_execution_id"]
        if (content_identity(reconstructed) != source_ref
                or reconstructed["result_ref"] != record["source_result_ref"]):
            raise ValueError("Imported source report cannot be reconstructed exactly")
    return len(rows)


def identity_without(value: dict, field: str) -> str:
    return content_identity({k: v for k, v in value.items() if k != field})


def _view(store_id, stream_id, spec_ref, journal, acknowledgements, stats):
    report = journal.report() if acknowledgements else None
    return {"schema": "ciw.toolpoint-store-head.v1", "store_id": store_id, "stream_id": stream_id,
        "spec_ref": spec_ref, "head_ref": journal.head_ref,
        "commit_ref": acknowledgements[-1]["commit_ref"] if acknowledgements else None,
        "accepted_updates": len(acknowledgements), "finalized": journal.finalized,
        "quality_status": report["result"]["status"] if report else "AWAITING_EVIDENCE",
        "storage_statistics": stats, "authority": dict(AUTHORITY)}


class StreamStore:
    """Short-lived SQLite transactions; multiple processes may safely call this API.

The directory and OS are trusted. This is bounded local persistence, not a network
filesystem service, authenticity system, device driver or high-rate real-time path.
"""
    def __init__(self, path: str | Path, *, timeout_s: float = 1.0):
        if type(timeout_s) not in (int, float) or not 0 <= timeout_s <= 30:
            raise ValueError("Require a finite lock timeout in [0,30] seconds")
        self.path = _path(path, existing=True)
        self.timeout_s = float(timeout_s)
        with _transaction(self.path, timeout_s=self.timeout_s, write=False):
            pass

    def register(self, stream_id: str, spec: dict) -> dict:
        _identifier(stream_id)
        validate_spec(spec)
        spec = deepcopy(spec)
        spec_ref = content_identity(spec)
        with _transaction(self.path, timeout_s=self.timeout_s, write=True) as (db, store_id):
            self._register(db, stream_id, spec, spec_ref)
            journal, acks, stats = _replay(db, store_id, stream_id, spec_ref)
            result = _view(store_id, stream_id, spec_ref, journal, acks, stats)
        return result

    def _register(self, db, stream_id, spec, spec_ref):
        found = db.execute("SELECT spec_ref FROM streams WHERE stream_id=?", (stream_id,)).fetchone()
        if found is None:
            if db.execute("SELECT count(*) FROM streams").fetchone()[0] >= MAX_STREAMS:
                raise ValueError("Local QC store stream budget exhausted")
            if db.execute("SELECT 1 FROM streams WHERE stream_key=?", (_stream_key(spec),)).fetchone():
                raise ValueError("Cycle slot already has a registered stream; cannot fork its quality history")
            db.execute("INSERT INTO streams VALUES(?,?,?,?,?)", (stream_id, _stream_key(spec), spec_ref,
                       codec.dumps(spec), new_identity("execution")))

    def _admit(self, db, store_id, stream_id, journal, acks, update, spec_ref):
        bounded(update)
        original_count = len(acks)
        receipt = journal.append(update)
        if update["sequence"] < original_count:
            return deepcopy(acks[update["sequence"]])  # Exact idempotent retry, no new write identity.
        report = journal.report()
        previous = report["updates"][-2] if original_count else None
        delta = codec.encode(previous, update, profile=report["spec"]["plan"]["schema"])
        ack = _ack(store_id, stream_id, spec_ref, update, delta, receipt, report["execution_id"])
        db.execute("INSERT INTO entries VALUES(?,?,?,?,?,?)", (stream_id, update["sequence"],
                   codec.dumps(delta), receipt["receipt_ref"], codec.dumps(ack), ack["commit_ref"]))
        return ack

    def import_report(self, stream_id: str, report: dict, *, expected_spec_ref: str,
                      expected_head_ref: str) -> dict:
        """Migrate a verified checkpoint atomically; no partial imported prefix.

Required anchors bind the supplied source. An older valid checkpoint may be an
idempotent subset of a newer local stream, but can never replace its newer head.
Storage acknowledgements are new occurrences; old scientific receipts are exact.
"""
        _identifier(stream_id)
        ref(expected_spec_ref)
        ref(expected_head_ref)
        bounded(report)
        report = deepcopy(report)
        CaptureJournal.restore(report, expected_spec_ref=expected_spec_ref,
                               expected_head_ref=expected_head_ref)
        spec = report["spec"]
        with _transaction(self.path, timeout_s=self.timeout_s, write=True) as (db, store_id):
            self._register(db, stream_id, spec, expected_spec_ref)
            journal, acks, _ = _replay(db, store_id, stream_id, expected_spec_ref)
            for update in report["updates"]:
                prior_count = len(acks)
                ack = self._admit(db, store_id, stream_id, journal, acks, update, expected_spec_ref)
                if update["sequence"] == prior_count:
                    acks.append(ack)
            source_ref = content_identity(report)
            if not db.execute("SELECT 1 FROM imports WHERE stream_id=? AND source_report_ref=?",
                              (stream_id, source_ref)).fetchone():
                if db.execute("SELECT count(*) FROM imports WHERE stream_id=?", (stream_id,)).fetchone()[0] >= MAX_IMPORTS_PER_STREAM:
                    raise ValueError("Imported checkpoint provenance budget exhausted")
                origin = {"schema": "ciw.toolpoint-store-import-record.v1", "store_id": store_id,
                    "stream_id": stream_id, "spec_ref": expected_spec_ref, "source_report_ref": source_ref,
                    "source_execution_id": report["execution_id"], "source_head_ref": expected_head_ref,
                    "source_result_ref": report["result_ref"], "local_head_after_import": journal.head_ref,
                    "import_execution_id": new_identity("execution"), "authority": dict(AUTHORITY)}
                origin["import_ref"] = content_identity(origin)
                db.execute("INSERT INTO imports VALUES(?,?,?,?)", (stream_id, source_ref, codec.dumps(origin), origin["import_ref"]))
            journal, acks, stats = _replay(db, store_id, stream_id, expected_spec_ref)
            result = _view(store_id, stream_id, expected_spec_ref, journal, acks, stats)
            result.update(imported_source_head_ref=expected_head_ref,
                          imported_source_execution_id=report["execution_id"],
                          storage_status="COMMITTED")
        return result

    def append(self, stream_id: str, update: dict, *, expected_spec_ref: str) -> dict:
        """The native update's previous_receipt_ref is the atomic expected-parent check."""
        _identifier(stream_id)
        ref(expected_spec_ref)
        bounded(update)
        update = deepcopy(update)
        with _transaction(self.path, timeout_s=self.timeout_s, write=True) as (db, store_id):
            journal, acks, _ = _replay(db, store_id, stream_id, expected_spec_ref)
            result = self._admit(db, store_id, stream_id, journal, acks, update, expected_spec_ref)
        return result  # COMMIT completed before any acknowledgement is returned.

    def append_capture(self, stream_id: str, observation: dict, *, received_at_s: float,
                       final: bool, expected_head_ref: str | None, expected_spec_ref: str) -> dict:
        """Convenience ingress with required anchors, including retry after lost response.

An old known parent can only reproduce its exact previously accepted update.
It cannot append a different capture to the current head or supersede a hold.
"""
        _identifier(stream_id)
        ref(expected_spec_ref)
        if expected_head_ref is not None:
            ref(expected_head_ref)
        bounded(observation)
        observation = deepcopy(observation)
        with _transaction(self.path, timeout_s=self.timeout_s, write=True) as (db, store_id):
            journal, acks, _ = _replay(db, store_id, stream_id, expected_spec_ref)
            parents = [None] + [a["receipt"]["receipt_ref"] for a in acks]
            if expected_head_ref not in parents:
                raise ValueError("Unknown expected parent; no automatic retry against a newer head")
            sequence = parents.index(expected_head_ref)
            update = {"schema": UPDATE_SCHEMA, "sequence": sequence, "previous_receipt_ref": expected_head_ref,
                      "received_at_s": received_at_s, "final": final, "observation": observation}
            result = self._admit(db, store_id, stream_id, journal, acks, update, expected_spec_ref)
        return result

    def head(self, stream_id: str, *, expected_spec_ref: str | None = None) -> dict:
        _identifier(stream_id)
        with _transaction(self.path, timeout_s=self.timeout_s, write=False) as (db, store_id):
            journal, acks, stats = _replay(db, store_id, stream_id, expected_spec_ref)
            spec_ref = db.execute("SELECT spec_ref FROM streams WHERE stream_id=?", (stream_id,)).fetchone()[0]
            return _view(store_id, stream_id, spec_ref, journal, acks, stats)

    def export(self, stream_id: str, *, expected_head_ref: str | None = None,
               expected_spec_ref: str | None = None, expected_commit_ref: str | None = None) -> dict:
        """Export the original report schema, ready for existing containment and verification."""
        _identifier(stream_id)
        with _transaction(self.path, timeout_s=self.timeout_s, write=False) as (db, store_id):
            journal, acks, _ = _replay(db, store_id, stream_id, expected_spec_ref)
            if expected_head_ref is not None and ref(expected_head_ref) != journal.head_ref:
                raise ValueError("Stored head differs from independently retained head anchor")
            if expected_commit_ref is not None and (not acks or ref(expected_commit_ref) != acks[-1]["commit_ref"]):
                raise ValueError("Stored commit differs from independently retained commit anchor")
            return journal.report()

    def verify(self, stream_id: str, *, expected_head_ref: str | None = None,
               expected_spec_ref: str | None = None, expected_commit_ref: str | None = None) -> dict:
        report = self.export(stream_id, expected_head_ref=expected_head_ref, expected_spec_ref=expected_spec_ref,
                             expected_commit_ref=expected_commit_ref)
        return {"schema": "ciw.toolpoint-store-verification.v1", "status": "PASS",
            "verification_id": new_identity("verification"), "replay_execution_id": report["execution_id"],
            "spec_ref": report["spec_ref"], "head_ref": report["receipts"][-1]["receipt_ref"],
            "result_ref": report["result_ref"], "head_anchor_checked": expected_head_ref is not None,
            "spec_anchor_checked": expected_spec_ref is not None, "commit_anchor_checked": expected_commit_ref is not None,
            "quality_status": report["result"]["status"],
            "scope": "transaction-consistent lossless reconstruction and unchanged full QC prefix replay",
            "authority": dict(AUTHORITY)}

    def provenance(self, stream_id: str, *, expected_spec_ref: str | None = None) -> dict:
        _identifier(stream_id)
        with _transaction(self.path, timeout_s=self.timeout_s, write=False) as (db, store_id):
            journal, _, _ = _replay(db, store_id, stream_id, expected_spec_ref)
            rows = db.execute("SELECT record_json FROM imports WHERE stream_id=? ORDER BY source_report_ref",
                              (stream_id,)).fetchall()
            return {"schema": "ciw.toolpoint-store-provenance.v1", "store_id": store_id,
                "stream_id": stream_id, "head_ref": journal.head_ref, "imports": [codec.loads(row[0]) for row in rows],
                "authority": dict(AUTHORITY)}

    def audit(self) -> dict:
        """Bounded, single-snapshot database checks and replay of every registered stream."""
        with _transaction(self.path, timeout_s=self.timeout_s, write=False) as (db, store_id):
            if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("SQLite physical structure check failed")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Orphaned QC store evidence")
            streams = db.execute("SELECT stream_id FROM streams ORDER BY stream_id").fetchmany(MAX_STREAMS + 1)
            if len(streams) > MAX_STREAMS:
                raise ValueError("Store stream budget exceeded")
            heads = []
            for (sid,) in streams:
                journal, acks, _ = _replay(db, store_id, sid)
                heads.append({"stream_id": sid, "head_ref": journal.head_ref, "accepted_updates": len(acks),
                              "quality_status": journal.report()["result"]["status"] if acks else "AWAITING_EVIDENCE"})
            return {"schema": "ciw.toolpoint-store-audit.v1", "status": "PASS", "store_id": store_id,
                "verification_id": new_identity("verification"), "streams": heads,
                "sqlite_version": sqlite3.sqlite_version, "durability_profile": "DELETE + synchronous EXTRA",
                "scope": "local structure, foreign keys and same-implementation full prefix replay; not authenticity",
                "authority": dict(AUTHORITY)}
