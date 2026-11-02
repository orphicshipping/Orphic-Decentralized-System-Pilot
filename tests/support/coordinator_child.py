"""Child process for the coordinator multi-process tests.

usage: coordinator_child.py MODE OWNER KEY [EXTRA]
Every mode prints one JSON line on stdout. The retain directory comes from
NCP_RETAIN_DIR. "hang" blocks inside the job after admission; "hold" takes an
exclusive resource and blocks. Both write a ready file first.
"""
import json
import os
import sys
import time
from pathlib import Path

from ciw import coordinator, services
from ciw.retention import RetainedFailure, Store
from ciw.sequence_kinetics import example_request


def payload(variant: str = "base") -> dict:
    if variant == "example":
        return {"domain": "sequence", "example": True}
    return {"domain": "sequence", "request": example_request()}


def ready(marker: str) -> None:
    Path(marker).write_text(str(os.getpid()), encoding="utf-8")


def main() -> None:
    mode, owner, key = sys.argv[1:4]
    extra = sys.argv[4] if len(sys.argv) > 4 else ""
    store = Store(owner)
    out = {"mode": mode, "pid": os.getpid()}
    if mode in {"submit", "submit-example"}:
        start = Path(extra) if extra else None
        while start is not None and not start.exists():
            time.sleep(0.005)
        before = coordinator._EXECUTIONS
        try:
            result = coordinator.submit(store, "ncp_domain_run", payload("example" if mode == "submit-example" else "base"), key)
            out.update(status=result["status"], replayed=result["replayed"], artifact_id=result.get("artifact_id"))
        except ValueError as exc:
            out.update(error=str(exc))
        out["executed_here"] = coordinator._EXECUTIONS - before
    elif mode in {"hang", "hang-cancel"}:
        def stuck(store=None, **_):
            ready(extra)
            if mode == "hang-cancel":
                raise KeyboardInterrupt
            while True:
                time.sleep(1)
        services.domain_run = stuck
        try:
            coordinator.submit(store, "ncp_domain_run", payload(), key)
        except KeyboardInterrupt:
            out.update(cancelled=True)
            # The process stays alive after a cancellation, so the record must
            # not depend on this PID dying.
            print(json.dumps(out), flush=True)
            time.sleep(float(os.environ.get("CHILD_LINGER", "0")))
            return
    elif mode == "hold":
        held = coordinator._hold_exclusive(store, key)
        out["held"] = held is not None
        ready(extra)
        while True:
            time.sleep(1)
    elif mode == "exclusive":
        try:
            result = coordinator.submit(store, "ncp_domain_run", payload(), key, exclusive=extra,
                                        exclusive_wait=float(os.environ.get("EXCLUSIVE_WAIT", "0")))
            out.update(status=result["status"])
        except RetainedFailure as exc:
            out.update(error=str(exc), artifact_id=exc.artifact_id)
    elif mode == "claim":
        start = Path(extra) if extra else None
        while start is not None and not start.exists():
            time.sleep(0.005)
        try:
            out.update(coordinator.claim(store, key))
        except ValueError as exc:
            out.update(error=str(exc))
    elif mode == "recall":
        try:
            out.update(coordinator.coordinate("recall", store=store, idempotency_key=key))
        except ValueError as exc:
            out.update(error=str(exc))
    elif mode == "recover":
        out.update(coordinator.recover(store))
    elif mode == "release":
        start = Path(extra) if extra else None
        while start is not None and not start.exists():
            time.sleep(0.005)
        try:
            out.update(store.release(key))
        except (ValueError, OSError) as exc:
            out.update(error=type(exc).__name__ + ": " + str(exc))
    else:
        raise SystemExit("unknown mode")
    print(json.dumps(out), flush=True)


if __name__ == "__main__":
    main()
