"""Independent static mathematical checks of the selected bead-spring model.

These scalar pair/bond sums do not run dynamics. They compare stored native
frames against the explicitly configured potential, virial and geometry. They
cannot establish equilibration, long-time diffusion or real material validity.
"""
from copy import deepcopy
import math

from .control_plane import ObservationBus
from .hoomd_contract import bonds, frames, validate_result
from .hoomd_workflow import observation_stream
from .operations.runner import seal

QUANTITIES = ("potential_energy", "kinetic_energy", "pressure", "radius_gyration")


def reference_observations(value):
    value = validate_result(value)
    study = value["study"]
    native = frames(value["artifacts"]["production"])
    topology = bonds(study)
    excluded = {tuple(pair) for pair in topology}
    expected = []
    for frame in native:
        positions = frame.particles.position.tolist()
        velocities = frame.particles.velocity.tolist()
        lengths = [float(x) for x in frame.configuration.box[:3]]
        images = frame.particles.image.tolist()
        def distance(a, b):
            delta = [positions[b][axis] - positions[a][axis] for axis in range(3)]
            delta = [d - length * round(d / length) for d, length in zip(delta, lengths)]
            return math.sqrt(sum(d*d for d in delta))
        energy, virial = 0.0, 0.0
        for left, right in topology:
            r = distance(left, right)
            extension = r - study["potential"]["bond_r0"]
            k = study["potential"]["bond_k"]
            energy += .5 * k * extension**2
            virial -= k * extension * r
        for left in range(len(positions)):
            for right in range(left+1, len(positions)):
                if (left, right) in excluded:
                    continue
                r = distance(left, right)
                if r < 2**(1/6):
                    if r == 0:
                        raise ValueError("Overlapping particles in reference calculation")
                    inverse6 = r**-6
                    energy += 4 * (inverse6**2 - inverse6) + 1
                    virial += 24 * (2 * inverse6**2 - inverse6)
        kinetic = .5 * sum(component**2 for velocity in velocities for component in velocity)
        offset, rg2 = 0, []
        for size in study["topology"]["chain_lengths"]:
            # Pair-distance identity Rg^2 = sum_ij |r_i-r_j|^2 / (2N^2).
            chain = [[positions[i][axis] + images[i][axis]*lengths[axis] for axis in range(3)]
                     for i in range(offset, offset+size)]
            squared = sum(sum((a-b)**2 for a, b in zip(first, second))
                          for first in chain for second in chain)
            rg2.append(squared / (2 * size**2))
            offset += size
        expected.append({"potential_energy": energy, "kinetic_energy": kinetic,
            "pressure": (2*kinetic + virial) / (3 * math.prod(lengths)),
            "radius_gyration": math.sqrt(sum(rg2) / len(rg2))})
    scales = study["units"]
    factors = {"potential_energy": scales["energy_J"], "kinetic_energy": scales["energy_J"],
               "pressure": scales["energy_J"] / scales["length_m"]**3,
               "radius_gyration": scales["length_m"]}
    observed = observation_stream(value)["observations"]
    bus = ObservationBus(capacity=len(native) * len(QUANTITIES))
    times = sorted({item["clock"]["time_s"] for item in observed})
    by_time = dict(zip(times, expected))
    for original in observed:
        quantity = original["quantity"].removeprefix("hoomd.")
        if quantity not in QUANTITIES:
            continue
        item = deepcopy(original)
        item["value"] = by_time[item["clock"]["time_s"]][quantity] * factors[quantity]
        item["provenance"]["provider"] = "ciw.hoomd-static-pairwise-reference.v1"
        item["provenance"]["semantics"] = "reference"
        bus.publish(seal(item))
    return bus.snapshot()
