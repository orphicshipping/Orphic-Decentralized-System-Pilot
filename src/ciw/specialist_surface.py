"""Discovery of specialist hosts.

A row says the module file is present. It does not import the host, share its
grants, or run its engine.
"""
from __future__ import annotations

from pathlib import Path

_HOSTS = (
    ("knowledge", "knowledge_mcp.py"),
    ("hoomd", "hoomd_worker.py"),
    ("operational", "operational_view.py"),
)


def listing() -> dict:
    here = Path(__file__).resolve().parent
    return {
        "schema": "ncp.specialist-hosts.v1",
        "authorizes_execution": False,
        "hosts": [
            {"host": name, "module_file": filename, "present": (here / filename).is_file(),
             "grants_shared": False, "executed": False}
            for name, filename in _HOSTS
        ],
    }


def describe(host: str) -> dict:
    if host == "knowledge":
        from .knowledge_mcp import TOOLS
        return {"schema": "ncp.specialist-host.v1", "host": host, "tools": sorted(TOOLS),
                "grants_shared": False, "executed": False, "authorizes_execution": False}
    if host == "hoomd":
        return {"schema": "ncp.specialist-host.v1", "host": host, "module": "ciw.hoomd_worker",
                "qualification": "not_run", "grants_shared": False, "executed": False,
                "authorizes_execution": False}
    if host == "operational":
        return {"schema": "ncp.specialist-host.v1", "host": host, "module": "ciw.operational_view",
                "grants_shared": False, "executed": False, "authorizes_execution": False}
    raise ValueError("unknown_specialist_host")


def run(host: str) -> dict:
    if host not in {"knowledge", "hoomd", "operational"}:
        raise ValueError("unknown_specialist_host")
    raise ValueError("specialist_grant_refused")
