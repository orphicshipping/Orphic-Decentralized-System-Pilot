"""Opt-in bounded reference-data qualification using existing NET consumers.

No downloads, imported author programs, substitute solver or source publication.
The five named SBML fixtures are explicitly mapped to the existing binary-site
model; this is not a general SBML reader. Acquisition, typed retention, numerical
agreement and physical qualification are deliberately separate in the report.
"""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import platform

from ciw import sequence_kinetics, sequence_workflow
from ciw.core.identities import content_identity
from ciw.data_sources import csv_observation_stream, native_record
from ciw.data_workflow import COMPARE, comparison_parameters, operation
from ciw.session import Session

ACQUISITION_SHA256 = "03eeb282856e606cc58213912759f6dee7af93443bd2510129eb28b44224fd2d"
SBML_REVISION = "473e119dd57226c3a7a729d598f9007f06f781c3"
TCLAB_SHA256 = "943b47adad39f5cac8db1daa9dad7043e812b4a37066b4d912931d8661d5b195"
# Initial conserved amount, initial probability, forward/reverse s^-1 and
# amount per normalized B state. Case 3 preserves S1 + S2/2, not S1 + S2.
CASES = {
    1: (0.00015, [1.0, 0.0], 1.0, 0.0, 1),
    2: (0.0015, [1.0, 0.0], 1.0, 0.0, 1),
    3: (0.015, [1.0, 0.0], 1.0, 0.0, 2),
    5: (0.0003, [0.5, 0.5], 1.0, 0.0, 1),
    6: (0.003, [0.5, 0.5], 0.5, 0.5, 1),
}
UNSUPPORTED_SBML = {
    4: "Bimolecular reverse reaction is outside independent binary-site kinetics.",
    7: "Fixed boundary species creates an open amount balance; no closed binary mapping.",
    8: "Fixed boundary species creates an open amount balance; no closed binary mapping.",
    9: "Both boundary species are fixed; turnover is not represented by binary-state evolution.",
    10: "Three species and a bimolecular reaction are outside this two-state model.",
}


def checked_bytes(path: Path, digest: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Require original regular file: {path.name}")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != digest:
        raise ValueError(f"Source SHA256 mismatch: {path.name}")
    return raw


def check_acquisition(root: Path) -> dict:
    receipt = json.loads(checked_bytes(root / "acquisition.json", ACQUISITION_SHA256))
    for row in receipt["files"]:
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe acquisition path")
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Acquisition path escapes root")
        raw = checked_bytes(path, row["sha256"])
        if len(raw) != row["bytes"]:
            raise ValueError("Source byte count mismatch")
        if row.get("git_blob_sha1"):
            from hashlib import sha1
            blob = sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if blob != row["git_blob_sha1"]:
                raise ValueError("Publisher Git blob mismatch")
    return receipt


def mapping(case: int, semantics: str, execution_id=None) -> dict:
    return {
        "identity": {"model_id": f"sbml.semantic.{case:05d}", "entity_id": "compartment",
                     "execution_id": execution_id},
        "clock": {"id": f"sbml.semantic.{case:05d}/time", "column": "time", "unit": "s"},
        "frame": "species-amount",
        "provenance": {"provider": "SBML-test-suite" if semantics == "reference" else
                        "ciw.sequence.binary-evolve.v1/explicit-amount-projection", "semantics": semantics},
        "quantities": [{"column": name, "quantity": name, "unit": "mol"} for name in ("S1", "S2")],
    }


def add_csv(session, raw, spec, dataset, stamp):
    envelope = csv_observation_stream(raw, mapping=spec, dataset_id=dataset, revision=1,
                                     parent_source_id=None, recorded_at=stamp)
    source = session.workbench.add_source({"kind": "data-record", "label": dataset,
        "bytes_b64": base64.b64encode(json.dumps(envelope, allow_nan=False).encode()).decode()})
    observations = native_record(session.workbench.get_source(source["source_id"]))["observations"]
    if any(row["uncertainty"] is not None for row in observations):
        raise ValueError("Missing uncertainty was replaced")
    if base64.b64decode(envelope["source_bytes"]["bytes_b64"]) != raw:
        raise ValueError("Exact input bytes changed")
    return source, envelope


def execute(session, op, parameters):
    answer = session.handle({"protocol_version": 1, "request_id": "academic-qualification",
        "type": "operation.execute", "payload": {"operation_id": op, "parameters": parameters}})
    if answer["type"] != "response" or answer["payload"]["status"] != "completed":
        raise ValueError(f"Native consumer refused: {answer}")
    return answer["payload"]


def run_sbml(root: Path, receipt: dict, case: int, destination: Path, stamp: str) -> dict:
    if case not in CASES:
        raise ValueError(UNSUPPORTED_SBML.get(case, "No reviewed mapping for this case"))
    prefix = f"sources/sbml/cases/semantic/{case:05d}/"
    sources = [row for row in receipt["files"] if row["path"].startswith(prefix)]
    if len(sources) != 4 or any(row.get("commit") != SBML_REVISION for row in sources):
        raise ValueError("The four pinned SBML source files are required")
    # Recheck at consumption, even if an earlier acquisition audit succeeded.
    # Copy these checked bytes at the end rather than rereading mutable paths.
    source_bytes = {row["path"]: checked_bytes(root / row["path"], row["sha256"]) for row in sources}
    raw = source_bytes[f"{prefix}{case:05d}-results.csv"]
    rows = list(csv.DictReader(io.StringIO(raw.decode())))
    if len(rows) != 51 or set(rows[0]) != {"time", "S1", "S2"}:
        raise ValueError("Unexpected reference table shape")
    total, initial, forward, reverse, factor = CASES[case]
    projection = {"schema": "ciw.academic-sequence-projection.v1", "case": case,
        "source_files": [{k: row[k] for k in ("path", "sha256", "bytes", "commit")} for row in sources],
        "formula": "S1=M*p0; S2=M*stoichiometric_factor*p1",
        "M_mol": total, "initial_probability": initial, "stoichiometric_factor": factor,
        "rate_0_to_1_per_s": forward, "rate_1_to_0_per_s": reverse,
        "scope": "reviewed_fixed_case_equivalence_not_general_SBML_execution"}
    request = sequence_kinetics.example_request()
    request.update(source_kind="declared_distribution", source_ref=content_identity(projection),
        alphabet=["S1", "S2"], block_length=1, probabilities=initial, queries=[])
    request["evolution"].update(rate_0_to_1_per_s=forward, rate_1_to_0_per_s=reverse,
                                time_s=[float(row["time"]) for row in rows])
    registry = sequence_workflow.registry()
    registry.register(operation())
    destination.mkdir(parents=True, exist_ok=False)
    session = Session(sequence_workflow.make_source(request), destination, operations=registry)
    reference, envelope = add_csv(session, raw, mapping(case, "reference"), f"sbml-{case:05d}/reference", stamp)
    analyze = execute(session, sequence_workflow.ANALYZE, {})
    evolution = execute(session, sequence_workflow.EVOLVE, {})
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["time", "S1", "S2"])
    for sample in evolution["result"]["data"]["samples"]:
        a, b = sample["probabilities"]
        writer.writerow([sample["time_s"], total * a, total * factor * b])
    predicted, _ = add_csv(session, stream.getvalue().encode(),
        mapping(case, "simulated", evolution["execution"]["execution_id"]), f"sbml-{case:05d}/prediction", stamp)
    # Stricter than the publisher's 1e-7/1e-6 absolute and 1e-4 relative policy.
    # This is a finite numerical check, not experimental uncertainty.
    comparisons = []
    for quantity in ("S1", "S2"):
        result = execute(session, COMPARE, comparison_parameters(session.workbench,
            left_source_id=predicted["source_id"], right_source_id=reference["source_id"],
            quantity=quantity, atol=1e-14, rtol=1e-12))
        outcome = result["result"]["data"]["comparison"]["outcome"]
        if outcome["status"] != "PASS":
            raise ValueError(f"Published SBML reference mismatch: {outcome}")
        comparisons.append({"quantity": quantity, **outcome})
    workspace = session.save_workspace(destination / "workspace.json")
    restored = Session.from_workspace(workspace, output_dir=destination / "offline")
    if restored.results != session.results or restored.workbench.serialize() != session.workbench.serialize():
        raise ValueError("Offline restoration changed retained evidence")
    (destination / "projection.json").write_text(json.dumps(projection, indent=2) + "\n")
    # Preserve every reviewed model/settings/source byte without executing it.
    for row in sources:
        (destination / Path(row["path"]).name).write_bytes(source_bytes[row["path"]])
    return {"case": f"{case:05d}", "status": "PASS", "reference_samples": len(rows),
        "reference_scalar_values": 2 * len(rows), "native_executions": len(session.executions),
        "consumer_operations": [sequence_workflow.ANALYZE, sequence_workflow.EVOLVE, COMPARE],
        "projection_ref": content_identity(projection), "source_evidence_id": session.run["evidence_id"],
        "reference_source_id": reference["source_id"], "prediction_source_id": predicted["source_id"],
        "source_csv_sha256": envelope["source_bytes"]["sha256"], "comparisons": comparisons,
        "source_bytes_and_uncertainty": "preserved; uncertainty absent, never zero",
        "workspace": str(workspace), "runtime": evolution["execution"]["runtime"],
        "authority": sequence_kinetics.AUTHORITY, "general_sbml_solver": "not_implemented"}


def retain_tclab(path: Path, destination: Path, stamp: str) -> dict:
    raw = checked_bytes(path, TCLAB_SHA256)
    quantities = [("heater1_command_pct", "percent_command"), ("heater2_command_pct", "percent_command"),
                  ("temperature1_degC", "degC"), ("temperature2_degC", "degC")]
    spec = {"identity": {"model_id": "TCLab-published-bench", "entity_id": "two-heater-recording", "execution_id": None},
        "clock": {"id": "TCLab-data2-relative-time", "column": "time_s", "unit": "s"},
        "frame": "TCLab-sensor-and-actuator-labels", "provenance": {"provider": "APMonitor/published-prefix-transcription", "semantics": "observed"},
        "quantities": [{"column": name, "quantity": name, "unit": unit} for name, unit in quantities]}
    from ciw.instruments import make_demo_run
    session = Session(make_demo_run(), destination)
    source, envelope = add_csv(session, raw, spec, "tclab/published-0-200s-prefix", stamp)
    session.save_workspace(destination / "workspace.json")
    return {"dataset": "tclab_prefix", "status": "TYPED_RETENTION_ONLY", "source_id": source["source_id"],
        "source_sha256": envelope["source_bytes"]["sha256"], "rows": envelope["adapter_receipt"]["input_row_count"],
        "observations": envelope["adapter_receipt"]["output_observation_count"], "native_executions": 0,
        "scope": "Published 0-200s prefix only; full recording, calibration, noise, heat capacity and flux absent.",
        "physical_validation": "not_performed", "uncertainty": "absent", "synthetic_values_used": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition-root", type=Path, required=True)
    parser.add_argument("--tclab-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = check_acquisition(args.acquisition_root)
    checked_bytes(args.tclab_csv, TCLAB_SHA256)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    stamp = datetime.now(timezone.utc).isoformat()
    runs = [run_sbml(args.acquisition_root, receipt, case, args.output_dir / f"sbml-{case:05d}", stamp)
            for case in CASES]
    report = {"schema": "ciw.academic-consumer-qualification.v1", "recorded_at": stamp,
        "python": platform.python_version(), "source_receipt_sha256": ACQUISITION_SHA256,
        "acquisition_files_hash_verified": len(receipt["files"]), "retained_source_bytes": receipt["retained_bytes"],
        "original_acquisition_failures": receipt["failures"], "sbml": runs,
        "sbml_cases_without_compatible_consumer": UNSUPPORTED_SBML,
        "tclab": retain_tclab(args.tclab_csv, args.output_dir / "tclab", stamp),
        "native_executions": sum(row["native_executions"] for row in runs),
        "reference_scalar_values": sum(row["reference_scalar_values"] for row in runs),
        "physical_qualification": "not_established", "hardware_actuation": "not_performed"}
    (args.output_dir / "qualification.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: report[key] for key in ("native_executions", "reference_scalar_values", "acquisition_files_hash_verified")}))


if __name__ == "__main__":
    main()
