"""Owner-scoped store for retained results.

The owner is the host principal in NCP_RETAIN_OWNER, or an explicit Store.
A tool argument cannot name it. Restart with the same principal and directory
reopens that owner's records. A different principal cannot read, release, or
evict them. A caller cannot name a filesystem path.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from threading import RLock

import fcntl

_LOCK = RLock()
_LIMIT = 32
_BYTE_LIMIT = 4_000_000
_PAGE = 32768
_OWNER = os.environ.get("NCP_RETAIN_OWNER") or secrets.token_hex(16)


class RetainedFailure(ValueError):
    """A refused call whose record is already in this owner's store."""

    def __init__(self, message: str, artifact_id: str):
        super().__init__(message)
        self.artifact_id = artifact_id


def _root() -> Path:
    raw = os.environ.get("NCP_RETAIN_DIR")
    root = Path(raw) if raw else Path(os.environ.get("TMPDIR", "/tmp")) / "ncp-retained"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _owner_dir(owner: str) -> Path:
    if type(owner) is not str or not owner or len(owner) > 4096:
        raise ValueError("Host owner is not a usable identity")
    directory = _root() / sha256(owner.encode()).hexdigest()[:32]
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _artifact_name(artifact_id: str) -> str:
    if type(artifact_id) is not str or not artifact_id.startswith(("retained-", "failure-")) or "/" in artifact_id or ".." in artifact_id:
        raise ValueError("Unknown retained artifact")
    return artifact_id + ".json"


class Store:
    def __init__(self, owner: str):
        self.owner = owner

    def _path(self, artifact_id: str) -> Path:
        return _owner_dir(self.owner) / _artifact_name(artifact_id)

    def _read(self, artifact_id: str) -> dict:
        target = self._path(artifact_id)
        if target.is_symlink():
            raise ValueError("Unknown retained artifact")
        if not target.is_file():
            if _held_by_other(self.owner, artifact_id):
                raise ValueError("Retained artifact belongs to another host session")
            raise ValueError("Unknown retained artifact")
        try:
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ValueError("Unknown retained artifact") from exc
        try:
            raw = os.read(fd, _BYTE_LIMIT + 1)
        finally:
            os.close(fd)
        if len(raw) > _BYTE_LIMIT:
            raise ValueError("Retained artifact exceeds the byte budget")
        value = json.loads(raw.decode("utf-8"))
        if type(value) is not dict or value.get("schema") not in {"ncp.retained-bundle.v1", "ncp.retained-failure.v1"}:
            raise ValueError("Retained artifact is not a record")
        owner = value.get("owner")
        if type(owner) is not str or not compare_digest(owner, self.owner):
            raise ValueError("Retained artifact belongs to another host session")
        if type(value.get("native")) is not dict or type(value.get("catalog")) is not dict:
            raise ValueError("Retained artifact is not a record")
        return value

    def _write(self, envelope: dict) -> str:
        raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        if len(raw) > _BYTE_LIMIT:
            raise ValueError("Retained artifact exceeds the byte budget")
        prefix = "failure-" if envelope["schema"] == "ncp.retained-failure.v1" else "retained-"
        artifact_id = prefix + sha256(raw).hexdigest()[:32]
        target = self._path(artifact_id)
        directory = target.parent
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        try:
            fd = os.open(target, flags, 0o600)
        except OSError as exc:
            raise ValueError("Unknown retained artifact") from exc
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)
        self._evict(directory, prefix, target)
        return artifact_id

    def _evict(self, directory: Path, prefix: str, target: Path) -> None:
        ranked = []
        total = 0
        for old in directory.glob(prefix + "*.json"):
            if old.is_symlink():
                continue
            try:
                sequence = json.loads(old.read_text(encoding="utf-8")).get("sequence", 0)
                size = old.stat().st_size
            except (OSError, ValueError):
                sequence, size = 0, 0
            ranked.append((sequence if type(sequence) is int else 0, size, old))
            total += size
        ranked.sort(key=lambda item: item[0])
        overflow = ranked[:-_LIMIT]
        for _sequence, size, old in overflow:
            if old != target:
                old.unlink(missing_ok=True)
                total -= size
        for _sequence, size, old in ranked:
            if total <= _BYTE_LIMIT or old == target:
                break
            if old not in {item[2] for item in overflow}:
                old.unlink(missing_ok=True)
                total -= size

    def keep(self, bundle: dict, *, kind: str, catalog: dict) -> str:
        with _LOCK:
            sequence = _allocate(_owner_dir(self.owner))
            return self._write({
                "schema": "ncp.retained-bundle.v1",
                "owner": self.owner,
                "kind": kind,
                "sequence": sequence,
                "native": bundle,
                "catalog": catalog,
            })

    def keep_failure(self, record: dict) -> str:
        with _LOCK:
            sequence = _allocate(_owner_dir(self.owner))
            return self._write({
                "schema": "ncp.retained-failure.v1",
                "owner": self.owner,
                "kind": "failure",
                "sequence": sequence,
                "native": record,
                "catalog": {"schema": "ncp.failure-record.v1"},
            })

    def native(self, artifact_id: str) -> dict:
        return self._read(artifact_id)["native"]

    def catalog(self, artifact_id: str) -> dict:
        return self._read(artifact_id)["catalog"]

    def bounded(self, artifact_id: str) -> dict:
        value = self.native(artifact_id)
        raw = _canonical(value)
        view = value if len(raw) <= 65536 else {
            "bundle_digest": value.get("bundle_digest"),
            "steps": len(value.get("steps") or []),
        }
        return {
            "schema": "ncp.retained-artifact.v1",
            "artifact_id": artifact_id,
            "byte_size": len(raw),
            "digest": "sha256:" + sha256(raw).hexdigest(),
            "projection": "full" if view is value else "bounded",
            "bundle": view,
            "authorizes_execution": False,
            "note": "A bounded view is not the whole record. Page it with offset and limit.",
        }

    def page(self, artifact_id: str, offset: int, limit: int = _PAGE) -> dict:
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= _PAGE:
            raise ValueError("Page offset and limit are outside the retained artifact")
        raw = _canonical(self.native(artifact_id))
        if offset > len(raw):
            raise ValueError("Page offset is outside the retained artifact")
        chunk = raw[offset:offset + limit]
        nxt = offset + len(chunk)
        return {
            "schema": "ncp.retained-page.v1",
            "artifact_id": artifact_id,
            "byte_size": len(raw),
            "digest": "sha256:" + sha256(raw).hexdigest(),
            "offset": offset,
            "next_offset": None if nxt >= len(raw) else nxt,
            "chunk_b64": base64.b64encode(chunk).decode("ascii"),
            "authorizes_execution": False,
        }

    def release(self, artifact_id: str) -> dict:
        target = self._path(artifact_id)
        with _LOCK:
            self._read(artifact_id)
            if target.is_symlink():
                raise ValueError("Unknown retained artifact")
            target.unlink()
        return {"artifact_id": artifact_id, "released": True, "authorizes_execution": False}


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _allocate(directory: Path) -> int:
    lock_path = directory / "sequence.lock"
    sequence_path = directory / "sequence"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        current = 0
        if sequence_path.is_file() and not sequence_path.is_symlink():
            text = sequence_path.read_text(encoding="utf-8").strip()
            current = int(text) if text.isdigit() else 0
        nxt = current + 1
        temporary = directory / "sequence.tmp"
        temporary.write_text(str(nxt), encoding="utf-8")
        os.replace(temporary, sequence_path)
        return nxt
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _held_by_other(owner: str, artifact_id: str) -> bool:
    name = _artifact_name(artifact_id)
    own = _owner_dir(owner)
    for child in _root().iterdir():
        if not child.is_dir() or child == own:
            continue
        candidate = child / name
        if candidate.is_file() and not candidate.is_symlink():
            return True
    return False


def _store() -> Store:
    return Store(_OWNER)


def keep(bundle: dict, *, kind: str, catalog: dict) -> str:
    return _store().keep(bundle, kind=kind, catalog=catalog)


def keep_failure(record: dict) -> str:
    return _store().keep_failure(record)


def native(artifact_id: str) -> dict:
    return _store().native(artifact_id)


def catalog(artifact_id: str) -> dict:
    return _store().catalog(artifact_id)


def bounded(artifact_id: str) -> dict:
    return _store().bounded(artifact_id)


def page(artifact_id: str, offset: int, limit: int = _PAGE) -> dict:
    return _store().page(artifact_id, offset, limit)


def release(artifact_id: str) -> dict:
    return _store().release(artifact_id)
