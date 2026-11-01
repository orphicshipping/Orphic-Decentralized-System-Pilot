"""The workbench folded into one MCP call.

Every session workflow is named here. The call does not pick the easiest one.
Workflows that already run without a host pin use their retained fixtures.
The others stay on their documentation and evidence and refuse until the host
selects that binding. This module does not install a provider or delete a record.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Order matches ciw.workbench.OPERATIONS. listing() refuses if they drift.
WORKFLOWS = (
    "calibrated-observable",
    "identified-design",
    "telemetry",
    "calibrated-window",
    "schematic-assessment",
    "numerical-heat",
    "proved-heat",
    "schematic-companions",
    "bim-quantity",
    "acquired-dataset",
    "acquired-calibrated-window",
    "residual-monitor",
    "measurement-chain",
    "geometric-circle",
    "identified-stability",
    "flat-torus-reference",
    "curved-path-transfer",
    "covariance-geometry",
    "mesh-path",
    "translation-flow",
    "variational-free-energy",
    "energy-accuracy",
    "instrument-exchange",
    "thermal-observer",
    "measured-thermal",
    "machine-manifest",
    "julia-oscillator",
    "native-interop",
    "project-graph",
)

# These five are bound on a fresh Workbench. The rest need an explicit host checkout.
UNBOUND = frozenset({
    "energy-accuracy",
    "machine-manifest",
    "measured-thermal",
    "project-graph",
    "thermal-observer",
})

_FIXTURES = {
    "energy-accuracy": "examples/energy-accuracy/baseline.json",
    "machine-manifest": "examples/corrections/encoder-original.json",
    "measured-thermal": "examples/measured-thermal/source.json",
    "thermal-observer": "examples/thermal-observations/source.json",
}

_DOCS = {
    "energy-accuracy": "docs/ENERGY_ACCURACY.md",
    "measured-thermal": "docs/MEASURED_THERMAL.md",
    "thermal-observer": "docs/MEASURED_THERMAL.md",
    "proved-heat": "docs/PROVED_HEAT.md",
    "variational-free-energy": "docs/VARIATIONAL_FREE_ENERGY.md",
    "julia-oscillator": "docs/JULIA_OSCILLATOR.md",
    "native-interop": "docs/NATIVE_INTEROP.md",
    "machine-manifest": "docs/CONTRACT_FOUNDATIONS.md",
    "project-graph": "docs/PROTOCOL.md",
    "bim-quantity": "docs/METROLOGY_CLEARANCE.md",
    "measurement-chain": "docs/LEARNING.md",
}


def _doc(kind: str) -> str | None:
    relative = _DOCS.get(kind)
    if relative and (ROOT / relative).is_file():
        return relative
    return None


def listing() -> dict:
    from .workbench import OPERATIONS, Workbench

    if tuple(OPERATIONS) != WORKFLOWS:
        raise RuntimeError("Workbench fold drifted from workbench.OPERATIONS")
    described = {
        row["source_kind"]: row
        for row in Workbench().describe_operations()
        if "source_kind" in row
    }
    from .host_context import declared

    operations = []
    for kind in WORKFLOWS:
        row = described[kind]
        unbound = kind in UNBOUND
        if unbound != bool(row["available"]):
            raise RuntimeError("Workbench availability drifted for " + kind)
        if unbound:
            status = "available_in_unbound_session"
            evidence = _FIXTURES.get(kind) if kind != "project-graph" else "built_from_the_retained_project_model"
        elif declared(kind):
            status = "host_manifest_declared"
            evidence = "operator_manifest"
        else:
            status = "explicit_bindings_required"
            evidence = "host_selected_pin_required"
        operations.append({
            "workflow": kind,
            "operation_id": OPERATIONS[kind],
            "role": row["role"],
            "binding_status": status,
            "requires_upstream_bundle": bool(row.get("requires_upstream_bundle")),
            "requires_configuration": kind == "telemetry",
            "documentation": _doc(kind),
            "evidence": evidence,
            "authorizes_execution": False,
        })
    return {
        "schema": "ncp.workbench-fold.v1",
        "authorizes_execution": False,
        "qualification": "not_performed_by_this_file",
        "note": "One fold of the workbench. Name the workflow. This list does not opt for the easiest task. Documentation and evidence stay in the repository.",
        "operations": operations,
        "not_selected_by_default": True,
    }


def _project_bytes() -> bytes:
    from . import project_model as project
    from . import project_workflow
    from .telemetry import canonical

    value = project.create(
        "project:workflow-fixture",
        "Workflow fixture",
        {"place": {"value": None, "evidence_refs": []}},
    )
    value = project.put(value, {
        "object_id": "evidence:manual", "kind": "evidence", "label": "Manual",
        "content": {"status": "documented", "source_digest": "sha256:" + "1" * 64,
                    "locator": "fixture://manual", "subject_id": "machine:fixture"},
    })
    value = project.put(value, {
        "object_id": "signal:position", "kind": "signal", "label": "Position",
        "content": {"quantity": "position", "unit": "m", "frame": "frame:carriage",
                    "time_basis": "clock:utc", "semantics": "observed"},
    })
    value = project.put(value, {
        "object_id": "computation:position", "kind": "computation", "label": "Position model",
        "content": {"operation": "ciw.encoder-position.v1", "parameters": {"mode": "declared"}},
    })
    objects = {item["object_id"]: item for item in project.inspect(value)["objects"]}
    value = project.put(value, {
        "object_id": "result:position", "kind": "result", "label": "Position estimate",
        "content": {
            "value": 1.0,
            "input_revisions": {
                "signal:position": objects["signal:position"]["revision"],
                "computation:position": objects["computation:position"]["revision"],
            },
            "unit": "m", "frame": "frame:carriage", "time_basis": "clock:utc",
            "semantics": "estimated", "claim_scope": "declared_kinematic_model",
        },
    })
    value = project.connect(value, {
        "edge_id": "edge:model-result", "relation": "computation",
        "from": "computation:position", "to": "result:position", "resolution": "resolved",
    })
    value = project.connect(value, {
        "edge_id": "edge:evidence-result", "relation": "evidence",
        "from": "evidence:manual", "to": "result:position", "resolution": "resolved",
    })
    source = {
        "schema": project_workflow.SOURCE_SCHEMA,
        "experiment_id": "project:workflow-fixture",
        "configuration": deepcopy(project_workflow.CONFIGURATION),
        "project": value,
    }
    return canonical(source)


def _fixture_bytes(kind: str) -> bytes:
    if kind == "project-graph":
        return _project_bytes()
    path = ROOT / _FIXTURES[kind]
    raw = path.read_bytes()
    if not raw:
        raise ValueError("Retained evidence is empty")
    return raw


def _call(session, kind: str, payload: dict) -> dict:
    response = session.handle({
        "protocol_version": 1,
        "request_id": "ncp-workbench-" + kind,
        "type": kind,
        "payload": payload,
    })
    if response["type"] == "error":
        message = response["payload"].get("message") or response["payload"].get("code") or "refused"
        raise ValueError(str(message))
    return response["payload"]


def _stamp(body: dict) -> dict:
    body["physical_qualification"] = "not_established"
    body["authorizes_execution"] = False
    body["bundle_retained"] = True
    return body


def _read_bounded(path: Path, limit: int) -> bytes:
    if path.is_symlink():
        raise ValueError("Operator input must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        raw = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if len(raw) > limit:
        raise ValueError("Operator input exceeds the byte budget")
    return raw


def _source_bytes(workflow: str, source_b64: str | None) -> tuple[bytes, bool]:
    from .host_context import input_path

    if source_b64 is not None:
        if not isinstance(source_b64, str) or not source_b64:
            raise ValueError("source_b64 must be the scientific source, not a path")
        if len(source_b64) > 40000:
            raise ValueError("source_b64 exceeds the byte budget")
        try:
            raw = base64.b64decode(source_b64.encode("ascii"), validate=True)
        except Exception as exc:
            raise ValueError("source_b64 is not valid base64") from exc
        if not raw or len(raw) > 40000:
            raise ValueError("Scientific source exceeds the byte budget")
        return raw, False
    operator = input_path(workflow)
    if operator is not None:
        if not operator.is_file():
            raise ValueError("Operator input file is missing")
        raw = _read_bounded(operator, 65536)
        if not raw:
            raise ValueError("Operator input file is empty")
        return raw, False
    if workflow not in _FIXTURES and workflow != "project-graph":
        raise ValueError(
            "This workflow needs a validated source from the operator manifest. "
            "A tool argument cannot name a checkout."
        )
    return _fixture_bytes(workflow), True


def compose_catalogs(catalogs: list[dict]) -> dict:
    """Identity-aware union. Identical records collapse. Conflicts refuse."""
    import json
    if not catalogs:
        raise ValueError("Upstream artifact has no retained bundle")
    schema = catalogs[0].get("schema")
    if schema not in {"ciw.retained-workbench.v1", "ciw.retained-workbench.v2", "ciw.retained-workbench.v3"}:
        raise ValueError("Upstream artifact is not a workbench catalog")
    if any(item.get("schema") != schema for item in catalogs):
        raise ValueError("Upstream artifacts do not share a catalog schema")

    def union(records, key):
        seen = {}
        ordered = []
        for record in records:
            if type(record) is not dict or type(record.get(key)) is not str:
                raise ValueError("Upstream record has no identity")
            ident = record[key]
            raw = json.dumps(record, sort_keys=True, separators=(",", ":"))
            if ident in seen:
                if seen[ident] != raw:
                    raise ValueError("Upstream records share an identity and conflict")
                continue
            seen[ident] = raw
            ordered.append(record)
        return ordered

    sources = union([row for item in catalogs for row in item.get("sources") or []], "source_id")
    bundles = union([row for item in catalogs for row in item.get("bundles") or []], "bundle_id")
    if not bundles:
        raise ValueError("Upstream artifact has no retained bundle")
    identities = {row["bundle_id"] for row in bundles}
    graph = {row["bundle_id"]: row.get("upstream_bundle_id") for row in bundles}
    for ident, parent in graph.items():
        if parent is None:
            continue
        if type(parent) is not str or parent not in identities:
            raise ValueError("Upstream bundle is missing from the retained catalog")
        seen = set()
        cursor = ident
        while cursor is not None:
            if cursor in seen:
                raise ValueError("Upstream catalog contains a cycle")
            seen.add(cursor)
            cursor = graph.get(cursor)
    document = {
        "schema": schema,
        "revision": len(sources) + len(bundles),
        "sources": sources,
        "bundles": bundles,
    }
    return document


def _adopt(session, artifact_ids: list[str], store) -> None:
    from .workbench import Workbench

    restored = Workbench.restore(compose_catalogs([store.catalog(item) for item in artifact_ids]))
    restored._bindings = dict(session.workbench._bindings)
    session.workbench = restored


def run(workflow: str, source_b64: str | None = None, upstream_artifact: str | None = None,
         upstream_artifacts: list | None = None, store=None) -> dict:
    if not isinstance(workflow, str) or not workflow:
        raise ValueError("Name the workflow. This fold does not opt for the easiest task in the set.")
    from .adapters.protocol import AdapterRefusal
    from .host_context import bindings_for, configuration_for
    from .instruments import make_demo_run
    from .retention import _store
    from .scientific import bind
    from .session import Session
    from .workbench import OPERATIONS, UPSTREAM_KINDS

    if workflow not in OPERATIONS:
        raise ValueError("Unknown workbench workflow. Call ncp_workbench_list.")
    if upstream_artifact is not None and upstream_artifacts is not None:
        raise ValueError("Name one upstream form. This call does not opt for either.")
    holder = store if store is not None else _store()
    parent = Path(tempfile.mkdtemp(prefix="ncp-workbench-"))
    try:
        repositories = None
        if workflow not in UNBOUND:
            repositories = bindings_for(workflow)
            if repositories is None:
                raise ValueError(
                    workflow + " remains documentation and evidence until the host selects its binding. "
                    "This call does not pick a different workflow and does not invent a provider."
                )
        session = Session(make_demo_run(), parent)
        if repositories is not None:
            try:
                bind(session, workflow, repositories)
            except AdapterRefusal as exc:
                raise ValueError(getattr(exc, "message", None) or str(exc)) from exc
        if workflow in UPSTREAM_KINDS:
            if not isinstance(upstream_artifact, str) or not upstream_artifact:
                raise ValueError("Name the upstream artifact. This call does not pick one.")
            _adopt(session, [upstream_artifact], holder)
            upstream_id = holder.native(upstream_artifact)["bundle_digest"]
        elif workflow == "residual-monitor":
            if type(upstream_artifacts) is not list or not upstream_artifacts or any(type(item) is not str for item in upstream_artifacts):
                raise ValueError("Name the retained window artifacts. This call does not pick them.")
            _adopt(session, list(upstream_artifacts), holder)
            upstream_id = None
        elif upstream_artifact is not None or upstream_artifacts is not None:
            raise ValueError("This workflow does not accept an upstream bundle")
        else:
            upstream_id = None
        raw, synthetic = _source_bytes(workflow, source_b64)
        source = _call(session, "source.add", {
            "kind": workflow,
            "label": "folded workbench source",
            "bytes_b64": base64.b64encode(raw).decode("ascii"),
        })
        parameters = {"source_id": source["source_id"]}
        if workflow == "telemetry":
            configuration = configuration_for(workflow)
            if type(configuration) is not dict:
                raise ValueError("Telemetry requires an operator configuration. A tool argument cannot supply it.")
            parameters["configuration"] = configuration
        if upstream_id is not None:
            parameters["upstream_bundle_id"] = upstream_id
        completed = _call(session, "operation.execute", {
            "operation_id": OPERATIONS[workflow],
            "parameters": parameters,
        })
        bundle = _call(session, "bundle.get", {"bundle_id": completed["bundle_id"]})
        artifact_id = holder.keep(bundle, kind=workflow, catalog=session.workbench.serialize())
    except ValueError as exc:
        from .retention import RetainedFailure
        files = {}
        if parent.is_dir():
            for path in sorted(parent.rglob("*.json")):
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 500_000:
                    continue
                files[str(path.relative_to(parent))] = path.read_text(encoding="utf-8")[:20000]
        failure_id = holder.keep_failure({
            "workflow": workflow,
            "error": str(exc)[:500],
            "workspace": files,
        })
        raise RetainedFailure(str(exc), failure_id) from exc
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    step = bundle["steps"][0]
    authority = bundle.get("verification", {}).get("authority")
    data = step["result"].get("data") if isinstance(step["result"], dict) else None
    if isinstance(data, dict) and isinstance(data.get("authority"), dict):
        authority = data["authority"]
    return _stamp({
        "workflow": workflow,
        "operation_id": OPERATIONS[workflow],
        "status": "retained",
        "artifact_id": artifact_id,
        "bundle_digest": bundle.get("bundle_digest"),
        "result_id": step.get("result_id"),
        "authority": authority,
        "documentation": _doc(workflow),
        "evidence": _FIXTURES.get(workflow, "operator_or_caller_source"),
        "synthetic_fixture": synthetic,
    })
