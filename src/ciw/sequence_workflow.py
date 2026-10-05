"""Opt-in sequence workloads using NET's existing Session and OperationRegistry.

Inspection registers only fixed, trusted payload validators. Retained requests
cannot name providers, import modules, evaluate programs or grant authority.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import platform
import tempfile
import threading
import uuid

from . import sequence_kinetics as kernel
from .core.identities import canonical_json, content_identity, evidence_id, validate_evidence_identity

ANALYZE = "sequence.markov-analyze.v1"
EVOLVE = "sequence.binary-evolve.v1"
FRAME = "sequence.lexicographic-block.v1"
MAX_WORKSPACE_BYTES = 32 * 1024 * 1024
_SCHEMAS_REGISTERED = False
_SCHEMA_LOCK = threading.Lock()


def make_source(request: dict) -> dict:
    request = kernel.validate_request(request)
    manifest = {"manifest_schema": "ciw.instrument-manifest.v1", "instrument_id": "sequence-declaration.v1",
                "version": "1", "role": "declared_sequence_distribution", "inputs": [], "outputs": ["run.v1"],
                "units": {"declaration": "1"}, "frames": [FRAME],
                "sampling": {"kind": "one_declaration", "time_semantics": "synthetic_selection_not_kinetic_time"},
                "normalization": {"probabilities": "no_clipping_or_renormalization"},
                "supported_operations": [ANALYZE, EVOLVE], "determinism": {},
                "tolerance_policy": {"probability_and_overlap_atol": kernel.TOLERANCE},
                "calibration_requirements": {"physical_validation": "not_established"}}
    source = {"run_schema": "run.v1", "run_id": "run-sequence-" + content_identity(request)[7:23],
              "instrument": manifest["instrument_id"],
              "metadata": {"duration_s": 1.0, "sample_count": 1, "sample_rate_hz": None,
                           "coordinate_frame": FRAME, "manifest": manifest, "sequence_request": request,
                           "provenance": {"source": request["source_kind"],
                                          "generator": "ciw.sequence_workflow.make_source", "generator_version": 1}},
              "time_s": [0.0], "channels": {"declaration": {"unit": "1", "values": [1.0]}}, "render": {}}
    source["evidence_id"] = evidence_id(source)
    return source


def source_request(source: dict) -> dict:
    kernel.bounded_json(source, kernel.MAX_RESULT_BYTES)
    validate_evidence_identity(source)
    request = kernel.validate_request(source["metadata"]["sequence_request"])
    if canonical_json(source) != canonical_json(make_source(request)):
        raise ValueError("Source differs from its exact sequence declaration")
    return request


def runtime_identity() -> dict:
    # Fingerprint actually executed modules and the unmodified identity encoder.
    from .core import identities
    paths = [Path(kernel.__file__), Path(__file__), Path(identities.__file__)]
    return {"provider": "ciw.sequence-kinetics", "version": "1",
            "source_normalization": "utf8_lf", "python": platform.python_version(),
            "source_hashes": {p.name: hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                              for p in paths},
            "scope": "sequence_statistics_and_independent_binary_sites_only",
            "native_gambit": "not_loaded"}


def _analyze(source: dict, parameters: dict) -> dict:
    kernel.exact_keys(parameters, set())
    return kernel.analyze(source_request(source))


def _evolve(source: dict, parameters: dict) -> dict:
    kernel.exact_keys(parameters, set())
    return kernel.evolve_binary(source_request(source))


def operations():
    from .operations.registry import Operation
    return [Operation(ANALYZE, "backend", _analyze, runtime_identity),
            Operation(EVOLVE, "backend", _evolve, runtime_identity)]


def register_schemas() -> None:
    """Trusted opt-in, idempotent process setup, never triggered by saved content."""
    global _SCHEMAS_REGISTERED
    with _SCHEMA_LOCK:
        if _SCHEMAS_REGISTERED:
            return
        from .operations.schemas import register_payload_validator
        for operation in operations():
            register_payload_validator(operation.operation_id, validate_payload, role=operation.role)
        _SCHEMAS_REGISTERED = True


def registry():
    from .operations.registry import OperationRegistry
    register_schemas()
    result = OperationRegistry()
    for operation in operations():
        result.register(operation)
    return result


def _array(value, length: int, lower: float = 0.0, upper: float = 1.0) -> None:
    if type(value) is not list or len(value) != length:
        raise ValueError("Invalid retained sequence array shape")
    for x in value:
        kernel.finite_number(x, lower, upper)


def validate_payload(operation_id: str, data: dict, source: dict, parameters: dict, selection: dict) -> None:
    """Static shape, binding and domain checks; NEVER rerun either operation.

    A coherently edited numerical result can still pass structural inspection;
    new execution and comparison are separate from reopening a retained report.
    """
    request = source_request(source)
    kernel.exact_keys(parameters, set())
    kernel.bounded_json(data, kernel.MAX_RESULT_BYTES)
    if selection.get("channel") != "declaration" or selection.get("interval_s") != [0.0, 1.0]:
        raise ValueError("Sequence operation requires the complete declaration selection")
    if (data.get("request_ref") != content_identity(request) or data.get("authority") != kernel.AUTHORITY
            or data.get("source_kind") != request["source_kind"]):
        raise ValueError("Sequence result source or authority mismatch")
    n, k = len(request["alphabet"]), request["block_length"]
    count, ctx_count = n ** k, n ** (k - 1)
    shared = {"schema", "request_ref", "source_kind", "status", "authority"}
    if operation_id == ANALYZE:
        kernel.exact_keys(data, shared | {"alphabet", "block_length", "state_count", "probability_sum",
            "marginal_tolerance", "max_prefix_suffix_difference", "prefix_masses", "suffix_masses",
            "contexts", "conditional_probabilities", "zero_mass_context_indices", "transfer_orientation",
            "transfer_edges", "block_entropy_nats", "conditional_entropy_nats_per_symbol",
            "markov_entropy_rate_nats_per_symbol", "queries", "closure"})
        if (data["schema"] != "ciw.sequence-markov-analysis.v1" or data["alphabet"] != request["alphabet"]
                or type(data["block_length"]) is not int or type(data["state_count"]) is not int
                or data["block_length"] != k or data["state_count"] != count
                or data["status"] not in {"PASS", "REFUSE"} or data["marginal_tolerance"] != kernel.TOLERANCE):
            raise ValueError("Invalid sequence analysis metadata")
        for field in ("prefix_masses", "suffix_masses"):
            _array(data[field], ctx_count, 0, 1 + kernel.TOLERANCE)
        for field in ("contexts", "conditional_probabilities"):
            if type(data[field]) is not list or len(data[field]) != ctx_count:
                raise ValueError("Invalid context table shape")
        for i, row in enumerate(data["conditional_probabilities"]):
            if data["prefix_masses"][i] == 0:
                if row is not None:
                    raise ValueError("A zero-mass context must remain undefined")
            else:
                _array(row, n)
        expected_zero = [i for i, p in enumerate(data["prefix_masses"]) if p == 0]
        if data["zero_mass_context_indices"] != expected_zero:
            raise ValueError("Invalid zero-context index list")
        expected_closure = {"order": k - 1, "longer_sequences_authorized": request["allow_markov_closure"],
                            "status": "model_assumption_not_experimental_validation"}
        if data["closure"] != expected_closure:
            raise ValueError("Closure declaration changed")
        import itertools
        import math
        expected_contexts = [list(xs) for xs in itertools.product(request["alphabet"], repeat=k - 1)]
        if data["contexts"] != expected_contexts:
            raise ValueError("Retained context symbol order differs")
        mass = kernel.finite_number(data["probability_sum"], 0, 1 + kernel.TOLERANCE)
        delta = kernel.finite_number(data["max_prefix_suffix_difference"], 0, 1 + kernel.TOLERANCE)
        if abs(mass - 1) > kernel.TOLERANCE or data["status"] != ("PASS" if delta <= kernel.TOLERANCE else "REFUSE"):
            raise ValueError("Retained probability or marginal status is contradictory")
        kernel.finite_number(data["block_entropy_nats"], -kernel.TOLERANCE, k * math.log(n) + kernel.TOLERANCE)
        kernel.finite_number(data["conditional_entropy_nats_per_symbol"], -kernel.TOLERANCE, math.log(n) + kernel.TOLERANCE)
        if data["status"] == "REFUSE":
            if data["markov_entropy_rate_nats_per_symbol"] is not None:
                raise ValueError("Incompatible marginals cannot establish a Markov entropy rate")
        elif data["markov_entropy_rate_nats_per_symbol"] != data["conditional_entropy_nats_per_symbol"]:
            raise ValueError("Retained entropy declarations disagree")
        if data["transfer_orientation"] != "edges_from_context_to_context; upstream_matrix_is_target_source":
            raise ValueError("Transfer orientation changed")
        if type(data["transfer_edges"]) is not list or len(data["transfer_edges"]) > count:
            raise ValueError("Invalid sparse transfer size")
        seen_edges = set()
        for edge in data["transfer_edges"]:
            kernel.exact_keys(edge, {"from_context", "to_context", "symbol", "probability"})
            for name in ("from_context", "to_context"):
                if type(edge[name]) is not int or not 0 <= edge[name] < ctx_count:
                    raise ValueError("Invalid transfer context index")
            if edge["symbol"] not in request["alphabet"]:
                raise ValueError("Invalid transfer symbol")
            key = (edge["from_context"], edge["symbol"])
            if key in seen_edges or data["prefix_masses"][edge["from_context"]] == 0:
                raise ValueError("Duplicate or undefined-context transfer")
            seen_edges.add(key)
            symbol = request["alphabet"].index(edge["symbol"])
            if edge["to_context"] != (edge["from_context"] * n + symbol) % ctx_count:
                raise ValueError("Transfer breaks context overlap")
            kernel.finite_number(edge["probability"], 0, 1)
        queries = request["queries"] if data["status"] == "PASS" else []
        if type(data["queries"]) is not list or len(data["queries"]) != len(queries):
            raise ValueError("Query results do not match retained request")
        for item, seq in zip(data["queries"], queries):
            kernel.exact_keys(item, {"sequence", "scope", "status", "probability", "log_probability_nats"})
            if item["sequence"] != seq or item["status"] not in {"PASS", "EXPAND", "UNDERFLOW", "UNDEFINED_CONTEXT"}:
                raise ValueError("Invalid query result")
            scope = "finite_order_markov_closure" if len(seq) > k else "retained_block_marginal"
            if item["scope"] != scope:
                raise ValueError("Query evidence scope differs")
            if item["status"] == "PASS" and item["probability"] is None:
                raise ValueError("Passing query has no probability")
            logp = item["log_probability_nats"]
            if logp is not None:
                kernel.finite_number(logp, -1e6, kernel.TOLERANCE)
            if item["status"] == "EXPAND" and logp is not None:
                raise ValueError("Unperformed query cannot publish log probability")
            if item["status"] == "UNDERFLOW" and logp is None:
                raise ValueError("Underflow must retain finite log probability")
            if item["probability"] is not None:
                kernel.finite_number(item["probability"], 0, 1 + kernel.TOLERANCE)
            if item["status"] != "PASS" and item["probability"] is not None:
                raise ValueError("Unavailable query must not publish a probability")
            if len(seq) > k and not request["allow_markov_closure"] and item["status"] != "EXPAND":
                raise ValueError("Undeclared closure cannot produce a long sequence prediction")
    elif operation_id == EVOLVE:
        kernel.exact_keys(data, shared | {"model", "method", "initial_derivative_per_s", "samples", "scope"})
        if (data["schema"] != "ciw.sequence-binary-evolution.v1" or data["status"] != "PASS"
                or request["evolution"] is None or canonical_json(data["model"]) != canonical_json(request["evolution"])
                or data["method"] != "analytic_two_state_transition_tensor_product_no_time_stepping"
                or data["scope"] != "independent_sites_only; no_interacting_tapes_or_copolymerization_prediction"):
            raise ValueError("Invalid independent-site result metadata")
        _array(data["initial_derivative_per_s"], count, -1e8, 1e8)
        if type(data["samples"]) is not list or len(data["samples"]) != len(request["evolution"]["time_s"]):
            raise ValueError("Invalid retained kinetic time axis")
        for item, t in zip(data["samples"], request["evolution"]["time_s"]):
            kernel.exact_keys(item, {"time_s", "probabilities", "probability_sum", "max_prefix_suffix_difference"})
            if item["time_s"] != t:
                raise ValueError("Retained kinetic time differs")
            _array(item["probabilities"], count, 0, 1 + kernel.TOLERANCE)
            kernel.finite_number(item["time_s"], 0, 1e6)
            mass = kernel.finite_number(item["probability_sum"], 0, 1 + kernel.TOLERANCE)
            kernel.finite_number(item["max_prefix_suffix_difference"], 0, kernel.TOLERANCE)
            if abs(mass - 1) > kernel.TOLERANCE:
                raise ValueError("Retained evolution mass is not normalized")
    else:
        raise ValueError("Unsupported sequence operation")


def _execute(session, operation_id: str) -> dict:
    response = session.handle({"protocol_version": 1, "request_id": uuid.uuid4().hex,
                               "type": "operation.execute", "payload": {"operation_id": operation_id, "parameters": {}}})
    if response["type"] != "response":
        raise ValueError(response["payload"]["message"])
    return response["payload"]


def run(request: dict, destination: Path) -> dict:
    from .control_contracts import save_new
    from .session import Session
    source = make_source(request)
    bound = registry()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    save_new(destination / "request.json", source_request(source))
    session = Session(source, destination, operations=bound)
    try:
        result = _execute(session, ANALYZE)
        if (result["status"] == "completed" and result["result"]["data"]["status"] == "PASS"
                and request["evolution"] is not None):
            _execute(session, EVOLVE)
    finally:
        session.save_workspace(destination / "workspace.json")
    return inspect(destination)


def _read(destination: Path):
    from .control_contracts import load
    from .session import Session
    register_schemas()
    destination = Path(destination)
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError("Require a regular sequence bundle directory")
    for name, bound in (("workspace.json", MAX_WORKSPACE_BYTES), ("request.json", kernel.MAX_BYTES)):
        path = destination / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > bound:
            raise ValueError("Require bounded regular sequence bundle files")
    with tempfile.TemporaryDirectory(prefix="sequence-inspect-") as directory:
        session = Session.from_workspace(destination / "workspace.json", Path(directory))
    request = source_request(session.run)
    if canonical_json(kernel.validate_request(load(destination / "request.json"))) != canonical_json(request):
        raise ValueError("Request sidecar differs from retained source evidence")
    attempts = list(session.executions.values())
    expected = [ANALYZE, EVOLVE] if request["evolution"] is not None else [ANALYZE]
    observed = [x["operation_id"] for x in attempts]
    if not attempts or observed != expected[:len(observed)] or len(observed) > len(expected):
        raise ValueError("Invalid sequence execution ordering or unrelated operation")
    first = attempts[0]
    if first["status"] == "refused":
        if len(attempts) != 1 or session.results:
            raise ValueError("Refused analysis cannot have downstream results")
    else:
        analysis = session.results[first["result_id"]]["data"]
        required = [ANALYZE] if analysis["status"] == "REFUSE" else expected
        if observed != required:
            raise ValueError("Missing or inappropriate dependent sequence execution")
    if any(x["operation_id"] not in {ANALYZE, EVOLVE} for x in session.results.values()):
        raise ValueError("Unrelated retained sequence result")
    return session


def inspect(destination: Path) -> dict:
    session = _read(destination)
    attempts = list(session.executions.values())
    data = [x["data"] for x in session.results.values()]
    status = "REFUSE" if any(x["status"] == "refused" for x in attempts) or any(x["status"] == "REFUSE" for x in data) else "PASS"
    return {"schema": "ciw.sequence-workflow-summary.v1", "status": status,
            "evidence_id": session.run["evidence_id"], "executions": attempts,
            "results": list(session.results.values()), "reproducibility": "not_established_by_inspection",
            "authority": deepcopy(kernel.AUTHORITY)}


def replay(destination: Path, new_destination: Path) -> dict:
    original = _read(destination)
    return run(source_request(original.run), new_destination)
