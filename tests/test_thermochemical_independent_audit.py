"""Additional independent audits of PR173, without importing its numerical RHS."""
import copy
import math
import random

import numpy as np
import pytest
solve_ivp = pytest.importorskip("scipy.integrate", reason="Independent chemistry audit requires numerical-checks").solve_ivp

from ciw import thermochemical_benchmark as c


def request(seed=0):
    rng=random.Random(seed)
    cv=10**rng.uniform(4,7)
    return {"schema":c.REQUEST_SCHEMA,"model":c.MODEL,"species_order":["A","B"],
        "initial_concentration_mol_m3":[rng.uniform(.1,4),rng.uniform(0,.2)],
        "initial_temperature_k":310,"bath_temperature_k":rng.uniform(285,340),
        "reference_temperature_k":310,"reference_rate_s_inv":10**rng.uniform(-3,.3),
        "activation_energy_j_mol":rng.uniform(1000,95000),
        "reaction_internal_energy_j_mol":rng.uniform(-40000,40000),
        "heat_capacity_j_m3_k":cv,"heat_transfer_w_m3_k":cv*10**rng.uniform(-3,0),
        "volume_m3":10**rng.uniform(-6,0),"time_s":[0,.01,.1,.25,.5,1,2,5],
        "solver":{"rtol":1e-10,"atol_concentration_mol_m3":1e-12,
                  "atol_temperature_k":1e-10,"atol_heat_j":1e-10,"max_step_s":.5,"max_steps":100000}}


@pytest.mark.parametrize("method",["DOP853","Radau"])
@pytest.mark.parametrize("seed",range(16))
def test_nonlinear_chemistry_against_independent_absolute_temperature_rhs(seed,method):
    p=request(seed); actual=c.simulate(p)
    def rhs(t,y):
        a,b,temp,q=y
        exponent=p["activation_energy_j_mol"]/(8.31446261815324*temp)*(temp/p["reference_temperature_k"]-1)
        progress=p["reference_rate_s_inv"]*math.exp(exponent)*a
        heat_density=p["heat_transfer_w_m3_k"]*(temp-p["bath_temperature_k"])
        return [-progress,progress,(-p["reaction_internal_energy_j_mol"]*progress-heat_density)/p["heat_capacity_j_m3_k"],
                p["volume_m3"]*heat_density]
    y0=[*p["initial_concentration_mol_m3"],p["initial_temperature_k"],0]
    solved=solve_ivp(rhs,(0,5),y0,t_eval=p["time_s"],method=method,rtol=3e-12,atol=[1e-13,1e-13,1e-11,1e-11])
    assert solved.success
    values=actual["trajectory"]
    np.testing.assert_allclose(values["concentration_mol_m3"],solved.y[:2].T,rtol=3e-8,atol=2e-9)
    np.testing.assert_allclose(values["temperature_k"],solved.y[2],rtol=0,atol=2e-7)
    np.testing.assert_allclose(values["heat_to_bath_j"],solved.y[3],rtol=3e-8,atol=2e-5)
    assert actual["solver"]["attempted_steps"]==actual["solver"]["accepted_steps"]+actual["solver"]["rejected_steps"]


@pytest.mark.parametrize("cv",[1e3,1e6,1e9])
@pytest.mark.parametrize("volume",[1e-6,1])
@pytest.mark.parametrize("energy",[-20000,0,20000])
def test_closed_form_high_capacity_volume_and_reaction_signs(cv,volume,energy):
    p=request();p.update(activation_energy_j_mol=0,reference_rate_s_inv=.3,
        heat_capacity_j_m3_k=cv,volume_m3=volume,reaction_internal_energy_j_mol=energy,
        heat_transfer_w_m3_k=.3*cv,bath_temperature_k=310)
    a=c.simulate(p);ref=c.constant_rate_reference(p)
    assert c.compare_trajectories(p,a["trajectory"],ref)["within_policy"]
    assert max(abs(a["balance_metrics"]["concentration_balance_max_abs_mol_m3"]),0)<1e-9
    if energy:
        assert math.copysign(1,a["trajectory"]["temperature_change_k"][-1]) == -math.copysign(1,energy)


@pytest.mark.parametrize("offset",[0,-1e-12,1e-12,-.1,.1])
def test_equal_and_near_equal_thermal_reaction_rates(offset):
    p=request();p.update(activation_energy_j_mol=0,reference_rate_s_inv=.3)
    p["heat_transfer_w_m3_k"]=(.3+offset)*p["heat_capacity_j_m3_k"]
    assert c.make_report(p)["analytic_reference"]["status"]=="passed"


def test_conservation_cannot_substitute_for_trajectory_accuracy():
    p=request();p.update(activation_energy_j_mol=0,reference_rate_s_inv=.3)
    other=copy.deepcopy(p);other["reference_rate_s_inv"]*=2
    wrong=c.constant_rate_reference(other)
    c.check_balances(p,wrong)
    assert not c.compare_trajectories(p,wrong,c.constant_rate_reference(p))["within_policy"]


@pytest.mark.parametrize("field",["initial_temperature_k","reference_rate_s_inv","activation_energy_j_mol","volume_m3"])
@pytest.mark.parametrize("value",[True,None,"1",float("nan"),float("inf")])
def test_invalid_numeric_declarations_refuse(field,value):
    p=request();p[field]=value
    with pytest.raises(c.Refusal):c.validate_request(p)


def test_refused_budget_is_not_a_partial_success():
    p=request();p["solver"]["max_steps"]=1
    with pytest.raises(c.Refusal,match="budget exhausted"):c.simulate(p)


def test_rerun_keeps_content_and_allocates_new_execution_identity():
    p=request();a=c.make_report(p);b=c.make_report(p)
    assert a["request_sha256"]==b["request_sha256"]
    assert a["content_sha256"]==b["content_sha256"]
    assert a["execution_id"]!=b["execution_id"]
    assert a["authority"]["physical_validation"] is False
