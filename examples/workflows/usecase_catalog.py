"""Data-only teaching catalog validation shared by generation and emission."""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
INPUTS = Path(__file__).with_name("teaching-inputs.json")
INDEX = ROOT / "examples/usecase-index.json"
SCHEMA = "ncp.teaching-catalog.v1"


def load_json(path: Path) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    def constant(value):
        raise ValueError(f"nonfinite JSON: {value}")
    def finite_float(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("overflowing JSON number")
        return value
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("teaching input exceeds 4 MiB")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)


def validate_entries(entries: object, families: set[str]) -> list[dict]:
    if type(entries) is not list or not 1 <= len(entries) <= 10000:
        raise ValueError("require 1..10000 teaching entries")
    seen = set()
    for entry in entries:
        required = {"slug", "title", "industry", "sentence", "non_claim", "family", "generated", "render_path"}
        if type(entry) is not dict or set(entry) != required:
            raise ValueError("unexpected or missing scenario fields")
        slug = entry["slug"]
        if type(slug) is not str or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) is None or len(slug) > 96 or slug in seen:
            raise ValueError("invalid or duplicate scenario slug")
        seen.add(slug)
        if entry["family"] not in families or entry["generated"] is not True:
            raise ValueError("unsupported family or executable wrapper request")
        for key in ("title", "industry", "sentence", "non_claim"):
            if type(entry[key]) is not str or not 1 <= len(entry[key]) <= 2048:
                raise ValueError(f"invalid scenario {key}")
        expected = f"examples/usecase-{slug}/results/{slug}_render.json"
        if entry["render_path"] != expected:
            raise ValueError("render path differs from the contained scenario output")
    return entries


def generate(source: Path = INPUTS) -> dict:
    data = load_json(source)
    if set(data) != {"schema", "scope", "families", "entries"} or data["schema"] != "ncp.teaching-inputs.v1":
        raise ValueError("unsupported teaching input document")
    families = set(data["families"])
    from usecase_templates import EMIT_FUNCTIONS
    if not families <= set(EMIT_FUNCTIONS):
        raise ValueError("unknown owned teaching family")
    reviewed = load_json(INPUTS)["families"]
    if source.resolve() != INPUTS.resolve() and any(value != reviewed[key] for key, value in data["families"].items()):
        raise ValueError("custom scenarios must reuse the reviewed owned family definitions")
    entries = validate_entries(data["entries"], families)
    if set(entry["family"] for entry in entries) != families:
        raise ValueError("every declared teaching family requires a scenario")
    for definition in data["families"].values():
        if not definition["sources"]:
            raise ValueError("teaching family requires source inputs")
        for relative in definition["sources"]:
            path = (ROOT / relative).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file():
                raise ValueError(f"missing or uncontained teaching source: {relative}")
    return {"schema": SCHEMA, "scope": data["scope"], "entries": entries,
            "counts": dict(sorted(Counter(entry["family"] for entry in entries).items())),
            "historical_9074_corpus_restored": False}


def read_index(path: Path, families: set[str]) -> dict:
    data = load_json(path)
    if (type(data) is not dict or data.get("schema") != SCHEMA
            or set(data) != {"schema", "scope", "entries", "counts", "historical_9074_corpus_restored"}
            or data["historical_9074_corpus_restored"] is not False):
        raise ValueError("unsupported teaching catalog")
    validate_entries(data.get("entries"), families)
    expected = dict(sorted(Counter(entry["family"] for entry in data["entries"]).items()))
    if data.get("counts") != expected or any(type(value) is not int for value in data["counts"].values()):
        raise ValueError("catalog counts differ from actual entries")
    return data
