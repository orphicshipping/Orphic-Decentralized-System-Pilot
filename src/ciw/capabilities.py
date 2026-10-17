"""Provider-free index of surfaces that already exist in this tree.

Importing this module does not import or run those surfaces. A record here
names a capability; it does not authorize execution, bind a provider, or
perform qualification. This is not the shared profile/capability discovery
registry, and it does not register a composition schema.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import platform
from .legibility_runtime import runtime_identity as legibility_runtime_identity
from .system_runtime import runtime_identity as system_runtime_identity
from .data_runtime import comparison_identity, polymer_identity, product_study_identity


SCHEMA = "ciw.capability-catalog.v1"

_FIELDS = (
    "capability_id",
    "disposition",
    "kind",
    "summary",
    "command",
    "operation_id",
    "role",
    "runtime_identity",
    "module",
    "profiles",
    "topic",
    "lesson_commands",
    "requires_private_checkout",
    "authorizes_execution",
    "qualification",
    "notes",
)


def _record(**fields):
    unknown = set(fields) - set(_FIELDS)
    if unknown:
        raise RuntimeError("capability index field is not part of the record: " + ", ".join(sorted(unknown)))
    record = {name: None for name in _FIELDS}
    record.update(fields)
    record["requires_private_checkout"] = False
    record["authorizes_execution"] = False
    record["qualification"] = "not_performed"
    if record["disposition"] != "implemented":
        raise RuntimeError("capability index lists only implemented surfaces")
    if not isinstance(record["capability_id"], str) or not record["capability_id"]:
        raise RuntimeError("capability index entries need an id")
    return record


def _toolpoint_containment_identity():
    """Mirror the owner metadata without importing its execution module.

    The registry-completeness test binds this projection to the owner's actual
    runtime identity. Do not change owner source bytes just to index them.
    """
    root = Path(__file__).parent
    names = ("data_manufacturing_bridge.py", "manufacturing_contracts.py", "manufacturing_runtime.py",
             "manufacturing_quality.py", "polymer_microfab.py", "polymer_toolpoint_qc.py",
             "toolpoint_stream.py", "toolpoint_stream_containment.py", "core/identities.py")
    return {"provider": "ciw.native-toolpoint-containment", "version": "1",
            "source_files_sha256": {name: sha256((root / name).read_bytes()).hexdigest() for name in names},
            "environment": {"python": platform.python_version()},
            "scope": "bounded_same_implementation_retained_record_replay_no_physical_authority"}


_SYSTEM_CAPABILITIES = tuple(_record(
    capability_id=name, disposition="implemented", kind="registered_operation",
    summary=summary, command=["system", command], operation_id=name, role=role,
    runtime_identity=system_runtime_identity(), module="ciw.system_workflow",
    notes="Explicit execution only; physical validation and canonical admission remain separate.",
) for name, role, command, summary in (
    ("system.compile.v1", "backend", "compile", "Compile typed scientific configurations into state and execution graphs."),
    ("system.simulate.v1", "backend", "run", "Execute the coupled reference thermal, mechanical and sensor models."),
    ("system.verify.v1", "verification", "verify", "Retain separately identified numerical verification of a specific occurrence."),
    ("system.compare.v1", "backend", "demo", "Measure reduction and evolution disagreement between configurations."),
    ("system.study.v1", "backend", "study", "Measure timestep accuracy at common times including heating startup."),
))

_CAPABILITIES = (
    _record(
        capability_id="synthetic-oscillator-demo",
        disposition="implemented",
        kind="provider_free_command",
        summary="Writes a deterministic synthetic oscillator recording. No sensor and no private provider.",
        command=["demo"],
        notes="ciw demo writes that recording only when invoked separately. This index does not.",
    ),
    _record(
        capability_id="statistics.v1",
        disposition="implemented",
        kind="registered_operation",
        summary="Sample statistics on a retained run through the built-in oscillator adapter.",
        command=["analyze", "stats"],
        operation_id="statistics.v1",
        role="analysis",
        runtime_identity={"provider": "ciw.oscillator", "version": "1"},
        notes="ciw analyze stats executes this operation only when invoked separately. Listing the id does not.",
    ),
    _record(
        capability_id="spectrum.periodogram.v1",
        disposition="implemented",
        kind="registered_operation",
        summary="Periodogram spectrum on a retained run through the built-in oscillator adapter.",
        command=["analyze", "spectrum"],
        operation_id="spectrum.periodogram.v1",
        role="analysis",
        runtime_identity={"provider": "ciw.oscillator", "version": "1"},
        notes="ciw analyze spectrum executes this operation only when invoked separately. Listing the id does not.",
    ),
    _record(
        capability_id="legibility.compile.v1",
        disposition="implemented",
        kind="registered_operation",
        summary="Compile identity-bound human, reasoning and vision records from retained evidence.",
        command=["legibility", "compile"],
        operation_id="legibility.compile.v1",
        role="backend",
        runtime_identity=legibility_runtime_identity(),
        notes="Metadata consistency is separate from artifact bytes, signatures, issuer trust and physical validation.",
    ),
    *_SYSTEM_CAPABILITIES,
    _record(
        capability_id="data.project-gltf-mesh.v1", disposition="implemented", kind="registered_operation",
        summary="Project checked retained glTF component geometry into a mesh source.",
        command=["data", "project-gltf"], operation_id="data.project-gltf-mesh.v1", role="backend",
        runtime_identity={"provider": "ciw.gltf-reference.component-projection", "version": "1",
            "adapter_sha256": "sha256:" + sha256(Path(__file__).with_name("gltf_reference.py").read_bytes()).hexdigest(),
            "mesh_solver": "not_executed_by_projection"}, module="ciw.gltf_reference_runtime",
        notes="Explicit retained source only; projection does not execute a mesh solver or establish physical qualification.",
    ),
    _record(
        capability_id="data.compare-observations.v1", disposition="implemented", kind="registered_operation",
        summary="Compare explicitly selected observations from immutable native datasets.",
        command=None, operation_id="data.compare-observations.v1", role="backend",
        runtime_identity=comparison_identity(), module="ciw.data_workflow",
        notes="The net data compare command uses retained Workbench sources. No conversion, interpolation or physical qualification.",
    ),
    _record(
        capability_id="data.review-product-study.v1", disposition="implemented", kind="registered_operation",
        summary="Review scalar product requirements against exact retained calculation artifacts.",
        command=["product-study", "review"], operation_id="data.review-product-study.v1", role="backend",
        runtime_identity=product_study_identity(), module="ciw.product_study_workflow",
        notes="Separate variants retain source/CAD identities, missing material data and release holds. No physics rerun or physical qualification.",
    ),
    _record(
        capability_id="data.assess-polymer-cycle.v1", disposition="implemented", kind="registered_operation",
        summary="Apply the existing bounded polymer assessment to a retained native cycle dataset.",
        command=None, operation_id="data.assess-polymer-cycle.v1", role="backend",
        runtime_identity=polymer_identity(), module="ciw.data_compute_bridge",
        notes="Native sensors, histories and uncertainty declarations remain intact. This is not physical product acceptance.",
    ),
    _record(
        capability_id="data.review-toolpoint-containment.v1", disposition="implemented", kind="registered_operation",
        summary="Review exact retained manufacturing and toolpoint stream reports through the existing containment reducer.",
        command=["data", "review-toolpoint"], operation_id="data.review-toolpoint-containment.v1", role="backend",
        runtime_identity=_toolpoint_containment_identity(), module="ciw.data_manufacturing_bridge",
        notes="Retained-record replay only; no store writes, hold release, shipment authorization or physical qualification.",
    ),
    _record(
        capability_id="learning.oscillator-rms",
        disposition="implemented",
        kind="lesson",
        summary="One retained RMS lesson over the synthetic oscillator and statistics.v1. Not a curriculum engine.",
        command=["math"],
        topic="oscillator-rms",
        lesson_commands=["history", "learn", "explore", "work", "inspect", "verify", "replay"],
        notes="history, learn, explore and inspect read retained material. work, verify and replay calculate only when those math subcommands are invoked. This index does not run them.",
    ),
    _record(
        capability_id="doctor.preflight",
        disposition="implemented",
        kind="read_only_diagnostic",
        summary="Local identity preflight for one explicit profile. preflight_passed is not qualification.",
        command=["doctor"],
        profiles=["core", "legibility", "declared-workloads", "native-interop", "interval-requirement",
                  "reaction-catalyst", "reaction-cantera"],
        notes="ciw doctor inspects local metadata and explicit bindings. It does not install, fetch, execute science, or qualify. Core and legibility use installed metadata; other profiles need bindings this index does not supply.",
    ),
    _record(
        capability_id="linear-response.library",
        disposition="implemented",
        kind="library",
        summary="Bounded analytic educational previews. No CLI, no retained operation, and no admission.",
        module="ciw.linear_response",
        profiles=["scalar-square.v1", "affine.v1", "nonlinear-vector.v1", "composed-vector.v1",
                  "absolute.v1", "oscillator-rate.v1", "oscillator-energy.v1"],
        notes="There is no ciw command for this library. Importing the index does not import or evaluate it.",
    ),
)

_IDS = [item["capability_id"] for item in _CAPABILITIES]
if len(_IDS) != len(set(_IDS)):
    raise RuntimeError("duplicate capability id")


def catalog():
    """Return a detached copy of the index. Mutating it does not change the module."""
    return {
        "schema": SCHEMA,
        "status": "index_only",
        "qualification": "not_performed",
        "authorizes_execution": False,
        "closes_shared_discovery": False,
        "discovers_private_providers": False,
        "not_indexed": [
            "shared profile and capability discovery",
            "typed composition graph",
            "frame transformations",
            "device gateway",
            "private provider discovery",
        ],
        "capabilities": [deepcopy(item) for item in _CAPABILITIES],
    }


def get(capability_id):
    """Return one detached record, or refuse an id this index does not name."""
    if isinstance(capability_id, str):
        for item in _CAPABILITIES:
            if item["capability_id"] == capability_id:
                return deepcopy(item)
    from .adapters.protocol import AdapterRefusal
    name = capability_id if isinstance(capability_id, str) else type(capability_id).__name__
    raise AdapterRefusal(
        "capability_unavailable",
        f"No capability named {name} is in the provider-free index",
    )
