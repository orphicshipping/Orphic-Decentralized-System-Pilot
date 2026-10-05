"""Bounded append-only QC capture journal, with adapters for both NET QC profiles.

This is retained-data ingestion, not a sensor driver or hard real-time controller.
A process hold never authorizes a machine action or establishes a defective part.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext

from .core.identities import new_identity, validate_identity
from .manufacturing_contracts import bounded, content_identity, count, items, keys, number, ref, text

SPEC_SCHEMA = "ciw.toolpoint-stream-spec.v1"
UPDATE_SCHEMA = "ciw.toolpoint-stream-update.v1"
REPORT_SCHEMA = "ciw.toolpoint-stream-report.v1"
OPERATION = "polymer.toolpoint-stream.ingest.v1"
LEGACY = "ciw.toolpoint-control-plan.v1"
NATIVE = "ciw.toolpoint-monitoring-plan.v1"
AUTHORITY = {
    "hardware_actuation": "not_performed", "physical_containment": "not_performed",
    "physical_qualification": "not_established", "state_admission": "not_performed",
    "source_authentication": "not_established", "shipment_release": "not_authorized",
    "action_authorized": False,
}


def _d(value):
    return Decimal(str(value))


def _profile(plan):
    if type(plan) is not dict:
        raise ValueError("Require an explicit QC plan object")
    if plan.get("schema") == LEGACY:
        from .toolpoint_quality import screen, validate_plan
        return validate_plan, screen, "streams", "observed_until_s", "samples_ref"
    if plan.get("schema") == NATIVE:
        from .polymer_toolpoint_qc import assess, validate_plan
        return validate_plan, assess, "signals", "completed_at_s", "source_ref"
    raise ValueError("Unsupported QC plan profile; no dynamic providers")


def _start(plan, observation):
    return observation["cycle_start_s" if plan["schema"] == LEGACY else "started_at_s"]


def _through(plan, observation):
    if plan["schema"] == LEGACY:
        return _d(observation["observed_until_s"])
    return _d(observation["completed_at_s"]) - _d(observation["started_at_s"])


def _rules(plan):
    if plan["schema"] == LEGACY:
        return [{"rule_id": r["rule_id"], "sensor_id": r["sensor_id"], "kind": r["kind"],
                 "cavity_id": r["cavity_id"], "start_s": r["start_s"], "end_s": r["end_s"],
                 "max_skew_s": r["max_time_uncertainty_s"], "limits": r} for r in plan["rules"]]
    sensors = {s["sensor_id"]: s for s in plan["sensors"]}
    phases = {p["phase_id"]: p for p in plan["phases"]}
    return [{"rule_id": r["rule_id"], "sensor_id": r["sensor_id"], "kind": r["feature"],
             "cavity_id": sensors[r["sensor_id"]]["location"]["cavity_id"],
             "start_s": phases[r["phase_id"]]["start_s"], "end_s": phases[r["phase_id"]]["end_s"],
             "max_skew_s": plan["max_clock_skew_s"], "limits": r["limits"]} for r in plan["rules"]]


def validate_spec(spec):
    bounded(spec)
    keys(spec, {"schema", "plan", "binding", "limits"})
    if spec["schema"] != SPEC_SCHEMA:
        raise ValueError("Wrong stream specification schema")
    validate, _, _, _, _ = _profile(spec["plan"])
    validate(spec["plan"])
    binding = spec["binding"]
    keys(binding, {"cycle_id", "cycle_ref", "source_kind", "started_at_s"})
    text(binding["cycle_id"])
    ref(binding["cycle_ref"])
    number(binding["started_at_s"])
    if binding["source_kind"] not in {"synthetic", "retained_observation"}:
        raise ValueError("Declare stream source semantics")
    if spec["plan"]["schema"] == NATIVE and binding["source_kind"] != spec["plan"]["source_kind"]:
        raise ValueError("Stream semantics differ from the plan")
    limits = spec["limits"]
    keys(limits, {"max_updates", "max_samples_per_sensor", "max_total_samples"})
    count(limits["max_updates"], maximum=64)
    count(limits["max_samples_per_sensor"], maximum=512 if spec["plan"]["schema"] == LEGACY else 2048)
    count(limits["max_total_samples"], maximum=32768)


def make_spec(plan, observation, *, max_updates=32):
    """Bind a stream to one exact plan and cycle; not a trust or execute grant."""
    cycle_field = "cycle_event_ref" if plan["schema"] == LEGACY else "cycle_evidence_ref"
    spec = {"schema": SPEC_SCHEMA, "plan": deepcopy(plan), "binding": {
        "cycle_id": observation["cycle_id"], "cycle_ref": observation[cycle_field],
        "source_kind": observation["source_kind"], "started_at_s": _start(plan, observation)},
        "limits": {"max_updates": max_updates, "max_samples_per_sensor": 512 if plan["schema"] == LEGACY else 2048, "max_total_samples": 32768}}
    validate_spec(spec)
    return spec


def _point_witnesses(plan, observation):
    """Definite sampled violations survive unrelated invalid/missing samples.

Only sample-band and high-peak witnesses are supported here. An isolated low
sample cannot establish a low peak, a missing threshold, or an integral error.
The legacy/native evaluators remain owners of their other feature semantics.
"""
    legacy = plan["schema"] == LEGACY
    channels = {s["sensor_id"]: s for s in observation["streams" if legacy else "signals"]}
    found = []
    start = _d(_start(plan, observation))
    for rule in _rules(plan):
        if rule["kind"] not in {"sample_band", "peak_band", "peak"}:
            continue
        channel = channels.get(rule["sensor_id"])
        if channel is None or channel["health"] != "good":
            continue
        if legacy:
            cal = channel["calibration"]
            time_u = channel["time_uncertainty_s"]
            if cal is None or channel["source_ref"] is None or channel["clock_alignment_ref"] is None:
                continue
            cal_from, cal_until = cal["valid_from_s"], cal["valid_until_s"]
        else:
            time_u = channel["clock_skew_s"]
            cal_from, cal_until = channel["calibration_valid_from_s"], channel["calibration_valid_until_s"]
            if any(channel[k] is None for k in ("calibration_ref", "clock_alignment_ref")):
                continue
        if time_u is None or cal_from is None or cal_until is None or _d(time_u) > _d(rule["max_skew_s"]):
            continue
        tu = _d(time_u)
        a, b = _d(rule["start_s"]), _d(rule["end_s"])
        low, high = _d(rule["limits"]["lower"]), _d(rule["limits"]["upper"])
        for index, sample in enumerate(channel["samples"]):
            stamp = _d(sample["time_s"])
            if sample["expanded_uncertainty"] is None or not a <= stamp-tu <= stamp+tu <= b:
                continue
            if not _d(cal_from) <= start+stamp-tu <= start+stamp+tu <= _d(cal_until):
                continue
            if not legacy and not _d(plan["valid_from_s"]) <= start+stamp-tu <= start+stamp+tu <= _d(plan["valid_until_s"]):
                continue
            lo = _d(sample["value"]) - _d(sample["expanded_uncertainty"])
            hi = _d(sample["value"]) + _d(sample["expanded_uncertainty"])
            if lo > high or rule["kind"] == "sample_band" and hi < low:
                found.append({"rule_id": rule["rule_id"], "sensor_id": rule["sensor_id"],
                    "cavity_id": rule["cavity_id"], "kind": "VALID_SAMPLED_LIMIT_VIOLATION",
                    "sample_index": index, "sample_ref": content_identity(sample),
                    "sample_time_s_decimal": str(stamp), "lower_decimal": str(lo), "upper_decimal": str(hi)})
                break
    return found


def _metadata(channel, dynamic_hash):
    return {k: v for k, v in channel.items() if k not in {"samples", dynamic_hash, "health"}}


def _check_continuation(spec, previous, update):
    plan, binding = spec["plan"], spec["binding"]
    observation = update["observation"]
    _, _, channels_key, through_key, dynamic_hash = _profile(plan)
    cycle_key = "cycle_event_ref" if plan["schema"] == LEGACY else "cycle_evidence_ref"
    if (observation["cycle_id"] != binding["cycle_id"] or observation[cycle_key] != binding["cycle_ref"]
            or observation["source_kind"] != binding["source_kind"]
            or content_identity(_start(plan, observation)) != content_identity(binding["started_at_s"])):
        raise ValueError("Observation differs from the bound cycle, source or start time")
    if plan["schema"] == LEGACY and observation["complete"] is not update["final"]:
        raise ValueError("Capture and stream finalization disagree")
    through = _through(plan, observation)
    if _d(update["received_at_s"]) < _d(binding["started_at_s"]) + through:
        raise ValueError("Receipt timestamp precedes capture watermark")
    channels = observation[channels_key]
    total = sum(len(s["samples"]) for s in channels)
    if total > spec["limits"]["max_total_samples"] or any(len(s["samples"]) > spec["limits"]["max_samples_per_sensor"] for s in channels):
        raise ValueError("Stream sample budget exceeded")
    if previous is None:
        return
    prior = previous["observation"]
    prior_through = _through(plan, prior)
    if through < prior_through or _d(update["received_at_s"]) < _d(previous["received_at_s"]):
        raise ValueError("Stream capture or receipt clock moved backwards")
    excluded = {channels_key, through_key, "complete"} if plan["schema"] == LEGACY else {channels_key, through_key}
    if content_identity({k: v for k, v in observation.items() if k not in excluded}) != content_identity({k: v for k, v in prior.items() if k not in excluded}):
        raise ValueError("Capture identity, mapping or metadata changed within a stream")
    old_channels = {s["sensor_id"]: s for s in prior[channels_key]}
    new_channels = {s["sensor_id"]: s for s in channels}
    if not set(old_channels).issubset(new_channels):
        raise ValueError("Previously observed sensor disappeared")
    for sid, channel in new_channels.items():
        old = old_channels.get(sid)
        n = 0 if old is None else len(old["samples"])
        if old is not None:
            if content_identity(_metadata(old, dynamic_hash)) != content_identity(_metadata(channel, dynamic_hash)):
                raise ValueError("Sensor provenance/calibration/clock changed within a stream")
            if len(channel["samples"]) < n or content_identity(channel["samples"][:n]) != content_identity(old["samples"]):
                raise ValueError("Retained samples were removed or rewritten")
        if any(_d(s["time_s"]) <= prior_through for s in channel["samples"][n:]):
            raise ValueError("Late sample backfills an acknowledged capture watermark")
    # Equal watermarks may add health evidence or finalize, but may not duplicate
    # an otherwise identical observation as a new event.
    if not update["final"] and content_identity(observation) == content_identity(prior):
        raise ValueError("No-op update must retry its existing sequence instead")


class CaptureJournal:
    """Single-writer, in-memory append-only session; exported reports are replayable.

All caller objects and returned objects are detached. A refused append leaves the
entire state unchanged. No cross-process transaction or durable device IO is implied.
"""
    def __init__(self, spec):
        validate_spec(spec)
        self._spec = deepcopy(spec)
        self._updates = []
        self._receipts = []
        self._result = None
        self._execution_id = new_identity("execution")

    @property
    def head_ref(self):
        return self._receipts[-1]["receipt_ref"] if self._receipts else None

    @property
    def finalized(self):
        return bool(self._updates and self._updates[-1]["final"])

    def update(self, observation, *, received_at_s, final=False):
        """Construct the next detached update. Call append() to accept it."""
        return {"schema": UPDATE_SCHEMA, "sequence": len(self._updates),
                "previous_receipt_ref": self.head_ref, "received_at_s": received_at_s,
                "final": final, "observation": deepcopy(observation)}

    def append(self, update):
        bounded(update)
        keys(update, {"schema", "sequence", "previous_receipt_ref", "received_at_s", "final", "observation"})
        if update["schema"] != UPDATE_SCHEMA:
            raise ValueError("Wrong stream update schema")
        count(update["sequence"], minimum=0, maximum=63)
        number(update["received_at_s"])
        if type(update["final"]) is not bool:
            raise ValueError("Explicit final boolean required")
        if update["previous_receipt_ref"] is not None:
            ref(update["previous_receipt_ref"])
        sequence = update["sequence"]
        if sequence < len(self._updates):
            if content_identity(update) != content_identity(self._updates[sequence]):
                raise ValueError("Conflicting duplicate sequence")
            return deepcopy(self._receipts[sequence])
        if self.finalized:
            raise ValueError("Finalized capture is sealed")
        if sequence != len(self._updates) or update["previous_receipt_ref"] != self.head_ref:
            raise ValueError("Out-of-order update or stale parent receipt")
        if len(self._updates) >= self._spec["limits"]["max_updates"]:
            raise ValueError("Stream update budget exceeded")
        plan = self._spec["plan"]
        # The adapter validates exact schemas, retained hashes and sensor meanings.
        _, assess, _, _, _ = _profile(plan)
        assessment = assess(plan, update["observation"])
        with localcontext() as context:
            context.prec = 512
            _check_continuation(self._spec, self._updates[-1] if self._updates else None, update)
            witnesses = _point_witnesses(plan, update["observation"])
            result = self._derive(update, assessment, witnesses)
        receipt_body = {"schema": "ciw.toolpoint-stream-receipt.v1", "sequence": sequence,
            "previous_receipt_ref": self.head_ref, "spec_ref": content_identity(self._spec),
            "update_ref": content_identity(update), "assessment_ref": content_identity(assessment),
            "result_ref": content_identity(result), "status": result["status"]}
        receipt = {**receipt_body, "receipt_ref": content_identity(receipt_body)}
        next_updates, next_receipts = self._updates + [deepcopy(update)], self._receipts + [receipt]
        candidate = self._report(next_updates, next_receipts, result)
        bounded(candidate)  # Refuse before changing state when retained history exceeds budgets.
        self._updates, self._receipts, self._result = next_updates, next_receipts, result
        return deepcopy(receipt)

    def _derive(self, update, assessment, witnesses):
        plan = self._spec["plan"]
        legacy = plan["schema"] == LEGACY
        decisions = assessment["decisions" if legacy else "rules"]
        rules = {r["rule_id"]: r for r in _rules(plan)}
        through = _through(plan, update["observation"])
        latched = deepcopy(self._result["latched_findings"]) if self._result else []
        known = {(f["kind"], f["rule_id"]) for f in latched}

        def latch(kind, rule_id, detail):
            key = (kind, rule_id)
            if key not in known:
                body = {"kind": kind, "rule_id": rule_id, "cavity_id": rules[rule_id]["cavity_id"],
                        "first_sequence": update["sequence"], "first_received_at_s": update["received_at_s"],
                        "first_update_ref": content_identity(update), "detail": deepcopy(detail)}
                latched.append({**body, "finding_ref": content_identity(body)})
                known.add(key)

        for witness in witnesses:
            latch("SAMPLED_LIMIT_VIOLATION", witness["rule_id"], witness)
        for decision in decisions:
            if decision["status"] == "DEVIATION":
                latch("EVALUATOR_DEVIATION", decision["rule_id"], decision)
            elif decision["status"] not in {"NO_ALARM", "PROCESS_SIGNATURE_ACCEPTABLE"} and through >= _d(rules[decision["rule_id"]]["end_s"]):
                latch("COMPLETED_WINDOW_NOT_CLEARED", decision["rule_id"], decision)
        # A reported channel fault cannot be erased by a subsequent health=good snapshot.
        channels = update["observation"]["streams" if legacy else "signals"]
        for channel in channels:
            if channel["health"] != "good":
                for rule in rules.values():
                    if rule["sensor_id"] == channel["sensor_id"]:
                        latch("SENSOR_HEALTH_EVENT", rule["rule_id"], {"sensor_id": channel["sensor_id"], "health": channel["health"]})
        all_done = through >= max(_d(r["end_s"]) for r in rules.values())
        good = assessment["status"] in {"NO_ALARM", "PROCESS_SIGNATURE_ACCEPTABLE"}
        status = ("HELD" if latched else "PENDING" if not update["final"] else
                  "NO_PROCESS_HOLD_IDENTIFIED" if good and all_done else "HELD")
        relevant_cavities = sorted({r["cavity_id"] for r in rules.values() if r["cavity_id"] is not None})
        cavity_states = {}
        for cavity in relevant_cavities:
            applicable = [f for f in latched if f["cavity_id"] in (None, cavity)]
            relevant = [d for d in decisions if rules[d["rule_id"]]["cavity_id"] in (None, cavity)]
            cavity_good = all(d["status"] in {"NO_ALARM", "PROCESS_SIGNATURE_ACCEPTABLE"} for d in relevant)
            cavity_states[cavity] = {"state": "HELD" if applicable else "PENDING" if not update["final"] else
                "NO_PROCESS_HOLD_IDENTIFIED" if cavity_good and all_done else "HELD",
                "finding_refs": [f["finding_ref"] for f in applicable]}
        # Mapping uncertainty is cohort-wide; do not expose a clean cavity as a release view.
        if not legacy and assessment["mapping_status"] != "RESOLVED_DECLARATION":
            for state in cavity_states.values():
                state["state"] = "HELD"
            status = "HELD"
        latency = _d(update["received_at_s"]) - (_d(self._spec["binding"]["started_at_s"]) + through)
        return {"schema": "ciw.toolpoint-stream-result.v1", "status": status, "finalized": update["final"],
            "cycle_id": self._spec["binding"]["cycle_id"], "source_kind": self._spec["binding"]["source_kind"],
            "captured_through_s_decimal": str(through), "declared_capture_to_receipt_s_decimal": str(latency),
            "latest_assessment": deepcopy(assessment), "latched_findings": latched, "cavities": cavity_states,
            "scope": "append-only retained prefixes; supplied limits; no measured hardware latency or product release",
            "authority": dict(AUTHORITY)}

    def _report(self, updates, receipts, result):
        return {"schema": REPORT_SCHEMA, "operation_ref": content_identity({"operation": OPERATION}),
            "execution_id": self._execution_id, "spec": deepcopy(self._spec), "spec_ref": content_identity(self._spec),
            "updates": deepcopy(updates), "receipts": deepcopy(receipts),
            "result": deepcopy(result), "result_ref": content_identity(result)}

    def report(self):
        if not self._updates:
            raise ValueError("Cannot export a stream without an accepted update")
        return self._report(self._updates, self._receipts, self._result)

    @classmethod
    def restore(cls, report, *, expected_head_ref=None, expected_spec_ref=None):
        """Recompute every accepted prefix before resuming; no trusted cached verdict."""
        _validate_report(report)
        if expected_head_ref is not None:
            ref(expected_head_ref)
            if report["receipts"][-1].get("receipt_ref") != expected_head_ref:
                raise ValueError("Checkpoint differs from the externally expected head")
        if expected_spec_ref is not None:
            ref(expected_spec_ref)
            if report["spec_ref"] != expected_spec_ref:
                raise ValueError("Checkpoint differs from the externally expected specification")
        journal = cls(report["spec"])
        for update in report["updates"]:
            journal.append(update)
        fresh = journal.report()
        for key in ("receipts", "result", "result_ref"):
            if content_identity(fresh[key]) != content_identity(report[key]):
                raise ValueError("Retained stream differs from full prefix replay")
        # A restoration is a new local execution, not the earlier occurrence.
        return journal


def _validate_report(report):
    bounded(report)
    keys(report, {"schema", "operation_ref", "execution_id", "spec", "spec_ref", "updates", "receipts", "result", "result_ref"})
    if report["schema"] != REPORT_SCHEMA or report["operation_ref"] != content_identity({"operation": OPERATION}):
        raise ValueError("Wrong stream report or operation")
    validate_identity(report["execution_id"], "execution")
    if report["spec_ref"] != content_identity(report["spec"]) or report["result_ref"] != content_identity(report["result"]):
        raise ValueError("Stream content identity mismatch")
    items(report["updates"], minimum=1, maximum=64)
    if len(items(report["receipts"], minimum=1, maximum=64)) != len(report["updates"]):
        raise ValueError("Stream receipt count mismatch")


def verify(report, *, expected_head_ref=None, expected_spec_ref=None):
    CaptureJournal.restore(report, expected_head_ref=expected_head_ref, expected_spec_ref=expected_spec_ref)
    return {"schema": "ciw.toolpoint-stream-verification.v1", "status": "PASS",
        "verification_id": new_identity("verification"), "execution_id": report["execution_id"],
        "operation_ref": report["operation_ref"], "spec_ref": report["spec_ref"], "result_ref": report["result_ref"],
        "head_ref": report["receipts"][-1]["receipt_ref"],
        "head_anchor_checked": expected_head_ref is not None, "spec_anchor_checked": expected_spec_ref is not None,
        "scope": "same-implementation full prefix replay; not authenticity or independent physical verification",
        "authority": dict(AUTHORITY)}


def final_observation(report):
    """Return the native final capture plus inseparable stream screening metadata.

Consumers must use this capsule's stream verdict AND their manufacturing/inspection
rules. Stripping the envelope and assessing only observation discards stream holds.
"""
    verify(report)
    if not report["result"]["finalized"]:
        raise ValueError("Only a finalized stream can supply final-cycle evidence")
    return {"schema": "ciw.toolpoint-final-capture.v1", "spec_ref": report["spec_ref"],
        "stream_result_ref": report["result_ref"], "stream_head_ref": report["receipts"][-1]["receipt_ref"],
        "status": report["result"]["status"], "cavities": deepcopy(report["result"]["cavities"]),
        "latched_findings": deepcopy(report["result"]["latched_findings"]),
        "observation": deepcopy(report["updates"][-1]["observation"]), "authority": dict(AUTHORITY)}
