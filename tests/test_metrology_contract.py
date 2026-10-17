"""Bounded contract and authorized intake, without operation execution."""
import math
import pytest
from ciw.agent_api import encode
from ciw.metrology_contract import case,example,PROFILES
from ciw.metrology_ingress import retain,resolve


@pytest.mark.parametrize("profile",PROFILES)
def test_contract_roundtrip(profile):
    raw=encode(example(profile));assert encode(case(raw))==raw


@pytest.mark.parametrize("fault", ["frame","unit","capture","provider","geometry","boolean","extra","too_many","policy_unit","nonfinite"])
def test_contradictory_and_unsupported_bindings_refuse(fault):
    s=example()
    if fault=="frame":s["calibration"]["frame"]="other"
    elif fault=="unit":s["capture"]["unit"]="mm"
    elif fault=="capture":s["calibration"]["capture_ref"]="other"
    elif fault=="provider":s["provider"]["revision"]="arbitrary"
    elif fault=="geometry":s["feature"]["geometry"]="cylinder"
    elif fault=="boolean":s["calibration"]["uncertainty"]["point_bound_m"]=True
    elif fault=="extra":s["provider"]["path"]="/tmp/code"
    elif fault=="too_many":s["capture"]["points_m"]*=30
    elif fault=="policy_unit":s["policy"]["unit"]="mm"
    else:s["capture"]["points_m"][0][0]=math.inf
    with pytest.raises(ValueError):case(encode(s))


def test_retained_intake_authorization_tamper_paths_and_budget(tmp_path):
    raw=encode(example());key=retain(raw,tmp_path);assert retain(raw,tmp_path)==key
    assert resolve(tmp_path,key,authorized={key})==raw
    with pytest.raises(ValueError):resolve(tmp_path,key,authorized=set())
    for bad in ("../source","https://example.org/data","/etc/passwd"):
        with pytest.raises(ValueError):resolve(tmp_path,bad,authorized={bad})
    target=tmp_path/(key+".json");target.write_bytes(raw+b" ")
    with pytest.raises(ValueError):resolve(tmp_path,key,authorized={key})
    target.unlink();outside=tmp_path/"outside";outside.write_bytes(raw)
    target.symlink_to(outside)
    with pytest.raises(ValueError):resolve(tmp_path,key,authorized={key})
    with pytest.raises(ValueError):retain(b" "*131073,tmp_path)


