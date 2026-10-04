from copy import deepcopy

import pytest

from ciw import hardware_contract as hardware
from ciw import refurbishment_contract as contract
from ciw.operations.runner import digest


def project(commands):
    state = None
    for command in commands:
        state = contract.apply_command(state, command)
    return state


def next_command(state, action, payload):
    return {"schema":contract.COMMAND_SCHEMA, "command_id":"next-"+str(state['revision']),
            "asset_id":state['asset_id'], "expected_revision":state['revision'],
            "actor":"Test operator", "action":action, "payload":deepcopy(payload)}


def test_lifecycle_records_exact_costs_configuration_history_and_no_authority():
    commands = contract.example_commands()
    original = deepcopy(commands)
    state = project(commands)
    assert commands == original
    assert state['revision'] == 11 and state['stage'] == 'released'
    assert state['total_repair_cost_minor'] == 5500 and state['total_cost_minor'] == 15500
    assert len(state['configuration_history']) == 2
    order = state['work_orders']['WO-DEMO-1']
    assert order['assessments'][0]['assessment']['status'] == 'PASS'
    assert state['releases'][0]['scope'] == 'synthetic_or_public_demo_only'
    assert state['releases'][0]['certification_authority'] is False
    assert state['authority']['physical_testing'] == 'not_performed_by_registry'
    assert state['releases'][0]['configuration_ref'] == state['configuration_ref']
    assert state['warranties'][0]['configuration_ref'] == state['configuration_ref']


@pytest.mark.parametrize('amount', [True, 1.0, -1, 10**15+1, '100'])
def test_exact_bounded_money(amount):
    command = contract.example_commands()[0]
    command['payload']['acquisition_cost_minor'] = amount
    with pytest.raises(ValueError): contract.validate_command(command)


@pytest.mark.parametrize('field,value', [('source_kind', []), ('currency','cad'), ('minor_unit_exponent',True), ('intake_id',' x'), ('notes','x\x00y')])
def test_invalid_intake_fields(field,value):
    command = contract.example_commands()[0]
    command['payload'][field] = value
    with pytest.raises(ValueError): contract.validate_command(command)


def test_configuration_identity_matches_asset_and_revision_never_reused():
    commands = contract.example_commands()
    bad = deepcopy(commands[0]); bad['payload']['configuration']['asset_id'] = 'other'
    with pytest.raises(ValueError, match='different asset'): contract.validate_command(bad)
    state = project(commands[:5])
    with pytest.raises(ValueError, match='cannot be reused'):
        contract.apply_command(state, next_command(state,'replace_configuration',commands[4]['payload']))
    state['configuration']['components'][0]['model'] = 'changed view'
    assert state['configuration_history'][-1]['configuration']['components'][0]['model'] != 'changed view'


def test_repair_requires_diagnosis_and_bounds_quantity_cost():
    commands = contract.example_commands(); state=project(commands[:2])
    with pytest.raises(ValueError, match='diagnosis'):
        contract.apply_command(state, next_command(state,'record_repair',commands[3]['payload']))
    for quantity,cost in ((True,1),(0,1),(10001,1),(2,10**15)):
        bad=deepcopy(commands[3]); bad['payload']['parts'][0].update(quantity=quantity,unit_cost_minor=cost)
        with pytest.raises(ValueError): contract.validate_command(bad)


def test_release_requires_review_and_synthetic_cannot_be_operator_release():
    commands=contract.example_commands()
    state=project(commands[:7])
    with pytest.raises(ValueError, match='current explicit approval'):
        contract.apply_command(state,next_command(state,'release',commands[9]['payload']))
    state=project(commands[:9]); payload=deepcopy(commands[9]['payload']); payload['disposition']='operator_release'
    with pytest.raises(ValueError, match='demo release only'):
        contract.apply_command(state,next_command(state,'release',payload))


@pytest.mark.parametrize('change', ['repair','configuration','evidence'])
def test_changes_invalidate_review_without_erasing_history(change):
    commands=contract.example_commands(); state=project(commands[:9])
    if change=='repair':
        payload=deepcopy(commands[3]['payload']); payload['repair_id']='repair-2'
        action='record_repair'
    elif change=='configuration':
        payload=deepcopy(commands[4]['payload']); payload['configuration']['revision']='3'
        action='replace_configuration'
    else:
        payload=deepcopy(commands[5]['payload']); payload['evidence_id']='evidence-2'
        action='record_evidence'
    state=contract.apply_command(state,next_command(state,action,payload))
    order=state['work_orders']['WO-DEMO-1']
    assert order['current_review_id'] is None and len(order['reviews'])==1
    with pytest.raises(ValueError, match='current explicit approval'):
        contract.apply_command(state,next_command(state,'release',commands[9]['payload']))
    reassess=deepcopy(commands[6]['payload']); reassess['assessment_id']='assessment-2'
    with pytest.raises(ValueError, match='stale|all evidence'):
        contract.apply_command(state,next_command(state,'reassess',reassess))


def test_review_cannot_approve_failed_assessment():
    commands=contract.example_commands()
    for index,path in ((5,'record'), (6,'request')):
        observations=commands[index]['payload'][path]
        if path=='request': observations=observations['observations']
        observations['records'][0]['value']=10
    commands[5]['payload']['content_ref']=digest(commands[5]['payload']['record'])
    state=project(commands[:7])
    assert state['work_orders']['WO-DEMO-1']['assessments'][0]['assessment']['status']=='FAIL'
    with pytest.raises(ValueError, match='passing'):
        contract.apply_command(state,commands[7])


def test_unrelated_or_modified_observation_evidence_cannot_justify_assessment():
    commands=contract.example_commands(); state=project(commands[:6])
    request=commands[6]['payload']['request']; request['observations']['records'][0]['notes']='unretained change'
    with pytest.raises(ValueError, match='exact retained observations'):
        contract.apply_command(state,commands[6])
    bad=deepcopy(commands[5]); bad['payload']['record']['source_kind']='operator_import'
    bad['payload']['content_ref']=digest(bad['payload']['record'])
    with pytest.raises(ValueError, match='cannot be relabeled'):
        contract.apply_command(project(contract.example_commands()[:5]),bad)


def test_return_preserves_release_and_customer_version_and_opens_new_work():
    state=project(contract.example_commands())
    original=deepcopy(state['releases'])
    state=contract.apply_command(state,next_command(state,'record_customer',{'customer_id':'customer-demo','name':'New name','contact':'changed','notes':''}))
    assert state['releases']==original
    state=contract.apply_command(state,next_command(state,'record_return',{'return_id':'return-1','release_id':'release-1','received_on':'2026-02-01','reason':'Demo return','condition':'Inspect','notes':''}))
    assert state['releases']==original and state['current_release_id'] is None
    assert state['work_orders']['WO-DEMO-1']['current_review_id'] is None
    state=contract.apply_command(state,next_command(state,'open_order',{'work_order_id':'WO-2','summary':'Returned item inspection'}))
    assert state['active_work_order_id']=='WO-2' and len(state['work_orders'])==2


def test_maximum_identifier_example_is_valid():
    assert project(contract.example_commands('a'*128))['asset_id']=='a'*128


@pytest.mark.parametrize('case,expected', [('public','PASS'),('pass','PASS'),('fail','FAIL'),('missing','INDETERMINATE')])
def test_bench_records_rederived_and_assessments_keep_origin(case,expected):
    from ciw.hardware_bench import example_bundle
    bundle=example_bundle(case)
    commands=contract.example_commands(bundle['configuration']['asset_id'])
    commands[0]['payload']['configuration']=bundle['configuration']
    commands[0]['payload']['source_kind']=bundle['evidence']['source_kind']
    state=project(commands[:3])
    evidence={'evidence_id':'bench-1','kind':'diagnostic','configuration_ref':hardware.configuration_ref(bundle['configuration']),
              'source_kind':bundle['evidence']['source_kind'],'content_ref':digest(bundle['evidence']),
              'summary':'Public fixture or derived synthetic evidence','record':bundle['evidence']}
    state=contract.apply_command(state,next_command(state,'record_evidence',evidence))
    payload={'assessment_id':'bench-assessment','request':bundle['request'],'evidence_ids':['bench-1']}
    state=contract.apply_command(state,next_command(state,'reassess',payload))
    assert state['work_orders']['WO-DEMO-1']['assessments'][0]['assessment']['status']==expected
    assert state['work_orders']['WO-DEMO-1']['assessments'][0]['assessment']['source_kind']=='synthetic'
    bad=deepcopy(payload); bad['assessment_id']='forged'
    bad['request']['observations']['records'][0]['value']=12345
    with pytest.raises(ValueError): contract.apply_command(state,next_command(state,'reassess',bad))


def test_repaired_asset_cannot_relabel_old_evidence_with_new_identity():
    commands=contract.example_commands(); state=project(commands[:8])
    repair=deepcopy(commands[3]['payload']); repair['repair_id']='repair-2'
    state=contract.apply_command(state,next_command(state,'record_repair',repair))
    evidence=deepcopy(commands[5]['payload']); evidence['evidence_id']='renamed-old-evidence'
    with pytest.raises(ValueError,match='cannot be reattached'):
        contract.apply_command(state,next_command(state,'record_evidence',evidence))


def test_manual_operator_disposition_is_explicit_and_does_not_grant_authority():
    commands=contract.example_commands()
    commands[0]['payload']['source_kind']='operator_import'
    commands[5]['payload']['source_kind']='operator_import'
    commands[5]['payload']['record']['source_kind']='operator_import'
    commands[5]['payload']['content_ref']=digest(commands[5]['payload']['record'])
    commands[6]['payload']['request']['observations']['source_kind']='operator_import'
    commands[9]['payload']['disposition']='operator_release'
    state=project(commands)
    release=state['releases'][0]
    assert release['scope']=='operator_declared_disposition_only'
    assert release['source_kinds']==['operator_import']
    assert release['certification_authority'] is False
    assert state['authority']['operator_identity']=='not_authenticated'


def test_public_origin_cannot_be_promoted_by_manual_observation_import():
    commands=contract.example_commands()
    commands[0]['payload']['source_kind']='external_public'
    commands[5]['payload']['source_kind']='external_public'
    commands[5]['payload']['record']['source_kind']='operator_import'
    commands[5]['payload']['content_ref']=digest(commands[5]['payload']['record'])
    commands[6]['payload']['request']['observations']['source_kind']='operator_import'
    commands[9]['payload']['disposition']='operator_release'
    state=project(commands[:9])
    with pytest.raises(ValueError,match='demo release only'): contract.apply_command(state,commands[9])


def test_date_ranges_and_immutable_currency_payload():
    commands=contract.example_commands()
    for date in ('2026-02-30','2026-4-1','2027-01-01'):
        bad=deepcopy(commands[-1]); bad['payload']['starts_on']=date
        with pytest.raises(ValueError): contract.validate_command(bad)
    bad=deepcopy(commands[3]); bad['payload']['currency']='USD'
    with pytest.raises(ValueError): contract.validate_command(bad)
