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
from decimal import Decimal
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import platform

from ciw import sequence_kinetics, sequence_workflow
from ciw.core.identities import content_identity
from ciw.data_sources import csv_observation_stream, native_record, retain_record
from ciw.data_workflow import COMPARE, comparison_parameters, operation
from ciw.session import Session

ACQUISITION_SHA256 = "03eeb282856e606cc58213912759f6dee7af93443bd2510129eb28b44224fd2d"
SBML_REVISION = "473e119dd57226c3a7a729d598f9007f06f781c3"
TCLAB_SHA256 = "943b47adad39f5cac8db1daa9dad7043e812b4a37066b4d912931d8661d5b195"
FAO56_SHA256 = "a1065d6063362cf68f97e09e1b666fa94ce7fd0bd66396b1fb18b41551845332"
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


def tclab_mapping() -> dict:
    quantities = [("heater1_command_pct", "percent_command"), ("heater2_command_pct", "percent_command"),
                  ("temperature1_degC", "degC"), ("temperature2_degC", "degC")]
    return {"identity": {"model_id": "TCLab-published-bench", "entity_id": "two-heater-recording", "execution_id": None},
        "clock": {"id": "TCLab-data2-relative-time", "column": "time_s", "unit": "s"},
        "frame": "TCLab-sensor-and-actuator-labels", "provenance": {"provider": "APMonitor/published-prefix-transcription", "semantics": "observed"},
        "quantities": [{"column": name, "quantity": name, "unit": unit} for name, unit in quantities]}


def retain_tclab(path: Path, destination: Path, stamp: str) -> dict:
    raw = checked_bytes(path, TCLAB_SHA256)
    from ciw.instruments import make_demo_run
    session = Session(make_demo_run(), destination)
    source, envelope = add_csv(session, raw, tclab_mapping(), "tclab/published-0-200s-prefix", stamp)
    session.save_workspace(destination / "workspace.json")
    return {"dataset": "tclab_prefix", "status": "TYPED_RETENTION_ONLY", "source_id": source["source_id"],
        "source_sha256": envelope["source_bytes"]["sha256"], "rows": envelope["adapter_receipt"]["input_row_count"],
        "observations": envelope["adapter_receipt"]["output_observation_count"], "native_executions": 0,
        "scope": "Published 0-200s prefix only; full recording, calibration, noise, heat capacity and flux absent.",
        "physical_validation": "not_performed", "uncertainty": "absent", "synthetic_values_used": False}


def run_tclab_fir(path: Path, library: Path, library_sha256: str, destination: Path, stamp: str) -> dict:
    """Use actual source predecessors; no zero padding, fake clock or new DSP kernel."""
    from ciw import dsp
    from ciw.adapters.protocol import InstrumentManifest
    from ciw.control_contracts import observation, record
    from ciw.core.identities import evidence_id
    from ciw.operations.registry import OperationRegistry

    raw = checked_bytes(path, TCLAB_SHA256)
    rows = list(csv.DictReader(io.StringIO(raw.decode())))
    mapping_spec = tclab_mapping()
    units = {item["column"]: item["unit"] for item in mapping_spec["quantities"]}
    times = [float(row["time_s"]) for row in rows]
    if times != list(range(201)):
        raise ValueError("The pinned 1 Hz source grid changed")
    manifest = InstrumentManifest(instrument_id="academic.tclab-recording.v1", role="measurement_adapter",
        units=units, frames=(mapping_spec["frame"],), sampling={"kind": "uniform", "time_unit": "s",
        "source_scope": "published 0-200 s prefix; first two rows reserved as real FIR predecessors"},
        supported_operations=(), calibration_requirements={"status": "not_established"})
    source = {"run_schema": "run.v1", "run_id": "run-tclab-" + TCLAB_SHA256[:16],
        "instrument": manifest.instrument_id, "metadata": {"duration_s": 201.0, "sample_count": 199,
            "sample_rate_hz": 1.0, "coordinate_frame": mapping_spec["frame"], "manifest": manifest.to_dict(),
            "provenance": {"source": "published physical bench prefix transcription", "sha256": TCLAB_SHA256,
                "recording_rows": [2, 200], "predecessor_rows": [0, 1], "clock": mapping_spec["clock"]["id"],
                "calibration": "not_established", "uncertainty": "absent"}},
        "time_s": times[2:], "channels": {name: {"unit": unit, "values": [float(row[name]) for row in rows[2:]]}
                                                   for name, unit in units.items()}, "render": {}}
    source["evidence_id"] = evidence_id(source)
    binding = dsp.FirBinding(library, expected_sha256=library_sha256)
    registry = OperationRegistry()
    registry.register(binding.operation())
    registry.register(operation())
    destination.mkdir(parents=True, exist_ok=False)
    session = Session(source, destination, operations=registry)
    original, _ = add_csv(session, raw, mapping_spec, "tclab/published-0-200s-prefix", stamp)

    def add_native(native, dataset):
        envelope = retain_record(native, dataset_id=dataset, revision=1, parent_source_id=None,
                                 recorded_at=stamp, data_family="observation-stream")
        return session.workbench.add_source({"kind": "data-record", "label": dataset,
            "bytes_b64": base64.b64encode(json.dumps(envelope).encode()).decode(),
            "input_source_ids": [original["source_id"]]})

    comparisons = []
    for name in units:
        parameters = {"channel": name, "interval_s": [2.0, 201.0], "taps": [.25, .5, .25],
            "initial_history": [float(row[name]) for row in rows[:2]], "clock_id": mapping_spec["clock"]["id"]}
        result = execute(session, dsp.OPERATION, parameters)["result"]
        if result["data"]["history_used"] != parameters["initial_history"]:
            raise ValueError("Native FIR did not use the actual source predecessors")
        predicted_stream = dsp.observations(result, semantics="estimated")
        if any(row["uncertainty"] is not None for row in predicted_stream["observations"]):
            raise ValueError("FIR introduced measurement uncertainty")
        # Independent decimal convolution from original published decimal tokens,
        # not the native library, NumPy or another call to the NET implementation.
        reference = []
        for index, prototype in enumerate(predicted_stream["observations"], 2):
            value = sum(Decimal(coefficient) * Decimal(rows[index - lag][name])
                        for lag, coefficient in enumerate(("0.25", "0.5", "0.25")))
            identity = {**prototype["identity"], "execution_id": None}
            reference.append(observation(identity=identity, clock=prototype["clock"], frame=prototype["frame"],
                quantity=prototype["quantity"], value=float(value), unit=prototype["unit"],
                provenance={"provider": "independent-decimal-convolution", "semantics": "reference",
                            "sources": ["sha256:" + TCLAB_SHA256]}, uncertainty=None))
        predicted = add_native(predicted_stream, "tclab/fir/" + name)
        expected = add_native(record("observation-stream", observations=reference), "tclab/oracle/" + name)
        comparison = execute(session, COMPARE, comparison_parameters(session.workbench,
            left_source_id=predicted["source_id"], right_source_id=expected["source_id"],
            quantity="filtered." + name, atol=1e-12, rtol=0.0))["result"]["data"]["comparison"]["outcome"]
        if comparison["status"] != "PASS":
            raise ValueError(f"Native FIR differs from independent convolution: {comparison}")
        comparisons.append({"channel": name, **comparison})
    workspace = session.save_workspace(destination / "workspace.json")
    restored = Session.from_workspace(workspace, output_dir=destination / "offline")
    if restored.results != session.results:
        raise ValueError("Native FIR workspace did not restore unchanged")
    return {"dataset": "tclab_prefix", "status": "BOUNDED_NATIVE_FILTER_COMPARISON_PASS",
        "consumer_operations": [dsp.OPERATION, COMPARE], "native_executions": len(session.executions),
        "native_filter_executions": 4, "filtered_scalar_values": 796, "comparisons": comparisons,
        "source_sha256": TCLAB_SHA256, "source_evidence_id": source["evidence_id"], "runtime": binding.runtime_identity(),
        "clock": mapping_spec["clock"]["id"], "time_s": [2, 200], "source_predecessors_s": [0, 1],
        "padding": "none", "uncertainty": "absent_not_propagated", "workspace": str(workspace),
        "scope": "native numerical filter agreement; no calibrated thermal model or physical frequency-response claim"}


def run_fao56(path: Path, destination: Path, stamp: str) -> dict:
    """Execute the existing ET0 endpoint without inventing a full farm request."""
    import math
    from ciw import irrigation_reference, irrigation_solver
    from ciw.control_contracts import artifact, validate_artifact
    from ciw.workbench import Workbench

    raw = checked_bytes(path, FAO56_SHA256)
    published = json.loads(raw)
    inputs = published["inputs"]
    # These additional reported inputs are from the same official Example 18,
    # verified at its source on 2026-10-05. They are not inferred from the desired
    # answer or manufactured soil/pump/calibration fields.
    supplement = {"source": "https://www.fao.org/4/x0490e/x0490e08.htm", "example": 18,
        "temperature_min_degC": 12.3, "temperature_max_degC": 21.5, "pressure_kpa": 100.1,
        "reported_saturation_vapour_pressure_kpa": 1.997, "reported_actual_vapour_pressure_kpa": 1.409,
        "verified_at": "2026-10-05", "kind": "reported_rounded_numerical_inputs"}
    weather = {"temp_mean_c": inputs["mean_temperature_degC"], "pressure_kpa": supplement["pressure_kpa"],
        "wind_2m_m_s": inputs["wind_at_2m_m_s"], "net_radiation_mj_m2_day": inputs["net_radiation_MJ_m2_day"],
        "soil_heat_flux_mj_m2_day": inputs["soil_heat_flux_MJ_m2_day"],
        "saturation_vapour_pressure_kpa": supplement["reported_saturation_vapour_pressure_kpa"],
        "actual_vapour_pressure_kpa": supplement["reported_actual_vapour_pressure_kpa"]}
    actual = irrigation_solver.reference_et0(weather)
    tolerance = published["comparison_absolute_allowance_mm_day"]
    if abs(actual - published["expected_ETo_mm_day"]) > tolerance:
        raise ValueError("Native ET0 endpoint differs from the published rounded reference")
    # Separate matched-input algorithm check. The production formula recomputes
    # slope and psychrometric constant; the published rounded values remain data.
    exact_weather = {**weather, "saturation_vapour_pressure_kpa": sum(
        0.6108 * math.exp(17.27 * t / (t + 237.3)) for t in (12.3, 21.5)) / 2}
    exact = irrigation_solver.reference_et0(exact_weather)
    independent = irrigation_reference.fao56_daily_et0(12.3, 21.5, weather["pressure_kpa"],
        weather["wind_2m_m_s"], weather["net_radiation_mj_m2_day"], weather["soil_heat_flux_mj_m2_day"],
        weather["actual_vapour_pressure_kpa"])
    if abs(exact - independent) > 1e-12:
        raise ValueError("Existing independent FAO evaluator disagrees on matched inputs")
    destination.mkdir(parents=True, exist_ok=False)
    workbench = Workbench()
    source_ids = []
    for label, payload in [("published-example18", raw), ("official-example18-additional-inputs", json.dumps(supplement).encode())]:
        native = artifact(payload, media_type="application/json", producer="FAO56-reference-qualification",
                          experiment_id="FAO56-example18", metadata={"payload_encoding": "base64",
                          "payload_b64": base64.b64encode(payload).decode()})
        validate_artifact(native, payload)
        envelope = retain_record(native, dataset_id=label, revision=1, parent_source_id=None,
                                 recorded_at=stamp, data_family="artifact")
        source_ids.append(workbench.add_source({"kind": "data-record", "label": label,
            "bytes_b64": base64.b64encode(json.dumps(envelope).encode()).decode()})["source_id"])
    if Workbench.restore(workbench.serialize()).serialize() != workbench.serialize():
        raise ValueError("FAO source artifacts did not restore unchanged")
    result = {"dataset": "fao56_worked/example18", "status": "BOUNDED_EXISTING_ENDPOINT_PASS",
        "consumer": "ciw.irrigation_solver.reference_et0", "independent_consumer": "ciw.irrigation_reference.fao56_daily_et0",
        "native_endpoint_evaluations": 3, "session_executions": 0, "weather": weather, "source_ids": source_ids,
        "value": actual, "unit": "mm/day", "published_reference": published["expected_ETo_mm_day"],
        "published_absolute_discrepancy": abs(actual - published["expected_ETo_mm_day"]),
        "allowance": tolerance, "allowance_kind": published["allowance_kind"],
        "matched_input_discrepancy": abs(exact - independent), "matched_input_tolerance": 1e-12,
        "reported_deficit_kpa": inputs["vapour_pressure_deficit_kPa"],
        "actual_reported_es_minus_ea_kpa": weather["saturation_vapour_pressure_kpa"] - weather["actual_vapour_pressure_kpa"],
        "source_rounding_discrepancy": "retained explicitly, not repaired or fitted",
        "runtime_source_sha256": {module.__name__: sha256(Path(module.__file__).read_bytes()).hexdigest()
                                  for module in (irrigation_solver, irrigation_reference)},
        "scope": "daily ET0 endpoint only; no invented calendar year, soil state, pump or calibrated field sensors",
        "physical_validation": "not_performed", "uncertainty": "absent", "hardware_actuation": "not_performed"}
    (destination / "workbench.json").write_text(json.dumps(workbench.serialize(), indent=2) + "\n")
    (destination / "source.json").write_bytes(raw)
    (destination / "supplement.json").write_text(json.dumps(supplement, indent=2) + "\n")
    (destination / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition-root", type=Path, required=True)
    parser.add_argument("--tclab-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dsp-library", type=Path)
    parser.add_argument("--dsp-library-sha256")
    parser.add_argument("--fao56-reference", type=Path)
    args = parser.parse_args(argv)
    if (args.dsp_library is None) != (args.dsp_library_sha256 is None):
        parser.error("DSP qualification requires both the library and its exact SHA256")
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
    if args.dsp_library:
        report["tclab_fir"] = run_tclab_fir(args.tclab_csv, args.dsp_library, args.dsp_library_sha256,
                                           args.output_dir / "tclab-fir", stamp)
        report["native_executions"] += report["tclab_fir"]["native_executions"]
    if args.fao56_reference:
        report["fao56"] = run_fao56(args.fao56_reference, args.output_dir / "fao56", stamp)
    (args.output_dir / "qualification.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: report[key] for key in ("native_executions", "reference_scalar_values", "acquisition_files_hash_verified")}))


if __name__ == "__main__":
    main()
