"""Deterministic SDK call. No model and no MCP transport."""
from __future__ import annotations

from ciw.sequence_kinetics import example_request
from ciw.services import invoke


def main() -> int:
    body = invoke("ncp_domain_run", {"domain": "sequence", "request": example_request()})
    page = invoke("ncp_retained_read", {"artifact_id": body["artifact_id"], "offset": 0, "limit": 32})
    print(body["example"])
    print(page["schema"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
