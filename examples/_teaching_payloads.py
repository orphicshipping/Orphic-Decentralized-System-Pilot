"""Project owned fixtures and documented outcomes; never impersonate a provider."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from _render_json import normalize_render, write_render

EXAMPLES = Path(__file__).resolve().parent
REPO = EXAMPLES.parent


def retained(relative: str) -> tuple[dict, dict]:
    path = REPO / relative
    raw = path.read_bytes()
    citation = {"path": relative, "sha256": sha256(raw).hexdigest(), "bytes": len(raw)}
    return json.loads(raw) if path.suffix == ".json" else {}, citation


def build_payload(family: str) -> dict:
    config = json.loads((EXAMPLES / "workflows/teaching-inputs.json").read_text())
    definition = config["families"][family]
    sources = []
    inputs = []
    for name in definition["sources"]:
        value, citation = retained(name)
        inputs.append(value)
        sources.append(citation)
    data = {
        "source": "HOST_FROM_OWNED_CONFIG", "upstream_status": "HISTORICAL",
        "status": "HISTORICAL", "presentation_only": True,
        "may_authorize": False, "fresh_verifier_occurrence": False,
        "claim_scope": "computational-integrity-only", "family": family,
        "data_source": "Owned fixture/configuration bytes and documented outcomes; see source_records",
        "source_records": sources, "caption": definition["caption"],
        "native_execution": "NOT_RUN", "fresh_verification": "NOT_RUN",
        "cards": definition["cards"], "cases": definition.get("cases", []),
        "forbidden_claims": ["physical verification", "state admission", "new CIW kind", "fresh native provider execution"],
    }
    if family == "CSG":
        data["trajectories"] = {"declared_arclength_axis": {
            "points": [[s, 0, 0] for s in inputs[0]["arclength"]],
            "basis": "schematic arclength axis; not an integrated geodesic",
        }}
        data["declared_geometry"] = inputs[0]
    elif family == "PROVED_HEAT":
        data.update(status="UNAVAILABLE", upstream_status="UNAVAILABLE", proof_status="NOT_CHECKED")
        data["trajectories"] = {"initial_values": {
            "points": [[i, value, 0] for i, value in enumerate(inputs[0]["initial_values"])],
            "basis": "retained initial integer profile; no proof or time stepping",
        }}
        data["declared_configuration"] = inputs[0]
    elif family == "VFE":
        data["contrasts"] = [{"experiment_id": value["experiment_id"],
                              "generator": value["generator"], "assumed_model": value["assumed_model"]}
                             for value in inputs]
    elif family == "ENERGY_ACCURACY":
        # Reuse the one existing analyzer; no numerical implementation in this projector.
        sys.path.insert(0, str(REPO / "src"))
        from ciw.energy_records import analyze
        data["analyses"] = [analyze(value) for value in inputs]
        data["analysis_execution"] = "existing ciw.energy_records.analyze over synthetic fixtures"
    return normalize_render(data)


def main(family: str, filename: str) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=EXAMPLES / filename)
    args = parser.parse_args()
    write_render(args.output, build_payload(family))
    print(f"wrote {args.output}; native_execution=NOT_RUN fresh_verification=NOT_RUN")
    return 0
