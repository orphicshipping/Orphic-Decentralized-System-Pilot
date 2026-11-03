"""Optional bounded in-process cache for disclosed schemas and immutable reads.

SPDX-License-Identifier: AGPL-3.0-or-later
Host-only integration: this module dispatches nothing and caches no permissions.
The host rechecks current disclosure/read authority on every lookup and insertion.
Do not route writes, execution, admission, verification occurrences or skill steps
through this cache. A hit is retained data, never a fresh execution or receipt.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Callable

from .control_contracts import json_tree

MAX_ENTRY_BYTES = 256 * 1024
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 1024
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_NAME = re.compile(r'[A-Za-z0-9_.-]{1,128}\Z')


def canonical(value) -> bytes:
    json_tree(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def digest(value) -> str:
    return sha256(canonical(value)).hexdigest()


def _text(value):
    if type(value) is not str or not 1 <= len(value.encode('utf-8')) <= 512:
        raise ValueError('invalid_cache_identity')
    return value


def _hash(value):
    if type(value) is not str or _HEX.fullmatch(value) is None:
        raise ValueError('invalid_cache_hash')
    return value


def _names(value):
    if (type(value) is not tuple or len(value) > MAX_ENTRIES or
            any(type(n) is not str or _NAME.fullmatch(n) is None for n in value)
            or len(value) != len(set(value))):
        raise ValueError('invalid_cache_authority')
    return tuple(sorted(value))


@dataclass(frozen=True)
class CacheScope:
    """Trusted host bindings. Never accept this object from a model request.

    pins must identify every relevant bound source/runtime version. Replay
    identity must be the host's exact read/replay context, not a new occurrence.
    policy_sha256 must cover current principal membership and grant state.
    """
    tenant: str
    principal: str
    policy_sha256: str
    pins: tuple[tuple[str, str], ...]
    replay_identity: str

    def __post_init__(self):
        _text(self.tenant); _text(self.principal); _text(self.replay_identity)
        _hash(self.policy_sha256)
        if type(self.pins) is not tuple or not 1 <= len(self.pins) <= 32:
            raise ValueError('invalid_cache_pins')
        for pair in self.pins:
            if type(pair) is not tuple or len(pair) != 2:
                raise ValueError('invalid_cache_pins')
            _text(pair[0]); _text(pair[1])
        if len({name for name, _ in self.pins}) != len(self.pins):
            raise ValueError('invalid_cache_pins')
        object.__setattr__(self, 'pins', tuple(sorted(self.pins)))


@dataclass(frozen=True)
class CacheAuthority:
    """One coherent current host snapshot, not a cached permission decision.

    schema_names are currently disclosed tools; immutable_reads are explicitly
    approved immutable data reads. Metadata readOnlyHint alone is insufficient.
    An empty set revokes all corresponding cache access.
    """
    scope: CacheScope
    schema_names: tuple[str, ...] = ()
    immutable_reads: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.scope) is not CacheScope:
            raise ValueError('invalid_cache_authority')
        object.__setattr__(self, 'schema_names', _names(self.schema_names))
        object.__setattr__(self, 'immutable_reads', _names(self.immutable_reads))


class CacheScopeChanged(ValueError):
    """Current authority changed; all entries cleared before any data return."""


class ToolboxCache:
    """Host-owned per-client cache; no transport, persistence or invocation API.

    Byte bounds cover canonical entry bytes (key plus returned envelope), not
    Python allocator overhead. The host reader must itself be bounded and return
    a coherent current snapshot or raise. Callers never select scope or grants.
    """
    def __init__(self, read_authority: Callable[[], CacheAuthority], *, max_entries=64,
                 max_bytes=1024 * 1024, max_entry_bytes=MAX_ENTRY_BYTES):
        if not callable(read_authority):
            raise TypeError('host_authority_reader_required')
        for value, maximum in ((max_entries, MAX_ENTRIES), (max_bytes, MAX_CACHE_BYTES),
                               (max_entry_bytes, MAX_ENTRY_BYTES)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('invalid_cache_limit')
        self._read = read_authority
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._max_entry_bytes = min(max_entry_bytes, max_bytes)
        self._lock = RLock()
        self._authority_id = None
        self._entries = OrderedDict()
        self._bytes = 0

    def clear(self):
        """Drop data only; no authority is retained or granted by clearing."""
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def _current(self):
        try:
            authority = self._read()
            if type(authority) is not CacheAuthority:
                raise ValueError('invalid_cache_authority')
            scope = authority.scope
            identity = digest({'tenant': scope.tenant, 'principal': scope.principal,
                'policy_sha256': scope.policy_sha256,
                'pins': [list(pair) for pair in scope.pins],
                'replay_identity': scope.replay_identity,
                'schema_names': list(authority.schema_names),
                'immutable_reads': list(authority.immutable_reads)})
        except Exception:
            self.clear()
            self._authority_id = None
            raise
        old = self._authority_id
        self._authority_id = identity
        if old is not None and old != identity:
            self.clear()
            raise CacheScopeChanged('cache_scope_changed_entries_cleared')
        return authority, identity

    def _key(self, authority, identity, namespace, name, content_version, schema_sha256, request):
        if type(name) is not str or _NAME.fullmatch(name) is None:
            raise ValueError('invalid_cache_name')
        allowed = authority.schema_names if namespace == 'schema' else authority.immutable_reads
        if name not in allowed:
            raise ValueError('cache_read_not_authorized')
        _text(content_version); _hash(schema_sha256)
        request_raw = canonical(request)
        if len(request_raw) > MAX_ENTRY_BYTES:
            raise ValueError('cache_request_budget_exceeded')
        return canonical({'authority': identity, 'namespace': namespace, 'name': name,
            'content_version': content_version, 'schema_sha256': schema_sha256,
            'request_sha256': sha256(request_raw).hexdigest()})

    def _get(self, namespace, name, content_version, schema_sha256, request):
        with self._lock:
            authority, identity = self._current()
            key = self._key(authority, identity, namespace, name, content_version, schema_sha256, request)
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return json.loads(entry)

    def _put(self, namespace, name, content_version, schema_sha256, request, value):
        with self._lock:
            authority, identity = self._current()
            key = self._key(authority, identity, namespace, name, content_version, schema_sha256, request)
            value_raw = canonical(value)
            frozen_value = json.loads(value_raw)
            if namespace == 'schema' and sha256(value_raw).hexdigest() != schema_sha256:
                raise ValueError('cache_schema_hash_mismatch')
            entry = canonical({'cache_kind': namespace, 'scope_sha256': identity,
                'content_version': content_version, 'schema_sha256': schema_sha256,
                'value_sha256': sha256(value_raw).hexdigest(), 'value': frozen_value,
                'authorizes_execution': False, 'fresh_execution': False,
                'creates_receipt': False})
            size = len(key) + len(entry)
            if size > self._max_entry_bytes:
                raise ValueError('cache_entry_budget_exceeded')
            prior = self._entries.pop(key, None)
            if prior is not None:
                self._bytes -= len(key) + len(prior)
            while self._entries and (len(self._entries) >= self._max_entries or
                                     self._bytes + size > self._max_bytes):
                victim, raw = self._entries.popitem(last=False)
                self._bytes -= len(victim) + len(raw)
            self._entries[key] = entry
            self._bytes += size

    def get_schema(self, name, *, content_version, schema_sha256):
        return self._get('schema', name, content_version, schema_sha256, None)

    def put_schema(self, name, value, *, content_version, schema_sha256):
        self._put('schema', name, content_version, schema_sha256, None, value)

    def get_result(self, name, *, request, content_version, schema_sha256):
        return self._get('immutable_read', name, content_version, schema_sha256, request)

    def put_result(self, name, value, *, request, content_version, schema_sha256):
        self._put('immutable_read', name, content_version, schema_sha256, request, value)
