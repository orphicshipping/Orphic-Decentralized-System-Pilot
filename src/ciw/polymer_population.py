"""Finite declared molar-mass populations; no inference of chemistry or moduli."""
from __future__ import annotations
from copy import deepcopy
import math

from .polymer_viscoelastic import Refusal, _keys, _number, _ID

MODEL = "finite-molar-mass-population.v1"
SCHEMA = "ciw.polymer-population-request.v1"
AUTHORITY = {"scope": "declared-discrete-population-moments",
             "physical_validation": False, "parameter_calibration": False,
             "state_admission": False, "hardware_actuation": False,
             "measurement_uncertainty": "not_declared"}


def validate_request(request):
    _keys(request, {"schema", "model", "weight_basis", "bins"}, "population")
    p = deepcopy(request)
    if p["schema"] != SCHEMA or p["model"] != MODEL:
        raise Refusal("Unsupported population schema/model")
    if p["weight_basis"] not in ("number", "mass"):
        raise Refusal("Explicit number or mass weight basis required")
    if type(p["bins"]) is not list or not 1 <= len(p["bins"]) <= 2048:
        raise Refusal("Expected 1 to 2048 population bins")
    ids = set()
    for b in p["bins"]:
        _keys(b, {"id", "molar_mass_kg_mol", "weight"}, "population bin")
        if type(b["id"]) is not str or not _ID.fullmatch(b["id"]) or b["id"] in ids:
            raise Refusal("Population bin IDs must be bounded and unique")
        ids.add(b["id"])
        b["molar_mass_kg_mol"] = _number(b["molar_mass_kg_mol"], 1e-3, 1e6, "molar mass kg/mol")
        b["weight"] = _number(b["weight"], 0, 1e12, "relative weight")
    if not any(b["weight"] > 0 for b in p["bins"]):
        raise Refusal("Population cannot have zero total weight")
    return p


def analyze(request):
    p = validate_request(request)
    masses = [b["molar_mass_kg_mol"] for b in p["bins"]]
    weights = [b["weight"] for b in p["bins"]]
    # Scale before summing; no automatic reinterpretation of the input basis.
    maximum = max(weights)
    relative = [w/maximum for w in weights]
    if any(w > 0 and r == 0 for w,r in zip(weights,relative)):
        raise Refusal("Population normalization would underflow a nonzero bin")
    total = math.fsum(relative)
    fractions = [w/total for w in relative]
    if p["weight_basis"] == "number":
        number = fractions
    else:
        inv_mass = [w/m for w,m in zip(fractions,masses)]
        normalization = math.fsum(inv_mass)
        number = [x/normalization for x in inv_mass]
    if any(w > 0 and n == 0 for w,n in zip(weights,number)):
        raise Refusal("Number-basis conversion would underflow a nonzero bin")
    mn = math.fsum(n*m for n,m in zip(number,masses))
    mass = [n*m/mn for n,m in zip(number,masses)]
    if any(w > 0 and v == 0 for w,v in zip(weights,mass)):
        raise Refusal("Mass-basis conversion would underflow a nonzero bin")
    mw = math.fsum(w*m for w,m in zip(mass,masses))
    mz = math.fsum(w*m*m for w,m in zip(mass,masses))/mw
    dispersity = mw/mn
    if dispersity < 1-1e-12 or mw < mn*(1-1e-12) or mz < mw*(1-1e-12):
        raise Refusal("Population moment ordering failed")
    return {"bin_order": [b["id"] for b in p["bins"]], "number_fraction": number,
            "mass_fraction": mass, "number_average_kg_mol": mn, "mass_average_kg_mol": mw,
            "z_average_kg_mol": mz, "dispersity": dispersity,
            "normalization": "relative-input-weights-explicitly-normalized",
            "authority": deepcopy(AUTHORITY)}
