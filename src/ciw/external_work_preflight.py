"""Read-only startup prerequisites for the existing Cloudflare work gateway.

Standard library only: usable before optional gateway dependencies are installed.
This is not authentication, service health, or permission to activate a connector.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import sqlite3


# Keep aligned with pyproject.toml base and external-work requirements.
REQUIRED_VERSIONS = {
    "numpy": "2.4.3", "websockets": "16.0", "mcp": "2.3.0",
    "PyJWT": "2.15.1", "uvicorn": "0.54.0",
}
MAX_CONFIG_BYTES = 65536


def _regular_file(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("indirect_path")
    if not path.is_file():
        raise ValueError("missing_file")
    return path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite_json")


def _read_config(path):
    path = _regular_file(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("oversized_configuration")
    config = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if type(config) is not dict or config.get("schema") != "ciw.external-work-cloudflare.v1":
        raise ValueError("invalid_configuration")
    database = config.get("database")
    endpoints = config.get("endpoints")
    if type(database) is not str or not database.strip() or len(database) > 2048:
        raise ValueError("invalid_database_path")
    if type(endpoints) is not list or not 1 <= len(endpoints) <= 2:
        raise ValueError("invalid_endpoints")
    for endpoint in endpoints:
        if type(endpoint) is not dict or endpoint.get("role") not in ("requester", "worker"):
            raise ValueError("invalid_endpoint_role")
        subjects = endpoint.get("subjects")
        if type(subjects) is not dict or not 1 <= len(subjects) <= 64:
            raise ValueError("subject_bindings_missing_or_invalid")
        if any(type(subject) is not str or not subject.strip() or len(subject) > 512 or
               type(principal) is not str or not principal.strip() or len(principal) > 128
               for subject, principal in subjects.items()):
            raise ValueError("subject_bindings_missing_or_invalid")
    database = Path(database)
    return config, database if database.is_absolute() else path.parent / database


def _check_database(path, endpoints):
    path = _regular_file(path)
    # An immutable read cannot reconcile outstanding WAL/journal state. Refuse it
    # instead of reading stale principal state or creating SQLite sidecar files.
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("database_not_quiescent")
    db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("database_version_not_provisioned")
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("database_integrity_failed")
        for table, columns in {
            "clients": {"id", "role", "parent", "enabled"},
            "contexts": {"id", "parent", "document", "enabled"},
            "tasks": {"seq", "id", "requester", "request_key", "document"},
        }.items():
            if {row[1] for row in db.execute("PRAGMA table_info(" + table + ")")} != columns:
                raise ValueError("database_schema_not_provisioned")
        for endpoint in endpoints:
            for principal in endpoint["subjects"].values():
                row = db.execute("SELECT role, enabled FROM clients WHERE id=?", (principal,)).fetchone()
                if row != (endpoint["role"], 1):
                    raise ValueError("principal_not_provisioned_for_role")
    finally:
        db.close()


def inspect_prerequisites(config_path, cloudflared, expected_sha256):
    """Return sanitized evidence without prompts, imports, network or writes.

    Configuration semantics still belong to validate_config/create_app in the
    existing gateway and are checked by the launcher after this gate succeeds.
    """
    checks, blockers = [], []
    for name, expected in REQUIRED_VERSIONS.items():
        try:
            actual = metadata.version(name)
        except (metadata.PackageNotFoundError, OSError, UnicodeError, ValueError, TypeError):
            actual = None
        # Distribution metadata is local input too; never echo arbitrary text or
        # paths from malformed version fields into the sanitized report.
        if type(actual) is not str or re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,10}){0,7}", actual) is None:
            actual = None
        ok = actual == expected
        checks.append({"check": "dependency", "package": name, "expected": expected,
                       "actual": actual, "passed": ok})
        if not ok:
            blockers.append({"check": "dependency", "package": name,
                             "reason": "exact_pin_missing_or_mismatched"})
    try:
        config, database = _read_config(config_path)
    except (ValueError, TypeError, OSError, UnicodeError, RecursionError):
        checks.append({"check": "configuration_prerequisites", "passed": False})
        blockers.append({"check": "configuration_prerequisites", "reason": "configuration_missing_or_invalid"})
    else:
        checks.append({"check": "configuration_prerequisites", "passed": True})
        try:
            _check_database(database, config["endpoints"])
        except ValueError as exc:
            reason = str(exc)
            allowed = {"indirect_path", "missing_file", "database_not_quiescent",
                       "database_version_not_provisioned", "database_integrity_failed",
                       "database_schema_not_provisioned", "principal_not_provisioned_for_role"}
            blockers.append({"check": "database", "reason": reason if reason in allowed else "database_unavailable"})
            checks.append({"check": "database", "passed": False})
        except (sqlite3.Error, OSError):
            blockers.append({"check": "database", "reason": "database_unavailable"})
            checks.append({"check": "database", "passed": False})
        else:
            checks.append({"check": "database", "passed": True})
    try:
        if type(expected_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            raise ValueError("invalid_binary_pin")
        binary = _regular_file(cloudflared)
        with binary.open("rb") as stream:
            actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual_hash != expected_sha256:
            raise ValueError("binary_pin_mismatch")
    except (ValueError, OSError):
        checks.append({"check": "cloudflared", "passed": False})
        blockers.append({"check": "cloudflared", "reason": "binary_missing_or_pin_mismatched"})
    else:
        checks.append({"check": "cloudflared", "passed": True})
    return {
        "schema": "ciw.external-work-preflight.v1",
        "status": "blocked" if blockers else "prerequisites_ready",
        "checks": checks, "blockers": blockers,
        "gateway_configuration_validation": "not_run",
        "live_authentication_and_health": "not_tested",
        "activation_authorized": False,
    }
