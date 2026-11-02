"""MCP stdio client. Run outside the checkout with the installed package."""
from __future__ import annotations

import json
import subprocess
import sys


def _open():
    return subprocess.Popen(
        [sys.executable, "-m", "ciw.folded_stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/tmp")


def _request(proc, method, params, request_id=None):
    message = {"jsonrpc": "2.0", "method": method, "params": params}
    if request_id is not None:
        message["id"] = request_id
    proc.stdin.write((json.dumps(message) + "\n").encode())
    proc.stdin.flush()
    if request_id is None:
        return None
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError(proc.stderr.read().decode()[:500] or "MCP server closed")
    return json.loads(line)


def main() -> int:
    from ciw.sequence_kinetics import example_request
    proc = _open()
    try:
        init = _request(proc, "initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "ncp-example", "version": "1"},
        }, 1)
        if "error" in init:
            raise RuntimeError(init["error"])
        _request(proc, "notifications/initialized", {})
        called = _request(proc, "tools/call", {
            "name": "ncp_domain_run",
            "arguments": {"domain": "sequence", "request": example_request()},
        }, 2)
        data = called["result"]["structuredContent"]["data"]
        page = _request(proc, "tools/call", {
            "name": "ncp_retained_read",
            "arguments": {"artifact_id": data["artifact_id"], "offset": 0, "limit": 32},
        }, 3)
        print(data["example"])
        print(page["result"]["structuredContent"]["data"]["schema"])
        print(init["result"]["protocolVersion"])
    finally:
        proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
