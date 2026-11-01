"""Bounded store for workbench bundles.

The session directory is scratch. The copy here is the retrievable result.
Each record belongs to this process. Another session cannot read or release it.
A caller cannot name a filesystem path. Large results are read in pages.
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

_LOCK = RLock()
_LIMIT = 32
_OWNER = secrets.token_hex(16)
_PAGE = 32768
_SEQUENCE = 0


def _directory() -> Path:
    raw = os.environ.get("NCP_RETAIN_DIR")
    root = Path(raw) if raw else Path(os.environ.get("TMPDIR", "/tmp")) / "ncp-retained"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path(artifact_id: str) -> Path:
    if type(artifact_id) is not str or not artifact_id.startswith("retained-") or "/" in artifact_id or ".." in artifact_id:
        raise ValueError("Unknown retained artifact")
    root = _directory().resolve()
    candidate = root / (artifact_id + ".json")
    if candidate.is_symlink():
        raise ValueError("Unknown retained artifact")
    if candidate.exists():
        resolved = candidate.resolve()
        if resolved.parent != root:
            raise ValueError("Unknown retained artifact")
    elif candidate.parent != root:
        raise ValueError("Unknown retained artifact")
    return candidate


def _owns(envelope: dict) -> None:
    owner = envelope.get("owner")
    if type(owner) is not str or not compare_digest(owner, _OWNER):
        raise ValueError("Retained artifact belongs to another host session")


def _load(artifact_id: str) -> dict:
    target = _path(artifact_id)
    if not target.is_file():
        raise ValueError("Unknown retained artifact")
    value = json.loads(target.read_text(encoding="utf-8"))
    if type(value) is not dict or value.get("schema") != "ncp.retained-bundle.v1":
        raise ValueError("Retained artifact belongs to another host session")
    _owns(value)
    if type(value.get("native")) is not dict or type(value.get("catalog")) is not dict:
        raise ValueError("Retained artifact is not a record")
    return value


def keep(bundle: dict, *, kind: str, catalog: dict) -> str:
    global _SEQUENCE
    with _LOCK:
        _SEQUENCE += 1
        envelope = {
            "schema": "ncp.retained-bundle.v1",
            "owner": _OWNER,
            "kind": kind,
            "sequence": _SEQUENCE,
            "native": bundle,
            "catalog": catalog,
        }
        raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        artifact_id = "retained-" + sha256(raw).hexdigest()[:32]
        target = _path(artifact_id)
        target.write_bytes(raw)
        ranked = []
        for old in _directory().resolve().glob("retained-*.json"):
            if old.is_symlink():
                old.unlink()
                continue
            try:
                sequence = json.loads(old.read_text(encoding="utf-8")).get("sequence", 0)
            except (OSError, ValueError):
                sequence = 0
            ranked.append((sequence if type(sequence) is int else 0, old))
        ranked.sort(key=lambda item: item[0])
        for _sequence, old in ranked[:-_LIMIT]:
            if old != target:
                old.unlink(missing_ok=True)
    return artifact_id


def native(artifact_id: str) -> dict:
    return _load(artifact_id)["native"]


def catalog(artifact_id: str) -> dict:
    return _load(artifact_id)["catalog"]


def bounded(artifact_id: str) -> dict:
    value = native(artifact_id)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    view = value if len(raw) <= 65536 else {
        "bundle_digest": value.get("bundle_digest"),
        "steps": len(value.get("steps") or []),
    }
    return {
        "schema": "ncp.retained-artifact.v1",
        "artifact_id": artifact_id,
        "byte_size": len(raw),
        "projection": "full" if view is value else "bounded",
        "bundle": view,
        "authorizes_execution": False,
        "note": "A bounded view is not the whole record. Page it with offset and limit.",
    }


def page(artifact_id: str, offset: int, limit: int = _PAGE) -> dict:
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= _PAGE:
        raise ValueError("Page offset and limit are outside the retained artifact")
    raw = json.dumps(native(artifact_id), sort_keys=True, separators=(",", ":")).encode()
    if offset > len(raw):
        raise ValueError("Page offset is outside the retained artifact")
    chunk = raw[offset:offset + limit]
    nxt = offset + len(chunk)
    return {
        "schema": "ncp.retained-page.v1",
        "artifact_id": artifact_id,
        "byte_size": len(raw),
        "offset": offset,
        "next_offset": None if nxt >= len(raw) else nxt,
        "chunk_b64": base64.b64encode(chunk).decode("ascii"),
        "authorizes_execution": False,
    }


def release(artifact_id: str) -> dict:
    target = _path(artifact_id)
    with _LOCK:
        _load(artifact_id)
        target.unlink()
    return {"artifact_id": artifact_id, "released": True, "authorizes_execution": False}
