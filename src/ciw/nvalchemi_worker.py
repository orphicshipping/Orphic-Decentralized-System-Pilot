"""Isolated ALCHEMI worker. Imports torch/ALCHEMI only for explicit evaluation.

No model downloads, training, trajectory generation or device fallback. The
--probe command reads installed metadata and toolkit files, without GPU imports.
"""
from __future__ import annotations

import base64
from contextlib import redirect_stdout
from hashlib import sha256
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import sys
from urllib.parse import urlparse
import uuid

from . import nvalchemi_contract as c

REQUIRED = ("nvalchemi-toolkit", "nvalchemi-toolkit-ops", "torch", "warp-lang", "numpy", "pydantic")


def _package_files(name: str) -> str:
    """Check installed toolkit/ops file bytes against RECORD, then fingerprint.

    This detects local drift; RECORD is not a publisher signature. Other
    dependencies are version/metadata-pinned, not byte-authenticated here.
    """
    dist = metadata.distribution(name)
    entries = dist.files
    if not entries:
        raise ValueError("An installed wheel RECORD is required: " + name)
    root = Path(dist.locate_file("")).resolve()
    paths = {}
    package_roots = set()
    total = 0
    for item in entries:
        parts = item.parts
        if not parts or not parts[0].startswith("nvalchemi") or parts[0].endswith(".dist-info"):
            continue
        if ".." in parts or item.is_absolute():
            raise ValueError("Unsafe installed toolkit path")
        path = root.joinpath(*parts)
        if "__pycache__" in parts or path.suffix == ".pyc":
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError("Toolkit file missing, linked or outside its installation")
        if item.hash is None or item.hash.mode != "sha256":
            raise ValueError("Toolkit source requires a SHA-256 RECORD entry")
        size = path.stat().st_size
        total += size
        if total > 256 * 1024 * 1024:
            raise ValueError("Toolkit source-verification byte budget exceeded")
        raw = path.read_bytes()
        h = sha256(raw).digest()
        if base64.urlsafe_b64encode(h).decode("ascii").rstrip("=") != item.hash.value:
            raise ValueError("Installed toolkit file differs from RECORD")
        paths[item.as_posix()] = h.hex()
        package_roots.add(parts[0])
    if not paths:
        raise ValueError("No non-editable toolkit source files were found")
    for package in package_roots:
        for path in (root / package).rglob("*"):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.is_symlink():
                raise ValueError("Toolkit package contains a source symlink")
            if path.is_file() and path.relative_to(root).as_posix() not in paths:
                raise ValueError("Toolkit package contains an unrecorded file")
    return c.digest(paths)


def probe_environment() -> dict:
    """Metadata-only inspection, with explicit upstream revision and file checks."""
    selected = {name: metadata.version(name) for name in REQUIRED}
    dist = metadata.distribution("nvalchemi-toolkit")
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    url = urlparse(direct.get("url", ""))
    vcs = direct.get("vcs_info", {})
    if (url.scheme != "https" or url.hostname != "github.com" or url.username is not None
            or url.path.lower().removesuffix(".git") != "/nvidia/nvalchemi-toolkit"
            or vcs.get("vcs") != "git" or vcs.get("commit_id") != c.TOOLKIT_REVISION
            or direct.get("dir_info", {}).get("editable", False)):
        raise ValueError("Install the exact non-editable, VCS-pinned ALCHEMI source revision")
    inventory = {}
    for item in metadata.distributions():
        name = item.metadata.get("Name", "").lower().replace("_", "-").replace(".", "-")
        if not name or name in inventory:
            raise ValueError("Ambiguous installed distribution inventory")
        record = item.read_text("RECORD")
        inventory[name] = {"version": item.version,
                           "record_sha256": None if record is None else sha256(record.encode()).hexdigest()}
    selected.update({
        "toolkit_revision": c.TOOLKIT_REVISION,
        "toolkit_files_sha256": _package_files("nvalchemi-toolkit"),
        "ops_files_sha256": _package_files("nvalchemi-toolkit-ops"),
        "distribution_inventory_sha256": c.digest(inventory),
    })
    return {"python_version": platform.python_version(), "dependencies": selected}


def evaluate(inputs: dict) -> dict:
    c.keys(inputs, {"configuration", "device", "environment_sha256"})
    state = c.validate_input(inputs["configuration"])
    device = inputs["device"]
    if device not in {"cpu", "cuda:0"}:
        raise ValueError("An explicit cpu or cuda:0 binding is required")
    environment = probe_environment()  # verify before loading third-party code
    if c.digest(environment) != inputs["environment_sha256"]:
        raise ValueError("Worker environment differs from the approved operator fingerprint")

    import torch
    from nvalchemi.data import AtomicData, Batch
    from nvalchemi.models.lj import LennardJonesModelWrapper

    if device == "cuda:0" and not torch.cuda.is_available():
        raise RuntimeError("The explicitly requested CUDA device is unavailable; no CPU fallback")
    count = len(state["atom_ids"])
    positions = torch.tensor(state["positions"], dtype=torch.float64, device=device)
    atomic = AtomicData(positions=positions,
                        atomic_numbers=torch.tensor(state["atomic_numbers"], dtype=torch.long, device=device))
    batch = Batch.from_data_list([atomic])
    if batch.positions.dtype != torch.float64 or batch.positions.device != positions.device:
        raise ValueError("Batch conversion changed position precision or device")
    # Bounded explicit full directed list, NOT qualification of upstream neighbor
    # discovery, periodic images, half lists, batching or spatial decomposition.
    neighbors = torch.full((count, count - 1), count, dtype=torch.int32, device=device)
    num_neighbors = torch.zeros(count, dtype=torch.int32, device=device)
    for i in range(count):
        others = [j for j in range(count) if i != j]
        neighbors[i] = torch.tensor(others, dtype=torch.int32, device=device)
        num_neighbors[i] = count - 1
    batch["neighbor_matrix"] = neighbors
    batch["num_neighbors"] = num_neighbors
    model = LennardJonesModelWrapper(
        epsilon=c.MODEL["epsilon_eV"], sigma=c.MODEL["sigma_angstrom"],
        cutoff=c.MODEL["cutoff_angstrom"], switch_width=0.0, half_list=False,
    ).to(device=device, dtype=torch.float64)
    with torch.no_grad():
        output = model(batch)
    if set(output) != {"energy", "forces"} or tuple(output["energy"].shape) != (1, 1) or tuple(output["forces"].shape) != (count, 3):
        raise ValueError("ALCHEMI returned unexpected output keys or tensor shapes")
    if output["energy"].device != positions.device or output["forces"].device != positions.device:
        raise ValueError("ALCHEMI changed the explicitly bound execution device")
    if output["energy"].dtype != torch.float64 or output["forces"].dtype != torch.float64:
        raise ValueError("ALCHEMI changed the declared float64 precision")
    if not bool(torch.isfinite(output["energy"]).all()) or not bool(torch.isfinite(output["forces"]).all()):
        raise ValueError("ALCHEMI returned non-finite energy or forces")
    info = {
        "device": device, "dtype": "float64", "torch_version": str(torch.__version__),
        "cuda_runtime": torch.version.cuda, "gpu_name": None, "compute_capability": None,
        "driver_version": None,  # unavailable: no fabricated driver attestation
    }
    if device == "cuda:0":
        torch.cuda.synchronize(0)
        info["gpu_name"] = torch.cuda.get_device_name(0)
        info["compute_capability"] = list(torch.cuda.get_device_capability(0))
    result = {
        "schema": "ciw.atomic-lj-prediction.v1", "prediction_id": "prediction-" + uuid.uuid4().hex,
        "input_sha256": c.digest(state), "atom_ids": state["atom_ids"],
        "model": c.MODEL, "units": c.UNITS,
        "values": {"energy_eV": output["energy"].detach().cpu().item(),
                   "forces_eV_per_angstrom": output["forces"].detach().cpu().tolist()},
        "uncertainty": {"status": "unavailable", "covariance": None},
        "execution_device": info, "environment_sha256": c.digest(environment),
    }
    c.validate_worker_data(result, state, device)
    if probe_environment() != environment:
        raise ValueError("Worker dependency identity changed during computation")
    return result


def main() -> int:
    output_stream = sys.stdout
    try:
        if sys.argv[-1:] == ["--probe"]:
            result = probe_environment()
        else:
            request = c.loads(sys.stdin.buffer.read(c.MAX_BYTES + 1))
            c.keys(request, {"schema", "operation_id", "inputs"})
            if request["schema"] != "ciw.adapter-request.v1" or request["operation_id"] != c.OPERATION:
                raise ValueError("Unsupported worker operation")
            with redirect_stdout(sys.stderr):
                result = {"schema": "ciw.adapter-response.v1", "status": "ok",
                          "data": evaluate(request["inputs"])}
        output_stream.write(c.canonical(result).decode("ascii") + "\n")
        return 0
    except Exception as exc:
        # Refusal only: never substitute the scalar reference for missing ALCHEMI.
        code = "RUNTIME_UNAVAILABLE" if isinstance(exc, (ImportError, metadata.PackageNotFoundError)) else "ALCHEMI_REFUSED"
        result = {"schema": "ciw.adapter-response.v1", "status": "refused",
                  "refusal": {"code": code, "message": (str(exc) or type(exc).__name__)[:1000]}}
        output_stream.write(c.canonical(result).decode("ascii") + "\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
