from copy import deepcopy
from decimal import Decimal, localcontext
import math
import random

import pytest

from ciw import polymer_viscoelastic as pv


@pytest.fixture
def specimen():
    return {"schema": pv.SCHEMA, "model": pv.MODEL,
            "equilibrium_modulus_pa": 1e6,
            "modes": [{"id":"fast","modulus_pa":2e6,"relaxation_time_s":0.1},
                      {"id":"slow","modulus_pa":3e6,"relaxation_time_s":10}],
            "temperature_k":300, "initial_condition":"instantaneous-elastic-at-t0",
            "time_s":[0,0.01,0.1,1,10,100], "strain":[0.01]*6,
            "frequency_hz":[0,0.01,1,100,1e9]}


@pytest.mark.parametrize('key', sorted(pv.FIELDS))
def test_missing_field(specimen,key):
    del specimen[key]
    with pytest.raises(pv.Refusal): pv.validate_request(specimen)


@pytest.mark.parametrize('value',[True,False,None,'300',float('nan'),float('inf'),10**1000])
def test_nonnumeric(specimen,value):
    specimen['temperature_k']=value
    with pytest.raises(pv.Refusal): pv.validate_request(specimen)


@pytest.mark.parametrize('field,value',[
    ('schema','wrong'),('model','wrong'),('initial_condition','arbitrary-prehistory'),
    ('equilibrium_modulus_pa',-1),('equilibrium_modulus_pa',1e13),
    ('temperature_k',199),('temperature_k',601),('modes',{}),
    ('time_s',[0]),('time_s',[1,2]),('time_s',[0,1,1]),('time_s',[0,True]),
    ('time_s',[0,1e-10]),('time_s',[0,1e7]),('strain',[0.1]*6),
    ('strain',[True]*6),('strain',[0]),('frequency_hz',[1,1]),('frequency_hz',[-1]),
    ('frequency_hz',[True]),('frequency_hz',[1e10]),('frequency_hz',[0]*257)])
def test_bad_request(specimen,field,value):
    specimen[field]=value
    with pytest.raises(pv.Refusal): pv.validate_request(specimen)


@pytest.mark.parametrize('field,value',[('id',''),('id','a'*33),('id','../bad'),('id',True),
    ('modulus_pa',0),('modulus_pa',-1),('modulus_pa',True),('relaxation_time_s',0),
    ('relaxation_time_s',float('nan')),('relaxation_time_s',1e10)])
def test_bad_modes(specimen,field,value):
    specimen['modes'][0][field]=value
    with pytest.raises(pv.Refusal): pv.validate_request(specimen)


def test_modes_and_total_caps(specimen):
    p=deepcopy(specimen);p['modes']=[p['modes'][0]]*33
    with pytest.raises(pv.Refusal): pv.validate_request(p)
    p=deepcopy(specimen);p['modes'][1]['id']='fast'
    with pytest.raises(pv.Refusal): pv.validate_request(p)
    p=deepcopy(specimen);p['modes'][0]['modulus_pa']=1e12
    with pytest.raises(pv.Refusal): pv.validate_request(p)
    p=deepcopy(specimen);p['modes']=[];p['equilibrium_modulus_pa']=0
    with pytest.raises(pv.Refusal): pv.validate_request(p)
    p=deepcopy(specimen);p['modes'][0]['unknown']=True
    with pytest.raises(pv.Refusal): pv.validate_request(p)


def test_unexpected_code_or_unit_alias_refused(specimen):
    for key in ['code','temperature_c','stress_mpa','provider']:
        with pytest.raises(pv.Refusal): pv.validate_request({**specimen,key:'not accepted'})


def test_relaxation_analytic(specimen):
    result=pv.simulate(specimen)['trajectory']
    for t,s in zip(result['time_s'],result['stress_pa']):
        expected=.01*(1e6+2e6*math.exp(-t/.1)+3e6*math.exp(-t/10))
        assert s==pytest.approx(expected,rel=1e-13,abs=1e-8)
    assert all(w==0 for w in result['work_j_m3'])
    assert all(b<=a for a,b in zip(result['stress_pa'],result['stress_pa'][1:]))
    assert result['dissipated_energy_j_m3'][-1]>0


def test_ramp_hold_unload_compression(specimen):
    specimen['strain']=[0,.001,.01,.01,0,-.01]
    r=pv.simulate(specimen)
    assert r['balance_metrics']['energy_balance_max_abs_j_m3']<1e-8
    assert r['trajectory']['stress_pa'][-1]<0
    assert r['trajectory']['dissipated_energy_j_m3'][-1]>0


def test_pure_elastic_and_zero(specimen):
    specimen['modes']=[];specimen['strain']=[0,.001,.01,0,-.01,0]
    r=pv.simulate(specimen)
    assert r['trajectory']['stress_pa']==[1e6*e for e in specimen['strain']]
    assert all(d==0 for d in r['trajectory']['dissipated_energy_j_m3'])
    assert r['trajectory']['work_j_m3'][-1]==pytest.approx(0,abs=1e-12)
    specimen['strain']=[0]*6
    r=pv.simulate(specimen)
    assert all(s==0 for s in r['trajectory']['stress_pa'])


def test_frequency_complex_oracle_and_hz(specimen):
    r=pv.dynamic_moduli(specimen)
    for f,re,im in zip(r['frequency_hz'],r['storage_modulus_pa'],r['loss_modulus_pa']):
        exact=complex(specimen['equilibrium_modulus_pa'])
        for m in specimen['modes']:
            z=complex(0,2*math.pi*f*m['relaxation_time_s'])
            exact+=m['modulus_pa']*z/(1+z)
        assert re==pytest.approx(exact.real,rel=1e-14)
        assert im==pytest.approx(exact.imag,rel=1e-14,abs=1e-15)
        assert im>=0
    assert r['storage_modulus_pa'][0]==1e6 and r['loss_modulus_pa'][0]==0
    assert r['storage_modulus_pa'][-1]==pytest.approx(6e6)


def test_single_maxwell_loss_peak(specimen):
    specimen['equilibrium_modulus_pa']=0;specimen['modes']=specimen['modes'][:1]
    specimen['frequency_hz']=[1/(2*math.pi*.1)]
    r=pv.dynamic_moduli(specimen)
    assert r['storage_modulus_pa']==pytest.approx([1e6])
    assert r['loss_modulus_pa']==pytest.approx([1e6])


def test_mode_permutation_and_no_mutation(specimen):
    before=deepcopy(specimen);a=pv.simulate(specimen)
    assert specimen==before
    specimen['modes'].reverse();b=pv.simulate(specimen)
    assert a['trajectory']['stress_pa']==b['trajectory']['stress_pa']
    assert a['trajectory']['work_j_m3']==b['trajectory']['work_j_m3']
    assert a['trajectory']['mode_stress_pa']==[row[::-1] for row in b['trajectory']['mode_stress_pa']]


def test_grid_refinement_semigroup(specimen):
    specimen.update(time_s=[0,1,4],strain=[0,.01,-.01])
    a=pv.simulate(specimen)['trajectory']
    specimen.update(time_s=[0,.5,1,2.5,4],strain=[0,.005,.01,0,-.01])
    b=pv.simulate(specimen)['trajectory']
    for key in ['stress_pa','work_j_m3','stored_energy_j_m3','dissipated_energy_j_m3']:
        assert a[key]==pytest.approx([b[key][i] for i in [0,2,4]],rel=1e-12,abs=1e-9)


@pytest.mark.parametrize('factor',[-1,.1,1.5])
def test_linear_scaling_and_quadratic_energy(specimen,factor):
    a=pv.simulate(specimen)['trajectory']
    specimen['strain']=[factor*e for e in specimen['strain']]
    b=pv.simulate(specimen)['trajectory']
    assert b['stress_pa']==pytest.approx([factor*v for v in a['stress_pa']],abs=1e-8)
    for key in ['work_j_m3','stored_energy_j_m3','dissipated_energy_j_m3']:
        assert b[key]==pytest.approx([factor**2*v for v in a[key]],abs=1e-8)


@pytest.mark.parametrize('x',[1e-18,1e-12,1e-6,.01,.049999,.05,.050001,.2,1,100,1e6,1e15])
@pytest.mark.parametrize('case',['rest','load','unload'])
def test_segment_against_decimal_oracle(x,case):
    q0=0 if case=='rest' else 10000
    de=.003 if case!='unload' else -.003
    E=2e6
    q,w,d=pv._segment(q0,E,1,x,de)
    with localcontext() as ctx:
        ctx.prec=100
        X=Decimal(str(x));Q=Decimal(q0);M=Decimal(str(E));DE=Decimal(str(de))
        a=(-X).exp();steady=M*DE/X;transient=Q-steady
        qe=steady+transient*a
        we=DE*(steady+transient*(1-a)/X)
        diss=(steady**2*X+2*steady*transient*(1-a)+transient**2*(1-a*a)/2)/M
    assert q==pytest.approx(float(qe),rel=2e-12,abs=1e-10)
    assert w==pytest.approx(float(we),rel=2e-11,abs=1e-12)
    assert d==pytest.approx(float(diss),rel=2e-10,abs=1e-25)
    assert d>=0


@pytest.mark.parametrize('mutation',['stress','energy','loss','work','initial','grid','mode_order','negative_loss','bool'])
def test_trajectory_forgery_refused(specimen,mutation):
    r=pv.simulate(specimen)['trajectory']
    if mutation=='stress':r['stress_pa'][-1]+=10
    elif mutation=='energy':r['stored_energy_j_m3'][-1]+=1
    elif mutation=='loss':r['dissipated_energy_j_m3'][-1]+=1
    elif mutation=='work':r['work_j_m3'][-1]+=1
    elif mutation=='initial':r['mode_stress_pa'][0][0]=0
    elif mutation=='grid':r['time_s'][0]=False
    elif mutation=='mode_order':r['mode_order'].reverse()
    elif mutation=='negative_loss':r['dissipated_energy_j_m3'][-1]=-1e-20
    else:r['work_j_m3'][0]=False
    with pytest.raises(pv.Refusal):pv.check_balances(specimen,r)


def test_wrong_relaxation_can_balance(specimen):
    wrong=deepcopy(specimen);wrong['modes'][0]['relaxation_time_s']*=10
    r=pv.simulate(wrong)['trajectory']
    pv.check_balances(specimen,r)
    assert r['stress_pa'][2]!=pytest.approx(pv.simulate(specimen)['trajectory']['stress_pa'][2],rel=.01)


@pytest.mark.parametrize('method',['DOP853','Radau'])
@pytest.mark.parametrize('seed',range(8))
def test_independent_scipy_stress_work_dissipation(specimen,seed,method):
    np=pytest.importorskip('numpy');integrate=pytest.importorskip('scipy.integrate')
    rng=random.Random(seed)
    specimen['modes']=[{'id':f'm{i}','modulus_pa':rng.uniform(1e5,2e6),
                       'relaxation_time_s':rng.uniform(.05,4)} for i in range(3)]
    specimen['time_s']=[0,.02,.1,.3,1,3]
    specimen['strain']=[rng.uniform(-.01,.01) for _ in range(6)]
    r=pv.simulate(specimen)['trajectory']
    E=np.array([m['modulus_pa'] for m in specimen['modes']]);tau=np.array([m['relaxation_time_s'] for m in specimen['modes']])
    state=np.r_[E*specimen['strain'][0],0.,0.]
    for i,(t0,t1) in enumerate(zip(specimen['time_s'],specimen['time_s'][1:])):
        v=(specimen['strain'][i+1]-specimen['strain'][i])/(t1-t0)
        def rhs(t,y):
            strain=specimen['strain'][i]+v*(t-t0)
            q=y[:3]
            stress=specimen['equilibrium_modulus_pa']*strain+sum(q)
            return np.r_[E*v-q/tau,stress*v,sum(q*q/(E*tau))]
        sol=integrate.solve_ivp(rhs,(t0,t1),state,method=method,rtol=2e-12,atol=1e-10)
        assert sol.success
        state=sol.y[:,-1]
        assert r['mode_stress_pa'][i+1]==pytest.approx(state[:3],rel=2e-9,abs=1e-5)
        assert r['work_j_m3'][i+1]==pytest.approx(state[3],rel=2e-9,abs=1e-6)
        assert r['dissipated_energy_j_m3'][i+1]==pytest.approx(state[4],rel=2e-9,abs=1e-6)


def test_maximum_modes_and_samples(specimen):
    specimen['equilibrium_modulus_pa']=0
    specimen['modes']=[{'id':f'm{i}','modulus_pa':1e6,'relaxation_time_s':10.**(-9+18*i/31)} for i in range(32)]
    specimen['time_s']=[float(i) for i in range(2048)]
    specimen['strain']=[.01*math.sin(i*.1) for i in range(2048)]
    result=pv.simulate(specimen)
    assert result['work_accounting']['mode_updates']==2047*32
    assert result['trajectory']['dissipated_energy_j_m3'][-1]>0
