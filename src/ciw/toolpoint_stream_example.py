"""Synthetic fixtures for both existing QC schemas; not process recommendations."""
from copy import deepcopy
from decimal import Decimal

from .manufacturing_contracts import content_identity as identity
from .manufacturing_example import example_request as manufacturing_example
from .toolpoint_stream import CaptureJournal, LEGACY, NATIVE, make_spec

CASES = ("nominal", "late-unknown", "health-recovery", "single-packet-fault", "high-nozzle", "sampling-gap")


def fixture(*, profile="native", case="late-unknown", cycle_index=0):
    if profile not in {"native", "legacy"} or case not in CASES:
        raise ValueError("Unknown fixture profile or case")
    source = manufacturing_example()
    event = [e for e in source["events"] if e["kind"] == "cycle"][cycle_index]
    config = source["configuration"]
    cavities = config["tool"]["cavity_ids"]
    definitions = [{"sensor_id": "sensor."+c, "cavity_id": c} for c in cavities]
    definitions.append({"sensor_id": "sensor.nozzle", "cavity_id": None})
    channels, rules, sensors = [], [], []
    for definition in definitions:
        sid, cavity = definition["sensor_id"], definition["cavity_id"]
        values = [100000, 1000000, 4000000, 8000000, 7000000]
        if cavity == "cavity.2" and case in {"late-unknown", "single-packet-fault", "sampling-gap"}:
            values[3] = 18000000
        if cavity is None and case == "high-nozzle":
            values[3] = 18000000
        samples = [{"time_s": t, "value": v, "expanded_uncertainty": 10000}
                   for t, v in zip([0, 0.2, 0.4, 0.6, 0.8], values)]
        if cavity == "cavity.2" and case in {"late-unknown", "single-packet-fault"}:
            samples[-1]["expanded_uncertainty"] = None
        if cavity == "cavity.2" and case == "sampling-gap":
            samples.pop(2)
        if profile == "legacy":
            common = {"sensor_id": sid, "position": "end_of_fill" if cavity else "nozzle", "cavity_id": cavity,
                "quantity": "pressure", "unit": "Pa", "start_s": 0, "end_s": 0.8, "max_sample_gap_s": 0.21,
                "max_time_uncertainty_s": 0.001, "minimum_samples": 5}
            rules.append({**common, "rule_id": sid+".pressure", "kind": "sample_band", "lower": 0, "upper": 15000000})
            channels.append({k: common[k] for k in ("sensor_id", "position", "cavity_id", "quantity", "unit")})
            channels[-1].update({"source_ref": identity({"synthetic_source": sid}),
                "calibration": {"record_ref": identity({"synthetic_cal": sid}), "valid_from_s": 0, "valid_until_s": 100},
                "clock_alignment_ref": identity({"synthetic_clock": True}), "time_uncertainty_s": 0,
                "health": "good", "samples": samples, "samples_ref": identity(samples)})
        else:
            definition = {"sensor_id": sid, "quantity": "cavity_pressure" if cavity else "melt_pressure", "unit": "Pa",
                "location": {"kind": "end_of_fill" if cavity else "nozzle", "cavity_id": cavity, "gate_id": None}}
            sensors.append(definition)
            rules.append({"rule_id": sid+".pressure", "sensor_id": sid, "phase_id": "fill", "feature": "peak",
                "limits": {"lower": 0, "upper": 15000000, "unit": "Pa"}, "threshold": None, "reference_curve": None})
            channels.append({**deepcopy(definition), "frame": "synthetic.tool-frame", "clock_id": source["clock_id"],
                "calibration_ref": identity({"synthetic_cal": sid}), "calibration_valid_from_s": 0,
                "calibration_valid_until_s": 100, "clock_alignment_ref": identity({"synthetic_clock": True}),
                "clock_skew_s": 0, "health": "good", "samples": samples})
            from .polymer_toolpoint_qc import signal_ref
            channels[-1]["source_ref"] = signal_ref(channels[-1])
    shared = {"plan_id": "synthetic.stream.plan", "revision": "A", "configuration_ref": identity(config),
              "part_ref": config["part_ref"], "clock_id": source["clock_id"], "rules": rules}
    start = event["at_s"]-1
    if profile == "legacy":
        plan = {**shared, "schema": LEGACY, "tool_ref": identity(config["tool"]),
                "frame_id": "synthetic.tool-frame", "basis_ref": identity({"synthetic_limits": True})}
        observation = {"schema": "ciw.toolpoint-acquisition.v1", "plan_ref": identity(plan),
            "configuration_ref": identity(config), "cycle_event_ref": identity(event), "cycle_id": event["data"]["cycle_id"],
            "source_kind": "synthetic", "clock_id": source["clock_id"], "frame_id": plan["frame_id"],
            "cycle_start_s": start, "observed_until_s": 0.8, "complete": True, "streams": channels}
    else:
        plan = {**shared, "schema": NATIVE, "source_kind": "synthetic", "process": "injection_molding",
            "frame": "synthetic.tool-frame", "valid_from_s": 0, "valid_until_s": 100,
            "max_clock_skew_s": 0.001, "max_sample_gap_s": 0.21, "cavity_ids": cavities,
            "reference_evidence_refs": [identity({"synthetic_limits": True})],
            "phases": [{"phase_id": "fill", "start_s": 0, "end_s": 0.8}], "sensors": sensors}
        observation = {"schema": "ciw.toolpoint-observation.v1", "source_kind": "synthetic",
            "cycle_id": event["data"]["cycle_id"], "cycle_evidence_ref": event["data"]["cycle_evidence_ref"],
            "part_ref": config["part_ref"], "configuration_ref": identity(config), "clock_id": source["clock_id"],
            "frame": plan["frame"], "started_at_s": start, "completed_at_s": start+0.8, "signals": channels,
            "part_mapping": {"status": "cavity_resolved", "evidence_ref": identity({"synthetic_mapping": True}),
                "items": [{"serial": p["serial"], "cavity_id": p["cavity_id"]} for p in event["data"]["outputs"]]}}
    return plan, observation


def prefix(plan, observation, through, *, final=False):
    result = deepcopy(observation)
    legacy = plan["schema"] == LEGACY
    if legacy:
        result["observed_until_s"], result["complete"] = through, final
    else:
        result["completed_at_s"] = float(Decimal(str(result["started_at_s"]))+Decimal(str(through)))
    for channel in result["streams" if legacy else "signals"]:
        channel["samples"] = [s for s in channel["samples"] if s["time_s"] <= through]
        if legacy:
            channel["samples_ref"] = identity(channel["samples"])
        else:
            from .polymer_toolpoint_qc import signal_ref
            channel["source_ref"] = signal_ref(channel)
    return result


def demo(*, profile="native", case="late-unknown", cycle_index=0):
    plan, observation = fixture(profile=profile, case=case, cycle_index=cycle_index)
    journal = CaptureJournal(make_spec(plan, observation))
    steps = [0.8] if case == "single-packet-fault" else [0.2, 0.6, 0.8]
    for index, through in enumerate(steps):
        final = index == len(steps)-1
        capture = prefix(plan, observation, through, final=final)
        if case == "health-recovery" and index == 0:
            capture["streams" if profile == "legacy" else "signals"][1]["health"] = "fault"
        start = observation["cycle_start_s" if profile == "legacy" else "started_at_s"]
        received = float(Decimal(str(start))+Decimal(str(through))+Decimal("0.01"))
        journal.append(journal.update(capture, received_at_s=received, final=final))
    return journal.report()
