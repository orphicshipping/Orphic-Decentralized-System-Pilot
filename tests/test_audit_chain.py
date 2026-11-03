"""Local POSIX audit durability, independent anchors and no duplicate dispatch."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

fcntl = pytest.importorskip('fcntl', reason='Audit lock qualification requires POSIX flock')

import ciw
from ciw import audit_chain as audit

pytestmark = pytest.mark.skipif(os.name != 'posix' or not hasattr(os,'O_NOFOLLOW'),
                               reason='Real no-follow reads and flock require POSIX')
REF = 'sha256:'+'a'*64


def event(call='call-1', **updates):
    return {'call_id':call, 'operation':'ncp_fixture', 'principal_ref':REF,
            'request_ref':'sha256:'+'b'*64, 'result_ref':None, 'phase':'admitted',
            'effect_state':'not_started','recorded_at':'2026-11-03T00:00:00.000Z',
            'error_code':None, 'reconciliation':None, **updates}


def chain(root, **kwargs):
    return audit.AuditChain(root/'audit.jsonl', chain_id='test-chain',
                           allowed_operations=['ncp_fixture'], **kwargs)


class DurableAnchor:
    """Test host's independent SQLite CAS, never an in-memory restart claim."""
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute('create table if not exists anchor (id integer primary key, value text not null)')
            db.execute('insert or ignore into anchor values (1,?)',
                       (json.dumps(audit.empty_anchor('test-chain')),))
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.execute('pragma synchronous=FULL')
        return db
    def read(self):
        with self.connect() as db: return json.loads(db.execute('select value from anchor where id=1').fetchone()[0])
    def cas(self, before, after):
        with self.connect() as db:
            db.execute('begin immediate')
            if json.loads(db.execute('select value from anchor where id=1').fetchone()[0]) != before: return False
            db.execute('update anchor set value=? where id=1', (json.dumps(after),))
        return True


def wrapper(root, **chain_kwargs):
    journal = chain(root, **chain_kwargs)
    anchor = DurableAnchor(root/'independent-anchor.sqlite')
    bound = audit.AuditedCall(journal, read_anchor=anchor.read, compare_and_set_anchor=anchor.cas,
                             principal_ref=REF)
    return bound, anchor


def invoke(bound, call=lambda: {'ok':True}, **kwargs):
    return bound.invoke(operation='ncp_fixture', call_id='call-1', request_ref='sha256:'+'b'*64,
                        call=call, result_ref=lambda value:'sha256:'+sha256(audit._encode(value)).hexdigest(), **kwargs)


def test_canonical_chain_reopen_replay_and_independent_anchor(tmp_path):
    journal = chain(tmp_path); empty = audit.empty_anchor('test-chain')
    first = journal.append(empty, event())
    assert journal.append(empty,event())['replayed'] is True
    last = journal.append(first['anchor'], event(phase='succeeded',effect_state='returned',result_ref=REF))
    assert len(chain(tmp_path).verify(last['anchor'])) == 2
    records = [json.loads(line) for line in journal.path.read_bytes().splitlines()]
    assert records[1]['previous_hash'] == records[0]['hash']
    assert journal.path.read_bytes() == b''.join(audit._encode(row)+b'\n' for row in records)
    for removed in (b'',journal.path.read_bytes().splitlines(keepends=True)[0]):
        journal.path.write_bytes(removed)
        with pytest.raises(audit.AuditRefusal,match='anchor_mismatch'):
            chain(tmp_path).verify(last['anchor'])
    journal.path.unlink()
    with pytest.raises(audit.AuditRefusal,match='anchor_mismatch'):
        chain(tmp_path).verify(last['anchor'])


@pytest.mark.parametrize('rehash',[False,True])
def test_content_mutation_even_rehashed_cannot_match_external_anchor(tmp_path,rehash):
    journal = chain(tmp_path)
    result = journal.append(audit.empty_anchor('test-chain'),event(phase='observed'))
    record = result['record']; record['event']['request_ref']='sha256:'+'c'*64
    if rehash: record['hash']=audit._digest({k:v for k,v in record.items() if k!='hash'})
    journal.path.write_bytes(audit._encode(record)+b'\n')
    with pytest.raises(audit.AuditRefusal,match='hash_mismatch|anchor_mismatch'):
        journal.verify(result['anchor'])


@pytest.mark.parametrize('updates',[
    {'raw_request':{'credential':'synthetic-secret'}}, {'operation':[]}, {'phase':{}},
    {'effect_state':False}, {'call_id':'x'*129}, {'recorded_at':'2026-02-30T00:00:00.000Z'},
    {'result_ref':'not-a-digest'},{'error_code':'exception text with secrets'}, {'operation':'unlisted'},
    {'phase':'succeeded'}, {'effect_state':'committed'},
])
def test_closed_redacted_event_schema_refuses_before_write(tmp_path,updates):
    journal = chain(tmp_path)
    with pytest.raises(audit.AuditRefusal): journal.append(audit.empty_anchor('test-chain'),event(**updates))
    assert not journal.path.exists()


def test_capacity_reserves_terminal_record_and_stops_new_dispatch(tmp_path):
    bound, _ = wrapper(tmp_path,max_records=1)
    calls=[]
    with pytest.raises(audit.AuditOutcomeUncertain) as caught:
        invoke(bound,lambda:calls.append('not allowed'))
    assert caught.value.effect_state=='not_started' and calls==[]
    assert not bound.chain.path.exists()
    journal = chain(tmp_path,max_records=3)
    first=journal.append(audit.empty_anchor('test-chain'),event())
    with pytest.raises(audit.AuditRefusal,match='capacity|unresolved'):
        journal.append(first['anchor'],event('call-2'))
    assert journal.append(first['anchor'],event(phase='unknown',effect_state='unknown'))['anchor']['sequence']==2


@pytest.mark.parametrize('stage',['fsync','directory_fsync','guard_exit'])
def test_after_effect_failure_preserves_bytes_and_requires_reconciliation(tmp_path,monkeypatch,stage):
    journal=chain(tmp_path); original_sync=os.fsync; original_flock=fcntl.flock
    primary=OSError('synthetic after-effect fault'); syncs=0
    def sync(fd):
        nonlocal syncs
        original_sync(fd); syncs+=1
        if (stage=='fsync' and syncs==1) or (stage=='directory_fsync' and syncs==2): raise primary
    def flock(fd,operation):
        original_flock(fd,operation)
        if stage=='guard_exit' and operation==fcntl.LOCK_UN: raise primary
    with monkeypatch.context() as patch:
        patch.setattr(os,'fsync',sync); patch.setattr(fcntl,'flock',flock)
        with pytest.raises(audit.AuditOutcomeUncertain) as caught:
            journal.append(audit.empty_anchor('test-chain'),event())
    assert caught.value.__cause__ is primary
    assert journal.path.stat().st_size>0
    with pytest.raises(audit.AuditOutcomeUncertain): journal.append(audit.empty_anchor('test-chain'),event())
    diagnostic=journal.inspect_unanchored()
    assert diagnostic['externally_anchored'] is False
    # Explicit test-host reconciliation adopts the independently inspected
    # durable write. Production must apply its own custody reconciliation.
    journal.reconciled(diagnostic['anchor'])
    result=journal.append(diagnostic['anchor'],event(phase='unknown',effect_state='unknown'))
    assert len(journal.verify(result['anchor']))==2


def test_partial_write_is_preserved_as_uncertain_not_truncated(tmp_path,monkeypatch):
    journal=chain(tmp_path); original=os.write; count=0
    def write(fd,data):
        nonlocal count
        count+=1
        if count==1: return original(fd,data[:13])
        raise OSError('incomplete publication')
    with monkeypatch.context() as patch:
        patch.setattr(os,'write',write)
        with pytest.raises(audit.AuditOutcomeUncertain): journal.append(audit.empty_anchor('test-chain'),event())
    retained=journal.path.read_bytes()
    assert len(retained)==13
    with pytest.raises(audit.AuditRefusal,match='incomplete'): chain(tmp_path).verify(audit.empty_anchor('test-chain'))
    assert journal.path.read_bytes()==retained


LOCK_PROBE = """import fcntl,os,sys
fd=os.open(sys.argv[1],os.O_RDWR)
fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
os.close(fd)
"""


@pytest.mark.parametrize('stage',['after_acquire','unlock_before','unlock_after','close_after'])
def test_failed_owner_stays_alive_while_another_process_reacquires(tmp_path,monkeypatch,stage):
    journal=chain(tmp_path); target=[]; reused=[]; original_lock=fcntl.flock; original_close=os.close
    fault=KeyboardInterrupt() if stage=='after_acquire' else OSError(stage)
    def flock(fd,operation):
        if operation & fcntl.LOCK_EX:
            target.append(fd); original_lock(fd,operation)
            if stage=='after_acquire': raise fault
        elif operation==fcntl.LOCK_UN:
            if stage=='unlock_before': raise fault
            original_lock(fd,operation)
            if stage=='unlock_after': raise fault
    def close(fd):
        original_close(fd)
        if stage=='close_after' and target and fd==target[-1] and not reused:
            reused.append(os.open('/dev/null',os.O_RDONLY))
            assert reused[0]==fd
            raise fault
    with monkeypatch.context() as patch:
        patch.setattr(fcntl,'flock',flock); patch.setattr(os,'close',close)
        with pytest.raises(type(fault)) as caught:
            with journal._guard(): pass
    assert caught.value is fault
    lock_path=journal.path.with_name(journal.path.name+'.lock'); inode=lock_path.stat().st_ino
    # Test process is still alive; cleanup cannot rely on process death.
    probe=subprocess.run([sys.executable,'-c',LOCK_PROBE,str(lock_path)],capture_output=True,timeout=5)
    assert probe.returncode==0,probe.stderr
    for fd in reused:
        os.fstat(fd); original_close(fd)  # cleanup did not close another owner's reused descriptor
    result=journal.append(audit.empty_anchor('test-chain'),event(phase='observed'))
    assert journal.verify(result['anchor']) and lock_path.stat().st_ino==inode


@pytest.mark.parametrize('stage',['admission_write','admission_cas','terminal_write','terminal_cas'])
def test_audited_dispatch_failure_preserves_effects_and_fences_retry(tmp_path,monkeypatch,stage):
    bound,anchor=wrapper(tmp_path); effects=[]; original_append=bound.chain.append; original_cas=anchor.cas
    fault=OSError('synthetic sink failure')
    def append(before,event):
        if stage==('admission_write' if event['phase']=='admitted' else 'terminal_write'): raise fault
        return original_append(before,event)
    def cas(before,after):
        if stage==('admission_cas' if after['sequence']==1 else 'terminal_cas'): raise fault
        return original_cas(before,after)
    with monkeypatch.context() as patch:
        patch.setattr(bound.chain,'append',append); patch.setattr(bound,'compare_and_set_anchor',cas)
        with pytest.raises(audit.AuditOutcomeUncertain) as caught:
            invoke(bound,lambda:effects.append('committed') or {'committed':True})
    assert effects == ([] if stage.startswith('admission') else ['committed'])
    assert caught.value.effect_state == ('not_started' if stage.startswith('admission') else 'returned')
    with pytest.raises(audit.AuditOutcomeUncertain): invoke(bound,lambda:effects.append('duplicate'))
    diagnostic=bound.chain.inspect_unanchored()
    if stage!='admission_write':
        resolution=resolution_for(diagnostic['records'][-1])
        bound.reconcile_call('call-1',resolution=resolution,validate=lambda context:True)
    else:
        bound.resume_after_reconciliation()
    if stage!='admission_write':
        with pytest.raises(audit.AuditReplay): invoke(bound,lambda:effects.append('duplicate'))
    else:
        assert invoke(bound)=={'ok':True}
    assert 'duplicate' not in effects


def resolution_for(record, **overrides):
    return {'schema':'ciw.audit-reconciliation.v1','review_id':'synthetic-host-review-1',
            'record_hash':record['hash'],'decision':'preserve_effects_no_retry',
            'custody':'confirmed_stopped','effect_state':'unknown','evidence_ref':REF,
            'reviewed_at':'2026-11-03T00:01:00.000Z',**overrides}


def test_new_correlation_id_cannot_escape_durable_pending_or_unknown_fence(tmp_path):
    bound,anchor=wrapper(tmp_path)
    admitted=bound.chain.append(anchor.read(),event())
    assert anchor.cas(anchor.read(),admitted['anchor'])
    for phase in ('admitted','unknown'):
        if phase=='unknown':
            terminal=bound.chain.append(anchor.read(),event(phase='unknown',effect_state='unknown'))
            assert anchor.cas(anchor.read(),terminal['anchor'])
        reopened,_=wrapper(tmp_path)
        with pytest.raises(audit.AuditRefusal,match='unresolved'):
            reopened.invoke(operation='ncp_fixture',call_id='fresh-random-correlation',request_ref=REF,
                call=lambda:pytest.fail('pending custody must fence fresh work'),result_ref=lambda _:REF)
        with pytest.raises(audit.AuditRefusal,match='unresolved'): reopened.resume_after_reconciliation()
        with pytest.raises(audit.AuditRefusal,match='unresolved'):
            reopened.chain.append(anchor.read(),event('another-correlation'))
    record=bound.chain.verify(anchor.read())[-1]
    with pytest.raises(audit.AuditRefusal,match='not_validated'):
        reopened.reconcile_call('call-1',resolution=resolution_for(record),validate=lambda context:False)
    with pytest.raises(audit.AuditRefusal,match='binding_mismatch'):
        reopened.reconcile_call('call-1',resolution=resolution_for(record,record_hash='sha256:'+'0'*64),validate=lambda _:True)
    seen=[]
    def validate(context):
        seen.append(context)
        return context['record']==record and context['resolution']==resolution_for(record)
    reopened.reconcile_call('call-1',resolution=resolution_for(record),validate=validate)
    assert len(seen)==1
    reopened,_=wrapper(tmp_path)
    with pytest.raises(audit.AuditReplay): invoke(reopened)
    assert reopened.invoke(operation='ncp_fixture',call_id='new-reviewed-call',request_ref=REF,
        call=lambda:{'ok':True},result_ref=lambda _:REF)=={'ok':True}
    records=reopened.chain.verify(anchor.read())
    assert records[2]['event']['phase']=='reconciled'
    assert records[2]['event']['effect_state']=='unknown'  # review did not turn ambiguity into rollback


def test_reconciliation_does_not_adopt_tampered_external_anchor_history(tmp_path):
    bound,anchor=wrapper(tmp_path)
    admitted=bound.chain.append(anchor.read(),event());anchor.cas(anchor.read(),admitted['anchor'])
    record=deepcopy(admitted['record']); record['event']['request_ref']='sha256:'+'d'*64
    record['hash']=audit._digest({k:v for k,v in record.items() if k!='hash'})
    bound.chain.path.write_bytes(audit._encode(record)+b'\n')
    with pytest.raises(audit.AuditRefusal,match='anchor_not_prefix'):
        bound.reconcile_call('call-1',resolution=resolution_for(record),validate=lambda _:pytest.fail('invalid history'))


CRASH = r'''
import json,os,sqlite3,sys
from pathlib import Path
from ciw import audit_chain as a
root=Path(sys.argv[1]);stage=sys.argv[2]
def read():
 with sqlite3.connect(root/'independent-anchor.sqlite') as db:
  return json.loads(db.execute('select value from anchor where id=1').fetchone()[0])
def cas(before,after):
 with sqlite3.connect(root/'independent-anchor.sqlite') as db:
  db.execute('pragma synchronous=FULL');db.execute('begin immediate')
  if json.loads(db.execute('select value from anchor where id=1').fetchone()[0])!=before:return False
  db.execute('update anchor set value=? where id=1',(json.dumps(after),))
 return True
def op():
 if stage=='after_effect':
  fd=os.open(root/'committed-effect',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  os.write(fd,b'synthetic durable effect');os.fsync(fd);os.close(fd)
 os._exit(91)
bound=a.AuditedCall(a.AuditChain(root/'audit.jsonl',chain_id='test-chain',allowed_operations=['ncp_fixture']),
 read_anchor=read,compare_and_set_anchor=cas,principal_ref='sha256:'+'a'*64)
bound.invoke(operation='ncp_fixture',call_id='crashed-call',request_ref='sha256:'+'b'*64,call=op,result_ref=lambda _:'sha256:'+'c'*64)
'''


@pytest.mark.parametrize('stage',['after_admission','after_effect'])
def test_real_crash_reopen_requires_durable_host_custody_review(tmp_path,stage):
    bound,anchor=wrapper(tmp_path)
    env={**os.environ,'PYTHONPATH':str(Path(ciw.__file__).resolve().parent.parent)}
    child=subprocess.run([sys.executable,'-c',CRASH,str(tmp_path),stage],env=env,
                         capture_output=True,timeout=10)
    assert child.returncode==91,child.stderr
    reopened,_=wrapper(tmp_path)
    with pytest.raises(audit.AuditRefusal,match='unresolved'):
        invoke(reopened,lambda:pytest.fail('fresh correlation must not escape crash'))
    record=reopened.chain.verify(anchor.read())[-1]
    assert record['event']['phase']=='admitted'
    effect=tmp_path/'committed-effect'
    assert effect.exists()==(stage=='after_effect')
    resolution=resolution_for(record,effect_state='committed' if effect.exists() else 'unknown')
    reviewed=[]
    def independently_review(context):
        reviewed.append(context)
        return (child.returncode==91 and context['record']==record and
                (not effect.exists() or effect.read_bytes()==b'synthetic durable effect'))
    reopened.reconcile_call('crashed-call',resolution=resolution,validate=independently_review)
    assert len(reviewed)==1
    restarted,_=wrapper(tmp_path)
    with pytest.raises(audit.AuditReplay):
        restarted.invoke(operation='ncp_fixture',call_id='crashed-call',request_ref='sha256:'+'b'*64,
            call=lambda:pytest.fail('crashed call must never replay execution'),result_ref=lambda _:REF)
    assert invoke(restarted)=={'ok':True}
    assert restarted.chain.verify(anchor.read())[1]['event']['effect_state']==resolution['effect_state']


@pytest.mark.parametrize('after_effect',[False,True])
def test_reconciliation_anchor_failure_recovers_exact_resolution_without_new_event(tmp_path,monkeypatch,after_effect):
    bound,anchor=wrapper(tmp_path)
    initial=bound.chain.append(anchor.read(),event());anchor.cas(anchor.read(),initial['anchor'])
    resolution=resolution_for(initial['record'])
    original=bound.compare_and_set_anchor
    def fail(before,after):
        if after_effect: original(before,after)
        raise OSError('anchor response lost')
    with monkeypatch.context() as patch:
        patch.setattr(bound,'compare_and_set_anchor',fail)
        with pytest.raises(audit.AuditOutcomeUncertain):
            bound.reconcile_call('call-1',resolution=resolution,validate=lambda _:True)
    assert len(bound.chain.inspect_unanchored()['records'])==2
    reopened,_=wrapper(tmp_path)
    result=reopened.reconcile_call('call-1',resolution=resolution,validate=lambda _:True)
    assert result['replayed'] is True
    assert len(reopened.chain.verify(anchor.read()))==2
    with pytest.raises(audit.AuditReplay): invoke(reopened)


@pytest.mark.parametrize('phase,effect_state',[
    ('failed','unknown'),('refused','may_have_effects'),('succeeded','unknown'),('failed','may_have_effects')])
def test_effect_uncertainty_fences_reopen_regardless_of_presentation_phase(tmp_path,phase,effect_state):
    bound,anchor=wrapper(tmp_path,max_records=3)
    bound.result_outcome=lambda value:{'phase':phase,'effect_state':effect_state}
    invoke(bound)
    reopened,_=wrapper(tmp_path,max_records=3)
    with pytest.raises(audit.AuditRefusal,match='unresolved'):
        reopened.invoke(operation='ncp_fixture',call_id='new-id',request_ref=REF,
            call=lambda:pytest.fail('effect uncertainty bypassed'),result_ref=lambda _:REF)
    with pytest.raises(audit.AuditRefusal,match='unresolved'): reopened.resume_after_reconciliation()
    record=reopened.chain.verify(anchor.read())[-1]
    reopened.reconcile_call('call-1',resolution=resolution_for(record),validate=lambda _:True)
    assert len(reopened.chain.verify(anchor.read()))==3


@pytest.mark.parametrize('value',[
    {'status':'refused','error':{'code':'outcome_unknown'}},
    {'status':'ok','error':{'code':'outcome_unknown'}},
    {'status':'refused','reason':'runtime_cleanup_uncertain'},
    {'status':'failed','error':{'code':'RUNTIME_CLEANUP_UNCERTAIN'}},
    {'status':'refused','effect_state':'unknown'},
    {'status':'refused','error':{'effect_state':'may_have_effects'}},
    {'status':'ok','uncertain':True},
    {'status':'refused','outcome_unknown':True},
])
def test_standard_principal_uncertainty_envelope_remains_durably_fenced(tmp_path,value):
    bound,anchor=wrapper(tmp_path)
    bound.result_outcome=audit.standard_result_outcome
    assert invoke(bound,lambda:value)==value
    last=bound.chain.verify(anchor.read())[-1]['event']
    assert last['phase']=='unknown' and last['effect_state'] in {'unknown','may_have_effects'}
    reopened,_=wrapper(tmp_path)
    with pytest.raises(audit.AuditRefusal,match='unresolved'):
        reopened.invoke(operation='ncp_fixture',call_id='fresh-correlation',request_ref=REF,
            call=lambda:pytest.fail('unknown effects hidden by refused envelope'),result_ref=lambda _:REF)


def test_initial_anchor_io_failure_is_redacted_predispatch_uncertainty(tmp_path,monkeypatch):
    bound,_=wrapper(tmp_path);fault=OSError('synthetic private path must not be public error text')
    def unavailable(): raise fault
    monkeypatch.setattr(bound,'read_anchor',unavailable)
    with pytest.raises(audit.AuditOutcomeUncertain) as caught:
        invoke(bound,lambda:pytest.fail('anchor I/O failed before dispatch'))
    assert str(caught.value)=='audit_outcome_uncertain'
    assert caught.value.effect_state=='not_started' and caught.value.__cause__ is fault


@pytest.mark.parametrize('target',['journal','lock'])
def test_symlink_records_and_locks_refuse_without_following(tmp_path,target):
    journal=chain(tmp_path);outside=tmp_path/'synthetic-external';outside.write_bytes(b'unchanged')
    chosen=journal.path if target=='journal' else journal.path.with_name(journal.path.name+'.lock')
    chosen.symlink_to(outside)
    with pytest.raises((OSError,audit.AuditRefusal)):
        journal.append(audit.empty_anchor('test-chain'),event())
    assert outside.read_bytes()==b'unchanged'


def test_short_writes_finish_complete_canonical_record_and_duplicate_keys_refuse(tmp_path,monkeypatch):
    journal=chain(tmp_path);original=os.write
    with monkeypatch.context() as patch:
        patch.setattr(os,'write',lambda fd,raw:original(fd,raw[:3]))
        result=journal.append(audit.empty_anchor('test-chain'),event(phase='observed'))
    assert journal.verify(result['anchor'])
    raw=journal.path.read_bytes();journal.path.write_bytes(raw.replace(b'{',b'{"schema":"duplicate",',1))
    with pytest.raises(audit.AuditRefusal,match='duplicate'): journal.verify(result['anchor'])


def test_reopen_uses_durable_anchor_and_never_reexecutes_success(tmp_path):
    bound,anchor=wrapper(tmp_path)
    assert invoke(bound)=={'ok':True}
    reopened,new_anchor=wrapper(tmp_path)
    assert new_anchor.read()==anchor.read()
    with pytest.raises(audit.AuditReplay) as caught: invoke(reopened,lambda:pytest.fail('duplicate dispatch'))
    assert caught.value.event['phase']=='succeeded'


@pytest.mark.parametrize('status,phase,effects',[
    ('ok','succeeded','returned'),('refused','refused','returned'),('denied','refused','returned'),
    ('failed','failed','returned'),('unknown','unknown','unknown'),('partial','unknown','unknown'),
    ('unrecognized','unknown','returned'),([], 'unknown','returned'),
])
def test_host_status_classification_never_calls_refusal_success_or_claims_rollback(tmp_path,status,phase,effects):
    bound,anchor=wrapper(tmp_path)
    bound.result_outcome=audit.standard_result_outcome
    value={'status':status,'synthetic':True}
    assert invoke(bound,lambda:value)==value
    recorded=bound.chain.verify(anchor.read())[-1]['event']
    assert recorded['phase']==phase and recorded['effect_state']==effects


def test_invalid_host_outcome_and_committed_sink_error_are_uncertain(tmp_path,monkeypatch):
    bound,anchor=wrapper(tmp_path)
    bound.result_outcome=lambda value:{'phase':'refused','effect_state':'not_started'}
    with pytest.raises(audit.AuditOutcomeUncertain) as caught: invoke(bound)
    assert caught.value.effect_state=='returned'
    assert bound.chain.verify(anchor.read())[-1]['event']['phase']=='admitted'
    other=tmp_path/'other';other.mkdir();bound,_=wrapper(other)
    bound.result_outcome=lambda value:{'phase':'succeeded','effect_state':'committed'}
    original=bound.chain.append
    def append(before,event):
        if event['phase']=='succeeded': raise OSError('audit unavailable after committed effect')
        return original(before,event)
    monkeypatch.setattr(bound.chain,'append',append)
    with pytest.raises(audit.AuditOutcomeUncertain) as caught: invoke(bound)
    assert caught.value.effect_state=='committed' and caught.value.result_ref is not None


def test_primary_cancellation_and_cleanup_failure_both_remain_diagnostic(tmp_path,monkeypatch):
    bound,_=wrapper(tmp_path); cancellation=KeyboardInterrupt(); cleanup=OSError('terminal audit error')
    original=bound.chain.append
    def append(before,event):
        if event['phase']=='unknown': raise cleanup
        return original(before,event)
    monkeypatch.setattr(bound.chain,'append',append)
    def operation(): raise cancellation
    with pytest.raises(audit.AuditOutcomeUncertain) as caught: invoke(bound,operation)
    assert caught.value.effect_state=='unknown'
    assert caught.value.__cause__ is cleanup and cleanup.__context__ is cancellation


def test_cancellation_with_successful_audit_keeps_exact_primary_exception(tmp_path):
    bound,anchor=wrapper(tmp_path); cancellation=KeyboardInterrupt()
    def operation(): raise cancellation
    with pytest.raises(KeyboardInterrupt) as caught: invoke(bound,operation)
    assert caught.value is cancellation
    assert bound.chain.verify(anchor.read())[-1]['event']['phase']=='unknown'
    with pytest.raises(audit.AuditReplay): invoke(bound)


CHILD = r'''
import json,os,sqlite3,sys,time
from pathlib import Path
from ciw import audit_chain as a
from datetime import datetime as RealClock
root=Path(sys.argv[1]); worker=sys.argv[2]
class Clock:
 fromisoformat=RealClock.fromisoformat
 @staticmethod
 def now(_):
  class Stamp:
   def isoformat(self,**kwargs): return '2026-11-03T00:00:00.000+00:00'
  return Stamp()
a.datetime=Clock
class Chain(a.AuditChain):
 def verify(self,anchor):
  rows=super().verify(anchor)
  if anchor['sequence']==0:
   (root/('ready-'+worker)).write_text('ready')
   until=time.monotonic()+10
   while not (root/'start').exists():
    if time.monotonic()>until: raise RuntimeError('barrier timeout')
    time.sleep(.01)
  return rows
def read():
 with sqlite3.connect(root/'independent-anchor.sqlite') as db:
  return json.loads(db.execute('select value from anchor where id=1').fetchone()[0])
def cas(before,after):
 with sqlite3.connect(root/'independent-anchor.sqlite',timeout=3) as db:
  db.execute('pragma synchronous=FULL');db.execute('begin immediate')
  if json.loads(db.execute('select value from anchor where id=1').fetchone()[0])!=before: return False
  db.execute('update anchor set value=? where id=1',(json.dumps(after),))
 return True
def op():
 fd=os.open(root/'effects',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
 try: os.write(fd,b'once\n');os.fsync(fd)
 finally: os.close(fd)
 return {'ok':True}
c=Chain(root/'audit.jsonl',chain_id='test-chain',allowed_operations=['ncp_fixture'])
bound=a.AuditedCall(c,read_anchor=read,compare_and_set_anchor=cas,principal_ref='sha256:'+'a'*64)
try:
 bound.invoke(operation='ncp_fixture',call_id='same-call',request_ref='sha256:'+'b'*64,call=op,result_ref=lambda _:'sha256:'+'c'*64)
 print('dispatched',flush=True)
except a.AuditRefusal as exc:
 print(type(exc).__name__,flush=True)
'''


def test_separate_processes_identical_admission_never_duplicate_execution(tmp_path):
    anchor=DurableAnchor(tmp_path/'independent-anchor.sqlite')
    env={**os.environ,'PYTHONPATH':str(Path(ciw.__file__).resolve().parent.parent)}
    children=[subprocess.Popen([sys.executable,'-c',CHILD,str(tmp_path),str(i)],stdout=subprocess.PIPE,
              stderr=subprocess.PIPE,env=env) for i in range(6)]
    try:
        until=time.monotonic()+10
        while len(list(tmp_path.glob('ready-*')))<6 and time.monotonic()<until: time.sleep(.02)
        assert len(list(tmp_path.glob('ready-*')))==6
        (tmp_path/'start').write_text('go')
        outputs=[]
        for child in children:
            out,err=child.communicate(timeout=10)
            assert child.returncode==0,err
            outputs.append(out.strip())
        assert outputs.count(b'dispatched')==1
        assert (tmp_path/'effects').read_bytes()==b'once\n'
        assert len(chain(tmp_path).verify(anchor.read()))==2
    finally:
        for child in children:
            if child.poll() is None: child.kill()
            child.wait(timeout=5)
