"""Retain published CAREX matrices and qualify the existing OIT rank endpoint.

No Riccati solve, covariance inference, discretization, observability claim, or
new numerical provider is introduced. Corpus bytes stay in operator-owned output.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path

import numpy as np

from ciw.control_contracts import artifact, validate_artifact
from ciw.data_sources import native_record, retain_record

from ciw import data_rank_bridge as bridge
from ciw.data_rank_bridge import (MAX_ARCHIVE, POLICY, TOLERANCES, RankRuntime,
                                  encoded, parse_npz, retain_carex, source_parameters)

SOURCE_SHA256 = {
    6: "1b2a0736b541ebf5c4b9b4c00d6dab281e73c9fb9913c6e2581a781b37b602f9",
    15: "13f3e1491a876bbf59d7ea10ad29c1f9b5996a2ab99216f31d5bfcd659012c1e",
    18: "59f839467f2752b7df6fb6d4094396edd32a5929b764f7ffa1e6666431e6cac6",
    19: "38e8fc7b041df0b23d7e5ca15ead1a065e6467611ef9a848cc7db93f80adfd87",
    20: "14e222d34a7118c7284a1675c6feceee77b84df951a5c6ba2a5ee9ff3054fa1d",
}
SOURCE_URL = "https://github.com/scipy/scipy/blob/v1.17.0/scipy/linalg/tests/test_solvers.py"
DEFINITION_SHA256 = "f244a6fd63a1ca789a2f735699d292fad4ddf4ad0805d0f58b29c08eb47c8789"
ORACLE = {"oracle": "scipy.linalg.svd_LAPACK_gesvd_vs_native_numpy_gesdd",
          "oracle_spectrum_scaled_atol": 2e-13, "oracle_spectrum_rtol": 2e-12}


def checked_source(path: Path, expected: str, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Require original regular acquired source file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit or sha256(raw).hexdigest() != expected:
        raise ValueError("Source SHA256 or byte budget differs from reviewed acquisition")
    return raw


def check_reference(matrices: dict, result: dict, tolerances: dict | None = None) -> dict:
    """Independent existing QR-iteration SVD, plus residual and basis checks.

    This is a qualification-only oracle, not a new numerical provider. The two
    SVD paths share floating-point/BLAS infrastructure; neither is physical truth.
    """
    import scipy
    from scipy.linalg import svd
    tolerances = TOLERANCES if tolerances is None else tolerances
    if [row["name"] for row in result["rows"]] != list(matrices):
        raise ValueError("Native output order changed")
    checks = []
    for name, row in zip(matrices, result["rows"], strict=True):
        matrix = np.asarray(matrices[name], dtype=np.float64)
        scale = float(np.max(np.abs(matrix)))
        normalized = matrix / scale if scale else matrix
        singular = svd(normalized, compute_uv=False, lapack_driver="gesvd", check_finite=True)
        threshold = max(tolerances["rank_atol"] / scale if scale else
                        (math.inf if tolerances["rank_atol"] else 0.0),
                        tolerances["rank_rtol"] * float(singular[0]))
        rank = int(sum(singular > threshold))
        d = row["diagnostics"]
        expected_fields = {"rank", "input_dimension", "full_column_rank", "singular_values", "rank_rtol",
            "rank_atol", "rank_threshold", "weak_rtol", "condition_number", "retained_condition_number",
            "numerical_nullspace", "weak_directions"}
        if (set(d) != expected_fields or type(d["rank"]) is not int or d["rank"] != rank
                or d["input_dimension"] != matrix.shape[1]
                or d["full_column_rank"] is not (rank == matrix.shape[1])
                or any(d[k] != v for k, v in tolerances.items())):
            raise ValueError("Native rank disagrees with independent SVD oracle")
        actual = np.asarray(d["singular_values"])
        if (actual.shape != singular.shape or not np.isfinite(actual).all()
                or not np.allclose(actual / scale if scale else actual, singular,
                    atol=ORACLE["oracle_spectrum_scaled_atol"], rtol=ORACLE["oracle_spectrum_rtol"])):
            raise ValueError("Native spectrum disagrees with independent SVD oracle")
        expected_threshold = max(tolerances["rank_atol"], tolerances["rank_rtol"] * float(actual[0]))
        if d["rank_threshold"] != expected_threshold:
            raise ValueError("Native threshold contradicts declared policy")
        nullspace = np.asarray(d["numerical_nullspace"], dtype=float)
        weak = np.asarray(d["weak_directions"], dtype=float)
        weak_count = int(sum((singular > threshold) & (singular <= tolerances["weak_rtol"] * singular[0])))
        if (nullspace.shape != (matrix.shape[1], matrix.shape[1] - rank)
                or weak.shape != (matrix.shape[1], weak_count)
                or not np.isfinite(nullspace).all() or not np.isfinite(weak).all()):
            raise ValueError("Native right-direction dimensions changed")
        error = 0.0
        if nullspace.shape[1]:
            error = float(np.linalg.norm(normalized @ nullspace, ord=2))
            if (error > threshold + 2e-12 * max(1.0, float(singular[0]))
                    or not np.allclose(nullspace.T @ nullspace, np.eye(nullspace.shape[1]), atol=2e-12, rtol=0)):
                raise ValueError("Native nullspace failed independent residual/basis checks")
        retained_condition = float(singular[0] / singular[rank - 1]) if rank else math.inf
        for key, expected in (("retained_condition_number", retained_condition),
                              ("condition_number", retained_condition if rank == matrix.shape[1] else math.inf)):
            value = d[key]
            if (math.isinf(expected) and value != "positive_infinity"
                    or math.isfinite(expected) and (type(value) not in (int, float)
                        or not math.isclose(value, expected, rel_tol=2e-5, abs_tol=1e-12))):
                raise ValueError("Native condition measure disagrees with independent oracle")
        checks.append({"name": name, "rank": rank, "input_dimension": matrix.shape[1],
            "native_condition_number": d["condition_number"], "matrix_max_abs_scale": scale,
            "normalized_rank_threshold": threshold if math.isfinite(threshold) else "positive_infinity",
            "normalized_nullspace_residual": error, "status": "PASS"})
    return {"status": "PASS", "scipy_version": scipy.__version__, "checks": checks}


def retain_payload(workbench, payload: bytes, label: str, stamp: str, *, dependencies=(), media="application/json"):
    # Artifact metadata has a per-string bound: use ordered base64 chunks, not
    # truncation, external filenames, or a hidden unretained corpus dependency.
    b64 = base64.b64encode(payload).decode()
    native = artifact(payload, media_type=media, producer="CAREX-rank-qualification", experiment_id=label,
        metadata={"payload_encoding": "ordered_base64_chunks",
                  "payload_chunks": [b64[i:i + 32768] for i in range(0, len(b64), 32768)]})
    validate_artifact(native, payload)
    envelope = retain_record(native, dataset_id=label, revision=1, parent_source_id=None,
                             recorded_at=stamp, data_family="artifact")
    return workbench.add_source({"kind": "data-record", "label": label,
        "bytes_b64": base64.b64encode(encoded(envelope)).decode(),
        **({"input_source_ids": list(dependencies)} if dependencies else {})})["source_id"]


def recover_payload(workbench, source_id):
    native = native_record(workbench.get_source(source_id))
    metadata = native["metadata"]
    if set(metadata) != {"payload_encoding", "payload_chunks"} or metadata["payload_encoding"] != "ordered_base64_chunks":
        raise ValueError("Changed retained artifact payload encoding")
    b64 = "".join(metadata["payload_chunks"])
    raw = base64.b64decode(b64, validate=True)
    if base64.b64encode(raw).decode() != b64:
        raise ValueError("Changed retained artifact byte encoding")
    validate_artifact(native, raw)
    return raw


def qualify(source_dir: Path, source_root: Path, destination: Path) -> dict:
    definition = checked_source(source_dir / "test_solvers.py", DEFINITION_SHA256, 65536)
    sources = []
    for case, expected in SOURCE_SHA256.items():
        raw = checked_source(source_dir / f"carex_{case}_data.npz", expected, MAX_ARCHIVE)
        arrays, manifest = parse_npz(raw)
        sources.append((case, raw, arrays, manifest))
    runtime = RankRuntime(source_root)
    identity = runtime.identity()
    stamp = datetime.now(timezone.utc).isoformat()
    destination.mkdir(parents=True, exist_ok=False)
    reports = []
    from ciw.instruments import make_demo_run
    from ciw.operations.registry import OperationRegistry
    from ciw.session import Session
    for case, raw, arrays, manifest in sources:
        prefix = f"carex-{case}"
        case_dir = destination / prefix
        registry = OperationRegistry()
        registry.register(runtime.operation())
        # Session currently requires a recording. Its existing synthetic demo
        # is only a lifecycle harness; no time/channel/sample enters rank.
        session = Session(make_demo_run(), case_dir / "live", operations=registry)
        definition_id = retain_payload(session.workbench, definition, prefix + "/source-definition", stamp, media="text/x-python")
        provenance = {"source_url": SOURCE_URL, "distribution": "scipy", "distribution_version": "1.17.0",
            "acquisition": "exact_installed_distribution_bytes", "definition_sha256": DEFINITION_SHA256}
        descriptor = retain_carex(session.workbench, raw, dataset_id=prefix, recorded_at=stamp,
                                 provenance=provenance, dependencies=[definition_id])
        source_id = descriptor["source_id"]
        def execute(current):
            response = current.handle({"protocol_version": 1, "request_id": "carex-rank-qualification",
                "type": "operation.execute", "payload": {"operation_id": bridge.OPERATION,
                    "parameters": source_parameters(current.workbench, source_id)}})
            if response["type"] != "response" or response["payload"]["status"] != "completed":
                raise ValueError(f"Native rank Session execution refused: {response}")
            return response["payload"]
        first = execute(session)
        result = first["result"]["data"]["native"]
        comparison = check_reference(arrays, result)
        oracle_id = retain_payload(session.workbench, encoded(comparison), prefix + "/oracle", stamp, dependencies=[source_id])
        workspace = session.save_workspace(case_dir / "workspace.json")
        restored = Session.from_workspace(workspace, output_dir=case_dir / "offline")
        if restored.results != session.results or restored.workbench.serialize() != session.workbench.serialize():
            raise ValueError("Offline Session restoration changed retained evidence")
        recovered_arrays, recovered_manifest, retained_raw = bridge._source(source_parameters(restored.workbench, source_id))
        if (retained_raw != raw or recovered_manifest != manifest
                or recover_payload(restored.workbench, definition_id) != definition
                or recover_payload(restored.workbench, oracle_id) != encoded(comparison)):
            raise ValueError("Retained source/mapping/oracle changed on offline restore")
        restored.operations = registry
        replay = execute(restored)
        if (replay["result"]["data"] != first["result"]["data"]
                or replay["execution"]["execution_id"] == first["execution"]["execution_id"]):
            raise ValueError("Fresh native replay changed result or reused execution identity")
        check_reference(recovered_arrays, replay["result"]["data"]["native"])
        restored.save_workspace(case_dir / "replayed-workspace.json")
        (case_dir / "source.npz").write_bytes(raw)
        (case_dir / "qualification.json").write_bytes(encoded({"mapping": manifest, "comparison": comparison,
            "source_id": source_id, "result_id": first["result"]["result_id"], "oracle_id": oracle_id}))
        reports.append({"case": case, "source_sha256": SOURCE_SHA256[case], "array_count": len(arrays),
            "source_id": source_id, "result_id": first["result"]["result_id"], "comparison": comparison,
            "native_endpoint_calls": 2 * len(arrays), "native_processes": 2, "session_executions": 2,
            "execution_ids": [first["execution"]["execution_id"], replay["execution"]["execution_id"]],
            "offline_restore": "exact_match", "fresh_process_replay": "exact_match"})
    if runtime.identity() != identity:
        raise ValueError("Native runtime drifted during qualification")
    report = {"schema": "ciw.academic-carex-rank-qualification.v1", "status": "PASS",
        "recorded_at": stamp, "policy": POLICY, "runtime": identity, "cases": reports,
        "published_arrays": sum(row["array_count"] for row in reports),
        "native_endpoint_calls": sum(row["native_endpoint_calls"] for row in reports),
        "session_executions": sum(row["session_executions"] for row in reports),
        "session_recording": "existing_synthetic_demo_lifecycle_harness_not_consumed_by_rank",
        "retention": "existing_data_record_artifacts_Workbench_and_Session", "oracle": ORACLE,
        "synthetic_source_substitution": False, "mapping_script_sha256": sha256(Path(__file__).read_bytes()).hexdigest()}
    (destination / "qualification.json").write_bytes(encoded(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--carex-source-dir", type=Path, required=True)
    parser.add_argument("--oit-source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = qualify(args.carex_source_dir, args.oit_source_root, args.output_dir)
    print(json.dumps({key: report[key] for key in ("status", "published_arrays", "native_endpoint_calls", "session_executions")}))


if __name__ == "__main__":
    main()
