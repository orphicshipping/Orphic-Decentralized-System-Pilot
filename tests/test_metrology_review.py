"""Synthetic campaign binding and action drafts; no physical release or writes."""
from copy import deepcopy
import math

import pytest

from ciw.agent_api import encode
from ciw.control_contracts import observation, record
from ciw.core.identities import content_identity
from ciw.metrology_contract import example, digest
from ciw.metrology_numeric import assess, runtime_identity, seal
from ciw.metrology_audit import validate_retained
from ciw.metrology_review import campaign, draft_action, validate_proposal
from ciw.reference_comparison import example_request


def campaign_fixture():
    raws=[];left=[];right=[];reference=digest(b"synthetic held-out reference")
    for i in range(2):
        s=example();s["capture"]["id"]=f"capture-{i}";s["calibration"]["capture_ref"]=s["capture"]["id"]
        raw=encode(s);raws.append(raw);result=assess(raw)
        for rows,source in ((left,digest(raw)),(right,reference)):
            rows.append(observation(identity={"model_id":"fixture","entity_id":"sample","execution_id":"run"},
                clock={"id":"fixture.clock","time_s":float(i)},frame=s["capture"]["frame"],
                quantity=result["quantity"]["name"],value=result["quantity"]["nominal"],unit="m",uncertainty=None,
                provenance={"provider":"synthetic.fixture","sources":[source],"semantics":"simulated"}))
    request=example_request("unknown");request["left"]=record("observation-stream",observations=left)
    request["right"]=record("observation-stream",observations=right)
    request["policy"].update(unit="m",left_ref=content_identity(request["left"]),right_ref=content_identity(request["right"]))
    manifest={"schema":"ciw.metrology-campaign.v1","candidate_ref":digest(encode(runtime_identity())),
        "plan_ref":digest(b"synthetic predeclared plan"),"calibration_refs":[s["calibration"]["evidence_sha256"]],
        "reference_capture_refs":[reference],"comparison_request":request}
    return manifest,raws


def test_campaign_retains_exact_candidate_reference_and_synthetic_blockers():
    manifest,raws=campaign_fixture();r=campaign(manifest,raws)
    assert r["qualification_status"]=="BLOCKED"
    assert r["physical_qualification"]=="not_established"
    assert "synthetic_evidence" in r["blockers"]
    assert r["case_refs"]==list(map(digest,raws))
    assert r["comparison"]["outcome"]["status"]=="INDETERMINATE"


@pytest.mark.parametrize("fault",["candidate","calibration","held_out","value","source","repeat"])
def test_campaign_binding_refusals(fault):
    m,raws=campaign_fixture()
    if fault=="candidate":m["candidate_ref"]=digest(b"other")
    elif fault=="calibration":m["calibration_refs"]=[digest(b"other")]
    elif fault=="held_out":m["reference_capture_refs"]=[digest(raws[0])]
    elif fault=="value":m["comparison_request"]["left"]["observations"][0]["value"]=42
    elif fault=="source":m["reference_capture_refs"]=[digest(b"other")]
    else:raws[1]=raws[0]
    with pytest.raises(ValueError):campaign(m,raws)


@pytest.mark.parametrize("profile",["sampled-plane.v1","freight-envelope.v1","visible-lean.v1"])
def test_retained_audit_and_drafting_never_call_solver(monkeypatch,profile):
    raw=encode(example(profile));r=assess(raw)
    def forbidden(*a,**kw):raise AssertionError("Solver called during retained read")
    monkeypatch.setattr("numpy.linalg.svd",forbidden);monkeypatch.setattr("numpy.linalg.eigh",forbidden)
    monkeypatch.setattr("ciw.metrology_numeric.assess",forbidden)
    validate_retained(r,raw)
    args={"current_source_ref":digest(raw),"current_policy_revision":r["policy"]["revision"]}
    p=draft_action(r,raw,target="review-target",action="request_review",**args)
    assert p["execution_performed"] is False and p["approval_required"] is True
    assert validate_proposal(p,r,raw,**args)==p
    with pytest.raises(ValueError):validate_proposal(p,r,raw,**{**args,"current_source_ref":digest(b"changed")})
    with pytest.raises(ValueError):validate_proposal(p,r,raw,**{**args,"current_policy_revision":"changed"})


@pytest.mark.parametrize("fault",["one_ulp_bound","authority","normal","billing"])
def test_resealed_tampering_cannot_change_retained_claims(fault):
    raw=encode(example("freight-envelope.v1" if fault=="billing" else "sampled-plane.v1"));r=assess(raw)
    if fault=="one_ulp_bound":r["quantity"]["upper"]=math.nextafter(r["quantity"]["upper"],-math.inf)
    elif fault=="authority":r["authority"]["physical_qualification"]="established"
    elif fault=="normal":r["metrics"]["normal"]=[1.,0.,0.]
    else:r["metrics"]["billing"]["nominal"]["dimensional_mass"]="0"
    r.pop("result_ref");seal(r)
    with pytest.raises(ValueError):validate_retained(r,raw)


def test_review_cli_create_only_and_stale_refusal(tmp_path):
    from ciw.metrology_review import main
    manifest,raws=campaign_fixture();m=tmp_path/"manifest.json";m.write_bytes(encode(manifest))
    cases=[]
    for i,raw in enumerate(raws):
        p=tmp_path/f"case-{i}.json";p.write_bytes(raw);cases.append(str(p))
    output=tmp_path/"report.json"
    args=["campaign",str(m),*cases,"--output",str(output)]
    assert main(args)==0;original=output.read_bytes();assert main(args)==2;assert output.read_bytes()==original
    result=assess(raws[0]);r=tmp_path/"result.json";r.write_bytes(encode(result))
    proposal=tmp_path/"proposal.json"
    args=["draft",cases[0],str(r),"--target","review-target","--action","request_review",
          "--current-source-ref",digest(raws[0]),"--current-policy-revision",result["policy"]["revision"],"--output",str(proposal)]
    from ciw.agent_api import parse
    assert main(args)==0;assert parse(proposal.read_bytes())["execution_performed"] is False
