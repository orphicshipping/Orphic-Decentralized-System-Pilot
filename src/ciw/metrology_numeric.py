"""Three fixed sampled-geometry calculations with explicit conditional error sets."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha256
import math
from pathlib import Path

import numpy as np

from .agent_api import encode
from .metrology_contract import AUTHORITY, case, digest


def runtime_identity():
    return {"provider":"ciw.metrology-numeric", "revision":"1", "numpy":np.__version__,
            "source_sha256":sha256(Path(__file__).read_bytes()).hexdigest(),
            "contract_sha256":sha256(Path(__file__).with_name("metrology_contract.py").read_bytes()).hexdigest()}


def interval(value, radius):
    # Outward padding is numerical protection, not a metrological coverage claim.
    return {"nominal":float(value), "lower":max(0., math.nextafter(float(value-radius), -math.inf)),
            "upper":math.nextafter(float(value+radius), math.inf)}


def _billing(dimensions, rule):
    """Ceil each dimension, then ceil dimensional mass; no occupied-volume claim."""
    with localcontext() as context:
        context.prec = 80
        factor = Fraction(100) if rule["length_unit"] == "cm" else Fraction(5000,127)
        step = Fraction(Decimal(rule["length_increment"]))
        rounded = [math.ceil(Fraction.from_float(float(x))*factor/step)*step for x in dimensions]
        mass = rounded[0]*rounded[1]*rounded[2]/Fraction(Decimal(rule["divisor"]))
        mass_step = Fraction(Decimal(rule["mass_increment"]))
        mass = math.ceil(mass/mass_step)*mass_step
        def display(value):
            return str(Decimal(value.numerator)/Decimal(value.denominator))
        return {"rounded_dimensions":list(map(display,rounded)), "length_unit":rule["length_unit"],
                "dimensional_mass":display(mass), "mass_unit":rule["mass_unit"],
                "rule_ref":rule["id"], "rule_revision":rule["revision"],
                "actual_mass_assessed":False, "carrier_acceptance":"not_established"}


def assess(raw):
    s = case(raw)
    if np.__version__ != "2.4.3":
        raise ValueError("Requires pinned numpy 2.4.3 runtime")
    u = s["calibration"]["uncertainty"]
    result = {"schema":"ciw.metrology-assessment.v1", "inspection_ref":s["inspection_ref"],
              "source_ref":digest(raw), "profile":s["profile"], "feature":deepcopy(s["feature"]),
              "capture_ref":s["capture"]["id"], "frame":s["capture"]["frame"],
              "calibration_ref":s["calibration"]["id"], "calibration_revision":s["calibration"]["revision"],
              "policy":deepcopy(s["policy"]), "provider":runtime_identity(), "origin":s["origin"],
              "computation":"not_computed", "decision":"INCONCLUSIVE", "approval":"not_granted",
              "reasons":[], "quantity":None, "metrics":None, "authority":deepcopy(AUTHORITY)}
    reasons = result["reasons"]
    if s["capture"]["coverage"]["status"] != "declared_complete":
        reasons.append("coverage_incomplete")
    if s["calibration"]["applicability"] != "declared_applicable":
        reasons.append("calibration_unresolved")
    if u["status"] != "declared_bounded":
        reasons.append("uncertainty_unknown")
    if any(s["capture"]["quality"].values()):
        reasons.append("motion_or_blur")
    if None in s["capture"]["points_m"]:
        reasons.append("missing_coordinates")
        return seal(result)
    points = [[x*u["scale_factor"] for x in p] for p in s["capture"]["points_m"]]
    center = [math.fsum(p[i] for p in points)/len(points) for i in range(3)]
    centered = [[p[i]-center[i] for i in range(3)] for p in points]
    dot = lambda a,b: math.fsum(x*y for x,y in zip(a,b))
    rounding = 128*2.220446049250313e-16*max(1.,max(abs(x) for p in points for x in p))
    try:
        if s["profile"] == "sampled-plane.v1":
            _, singular, vh = np.linalg.svd(centered, full_matrices=False)
            if singular[1] <= max(1e-10, singular[0]*1e-8) or singular[1]-singular[2] <= max(1e-10, singular[0]*1e-8):
                raise ValueError("plane_orientation_degenerate")
            normal = vh[-1]
            if normal[np.abs(normal).argmax()] < 0:
                normal = -normal
            normal = normal.tolist()
            residual = [dot(p,normal) for p in centered]
            pv = max(residual)-min(residual)
            radius = 2*u["point_bound_m"] + pv*u["scale_bound_relative"] + 4*max(math.sqrt(dot(p,p)) for p in centered)*math.sin(u["normal_angle_bound_rad"]/2) + rounding
            result["quantity"] = {**interval(pv,radius), "unit":"m", "name":"sampled_lsq_peak_to_valley"}
            result["metrics"] = {"normal":normal,"centroid_m":center,
                                 "rms_m":math.sqrt(math.fsum(r*r for r in residual)/len(points)),"sample_count":len(points),
                                 "outlier_policy":"retain_all", "minimum_zone_solved":False,
                                 "common_translation_cancels":True}
            if s["settings"]["pmi"] is not None:
                reasons.append("PMI_normative_and_datum_review_required")
        elif s["profile"] == "freight-envelope.v1":
            dimensions = [max(p[i] for p in points)-min(p[i] for p in points) for i in range(3)]
            if min(dimensions) <= rounding:
                raise ValueError("envelope_dimension_degenerate")
            radii = [2*u["point_bound_m"] + d*u["scale_bound_relative"] + rounding for d in dimensions]
            bounds = [interval(float(x),float(e)) for x,e in zip(dimensions,radii)]
            rule = s["settings"]["carrier_rule"]
            result["quantity"] = {**interval(float(max(dimensions)),float(max(radii))),"unit":"m","name":"longest_frame_aligned_envelope_dimension"}
            result["metrics"] = {"dimensions_m":dimensions, "dimension_bounds_m":bounds,
                "envelope_volume_m3":math.prod(dimensions), "occupied_volume_m3":None,
                "minimum_rotated_box_solved":False, "common_translation_cancels":True,
                "billing":None if reasons else {"nominal":_billing(dimensions,rule),
                    "lower":_billing([v["lower"] for v in bounds],rule),
                    "upper":_billing([v["upper"] for v in bounds],rule)}}
        else:
            covariance = [[math.fsum(p[i]*p[j] for p in centered)/len(points) for j in range(3)] for i in range(3)]
            values,vectors = np.linalg.eigh(covariance)
            axis = vectors[:,-1].tolist()
            lam = dot(axis,[dot(row,axis) for row in covariance])
            trace = sum(covariance[i][i] for i in range(3))
            trace2 = sum(v*v for row in covariance for v in row)
            others = trace-lam
            gap = lam-(others+math.sqrt(max(0.,2*(trace2-lam*lam)-others*others)))/2
            if gap <= max(1e-12, float(values[-1])*1e-8):
                raise ValueError("visible_axis_degenerate")
            gravity = s["settings"]["gravity"]["down"]
            gravity = [v/math.sqrt(dot(gravity,gravity)) for v in gravity]
            angle = math.degrees(math.acos(min(1.,abs(dot(axis,gravity)))))
            e = u["point_bound_m"]
            perturbation = 4*e*math.sqrt(math.fsum(dot(p,p) for p in centered))/math.sqrt(len(points))+4*e*e+rounding
            if 2*perturbation >= gap:
                reasons.append("axis_uncertainty_unresolved")
                radius = 90.
            else:
                radius = math.degrees(math.asin(min(1.,perturbation/(gap-perturbation)))+u["normal_angle_bound_rad"])+1e-10
            q = interval(angle,radius);q["upper"] = min(90.,q["upper"])
            result["quantity"] = {**q,"unit":"deg","name":"visible_principal_axis_lean"}
            result["metrics"] = {"axis":axis,"gravity":gravity,
                "eigen_gap_m2":gap,"covariance_perturbation_bound_m2":perturbation,
                "hidden_integrity":"not_assessed", "safety_disposition":"not_issued",
                "camera_calibration_ref":s["settings"]["camera_calibration_ref"],"common_translation_cancels":True}
        result["computation"] = "completed"
        if not reasons:
            q = result["quantity"];limit = s["policy"]["maximum"]
            result["decision"] = "WITHIN_LIMIT" if q["upper"] <= limit else "OUTSIDE_LIMIT" if q["lower"] > limit else "INCONCLUSIVE"
            if result["decision"] == "INCONCLUSIVE":
                reasons.append("uncertainty_interval_overlaps_limit")
    except (ValueError, np.linalg.LinAlgError) as error:
        result["computation"] = "refused";reasons.append(str(error))
    return seal(result)


def seal(value):
    value["result_ref"] = digest(encode(value))
    return value


def verify(value, raw):
    if value != assess(raw):
        raise ValueError("Assessment differs from exact retained source and runtime")
    return {"status":"PASS", "source_ref":digest(raw), "subject_ref":value["result_ref"],
            "scope":"same-implementation numerical recomputation", "independent_verification":False}
