"""Analytic geometry, exact carrier arithmetic and retained-operation boundaries."""
from copy import deepcopy
import math
from pathlib import Path

import numpy as np
import pytest

from ciw.agent_api import encode, parse
from ciw.metrology_contract import case, example, pmi_subset, PROFILES
from ciw.metrology_ingress import retain, resolve
from ciw.metrology_numeric import assess, verify
from ciw.metrology_workflow import prepare, from_profile, make_source, source_bytes


@pytest.mark.parametrize("profile", PROFILES)
def test_contract_exact_roundtrip_and_separate_statuses(profile):
    raw=encode(example(profile));assert encode(case(raw))==raw
    assert source_bytes(make_source(raw))==raw
    r=assess(raw)
    assert r["computation"]=="completed" and r["decision"]=="WITHIN_LIMIT"
    assert r["approval"]=="not_granted" and r["authority"]["physical_qualification"]=="not_established"
    assert verify(r,raw)["independent_verification"] is False
    changed=deepcopy(r);changed["approval"]="approved"
    with pytest.raises(ValueError):verify(changed,raw)


@pytest.mark.parametrize("fault", ["partial","unknown_uncertainty","calibration","missing","blurred","moving"])
@pytest.mark.parametrize("profile", PROFILES)
def test_missing_or_unqualified_evidence_never_passes(profile,fault):
    s=example(profile)
    if fault=="partial":s["capture"]["coverage"]["status"]="partial"
    elif fault=="unknown_uncertainty":s["calibration"]["uncertainty"]["status"]="unknown"
    elif fault=="calibration":s["calibration"]["applicability"]="expired"
    elif fault=="missing":s["capture"]["points_m"][0]=None
    else:s["capture"]["quality"][fault]=True
    raw=encode(s);r=assess(raw)
    assert r["decision"]=="INCONCLUSIVE" and r["reasons"]
    if profile=="freight-envelope.v1" and r["metrics"]:assert r["metrics"]["billing"] is None
    if fault=="missing":assert case(raw)["capture"]["points_m"][0] is None


def test_plane_analytic_tilt_saddle_and_outliers():
    s=example();s["capture"]["points_m"]=[[x,y,2*x-3*y+1] for x in (-1.,0.,1.) for y in (-1.,0.,1.)]
    r=assess(encode(s));expected=np.array([-2.,3.,1.]);expected/=np.linalg.norm(expected)
    assert abs(np.dot(r["metrics"]["normal"],expected))==pytest.approx(1.)
    assert r["quantity"]["nominal"]<1e-12
    s["capture"]["points_m"]=[[x,y,.01*x*y] for x in (-1.,0.,1.) for y in (-1.,0.,1.)]
    r=assess(encode(s));assert r["quantity"]["nominal"]==pytest.approx(.02)
    assert r["decision"]=="OUTSIDE_LIMIT"
    s=example();s["capture"]["points_m"][4][2]=.05
    r=assess(encode(s));assert r["quantity"]["nominal"]==pytest.approx(.05)
    assert not r["metrics"]["minimum_zone_solved"] and r["metrics"]["sample_count"]==9


@pytest.mark.parametrize("points", [[[0.,0.,0.]]*3,[[0.,0.,0.],[1.,1.,1.],[2.,2.,2.]]])
def test_degenerate_plane_and_axis_refuse(points):
    for profile in ("sampled-plane.v1", "visible-lean.v1"):
        s=example(profile);s["capture"]["points_m"]=points
        r=assess(encode(s))
        if profile=="sampled-plane.v1" or len(set(map(tuple,points)))==1:
            assert r["computation"]=="refused" and r["decision"]=="INCONCLUSIVE"


@pytest.mark.parametrize("profile", PROFILES)
def test_shared_translation_does_not_become_independent_noise(profile):
    s=example(profile);a=assess(encode(s))
    s["capture"]["points_m"]=(np.array(s["capture"]["points_m"])+[5.,-2.,3.]).tolist()
    s["calibration"]["uncertainty"]["common_translation_bound_m"]=.9
    b=assess(encode(s));assert a["quantity"]["nominal"]==pytest.approx(b["quantity"]["nominal"],abs=1e-10)
    assert b["metrics"]["common_translation_cancels"]


def test_near_threshold_is_inconclusive_and_larger_error_bounds_do_not_improve_it():
    s=example();s["capture"]["points_m"][4][2]=.01
    a=assess(encode(s));assert a["decision"]=="INCONCLUSIVE"
    s["calibration"]["uncertainty"]["point_bound_m"]*=2
    b=assess(encode(s));assert b["quantity"]["upper"]>=a["quantity"]["upper"]


def test_carrier_rounding_is_explicit_and_volume_is_only_an_envelope():
    s=example("freight-envelope.v1");r=assess(encode(s));m=r["metrics"]
    assert m["dimensions_m"]==[1.,.5,.25] and m["envelope_volume_m3"]==pytest.approx(.125)
    assert m["occupied_volume_m3"] is None
    assert m["billing"]["nominal"]["rounded_dimensions"]==["100","50","25"]
    assert m["billing"]["nominal"]["dimensional_mass"]=="25"
    assert float(m["billing"]["upper"]["dimensional_mass"])>=25.
    del s["settings"]["carrier_rule"]
    with pytest.raises(ValueError):case(encode(s))


def test_visible_lean_matches_analytic_inclinometer_and_rotated_view():
    s=example("visible-lean.v1");r=assess(encode(s))
    assert r["quantity"]["nominal"]==pytest.approx(math.degrees(math.atan(.01)),abs=1e-9)
    rotation=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
    s["capture"]["points_m"]=(np.array(s["capture"]["points_m"])@rotation.T).tolist()
    s["settings"]["gravity"]["down"]=(rotation@np.array([0.,0.,-1.])).tolist()
    changed=assess(encode(s));assert changed["quantity"]["nominal"]==pytest.approx(r["quantity"]["nominal"])
    assert changed["metrics"]["safety_disposition"]=="not_issued"


def test_semantic_PMI_preserves_association_but_cannot_claim_reviewed_compliance():
    s=example();v={"standard":"ISO1101:2017","control":"perpendicularity","representation":"semantic","feature_id":"feature-1","datum_ref":"datum-1","modifiers":[],"tolerance_m":.01,"source_ref":"pmi-1"}
    assert pmi_subset(v,"feature-1","datum-1")==v
    s["settings"]["pmi"]=v;r=assess(encode(s));assert r["decision"]=="INCONCLUSIVE"
    for field,value in (("representation","graphical"),("control","position"),("modifiers",["MMC"]),("datum_ref","other")):
        wrong={**v,field:value}
        with pytest.raises(ValueError):pmi_subset(wrong,"feature-1","datum-1")


@pytest.mark.parametrize("profile", PROFILES)
def test_real_operation_restart_idempotence_and_retained_tamper(tmp_path,profile):
    raw=encode(example(profile));path=prepare(raw,tmp_path/"profile")
    with from_profile(path) as host:
        first=host.execute("source","baseline","attempt-1")
        assert first["status"]=="completed"
        repeated=host.execute("source","baseline","attempt-1")
        assert repeated["reused_response"]
        assert repeated["execution_ids"]==first["execution_ids"]
    with from_profile(path,resume=True) as host:
        recovered=host.execute("source","baseline","attempt-1")
        assert recovered["execution_ids"]==first["execution_ids"]
    source=parse((path.parent/"source.json").read_bytes());source["metadata"]["coordinate_frame"]="invented"
    with pytest.raises(ValueError):source_bytes(source)


@pytest.mark.parametrize("profile", PROFILES)
def test_unknown_interrupted_attempt_cannot_dispatch_on_resume(tmp_path,monkeypatch,profile):
    path=prepare(encode(example(profile)),tmp_path/"profile")
    def interrupted(*a,**kw):raise KeyboardInterrupt("interrupted")
    host=from_profile(path);monkeypatch.setattr("ciw.agent_api.run_graph",interrupted)
    with pytest.raises(KeyboardInterrupt):host.execute("source","baseline","unknown")
    host.close()
    def forbidden(*a,**kw):raise AssertionError("Unknown attempt was dispatched again")
    monkeypatch.setattr("ciw.agent_api.run_graph",forbidden)
    with from_profile(path,resume=True) as resumed:
        r=resumed.execute("source","baseline","unknown")
        assert r["status"]=="incomplete" and r["execution_outcome"]=="unknown" and r["reused_response"]


@pytest.mark.parametrize("profile", PROFILES)
def test_reopening_completed_workspace_is_provider_free(tmp_path,monkeypatch,profile):
    path=prepare(encode(example(profile)),tmp_path/"profile")
    with from_profile(path) as host:
        r=host.execute("source","baseline","complete")
        handle=r["artifacts"]["workspace"]["artifact_id"]
    def forbidden(*a,**kw):raise AssertionError("Provider called while reopening")
    monkeypatch.setattr("ciw.metrology_numeric.assess",forbidden)
    monkeypatch.setattr("numpy.linalg.svd",forbidden);monkeypatch.setattr("numpy.linalg.eigh",forbidden)
    with from_profile(path,resume=True) as host:
        assert host.inspect(handle)
        assert host.execute("source","baseline","complete")["reused_response"]


def test_maximal_point_capture_stays_bounded_and_fixture_roundtrips():
    import tracemalloc
    from ciw.metrology_contract import MAX_POINTS,MAX_BYTES
    s=example();s["capture"]["points_m"]=[[float(i%16),float(i//16),0.] for i in range(MAX_POINTS)]
    raw=encode(s);assert len(raw)<MAX_BYTES
    tracemalloc.start()
    try:
        assert assess(raw)["computation"]=="completed"
        assert tracemalloc.get_traced_memory()[1]<8*1024*1024
    finally:tracemalloc.stop()
    root=Path(__file__).resolve().parents[1]/"examples/metrology-sampled"
    for profile in PROFILES:
        assert case((root/(profile+".json")).read_bytes())==example(profile)
