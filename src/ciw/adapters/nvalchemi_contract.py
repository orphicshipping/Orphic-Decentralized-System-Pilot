"""Narrow, dependency-light ALCHEMI LJ qualification contract.

This is a synthetic single-species benchmark, not an argon calibration, learned
potential, MD trajectory, or qualified material/continuum model. No imports or
downloads are selected by a saved request. Periodicity and stress are refused
until separately qualified. The vendor itself has broader capabilities.
"""
from __future__ import annotations

from copy import deepcopy
import math

from ..core.identities import content_identity
from ..core.records import mapping, number, string
from .protocol import AdapterRefusal

OPERATION = "nvalchemi.lj-single-point.v1"
MODEL = {"kind": "lennard-jones-single-species", "epsilon_ev": 0.0104,
         "sigma_angstrom": 3.4, "cutoff_angstrom": 8.5,
         "switch_width_angstrom": 0.0, "half_list": False}
UNITS = {"positions": "angstrom", "energy": "eV", "forces": "eV/angstrom"}
INPUT_FIELDS = {"schema", "atom_ids", "atomic_numbers", "positions", "coordinate_frame",
                "units", "cell", "pbc", "charge", "spin", "model"}


def _keys(value, fields, name):
    if set(mapping(value, name)) != fields:
        raise ValueError(f"{name} fields differ from the bounded LJ contract")


def _vectors(value, count, name):
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"{name} must have shape [{count}, 3]")
    for row in value:
        if not isinstance(row, list) or len(row) != 3:
            raise ValueError(f"{name} must have shape [{count}, 3]")
        for item in row:
            number(item, name)


def validate_input(value):
    """Validate only the declared neutral, nonperiodic, fixed LJ profile."""
    _keys(value, INPUT_FIELDS, "ALCHEMI input")
    if value["schema"] != "ciw.nvalchemi-lj-input.v1":
        raise ValueError("Unsupported ALCHEMI input schema")
    ids = value["atom_ids"]
    if not isinstance(ids, list) or not 2 <= len(ids) <= 32:
        raise ValueError("The LJ profile requires 2..32 atom identities")
    for name in ids:
        if len(string(name, "atom identity")) > 128:
            raise ValueError("Atom identity exceeds 128 characters")
    if len(set(ids)) != len(ids):
        raise ValueError("Atom identities must be unique")
    numbers = value["atomic_numbers"]
    if (not isinstance(numbers, list) or len(numbers) != len(ids)
            or any(type(z) is not int or z != 18 for z in numbers)):
        raise ValueError("Only the synthetic neutral Z=18 single-species fixture is supported")
    _vectors(value["positions"], len(ids), "positions")
    if any(abs(number(v, "position")) > 1000 for row in value["positions"] for v in row):
        raise ValueError("Coordinates exceed the bounded 1000 angstrom profile")
    string(value["coordinate_frame"], "coordinate frame")
    if value["units"] != UNITS:
        raise ValueError("Explicit angstrom/eV/eV-per-angstrom units are required")
    if (not isinstance(value["pbc"], list) or len(value["pbc"]) != 3
            or any(type(p) is not bool for p in value["pbc"])):
        raise ValueError("PBC must declare three boolean flags")
    if any(value["pbc"]) or value["cell"] is not None:
        raise ValueError("Periodic cells and stress are outside this qualified profile")
    if number(value["charge"], "charge") != 0 or value["spin"] is not None:
        raise ValueError("Charge and spin are unsupported by the neutral LJ fixture")
    _keys(value["model"], set(MODEL), "LJ model")
    if type(value["model"]["half_list"]) is not bool or value["model"] != MODEL:
        raise ValueError("Model parameters differ from the fixed unshifted LJ fixture")
    for key in ("epsilon_ev", "sigma_angstrom", "cutoff_angstrom", "switch_width_angstrom"):
        number(value["model"][key], key)
    # Bound steep short-range forces and avoid duplicate coordinates. A cutoff
    # discontinuity is intentional with switch_width=0 and must not enter a
    # derivative test; the qualification harness stays well away from it.
    for i, left in enumerate(value["positions"]):
        for right in value["positions"][i + 1:]:
            if math.dist(left, right) < 0.8 * MODEL["sigma_angstrom"]:
                raise ValueError("Pair separation is below 0.8 sigma")


def configuration_id(value):
    validate_input(value)
    return content_identity(value)


def validate_output(value, request):
    validate_input(request)
    _keys(value, {"schema", "configuration_id", "atom_ids", "energy", "forces", "units",
                  "model", "interpretation", "energy_reference", "uncertainty"}, "ALCHEMI output")
    if value["schema"] != "ciw.nvalchemi-lj-result.v1":
        raise ValueError("Unsupported ALCHEMI output schema")
    if value["configuration_id"] != configuration_id(request):
        raise ValueError("ALCHEMI output is bound to a different configuration")
    if value["atom_ids"] != request["atom_ids"]:
        raise ValueError("ALCHEMI output must preserve atom ordering")
    number(value["energy"], "predicted energy")
    _vectors(value["forces"], len(request["atom_ids"]), "predicted forces")
    if value["units"] != UNITS or value["model"] != MODEL:
        raise ValueError("ALCHEMI output model or units differ from the input")
    if (value["interpretation"] != "model_prediction"
            or value["energy_reference"] != "unshifted-pair-sum-zero-at-infinite-separation"
            or value["uncertainty"] != {"status": "unavailable"}):
        raise ValueError("ALCHEMI predictions cannot become measurements or zero uncertainty")


def example_input():
    return {"schema": "ciw.nvalchemi-lj-input.v1", "atom_ids": ["atom-a", "atom-b"],
            "atomic_numbers": [18, 18], "positions": [[0.0, 0.0, 0.0], [4.25, 0.0, 0.0]],
            "coordinate_frame": "cartesian-right-handed", "units": deepcopy(UNITS),
            "cell": None, "pbc": [False, False, False], "charge": 0.0, "spin": None,
            "model": deepcopy(MODEL)}


def checked_input(value):
    try:
        validate_input(value)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise AdapterRefusal("INVALID_INPUT", str(exc)) from exc
    return deepcopy(value)
