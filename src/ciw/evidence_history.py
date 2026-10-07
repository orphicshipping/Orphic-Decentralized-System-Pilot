"""Read-only rule checks over existing Link journals and control checkpoints.

These projections are not another event store and do not resume work. Journal
ordinals and caller-ordered checkpoint snapshots are declared order, not an
authenticated physical or administrator history.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from . import link_contract as c
from .agent_api import encode
from .agent_recovery import MAX_CHECKPOINT
from .calibration_inspection import _read
from .link_protocol import decode_one
from .link_store import Journal, MAX_STORE_BYTES
from .operations.runner import check_seal
from .session import loads_json

RULES = "ncp.retained-history-rules.v1"
MAX_HISTORY_BYTES = 32 * 1024 * 1024


def _rule(name, status, basis, ids=()):
    return {"rule": name, "status": status, "basis": basis,
            "counterexample_event_ids": list(ids)}


def _report(kind, sources, rules):
    return {"schema": "ncp.evidence-history-inspection.v1", "rule_version": RULES,
            "kind": kind, "sources": sources, "rules": rules,
            "evidence_consistency": "FAIL" if any(r["status"] == "VIOLATED" for r in rules) else "NO_VIOLATION_FOUND",
            "author_authentication": "not_established", "physical_qualification": "not_established",
            "authorizes_execution": False, "automatic_replay": False}


def inspect_link_journal(path, *, claim_complete_stream=False):
    """Check a closed retained journal without touching its original SQLite files.

    All bytes, indexes and manifest bindings are first checked by Journal's own
    read-only inspector. Equal ticks across channels are allowed; rollback within
    one device/boot is not. A missing reset event is never invented.
    """
    if type(claim_complete_stream) is not bool:
        raise ValueError("Complete-stream assertion must be a boolean")
    raw = _read(path, MAX_STORE_BYTES * 2)
    sources = [{"sha256": c.bytes_ref(raw), "byte_count": len(raw)}]
    try:
        with Journal(path, read_only=True) as journal:
            rows = journal.rows()
        if _read(path, MAX_STORE_BYTES * 2) != raw:
            raise ValueError("Journal changed during history inspection")
    except ValueError as exc:
        return _report("link_journal", sources, [_rule("retained_wire_and_manifest_binding", "VIOLATED", str(exc))])
    rules = [_rule("configuration_binding_before_interpretation", "CONSISTENT",
                   "Original prior manifest, configuration references and bytes validated by the existing Journal reader")]
    ticks, sequences, reset, boots = {}, {}, {}, set()
    rollback, reset_reuse, gap, overflow, observations = [], [], [], [], []
    origins, missing_ticks = set(), []
    for row in rows:
        frame = decode_one(row["wire"], allow_unverified=True)
        value = c.loads(frame.payload, 65536)
        event_id = row["message_key"]
        if frame.kind == "manifest":
            continue
        domain = (value["device_id"], value["boot_id"])
        boots.add(domain)
        if frame.kind == "event":
            if value["event"] == "clock_reset":
                reset[domain] = event_id
            if value["event"] == "overflow":
                overflow.append(event_id)
            continue
        observations.append(event_id)
        origins.add(value["origin"])
        if domain in reset:
            reset_reuse.extend((reset[domain], event_id))
        tick = value["device_tick"]
        if tick is None:
            missing_ticks.append(event_id)
        elif domain in ticks and tick < ticks[domain][0]:
            rollback.extend((ticks[domain][1], event_id))
        if tick is not None:
            ticks[domain] = tick, event_id
        sequence = value["sequence"]
        if domain in sequences:
            previous, previous_id = sequences[domain]
            if sequence <= previous:
                rules.append(_rule("observation_sequence_order", "VIOLATED",
                                   "Observation sequence did not increase in retained journal order", (previous_id, event_id)))
            elif sequence != previous + 1:
                gap.extend((previous_id, event_id))
        elif sequence != 0:
            gap.append(event_id)
        sequences[domain] = sequence, event_id
    rules.extend([
        _rule("tick_rollback_requires_new_boot", "VIOLATED" if rollback else "UNKNOWN" if missing_ticks or not observations else "CONSISTENT",
              "Compare retained observation ticks within the same device/boot; absent ticks cannot establish order", sorted(set(rollback))),
        _rule("clock_reset_requires_new_boot", "VIOLATED" if reset_reuse else "CONSISTENT" if reset else "UNKNOWN",
              "A retained clock_reset must precede a different boot before another observation; absence of a reset record does not prove absence of a physical reboot",
              sorted(set(reset_reuse))),
        _rule("complete_stream_after_overflow_or_gap", "VIOLATED" if claim_complete_stream and (overflow or gap) else "UNKNOWN",
              "Explicit overflow/sequence gaps contradict a complete-stream assertion; their absence does not prove physical completeness",
              sorted(set(overflow + gap)) if claim_complete_stream else []),
        _rule("revoked_grant_prevents_dispatch", "UNKNOWN", "This journal has no execution-grant/revocation/dispatch history"),
        _rule("interrupted_work_is_not_retried", "UNKNOWN", "This acquisition journal has no job transitions"),
    ])
    report = _report("link_journal", sources, rules)
    report["observed"] = {"message_count": len(rows), "observation_count": len(observations),
        "device_boot_domains": len(boots), "origins": sorted(origins), "overflow_event_ids": overflow,
        "sequence_gap_event_ids": sorted(set(gap)), "missing_tick_observation_ids": missing_ticks,
        "complete_stream_assertion": claim_complete_stream}
    return report


def _checkpoint(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_CHECKPOINT:
        raise ValueError("Require bounded checkpoint bytes")
    sealed = loads_json(raw.decode("utf-8"))
    c.keys(sealed, {"state", "sha256"})
    state = sealed["state"]
    payload = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if sha256(payload).hexdigest() != sealed["sha256"]:
        raise ValueError("Checkpoint content digest mismatch")
    c.keys(state, {"schema", "artifacts", "policy", "plans", "jobs"})
    if (state["schema"] != "ncp.control-state.v1" or type(state["plans"]) is not dict
            or type(state["jobs"]) is not dict or len(state["plans"]) > 64 or len(state["jobs"]) > 1024):
        raise ValueError("Unsupported control checkpoint shape")
    return state


def inspect_control_checkpoints(snapshots):
    """Inspect caller-ordered copies of existing control checkpoint bytes.

    Snapshot order is explicitly a caller assertion. A single current checkpoint
    supports static consistency checks only. These checks do not replace owner
    recovery, source/plan recompilation or execution-grant enforcement.
    """
    if type(snapshots) is not list or not 1 <= len(snapshots) <= 64:
        raise ValueError("Require 1..64 ordered checkpoint byte snapshots")
    if any(type(raw) is not bytes or not 0 < len(raw) <= MAX_CHECKPOINT for raw in snapshots):
        raise ValueError("Require bounded checkpoint bytes")
    if sum(map(len, snapshots)) > MAX_HISTORY_BYTES:
        raise ValueError("Checkpoint history exceeds 32 MiB total input budget")
    sources, rules, states = [], [], []
    for index, raw in enumerate(snapshots):
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_CHECKPOINT:
            raise ValueError("Require bounded checkpoint bytes")
        sources.append({"snapshot_index": index, "sha256": c.bytes_ref(raw), "byte_count": len(raw)})
        try:
            states.append(_checkpoint(raw))
        except (ValueError, UnicodeError, RecursionError) as exc:
            rules.append(_rule("checkpoint_byte_integrity", "VIOLATED", str(exc)[:512], ("snapshot:" + str(index),)))
            return _report("control_checkpoints", sources, rules)
    rules.append(_rule("checkpoint_byte_integrity", "CONSISTENT", "Canonical checkpoint digest; not author authentication"))
    seen_attempts, seen_engines, previous_jobs = {}, {}, {}
    admission_bad, identity_bad, terminal_bad, outcome_bad, plan_bad = [], [], [], [], []
    unfinished, unknown = [], []
    for index, state in enumerate(states):
        invalid_plans = set()
        for plan_id, plan in state["plans"].items():
            try:
                c.keys(plan, {"schema", "source", "baseline", "graph", "compilation", "host_policy_sha256",
                              "plan_id", "digest", "admitted", "uri"})
                body = {key: value for key, value in plan.items() if key not in {"plan_id", "digest", "admitted", "uri"}}
                expected_digest = "sha256:" + sha256(encode(body)).hexdigest()
                if (plan["schema"] != "ncp.control-plan.v1" or plan["digest"] != expected_digest
                        or plan["plan_id"] != plan_id or plan_id != "p-" + expected_digest[7:]
                        or plan["uri"] != "ncp://plans/" + plan_id or type(plan["admitted"]) is not bool
                        or plan["host_policy_sha256"] != state["policy"]):
                    raise ValueError("Retained plan digest, identity or policy mismatch")
                check_seal(plan["compilation"])
                check_seal(plan["compilation"]["experiment"])
            except (ValueError, TypeError, KeyError, AttributeError):
                invalid_plans.add(plan_id)
                plan_bad.append("snapshot:" + str(index) + "/" + str(plan_id))
        current_jobs = {}
        for run_id, job in state["jobs"].items():
            eid = "snapshot:" + str(index) + "/" + str(run_id)
            try:
                c.keys(job, {"run_id", "plan_id", "plan_digest", "attempt", "engine_attempt", "state", "cancel_requested", "result", "uri"})
                for name in ("run_id", "plan_id", "attempt", "engine_attempt"):
                    c.text(job[name])
                c.content_ref(job["plan_digest"])
                if (run_id != job["run_id"] or job["uri"] != "ncp://runs/" + run_id
                        or type(job["cancel_requested"]) is not bool
                        or job["state"] not in {"queued", "running", "completed", "failed", "unknown", "cancelled"}):
                    raise ValueError("Invalid retained job identity/state")
                plan = state["plans"].get(job["plan_id"])
                if (job["plan_id"] in invalid_plans or type(plan) is not dict or plan.get("admitted") is not True
                        or plan.get("digest") != job["plan_digest"] or plan.get("plan_id") != job["plan_id"]):
                    admission_bad.append(eid)
                identity = (run_id, job["plan_id"], job["plan_digest"], job["engine_attempt"])
                if job["attempt"] in seen_attempts and seen_attempts[job["attempt"]] != identity:
                    identity_bad.append(eid)
                seen_attempts[job["attempt"]] = identity
                if job["engine_attempt"] in seen_engines and seen_engines[job["engine_attempt"]] != run_id:
                    identity_bad.append(eid)
                seen_engines[job["engine_attempt"]] = run_id
                prior = previous_jobs.get(run_id)
                if prior is not None:
                    if any(prior[field] != job[field] for field in ("attempt", "engine_attempt", "plan_id", "plan_digest")):
                        identity_bad.append(eid)
                    if prior["state"] in {"completed", "failed", "cancelled", "unknown"} and prior != job:
                        terminal_bad.append(eid)
                status, result = job["state"], job["result"]
                if status in {"queued", "running"}:
                    unfinished.append(eid)
                    if result is not None:
                        outcome_bad.append(eid)
                elif status == "cancelled":
                    if result is not None or not job["cancel_requested"]:
                        outcome_bad.append(eid)
                elif status in {"completed", "failed"}:
                    expected = "unknown" if result is None or result.get("execution_outcome") == "unknown" else "completed" if result.get("status") == "completed" else "failed"
                    if expected != status:
                        outcome_bad.append(eid)
                else:
                    unknown.append(eid)
                current_jobs[run_id] = job
            except (ValueError, TypeError, KeyError, AttributeError):
                identity_bad.append(eid)
        for run_id in previous_jobs.keys() - current_jobs.keys():
            identity_bad.append("snapshot:" + str(index) + "/" + run_id)
        previous_jobs = current_jobs
    rules.extend([
        _rule("retained_plan_content_integrity", "VIOLATED" if plan_bad else "CONSISTENT",
              "Recomputed plan digest/identity/policy and nested compilation/experiment seals; owner recompilation and source revalidation not performed", plan_bad),
        _rule("plan_admission_before_job", "VIOLATED" if admission_bad else "CONSISTENT",
              "Every job references an admitted plan with the exact retained digest", admission_bad),
        _rule("attempt_identity_is_stable", "VIOLATED" if identity_bad else "CONSISTENT",
              "Attempt, engine attempt and run identity are unique and stable across supplied snapshots", sorted(set(identity_bad))),
        _rule("terminal_or_unknown_work_is_not_retried", "VIOLATED" if terminal_bad else "CONSISTENT" if len(states) > 1 else "UNKNOWN",
              "Terminal and unknown outcomes are immutable across caller-ordered snapshots; one snapshot cannot show a transition", terminal_bad),
        _rule("outcome_consistency", "VIOLATED" if outcome_bad else "CONSISTENT",
              "Unfinished/cancelled jobs cannot claim results; terminal status must match retained response", outcome_bad),
        _rule("interrupted_work_is_unknown", "UNKNOWN" if unfinished else "CONSISTENT",
              "Queued/running snapshots cannot establish completion or an actual interruption; resume belongs to the original owner", unfinished),
        _rule("revoked_grant_prevents_dispatch", "UNKNOWN", "Control snapshots do not retain a complete ordered grant revocation and dispatch history"),
    ])
    report = _report("control_checkpoints", sources, rules)
    report["observed"] = {"snapshot_order": "caller_declared", "snapshot_count": len(states),
        "unfinished_occurrences": unfinished, "unknown_occurrences": unknown}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="kind", required=True)
    link = sub.add_parser("link-journal")
    link.add_argument("path")
    link.add_argument("--claim-complete-stream", action="store_true")
    control = sub.add_parser("control-checkpoints")
    control.add_argument("paths", nargs="+")
    for command in (link, control):
        command.add_argument("--output", required=True, help="New JSON inspection file")
    args = parser.parse_args(argv)
    if args.kind == "link-journal":
        report = inspect_link_journal(args.path, claim_complete_stream=args.claim_complete_stream)
    else:
        if not 1 <= len(args.paths) <= 64:
            raise ValueError("Require 1..64 checkpoint paths")
        snapshots, used = [], 0
        for path in args.paths:
            raw = _read(path, min(MAX_CHECKPOINT, MAX_HISTORY_BYTES - used))
            snapshots.append(raw)
            used += len(raw)
        report = inspect_control_checkpoints(snapshots)
    with Path(args.output).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=True)
        stream.write("\n")
    print(json.dumps(report, ensure_ascii=True))
    return 2 if report["evidence_consistency"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
