"""Bounded file evidence primitives for development measurement adapters."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re

FILE_LIMIT = 8 * 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024


def keys(value, expected):
    if type(value) is not dict or set(value) != set(expected.split()):
        raise ValueError("Missing or unexpected fields; expected: " + expected)


def text(value):
    if type(value) is not str or not value.strip() or len(value) > 8192:
        raise ValueError("Require bounded nonempty text")
    return value


def number(value, low=-math.inf, high=math.inf):
    if type(value) not in (int, float):
        raise ValueError("Require a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError("Number exceeds binary64") from exc
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError("Number outside supported domain")
    return result



def decimal_float(token):
    """Parse a decimal token without silently converting a nonzero value to zero."""
    if type(token) is not str or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", token.strip()):
        raise ValueError("Require a decimal numeric token")
    result = number(float(token))
    if result == 0.0 and any(c in "123456789" for c in token.lower().split("e")[0]):
        raise ValueError("Nonzero numeric token underflows binary64")
    return result

def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Require a bounded integer")
    return value


def utc(value):
    text(value)
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("Require an explicit UTC timestamp")
    # Reject submicrosecond values rather than silently changing time identities.
    for match in re.finditer(r"[.,](\d+)", value):
        if match.group(1)[6:].strip("0"):
            raise ValueError("Timestamp precision exceeds microseconds")
    return result


def identifiers(value):
    if type(value) is not list:
        raise ValueError("Require unique text identifiers")
    for item in value:
        text(item)
    if len(value) != len(set(value)):
        raise ValueError("Repeated identifier")
    return set(value)


def digest(raw):
    return "sha256:" + sha256(raw).hexdigest()


def hash_id(value):
    if type(value) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("Require a SHA-256 identity")
    return value


def dump(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def load(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("Nonfinite JSON constant: " + value)
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=invalid, parse_float=decimal_float)


class EvidenceStore:
    """Retain exact local bytes, never execute declarations or follow remote URLs."""
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        self.retained = {}
        if not self.path.is_file() or not 0 < self.path.stat().st_size <= FILE_LIMIT:
            raise ValueError("Manifest absent or exceeds file limit")
        self.raw_manifest = self.path.read_bytes()
        if not 0 < len(self.raw_manifest) <= FILE_LIMIT:
            raise ValueError("Manifest exceeds file limit")
        self.manifest = load(self.raw_manifest)
        self.blockers = []

    def block(self, reason):
        if reason not in self.blockers:
            self.blockers.append(reason)

    def read(self, descriptor):
        keys(descriptor, "file sha256")
        name = text(descriptor["file"])
        path = (self.root / name).resolve()
        if Path(name).is_absolute() or not path.is_relative_to(self.root) or path == self.path:
            raise ValueError("Evidence must remain inside its campaign directory")
        if not path.is_file() or not 0 < path.stat().st_size <= FILE_LIMIT:
            raise ValueError("Evidence absent, empty, or exceeds file limit")
        raw = path.read_bytes()
        if not 0 < len(raw) <= FILE_LIMIT:
            raise ValueError("Evidence exceeds file limit")
        identity = digest(raw)
        if identity != hash_id(descriptor["sha256"]):
            raise ValueError("Evidence hash mismatch: " + name)
        self.retained[identity] = raw
        if sum(map(len, self.retained.values())) > TOTAL_LIMIT:
            raise ValueError("Retained campaign exceeds 64 MiB")
        return raw

    def retain_bundle(self, output_dir, report, inference, reference):
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=False)
        (target / "raw").mkdir()
        (target / "campaign.json").write_bytes(self.raw_manifest)
        for identity, raw in self.retained.items():
            (target / "raw" / (identity.split(":")[1] + ".bin")).write_bytes(raw)
        (target / "inference-input.json").write_bytes(dump(inference))
        (target / "reference-evaluation.json").write_bytes(dump(reference))
        # A complete result is published last; partial directories are never reused.
        (target / "report.json").write_bytes(dump(report))
        return report
