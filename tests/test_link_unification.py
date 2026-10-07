"""One Link, data profiles, and existing scientific contracts; all fixtures synthetic."""
from copy import deepcopy
from fractions import Fraction as F
import json
import socket

import pytest

from ciw import link_contract as c, link_profiles as p, link_semantics as sem
from ciw import link_study as s, link_study_io as io
from ciw.link_study_cli import example
from ciw.link_store import Journal
from ciw.link_runtime import DeviceRuntime
from ciw.link_gateway import capture
from ciw.link_cli import main
from ciw.operations.runner import seal


@pytest.mark.parametrize('canonical,legacy',list(p._SELECTORS.items())[:7])
def test_legacy_aliases_preserve_entire_manifest_and_observations(canonical,legacy):
    manifest=p.example_manifest(legacy)
    observations=p.example_observations(manifest)
    for selector in (canonical,legacy):
        new,rows=p.profile_examples(selector)
        assert c.bounded(new)==c.bounded(manifest)
        assert c.bounded(rows)==c.bounded(observations)
    assert c.PERSONAS==('GaugeLink','MachineLink','ConditionLink','FieldLink','ThermalLink','DAQLink','CalLink')


@pytest.mark.parametrize('name',['pressure','vibration','electrical'])
def test_new_profiles_share_actual_runtime_journal_session_and_replay(name,tmp_path):
    from ciw.link_protocol import encode
    from ciw import link_gateway as gateway,link_workflow as workflow
    device,rows=p.profile_examples(name)
    device_alias,alias_rows=p.profile_examples(p._SELECTORS[name])
    assert c.bounded(device_alias)==c.bounded(device) and c.bounded(alias_rows)==c.bounded(rows)
    stream=encode('manifest',c.bounded(device))+b''.join(encode('observation',c.bounded(x)) for x in rows)
    with Journal(tmp_path/'outbox',create=True) as a,Journal(tmp_path/'ingress',create=True) as b:
        gateway.receive([stream],a,expected_manifest=device)
        assert a.sync_to(b,manifests=[device])['new_acknowledgements']==4
        bound=capture(b,expected_manifest=device,boot_id='synthetic.boot.1')
    result=workflow.run(bound,tmp_path/'run')
    replay=workflow.replay(tmp_path/'run',tmp_path/'replay')
    assert result['status']=='RETAINED'
    assert result['evidence_id']==replay['evidence_id']
    assert result['execution_id']!=replay['execution_id']
    assert c.canonical_json(bound['manifest'])==c.canonical_json(device)
    assert all(x['origin']=='synthetic' for x in rows)


def view(device):
    raw=json.dumps(device,indent=3).encode()+b'\n'
    return p.manifest_view(raw,expected_ref=c.bytes_ref(raw))


def test_profile_predicates_follow_channels_not_product_persona():
    device=p.example_manifest('GaugeLink')
    device['channels'][0].update(quantity='temperature',unit='K')
    result=view(device)
    assert result['legacy_persona']=='GaugeLink'
    assert [x['profile'] for x in result['profile_matches'] if x['status']=='DECLARED_MATCH']==['thermal']
    assert result['qualification']['calibration']=='UNKNOWN'
    assert not any(result['authority'].values())


def test_one_device_can_match_pressure_and_thermal_and_types_do_not_collapse():
    result=view(p.example_manifest('MachineLink'))
    assert {x['profile'] for x in result['profile_matches'] if x['status']=='DECLARED_MATCH'}=={'pressure','thermal'}
    sets=result['sets']
    assert len(sets['devices'])==1 and len(sets['channels'])==2 and len(sets['quantities'])==2
    refs=[x['ref'] for records in sets.values() for x in records]
    assert len(set(refs))==len(refs)
    assert all(x['source'] in refs and x['target'] in refs for x in result['relations'])
    assert all(x['execution_authority'] is False for x in result['profile_matches'])


def test_duplicate_channel_labels_on_different_devices_are_not_one_channel():
    a=p.example_manifest(); b=deepcopy(a);b['device_id']='synthetic.another'
    assert view(a)['sets']['channels'][0]['ref']!=view(b)['sets']['channels'][0]['ref']


@pytest.mark.parametrize('quantity,unit,enabled,status',[
    ('temperature','K',True,'DECLARED_MATCH'),('temperature','C',True,'UNKNOWN'),
    ('temperature','K',False,'NO_MATCH'),('pressure','K',True,'NO_MATCH'),
    ('temperature_difference','degC',True,'DECLARED_MATCH'),
])
def test_three_valued_predicates(quantity,unit,enabled,status):
    device=p.example_manifest();device['channels'][0].update(quantity=quantity,unit=unit)
    if not enabled:
        device['channels'].append({**device['channels'][0],'id':'CH1','quantity':'voltage','unit':'V'})
        device['configuration']['enabled_channels']=['CH1']
        device['configuration']['sample_rate_hz']={'CH1':None}
        device['configuration']['ranges']={'CH1':None}
    result=view(device)
    assert next(x for x in result['profile_matches'] if x['profile']=='thermal')['status']==status
    assert not any(result['authority'].values())


@pytest.mark.parametrize('bad',[None,True,[],{},'unknown','Pressure','../thermal','thermal;run','GaugeLink '])
def test_unregistered_selector_never_loads_code(bad):
    with pytest.raises((ValueError,TypeError)):p.profile_examples(bad)


def test_exact_manifest_anchor_no_reseal_or_mutation():
    device=p.example_manifest();raw=c.bounded(device)
    with pytest.raises(ValueError):p.manifest_view(raw,expected_ref='sha256:'+'0'*64)
    result=p.manifest_view(raw,expected_ref=c.bytes_ref(raw))
    assert result['manifest_bytes_ref']==c.bytes_ref(raw)
    assert result['manifest_ref']==c.content_identity(device)
    result['sets']['channels'][0]['unit']='invented'
    assert view(device)['sets']['channels'][0]['unit']=='mm'


@pytest.mark.parametrize('raw',[b'',pytest.param(b' '*65537,id='over-manifest-byte-limit'),
                               'not bytes',b'{"duplicate":1,"duplicate":2}'])
def test_manifest_reader_bounds_and_strict_json(raw):
    with pytest.raises((ValueError,TypeError)):
        p.manifest_view(raw,expected_ref=c.bytes_ref(raw) if isinstance(raw,bytes) else 'sha256:'+'0'*64)


@pytest.mark.parametrize('selector',list(p._SELECTORS))
def test_cli_all_profiles_use_one_command(selector,tmp_path,capsys):
    assert main(['example','--profile',selector,'--output-dir',str(tmp_path/'example')])==0
    result=json.loads(capsys.readouterr().out)
    raw=(tmp_path/'example/manifest.json').read_bytes()
    assert result['status']=='CREATED_SYNTHETIC'
    assert main(['catalog','--manifest',str(tmp_path/'example/manifest.json'),'--manifest-ref',c.bytes_ref(raw)])==0
    assert json.loads(capsys.readouterr().out)['application']=='Link'


def test_catalog_one_runtime_no_automatic_drivers(capsys):
    assert main(['catalog'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['application']=='Link' and result['runtime']=='ciw.link_runtime.DeviceRuntime'
    assert len(result['profiles'])==7 and len(result['profile_selectors'])==10
    assert main(['catalog','--manifest-ref','sha256:'+'0'*64])==2


@pytest.fixture
def investigation(tmp_path):
    req=example();root=tmp_path/'study'
    result=io.run(req,root)
    return root,req,result['request_ref']


def test_existing_math_substrate_is_bound_to_exact_original_occurrences(investigation):
    root,req,ref=investigation
    original={x.name:x.read_bytes() for x in root.iterdir() if x.is_file()}
    value=sem.review(root,expected_request_ref=ref)
    old=io.inspect(root)
    assert value['application']=='Link'
    assert value['arithmetic_audit']=='PASS' and value['scope']['physical_qualification']=='HELD'
    assert len(value['morphism_registry']['representations'])==5
    assert len(value['morphism_registry']['morphisms'])==4
    assert [x['status'] for x in value['compositions']]==['COMPOSABLE']*3
    assert all(x['schema']=='ciw.preservation-composition.v1' for x in value['compositions'])
    assert {x['execution_id'] for x in value['original_stages']}==set(old['executions'])
    assert {x['result_id'] for x in value['original_stages']}=={x['result_id'] for x in old['results'].values()}
    assert all(not row['bound'] for rows in value['semantic_catalog']['lowerings'].values() for row in rows)
    assert original=={x.name:x.read_bytes() for x in root.iterdir() if x.is_file()}
    assert sem.verify_review(value,root,expected_request_ref=ref)==value


def test_refusal_is_not_described_as_executed_or_physically_qualified(tmp_path):
    req=example();req['calibration']['input_range']=[0,0.5];req['calibration']['joint_covariance']=None
    root=tmp_path/'refused';result=io.run(req,root)
    value=sem.review(root,expected_request_ref=result['request_ref'])
    assert value['arithmetic_audit']=='NOT_RUN'
    assert value['original_stages'][1]['status']=='refused'
    assert value['original_stages'][2]['execution_id'] is None
    assert value['scope']['physical_qualification']=='HELD'


def test_wrong_anchor_refuses_before_output_and_offline_calls_no_ports(investigation,tmp_path,monkeypatch):
    root,req,ref=investigation
    def forbid(*a,**k):raise AssertionError('no network or device access in semantic inspection')
    monkeypatch.setattr(socket,'create_connection',forbid)
    monkeypatch.setattr(DeviceRuntime,'__init__',forbid)
    target=tmp_path/'review.json'
    assert main(['semantics',str(root),'--request-ref','sha256:'+'0'*64,'--output',str(target)])==2
    assert not target.exists()
    assert main(['semantics',str(root),'--request-ref',ref,'--output',str(target)])==0
    assert target.exists()
    assert main(['semantics',str(root),'--request-ref',ref,'--output',str(target)])==2


@pytest.mark.parametrize('part',['scope','stages','composition','unit_witness'])
def test_resealed_false_reviews_cannot_override_retained_evidence(investigation,part):
    root,req,ref=investigation;value=sem.review(root,expected_request_ref=ref)
    if part=='scope':value['scope']['machine_control']=True
    elif part=='stages':value['original_stages'][0]['execution_id']='execution-other'
    elif part=='composition':value['compositions'][0]['status']='REFUSED'
    else:value['unit_expression_witness']['record']['commutes']=False
    value=seal({k:v for k,v in value.items() if k!='record_digest'})
    with pytest.raises(ValueError):sem.verify_review(value,root,expected_request_ref=ref)


def test_replay_equal_input_is_not_same_operation_occurrence(investigation,tmp_path):
    root,req,ref=investigation;a=sem.review(root,expected_request_ref=ref)
    io.replay(root,tmp_path/'replay',expected_request_ref=ref)
    b=sem.review(tmp_path/'replay',expected_request_ref=ref)
    assert a['evidence_id']==b['evidence_id'] and a['request_ref']==b['request_ref']
    assert {x['execution_id'] for x in a['original_stages']}.isdisjoint(x['execution_id'] for x in b['original_stages'])
    with pytest.raises(ValueError):sem.verify_review(a,tmp_path/'replay',expected_request_ref=ref)


@pytest.mark.parametrize('quantity,unit',list(sem._UNIT_MAPS))
def test_affine_commutation_independent_closed_form(quantity,unit):
    witness=sem.unit_path_witness(quantity,unit,1.02,0.5)
    target,scale,shift=sem._UNIT_MAPS[(quantity,unit)];a,b,s,t=F('1.02'),F('0.5'),F(scale),F(shift)
    assert witness['target_operator']==[[str(a),str(s*b+t-a*t)],['0','1']]
    # Independent direct scalar evaluation, not the implementation's matrix multiply.
    for x in [F(-10),F(0),F(25),F('24982/1000')]:
        assert s*(a*x+b)+t == a*(s*x+t)+(s*b+t-a*t)
    assert witness['commutes'] and witness['status']=='PASS'
    assert not any(witness['claims'].values())


def test_celsius_origin_and_temperature_difference_are_not_interchangeable():
    absolute=sem.unit_path_witness('temperature','degC',2.0,1.0)
    interval=sem.unit_path_witness('temperature_difference','degC',2.0,1.0)
    assert absolute['unit_map'][0][1]=='5463/20' and interval['unit_map'][0][1]=='0'
    assert absolute['target_operator'][0][1]=='-5443/20'
    bad=sem.unit_path_witness('temperature','degC',2.0,1.0,
                            proposed_target_operator=[['2','1'],['0','1']])
    assert bad['status']=='FAIL' and not bad['commutes']
    assert bad['tested_target_operator']==bad['source_operator']
    assert bad['claims']['physical_validation'] is False


@pytest.mark.parametrize('quantity,unit,gain,offset',[
    ('pressure','K',1,0),('voltage','degC',1,0),('temperature','C',1,0),
    ('temperature','K',True,0),('length','mm',float('nan'),0),('length','mm',1,float('inf')),
    ('length','mm',1e-100,0),('length','mm',1e30,0),
])
def test_units_not_guessed_and_existing_rational_budget_not_weakened(quantity,unit,gain,offset):
    with pytest.raises(ValueError):sem.unit_path_witness(quantity,unit,gain,offset)


def test_zero_gain_does_not_claim_invertible_calibration():
    value=sem.unit_path_witness('length','mm',0,2)
    assert value['commutes'] and value['affine_map_invertible'] is False


def test_invalid_projective_matrix_is_not_affine_scalar():
    with pytest.raises(ValueError):sem.unit_path_witness('length','mm',2,1,proposed_target_operator=[['2','1'],['1','1']])


def test_existing_typed_composition_rejects_incompatible_intermediates():
    from ciw.preservation_contracts import compose_contracts
    device=p.example_manifest()
    semantic, registry, contracts, _=sem._contracts(device['channels'][0],[c.content_identity(device)])
    with pytest.raises(ValueError,match='intermediate'):
        compose_contracts(registry,semantic,contracts[0],contracts[2])
    assert semantic.concrete.operations.describe()==[]


def test_budget_limited_algebra_does_not_block_raw_recovery(investigation):
    root,req,ref=investigation
    # An unsupported unit witness is separate from the historical audit/record.
    with pytest.raises(ValueError):sem.unit_path_witness('unknown_quantity','N',1,0)
    assert io.inspect(root,expected_request_ref=ref)['request_ref']==ref


def test_type_composition_and_unit_algebra_never_clear_a_failed_numeric_audit(tmp_path,monkeypatch):
    original=s._transport
    monkeypatch.setattr(s,'_transport',lambda matrix,jac: [[2*v for v in row] for row in original(matrix,jac)])
    root=tmp_path/'numeric-fault';result=io.run(example(),root)
    before={x.name:x.read_bytes() for x in root.iterdir() if x.is_file()}
    review=sem.review(root,expected_request_ref=result['request_ref'])
    assert review['arithmetic_audit']=='FAIL'
    assert [x['status'] for x in review['compositions']]==['COMPOSABLE']*3
    assert review['unit_expression_witness']['record']['status']=='PASS'
    assert review['scope']['physical_qualification']=='HELD'
    assert review['scope']['machine_control'] is False
    with pytest.raises(ValueError,match='audit'):io.export(root,tmp_path/'not-exported')
    assert before=={x.name:x.read_bytes() for x in root.iterdir() if x.is_file()}
