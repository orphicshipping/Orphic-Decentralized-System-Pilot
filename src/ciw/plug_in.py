"""The bind contract for a model or another program.

Reading it does not run a solver, select a pin, or qualify a result.
"""
from __future__ import annotations

import json
from pathlib import Path

PACKAGED = Path(__file__).resolve().parent / "plug-in.json"
CHECKOUT = Path(__file__).resolve().parents[2] / "mcp" / "plug-in.json"


def document() -> dict:
    if PACKAGED.is_file() and CHECKOUT.is_file() and PACKAGED.read_bytes() != CHECKOUT.read_bytes():
        raise RuntimeError("Packaged plug-in drifted from mcp/plug-in.json")
    path = PACKAGED if PACKAGED.is_file() else CHECKOUT
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "ncp.mcp-plug-in.v1":
        raise RuntimeError("Plug-in schema drifted")
    if value.get("authorizes_execution") is not False:
        raise RuntimeError("Plug-in must not authorize execution")
    if value.get("not_a_human_console") is not True:
        raise RuntimeError("Plug-in must not be a human console")
    return value
