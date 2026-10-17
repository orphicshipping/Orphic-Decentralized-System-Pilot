"""Explicit host-only retention; data references never select local files or URLs."""
from __future__ import annotations

import os
from pathlib import Path
import re

from .metrology_contract import MAX_BYTES, case, digest


def read_case(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Inspection source cannot be a symlink")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    case(raw)
    return raw


def retain(raw, root):
    case(raw)
    root = Path(root)
    if root.is_symlink():
        raise ValueError("Retention root cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    key = "case-" + digest(raw).split(":")[1]
    target = root / (key + ".json")
    try:
        with target.open("xb") as stream:
            stream.write(raw)
    except FileExistsError:
        if target.is_symlink() or read_case(target) != raw:
            raise ValueError("Retained inspection identity collision or tamper")
    return key


def resolve(root, handle, *, authorized):
    if type(handle) is not str or re.fullmatch(r"case-[0-9a-f]{64}", handle) is None:
        raise ValueError("Require an opaque inspection handle")
    if handle not in authorized:
        raise ValueError("Inspection is outside host grants")
    root = Path(root)
    if root.is_symlink():
        raise ValueError("Retention root cannot be a symlink")
    # O_NOFOLLOW prevents a final-component symlink substitution after validation.
    path = root / (handle + ".json")
    if path.is_symlink():
        raise ValueError("Retained inspection cannot be a symlink")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    case(raw)
    if "case-" + digest(raw).split(":")[1] != handle:
        raise ValueError("Retained inspection digest differs")
    return raw
