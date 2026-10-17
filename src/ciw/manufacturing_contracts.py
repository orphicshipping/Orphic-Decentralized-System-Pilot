"""Bounded data and create-only IO for offline manufacturing records.

Content references use NET's existing encoder. They do not authenticate an
operator, qualify material, admit canonical state, or authorize a physical act.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from .core.identities import canonical_json, content_identity

MAX_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 4096


def keys(value: Any, fields: set[str]) -> None:
    if type(value) is not dict or set(value) != fields:
        raise ValueError("Unexpected or missing manufacturing fields")


def text(value: Any) -> str:
    if type(value) is not str or not value.strip() or len(value) > 256:
        raise ValueError("Require bounded nonempty text")
    return value


def number(value: Any, *, minimum: float = 0) -> float:
    if type(value) not in (int, float) or not minimum <= value <= 1e50 or not math.isfinite(value):
        raise ValueError("Require a finite bounded number, not a boolean")
    return float(value)


def count(value: Any, *, minimum: int = 1, maximum: int = MAX_ITEMS) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Require a bounded integer count")
    return value


def items(value: Any, *, minimum: int = 0, maximum: int = MAX_ITEMS) -> list:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ValueError("Require a bounded list")
    return value


def ref(value: Any) -> str:
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("Require a canonical SHA256 reference")
    return value


def refs(value: Any, *, minimum: int = 0) -> list:
    result = items(value, minimum=minimum, maximum=64)
    for entry in result:
        ref(entry)
    if len(set(result)) != len(result):
        raise ValueError("Duplicate evidence reference")
    return result


def scalar(value: Any, unit: str, *, minimum: float = 0) -> float:
    keys(value, {"value", "unit"})
    if value["unit"] != unit:
        raise ValueError("Require the declared SI unit; no implicit conversion")
    return number(value["value"], minimum=minimum)


def interval(value: Any, unit: str, *, minimum: float = 0) -> tuple[float, float]:
    keys(value, {"lower", "upper", "unit"})
    if value["unit"] != unit:
        raise ValueError("Incorrect interval unit")
    lo, hi = (number(value[k], minimum=minimum) for k in ("lower", "upper"))
    if lo >= hi:
        raise ValueError("Require an ordered nonempty interval")
    return lo, hi


def bounded(value: Any) -> None:
    remaining = [100000]

    def visit(node: Any, depth: int) -> None:
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 20:
            raise ValueError("Manufacturing data exceeds traversal budget")
        if node is None or type(node) is bool:
            return
        if type(node) is str:
            if len(node) > 4096:
                raise ValueError("Excessive text")
        elif type(node) in (int, float):
            number(node, minimum=-1e50)
        elif type(node) is list:
            for child in items(node):
                visit(child, depth + 1)
        elif type(node) is dict and len(node) <= 128:
            for name, child in node.items():
                text(name)
                visit(child, depth + 1)
        else:
            raise ValueError("Require a bounded JSON tree")

    visit(value, 0)
    if len(canonical_json(value).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Manufacturing input exceeds 2 MiB")


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _float(token: str) -> float:
    from decimal import Decimal
    value = float(token)
    if not math.isfinite(value) or value == 0 and Decimal(token) != 0:
        raise ValueError("Nonfinite or underflowed JSON number")
    return value


def load(path: Path) -> dict:
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Require a regular non-symlink input")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Require a regular opened input")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Input exceeds 2 MiB")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_float=_float,
                       parse_constant=lambda token: (_ for _ in ()).throw(ValueError("Invalid number")))
    bounded(value)
    return value


def save_new(path: Path, value: dict) -> None:
    """Publish a complete file atomically without replacing any existing path."""
    bounded(value)
    raw = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("Formatted output exceeds 2 MiB")
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".net-manufacturing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)
