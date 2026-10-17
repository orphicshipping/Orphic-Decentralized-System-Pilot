"""Scientific projection guard regressions; synthetic data, not physical metrology.

Real Link contracts, Session, covariance and retained IO are used. One explicitly
injected arithmetic defect tests that a failed independent audit blocks export.
"""
from copy import deepcopy
import json
import random

import pytest

from ciw import link_contract as c, link_study as study, link_study_io as io
from ciw import link_inspection_cli as inspection
from ciw.link_gateway import validate_capture
from ciw.link_profiles import example_manifest
from ciw.link_protocol import encode, MAX_FRAME
from ciw.link_study_cli import example


def request_for(*, ticks=(0, 100), hz=10**9, flag=None, encoding='opaque.v1'):
    device = example_manifest('GaugeLink')
    device['channels'][0].update(quantity='force', unit='N', encoding=encoding)
    device['clock'].update(id='synthetic.tick-clock', tick_hz=hz)
    records = []
    frames = []
    for i, tick in enumerate(ticks):
        record = c.make_observation(device, boot_id='synthetic.guard.boot', sequence=i,
            channel_id='CH0', raw=f'{i+1}.000 N\r\n'.encode(), tick=tick, origin='synthetic')
        if flag is not None and i == len(ticks)-1:
            record['quality_flags'] = [flag]
        records.append(record)
        wire = encode('observation', c.bounded(record))
        frames.append({'wire':c.packed(wire, MAX_FRAME), 'received_ns':100+i,
            'host_clock':'synthetic.receipt-clock', 'authentication_at_capture':'not_authenticated'})
    capture = {'schema':c.CAPTURE_SCHEMA, 'manifest':device,
        'manifest_wire':c.packed(encode('manifest',c.bounded(device)), MAX_FRAME),
        'gateway_id':'synthetic.guard.gateway', 'boot_id':'synthetic.guard.boot', 'frames':frames,
        'calibrations':[], 'joint_covariance':None, 'transport':'local_retained_byte_stream',
        'closure':'operator_selected_finite_capture'}
    request = {'schema':study.SCHEMA, 'capture':capture, 'channel_id':'CH0',
        'calibration':{'gain':1.0,'offset':0.0,'input_range':[-100,100],
                       'declaration':None,'joint_covariance':None},
        'model':{'reference_kind':'declared_exact','expected_mean':1.5,'residual_limit':0.1}}
    return request, records


def nearest_even(numerator, denominator):
    # Independent integer oracle; avoids float multiplication and Fraction.round.
    integer, remainder = divmod(numerator, denominator)
    return integer + (2*remainder > denominator or (2*remainder == denominator and integer % 2 == 1))


def faithful(seconds, delta, hz):
    numerator, denominator = seconds.as_integer_ratio()
    return nearest_even(numerator*hz, denominator) == delta


@pytest.mark.parametrize('delta,hz',[(2**53,10**9), (9007199254740996,10**12),
    (389676623564206912,7), (9007199254741022,1000)])
def test_float_round_trip_cannot_hide_loss_of_device_tick(delta, hz):
    request, _ = request_for(ticks=(0,delta),hz=hz)
    decoded = study.decode(request)
    corrected = study.affine(request, decoded)
    seconds = delta/hz
    assert round(seconds*hz) == delta  # Exact reproduced old false-positive check.
    assert not faithful(seconds, delta, hz)
    before = deepcopy(request)
    value = study.native(request,decoded,corrected,execution_id='synthetic.execution',
                         source_evidence=c.content_identity(request))
    assert len(value['stream']['observations']) == 1
    assert value['withheld'] == [{'id':decoded['rows'][1]['id'], 'reason':'native_time_precision_insufficient'}]
    assert value['rows'][1]['receipt_tick'] == delta
    assert request == before


@pytest.mark.parametrize('hz',[1,3,7,1000,44100,10**9,10**12])
def test_projection_acceptance_matches_independent_integer_oracle(hz):
    rng = random.Random(60401+hz)
    cases = [1,2,17,2**52,2**53-1,2**53,2**53+1,2**63-1,2**64-1]
    cases += [rng.randrange(1,2**64) for _ in range(32)]
    for delta in cases:
        request,_ = request_for(ticks=(0,delta),hz=hz)
        decoded = study.decode(request)
        actual = study.native(request,decoded,study.affine(request,decoded),
            execution_id='synthetic.execution',source_evidence=c.content_identity(request))
        projected = len(actual['stream']['observations']) == 2
        assert projected == faithful(delta/hz,delta,hz), (delta,hz)
        assert len(actual['rows']) == 2
        base = validate_capture(request['capture'])['projection']
        assert projected == (len(base['mappings']) == 2)


@pytest.mark.parametrize('origin',[0,2**53,2**63,2**64-10000])
def test_small_relative_ticks_do_not_lose_precision_at_large_origins(origin):
    request,_ = request_for(ticks=(origin,origin+2000))
    decoded = study.decode(request)
    actual = study.native(request,decoded,study.affine(request,decoded),
        execution_id='synthetic.execution',source_evidence=c.content_identity(request))
    assert len(actual['stream']['observations']) == 2
    assert actual['stream']['observations'][1]['clock']['time_s'] == 0.000002
    assert not actual['withheld']


@pytest.mark.parametrize('flag',['overload','sensor_fault','saturated','vendor.unspecified'])
@pytest.mark.parametrize('encoding',['notation-ascii-scalar.v1','opaque.v1'])
def test_flagged_source_retained_but_not_silently_projected(flag, encoding):
    request, records = request_for(flag=flag,encoding=encoding)
    captured = validate_capture(request['capture'])
    projection = captured['projection']
    assert captured['observations'] == records
    assert projection['withheld'] == [{'measurement_id':records[-1]['measurement_id'],
                                       'reason':'quality_flags_require_review'}]
    assert len(projection['mappings']) == 1
    assert records[-1]['quality_flags'] == [flag]


@pytest.mark.parametrize('flag',['overload','sensor_fault','saturated','vendor.unspecified'])
def test_flagged_raw_scalar_stream_cannot_become_unmarked_model(flag,tmp_path):
    request, _ = request_for(flag=flag)
    raw = (json.dumps(request,indent=3)+'\n').encode()
    with pytest.raises(ValueError,match='quality flags'):
        study.decode(request)
    root = tmp_path/'flagged'
    result = io.run(request,root,raw=raw)
    assert result['status'] == 'incomplete'
    assert not result['results']
    assert result['origin'] == 'synthetic'
    assert (root/'request.json').read_bytes() == raw
    view = inspection.view(root,request_ref=c.bytes_ref(raw))
    assert view['qualification']=='HELD' and view['reading']['value'] is None
    with pytest.raises(ValueError): io.export(root,tmp_path/'no-export')
    assert not (tmp_path/'no-export').exists()
    with io.agent(root) as host:
        assert host.capabilities()['host_policy']['allowed_operations'] == []


def test_failed_arithmetic_audit_blocks_export_before_output_creation(tmp_path,monkeypatch):
    original = study._transport
    def corrupted_arithmetic(matrix,jacobian):
        return [[2*v for v in row] for row in original(matrix,jacobian)]
    # Deliberate numerical fault, NOT a substituted Session/validator/proof.
    # The independent Fraction audit is left entirely unchanged.
    monkeypatch.setattr(study,'_transport',corrupted_arithmetic)
    root = tmp_path/'faulted'
    result = io.run(example(),root)
    assert result['status'] == 'completed'  # Four operation occurrences completed.
    assert len(result['results']) == 4
    assert result['results'][study.OPS[-1]]['data']['status'] == 'FAIL'
    before = {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    assert inspection.view(root)['qualification'] == 'HELD'
    destination = tmp_path/'must-not-export'
    with pytest.raises(ValueError,match='audit'):
        io.export(root,destination)
    assert not destination.exists()
    assert before == {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}


def test_flag_free_success_is_byte_compatible_and_exports_full_covariance(tmp_path):
    request=example()
    result=io.run(request,tmp_path/'study')
    assert result['results'][study.OPS[-1]]['data']['status']=='PASS'
    assert result['results'][study.OPS[2]]['data']['mean']==pytest.approx(2.03)
    io.export(tmp_path/'study',tmp_path/'native')
    matrix=json.loads((tmp_path/'native/covariance.json').read_bytes())
    assert matrix==result['results'][study.OPS[1]]['data']['covariance']
    assert matrix['matrix'][0][1] != 0
    replay=io.replay(tmp_path/'study',tmp_path/'replay')
    assert replay['evidence_id']==result['evidence_id']
    assert set(replay['executions']).isdisjoint(result['executions'])


def test_signed_custody_does_not_clear_source_quality_flags(tmp_path):
    from ciw.link_store import Journal
    from ciw import link_delivery as delivery
    from ciw.link_gateway import capture
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    request,records=request_for(flag='sensor_fault')
    device=request['capture']['manifest']
    private=Ed25519PrivateKey.generate()
    public=private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    key=b's'*32  # Synthetic transport key only.
    with Journal(tmp_path/'source.sqlite',create=True) as source, Journal(tmp_path/'net.sqlite',create=True) as receiver:
        original=[encode('manifest',c.bounded(device),key=key)]
        original += [encode('observation',c.bounded(record),key=key) for record in records]
        for wire in original:source.append(wire,expected_manifest=device,key=key,require_auth=True)
        binding=delivery.DeliveryBinding('synthetic.tenant','synthetic.node',device['device_id'],
            c.content_identity(device),source.store_id,receiver.store_id,'synthetic.epoch',public,0,10000)
        first=delivery.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,
                                   now_ns=1,key=key)
        second=delivery.sync_signed(source,receiver,device=device,binding=binding,signing_key=private,
                                    now_ns=2,key=key)
        assert first['new_acknowledgements']==3 and second['existing_acknowledgements']==3
        assert [r['wire'] for r in source.rows()]==original
        assert [r['wire'] for r in receiver.rows()]==original
        request['capture']=capture(receiver,expected_manifest=device,boot_id='synthetic.guard.boot')
    raw=c.bounded(request,study.MAX_BYTES)
    result=inspection.run(request,tmp_path/'inspection',raw=raw)
    assert result['status']=='INCOMPLETE' and result['qualification']=='HELD'
    assert result['checks']['source_quality']=='FLAGGED_REVIEW_REQUIRED'
    assert 'quality flags' in result['next_action']
    report=io.inspect(tmp_path/'inspection')
    assert report['source_quality_flags']==[{'measurement_id':records[-1]['measurement_id'], 'flags':['sensor_fault']}]
    assert not report['results']
    assert (tmp_path/'inspection/request.json').read_bytes()==raw
    html=(tmp_path/'inspection/inspection.html').read_text()
    assert 'Flagged review required' in html and 'Source quality' in html
    assert '<details><summary>Advanced:' in html and '<script' not in html
    assert all(not flag for flag in result['authority'].values())


def test_quality_summary_comes_from_original_source_not_html(tmp_path):
    request,records=request_for(flag='vendor.unspecified')
    root=tmp_path/'flagged'
    result=inspection.run(request,root)
    (root/'inspection.html').write_text('QUALITY PASS; ACCEPT PART')
    result=inspection.view(root,request_ref=result['reference'],advanced=True)
    assert result['checks']['source_quality']=='FLAGGED_REVIEW_REQUIRED'
    assert result['advanced']['source_quality_flags'][0]['flags']==['vendor.unspecified']
    assert result['qualification']=='HELD' and result['authority']['part_release'] is False


def test_large_gap_export_preserves_original_integer_ticks_and_covariance(tmp_path):
    request,_=request_for(ticks=(0,2**53))
    original=(json.dumps(request,indent=2)+'\n').encode()
    result=io.run(request,tmp_path/'study',raw=original)
    assert result['results'][study.OPS[-1]]['data']['status']=='PASS'
    io.export(tmp_path/'study',tmp_path/'native')
    stream=json.loads((tmp_path/'native/observations.json').read_bytes())
    mapping=json.loads((tmp_path/'native/mapping.json').read_bytes())
    assert len(stream['observations'])==1 and len(mapping['rows'])==2
    assert mapping['rows'][-1]['receipt_tick']==2**53
    assert mapping['withheld'][0]['reason']=='native_time_precision_insufficient'
    assert mapping['covariance'] is None
    assert (tmp_path/'study/request.json').read_bytes()==original
    assert result['results'][study.OPS[2]]['data']['mean']==1.5  # Scientific computation remains intact.


@pytest.mark.parametrize('ticks',[(None,None),(0,None),(0,100,100),(0,100,200)])
def test_existing_unknown_and_duplicate_time_policy_unchanged(ticks):
    request,_=request_for(ticks=ticks)
    if ticks==(0,100,100):
        # Equal ticks on distinct chunks already refuse at the Link boundary.
        with pytest.raises(ValueError):study.decode(request)
        return
    decoded=study.decode(request)
    projected=study.native(request,decoded,study.affine(request,decoded),
        execution_id='synthetic.execution',source_evidence=c.content_identity(request))
    assert len(projected['stream']['observations'])==sum(t is not None for t in ticks)
    assert len(projected['rows'])==len(ticks)
    assert all(r['acquired_at'] is None for r in projected['rows'])
