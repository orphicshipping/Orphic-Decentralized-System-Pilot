"""Bounded, retained BIM clearance profile; no physical qualification or actuation."""

from __future__ import annotations

import base64
import math
import re
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from .adapters.protocol import AdapterRefusal, InstrumentManifest
from .adapters.subprocess import PinnedSubprocessAdapter, _json
from .agent_api import AgentHost, encode, parse
from .control_contracts import bytes_ref, keys, save_new
from .control_plane import CapabilityRegistry, experiment
from .core.identities import evidence_id, validate_evidence_identity
from .core.records import validate_run_structure
from .operations.registry import Operation
from .operations.schemas import register_payload_validator

OP = "metrology.bim-clearance.v1"
SCHEMA = "ciw.metrology-inspection.v1"
PROFILE = "bim-clearance.v1"
NATIVE_TREE = "f91c982b580ad4f16a27ea149eedf0e338f6d764"
MAX_BYTES = 48 * 1024
AUTHORITY = {
    "physical_qualification": "not_established",
    "calibration_authenticity": "not_established",
    "state_admission": "not_performed",
    "actuation": "not_permitted",
    "independent_numerical_verification": "not_performed",
}
_registered = False


def _text(value):
    if type(value) is not str or not value.strip() or len(value) > 160:
        raise ValueError("Expected bounded nonempty reference")


def _number(value, low=-1e4, high=1e4):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError("Expected bounded finite number")


def _vector(value, length=3):
    if type(value) is not list or len(value) != length:
        raise ValueError("Wrong vector dimension")
    for item in value:
        _number(item)


def inspection(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("Inspection exceeds 48 KiB")
    s = parse(raw)
    keys(
        s,
        {
            "schema",
            "profile",
            "inspection_ref",
            "origin",
            "model",
            "scan",
            "frame",
            "pose",
            "calibration",
            "requirement",
            "target_element",
        },
    )
    if (
        s["schema"] != SCHEMA
        or s["profile"] != PROFILE
        or s["origin"] not in ("synthetic", "recorded")
    ):
        raise ValueError("Unsupported inspection profile or origin")
    if (
        type(s["inspection_ref"]) is not str
        or re.fullmatch(r"inspection:[a-zA-Z0-9_-]{1,64}", s["inspection_ref"]) is None
    ):
        raise ValueError("Expected opaque inspection reference")
    for name in ("frame", "target_element"):
        _text(s[name])
    m = s["model"]
    keys(m, {"revision", "sha256", "bytes_b64"})
    _text(m["revision"])
    if type(m["bytes_b64"]) is not str or len(m["bytes_b64"]) > 22000:
        raise ValueError("Model exceeds bounded export budget")
    raw_model = base64.b64decode(m["bytes_b64"], validate=True)
    if (
        not raw_model
        or bytes_ref(raw_model) != m["sha256"]
        or base64.b64encode(raw_model).decode() != m["bytes_b64"]
    ):
        raise ValueError("Model byte binding differs")
    if len(re.findall(r"#\d+\s*=", raw_model.decode("utf-8"))) > 256:
        raise ValueError("Model exceeds 256 STEP records")
    scan = s["scan"]
    keys(scan, {"capture_ref", "frame", "unit", "points_m"})
    _text(scan["capture_ref"])
    _text(scan["frame"])
    if scan["unit"] != "m":
        raise ValueError("Scan frame or units differ")
    if type(scan["points_m"]) is not list or not 10 <= len(scan["points_m"]) <= 1024:
        raise ValueError("Scan requires 10..1024 points")
    for row in scan["points_m"]:
        _vector(row)
    p = s["pose"]
    keys(
        p,
        {
            "basis",
            "control_ref",
            "model_sha256",
            "capture_ref",
            "frame",
            "scan_frame",
            "yaw",
            "translation_m",
            "covariance",
        },
    )
    if p["basis"] not in ("independent_control", "model_alignment", "unknown"):
        raise ValueError("Unknown pose basis")
    if p["scan_frame"] != scan["frame"]:
        raise ValueError("Pose scan frame differs")
    _text(p["control_ref"])
    _number(p["yaw"], -math.pi, math.pi)
    _vector(p["translation_m"])
    if (p["model_sha256"], p["capture_ref"], p["frame"]) != (
        m["sha256"],
        scan["capture_ref"],
        s["frame"],
    ):
        raise ValueError("Pose applicability differs")
    if type(p["covariance"]) is not list or len(p["covariance"]) != 4:
        raise ValueError("Pose covariance requires yaw, tx, ty, tz")
    for row in p["covariance"]:
        _vector(row, 4)
    from .core.covariance import _validate_matrix

    _validate_matrix(p["covariance"], 4)
    import numpy as np

    if float(np.linalg.eigvalsh(p["covariance"]).min()) <= 0:
        raise ValueError("Pose covariance must be positive definite")
    c = s["calibration"]
    keys(
        c,
        {
            "record_ref",
            "capture_ref",
            "applicability",
            "sensor_sigma_m",
            "systematic_sigma_m",
            "uncertainty_policy",
            "prior_cross_covariance",
        },
    )
    if c["uncertainty_policy"] != "independent_sampling_pose_systematic" or c[
        "prior_cross_covariance"
    ] not in ("declared_independent", "unknown"):
        raise ValueError("Declare uncertainty decomposition and prior cross covariance")
    _text(c["record_ref"])
    if c["capture_ref"] != scan["capture_ref"] or c["applicability"] not in (
        "declared_applicable",
        "unknown",
        "expired",
    ):
        raise ValueError("Calibration applicability differs")
    for k in ("sensor_sigma_m", "systematic_sigma_m"):
        _number(c[k], 1e-9, 1)
    r = s["requirement"]
    keys(
        r,
        {
            "id",
            "revision",
            "model_sha256",
            "frame",
            "minimum_m",
            "confidence",
            "position_sigma_m",
            "proposed",
        },
    )
    _text(r["id"])
    _text(r["revision"])
    if r["model_sha256"] != m["sha256"] or r["frame"] != s["frame"]:
        raise ValueError("Requirement model/frame differs")
    _number(r["minimum_m"], 0, 100)
    _number(r["confidence"], 0.500001, 0.999999)
    _number(r["position_sigma_m"], 0, 1)
    b = r["proposed"]
    keys(b, {"origin", "yaw", "extents"})
    _vector(b["origin"])
    _vector(b["extents"])
    _number(b["yaw"], -math.pi, math.pi)
    for v in b["extents"]:
        _number(v, 1e-6, 100)
    return s


def make_source(raw):
    s = inspection(raw)
    manifest = InstrumentManifest(
        instrument_id=PROFILE,
        role="retained_inspection",
        units={"inspection": "1"},
        frames=(s["frame"],),
        sampling={
            "kind": "declaration",
            "time_semantics": "synthetic_transport_envelope",
        },
        supported_operations=(OP,),
        calibration_requirements={"scope": "declared_applicability"},
    )
    source = {
        "run_schema": "run.v1",
        "run_id": "run-clearance-" + sha256(raw).hexdigest()[:24],
        "instrument": PROFILE,
        "metadata": {
            "duration_s": 1.0,
            "sample_count": 1,
            "sample_rate_hz": None,
            "coordinate_frame": s["frame"],
            "manifest": manifest.to_dict(),
            "inspection": {
                "sha256": bytes_ref(raw),
                "bytes_b64": base64.b64encode(raw).decode(),
            },
            "provenance": {
                "source": s["origin"],
                "generator": "ciw.metrology_clearance.make_source",
                "generator_version": 1,
            },
        },
        "time_s": [0.0],
        "channels": {"inspection": {"unit": "1", "values": [1.0]}},
        "render": {},
    }
    source["evidence_id"] = evidence_id(source)
    return source


def source_bytes(source):
    validate_run_structure(source)
    validate_evidence_identity(source)
    value = source["metadata"]["inspection"]
    keys(value, {"sha256", "bytes_b64"})
    if type(value["bytes_b64"]) is not str or len(value["bytes_b64"]) > 65536:
        raise ValueError("Inspection envelope exceeds budget")
    raw = base64.b64decode(value["bytes_b64"], validate=True)
    if source != make_source(raw):
        raise ValueError("Inspection envelope differs from exact evidence")
    return raw


def graph():
    return experiment(
        "bim-clearance",
        model_id=PROFILE,
        nodes=[
            {
                "node_id": "assess",
                "operation_id": OP,
                "parameters": {},
                "inputs": {},
                "depends_on": [],
            }
        ],
    )


def _assessment(value, requirement):
    keys(
        value,
        {
            "decision",
            "risks",
            "p_any_violation_lower",
            "p_any_violation_upper",
            "verdict",
            "scene_version",
        },
    )
    d = value["decision"]
    p = requirement["proposed"]
    expected = {
        "proposed": {"origin": p["origin"], "angle": p["yaw"], "extents": p["extents"]},
        "required_clearance": requirement["minimum_m"],
        "confidence": requirement["confidence"],
        "position_sigma": requirement["position_sigma_m"],
        "label": requirement["id"],
    }
    if d != expected:
        raise ValueError("Native assessment changed requirement")
    risks = value["risks"]
    if type(risks) is not list or not 1 <= len(risks) <= 64:
        raise ValueError("Missing or excessive risk rows")
    probs = []
    names = set()
    for risk in risks:
        keys(
            risk,
            {
                "element_name",
                "clearance_mean",
                "clearance_sigma",
                "required_clearance",
                "p_violates",
                "direction",
                "witness",
            },
        )
        _text(risk["element_name"])
        if risk["element_name"] in names:
            raise ValueError("Duplicate risk element")
        names.add(risk["element_name"])
        _number(risk["clearance_mean"])
        _number(risk["clearance_sigma"], 0, 1e4)
        _vector(risk["direction"])
        _vector(risk["witness"])
        _number(risk["p_violates"], 0, 1)
        if risk["required_clearance"] != requirement["minimum_m"]:
            raise ValueError("Risk requirement differs")
        sigma = risk["clearance_sigma"]
        delta = requirement["minimum_m"] - risk["clearance_mean"]
        probability = (
            0.5 * (1 + math.erf(delta / sigma / math.sqrt(2)))
            if sigma > 1e-15
            else float(delta > 0)
        )
        if not math.isclose(risk["p_violates"], probability, rel_tol=0, abs_tol=1e-12):
            raise ValueError(
                "Risk probability differs from retained mean and uncertainty"
            )
        probs.append(risk["p_violates"])
    lo = max(probs)
    hi = min(1.0, sum(probs))
    if not math.isclose(
        value["p_any_violation_lower"], lo, abs_tol=1e-12
    ) or not math.isclose(value["p_any_violation_upper"], hi, abs_tol=1e-12):
        raise ValueError("Invalid dependence-safe risk bounds")
    verdict = (
        "SATISFIED"
        if hi <= 1 - requirement["confidence"]
        else "VIOLATED"
        if lo >= requirement["confidence"]
        else "UNRESOLVED"
    )
    if value["verdict"] != verdict:
        raise ValueError("Native verdict differs from retained risk bounds")


def validate_payload(operation, data, source, parameters, selection):
    keys(parameters, set())
    raw = source_bytes(source)
    s = inspection(raw)
    keys(data, {"schema", "inspection_ref", "source_ref", "authority", "native"})
    if (
        operation != OP
        or data["schema"] != "ciw.metrology-clearance-result.v1"
        or data["inspection_ref"] != s["inspection_ref"]
        or data["source_ref"] != bytes_ref(raw)
        or data["authority"] != AUTHORITY
    ):
        raise ValueError("Result evidence or authority differs")
    n = data["native"]
    keys(
        n,
        {
            "status",
            "reason",
            "prior",
            "posterior",
            "likelihood",
            "registration",
            "evidence",
            "plan",
            "invariants_passed",
        },
    )
    if (
        n["status"] not in ("held", "assessed")
        or type(n["invariants_passed"]) is not bool
    ):
        raise ValueError("Invalid native disposition")
    _text(n["reason"])
    for a in ("prior", "posterior"):
        if n[a] is not None:
            _assessment(n[a], s["requirement"])
    _native_links(n, s)
    if n["status"] == "assessed":
        if (
            n["reason"] != "modeled_clearance"
            or n["posterior"] is None
            or not n["invariants_passed"]
            or s["calibration"]["prior_cross_covariance"] != "declared_independent"
            or s["pose"]["basis"] != "independent_control"
            or s["calibration"]["applicability"] != "declared_applicable"
            or not isinstance(n["likelihood"], dict)
            or n["likelihood"]["element_name"] != s["target_element"]
        ):
            raise ValueError("Assessed result lacks applicable evidence")
        l = n["likelihood"]
        for k in ("sampling_sigma", "pose_sigma", "calibration_sigma", "noise_sigma"):
            _number(l[k], 0, 1e4)
        if not math.isclose(
            l["noise_sigma"] ** 2,
            sum(
                l[k] ** 2 for k in ("sampling_sigma", "pose_sigma", "calibration_sigma")
            ),
            rel_tol=1e-10,
            abs_tol=1e-20,
        ):
            raise ValueError("Likelihood uncertainty budget differs")
        if (
            l["pose_source_id"] != s["pose"]["control_ref"]
            or l["calibration_sigma"] != s["calibration"]["systematic_sigma_m"]
        ):
            raise ValueError("Likelihood calibration binding differs")
    elif n["posterior"] is not None or n["invariants_passed"]:
        raise ValueError("Held result cannot claim a posterior acceptance")


def _native_links(n, s):
    import struct

    points = s["scan"]["points_m"]
    scan = sha256(
        struct.pack("<qq", len(points), 3)
        + b"".join(struct.pack("<d", v) for row in points for v in row)
    ).hexdigest()
    registration = n["registration"]
    if registration is not None:
        keys(
            registration,
            {"accepted", "nll", "transform", "scan_digest", "scene_version"},
        )
        _number(registration["nll"])
        keys(registration["transform"], {"theta", "t"})
        _number(registration["transform"]["theta"])
        _vector(registration["transform"]["t"])
        if (
            type(registration["accepted"]) is not bool
            or registration["accepted"] != (registration["nll"] < 6)
            or registration["scan_digest"] != scan
            or n["prior"] is None
            or registration["scene_version"] != n["prior"]["scene_version"]
        ):
            raise ValueError("Registration source/scene or acceptance differs")
    evidence = n["evidence"]
    if evidence is not None:
        keys(
            evidence,
            {
                "scan_digest",
                "scene_version",
                "point_count",
                "inlier_effective_points",
                "outlier_fraction",
                "elements",
            },
        )
        if (
            registration is None
            or not registration["accepted"]
            or evidence["scan_digest"] != scan
            or evidence["scene_version"] != registration["scene_version"]
            or evidence["point_count"] != len(points)
        ):
            raise ValueError("Evidence scan/scene binding differs")
        _number(evidence["inlier_effective_points"], 0, len(points))
        _number(evidence["outlier_fraction"], 0, 1)
        if (
            type(evidence["elements"]) is not list
            or not 1 <= len(evidence["elements"]) <= 64
        ):
            raise ValueError("Invalid evidence rows")
        names = set()
        for e in evidence["elements"]:
            keys(
                e,
                {
                    "element_row",
                    "element_name",
                    "primitive_count",
                    "effective_points",
                    "responsibility_fraction",
                    "mean_mahalanobis2",
                    "support_diversity",
                    "assignment_confidence",
                },
            )
            _text(e["element_name"])
            if e["element_name"] in names:
                raise ValueError("Duplicate evidence element")
            names.add(e["element_name"])
            if (
                type(e["element_row"]) is not int
                or e["element_row"] < 0
                or type(e["primitive_count"]) is not int
                or not 1 <= e["primitive_count"] <= 10000
            ):
                raise ValueError("Invalid evidence indices")
            _number(e["effective_points"], 0, len(points))
            _number(e["mean_mahalanobis2"], 0, 1e6)
            for k in (
                "responsibility_fraction",
                "support_diversity",
                "assignment_confidence",
            ):
                _number(e[k], 0, 1 + 1e-12)
    plan = n["plan"]
    if plan is not None:
        keys(
            plan,
            {
                "element_name",
                "action",
                "p_violates",
                "decision_entropy_nats",
                "effective_points",
                "support_diversity",
                "assignment_confidence",
                "evidence_deficit",
                "priority_proxy",
                "direction",
                "witness",
            },
        )
        if evidence is None or plan["element_name"] not in {
            e["element_name"] for e in evidence["elements"]
        }:
            raise ValueError("Plan lacks element evidence")
        if plan["action"] not in ("EXTRACT_SCAN_MEASUREMENT", "RESCAN_ELEMENT"):
            raise ValueError("Invalid plan action")
        for k in (
            "p_violates",
            "decision_entropy_nats",
            "effective_points",
            "support_diversity",
            "assignment_confidence",
            "evidence_deficit",
            "priority_proxy",
        ):
            _number(plan[k], 0, 1e4)
        _vector(plan["direction"])
        _vector(plan["witness"])
    l = n["likelihood"]
    if l is not None:
        fields = {
            "element_name",
            "direction",
            "predicted_support",
            "observed_support",
            "effective_face_points",
            "face_assignment_confidence",
            "tangent_rms",
            "sampling_sigma",
            "pose_sigma",
            "calibration_sigma",
            "noise_sigma",
            "innovation_sigma",
            "pose_disagreement_m2",
            "scan_digest",
            "scene_version",
            "pose_source_id",
            "evidence_digest",
        }
        keys(l, fields)
        if (
            plan is None
            or plan["action"] != "EXTRACT_SCAN_MEASUREMENT"
            or l["element_name"] != plan["element_name"]
            or l["element_name"] != s["target_element"]
            or l["scan_digest"] != scan
            or l["scene_version"] != registration["scene_version"]
            or l["pose_source_id"] != s["pose"]["control_ref"]
        ):
            raise ValueError("Likelihood binding differs")
        _vector(l["direction"])
        for k in fields - {
            "element_name",
            "direction",
            "scan_digest",
            "scene_version",
            "pose_source_id",
            "evidence_digest",
        }:
            _number(l[k])
        if (
            l["effective_face_points"] < 15
            or l["face_assignment_confidence"] < 0.8
            or l["tangent_rms"] < 0.1
            or l["pose_disagreement_m2"] > 16
            or abs(l["innovation_sigma"]) > 5
        ):
            raise ValueError("Likelihood violates fixed numerical gates")
    if n["status"] == "assessed" and (
        n["prior"] is None
        or registration is None
        or evidence is None
        or plan is None
        or l is None
    ):
        raise ValueError("Assessment evidence is incomplete")


def register():
    global _registered
    if not _registered:
        register_payload_validator(OP, validate_payload, role="state_estimator")
        _registered = True


class Provider:
    def __init__(self, repository, revision, *, python=sys.executable):
        self.adapter = PinnedSubprocessAdapter(
            repository,
            revision,
            "gat.session",
            source_root=".",
            python_executable=python,
            timeout_seconds=30,
            max_output_bytes=256 * 1024,
        )
        self._bound = self.runtime_identity()

    def runtime_identity(self):
        native = self.adapter.runtime_identity()
        if native["source_tree"] != NATIVE_TREE:
            raise ValueError("Unsupported CSE source tree")
        identity = {
            "provider": "ciw.metrology-clearance",
            "version": "1",
            "native": native,
            "adapter_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "worker_sha256": sha256(
                Path(__file__).with_name("metrology_clearance_worker.py").read_bytes()
            ).hexdigest(),
        }
        if hasattr(self, "_bound") and self._bound != identity:
            raise ValueError("Bound provider changed")
        return identity

    def execute(self, source, parameters):
        keys(parameters, set())
        raw = source_bytes(source)
        s = inspection(raw)
        before = self.runtime_identity()
        code, out = self.adapter._run(
            Path(__file__).with_name("metrology_clearance_worker.py").read_text(),
            [str(self.adapter.source_root)],
            raw,
        )
        if self.runtime_identity() != before:
            raise ValueError("Provider changed during execution")
        if code:
            raise AdapterRefusal(
                "CLEARANCE_PROVIDER_REFUSED",
                "Pinned CSE did not complete the inspection",
            )
        return {
            "schema": "ciw.metrology-clearance-result.v1",
            "inspection_ref": s["inspection_ref"],
            "source_ref": bytes_ref(raw),
            "authority": deepcopy(AUTHORITY),
            "native": _json(out),
        }


def registry(provider=None):
    register()
    r = CapabilityRegistry()
    manifest = InstrumentManifest(
        instrument_id=PROFILE,
        role="state_estimator",
        units={},
        frames=(),
        sampling={"kind": "bounded_inspection"},
        supported_operations=(OP,),
        calibration_requirements={"physical_qualification": "not_established"},
    )
    identity = (
        {
            "provider": "ciw.metrology-clearance",
            "version": "1",
            "native_tree": NATIVE_TREE,
        }
        if provider is None
        else provider.runtime_identity()
    )
    r.advertise(
        manifest,
        runtime=identity,
        capabilities={OP: ["metrology.bim-clearance"]},
        inputs={OP: {}},
    )
    if provider is not None:
        r.bind(
            Operation(
                OP, "state_estimator", provider.execute, provider.runtime_identity
            )
        )
    return r


class ClearanceHost(AgentHost):
    def validate_plan(self, run, value):
        source_bytes(run)
        if value != graph():
            raise ValueError("Clearance inspection requires its fixed graph and policy")
        return super().validate_plan(run, value)


def prepare(raw, destination):
    source = make_source(raw)
    root = Path(destination)
    root.mkdir(parents=False, exist_ok=False)
    (root / "inspection.json").write_bytes(raw)
    save_new(root / "source.json", source)
    save_new(root / "experiment.json", graph())
    save_new(
        root / "profile.json",
        {
            "schema": "ciw.agent-host-profile.v1",
            "inputs": {"source": "source.json", "baseline": "experiment.json"},
            "output_dir": "agent-output",
            "allow_operations": [OP],
            "candidate_domains": {},
            "comparisons": {},
            "check_suites": {},
            "max_executions": 8,
            "max_nodes": 1,
        },
    )
    return root / "profile.json"


def main(argv=None):
    import argparse

    from .agent_mcp import from_profile, serve

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("inspection")
    prep.add_argument("destination")
    for command in ("run", "serve"):
        p = sub.add_parser(command)
        p.add_argument("profile")
        p.add_argument("--provider", required=True)
        p.add_argument("--revision", required=True)
        p.add_argument("--python", default=sys.executable)
        p.add_argument("--resume", action="store_true")
        if command == "run":
            p.add_argument("--attempt", required=True)
    p = sub.add_parser("inspect")
    p.add_argument("workspace")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            print(prepare(Path(args.inspection).read_bytes(), args.destination))
            return 0
        if args.command == "inspect":
            register()
            from .agent_api import _validated

            print(encode(_validated(parse(Path(args.workspace).read_bytes()))).decode())
            return 0
        provider = Provider(args.provider, args.revision, python=args.python)
        with from_profile(
            Path(args.profile),
            instrument="bim-clearance",
            clearance_provider=provider,
            resume=args.resume,
        ) as host:
            if args.command == "serve":
                return serve(host, sys.stdin.buffer, sys.stdout.buffer)
            result = host.call(
                "net_execute",
                {"source": "source", "graph": "baseline", "attempt": args.attempt},
            )
            print(encode(result).decode())
            return 0 if result["status"] == "completed" else 2
    except (ValueError, OSError, AdapterRefusal) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
