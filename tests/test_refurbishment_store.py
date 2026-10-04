from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from ciw.refurbishment_contract import example_commands
from ciw.refurbishment_store import CommandConflict, IntegrityError, RefurbishmentStore, RevisionConflict, SCHEMA, validate_export_bundle


def fill(store,asset_id='NS-DEMO-0001'):
    for command in example_commands(asset_id): store.execute(command)
    return store.get_asset(asset_id)


def test_creation_read_open_context_and_no_wal(tmp_path):
    path=tmp_path/'registry.sqlite'
    with pytest.raises(FileNotFoundError): RefurbishmentStore(path)
    assert not path.exists()
    with RefurbishmentStore.create(path) as store:
        assert store.list_assets()==[]
        fill(store)
        with pytest.raises(FileExistsError): RefurbishmentStore.create(path)
        assert store.verify('NS-DEMO-0001')['event_count']==12
    with pytest.raises(ValueError,match='closed'): store.list_assets()
    assert not Path(str(path)+'-wal').exists() and not Path(str(path)+'-journal').exists()
    published=tmp_path/'published.sqlite'; os.link(path,published)
    with RefurbishmentStore(published) as reopened: assert reopened.get_asset('NS-DEMO-0001')['revision']==12
    with pytest.raises(ValueError,match='persistent'): RefurbishmentStore.create(':memory:')


def test_idempotency_returns_original_receipt_after_later_changes(tmp_path):
    with RefurbishmentStore.create(tmp_path/'registry.sqlite') as store:
        commands=example_commands(); first=store.execute(commands[0]); store.execute(commands[1])
        repeated=store.execute(commands[0])
        assert repeated=={**first,'replayed':True}
        assert repeated['asset']['revision']==1 and store.get_asset(first['asset_id'])['revision']==2
        changed=deepcopy(commands[0]); changed['actor']='Different actor'
        with pytest.raises(CommandConflict): store.execute(changed)
        other=example_commands('OTHER')[0]; other['command_id']=commands[0]['command_id']
        with pytest.raises(CommandConflict): store.execute(other)
        assert len(store.export_history())==2


def test_revision_and_transition_failure_roll_back_everything(tmp_path):
    with RefurbishmentStore.create(tmp_path/'registry.sqlite') as store:
        commands=example_commands(); store.execute(commands[0]); before=store.export_bundle()
        with pytest.raises(RevisionConflict): store.execute(commands[3])
        bad=deepcopy(commands[3]); bad['expected_revision']=1
        with pytest.raises(ValueError,match='active work order'): store.execute(bad)
        assert store.export_bundle()==before


def test_primary_serial_and_intake_uniqueness_with_atomic_rollback(tmp_path):
    with RefurbishmentStore.create(tmp_path/'registry.sqlite') as store:
        first=example_commands('A')[0]; first['payload']['configuration']['serial_number']='KNOWN-1'
        store.execute(first); before=store.export_bundle()
        second=example_commands('B')[0]; second['payload']['configuration']['serial_number']='known-1'
        with pytest.raises(ValueError,match='serial'): store.execute(second)
        assert store.export_bundle()==before
        second['payload']['configuration']['serial_number']=None
        second['payload']['intake_id']=first['payload']['intake_id']
        with pytest.raises(ValueError,match='Intake'): store.execute(second)
        assert store.export_bundle()==before
        second['payload']['intake_id']='second'; store.execute(second)
        for name in ('C','D'):
            command=example_commands(name)[0]; command['payload']['configuration']['serial_number']='REDACTED'
            store.execute(command)
        assert len(store.list_assets())==4


@pytest.mark.parametrize('tamper', ['cache','event','guard','version','serial'])
def test_database_tampering_refused(tmp_path,tamper):
    path=tmp_path/'registry.sqlite'
    with RefurbishmentStore.create(path) as store:
        command=example_commands()[0]; command['payload']['configuration']['serial_number']='KNOWN'
        store.execute(command)
        with sqlite3.connect(path) as connection:
            if tamper=='cache': connection.execute("UPDATE assets SET state_json='{}'")
            elif tamper=='event':
                connection.execute('DROP TRIGGER events_no_update')
                connection.execute("UPDATE events SET recorded_at='2020-01-01T00:00:00+00:00'")
                connection.execute(SCHEMA['events_no_update'])
            elif tamper=='guard': connection.execute('DROP TRIGGER events_no_delete')
            elif tamper=='version': connection.execute('PRAGMA user_version=2')
            else:
                connection.execute('DROP TRIGGER serials_no_delete')
                connection.execute('DELETE FROM serial_claims')
                connection.execute(SCHEMA['serials_no_delete'])
        with pytest.raises(IntegrityError): store.export_bundle()
        with pytest.raises(IntegrityError): store.get_asset(command['asset_id'])


def test_append_only_sql_guards_prevent_mutations(tmp_path):
    path=tmp_path/'registry.sqlite'
    with RefurbishmentStore.create(path) as store:
        store.execute(example_commands()[0]); before=store.export_bundle()
        with sqlite3.connect(path) as connection:
            for sql in ('DELETE FROM events','UPDATE events SET revision=2','DELETE FROM metadata'):
                with pytest.raises(sqlite3.IntegrityError): connection.execute(sql)
        assert store.export_bundle()==before


def test_export_global_order_and_full_command_projection_validation(tmp_path):
    with RefurbishmentStore.create(tmp_path/'registry.sqlite') as store:
        a,b=example_commands('A'),example_commands('B')
        for command in (b[0],a[0],b[1],a[1]): store.execute(command)
        bundle=store.export_bundle()
        assert [e['position'] for e in bundle['events']]==[1,2,3,4]
        assert [a['asset_id'] for a in bundle['assets']]==['A','B']
        checked=validate_export_bundle(bundle); checked['assets'][0]['location']='changed'
        assert checked!=bundle
        for mutation in ('hash','snapshot','position','registry','command'):
            bad=deepcopy(bundle)
            if mutation=='hash': bad['events'][0]['event_ref']='sha256:'+'0'*64
            elif mutation=='snapshot': bad['assets'][0]['location']='changed'
            elif mutation=='position': bad['events'].reverse()
            elif mutation=='registry': bad['registry_id']='registry:'+'0'*32
            else: bad['events'][0]['command']['actor']='changed'
            with pytest.raises(IntegrityError): validate_export_bundle(bad)


@pytest.mark.parametrize('same_command',[False,True])
def test_independent_processes_atomically_contend_for_revision(tmp_path,same_command):
    path=tmp_path/'registry.sqlite'
    with RefurbishmentStore.create(path) as store: store.execute(example_commands()[0])
    start=tmp_path/'start'
    script='''import json,sys,time
from pathlib import Path
from ciw.refurbishment_store import RefurbishmentStore, RevisionConflict
while not Path(sys.argv[2]).exists(): time.sleep(.01)
command=json.loads(sys.argv[3])
try:
 with RefurbishmentStore(sys.argv[1]) as store:
  receipt=store.execute(command)
  print(json.dumps({'event_id':receipt['event_id'],'replayed':receipt['replayed']}))
except RevisionConflict: print(json.dumps({'conflict':True}))
'''
    environment=dict(os.environ); environment['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
    processes=[]
    for number in range(2):
        command={'schema':'ciw.refurbishment-command.v1','command_id':'race' if same_command else 'race-'+str(number),
                 'asset_id':'NS-DEMO-0001','expected_revision':1,'actor':'Process test','action':'relocate',
                 'payload':{'location':'Shelf B','notes':''}}
        processes.append(subprocess.Popen([sys.executable,'-c',script,str(path),str(start),json.dumps(command)],env=environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
    start.write_text('start')
    results=[]
    try:
        for process in processes:
            stdout,stderr=process.communicate(timeout=45)
            assert process.returncode==0,stderr
            results.append(json.loads(stdout))
    finally:
        for process in processes:
            if process.poll() is None: process.kill(); process.wait(timeout=10)
    with RefurbishmentStore(path) as store:
        assert store.get_asset('NS-DEMO-0001')['revision']==2
        assert len(store.export_history())==2
    if same_command:
        assert results[0]['event_id']==results[1]['event_id']
        assert {r['replayed'] for r in results}=={False,True}
    else: assert sum(r.get('conflict',False) for r in results)==1


def test_creation_makes_parent_and_missing_existing_file_is_never_recreated(tmp_path):
    path=tmp_path/'operations'/'assets.sqlite3'
    with RefurbishmentStore.create(path) as store:
        assert path.exists()
        path.unlink()
        with pytest.raises(IntegrityError): store.list_assets()
        assert not path.exists()


def test_configuration_serial_collision_rolls_back_new_configuration(tmp_path):
    with RefurbishmentStore.create(tmp_path/'registry.sqlite') as store:
        first=example_commands('A')[0]; first['payload']['configuration']['serial_number']='RESERVED'
        store.execute(first)
        second=example_commands('B')[0]; store.execute(second)
        configuration=deepcopy(second['payload']['configuration']); configuration.update(serial_number='RESERVED',revision='2')
        change={'schema':'ciw.refurbishment-command.v1','command_id':'config-change','asset_id':'B','expected_revision':1,
                'actor':'Operator','action':'replace_configuration','payload':{'configuration':configuration,'reason':'Correction'}}
        before=store.export_bundle()
        with pytest.raises(ValueError,match='serial'): store.execute(change)
        assert store.export_bundle()==before


def test_export_uses_single_snapshot_while_concurrent_writer_waits(tmp_path,monkeypatch):
    from threading import Event, Thread, current_thread
    from ciw import refurbishment_contract
    path=tmp_path/'registry.sqlite'
    with RefurbishmentStore.create(path) as store:
        for name in ('A','B'): store.execute(example_commands(name)[0])
        command={'schema':'ciw.refurbishment-command.v1','command_id':'move-A','asset_id':'A','expected_revision':1,
                 'actor':'Writer','action':'relocate','payload':{'location':'Concurrent new location','notes':''}}
        ready=Event(); failures=[]
        real_apply=refurbishment_contract.apply_command
        def apply(previous,value):
            result=real_apply(previous,value)
            if current_thread().name=='registry-writer': ready.set()
            return result
        monkeypatch.setattr(refurbishment_contract,'apply_command',apply)
        def write():
            try:
                with RefurbishmentStore(path) as other: other.execute(command)
            except BaseException as exc: failures.append(exc); ready.set()
        writer=Thread(target=write,name='registry-writer')
        real_read=store._read_asset
        launched=False
        def read(connection,asset_id,**kwargs):
            nonlocal launched
            result=real_read(connection,asset_id,**kwargs)
            if not launched:
                launched=True; writer.start()
                assert ready.wait(10)
            return result
        monkeypatch.setattr(store,'_read_asset',read)
        try: bundle=store.export_bundle()
        finally: writer.join(15)
        assert not writer.is_alive() and not failures
        assert len(bundle['events'])==2 and all(asset['revision']==1 for asset in bundle['assets'])
        assert store.get_asset('A')['location']=='Concurrent new location'
        assert store.get_asset('A')['revision']==2
