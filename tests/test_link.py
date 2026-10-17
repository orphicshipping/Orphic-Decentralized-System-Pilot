"""Executable Link contract, durability, native Session and firmware-codec gates.

Fixtures are authored synthetic/recorded declarations, not physical readings.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import sqlite3
import struct
import subprocess
import sys
import zlib

import pytest

from ciw import link_contract as c, link_gateway as gateway, link_workflow as workflow
from ciw.control_checks import inspect_record
from ciw.link_capture_model import CaptureModel
from ciw.link_profiles import example_manifest, example_observations, catalog
from ciw.link_protocol import Decoder, MAX_PAYLOAD, MAX_FRAME, HEADER, decode_one, encode
from ciw.link_runtime import DeviceRuntime, Authority, prepare_configuration
from ciw.link_security import DOMAIN, verify_update, record_update_review
from ciw.link_store import Journal
from ciw.link_view import report


@pytest.fixture
def device():
    return example_manifest()


@pytest.fixture
def observations(device):
    return example_observations(device)


@pytest.fixture
def capture(tmp_path, device, observations):
    with Journal(tmp_path / "source.sqlite", create=True) as store:
        store.append(encode("manifest", c.bounded(device)), expected_manifest=device)
        for item in observations:
            store.append(encode("observation", c.bounded(item)), expected_manifest=device)
        return gateway.capture(store, expected_manifest=device, boot_id=observations[0]["boot_id"])


def mutate_wire(value, index, update, device):
    changed = deepcopy(value)
    raw = c.unpack(changed["frames"][index]["wire"], MAX_FRAME)
    frame = decode_one(raw)
    obj = c.loads(frame.payload)
    update(obj)
    changed["frames"][index]["wire"] = c.packed(encode(frame.kind, c.bounded(obj)), MAX_FRAME)
    return changed


@pytest.mark.parametrize("kind", ["manifest", "observation", "event"])
@pytest.mark.parametrize("authenticated", [False, True])
def test_framing_roundtrip(kind, authenticated):
    key = b"k" * 32 if authenticated else None
    raw = encode(kind, b'{"x":1}', key=key)
    frame = decode_one(raw, key=key, require_auth=authenticated)
    assert frame.payload == b'{"x":1}' and frame.kind == kind and frame.wire == raw
    assert frame.authentication == ("shared_key_possession_verified" if authenticated else "not_authenticated")


@pytest.mark.parametrize("stride", [1,2,3,4,7,11,12,13,16,31,256,1024])
def test_arbitrary_stream_fragmentation(stride, device):
    key = b"q" * 32
    wires = [encode("manifest", c.bounded(device), key=key)] + [encode("observation", c.bounded(v), key=key) for v in example_observations(device)]
    decoder = Decoder(key=key, require_auth=True)
    found = []
    raw = b"".join(wires)
    for offset in range(0,len(raw),stride):
        found.extend(decoder.feed(raw[offset:offset+stride]))
    decoder.finish()
    assert [v.wire for v in found] == wires


@pytest.mark.parametrize("offset", [0,1,3,4,5,6,7,8,9,10,11,12,15,20])
def test_corrupted_frame_refuses(offset):
    wire = bytearray(encode("observation", b'{"reading":25}'))
    wire[offset] ^= 1
    decoder = Decoder()
    with pytest.raises(ValueError):
        decoder.feed(bytes(wire)); decoder.finish()
    with pytest.raises(ValueError):
        decoder.feed(encode("event", b'{}'))


@pytest.mark.parametrize("key", [b"", b"k"*31, b"k"*65, "key", 1])
def test_bad_hmac_key_rejected(key):
    with pytest.raises(ValueError):
        encode("event", b'{}', key=key)


def test_hmac_and_downgrade_boundaries():
    wire = encode("event", b'{}', key=b"a"*32)
    with pytest.raises(ValueError): decode_one(wire, key=b"b"*32)
    with pytest.raises(ValueError): decode_one(wire)
    with pytest.raises(ValueError): decode_one(encode("event",b'{}'),key=b"a"*32,require_auth=True)
    assert decode_one(wire,allow_unverified=True).authentication == "not_reverified"
    with pytest.raises(ValueError): Decoder(require_auth=True)
    with pytest.raises(ValueError): Decoder(key=b"a"*32,require_auth=True,allow_unverified=True)


@pytest.mark.parametrize("cut", [1,2,4,10,11,12,13,15])
def test_truncation_and_extra_frame_refuse(cut):
    wire = encode("event", b'{"x":10}')
    with pytest.raises(ValueError): decode_one(wire[:-cut])
    with pytest.raises(ValueError): decode_one(wire+wire)


@pytest.mark.parametrize("length", [0,MAX_PAYLOAD+1,2**32-1])
def test_length_header_fails_before_allocation(length):
    raw = HEADER.pack(b"NLK1",1,0,1,length)
    with pytest.raises(ValueError): Decoder().feed(raw)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}',
                                  b'{"x":1e-9999}', b'{"x":"\\ud800"}', b'\xff', b'', b'['*1000])
def test_strict_json(raw):
    with pytest.raises((ValueError,RecursionError)): c.loads(raw)


@pytest.mark.parametrize("field,value", [("control","ENABLED"),("persona","auto-import-driver"),("device_id",""),
                                         ("hardware_revision",True),("firmware",None),("gateware",{"version":"1"})])
def test_bad_manifest_fields(device,field,value):
    device[field] = value
    with pytest.raises((ValueError,TypeError)): c.manifest(device)


@pytest.mark.parametrize("caps", [["READ","CONTROL"], ["READ","READ"], [], ["UPDATE"], ["READ","IMPORT_CODE"]])
def test_capabilities_do_not_grant_control(device,caps):
    device["capabilities"] = caps
    with pytest.raises(ValueError): c.manifest(device)


@pytest.mark.parametrize("raw", [b'25 cm\n',b'25mm\n',b'25 mm',b'NaN mm\n',b'1e4 mm\n',b'25 mm\ntrailing',b'25 mm\x00\n'])
def test_scalar_grammar(raw):
    with pytest.raises(ValueError): c.scalar(raw,"mm")


@pytest.mark.parametrize("field,value", [("device_id","other"),("instrument_id","other"),("sequence",True),
    ("device_tick",-1),("acquired_at","2026-10-05"),("manifest_ref","sha256:"+"0"*64),
    ("configuration_ref","sha256:"+"0"*64),("channel_id","missing"),("value",True),("value",float('nan')),
    ("uncertainty_reason",None),("stage","calibrated"),("origin","physically_verified")])
def test_observation_binding_refusals(device,observations,field,value):
    observations[0][field] = value
    with pytest.raises((ValueError,TypeError)): c.validate_observation(observations[0],device)


def test_raw_to_reported_tamper_even_after_new_wire_crc(capture,device):
    changed = mutate_wire(capture,0,lambda item:item.update(value=100.0),device)
    with pytest.raises(ValueError,match="Reported value"): gateway.validate_capture(changed)


def test_missing_reading_is_not_zero(device,observations):
    observations[0].update(value=None,missing_reason="sensor_disconnected")
    result = c.project(device,observations)
    assert result['native_stream']['observations'][0]['value'] is None


def test_unknown_clock_withholds_without_host_substitution(device,observations):
    device['clock']['tick_hz'] = None
    for item in observations: item['manifest_ref'] = c.content_identity(device)
    result = c.project(device,observations)
    assert not result['native_stream']['observations']
    assert len(result['withheld']) == 3 and result['clock_relation_to_host'] == 'UNKNOWN'


def test_large_exact_ticks_and_precision_withholding(device,observations):
    observations[0]['device_tick'] = 0
    observations[1]['device_tick'] = 2**64-2
    observations[2]['device_tick'] = 2**64-1
    result = c.project(device,observations)
    assert result['withheld'] and observations[2]['device_tick'] == 2**64-1


def test_relative_clock_origin_cannot_alias(device,observations):
    a = c.project(device,observations)
    b = c.project(device,observations[1:])
    assert a['native_stream']['observations'][0]['clock']['id'] != b['native_stream']['observations'][0]['clock']['id']


def test_channel_identity_distinguishes_same_quantity_same_tick(device):
    device['channels'].append({**device['channels'][0], 'id':'CH1','transducer_id':'other-sensor'})
    for k,v in [('enabled_channels',['CH0','CH1']),('sample_rate_hz',{'CH0':None,'CH1':None}),('ranges',{'CH0':None,'CH1':None})]:
        device['configuration'][k]=v
    rows = [c.make_observation(device,boot_id='b',sequence=i,channel_id=ch,raw=b'25 mm\n',tick=10,origin='synthetic') for i,ch in enumerate(['CH0','CH1'])]
    native = c.project(device,rows)['native_stream']['observations']
    assert len(native)==2 and native[0]['identity']['entity_id'] != native[1]['identity']['entity_id']


def test_gap_duplicate_reorder_and_boot_boundaries(device,observations):
    result=c.project(device,[observations[0],observations[2]])
    assert result['gaps']==[{'first_missing':1,'last_missing':1}]
    for values in ([observations[0],observations[0]],list(reversed(observations))):
        with pytest.raises(ValueError): c.project(device,values)
    changed=deepcopy(observations);changed[1]['boot_id']='new';changed[1]['measurement_id']=c.observation_id(device['device_id'],'new',1)
    with pytest.raises(ValueError,match='boot'): c.project(device,changed)


def test_tick_rollback(device,observations):
    observations[1]['device_tick']=observations[0]['device_tick']
    with pytest.raises(ValueError,match='tick'): c.project(device,observations)


def test_processed_stages_retained_but_not_promoted(device,observations):
    x=deepcopy(observations[0]);x.update(sequence=3,measurement_id=c.observation_id(device['device_id'],x['boot_id'],3),stage='derived',
        parents=[c.content_identity(observations[0])],transform_ref=c.bytes_ref(b'declared transform'),device_tick=2000000)
    result=c.project(device,observations+[x])
    assert len(result['native_stream']['observations'])==3
    assert result['withheld'][0]['reason']=='processed_stage_requires_qualified_transform'
    x['parents']=[c.bytes_ref(b'not retained')]
    with pytest.raises(ValueError,match='parent'): c.project(device,observations+[x])


def make_covariance(rows,device,matrix):
    from ciw.core.covariance import create_covariance_artifact
    return create_covariance_artifact(quantity_ids=[v['measurement_id'] for v in rows],units=['mm']*len(rows),
        frame=device['channels'][0]['frame'],reference_values=[v['value'] for v in rows],matrix=matrix,
        method='declared synthetic joint covariance',basis={'kind':'observation','id':c.bytes_ref(b'fixture')},
        provenance={'provider':'synthetic-fixture','source_evidence_ids':[c.bytes_ref(b'fixture')],'source_covariance_ids':[]},assumptions=['synthetic only'])


def test_full_joint_covariance_retained_without_diagonalization(device,observations):
    # The existing native constructor is used, not a Link covariance replacement.
    covariance=make_covariance(observations,device,[[1.,.5,.1],[.5,1.,.2],[.1,.2,1.]])
    result=c.project(device,observations,joint_covariance=covariance)
    assert c.canonical_json(result['joint_covariance'])==c.canonical_json(covariance)
    for item in result['native_stream']['observations']:
        assert item['uncertainty'] is None
        assert covariance['covariance_id'] in item['provenance']['sources']
    assert all(x['uncertainty_scope']=='joint_covariance_retained_separately' for x in result['mappings'])


def test_manifest_must_be_host_bound(tmp_path,device):
    other=deepcopy(device);other['device_id']='other'
    with Journal(tmp_path/'j',create=True) as store:
        with pytest.raises(ValueError,match='operator-bound'):
            store.append(encode('manifest',c.bounded(other)),expected_manifest=device)
        assert store.rows()==[]


def test_manifest_before_observation(tmp_path,device,observations):
    with Journal(tmp_path/'j',create=True) as store:
        with pytest.raises(ValueError,match='manifest'):
            store.append(encode('observation',c.bounded(observations[0])),expected_manifest=device)


def test_idempotency_conflicting_sequence_and_ack(tmp_path,device,observations):
    with Journal(tmp_path/'j',create=True) as store:
        wire=encode('manifest',c.bounded(device))
        assert store.append(wire,expected_manifest=device)==store.append(wire,expected_manifest=device)
        original=encode('observation',c.bounded(observations[0]));store.append(original,expected_manifest=device)
        changed=deepcopy(observations[0]);changed['device_tick']+=1
        with pytest.raises(ValueError,match='retry conflict'):
            store.append(encode('observation',c.bounded(changed)),expected_manifest=device)
        assert len(store.rows())==2
        with pytest.raises(ValueError): store.receipt('absent')


def test_capacity_never_silently_drops_or_advances(tmp_path,device):
    with Journal(tmp_path/'j',create=True,capacity_messages=1) as store:
        runtime=DeviceRuntime(device,store,boot_id='b')
        with pytest.raises(ValueError,match='NOT committed'): runtime.emit('CH0',b'25 mm\n',tick=1)
        assert runtime.sequence==0 and len(store.rows())==1


def test_disconnect_before_ack_recovery_is_idempotent(tmp_path,device,observations):
    source_path,receiver_path=tmp_path/'edge',tmp_path/'receiver'
    with Journal(source_path,create=True) as source, Journal(receiver_path,create=True) as receiver:
        source.append(encode('manifest',c.bounded(device)),expected_manifest=device)
        for item in observations: source.append(encode('observation',c.bounded(item)),expected_manifest=device)
        def crash(_): raise RuntimeError('simulated crash after receiver commit')
        with pytest.raises(RuntimeError): source.sync_to(receiver,manifests=[device],after_receive=crash)
        assert len(receiver.rows())==1
        assert source.db.execute('SELECT count(*) FROM acks').fetchone()==(0,)
    with Journal(source_path) as source, Journal(receiver_path) as receiver:
        assert source.sync_to(receiver,manifests=[device])['new_acknowledgements']==4
        assert source.sync_to(receiver,manifests=[device])['existing_acknowledgements']==4
        assert len(receiver.rows())==4


def test_real_process_exit_after_commit(tmp_path,device):
    path=tmp_path/'crash-store'
    # Actual process termination, not a mock transaction. No ACK follows the exit.
    script="""from ciw.link_store import Journal
from ciw.link_profiles import example_manifest
from ciw.link_protocol import encode
from ciw import link_contract as c
import os,sys
j=Journal(sys.argv[1],create=True);m=example_manifest();j.append(encode('manifest',c.bounded(m)),expected_manifest=m);os._exit(23)
"""
    child=subprocess.run([sys.executable,'-c',script,str(path)],check=False,capture_output=True)
    assert child.returncode==23,child.stderr.decode()
    with Journal(path) as reopened:
        assert len(reopened.rows())==1


def test_real_process_exit_uncommitted_transaction_rolls_back(tmp_path):
    path=tmp_path/'rollback-store'
    with Journal(path,create=True): pass
    script="""from ciw.link_store import Journal
import os,sys
j=Journal(sys.argv[1]);j.db.execute('BEGIN IMMEDIATE');j.db.execute("INSERT INTO state VALUES('uncommitted',1,?)",(b'{}',));os._exit(24)
"""
    assert subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True).returncode==24
    with Journal(path) as j: assert j.state('uncommitted') is None


def test_ack_destination_cannot_change(tmp_path,device):
    with Journal(tmp_path/'s',create=True) as source,Journal(tmp_path/'a',create=True) as a,Journal(tmp_path/'b',create=True) as b:
        source.append(encode('manifest',c.bounded(device)),expected_manifest=device)
        source.sync_to(a,manifests=[device])
        with pytest.raises(ValueError,match='destination'): source.sync_to(b,manifests=[device])


def test_store_detects_wire_corruption_and_schema_injection(tmp_path,device):
    path=tmp_path/'store'
    with Journal(path,create=True) as j:
        j.append(encode('manifest',c.bounded(device)),expected_manifest=device)
        j.db.execute('UPDATE messages SET wire=?',(b'corrupt',))
        with pytest.raises(ValueError): j.rows()
        j.db.execute('CREATE VIEW malicious AS SELECT 1')
    with pytest.raises(ValueError,match='schema'): Journal(path)


def test_store_rejects_symlinks(tmp_path):
    path=tmp_path/'target'
    with Journal(path,create=True):pass
    link=tmp_path/'link'
    try: link.symlink_to(path)
    except OSError: pytest.skip('Host does not grant symlink creation; no symlink-safety pass claimed')
    with pytest.raises(ValueError,match='symlink'): Journal(link)


def test_recorded_prefix_survives_transport_failure(tmp_path,device,observations):
    chunks=[encode('manifest',c.bounded(device)),encode('observation',c.bounded(observations[0])),b'garbage header']
    with Journal(tmp_path/'j',create=True) as j:
        with pytest.raises(ValueError): gateway.receive(chunks,j,expected_manifest=device)
        assert len(j.rows())==2


def test_hmac_capture_recheck_separate_from_historical_receipt(tmp_path,device,observations):
    key=b'k'*32
    with Journal(tmp_path/'j',create=True) as j:
        for kind,value in [('manifest',device)]+[('observation',v) for v in observations]:
            j.append(encode(kind,c.bounded(value),key=key),expected_manifest=device,key=key,require_auth=True)
        value=gateway.capture(j,expected_manifest=device,boot_id=observations[0]['boot_id'])
    assert gateway.validate_capture(value)['authentication_recheck']=='not_reverified'
    assert gateway.validate_capture(value,key=key)['authentication_recheck']=='shared_key_possession_verified'
    with pytest.raises(ValueError): gateway.validate_capture(value,key=b'x'*32)


def test_unsigned_cannot_claim_historical_auth(capture):
    capture['frames'][0]['authentication_at_capture']='shared_key_possession_verified'
    with pytest.raises(ValueError): gateway.validate_capture(capture)


def test_simulated_configuration_binds_plan_and_stales_old_runtime(tmp_path,device):
    with Journal(tmp_path/'j',create=True) as j:
        runtime=DeviceRuntime(device,j,boot_id='b')
        first=runtime.emit('CH0',b'25 mm\n',tick=1)['record']
        cfg=deepcopy(device['configuration']);cfg['revision']=2;cfg['sample_rate_hz']['CH0']=1000
        plan=prepare_configuration(device,cfg,expires_ns=1000)
        auth=Authority('operator',device['device_id'],frozenset({'CONFIGURE'}),1000)
        next_runtime,review=runtime.configure_simulated(plan,auth,now_ns=10)
        assert next_runtime.boot_id != runtime.boot_id
        assert review['physical_dispatch']=='not_performed'
        with pytest.raises(ValueError,match='stale'): runtime.emit('CH0',b'25 mm\n',tick=2)
        fresh=next_runtime.emit('CH0',b'26 mm\n',tick=1)['record']
        assert fresh['configuration_ref']!=first['configuration_ref']
        assert len(gateway.capture(j,expected_manifest=device,boot_id='b')['frames'])==1
        with pytest.raises(ValueError): runtime.configure_simulated(plan,auth,now_ns=11)


@pytest.mark.parametrize('permission',['READ','CALIBRATE','UPDATE','CONTROL'])
def test_permissions_are_not_interchangeable(tmp_path,device,permission):
    with Journal(tmp_path/'j',create=True) as j:
        runtime=DeviceRuntime(device,j)
        cfg=deepcopy(device['configuration']);cfg['revision']=2
        plan=prepare_configuration(device,cfg,expires_ns=1000)
        with pytest.raises(PermissionError):runtime.configure_simulated(plan,Authority('p',device['device_id'],frozenset({permission}),1000),now_ns=10)


def test_expired_or_modified_configuration_and_physical_refusal(tmp_path,device):
    with Journal(tmp_path/'j',create=True) as j:
        runtime=DeviceRuntime(device,j,origin='recorded')
        cfg=deepcopy(device['configuration']);cfg['revision']=2
        plan=prepare_configuration(device,cfg,expires_ns=100)
        auth=Authority('p',device['device_id'],frozenset({'CONFIGURE'}),1000)
        with pytest.raises(PermissionError):runtime.configure_simulated(plan,auth,now_ns=10)
        with pytest.raises(PermissionError):runtime.dispatch('CONFIGURE',{},auth,now_ns=10)
        with pytest.raises(PermissionError):auth.check('CONFIGURE',device,1000)
        with pytest.raises(PermissionError):Authority('p','other',frozenset({'CONFIGURE'}),1000).check('CONFIGURE',device,10)


def calibration(device):
    return {'schema':'ciw.link-calibration-declaration.v1','calibration_id':'synthetic.cal.1',
            'manifest_ref':c.content_identity(device),'channel_id':'CH0','unit':'mm',
            'valid_from':'2026-01-01T00:00:00Z','valid_until':'2027-01-01T00:00:00Z',
            'certificate':c.packed(b'SYNTHETIC declaration; not a certificate'), 'reference_standard_ref':None}


@pytest.mark.parametrize('at,status',[(None,'ACQUISITION_TIME_UNKNOWN'),('2026-10-05T00:00:00Z','DECLARED_APPLICABLE_AT_ACQUISITION'),
                                    ('2027-01-01T00:00:00Z','OUTSIDE_DECLARED_VALIDITY'),('2025-12-31T23:59:59Z','OUTSIDE_DECLARED_VALIDITY')])
def test_calibration_acquisition_interval_not_current_date(capture,device,at,status):
    declaration=calibration(device);capture['calibrations']=[declaration]
    changed=mutate_wire(capture,0,lambda x:x.update(calibration_ref=c.content_identity(declaration),acquired_at=at),device)
    result=gateway.validate_capture(changed)
    assert result['calibrations'][0]['status']==status
    assert result['calibrations'][0]['traceability']=='not_established'


def test_calibration_declaration_requires_distinct_permission(tmp_path,device):
    with Journal(tmp_path/'j',create=True) as j:
        with pytest.raises(PermissionError):gateway.register_calibration_declaration(j,calibration(device),device=device,
            authority=Authority('p',device['device_id'],frozenset({'READ'}),1000),now_ns=10)
        result=gateway.register_calibration_declaration(j,calibration(device),device=device,
            authority=Authority('p',device['device_id'],frozenset({'CALIBRATE'}),1000),now_ns=10)
        assert result['physical_calibration']=='not_performed'


def signed_package(device,generation=1,kind='firmware'):
    crypto=pytest.importorskip('cryptography.hazmat.primitives.asymmetric.ed25519')
    private=crypto.Ed25519PrivateKey.generate()
    image=b'SYNTHETIC IMAGE; never flashed'
    m={'schema':'ciw.link-update-manifest.v1','device_id':device['device_id'],'hardware_revision':device['hardware_revision'],
       'kind':kind,'version':'test-1','generation':generation,'image_ref':c.bytes_ref(image),'image_bytes':len(image)}
    package={'manifest':m,'signature_b64':base64.b64encode(private.sign(DOMAIN+c.bounded(m))).decode()}
    return package,image,private.public_key().public_bytes_raw()


@pytest.mark.parametrize('kind',['firmware','gateware'])
def test_signed_update_review_and_replay_counter(tmp_path,device,kind):
    package,image,key=signed_package(device,kind=kind)
    with Journal(tmp_path/'j',create=True) as j:
        auth=Authority('p',device['device_id'],frozenset({'UPDATE'}),1000)
        result=record_update_review(j,package,image,device=device,trusted_public_key=key,authority=auth,now_ns=10)
        assert result['installation']=='not_performed' and result['secure_boot']=='not_established'
        with pytest.raises(ValueError,match='rollback'):record_update_review(j,package,image,device=device,trusted_public_key=key,authority=auth,now_ns=11)


@pytest.mark.parametrize('mutation',['signature','key','image','target','hardware','generation'])
def test_update_tamper_rejected(device,mutation):
    package,image,key=signed_package(device)
    if mutation=='signature': package['signature_b64']=base64.b64encode(b'0'*64).decode()
    elif mutation=='key': key=b'0'*32
    elif mutation=='image': image=b'x'+image[1:]
    elif mutation=='target': package['manifest']['device_id']='other'
    elif mutation=='hardware': package['manifest']['hardware_revision']='other'
    else:package['manifest']['generation']=0
    with pytest.raises(ValueError):verify_update(package,image,device=device,trusted_public_key=key,minimum_generation=0)


def test_capture_model_trigger_and_overflow_accounting():
    model=CaptureModel(channels=['a','b'],pretrigger=2,posttrigger=2,capacity=5)
    for tick in range(8):model.sample(tick,{'a':tick,'b':tick+1},trigger=tick==4)
    result=model.snapshot()
    assert result['state']=='FROZEN'
    assert [x['tick'] for x in result['samples']]==[2,3,4,5,6]
    assert result['discarded_before_selected_window']==2 and result['rejected_after_freeze']==1
    assert result['physical_acquisition']=='not_performed'
    with pytest.raises(ValueError):model.sample(0,{'a':0,'b':0})
    with pytest.raises(ValueError):CaptureModel(channels=['a'],pretrigger=3,posttrigger=3,capacity=4)


@pytest.mark.parametrize('persona',c.PERSONAS)
def test_all_profiles_execute_through_native_session(tmp_path,persona):
    from ciw.link_cli import demo
    result=demo(persona,tmp_path/persona)
    assert result['status']=='PASS'
    assert result['execution_id']!=result['replay_execution_id'] and result['replay_preserved_evidence']
    native=c.loads((tmp_path/persona/'native/observations.json').read_bytes())
    inspect_record(native)
    assert native['observations']


def test_offline_reopen_no_runtime_or_network_and_exact_byte_replay(tmp_path,capture,monkeypatch):
    raw=json.dumps(capture,indent=4).encode()+b'\n\n'
    before=workflow.run(capture,tmp_path/'inv',raw=raw)
    def forbidden(*args,**kwargs):raise AssertionError('unexpected runtime/network access')
    import socket
    with monkeypatch.context() as context:
        context.setattr(socket,'socket',forbidden)
        context.setattr(workflow,'runtime_identity',forbidden)
        assert workflow.inspect(tmp_path/'inv')['execution_id']==before['execution_id']
    after=workflow.replay(tmp_path/'inv',tmp_path/'replay')
    assert (tmp_path/'replay/capture.json').read_bytes()==raw
    assert before['evidence_id']==after['evidence_id'] and before['result_id']!=after['result_id']


def test_invalid_capture_retains_refused_session(tmp_path,capture):
    capture['frames'][0]['host_clock']=''
    result=workflow.run(capture,tmp_path/'inv')
    assert result['status']=='REFUSED' and result['result_id'] is None
    assert workflow.inspect(tmp_path/'inv')['status']=='REFUSED'


def test_coherently_resealed_result_still_checked_against_wire(tmp_path,capture):
    from ciw.operations.runner import seal
    workflow.run(capture,tmp_path/'inv')
    path=tmp_path/'inv/workspace.json';workspace=json.loads(path.read_text())
    r=workspace['results'][0];r['data']['projection']['native_stream']['observations'][0]['value']=999
    workspace['results'][0]=seal(r)
    path.write_text(json.dumps(workspace))
    with pytest.raises(ValueError):workflow.inspect(tmp_path/'inv')


def test_original_input_bytes_are_bound(tmp_path,capture):
    workflow.run(capture,tmp_path/'inv')
    p=tmp_path/'inv/capture.json';p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError,match='bytes'):workflow.inspect(tmp_path/'inv')


def test_no_clobber_and_html_escaping(tmp_path,capture,device):
    # Context is data, not executable UI markup.
    capture=mutate_wire(capture,0,lambda x:x['context'].update(asset_id='<img src=x onerror=alert(1)>'),device)
    workflow.run(capture,tmp_path/'inv')
    with pytest.raises(FileExistsError):workflow.run(capture,tmp_path/'inv')
    report(tmp_path/'inv',tmp_path/'panel.html')
    body=(tmp_path/'panel.html').read_text()
    assert '<img src=x' not in body and '&lt;img src=x' in body
    assert 'NOT VERIFIED' in body and 'Not a live reading' in body
    with pytest.raises(FileExistsError):report(tmp_path/'inv',tmp_path/'panel.html')


def test_context_preserved_without_manufacturing_authority(capture,tmp_path):
    result=workflow.run(capture,tmp_path/'inv')
    context=result['data']['observations'][0]['context']
    assert context['part_revision']=='A' and context['lot_id']=='synthetic.lot.1'
    assert result['limits']['production_release']=='not_performed'


def test_installed_top_level_cli(tmp_path):
    command=[sys.executable,'-m','ciw.net','link']
    result=subprocess.run(command+['demo','--output-dir',str(tmp_path/'cli')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['status']=='PASS'
    again=subprocess.run(command+['demo','--output-dir',str(tmp_path/'cli')],capture_output=True,text=True)
    assert again.returncode==2
    inspect=subprocess.run(command+['inspect',str(tmp_path/'cli/investigation')],capture_output=True,text=True)
    assert inspect.returncode==0 and json.loads(inspect.stdout)['status']=='RETAINED'


@pytest.fixture(scope='session')
def codec(tmp_path_factory):
    supplied=os.environ.get('NET_LINK_NATIVE_CODEC')
    if supplied:
        path=Path(supplied)
        assert path.is_file()
        return str(path)
    root=Path(__file__).resolve().parents[1]
    source=root/'examples/device-link/native'
    cc=shutil.which('cc') or shutil.which('gcc')
    if not cc or not source.is_dir():
        pytest.skip('Native C codec gate requires an explicit executable or compiler and source')
    output=tmp_path_factory.mktemp('link-c')/'link-wire'
    subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Wpedantic','-Werror',str(source/'link_wire.c'),str(source/'link_wire_cli.c'),'-o',str(output)],check=True,capture_output=True)
    return str(output)


@pytest.mark.parametrize('kind',[1,2,3])
@pytest.mark.parametrize('length',[1,2,15,255,256,4096,65536])
def test_real_native_c_python_frame_agreement(codec,kind,length):
    payload=random.Random(kind*100000+length).randbytes(length)
    native=subprocess.run([codec,'encode',str(kind)],input=payload,capture_output=True,check=True).stdout
    assert native==encode({1:'manifest',2:'observation',3:'event'}[kind],payload)
    assert subprocess.run([codec,'decode'],input=native,capture_output=True,check=True).stdout==payload
    assert decode_one(native).payload==payload


def test_native_producer_bytes_into_real_gateway_and_session(codec,tmp_path,device,observations):
    wires=[]
    for kind,value in [(1,device)]+[(2,v) for v in observations]:
        wires.append(subprocess.run([codec,'encode',str(kind)],input=c.bounded(value),capture_output=True,check=True).stdout)
    with Journal(tmp_path/'native-store',create=True) as store:
        gateway.receive(wires,store,expected_manifest=device)
        retained=gateway.capture(store,expected_manifest=device,boot_id=observations[0]['boot_id'])
    summary=workflow.run(retained,tmp_path/'native-inv')
    assert summary['status']=='RETAINED'
    assert [c.unpack(x['wire'],MAX_FRAME) for x in retained['frames']]==wires[1:]


@pytest.mark.parametrize('mode',['crc','truncate','trailing','mac','empty','oversize'])
def test_native_refuses_bad_frames(codec,mode):
    wire=encode('event',b'{}')
    if mode=='crc':wire=wire[:-1]+bytes([wire[-1]^1])
    elif mode=='truncate':wire=wire[:-1]
    elif mode=='trailing':wire+=b'x'
    elif mode=='mac':wire=encode('event',b'{}',key=b'x'*32)
    elif mode=='empty':wire=b''
    else:wire=b'x'*(MAX_FRAME+1)
    assert subprocess.run([codec,'decode'],input=wire,capture_output=True).returncode==2


def test_joint_covariance_session_export_and_view(tmp_path,device,observations,capture):
    covariance=make_covariance(observations,device,[[1.,.5,.1],[.5,1.,.2],[.1,.2,1.]])
    capture['joint_covariance']=covariance
    result=workflow.run(capture,tmp_path/'joint')
    assert c.canonical_json(result['data']['projection']['joint_covariance'])==c.canonical_json(covariance)
    workflow.export_native(tmp_path/'joint',tmp_path/'native')
    assert c.canonical_json(c.loads((tmp_path/'native/covariance.json').read_bytes()))==c.canonical_json(covariance)
    report(tmp_path/'joint',tmp_path/'joint.html')
    assert 'JOINT DECLARED' in (tmp_path/'joint.html').read_text()
    assert workflow.replay(tmp_path/'joint',tmp_path/'replay')['evidence_id']==result['evidence_id']


def test_identical_clock_aliases_do_not_merge_devices(device,observations):
    first=c.project(device,observations)['native_stream']['observations'][0]['clock']['id']
    other=deepcopy(device);other['device_id']='different-device'
    second=c.project(other,example_observations(other))['native_stream']['observations'][0]['clock']['id']
    assert first!=second


@pytest.mark.parametrize('mutation',['expiry','plan_ref','effects','rate','base'])
def test_simulated_configuration_refuses_changed_plan(tmp_path,device,mutation):
    cfg=deepcopy(device['configuration']);cfg['revision']=2
    plan=prepare_configuration(device,cfg,expires_ns=1000)
    if mutation=='expiry':now=1000
    else:
        now=10
        if mutation=='plan_ref':plan['plan_ref']=c.bytes_ref(b'wrong')
        elif mutation=='effects':plan['effect']='move_machine'
        elif mutation=='rate':plan['new_configuration']['sample_rate_hz']['CH0']=500
        else:plan['base_manifest_ref']=c.bytes_ref(b'wrong')
    with Journal(tmp_path/'j',create=True) as j:
        runtime=DeviceRuntime(device,j,boot_id='boot')
        with pytest.raises((ValueError,PermissionError)):
            runtime.configure_simulated(plan,Authority('operator',device['device_id'],frozenset({'CONFIGURE'}),2000),now_ns=now)
        assert j.state('config:'+device['device_id']) is None
        assert len(j.rows())==1


def test_simulation_state_compare_and_set_fences_competing_connections(tmp_path):
    path=tmp_path/'j'
    with Journal(path,create=True) as first,Journal(path) as second:
        first.compare_and_set_state('simulated-config',0,{'value':1})
        with pytest.raises(ValueError,match='Stale'):
            second.compare_and_set_state('simulated-config',0,{'value':2})
        assert second.state('simulated-config')['value']=={'value':1}
        first.db.execute("UPDATE state SET revision=-1")
        with pytest.raises(ValueError):second.state('simulated-config')


def test_two_connections_same_wire_have_one_committed_receipt(tmp_path,device):
    path=tmp_path/'j';wire=encode('manifest',c.bounded(device))
    with Journal(path,create=True) as first,Journal(path) as second:
        a=first.append(wire,expected_manifest=device)
        b=second.append(wire,expected_manifest=device)
        assert a==b and len(first.rows())==1


def test_hmac_requirement_applies_again_at_receiver(tmp_path,device):
    with Journal(tmp_path/'source',create=True) as source,Journal(tmp_path/'target',create=True) as target:
        source.append(encode('manifest',c.bounded(device)),expected_manifest=device)
        with pytest.raises(ValueError,match='Unsigned'):
            source.sync_to(target,manifests=[device],key=b'k'*32,require_auth=True)
        assert target.rows()==[]
        assert source.db.execute('SELECT count(*) FROM acks').fetchone()[0]==0


def test_zero_posttrigger_and_not_yet_triggered_capture():
    waiting=CaptureModel(channels=['a'],pretrigger=2,posttrigger=0,capacity=3)
    waiting.sample(1,{'a':2});waiting.sample(2,{'a':3})
    assert waiting.snapshot()['state']=='ARMED' and waiting.snapshot()['samples']==[]
    waiting.sample(3,{'a':4},trigger=True)
    assert waiting.snapshot()['state']=='FROZEN'
    assert [x['tick'] for x in waiting.snapshot()['samples']]==[1,2,3]


def test_full_operator_record_sync_capture_and_security_commands(tmp_path,device):
    command=[sys.executable,'-m','ciw.net','link']
    def run(*args,expected=0):
        result=subprocess.run(command+[str(a) for a in args],capture_output=True,text=True,timeout=30)
        assert result.returncode==expected,result.stdout+result.stderr
        return json.loads(result.stdout if expected==0 else result.stderr)
    source=run('example','--output-dir',tmp_path/'example')
    m=tmp_path/'example/manifest.json'
    run('record',source['wire'],'--manifest',m,'--journal',tmp_path/'edge','--create')
    run('sync','--source',tmp_path/'edge','--receiver',tmp_path/'gateway','--create-receiver','--manifest',m)
    again=run('sync','--source',tmp_path/'edge','--receiver',tmp_path/'gateway','--manifest',m)
    assert again['new_acknowledgements']==0 and again['existing_acknowledgements']==4
    run('capture','--journal',tmp_path/'gateway','--manifest',m,'--boot-id',source['boot_id'],'--output',tmp_path/'capture.json')
    result=run('run',tmp_path/'capture.json','--output-dir',tmp_path/'investigation')
    assert result['status']=='RETAINED'
    run('export',tmp_path/'investigation','--output-dir',tmp_path/'native')
    run('report',tmp_path/'investigation','--output',tmp_path/'panel.html')
    cfg=deepcopy(device['configuration']);cfg['revision']=2
    (tmp_path/'configuration.json').write_bytes(c.bounded(cfg))
    plan=run('config-plan','--manifest',m,'--configuration',tmp_path/'configuration.json','--expires-ns',1000,'--output',tmp_path/'plan.json')
    assert plan['dispatch']=='not_performed'
    package,image,key=signed_package(device)
    (tmp_path/'package.json').write_bytes(c.bounded(package));(tmp_path/'image.bin').write_bytes(image);(tmp_path/'public.key').write_bytes(key)
    review=run('verify-update','--manifest',m,'--package',tmp_path/'package.json','--image',tmp_path/'image.bin','--public-key',tmp_path/'public.key','--minimum-generation',0)
    assert review['installation']=='not_performed'
    run('verify-update','--manifest',m,'--package',tmp_path/'package.json','--image',tmp_path/'image.bin','--public-key',tmp_path/'public.key','--minimum-generation',1,expected=2)
