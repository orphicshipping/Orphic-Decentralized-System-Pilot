from copy import deepcopy
from decimal import Decimal,localcontext
import math
import numpy as np
import pytest
from ciw import materials_uncertainty as mu,materials_cli as cli
from ciw.core.covariance import covariance_identity
from ciw.core.identities import content_identity


def rebind(p):
    c=p['covariance'];o=p['observation']
    c['reference_values']=deepcopy(o['values'])
    c['basis']['id']=content_identity(o)
    c['provenance']['source_evidence_ids']=deepcopy(o['source_refs'])
    c['covariance_id']=covariance_identity(c)


@pytest.mark.parametrize('kind',['complex-viscosity','joint-residual'])
def test_missing_covariance_held_not_zero(kind):
    p=cli.example(kind);p['covariance']=None
    r=mu.analyze(p)
    assert r['status']=='HELD' and r['calculation'] is None
    assert r['reason']=='joint-covariance-not-supplied'


def test_viscosity_decimal_uncertainty_and_offdiagonal():
    p=cli.example('complex-viscosity');r=mu.analyze(p)['calculation']
    assert r['value']==2.5
    with localcontext() as ctx:
        ctx.prec=60
        g=[Decimal('.3'),Decimal('.4'),Decimal('-1.25')]
        var=sum(g[i]*Decimal(str(p['covariance']['matrix'][i][j]))*g[j] for i in range(3) for j in range(3))
    assert r['standard_uncertainty']==pytest.approx(float(var.sqrt()),rel=2e-15)
    assert r['output_covariance']['matrix'][0][0]==pytest.approx(.022945,rel=2e-15)
    assert r['output_covariance']['provenance']['source_covariance_ids']==[p['covariance']['covariance_id']]
    p['covariance']['matrix'][0][1]=p['covariance']['matrix'][1][0]=0;rebind(p)
    assert mu.analyze(p)['calculation']['standard_uncertainty'] != r['standard_uncertainty']


def test_gradient_independent_finite_difference():
    p=cli.example('complex-viscosity');r=mu.analyze(p)['calculation'];v=np.array(p['observation']['values'])
    def f(x):return np.sqrt(x[0]**2+x[1]**2)/x[2]
    for i in range(3):
        eps=1e-5;left=v.copy();right=v.copy();left[i]-=eps;right[i]+=eps
        assert (f(right)-f(left))/(2*eps)==pytest.approx(r['sensitivity'][i],rel=1e-9)


def test_joint_score_includes_correlation_without_pvalue():
    p=cli.example('joint-residual');r=mu.analyze(p)['calculation']
    assert r['squared_mahalanobis']==pytest.approx(4/3)
    assert r['p_value'] is None and r['statistical_coverage']=='not-asserted'
    p['criterion']=1.0
    assert mu.analyze(p)['calculation']['outcome']=='OUTSIDE_DECLARED_THRESHOLD'
    p['covariance']['matrix']=[[4,0],[0,1]];rebind(p)
    assert mu.analyze(p)['calculation']['squared_mahalanobis']==pytest.approx(2)


@pytest.mark.parametrize('matrix',[[[1,1],[1,1]],[[0,0],[0,1]],[[1,1-1e-14],[1-1e-14,1]]])
def test_singular_joint_covariance_held_without_pseudoinverse(matrix):
    p=cli.example('joint-residual');p['covariance']['matrix']=matrix;rebind(p)
    r=mu.analyze(p);assert r['status']=='HELD' and r['calculation'] is None


@pytest.mark.parametrize('matrix',[[[1,2],[2,1]],[[-1,0],[0,1]],[[0,.001],[.001,1]],[[1,.3],[.2,1]]])
def test_invalid_covariance_not_repaired(matrix):
    p=cli.example('joint-residual');p['covariance']['matrix']=matrix;rebind(p);before=deepcopy(p)
    with pytest.raises(ValueError):mu.analyze(p)
    assert p==before


@pytest.mark.parametrize('fault',['source-ref','axis','unit','frame','basis','reference','digest','authority','extra-field'])
def test_declaration_covariance_binding_refusal(fault):
    p=cli.example('complex-viscosity');c=p['covariance']
    if fault=='source-ref':c['provenance']['source_evidence_ids']=['sha256:'+'0'*64]
    elif fault=='axis':c['quantity_ids'].reverse()
    elif fault=='unit':c['units'][0]='kPa'
    elif fault=='frame':c['frame']='another-condition'
    elif fault=='basis':c['basis']['id']='another-observation'
    elif fault=='reference':c['reference_values'][0]+=1
    elif fault=='digest':c['covariance_id']='sha256:'+'0'*64
    elif fault=='authority':p['observation']['source_kind']='native-calibrated-measurement'
    else:p['equipment_authorized']=True
    if fault!='digest':c['covariance_id']=covariance_identity(c)
    with pytest.raises(ValueError):mu.analyze(p)


@pytest.mark.parametrize('value',[True,None,'3',float('nan'),float('inf'),10**1000])
def test_bad_values_refuse(value):
    p=cli.example('complex-viscosity');p['observation']['values'][0]=value
    with pytest.raises((ValueError,OverflowError)):mu.analyze(p)


@pytest.mark.parametrize('omega',[0,-1])
def test_nonpositive_frequency_refuses(omega):
    p=cli.example('complex-viscosity');p['observation']['values'][2]=omega;rebind(p)
    with pytest.raises(ValueError):mu.analyze(p)


def test_zero_gradient_held():
    p=cli.example('complex-viscosity');p['observation']['values'][:2]=[0,0];rebind(p)
    assert mu.analyze(p)['reason']=='zero-modulus-gradient-undefined'


def test_no_double_counted_uncertain_prediction():
    p=cli.example('joint-residual');p['reference']['basis']='fitted-uncertain-prediction'
    with pytest.raises(ValueError):mu.analyze(p)


@pytest.mark.parametrize('factor',[1e-9,1e-3,1e3,1e9])
def test_joint_score_invariant_under_declared_unit_scaling(factor):
    p=cli.example('joint-residual');base=mu.analyze(p)['calculation']['squared_mahalanobis']
    p['observation']['values'][0]*=factor;p['observation']['units'][0]='explicit-test-unit'
    c=p['covariance'];c['units'][0]='explicit-test-unit'
    c['matrix'][0][0]*=factor**2;c['matrix'][0][1]*=factor;c['matrix'][1][0]*=factor
    rebind(p)
    assert mu.analyze(p)['calculation']['squared_mahalanobis']==pytest.approx(base,rel=1e-13)


def test_nist_covariance_holds_unchanged():
    from ciw import rheology_empirical as er
    r=er.run(cli.example('empirical-rheology'))
    assert r['holds']['complex_viscosity_uncertainty']['status']=='HELD'
    assert r['holds']['joint_frequency_goodness_of_fit']['status']=='HELD'
    assert r['holds']['current_instrument_calibration']['status']=='HELD'
