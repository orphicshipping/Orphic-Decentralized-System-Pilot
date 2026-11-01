"""The bind contract for a model or another program.

Reading it does not run a solver, select a pin, or qualify a result.
"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "mcp" / "plug-in.json"


def document() -> dict:
    value = json.loads(PATH.read_text(encoding="utf-8"))
    if value.get("schema") != "ncp.mcp-plug-in.v1":
        raise RuntimeError("Plug-in schema drifted")
    if value.get("authorizes_execution") is not False:
        raise RuntimeError("Plug-in must not authorize execution")
    if value.get("not_a_human_console") is not True:
        raise RuntimeError("Plug-in must not be a human console")
    return value
