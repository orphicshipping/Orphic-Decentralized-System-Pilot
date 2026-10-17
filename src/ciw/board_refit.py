"""Bounded MCP Board refits using existing Board, Session and Needle operations.

Operator-bound grants never come from model input. Durable execution claims are
never replayed after interruption. Candidates remain unreviewed, not admitted.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re
from threading import RLock
from .agent_recovery import RecoveryStore
from .control_contracts import content_ref, detached, keys, text
from .control_checks import inspect_record
from .control_plane import builtin_registry
from .core.identities import canonical_json, content_identity, validate_evidence_identity
from .instruments import validate_run
from .needle import candidate_experiment, delta_projection, plan_from_spec, validate_needle_run
from .operations.runner import check_seal, seal
from .semantic_capabilities import builtin_semantic_registry
from .session import Session, _channel_for_run, _interval_for_run, loads_json
from .system_board import compile_board, validate_board
from .visual_board import apply_parameter_edit, validate_edit
from .visual_board_server import BoardWorkbench, SAFE_OPERATIONS

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ARGUMENT_BYTES = 64 * 1024
PUBLIC_REFUSALS = frozenset({
    "invalid_arguments", "authorization_refused", "scope_refused", "stale_board",
    "preview_unknown", "validation_unknown", "validation_mismatch", "baseline_required",
    "baseline_mismatch", "baseline_already_established", "baseline_attempt_unresolved",
    "attempt_conflict", "attempt_outcome_unknown", "candidate_unknown", "budget_exhausted",
    "retained_state_invalid", "execution_incomplete",
})

class BoardRefitRefusal(ValueError):
    """A fixed public refusal code, without filesystem or provider diagnostics."""
    def __init__(self, code):
        if code not in PUBLIC_REFUSALS:
            raise ValueError("Unknown Board refit refusal code")
        self.code = code
        super().__init__(code)

def _record(name, **fields):
    return seal({"schema": "ciw.board-refit-" + name + ".v1", **fields})

def _raw(value):
    return canonical_json(value).encode("utf-8")

def _schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}

_REF = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
_ID = {"type": "string", "minLength": 1, "maxLength": 64,
       "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 512}
_TOOL_INPUTS = {
    "ncp_board_status": {},
    "ncp_board_preview": {"base_board_ref": _REF, "node_id": _TEXT, "parameter": _TEXT, "replacement": {}},
    "ncp_board_validate": {"preview_ref": _REF},
    "ncp_board_baseline": {"attempt_id": _ID},
    "ncp_board_recompute": {"preview_ref": _REF, "validation_ref": _REF, "baseline_ref": _REF, "attempt_id": _ID},
    "ncp_board_compare": {"candidate_ref": _REF},
    "ncp_board_candidate": {"candidate_ref": _REF},
}
_TOOL_DESCRIPTIONS = {
    "ncp_board_status": "Inspect the operator-bound Board, editable parameters, budgets and retained attempts. Executes no providers.",
    "ncp_board_preview": "Retain a parameter-change preview and dependency closure without changing the original Board or executing providers.",
    "ncp_board_validate": "Validate a preview against the exact Board, source selections and existing grant. Executes no providers; grants no authority.",
    "ncp_board_baseline": "Explicitly establish the one retained baseline under the existing grant. Use a stable attempt_id; interrupted attempts are never replayed.",
    "ncp_board_recompute": "Recompute the validated dependency closure under the existing grant and retain an unreviewed candidate. Reuse attempt_id for retries.",
    "ncp_board_compare": "Read retained baseline/candidate differences, including unchanged recomputed outputs. Differences do not establish improvement or acceptance.",
    "ncp_board_candidate": "Read retained candidate identities and unreviewed status. Cannot approve, promote or accept a candidate.",
}

class BoardRefitHost:
    """One operator-bound investigation with a durable lifetime writer lock."""
    instructions = (
        "Inspect status, preview one permitted change, validate it, explicitly establish a "
        "baseline if absent, and recompute using exact retained refs and a stable attempt_id. "
        "Read the comparison and unreviewed candidate. Preview/validation execute no providers. "
        "Reconnect using the same operator profile. Never retry unknown work under a new ID. "
        "This profile supports synthetic oscillator analysis, not physical control or acceptance.")

    def __init__(self, board, output_dir, *, source, grant, authority_check=None,
                 resume=False, investigation_ref=None, execution_profile=None):
        self._lock = RLock()
        self._store = None
        self._authority_check = authority_check
        self.board = validate_board(board)
        self.source = detached(source)
        self.grant = detached(grant)
        self.investigation_ref = None if investigation_ref is None else text(investigation_ref)
        self.execution_profile = execution_profile
        self._profile_ref = None if execution_profile is None else content_identity(execution_profile.identity())
        self.concrete = builtin_registry(bind=True) if execution_profile is None else execution_profile.registry()
        self.semantic = builtin_semantic_registry(self.concrete) if execution_profile is None else execution_profile.semantic(self.concrete)
        self.compilation = compile_board(self.board, self.semantic)
        self._board_ref = self.board["record_digest"]
        self._source_ref = content_identity(self.source)
        self._grant_ref = content_identity(self.grant)
        if execution_profile is not None:
            self.instructions = execution_profile.instructions
        self._validate_grant()
        self._preflight(self.board, self.compilation)
        self._check_authority()
        self.output_dir = Path(output_dir)
        if self.output_dir.is_symlink() or (hasattr(self.output_dir, "is_junction") and self.output_dir.is_junction()):
            raise ValueError("Unsafe Board refit output directory")
        bindings = {"board_ref": self._board_ref, "source_ref": self._source_ref,
                    "grant_ref": self._grant_ref, "investigation_ref": self.investigation_ref,
                    "semantic_catalog_ref": content_identity(self.semantic.catalog()),
                    "operator_catalog_ref": content_identity(self.concrete.catalog()),
                    "compilation_ref": self.compilation["record_digest"]}
        if execution_profile is not None:
            bindings["execution_profile_ref"] = self._profile_ref
        if not resume:
            self.output_dir.mkdir(parents=True, exist_ok=False)
        elif not self.output_dir.is_dir():
            raise ValueError("Missing Board refit output; cannot resume")
        try:
            self._store = RecoveryStore(self.output_dir, resume=resume)
            if resume:
                self._state, self._artifacts = self._store.load()
                self._restore(bindings)
            else:
                self._state = {"schema": "ciw.board-refit-state.v1", "bindings": bindings,
                               "baseline": None, "previews": {}, "validations": {},
                               "attempts": {}, "candidates": {}, "artifacts": {}}
                self._artifacts = {}
                self._save()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._store is not None:
            self._store.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _validate_grant(self):
        keys(self.grant, {"schema", "grant_id", "board_ref", "source_evidence_id", "source_ref",
                          "allow_baseline", "targets", "max_candidate_runs", "max_previews"})
        if self.grant["schema"] != "ciw.board-refit-grant.v1":
            raise ValueError("Unsupported Board refit grant")
        text(self.grant["grant_id"])
        if (self.grant["board_ref"] != self._board_ref
                or self.grant["source_evidence_id"] != self.source["evidence_id"]
                or self.grant["source_ref"] != self._source_ref):
            raise ValueError("Grant must bind the exact Board and retained source")
        if type(self.grant["allow_baseline"]) is not bool:
            raise ValueError("Grant allow_baseline must be boolean")
        for field, maximum in (("max_candidate_runs", 64), ("max_previews", 128)):
            if type(self.grant[field]) is not int or not 1 <= self.grant[field] <= maximum:
                raise ValueError("Grant budget must be a bounded positive integer")
        targets = self.grant["targets"]
        if type(targets) is not list or not 1 <= len(targets) <= 32:
            raise ValueError("Grant requires bounded explicit parameter targets")
        nodes = {n["node_id"]: n for n in self.board["nodes"]}
        seen = set()
        for target in targets:
            keys(target, {"node_id", "parameter"})
            coordinate = (text(target["node_id"]), text(target["parameter"]))
            if coordinate in seen:
                raise ValueError("Duplicate grant target")
            seen.add(coordinate)
            node = nodes.get(coordinate[0])
            if (node is None or node["kind"] != "OPERATION"
                    or coordinate[1] not in node["parameters"]
                    or node["parameters"][coordinate[1]]["exposed"] is not True):
                raise ValueError("Grant target must name an exposed operation parameter")

    def _check_authority(self):
        if self.execution_profile is not None and content_identity(self.execution_profile.identity()) != self._profile_ref:
            raise BoardRefitRefusal("retained_state_invalid")
        if self._authority_check is not None:
            try:
                self._authority_check()
            except Exception as exc:
                raise BoardRefitRefusal("authorization_refused") from exc
        if (self.board["record_digest"] != self._board_ref
                or content_identity(self.source) != self._source_ref
                or content_identity(self.grant) != self._grant_ref):
            raise BoardRefitRefusal("retained_state_invalid")
        validate_board(self.board)
        if self._store is not None:
            self._store.check_current()

    def _preflight(self, board, compilation):
        if self.execution_profile is not None:
            return self.execution_profile.preflight(board, compilation, self.source)
        validate_run(self.source)
        validate_evidence_identity(self.source)
        if (board["model_id"] != "analytic-damped-oscillator.v1"
                or self.source["instrument"] != board["model_id"]):
            raise ValueError("Board refit V1 supports the retained analytic oscillator only")
        graph = compilation["semantic_compilation"]["experiment"]
        if not 1 <= len(graph["nodes"]) <= 16 or len(self.source["time_s"]) > 4096:
            raise ValueError("Board refit workload exceeds qualified bounds")
        for node in graph["nodes"]:
            if node["operation_id"] not in SAFE_OPERATIONS or set(node["parameters"]) - {"channel", "interval_s"}:
                raise ValueError("Unsupported Board operation or parameter")
            parameters = {**graph["parameters"], **node["parameters"]}
            if set(parameters) - {"channel", "interval_s"} or node["inputs"]:
                raise ValueError("Unsupported parameters or dynamic parameter inputs")
            if "channel" in parameters:
                _channel_for_run(self.source, parameters["channel"])
            if "interval_s" in parameters:
                _interval_for_run(self.source, parameters["interval_s"])

    def _save(self):
        try:
            self._state["artifacts"] = {key: len(raw) for key, raw in self._artifacts.items()}
            if len(self._artifacts) > 256 or sum(map(len, self._artifacts.values())) > MAX_TOTAL_BYTES:
                raise BoardRefitRefusal("budget_exhausted")
            self._store.save(self._state, self._artifacts)
        except BaseException:
            self.close()  # Reopen the durable checkpoint after every publication failure.
            raise

    def _retain(self, value):
        raw = _raw(value)
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise BoardRefitRefusal("budget_exhausted")
        key = "a-" + sha256(raw).hexdigest()
        self._artifacts.setdefault(key, raw)
        return key

    def _artifact(self, key):
        raw = self._artifacts[key]
        if "a-" + sha256(raw).hexdigest() != key:
            raise BoardRefitRefusal("retained_state_invalid")
        return loads_json(raw.decode("utf-8"))

    def _restore(self, bindings):
        state = self._state
        keys(state, {"schema", "bindings", "baseline", "previews", "validations", "attempts", "candidates", "artifacts"})
        if state["schema"] != "ciw.board-refit-state.v1" or state["bindings"] != bindings:
            raise ValueError("Board refit recovery bindings differ")
        for field in ("previews", "validations", "attempts", "candidates"):
            if type(state[field]) is not dict:
                raise ValueError("Invalid Board refit recovery index")
        if (len(state["previews"]) > self.grant["max_previews"]
                or len(state["validations"]) > len(state["previews"])
                or len(state["attempts"]) > self.grant["max_candidate_runs"] + 1
                or len(state["candidates"]) > self.grant["max_candidate_runs"]):
            raise ValueError("Recovered Board refit budget exceeded")
        for ref, key in state["previews"].items():
            preview = self._artifact(key)
            if preview["record_digest"] != ref:
                raise ValueError("Recovered preview identity differs")
            validate_edit(preview, self.board, self.semantic)
            self._check_target(preview["request"])
        for ref, validation in state["validations"].items():
            if validation != self._validation(validation["preview_ref"]) or validation["record_digest"] != ref:
                raise ValueError("Recovered validation differs")
        if state["baseline"] is not None:
            self._baseline()
        for ref in state["candidates"]:
            self._candidate_bundle(ref)
        baseline_attempts, candidate_attempts = 0, 0
        for attempt_id, attempt in state["attempts"].items():
            self._attempt_id(attempt_id)
            keys(attempt, {"kind", "input", "status", "outcome"})
            if attempt["kind"] not in {"baseline", "candidate"} or attempt["status"] not in {"claimed", "completed"}:
                raise ValueError("Invalid recovered attempt state")
            if attempt["kind"] == "baseline":
                baseline_attempts += 1
                if attempt["input"] != {"board_ref": self._board_ref, "grant_ref": self._grant_ref}:
                    raise ValueError("Recovered baseline attempt differs")
            else:
                candidate_attempts += 1
                keys(attempt["input"], {"preview_ref", "validation_ref", "baseline_ref", "grant_ref"})
                if attempt["input"]["grant_ref"] != self._grant_ref:
                    raise ValueError("Recovered candidate grant differs")
                self._checked_candidate_inputs(attempt["input"])
            if attempt["status"] == "claimed":
                if attempt["outcome"] is not None:
                    raise ValueError("Claimed attempt has an outcome")
            elif attempt["kind"] == "baseline":
                baseline = self._baseline()
                if attempt["outcome"] != baseline["run"]["record_digest"] or baseline["attempt_id"] != attempt_id:
                    raise ValueError("Recovered baseline outcome differs")
            else:
                candidate = self._candidate_bundle(attempt["outcome"])
                expected_input = {key: candidate[key] for key in ("preview_ref", "validation_ref", "baseline_ref", "grant_ref")}
                if candidate["attempt_id"] != attempt_id or attempt["input"] != expected_input:
                    raise ValueError("Recovered candidate attempt differs")
        if baseline_attempts > 1 or candidate_attempts > self.grant["max_candidate_runs"]:
            raise ValueError("Recovered execution budget exceeded")
        completed_baselines = [a for a in state["attempts"].values() if a["kind"] == "baseline" and a["status"] == "completed"]
        if (state["baseline"] is not None) != (len(completed_baselines) == 1):
            raise ValueError("Retained baseline lacks a completed execution attempt")
        completed_candidates = {a["outcome"] for a in state["attempts"].values() if a["kind"] == "candidate" and a["status"] == "completed"}
        if set(state["candidates"]) != completed_candidates:
            raise ValueError("Retained candidate lacks a completed execution attempt")
        expected_artifacts = set(state["previews"].values()) | set(state["candidates"].values())
        if state["baseline"] is not None:
            expected_artifacts.add(state["baseline"])
        if set(self._artifacts) != expected_artifacts:
            raise ValueError("Unexpected retained Board refit artifacts")

    def _check_target(self, request):
        if request["base_board_ref"] != self._board_ref:
            raise BoardRefitRefusal("stale_board")
        target = {"node_id": request["node_id"], "parameter": request["parameter"]}
        if target not in self.grant["targets"]:
            raise BoardRefitRefusal("scope_refused")

    def _preview(self, ref):
        content_ref(ref)
        key = self._state["previews"].get(ref)
        if key is None:
            raise BoardRefitRefusal("preview_unknown")
        preview = self._artifact(key)
        validate_edit(preview, self.board, self.semantic)
        self._check_target(preview["request"])
        return preview

    def _validation(self, preview_ref):
        preview = self._preview(preview_ref)
        self._preflight(preview["candidate_board"], preview["compilation"])
        return _record("validation", preview_ref=preview_ref, board_ref=self._board_ref,
                       source_ref=self._source_ref, source_evidence_id=self.source["evidence_id"],
                       grant_ref=self._grant_ref, valid=True, source_selection_checked=True,
                       execution_authority=False, provider_execution=False)

    def _baseline(self):
        key = self._state["baseline"]
        if key is None:
            raise BoardRefitRefusal("baseline_required")
        bundle = self._artifact(key)
        keys(bundle, {"schema", "record_digest", "attempt_id", "board_ref", "source_ref", "grant_ref", "run", "workspace"})
        check_seal(bundle)
        if (bundle["schema"] != "ciw.board-refit-baseline.v1" or bundle["board_ref"] != self._board_ref
                or bundle["source_ref"] != self._source_ref or bundle["grant_ref"] != self._grant_ref):
            raise BoardRefitRefusal("retained_state_invalid")
        run = bundle["run"]
        inspect_record(run)
        if (run["status"] != "completed" or run["source_evidence_id"] != self.source["evidence_id"]
                or run["experiment"] != self.compilation["semantic_compilation"]["experiment"]):
            raise BoardRefitRefusal("retained_state_invalid")
        self._check_workspace(bundle["workspace"], run)
        return bundle

    def _check_workspace(self, workspace, run):
        if self.execution_profile is not None:
            self.execution_profile.check_workspace(workspace, run)
        if workspace.get("run") != self.source:
            raise BoardRefitRefusal("retained_state_invalid")
        results, executions = workspace.get("results"), workspace.get("executions")
        if type(results) is not list or type(executions) is not list:
            raise BoardRefitRefusal("retained_state_invalid")
        results = {item["result_id"]: item for item in results}
        executions = {item["execution_id"]: item for item in executions}
        for outcome in run["nodes"].values():
            if outcome.get("status") == "completed":
                result, execution = outcome["result"], outcome["execution"]
                if (results.get(result["result_id"]) != result or executions.get(execution["execution_id"]) != execution):
                    raise BoardRefitRefusal("retained_state_invalid")

    def _candidate_bundle(self, ref):
        content_ref(ref)
        key = self._state["candidates"].get(ref)
        if key is None:
            raise BoardRefitRefusal("candidate_unknown")
        bundle = self._artifact(key)
        check_seal(bundle)
        keys(bundle, {"schema", "record_digest", "attempt_id", "investigation_ref", "grant_ref", "source_ref",
                      "preview_ref", "validation_ref", "baseline_ref", "candidate_board_ref", "run", "delta",
                      "receipt", "workspace", "review_status", "accepted"})
        if (bundle["schema"] != "ciw.board-refit-candidate.v1" or bundle["record_digest"] != ref
                or bundle["grant_ref"] != self._grant_ref or bundle["source_ref"] != self._source_ref
                or bundle["investigation_ref"] != self.investigation_ref
                or bundle["review_status"] != "unreviewed" or bundle["accepted"] is not False):
            raise BoardRefitRefusal("retained_state_invalid")
        preview = self._checked_candidate_inputs(bundle)
        if bundle["candidate_board_ref"] != preview["candidate_board"]["record_digest"]:
            raise BoardRefitRefusal("retained_state_invalid")
        baseline = self._baseline()["run"]
        validate_needle_run(bundle["run"], baseline)
        request = preview["request"]
        plan = plan_from_spec(baseline, {
            "needle_id": "board-edit-" + preview["record_digest"][7:31],
            "target": {"kind": "NODE_PARAMETER", "node_id": request["node_id"], "parameter": request["parameter"]},
            "replacement": request["replacement"],
            "propagation": {"relation": "DEPENDENCY", "scope": "DESCENDANTS_INCLUSIVE"},
        })
        if (bundle["run"]["needle_plan_ref"] != plan["record_digest"]
                or bundle["run"]["candidate_experiment"] != candidate_experiment(baseline, plan)
                or bundle["run"]["dependency_closure"] != preview["dependency_closure"]):
            raise BoardRefitRefusal("retained_state_invalid")
        if delta_projection(baseline, bundle["run"]) != bundle["delta"]:
            raise BoardRefitRefusal("retained_state_invalid")
        receipt = bundle["receipt"]
        check_seal(receipt)
        if (receipt["board_ref"] != self._board_ref or receipt["preview_ref"] != bundle["preview_ref"]
                or receipt["candidate_board_ref"] != bundle["candidate_board_ref"]
                or receipt["baseline_ref"] != bundle["baseline_ref"]
                or receipt["run_ref"] != bundle["run"]["record_digest"]
                or receipt["delta_ref"] != bundle["delta"]["record_digest"]
                or receipt["source_evidence_id"] != self.source["evidence_id"]):
            raise BoardRefitRefusal("retained_state_invalid")
        self._check_workspace(bundle["workspace"], bundle["run"])
        return bundle

    def _checked_candidate_inputs(self, args):
        preview = self._preview(args["preview_ref"])
        validation = self._state["validations"].get(args["validation_ref"])
        if validation is None:
            raise BoardRefitRefusal("validation_unknown")
        if validation != self._validation(args["preview_ref"]):
            raise BoardRefitRefusal("validation_mismatch")
        if self._baseline()["run"]["record_digest"] != args["baseline_ref"]:
            raise BoardRefitRefusal("baseline_mismatch")
        return preview

    @staticmethod
    def _attempt_id(value):
        if type(value) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) is None:
            raise BoardRefitRefusal("invalid_arguments")
        return value

    def _claim(self, attempt_id, kind, inputs):
        self._attempt_id(attempt_id)
        self._check_authority()
        existing = self._state["attempts"].get(attempt_id)
        if existing is not None:
            if existing["kind"] != kind or existing["input"] != inputs:
                raise BoardRefitRefusal("attempt_conflict")
            if existing["status"] == "claimed":
                raise BoardRefitRefusal("attempt_outcome_unknown")
            return existing["outcome"]
        if kind == "candidate":
            # Preview references preserve literal JSON (0 versus 0.0), while
            # Board validation normalizes numeric parameters. Compare the
            # actual candidate as well as its baseline/grant before allowing
            # another attempt after an unknown outcome. Old checkpoints need
            # no migration: their existing previews retain this identity.
            candidate_ref = self._preview(inputs["preview_ref"])["candidate_board"]["record_digest"]
            for attempt in self._state["attempts"].values():
                prior = attempt["input"]
                if (attempt["kind"] == kind and attempt["status"] == "claimed"
                        and prior["baseline_ref"] == inputs["baseline_ref"]
                        and prior["grant_ref"] == inputs["grant_ref"]
                        and self._preview(prior["preview_ref"])["candidate_board"]["record_digest"] == candidate_ref):
                    raise BoardRefitRefusal("attempt_outcome_unknown")
        if kind == "baseline":
            if not self.grant["allow_baseline"]:
                raise BoardRefitRefusal("authorization_refused")
            if self._state["baseline"] is not None:
                raise BoardRefitRefusal("baseline_already_established")
            if any(a["kind"] == "baseline" for a in self._state["attempts"].values()):
                raise BoardRefitRefusal("baseline_attempt_unresolved")
        elif sum(a["kind"] == "candidate" for a in self._state["attempts"].values()) >= self.grant["max_candidate_runs"]:
            raise BoardRefitRefusal("budget_exhausted")
        if sum(map(len, self._artifacts.values())) + MAX_ARTIFACT_BYTES > MAX_TOTAL_BYTES:
            raise BoardRefitRefusal("budget_exhausted")
        self._state["attempts"][attempt_id] = {"kind": kind, "input": detached(inputs), "status": "claimed", "outcome": None}
        self._save()  # A durable claim must exist before any provider executes.
        return None

    def _complete(self, attempt_id, outcome):
        attempt = self._state["attempts"][attempt_id]
        attempt["status"], attempt["outcome"] = "completed", outcome
        self._save()

    def _workbench(self, attempt_id):
        path = self.output_dir / ("attempt-" + sha256(attempt_id.encode()).hexdigest())
        return BoardWorkbench(self.board, path, source=self.source, allow_run=True,
                              execution_profile=self.execution_profile)

    @staticmethod
    def _workspace(workbench):
        path = workbench.output_dir / "workspace.json"
        workbench.session.save_workspace(path)
        return loads_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _node_result_refs(run, baseline=None):
        result = {}
        for name, outcome in run["nodes"].items():
            if outcome["status"] == "completed":
                result[name] = content_identity(outcome["result"])
            elif outcome["status"] == "reused" and baseline is not None:
                result[name] = content_identity(baseline["nodes"][name]["result"])
            else:
                result[name] = None
        return result

    def _baseline_summary(self):
        bundle = self._baseline()
        run = bundle["run"]
        return {"baseline_ref": run["record_digest"], "attempt_id": bundle["attempt_id"],
                "status": run["status"], "source_evidence_id": run["source_evidence_id"],
                "node_result_refs": self._node_result_refs(run), "provider_execution": True}

    def _candidate_summary(self, ref):
        value = self._candidate_bundle(ref)
        run, delta = value["run"], value["delta"]
        return {"candidate_ref": ref, "attempt_id": value["attempt_id"], "preview_ref": value["preview_ref"],
                "validation_ref": value["validation_ref"], "baseline_ref": value["baseline_ref"],
                "candidate_board_ref": value["candidate_board_ref"], "investigation_ref": self.investigation_ref,
                "grant_ref": self._grant_ref, "run_ref": run["record_digest"], "delta_ref": delta["record_digest"],
                "status": run["status"], "review_status": "unreviewed", "accepted": False,
                "rerun_nodes": run["rerun_nodes"], "reused_nodes": run["reused_nodes"],
                "source_evidence_id": run["source_evidence_id"], "comparison_summary": delta["summary"],
                "node_result_refs": self._node_result_refs(run, self._baseline()["run"]),
                "node_parameters": {node["node_id"]: node["parameters"] for node in run["candidate_experiment"]["nodes"]},
                "claims": {"baseline_mutated": False, "canonical_board_mutated": False,
                           "physical_authority": False, "scientific_acceptance": False}}

    def descriptions(self):
        with self._lock:
            self._check_authority()
            return [{"name": name, "description": _TOOL_DESCRIPTIONS[name], "inputSchema": _schema(deepcopy(fields)),
                     "annotations": {"readOnlyHint": name in {"ncp_board_status", "ncp_board_compare", "ncp_board_candidate"},
                                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}}
                    for name, fields in _TOOL_INPUTS.items()]

    def call(self, name, arguments):
        with self._lock:
            self._check_authority()
            if name not in _TOOL_INPUTS:
                raise BoardRefitRefusal("invalid_arguments")
            try:
                keys(arguments, set(_TOOL_INPUTS[name]))
                if len(_raw(arguments)) > MAX_ARGUMENT_BYTES:
                    raise ValueError("Arguments exceed bounds")
                arguments = detached(arguments)
                for key in _TOOL_INPUTS[name]:
                    if key.endswith("_ref"):
                        content_ref(arguments[key])
            except (ValueError, TypeError, OverflowError, RecursionError) as exc:
                raise BoardRefitRefusal("invalid_arguments") from exc
            try:
                value = getattr(self, "_call_" + name.removeprefix("ncp_board_"))(arguments)
                if self.execution_profile is not None:
                    value["execution_profile"] = self.execution_profile.summary()
                return detached(value)
            except BoardRefitRefusal:
                raise
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                raise BoardRefitRefusal("invalid_arguments") from exc

    def _call_status(self, _):
        nodes = {n["node_id"]: n for n in self.board["nodes"]}
        candidates = sum(a["kind"] == "candidate" for a in self._state["attempts"].values())
        return {"board_ref": self._board_ref, "source_evidence_id": self.source["evidence_id"],
                "source_ref": self._source_ref, "grant_ref": self._grant_ref, "investigation_ref": self.investigation_ref,
                "baseline_ref": None if self._state["baseline"] is None else self._baseline()["run"]["record_digest"],
                "editable_parameters": [{**target, **deepcopy(nodes[target["node_id"]]["parameters"][target["parameter"]])}
                                        for target in self.grant["targets"]],
                "candidate_refs": list(self._state["candidates"]),
                "attempts": [{"attempt_id": aid, "kind": a["kind"],
                              "status": "outcome_unknown" if a["status"] == "claimed" else "completed",
                              "outcome_ref": a["outcome"]} for aid, a in self._state["attempts"].items()],
                "budgets": {"candidate_runs_used": candidates, "candidate_runs_limit": self.grant["max_candidate_runs"],
                            "previews_used": len(self._state["previews"]), "previews_limit": self.grant["max_previews"]},
                "claims": {"provider_execution": False, "operator_grant_bound": True,
                           "candidate_acceptance_available": False, "physical_control_available": False}}

    def _call_preview(self, args):
        request = {"schema": "ciw.board-parameter-edit.v1", **args}
        self._check_target(request)
        preview = apply_parameter_edit(self.board, request, self.semantic)
        ref = preview["record_digest"]
        if ref not in self._state["previews"]:
            if len(self._state["previews"]) >= self.grant["max_previews"]:
                raise BoardRefitRefusal("budget_exhausted")
            self._state["previews"][ref] = self._retain(preview)
            self._save()
        node = next(n for n in self.board["nodes"] if n["node_id"] == args["node_id"])
        return {"preview_ref": ref, "base_board_ref": self._board_ref,
                "candidate_board_ref": preview["candidate_board"]["record_digest"],
                "target": {"node_id": args["node_id"], "parameter": args["parameter"]},
                "before": node["parameters"][args["parameter"]]["value"], "replacement": args["replacement"],
                "affected_nodes": preview["dependency_closure"], "unaffected_nodes": preview["unaffected_operation_ids"],
                "provider_execution": False}

    def _call_validate(self, args):
        value = self._validation(args["preview_ref"])
        self._state["validations"][value["record_digest"]] = value
        self._save()
        return {**value, "validation_ref": value["record_digest"]}

    def _call_baseline(self, args):
        attempt_id = self._attempt_id(args["attempt_id"])
        prior = self._claim(attempt_id, "baseline", {"board_ref": self._board_ref, "grant_ref": self._grant_ref})
        if prior is not None:
            return self._baseline_summary()
        try:
            workbench = self._workbench(attempt_id)
            outcome = workbench.run_baseline()
            if outcome["run"]["status"] != "completed":
                self._workspace(workbench)
                raise BoardRefitRefusal("execution_incomplete")
            bundle = _record("baseline", attempt_id=attempt_id, board_ref=self._board_ref,
                             source_ref=self._source_ref, grant_ref=self._grant_ref,
                             run=outcome["run"], workspace=self._workspace(workbench))
            self._state["baseline"] = self._retain(bundle)
            self._baseline()
            self._complete(attempt_id, outcome["run"]["record_digest"])
            return self._baseline_summary()
        except BaseException:
            self.close()
            raise

    def _call_recompute(self, args):
        preview = self._checked_candidate_inputs(args)
        attempt_id = self._attempt_id(args["attempt_id"])
        inputs = {key: args[key] for key in ("preview_ref", "validation_ref", "baseline_ref")}
        inputs["grant_ref"] = self._grant_ref
        prior = self._claim(attempt_id, "candidate", inputs)
        if prior is not None:
            return self._candidate_summary(prior)
        try:
            workbench = self._workbench(attempt_id)
            workbench.baseline = self._baseline()["run"]
            workbench.session = (Session(self.source, workbench.output_dir / "session", operations=workbench.concrete.operations)
                                 if self.execution_profile is None else self.execution_profile.session(
                                     workbench.output_dir / "session", workbench.concrete, self._baseline()["workspace"]))
            outcome = workbench.run_candidate(preview["request"], args["baseline_ref"])
            bundle = _record("candidate", attempt_id=attempt_id, investigation_ref=self.investigation_ref,
                             grant_ref=self._grant_ref, source_ref=self._source_ref, preview_ref=args["preview_ref"],
                             validation_ref=args["validation_ref"], baseline_ref=args["baseline_ref"],
                             candidate_board_ref=preview["candidate_board"]["record_digest"], run=outcome["run"],
                             delta=outcome["delta"], receipt=outcome["receipt"], workspace=self._workspace(workbench),
                             review_status="unreviewed", accepted=False)
            ref = bundle["record_digest"]
            self._state["candidates"][ref] = self._retain(bundle)
            self._candidate_bundle(ref)
            self._complete(attempt_id, ref)
            return self._candidate_summary(ref)
        except BaseException:
            self.close()
            raise

    def _call_compare(self, args):
        value = self._candidate_bundle(args["candidate_ref"])
        return {**value["delta"], "candidate_ref": args["candidate_ref"], "review_status": "unreviewed", "accepted": False}

    def _call_candidate(self, args):
        return self._candidate_summary(args["candidate_ref"])
