"""Independent bounded collector lifecycle in the existing Link journal.

An operator process owns finite receive/read-only sources, not the MCP client.
A process death leaves RUNNING as an unknown outcome until explicit recovery;
recovery never reconnects and never repeats an in-flight device read.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import time
import uuid

from . import link_contract as c
from .agent_api import encode, parse, identifier
from .agent_recovery import _open_lock
from .control_contracts import keys, number
from .link_store import Journal, safe_path, MAX_MESSAGES, MAX_STORE_BYTES
from .operational_store import Records
from .operational_context import reference, MAX_SOURCE


def workload_gate(temperature):
    """Independent station prerequisite; no workload launch exists in this adapter."""
    try:
        number(temperature)
        permitted=0 <= temperature < 80
    except ValueError: permitted=False
    return {'status':'PERMITTED_PREREQUISITE_ONLY' if permitted else 'BLOCKED', 'cutoff_c':80,
            'equality_blocked':True, 'temperature_c':temperature, 'workload_launched':False}


class Collector:
    def __init__(self, journal, name, *, max_segments=16, max_bytes=4*1024*1024, cadence_s=1.0):
        self.journal=journal; self.records=Records(journal); self.name=identifier(name)
        c.integer(max_segments,minimum=1,maximum=64); c.integer(max_bytes,minimum=1,maximum=8*1024*1024)
        if not 0 <= number(cadence_s) <= 60: raise ValueError('Collector cadence must be 0..60 seconds')
        self.limits={'max_segments':max_segments,'max_bytes':max_bytes,'cadence_s':cadence_s,
                     'overflow_policy':'stop_without_overwrite','restart':'explicit_no_automatic_reconnect'}
        self.key='oe-collector:' + self.name

    def state(self):
        v=self.journal.state(self.key)
        return {'status':'ABSENT'} if v is None else deepcopy(v['value'])

    def _save(self, value):
        # The process lock owns acquisition fields; operators own a monotone stop
        # flag. Pair each revision with the value read and merge that flag on CAS
        # conflict. Never adopt a newer revision while writing an older stop.
        for _ in range(32):
            old=self.journal.state(self.key)
            merged=deepcopy(value)
            if old and old['value'].get('occurrence') == value.get('occurrence'):
                merged['stop_requested'] = bool(value['stop_requested'] or old['value']['stop_requested'])
            if merged.get('stop_requested') and merged['status'] in {'CLOSED','LIMIT_CLOSED'}:
                merged['status']='STOPPED'
            try:
                self.journal.compare_and_set_state(self.key, 0 if old is None else old['revision'],merged)
            except ValueError as exc:
                if str(exc) != 'Stale simulation/configuration revision': raise
                continue
            value.clear(); value.update(merged)
            return
        raise RuntimeError('Collector state contention; retained outcome requires explicit recovery')

    def request_stop(self):
        for _ in range(32):
            old=self.journal.state(self.key)
            if old is None or old['value']['status']!='RUNNING': return self.state()
            value=dict(old['value'],stop_requested=True)
            try: self.journal.compare_and_set_state(self.key,old['revision'],value)
            except ValueError as exc:
                if str(exc) != 'Stale simulation/configuration revision': raise
                continue
            return self.state()
        raise RuntimeError('Collector stop contention; retry explicit stop')

    def recover(self):
        # The OS lock proves the owner is gone. This operation cannot contact sources.
        lock=_open_lock(safe_path(str(self.journal.path)+'.collector-'+self.name+'.lock'))
        try:
            v=self.state()
            if v['status']=='RUNNING': self._save(dict(v,status='INTERRUPTED_UNKNOWN',in_flight='unknown',automatic_replay=False))
            return self.state()
        finally: lock.close()

    def run(self, read_segment, *, source_identity, restart=False, wait=time.sleep):
        c.bounded(source_identity,8192)
        lock=_open_lock(safe_path(str(self.journal.path)+'.collector-'+self.name+'.lock'))
        try:
            old=self.state()
            if old['status']!='ABSENT' and not restart: raise ValueError('Collector already has an occurrence; explicit restart required')
            if old['status']=='RUNNING': raise ValueError('Recover unknown interrupted collector before restart')
            # A restart allocates a fresh occurrence; it never erases the old receipt.
            occurrence='collector-'+uuid.uuid4().hex
            state={'schema':'ncp.operational-collector.v1','collector':self.name,'occurrence':occurrence,
                'status':'RUNNING','limits':self.limits,'source_identity':deepcopy(source_identity),
                'segments':[],'bytes':0,'stop_requested':False,'in_flight':None,'gaps':[],
                'previous_occurrence':old.get('occurrence'),'automatic_replay':False}
            self._save(state)
            while len(state['segments'])<self.limits['max_segments']:
                # Read the external stop flag; collection does not depend on a client.
                state=self.state()
                if state['stop_requested']:
                    state.update(status='STOPPED',in_flight=None); break
                count,size=self.journal.db.execute('SELECT count(*),coalesce(sum(length(value)),0) FROM state').fetchone()
                if count>=MAX_MESSAGES-64 or size>=MAX_STORE_BYTES-2*MAX_SOURCE-131072:
                    state.update(status='STORE_FULL',in_flight=None)
                    state['gaps'].append({'reason':'reserved_control_headroom_backpressure'}); break
                state['in_flight']={'index':len(state['segments']),'started_receipt_ns':str(time.time_ns())}
                self._save(state)  # Claim before any read or receive.
                if state['stop_requested']:
                    state.update(status='STOPPED',in_flight=None); break
                try: raw=read_segment(len(state['segments']))
                except StopIteration:
                    state.update(status='CLOSED',in_flight=None); break
                except Exception as exc:
                    state.update(status='UNAVAILABLE',in_flight='failed_or_incomplete')
                    state['gaps'].append({'reason':type(exc).__name__,'detail':str(exc)[:256]}); break
                if type(raw) is not bytes or not 0<len(raw)<=MAX_SOURCE:
                    state.update(status='MALFORMED_SEGMENT',in_flight='not_admitted'); break
                if state['bytes']+len(raw)>self.limits['max_bytes']:
                    state.update(status='QUOTA_STOP',in_flight='received_not_admitted')
                    state['gaps'].append({'reason':'quota','discarded_byte_count':len(raw)}); break
                try:
                    ref=self.records.put_bytes(raw)
                    receipt={'schema':'ncp.operational-segment.v1','collector':self.name,'occurrence':occurrence,
                        'index':len(state['segments']),'source_ref':ref,'source_identity':deepcopy(source_identity),
                        'byte_count':len(raw),'received_ns':str(time.time_ns()),'closure':'bounded_segment',
                        'completeness':'unknown','hardware_action':'receive_or_read_only','admission':'not_performed'}
                    receipt_ref=self.records.put(receipt)
                except ValueError:
                    state.update(status='STORE_FULL',in_flight='received_not_admitted')
                    state['gaps'].append({'reason':'bounded_store_backpressure'}); break
                state['segments'].append(receipt_ref); state['bytes']+=len(raw); state['in_flight']=None
                state['stop_requested']=self.state()['stop_requested']
                self._save(state)
                if self.limits['cadence_s']: wait(self.limits['cadence_s'])
            else: state.update(status='LIMIT_CLOSED',in_flight=None)
            self._save(state)
            self.records.put(dict(state, schema='ncp.operational-collection-receipt.v1'))
            return deepcopy(state)
        finally: lock.close()


def nvml_segments(*, device_index, host, boot, count=4):
    """Open NVML only in the explicitly invoked collector, never during inspection."""
    from .energy_nvml import NVMLEnergyCounter
    c.integer(count,minimum=1,maximum=64)
    with NVMLEnergyCounter(device_index=device_index) as sensor:
        while True:
            yield encode({'schema':'ncp.nvml-recording.v1','sensor':sensor.identity(),
                          'host':host,'boot':boot,'readings':[sensor.read() for _ in range(count)]})


def link_segments(chunks, journal, *, expected_manifest, boot_id, max_records=1,
                  key=None, require_auth=False):
    """Incremental native decoding, durable ingress, bounded noncumulative captures.

    A single decoder owns a transport session. Frames are committed before the
    next frame is parsed; a corrupt/incomplete tail cannot discard that prefix.
    Rotation never closes a partial wire frame or repeats earlier segments.
    """
    from .link_protocol import Decoder, MAX_FRAME
    from .link_store import decode_bound
    from .link_gateway import validate_capture
    c.integer(max_records,minimum=1,maximum=c.MAX_RECORDS)
    device=c.manifest(expected_manifest); c.text(boot_id)
    decoder=Decoder(key=key,require_auth=require_auth)
    manifest_wire=None; pending=[]; seen=set(); ready=[]

    def close_segment():
        nonlocal pending
        if not pending: return
        value={'schema':c.CAPTURE_SCHEMA,'manifest':device,'manifest_wire':manifest_wire,
            'gateway_id':journal.store_id,'boot_id':boot_id,'frames':pending,'calibrations':[],
            'joint_covariance':None,'transport':'local_retained_byte_stream','closure':'operator_selected_finite_capture'}
        validate_capture(value,key=key)
        raw=encode(value)
        if len(raw)>MAX_SOURCE: raise ValueError('Link segment exceeds retained source budget')
        ready.append(raw); pending=[]

    def retain(frame):
        nonlocal manifest_wire
        _,record,message_key,_=decode_bound(frame.wire,device,key=key,require_auth=require_auth)
        journal.append(frame.wire,expected_manifest=device,key=key,require_auth=require_auth)
        if frame.kind=='manifest':
            manifest_wire=c.packed(frame.wire,MAX_FRAME); return
        if record['boot_id']!=boot_id: raise ValueError('Link collector boot differs; select a new source occurrence')
        if message_key in seen: return  # transport retry, not a new observation
        if len(seen)>=MAX_MESSAGES: raise ValueError('Link session budget exceeded; explicit restart required')
        seen.add(message_key)
        # Native receipt metadata comes from committed original rows, including retries.
        row=journal.db.execute('SELECT * FROM messages WHERE message_key=?',(message_key,)).fetchone()
        retained=journal._row(row)
        item={'wire':c.packed(frame.wire,MAX_FRAME),'received_ns':retained['received_ns'],
              'host_clock':retained['host_clock'],'authentication_at_capture':retained['authentication_at_capture']}
        if pending and (len(pending)>=max_records or len(encode(pending))+len(encode(item))+len(encode(device))+8192>MAX_SOURCE):
            close_segment()
        pending.append(item)
        if len(pending)>=max_records: close_segment()

    for chunk in chunks:
        if type(chunk) is not bytes: raise ValueError('Link transport chunks must be bytes')
        # Feed one bounded byte at a time only when necessary to preserve native
        # decoder bounds; callback commits a prefix even when a later frame fails.
        for offset in range(0,len(chunk),MAX_FRAME):
            try: decoder.feed(chunk[offset:offset+MAX_FRAME],on_frame=retain)
            except BaseException:
                # Closed frames remain available to this collector before failure
                # is propagated on its next read. Partial pending frames are held.
                for raw in ready: yield raw
                ready.clear()
                raise
            while ready: yield ready.pop(0)
    decoder.finish()
    close_segment()
    yield from ready
