"""Bounded store for workbench bundles.

The session directory is scratch. The copy here is the retrievable result.
Cleanup is explicit. A caller cannot name a filesystem path.
"""
from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from threading import RLock

_LOCK = RLock()
_LIMIT = 32


def _directory() -> Path:
    raw = os.environ.get("NCP_RETAIN_DIR")
    root = Path(raw) if raw else Path(os.environ.get("TMPDIR", "/tmp")) / "ncp-retained"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path(artifact_id: str) -> Path:
    if type(artifact_id) is not str or not artifact_id.startswith("retained-") or "/" in artifact_id or ".." in artifact_id:
        raise ValueError("Unknown retained artifact")
    candidate = _directory() / (artifact_id + ".json")
    if candidate.parent != _directory():
        raise ValueError("Unknown retained artifact")
    return candidate


def keep(bundle: dict) -> str:
    raw = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
    artifact_id = "retained-" + sha256(raw).hexdigest()[:32]
    target = _path(artifact_id)
    with _LOCK:
        target.write_bytes(raw)
        files = sorted(_directory().glob("retained-*.json"), key=lambda item: item.stat().st_mtime)
        overflow = files[:-_LIMIT]
        for old in overflow:
            if old != target:
                old.unlink(missing_ok=True)
    return artifact_id


def read(artifact_id: str) -> dict:
    target = _path(artifact_id)
    if not target.is_file():
        raise ValueError("Unknown retained artifact")
    value = json.loads(target.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError("Retained artifact is not a record")
    return value


def bounded(artifact_id: str) -> dict:
    value = read(artifact_id)
    raw = json.dumps(value, sort_keys=True).encode()
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
    }


def release(artifact_id: str) -> dict:
    target = _path(artifact_id)
    with _LOCK:
        if not target.is_file():
            raise ValueError("Unknown retained artifact")
        target.unlink()
    return {"artifact_id": artifact_id, "released": True, "authorizes_execution": False}
