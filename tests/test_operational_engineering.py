"""Connected operational journeys and deliberately adverse evidence cases."""
from copy import deepcopy
from pathlib import Path
import json
import os
import subprocess
import sys
import time

import pytest

from ciw import link_contract as c
from ciw.agent_api import encode, parse
from ciw.control_contracts import record
from ciw.operations.runner import seal
from ciw.operational_agent import OperationalHost
from ciw.operational_cli import prepare, example, default_context, configuration, default_template
from ciw.operational_context import make_binding, query, rows, attribution, config_diff
from ciw.operational_collection import Collector, workload_gate
from ciw.operational_store import Records
from ciw.link_store import Journal
from ciw.reference_comparison import example_request


def sources():
    v=example_request()['right']
    return {'recorded':{'raw':encode(v),'family':'observations',
        'context':default_context(clock='synthetic.clock',origin='synthetic'),
        'configurations':[configuration('before',{'power':10,'fan':1,'unit':'W'},clock='synthetic.clock'),
            configuration('after',{'power':20,'fan':2,'unit':'W'},effective=(0.5,0.5),clock='synthetic.clock')]}}


def profile(tmp_path, *, case='pass', configured=None, name='personal'):
    request=example_request(case)
    src=sources() if configured is None else configured
    src['recorded']['raw']=encode(request['right'])
    return prepare(tmp_path,investigation_id=name,sources=src,
        models={'assessment':{'source':'recorded','engine':'reference','request':encode(request),'policy':{}}},
        templates={'resolve':default_template()})


def selection(**changes):
    return {'source':'recorded','clock':'synthetic.clock','boot':'boot-1','channel':'temperature',
            'start':0.0,'end':2.0,**changes}


def execute(h, attempt='first'):
    return h.call('net_execute',{'source':'assessment','graph':'assessment-graph','attempt':attempt})


def test_complete_journeys_reopen_retry_and_explicit_fresh_replay(tmp_path):
    p=profile(tmp_path)
    with OperationalHost(p) as h:
        cat=h.call('oe_catalog',{}); assert cat['sources']['recorded']['model_status']=='available'
        a=h.call('oe_query',selection(end=0.5));b=h.call('oe_query',selection(start=0.5))
        change=h.call('oe_change',{'baseline':a['record'],'changed':b['record']})
        assert change['data']['status']=='RECORDED_ASSOCIATION'
        assert len(change['data']['confounders'])==2
        assert change['data']['response']['difference']==1.0
        row=a['data']['rows'][0]
        trace=h.call('oe_trace',{'source':'recorded','observation':row['id']})
        assert trace['source_ref']==cat['sources']['recorded']['original_bytes']
        first=execute(h);assert first['status']=='completed'
        finding=h.call('oe_model_support',{'attempt':'first'})
        assert finding['data']['status']=='AGREEMENT'
        prop=h.call('oe_propose',{'finding':finding['record'],'template':'resolve'})
        assert prop['data']['executed'] is False and prop['data']['approval']=='not_granted'
        note=h.call('oe_annotation',{'record':finding['record'],'text':'Review context <script>untrusted()</script>'})
        assert note['data']['alarm_reset'] is False
    with OperationalHost(p,resume=True) as h:
        again=execute(h)
        assert again['reused_response'] and again['result_ids']==first['result_ids']
        assert h.call('oe_model_support',{'attempt':'first'})['record']==finding['record']
        assert h.call('oe_inspect',{'record':prop['record']})['data']==prop['data']
        fresh=h.call('net_replay',{'original_attempt':'first','new_attempt':'fresh'})
        assert fresh['status']=='completed' and set(fresh['execution_ids']).isdisjoint(first['execution_ids'])
        assert set(fresh['result_ids']).isdisjoint(first['result_ids'])


@pytest.mark.parametrize('case,status',[('pass','AGREEMENT'),('fail','DISAGREEMENT'),('unknown','INSUFFICIENT_EVIDENCE')])
def test_predeclared_joint_policy_outcomes(tmp_path,case,status):
    with OperationalHost(profile(tmp_path,case=case)) as h:
        assert execute(h)['status']=='completed'
        finding=h.call('oe_model_support',{'attempt':'first'})['data']
        assert finding['status']==status
        assert finding['operation_id'] and finding['execution_id']!=finding['result_id']
        assert finding['comparison_ref']!=finding['prediction_ref']!=finding['observation_ref']
        assert finding['authority']['physical_qualification']=='not_established'


def test_uncertain_boundary_missing_configuration_and_unknown_sample_uncertainty_hold():
    src=sources()['recorded'];ctx=src['context']
    src['configurations'][1]['effective']=[0.5,1.5]
    binding=make_binding(src['raw'],src['family'],ctx,src['configurations'])
    q=query(src['raw'],binding,**{k:v for k,v in selection().items() if k!='source'})
    assert q['rows'][0]['configuration']['configuration']=='before'
    assert q['rows'][1]['configuration']['status']=='HELD'
    binding['context']['time_uncertainty']=None
    assert all(attribution(r,binding)['status']=='UNKNOWN' for r in q['rows'])
    binding['context']['time_uncertainty']=0.0;binding['configurations']=[]
    assert attribution(q['rows'][0],binding)['configuration'] is None


def test_contradictory_configuration_events_and_unit_diff():
    src=sources()['recorded'];src['configurations'][0]['effective']=[0,2]
    binding=make_binding(src['raw'],src['family'],src['context'],src['configurations'])
    q=query(src['raw'],binding,**{k:v for k,v in selection().items() if k!='source'})
    assert all(r['configuration']['status']=='HELD' for r in q['rows'])
    left=configuration('a',{'same':1,'changed':1,'absent':2,'unit':'K','typed':1})
    right=configuration('b',{'same':1,'changed':2,'unit':'degC','typed':'1','new':3})
    statuses={r['field']:r['status'] for r in config_diff(left,right)['fields']}
    assert statuses=={'/same':'unchanged','/changed':'changed','/absent':'absent_right','/unit':'changed','/typed':'incomparable','/new':'absent_left'}


def test_clock_boot_selection_quality_gaps_and_pagination():
    src=sources()['recorded'];src['context']['gaps']=[{'start':0.3,'end':0.7,'reason':'disconnect'}]
    binding=make_binding(src['raw'],src['family'],src['context'],src['configurations'])
    q=query(src['raw'],binding,**{k:v for k,v in selection().items() if k!='source'},limit=1)
    assert q['total']==2 and q['next_offset']==1 and len(q['rows'])==1
    assert q['coverage']['gaps'] and 'historical' in q['coverage']['freshness']
    assert query(src['raw'],binding,**{k:v for k,v in selection(boot='boot-2').items() if k!='source'})['total']==0
    assert query(src['raw'],binding,**{k:v for k,v in selection(clock='new-clock').items() if k!='source'})['total']==0
    assert query(src['raw'],binding,**{k:v for k,v in selection(start=10,end=20).items() if k!='source'})['rows']==[]


def test_shared_error_keeps_full_joint_matrix_no_uncertainty_reduction(tmp_path):
    request=example_request();joint=request['policy']['uncertainty']['joint_covariance']
    # Covariance provenance is retained across both sides, including common-mode components.
    from ciw.core.covariance import create_covariance_artifact
    joint=create_covariance_artifact(matrix=[[0.01]*4 for _ in range(4)],quantity_ids=joint['quantity_ids'],units=joint['units'],
        frame=joint['frame'],reference_values=joint['reference_values'],method=joint['method'],basis=joint['basis'],
        provenance=joint['provenance'],assumptions=['fully correlated software fixture'])
    request['policy']['uncertainty']['joint_covariance']=joint
    p=prepare(tmp_path,investigation_id='correlated',sources=sources(),models={'assessment':{
        'source':'recorded','engine':'reference','policy':{},'request':encode(request)}},templates={'resolve':default_template()})
    with OperationalHost(p) as h:
        assert execute(h)['status']=='completed'
        result=h.call('oe_model_support',{'attempt':'first'})['data']
        assert result['policy']['uncertainty']['joint_covariance']['matrix']==[[0.01]*4 for _ in range(4)]
        assert result['outcome']['difference_covariance']==[[0.0,0.0],[0.0,0.0]]


def test_new_admission_invalidates_proposal_without_refreshing_old_inputs(tmp_path):
    p=profile(tmp_path)
    with OperationalHost(p) as h:
        execute(h);f=h.call('oe_model_support',{'attempt':'first'})
        prop=h.call('oe_propose',{'finding':f['record'],'template':'resolve'})
        old=deepcopy(h.call('oe_catalog',{})['sources'])
        changed=sources();changed['recorded']['configurations'][0]['revision']='2'
        new=profile(tmp_path,configured=changed)
        assert new!=p
        assert h.call('oe_inspect',{'record':prop['record']})['applicability'].startswith('STALE')
        assert h.call('oe_catalog',{})['sources']==old
        assert execute(h)['reused_response']


@pytest.mark.parametrize('name,args',[
    ('oe_query',selection(source='https://device')),('oe_query',selection(start=True)),
    ('oe_query',selection(provider='arbitrary')),('oe_query',selection(limit=33)),
    ('net_execute',{'source':'assessment','graph':'assessment-graph','attempt':'../../shell'}),
    ('oe_propose',{'finding':'sha256:'+'0'*64,'template':'../../grant'}),
    ('unknown_shell',{'command':'anything'})])
def test_capability_escalation_refused_before_dispatch(tmp_path,name,args,monkeypatch):
    p=profile(tmp_path)
    from ciw import operational_engineering as eng
    monkeypatch.setattr(eng,'assess',lambda *a:pytest.fail('Invalid request dispatched a numerical operation'))
    with OperationalHost(p) as h:
        with pytest.raises((ValueError,PermissionError)):h.call(name,args)
        assert not h.agent._attempts


def test_wrong_investigation_and_revocation(tmp_path):
    p=profile(tmp_path/'a',name='one');q=profile(tmp_path/'b',name='two')
    with OperationalHost(p) as one,OperationalHost(q) as two:
        result=one.call('oe_query',selection())
        with pytest.raises(ValueError):two.call('oe_inspect',{'record':result['record']})
        from ciw.operational_cli import main
        assert main(['revoke','--profile',str(p)])==0
        with pytest.raises(PermissionError):one.call('oe_catalog',{})
        assert two.call('oe_catalog',{})['status']=='RETAINED'


def test_retained_inspection_disabled_providers_and_missing_model(tmp_path,monkeypatch):
    p=prepare(tmp_path,investigation_id='inspection',sources=sources(),grants=['inspect','derive','prepare'])
    from ciw import operational_engineering as eng
    monkeypatch.setattr(eng,'assess',lambda *a:pytest.fail('Read contacted provider'))
    with OperationalHost(p) as h:
        cat=h.call('oe_catalog',{})
        assert cat['sources']['recorded']['model_status']=='unsupported_or_provider_unavailable'
        assert h.call('oe_query',selection())['status']=='RETAINED'
        with pytest.raises(PermissionError):execute(h)
    with OperationalHost(p,resume=True) as h:assert h.call('oe_catalog',{})['device_contact'] is False


def test_thermal_actual_prediction_native_receipt_and_unknown_joint_budget(tmp_path):
    raw=(Path(__file__).parents[1]/'examples/measured-thermal/source.json').read_bytes()
    native=parse(raw)
    policy={'coverage_factor':2.0,'max_abs_error':1.0,'max_standard_uncertainty':1.0,'uncertainty':{
        'status':'unknown','scope':'unknown','relationship':'unknown','matrix':None,
        'justification':'No total joint budget is available','budget_ref':None}}
    p=prepare(tmp_path,investigation_id='thermal',sources={'thermal':{'raw':raw,'family':'measured-thermal',
        'context':default_context(clock=native['clock']['id'],origin=native['origin'],time_uncertainty=native['clock']['uncertainty_s']),
        'configurations':[configuration(native['configuration_id'],native['request']['model'],clock=native['clock']['id'])]}},
        models={'assessment':{'source':'thermal','engine':'thermal','policy':policy,'request':None}},templates={'resolve':default_template()})
    with OperationalHost(p) as h:
        result=execute(h);assert result['status']=='completed',result
        finding=h.call('oe_model_support',{'attempt':'first'})['data']
        assert finding['status']=='INSUFFICIENT_EVIDENCE'
        assert finding['model']['prediction_mode']=='shell_conditioned'
        assert finding['outcome']['samples']
    with OperationalHost(p,resume=True) as h:assert execute(h)['result_ids']==result['result_ids']


def test_bytes_retained_exact_and_corruption_refused(tmp_path):
    path=tmp_path/'journal.sqlite';raw=b'{ "unaltered": true }\n \n'
    with Journal(path,create=True) as db:
        records=Records(db);ref=records.put_bytes(raw)
        assert records.bytes(ref)==raw
        chunk=db.db.execute("SELECT namespace FROM state WHERE namespace LIKE 'oe-chunk:%'").fetchone()[0]
        item=db.state(chunk);db.compare_and_set_state(chunk,item['revision'],c.packed(b'altered',24576))
        with pytest.raises(ValueError):records.bytes(ref)


@pytest.mark.parametrize('temperature',[80,81,None,'invalid',float('nan'),-1])
def test_gpu_cutoff_blocks_equality_and_invalid_without_launch(temperature):
    assert workload_gate(temperature)['status']=='BLOCKED'
    assert workload_gate(temperature)['workload_launched'] is False


def test_collector_quota_stop_retry_accounting_and_explicit_restart(tmp_path):
    with Journal(tmp_path/'journal.sqlite',create=True) as db:
        collector=Collector(db,'reader',max_segments=3,max_bytes=5,cadence_s=0)
        state=collector.run(lambda i:b'abc',source_identity={'adapter':'test_fixture'})
        assert state['status']=='QUOTA_STOP' and len(state['segments'])==1 and state['bytes']==3
        assert state['gaps'][0]['discarded_byte_count']==3
        original=state['occurrence']
        with pytest.raises(ValueError):collector.run(lambda i:b'a',source_identity={'adapter':'test_fixture'})
        state=collector.run(lambda i:b'a',source_identity={'adapter':'test_fixture'},restart=True)
        assert state['occurrence']!=original and state['previous_occurrence']==original
        assert state['status']=='LIMIT_CLOSED'
        assert len(Records(db).catalog('ncp.operational-collection-receipt.v1'))==2


def test_collector_disconnect_stop_and_no_reconnect_on_inspection(tmp_path):
    with Journal(tmp_path/'journal.sqlite',create=True) as db:
        collector=Collector(db,'reader',cadence_s=0)
        def failed(i):raise OSError('disconnected')
        state=collector.run(failed,source_identity={'adapter':'test_fixture'})
        assert state['status']=='UNAVAILABLE' and state['gaps']
        assert collector.recover()==state
    with Journal(tmp_path/'journal.sqlite') as db:
        collector=Collector(db,'reader',cadence_s=0)
        state=collector.state()
        assert state['status']=='UNAVAILABLE'


def test_provider_process_kill_is_unknown_and_never_repeated(tmp_path):
    p=profile(tmp_path)
    code='''import os,sys
from ciw import operational_engineering as e
from ciw.operational_agent import OperationalHost
def die(*args): os._exit(77)
e.assess=die
h=OperationalHost(sys.argv[1])
h.call('net_execute',{'source':'assessment','graph':'assessment-graph','attempt':'interrupted'})
'''
    run=subprocess.run([sys.executable,'-c',code,str(p)],env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'src')))
    assert run.returncode==77
    with OperationalHost(p,resume=True) as h:
        result=execute(h,'interrupted')
        assert result['status']=='incomplete' and result['execution_outcome']=='unknown' and result['reused_response']
        assert h.call('oe_model_support',{'attempt':'interrupted'})['data']['status']=='INSUFFICIENT_EVIDENCE'


def test_collector_process_kill_recovery_retains_unknown_without_source_access(tmp_path):
    journal=tmp_path/'journal.sqlite'
    with Journal(journal,create=True):pass
    code='''import os,sys
from ciw.link_store import Journal
from ciw.operational_collection import Collector
with Journal(sys.argv[1]) as db:
 c=Collector(db,'reader',cadence_s=0)
 def die(i):os._exit(78)
 c.run(die,source_identity={'adapter':'test_fixture'})
'''
    run=subprocess.run([sys.executable,'-c',code,str(journal)],env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'src')))
    assert run.returncode==78
    with Journal(journal) as db:
        c=Collector(db,'reader',cadence_s=0)
        assert c.state()['status']=='RUNNING'
        assert c.recover()['status']=='INTERRUPTED_UNKNOWN'
        assert c.state()['automatic_replay'] is False


def test_native_link_adapter_preserves_report_receipt_semantics_and_history(tmp_path):
    from ciw.link_profiles import example_manifest,example_observations
    from ciw.link_protocol import encode as wire
    from ciw.link_gateway import receive,capture
    from ciw.evidence_history import inspect_link_journal
    device=example_manifest();observations=example_observations(device,rounds=2)
    path=tmp_path/'link.sqlite'
    with Journal(path,create=True) as db:
        frames=[wire('manifest',c.bounded(device))]+[wire('observation',c.bounded(r)) for r in observations]
        receive(frames,db,expected_manifest=device)
        first=db.append(frames[1],expected_manifest=device)
        retry=db.append(frames[1],expected_manifest=device)
        assert first==retry
        raw=encode(capture(db,expected_manifest=device,boot_id='synthetic.boot.1'))
    report=inspect_link_journal(path)
    assert report['evidence_consistency']=='NO_VIOLATION_FOUND'
    binding=make_binding(raw,'link-capture',default_context(producer=device['device_id'],boot='synthetic.boot.1',
        clock=device['clock']['id'],origin='synthetic'),[])
    projected=rows(raw,binding)
    assert len(projected)==len(observations) and len({r['id'] for r in projected})==len(projected)
    assert all(r['time'] is None for r in projected)
    assert all(r['lineage']['clock_semantics']['timestamp_event']=='instrument_report_receipt' for r in projected)
    assert all(r['receipt'] is not None for r in projected)


def test_native_calibrated_window_adapter_uses_device_clock_and_original_joint_covariance():
    raw=(Path(__file__).parents[1]/'examples/calibrated-window/source.json').read_bytes()
    v=parse(raw);binding=make_binding(raw,'calibrated-window',default_context(clock=v['device_clock']['clock_id']),[])
    projected=rows(raw,binding)
    assert len(projected)==len(v['samples'])
    assert projected[0]['lineage']['joint_covariance']==v['joint_covariance']
    assert projected[0]['value']==v['samples'][0]['indicated_value']
    assert projected[0]['lineage']['raw_value']==v['samples'][0]['raw_value']
    assert projected[0]['frame']==v['frame']['id']


def test_native_measurement_bundle_preserves_interpretation_and_unknown_time(tmp_path):
    from ciw.link_study_cli import example as study_example
    from ciw import link_study_io,link_measurements
    request=study_example();link_study_io.run(request,tmp_path/'study',raw=encode(request))
    bundle=link_measurements.build(tmp_path/'study')
    binding=make_binding(encode(bundle),'measurement-bundle',default_context(),[])
    projected=rows(encode(bundle),binding)
    assert len(projected)==6 and len({r['id'] for r in projected})==6
    assert all(r['time'] is None for r in projected)
    assert projected[1]['lineage']['provenance']['result_id']!=projected[0]['lineage']['provenance']['result_id']
    assert projected[1]['uncertainty']['covariance_ref']==bundle['native']['covariance']['covariance_id']


def test_equal_values_distinct_occurrences_and_duplicate_delivery():
    from ciw.adapters.oscillator import OscillatorAdapter
    # Native observation content equality is delivery identity; differing occurrence IDs preserve equal values.
    request=example_request();a=deepcopy(request['right']['observations'][0]);b=deepcopy(a)
    b['identity']['execution_id']='distinct-occurrence';b=seal(b)
    stream=record('observation-stream',observations=[a,b])
    raw=encode(stream);binding=make_binding(raw,'observations',default_context(clock='synthetic.clock',origin='synthetic'),[])
    q=query(raw,binding,clock='synthetic.clock',boot='boot-1',channel='temperature',start=0,end=1)
    assert q['total']==2 and q['coverage']['duplicate_deliveries']==0
    assert len({r['value'] for r in q['rows']})==1
    duplicate=record('observation-stream',observations=[a,a,b])
    with pytest.raises(ValueError):make_binding(encode(duplicate),'observations',binding['context'],[])


def test_gpu_context_has_no_invented_sample_time_and_retains_null_errors():
    reading={'read_start_ns':10,'read_end_ns':20,'utc_start_ns':100,'utc_end_ns':110,'status':'error',
        'energy_mj':None,'error_code':13,'power_mw':None,'temperature_c':81,'graphics_clock_mhz':300,'context_errors':{'power_mw':13}}
    raw=encode({'schema':'ncp.nvml-recording.v1','sensor':{'device_uuid':'fixture-GPU','driver_version':'fixture'},
        'host':'worker-1','boot':'boot-2','readings':[reading]})
    binding=make_binding(raw,'gpu-telemetry',default_context(acquisition_host='worker-1',boot='boot-2',origin='recorded'),[])
    projected=rows(raw,binding)
    assert all(r['time'] is None for r in projected)
    assert next(r for r in projected if r['channel']=='energy_mj')['quality']=='missing'
    assert next(r for r in projected if r['channel']=='temperature_c')['value']==81
    assert all(r['lineage']['host_call_bracket']==reading for r in projected)


def test_collector_external_stop_and_closed_segment_admission(tmp_path):
    from ciw.operational_cli import admit_segment
    src=sources()['recorded'];path=tmp_path/'operational.sqlite'
    with Journal(path,create=True) as db:
        collector=Collector(db,'reader',cadence_s=0.001)
        state=collector.run(lambda i:src['raw'],source_identity={'adapter':'retained_fixture'},wait=lambda _:collector.request_stop())
        assert state['status']=='STOPPED' and len(state['segments'])==1
        segment=state['segments'][0]
    profile_path=admit_segment(tmp_path/'host',journal=path,segment=segment,family='observations',
        context=src['context'],configurations=src['configurations'],investigation_id='segment')
    with OperationalHost(profile_path) as host:
        binding=host.call('oe_catalog',{})['sources']['segment']['binding']
        with Journal(path) as db:assert Records(db).get(binding)['parent_receipts']==[segment]


def test_changed_finding_invalidates_prior_proposal(tmp_path):
    with OperationalHost(profile(tmp_path)) as h:
        execute(h);f=h.call('oe_model_support',{'attempt':'first'})
        p=h.call('oe_propose',{'finding':f['record'],'template':'resolve'})
        h.call('net_replay',{'original_attempt':'first','new_attempt':'new'})
        newer=h.call('oe_model_support',{'attempt':'new'})
        assert newer['record']!=f['record']
        assert h.call('oe_inspect',{'record':p['record']})['applicability']=='STALE_FINDING'
        assert h.call('oe_model_support',{'attempt':'first'}) == f
        assert h.call('oe_inspect',{'record':p['record']})['applicability']=='STALE_FINDING'


def test_identical_record_publication_has_one_winner_across_journals(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / 'publication.sqlite'
    with Journal(path, create=True):
        pass
    def publish(_):
        with Journal(path) as db:
            return Records(db).publish({'schema': 'review.fixture.v1', 'value': 42})
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(publish, range(8)))
    assert len({ref for ref, _ in replies}) == 1
    assert sum(created for _, created in replies) == 1


def test_missing_finding_head_does_not_gain_applicability_on_retry(tmp_path, monkeypatch):
    with OperationalHost(profile(tmp_path)) as h:
        execute(h)
        original = Journal.compare_and_set_state
        def interrupted(db, namespace, *args, **kwargs):
            if namespace.startswith('oe-finding-head:'):
                raise ValueError('interrupted before head publication')
            return original(db, namespace, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Journal, 'compare_and_set_state', interrupted)
            with pytest.raises(ValueError, match='interrupted'):
                h.call('oe_model_support', {'attempt': 'first'})
        finding = h.call('oe_model_support', {'attempt': 'first'})
        proposal = h.call('oe_propose', {'finding': finding['record'], 'template': 'resolve'})
        assert h.call('oe_inspect', {'record': proposal['record']})['applicability'] == 'STALE_FINDING'


def test_historical_finding_retry_cannot_revive_an_approved_command(tmp_path):
    from ciw.link_profiles import example_manifest, example_observations
    from ciw.link_protocol import encode as wire
    from ciw.link_gateway import receive, capture
    from ciw.operational_supervision import approve_command
    device = example_manifest()
    device['clock']['timestamp_event'] = 'sensor_sample'
    device = c.manifest(device)
    with Journal(tmp_path / 'source.sqlite', create=True) as db:
        receive([wire('manifest', c.bounded(device))] + [wire('observation', c.bounded(row))
            for row in example_observations(device, rounds=2)], db, expected_manifest=device)
        raw = encode(capture(db, expected_manifest=device, boot_id='synthetic.boot.1'))
    replacement = deepcopy(device['configuration'])
    replacement['revision'] += 1
    replacement['ranges']['CH0'] = [0, 30]
    p = prepare(tmp_path / 'host', investigation_id='stale-command', sources={'link': {
        'raw': raw, 'family': 'link-capture', 'configurations': [],
        'context': default_context(producer=device['device_id'], boot='synthetic.boot.1',
            clock=device['clock']['id'], origin='synthetic')}}, templates={'resolve': default_template()},
        supervision={'alarms': {}, 'configuration_candidates': {'range-change': {
            'source': 'link', 'new_configuration': replacement, 'expiry_duration_s': 300,
            'required_proposal_template': 'resolve'}}}, grants=['inspect', 'derive', 'prepare', 'supervise'])
    with OperationalHost(p) as h:
        base = {'source': 'link', 'channel': 'CH0', 'clock': device['clock']['id'], 'boot': 'synthetic.boot.1'}
        a = h.call('oe_query', {**base, 'start': 1_000_000, 'end': 1_010_000})
        b = h.call('oe_query', {**base, 'start': 1_010_000, 'end': 1_020_000})
        args = {'baseline': a['record'], 'changed': a['record']}
        finding = h.call('oe_change', args)
        proposal = h.call('oe_propose', {'finding': finding['record'], 'template': 'resolve'})
        command = h.call('oe_command_prepare', {'proposal': proposal['record'], 'candidate': 'range-change'})
        approve_command(p, command['record'], expected_ref=command['record'], approval_ref='review-1',
            principal='operator', expires_ns=time.time_ns() + 60_000_000_000)
        h.call('oe_change', {'baseline': a['record'], 'changed': b['record']})
    with OperationalHost(p, resume=True) as h:
        for _ in range(2):
            assert h.call('oe_change', args) == finding
            assert h.call('oe_inspect', {'record': proposal['record']})['applicability'] == 'STALE_FINDING'
            with pytest.raises(ValueError, match='Proposal is stale'):
                h.call('oe_command_dispatch', {'command': command['record'], 'attempt': 'apply-1'})
        with h._records() as records:
            assert not any(records.journal.db.execute(
                "SELECT 1 FROM state WHERE namespace LIKE 'oe-command-attempt:%'"))


def test_alarm_fresh_stale_missing_acknowledgment_and_no_equipment_reset(tmp_path):
    rules={'high-temperature':{'source':'recorded','channel':'temperature','clock':'synthetic.clock','boot':'boot-1',
        'high':300.0,'max_age':2.0,'semantics':'engineering_review_only'}}
    p=prepare(tmp_path,investigation_id='alarms',sources=sources(),
        supervision={'alarms':rules,'configuration_candidates':{}},grants=['inspect','derive','acknowledge'])
    with OperationalHost(p) as h:
        q=h.call('oe_query',selection())
        active=h.call('oe_alarm_evaluate',{'query':q['record'],'rule':'high-temperature','as_of':2.0})
        assert active['data']['status']=='ACTIVE' and active['data']['stale'] is False
        stale=h.call('oe_alarm_evaluate',{'query':q['record'],'rule':'high-temperature','as_of':10.0})
        assert stale['data']['status']=='UNKNOWN' and stale['data']['stale'] is True
        ack=h.call('oe_alarm_acknowledge',{'alarm':active['record'],'note':'Reviewed; equipment protection remains independent.'})
        assert ack['data']['equipment_alarm_reset'] is False and ack['data']['alarm_condition_changed'] is False
        inspected=h.call('oe_alarm_inspect',{'alarm':active['record']})
        assert inspected['data']['status']=='ACTIVE' and inspected['acknowledgments'][0]['ref']==ack['record']
        with pytest.raises(PermissionError):h.call('oe_alarm_evaluate',{'query':q['record'],'rule':'arbitrary','as_of':2})


@pytest.mark.parametrize('approval_entry', ['function', 'cli'])
def test_supervisory_configuration_review_approval_dispatch_and_retry(tmp_path, approval_entry, capsys):
    from ciw.link_profiles import example_manifest,example_observations
    from ciw.link_protocol import encode as wire
    from ciw.link_gateway import receive,capture
    from ciw.operational_supervision import approve_command
    from ciw.link_runtime import DeviceRuntime
    device=example_manifest();observations=example_observations(device,rounds=2)
    path=tmp_path/'link.sqlite'
    with Journal(path,create=True) as db:
        receive([wire('manifest',c.bounded(device))]+[wire('observation',c.bounded(r)) for r in observations],db,expected_manifest=device)
        raw=encode(capture(db,expected_manifest=device,boot_id='synthetic.boot.1'))
    new=deepcopy(device['configuration']);new['revision']+=1;new['ranges']['CH0']=[0,10]
    src=sources();src['link']={'raw':raw,'family':'link-capture','context':default_context(producer=device['device_id'],
        boot='synthetic.boot.1',clock=device['clock']['id'],origin='synthetic'),'configurations':[]}
    request=example_request()
    p=prepare(tmp_path/'host',investigation_id='supervised',sources=src,models={'assessment':{
        'source':'recorded','engine':'reference','request':encode(request),'policy':{}}},templates={'resolve':default_template()},
        supervision={'alarms':{},'configuration_candidates':{'range-change':{'source':'link','new_configuration':new,
            'expiry_duration_s':300,'required_proposal_template':'resolve'}}},
        grants=['inspect','derive','numerical','prepare','supervise'])
    with OperationalHost(p) as h:
        execute(h)
        unrelated=h.call('oe_model_support',{'attempt':'first'})
        unrelated_proposal=h.call('oe_propose',{'finding':unrelated['record'],'template':'resolve'})
        with pytest.raises(ValueError,match='candidate source'):
            h.call('oe_command_prepare',{'proposal':unrelated_proposal['record'],'candidate':'range-change'})
        rows=h.call('oe_rows',{'source':'link'})['rows']
        first=rows[0]
        q=h.call('oe_query',{'source':'link','channel':first['channel'],'clock':first['clock'],
            'boot':first['boot'],'start':0,'end':1})
        f=h.call('oe_change',{'baseline':q['record'],'changed':q['record']})
        prop=h.call('oe_propose',{'finding':f['record'],'template':'resolve'})
        cmd=h.call('oe_command_prepare',{'proposal':prop['record'],'candidate':'range-change'})
        assert cmd['data']['approval']=='not_granted' and cmd['data']['dispatch']=='not_performed'
        with pytest.raises(PermissionError):h.call('oe_command_dispatch',{'command':cmd['record'],'attempt':'apply-1'})
        expires=time.time_ns()+60_000_000_000
        with pytest.raises(ValueError):approve_command(p,cmd['record'],expected_ref='sha256:'+'0'*64,
            approval_ref='review-1',principal='operator',expires_ns=expires)
        if approval_entry == 'cli':
            from ciw.operational_cli import main
            assert main(['approve-command', '--profile', str(p), '--command', cmd['record'],
                '--expected-ref', cmd['record'], '--approval-ref', 'review-1',
                '--principal', 'operator', '--expires-ns', str(expires)]) == 0
            assert json.loads(capsys.readouterr().out)['status'] == 'APPROVED_SIMULATED_CONFIGURATION'
        else:
            approve_command(p,cmd['record'],expected_ref=cmd['record'],approval_ref='review-1',principal='operator',expires_ns=expires)
        applied=h.call('oe_command_dispatch',{'command':cmd['record'],'attempt':'apply-1'})
        assert applied['status']=='completed' and applied['physical_dispatch']=='not_performed'
        assert applied['review']['plan_ref']==cmd['data']['plan']['plan_ref']
        assert applied['review']['manifest']['configuration']==new
        again=h.call('oe_command_dispatch',{'command':cmd['record'],'attempt':'apply-1'})
        assert again['reused_response'] and again['receipt']==applied['receipt']
    with OperationalHost(p,resume=True) as h:
        assert h.call('oe_command_dispatch',{'command':cmd['record'],'attempt':'apply-1'})['reused_response']
        assert h.call('oe_search',{'text':'model_support'})['matches']
        assert h.read_resource('ncp://operational-records/'+f['record'][7:])['record']==f['record']
        with pytest.raises(ValueError):h.read_resource('file:///etc/passwd')


def test_same_records_attach_existing_workbench_and_offline_view(tmp_path):
    from ciw.operational_view import snapshot,render_html,attach_workbench
    from ciw.session import Session
    with OperationalHost(profile(tmp_path/'host')) as h:
        q=h.call('oe_query',selection());execute(h);f=h.call('oe_model_support',{'attempt':'first'})
        view=snapshot(h);raw=render_html(view)
        assert f['record'] in json.dumps(view) and b'Historical snapshot' in raw
        assert b"connect-src 'none'" in raw
        workspace=h.profile.parent/h.spec['execution_output']/'first'/'workspace.json'
        output=tmp_path/'attached.json'
        attached=attach_workbench(h,workspace,output,recorded_at='2026-10-17T21:00:00Z')
        assert attached['status']=='RETAINED_IN_WORKBENCH' and attached['new_executions']==0
        original=json.loads(workspace.read_bytes());new=json.loads(output.read_bytes())
        assert original['results']==new['results'] and original['executions']==new['executions']
        restored=Session.from_workspace(output,output_dir=tmp_path/'restored')
        assert len(restored.results)==2


def test_elapsed_gauge_meter_keeps_proposals_synthetic_metrics_and_unknown_timestamps():
    raw=(Path(__file__).parent/'fixtures/operational/egm-synthetic-study.json').read_bytes()
    binding=make_binding(raw,'elapsed-gauge-study',default_context(origin='synthetic'),[])
    projected=rows(raw,binding)
    assert {r['channel'] for r in projected}=={'work','duration','energy'}
    assert all(r['origin']=='synthetic' and r['time'] is None for r in projected)
    assert all(r['lineage']['cross_record_comparison']=='unsupported_by_upstream_contract' for r in projected)
    assert all(r['lineage']['candidate']['kind']=='proposal' for r in projected)
    assert next(r for r in projected if r['channel']=='duration')['value']==2
    assert next(r for r in projected if r['channel']=='duration')['lineage']['native_observation']['duration']['basis']=='elapsed'
    q=query(raw,binding,clock='fixture.clock',boot='boot-1',channel='duration',start=0,end=10)
    assert q['rows']==[] and q['unplaced_count']==1
