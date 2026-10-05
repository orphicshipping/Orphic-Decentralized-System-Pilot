"""Fixed subprocess entry point for an explicitly bound official HOOMD install.

No user-provided source, module, shell command or output path is accepted.
This is a trusted runtime boundary, not a sandbox for untrusted installations.
"""
from hashlib import sha256
import base64
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import tempfile


def probe():
    import hoomd
    import gsd
    import numpy
    def code_hash(package):
        root = Path(package.__file__).parent
        entries = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".pyc", ".so", ".pyd", ".dll", ".dylib"}:
                entries.append((str(path.relative_to(root)), sha256(path.read_bytes()).hexdigest()))
        return sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()
    distribution = None
    manifests = list((Path(sys.prefix) / "conda-meta").glob("hoomd-*.json"))
    if len(manifests) == 1:
        metadata = json.loads(manifests[0].read_text())
        distribution = {key: metadata.get(key) for key in ("name", "version", "build", "subdir", "url", "sha256")}
    return {"distribution": distribution, "provider": "glotzerlab/hoomd-blue", "hoomd_version": hoomd.version.version,
            "hoomd_git_sha": hoomd.version.git_sha1, "gsd_version": importlib.metadata.version("gsd"),
            "numpy_version": numpy.__version__, "python_version": platform.python_version(),
            "device": "cpu", "platform": platform.platform(), "machine": platform.machine(),
            "build": {name: getattr(hoomd.version, name) for name in
                      ("gpu_enabled", "mpi_enabled", "md_built", "hpmc_built", "mpcd_built", "floating_point_precision", "cxx_compiler", "compile_flags")},
            "package_code_sha256": {"hoomd": code_hash(hoomd), "gsd": code_hash(gsd), "numpy": code_hash(numpy)},
            "executable_sha256": sha256(Path(sys.executable).read_bytes()).hexdigest()}


def run(study):
    import hoomd
    before = probe()
    if before["hoomd_version"] != study["hoomd_version"] or study["device"] != "cpu":
        raise ValueError("Runtime mismatch")
    with tempfile.TemporaryDirectory(prefix="net-hoomd-run-") as directory:
        root = Path(directory)
        raw = b"".join(base64.b64decode(part, validate=True) for part in study["initial_state"]["chunks_b64"])
        (root / "initial.gsd").write_bytes(raw)
        simulation = hoomd.Simulation(device=hoomd.device.CPU(notice_level=0), seed=study["seed"])
        simulation.create_state_from_gsd(filename=str(root / "initial.gsd"))
        simulation.always_compute_pressure = True
        potential = study["potential"]
        neighbor = hoomd.md.nlist.Cell(buffer=.4, exclusions=("bond",))
        pair = hoomd.md.pair.LJ(nlist=neighbor, default_r_cut=2**(1/6), mode="shift")
        pair.params[("A", "A")] = {"epsilon": 1.0, "sigma": 1.0}
        bond = hoomd.md.bond.Harmonic()
        bond.params["chain"] = {"k": potential["bond_k"], "r0": potential["bond_r0"]}
        thermostat = hoomd.md.methods.Langevin(filter=hoomd.filter.All(), kT=study["temperature"]["kT"])
        thermostat.gamma["A"] = study["integrator"]["gamma"]
        simulation.operations.integrator = hoomd.md.Integrator(
            dt=study["integrator"]["dt"], methods=[thermostat], forces=[pair, bond])
        simulation.state.thermalize_particle_momenta(filter=hoomd.filter.All(), kT=study["temperature"]["kT"])
        thermo = hoomd.md.compute.ThermodynamicQuantities(filter=hoomd.filter.All())
        simulation.operations.computes.append(thermo)
        logger = hoomd.logging.Logger(categories=["scalar"])
        logger.add(thermo, quantities=["potential_energy", "kinetic_energy", "pressure"])
        for stage in ("equilibration", "production"):
            writer = hoomd.write.GSD(filename=str(root / (stage + ".gsd")),
                trigger=hoomd.trigger.Periodic(study["schedule"]["sample_period"]), mode="xb", precision="double",
                dynamic=["property", "momentum", "attribute", "topology"], logger=logger)
            simulation.operations.writers.append(writer)
            simulation.run(study["schedule"][stage + "_steps"], write_at_start=True)
            writer.flush()
            simulation.operations.writers.remove(writer)
        artifacts = {}
        for stage in ("initial", "equilibration", "production"):
            raw = (root / (stage + ".gsd")).read_bytes()
            if len(raw) > 1024 * 1024:
                raise ValueError("Native GSD exceeded the bounded artifact budget")
            artifacts[stage] = {"media_type": "application/vnd.gsd", "byte_count": len(raw),
                "sha256": "sha256:" + sha256(raw).hexdigest(),
                "chunks_b64": [base64.b64encode(raw[i:i+24576]).decode("ascii") for i in range(0, len(raw), 24576)]}
        if probe() != before:
            raise ValueError("Runtime changed during execution")
        return {"artifacts": artifacts, "runtime": before}


if __name__ == "__main__":
    if sys.argv[1:] == ["probe"]:
        result = probe()
    elif sys.argv[1:] == ["run"]:
        result = run(json.load(sys.stdin))
    else:
        raise ValueError("Unknown fixed worker mode")
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
