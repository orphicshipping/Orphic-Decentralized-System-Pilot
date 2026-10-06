"""Actual Link/Session research journeys on explicitly synthetic installation data."""
from copy import deepcopy
from hashlib import sha256
import io as stdio
import json
import math
from pathlib import Path
import socket
import subprocess
import sys

import numpy as np
import pytest

from ciw import link_contract as c, link_installation as m, link_installation_io as io
from ciw.link_installation_example import fixture, demo
from ciw.link_protocol import encode, decode_one
from ciw.link_store import Journal
from ciw.link_gateway import capture
from ciw.operations.runner import seal


def bytes_json(value): return (json.dumps(value, indent=2, allow_nan=False)+'\n').encode()


@pytest.fixture(scope='module')
def study_request(tmp_path_factory):
    root = tmp_path_factory.mktemp('paired-source')
    device, plan, pr, records = fixture(compliant=True)
    with Journal(root/'journal', create=True) as journal:
        journal.append(encode('manifest', c.bounded(device)), expected_manifest=device)
        for record in records:
            journal.append(encode('observation', c.bounded(record)), expected_manifest=device)
        cap = capture(journal, expected_manifest=device, boot_id=plan['boot_id'])
    cr = bytes_json(cap)
    return m.prepare(pr, cr, expected_plan_ref=c.bytes_ref(pr), expected_capture_ref=c.bytes_ref(cr))


def changed(study_request, *, plan=None, row=None, device=None, rebind=False):
    original = deepcopy(study_request)
    p, cap, pr, cr = m.unpack_request(original)
    if plan: plan(p)
    if device:
        device(cap['manifest']); p['manifest_ref'] = c.content_identity(cap['manifest'])
        cap['manifest_wire'] = c.packed(encode('manifest', c.bounded(cap['manifest'])), 70000)
    pr = bytes_json(p)
    for i, f in enumerate(cap['frames']):
        value = c.loads(decode_one(c.unpack(f['wire'], 70000)).payload)
        if row: row(value, i)
        if device:
            value['manifest_ref'] = c.content_identity(cap['manifest'])
            value['configuration_ref'] = c.content_identity(cap['manifest']['configuration'])
        if rebind: value['procedure_ref'] = c.bytes_ref(pr)
        f['wire'] = c.packed(encode('observation', c.bounded(value)), 70000)
    # Preserve original formatting if the plan did not change.
    if not plan and not device: pr = c.unpack(original['plan'], m.MAX_PLAN)
    return {'schema': m.SCHEMA, 'plan': c.packed(pr, m.MAX_PLAN), 'capture': m.pack_capture(bytes_json(cap))}


@pytest.fixture(scope='module')
def investigation(tmp_path_factory, study_request):
    root = tmp_path_factory.mktemp('installation-run')/'investigation'
    result = io.run(study_request, root)
    assert result['status'] == 'RETAINED_RESPONSE_PREVIEW'
    return root, result


def test_existing_h1_kernel_used_unchanged_and_native_ancestry_preserved(study_request, monkeypatch):
    old = m.mount.analyze; seen = []
    def observe(req, **kw):
        seen.append(deepcopy(req)); return old(req, **kw)
    monkeypatch.setattr(m.mount, 'analyze', observe)
    result = m.analyze(study_request)
    paired = seen[0]
    assert len(seen) == 1 and len(paired['reference']) == len(paired['dut']) == 128
    assert paired['schema'] == m.mount.SCHEMA
    assert paired['reference'][0]['identity']['entity_id'] != paired['dut'][0]['identity']['entity_id']
    assert paired['reference'][0]['clock'] == paired['dut'][0]['clock']
    assert paired['mount_id'] == result['installation']['occurrence_id']
    for side in ('reference', 'dut'):
        assert all(x['provenance']['provider'] == 'ciw.link' for x in paired[side])
        assert all(x['uncertainty'] is None for x in paired[side])
    response = result['response']
    assert response['passing_sampled_frequencies_hz'] == [32., 64.]
    point = next(x for x in response['frequencies'] if x['frequency_hz'] == 384.)
    assert point['observations']['relative_gain']['value'] == pytest.approx(math.sqrt(1+0.24**2)/0.24, abs=2e-13)
    assert point['observations']['coherence']['value'] == pytest.approx(1, abs=2e-14)
    assert 'GAIN_DEVIATION' in point['reasons']
    assert response['continuous_qualified_band_hz'] is None
    assert response['scope'] == m.mount.SCOPE  # Nested offline kernel claims not rewritten.
    assert result['scope'] == m.SCOPE


def test_replay_is_new_computation_not_new_measurement(investigation, tmp_path):
    root, old = investigation
    before = {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    new = io.replay(root, tmp_path/'replay', expected_request_ref=old['request_ref'])
    assert new['request_ref'] == old['request_ref'] and new['evidence_id'] == old['evidence_id']
    assert new['execution_id'] != old['execution_id'] and new['result_id'] != old['result_id']
    assert new['verification_id'] is None
    assert new['result']['data'] == old['result']['data']
    assert before == {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}


def test_exact_original_plan_capture_and_request_roundtrip(investigation, tmp_path, study_request):
    root, old = investigation
    restored = tmp_path/'originals'; io.restore(root, restored, expected_request_ref=old['request_ref'])
    assert (restored/'request.json').read_bytes() == (root/'request.json').read_bytes()
    assert (restored/'plan.json').read_bytes() == c.unpack(study_request['plan'], m.MAX_PLAN)
    assert (restored/'capture.json').read_bytes() == m.unpack_capture(study_request['capture'])


@pytest.mark.parametrize('part', ['plan', 'capture'])
def test_external_selection_refuses_mismatch_before_assembly(study_request, part):
    _, _, pr, cr = m.unpack_request(study_request)
    with pytest.raises(ValueError, match='external'):
        m.prepare(pr, cr, expected_plan_ref='sha256:'+'0'*64 if part=='plan' else c.bytes_ref(pr),
                  expected_capture_ref='sha256:'+'0'*64 if part=='capture' else c.bytes_ref(cr))


@pytest.mark.parametrize('mutate', [
    lambda p: p['installation'].update(occurrence_id='synthetic.remount.2'),
    lambda p: p['installation'].update(mount_revision='B'),
    lambda p: p['installation']['dut'].update(hardware_revision='B'),
    lambda p: p['analysis']['limits'].update(gain_error=1.0),
    lambda p: p['conditions'].update(temperature_K=350),
    lambda p: p.update(protocol=c.packed(b'Changed original procedure')),
])
def test_changed_plan_cannot_relabel_existing_acquisition(study_request, mutate, tmp_path):
    altered = changed(study_request, plan=mutate)  # Do NOT rebind historical observation bytes.
    result = io.run(altered, tmp_path/'refused')
    assert result['status'] == 'REFUSED' and result['result_id'] is None
    assert 'exact original plan' in result['execution']['refusal']['message']
    assert (tmp_path/'refused/request.json').is_file()


@pytest.mark.parametrize('kind', ['remount', 'hardware', 'fixture', 'temperature', 'threshold', 'timing'])
def test_applicability_requires_new_review_and_transfers_no_qualification(investigation, study_request, kind):
    root, old = investigation; p, _, pr, _ = m.unpack_request(study_request)
    if kind=='remount': p['installation']['occurrence_id']='synthetic.remount.2'
    elif kind=='hardware': p['installation']['dut']['hardware_revision']='B'
    elif kind=='fixture': p['installation']['fixture']=c.packed(b'New fixture')
    elif kind=='temperature': p['conditions']['temperature_K']=350.
    elif kind=='threshold': p['analysis']['limits']['gain_error']=.1
    else: p['analysis']['phase_skew_bound_s']=1e-5
    raw=bytes_json(p)
    check=io.applicability(root, raw, expected_request_ref=old['request_ref'], expected_plan_ref=c.bytes_ref(raw))
    assert check['status']=='RECHARACTERIZATION_REQUIRED' and check['changed_fields']
    assert check['same_declared_physical_unit'] is True
    assert check['same_declared_installation'] is (kind!='remount')
    assert check['qualification_transferred'] is False and check['new_scientific_execution'] is False


def test_unchanged_plan_does_not_claim_unchanged_physical_installation(investigation, study_request):
    root, old=investigation; _, _, pr, _=m.unpack_request(study_request)
    check=io.applicability(root,pr,expected_request_ref=old['request_ref'],expected_plan_ref=c.bytes_ref(pr))
    assert check['status']=='DECLARATIONS_UNCHANGED'
    assert check['qualification_transferred'] is False and check['scope']['physical_qualification']=='HELD'


@pytest.mark.parametrize('flag', ['sensor_fault', 'saturated', 'unrecognized_vendor_flag'])
def test_flags_preserve_data_and_prevent_fft(study_request, flag, monkeypatch, tmp_path):
    r=changed(study_request,row=lambda v,i: v.update(quality_flags=[flag]) if i==13 else None)
    monkeypatch.setattr(np.fft,'rfft',lambda *a,**kw:pytest.fail('Flagged source entered FFT'))
    result=io.run(r,tmp_path/'flagged')
    assert result['status']=='SOURCE_REVIEW_REQUIRED'
    data=result['result']['data'];assert data['response'] is None and data['scope']['physical_qualification']=='HELD'
    assert data['source_quality_flags'][0]['flags']==[flag]
    io.restore(tmp_path/'flagged',tmp_path/'raw',expected_request_ref=result['request_ref'])
    assert (tmp_path/'raw/capture.json').read_bytes()==m.unpack_capture(r['capture'])


@pytest.mark.parametrize('condition', ['unknown_timing','unknown_chain','skewed_timing'])
def test_missing_or_unfavorable_basis_never_creates_passing_points(study_request, condition):
    def edit(p):
        if condition=='unknown_timing':p['analysis']['phase_skew_bound_s']=None
        elif condition=='unknown_chain':p['analysis']['reference_response']='UNKNOWN';p['chain_evidence']=None
        else:p['analysis']['phase_skew_bound_s']=.1
    result=m.analyze(changed(study_request,plan=edit,rebind=True))
    assert result['response']['passing_sampled_frequencies_hz']==[]
    assert result['scope']['physical_qualification']=='HELD'


@pytest.mark.parametrize('problem', ['receipt_clock','wrong_unit','wrong_frame','wrong_transducer','unequal_rate'])
def test_no_silent_physical_reinterpretation(study_request, problem):
    def edit(d):
        if problem=='receipt_clock':d['clock']['timestamp_event']='instrument_report_receipt'
        elif problem=='wrong_unit':d['channels'][1]['unit']='g'
        elif problem=='wrong_frame':d['channels'][1]['frame']='rotated.axis'
        elif problem=='wrong_transducer':d['channels'][1]['transducer_id']='another.sensor'
        else:d['configuration']['sample_rate_hz']['DUT']=512.
    with pytest.raises(ValueError):m.analyze(changed(study_request,device=edit,rebind=True))


@pytest.mark.parametrize('problem', ['unequal_tick','equal_gap','missing_sample','null_tick','mismatched_context','mismatched_origin'])
def test_timing_and_source_discontinuities_refuse(study_request, problem):
    def edit(v,i):
        if problem=='unequal_tick' and i==9:v['device_tick']+=1
        elif problem=='equal_gap' and i>=128:v['device_tick']+=1
        elif problem=='missing_sample' and i>=128:v['sequence']+=1;v['measurement_id']=c.observation_id(v['device_id'],v['boot_id'],v['sequence'])
        elif problem=='null_tick' and i==9:v['device_tick']=None
        elif problem=='mismatched_context' and i==9:v['context']['asset_id']='other.asset'
        elif problem=='mismatched_origin' and i==9:v['origin']='recorded'
    with pytest.raises(ValueError):m.analyze(changed(study_request,row=edit))


@pytest.mark.parametrize('problem', ['self_reference','no_evidence','boolean_block','unset_limit','unknown_temperature'])
def test_plan_does_not_invent_independence_calibration_or_criteria(study_request, problem):
    p,_,_,_=m.unpack_request(study_request)
    if problem=='self_reference':p['installation']['reference']['unit_id']=p['installation']['dut']['unit_id']
    elif problem=='no_evidence':p['chain_evidence']=None
    elif problem=='boolean_block':p['analysis']['block_samples']=True
    elif problem=='unset_limit':p['analysis']['limits']['gain_error']=None
    else:p['conditions']['temperature_measurand']='sensor_temperature_maybe'
    with pytest.raises(ValueError):m.validate_plan(p)


@pytest.mark.parametrize('bad', ['reorder','delete','wrong_hash','too_many','bad_size'])
def test_byte_chunking_does_not_permit_reordered_or_resealed_input(study_request,bad):
    packed=deepcopy(study_request['capture'])
    if bad=='reorder':packed['chunks'].reverse()
    elif bad=='delete':packed['chunks'].pop()
    elif bad=='wrong_hash':packed['sha256']='sha256:'+'0'*64
    elif bad=='too_many':packed['chunks']*=100
    else:packed['byte_count']=True
    with pytest.raises(ValueError):m.unpack_capture(packed)


@pytest.mark.parametrize('action',['inspect','replay','restore','report','agent'])
def test_wrong_anchor_refuses_before_side_effects(investigation,tmp_path,action):
    root,_=investigation;out=tmp_path/'absent'
    with pytest.raises(ValueError,match='external'):
        getattr(io,action)(root,*([out] if action in {'replay','restore','report'} else []),expected_request_ref='sha256:'+'0'*64)
    assert not out.exists()


def test_no_clobber_and_no_network(investigation,tmp_path,monkeypatch):
    root,old=investigation
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('network accessed'))
    before={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    io.inspect(root,expected_request_ref=old['request_ref'])
    req=c.loads((root/'request.json').read_bytes(),m.MAX_BYTES)
    with pytest.raises(FileExistsError):io.run(req,root)
    assert before=={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}


def test_forged_response_with_resealed_envelopes_refuses(investigation,tmp_path):
    root,old=investigation;out=tmp_path/'tampered';out.mkdir()
    for name in ['request.json','workspace.json','graph.json']:(out/name).write_bytes((root/name).read_bytes())
    ws=json.loads((out/'workspace.json').read_bytes())
    # Locate the standard result object independent of workspace container layout.
    def damage(v):
        if isinstance(v,dict):
            if v.get('schema')=='ciw.operation-result.v1':
                v['data']['response']['frequencies'][0]['h_real']=99.;seal(v)
            else:
                for x in v.values():damage(x)
        elif isinstance(v,list):
            for x in v:damage(x)
    damage(ws);(out/'workspace.json').write_bytes(bytes_json(ws))
    with pytest.raises(ValueError):io.inspect(out,expected_request_ref=old['request_ref'])


def test_real_demo_one_command_no_qualified_claim(tmp_path):
    summary=demo(tmp_path/'demo')
    assert summary['status']=='RETAINED_RESPONSE_PREVIEW'
    assert summary['delivery']['new_acknowledgements']==257
    assert summary['delivery']['historical_acknowledgements']==257
    assert len(summary['passing_sampled_frequencies_hz'])==15
    page=(tmp_path/'demo/installation.html').read_text()
    assert 'p,h1,h2{overflow-wrap:anywhere}' in page
    assert "default-src 'none'" in page and '<script' not in page
    assert 'PHYSICAL QUALIFICATION HELD' in page and 'synthetic' in page


def test_mcp_uses_same_existing_server_frozen_snapshot_and_zero_grants(investigation,tmp_path):
    from ciw.agent_mcp import Server
    root,old=investigation
    host=io.agent(root,expected_request_ref=old['request_ref'])
    server=Server(host)
    a=server.handle(bytes_json({'jsonrpc':'2.0','id':1,'method':'initialize','params':
        {'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}}))
    assert 'result' in a
    assert server.handle(bytes_json({'jsonrpc':'2.0','method':'notifications/initialized'})) is None
    tools=server.handle(bytes_json({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}))
    assert 'result' in tools
    assert host.capabilities()['host_policy']['allowed_operations'] == []
    host.close()


def test_actual_stdio_mcp_inspection_and_denied_execution(investigation):
    root, old=investigation;rid=old['result_id']
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':
        {'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'installation-test','version':'1'}}},
        {'jsonrpc':'2.0','method':'notifications/initialized'},
        {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'net_inspect','arguments':
            {'artifact':'investigation','selector':['results',rid,'data','installation','occurrence_id']}}},
        {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'net_execute','arguments':
            {'source':'investigation','graph':'other','attempt':'test'}}}]
    proc=subprocess.run([sys.executable,'-m','ciw.link_installation_io','mcp',str(root),
        '--request-ref',old['request_ref']],input=b''.join(json.dumps(x).encode()+b'\n' for x in messages),
        capture_output=True,timeout=20)
    assert proc.returncode==0,proc.stderr.decode()
    rows=[json.loads(x) for x in proc.stdout.splitlines()]
    assert rows[1]['result']['structuredContent']['data']==old['installation_occurrence']
    assert rows[2]['result']['isError'] is True


def test_frozen_mcp_cannot_follow_later_file_replacement(investigation,tmp_path,monkeypatch):
    root,old=investigation
    with io.agent(root,expected_request_ref=old['request_ref']) as host:
        monkeypatch.setattr(io,'read_bytes',lambda *a,**k:pytest.fail('MCP reread input paths'))
        result=host.inspect('investigation',['results',old['result_id'],'data','installation'])
        assert result['data']['occurrence_id']==old['installation_occurrence']
        result['data']['occurrence_id']='forged'
        assert host.inspect('investigation',['results',old['result_id'],'data','installation'])['data']['occurrence_id']!='forged'


def test_explicit_prepare_run_and_restore_cli(investigation,tmp_path,capsys,study_request):
    _,_,pr,cr=m.unpack_request(study_request)
    (tmp_path/'plan.json').write_bytes(pr);(tmp_path/'capture.json').write_bytes(cr)
    assert io.main(['prepare','--plan',str(tmp_path/'plan.json'),'--plan-ref',c.bytes_ref(pr),
        '--capture',str(tmp_path/'capture.json'),'--capture-ref',c.bytes_ref(cr),
        '--output',str(tmp_path/'request.json')])==0
    ref=json.loads(capsys.readouterr().out)['request_ref']
    assert ref==c.bytes_ref((tmp_path/'request.json').read_bytes())
    assert io.main(['run',str(tmp_path/'request.json'),'--request-ref',ref,'--output-dir',str(tmp_path/'run')])==0
    assert io.main(['inspect',str(tmp_path/'run'),'--request-ref',ref])==0
    assert io.main(['report',str(tmp_path/'run'),'--request-ref',ref,'--output',str(tmp_path/'view.html')])==0
    assert io.main(['restore',str(tmp_path/'run'),'--request-ref',ref,'--output-dir',str(tmp_path/'restore')])==0
    assert (tmp_path/'restore/capture.json').read_bytes()==cr


def test_new_mount_gets_distinct_occurrence_without_new_physical_unit(tmp_path):
    a=fixture(occurrence='synthetic.install.1');b=fixture(occurrence='synthetic.install.2')
    assert a[0]==b[0]
    assert a[1]['installation']['dut']==b[1]['installation']['dut']
    assert a[1]['installation']['occurrence_id']!=b[1]['installation']['occurrence_id']
    assert a[3][0]['measurement_id']!=b[3][0]['measurement_id']
    assert a[3][0]['procedure_ref']!=b[3][0]['procedure_ref']


def test_shared_link_entrypoint_keeps_new_workload_bounded(tmp_path,capsys):
    from ciw.link_cli import main
    assert main(['installation','demo','--output-dir',str(tmp_path/'one-link')])==0
    summary=json.loads(capsys.readouterr().out)
    assert summary['status']=='RETAINED_RESPONSE_PREVIEW' and summary['scope']['hardware_access'] is False


def test_predeclared_block_count_detects_complete_tail_loss(study_request):
    # Eight 16-sample blocks are planned; losing complete blocks must not turn
    # an incomplete recording into an apparently valid four-block experiment.
    r=changed(study_request,plan=lambda p:p['analysis'].update(block_samples=16,replicate_blocks=8),rebind=True)
    cap=c.loads(m.unpack_capture(r['capture']),c.MAX_CAPTURE_BYTES)
    cap['frames']=cap['frames'][:128]
    r['capture']=m.pack_capture(bytes_json(cap))
    with pytest.raises(ValueError,match='complete paired'):
        m.analyze(r)


def test_fixed_link_record_budget_not_enlarged_to_accommodate_waveforms(study_request):
    plan,_,_,_=m.unpack_request(study_request)
    plan['analysis'].update(block_samples=32,replicate_blocks=8)
    with pytest.raises(ValueError,match='unchanged Link record budget'):m.validate_plan(plan)


def test_correlated_source_covariance_is_recoverable_not_claimed_as_spectral_uncertainty(study_request, tmp_path):
    from ciw.core.covariance import create_covariance_artifact
    from ciw.link_gateway import validate_capture
    p, cap, pr, _ = m.unpack_request(study_request)
    rows = validate_capture(cap)['observations']
    matrix = np.eye(len(rows)); matrix[0,1] = matrix[1,0] = -0.4
    covariance = create_covariance_artifact(matrix=matrix.tolist(),
        quantity_ids=[v['measurement_id'] for v in rows], units=[m.mount.UNIT]*len(rows),
        frame=m.mount.FRAME, reference_values=[v['value'] for v in rows],
        method='Declared synthetic correlated uncertainty', basis={'kind':'observation','id':c.bytes_ref(b'fixture')},
        provenance={'provider':'synthetic-test','source_evidence_ids':[c.bytes_ref(b'fixture')],'source_covariance_ids':[]},
        assumptions=['Synthetic only; not a sensor uncertainty budget'])
    cap['joint_covariance'] = covariance
    cr = bytes_json(cap)
    prepared = m.prepare(pr, cr, expected_plan_ref=c.bytes_ref(pr), expected_capture_ref=c.bytes_ref(cr))
    result = io.run(prepared, tmp_path/'with-covariance')
    assert result['status'] == 'RETAINED_RESPONSE_PREVIEW'
    data = result['result']['data']
    assert data['input_joint_covariance_ref'] == covariance['covariance_id']
    assert data['scope']['spectral_uncertainty'] == 'NOT_EVALUATED'
    assert all(o['uncertainty'] is None for f in data['response']['frequencies'] for o in f['observations'].values())
    io.restore(tmp_path/'with-covariance', tmp_path/'original', expected_request_ref=result['request_ref'])
    assert (tmp_path/'original/capture.json').read_bytes() == cr
    original_cov = json.loads((tmp_path/'original/capture.json').read_bytes())['joint_covariance']
    assert m.equal(original_cov, covariance) and original_cov['matrix'][0][1] == -0.4
