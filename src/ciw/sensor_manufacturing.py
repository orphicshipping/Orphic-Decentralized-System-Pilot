"""Compile a retained sensor product into a capability-bound manufacturing plan.

A planning operation on the existing sensor-project source and NET Session. It
never interprets a plan as physical execution, writes to machines, issues orders,
or creates a replacement manufacturing/measurement ledger.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from decimal import Decimal
import io
import math
from pathlib import Path
import platform
import re
import tempfile
from threading import Lock
from typing import Any

from . import sensor_project as project
from .adapters.protocol import InstrumentManifest
from .control_contracts import bytes_ref, content_ref, detached, keys, text
from .control_plane import CapabilityRegistry
from .core.identities import content_identity
from .operations.registry import Operation
from .operations.schemas import register_payload_validator
from .session import Session, loads_json

OPERATION = "engineering.manufacturing-plan.v1"
ANALYSIS_OPERATION = "engineering.manufacturing-plan.v2"
DEFINITION = "ciw.sensor-manufacturing-definition.v1"
RESULT = "ciw.sensor-manufacturing-plan.v1"
PLAN_PATH = "manufacturing.json"
PARAMETERS = {"channel": project.CHANNEL, "interval_s": [0.0, 1.0]}
CLAIMS = {**project.CLAIMS, "physical_execution": False,
          "supplier_order": False, "unit_genealogy": "planned_or_declared_not_authenticated",
          "inspection_acceptance": False}
MAX_UNITS, MAX_STEPS, MAX_RESOURCES = 128, 64, 32
_LOCK = Lock()
_REGISTERED = False


def _list(value: Any, maximum: int, minimum: int = 0) -> list:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ValueError("Require a bounded list")
    return value


def _texts(value: Any, maximum: int, minimum: int = 0) -> list[str]:
    for item in _list(value, maximum, minimum):
        text(item)
    if len(set(value)) != len(value):
        raise ValueError("Duplicate named reference")
    return value


def _number(value: Any, minimum: float = 0) -> Decimal:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= 1e9:
        raise ValueError("Require a bounded finite number, not a boolean")
    return Decimal(str(value))


def _count(value: Any, maximum: int = MAX_UNITS) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("Require a positive bounded integer")
    return value


def _named(rows: Any, field: str, maximum: int) -> dict[str, dict]:
    result = {}
    for row in _list(rows, maximum, 1):
        if type(row) is not dict or field not in row:
            raise ValueError("Missing named record")
        name = text(row[field])
        if name in result:
            raise ValueError("Duplicate named record")
        result[name] = row
    return result


def _artifact(path: str, files: dict, roles: dict, kinds: set[str] | None = None) -> bytes:
    project.packets.relative(path)
    if path not in files or kinds is not None and roles[path] not in kinds:
        raise ValueError("Missing or wrong-role retained manufacturing artifact")
    return files[path]


def _json(path: str, files: dict, roles: dict, kinds: set[str] | None = None) -> Any:
    return loads_json(_artifact(path, files, roles, kinds).decode("utf-8"))


def _lookup(mapping: dict, files: dict, roles: dict) -> Any:
    """A typed JSON path into original bytes; never a formula or executable string."""
    keys(mapping, {"path", "keys"})
    value = _json(mapping["path"], files, roles)
    for key in _list(mapping["keys"], 12):
        if type(value) is dict and type(key) is str and key in value:
            value = value[key]
        elif type(value) is list and type(key) is int and 0 <= key < len(value):
            value = value[key]
        else:
            raise ValueError("Missing or ambiguous design-property path")
    return value


def _order(steps: dict[str, dict]) -> list[str]:
    remaining, result = set(steps), []
    while remaining:
        ready = sorted(name for name in remaining if set(steps[name]["depends_on"]) <= set(result))
        if not ready:
            raise ValueError("Cyclic process graph or absent dependency")
        result.extend(ready)
        remaining.difference_update(ready)
    return result


def _bom(spec: dict, files: dict, roles: dict, units: int) -> dict:
    keys(spec, {"path", "columns"})
    cols = spec["columns"]
    keys(cols, {"reference", "quantity", "manufacturer", "part_number"})
    if len(set(cols.values())) != 4:
        raise ValueError("BOM columns must be distinct")
    for column in cols.values(): text(column)
    raw = _artifact(spec["path"], files, roles, {"manufacturing.output"})
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""), strict=True)
    header = reader.fieldnames
    if header is None or len(set(header)) != len(header) or not set(cols.values()) <= set(header):
        raise ValueError("Missing or duplicate BOM headers")
    lines, seen, aggregate = [], set(), {}
    for index, row in enumerate(reader, 1):
        if index > 256 or None in row or any(v is None for v in row.values()):
            raise ValueError("Malformed or excessive BOM records")
        reference = text(row[cols["reference"]])
        if reference in seen: raise ValueError("Duplicate BOM reference designator")
        seen.add(reference)
        token = row[cols["quantity"]]
        if re.fullmatch(r"[1-9][0-9]{0,3}", token) is None:
            raise ValueError("BOM quantity requires a positive decimal integer")
        quantity = _count(int(token), 4096)
        manufacturer, part = text(row[cols["manufacturer"]]), text(row[cols["part_number"]])
        key = (manufacturer, part)
        aggregate[key] = aggregate.get(key, 0) + quantity * units
        lines.append({"csv_record": index, "source_line_end": reader.line_num,
                      "reference": reference, "manufacturer": manufacturer, "part_number": part,
                      "per_unit": quantity, "required_quantity": quantity * units,
                      "source_record": deepcopy(row)})
    if not lines: raise ValueError("Empty BOM")
    return {"source_path": spec["path"], "source_ref": bytes_ref(raw), "lines": lines,
            "components_per_unit": sum(v["per_unit"] for v in lines),
            "total_components": sum(aggregate.values()),
            "procurement": [{"manufacturer": k[0], "part_number": k[1], "quantity": aggregate[k]}
                            for k in sorted(aggregate)],
            "scope": "exact_declared_BOM_no_substitutions_no_scrap_allowance_no_orders"}


def _definition(run: dict) -> tuple:
    spec, inventory, files = project.context(run)
    roles = {row["path"]: row["kind"] for row in spec["artifacts"]}
    plan = _json(PLAN_PATH, files, roles, {"model.input"})
    keys(plan, {"schema", "product", "order", "requirements_path", "bom", "resources", "operations", "inspections", "basis"})
    if plan["schema"] != DEFINITION or plan["basis"] != "declared_planning_assumptions":
        raise ValueError("Unsupported manufacturing definition or assumption basis")
    product = plan["product"]
    keys(product, {"product_id", "revision", "native_paths"})
    if product["product_id"] != spec["project_id"] or product["revision"] != spec["hardware_revision"]:
        raise ValueError("Product/revision differs from retained project")
    native = _texts(product["native_paths"], 32, 1)
    for path in native:
        _artifact(path, files, roles, {"electronics.pcb", "electronics.schematic", "mechanical.cad", "geometry.step"})
    if not any(roles[p] in {"electronics.pcb", "mechanical.cad"} for p in native):
        raise ValueError("Product needs a native engineering design")
    order = plan["order"]
    keys(order, {"work_order_id", "quantity", "unit_ids"})
    text(order["work_order_id"]); n = _count(order["quantity"])
    if len(_texts(order["unit_ids"], MAX_UNITS, 1)) != n:
        raise ValueError("Planned unit inventory differs from order quantity")
    req = _json(plan["requirements_path"], files, roles, {"model.input"})
    keys(req, {"product_id", "revision", "requirements"})
    if req["product_id"] != product["product_id"] or req["revision"] != product["revision"]:
        raise ValueError("Requirement revision differs from product")
    requirements = _named(req["requirements"], "requirement_id", 32)
    for requirement in requirements.values():
        keys(requirement, {"requirement_id", "quantity", "unit", "lower", "upper", "procedure_path", "criterion_basis"})
        for key in ("quantity", "unit"): text(requirement[key])
        _artifact(requirement["procedure_path"], files, roles, {"model.input", "source.notice"})
        lo, hi = requirement["lower"], requirement["upper"]
        if requirement["criterion_basis"] == "unspecified":
            if lo is not None or hi is not None: raise ValueError("Unknown criterion cannot carry numeric limits")
        elif requirement["criterion_basis"] in {"operator_declared", "synthetic_example"}:
            if _number(lo, -1e9) >= _number(hi, -1e9): raise ValueError("Require ordered inspection limits")
        else: raise ValueError("Unrecognized criterion basis")
    resources = _named(plan["resources"], "resource_id", MAX_RESOURCES)
    for resource in resources.values():
        keys(resource, {"resource_id", "kind", "capabilities", "limits", "batch_capacity", "rate_per_hour", "currency"})
        if resource["kind"] not in {"supplier", "cell", "instrument"}: raise ValueError("Unknown resource kind")
        _texts(resource["capabilities"], 32, 1); _count(resource["batch_capacity"])
        limits = {}
        for limit in _list(resource["limits"], 32):
            keys(limit, {"parameter", "unit", "lower", "upper"})
            name = text(limit["parameter"]); text(limit["unit"])
            if name in limits or _number(limit["lower"], -1e9) > _number(limit["upper"], -1e9):
                raise ValueError("Duplicate or unordered resource envelope")
            limits[name] = limit
        if resource["rate_per_hour"] is not None: _number(resource["rate_per_hour"])
        if resource["currency"] is not None and re.fullmatch(r"[A-Z]{3}", resource["currency"]) is None:
            raise ValueError("Currency must be an explicit three-letter label")
        if resource["rate_per_hour"] is not None and resource["currency"] is None:
            raise ValueError("Cost requires an explicit currency")
    steps = _named(plan["operations"], "operation_id", MAX_STEPS)
    for name, step in steps.items():
        keys(step, {"operation_id", "kind", "depends_on", "artifact_paths", "requires", "demands",
                    "allowed_resources", "setup_s", "per_batch_s", "batch_size", "adapter_id"})
        if step["kind"] not in {"fabricate", "procure", "assemble", "inspect", "package"}:
            raise ValueError("Unknown manufacturing operation kind")
        deps = _texts(step["depends_on"], MAX_STEPS)
        if name in deps or not set(deps) <= set(steps): raise ValueError("Unresolved operation dependency")
        _texts(step["requires"], 32, 1)
        if not set(_texts(step["allowed_resources"], MAX_RESOURCES, 1)) <= set(resources):
            raise ValueError("Unknown explicit resource binding")
        for path in _texts(step["artifact_paths"], 32, 1): _artifact(path, files, roles)
        seen = set()
        for demand in _list(step["demands"], 32):
            keys(demand, {"parameter", "unit", "value", "source"})
            parameter = text(demand["parameter"]); text(demand["unit"]); _number(demand["value"], -1e9)
            if parameter in seen: raise ValueError("Duplicate process demand")
            seen.add(parameter)
            if demand["source"] is not None and not project.same(_lookup(demand["source"], files, roles), demand["value"]):
                raise ValueError("Process demand differs from retained design field")
        _number(step["setup_s"]); _number(step["per_batch_s"]); _count(step["batch_size"])
        text(step["adapter_id"])
    _order(steps)
    return spec, inventory, files, roles, plan, requirements, resources, steps


def _schedule(plan: dict, steps: dict, resources: dict) -> dict:
    """Deterministic greedy whole-lot schedule, not a globally optimal planner."""
    available = {name: Decimal(0) for name in resources}
    busy = {name: Decimal(0) for name in resources}
    finish, instructions, blocked = {}, [], set()
    costs, all_costs_known, currencies = [], True, set()
    for name in _order(steps):
        step = steps[name]
        batches = (plan["order"]["quantity"] + step["batch_size"] - 1) // step["batch_size"]
        duration = _number(step["setup_s"]) + batches * _number(step["per_batch_s"])
        rejected, eligible = {}, []
        for rid in sorted(step["allowed_resources"]):
            resource = resources[rid]
            reasons = []
            if step["batch_size"] > resource["batch_capacity"]: reasons.append("INSUFFICIENT_BATCH_CAPACITY")
            if not set(step["requires"]) <= set(resource["capabilities"]): reasons.append("MISSING_CAPABILITY")
            limits = {v["parameter"]: v for v in resource["limits"]}
            for demand in step["demands"]:
                limit = limits.get(demand["parameter"])
                if limit is None:
                    reasons.append("UNKNOWN_ENVELOPE:" + demand["parameter"])
                elif limit["unit"] != demand["unit"]:
                    reasons.append("UNIT_MISMATCH:" + demand["parameter"])
                elif not _number(limit["lower"], -1e9) <= _number(demand["value"], -1e9) <= _number(limit["upper"], -1e9):
                    reasons.append("OUTSIDE_ENVELOPE:" + demand["parameter"])
            if reasons: rejected[rid] = reasons
            else: eligible.append(rid)
        predecessor_blocked = bool(set(step["depends_on"]) & blocked)
        record = {"operation_id": name, "operation_ref": content_identity(step), "kind": step["kind"],
                  "depends_on": sorted(step["depends_on"]), "adapter_id": step["adapter_id"],
                  "batches": batches, "duration_s": float(duration), "artifact_paths": deepcopy(step["artifact_paths"]),
                  "rejected_resources": rejected, "resource_id": None, "resource_ref": None,
                  "start_s": None, "finish_s": None, "physical_execution": False}
        if predecessor_blocked or not eligible:
            blocked.add(name)
            record["status"] = "BLOCKED_PREDECESSOR" if predecessor_blocked else "NO_COMPATIBLE_RESOURCE"
        else:
            predecessor_finish = max((finish[v] for v in step["depends_on"]), default=Decimal(0))
            rid = min(eligible, key=lambda r: (max(available[r], predecessor_finish) + duration, r))
            start = max(available[rid], predecessor_finish); end = start + duration
            available[rid] = end; busy[rid] += duration; finish[name] = end
            resource = resources[rid]
            record.update(status="PLANNED", resource_id=rid, resource_ref=content_identity(resource),
                          start_s=float(start), finish_s=float(end))
            if resource["rate_per_hour"] is None:
                all_costs_known = False
            else:
                costs.append(duration * _number(resource["rate_per_hour"]) / Decimal(3600))
                currencies.add(resource["currency"])
        instructions.append(record)
    makespan = max(finish.values(), default=Decimal(0))
    return {"status": "INFEASIBLE" if blocked else "FEASIBLE_UNDER_DECLARATIONS",
            "algorithm": "lexical-topological_greedy-earliest-finish_whole-lot_exclusive-resource",
            "optimality": "not_claimed", "clock": "relative_simulation_seconds",
            "makespan_s": None if blocked else float(makespan), "scheduled_prefix_finish_s": float(makespan),
            "modeled_processing_cost": float(sum(costs)) if all_costs_known and len(currencies) == 1 and not blocked else None,
            "currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "cost_scope": "declared_processing_only_excludes_materials_freight_yield_and_unmodeled_lead_time",
            "resource_busy_s": {k: float(v) for k, v in sorted(busy.items())}, "instructions": instructions}


def _inspections(run: dict, plan: dict, requirements: dict, steps: dict, files: dict) -> list[dict]:
    # Reuse the existing native stream validator, not a second observation schema.
    projection = project.compile_project(run, PARAMETERS)
    observations = {row["observation"]["record_digest"]: row for row in projection["observations"]}
    expected = {(unit, req) for unit in plan["order"]["unit_ids"] for req in requirements}
    seen, used, result = set(), set(), []
    for item in _list(plan["inspections"], MAX_UNITS * 32):
        keys(item, {"unit_id", "requirement_id", "operation_id", "observation_ref", "expected_identity", "frame", "clock_id", "required_source_paths"})
        key = (text(item["unit_id"]), text(item["requirement_id"]))
        if key not in expected or key in seen: raise ValueError("Missing, duplicate or unrelated unit inspection")
        seen.add(key)
        if item["operation_id"] not in steps or steps[item["operation_id"]]["kind"] != "inspect":
            raise ValueError("Inspection must bind an inspection operation")
        identity = item["expected_identity"]
        keys(identity, {"model_id", "entity_id"})
        for v in identity.values(): text(v)
        text(item["frame"]); text(item["clock_id"])
        requirement = requirements[key[1]]
        required_paths = _texts(item["required_source_paths"], 32)
        if any(p not in files for p in required_paths):
            raise ValueError("Unresolved declared measurement-source artifact")
        out = {"unit_id": key[0], "requirement_id": key[1], "operation_id": item["operation_id"],
               "criterion_basis": requirement["criterion_basis"], "observation_ref": item["observation_ref"],
               "nominal_comparison": "PENDING", "measurement": None,
               "qualification": "NOT_ESTABLISHED", "acceptance": "NOT_AUTHORIZED",
               "unit_association": "operator_declared_not_authenticated_genealogy",
               "procedure_binding": "PENDING"}
        if item["observation_ref"] is not None:
            ref = content_ref(item["observation_ref"])
            if ref in used or ref not in observations: raise ValueError("Missing or reused unit observation")
            used.add(ref)
            original = observations[ref]; row = original["observation"]
            if ({k: row["identity"][k] for k in identity} != identity or row["frame"] != item["frame"]
                    or row["clock"]["id"] != item["clock_id"] or row["quantity"] != requirement["quantity"]
                    or row["unit"] != requirement["unit"]):
                raise ValueError("Inspection identity/unit/frame/clock differs from original observation")
            if not {bytes_ref(files[p]) for p in required_paths} <= set(row["provenance"]["sources"]):
                raise ValueError("Observation lacks required original procedure/design references")
            value = _number(row["value"], -1e9)
            if requirement["criterion_basis"] == "unspecified":
                status = "CRITERION_UNSPECIFIED"
            else:
                status = "NOMINAL_WITHIN" if _number(requirement["lower"], -1e9) <= value <= _number(requirement["upper"], -1e9) else "NOMINAL_OUTSIDE"
            out.update(nominal_comparison=status, measurement=deepcopy(original),
                       procedure_binding=("ORIGINAL_SOURCE_REFERENCE" if bytes_ref(files[requirement["procedure_path"]]) in row["provenance"]["sources"] else "PLANNING_ASSOCIATION_ONLY"))
        result.append(out)
    if seen != expected: raise ValueError("Inspection matrix must cover every planned unit and requirement, including pending entries")
    return sorted(result, key=lambda v: (v["unit_id"], v["requirement_id"]))


def compile_definition(run: dict, parameters: dict) -> dict:
    if not project.same(parameters, PARAMETERS): raise ValueError("Select the entire product declaration")
    spec, inventory, files, roles, plan, requirements, resources, steps = _definition(run)
    bom = _bom(plan["bom"], files, roles, plan["order"]["quantity"])
    schedule = _schedule(plan, steps, resources)
    inspections = _inspections(run, plan, requirements, steps, files)
    return {"schema": RESULT, "definition_ref": bytes_ref(files[PLAN_PATH]),
            "inventory_id": inventory["inventory_id"], "project_mode": spec["mode"],
            "product": deepcopy(plan["product"]), "order": deepcopy(plan["order"]),
            "product_artifacts": {p: inventory["files"][p]["sha256"] for p in plan["product"]["native_paths"]},
            "requirements_ref": bytes_ref(files[plan["requirements_path"]]),
            "work_package_type": "planning_instructions_not_machine_programs",
            "bom": bom, "schedule": schedule, "inspections": inspections,
            "physical_units_established": 0, "claims": deepcopy(CLAIMS)}


def _validate_payload(operation, data, run, parameters, selection):
    if operation != OPERATION or not project.same({k: selection[k] for k in ("channel", "interval_s")}, PARAMETERS):
        raise ValueError("Wrong manufacturing planning operation or selection")
    if not project.same(data, compile_definition(run, parameters)):
        raise ValueError("Manufacturing plan differs from retained source computation")


def _validate_analysis(operation, data, run, parameters, selection):
    from .sensor_manufacturing_analysis import analyze
    if operation != ANALYSIS_OPERATION or not project.same({k: selection[k] for k in ("channel", "interval_s")}, PARAMETERS):
        raise ValueError("Wrong manufacturing analysis operation or selection")
    if not project.same(data, analyze(run, parameters)):
        raise ValueError("Manufacturing analysis differs from retained source computation")


def _validate_timing(operation, data, run, parameters, selection):
    from . import sensor_manufacturing_timing as timing
    if operation != timing.OPERATION or not project.same({k: selection[k] for k in ("channel", "interval_s")}, PARAMETERS):
        raise ValueError("Wrong timing characterization operation or selection")
    if not project.same(data, timing.characterize(run, parameters)):
        raise ValueError("Timing characterization differs from retained source computation")


def _validate_uncertainty(operation, data, run, parameters, selection):
    from . import sensor_manufacturing_uncertainty as uncertainty
    if operation != uncertainty.OPERATION or not project.same({k: selection[k] for k in ("channel", "interval_s")}, PARAMETERS):
        raise ValueError("Wrong timing uncertainty operation or selection")
    if not project.same(data, uncertainty.characterize(run, parameters)):
        raise ValueError("Timing uncertainty differs from retained source computation")


def register_schema() -> None:
    global _REGISTERED
    project.register_schema()
    with _LOCK:
        if not _REGISTERED:
            register_payload_validator(OPERATION, _validate_payload, role="analysis")
            register_payload_validator(ANALYSIS_OPERATION, _validate_analysis, role="analysis")
            from .sensor_manufacturing_timing import OPERATION as TIMING
            register_payload_validator(TIMING, _validate_timing, role="analysis")
            from .sensor_manufacturing_uncertainty import OPERATION as UNCERTAINTY
            register_payload_validator(UNCERTAINTY, _validate_uncertainty, role="analysis")
            _REGISTERED = True


def runtime_identity() -> dict:
    return {"provider": "ciw.sensor-manufacturing", "version": "1", "python": platform.python_version(),
            "sources": {name: bytes_ref(Path(__file__).with_name(name).read_bytes().replace(b"\r\n", b"\n"))
                        for name in ("sensor_manufacturing.py", "sensor_project.py", "control_contracts.py", "foundry_packets.py")},
            "scope": "declared_manufacturing_plan_and_nominal_inspection_review"}


def analysis_runtime_identity() -> dict:
    return {**runtime_identity(), "version": "2", "analysis_source": bytes_ref(
        Path(__file__).with_name("sensor_manufacturing_analysis.py").read_bytes().replace(b"\r\n", b"\n"))}


def timing_runtime_identity() -> dict:
    return {**analysis_runtime_identity(), "timing_source": bytes_ref(
        Path(__file__).with_name("sensor_manufacturing_timing.py").read_bytes().replace(b"\r\n", b"\n"))}


def uncertainty_runtime_identity() -> dict:
    return {**timing_runtime_identity(), "uncertainty_sources": {
        name: bytes_ref(Path(__file__).parent.joinpath(name).read_bytes().replace(b"\r\n", b"\n"))
        for name in ("sensor_manufacturing_uncertainty.py", "core/covariance.py")}}


def registry() -> CapabilityRegistry:
    from .sensor_manufacturing_analysis import analyze
    from . import sensor_manufacturing_timing as timing
    from . import sensor_manufacturing_uncertainty as uncertainty
    register_schema(); result = CapabilityRegistry()
    for operation, version, compute, runtime, capability in (
        (OPERATION, "1", compile_definition, runtime_identity, "engineering.manufacturing-plan"),
        (ANALYSIS_OPERATION, "2", analyze, analysis_runtime_identity, "engineering.manufacturing-analysis"),
        (timing.OPERATION, "timing-1", timing.characterize, timing_runtime_identity, "engineering.manufacturing-timing"),
        (uncertainty.OPERATION, "timing-2", uncertainty.characterize, uncertainty_runtime_identity, "engineering.manufacturing-timing-uncertainty"),
    ):
        manifest = InstrumentManifest(instrument_id="sensor-manufacturing-planning.v" + version,
            role="engineering_projection", units={project.CHANNEL: "1"},
            frames=("project-declaration-not-physical-clock",), sampling={"kind": "one_declaration"},
            supported_operations=(operation,), calibration_requirements={"physical_qualification": "not_established"})
        result.advertise(manifest, runtime=runtime(), capabilities={operation: [capability]})
        result.bind(Operation(operation, "analysis", compute, runtime))
    return result


def execute(run: dict, output: str | Path, *, analyze: bool = False, characterize_timing: bool = False, timing_uncertainty: bool = False) -> dict:
    if type(analyze) is not bool:
        raise ValueError("Analysis selection must be an explicit boolean")
    if type(characterize_timing) is not bool:
        raise ValueError("Timing selection must be an explicit boolean")
    if type(timing_uncertainty) is not bool:
        raise ValueError("Timing uncertainty selection must be an explicit boolean")
    if sum((analyze, characterize_timing, timing_uncertainty)) > 1:
        raise ValueError("Select one explicit analysis mode")
    from .sensor_manufacturing_timing import OPERATION as TIMING
    from .sensor_manufacturing_uncertainty import OPERATION as UNCERTAINTY
    project.context(run)
    output = project._destination(output); output.mkdir(parents=True, exist_ok=False)
    session = Session(deepcopy(run), output, operations=registry().operations)
    response = session.handle({"protocol_version": 1, "request_id": "manufacturing-plan", "type": "operation.execute",
                               "payload": {"operation_id": UNCERTAINTY if timing_uncertainty else (TIMING if characterize_timing else (ANALYSIS_OPERATION if analyze else OPERATION)), "parameters": {}}})
    session.save_workspace(output / "workspace.json")
    if response["type"] == "error": raise ValueError(str(response["payload"]))
    return inspect(output)


def load(directory: str | Path, expected: str | None = None) -> tuple[Session, str]:
    register_schema()
    directory = project.packets.root_dir(directory)
    path = directory / "workspace.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > project.MAX_WORKSPACE:
        raise ValueError("Invalid manufacturing workspace")
    raw = path.read_bytes()
    if expected is not None and bytes_ref(raw) != content_ref(expected): raise ValueError("Workspace anchor changed")
    with tempfile.TemporaryDirectory(prefix="net-manufacturing-inspect-") as tmp:
        p = Path(tmp); (p / "workspace.json").write_bytes(raw)
        session = Session.from_workspace(p / "workspace.json", p / "restored")
    project.context(session.run)
    if len(session.executions) != 1 or len(session.results) > 1 or next(iter(session.executions.values()))["operation_id"] not in {OPERATION, ANALYSIS_OPERATION, "engineering.characterize-manufacturing-time.v1", "engineering.characterize-manufacturing-time.v2"}:
        raise ValueError("Expected one native manufacturing-plan execution")
    return session, bytes_ref(raw)


def inspect(directory: str | Path, expected: str | None = None) -> dict:
    session, anchor = load(directory, expected)
    occurrence = next(iter(session.executions.values())); result = session.results.get(occurrence.get("result_id"))
    return {"status": "RETAINED" if result else "REFUSED", "snapshot_ref": anchor,
            "evidence_id": session.run["evidence_id"], "execution_id": occurrence["execution_id"],
            "result_id": result["result_id"] if result else None, "data": deepcopy(result["data"]) if result else None,
            "refusal": occurrence.get("refusal"), "claims": deepcopy(CLAIMS)}


def replay(directory: str | Path, output: str | Path, expected: str | None = None) -> dict:
    session, _ = load(directory, expected)
    operation = next(iter(session.executions.values()))["operation_id"]
    return execute(session.run, output, analyze=operation == ANALYSIS_OPERATION,
                   characterize_timing=operation == "engineering.characterize-manufacturing-time.v1",
                   timing_uncertainty=operation == "engineering.characterize-manufacturing-time.v2")


def export_inputs(directory: str | Path, output: str | Path, expected: str | None = None) -> dict:
    session, _ = load(directory, expected)
    _, inventory, files = project.context(session.run)
    output = project._destination(output); output.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        dest = output / name; dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("xb") as stream: stream.write(raw)
        dest.chmod(0o755 if inventory["files"][name]["executable"] else 0o644)
    if project.packets.inventory(output) != inventory: raise ValueError("Original manufacturing input reconstruction differs")
    return {"status": "EXPORTED_ORIGINAL_INPUTS", "inventory_id": inventory["inventory_id"], "claims": deepcopy(CLAIMS)}
