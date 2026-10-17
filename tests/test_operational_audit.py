"""Focused audit checks for corruption, refusal, resource budgets and identity."""
from copy import deepcopy
from pathlib import Path
import os
import time

import pytest

from ciw.agent_api import encode,parse
from ciw.link_store import Journal,MAX_MESSAGES
from ciw.operational_collection import Collector
from ciw.operational_cli import example,default_context
from ciw.operational_agent import OperationalHost
from ciw import operational_engineering as eng


def test_retained_validation_allocates_no_verification_occurrence(tmp_path,monkeypatch):
    with OperationalHost(example(tmp_path)) as host:
        run=host.call('net_execute',{'source':'assessment','graph':'assessment-graph','attempt':'first'})
        assert run['status']=='completed'
    from ciw.core import identities
    monkeypatch.setattr(identities,'new_identity',lambda *a:pytest.fail('Inspection allocated a verification occurrence'))
    with OperationalHost(next(tmp_path.glob('profile-*.json')),resume=True) as host:
        assert host.call('oe_model_support',{'attempt':'first'})['data']['verification']['verification_id']


def test_resealed_model_scope_tamper_is_rejected(tmp_path):
    from ciw.operations.runner import seal
    from ciw.session import Session
    with OperationalHost(example(tmp_path)) as host:
        response=host.call('net_execute',{'source':'assessment','graph':'assessment-graph','attempt':'first'})
        workspace=host.profile.parent/host.spec['execution_output']/'first'/'workspace.json'
        value=parse(workspace.read_bytes())
        candidate=next(r for r in value['results'] if r['operation_id']==eng.ASSESS)
        candidate['data']['model']['validity_domain']='physically qualified unrestricted model'
        seal(candidate)
        corrupt=tmp_path/'corrupt.json';corrupt.write_bytes(encode(value))
        with pytest.raises(ValueError):Session.from_workspace(corrupt,output_dir=tmp_path/'readback')


def test_store_headroom_stops_before_read_and_persists_reason(tmp_path,monkeypatch):
    import ciw.operational_collection as collection
    path=tmp_path/'journal.sqlite'
    with Journal(path,create=True) as journal:
        # Reduce the policy cap, keeping Journal's original storage validator intact.
        monkeypatch.setattr(collection,'MAX_MESSAGES',65)
        collector=Collector(journal,'bounded',cadence_s=0)
        state=collector.run(lambda i:pytest.fail('Backpressure did not stop source access'),source_identity={'adapter':'test_fixture'})
        assert state['status']=='STORE_FULL' and state['gaps'][0]['reason']=='reserved_control_headroom_backpressure'
        assert collector.state()['status']=='STORE_FULL'


def test_workload_and_acquisition_grants_are_not_exposed_by_inspection(tmp_path):
    with OperationalHost(example(tmp_path)) as h:
        names={t['name'] for t in h.descriptions()}
        assert not {'approve_command','collector_start','shell','power_change'}&names
        assert 'supervise' not in h.grants and 'acknowledge' not in h.grants
        with pytest.raises(PermissionError):h.call('oe_command_dispatch',{'command':'sha256:'+'0'*64,'attempt':'first'})


def test_source_and_policy_substitution_cannot_resume_same_output(tmp_path):
    profile=example(tmp_path)
    with OperationalHost(profile):pass
    changed=parse(profile.read_bytes());changed['grants']=['inspect']
    altered=tmp_path/'altered.json';altered.write_bytes(encode(changed))
    with pytest.raises(ValueError):OperationalHost(altered,resume=True)


def test_derive_grant_is_required_for_existing_retained_net_mutations(tmp_path):
    p=example(tmp_path)
    spec=parse(p.read_bytes());spec['grants']=['inspect']
    p.write_bytes(encode(spec))
    with OperationalHost(p) as host:
        for name,args in [('net_extract',{'artifact':'assessment','selector':[]}),
                          ('net_candidate',{'baseline':'assessment-graph','changes':{}}),
                          ('oe_command_prepare',{'proposal':'sha256:'+'0'*64,'candidate':'something'})]:
            with pytest.raises(PermissionError):host.call(name,args)


def test_missing_and_out_of_range_are_preserved_without_zero_fill():
    from ciw.operational_context import make_binding,rows,query
    from ciw.operations.runner import seal
    from ciw.control_contracts import record
    from ciw.reference_comparison import example_request
    stream=example_request()['right'];sample=stream['observations'][0]
    sample['value']=None;sample['uncertainty']=None;seal(sample);seal(stream)
    raw=encode(stream);binding=make_binding(raw,'observations',default_context(clock='synthetic.clock',origin='synthetic'),[])
    projected=rows(raw,binding)
    assert projected[0]['quality']=='missing' and projected[0]['value'] is None
    source=(Path(__file__).parents[1]/'examples/calibrated-window/source.json').read_bytes()
    native=parse(source);native['samples'][0]['indicated_value']=native['calibration_profile']['input_range'][1]+1
    raw=encode(native);binding=make_binding(raw,'calibrated-window',default_context(clock=native['device_clock']['clock_id']),[])
    assert rows(raw,binding)[0]['quality']=='out_of_range'
    with pytest.raises(ValueError):make_binding(b'{malformed','observations',default_context(),[])
