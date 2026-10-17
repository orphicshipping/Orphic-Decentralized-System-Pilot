"""Local synthetic shared-task pilot. No external service or routine is called."""
import argparse
import json
from pathlib import Path
import time

from ciw.control_contracts import bytes_ref
from ciw.external_work import ExternalWorkStore
from ciw.external_work_mcp import ExternalWorkHost


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    store = ExternalWorkStore(args.output_dir / "external-work.sqlite3")
    parent = "synthetic-investigation:external-corpus-extraction"
    store.provision_client("fixture-requester", role="requester", parent_ref=parent)
    store.provision_client("fixture-worker", role="worker", parent_ref=parent)
    raw = b"Synthetic fixture: calibration declarations do not establish installed-chain qualification."
    (args.output_dir / "synthetic-source.txt").write_bytes(raw)
    context_id = store.retain_context(
        parent_ref=parent, snapshot_ref="synthetic-snapshot:v1", source_id="synthetic-source",
        version_ref="v1", source_bytes=raw, expected_source_hash=bytes_ref(raw), start=0, end=len(raw))
    requester = ExternalWorkHost(store, principal="fixture-requester")
    worker = ExternalWorkHost(ExternalWorkStore(store.path), principal="fixture-worker")
    proposed = requester.call("ncp_work_propose", {"request_key": "synthetic-pilot-1", "task": {
        "parent_ref": parent, "instruction": "Extract the source's stated qualification limitation.",
        "expected_output": "A candidate with an exact citation and limitations.", "context_ids": [context_id]}})
    assert proposed["status"] == "ok"
    task = proposed["data"]
    store.authorize(task["task_id"], expected_hash=task["task_hash"],
                    approval_ref="synthetic-fixture-grant:local-only", worker_id="fixture-worker",
                    expires_at=time.time() + 3600)
    wakeup = store.wake(task["task_id"], expected_hash=task["task_hash"], delivery_id="synthetic-delivery-1")
    discovered = worker.call("ncp_work_discover", {})
    assert discovered["data"]["items"][0]["task_id"] == task["task_id"]
    worker_context = worker.call("ncp_work_context", {"task_id": task["task_id"], "context_id": context_id})
    assert worker_context == requester.call("ncp_work_context", {"task_id": task["task_id"], "context_id": context_id})
    claimed = worker.call("ncp_work_claim", {"task_id": task["task_id"], "expected_hash": task["task_hash"],
                                           "attempt_id": "synthetic-attempt-1", "bot_label": "local fixture"})
    assert claimed["status"] == "ok"
    submission = worker.call("ncp_work_submit", {"task_id": task["task_id"], "expected_hash": task["task_hash"],
        "attempt_id": "synthetic-attempt-1", "result": {"outcome": "completed",
            "candidate": "Installed-chain qualification remains unestablished.",
            "citations": [{"context_id": context_id,
                           "quote": "calibration declarations do not establish installed-chain qualification"}],
            "limitations": ["Synthetic software fixture; no physical measurement or scientific validation."]}})
    assert submission["status"] == "ok"
    store.record_review(task["task_id"], result_id=submission["data"]["result_id"],
                        decision="needs_review", reviewer_ref="synthetic-fixture-check",
                        scope="software exchange only", findings="Exact citation retained; scientific acceptance not performed.")
    retained = requester.call("ncp_work_read", {"task_id": task["task_id"]})["data"]
    report = {"schema": "ciw.external-work-pilot-report.v1", "scope": "local synthetic software exchange",
              "wakeup": wakeup, "shared_context_equal": True,
              "task": retained, "external_calls": 0, "deployment": "not_performed"}
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"task_id": task["task_id"], "execution": retained["execution_status"],
                      "review": retained["review"]["decision"], "report": str(args.output_dir / "report.json")}))


if __name__ == "__main__":
    main()
