"""Synthetic identification constraints and numerical checks, not physical tests."""
from copy import deepcopy
from hashlib import sha256
import cmath
import json
import math
import subprocess

import numpy as np
import pytest

from ciw import mount_characterization as m
from ciw.core.identities import content_identity
from ciw.operations.runner import seal
from ciw.control_contracts import validate_observation
from ciw.telemetry import canonical


@pytest.fixture(scope='module')
def fixture():return m.example()


def reseal(row):return seal({k:v for k,v in row.items() if k!='record_digest'})


def test_unity_chain_no_physical_or_mount_identification(fixture):
    before=canonical(fixture);r=m.analyze(fixture)
    assert before==canonical(fixture) and r['origin']=='simulated'
    assert len(r['passing_sampled_frequencies_hz'])==48
    assert r['continuous_qualified_band_hz'] is None
    assert r['scope']==m.SCOPE and r['verification_id'] is None
    for point in r['frequencies']:
        assert point['h_real']==pytest.approx(1,abs=1e-14)
        assert point['h_imag']==pytest.approx(0,abs=1e-14)
        assert point['observations']['coherence']['value']==pytest.approx(1,abs=1e-14)
        for obs in point['observations'].values():
            validate_observation(obs);assert obs['identity']['execution_id'] is None
            assert obs['provenance']['semantics']=='simulated' and obs['uncertainty'] is None


def test_compliant_fixture_agrees_with_independent_mass_stiffness_equation():
    r=m.analyze(m.example(compliant=True))
    mass=0.0008;natural=2*math.pi*512;stiffness=mass*natural**2;damping=2*.12*mass*natural
    for row in r['frequencies']:
        omega=2*math.pi*row['frequency_hz']
        wanted=(stiffness+1j*damping*omega)/(stiffness-mass*omega**2+1j*damping*omega)
        assert complex(row['h_real'],row['h_imag'])==pytest.approx(wanted,abs=3e-14)
    assert r['passing_sampled_frequencies_hz']==[32.,64.,96.]
    resonance=next(x for x in r['frequencies'] if x['frequency_hz']==512)
    assert resonance['observations']['relative_gain']['value']>4
    assert resonance['observations']['coherence']['value']>0.999999
    assert 'GAIN_DEVIATION' in resonance['reasons']


@pytest.mark.parametrize('samples',[128,256,384])
def test_one_to_three_replicates_do_not_claim_coherence(fixture,samples):
    request=deepcopy(fixture)
    for side in ('reference','dut'):request[side]=request[side][:samples]
    with pytest.raises(ValueError,match='four'):m.analyze(request)


@pytest.mark.parametrize('field,value',[('sample_rate_hz',0),('sample_rate_hz',True),
    ('block_samples',17),('phase_skew_bound_s',-1),('phase_skew_bound_s',float('nan')),
    ('temperature_K',0),('reference_response','CALIBRATED'),('estimator','blind-identification'),('schema','generic')])
def test_closed_scope_and_numerical_refusals(fixture,field,value):
    request=deepcopy(fixture);request[field]=value
    with pytest.raises((ValueError,TypeError)):m.analyze(request)


@pytest.mark.parametrize('change',['identity','unit','quantity','frame','clock','timestamp','missing_sample','nan','boolean','vector','unsealed'])
def test_no_silent_alignment_reinterpretation_or_source_swap(fixture,change):
    r=deepcopy(fixture);row=r['dut'][17]
    if change=='identity':row['identity']['entity_id']='different-instrument'
    elif change=='unit':row['unit']='g'
    elif change=='quantity':row['quantity']='temperature'
    elif change=='frame':row['frame']='rotated-axis'
    elif change=='clock':row['clock']['id']='different-clock'
    elif change=='timestamp':row['clock']['time_s']+=1e-6
    elif change=='missing_sample':r['dut'].pop(17)
    elif change=='nan':row['value']=float('nan')
    elif change=='boolean':row['value']=True
    elif change=='vector':row['value']=[1,2,3]
    elif change=='unsealed':row['value']+=1
    if change not in {'missing_sample','nan','unsealed','boolean'}:r['dut'][17]=reseal(row)
    with pytest.raises((ValueError,TypeError)):m.analyze(r)


def test_same_sensor_cannot_supply_its_own_independent_reference(fixture):
    r=deepcopy(fixture)
    for i,row in enumerate(r['dut']):
        row['identity']['entity_id']=r['reference'][0]['identity']['entity_id'];r['dut'][i]=reseal(row)
    with pytest.raises(ValueError,match='distinct'):m.analyze(r)


def test_synthetic_observed_mixture_refuses(fixture):
    r=deepcopy(fixture)
    for i,row in enumerate(r['dut']):row['provenance']['semantics']='observed';r['dut'][i]=reseal(row)
    with pytest.raises(ValueError,match='mix'):m.analyze(r)


def test_observed_inputs_still_yield_estimates_not_physical_qualification(fixture):
    r=deepcopy(fixture)
    for side in ('reference','dut'):
        for i,row in enumerate(r[side]):row['provenance']['semantics']='observed';r[side][i]=reseal(row)
    output=m.analyze(r)
    assert output['origin']=='observed' and output['scope']['physical_qualification']=='HELD'
    assert output['frequencies'][0]['observations']['relative_gain']['provenance']['semantics']=='estimated'


def test_flags_preserved_and_fft_not_attempted(fixture,monkeypatch):
    r=deepcopy(fixture);r['source_quality_flags']=['saturated','dropped_samples']
    monkeypatch.setattr(np.fft,'rfft',lambda *a,**k:pytest.fail('flagged stream entered FFT'))
    result=m.analyze(r)
    assert result['status']=='SOURCE_REVIEW_REQUIRED' and result['flags']==r['source_quality_flags']
    assert result['frequencies']==[] and result['scope']['inverse_correction_applied'] is False


def test_unknown_chain_response_and_timing_never_give_passing_points(fixture):
    r=deepcopy(fixture);r['reference_response']='UNKNOWN';r['phase_skew_bound_s']=None
    output=m.analyze(r)
    assert output['passing_sampled_frequencies_hz']==[]
    assert all({'TIMING_BOUND_UNKNOWN','CHAIN_RESPONSE_UNCHARACTERIZED'}<=set(x['reasons']) for x in output['frequencies'])


def test_timing_phase_error_budget_is_frequency_dependent(fixture):
    r=deepcopy(fixture);r['phase_skew_bound_s']=10e-6
    result=m.analyze(r)
    assert 1376 in result['passing_sampled_frequencies_hz']
    assert 1408 not in result['passing_sampled_frequencies_hz']
    assert all(x['h_real']==pytest.approx(1) for x in result['frequencies'])


def test_unexcited_input_has_no_invented_transfer_or_band(fixture):
    r=deepcopy(fixture)
    for side in ('reference','dut'):
        for i,row in enumerate(r[side]):row['value']=0.;r[side][i]=reseal(row)
    output=m.analyze(r)
    assert output['passing_sampled_frequencies_hz']==[]
    for point in output['frequencies']:
        assert point['h_real'] is point['h_imag'] is None
        assert point['observations']['coherence']['value'] is None
        assert 'INSUFFICIENT_BIN_POWER' in point['reasons']


def test_known_delay_uses_declared_cross_spectral_sign(fixture):
    r=deepcopy(fixture);raw=np.array([x['value'] for x in r['reference']]).reshape(-1,128)
    delayed=np.roll(raw,1,axis=1).ravel()
    for i,row in enumerate(r['dut']):row['value']=float(delayed[i]);r['dut'][i]=reseal(row)
    result=m.analyze(r)
    for point in result['frequencies']:
        expected=cmath.exp(-2j*math.pi*point['frequency_hz']/4096)
        assert complex(point['h_real'],point['h_imag'])==pytest.approx(expected,abs=1e-14)
    assert 'PHASE_OR_TIMING_MARGIN_EXCEEDED' in result['frequencies'][-1]['reasons']


def test_against_explicit_non_fft_complex_sums(fixture):
    result=m.analyze(fixture);n=128;k=7;xs=[];ys=[]
    for block in range(8):
        pair=[]
        for side in ('reference','dut'):
            a=[row['value'] for row in fixture[side][block*n:(block+1)*n]];mean=math.fsum(a)/n
            pair.append(sum((a[i]-mean)*cmath.exp(-2j*math.pi*k*i/n) for i in range(n))*2/n)
        xs.append(pair[0]);ys.append(pair[1])
    expected=sum(x.conjugate()*y for x,y in zip(xs,ys))/sum(abs(x)**2 for x in xs)
    row=next(p for p in result['frequencies'] if p['frequency_hz']==k*4096/n)
    assert complex(row['h_real'],row['h_imag'])==pytest.approx(expected,abs=1e-14)


def test_blind_factorization_is_not_identifiable():
    frequencies=np.array([10.,100.,1000.]);plant=np.array([1+2j,3-1j,2.]);mount=np.array([1.,1.1+.1j,2+1j])
    change=np.array([2+.1j,1.5-.2j,.7+.4j])
    assert np.allclose(plant*mount,(plant*change)*(mount/change))
    assert not np.allclose(mount,mount/change)


def test_offline_inspection_recomputes_without_external_solver(fixture,monkeypatch):
    result=m.analyze(fixture);ref=content_identity(fixture)
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('external solver invoked'))
    assert m.inspect(result,fixture,expected_input_ref=ref)['status']=='NUMERICAL_PREVIEW_REPRODUCED'
    bad=deepcopy(result);bad['frequencies'][0]['h_real']=10;bad=reseal(bad)
    with pytest.raises(ValueError):m.inspect(bad,fixture,expected_input_ref=ref)
    with pytest.raises(ValueError):m.inspect(result,fixture,expected_input_ref='sha256:'+'0'*64)


def test_cli_checks_exact_bytes_and_does_not_overwrite(tmp_path):
    source=tmp_path/'request.json';out=tmp_path/'result.json'
    assert m.main(['example','--output',str(source)])==0
    original=source.read_bytes();ref='sha256:'+sha256(original).hexdigest()
    assert m.main(['analyze',str(source),'--input-ref','sha256:'+'0'*64,'--output',str(out)])==2
    assert not out.exists() and source.read_bytes()==original
    assert m.main(['analyze',str(source),'--input-ref',ref,'--output',str(out)])==0
    result=json.loads(out.read_bytes());assert result['input_ref']==ref
    assert m.main(['analyze',str(source),'--input-ref',ref,'--output',str(out)])==2
    assert source.read_bytes()==original
