"""Read-only inventory of NET data routes and independent local packages.

The live Session registries remain authoritative. This view names retention,
parsing, mapping and consuming callables separately; it never installs, binds,
probes a provider runtime, or interprets presence of source as qualification.
Use ``inventory(repository=Path(...), session=session)`` to include a checkout's
consolidated package manifest and the actual Session's additional bindings.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from importlib import import_module
import inspect
import json
from pathlib import Path
import tomllib

SCHEMA = "ciw.data-integration-inventory.v1"


def _name(function) -> str | None:
    if not callable(function):
        return None
    return function.__module__ + "." + function.__qualname__


def _source_kinds() -> list[str]:
    # Installed dispatch is trusted code, never a source document's import name.
    from . import workbench
    kinds = set(workbench.OPERATIONS)
    tree = ast.parse(inspect.getsource(workbench._workflow))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name) or node.left.id != "kind":
            continue
        for operand in node.comparators:
            if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
                kinds.add(operand.value)
            elif isinstance(operand, (ast.Set, ast.List, ast.Tuple)):
                kinds.update(item.value for item in operand.elts
                             if isinstance(item, ast.Constant) and isinstance(item.value, str))
    return sorted(kinds)


def _routes() -> list[dict]:
    # These are bounded, installed routes, not universal family converters.
    specs = [
        ("product.preliminary-study", ["product-study"],
         "ciw.product_study.validate", "ciw.data_sources.retain_record",
         ["ciw.product_study.review"], "Existing versioned data-record, Workbench and Session occurrences",
         [], ["tests/test_product_study.py"],
         "Explicit selected-artifact mapping and scalar comparisons only. Private deliveries stay local. Physical release remains blocked."),
        ("tables.csv-observations", ["tables", "time-series"],
         "ciw.data_sources.csv_observation_stream", "ciw.data_sources.csv_observation_stream",
         ["ciw.control_checks.compare"], "Exact CSV bytes plus explicit column/unit/clock mapping; Workbench data-record source",
         [], ["tests/test_data_sources.py", "tests/test_data_integration_matrix.py"],
         "Each column must be mapped. No inferred units, missing-cell imputation or general dataframe execution."),
        ("tables.columnar-observations", ["tables", "time-series"],
         "ciw.columnar.decode", "ciw.columnar.encode", ["ciw.control_checks.compare"],
         "Original observation stream plus byte-bound interchange artifact",
         ["pyarrow==25.0.1"], ["tests/test_data_integration_matrix.py", "tests/test_columnar_optional.py"],
         "Homogeneous scalar Float64 only; uncertainty, nulls, mixed descriptors and coercion refuse."),
        ("time-series.recording", ["time-series", "simulation"],
         "ciw.instruments.validate_run", "ciw.control_plane.observations_from_run",
         ["ciw.instruments.compute_statistics", "ciw.instruments.compute_spectrum", "ciw.control_checks.compare"],
         "Existing Session run, occurrence records and workspace",
         [], ["tests/test_data_integration_matrix.py", "tests/test_control_plane.py"],
         "Statistics/spectrum require the run's bound adapter. Projecting a run does not establish sensor origin."),
        ("time-series.acquired-window", ["tables", "time-series"],
         "ciw.acquired_window._source", "ciw.acquired_window.create_session",
         ["ciw.calibrated_window.create_session"], "Workbench acquisition and child-window bundles",
         ["explicit pinned PPDA/TBRT/MCUR/STFE/GSIE/SET bindings"], ["tests/test_acquired_window.py"],
         "Explicit retained acquisition/window mapping; no automatic polling, clock recovery or inter-window covariance."),
        ("time-series.temperature-recording", ["time-series", "covariance", "simulation"],
         "ciw.temperature_contract.validate_request", "ciw.temperature_recording_bridge.bridge_temperature_recording",
         ["ciw.measured_thermal_workflow.MeasuredThermalWorkflow.create_session"],
         "Full temperature request/report, covariance ancestry and exact measured-thermal recording artifacts", [],
         ["tests/test_temperature_recording_bridge.py"],
         "Exact selected channel/device/clock/sample agreement and fresh numerical audit. Physical calibration and held-out independence remain separate."),
        ("geometry.bim-quantity", ["geometry", "tables"],
         "ciw.bim_quantity._source", "ciw.bim_interop.execute_bim_mapping_bundle",
         ["ciw.bim_quantity.BimQuantityWorkflow.create_session"],
         "Exact IFC evidence, native CSE mapping bundle and identity-bound mapping witness",
         ["explicit pinned CSE binding and profile dependencies"], ["tests/test_bim_interop.py", "tests/test_bim_quantity.py"],
         "Qualified bounded IFC quantity mapping; no general CAD import or arbitrary geometric conversion."),
        ("geometry.translation-flow", ["geometry", "simulation"],
         "ciw.geometry_translation_contract.validate_request", None,
         ["ciw.geometry_research.GeometryResearchWorkflow.create_session"],
         "Exact rational request, event/segment chain and partial-completion status",
         ["explicit pinned TSDE binding for Workbench execution"],
         ["tests/test_geometry_translation_contract.py", "tests/test_data_integration_matrix.py"],
         "Local-package execution is tested separately from Workbench pin qualification. Vertex/budget stops remain partial trajectories."),
        ("geometry.mesh-path", ["geometry", "graphs"],
         "ciw.geometry_mesh_contract.validate_request", None,
         ["ciw.geometry_research.GeometryResearchWorkflow.create_session"], "Workbench exact mesh request and native bundle",
         ["explicit pinned ISGT binding"], ["tests/test_geometry_research.py"],
         "Edge-constrained path on a declared mesh; no general CAD conversion or continuum geodesic claim."),
        ("geometry.covariance", ["geometry", "covariance"],
         "ciw.geometry_covariance_contract.validate_request", None,
         ["ciw.geometry_research.GeometryResearchWorkflow.create_session"], "Workbench SPD geometry request and native bundle",
         ["explicit pinned CGGT binding"], ["tests/test_geometry_research.py"],
         "Bounded SPD geometry profile; retaining covariance does not execute a geometry provider."),
        ("geometry.local-frame", ["geometry", "time-series"],
         "ciw.spatial_records.validate_payload", "ciw.spatial_scene.projection",
         ["ciw.spatial_scene.html_bytes", "ciw.spatial_scene.usd_bytes"], "Session local-frame result and detached scene export",
         ["explicit pinned local-frame provider for coordinate computation", "usd-core optional for USD export"],
         ["tests/test_spatial_provider.py"],
         "Retained WGS84-to-ENU profile and display copy; no surveyed-frame authority or covariance propagation."),
        ("fields.wave-observations", ["grid-fields", "time-series", "simulation"],
         "ciw.fluid_wave_contract.validate_result", "ciw.data_field_bridge.wave_observations",
         ["ciw.control_checks.compare"], "Complete wave request/result, explicit cell/face selection and observation stream",
         [], ["tests/test_fluid_wave.py", "tests/test_data_integration_matrix.py"],
         "One exact site and trace; no spatial/time interpolation, resampling or measured-data semantics."),
        ("fields.impact-plate", ["grid-fields", "geometry", "simulation"],
         "ciw.impact_plate_contract.validate_result", "ciw.impact_workflow.export_plate_field",
         ["ciw.impact_workflow.export_plate_field"], "Session modal history plus typed finite-basis displacement grid",
         [], ["tests/test_impact_plate_workflow.py"],
         "Finite modal reconstruction after fresh numerical audit; no generic mesh association, stress or continuum qualification."),
        ("graphs.compute-canonical", ["graphs", "geometry", "tables"],
         "ciw.data_compute_bridge.validate_record", "ciw.data_compute_bridge.canonical_record",
         ["ciw.data_compute_bridge.ComputeRuntime.execute"],
         "Complete native Compute Runtime version, ordered edges and typed fields in Workbench data-record",
         ["explicit process-local binding to consolidated Compute Runtime source"],
         ["tests/test_data_compute_bridge.py", "tests/test_data_integration_matrix.py"],
         "Executes the native projection, preserves vector/quaternion shape and graph order; does not provide arbitrary tensors or state admission."),
        ("simulation.polymer-cycle", ["tables", "time-series", "simulation"],
         "ciw.polymer_contract.validate_request", "ciw.polymer_workflow.make_source",
         ["ciw.data_compute_bridge._polymer"],
         "Complete native polymer request retained as data-record and consumed by a Session operation", [],
         ["tests/test_data_compute_bridge.py"],
         "Existing bounded polymer assessment; unsupported correlated/cross-clock inputs remain subject to its native refusals."),
        ("graphs.project", ["graphs"],
         "ciw.project_model.validate", "ciw.project_workflow.validate_source",
         ["ciw.project_model.inspect", "ciw.project_workflow.ProjectGraphWorkflow.create_session"],
         "Project revision history, Workbench source and inspection bundle", [],
         ["tests/test_project_model.py", "tests/test_project_workflow.py", "tests/test_data_integration_matrix.py"],
         "Typed declarations and dependency status; graph inspection does not execute declared computations."),
        ("simulation.scientific-system", ["simulation", "grid-fields", "time-series"],
         "ciw.system_spec.validate_spec", "ciw.system_spec.compile_spec",
         ["ciw.system_workflow.run_specification"], "Session source, compiled plan, candidate and verification occurrences",
         ["external executable/container only when explicitly selected; local reference uses base dependencies"],
         ["tests/test_system_workflow.py", "tests/test_data_integration_matrix.py"],
         "Explicit bounded system models. No universal solver loading, physical validation or automatic state admission."),
    ]
    rows = []
    for route_id, families, parser, mapping, consumers, retention, runtimes, tests, limits in specs:
        rows.append({"route_id": route_id, "families": families, "retention": retention,
                     "parser": parser, "mapping": mapping, "consumers": consumers,
                     "runtime_requirements": runtimes,
                     "executable_evidence": {"tests": tests, "status": "test_references_not_run_by_inventory"},
                     "limits": limits})
    return rows


def _callable_exists(reference: str | None) -> bool:
    if reference is None:
        return False
    # Only installed references from the constant route list enter here.
    parts = reference.split(".")
    for boundary in range(len(parts) - 1, 0, -1):
        try:
            value = import_module(".".join(parts[:boundary]))
        except ModuleNotFoundError as error:
            if error.name != ".".join(parts[:boundary]):
                raise
            continue
        for name in parts[boundary:]:
            value = getattr(value, name, None)
        return callable(value)
    return False


def _read_metadata(path: Path) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if not 0 < len(raw) <= 1024 * 1024:
        raise ValueError("Inventory metadata exceeds its 1 MiB byte budget")
    return raw


def _unique_fields(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("Duplicate instrument manifest field")
        value[key] = item
    return value


def _local_packages(repository: Path | None, requirements: dict) -> dict:
    if repository is None:
        candidate = Path(__file__).resolve().parents[2]
        repository = candidate if (candidate / "instruments/manifest.json").is_file() else None
    if repository is None:
        return {"status": "source_checkout_not_available", "manifest": None, "packages": []}
    root = Path(repository).resolve()
    manifest = root / "instruments/manifest.json"
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError("Repository needs a regular instruments/manifest.json")
    raw = _read_metadata(manifest)
    value = json.loads(raw, object_pairs_hook=_unique_fields)
    if not isinstance(value, dict) or value.get("schema") != "notations.monorepo-imports.v1" or not isinstance(value.get("modules"), list):
        raise ValueError("Unsupported consolidated instrument manifest")
    if len(value["modules"]) > 256:
        raise ValueError("Instrument manifest exceeds the inventory package budget")
    packages = []
    identifiers = set()
    for row in value["modules"]:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("Instrument manifest entry needs an explicit relative path")
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError("Instrument manifest requires distinct nonempty package identities")
        identifiers.add(identifier)
        relative = Path(row["path"])
        if (relative.is_absolute() or not relative.parts or relative.parts[0] != "instruments"
                or any(part in {".", ".."} for part in relative.parts)):
            raise ValueError("Instrument package path must remain under instruments")
        path = root / relative
        if not path.resolve().is_relative_to(root / "instruments"):
            raise ValueError("Instrument package path escapes the source inventory")
        metadata = {}
        pyproject = path / "pyproject.toml"
        if pyproject.is_file() and not pyproject.is_symlink():
            raw_project = _read_metadata(pyproject)
            project = tomllib.loads(raw_project.decode("utf-8")).get("project", {})
            metadata = {"dependencies": project.get("dependencies", []),
                        "optional_dependencies": project.get("optional-dependencies", {}),
                        "requires_python": project.get("requires-python")}
        role = row.get("role")
        packages.append({**deepcopy(row), "source_present": path.is_dir(),
                         "package_metadata": metadata,
                         "host_workflows": sorted(kind for kind, requirement in requirements.items()
                                                   if role in requirement["providers"]),
                         "binding_status": "source_presence_does_not_install_or_bind",
                         "execution_evidence": "not_executed_by_inventory"})
    return {"status": "manifest_read", "manifest": str(manifest), "packages": packages}


def inventory(*, repository: Path | str | None = None, session=None) -> dict:
    """Return detached source, operation, provider and family route descriptions.

    Supplying a Session reports its actual registered operations and Workbench
    availability. Additional standalone workflow factories are listed separately
    as explicit choices, never as already active in that Session.
    """
    from .data_sources import SCHEMA_FAMILIES
    from .adapters.registry import default_registry as adapter_registry
    from .operations.registry import default_registry
    from .operator_provision import catalog as provision_catalog
    from .provider_catalog import catalog as roadmap_catalog
    from .workbench import OPERATIONS, Workbench, _workflow

    workbench = Workbench() if session is None else session.workbench
    registry = default_registry() if session is None else session.operations
    requirements = {row["workflow"]: row for row in provision_catalog()["workflows"]}
    workbench_operations = workbench.describe_operations()
    by_kind = {row["source_kind"]: row for row in workbench_operations if "source_kind" in row}
    sources = []
    for kind in _source_kinds():
        workflow = _workflow(kind)
        requirement = requirements.get(kind, {"providers": {}, "external_bindings": {}})
        sources.append({
            "kind": kind, "source_schema": getattr(workflow, "SOURCE_SCHEMA", {
                # These two legacy parsers validate inline schema literals.
                "calibrated-observable": "fsrt.calibrated-observable-two-channel.v1",
                "telemetry": "ciw.telemetry-source.v1",
            }.get(kind)),
            "retention": "Workbench exact source bytes and content identity",
            "parser": _name(getattr(workflow, "_source", None)),
            "ingress": "ciw.workbench._source",
            "consumer": _name(getattr(workflow, "create_session", None)) if kind in OPERATIONS else None,
            "operation_id": OPERATIONS.get(kind),
            "available_in_session": by_kind.get(kind, {}).get("available", False),
            "upstream_required": by_kind.get(kind, {}).get("requires_upstream_bundle", False),
            "providers": deepcopy(requirement["providers"]),
            "external_bindings": deepcopy(requirement["external_bindings"]),
            "status": "workflow_route" if kind in OPERATIONS else "retention_route_without_workbench_execution",
            "qualification": "not_performed_by_inventory",
        })
    operations = []
    for descriptor in registry.describe():
        operation = registry.get(descriptor["operation_id"])
        operations.append({**descriptor, "consumer": _name(operation.execute),
                           "runtime_identity_callable": _name(operation.runtime_identity),
                           "registration": "current_session" if session is not None else "default_session"})
    explicit = []
    for name in ("atmosphere", "fluid", "impact", "irrigation", "leakage", "polymer", "temperature"):
        module = import_module("ciw." + name + "_workflow")
        for operation in module.operations():
            explicit.append({**operation.descriptor(), "factory": module.__name__ + ".operations",
                             "consumer": _name(operation.execute),
                             "registration": "explicit_host_factory_not_automatically_bound",
                             "runtime_requirements": (["explicit pinned leakage backend"] if name == "leakage" else []),
                             "qualification": "not_performed_by_inventory"})
    from .hardware_workflow import operation as hardware_operation
    operation = hardware_operation()
    explicit.append({**operation.descriptor(), "factory": "ciw.hardware_workflow.operation",
                     "consumer": _name(operation.execute),
                     "registration": "explicit_host_factory_not_automatically_bound",
                     "runtime_requirements": [], "qualification": "not_performed_by_inventory"})
    from .data_compute_bridge import PROJECT
    explicit.append({"operation_id": PROJECT, "role": "backend",
                     "factory": "ciw.data_compute_bridge.ComputeRuntime.operation",
                     "consumer": "ciw.data_compute_bridge.ComputeRuntime.execute",
                     "registration": "explicit_host_factory_not_automatically_bound",
                     "runtime_requirements": ["explicit process-local Compute Runtime source binding"],
                     "qualification": "not_performed_by_inventory"})
    routes = _routes()
    for route in routes:
        references = [route["parser"], route["mapping"], *route["consumers"]]
        route["callable_checks"] = {reference: _callable_exists(reference) for reference in references if reference}
    local = _local_packages(Path(repository) if repository is not None else None, requirements)
    return {"schema": SCHEMA, "read_only": True, "authorizes_execution": False,
            "runtime_probing": "not_performed", "qualification": "not_performed_by_inventory",
            "sources": sources, "typed_native_schemas": deepcopy(SCHEMA_FAMILIES), "operations": operations, "workbench_operations": workbench_operations,
            "explicit_workload_operations": explicit, "adapter_providers": adapter_registry().manifests(),
            "local_packages": local, "provider_roadmap": roadmap_catalog(), "family_routes": routes,
            "limits": [
                "Runtime identities are not invoked and local packages are not imported or bound.",
                "Source presence, parser validation, mapping and actual numerical execution are separate claims.",
                "Typed source envelopes are bounded to 32 MiB and the Workbench to 64 MiB; large sources permit only a few datasets per workspace.",
                "No general CAD conversion, arbitrary field resampling, universal dataset compiler or corpus ingestion is claimed.",
                "Test references are executable evidence locations, not a fresh pass result.",
            ]}
