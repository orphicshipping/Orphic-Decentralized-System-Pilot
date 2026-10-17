"""Content-addressed connective records in the existing bounded Link journal.

The journal remains the only storage owner; indexes can be rebuilt by scanning
immutable record namespaces. Original bytes are chunked, never normalized.
"""
from __future__ import annotations

from . import link_contract as c
from .agent_api import encode, parse
from .operational_context import MAX_SOURCE, reference


class Records:
    def __init__(self, journal):
        self.journal = journal

    def put_bytes(self, raw):
        if type(raw) is not bytes or not 0 < len(raw) <= 8*1024*1024:
            raise ValueError('Bounded original bytes required')
        ref = reference(raw); chunks = []
        for i in range(0, len(raw), 24576):
            part = raw[i:i+24576]; key = 'oe-chunk:' + reference(part)
            value = c.packed(part, 24576)
            self._immutable(key, value); chunks.append(key)
        self._immutable('oe-bytes:' + ref, {'ref': ref, 'size': len(raw), 'chunks': chunks})
        return ref

    def _immutable(self, key, value):
        old = self.journal.state(key)
        if old is None:
            try: self.journal.compare_and_set_state(key, 0, value)
            except ValueError:
                old = self.journal.state(key)
                if old is None or old['value'] != value: raise
        elif old['value'] != value:
            raise ValueError('Immutable operational record conflicts with retained bytes')

    def bytes(self, ref):
        c.content_ref(ref)
        item = self.journal.state('oe-bytes:' + ref)
        if item is None: raise ValueError('Unknown retained original bytes')
        value = item['value']; c.keys(value, {'ref', 'size', 'chunks'})
        c.integer(value['size'], minimum=1, maximum=8*1024*1024)
        if type(value['chunks']) is not list or not 1 <= len(value['chunks']) <= 342:
            raise ValueError('Invalid retained byte chunks')
        parts=[]
        for name in value['chunks']:
            part = self.journal.state(name)
            if part is None: raise ValueError('Missing original byte chunk')
            raw = c.unpack(part['value'], 24576)
            if name != 'oe-chunk:' + reference(raw): raise ValueError('Chunk identity mismatch')
            parts.append(raw)
        raw=b''.join(parts)
        if len(raw) != value['size'] or reference(raw) != ref or value['ref'] != ref:
            raise ValueError('Retained original bytes failed identity check')
        return raw

    def put(self, value):
        raw = encode(value); ref=self.put_bytes(raw)
        self._immutable('oe-record:' + ref, {'ref': ref, 'schema': value.get('schema', value.get('run_schema', 'unknown'))})
        return ref

    def get(self, ref):
        return parse(self.bytes(ref))

    def catalog(self, schema=None):
        # Reconstructible projection; this is not a second event ledger.
        values = [self.journal.state(name)['value'] for (name,) in self.journal.db.execute(
            "SELECT namespace FROM state WHERE namespace LIKE 'oe-record:%' ORDER BY namespace").fetchall()]
        return [v for v in values if schema is None or v['schema'] == schema]
