"""Detached operational projections over exact retained native evidence.

No acquisition, numerical dispatch, clock fitting, calibration or causal inference.
Configuration applicability is a declaration, with uncertain boundaries held.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import math

from . import link_contract as c
from .agent_api import encode, parse
from .control_contracts import keys, number, text
from .core.identities import content_identity

SCHEMA = 'ncp.operational-binding.v1'
MAX_SOURCE = 512 * 1024
MAX_ROWS = 256
FAMILIES = ('recording', 'observations', 'link-capture', 'measurement-bundle', 'measured-thermal', 'calibrated-window', 'gpu-telemetry', 'elapsed-gauge-study', 'residual-monitor')
AUTHORITY = {'physical_validation': 'not_established', 'source_authentication': 'not_established',
             'equipment_permission': 'not_granted', 'causal_identification': 'not_established'}


def reference(raw):
    return 'sha256:' + sha256(raw).hexdigest()


def context(value):
    keys(value, {'producer', 'acquisition_host', 'boot', 'clock', 'time_uncertainty', 'receipt_clock',
                 'origin', 'calibration', 'completeness', 'connectivity', 'gaps', 'material_process'})
    for field in ('producer', 'acquisition_host', 'boot', 'clock', 'receipt_clock'):
        text(value[field])
    if value['origin'] not in {'measured', 'recorded', 'synthetic', 'test_fixture', 'unknown'}:
        raise ValueError('Explicit source origin required')
    if value['time_uncertainty'] is not None and number(value['time_uncertainty']) < 0:
        raise ValueError('Timing uncertainty cannot be negative')
    if value['completeness'] not in {'closed_with_limits', 'incomplete', 'unknown'}:
        raise ValueError('Capture completeness is independent of connectivity')
    if value['connectivity'] not in {'connected', 'disconnected', 'unknown'}:
        raise ValueError('Unknown connectivity declaration')
    if type(value['gaps']) is not list or len(value['gaps']) > 32:
        raise ValueError('Bounded gap declarations required')
    for gap in value['gaps']:
        keys(gap, {'start', 'end', 'reason'})
        if number(gap['start']) > number(gap['end']):
            raise ValueError('Reversed gap')
        text(gap['reason'])
    for field in ('calibration', 'material_process'):
        if type(value[field]) is not dict:
            raise ValueError('Context declarations must be objects; absence stays explicit')
    c.bounded(value, 8192)
    return deepcopy(value)


def configurations(values):
    if type(values) is not list or len(values) > 16:
        raise ValueError('At most 16 retained configuration events')
    seen = set()
    for v in values:
        keys(v, {'id', 'revision', 'raw', 'clock', 'boot', 'effective', 'basis', 'observed_fields'})
        text(v['id']); text(v['revision']); text(v['clock']); text(v['boot'])
        if v['id'] in seen:
            raise ValueError('Configuration identity is ambiguous')
        seen.add(v['id'])
        raw = c.unpack(v['raw'], 16384)
        if type(parse(raw)) is not dict:
            raise ValueError('Configuration must retain an exact JSON representation')
        if v['basis'] not in {'recorded_metadata', 'operator_declared', 'physical_observation'}:
            raise ValueError('Configuration basis must be explicit')
        if type(v['effective']) is not list or len(v['effective']) != 2:
            raise ValueError('Effective boundary requires [earliest, latest]')
        lo, hi = map(number, v['effective'])
        if lo > hi:
            raise ValueError('Reversed effective boundary')
        if type(v['observed_fields']) is not list or len(v['observed_fields']) > 64:
            raise ValueError('Observed fields must be explicit')
        for field in v['observed_fields']: text(field)
    return deepcopy(values)


def make_binding(raw, family, declaration, configs, parent_receipts=()):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_SOURCE or family not in FAMILIES:
        raise ValueError('Unsupported or oversized retained source')
    configs=deepcopy(configs)
    if family=='link-capture':
        v=parse(raw);device=v['manifest'];ref=content_identity(device['configuration'])
        recorded={'id':ref,'revision':str(device['configuration']['revision']),
            'raw':c.packed(encode(device['configuration']),16384),'clock':device['clock']['id'],'boot':v['boot_id'],
            'effective':[0,0],'basis':'recorded_metadata','observed_fields':[]}
        found=[x for x in configs if x['id']==ref]
        if found and any(x!=recorded for x in found):raise ValueError('Contradictory native configuration declaration')
        if not found:configs.append(recorded)
    value = {'schema': SCHEMA, 'source_ref': reference(raw), 'family': family,
             'context': context(declaration), 'configurations': configurations(configs), 'parent_receipts': list(parent_receipts)}
    # Check adapter compatibility now; no provider is contacted.
    rows(raw, value)
    return value


def _row(identity, channel, quantity, unit, frame, timestamp, value, *, receipt=None,
         uncertainty=None, quality=None, boot=None, clock=None, origin=None, lineage=None):
    if value is not None: number(value)
    if timestamp is not None: number(timestamp)
    return dict(id=identity, channel=channel, quantity=quantity, unit=unit, frame=frame,
                time=timestamp, value=value, receipt=receipt, uncertainty=uncertainty,
                quality=quality or ('missing' if value is None else 'valid'), boot=boot, clock=clock,
                origin=origin, lineage=lineage or {})


def rows(raw, binding):
    """Use native validators and preserve source/measurement/interpretation IDs."""
    keys(binding, {'schema', 'source_ref', 'family', 'context', 'configurations', 'parent_receipts'})
    if binding['schema'] != SCHEMA or reference(raw) != binding['source_ref']:
        raise ValueError('Operational binding differs from exact original bytes')
    ctx = context(binding['context']); configurations(binding['configurations'])
    if type(binding['parent_receipts']) is not list or len(binding['parent_receipts'])>16: raise ValueError('Bounded parent receipts required')
    for ref in binding['parent_receipts']: c.content_ref(ref)
    v = parse(raw); family = binding['family']; result = []
    if family == 'residual-monitor':
        from .residual_monitor import _source
        _source(raw)  # Native monitor declarations name retained upstream windows.
    elif family == 'recording':
        from .instruments import validate_run
        from .core.identities import validate_evidence_identity
        validate_run(v); validate_evidence_identity(v)
        declared_origin = v['metadata'].get('provenance', {}).get('source', 'unknown')
        if ctx['origin'] == 'measured' and declared_origin not in {'measured', 'observed', 'recorded'}:
            raise ValueError('A synthetic recording cannot acquire measured status')
        for channel, data in v['channels'].items():
            for i, (t, val) in enumerate(zip(v['time_s'], data['values'])):
                if type(val) is list: raise ValueError('Operational recording adapter requires scalar channels')
                result.append(_row(content_identity([v['evidence_id'], channel, i]), channel, channel,
                    data['unit'], v['metadata']['coordinate_frame'], t, val,
                    origin='synthetic' if declared_origin in {'synthetic','simulated','fixture','test_fixture'} else None,
                    lineage={'evidence_id': v['evidence_id'], 'sample_index': i}))
    elif family == 'observations':
        from .control_checks import inspect_record
        inspect_record(v)
        if v.get('schema') != 'ciw.observation-stream.v1': raise ValueError('Native observation stream required')
        for row in v['observations']:
            if ctx['origin']=='measured' and row['provenance']['semantics']=='simulated':
                raise ValueError('Simulated observations cannot acquire measured status')
            if row['clock']['id'] != ctx['clock']: raise ValueError('Observation clock differs')
            result.append(_row(content_identity(row), row['quantity'], row['quantity'], row['unit'], row['frame'],
                row['clock']['time_s'], row['value'], uncertainty=row['uncertainty'],
                origin='synthetic' if row['provenance']['semantics']=='simulated' else None,
                lineage={'observation_ref':content_identity(row), 'provenance':row['provenance']}))
    elif family == 'measured-thermal':
        from . import measured_thermal as thermal
        thermal.validate_source(raw)
        if ctx['clock'] != v['clock']['id'] or ctx['origin'] != v['origin'] or ctx['time_uncertainty'] != v['clock']['uncertainty_s']:
            raise ValueError('Thermal clock/origin differs from native recording')
        for channel, stream in thermal.streams(v).items():
            for row in stream['observations']:
                result.append(_row(content_identity(row), channel, row['quantity'], row['unit'], row['frame'],
                    row['clock']['time_s'], row['value'], uncertainty=row['uncertainty'],
                    lineage={'stream_ref': v['series'][channel], 'observation_ref': content_identity(row)}))
    elif family == 'link-capture':
        from .link_gateway import validate_capture
        from .link_store import decode_bound
        from .link_protocol import MAX_FRAME
        native = validate_capture(v)
        device = v['manifest']
        if ctx['boot'] != v['boot_id'] or ctx['producer'] != device['device_id'] or ctx['clock'] != device['clock']['id']:
            raise ValueError('Link producer/boot binding differs')
        # Device ticks remain device ticks; there is no invented seconds mapping.
        for f in v['frames']:
            _, row, _, _ = decode_bound(c.unpack(f['wire'], MAX_FRAME), device, offline=True)
            if row['schema'] != c.OBS_SCHEMA: continue
            ch = c.channel_for(device, row['channel_id'])
            result.append(_row(row['measurement_id'], row['channel_id'], ch['quantity'], ch['unit'], ch['frame'],
                row['device_tick'] if device['clock']['timestamp_event']=='sensor_sample' else None, row['value'], receipt={'value': f['received_ns'], 'clock': f['host_clock']},
                origin=row['origin'], quality='missing' if row['value'] is None else 'out_of_range' if 'out_of_range' in row['quality_flags'] else 'invalid' if row['quality_flags'] else 'valid', uncertainty=row['uncertainty'], lineage={'manifest_ref': native['manifest_ref'], 'wire_ref': f['wire']['sha256'],
                                               'sequence': row['sequence'], 'acquired_at': row['acquired_at'], 'raw_tick':row['device_tick'], 'clock_semantics':device['clock'], 'quality_flags':row['quality_flags'], 'configuration_ref':row['configuration_ref'], 'calibration_ref':row['calibration_ref']}))
    elif family == 'measurement-bundle':
        from .link_measurements import validate
        native = validate(v)
        for row in native['measurements']:
            q, ident = row['quantity'], row['identity']
            result.append(_row(ident['measurement_id'], ident['channel_id'] + ':' + q['stage'], q['kind'], q['unit'],
                row['context']['frame'], None, q['value'], boot=row['time']['boot_id'],
                uncertainty=row['metrology']['uncertainty'], lineage={'native': ident, 'provenance': row['provenance'],
                    'instrument_time': row['time'], 'metrology': row['metrology']}))
    elif family == 'calibrated-window':
        from .calibrated_window import _source
        native = _source(raw)
        if ctx['clock'] != native['device_clock']['clock_id']: raise ValueError('Window device clock differs')
        for row in native['samples']:
            result.append(_row(row['observation_id'], native['channel_id'], row['quantity_id'], row['unit'],
                native['frame']['id'], row['device_time'], row['indicated_value'],
                quality='out_of_range' if not native['calibration_profile']['input_range'][0]<=row['indicated_value']<=native['calibration_profile']['input_range'][1] else 'valid',
                receipt={'value': row['received_at'], 'clock': native['receipt_clock']['clock_id']},
                lineage={'artifact_id': row['artifact_id'], 'raw_value': row['raw_value'],
                    'calibration_profile': native['calibration_profile'], 'joint_covariance': native['joint_covariance']}))
    elif family == 'elapsed-gauge-study':
        from .operational_egm import project
        result=project(v)
    elif family == 'gpu-telemetry':
        keys(v, {'schema', 'sensor', 'readings', 'host', 'boot'})
        if v['schema'] != 'ncp.nvml-recording.v1' or ctx['acquisition_host'] != v['host'] or ctx['boot'] != v['boot']:
            raise ValueError('GPU recording host/boot mismatch')
        if type(v['sensor']) is not dict or type(v['readings']) is not list or len(v['readings']) > 64:
            raise ValueError('Bounded retained NVML readings required')
        for i, row in enumerate(v['readings']):
            keys(row, {'read_start_ns', 'read_end_ns', 'utc_start_ns', 'utc_end_ns', 'status', 'energy_mj',
                       'error_code', 'power_mw', 'temperature_c', 'graphics_clock_mhz', 'context_errors'})
            for field in ('read_start_ns', 'read_end_ns', 'utc_start_ns', 'utc_end_ns'): c.integer(row[field])
            if row['read_end_ns'] < row['read_start_ns'] or row['utc_end_ns'] < row['utc_start_ns']:
                raise ValueError('Reversed NVML call bracket')
            for field, unit in (('energy_mj', 'mJ'), ('power_mw', 'mW'), ('temperature_c', 'degC'), ('graphics_clock_mhz', 'MHz')):
                result.append(_row(content_identity([reference(raw), i, field]), field, field, unit, 'gpu_device',
                    None, row[field], receipt={'value': row['utc_end_ns'], 'clock': ctx['receipt_clock']},
                    quality='missing' if row[field] is None else 'valid', origin='recorded',
                    lineage={'sensor': v['sensor'], 'host_call_bracket': deepcopy(row),
                             'sample_time': 'unknown; context sampled after energy bracket', 'sample_index': i}))
    else:
        raise ValueError('Unsupported operational source family')
    if len(result) > MAX_ROWS: raise ValueError('Source exceeds 256 projected observations; rotate segments')
    for row in result:
        row['recording_origin']=ctx['origin']
        row['boot'] = row['boot'] or ctx['boot']; row['clock'] = row['clock'] or ctx['clock']
        row['origin'] = row['origin'] or ctx['origin']
        for f in ('id', 'channel', 'quantity', 'unit', 'frame', 'boot', 'clock'): text(row[f])
    return result


def attribution(row, binding):
    direct=row['lineage'].get('configuration_ref')
    if direct is not None:
        matches=[v for v in binding['configurations'] if v['id']==direct and v['basis']=='recorded_metadata']
        if len(matches)==1:
            return {'status':'RECORDED_ASSOCIATION','configuration':direct,'candidates':[direct],
                    'reason':'exact_native_observation_configuration_binding'}
    events = [v for v in binding['configurations'] if v['clock'] == row['clock'] and v['boot'] == row['boot']]
    t = row['time']; u = binding['context']['time_uncertainty']
    if t is None or u is None:
        return {'status': 'UNKNOWN', 'configuration': None, 'candidates': [e['id'] for e in events],
                'reason': 'sample_time_or_uncertainty_unknown'}
    events.sort(key=lambda e: e['effective'][0])
    certain = [e for e in events if e['effective'][1] <= t-u]
    uncertain = [e for e in events if e['effective'][0] <= t+u and e['effective'][1] > t-u]
    if not certain or uncertain:
        return {'status': 'HELD', 'configuration': None, 'candidates': [e['id'] for e in certain[-1:] + uncertain],
                'reason': 'missing_or_uncertain_effective_boundary'}
    chosen = max(certain, key=lambda e: e['effective'][1])
    # Overlapping/reversed events cannot give a unique effective configuration.
    if any(e != chosen and e['effective'][1] >= chosen['effective'][0] for e in certain):
        return {'status': 'HELD', 'configuration': None, 'candidates': [e['id'] for e in certain],
                'reason': 'overlapping_configuration_events'}
    return {'status': 'RECORDED_ASSOCIATION', 'configuration': chosen['id'], 'candidates': [chosen['id']],
            'reason': chosen['basis']}


def query(raw, binding, *, clock, boot, channel, start, end, offset=0, limit=16):
    for s in (clock, boot, channel): text(s)
    if number(start) >= number(end): raise ValueError('Require a nonempty half-open interval')
    c.integer(offset, maximum=MAX_ROWS); c.integer(limit, minimum=1, maximum=32)
    all_rows = [r for r in rows(raw, binding) if r['clock'] == clock and r['boot'] == boot and r['channel'] == channel]
    selected = [dict(r, configuration=attribution(r, binding)) for r in all_rows if r['time'] is not None and start <= r['time'] < end]
    unplaced = [r for r in all_rows if r['time'] is None]
    ids = {}; duplicates = 0
    for r in selected:
        if r['id'] in ids:
            if ids[r['id']] != r: raise ValueError('Conflicting duplicate observation identity')
            duplicates += 1
        else: ids[r['id']] = r
    selected = sorted(ids.values(), key=lambda r: (r['time'], r['id']))
    receipts = [r['receipt']['value'] for r in all_rows if r['receipt'] and type(r['receipt']['value']) in (float, int)]
    gaps = [g for g in binding['context']['gaps'] if g['end'] >= start and g['start'] < end]
    return {'schema': 'ncp.operational-query.v1', 'binding_ref': content_identity(binding), 'source_ref': binding['source_ref'],
        'selection': {'clock': clock, 'boot': boot, 'channel': channel, 'start': start, 'end': end, 'interval': 'half_open'},
        'rows': selected[offset:offset+limit], 'total': len(selected), 'unplaced_count': len(unplaced),
        'next_offset': offset+limit if offset+limit < len(selected) else None,
        'coverage': {'observed_range': None if not selected else [selected[0]['time'], selected[-1]['time']],
                     'gaps': gaps, 'completeness': binding['context']['completeness'],
                     'duplicate_deliveries': duplicates, 'receipt_order_reversed': receipts != sorted(receipts),
                     'sample_validity': sorted(set(r['quality'] for r in selected)),
                     'freshness': 'historical_snapshot; global_currentness_not_established',
                     'connectivity': binding['context']['connectivity'], 'clock_drift': 'not_inferred'},
        'limits': {'maximum_rows': MAX_ROWS, 'page_size': limit, 'new_executions': 0, 'device_contact': False}}


def config_diff(left, right):
    configurations([left]); configurations([right])
    a, b = parse(c.unpack(left['raw'], 16384)), parse(c.unpack(right['raw'], 16384))
    def flatten(v, prefix=''):
        result = {}
        for k, val in v.items():
            path = prefix + '/' + k.replace('~', '~0').replace('/', '~1')
            if type(val) is dict and val: result.update(flatten(val, path))
            else: result[path] = val
        return result
    a, b = flatten(a), flatten(b); fields = []
    for k in sorted(a.keys() | b.keys()):
        status = ('absent_left' if k not in a else 'absent_right' if k not in b else
                  'incomparable' if type(a[k]) != type(b[k]) else 'unchanged' if a[k] == b[k] else 'changed')
        fields.append({'field': k, 'status': status, 'left': a.get(k), 'right': b.get(k)})
    return {'schema': 'ncp.configuration-diff.v1', 'left': deepcopy(left), 'right': deepcopy(right), 'fields': fields,
            'confounders': [v['field'] for v in fields if v['status'] != 'unchanged'],
            'claim': 'recorded association; no causal identification', 'authority': deepcopy(AUTHORITY)}
