"""One agent, one idempotency key, one host."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ciw.agent_mcp import demo_config, from_profile
from ciw.control_gateway import ControlHost
from ciw.sequence_kinetics import example_request


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ncp-agent-"))
    host = ControlHost(from_profile(demo_config(root / "host")))
    try:
        job = host.call("ncp_coordinate", {
            "action": "submit",
            "idempotency_key": "agent-1",
            "operation": "ncp_domain_run",
            "arguments": {"domain": "sequence", "request": example_request()},
        })
        again = host.call("ncp_coordinate", {
            "action": "submit",
            "idempotency_key": "agent-1",
            "operation": "ncp_domain_run",
            "arguments": {"domain": "sequence", "request": example_request()},
        })
        print(job["status"])
        print(again["data"]["replayed"])
        print(again["data"]["executions"])
    finally:
        host.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
