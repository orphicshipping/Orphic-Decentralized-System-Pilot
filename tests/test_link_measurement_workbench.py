"""Opt-in positive checks through the unchanged typed-data owner's native APIs.

Set NET_MEASUREMENT_WORKBENCH=1 only in an explicitly assembled implementation
containing data_sources and the data-record Workbench path. No mocks or copied
implementation are supplied by this test. The native file identities are logged
by the separate qualification runner. This is not full PR200 qualification.
"""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from ciw import link_contract as c, link_measurements as m, link_measurement_io as io, link_study_io as prior
from ciw.link_study_cli import example
from ciw.session import Session

pytestmark=pytest.mark.skipif(os.environ.get('NET_MEASUREMENT_WORKBENCH')!='1',reason='Explicit typed-data owner integration not selected')


@pytest.fixture
def investigation(tmp_path):
    request=example();prior.run(request,tmp_path/'study');b=m.build(tmp_path/'study')
    _,_,session,_=prior.read(tmp_path/'study')
    return b,session


def test_real_workbench_source_covariance_reopen(investigation,tmp_path):
    from ciw.data_sources import native_record
    bundle,session=investigation
    before=deepcopy(session.results);executions=deepcopy(session.executions)
    original=(tmp_path/'study/workspace.json').read_bytes()
    receipt=io.attach_workbench(bundle,session,dataset_id='synthetic.unit.1',recorded_at='2026-10-06T00:00:00Z')
    assert len(receipt['sources'])==4 and receipt['new_executions']==0
    retained=session.workbench.serialize()
    sources=retained['sources']
    native=[native_record(source) for source in sources]
    assert native[0]['schema']=='ciw.artifact.v1'
    assert m._unpack(native[0]['metadata']['payload'])==c.bounded(bundle,m.MAX_BYTES)
    assert native[1]==bundle['input_covariance']
    assert native[2]==bundle['native']['covariance']
    assert native[3]==bundle['native']['stream']
    assert sources[1]['input_source_ids']==[sources[0]['source_id']]
    assert set(sources[2]['input_source_ids'])=={sources[0]['source_id'],sources[1]['source_id']}
    assert set(sources[3]['input_source_ids'])=={sources[0]['source_id'],sources[2]['source_id']}
    assert len({x['source_id'] for x in sources})==4
    assert all(x['source_id']!=json.loads(base64.b64decode(x['bytes_b64']))['source_id'] for x in sources)
    again=io.attach_workbench(bundle,session,dataset_id='synthetic.unit.1',recorded_at='2026-10-06T00:00:00Z')
    assert receipt==again and retained==session.workbench.serialize()
    session.save_workspace(tmp_path/'attached.json')
    resumed=Session.from_workspace(tmp_path/'attached.json',tmp_path/'reopened')
    assert resumed.results==before and resumed.executions==executions
    assert resumed.workbench.serialize()==retained
    assert (tmp_path/'study/workspace.json').read_bytes()==original


@pytest.mark.parametrize('timestamp',['2026-10-06T00:00:01Z','2026-10-06','not-a-date'])
def test_attachment_failure_leaves_workbench_unchanged(investigation,timestamp):
    b,s=investigation;io.attach_workbench(b,s,dataset_id='s',recorded_at='2026-10-06T00:00:00Z')
    old=s.workbench.serialize()
    with pytest.raises(ValueError):io.attach_workbench(b,s,dataset_id='s',recorded_at=timestamp)
    assert s.workbench.serialize()==old


def test_missing_covariance_stays_missing(tmp_path):
    from ciw.data_sources import native_record
    r=example();r['calibration']['joint_covariance']=None;prior.run(r,tmp_path/'study')
    b=m.build(tmp_path/'study');_,_,s,_=prior.read(tmp_path/'study')
    out=io.attach_workbench(b,s,dataset_id='unknown',recorded_at='2026-10-06T00:00:00Z')
    assert len(out['sources'])==2
    stream=native_record(s.workbench.get_source(out['sources'][-1]['source_id']))
    assert all(x['uncertainty'] is None for x in stream['observations'])


def test_wrong_occurrence_rejected(investigation,tmp_path):
    b,s=investigation;prior.replay(tmp_path/'study',tmp_path/'replayed');_,_,other,_=prior.read(tmp_path/'replayed')
    with pytest.raises(ValueError,match='exact retained result'):io.attach_workbench(b,other,dataset_id='s',recorded_at='2026-10-06T00:00:00Z')
    assert not other.workbench.list_sources()


def test_native_source_mutation_refuses_on_reopen(investigation):
    from ciw.workbench import Workbench
    b,s=investigation;io.attach_workbench(b,s,dataset_id='s',recorded_at='2026-10-06T00:00:00Z')
    saved=s.workbench.serialize();saved['sources'][-1]['input_source_ids']=[]
    with pytest.raises(ValueError):Workbench.restore(saved)


def test_does_not_register_new_data_family(investigation):
    from ciw.data_sources import retain_record
    b,_=investigation
    with pytest.raises(ValueError):retain_record(b,dataset_id='s',revision=1,parent_source_id=None,
                                                recorded_at='2026-10-06T00:00:00Z',data_family='measurement')
