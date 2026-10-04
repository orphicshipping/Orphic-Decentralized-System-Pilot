"""Transactional local refurbishment ledger with deterministic history replay.

Each operation uses a short-lived SQLite connection and an explicit transaction.
``create`` is creation-only; construction opens an existing file without migration.
The DELETE journal is closed before returning, so ``close`` has no WAL to drain.
Hashes establish local consistency, not external authenticity or actor identity.
Memory databases are deliberately unsupported because connections are short-lived.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote
from uuid import uuid4

from . import refurbishment_contract as contract
from .control_contracts import content_ref, keys
from .operations.runner import digest

APPLICATION_ID = 0x4E535246
MAX_COMMAND_BYTES = 8 * 1024 * 1024
MAX_ASSET_BYTES = 32 * 1024 * 1024
MAX_EVENTS_PER_ASSET = 10000
EXPORT_SCHEMA = "ciw.refurbishment-export-state.v1"
EVENT_SCHEMA = "ciw.refurbishment-event.v1"
SCHEMA = {
    "metadata": "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "assets": "CREATE TABLE assets (asset_id TEXT PRIMARY KEY, intake_id TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL CHECK(revision > 0), state_json TEXT NOT NULL, state_ref TEXT NOT NULL, head_ref TEXT NOT NULL)",
    "events": "CREATE TABLE events (position INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT NOT NULL REFERENCES assets(asset_id), revision INTEGER NOT NULL CHECK(revision > 0), event_id TEXT NOT NULL UNIQUE, command_id TEXT NOT NULL UNIQUE, command_json TEXT NOT NULL, recorded_at TEXT NOT NULL, previous_event_ref TEXT, state_ref TEXT NOT NULL, event_ref TEXT NOT NULL UNIQUE, UNIQUE(asset_id, revision))",
    "serial_claims": "CREATE TABLE serial_claims (manufacturer TEXT NOT NULL, serial TEXT NOT NULL, asset_id TEXT NOT NULL REFERENCES assets(asset_id), PRIMARY KEY(manufacturer, serial))",
    "events_no_update": "CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'append-only events'); END",
    "events_no_delete": "CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'append-only events'); END",
    "metadata_no_update": "CREATE TRIGGER metadata_no_update BEFORE UPDATE ON metadata BEGIN SELECT RAISE(ABORT, 'immutable metadata'); END",
    "metadata_no_delete": "CREATE TRIGGER metadata_no_delete BEFORE DELETE ON metadata BEGIN SELECT RAISE(ABORT, 'immutable metadata'); END",
    "serials_no_update": "CREATE TRIGGER serials_no_update BEFORE UPDATE ON serial_claims BEGIN SELECT RAISE(ABORT, 'immutable serial claims'); END",
    "serials_no_delete": "CREATE TRIGGER serials_no_delete BEFORE DELETE ON serial_claims BEGIN SELECT RAISE(ABORT, 'immutable serial claims'); END",
}
EVENT_FIELDS = {"schema", "registry_id", "position", "event_id", "asset_id", "revision", "command", "recorded_at", "previous_event_ref", "state_ref", "event_ref"}


class IntegrityError(ValueError):
    """The retained schema, history, or cached projection is inconsistent."""


class RevisionConflict(ValueError):
    """Another command advanced the asset after the caller read it."""


class CommandConflict(ValueError):
    """An idempotency identity was already used for a different command."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _load(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise IntegrityError("Duplicate JSON key in retained data")
            result[key] = item
        return result
    def constant(value):
        raise IntegrityError("Nonfinite JSON value in retained data")
    try:
        return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, RecursionError) as exc:
        raise IntegrityError("Invalid retained JSON") from exc


def _serial(configuration):
    serial = configuration["serial_number"]
    if serial is None or serial.strip().casefold() in {"unknown", "redacted", "n/a", "none", "not provided", "unspecified", "synthetic"}:
        return None
    return configuration["manufacturer"].strip().casefold(), serial.strip().casefold()


def _path(value):
    if str(value) == ":memory:":
        raise ValueError("A persistent registry file is required")
    path = Path(value).expanduser().absolute()
    if path.is_symlink():
        raise ValueError("Registry path must not be a symbolic link")
    return path.resolve()


def _registry_id(value):
    if type(value) is not str or re.fullmatch(r"registry:[0-9a-f]{32}", value) is None:
        raise IntegrityError("Invalid registry identity")


def _event_ref(event):
    return digest({k:v for k,v in event.items() if k != "event_ref"})


def _event(row, registry_id):
    return {"schema":EVENT_SCHEMA, "registry_id":registry_id, "position":row["position"],
            "event_id":row["event_id"], "asset_id":row["asset_id"], "revision":row["revision"],
            "command":_load(row["command_json"]), "recorded_at":row["recorded_at"],
            "previous_event_ref":row["previous_event_ref"], "state_ref":row["state_ref"], "event_ref":row["event_ref"]}


def _replay_event(event, previous, previous_ref, registry_id):
    try:
        keys(event, EVENT_FIELDS)
        if event["schema"] != EVENT_SCHEMA or event["registry_id"] != registry_id:
            raise ValueError("Event schema or registry differs")
        if type(event["position"]) is not int or event["position"] < 1:
            raise ValueError("Invalid event position")
        if type(event["event_id"]) is not str or re.fullmatch(r"refurbishment-event:[0-9a-f]{32}", event["event_id"]) is None:
            raise ValueError("Invalid event identity")
        command = contract.validate_command(event["command"])
        if len(_json(command).encode("utf-8")) > MAX_COMMAND_BYTES:
            raise ValueError("Command exceeds byte bound")
        expected = 1 if previous is None else previous["revision"] + 1
        if type(event["revision"]) is not int or event["revision"] != expected:
            raise ValueError("Event revision is not sequential")
        if command["asset_id"] != event["asset_id"] or command["expected_revision"] != expected - 1:
            raise ValueError("Event and command identities differ")
        if event["previous_event_ref"] != previous_ref:
            raise ValueError("Event chain differs")
        content_ref(event["state_ref"]); content_ref(event["event_ref"])
        if event["event_ref"] != _event_ref(event):
            raise ValueError("Event content identity differs")
        stamp = event["recorded_at"]
        if type(stamp) is not str or len(stamp) > 40 or datetime.fromisoformat(stamp).utcoffset() is None:
            raise ValueError("Event timestamp must include a timezone")
        state = contract.apply_command(previous, command)
        if digest(state) != event["state_ref"] or len(_json(state).encode("utf-8")) > MAX_ASSET_BYTES:
            raise ValueError("Replayed state differs or exceeds bound")
        return state
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise IntegrityError("Invalid refurbishment event: " + str(exc)) from exc


def validate_export_bundle(value):
    """Validate exact source chains and replay all snapshots; no authenticity claim.

    A successful import replays these commands as NEW local event occurrences.
    Original event identities and timestamps must not be presented as local ones.
    """
    try:
        keys(value, {"schema", "registry_id", "events", "assets"})
        if value["schema"] != EXPORT_SCHEMA:
            raise IntegrityError("Unsupported refurbishment export schema")
        _registry_id(value["registry_id"])
        if type(value["events"]) is not list or len(value["events"]) > 100000:
            raise IntegrityError("Export event count exceeds bound")
        if type(value["assets"]) is not list or len(value["assets"]) > 10000:
            raise IntegrityError("Export asset count exceeds bound")
        states, heads, command_ids, event_ids, intakes, serials = {}, {}, set(), set(), {}, {}
        for position, event in enumerate(value["events"], 1):
            keys(event, EVENT_FIELDS)
            asset_id = contract.identifier(event["asset_id"])
            previous = states.get(asset_id)
            state = _replay_event(event, previous, heads.get(asset_id), value["registry_id"])
            if event["position"] != position or state["revision"] > MAX_EVENTS_PER_ASSET:
                raise IntegrityError("Invalid global order or asset event bound")
            command_id = event["command"]["command_id"]
            if command_id in command_ids or event["event_id"] in event_ids:
                raise IntegrityError("Duplicate command or event identity")
            command_ids.add(command_id); event_ids.add(event["event_id"])
            intake = state["intake_id"]
            if intake in intakes and intakes[intake] != asset_id:
                raise IntegrityError("Duplicate intake identity")
            intakes[intake] = asset_id
            serial = _serial(state["configuration"])
            if serial is not None:
                if serial in serials and serials[serial] != asset_id:
                    raise IntegrityError("Duplicate primary asset serial")
                serials[serial] = asset_id
            states[asset_id], heads[asset_id] = state, event["event_ref"]
        assets = [states[name] for name in sorted(states)]
        if _json(assets) != _json(value["assets"]):
            raise IntegrityError("Export snapshots differ from command replay")
        return deepcopy(value)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        if isinstance(exc, IntegrityError):
            raise
        raise IntegrityError("Invalid refurbishment export: " + str(exc)) from exc


class RefurbishmentStore:
    """Local registry; no persistent handles, implicit creation, or migration.

    Use ``with RefurbishmentStore.create(path) as store`` for a NEW database or
    ``with RefurbishmentStore(path) as store`` for an existing one. ``close``
    disables the handle; each completed method already closed its connection.
    """

    def __init__(self, path):
        self.path = _path(path)
        self._closed = False
        if not self.path.is_file():
            raise FileNotFoundError("Registry does not exist: " + str(self.path))
        with self._transaction() as connection:
            self.registry_id = dict(connection.execute("SELECT key,value FROM metadata"))["registry_id"]

    @classmethod
    def create(cls, path):
        target = _path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        owned = os.fstat(descriptor)
        os.close(descriptor)
        connection = None
        try:
            connection = sqlite3.connect(target, isolation_level=None)
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            for sql in SCHEMA.values():
                connection.execute(sql)
            connection.executemany("INSERT INTO metadata(key,value) VALUES (?,?)", [("schema_version", "1"), ("registry_id", "registry:" + uuid4().hex)])
            connection.execute("PRAGMA application_id=" + str(APPLICATION_ID))
            connection.execute("PRAGMA user_version=1")
            connection.execute("COMMIT")
            connection.close(); connection = None
            return cls(target)
        except BaseException:
            if connection is not None:
                connection.close()
            if target.exists():
                current = target.stat()
                if (owned.st_dev, owned.st_ino) == (current.st_dev, current.st_ino):
                    target.unlink()
            raise

    def close(self):
        self._closed = True

    def __enter__(self):
        if self._closed:
            raise ValueError("Registry handle is closed")
        return self

    def __exit__(self, *args):
        self.close()

    def _schema(self, connection):
        if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID or connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise IntegrityError("Unsupported registry database; no implicit migration")
        normalize = lambda value: " ".join(value.rstrip(";").split())
        objects = {row["name"]:normalize(row["sql"]) for row in connection.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")}
        if objects != {name:normalize(sql) for name,sql in SCHEMA.items()}:
            raise IntegrityError("Registry schema or append-only guards differ")
        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        if set(metadata) != {"schema_version", "registry_id"} or metadata["schema_version"] != "1":
            raise IntegrityError("Registry metadata differs")
        _registry_id(metadata["registry_id"])
        if hasattr(self, "registry_id") and self.registry_id != metadata["registry_id"]:
            raise IntegrityError("Registry identity changed")
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise IntegrityError("Registry requires DELETE journal mode")

    @contextmanager
    def _transaction(self, *, write=False):
        if self._closed:
            raise ValueError("Registry handle is closed")
        if self.path.is_symlink():
            raise IntegrityError("Registry file became a symbolic link")
        connection = None
        try:
            uri = "file:" + quote(self.path.as_posix(), safe="/:") + "?mode=rw"
            connection = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._schema(connection)
            yield connection
            connection.execute("COMMIT")
        except sqlite3.DatabaseError as exc:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise IntegrityError("Registry database operation failed: " + str(exc)) from exc
        except BaseException:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            if connection is not None:
                connection.close()

    def _read_asset(self, connection, asset_id, *, at_revision=None):
        header = connection.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        rows = connection.execute("SELECT * FROM events WHERE asset_id=? ORDER BY revision", (asset_id,)).fetchall()
        if header is None:
            if rows:
                raise IntegrityError("Orphan asset events")
            return None, [], None
        if not rows or len(rows) > MAX_EVENTS_PER_ASSET:
            raise IntegrityError("Asset event count is invalid")
        state, previous_ref, historical, events = None, None, None, []
        for row in rows:
            event = _event(row, self.registry_id)
            state = _replay_event(event, state, previous_ref, self.registry_id)
            if row["command_id"] != event["command"]["command_id"] or row["command_json"] != _json(event["command"]):
                raise IntegrityError("Stored command index or canonical bytes differ")
            previous_ref = event["event_ref"]; events.append(event)
            if state["revision"] == at_revision:
                historical = deepcopy(state)
        if (header["revision"] != state["revision"] or header["intake_id"] != state["intake_id"]
                or header["head_ref"] != previous_ref or header["state_ref"] != digest(state)
                or header["state_json"] != _json(state)):
            raise IntegrityError("Cached asset projection differs from event replay")
        expected_serials = {_serial(entry["configuration"]) for entry in state["configuration_history"]} - {None}
        stored_serials = {(row[0],row[1]) for row in connection.execute("SELECT manufacturer,serial FROM serial_claims WHERE asset_id=?", (asset_id,))}
        if expected_serials != stored_serials:
            raise IntegrityError("Asset serial claims differ from configuration history")
        return state, events, historical

    @staticmethod
    def _receipt(event, state, replayed):
        return {"schema":"ciw.refurbishment-receipt.v1", "command_id":event["command"]["command_id"],
                "asset_id":event["asset_id"], "revision":event["revision"], "event_id":event["event_id"],
                "event_ref":event["event_ref"], "replayed":replayed, "asset":state}

    def execute(self, value):
        command = contract.validate_command(value)
        serialized = _json(command)
        if len(serialized.encode("utf-8")) > MAX_COMMAND_BYTES:
            raise ValueError("Command exceeds byte bound")
        with self._transaction(write=True) as connection:
            duplicate = connection.execute("SELECT * FROM events WHERE command_id=?", (command["command_id"],)).fetchone()
            if duplicate is not None:
                if duplicate["command_json"] != serialized:
                    raise CommandConflict("Command identity was already used with different content")
                _, _, historical = self._read_asset(connection, duplicate["asset_id"], at_revision=duplicate["revision"])
                return self._receipt(_event(duplicate, self.registry_id), historical, True)
            previous, events, _ = self._read_asset(connection, command["asset_id"])
            revision = 0 if previous is None else previous["revision"]
            if revision != command["expected_revision"]:
                raise RevisionConflict("Expected asset revision differs from retained revision")
            if revision >= MAX_EVENTS_PER_ASSET:
                raise ValueError("Asset event count exceeds bound")
            state = contract.apply_command(previous, command)
            state_json, state_ref = _json(state), digest(state)
            if len(state_json.encode("utf-8")) > MAX_ASSET_BYTES:
                raise ValueError("Asset projection exceeds byte bound")
            if previous is None:
                if connection.execute("SELECT 1 FROM assets WHERE intake_id=?", (state["intake_id"],)).fetchone():
                    raise ValueError("Intake identity already belongs to another asset")
                connection.execute("INSERT INTO assets(asset_id,intake_id,revision,state_json,state_ref,head_ref) VALUES (?,?,?,?,?,?)", (state["asset_id"],state["intake_id"],state["revision"],state_json,state_ref,"pending"))
            serial = _serial(state["configuration"])
            if serial is not None:
                owner = connection.execute("SELECT asset_id FROM serial_claims WHERE manufacturer=? AND serial=?", serial).fetchone()
                if owner is not None and owner[0] != state["asset_id"]:
                    raise ValueError("Primary asset serial already belongs to another asset")
                if owner is None:
                    connection.execute("INSERT INTO serial_claims(manufacturer,serial,asset_id) VALUES (?,?,?)", (*serial,state["asset_id"]))
            position = connection.execute("SELECT COALESCE(MAX(position),0)+1 FROM events").fetchone()[0]
            event = {"schema":EVENT_SCHEMA, "registry_id":self.registry_id, "position":position,
                     "event_id":"refurbishment-event:"+uuid4().hex, "asset_id":state["asset_id"], "revision":state["revision"],
                     "command":command, "recorded_at":datetime.now(timezone.utc).isoformat(),
                     "previous_event_ref":events[-1]["event_ref"] if events else None, "state_ref":state_ref}
            event["event_ref"] = _event_ref(event)
            connection.execute("INSERT INTO events(position,asset_id,revision,event_id,command_id,command_json,recorded_at,previous_event_ref,state_ref,event_ref) VALUES (?,?,?,?,?,?,?,?,?,?)", (position,state["asset_id"],state["revision"],event["event_id"],command["command_id"],serialized,event["recorded_at"],event["previous_event_ref"],state_ref,event["event_ref"]))
            connection.execute("UPDATE assets SET revision=?,state_json=?,state_ref=?,head_ref=? WHERE asset_id=?", (state["revision"],state_json,state_ref,event["event_ref"],state["asset_id"]))
            return self._receipt(event, state, False)

    def get_asset(self, asset_id):
        contract.identifier(asset_id)
        with self._transaction() as connection:
            state, _, _ = self._read_asset(connection, asset_id)
            if state is None:
                raise ValueError("Unknown asset: " + asset_id)
            return state

    def history(self, asset_id):
        contract.identifier(asset_id)
        with self._transaction() as connection:
            state, events, _ = self._read_asset(connection, asset_id)
            if state is None:
                raise ValueError("Unknown asset: " + asset_id)
            return events

    def verify(self, asset_id):
        contract.identifier(asset_id)
        with self._transaction() as connection:
            state, events, _ = self._read_asset(connection, asset_id)
            if state is None:
                raise ValueError("Unknown asset: " + asset_id)
            return {"schema":"ciw.refurbishment-integrity.v1", "status":"consistent", "registry_id":self.registry_id,
                    "asset_id":asset_id, "revision":state["revision"], "event_count":len(events),
                    "head_ref":events[-1]["event_ref"], "state_ref":digest(state),
                    "scope":"local_history_replay_and_content_consistency", "source_authenticity":"not_established"}

    def export_bundle(self):
        """Return fully replay-validated assets and events from ONE read transaction."""
        with self._transaction() as connection:
            assets, events = [], []
            for row in connection.execute("SELECT asset_id FROM assets ORDER BY asset_id"):
                state, history, _ = self._read_asset(connection, row[0])
                assets.append(state); events.extend(history)
            events.sort(key=lambda event: event["position"])
            if len(events) != connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] or connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise IntegrityError("Registry contains orphan events or serial claims")
            bundle = {"schema":EXPORT_SCHEMA, "registry_id":self.registry_id, "events":events, "assets":assets}
            return validate_export_bundle(bundle)

    def list_assets(self):
        return self.export_bundle()["assets"]

    def export_history(self):
        return self.export_bundle()["events"]
