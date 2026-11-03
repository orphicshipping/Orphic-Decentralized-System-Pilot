"""Bounded Python reference client for model-context selection only.

SPDX-License-Identifier: AGPL-3.0-or-later
The trusted embedding host supplies a fresh authorized catalog on every operation.
This module has no transport, dispatch, identity, permission cache or persistence.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Callable

from .control_contracts import json_tree

MAX_OUTPUT = 256 * 1024
MAX_CATALOG = 4 * 1024 * 1024
MAX_TOOLS = 1024
MAX_SELECTED = 64


def serialize(value) -> bytes:
    """Exact JSON value preservation, deterministic bytes (including Unicode)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _count(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('invalid_limit')
    return value


@dataclass(frozen=True)
class AuthorizedCatalog:
    """Host-only input, never a model argument.

    revision must change on host identity/membership/policy changes, even when
    tool schemas are identical. families contains ONLY currently disclosed tools.
    The host must authenticate and authorize independently again at invocation.
    """
    revision: str
    families: dict[str, list[dict]]


class StaleCatalog(ValueError):
    """Catalog changed; selection cleared. Rediscover and explicitly reselect."""


class ContextSelector:
    """One instance per client; serialized operations; no global selection state.

    read_authorized is trusted host code, not a model-selectable function/path.
    It must return a coherent current snapshot or raise on refresh failure.
    Returned dictionaries are detached copies. Selection never grants authority.
    """
    def __init__(self, read_authorized: Callable[[], AuthorizedCatalog]):
        if not callable(read_authorized):
            raise TypeError('host_reader_required')
        self._read = read_authorized
        self._lock = RLock()
        self._identity = None
        self._selected = set()

    def _refresh(self):
        try:
            snapshot = self._read()
            if (type(snapshot) is not AuthorizedCatalog or type(snapshot.revision) is not str
                    or not 1 <= len(snapshot.revision.encode('utf-8')) <= 512
                    or type(snapshot.families) is not dict or len(snapshot.families) > 64):
                raise ValueError('invalid_host_catalog')
            # Serialize before copying or indexing; JSON trees only, finite values.
            json_tree(snapshot.families)
            raw = serialize({'revision': snapshot.revision, 'families': snapshot.families})
            if len(raw) > MAX_CATALOG:
                raise ValueError('catalog_budget_exceeded')
            families = json.loads(raw)['families']
            tools = {}
            for family, rows in families.items():
                if re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', family) is None or type(rows) is not list:
                    raise ValueError('invalid_host_catalog')
                for row in rows:
                    if (type(row) is not dict or type(row.get('name')) is not str
                            or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', row['name']) is None
                            or type(row.get('inputSchema')) is not dict
                            or type(row.get('description', '')) is not str
                            or row['name'] in tools):
                        raise ValueError('invalid_host_catalog')
                    tools[row['name']] = (family, row)
            if len(tools) > MAX_TOOLS:
                raise ValueError('catalog_budget_exceeded')
            # Order-independent identity; revision includes host policy generation.
            identity = sha256(serialize({'revision': snapshot.revision, 'tools': tools,
                                         'families': sorted(families)})).hexdigest()
        except Exception:
            self._selected.clear()
            self._identity = None
            raise
        old = self._identity
        self._identity = identity
        if old is not None and old != identity:
            self._selected.clear()
            raise StaleCatalog('catalog_changed_selection_cleared')
        return identity, families, tools

    @staticmethod
    def _response(identity, data, max_bytes):
        _count(max_bytes, 128, MAX_OUTPUT)
        result = {'catalog_id': identity, 'authorizes_execution': False, **data}
        if len(serialize(result)) > max_bytes:
            raise ValueError('output_budget_exceeded')
        return result

    def categories(self, *, max_bytes=MAX_OUTPUT):
        with self._lock:
            identity, families, _ = self._refresh()
            return self._response(identity, {'categories': [
                {'name': k, 'count': len(families[k])} for k in sorted(families)]}, max_bytes)

    def list(self, *, category=None, cursor=None, limit=16, max_bytes=MAX_OUTPUT):
        return self._page(category, cursor, limit, max_bytes, None)

    def search(self, query, *, category=None, cursor=None, limit=16, max_bytes=MAX_OUTPUT):
        if type(query) is not str or not 1 <= len(query.encode('utf-8')) <= 512 or not query.strip():
            raise ValueError('invalid_query')
        return self._page(category, cursor, limit, max_bytes, query.casefold())

    def _page(self, category, cursor, limit, max_bytes, query):
        _count(limit, 1, 64)
        _count(max_bytes, 128, MAX_OUTPUT)
        with self._lock:
            identity, families, tools = self._refresh()
            if category is not None and (type(category) is not str or category not in families):
                raise ValueError('unknown_category')
            # Bind pagination to the current authorized catalog AND search/filter.
            scope = sha256(serialize([identity, category, query])).hexdigest()
            offset = 0
            if cursor is not None:
                if type(cursor) is not str or len(cursor) > 80:
                    raise ValueError('invalid_cursor')
                parts = cursor.split(':')
                if len(parts) != 2 or parts[0] != scope or not parts[1].isascii() or not parts[1].isdigit():
                    raise ValueError('stale_or_invalid_cursor')
                offset = _count(int(parts[1]), 0, MAX_TOOLS)
            rows = [(name, family, row) for name, (family, row) in sorted(tools.items())
                    if (category is None or family == category)
                    and (query is None or query in (name + ' ' + row.get('description', '')).casefold())]
            if offset > len(rows):
                raise ValueError('invalid_cursor')
            items = []
            end = offset
            while end < min(len(rows), offset + limit):
                name, family, row = rows[end]
                item = {'name': name, 'category': family,
                        'description': row.get('description', '')[:160],
                        'schema_sha256': sha256(serialize(row)).hexdigest()}
                candidate = {'items': items + [item], 'total': len(rows),
                             'next_cursor': f'{scope}:{end + 1}' if end + 1 < len(rows) else None}
                try:
                    self._response(identity, candidate, max_bytes)
                except ValueError:
                    if not items:
                        raise
                    break
                items.append(item)
                end += 1
            return self._response(identity, {'items': items, 'total': len(rows),
                'next_cursor': f'{scope}:{end}' if end < len(rows) else None}, max_bytes)

    def describe(self, name, *, max_bytes=MAX_OUTPUT):
        with self._lock:
            identity, _, tools = self._refresh()
            if type(name) is not str or name not in tools:
                raise ValueError('unknown_tool')
            family, row = tools[name]
            return self._response(identity, {'category': family, 'tool': deepcopy(row),
                'schema_sha256': sha256(serialize(row)).hexdigest()}, max_bytes)

    def _selection(self, names, remove, max_bytes):
        if type(names) not in (list, tuple) or len(names) > MAX_SELECTED or any(type(n) is not str for n in names):
            raise ValueError('invalid_selection')
        with self._lock:
            identity, _, tools = self._refresh()
            if set(names) - tools.keys():
                raise ValueError('unknown_tool')
            chosen = self._selected - set(names) if remove else self._selected | set(names)
            _count(len(chosen), 0, MAX_SELECTED)
            result = self._response(identity, {'tools': [deepcopy(tools[n][1]) for n in sorted(chosen)]}, max_bytes)
            self._selected = chosen
            return result

    def select(self, names, *, max_bytes=MAX_OUTPUT):
        return self._selection(names, False, max_bytes)

    def deselect(self, names, *, max_bytes=MAX_OUTPUT):
        return self._selection(names, True, max_bytes)

    def selected(self, *, max_bytes=MAX_OUTPUT):
        return self._selection([], False, max_bytes)
