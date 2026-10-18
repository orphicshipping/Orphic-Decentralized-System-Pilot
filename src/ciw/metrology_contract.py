"""Bounded sampled metrology cases. Registration and declarations grant no authority."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import math
import re

from .agent_api import encode, parse
from .control_contracts import keys

SCHEMA = "ciw.metrology-case.v1"
MAX_BYTES = 48 * 1024
MAX_POINTS = 256
PROFILES = {"sampled-plane.v1": "metrology.sampled-plane.v1",
            "freight-envelope.v1": "metrology.freight-envelope.v1",
            "visible-lean.v1": "metrology.visible-lean.v1"}
AUTHORITY = {"physical_qualification": "not_established", "calibration_authenticity": "not_established",
             "state_admission": "not_performed", "actuation": "not_permitted",
             "standards_compliance": "not_established", "approval": "not_granted"}


def ref(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}", value) is None:
        raise ValueError("Require a bounded reference")
    return value


def digest(raw):
    return "sha256:" + sha256(raw).hexdigest()


def hash_ref(value):
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("Require a SHA-256 binding")
    return value


def number(value, low=0, high=1e4):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Require a bounded finite number")
    return value


def vector(value):
    if type(value) is not list or len(value) != 3:
        raise ValueError("Require xyz coordinates")
    for x in value:
        number(x, -1e4, 1e4)


def decimal(value):
    if type(value) is not str or len(value) > 32 or not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise ValueError("Require a positive finite decimal string")
    try:
        d = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Invalid decimal") from error
    if not Decimal("0.000001") <= d <= Decimal("1000000"):
        raise ValueError("Decimal is outside supported bounds")
    return d


def pmi_subset(value, feature_id, datum_ref):
    """Semantic association only; normative interpretation requires separate review."""
    keys(value, {"standard", "control", "representation", "feature_id", "datum_ref", "modifiers", "tolerance_m", "source_ref"})
    if (value["standard"] != "ISO1101:2017" or value["control"] != "perpendicularity"
            or value["representation"] != "semantic" or value["modifiers"] != []):
        raise ValueError("Only semantic ISO1101:2017 perpendicularity without modifiers is supported")
    if value["feature_id"] != feature_id or value["datum_ref"] != datum_ref or not datum_ref:
        raise ValueError("PMI feature/datum association differs")
    number(value["tolerance_m"], 1e-9, 1)
    ref(value["source_ref"])
    return deepcopy(value)


def case(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("Metrology case exceeds 48 KiB")
    s = parse(raw)
    keys(s, {"schema", "profile", "inspection_ref", "origin", "feature", "capture", "calibration", "policy", "provider", "settings"})
    if s["schema"] != SCHEMA or s["profile"] not in PROFILES or s["origin"] not in {"synthetic", "recorded"}:
        raise ValueError("Unsupported metrology schema/profile/origin")
    ref(s["inspection_ref"])
    f = s["feature"]
    keys(f, {"id", "revision", "geometry", "region_ref", "datum_ref"})
    for name in f:
        ref(f[name])
    expected = {"sampled-plane.v1": "sampled_plane", "freight-envelope.v1": "frame_aligned_envelope", "visible-lean.v1": "visible_straight_axis"}
    if f["geometry"] != expected[s["profile"]]:
        raise ValueError("Unsupported geometry for selected profile")
    c = s["capture"]
    keys(c, {"id", "revision", "frame", "unit", "points_m", "coverage", "quality"})
    for name in ("id", "revision", "frame"):
        ref(c[name])
    if c["unit"] != "m" or type(c["points_m"]) is not list or not 3 <= len(c["points_m"]) <= MAX_POINTS:
        raise ValueError("Require 3..256 points in metres")
    for point in c["points_m"]:
        if point is not None:
            vector(point)
    keys(c["coverage"], {"status", "evidence_ref"})
    if c["coverage"]["status"] not in {"declared_complete", "partial", "unknown"}:
        raise ValueError("Unsupported coverage declaration")
    ref(c["coverage"]["evidence_ref"])
    keys(c["quality"], {"blurred", "moving"})
    if any(type(x) is not bool for x in c["quality"].values()):
        raise ValueError("Capture quality flags must be explicit booleans")
    a = s["calibration"]
    keys(a, {"id", "revision", "capture_ref", "frame", "unit", "applicability", "evidence_sha256", "uncertainty"})
    for name in ("id", "revision"):
        ref(a[name])
    if (a["capture_ref"], a["frame"], a["unit"]) != (c["id"], c["frame"], c["unit"]):
        raise ValueError("Calibration capture/frame/unit binding differs")
    if a["applicability"] not in {"declared_applicable", "unknown", "expired"}:
        raise ValueError("Invalid calibration applicability")
    hash_ref(a["evidence_sha256"])
    u = a["uncertainty"]
    keys(u, {"status", "model", "point_bound_m", "common_translation_bound_m", "scale_factor", "scale_bound_relative", "normal_angle_bound_rad", "basis_ref"})
    if u["status"] not in {"declared_bounded", "unknown"} or u["model"] != "common_translation_plus_bounded_residual.v1":
        raise ValueError("Unsupported uncertainty model; independence cannot be inferred")
    ref(u["basis_ref"])
    for name in ("point_bound_m", "common_translation_bound_m"):
        number(u[name], 0, 1)
    number(u["scale_factor"], 0.5, 2)
    number(u["scale_bound_relative"], 0, 0.1)
    number(u["normal_angle_bound_rad"], 0, 0.2)
    p = s["policy"]
    keys(p, {"id", "revision", "kind", "maximum", "unit"})
    ref(p["id"]); ref(p["revision"])
    if p["kind"] != "absolute_bound_guardband.v1" or p["unit"] != ("deg" if s["profile"] == "visible-lean.v1" else "m"):
        raise ValueError("Policy kind/unit differs")
    number(p["maximum"], 0, 90 if p["unit"] == "deg" else 100)
    if s["provider"] != {"id": "ciw.metrology-numeric", "revision": "1"}:
        raise ValueError("Provider selection is fixed by this profile")
    t = s["settings"]
    if s["profile"] == "sampled-plane.v1":
        keys(t, {"method", "outliers", "pmi"})
        if t["method"] != "orthogonal_lsq_svd" or t["outliers"] != "retain_all":
            raise ValueError("Only orthogonal least squares retaining every point is supported")
        if t["pmi"] is not None:
            pmi_subset(t["pmi"], f["id"], f["datum_ref"])
    elif s["profile"] == "freight-envelope.v1":
        keys(t, {"carrier_rule"})
        rule = t["carrier_rule"]
        keys(rule, {"carrier", "id", "revision", "evidence_sha256", "length_unit", "length_increment", "divisor", "mass_unit", "mass_increment"})
        for name in ("carrier", "id", "revision"):
            ref(rule[name])
        hash_ref(rule["evidence_sha256"])
        if rule["length_unit"] not in {"cm", "in"} or rule["mass_unit"] not in {"kg", "lb"}:
            raise ValueError("Unsupported carrier units")
        for name in ("length_increment", "divisor", "mass_increment"):
            decimal(rule[name])
    else:
        keys(t, {"gravity", "camera_calibration_ref", "capture_ref"})
        keys(t["gravity"], {"frame", "down", "calibration_ref"})
        if t["gravity"]["frame"] != c["frame"] or t["capture_ref"] != c["id"]:
            raise ValueError("Gravity/camera applicability differs")
        vector(t["gravity"]["down"])
        if sum(x*x for x in t["gravity"]["down"]) <= 1e-12:
            raise ValueError("Gravity direction is degenerate")
        ref(t["gravity"]["calibration_ref"]); ref(t["camera_calibration_ref"])
    return s


def example(profile="sampled-plane.v1"):
    if profile not in PROFILES:
        raise ValueError("Unknown profile")
    geometries = {"sampled-plane.v1": "sampled_plane", "freight-envelope.v1": "frame_aligned_envelope", "visible-lean.v1": "visible_straight_axis"}
    points = [[x, y, 0.] for x in (-.5, 0., .5) for y in (-.5, 0., .5)]
    settings = {"method": "orthogonal_lsq_svd", "outliers": "retain_all", "pmi": None}
    if profile == "freight-envelope.v1":
        points = [[x,y,z] for x in (0.,1.) for y in (0.,.5) for z in (0.,.25)]
        settings = {"carrier_rule": {"carrier":"synthetic-carrier", "id":"example-dim", "revision":"1", "evidence_sha256":digest(b"synthetic-rule"), "length_unit":"cm", "length_increment":"1", "divisor":"5000", "mass_unit":"kg", "mass_increment":"0.5"}}
    elif profile == "visible-lean.v1":
        points = [[.01*z,0.,z] for z in (0.,.25,.5,.75,1.)]
        settings = {"gravity":{"frame":"fixture-frame", "down":[0.,0.,-1.], "calibration_ref":"fixture-gravity"}, "camera_calibration_ref":"fixture-camera", "capture_ref":"capture-1"}
    return {"schema":SCHEMA,"profile":profile,"inspection_ref":"inspection-1","origin":"synthetic",
            "feature":{"id":"feature-1","revision":"1","geometry":geometries[profile],"region_ref":"sampled-region-1","datum_ref":"datum-1"},
            "capture":{"id":"capture-1","revision":"1","frame":"fixture-frame","unit":"m","points_m":points,"coverage":{"status":"declared_complete","evidence_ref":"synthetic-coverage"},"quality":{"blurred":False,"moving":False}},
            "calibration":{"id":"calibration-1","revision":"1","capture_ref":"capture-1","frame":"fixture-frame","unit":"m","applicability":"declared_applicable","evidence_sha256":digest(b"synthetic-calibration"),"uncertainty":{"status":"declared_bounded","model":"common_translation_plus_bounded_residual.v1","point_bound_m":.0001,"common_translation_bound_m":.001,"scale_factor":1.,"scale_bound_relative":0.,"normal_angle_bound_rad":0.,"basis_ref":"synthetic-error-set"}},
            "policy":{"id":"project-limit","revision":"1","kind":"absolute_bound_guardband.v1","maximum":1. if profile=="visible-lean.v1" else .01 if profile=="sampled-plane.v1" else 2.,"unit":"deg" if profile=="visible-lean.v1" else "m"},
            "provider":{"id":"ciw.metrology-numeric","revision":"1"},"settings":settings}
