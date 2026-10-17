from copy import deepcopy
from decimal import Decimal, localcontext
import math
import random
import pytest
from ciw import polymer_population as pp


@pytest.fixture
def population():
    return {'schema':pp.SCHEMA,'model':pp.MODEL,'weight_basis':'number',
            'bins':[{'id':'short','molar_mass_kg_mol':10,'weight':1},
                    {'id':'long','molar_mass_kg_mol':100,'weight':1}]}


def test_hand_computable_population(population):
    r=pp.analyze(population)
    assert r['number_average_kg_mol']==55
    assert r['mass_average_kg_mol']==pytest.approx(10100/110)
    assert r['z_average_kg_mol']==pytest.approx(1001000/10100)
    assert r['dispersity']==pytest.approx((10100/110)/55)
    assert r['number_fraction']==[.5,.5]
    assert r['mass_fraction']==pytest.approx([1/11,10/11])


def test_basis_conversion_and_basis_not_interchangeable(population):
    a=pp.analyze(population)
    population['weight_basis']='mass'
    b=pp.analyze(population)
    assert b['number_average_kg_mol']!=a['number_average_kg_mol']
    for item,w in zip(population['bins'],a['mass_fraction']):item['weight']=w
    c=pp.analyze(population)
    for key in ['number_average_kg_mol','mass_average_kg_mol','z_average_kg_mol','dispersity']:
        assert c[key]==pytest.approx(a[key],rel=1e-14)


@pytest.mark.parametrize('basis',['number','mass'])
@pytest.mark.parametrize('mass',[.001,1,10,1e6])
def test_uniform_population(population,basis,mass):
    population['weight_basis']=basis
    for b in population['bins']:b['molar_mass_kg_mol']=mass
    r=pp.analyze(population)
    assert r['dispersity']==pytest.approx(1)
    for key in ['number_average_kg_mol','mass_average_kg_mol','z_average_kg_mol']:
        assert r[key]==pytest.approx(mass)


@pytest.mark.parametrize('factor',[1e-200,1e-9,1000])
def test_weight_scaling(population,factor):
    a=pp.analyze(population)
    for b in population['bins']:b['weight']*=factor
    assert pp.analyze(population)==a


def test_zero_bin_and_bin_split_and_permutation(population):
    a=pp.analyze(population)
    population['bins']=[{'id':'long','molar_mass_kg_mol':100,'weight':1},
        {'id':'s1','molar_mass_kg_mol':10,'weight':.4},
        {'id':'s2','molar_mass_kg_mol':10,'weight':.6},
        {'id':'zero','molar_mass_kg_mol':1e6,'weight':0}]
    b=pp.analyze(population)
    for key in ['number_average_kg_mol','mass_average_kg_mol','z_average_kg_mol','dispersity']:
        assert a[key]==pytest.approx(b[key])
    assert b['number_fraction'][-1]==0


def test_molar_mass_scaling(population):
    a=pp.analyze(population)
    for b in population['bins']:b['molar_mass_kg_mol']*=10
    c=pp.analyze(population)
    for key in ['number_average_kg_mol','mass_average_kg_mol','z_average_kg_mol']:
        assert c[key]==pytest.approx(10*a[key])
    assert c['dispersity']==pytest.approx(a['dispersity'])


@pytest.mark.parametrize('field',['schema','model','weight_basis','bins'])
def test_missing_field(population,field):
    del population[field]
    with pytest.raises(pp.Refusal):pp.analyze(population)


@pytest.mark.parametrize('value',[None,True,'bad',[],0])
def test_bad_weight_basis(population,value):
    population['weight_basis']=value
    with pytest.raises(pp.Refusal):pp.analyze(population)


@pytest.mark.parametrize('field,value',[('id','long'),('id',''),('id','x'*33),
    ('molar_mass_kg_mol',0),('molar_mass_kg_mol',-1),('molar_mass_kg_mol',1e7),
    ('molar_mass_kg_mol',True),('weight',True),('weight',-1),('weight',1e13),
    ('weight',float('inf')),('weight',float('nan')),('weight',10**1000)])
def test_invalid_bins(population,field,value):
    population['bins'][0][field]=value
    with pytest.raises(pp.Refusal):pp.analyze(population)


def test_explicit_unit_and_weight_presence(population):
    population['bins'][0]['molar_mass_g_mol']=population['bins'][0].pop('molar_mass_kg_mol')
    with pytest.raises(pp.Refusal):pp.analyze(population)


def test_zero_population_and_underflow_refused(population):
    for b in population['bins']:b['weight']=0
    with pytest.raises(pp.Refusal):pp.analyze(population)
    population['bins'][0]['weight']=math.ulp(0.0)
    population['bins'][1]['weight']=1e12
    with pytest.raises(pp.Refusal,match='underflow'):pp.analyze(population)


@pytest.mark.parametrize('seed',range(20))
@pytest.mark.parametrize('basis',['number','mass'])
def test_decimal_reference(population,seed,basis):
    rng=random.Random(seed)
    population['weight_basis']=basis
    population['bins']=[{'id':f'b{i}','molar_mass_kg_mol':10**rng.uniform(-3,6),
                         'weight':10**rng.uniform(-12,12)} for i in range(12)]
    before=deepcopy(population);r=pp.analyze(population)
    assert population==before
    with localcontext() as ctx:
        ctx.prec=60
        masses=[Decimal.from_float(b['molar_mass_kg_mol']) for b in population['bins']]
        weights=[Decimal.from_float(b['weight']) for b in population['bins']]
        n=weights if basis=='number' else [w/m for w,m in zip(weights,masses)]
        m0=sum(n);m1=sum(w*m for w,m in zip(n,masses))
        m2=sum(w*m*m for w,m in zip(n,masses));m3=sum(w*m*m*m for w,m in zip(n,masses))
        expected=[m1/m0,m2/m1,m3/m2,m0*m2/m1**2]
    for k,v in zip(['number_average_kg_mol','mass_average_kg_mol','z_average_kg_mol','dispersity'],expected):
        assert r[k]==pytest.approx(float(v),rel=2e-14)
    assert sum(r['mass_fraction'])==pytest.approx(1)
    assert sum(r['number_fraction'])==pytest.approx(1)
