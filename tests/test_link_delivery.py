"""Adversarial local custody and occurrence checks, never real instrument qualification."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ciw import link_contract as c, link_delivery as d
from ciw.link_profiles import example_manifest
from ciw.link_runtime import DeviceRuntime, Authority
from ciw.link_store import Journal

NOW = 1_000_000_000
FRAME_KEY = b'x'*32  # Explicit synthetic fixture, no customer key.


@pytest.fixture
def rig(tmp_path):
    device = example_manifest('GaugeLink')
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    with Journal(tmp_path/'source.sqlite',create=True) as source, Journal(tmp_path/'receiver.sqlite',create=True) as receiver:
        rt=DeviceRuntime(device,source,boot_id='synthetic.boot',key=FRAME_KEY)
        rt.emit('CH0',b'24.982 mm\n',tick=100)
        binding=d.DeliveryBinding('tenant.fixture','node.fixture',device['device_id'],c.content_identity(device),
            source.store_id,receiver.store_id,'epoch.1',public,NOW-100,NOW+120_000_000_000)
        yield source,receiver,device,binding,private


def observation(source):
    return next(row for row in source.rows() if row['message_key'].startswith('observation:'))


def signed_one(rig,*,time=NOW):
    source,receiver,device,binding,private=rig
    for item in source.rows():
        receiver.append(item['wire'],expected_manifest=device,key=FRAME_KEY,require_auth=True)
    row=observation(source)
    req=d.request_delivery(source,row['message_key'],device=device,binding=binding,now_ns=time,key=FRAME_KEY)
    ack=d.issue_receipt(receiver,req,row['wire'],device=device,binding=binding,signing_key=private,now_ns=time,key=FRAME_KEY)
    return row,req,ack


def test_signed_retry_exact_bytes_distinct_reviews_and_no_authority(rig):
    source,receiver,device,binding,private=rig
    before=source.rows()
    first=d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY)
    signatures=source.db.execute('SELECT receipt FROM acks ORDER BY message_key').fetchall()
    second=d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW+1,key=FRAME_KEY)
    assert (first['new_acknowledgements'],second['existing_acknowledgements'])==(2,2)
    assert source.db.execute('SELECT receipt FROM acks ORDER BY message_key').fetchall()==signatures
    assert [r['wire'] for r in receiver.rows()]==[r['wire'] for r in before]
    assert source.rows()==before
    assert {r['verification_id'] for r in first['receipts']}.isdisjoint(r['verification_id'] for r in second['receipts'])
    assert all(r['scope']==d.SCOPE and r['active_authority'] is False for r in second['receipts'])
    for raw, in signatures:
        assert FRAME_KEY.hex().encode() not in raw
        assert b'PRIVATE KEY' not in raw


@pytest.mark.parametrize('kind',['observation','event'])
@pytest.mark.parametrize('same',[True,False])
def test_stale_producer_never_claims_new_occurrence(tmp_path,kind,same):
    device=example_manifest('GaugeLink')
    with Journal(tmp_path/'j.sqlite',create=True) as j:
        first=DeviceRuntime(device,j,boot_id='boot')
        stale=DeviceRuntime(device,j,boot_id='boot')
        if kind=='observation':
            first.emit('CH0',b'12.000 mm\n',tick=1)
            with pytest.raises(ValueError,match='Producer occurrence'):
                stale.emit('CH0',b'12.000 mm\n' if same else b'13.000 mm\n',tick=1)
            assert stale.sequence==0
            reopened=DeviceRuntime(device,j,boot_id='boot')
            assert reopened.emit('CH0',b'12.000 mm\n',tick=2)['record']['sequence']==1
        else:
            first.event('recording_started','first',tick=1)
            with pytest.raises(ValueError,match='Producer occurrence'):
                stale.event('recording_started','first' if same else 'second',tick=1)
            assert stale.event_sequence==0
            reopened=DeviceRuntime(device,j,boot_id='boot')
            assert reopened.event_sequence==1
        row=j.rows()[-1]
        assert j.append(row['wire'],expected_manifest=device)['wire_ref']==row['wire_ref']


@pytest.mark.parametrize('new_key',[None,b'y'*32])
def test_old_ack_does_not_skip_stricter_authentication(tmp_path,new_key):
    device=example_manifest('GaugeLink')
    with Journal(tmp_path/'a',create=True) as a, Journal(tmp_path/'b',create=True) as b:
        DeviceRuntime(device,a,boot_id='boot').emit('CH0',b'24.982 mm\n')
        a.sync_to(b,manifests=[device])
        old=a.db.execute('SELECT * FROM acks').fetchall()
        with pytest.raises(ValueError):a.sync_to(b,manifests=[device],key=new_key,require_auth=True)
        assert a.db.execute('SELECT * FROM acks').fetchall()==old


def test_cached_authenticated_ack_rechecks_changed_key(rig):
    source,receiver,device,_,_=rig
    source.sync_to(receiver,manifests=[device],key=FRAME_KEY,require_auth=True)
    with pytest.raises(ValueError):source.sync_to(receiver,manifests=[device],key=b'y'*32,require_auth=True)


@pytest.mark.parametrize('side',['source','receiver'])
@pytest.mark.parametrize('field,value',[('manifest_ref','sha256:'+'0'*64),('message_key','observation:wrong')])
def test_transport_metadata_cannot_replace_wire_binding(rig,side,field,value):
    source,receiver,device,binding,private=rig
    source.sync_to(receiver,manifests=[device],key=FRAME_KEY,require_auth=True)
    store=source if side=='source' else receiver
    # This is a deliberate untrusted database corruption test; never migration.
    if field=='message_key':store.db.execute('PRAGMA foreign_keys=OFF')
    key=observation(store)['message_key']
    store.db.execute(f'UPDATE messages SET {field}=? WHERE message_key=?',(value,key))
    with pytest.raises(ValueError):source.sync_to(receiver,manifests=[device],key=FRAME_KEY,require_auth=True)


@pytest.mark.parametrize('change',[
    {'tenant_id':'tenant.other'},{'node_id':'node.other'},{'epoch':'epoch.other'},
    {'source_id':'source.other'},{'receiver_id':'receiver.other'},
    {'device_id':'device.other'},{'manifest_ref':'sha256:'+'0'*64},
    {'valid_from_ns':NOW+1},{'valid_until_ns':NOW}, {'require_frame_auth':False}])
def test_changed_host_binding_does_not_inherit_receipt(rig,change):
    source,receiver,device,binding,private=rig
    _,_,ack=signed_one(rig)
    altered=replace(binding,**change)
    with pytest.raises((ValueError,PermissionError)):
        d.accept_receipt(source,ack,device=device,binding=altered,now_ns=NOW,key=FRAME_KEY)
    assert source.db.execute('SELECT count(*) FROM acks').fetchone()[0]==0


@pytest.mark.parametrize('now',[NOW-1,NOW+d.REQUEST_LIFETIME_NS,NOW+130_000_000_000])
def test_expired_or_preissue_clock_keeps_history_but_no_new_ack(rig,now):
    source,receiver,device,binding,private=rig
    _,_,ack=signed_one(rig)
    assert d.inspect_receipt(ack,expected_ref=c.content_identity(ack),public_key=binding.public_key)['active_authority'] is False
    with pytest.raises(PermissionError):d.accept_receipt(source,ack,device=device,binding=binding,now_ns=now,key=FRAME_KEY)
    assert source.db.execute('SELECT count(*) FROM acks').fetchone()[0]==0


def test_wrong_signing_key_never_appends_receiver(rig):
    source,receiver,device,binding,private=rig
    row=source.rows()[0]
    req=d.request_delivery(source,row['message_key'],device=device,binding=binding,now_ns=NOW,key=FRAME_KEY)
    with pytest.raises(PermissionError):
        d.issue_receipt(receiver,req,row['wire'],device=device,binding=binding,signing_key=Ed25519PrivateKey.generate(),now_ns=NOW,key=FRAME_KEY)
    assert receiver.rows()==[]


@pytest.mark.parametrize('field,value',[('wire_ref','sha256:'+'0'*64),('wire_bytes',1),('kind','event'),
    ('boot_id','other.boot'),('sequence',99),('nonce','0'*64),('message_key','observation:wrong')])
def test_changed_request_even_rehashed_cannot_ack_pending_original(rig,field,value):
    source,receiver,device,binding,private=rig
    row,req,ack=signed_one(rig)
    altered=deepcopy(req);altered[field]=value
    altered['request_ref']=c.content_identity({k:v for k,v in altered.items() if k!='request_ref'})
    if field=='nonce':
        other=d.issue_receipt(receiver,altered,row['wire'],device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY)
        with pytest.raises(ValueError,match='challenge'):
            d.accept_receipt(source,other,device=device,binding=binding,now_ns=NOW,key=FRAME_KEY)
    else:
        with pytest.raises(ValueError):
            d.issue_receipt(receiver,altered,row['wire'],device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY)


@pytest.mark.parametrize('mutation',['scope','time','execution','request','signature','extra','algorithm','other-key'])
def test_tampered_receipt_and_resealed_outer_anchor_still_refuse(rig,mutation):
    source,receiver,device,binding,private=rig
    _,_,ack=signed_one(rig);bad=deepcopy(ack)
    if mutation=='scope':bad['payload']['scope']['machine_authority']=True
    elif mutation=='time':bad['payload']['acknowledged_ns']+=1
    elif mutation=='execution':
        from ciw.core.identities import new_identity
        bad['execution_id']=new_identity('execution')
    elif mutation=='request':bad['payload']['request']['binding']['tenant_id']='other'
    elif mutation=='signature':bad['signature']['signature_b64']='A'*88
    elif mutation=='extra':bad['extra']='untrusted'
    elif mutation=='algorithm':bad['signature']['algorithm']='HS256'
    else:
        from ciw.legibility import sign_bundle
        bad['signature']=sign_bundle(d._bundle(bad['payload'],bad['execution_id']),Ed25519PrivateKey.generate())
    with pytest.raises(ValueError):d.inspect_receipt(bad,expected_ref=c.content_identity(bad),public_key=binding.public_key)


def test_wrong_external_anchor_and_key_refuse(rig):
    *_,binding,private=rig
    _,_,ack=signed_one(rig)
    with pytest.raises(ValueError):d.inspect_receipt(ack,expected_ref='sha256:'+'0'*64,public_key=binding.public_key)
    with pytest.raises(ValueError):d.inspect_receipt(ack,expected_ref=c.content_identity(ack),public_key=b'0'*32)


@pytest.mark.parametrize('stage',['raw','signed'])
def test_interruption_before_source_ack_retries_original_receipt(rig,stage):
    source,receiver,device,binding,private=rig
    row=source.rows()[0]
    req=d.request_delivery(source,row['message_key'],device=device,binding=binding,now_ns=NOW,key=FRAME_KEY)
    def fail(_):raise RuntimeError('crash fixture')
    with pytest.raises(RuntimeError):
        if stage=='raw':
            d.issue_receipt(receiver,req,row['wire'],device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY,after_commit=fail)
        else:d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY,after_receive=fail)
    assert source.db.execute('SELECT count(*) FROM acks').fetchone()[0]==0
    assert len(receiver.rows())==1
    result=d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW+1,key=FRAME_KEY)
    assert result['new_acknowledgements']==2 and len(receiver.rows())==2


@pytest.mark.parametrize('from_mode,to_mode',[('legacy','signed'),('signed','legacy')])
def test_receipt_modes_cannot_silently_upgrade_or_downgrade(rig,from_mode,to_mode):
    source,receiver,device,binding,private=rig
    def call(mode):
        if mode=='legacy':return source.sync_to(receiver,manifests=[device],key=FRAME_KEY,require_auth=True)
        return d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY)
    call(from_mode);before=source.db.execute('SELECT * FROM acks').fetchall()
    with pytest.raises(ValueError):call(to_mode)
    assert source.db.execute('SELECT * FROM acks').fetchall()==before


def test_unsigned_source_not_accepted_by_default_signed_delivery(tmp_path):
    device=example_manifest('GaugeLink');private=Ed25519PrivateKey.generate()
    pub=private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    with Journal(tmp_path/'a',create=True) as a,Journal(tmp_path/'b',create=True) as b:
        DeviceRuntime(device,a).emit('CH0',b'24.982 mm\n')
        bind=d.DeliveryBinding('t','n',device['device_id'],c.content_identity(device),a.store_id,b.store_id,'e',pub,NOW-1,NOW+100)
        with pytest.raises(ValueError):d.sync_signed(a,b,device=device,binding=bind,signing_key=private,now_ns=NOW)
        assert b.rows()==[] and a.db.execute('SELECT count(*) FROM acks').fetchone()[0]==0
        bind=replace(bind,require_frame_auth=False)
        result=d.sync_signed(a,b,device=device,binding=bind,signing_key=private,now_ns=NOW)
        assert result['scope']['device_authentication']=='not_established'


@pytest.mark.parametrize('tick',[NOW-1,NOW+120_000_000_000])
def test_live_clock_rechecked_after_node_reply(rig,tick):
    source,receiver,device,binding,private=rig
    ticks=iter([NOW,NOW,tick])
    with pytest.raises(PermissionError):
        d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY,clock=lambda:next(ticks))
    assert len(receiver.rows())==1
    assert source.db.execute('SELECT count(*) FROM acks').fetchone()[0]==0


def test_offline_receipt_check_never_opens_journal(rig,monkeypatch):
    _,_,device,binding,_=rig;_,_,ack=signed_one(rig)
    monkeypatch.setattr(Journal,'__init__',lambda *a,**k:pytest.fail('journal opened by inspector'))
    monkeypatch.setattr(d,'sign_bundle',lambda *a,**k:pytest.fail('inspector signed'))
    result=d.inspect_receipt(ack,expected_ref=c.content_identity(ack),public_key=binding.public_key)
    assert result['acknowledgement_applied'] is False and result['active_authority'] is False


@pytest.mark.skipif(sys.platform!='linux',reason='Linux receive-only TTY boundary')
def test_read_authority_expires_during_readiness_wait(tmp_path,monkeypatch):
    import pty
    from ciw import link_serial as serial
    device=example_manifest('GaugeLink');device['channels'][0]['encoding']='opaque.v1'
    device['clock'].update(id='link.host.monotonic_ns',tick_hz=1_000_000_000)
    master,slave=pty.openpty()
    now=[NOW]
    monkeypatch.setattr(serial.time,'time_ns',lambda:now[0])
    def ready(reads,*args):
        now[0]=NOW+100
        return reads,[],[]
    monkeypatch.setattr(serial.select,'select',ready)
    monkeypatch.setattr(serial.os,'read',lambda *a:pytest.fail('instrument read after grant expiry'))
    try:
        with Journal(tmp_path/'j',create=True) as journal:
            with pytest.raises(PermissionError):serial.capture_serial(os.ttyname(slave),device=device,journal=journal,
                authority=Authority('operator',device['device_id'],frozenset({'READ'}),NOW+100),
                channel_id='CH0',allow_pty=True)
            assert not any(r['message_key'].startswith('observation:') for r in journal.rows())
    finally:os.close(master);os.close(slave)


def test_demo_signed_custody_does_not_promote_calibration_or_time(tmp_path):
    from ciw.link_delivery_cli import demo,main
    out=tmp_path/'demo';summary=demo(out)
    assert summary['delivery']['new_acknowledgements']==4
    assert summary['retry']['existing_acknowledgements']==4
    study=summary['study'];model=study['results']['link.mean-model.v1']['data']
    assert model['mean']==2.0 and model['inspection_qualification']=='HELD'
    assert model['variance'] is None and model['standard_uncertainty'] is None
    rows=study['results']['link.decode-scalar.v1']['data']['rows']
    assert all(r['acquired_at'] is None for r in rows)
    assert study['evidence_id']==summary['replay']['evidence_id']
    assert {r['execution_id'] for r in study['results'].values()}.isdisjoint(
        r['execution_id'] for r in summary['replay']['results'].values())
    assert main(['inspect',str(out/'signed-receipt.json'),'--receipt-ref',summary['receipt_ref'],
                 '--node-public-key',str(out/'node-public-key.raw')])==0
    assert main(['demo','--output-dir',str(out)])==2
    assert summary['private_keys_retained'] is False


@pytest.mark.parametrize('phase',['raw-commit','signed-commit','source-ack'])
def test_real_process_termination_and_reopen_recover_committed_delivery(rig,phase):
    from cryptography.hazmat.primitives.serialization import PrivateFormat,NoEncryption
    source,receiver,device,binding,private=rig
    # Ephemeral SYNTHETIC signing material goes only through child stdin, never
    # into the journal, persistent test artifacts, argv or a production store.
    data={'source':str(source.path),'receiver':str(receiver.path),'device':device,
          'binding':{k:getattr(binding,k) for k in binding.__dataclass_fields__ if k!='public_key'},
          'public':binding.public_key.hex(), 'private':private.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()).hex(),
          'phase':phase,'now':NOW}
    code='''import json,sys,os
from ciw import link_delivery as d
from ciw.link_store import Journal
x=json.loads(sys.stdin.read());bind=d.DeliveryBinding(**x['binding'],public_key=bytes.fromhex(x['public']))
with Journal(x['source']) as a,Journal(x['receiver']) as b:
    if x['phase']=='raw-commit':
        row=a.rows()[0]
        request=d.request_delivery(a,row['message_key'],device=x['device'],binding=bind,now_ns=x['now'],key=b'x'*32)
        d.issue_receipt(b,request,row['wire'],device=x['device'],binding=bind,signing_key=bytes.fromhex(x['private']),
            now_ns=x['now'],key=b'x'*32,after_commit=lambda _:os._exit(73))
    d.sync_signed(a,b,device=x['device'],binding=bind,signing_key=bytes.fromhex(x['private']),now_ns=x['now'],key=b'x'*32,
        after_receive=(lambda _:os._exit(73)) if x['phase']=='signed-commit' else None)
    os._exit(73)
'''
    proc=subprocess.run([sys.executable,'-c',code],input=json.dumps(data),text=True,capture_output=True,timeout=20)
    assert proc.returncode==73,proc.stderr
    with Journal(source.path) as a, Journal(receiver.path) as b:
        before=a.db.execute('SELECT receipt FROM acks ORDER BY message_key').fetchall()
        assert len(before)==(2 if phase=='source-ack' else 0)
        assert len(b.rows())==(2 if phase=='source-ack' else 1)
        result=d.sync_signed(a,b,device=device,binding=binding,signing_key=private,now_ns=NOW+1,key=FRAME_KEY)
        assert result['existing_acknowledgements']==(2 if phase=='source-ack' else 0)
        assert len(a.rows())==len(b.rows())==2
        repeated=d.sync_signed(a,b,device=device,binding=binding,signing_key=private,now_ns=NOW+2,key=FRAME_KEY)
        assert repeated['new_acknowledgements']==0
        assert DeviceRuntime(device,a,boot_id='synthetic.boot',key=FRAME_KEY).sequence==1


def test_actual_competing_producer_processes_cannot_collapse_two_occurrences(tmp_path):
    device=example_manifest('GaugeLink');path=tmp_path/'j'
    with Journal(path,create=True) as j:DeviceRuntime(device,j,boot_id='same.boot')
    code='''import sys,json
from ciw.link_store import Journal
from ciw.link_runtime import DeviceRuntime
from ciw.link_profiles import example_manifest
with Journal(sys.argv[1]) as j:
    runtime=DeviceRuntime(example_manifest('GaugeLink'),j,boot_id='same.boot')
    print('READY',flush=True)
    if sys.stdin.readline().strip()!='GO':raise RuntimeError('missing test barrier')
    try:
        result=runtime.emit('CH0',b'24.982 mm\\n',tick=100)
        print(json.dumps({'status':'NEW','sequence':result['record']['sequence']}),flush=True)
    except ValueError as exc:
        print(json.dumps({'status':'REFUSED','reason':str(exc)}),flush=True)
'''
    workers=[subprocess.Popen([sys.executable,'-c',code,str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
    try:
        # Both runtimes have loaded the same sequence before either commit.
        for worker in workers:assert worker.stdout.readline().strip()=='READY'
        for worker in workers:worker.stdin.write('GO\n');worker.stdin.flush()
        outcomes=[]
        for worker in workers:
            out,err=worker.communicate(timeout=15)
            assert worker.returncode==0,err
            outcomes.append(json.loads(out))
        assert sorted(x['status'] for x in outcomes)==['NEW','REFUSED']
        assert 'Producer occurrence' in next(x['reason'] for x in outcomes if x['status']=='REFUSED')
    finally:
        for worker in workers:
            if worker.poll() is None:worker.kill();worker.wait(timeout=5)
    with Journal(path) as j:
        assert len(j.rows())==2
        next_result=DeviceRuntime(device,j,boot_id='same.boot').emit('CH0',b'24.982 mm\n',tick=200)
        assert next_result['record']['sequence']==1


@pytest.mark.parametrize('field,value',[('public_key',b'x'),('valid_from_ns',True),('valid_until_ns',NOW-101),
    ('valid_until_ns',2**63),('require_frame_auth',1),('manifest_ref','not-a-hash'),
    ('node_id',''),('tenant_id',''),('epoch',''),('source_id',42)])
def test_binding_rejects_malformed_claims(rig,field,value):
    binding=rig[3]
    with pytest.raises((ValueError,TypeError)):replace(binding,**{field:value})


@pytest.mark.parametrize('field,value',[('schema','wrong'),('created_ns',True),('expires_ns',2**63),
    ('nonce','x'*64),('sequence',True),('wire_bytes',0),('kind','control'),('extra','ignored?')])
def test_request_is_closed_bounded_and_typed(rig,field,value):
    source,receiver,device,binding,private=rig
    _,req,_=signed_one(rig);bad=deepcopy(req);bad[field]=value
    bad['request_ref']=c.content_identity({k:v for k,v in bad.items() if k!='request_ref'})
    with pytest.raises((ValueError,TypeError)):d._request(bad)


def test_capacity_refusal_never_issues_receipt_for_uncommitted_observation(rig):
    source,receiver,device,binding,private=rig
    receiver.capacity_messages=1
    with pytest.raises(ValueError,match='buffer full'):
        d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY)
    assert len(receiver.rows())==1
    assert source.db.execute('SELECT count(*) FROM acks').fetchone()[0]==1
    assert not receiver.db.execute("SELECT 1 FROM messages WHERE message_key LIKE 'observation:%'").fetchall()
    receiver.capacity_messages=2
    result=d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW+1,key=FRAME_KEY)
    assert result['new_acknowledgements']==result['existing_acknowledgements']==1


def test_expired_challenge_requires_explicit_new_epoch_not_silent_remint(rig):
    source,receiver,device,binding,private=rig;row=source.rows()[0]
    request=d.request_delivery(source,row['message_key'],device=device,binding=binding,now_ns=NOW,key=FRAME_KEY)
    later=NOW+d.REQUEST_LIFETIME_NS
    with pytest.raises(PermissionError):
        d.request_delivery(source,row['message_key'],device=device,binding=binding,now_ns=later,key=FRAME_KEY)
    newer=replace(binding,epoch='reviewed.new.epoch',valid_from_ns=later,valid_until_ns=later+100)
    replacement=d.request_delivery(source,row['message_key'],device=device,binding=newer,now_ns=later,key=FRAME_KEY)
    assert request['nonce']!=replacement['nonce'] and request['wire_ref']==replacement['wire_ref']
    assert source.state(d._pending_namespace(binding,row['message_key']))['value']==request


def test_stored_node_receipt_tamper_cannot_authorize_retry(rig):
    source,receiver,device,binding,private=rig
    d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY)
    key,value=receiver.db.execute("SELECT namespace,value FROM state WHERE namespace LIKE 'delivery-signed:%' LIMIT 1").fetchone()
    bad=c.loads(value);bad['payload']['acknowledged_ns']+=1
    receiver.db.execute('UPDATE state SET value=? WHERE namespace=?',(c.bounded(bad),key))
    with pytest.raises(ValueError):d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW+2,key=FRAME_KEY)


def test_signed_retry_cannot_skip_current_frame_key(rig):
    source,receiver,device,binding,private=rig
    d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY)
    before=source.db.execute('SELECT * FROM acks').fetchall()
    with pytest.raises(ValueError):d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW+1,key=b'z'*32)
    assert source.db.execute('SELECT * FROM acks').fetchall()==before


def test_store_schema_unchanged_for_preexisting_journals(rig):
    from ciw.link_store import _SCHEMA
    for store in rig[:2]:
        assert store.db.execute('PRAGMA user_version').fetchone()[0]==1
        assert dict(store.db.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))==_SCHEMA


def test_explicit_cli_sync_and_inspection_with_separately_anchored_files(rig,tmp_path,monkeypatch):
    from ciw import link_delivery_cli as cli
    from cryptography.hazmat.primitives.serialization import PrivateFormat,NoEncryption
    from hashlib import sha256
    source,receiver,device,binding,private=rig
    monkeypatch.setattr(cli.time,'time_ns',lambda:NOW)
    # Files below contain only fresh SYNTHETIC test credentials, not production keys.
    (tmp_path/'manifest.json').write_bytes(c.bounded(device))
    raw={k:getattr(binding,k) for k in binding.__dataclass_fields__ if k!='public_key'}
    (tmp_path/'binding.json').write_bytes(c.bounded(raw))
    (tmp_path/'public.raw').write_bytes(binding.public_key)
    (tmp_path/'private.raw').write_bytes(private.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()))
    (tmp_path/'frame.raw').write_bytes(FRAME_KEY)
    args=['sync','--source',str(source.path),'--receiver',str(receiver.path),
          '--manifest',str(tmp_path/'manifest.json'),'--binding',str(tmp_path/'binding.json'),
          '--manifest-sha256',sha256((tmp_path/'manifest.json').read_bytes()).hexdigest(),
          '--binding-sha256',sha256((tmp_path/'binding.json').read_bytes()).hexdigest(),
          '--node-key',str(tmp_path/'private.raw'),'--node-public-key',str(tmp_path/'public.raw'),
          '--frame-key',str(tmp_path/'frame.raw'),'--output',str(tmp_path/'summary.json')]
    bad=list(args);bad[bad.index('--binding-sha256')+1]='0'*64
    assert cli.main(bad)==2 and receiver.rows()==[]
    assert cli.main(args)==0
    report=json.loads((tmp_path/'summary.json').read_bytes())
    assert report['new_acknowledgements']==2
    assert cli.main(args)==2  # No overwrite or second transfer hidden by an output collision.
    raw_ack=source.db.execute('SELECT receipt FROM acks LIMIT 1').fetchone()[0]
    (tmp_path/'receipt.json').write_bytes(raw_ack)
    assert cli.main(['inspect',str(tmp_path/'receipt.json'),'--receipt-ref',c.content_identity(c.loads(raw_ack)),
        '--node-public-key',str(tmp_path/'public.raw')])==0


@pytest.mark.parametrize('new_field,new_value',[('device_id','wrong'),('sequence',8),('manifest_ref','sha256:'+'0'*64)])
def test_exact_observation_bytes_cannot_be_rebound_under_request(rig,new_field,new_value):
    from ciw.link_store import decode_bound
    from ciw.link_protocol import encode
    source,receiver,device,binding,private=rig
    row,request,_=signed_one(rig)
    frame,value,_,_=decode_bound(row['wire'],device,key=FRAME_KEY)
    value[new_field]=new_value
    wire=encode('observation',c.bounded(value),key=FRAME_KEY)
    with pytest.raises(ValueError):d.issue_receipt(receiver,request,wire,device=device,binding=binding,
        signing_key=private,now_ns=NOW,key=FRAME_KEY)


def test_recorded_device_events_keep_independent_identity_under_delivery(rig):
    source,receiver,device,binding,private=rig
    rt=DeviceRuntime(device,source,boot_id='synthetic.boot',key=FRAME_KEY)
    rt.event('disconnect','synthetic link interrupted',tick=123)
    result=d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY)
    assert result['new_acknowledgements']==3
    key=next(r['message_key'] for r in source.rows() if r['message_key'].startswith('event:'))
    signed=c.loads(source.db.execute('SELECT receipt FROM acks WHERE message_key=?',(key,)).fetchone()[0])
    assert signed['payload']['request']['kind']=='event'
    assert signed['payload']['request']['sequence']==0
    assert signed['payload']['request']['boot_id']=='synthetic.boot'


def test_old_ack_stays_historical_while_new_observations_sync_after_challenge_expiry(rig):
    source,receiver,device,binding,private=rig
    d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY)
    original=source.db.execute('SELECT * FROM acks ORDER BY message_key').fetchall()
    DeviceRuntime(device,source,boot_id='synthetic.boot',key=FRAME_KEY).emit('CH0',b'24.983 mm\n',tick=200)
    result=d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,
        now_ns=NOW+d.REQUEST_LIFETIME_NS+1,key=FRAME_KEY)
    assert result['new_acknowledgements']==1 and result['existing_acknowledgements']==2
    assert len(receiver.rows())==3
    assert all(x['acknowledgement_applied'] is False for x in result['receipts'] if x['duplicate'])
    for old in original:
        assert source.db.execute('SELECT * FROM acks WHERE message_key=?',(old[0],)).fetchone()==old


def test_same_key_explicit_trust_renewal_preserves_ack_history_without_renewing_receipt(rig):
    source,receiver,device,binding,private=rig
    d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY)
    old=source.db.execute('SELECT * FROM acks ORDER BY message_key').fetchall()
    later=binding.valid_until_ns
    with pytest.raises(PermissionError):d.sync_signed(source,receiver,device=device,binding=binding,
        signing_key=private,now_ns=later,key=FRAME_KEY)
    current=replace(binding,epoch='explicitly-renewed',valid_from_ns=later,valid_until_ns=later+100)
    result=d.sync_signed(source,receiver,device=device,binding=current,signing_key=private,now_ns=later,key=FRAME_KEY)
    assert result['new_acknowledgements']==0 and result['existing_acknowledgements']==2
    assert all(x['status']=='HISTORICAL_SIGNED_CUSTODY_STILL_RETAINED' for x in result['receipts'])
    assert source.db.execute('SELECT * FROM acks ORDER BY message_key').fetchall()==old


def test_historical_ack_does_not_hide_recipient_data_loss(rig):
    source,receiver,device,binding,private=rig
    d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY)
    receiver.db.execute("DELETE FROM messages WHERE message_key LIKE 'observation:%'")
    with pytest.raises(ValueError):d.sync_signed(source,receiver,device=device,binding=binding,
        signing_key=private,now_ns=NOW+1,key=FRAME_KEY)
    assert len(receiver.rows())==1  # No silent repair, reissue or invented new custody.


def test_historical_ack_rejects_index_tamper(rig):
    source,receiver,device,binding,private=rig
    d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=NOW,key=FRAME_KEY)
    source.db.execute("UPDATE acks SET wire_ref=?",('sha256:'+'0'*64,))
    with pytest.raises(ValueError):d.sync_signed(source,receiver,device=device,binding=binding,
        signing_key=private,now_ns=NOW+1,key=FRAME_KEY)


def test_expired_unacknowledged_challenge_can_renew_without_losing_history(rig):
    source,receiver,device,binding,private=rig
    row,old,ack=signed_one(rig)
    later=NOW+d.REQUEST_LIFETIME_NS+1
    new=d.request_delivery(source,row['message_key'],device=device,binding=binding,now_ns=later,key=FRAME_KEY,renew_expired=True)
    assert new['nonce']!=old['nonce'] and new['request_ref']!=old['request_ref']
    assert source.state('delivery-request-history:'+old['request_ref'])['value']==old
    with pytest.raises(PermissionError):d.accept_receipt(source,ack,device=device,binding=binding,now_ns=later,key=FRAME_KEY)
    result=d.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,now_ns=later,key=FRAME_KEY)
    assert result['new_acknowledgements']==2 and len(receiver.rows())==2
    historical=source.state('delivery-request-history:'+old['request_ref'])
    assert historical['revision']==1 and historical['value']==old


def test_acknowledged_challenge_is_never_renewed_in_place(rig):
    source,receiver,device,binding,private=rig
    row,request,ack=signed_one(rig)
    d.accept_receipt(source,ack,device=device,binding=binding,now_ns=NOW,key=FRAME_KEY)
    with pytest.raises(ValueError,match='historical'):
        d.request_delivery(source,row['message_key'],device=device,binding=binding,
            now_ns=NOW+d.REQUEST_LIFETIME_NS+1,key=FRAME_KEY,renew_expired=True)
    assert source.state(d._pending_namespace(binding,row['message_key']))['value']==request


def test_expired_challenge_renewal_still_requires_live_trust(rig):
    source,receiver,device,binding,private=rig
    row,old,_=signed_one(rig)
    with pytest.raises(PermissionError):
        d.request_delivery(source,row['message_key'],device=device,binding=binding,
            now_ns=binding.valid_until_ns,key=FRAME_KEY,renew_expired=True)
    assert source.state(d._pending_namespace(binding,row['message_key']))['value']==old
    assert source.state('delivery-request-history:'+old['request_ref']) is None
