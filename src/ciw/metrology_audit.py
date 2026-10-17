"""Provider-free retained witness checks; no SVD, eigensolver or operation dispatch."""
from __future__ import annotations

from copy import deepcopy
import math

from .agent_api import encode
from .control_contracts import keys
from .metrology_contract import AUTHORITY, case, digest, hash_ref, number, vector


def _close(a,b):
    if not math.isclose(float(a),float(b),rel_tol=2e-10,abs_tol=1e-11):
        raise ValueError("Retained numerical witness differs")


def _dot(a,b):return math.fsum(x*y for x,y in zip(a,b))


def _det(a):
    return a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1])-a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])+a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0])


def validate_retained(value,raw):
    s=case(raw);u=s["calibration"]["uncertainty"]
    keys(value,{"schema","inspection_ref","source_ref","profile","feature","capture_ref","frame","calibration_ref","calibration_revision","policy","provider","origin","computation","decision","approval","reasons","quantity","metrics","authority","result_ref"})
    unsealed=deepcopy(value);subject=unsealed.pop("result_ref")
    if subject!=digest(encode(unsealed)):raise ValueError("Result content identity differs")
    expected={"schema":"ciw.metrology-assessment.v1","inspection_ref":s["inspection_ref"],"source_ref":digest(raw),
        "profile":s["profile"],"feature":s["feature"],"capture_ref":s["capture"]["id"],"frame":s["capture"]["frame"],
        "calibration_ref":s["calibration"]["id"],"calibration_revision":s["calibration"]["revision"],"policy":s["policy"],
        "origin":s["origin"],"approval":"not_granted","authority":AUTHORITY}
    if any(value[k]!=v for k,v in expected.items()):raise ValueError("Result source, decision policy or authority differs")
    p=value["provider"];keys(p,{"provider","revision","numpy","source_sha256","contract_sha256"})
    if p["provider"]!="ciw.metrology-numeric" or p["revision"]!="1" or p["numpy"]!="2.4.3":
        raise ValueError("Unsupported retained runtime declaration")
    for name in ("source_sha256","contract_sha256"):hash_ref("sha256:"+p[name])
    reasons=[]
    if s["capture"]["coverage"]["status"]!="declared_complete":reasons.append("coverage_incomplete")
    if s["calibration"]["applicability"]!="declared_applicable":reasons.append("calibration_unresolved")
    if u["status"]!="declared_bounded":reasons.append("uncertainty_unknown")
    if any(s["capture"]["quality"].values()):reasons.append("motion_or_blur")
    if None in s["capture"]["points_m"]:
        reasons.append("missing_coordinates")
        if (value["computation"],value["decision"],value["quantity"],value["metrics"],value["reasons"])!=("not_computed","INCONCLUSIVE",None,None,reasons):
            raise ValueError("Missingness was not preserved")
        return
    if value["computation"]=="refused":
        allowed={"plane_orientation_degenerate","envelope_dimension_degenerate","visible_axis_degenerate"}
        if value["decision"]!="INCONCLUSIVE" or value["quantity"] is not None or value["metrics"] is not None or len(value["reasons"])!=len(reasons)+1 or value["reasons"][:-1]!=reasons or value["reasons"][-1] not in allowed:
            raise ValueError("Refusal cannot support a measured quantity")
        return
    if value["computation"]!="completed":raise ValueError("Unsupported computation status")
    points=[[x*u["scale_factor"] for x in p] for p in s["capture"]["points_m"]]
    center=[math.fsum(p[i] for p in points)/len(points) for i in range(3)]
    centered=[[p[i]-center[i] for i in range(3)] for p in points]
    rounding=128*2.220446049250313e-16*max(1.,max(abs(x) for p in points for x in p))
    m=value["metrics"];q=value["quantity"]
    keys(q,{"nominal","lower","upper","unit","name"})
    for k in ("nominal","lower","upper"):number(q[k],0,1e5)
    if not q["lower"]<=q["nominal"]<=q["upper"]:raise ValueError("Invalid measurement interval")
    if q["unit"]!=s["policy"]["unit"]:raise ValueError("Quantity unit differs")
    if s["profile"]=="sampled-plane.v1":
        keys(m,{"normal","centroid_m","rms_m","sample_count","outlier_policy","minimum_zone_solved","common_translation_cancels"})
        n=m["normal"];vector(n);_close(_dot(n,n),1.)
        for a,b in zip(m["centroid_m"],center):_close(a,b)
        residual=[_dot(p,n) for p in centered];nominal=max(residual)-min(residual)
        covariance=[[math.fsum(p[i]*p[j] for p in centered)/len(points) for j in range(3)] for i in range(3)]
        eigenvalue=math.fsum(r*r for r in residual)/len(points)
        # An exact stationary direction with C-lambda I PSD is a least-squares
        # plane witness. These tolerance checks are numerical, not a certificate.
        for row,v in zip(covariance,n):_close(_dot(row,n),eigenvalue*v)
        matrix=[[covariance[i][j]-(eigenvalue if i==j else 0.) for j in range(3)] for i in range(3)]
        scale=max(1.,sum(covariance[i][i] for i in range(3)))
        if (min(matrix[i][i] for i in range(3)) < -1e-10*scale
                or min(matrix[i][i]*matrix[j][j]-matrix[i][j]**2 for i,j in ((0,1),(0,2),(1,2))) < -1e-10*scale**2
                or _det(matrix)<-1e-10*scale**3):raise ValueError("Plane witness is not the minimum eigen-direction")
        radius=2*u["point_bound_m"]+nominal*u["scale_bound_relative"]+4*max(math.sqrt(_dot(p,p)) for p in centered)*math.sin(u["normal_angle_bound_rad"]/2)+rounding
        _close(m["rms_m"],math.sqrt(eigenvalue))
        if (m["sample_count"],m["outlier_policy"],m["minimum_zone_solved"],m["common_translation_cancels"])!=(len(points),"retain_all",False,True):raise ValueError("Unsupported plane claims")
        if s["settings"]["pmi"] is not None:reasons.append("PMI_normative_and_datum_review_required")
        name="sampled_lsq_peak_to_valley"
    elif s["profile"]=="freight-envelope.v1":
        keys(m,{"dimensions_m","dimension_bounds_m","envelope_volume_m3","occupied_volume_m3","minimum_rotated_box_solved","common_translation_cancels","billing"})
        dims=[max(p[i] for p in points)-min(p[i] for p in points) for i in range(3)]
        if len(m["dimensions_m"])!=3 or len(m["dimension_bounds_m"])!=3:raise ValueError("Invalid envelope dimension count")
        for a,b in zip(m["dimensions_m"],dims):_close(a,b)
        _close(m["envelope_volume_m3"],math.prod(dims))
        if m["occupied_volume_m3"] is not None or m["minimum_rotated_box_solved"] is not False or m["common_translation_cancels"] is not True:raise ValueError("Envelope does not establish occupied volume")
        nominal=max(dims);radius=2*u["point_bound_m"]+nominal*u["scale_bound_relative"]+rounding
        from .metrology_numeric import _billing
        for dim,bound in zip(dims,m["dimension_bounds_m"]):
            keys(bound,{"nominal","lower","upper"});err=2*u["point_bound_m"]+dim*u["scale_bound_relative"]+rounding
            expected_bound={"nominal":dim,"lower":max(0.,math.nextafter(dim-err,-math.inf)),"upper":math.nextafter(dim+err,math.inf)}
            if bound!=expected_bound:raise ValueError("Retained dimension bounds differ")
        rule=s["settings"]["carrier_rule"]
        billing=None if reasons else {"nominal":_billing(dims,rule),"lower":_billing([b["lower"] for b in m["dimension_bounds_m"]],rule),"upper":_billing([b["upper"] for b in m["dimension_bounds_m"]],rule)}
        if m["billing"]!=billing:raise ValueError("Retained carrier calculation differs")
        name="longest_frame_aligned_envelope_dimension"
    else:
        keys(m,{"axis","gravity","eigen_gap_m2","covariance_perturbation_bound_m2","hidden_integrity","safety_disposition","camera_calibration_ref","common_translation_cancels"})
        axis=m["axis"];vector(axis);_close(_dot(axis,axis),1.)
        gravity=s["settings"]["gravity"]["down"];length=math.sqrt(_dot(gravity,gravity));gravity=[v/length for v in gravity]
        for a,b in zip(m["gravity"],gravity):_close(a,b)
        c=[[math.fsum(p[i]*p[j] for p in centered)/len(points) for j in range(3)] for i in range(3)]
        lam=_dot(axis,[_dot(row,axis) for row in c])
        for row,v in zip(c,axis):_close(_dot(row,axis),lam*v)
        trace=sum(c[i][i] for i in range(3));trace2=sum(v*v for row in c for v in row)
        others=trace-lam;discriminant=max(0.,2*(trace2-lam*lam)-others*others)
        gap=lam-(others+math.sqrt(discriminant))/2
        if gap<=0:raise ValueError("Lean witness is not the largest eigen-direction")
        _close(m["eigen_gap_m2"],gap)
        perturb=4*u["point_bound_m"]*math.sqrt(math.fsum(_dot(p,p) for p in centered))/math.sqrt(len(points))+4*u["point_bound_m"]**2+rounding
        _close(m["covariance_perturbation_bound_m2"],perturb)
        nominal=math.degrees(math.acos(min(1.,abs(_dot(axis,gravity)))))
        if 2*perturb>=gap:reasons.append("axis_uncertainty_unresolved");radius=90.
        else:radius=math.degrees(math.asin(min(1.,perturb/(gap-perturb)))+u["normal_angle_bound_rad"])+1e-10
        if (m["hidden_integrity"],m["safety_disposition"],m["camera_calibration_ref"],m["common_translation_cancels"])!=("not_assessed","not_issued",s["settings"]["camera_calibration_ref"],True):raise ValueError("Unsupported lean authority")
        name="visible_principal_axis_lean"
    if q["name"]!=name:raise ValueError("Measured quantity differs")
    expected_interval = {"nominal":float(nominal),
        "lower":max(0.,math.nextafter(float(nominal-radius),-math.inf)),
        "upper":math.nextafter(float(nominal+radius),math.inf)}
    if q["unit"]=="deg":expected_interval["upper"]=min(90.,expected_interval["upper"])
    if any(q[k]!=v for k,v in expected_interval.items()):
        raise ValueError("Retained decision bounds differ from exact witness arithmetic")
    decision="INCONCLUSIVE"
    if not reasons:
        decision="WITHIN_LIMIT" if q["upper"]<=s["policy"]["maximum"] else "OUTSIDE_LIMIT" if q["lower"]>s["policy"]["maximum"] else "INCONCLUSIVE"
        if decision=="INCONCLUSIVE":reasons.append("uncertainty_interval_overlaps_limit")
    if value["reasons"]!=reasons or value["decision"]!=decision:raise ValueError("Retained decision differs from evidence and fixed guardband")
