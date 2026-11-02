"""Two workers. Handoff is explicit. A live exclusive resource stays busy."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import ControlHost
from ciw.coordinator import _exclusive_path
from ciw.sequence_kinetics import example_request


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ncp-workers-"))
    left = ControlHost(from_profile(demo_config(root / "left")))
    right = ControlHost(from_profile(demo_config(root / "right")))
    try:
        job = left.call("ncp_coordinate", {
            "action": "submit", "idempotency_key": "worker-1", "operation": "ncp_domain_run",
            "arguments": {"domain": "sequence", "request": example_request()},
        })
        grant = left.call("ncp_coordinate", {
            "action": "handoff", "artifact_id": job["data"]["artifact_id"],
            "recipient": right.retained.owner,
        })
        claimed = right.call("ncp_coordinate", {"action": "claim", "grant_id": grant["data"]["grant_id"]})
        denied = left.call("ncp_retained_read", {"artifact_id": claimed["data"]["artifact_id"]})
        held = _exclusive_path("oven")
        held.write_text(json.dumps({"pid": __import__("os").getpid(), "owner": left.retained.owner}), encoding="utf-8")
        try:
            busy = left.call("ncp_coordinate", {
                "action": "submit", "idempotency_key": "worker-2", "operation": "ncp_domain_run",
                "exclusive": "oven", "arguments": {"domain": "sequence", "request": example_request()},
            })
        finally:
            held.unlink(missing_ok=True)
        print(claimed["status"])
        print(denied["status"])
        print(busy["status"])
    finally:
        left.close()
        right.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
