"""MCP stdio for the folded ControlHost.

Protocol versions and methods are the existing agent profile. This module does
not select a checkout, an interpreter, or a pin.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def main() -> int:
    from .agent_mcp import demo_config, from_profile, serve
    from .control_gateway import ControlHost
    parent = Path(tempfile.mkdtemp(prefix="ncp-folded-"))
    host = ControlHost(from_profile(demo_config(parent / "host")))
    try:
        return serve(host, sys.stdin.buffer, sys.stdout.buffer)
    finally:
        host.close()


if __name__ == "__main__":
    raise SystemExit(main())
