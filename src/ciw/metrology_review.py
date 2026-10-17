"""Bound reference comparisons and non-executing action proposals; no release authority."""
from __future__ import annotations

from copy import deepcopy

from .agent_api import encode
from .control_contracts import keys
from .metrology_contract import AUTHORITY, case, digest, hash_ref, ref
from .metrology_numeric import assess, runtime_identity
from .reference_comparison import compare
from .metrology_audit import validate_retained


def campaign(manifest, raw_cases):
    keys(manifest, {"schema","candidate_ref","plan_ref","calibration_refs","reference_capture_refs","comparison_request"})
    if manifest["schema"] != "ciw.metrology-campaign.v1":
        raise ValueError("Unsupported campaign")
    if manifest["candidate_ref"] != digest(encode(runtime_identity())):
        raise ValueError("Campaign candidate/runtime changed")
    hash_ref(manifest["plan_ref"])
    if type(raw_cases) is not list or not 1 <= len(raw_cases) <= 64:
        raise ValueError("Campaign requires 1..64 separately retained captures")
    for name in ("calibration_refs","reference_capture_refs"):
        values=manifest[name]
        if type(values) is not list or not 1 <= len(values) <= 64 or len(set(values)) != len(values):
            raise ValueError("Require distinct bounded evidence identities")
        for value in values:hash_ref(value)
    if set(manifest["calibration_refs"]) & set(manifest["reference_capture_refs"]):
        raise ValueError("Held-out reference evidence was used for calibration")
    if set(manifest["reference_capture_refs"]) & {digest(raw) for raw in raw_cases}:
        raise ValueError("Reference captures must be held out from candidate inputs")
    cases=[case(raw) for raw in raw_cases]
    captures=[s["capture"]["id"] for s in cases]
    if len(set(captures)) != len(captures) or len({s["profile"] for s in cases}) != 1:
        raise ValueError("Repeated capture identity or mixed campaign profiles")
    results=[assess(raw) for raw in raw_cases]
    request=manifest["comparison_request"]
    keys(request,{"left","right","policy"})
    # Existing comparison owns covariance, clock, quantity and exact decision rules.
    comparison=compare(**request)
    left=request["left"]["observations"];right=request["right"]["observations"]
    if len(left)!=len(cases) or len(right)!=len(cases):
        raise ValueError("Comparison sample count differs from retained cases")
    blockers=[]
    for raw,s,result,l,r in zip(raw_cases,cases,results,left,right):
        if result["quantity"] is None or result["computation"]!="completed":
            raise ValueError("Campaign cannot compare a missing computed quantity")
        if (l["value"],l["unit"],l["frame"],l["quantity"]) != (result["quantity"]["nominal"],result["quantity"]["unit"],s["capture"]["frame"],result["quantity"]["name"]):
            raise ValueError("Comparison left value/semantics differs from retained result")
        if digest(raw) not in l["provenance"]["sources"]:
            raise ValueError("Comparison omitted exact case dependency")
        if s["calibration"]["evidence_sha256"] not in manifest["calibration_refs"]:
            raise ValueError("Campaign calibration binding differs")
        if not set(r["provenance"]["sources"]) & set(manifest["reference_capture_refs"]):
            raise ValueError("Independent reference capture is unbound")
        if s["origin"]!="recorded" or l["provenance"]["semantics"]=="simulated" or r["provenance"]["semantics"]=="simulated":
            blockers.append("synthetic_evidence")
        if result["decision"]=="INCONCLUSIVE":blockers.append("inspection_inconclusive")
    if len(cases)<3:blockers.append("fewer_than_three_declared_repeats")
    # Even a numerical PASS cannot authenticate capture independence or a calibration.
    blockers.append("independent_physical_review_and_source_authentication_required")
    result={"schema":"ciw.metrology-campaign-report.v1","manifest":deepcopy(manifest),
            "case_refs":[digest(raw) for raw in raw_cases],"result_refs":[r["result_ref"] for r in results],
            "comparison":comparison,"physical_qualification":"not_established",
            "qualification_status":"BLOCKED" if "synthetic_evidence" in blockers else "REQUIRES_INDEPENDENT_REVIEW",
            "blockers":sorted(set(blockers)),"authority":deepcopy(AUTHORITY)}
    result["report_ref"]=digest(encode(result))
    return result


def draft_action(result, raw, *, target, action, current_source_ref, current_policy_revision):
    validate_retained(result,raw)
    if current_source_ref != digest(raw) or current_policy_revision != result["policy"]["revision"]:
        raise ValueError("Finding/evidence/policy changed; proposal is stale")
    ref(target)
    if action not in {"request_review","propose_remeasurement","propose_quarantine"}:
        raise ValueError("Unsupported draft action")
    value={"schema":"ciw.metrology-action-proposal.v1","finding_ref":result["result_ref"],
           "source_ref":current_source_ref,"policy_revision":current_policy_revision,
           "target":target,"intended_action":action,"approval_required":True,
           "status":"DRAFT_BLOCKED_UNQUALIFIED","blockers":["physical_profile_not_qualified","deployment_specific_approval_missing"],
           "execution_performed":False,"authority":deepcopy(AUTHORITY)}
    value["proposal_ref"]=digest(encode(value))
    return value


def validate_proposal(value, result, raw, *, current_source_ref, current_policy_revision):
    expected=draft_action(result,raw,target=value["target"],action=value["intended_action"],
                          current_source_ref=current_source_ref,current_policy_revision=current_policy_revision)
    if value!=expected:
        raise ValueError("Proposal or its finding changed")
    return deepcopy(value)


def main(argv=None):
    """Explicit local report/draft generation; create-only output, no integration calls."""
    import argparse
    from pathlib import Path
    import sys
    from .agent_api import parse
    from .control_contracts import save_new
    from .metrology_ingress import read_case
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    c=sub.add_parser("campaign");c.add_argument("manifest");c.add_argument("cases",nargs="+");c.add_argument("--output",required=True)
    c=sub.add_parser("draft");c.add_argument("case");c.add_argument("result");c.add_argument("--target",required=True)
    c.add_argument("--action",choices=["request_review","propose_remeasurement","propose_quarantine"],required=True)
    c.add_argument("--current-source-ref",required=True);c.add_argument("--current-policy-revision",required=True);c.add_argument("--output",required=True)
    args=parser.parse_args(argv)
    def bounded_json(path):
        p=Path(path)
        if p.is_symlink():raise ValueError("Review input cannot be a symlink")
        with p.open("rb") as stream:raw=stream.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise ValueError("Review input exceeds 2 MiB")
        return parse(raw)
    try:
        if args.command=="campaign":
            if len(args.cases)>64:raise ValueError("Campaign exceeds 64 captures")
            value=campaign(bounded_json(args.manifest),[read_case(path) for path in args.cases])
        else:
            value=draft_action(bounded_json(args.result),read_case(args.case),target=args.target,action=args.action,
                current_source_ref=args.current_source_ref,current_policy_revision=args.current_policy_revision)
        save_new(Path(args.output),value)
        return 0
    except (ValueError,OSError,KeyError) as error:
        print(str(error),file=sys.stderr);return 2


if __name__=="__main__":raise SystemExit(main())
