"""Resolve pinned study captures through the existing durable QC store, read-only."""
from copy import deepcopy

from .manufacturing_contracts import bounded, items, keys, text
from .toolpoint_stream_store import StreamStore


def resolve(request: dict, database, bindings: list) -> dict:
    """Populate an empty stream collection using exact enrolled spec/head anchors.

Each export is an independently replayed read snapshot. This is a pinned
retrospective cohort, not a promise that the database has no newer events.
"""
    bounded(request)
    if request.get('streams') != []:
        raise ValueError('Store resolution requires an empty stream list')
    cycles = {}
    for sample in request['manifest']['samples']:
        cid = sample['cycle_id']
        anchor = (sample['stream_spec_ref'], sample['stream_head_ref'])
        if cid in cycles and cycles[cid] != anchor:
            raise ValueError('Conflicting enrolled stream anchors')
        cycles[cid] = anchor
    store = StreamStore(database)
    seen, stream_ids, envelopes = set(), set(), []
    for binding in items(bindings, maximum=128):
        keys(binding, {'cycle_id', 'stream_id', 'receipt_time_uncertainty_s'})
        cid, sid = text(binding['cycle_id']), text(binding['stream_id'])
        if cid not in cycles or cid in seen or sid in stream_ids:
            raise ValueError('Unknown or duplicated store-to-cycle mapping')
        spec, head = cycles[cid]
        report = store.export(sid, expected_spec_ref=spec, expected_head_ref=head)
        if report['spec']['binding']['cycle_id'] != cid:
            raise ValueError('Stored capture belongs to a different enrolled cycle')
        envelopes.append({'report': report, 'receipt_time_uncertainty_s': binding['receipt_time_uncertainty_s']})
        seen.add(cid)
        stream_ids.add(sid)
    result = deepcopy(request)
    result['streams'] = envelopes
    bounded(result)
    return result
