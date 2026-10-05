"""Authored outcomes for exercising the bench; not empirical detector performance."""
from decimal import Decimal

from .manufacturing_contracts import content_identity
from .manufacturing_example import example_request as manufacturing_example
from .manufacturing_runtime import evaluate as manufacture
from .toolpoint_stream import CaptureJournal, make_spec
from .toolpoint_stream_example import fixture, prefix
from .toolpoint_outcome_bench import PROTOCOL, REQUEST, unit_ref


def example_request(*, case="mixed"):
    if case not in {"mixed", "missing-inspection", "late-alarm", "all-clear"}:
        raise ValueError("Unknown authored outcome scenario")
    manufacturing = manufacture(manufacturing_example())
    streams = []
    for index in range(2):
        plan, observation = fixture(profile="native", case="late-unknown" if index == 0 and case != "all-clear" else "nominal", cycle_index=index)
        # Demonstrate an actual process-deviation witness, not missing-uncertainty diagnosis.
        observation["signals"][1]["samples"][-1]["expanded_uncertainty"] = 10000
        journal = CaptureJournal(make_spec(plan, prefix(plan, observation, .8, final=True)))
        for through in (.2, .6, .8):
            capture = prefix(plan, observation, through, final=through == .8)
            if index == 1 and through == .2 and case != "all-clear":
                capture["signals"][0]["health"] = "fault"
            delay = Decimal("1.01") if case == "late-alarm" and index == 0 and through >= .6 else Decimal("0.01")
            journal.append(journal.update(capture, final=through == .8,
                received_at_s=float(Decimal(str(capture["completed_at_s"]))+delay)))
        streams.append(journal.report())
    mref = manufacturing["request_ref"]
    scope = {"part_ref": manufacturing["request"]["configuration"]["part_ref"],
             "characteristic_id": "synthetic.internal-integrity", "specification_ref": content_identity({"synthetic_endpoint": "internal-integrity"})}
    protocol = {"schema": PROTOCOL, "study_id": "synthetic.toolpoint-outcomes", "revision": "A",
        "source_kind": "synthetic", **scope, "inspection_condition": "conditioned",
        "clock_id": manufacturing["request"]["clock_id"], "frozen_at_s": 0,
        "freeze_evidence_ref": content_identity({"synthetic_freeze": True}),
        "decision_deadline_after_start_s": 1.0,
        "monitoring_plan_refs": sorted({content_identity(s["spec"]["plan"]) for s in streams}),
        "enrollment": [{"manufacturing_request_ref": mref, "serial": f"p{i:03d}"} for i in range(1, 5)],
        "development_lot_ids": ["synthetic.development-lot"], "development_cycle_refs": [], "development_evidence_refs": [],
        "iid_lot_sampling_declared": True, "sampling_basis_ref": content_identity({"synthetic_sampling": "independent lots"}),
        "confidence_level": .95,
        "criteria": {"min_defective_lots": 3, "min_conforming_lots": 3, "max_lot_miss_upper": .1, "max_lot_false_hold_upper": .2}}
    outcomes = []
    for i in range(1, 5):
        outcomes.append({"unit_ref": unit_ref(mref, f"p{i:03d}"), "source_kind": "synthetic", **scope,
            "condition": "conditioned", "label": "CONFORMING" if i in (1, 3) else "NONCONFORMING",
            "observed_at_s": 12, "evidence_ref": content_identity({"synthetic_independent_outcome": i}),
            "method_ref": content_identity({"synthetic_method": "post-process independent inspection"}),
            "calibration_ref": content_identity({"synthetic_independent_calibration": True}),
            "independence_basis_ref": content_identity({"synthetic_blind_inspection": True}), "blinded_to_qc": True})
    if case == "missing-inspection":
        outcomes.pop()
    return {"schema": REQUEST, "protocol": protocol, "manufacturing_reports": [manufacturing],
            "streams": streams, "outcomes": outcomes, "assessed_at_s": 20}
