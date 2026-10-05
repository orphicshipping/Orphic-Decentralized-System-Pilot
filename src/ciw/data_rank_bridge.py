"""Explicit CAREX-shaped numerical reference to the existing OIT rank endpoint.

Uses the existing artifact/data-record and Session operation contracts. Loading
never executes archive contents or binds a provider. Replay requires an explicit
operator-owned OIT source binding; offline validation performs no SVD.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import platform
import sys
import tempfile
import zipfile

import numpy as np

from .adapters.subprocess import _bounded_process, _json
from .control_contracts import artifact, detached, keys, validate_artifact
from .data_sources import native_record, retain_record
from .operations.registry import Operation
from .operations.runner import digest

OPERATION = "data.rank-carex-matrices.v1"
MAX_ARCHIVE = 1024 * 1024
MAX_EXPANDED = 8 * 1024 * 1024
TOLERANCES = {"rank_rtol": 1e-12, "rank_atol": 0.0, "weak_rtol": 1e-6}
POLICY = {
    "scope": "raw_published_matrix_numerical_rank_only",
    "coordinates": "original_row_and_column_order_no_rescaling_or_unit_conversion",
    "physical_units": "not_declared_in_retained_source",
    "time_domain": "continuous_time_CARE_reference_not_discretized",
    "uncertainty": "not_supplied_not_required_for_matrix_rank_not_invented",
    "Q_R_semantics": "Riccati_cost_weights_not_measurement_covariance",
    "observability": "not_established_no_observation_matrix_or_horizon_supplied",
    "identifiability": "not_established_no_sensitivity_or_measurement_covariance_supplied",
    "Riccati_solution_or_Lyapunov_certificate": "not_computed",
    "physical_qualification": "not_established", "state_admission": "not_performed",
    "source_authentication": "not_established_by_content_hashes",
    "rank_rule": "s > max(rank_atol, rank_rtol * s_max)",
    "rank_atol_units": "raw_matrix_singular_value_units_unspecified_physically",
    "tolerances": TOLERANCES,
}
QUANTITIES = {"A": "continuous_time_state_coefficient_matrix", "B": "input_coefficient_matrix",
              "Q": "state_cost_weight_matrix", "R": "input_cost_weight_matrix"}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def parse_npz(raw: bytes) -> tuple[dict, dict]:
    """Decode only four bounded numeric NPY matrices; never unpickle or extract."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_ARCHIVE:
        raise ValueError("NPZ archive exceeds bounded byte profile")
    arrays, metadata = {}, []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if (len(members) != 4 or {m.filename for m in members} != {k + ".npy" for k in QUANTITIES}
                    or any(m.flag_bits & 1 or m.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                           for m in members)
                    or sum(m.file_size for m in members) > MAX_EXPANDED):
                raise ValueError("Require exactly four bounded unencrypted A/B/Q/R NPY members")
            for member in members:
                with archive.open(member) as stream:
                    data = stream.read(MAX_EXPANDED + 1)
                if len(data) != member.file_size or len(data) > MAX_EXPANDED:
                    raise ValueError("NPY member size mismatch")
                stream = io.BytesIO(data)
                version = np.lib.format.read_magic(stream)
                readers = {(1, 0): np.lib.format.read_array_header_1_0,
                           (2, 0): np.lib.format.read_array_header_2_0}
                if version not in readers:
                    raise ValueError("Unsupported NPY header version")
                shape, fortran, dtype = readers[version](stream, max_header_size=4096)
                if (dtype.hasobject or dtype.fields is not None or dtype.kind not in "fiu"
                        or dtype.itemsize > 8 or len(shape) != 2
                        or any(type(n) is not int or not 1 <= n <= 512 for n in shape)
                        or stream.tell() + math.prod(shape) * dtype.itemsize != len(data)):
                    raise ValueError("Require bounded plain real numeric matrices with exact payload size")
                matrix = np.load(io.BytesIO(data), allow_pickle=False, max_header_size=4096)
                if not np.isfinite(matrix).all():
                    raise ValueError("Nonfinite matrix is outside the rank contract")
                # The existing native rank endpoint converts to float64. Refuse
                # integer values that that conversion cannot represent exactly.
                if dtype.kind in "iu" and any(int(v) != int(float(v)) for v in matrix.flat):
                    raise ValueError("Integer matrix cannot be represented exactly by native float64")
                name = member.filename[:-4]
                arrays[name] = matrix
                metadata.append({"name": name, "archive_member": member.filename,
                    "member_sha256": sha256(data).hexdigest(), "dtype": dtype.str,
                    "shape": list(shape), "storage_order": "F" if fortran else "C",
                    "npy_version": list(version), "quantity": QUANTITIES[name],
                    "unit": "not_declared_in_retained_source", "semantics": "published_numerical_reference"})
    except (zipfile.BadZipFile, EOFError, OSError, TypeError, OverflowError) as exc:
        raise ValueError("Invalid bounded numerical NPZ") from exc
    n = arrays["A"].shape[0]
    m = arrays["B"].shape[1]
    if any(arrays[k].shape != shape for k, shape in {
            "A": (n, n), "B": (n, m), "Q": (n, n), "R": (m, m)}.items()):
        raise ValueError("CAREX A/B/Q/R dimensions disagree; no repair is performed")
    return arrays, {"source_sha256": sha256(raw).hexdigest(), "source_bytes": len(raw),
                    "archive_member_order": [row["name"] for row in metadata], "arrays": metadata}


_WORKER = r'''
import dataclasses, io, base64, json, math, pathlib, sys
import numpy as np
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from oit import rank_diagnostics
for name, module in tuple(sys.modules.items()):
    if name == 'oit' or name.startswith('oit.'):
        if not pathlib.Path(module.__file__).resolve().is_relative_to(root):
            raise ValueError('Native OIT module left source-only binding')
def wire(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, float) and math.isinf(value):
        return 'positive_infinity' if value > 0 else 'negative_infinity'
    return value
request = json.load(sys.stdin)
rows = []
for item in request['matrices']:
    matrix = np.load(io.BytesIO(base64.b64decode(item['npy_b64'], validate=True)), allow_pickle=False)
    result = rank_diagnostics(matrix, **request['tolerances'])
    rows.append({'name': item['name'], 'diagnostics': {key: wire(value)
                 for key, value in dataclasses.asdict(result).items()}})
print(json.dumps({'rows': rows, 'numpy': np.__version__}, allow_nan=False))
'''


class RankRuntime:
    """Explicit source-only binding; records cannot select executable code."""
    FILES = ("oit/__init__.py", "oit/diagnostics.py")

    def __init__(self, source_root: Path):
        self.root = source_root.resolve()
        self.hashes = self._hashes()

    def _hashes(self):
        hashes = {}
        for relative in self.FILES:
            path = self.root / relative
            if (not path.is_file() or path.is_symlink()
                    or not path.resolve().is_relative_to(self.root)):
                raise ValueError("Missing or unsafe native OIT source")
            hashes[relative] = sha256(path.read_bytes()).hexdigest()
        return hashes

    def identity(self):
        if self._hashes() != self.hashes:
            raise ValueError("Native OIT source changed after binding")
        return {"provider": "oit.rank_diagnostics", "source_files_sha256": deepcopy(self.hashes),
                "binding": "explicit_source_only_snapshot_without_bytecode_or_siblings",
                "bridge_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
                "python_version": platform.python_version(), "numpy": np.__version__,
                "python_sha256": sha256(Path(sys.executable).read_bytes()).hexdigest(),
                "dependency_binary_authentication": "not_established_by_version_metadata"}

    def evaluate(self, matrices: dict, tolerances: dict | None = None) -> dict:
        """Evaluate 1..16 bounded real matrices without assigning domain roles.

        Format-specific adapters own their source mapping and retained lifecycle.
        This shared native call preserves input order and uses no Session samples.
        """
        if type(matrices) is not dict or not 1 <= len(matrices) <= 16:
            raise ValueError("Require one to sixteen explicitly named matrices")
        total = 0
        for name, matrix in matrices.items():
            if (type(name) is not str or not name.strip() or len(name) > 64
                    or not isinstance(matrix, np.ndarray) or matrix.ndim != 2
                    or any(not 1 <= n <= 512 for n in matrix.shape)
                    or matrix.dtype.hasobject or matrix.dtype.fields is not None
                    or matrix.dtype.kind not in "fiu" or matrix.dtype.itemsize > 8
                    or not np.isfinite(matrix).all()):
                raise ValueError("Native rank requires named bounded finite real matrices")
            if matrix.dtype.kind in "iu" and any(int(v) != int(float(v)) for v in matrix.flat):
                raise ValueError("Integer matrix cannot be represented exactly by native float64")
            total += matrix.nbytes
        if total > MAX_EXPANDED:
            raise ValueError("Native rank matrices exceed the total byte budget")
        policy = deepcopy(TOLERANCES if tolerances is None else tolerances)
        keys(policy, set(TOLERANCES))
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
               or key != "rank_atol" and v > 1 for key, v in policy.items()):
            raise ValueError("Require explicit finite nonnegative rank/weak tolerances")
        self.identity()
        rows = []
        for name, matrix in matrices.items():
            data = io.BytesIO()
            np.save(data, matrix, allow_pickle=False)
            rows.append({"name": name, "npy_b64": base64.b64encode(data.getvalue()).decode()})
        with tempfile.TemporaryDirectory(prefix="ciw-carex-source-") as temporary:
            isolated = Path(temporary)
            for relative, expected in self.hashes.items():
                path = self.root / relative
                if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                    raise ValueError("Native OIT source left explicit binding")
                raw = path.read_bytes()
                if sha256(raw).hexdigest() != expected:
                    raise ValueError("Native OIT source changed during snapshot")
                target = isolated / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            self.identity()
            code, raw = _bounded_process([sys.executable, "-I", "-B", "-c", _WORKER, str(isolated)],
                cwd=isolated, timeout=90, limit=16 * 1024 * 1024,
                stdin=encoded({"matrices": rows, "tolerances": policy}))
        if code:
            raise ValueError("Existing native OIT rank consumer refused")
        result = _json(raw)
        if (set(result) != {"rows", "numpy"} or result["numpy"] != np.__version__
                or [row["name"] for row in result["rows"]] != list(matrices)):
            raise ValueError("Native OIT output changed source matrix order or runtime")
        self.identity()
        validate_native(matrices, result, policy)
        return result

    def execute(self, parameters):
        matrices, manifest, _ = _source(parameters)
        result = self.evaluate(matrices, parameters["tolerances"])
        payload = {"schema": "ciw.carex-rank-result.v1", "source_id": parameters["source"]["source_id"],
            "source_record_ref": digest(native_record(parameters["source"])), "manifest": manifest,
            "policy": deepcopy(POLICY), "native": result}
        validate_payload(OPERATION, payload, {}, parameters, {})
        return payload

    def operation(self):
        return Operation(OPERATION, "backend", lambda run, parameters: self.execute(parameters), self.identity)


def retain_carex(workbench, raw: bytes, *, dataset_id: str, recorded_at: str, provenance: dict,
                 dependencies=(), revision=1, parent_source_id=None):
    """Retain exact NPZ bytes and a checked mapping, never simulated observations."""
    _, manifest = parse_npz(raw)
    detached(provenance)
    if not isinstance(provenance, dict) or not provenance:
        raise ValueError("Explicit source provenance is required")
    b64 = base64.b64encode(raw).decode()
    record = artifact(raw, media_type="application/x-npz", producer="CAREX-reference-loader",
        experiment_id=dataset_id, metadata={"payload_encoding": "ordered_base64_chunks",
            "payload_chunks": [b64[i:i + 32768] for i in range(0, len(b64), 32768)],
            "rank_manifest": manifest, "provenance": deepcopy(provenance), "policy": deepcopy(POLICY)})
    envelope = retain_record(record, dataset_id=dataset_id, revision=revision,
        parent_source_id=parent_source_id, recorded_at=recorded_at, data_family="artifact")
    return workbench.add_source({"kind": "data-record", "label": dataset_id,
        "bytes_b64": base64.b64encode(encoded(envelope)).decode(),
        **({"input_source_ids": list(dependencies)} if dependencies else {})})


def source_parameters(workbench, source_id):
    return {"source": deepcopy(workbench.get_source(source_id)), "tolerances": deepcopy(TOLERANCES)}


def _source(parameters):
    from .data_workflow import _source as validate_source
    keys(parameters, {"source", "tolerances"})
    # This first integration deliberately has one explicit numerical policy.
    # A different policy is a new request contract, never an implicit default.
    if parameters["tolerances"] != TOLERANCES or any(type(value) is not float for value in parameters["tolerances"].values()):
        raise ValueError("Require the declared CAREX rank/absolute/weak tolerance policy")
    record = validate_source(parameters["source"])
    if record["schema"] != "ciw.artifact.v1" or record["media_type"] != "application/x-npz":
        raise ValueError("Rank consumer requires a retained NPZ artifact")
    metadata = record["metadata"]
    keys(metadata, {"payload_encoding", "payload_chunks", "rank_manifest", "provenance", "policy"})
    if (metadata["payload_encoding"] != "ordered_base64_chunks" or metadata["policy"] != POLICY
            or type(metadata["payload_chunks"]) is not list
            or not metadata["provenance"] or type(metadata["provenance"]) is not dict):
        raise ValueError("Require explicit byte encoding, quantity semantics and provenance")
    encoded_bytes = "".join(metadata["payload_chunks"])
    raw = base64.b64decode(encoded_bytes, validate=True)
    if base64.b64encode(raw).decode() != encoded_bytes:
        raise ValueError("Require canonical retained source encoding")
    validate_artifact(record, raw)
    matrices, manifest = parse_npz(raw)
    if manifest != metadata["rank_manifest"]:
        raise ValueError("Array mapping contradicts exact source bytes")
    return matrices, manifest, raw


def validate_payload(operation_id, data, run, parameters, selection):
    """Saved correspondence and structure only; no native import, SVD or claim of truth."""
    matrices, manifest, _ = _source(parameters)
    keys(data, {"schema", "source_id", "source_record_ref", "manifest", "policy", "native"})
    if (operation_id != OPERATION or data["schema"] != "ciw.carex-rank-result.v1"
            or data["source_id"] != parameters["source"]["source_id"]
            or data["source_record_ref"] != digest(native_record(parameters["source"]))
            or data["manifest"] != manifest or data["policy"] != POLICY):
        raise ValueError("Rank result differs from its retained source mapping or scope")
    validate_native(matrices, data["native"], parameters["tolerances"])


def validate_native(matrices, native, tolerances):
    """Shared saved-output structure check; never imports OIT or computes SVD.

    Callers retain their own source format, provenance, quantities and authority.
    This checks internal correspondence, not independent numerical correctness.
    """
    keys(tolerances, set(TOLERANCES))
    keys(native, {"rows", "numpy"})
    if (type(native["numpy"]) is not str or not native["numpy"]
            or type(native["rows"]) is not list or len(native["rows"]) != len(matrices)):
        raise ValueError("Malformed native rank output")
    for (name, matrix), row in zip(matrices.items(), native["rows"], strict=True):
        keys(row, {"name", "diagnostics"})
        d = row["diagnostics"]
        keys(d, {"rank", "input_dimension", "full_column_rank", "singular_values", "rank_rtol", "rank_atol",
            "rank_threshold", "weak_rtol", "condition_number", "retained_condition_number", "numerical_nullspace", "weak_directions"})
        singular = d["singular_values"]
        if (row["name"] != name or type(singular) is not list or len(singular) != min(matrix.shape)
                or any(type(v) not in (float, int) or not math.isfinite(v) or v < 0 for v in singular)
                or singular != sorted(singular, reverse=True)
                or any(d[key] != value for key, value in tolerances.items())):
            raise ValueError("Native spectrum/order/tolerance structure mismatch")
        threshold = max(tolerances["rank_atol"], tolerances["rank_rtol"] * singular[0])
        rank = sum(value > threshold for value in singular)
        if (type(d["rank"]) is not int or d["rank"] != rank or d["rank_threshold"] != threshold
                or type(d["input_dimension"]) is not int or d["input_dimension"] != matrix.shape[1]
                or d["full_column_rank"] is not (rank == matrix.shape[1])):
            raise ValueError("Native rank classification contradicts retained spectrum")
        weak_count = sum(threshold < value <= tolerances["weak_rtol"] * singular[0] for value in singular)
        for key, width in (("numerical_nullspace", matrix.shape[1] - rank), ("weak_directions", weak_count)):
            directions = d[key]
            if (type(directions) is not list or len(directions) != matrix.shape[1]
                    or any(type(line) is not list or len(line) != width
                        or any(type(v) not in (int, float) or not math.isfinite(v) for v in line) for line in directions)):
                raise ValueError("Native direction dimensions/values changed")
        retained = singular[0] / singular[rank - 1] if rank else math.inf
        for key, expected in (("retained_condition_number", retained),
                              ("condition_number", retained if rank == matrix.shape[1] else math.inf)):
            value = d[key]
            if (math.isinf(expected) and value != "positive_infinity"
                    or math.isfinite(expected) and (type(value) not in (int, float)
                        or not math.isclose(value, expected, rel_tol=1e-12))):
                raise ValueError("Native condition contradicts retained spectrum")


def validate_live_dependencies(operation_id, parameters, sources):
    if operation_id != OPERATION:
        return
    source = parameters.get("source")
    if (type(source) is not dict or type(source.get("source_id")) is not str
            or source["source_id"] not in sources
            or sources[source["source_id"]] != source):
        raise ValueError("Rank consumer requires its source unchanged in this Workbench")
    _source(parameters)


def validate_saved_dependencies(results, sources):
    for result in results.values():
        if result["operation_id"] == OPERATION:
            validate_live_dependencies(OPERATION, result["parameters"], sources)
