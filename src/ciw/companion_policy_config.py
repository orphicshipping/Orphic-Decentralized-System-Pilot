"""Explicit operator policy bindings for pinned scientific companions.

This is a host API. Requests, workspace records and receipts never select the
configuration file or reconstruct a ProcessPolicy. Loading verifies the bounded
configuration; the existing process gate qualifies every actual launch.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

from .control_contracts import json_tree
from .process_policy import ProcessPolicy, ProcessPolicyHeld, require_companion_policy
from .session import loads_json

SCHEMA = "ciw.companion-policies.v1"
MAX_CONFIG_BYTES = 65536
WORKFLOW_ROLES = MappingProxyType({
    "investigation": frozenset({"rci", "fsrt"}),
    "covariance": frozenset({"jspt"}),
    "geodesic": frozenset({"gte"}),
    "calibrated-window": frozenset({"tbrt", "mcur", "stfe", "gsie", "set"}),
    "residual-monitor": frozenset({"fdir", "oit"}),
    "bim-clearance": frozenset({"cse"}),
})
_REQUIRED = frozenset({"read_only_roots", "prlimit_executable", "expected_prlimit_sha256",
                       "bwrap_executable", "expected_bwrap_sha256"})
_LIMITS = frozenset({"wall_seconds", "max_input_bytes", "max_output_bytes", "cpu_seconds",
                    "memory_bytes", "file_bytes", "max_open_files", "max_processes"})


def validate_policy_identity(value):
    """Validate retained policy evidence without constructing launch authority."""
    json_tree(value)
    fields = {"schema", "mode", "policy_sha256", "environment_keys",
              "network_isolation_required", "filesystem_isolation_required",
              "process_limit_scope", "memory_limit_scope", "descendant_cleanup"} | _LIMITS
    if (type(value) is not dict or set(value) != fields
            or value["schema"] != "ciw.process-policy.v1" or value["mode"] not in {"bubblewrap", "limits"}
            or type(value["policy_sha256"]) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", value["policy_sha256"]) is None):
        raise ValueError("Invalid retained companion policy identity")
    isolated = value["mode"] == "bubblewrap"
    if any(value[name] is not isolated for name in ("network_isolation_required", "filesystem_isolation_required")):
        raise ValueError("Inconsistent retained companion isolation flags")
    keys = value["environment_keys"]
    if (type(keys) is not list or len(keys) > 64
            or any(type(k) is not str or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", k) is None for k in keys)
            or keys != sorted(set(keys))):
        raise ValueError("Invalid retained companion environment keys")
    for name in ("process_limit_scope", "memory_limit_scope", "descendant_cleanup"):
        if type(value[name]) is not str or not 1 <= len(value[name]) <= 256:
            raise ValueError("Invalid retained companion policy scope")
    if type(value["wall_seconds"]) not in (int, float) or not 0 < value["wall_seconds"] <= 300:
        raise ValueError("Invalid retained companion wall limit")
    for name, low, high in (("max_input_bytes", 1, 32*1024*1024), ("max_output_bytes", 1, 32*1024*1024),
            ("cpu_seconds", 1, 300), ("memory_bytes", 32*1024*1024, 64*1024*1024*1024),
            ("file_bytes", 0, 1024*1024*1024), ("max_open_files", 16, 4096), ("max_processes", 1, 4096)):
        if type(value[name]) is not int or not low <= value[name] <= high:
            raise ValueError("Invalid retained companion resource limit")


def bind_process_policy(repo, policy):
    """Keep the verified checkout read-only even during adapter Git preflight."""
    selected = require_companion_policy(policy)
    path = Path(repo)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Companion repository must be an absolute host path")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir() or str(resolved) not in selected.read_only_roots:
        raise ProcessPolicyHeld("companion_checkout_read_only_root_required")
    return selected


def bind_process_policies(repositories, process_policies):
    """Detach an exact role map; no default, inferred or weaker policy exists."""
    if not isinstance(repositories, Mapping) or not repositories:
        raise ValueError("An explicit companion repository map is required")
    if process_policies is None:
        raise ProcessPolicyHeld("companion_isolation_required")
    if not isinstance(process_policies, Mapping) or set(process_policies) != set(repositories):
        raise ValueError("Companion policies must match the exact repository roles")
    return MappingProxyType({role: bind_process_policy(repo, process_policies[role])
                             for role, repo in repositories.items()})


def check_replay_policy(policy, expected_runtime):
    """A retained identity is evidence to compare, never launch authority."""
    selected = require_companion_policy(policy)
    if not isinstance(expected_runtime, Mapping) or "process_policy" not in expected_runtime:
        raise ProcessPolicyHeld("companion_policy_history_missing")
    retained = expected_runtime["process_policy"]
    validate_policy_identity(retained)
    encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=True,
                                      allow_nan=False, separators=(",", ":"))
    if encode(retained) != encode(selected.identity()):
        raise ProcessPolicyHeld("companion_replay_policy_changed")


def _read_config(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Companion configuration requires an absolute operator path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022
                or info.st_uid not in {0, os.geteuid()}):
            raise ValueError("Companion configuration must be an operator-owned regular file without shared write access")
        if not 0 < info.st_size <= MAX_CONFIG_BYTES:
            raise ValueError("Companion configuration exceeds its byte budget")
        raw = bytearray()
        while len(raw) <= MAX_CONFIG_BYTES:
            block = os.read(fd, min(8192, MAX_CONFIG_BYTES + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        if not raw or len(raw) > MAX_CONFIG_BYTES:
            raise ValueError("Companion configuration exceeds its byte budget")
    finally:
        os.close(fd)
    value = loads_json(bytes(raw).decode("utf-8"))
    json_tree(value)
    return value


def load_companion_policies(path=None):
    """Load one bounded, closed operator file; never inherit host environment.

    Launcher digests and read-only mounts are explicit operator inputs. This
    function does not calculate expected digests from whatever is installed,
    execute a probe, or claim that isolation is available on the current host.
    """
    if path is None:
        return MappingProxyType({})
    document = _read_config(path)
    if (type(document) is not dict or set(document) != {"schema", "workflows"}
            or document["schema"] != SCHEMA):
        raise ValueError("Invalid companion policy configuration")
    workflows = document["workflows"]
    if (type(workflows) is not dict or not workflows
            or not set(workflows) <= set(WORKFLOW_ROLES)):
        raise ValueError("Unknown or empty companion policy workflow set")
    result = {}
    for workflow, rows in workflows.items():
        if type(rows) is not dict or set(rows) != WORKFLOW_ROLES[workflow]:
            raise ValueError("Companion policy configuration requires exact workflow roles")
        selected = {}
        for role, spec in rows.items():
            if (type(spec) is not dict or not _REQUIRED <= set(spec)
                    or not set(spec) <= _REQUIRED | _LIMITS):
                raise ValueError("Invalid companion policy fields")
            if (type(spec["read_only_roots"]) is not list
                    or any(type(root) is not str for root in spec["read_only_roots"])):
                raise ValueError("Companion read-only roots must be a list of absolute directories")
            for field in _REQUIRED - {"read_only_roots"}:
                if type(spec[field]) is not str:
                    raise ValueError("Companion launcher paths and digests must be strings")
            for field in ("prlimit_executable", "bwrap_executable"):
                launcher = Path(spec[field])
                if not launcher.is_absolute() or ".." in launcher.parts:
                    raise ValueError("Companion launcher paths must be absolute")
            selected[role] = ProcessPolicy(mode="bubblewrap", **spec)
        result[workflow] = MappingProxyType(selected)
    return MappingProxyType(result)


def policy_set(configuration, workflow):
    """Select a host binding only; absent workflows stay unbound and fail closed."""
    if not isinstance(configuration, Mapping):
        raise ValueError("Companion configuration must be a host mapping")
    return configuration.get(workflow)
