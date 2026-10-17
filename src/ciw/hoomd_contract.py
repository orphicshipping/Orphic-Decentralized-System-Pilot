"""Bounded CPU bead-spring studies and authoritative native GSD artifacts.

This module never imports HOOMD or runs dynamics. GSD is an optional, data-only
reader. Reduced units have explicit, caller-declared SI scales; they are not a
material calibration. A finite-lag MSD ratio is not a fitted diffusion constant.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
import math
import re
from pathlib import Path
import tempfile

from .control_contracts import bytes_ref, detached, keys, number, text
from .core.identities import canonical_json
from .operations.runner import digest

STUDY = "ciw.hoomd-study.v1"
RESULT = "ciw.hoomd-result.v1"
OPERATION = "particles.hoomd-md.v1"
VERSION = "7.2.0"
SOURCE_COMMIT = "8578236c7ce99151f9ec4b707b2095227494674d"
GSD_VERSION = "5.0.1"
QUALIFIED_DISTRIBUTION = {"name": "hoomd", "version": VERSION, "build": "cpu_py312_h3353ea4_0",
    "subdir": "linux-64",
    "url": "https://conda.anaconda.org/conda-forge/linux-64/hoomd-7.2.0-cpu_py312_h3353ea4_0.conda",
    "sha256": "367bfd99fb28733b525df490fbe8d5f4263195efd8d72fff9c4a8aa714b0a301"}
MAX_GSD_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 8 * 1024 * 1024
AUTHORITY = {"execution": "external_hoomd_cpu_md", "physical_validation": "not_established",
             "material_property_qualification": "not_performed", "state_admission": "not_performed",
             "machine_control": "not_performed", "equilibration": "scheduled_not_demonstrated"}
LOGS = {"potential_energy": "md/compute/ThermodynamicQuantities/potential_energy",
        "kinetic_energy": "md/compute/ThermodynamicQuantities/kinetic_energy",
        "pressure": "md/compute/ThermodynamicQuantities/pressure"}


def _bounded(value):
    detached(value)
    if len(canonical_json(value).encode()) > MAX_RECORD_BYTES:
        raise ValueError("HOOMD record exceeds its 8 MiB budget")


def _positive(value, upper=1e6):
    if not 0 < number(value) <= upper:
        raise ValueError("Require a positive bounded parameter")


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("Require an integer within the declared profile bounds")


def artifact(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_GSD_BYTES:
        raise ValueError("Native GSD must be nonempty and at most 1 MiB")
    return {"media_type": "application/vnd.gsd", "byte_count": len(raw), "sha256": bytes_ref(raw),
            "chunks_b64": [base64.b64encode(raw[i:i+24576]).decode("ascii")
                           for i in range(0, len(raw), 24576)]}


def artifact_bytes(value):
    keys(value, {"media_type", "byte_count", "sha256", "chunks_b64"})
    chunks = value["chunks_b64"]
    if (value["media_type"] != "application/vnd.gsd" or type(chunks) is not list
            or not 1 <= len(chunks) <= 43
            or any(type(chunk) is not str or not 0 < len(chunk) <= 32768 for chunk in chunks)):
        raise ValueError("Invalid bounded GSD byte envelope")
    try:
        raw = b"".join(base64.b64decode(chunk, validate=True) for chunk in chunks)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid native GSD encoding") from exc
    if type(value["byte_count"]) is not int or artifact(raw) != value:
        raise ValueError("Native GSD hash, count or canonical chunks mismatch")
    return raw


def _gsd():
    try:
        import gsd.hoomd
    except ImportError as exc:
        raise ValueError("Native GSD inspection requires the particles extra (gsd==5.0.1)") from exc
    return gsd.hoomd


def frames(value):
    """Read authoritative bytes with GSD only; never load a simulation provider."""
    raw = artifact_bytes(value)
    reader = _gsd()
    with tempfile.TemporaryDirectory(prefix="net-gsd-inspect-") as directory:
        path = Path(directory) / "native.gsd"
        path.write_bytes(raw)
        try:
            import gsd.fl
            import numpy as np
            # Bound native counts before the high-level reader allocates omitted
            # default arrays. A tiny GSD can otherwise declare billions of beads.
            with gsd.fl.open(str(path), mode="r") as raw_stream:
                if raw_stream.schema != "hoomd" or not 1 <= raw_stream.nframes <= 65:
                    raise ValueError("Unsupported GSD schema or frame budget")
                for index in range(raw_stream.nframes):
                    for group in ("particles", "bonds", "angles", "dihedrals", "impropers", "pairs", "constraints"):
                        name = group + "/N"
                        source = index if raw_stream.chunk_exists(index, name) else 0
                        if not raw_stream.chunk_exists(source, name):
                            count = 0
                        else:
                            count = np.asarray(raw_stream.read_chunk(source, name))
                            if count.shape != (1,) or count.dtype.kind not in "ui":
                                raise ValueError("Malformed GSD native count")
                            count = int(count[0])
                        if not 0 <= count <= 256 or (group == "particles" and count < 2):
                            raise ValueError("GSD native count exceeds bounded particle/topology profile")
            with reader.open(str(path), mode="r") as stream:
                if not 1 <= len(stream) <= 65:
                    raise ValueError("GSD trajectory exceeds the 65-frame profile")
                return list(stream)
        except (RuntimeError, OSError) as exc:
            raise ValueError("Invalid native GSD artifact") from exc


def bonds(study):
    result, offset = [], 0
    for size in study["topology"]["chain_lengths"]:
        result.extend([[i, i+1] for i in range(offset, offset+size-1)])
        offset += size
    return result


def topology_ref(study):
    return digest(study["topology"])


def _frame(frame, study):
    import numpy as np
    count = sum(study["topology"]["chain_lengths"])
    expected_bonds = bonds(study)
    step = np.asarray(frame.configuration.step)
    if step.shape != () or step.dtype.kind not in "ui" or not 0 <= int(step) <= 200000:
        raise ValueError("Native timestep must be an integer within the study budget")
    for group, name, dtype in ((frame.particles, "image", np.int32),
            (frame.particles, "typeid", np.uint32), (frame.particles, "body", np.int32),
            (frame.bonds, "group", np.uint32), (frame.bonds, "typeid", np.uint32)):
        if np.asarray(getattr(group, name)).dtype != np.dtype(dtype):
            raise ValueError("Native topology/image fields require their exact integer dtype")
    for name, shape in {"mass": (count,), "typeid": (count,), "body": (count,),
            "charge": (count,), "diameter": (count,), "moment_inertia": (count, 3),
            "orientation": (count, 4), "angmom": (count, 4)}.items():
        values = np.asarray(getattr(frame.particles, name))
        if values.shape != shape or not np.isfinite(values).all():
            raise ValueError("Malformed/nonfinite native particle attribute " + name)
    if (np.asarray(frame.bonds.group).shape != (len(expected_bonds), 2)
            or np.asarray(frame.bonds.typeid).shape != (len(expected_bonds),)):
        raise ValueError("Malformed native bond array shape")
    if (np.any(frame.particles.diameter != 1) or np.any(frame.particles.angmom != 0)
            or np.any(frame.particles.orientation != [1, 0, 0, 0])):
        raise ValueError("Unsupported non-default size or orientation/angular momentum")
    if (frame.configuration.dimensions != 3 or frame.particles.N != count
            or list(frame.particles.types) != ["A"] or np.any(frame.particles.typeid != 0)
            or frame.bonds.N != len(expected_bonds) or list(frame.bonds.types) != ["chain"]
            or frame.bonds.group.tolist() != expected_bonds or np.any(frame.bonds.typeid != 0)
            or np.any(frame.particles.mass != 1)
            or any(getattr(frame, name).N != 0 for name in ("angles", "dihedrals", "impropers", "pairs", "constraints"))):
        raise ValueError("GSD topology, mass or particle identity contradicts the linear-chain profile")
    box = np.asarray(frame.configuration.box, dtype=float)
    if (box.shape != (6,) or not np.isfinite(box).all() or np.any(box[:3] <= 0)
            or np.any(box[:3] > 64) or np.any(box[3:] != 0) or min(box[:3]) <= 2 * (2**(1/6) + .4)):
        raise ValueError("Require a sufficiently large orthorhombic periodic box")
    for name in ("position", "velocity", "image"):
        values = np.asarray(getattr(frame.particles, name))
        if values.shape != (count, 3) or not np.isfinite(values).all():
            raise ValueError("Nonfinite or malformed native particle arrays")
    if np.any(np.abs(frame.particles.position) > box[:3] / 2 + 1e-6):
        raise ValueError("Native positions must be wrapped inside the periodic box")
    # This profile has no charges, rigid bodies, rotational/anisotropic dynamics.
    if (np.any(frame.particles.charge != 0) or np.any(frame.particles.moment_inertia != 0)
            or np.any(frame.particles.body != -1)):
        raise ValueError("Unsupported charge, rigid body or rotational degrees of freedom")
    unwrapped = np.asarray(frame.particles.position, dtype=float) + frame.particles.image * box[:3]
    for left, right in expected_bonds:
        if np.linalg.norm(unwrapped[right] - unwrapped[left]) >= min(box[:3]) / 2:
            raise ValueError("Chain images are not a contiguous unwrapped topology")
    return unwrapped


def validate_study(value):
    _bounded(value)
    keys(value, {"schema", "material", "representation", "units", "topology", "potential",
                 "ensemble", "temperature", "pressure", "integrator", "schedule", "seed",
                 "device", "hoomd_version", "initial_state"})
    if (value["schema"] != STUDY or value["representation"] != "coarse_grained_linear_chains"
            or value["device"] != "cpu" or value["hoomd_version"] != VERSION
            or value["ensemble"] != "NVT_Langevin"):
        raise ValueError("Unsupported HOOMD version, device, representation or ensemble")
    keys(value["material"], {"id", "meaning"})
    text(value["material"]["id"])
    if value["material"]["meaning"] != "unqualified_model_specimen":
        raise ValueError("P0 materials are explicitly unqualified model specimens")
    units = value["units"]
    keys(units, {"system", "length_m", "energy_J", "mass_kg", "scale_authority"})
    if units["system"] != "reduced_lj" or units["scale_authority"] != "caller_declared_not_calibrated":
        raise ValueError("Require explicit reduced units and uncalibrated SI scales")
    for key in ("length_m", "energy_J", "mass_kg"):
        if not 1e-30 <= number(units[key]) <= 1e3:
            raise ValueError("Unit scale outside the supported numeric range")
    topology = value["topology"]
    keys(topology, {"kind", "particle_order", "chain_lengths"})
    if topology["kind"] != "linear" or topology["particle_order"] != "contiguous_chain_tags":
        raise ValueError("Unsupported topology/order")
    sizes = topology["chain_lengths"]
    if type(sizes) is not list or not 1 <= len(sizes) <= 16:
        raise ValueError("Require 1 to 16 declared linear chains")
    for size in sizes:
        _integer(size, 2, 64)
    if sum(sizes) > 256:
        raise ValueError("P0 supports at most 256 beads")
    potential = value["potential"]
    keys(potential, {"pair", "bond", "epsilon", "sigma", "bond_k", "bond_r0", "exclude_bonded_pairs"})
    if (potential["pair"] != "WCA" or potential["bond"] != "harmonic"
            or potential["epsilon"] != 1.0 or potential["sigma"] != 1.0
            or type(potential["epsilon"]) not in (int, float) or type(potential["sigma"]) not in (int, float)
            or potential["exclude_bonded_pairs"] is not True):
        raise ValueError("Require explicit unit WCA plus harmonic bond model")
    _positive(potential["bond_k"], 1000)
    _positive(potential["bond_r0"], 2)
    keys(value["temperature"], {"kT", "unit"})
    if value["temperature"]["unit"] != "epsilon":
        raise ValueError("Temperature is kT in reduced energy units, not kelvin")
    _positive(value["temperature"]["kT"], 10)
    if value["pressure"] != {"target": None, "unit": "epsilon/sigma^3"}:
        raise ValueError("NVT has no target pressure; pressure is an observable")
    keys(value["integrator"], {"dt", "gamma", "velocity_initialization"})
    _positive(value["integrator"]["dt"], .005)
    _positive(value["integrator"]["gamma"], 100)
    if value["integrator"]["velocity_initialization"] != "seeded_thermalize":
        raise ValueError("Require explicitly seeded momentum initialization")
    keys(value["schedule"], {"equilibration_steps", "production_steps", "sample_period"})
    schedule = value["schedule"]
    for key in schedule:
        _integer(schedule[key], 1, 100000)
    period = schedule["sample_period"]
    if any(schedule[key] % period or not 1 <= schedule[key] // period <= 64
           for key in ("equilibration_steps", "production_steps")):
        raise ValueError("Each stage must contain 1 to 64 complete sampling periods")
    _integer(value["seed"], 0, 65535)
    initial = frames(value["initial_state"])
    if len(initial) != 1 or initial[0].configuration.step != 0:
        raise ValueError("Initial GSD must have exactly one frame at timestep zero")
    positions = _frame(initial[0], value)
    import numpy as np
    if np.any(initial[0].particles.velocity != 0):
        raise ValueError("Initial velocities must be zero before explicit thermalization")
    # Refuse overlaps before launching expensive dynamics, without repairing them.
    box = np.asarray(initial[0].configuration.box[:3])
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            delta = positions[j] - positions[i]
            delta -= box * np.rint(delta / box)
            if np.linalg.norm(delta) < .5:
                raise ValueError("Initial particles overlap below the bounded safe-distance guard")
    return deepcopy(value)


def summarize(study, artifacts):
    """Derive descriptors from native bytes only. No trajectories become JSON arrays."""
    import numpy as np
    keys(artifacts, {"initial", "equilibration", "production"})
    if artifacts["initial"] != study["initial_state"]:
        raise ValueError("Run must preserve exact initial native bytes")
    schedule = study["schedule"]
    period, equil = schedule["sample_period"], schedule["equilibration_steps"]
    summaries, descriptors = {}, []
    initial_frame = frames(artifacts["initial"])[0]
    initial_box = initial_frame.configuration.box
    initial_production = None
    equil_final = None
    for stage in ("initial", "equilibration", "production"):
        native = frames(artifacts[stage])
        expected = ([0] if stage == "initial" else list(range(0, equil+1, period))
                    if stage == "equilibration" else list(range(equil, equil+schedule["production_steps"]+1, period)))
        if [int(frame.configuration.step) for frame in native] != expected:
            raise ValueError("Native stage frame timesteps contradict the schedule")
        frame_ids = []
        for index, frame in enumerate(native):
            positions = _frame(frame, study)
            if not np.array_equal(frame.configuration.box, initial_box):
                raise ValueError("NVT trajectory changed the declared box")
            identity = digest({"artifact_ref": artifacts[stage]["sha256"], "frame_index": index,
                               "step": expected[index], "topology_ref": topology_ref(study)})
            frame_ids.append(identity)
            if stage == "initial":
                continue
            if stage == "equilibration" and index == 0:
                for field in ("position", "image"):
                    if not np.array_equal(getattr(frame.particles, field), getattr(initial_frame.particles, field)):
                        raise ValueError("Equilibration does not begin from the retained initial state")
            logs = {}
            for name, path in LOGS.items():
                entry = np.asarray(frame.log.get(path, []))
                if entry.size != 1 or not np.isfinite(entry).all():
                    raise ValueError("Missing/nonfinite native thermodynamic log")
                logs[name] = number(float(entry.flat[0]))
            if stage != "production":
                if index == len(native)-1:
                    equil_final = frame
                continue
            if index == 0:
                for field in ("position", "velocity", "image"):
                    if not np.array_equal(getattr(frame.particles, field), getattr(equil_final.particles, field)):
                        raise ValueError("Production does not continue the final equilibration state")
                if any(not np.array_equal(frame.log[key], equil_final.log[key]) for key in LOGS.values()):
                    raise ValueError("Stage boundary thermodynamic logs disagree")
            if initial_production is None:
                initial_production = positions.copy()
            offset, rg2 = 0, []
            for size in study["topology"]["chain_lengths"]:
                chain = positions[offset:offset+size]
                rg2.append(float(np.mean(np.sum((chain - chain.mean(axis=0))**2, axis=1))))
                offset += size
            msd = float(np.mean(np.sum((positions - initial_production)**2, axis=1)))
            elapsed = (expected[index] - equil) * study["integrator"]["dt"]
            descriptors.append({"frame_id": identity, "step": expected[index], **logs,
                "radius_gyration": math.sqrt(sum(rg2) / len(rg2)), "mean_square_displacement": msd,
                "finite_lag_diffusivity": None if elapsed == 0 else msd / (6 * elapsed)})
        summaries[stage] = {"artifact_ref": artifacts[stage]["sha256"], "frame_ids": frame_ids,
                            "steps": expected, "topology_ref": topology_ref(study)}
    return {"native_frames": summaries, "production_descriptors": descriptors,
            "descriptor_units": {"potential_energy": "epsilon", "kinetic_energy": "epsilon",
                "pressure": "epsilon/sigma^3", "radius_gyration": "sigma",
                "mean_square_displacement": "sigma^2", "finite_lag_diffusivity": "sigma^2/tau"},
            "analysis": {"radius_gyration": "sqrt_mean_chain_rg_squared_unwrapped_equal_chain_weight",
                "displacement": "single_origin_particle_msd_unwrapped_no_drift_removal",
                "diffusion": "finite_lag_msd_over_6t_not_asymptotic_fit",
                "uncertainty": "not_estimated", "equilibration_test": "not_performed"}}


def validate_runtime(value):
    keys(value, {"provider", "hoomd_version", "hoomd_git_sha", "gsd_version", "numpy_version",
                 "python_version", "device", "platform", "machine", "build", "package_code_sha256",
                 "executable_sha256", "worker_sha256", "bridge_code_sha256", "analysis_environment", "binding", "distribution"})
    if (value["provider"] != "glotzerlab/hoomd-blue" or value["hoomd_version"] != VERSION
            or value["gsd_version"] != GSD_VERSION or value["device"] != "cpu"
            or value["binding"] != "explicit_process_local"):
        raise ValueError("HOOMD runtime identity/version/device mismatch")
    if value["hoomd_git_sha"] != SOURCE_COMMIT and not (
            value["hoomd_git_sha"] == "unknown" and value["distribution"] == QUALIFIED_DISTRIBUTION):
        raise ValueError("Runtime must match the pinned source commit or qualified conda archive")
    keys(value["analysis_environment"], {"python", "gsd", "numpy"})
    if value["analysis_environment"]["gsd"] != GSD_VERSION:
        raise ValueError("Native analysis requires the pinned GSD reader")
    for item in value["analysis_environment"].values():
        text(item)
    if value["distribution"] is not None:
        keys(value["distribution"], set(QUALIFIED_DISTRIBUTION))
        for item in value["distribution"].values():
            text(item)
    for key in ("hoomd_git_sha", "numpy_version", "python_version", "platform", "machine"):
        text(value[key])
    keys(value["build"], {"gpu_enabled", "mpi_enabled", "md_built", "hpmc_built", "mpcd_built",
                         "floating_point_precision", "cxx_compiler", "compile_flags"})
    if value["build"]["md_built"] is not True:
        raise ValueError("HOOMD runtime must include MD")
    for key in ("gpu_enabled", "mpi_enabled", "md_built", "hpmc_built", "mpcd_built"):
        if type(value["build"][key]) is not bool:
            raise ValueError("Malformed build feature flag")
    if value["build"]["floating_point_precision"] not in ([64, 32], [64, 64]):
        raise ValueError("Unsupported HOOMD arithmetic precision")
    keys(value["package_code_sha256"], {"hoomd", "gsd", "numpy"})
    for ref in [*value["package_code_sha256"].values(), value["executable_sha256"], value["worker_sha256"], value["bridge_code_sha256"]]:
        if type(ref) is not str or not re.fullmatch(r"[0-9a-f]{64}", ref):
            raise ValueError("Runtime code identities require exact SHA256 hashes")
    return deepcopy(value)


def validate_result(value):
    _bounded(value)
    keys(value, {"schema", "study", "study_ref", "artifacts", "summary", "runtime", "authority"})
    study = validate_study(value["study"])
    if value["schema"] != RESULT or value["study_ref"] != digest(study) or value["authority"] != AUTHORITY:
        raise ValueError("HOOMD result source or authority mismatch")
    validate_runtime(value["runtime"])
    if canonical_json(value["summary"]) != canonical_json(summarize(study, value["artifacts"])):
        raise ValueError("Descriptor/frame manifest differs from authoritative GSD bytes")
    return deepcopy(value)


def example_study():
    """Two four-bead model chains. Nominal scales are examples, not polymer data."""
    import numpy as np
    frame = _gsd().Frame()
    frame.configuration.box = [12, 12, 12, 0, 0, 0]
    frame.particles.N = 8
    frame.particles.types = ["A"]
    frame.particles.position = [[(i - 1.5) * 1.05, y, 0] for y in (-1.5, 1.5) for i in range(4)]
    frame.particles.mass = np.ones(8)
    frame.bonds.N = 6
    frame.bonds.types = ["chain"]
    frame.bonds.group = [[0, 1], [1, 2], [2, 3], [4, 5], [5, 6], [6, 7]]
    with tempfile.TemporaryDirectory(prefix="net-chain-") as directory:
        path = Path(directory) / "initial.gsd"
        with _gsd().open(str(path), "w", precision="double") as output:
            output.append(frame)
        initial = artifact(path.read_bytes())
    return validate_study({"schema": STUDY, "material": {"id": "generic-bead-spring-demo",
        "meaning": "unqualified_model_specimen"}, "representation": "coarse_grained_linear_chains",
        "units": {"system": "reduced_lj", "length_m": 1e-9, "energy_J": 4.14e-21,
                  "mass_kg": 1e-25, "scale_authority": "caller_declared_not_calibrated"},
        "topology": {"kind": "linear", "particle_order": "contiguous_chain_tags", "chain_lengths": [4, 4]},
        "potential": {"pair": "WCA", "bond": "harmonic", "epsilon": 1.0, "sigma": 1.0,
                      "bond_k": 30.0, "bond_r0": 1.0, "exclude_bonded_pairs": True},
        "ensemble": "NVT_Langevin", "temperature": {"kT": 1.0, "unit": "epsilon"},
        "pressure": {"target": None, "unit": "epsilon/sigma^3"},
        "integrator": {"dt": .002, "gamma": 1.0, "velocity_initialization": "seeded_thermalize"},
        "schedule": {"equilibration_steps": 100, "production_steps": 200, "sample_period": 20},
        "seed": 17, "device": "cpu", "hoomd_version": VERSION, "initial_state": initial})
