"""Graph-first application of NET's existing scientific-system lifecycle.

The native system specification is authoritative. The System Board is a derived
projection, not a second compiler, graph runner, state store or physics engine.
This profile deliberately supports only the existing rod reference family.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path
from threading import RLock

from .control_contracts import keys, detached, save_new
from .operations.runner import seal
from .core.identities import canonical_json, content_identity

from .session import Session, loads_json
from .system_board import board_from_spec
from .system_spec import compile_spec, validate_spec
from .system_workflow import run_specification, source_run
from .visual_board import default_view
from .visual_board_server import BoardWorkbench

PROFILE = "scientific-system-lab.v1"
MAX_SOURCE_BYTES = 64 * 1024
MAX_CELLS = 64
MAX_STEPS = 400
# These are bounded authoring ranges, not material validity or calibration claims.
RANGES = {
    "k_w_per_m_k": (1e-6, 1e4), "density_kg_per_m3": (1e-6, 1e6),
    "heat_capacity_j_per_kg_k": (1e-6, 1e8), "youngs_modulus_pa": (1.0, 1e14),
    "expansion_per_k": (0.0, 1.0), "force_n": (-1e8, 1e8),
    "temperature_bias_k": (-1e3, 1e3), "displacement_bias_m": (-1.0, 1.0),
    "temperature_std_k": (0.0, 1e3), "displacement_std_m": (0.0, 1.0),
    "seed": (0, 2**32 - 1),
}
CLAIMS = {"view_only": True, "synthetic": True, "physical_validation_status": "not_assessed",
          "canonical_admission": False, "physical_authority": False}


def _bytes(value):
    return canonical_json(value).encode("utf-8")


def _view_record(kind, **fields):
    return seal(detached({"schema": "ciw." + kind + ".v1", **fields}))


def checked_spec(raw: bytes) -> dict:
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_SOURCE_BYTES:
        raise ValueError("Lab requires 1..65536 exact UTF-8 specification bytes")
    spec = validate_spec(loads_json(raw.decode("utf-8")))
    if spec["discretization"]["cells"] > MAX_CELLS or spec["clock"]["duration"] / spec["clock"]["dt"] > MAX_STEPS:
        raise ValueError("Interactive Lab is bounded to 64 cells and 400 timesteps")
    compile_spec(spec)  # Same compiler as headless execution; no provider dispatch.
    return spec


def _parameter(name: str, value, spec: dict) -> dict:
    unit = value["unit"] if type(value) is dict else None
    scalar = value["value"] if type(value) is dict else value
    kind = "STRING" if type(scalar) is str else "INTEGER" if name == "seed" else "NUMBER"
    bound = RANGES.get(name)
    if name.endswith("temperature_k"):
        bound = (spec["validity"]["temperature_min_K"], spec["validity"]["temperature_max_K"])
    if name == "position_m":
        thermal = next(n for n in spec["nodes"] if n["model_id"] == "thermal.rod-fv.v1")
        bound = (0.0, thermal["parameters"]["length_m"]["value"])
    # A source outside our smaller edit envelope is inspectable, not repaired.
    exposed = bound is not None and bound[0] <= scalar <= bound[1]
    domain = {"kind": "RANGE" if exposed else "FIXED", "minimum": bound[0] if exposed else None,
              "maximum": bound[1] if exposed else None, "values": None, "log_base": None}
    return {"type": kind, "value": scalar, "unit": unit, "domain": domain, "exposed": exposed}


def project_board(specification: dict) -> dict:
    """Loss-declared System Board projection of a supported native specification."""
    spec = checked_spec(_bytes(specification))
    ref = content_identity(spec)
    scale = {"length_m": None, "time_s": spec["clock"]["duration"], "energy_j": None,
             "label": "declared one-dimensional scientific reference"}
    nodes = []
    for native in spec["nodes"]:
        nodes.append({"node_id": native["node_id"], "kind": "MODEL", "label": native["model_id"],
            "semantic_capability": None, "sockets": {"inputs": [], "outputs": []},
            "parameters": {k: _parameter(k, v, spec) for k, v in native["parameters"].items()},
            "resources": [], "inspection": True, "scale": deepcopy(scale), "provenance_refs": [ref],
            "authority_requirements": [], "invariants": ["Native system specification owns physical meaning"],
            "validity_conditions": list(spec["assumptions"])})
    edges = [{"edge_id": c["coupling_id"], "kind": "SEMANTIC",
              "source_node_id": c["source"]["node"], "target_node_id": c["target"]["node"],
              "source_socket": None, "target_socket": None,
              "relation": c["method"], "metadata": deepcopy(c)} for c in spec["couplings"]]
    return board_from_spec({"board_id": "lab-" + ref[7:31], "title": "Industrial Lab | " + spec["configuration_id"],
        "model_id": PROFILE, "nodes": nodes, "edges": edges, "groups": [],
        "board_invariants": ["Canvas layout is not physical placement", "Coupling declarations are not execution permissions",
                             "Synthetic observations are not physical measurements"],
        "notes": "Derived from " + ref + ". Full geometry, clock, tolerances and coupling semantics remain in the native specification. No CAD inference."})


def edit_specification(specification: dict, board: dict, request: dict) -> tuple[dict, dict]:
    """Interpret the existing parameter-request notation through the native compiler.

    This does not broaden the general Board compiler's OPERATION-only edit path.
    Shared geometry, topology, units, clocks and model identifiers are not editable
    through this bounded profile.
    """
    keys(request, {"schema", "base_board_ref", "node_id", "parameter", "replacement"})
    spec = checked_spec(_bytes(specification))
    if board != project_board(spec) or request["base_board_ref"] != board["record_digest"]:
        raise ValueError("Lab edit targets a stale or different native specification/Board")
    if request["schema"] != "ciw.board-parameter-edit.v1":
        raise ValueError("Require an explicit Board parameter edit")
    if type(request["node_id"]) is not str or type(request["parameter"]) is not str:
        raise ValueError("Require exact node and parameter identities")
    node = next((n for n in board["nodes"] if n["node_id"] == request["node_id"]), None)
    parameter = node["parameters"].get(request["parameter"]) if node else None
    if parameter is None or not parameter["exposed"]:
        raise ValueError("Parameter is not exposed by this Lab profile")
    replacement = request["replacement"]
    if type(replacement) not in (int, float) or (parameter["type"] == "INTEGER" and type(replacement) is not int):
        raise ValueError("Replacement has the wrong declared numeric type")
    if not parameter["domain"]["minimum"] <= replacement <= parameter["domain"]["maximum"]:
        raise ValueError("Replacement exceeds the declared authoring range")
    if replacement == parameter["value"]:
        raise ValueError("Replacement must differ from the baseline value")
    native = next(n for n in spec["nodes"] if n["node_id"] == request["node_id"])
    if type(native["parameters"][request["parameter"]]) is dict:
        native["parameters"][request["parameter"]]["value"] = replacement
    else:
        native["parameters"][request["parameter"]] = replacement
    spec["configuration_id"] = "lab-candidate-" + content_identity(request)[7:31]
    candidate = checked_spec(_bytes(spec))
    # Highlight dependency reachability only; all three coupled models are rerun.
    closure = {request["node_id"]}
    for _ in candidate["nodes"]:
        for c in candidate["couplings"]:
            if c["source"]["node"] in closure:
                closure.add(c["target"]["node"])
    preview = _view_record("industrial-lab-preview", request=deepcopy(request),
        base_board_ref=board["record_digest"], candidate_board=project_board(candidate),
        specification=candidate, compilation=compile_spec(candidate), dependency_closure=sorted(closure),
        unaffected_operation_ids=[], claims={"uses_system_compiler": True, "uses_board_compiler": False,
            "provider_execution": False, "base_board_mutated": False, "candidate_accepted": False,
            "full_coupled_rerun_required": True, "canonical_admission": False})
    return candidate, preview


def run_view(result: dict) -> dict:
    """Read-only copies of native trajectories and native verification, no fitting."""
    candidate = result["candidate"]
    sim = candidate["data"]["simulation"]
    native_report = result["verification"]["data"]
    return {"schema": "ciw.industrial-lab-run-view.v1", "claims": deepcopy(CLAIMS),
        "source_result_id": candidate["result_id"], "source_execution_id": candidate["execution_id"],
        "source_evidence_id": candidate["evidence_id"], "configuration_digest": sim["configuration_digest"],
        "plan_result_id": result["plan"]["result_id"], "verification_result_id": result["verification"]["result_id"],
        "verification_execution_id": result["verification"]["execution_id"],
        "verification": deepcopy(native_report), "units": deepcopy(sim["units"]),
        "frame": sim["frame"], "trajectory": deepcopy(sim["trajectory"])}


class IndustrialLab(BoardWorkbench):
    """Specialize the existing Board transport; keep one ordinary native Session.

    Board's default oscillator runner and Needle semantics remain unchanged.
    Numerical work here goes through system_workflow.run_specification instead.
    """
    def __init__(self, raw: bytes, output_dir: Path, *, allow_run=False, engine="subprocess"):
        self.specification = checked_spec(raw)
        if type(allow_run) is not bool or engine not in {"local", "subprocess"}:
            raise ValueError("Require explicit boolean authority and local/subprocess engine")
        self.board = project_board(self.specification)
        self.allow_run, self.engine = allow_run, engine
        self.lock, self.actions = RLock(), 0
        self.baseline = None
        self.latest_candidate = None
        self.previews = {}
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=False)
        save_new(self.output_dir / "board.json", self.board)
        self.session = Session(source_run([self.specification]), self.output_dir / "session")
        self.source = self._retain_source(raw, self.specification["configuration_id"])

    def _retain_source(self, raw: bytes, label: str) -> dict:
        source = self.session.workbench.add_source({"kind": "system-specification", "label": label,
                   "bytes_b64": base64.b64encode(raw).decode("ascii")})
        self.session.save_workspace(self.session.output_dir / "workspace.json")
        return source

    def state(self):
        return {"profile": PROFILE, "board": deepcopy(self.board), "view": default_view(self.board),
            "execution_enabled": self.allow_run, "scientific_gate_enabled": False,
            "compilation_error": None, "source_evidence_id": self.source["evidence_id"],
            "baseline_ref": self.baseline["candidate"]["result_id"] if self.baseline else None,
            "lab_baseline": run_view(self.baseline) if self.baseline else None,
            "lab_candidate": run_view(self.latest_candidate) if self.latest_candidate else None,
            "native_specification": deepcopy(self.specification), "claims": deepcopy(CLAIMS)}

    def preview(self, request):
        candidate, preview = edit_specification(self.specification, self.board, request)
        path = self._directory("edit")
        save_new(path / "request.json", request)
        save_new(path / "preview.json", preview)
        save_new(path / "candidate-specification.json", candidate)
        self.previews[content_identity(request)] = deepcopy(preview)
        return {"preview": preview, "intervention_gate": None, "scientific_context": None,
                "recovery_available": False, "retained_directory": path.name}

    def _run(self, specification, source, path):
        if not self.allow_run:
            raise ValueError("Execution is disabled; operator must supply --allow-run")
        result = run_specification(self.session, specification, engine=self.engine, source_id=source["source_id"])
        save_new(path / "results.json", result)
        save_new(path / "view.json", run_view(result))
        return result

    def run_baseline(self):
        if not self.allow_run:
            raise ValueError("Execution is disabled; operator must supply --allow-run")
        if self.baseline is not None:
            raise ValueError("Baseline already retained; use a new Lab occurrence for another baseline")
        path = self._directory("baseline")
        self.baseline = self._run(self.specification, self.source, path)
        return {"lab": run_view(self.baseline), "baseline_ref": self.baseline["candidate"]["result_id"],
                "retained_directory": path.name}

    def run_candidate(self, request, baseline_ref, promotion_ref=None):
        if not self.allow_run:
            raise ValueError("Execution is disabled; operator must supply --allow-run")
        if self.baseline is None or baseline_ref != self.baseline["candidate"]["result_id"]:
            raise ValueError("Candidate targets a missing or different baseline occurrence")
        if promotion_ref is not None:
            raise ValueError("Representation promotion is not part of this Lab profile")
        spec, preview = edit_specification(self.specification, self.board, request)
        if self.previews.get(content_identity(request)) != preview:
            raise ValueError("Compile and retain this exact candidate before execution")
        path = self._directory("candidate")
        save_new(path / "request.json", request)
        source = self._retain_source(_bytes(spec), spec["configuration_id"])
        result = self._run(spec, source, path)
        self.latest_candidate = result
        receipt = _view_record("industrial-lab-scenario", baseline_result_id=baseline_ref,
            candidate_result_id=result["candidate"]["result_id"], request=deepcopy(request),
            source_id=source["source_id"], preview_ref=preview["record_digest"],
            claims={"same_session": True, "full_coupled_rerun": True, "baseline_mutated": False,
                    "reduction_comparison_executed": False, "causal_validation": False,
                    "physical_authority": False, "canonical_admission": False})
        save_new(path / "scenario.json", receipt)
        return {"lab": run_view(result), "receipt": receipt, "retained_directory": path.name}

    def qualify_expansion(self, request, baseline_ref):
        raise ValueError("Representation expansion is not part of this bounded Lab profile")
