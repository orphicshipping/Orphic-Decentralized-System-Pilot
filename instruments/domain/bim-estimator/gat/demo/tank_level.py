"""Synthetic tank-level observation using the calibrated quantity contract, no IFC.

    python -m gat.demo.tank_level --demo -o out/tank-t1

The fixed prior and observation are demonstration inputs, never field evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gat.engine.executor import World
from gat.engine.verify import run_invariants
from gat.evidence import CalibratedObservation, EvidenceKind
from gat.harness.calibration import bind_measurement, load_calibration_file, load_measurement
from gat.ids import EntityId, VarId
from gat.ir.core import Entity, Module, NonNegative, QtySlot, Role, Unit
from gat.ledger import write_ledger
from gat.session import GatSession

_DEMO = Path(__file__).resolve().parent
_CAL = _DEMO / "harness_fixtures" / "tank-t1-calibration-v1.json"
_MEAS = _DEMO / "harness_fixtures" / "tank-t1-rci.jsonl"


def run_tank_level(output_dir: str | Path) -> dict[str, object]:
    """Condition one synthetic level prior on the bundled simulated RCI record."""
    calibration = load_calibration_file(_CAL)
    expected = ("IndustrialSlot", "TANK-T1", "Level", "m", "m")
    actual = (calibration.ifc_class, calibration.global_id, calibration.quantity,
              calibration.indicated_unit, calibration.canonical_unit)
    if actual != expected:
        raise ValueError("tank demo calibration must identify IndustrialSlot:TANK-T1.Level in metres")
    records = [json.loads(line) for line in _MEAS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError("tank demo requires exactly one RCI observation")
    record = records[0]
    quality = record.get("quality")
    if not isinstance(quality, dict) or quality.get("acquisition") != "received" or quality.get("calibration") != "applicable":
        raise ValueError("tank demo requires a received observation with applicable calibration")
    if record.get("indicated_unit") != calibration.indicated_unit:
        raise ValueError("RCI indicated unit does not match tank calibration")
    # RCI carries acquisition metadata; project only the explicit measurement
    # contract and retain both original files in the evidence source identity.
    measurement = load_measurement({"schema": "cse-measurement-v1",
        "claim_scope": "record-integrity-only", **{name: record.get(name) for name in
        ("observation_id", "calibration_id", "indicated", "sigma", "sigma_unit", "sigma_reason")}})
    indicated = bind_measurement(calibration, measurement)
    entity_id = EntityId("IndustrialSlot", "TANK-T1")
    subject = VarId(entity_id, "Level")
    prior_mean, prior_sigma = 2.0, 0.25
    slot = QtySlot(subject, Role.RAW, Unit.M, prior_mu=prior_mean, prior_sigma=prior_sigma)
    module = Module({entity_id: Entity(entity_id, "Synthetic Tank T1", slots={"Level": slot})},
                    (), (NonNegative(subject),), {"source": "synthetic:tank-level-demo-v1"})
    session = GatSession(World.compile(module))
    observation = CalibratedObservation.from_source_bytes(measurement.observation_id,
        subject, EvidenceKind.SIMULATED, indicated, measurement.sigma, "m",
        _CAL.read_bytes() + b"\0" + _MEAS.read_bytes(), calibration.calibration_id, calibration.digest)
    prior = session.world
    result = session.run(observation.transformation(prior), provenance=observation.provenance())
    if not result.committed:
        raise RuntimeError("synthetic tank observation was not committed")
    verification = run_invariants(session.world)
    disposition = "REQUEST_EVIDENCE" if verification.passed else "REJECT"
    document = {
        "schema": "cse-tank-level-demo-report-v1", "claim_scope": "record-integrity-only",
        "synthetic": True, "released": False, "calibration": calibration.to_document(),
        "prior": {"mean": prior_mean, "sigma": prior_sigma, "unit": "m", "kind": "SIMULATED"},
        "observation": observation.to_dict(),
        "posterior": {"mean": float(session.world.full.mean(subject)), "unit": "m"},
        "verification": {"passed": verification.passed, "counts": list(verification.counts()),
            "disposition": disposition, "reason": "Synthetic demonstration inputs are not field evidence."},
        "receipt": {"subject": "IndustrialSlot:TANK-T1.Level", "workflow": "TANK_LEVEL_VERIFICATION",
            "disposition": disposition, "evidence_kind": "SIMULATED", "evidence_digest": observation.digest(),
            "prior_world_digest": prior.digest(), "result_world_digest": session.world.digest(),
            "calibration_id": calibration.calibration_id, "calibration_digest": calibration.digest,
            "not_traceable": calibration.not_traceable, "usable_as_field_evidence": False},
        "world_digest": session.world.digest(),
    }
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "tank-level-report.json").write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    write_ledger(session.ledger, output / "ledger.json")
    return document


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the synthetic tank-level demo; no physical acquisition.")
    parser.add_argument("--demo", action="store_true", help="Use the bundled simulated tank observation")
    parser.add_argument("-o", "--output", default="out/tank-t1")
    args = parser.parse_args(argv)
    document = run_tank_level(args.output)
    print(f"wrote {args.output}/tank-level-report.json")
    print(f"slot {document['receipt']['subject']}")
    print(f"verification {document['verification']['disposition']}")
    print(f"field evidence {document['receipt']['usable_as_field_evidence']}")


if __name__ == "__main__":
    main()
