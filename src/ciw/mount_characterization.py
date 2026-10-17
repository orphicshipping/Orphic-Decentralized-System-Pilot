"""Offline, reference-paired installation response preview over NET observations.

One-axis, periodic disjoint blocks only. This estimates relative chain response,
not a mount isolated from sensor/electronic/reference dynamics. It does not
acquire, excite, calibrate, invert a response, change a model or qualify hardware.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

import numpy as np

from .control_contracts import keys, text, content_ref, observation, validate_observation
from .core.identities import content_identity
from .hardware_workflow import read_bytes
from .link_store import safe_path
from .operations.runner import seal, check_seal
from .telemetry import canonical
from .adapters.subprocess import _json

SCHEMA='ciw.mount-paired-frf-request.v1'
FRAME='mount-test.axis-x'
UNIT='m/s^2'
MAX_BYTES=8*1024*1024
SCOPE={'physical_qualification':'HELD','isolated_mount_transfer':'NOT_IDENTIFIED',
       'uncertainty':'UNAVAILABLE','device_access':False,'excitation_performed':False,
       'inverse_correction_applied':False,'model_update':False,'control_authority':False,
       'native_solver_execution':False,'session_operation_registered':False}


def _finite(value, lo, hi, name):
    if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:
        raise ValueError('Invalid finite bound: '+name)
    return float(value)


def validate_request(request):
    if len(canonical(request))>MAX_BYTES:raise ValueError('Mount request exceeds finite record budget')
    keys(request,{'schema','test_id','mount_id','mount_revision','temperature_K',
        'sample_rate_hz','block_samples','estimator','phase_skew_bound_s',
        'reference_response','source_quality_flags','limits','reference','dut'})
    if request['schema']!=SCHEMA or request['estimator']!='H1.periodic_disjoint_blocks.v1':
        raise ValueError('Require the bounded one-axis periodic reference-paired profile')
    for name in ('test_id','mount_id','mount_revision'):text(request[name])
    _finite(request['temperature_K'],1,1000,'temperature_K')
    fs=_finite(request['sample_rate_hz'],1,100000,'sample_rate_hz')
    n=request['block_samples']
    if type(n) is not int or n not in (16,32,64,128,256,512,1024):
        raise ValueError('Require an explicitly supported block length')
    if request['phase_skew_bound_s'] is not None:
        _finite(request['phase_skew_bound_s'],0,1,'phase_skew_bound_s')
    if request['reference_response'] not in {'UNKNOWN','DECLARED_MATCHED_CHAIN_RESPONSES'}:
        raise ValueError('A data declaration cannot assert calibration or isolated-mount identification')
    flags=request['source_quality_flags']
    if type(flags) is not list or len(flags)>32:raise ValueError('Invalid source quality flags')
    for flag in flags:text(flag)
    keys(request['limits'],{'input_bin_power_floor','minimum_coherence','gain_error','phase_error_deg','maximum_frequency_hz'})
    for name,lo,hi in [('input_bin_power_floor',1e-24,1e12),('minimum_coherence',0,1),
                       ('gain_error',0,1),('phase_error_deg',0,180),('maximum_frequency_hz',1,fs/2)]:
        _finite(request['limits'][name],lo,hi,name)
    arrays=[];origins=set();entities=[];count=None;clock_id=None;t0=None
    for side in ('reference','dut'):
        stream=request[side]
        if type(stream) is not list or len(stream)>4096 or len(stream)%n:
            raise ValueError('Require complete, bounded blocks; no cropping or padding')
        if not 4<=len(stream)//n<=32:
            raise ValueError('At least four declared replicate blocks are required; one block gives misleading coherence')
        if count is not None and count!=len(stream):raise ValueError('Paired sample counts differ')
        count=len(stream);values=[];identity=None
        for i,row in enumerate(stream):
            validate_observation(row)
            if row['quantity']!='acceleration' or row['unit']!=UNIT or row['frame']!=FRAME:
                raise ValueError('Exact scalar acceleration unit/frame required; no silent g or axis conversion')
            value=_finite(row['value'],-1e6,1e6,'acceleration')
            if identity is None:identity=deepcopy(row['identity'])
            if row['identity']!=identity:raise ValueError('Source identity changes within a stream')
            if clock_id is None:clock_id=row['clock']['id'];t0=row['clock']['time_s']
            if row['clock']['id']!=clock_id or row['clock']['time_s']!=t0+i/fs:
                raise ValueError('Unknown clock relation, nonuniform timestamps or sample gap; no interpolation')
            origins.add(row['provenance']['semantics']);values.append(value)
        entities.append(identity['entity_id']);arrays.append(np.asarray(values,dtype=np.float64).reshape(-1,n))
    if len(set(entities))!=2:raise ValueError('A reference and DUT must be distinct source entities')
    if len(origins)!=1 or not origins<={'simulated','observed'}:
        raise ValueError('Do not mix simulated evidence with observations or reference labels')
    if request['limits']['maximum_frequency_hz'] < fs/n:
        raise ValueError('Selected range contains no nonzero Fourier bin')
    return arrays,origins.pop(),clock_id,t0


def analyze(request, *, input_ref=None):
    arrays,origin,clock,t0=validate_request(request)
    if input_ref is None:input_ref=content_identity(request)
    content_ref(input_ref)
    base={'schema':'ciw.mount-frf-review.v1','input_ref':input_ref,
          'canonical_request_ref':content_identity(request),'test_id':request['test_id'],
          'mount_id':request['mount_id'],'mount_revision':request['mount_revision'],
          'temperature_K':request['temperature_K'],'origin':origin,
          'program':{'source_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),
                     'numpy':np.__version__,'estimator':request['estimator']},
          'scope':dict(SCOPE),'verification_id':None,'new_scientific_execution_id':None,
          'assumptions':['approximately linear time-invariant response during the test',
            'coherent periodic blocks; no unmodeled startup/leakage',
            'reference input noise negligible for the H1 estimator',
            'source timing, orientation and chain-response declarations require independent evidence',
            'coherence is a diagnostic, not calibrated uncertainty or identification of a physical cause']}
    if request['source_quality_flags']:
        return seal({**base,'status':'SOURCE_REVIEW_REQUIRED','flags':deepcopy(request['source_quality_flags']),
                     'frequencies':[],'passing_sampled_frequencies_hz':[],
                     'continuous_qualified_band_hz':None})
    n=request['block_samples'];fs=request['sample_rate_hz'];limit=request['limits']
    x,y=[a-a.mean(axis=1,keepdims=True) for a in arrays]
    X,Y=np.fft.rfft(x,axis=1)*(2/n),np.fft.rfft(y,axis=1)*(2/n)
    sxx=np.mean(np.abs(X)**2,axis=0);syy=np.mean(np.abs(Y)**2,axis=0)
    sxy=np.mean(np.conj(X)*Y,axis=0)
    rows=[];passing=[]
    for k in range(1,n//2):  # DC and Nyquist do not carry the same phase information.
        frequency=k*fs/n
        if frequency>limit['maximum_frequency_hz']:break
        reasons=[];h=None;coherence=None;phase=None;gain=None
        if sxx[k]<limit['input_bin_power_floor'] or syy[k]<limit['input_bin_power_floor']:
            reasons.append('INSUFFICIENT_BIN_POWER')
        else:
            h=sxy[k]/sxx[k];gain=float(abs(h));phase=float(np.angle(h)*180/math.pi)
            coherence=float(abs(sxy[k])**2/(sxx[k]*syy[k]))
            if not 0<=coherence<=1+1e-12:raise ValueError('Invalid numerical coherence; no fabricated diagnostic')
            # Keep the original small roundoff excess if any. Do not use it as
            # physical confidence or clamp it into a metrology claim.
            if coherence<limit['minimum_coherence']:reasons.append('LOW_COHERENCE')
            if abs(gain-1)>limit['gain_error']:reasons.append('GAIN_DEVIATION')
            skew=request['phase_skew_bound_s']
            if skew is None:reasons.append('TIMING_BOUND_UNKNOWN')
            elif abs(phase)+360*frequency*skew>limit['phase_error_deg']:
                reasons.append('PHASE_OR_TIMING_MARGIN_EXCEEDED')
        if request['reference_response']=='UNKNOWN':reasons.append('CHAIN_RESPONSE_UNCHARACTERIZED')
        quality='WITHIN_DECLARED_POINT_LIMITS' if not reasons else 'REVIEW_REQUIRED'
        if not reasons:passing.append(frequency)
        native={}
        for name,value,unit in [('relative_gain',gain,'1'),('relative_phase',phase,'deg'),('coherence',coherence,'1')]:
            native[name]=observation(identity={'model_id':'H1.relative_chain.v1','entity_id':request['mount_id'],'execution_id':None},
                clock={'id':clock,'time_s':t0},frame=FRAME,quantity=name,value=value,unit=unit,
                provenance={'provider':'ciw.mount_characterization','sources':[input_ref],
                    'semantics':'simulated' if origin=='simulated' else 'estimated'})
        rows.append({'frequency_hz':frequency,'input_bin_power':float(sxx[k]),'input_bin_power_unit':'(m/s^2)^2',
            'h_real':None if h is None else float(h.real),'h_imag':None if h is None else float(h.imag),
            'status':quality,'reasons':reasons,'observations':native})
    return seal({**base,'status':'PAIRED_RESPONSE_PREVIEW','replicate_blocks':len(x),'window':'rectangular_periodic',
        'frequencies':rows,'passing_sampled_frequencies_hz':passing,'continuous_qualified_band_hz':None,
        'claim':'Relative installed-chain response at tested bins; not isolated mounting response, diagnosis or qualification'})


def inspect(report, request, *, expected_input_ref):
    check_seal(report);content_ref(expected_input_ref)
    if report['input_ref']!=expected_input_ref:raise ValueError('Review does not refer to selected input')
    if canonical(report)!=canonical(analyze(request,input_ref=expected_input_ref)):
        raise ValueError('Review does not reproduce under the current bounded method')
    return {'status':'NUMERICAL_PREVIEW_REPRODUCED','report_ref':report['record_digest'],'scope':dict(SCOPE)}


def example(*, compliant=False):
    """Fresh synthetic paired waveforms; not Elmer or a physical reference trace."""
    fs=4096.;n=128;blocks=8;rng=np.random.default_rng(14791)
    x=rng.normal(size=(blocks,n));X=np.fft.rfft(x,axis=1)
    if compliant:
        r=np.fft.rfftfreq(n,1/fs)/512.0;zeta=.12
        H=(1+2j*zeta*r)/(1-r*r+2j*zeta*r);H[-1]=H[-1].real
    else:H=np.ones(n//2+1)
    y=np.fft.irfft(X*H,n=n,axis=1)
    reference=[];dut=[]
    seed_ref=content_identity({'seed':14791,'fixture':'periodic_linear_mount' if compliant else 'unity_chain'})
    for name,data,rows in [('synthetic.reference',x,reference),('synthetic.dut',y,dut)]:
        for i,value in enumerate(data.ravel()):
            rows.append(observation(identity={'model_id':'synthetic.periodic_fixture.v1','entity_id':name,'execution_id':None},
                clock={'id':'synthetic.sample_clock','time_s':i/fs},frame=FRAME,quantity='acceleration',value=float(value),unit=UNIT,
                provenance={'provider':'ciw.synthetic-mount-fixture','sources':[seed_ref],'semantics':'simulated'}))
    return {'schema':SCHEMA,'test_id':'synthetic.mount.001','mount_id':'synthetic.mount.compliant' if compliant else 'synthetic.mount.unity',
        'mount_revision':'fixture.1','temperature_K':293.15,'sample_rate_hz':fs,'block_samples':n,
        'estimator':'H1.periodic_disjoint_blocks.v1','phase_skew_bound_s':0.0,
        'reference_response':'DECLARED_MATCHED_CHAIN_RESPONSES','source_quality_flags':[],
        'limits':{'input_bin_power_floor':1e-12,'minimum_coherence':.95,'gain_error':.05,
                  'phase_error_deg':5.,'maximum_frequency_hz':1536.},'reference':reference,'dut':dut}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('example');p.add_argument('--compliant',action='store_true');p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('analyze');p.add_argument('request',type=Path);p.add_argument('--input-ref',required=True);p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        output=safe_path(args.output)
        if output.exists() or not output.parent.is_dir():raise ValueError('Choose a new file in an explicit existing directory')
        if args.command=='example':value=example(compliant=args.compliant)
        else:
            raw=read_bytes(safe_path(args.request),limit=MAX_BYTES);content_ref(args.input_ref)
            if 'sha256:'+sha256(raw).hexdigest()!=args.input_ref:raise ValueError('Request differs from external exact-byte selection')
            value=analyze(_json(raw),input_ref=args.input_ref)
        with output.open('xb') as stream:stream.write(canonical(value))
        print(json.dumps({'output':str(output),'bytes_ref':'sha256:'+sha256(output.read_bytes()).hexdigest(),'scope':SCOPE}))
        return 0
    except (ValueError,TypeError,KeyError,OSError,OverflowError) as exc:
        print(json.dumps({'status':'REFUSED','reason':str(exc),'scope':SCOPE}),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
