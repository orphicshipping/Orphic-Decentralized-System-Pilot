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
        old=self.journal.state(self.key)
        self.journal.compare_and_set_state(self.key, 0 if old is None else old['revision'],value)

    def request_stop(self):
        v=self.state()
        if v['status']!='RUNNING': return v
        self._save(dict(v,stop_requested=True)); return self.state()

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


def link_segments(chunks, journal, *, expected_manifest, boot_id):
    """Delegate finite receive and capture to the existing Link gateway."""
    from .link_gateway import receive, capture
    for chunk in chunks:
        receive([chunk],journal,expected_manifest=expected_manifest)
        yield encode(capture(journal,expected_manifest=expected_manifest,boot_id=boot_id))
