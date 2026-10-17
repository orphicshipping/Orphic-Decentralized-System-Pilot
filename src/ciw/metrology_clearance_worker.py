"""Fixed CSE bridge executed only by the operator-pinned subprocess adapter."""

import base64
import json
import sys
from dataclasses import asdict

import numpy as np


def main():
    sys.path.insert(0, sys.argv[1])
    from gat.adapters.ifc.units import length_unit_context
    from gat.errors import GatError
    from gat.geometry import (
        ClearanceDecision,
        ClearanceLikelihoodCalibration,
        IndependentPoseCalibration,
        OrientedBox,
        RegistrationResult,
        RigidTransformZ,
        ScanRegistrar,
        adapt_clearance_likelihood,
        assess_clearance,
        derive_scene,
        plan_clearance_evidence,
    )
    from gat.geometry.registration import _scan_digest
    from gat.session import GatSession

    source = json.loads(sys.stdin.buffer.read())
    result = {
        "status": "held",
        "reason": "evidence_not_assessed",
        "prior": None,
        "posterior": None,
        "likelihood": None,
        "registration": None,
        "evidence": None,
        "plan": None,
        "invariants_passed": False,
    }
    try:
        session = GatSession.from_text(
            base64.b64decode(source["model"]["bytes_b64"]).decode(),
            source=source["model"]["sha256"],
        )
        if session.world.binding.n_full > 64:
            raise ValueError("Model exceeds 64 variables")
        if length_unit_context(session.source_file).assumed:
            result["reason"] = "model_units_assumed"
        elif not session.initial_report.passed:
            result["reason"] = "initial_invariants_failed"
        elif source["pose"]["basis"] != "independent_control":
            result["reason"] = "independent_pose_missing"
        elif source["calibration"]["applicability"] != "declared_applicable":
            result["reason"] = "calibration_unresolved"
        elif source["calibration"]["prior_cross_covariance"] != "declared_independent":
            result["reason"] = "unknown_prior_cross_covariance"
        else:
            scene = derive_scene(session.world)
            requirement = source["requirement"]
            proposal = requirement["proposed"]
            decision = ClearanceDecision(
                OrientedBox(
                    tuple(proposal["origin"]),
                    proposal["yaw"],
                    tuple(proposal["extents"]),
                ),
                requirement["minimum_m"],
                requirement["confidence"],
                requirement["position_sigma_m"],
                requirement["id"],
            )
            before = assess_clearance(scene, decision)
            result["prior"] = asdict(before)
            scan = np.array(source["scan"]["points_m"], dtype=float)
            p = source["pose"]
            pose = IndependentPoseCalibration(
                RigidTransformZ(p["yaw"], tuple(p["translation_m"])),
                np.array(p["covariance"]),
                _scan_digest(scan),
                p["control_ref"],
            )
            registrar = ScanRegistrar(scene, max_iter=25)
            # Registration is an association gate, never the independent measurement pose.
            transform, nll, trace = registrar.register_from(
                scan, pose.transform, max_iter=25
            )
            registration = RegistrationResult(
                transform,
                nll,
                tuple(trace),
                (),
                (),
                registrar._information_matrix(scan, transform),
                bool(nll < 6.0),
                _scan_digest(scan),
                scene.version,
            )
            result["registration"] = {
                "accepted": registration.accepted,
                "nll": nll,
                "transform": asdict(transform),
                "scan_digest": registration.scan_digest,
                "scene_version": scene.version,
            }
            if not registration.accepted:
                result["reason"] = "association_rejected"
            else:
                evidence = registrar.evidence(scan, registration)
                plan = plan_clearance_evidence(before, evidence)
                result["evidence"] = asdict(evidence)
                result["plan"] = (
                    None if plan.selected is None else asdict(plan.selected)
                )
                if (
                    plan.selected is None
                    or plan.selected.element_name != source["target_element"]
                ):
                    result["reason"] = "target_not_selected"
                else:
                    likelihood = adapt_clearance_likelihood(
                        scene,
                        registrar,
                        scan,
                        registration,
                        evidence,
                        plan,
                        pose,
                        ClearanceLikelihoodCalibration(
                            sensor_sigma=source["calibration"]["sensor_sigma_m"],
                            calibration_sigma=source["calibration"][
                                "systematic_sigma_m"
                            ],
                        ),
                    )
                    result["likelihood"] = {
                        k: v
                        for k, v in asdict(likelihood).items()
                        if k != "observation"
                    }
                    transition = session.run(
                        likelihood.observation,
                        provenance={
                            "inspection_ref": source["inspection_ref"],
                            "calibration_ref": source["calibration"]["record_ref"],
                        },
                        strict=False,
                    )
                    result["invariants_passed"] = bool(
                        transition.report.passed and transition.committed
                    )
                    if result["invariants_passed"]:
                        result["posterior"] = asdict(
                            assess_clearance(derive_scene(session.world), decision)
                        )
                        result.update(status="assessed", reason="modeled_clearance")
                    else:
                        result["reason"] = "invariant_rejection"
    except GatError as error:
        result.update(
            status="held", reason=(type(error).__name__ + ": " + str(error))[:160]
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
