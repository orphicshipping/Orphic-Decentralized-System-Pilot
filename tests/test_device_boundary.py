"""Recorded-data boundary tests. Synthetic keys/samples are not physical evidence."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
from unittest.mock import patch

import pytest

from ciw import device_boundary as b
from ciw import device_boundary_workflow as w
from ciw.device_boundary_cli import example, main
from ciw.device_boundary_sp1 import ExistingSP1Verifier
from ciw.core.identities import content_identity
from ciw.instruments import make_demo_run
from ciw.legibility import key_fingerprint
from ciw.operations.runner import seal
from ciw.session import Session

NOW=1_800_000_000_000_000_000

@pytest.fixture
def specimen():
    return example(now_ns=NOW)


def signed(specimen, change=None):
    packet,policy,key,netkey=specimen
    payload=deepcopy(packet['payload'])
    if change:change(payload)
    return b.encode(b.signed_packet(payload,key)),policy


def review(specimen,change=None,policy_change=None):
    raw,policy=signed(specimen,change)
    policy=deepcopy(policy)
    if policy_change:policy_change(policy)
    return w.evaluate(raw,policy,NOW),raw


def host(tmp_path,specimen,clock=lambda:NOW,verifier=None):
    return w.BoundaryHost(Session(make_demo_run(),tmp_path),specimen[1],clock=clock,verifier=verifier)


def response(h,raw,function=None):
    value=h.request(raw,function)
    assert value['type']=='response', value
    return value['payload']


def test_real_session_readonly_journey(tmp_path,specimen):
    h=host(tmp_path,specimen);raw=b.encode(specimen[0])
    first=response(h,raw)
    assert first['status']=='completed'
    r=first['result']['data']['review']
    assert r['checks']['coherence']
    assert b.ratio(r['canonical_state_nm'])==24982000
    assert r['proof']['status']=='unavailable'
    assert r['authorizes_execution'] is False
    ids=[]
    for f in ('record_raw','record_calibrated','compare_tolerance'):
        result=response(h,raw,f)
        assert result['status']=='completed',result
        assert result['result']['verification_status']=='not_verified'
        assert result['result']['verification_id'] is None
        ids.extend((result['execution']['execution_id'],result['result']['result_id']))
    assert len(set(ids))==6
    for f in ('generate_inspection_record','run_model_inference','release_part','machine_control'):
        result=response(h,raw,f)
        assert result['status']=='refused' and result['result'] is None
    assert len(h.session.executions)==8 and len(h.session.results)==4


@pytest.mark.parametrize('unit,q',[('m',Fraction(12491,500000)),('mm',Fraction(12491,500)),
    ('um',Fraction(24982)),('nm',Fraction(24982000))])
def test_exact_unit_equivalence(specimen,unit,q):
    r,_=review(specimen,lambda p:p['device_state'].update(unit=unit,value=b.rational(q)))
    assert r['checks']['coherence'] and r['capabilities']['record_calibrated']['allow']


@pytest.mark.parametrize('change,failed',[
    (lambda p:p['device_state'].update(value=b.rational(25)),'coherence'),
    (lambda p:p.update(frame_id='other') or p['device_state'].update(frame_id='other'),'frame'),
    (lambda p:p.update(firmware_id=b.raw_id(b'changed firmware')),'firmware_declaration'),
    (lambda p:p.update(gateware_id=b.raw_id(b'changed fpga')),'gateware_declaration'),
    (lambda p:p.update(clock_id='different-clock'),'clock'),
    (lambda p:p.update(time_uncertainty_ns=None),'clock'),
    (lambda p:p.update(time_uncertainty_ns=1001),'clock'),
    (lambda p:p.update(captured_at_ns=NOW+1000),'freshness'),
    (lambda p:p.update(captured_at_ns=NOW-10**11),'freshness'),
    (lambda p:p['transform'].update(gain=999),'calibration'),
])
def test_unqualified_derived_does_not_destroy_raw(specimen,change,failed):
    r,_=review(specimen,change)
    assert not r['checks'][failed]
    assert r['capabilities']['record_raw']['allow']
    assert not r['capabilities']['record_calibrated']['allow']


@pytest.mark.parametrize('which,offset',[('valid_from_ns',0),('valid_until_ns',0)])
def test_calibration_entire_sample_uncertainty_support(specimen,which,offset):
    p=deepcopy(specimen[0]['payload']);c=p['transform'];t=p['captured_at_ns']
    c[which]=t
    policy=deepcopy(specimen[1]);policy['approved_transform_ids']=[content_identity(c)]
    r=w.evaluate(b.encode(b.signed_packet(p,specimen[2])),policy,NOW)
    assert not r['checks']['calibration']
    assert r['capabilities']['record_raw']['allow']


def test_unknown_uncertainty_blocks_comparison_not_declared_calculation(specimen):
    p=deepcopy(specimen[0]['payload']);p['transform'].update(uncertainty_bound_nm=None,uncertainty_basis='unknown')
    policy=deepcopy(specimen[1]);policy['approved_transform_ids']=[content_identity(p['transform'])]
    r=w.evaluate(b.encode(b.signed_packet(p,specimen[2])),policy,NOW)
    assert r['capabilities']['record_calibrated']['allow']
    assert not r['capabilities']['compare_tolerance']['allow']


@pytest.mark.parametrize('low,high,expected',[(24970000,25000000,'inside_declared_interval'),
    (25000000,26000000,'outside_declared_interval'),(24982000,26000000,'indeterminate')])
def test_interval_comparison_keeps_uncertainty(specimen,low,high,expected):
    r,raw=review(specimen,policy_change=lambda p:p['specification_nm'].update(lower=b.rational(low),upper=b.rational(high)))
    effect=w.functional_result(r,raw,'compare_tolerance')
    assert effect['comparison']==expected
    assert not effect['released'] and not effect['equipment_authority']


@pytest.mark.parametrize('field,value',[('device_id','wrong-device'),('boot_id','unbound-boot')])
def test_wrong_identity_never_opens_raw(specimen,field,value):
    r,_=review(specimen,lambda p:p.update({field:value}))
    assert not r['checks']['identity']
    assert not any(g['allow'] for g in r['capabilities'].values())


def test_wrong_key_and_unsigned_mutation_are_quarantined(specimen):
    for packet in (deepcopy(specimen[0]),deepcopy(specimen[0])):
        if packet['payload']['sequence']==1:
            packet['payload']['sequence']=2
        r=w.evaluate(b.encode(packet),specimen[1],NOW)
        assert not r['checks']['identity'] and not r['capabilities']['record_raw']['allow']
    wrong=example(now_ns=NOW)[0]
    r=w.evaluate(b.encode(wrong),specimen[1],NOW)
    assert not r['checks']['identity']


@pytest.mark.parametrize('raw',[b'{"x":1,"x":2}',b'{"x":NaN}',b'[]',b'not json',b'\xff',b'{}'])
def test_malformed_evidence_still_retained_as_quarantine(tmp_path,specimen,raw):
    h=host(tmp_path,specimen);value=response(h,raw)
    assert value['status']=='completed'
    r=value['result']['data']['review']
    assert r['packet_id']==b.raw_id(raw)
    assert r['quarantine_reasons']
    assert not any(g['allow'] for g in r['capabilities'].values())
    assert b.unb64(value['result']['parameters']['packet_b64'])==raw
    denied=response(h,raw,'record_calibrated')
    assert denied['status']=='refused' and denied['result'] is None


@pytest.mark.parametrize('change',[
    lambda p:p.update(sequence=True), lambda p:p.update(sequence=-1),
    lambda p:p.update(origin='physical'),lambda p:p.update(captured_at_ns=2**63),
    lambda p:p.update(raw_id=b.raw_id(b'other')),
    lambda p:p['device_state'].update(unit='kg'),
    lambda p:p['device_state'].update(value={'numerator':2,'denominator':2}),
    lambda p:p['transform'].update(denominator=0),lambda p:p['transform'].update(gain=2**20+1),
    lambda p:p['transform'].update(offset=2**60+1),
    lambda p:p['transform'].update(uncertainty_bound_nm=b.rational(-1)),
    lambda p:p.update(extra='unrecognized'),
])
def test_invalid_payload_fails_closed(specimen,change):
    p=deepcopy(specimen[0]['payload']);change(p)
    with pytest.raises((ValueError,TypeError)):b.validate_payload(p)


def test_review_never_consumes_function_permission_but_perform_does(tmp_path,specimen):
    h=host(tmp_path,specimen);raw=b.encode(specimen[0])
    assert response(h,raw)['status']=='completed'
    assert response(h,raw)['status']=='completed'
    assert response(h,raw,'record_calibrated')['status']=='completed'
    result=response(h,raw,'record_calibrated')
    assert result['status']=='refused' and 'sequence' in result['execution']['refusal']['message']
    assert response(h,raw,'record_raw')['status']=='completed'
    newer,_=signed(specimen,lambda p:p.update(sequence=2,sample_id='next-sample'))
    assert response(h,newer,'record_calibrated')['status']=='completed'
    assert response(h,raw,'record_calibrated')['status']=='refused'


def test_racing_same_function_consumes_at_most_once(tmp_path,specimen):
    h=host(tmp_path,specimen);raw=b.encode(specimen[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes=list(pool.map(lambda _:response(h,raw,'record_calibrated')['status'],range(4)))
    assert outcomes.count('completed')==1 and outcomes.count('refused')==3


def test_current_policy_revocation_beats_past_allow(tmp_path,specimen):
    h=host(tmp_path,specimen);raw=b.encode(specimen[0])
    old=response(h,raw)['result']['data']['review']
    assert old['capabilities']['record_calibrated']['allow']
    policy=deepcopy(specimen[1]);policy['approved_transform_ids']=[];policy['epoch']='revoked-2'
    h.set_policy(policy)
    denied=response(h,raw,'record_calibrated')
    assert denied['status']=='refused'
    assert old['capabilities']['record_calibrated']['allow'] # historical record unchanged


def test_policy_is_detached_from_caller(tmp_path,specimen):
    h=host(tmp_path,specimen);raw=b.encode(specimen[0])
    specimen[1]['allowed_functions'].clear()
    assert response(h,raw,'record_calibrated')['status']=='completed'


def test_clock_rollback_refuses_and_is_retained(tmp_path,specimen):
    times=iter([NOW,NOW-1]);h=host(tmp_path,specimen,clock=lambda:next(times))
    denied=response(h,b.encode(specimen[0]),'record_calibrated')
    assert denied['status']=='refused'
    assert denied['execution']['refusal']['code']=='boundary_clock_rollback'


def test_expiry_is_rechecked_at_execution_boundary(tmp_path,specimen):
    end=specimen[1]['valid_until_ns']
    times=iter([NOW,end]);h=host(tmp_path,specimen,clock=lambda:next(times))
    denied=response(h,b.encode(specimen[0]),'record_calibrated')
    assert denied['status']=='refused' and 'authorization' in denied['execution']['refusal']['message']


def test_unsupported_physical_authority_stays_closed_even_if_policy_requests_it(specimen):
    r,_=review(specimen,policy_change=lambda p:p.update(allowed_functions=list(b.FUNCTIONS),require_sp1_functions=[]))
    for f in ('release_part','machine_control','run_model_inference'):
        assert not r['capabilities'][f]['allow']


@pytest.mark.parametrize('proof',[True,{'verified':True},{'schema':'wrong','bytes_b64':'eA=='},
    {'schema':'ciw.device-boundary-sp1.v1','bytes_b64':''}])
def test_forged_proof_reports_are_not_verification(specimen,proof):
    packet=deepcopy(specimen[0]);packet['proof']=proof
    r=w.evaluate(b.encode(packet),specimen[1],NOW)
    assert r['proof']['status']=='failed'
    assert r['capabilities']['record_raw']['allow']
    assert not r['capabilities']['record_calibrated']['allow']
    assert not r['capabilities']['generate_inspection_record']['allow']


def test_proof_bytes_without_verifier_stay_unavailable(specimen):
    packet=deepcopy(specimen[0]);packet['proof']={'schema':'ciw.device-boundary-sp1.v1','bytes_b64':'eA=='}
    r=w.evaluate(b.encode(packet),specimen[1],NOW)
    assert r['proof']['status']=='unavailable' and r['proof']['verification_id'] is None
    assert not r['capabilities']['generate_inspection_record']['allow']


def test_untrusted_verifier_callback_is_rejected(tmp_path,specimen):
    with pytest.raises(ValueError,match='operator-bound'):
        host(tmp_path,specimen,verifier=lambda *a:True)


def test_statement_binds_device_sample_frame_config_and_origin(specimen):
    baseline=b.statement(specimen[0]['payload'])
    for field,value in [('sample_id','changed'),('sequence',2),('device_id','changed'),
                        ('firmware_id',b.raw_id(b'other')),('origin','recorded')]:
        payload=deepcopy(specimen[0]['payload']);payload[field]=value
        assert b.statement(payload)!=baseline
    payload=deepcopy(specimen[0]['payload']);payload['transform']['offset']=1
    assert b.statement(payload)!=baseline


def test_saved_workspace_opens_offline_but_restores_no_active_grant(tmp_path,specimen):
    h=host(tmp_path/'original',specimen);raw=b.encode(specimen[0])
    response(h,raw,'record_calibrated');path=h.session.save_workspace(tmp_path/'workspace.json')
    original=path.read_bytes()
    with patch.object(ExistingSP1Verifier,'verify',side_effect=AssertionError('no provider on reopen')):
        restored=Session.from_workspace(path,tmp_path/'restored')
    assert restored.results==h.session.results
    absent=restored.handle({'protocol_version':1,'request_id':'no-active-grant','type':'operation.execute',
        'payload':{'operation_id':w.PERFORM,'parameters':{'packet_b64':base64.b64encode(raw).decode(),'function':'record_calibrated'}}})
    assert absent['payload']['status']=='refused'
    assert absent['payload']['execution']['refusal']['code']=='operation_unavailable'
    rebound=w.BoundaryHost(restored,specimen[1],clock=lambda:NOW)
    assert response(rebound,raw,'record_calibrated')['status']=='refused'
    assert path.read_bytes()==original


def test_resealed_semantic_tampering_refuses_on_reopen(tmp_path,specimen):
    h=host(tmp_path/'original',specimen);raw=b.encode(specimen[0]);response(h,raw)
    path=h.session.save_workspace(tmp_path/'workspace.json');value=json.loads(path.read_text())
    result=value['results'][0]
    result['data']['review']['checks']['coherence']=False
    seal(result);path.write_text(json.dumps(value))
    with pytest.raises(ValueError):Session.from_workspace(path,tmp_path/'bad')


def test_caller_cannot_supply_allow_report_or_saved_execution_policy(tmp_path,specimen):
    h=host(tmp_path,specimen)
    params={'packet_b64':base64.b64encode(b.encode(specimen[0])).decode(),'function':'record_calibrated','allow':True}
    result=h.session.handle({'protocol_version':1,'request_id':'attack','type':'operation.execute',
        'payload':{'operation_id':w.PERFORM,'parameters':params}})
    assert result['payload']['status']=='refused' and result['payload']['result'] is None


def reply_arguments(reply,specimen):
    public=b.unb64(reply['signature']['public_key_b64'],32)
    return dict(trusted_keys={key_fingerprint(public):public},packet_id=b.raw_id(b.encode(specimen[0])),
                device_id=specimen[1]['device_id'],policy_id=content_identity(specimen[1]),net_id='net-1',now_ns=NOW)


def test_net_reply_uses_real_signature_without_granting_execution(specimen):
    r,_=review(specimen)
    reply=b.make_reply(r,specimen[3],net_id='net-1',now_ns=NOW,ttl_ns=10**6)
    args=reply_arguments(reply,specimen)
    assert b.verify_reply(reply,**args)['authorizes_execution'] is False
    for k,v in [('packet_id',b.raw_id(b'wrong')),('device_id','other'),('policy_id',b.raw_id(b'old')),
                ('net_id','other'),('now_ns',NOW+10**6)]:
        bad={**args,k:v}
        with pytest.raises(ValueError):b.verify_reply(reply,**bad)
    reply['payload']['capabilities']['machine_control']['allow']=True
    with pytest.raises(ValueError):b.verify_reply(reply,**args)


def test_reply_overflow_and_fake_authority_rejected(specimen):
    r,_=review(specimen)
    with pytest.raises(ValueError):b.make_reply(r,specimen[3],net_id='net-1',now_ns=2**63-1,ttl_ns=1)
    reply=b.make_reply(r,specimen[3],net_id='net-1',now_ns=NOW,ttl_ns=10**6)
    args=reply_arguments(reply,specimen)
    reply['payload']['authorizes_execution']=True
    with pytest.raises(ValueError):b.verify_reply(reply,**args)


def test_cli_demo_inspection_missing_proof_and_no_clobber(tmp_path,capsys):
    out=tmp_path/'demo'
    assert main(['demo','--output',str(out)])==0
    captured=json.loads(capsys.readouterr().out)
    anchor=captured['workspace_sha256']
    assert main(['inspect','--workspace',str(out/'workspace.json'),'--workspace-sha256',anchor])==0
    assert json.loads(capsys.readouterr().out)['authorizes_execution'] is False
    assert main(['demo','--output',str(out)])==2
    capsys.readouterr()
    gate=tmp_path/'proof-gate'
    assert main(['proof-gate','--output',str(gate)])==2
    assert json.loads((gate/'gate.json').read_text())['status']=='NOT_RUN'


def test_cli_partial_proof_binding_is_failed_not_passed(tmp_path,capsys):
    out=tmp_path/'gate'
    assert main(['proof-gate','--output',str(out),'--binding',str(tmp_path/'missing')])==1
    report=json.loads((out/'gate.json').read_text())
    assert report['status']=='FAIL' and report['proof_generated'] is False


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    compiler=shutil.which('c++') or shutil.which('g++') or shutil.which('clang++')
    if compiler is None:
        if os.environ.get('NET_BOUNDARY_REQUIRE_CXX')=='1':
            pytest.fail('native C++ compiler required by this qualification gate')
        pytest.skip('native C++ compiler absent; required CI gate supplies one')
    root=Path(__file__).resolve().parents[1]
    # Installed tests receive an explicit source path for this independent native checker.
    source=Path(os.environ.get('NET_BOUNDARY_CPP_SOURCE',str(root/'runtimes/device-boundary/affine.cpp')))
    exe=tmp_path_factory.mktemp('native')/('affine.exe' if os.name=='nt' else 'affine')
    subprocess.run([compiler,'-std=c++17','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(exe)],check=True,capture_output=True)
    return exe


def wire(count,gain,offset,den,context=bytes(range(32))):
    return b'DBI1'+context+struct.pack('<qqqQ',count,gain,offset,den)


@pytest.mark.parametrize('count,gain,offset,den',[(24982,1000,0,1),(0,0,0,1),
    (2**40,2**20,2**60,2**20),(-2**40,2**20,-2**60,1),
    (-2**40,-2**20,2**60,7),(1,-1,0,3),(-1,1,0,5)])
def test_native_exact_wide_signed_vectors(native,count,gain,offset,den):
    raw=wire(count,gain,offset,den)
    result=subprocess.run([str(native)],input=raw,capture_output=True,timeout=5)
    assert result.returncode==0
    assert result.stdout==b.compute(raw)


def test_native_randomized_differential(native):
    rng=random.Random(740051)
    for _ in range(256):
        raw=wire(rng.randint(-2**40,2**40),rng.randint(-2**20,2**20),rng.randint(-2**60,2**60),rng.randint(1,2**20),rng.randbytes(32))
        result=subprocess.run([str(native)],input=raw,capture_output=True,timeout=5)
        assert result.returncode==0 and result.stdout==b.compute(raw)


@pytest.mark.parametrize('raw',[b'',b'DBI1',wire(1,2,3,1)+b'x',wire(2**40+1,1,0,1),
    wire(1,2**20+1,0,1),wire(1,1,2**60+1,1),wire(1,1,0,0),wire(1,1,0,2**20+1)])
def test_native_malformed_and_bound_refusal(native,raw):
    result=subprocess.run([str(native)],input=raw,capture_output=True,timeout=5)
    assert result.returncode in (2,3) and result.stdout==b''
    with pytest.raises(ValueError):b.compute(raw)


def test_filter_computation_proof_and_gate_identities_are_separate(specimen):
    r,raw=review(specimen)
    w.validate_report(r,raw)
    objects=r['objects']
    assert objects['filters']['device']['filter_id']!=objects['filters']['net']['filter_id']
    assert objects['filters']['device']['status']=='device_declared_not_attested'
    assert objects['computation']['program_id']==b.raw_id(b.PROGRAM)
    assert objects['computation']['execution_id']!=r['review_id']
    assert len({g['gate_id'] for g in r['capabilities'].values()})==len(b.FUNCTIONS)
    second,_=review(specimen)
    assert second['objects']['computation']['computation_id']==objects['computation']['computation_id']
    assert second['objects']['computation']['execution_id']!=objects['computation']['execution_id']


def test_unknown_device_filter_retains_raw_but_not_calibrated(specimen):
    r,_=review(specimen,lambda p:p.update(filter_id='unreviewed-filter.v1'))
    assert r['capabilities']['record_raw']['allow']
    assert not r['capabilities']['record_calibrated']['allow']
    assert not r['checks']['filter_declaration']


@pytest.mark.parametrize('missing',['specification_nm','procedure_id'])
def test_missing_context_closes_inspection_not_raw(specimen,missing):
    r,_=review(specimen,policy_change=lambda p:p.update({missing:None,'require_sp1_functions':[]}))
    assert not r['capabilities']['generate_inspection_record']['allow']
    assert r['capabilities']['record_raw']['allow']


def test_reviewed_draft_inspection_is_never_part_release(specimen):
    r,raw=review(specimen,policy_change=lambda p:p.update(require_sp1_functions=[]))
    effect=w.functional_result(r,raw,'generate_inspection_record')
    assert effect['released'] is False and effect['equipment_authority'] is False
    assert effect['physical_validation']=='not_established'


@pytest.mark.parametrize('field,value',[('allowed_functions',['not-a-function']),
    ('require_sp1_functions',['record_raw','record_raw']),('max_time_uncertainty_ns',True),
    ('valid_until_ns',0),('device_public_key_b64','eA==')])
def test_malformed_host_policy_is_not_a_grant(specimen,field,value):
    policy=deepcopy(specimen[1]);policy[field]=value
    with pytest.raises(ValueError):w.validate_policy(policy)


def test_packet_cannot_supply_a_reusable_proof_verification_receipt(specimen):
    packet=deepcopy(specimen[0])
    packet['proof']={'schema':'ciw.device-boundary-sp1.v1','bytes_b64':'eA==','verified':True}
    r=w.evaluate(b.encode(packet),specimen[1],NOW)
    assert r['proof']['status']=='failed' and not r['capabilities']['generate_inspection_record']['allow']


def test_actual_sp1_adapter_missing_runtime_refuses_before_proving(tmp_path):
    binding={'schema':'ciw.device-boundary-sp1-binding.v1','scr_root':str(tmp_path),'scr_revision':'a'*40,'source_root':'.',
        'python':sys.executable,'python_sha256':'0'*64,'python_version':'3.13.5','dependencies':{},
        'host':str(tmp_path/'missing-host'),'host_sha256':'0'*64,
        'guest':str(tmp_path/'missing-guest'),'guest_sha256':'0'*64}
    with pytest.raises(ValueError,match='missing'):ExistingSP1Verifier(binding)
