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
