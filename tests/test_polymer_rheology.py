"""Independent responses, passive limits, data boundaries and CLI retention."""
import copy
import math
import random

import pytest

from ciw import polymer_rheology as r
from ciw.core.identities import content_identity, validate_identity
from ciw.operations.runner import check_seal, seal


@pytest.mark.parametrize("seed", range(20))
def test_seeded_spectra_pass_independent_decimal_audit(seed):
    rng = random.Random(seed)
    p = r.example_request()
    p["equilibrium_modulus_pa"] = 10**rng.uniform(-3, 11)
    p["step_strain"] = rng.choice([-1, 1])*10**rng.uniform(-8, -2)
    p["modes"] = [{"mode_id": str(i), "modulus_pa": 10**rng.uniform(-5, 11),
                   "relaxation_time_s": 10**rng.uniform(-8, 8)} for i in range(1+seed%8)]
    p["time_s"] = [0]+[10.0**i for i in range(-12, 10)]
    p["angular_frequency_rad_s"] = [0]+[10.0**i for i in range(-12, 13)]
    result = r.analyze(p)
    receipt = r.verify(result)
    assert receipt["status"] == "PASS", receipt["failures"]
    assert result["request"] == r.validate_request(p)


@pytest.mark.parametrize("exponent", [0, 1e-12, 0.01, 1, 10, 350, 370, 372, 374, 380, 700, 740, 744, 745, 750, 770, 800])
def test_exponential_tail_does_not_underflow_before_stiffness_scaling(exponent):
    p = r.example_request()
    p.update(equilibrium_modulus_pa=0, step_strain=0.01,
             modes=[{"mode_id": "one", "modulus_pa": 1e12, "relaxation_time_s": 1}],
             time_s=[0, exponent or 1e-12], angular_frequency_rad_s=[0, 1])
    receipt = r.verify(r.analyze(p))
    assert receipt["status"] == "PASS", receipt["failures"]


def test_one_mode_analytic_anchor():
    p = r.example_request()
    p.update(equilibrium_modulus_pa=10, modes=[{"mode_id": "one", "modulus_pa": 20, "relaxation_time_s": 2}],
             time_s=[0, 2], angular_frequency_rad_s=[0, 0.5])
    y = r.analyze(p)["response"]
    assert y["time_response"]["relaxation_modulus_pa"] == pytest.approx([30, 10+20/math.e])
    assert y["frequency_response"]["storage_modulus_pa"] == [10, 20]
    assert y["frequency_response"]["loss_modulus_pa"] == [0, 10]


@pytest.mark.parametrize("strain", [-0.02, 0, 0.02])
def test_elastic_limit_and_strain_sign(strain):
    p = r.example_request(); p.update(modes=[], step_strain=strain)
    result = r.analyze(p)
    y = result["response"]["time_response"]
    assert y["stress_pa"] == [strain*1e6]*len(p["time_s"])
    assert y["stored_energy_j_m3"] == [0.5*strain**2*1e6]*len(p["time_s"])
    assert y["dissipated_energy_j_m3"] == [0]*len(p["time_s"])
    assert r.verify(result)["status"] == "PASS"


def test_energy_balance_passivity_and_relaxation_monotonicity():
    p = r.example_request(); result = r.analyze(p)
    y = result["response"]["time_response"]
    initial = 0.5*p["step_strain"]**2*(p["equilibrium_modulus_pa"]+sum(m["modulus_pa"] for m in p["modes"]))
    for a,b in zip(y["stored_energy_j_m3"], y["dissipated_energy_j_m3"]):
        assert a+b == pytest.approx(initial, rel=2e-15)
    assert all(a>=b for a,b in zip(y["relaxation_modulus_pa"],y["relaxation_modulus_pa"][1:]))
    assert all(a<=b for a,b in zip(y["dissipated_energy_j_m3"],y["dissipated_energy_j_m3"][1:]))
    assert min(y["dissipation_rate_w_m3"]) >= 0


def test_mode_permutation_preserves_response_but_not_evidence():
    p = r.example_request(); before = r.analyze(p); p["modes"].reverse(); after=r.analyze(p)
    assert before["response"] == after["response"]
    assert before["evidence_id"] != after["evidence_id"]


def test_frequency_unit_is_angular_not_hz():
    p=r.example_request(); p.update(equilibrium_modulus_pa=0,
        modes=[{"mode_id":"one","modulus_pa":10,"relaxation_time_s":1}],
        angular_frequency_rad_s=[1,2*math.pi])
    f=r.analyze(p)["response"]["frequency_response"]
    assert f["loss_modulus_pa"][0] == 5
    assert f["loss_modulus_pa"][1] != pytest.approx(5)


@pytest.mark.parametrize("bad", [True, False, None, "300", float("nan"), float("inf"), -float("inf"), 10**200])
def test_non_numeric_temperatures_are_refused(bad):
    p=r.example_request(); p["temperature_k"]=bad
    with pytest.raises(ValueError): r.analyze(p)


@pytest.mark.parametrize("mutation", ["extra", "units", "negative_modulus", "zero_tau", "duplicate_mode", "too_many_modes",
    "too_many_times", "unordered_time", "zero_time_missing", "boolean_time", "subnormal_time", "zero_model",
    "too_large_strain", "too_small_strain", "false_origin", "source_ref", "frequency_negative", "frequency_duplicate"])
def test_request_contract_refusals(mutation):
    p=r.example_request()
    if mutation=="extra":p["extra"]=1
    elif mutation=="units":p["angular_frequency_hz"]=p.pop("angular_frequency_rad_s")
    elif mutation=="negative_modulus":p["modes"][0]["modulus_pa"]=-1
    elif mutation=="zero_tau":p["modes"][0]["relaxation_time_s"]=0
    elif mutation=="duplicate_mode":p["modes"][1]["mode_id"]=p["modes"][0]["mode_id"]
    elif mutation=="too_many_modes":p["modes"]*=17
    elif mutation=="too_many_times":p["time_s"]=list(range(257))
    elif mutation=="unordered_time":p["time_s"][1:3]=[1,0.1]
    elif mutation=="zero_time_missing":p["time_s"][0]=1e-5
    elif mutation=="boolean_time":p["time_s"][0]=False
    elif mutation=="subnormal_time":p["time_s"][1]=math.nextafter(0,math.inf)
    elif mutation=="zero_model":p.update(equilibrium_modulus_pa=0,modes=[])
    elif mutation=="too_large_strain":p["step_strain"]=0.021
    elif mutation=="too_small_strain":p["step_strain"]=1e-100
    elif mutation=="false_origin":p["source_kind"]="physically_validated"
    elif mutation=="source_ref":p["parameter_source_ref"]="unbound"
    elif mutation=="frequency_negative":p["angular_frequency_rad_s"][0]=-1
    elif mutation=="frequency_duplicate":p["angular_frequency_rad_s"][1]=0
    with pytest.raises(ValueError):r.analyze(p)


def test_identities_inspection_and_fresh_verification_remain_separate(monkeypatch):
    p=r.example_request(); untouched=copy.deepcopy(p); a=r.analyze(p); b=r.analyze(p)
    assert p==untouched and a["evidence_id"]==b["evidence_id"]==content_identity(r.validate_request(p))
    assert a["execution_id"]!=b["execution_id"] and a["result_id"]!=b["result_id"]
    for k in ("execution","result"):validate_identity(a[k+"_id"],k)
    original=copy.deepcopy(a)
    def prohibited(*args):raise AssertionError("inspection executed numerical science")
    with monkeypatch.context() as context:
        context.setattr(r,"_compute",prohibited)
        context.setattr("ciw.polymer_rheology_reference.reference_response",prohibited)
        assert r.validate_result(a) is a
    v1=r.verify(a);v2=r.verify(a)
    assert v1["verification_id"]!=v2["verification_id"] and a==original
    assert a["verification_status"]=="not_verified"
    check_seal(a);check_seal(v1)


@pytest.mark.parametrize("field", sorted(r.TIME_FIELDS|r.FREQUENCY_FIELDS))
def test_resealed_numerical_corruption_fails_independent_audit(field):
    result=r.analyze(r.example_request())
    group="time_response" if field in r.TIME_FIELDS else "frequency_response"
    result["response"][group][field][-1]+=1
    seal(result)
    assert r.verify(result)["status"]=="FAIL"


@pytest.mark.parametrize("mutation", ["seal", "shape", "bool", "negative_energy", "authority", "work", "request", "runtime", "id"])
def test_retained_contract_refusals(mutation):
    result=r.analyze(r.example_request())
    if mutation=="seal":result["response"]["time_response"]["stress_pa"][1]+=1
    elif mutation=="shape":result["response"]["time_response"]["stress_pa"].pop()
    elif mutation=="bool":result["response"]["frequency_response"]["loss_modulus_pa"][0]=False
    elif mutation=="negative_energy":result["response"]["time_response"]["dissipated_energy_j_m3"][0]=-1e-30
    elif mutation=="authority":result["authority"]["hardware_actuation"]=0
    elif mutation=="work":result["work"]["mode_time_evaluations"]+=1
    elif mutation=="request":result["request"]["temperature_k"]+=1
    elif mutation=="runtime":result["runtime"]["source_refs"].pop("ciw.polymer_rheology")
    elif mutation=="id":result["execution_id"]=result["result_id"]
    if mutation!="seal":seal(result)
    with pytest.raises(ValueError):r.validate_result(result)


def test_maximum_bounded_request():
    p=r.example_request();p["modes"]=[{"mode_id":str(i),"modulus_pa":1+i,"relaxation_time_s":1+i} for i in range(32)]
    p["time_s"]=list(range(256));p["angular_frequency_rad_s"]=list(range(256))
    result=r.analyze(p)
    assert sum(result["work"].values())==r.MAX_WORK
    assert r.verify(result)["status"]=="PASS"


def test_cli_roundtrip_and_create_only_publication(tmp_path,capfd):
    from ciw.polymer_cli import main
    from ciw.control_contracts import load
    p,result,audit=[tmp_path/name for name in ("request.json","result.json","audit.json")]
    assert main(["rheology","example","--output",str(p)])==0
    assert main(["rheology","analyze",str(p),"--output",str(result)])==0
    original=result.read_bytes()
    assert main(["rheology","inspect",str(result)])==0
    assert main(["rheology","verify",str(result),"--output",str(audit)])==0
    assert load(audit)["status"]=="PASS"
    assert main(["rheology","analyze",str(p),"--output",str(result)])==1
    assert result.read_bytes()==original
    p.write_text('{"schema":1,"schema":2}')
    assert main(["rheology","analyze",str(p),"--output",str(tmp_path/"refused.json")])==1
    assert not (tmp_path/"refused.json").exists()


def test_verifier_does_not_call_production_kernel(monkeypatch):
    result=r.analyze(r.example_request())
    def prohibited(*args):raise AssertionError("verification reused production kernel")
    monkeypatch.setattr(r,"_compute",prohibited)
    assert r.verify(result)["status"]=="PASS"


@pytest.mark.parametrize("field", ["loss_modulus_pa", "dissipated_energy_j_m3", "stored_energy_j_m3", "stress_pa", "dissipation_rate_w_m3"])
def test_exact_zero_contracts_do_not_receive_subnormal_tolerance(field):
    p=r.example_request();p["step_strain"]=0
    result=r.analyze(p)
    group="frequency_response" if field=="loss_modulus_pa" else "time_response"
    result["response"][group][field][0]=math.nextafter(0,math.inf)
    seal(result)
    assert r.verify(result)["status"]=="FAIL"


def test_forgery_at_tiny_reference_returns_finite_failure_receipt():
    p=r.example_request();p.update(equilibrium_modulus_pa=0,
        modes=[{"mode_id":"one","modulus_pa":1e12,"relaxation_time_s":1}],time_s=[0,750])
    result=r.analyze(p)
    result["response"]["time_response"]["relaxation_modulus_pa"][-1]=1e100
    seal(result)
    receipt=r.verify(result)
    assert receipt["status"]=="FAIL"
    assert math.isfinite(receipt["maximum_symmetric_relative_discrepancy"])
    check_seal(receipt)
