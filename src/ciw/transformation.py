"""Disposition of every tracked path. Reading this does not run a solver.

Paths stay where tests and pins import them. A disposition records why the
file is still in the tree. It is not permission to execute the file.
"""
from __future__ import annotations

SCHEMA = "ncp.transformation-ledger.v1"

_REWRITTEN = {
    "README.md",
    "src/ciw/services.py",
    "src/ciw/host_context.py",
    "src/ciw/retention.py",
    "src/ciw/plug_in.py",
    "src/ciw/domain_surface.py",
    "src/ciw/workbench_surface.py",
    "src/ciw/transformation.py",
    "src/ciw/control_gateway.py",
    "src/ciw/capabilities.py",
    "docs/MCP_MODEL.md",
    "docs/MCP_PROJECT.md",
    "docs/AGENT_OPERATIONS.md",
    "docs/README.md",
    "mcp/model-catalog.json",
    "mcp/project-map.json",
    "mcp/plug-in.json",
    "mcp/capability-map.json",
    "mcp/transformation-ledger.json",
    "mcp/verification-report.json",
    "scripts/write_transformation_ledger.py",
    "tests/test_transformation_ledger.py",
    "tests/test_mcp_project_map.py",
    "tests/test_capabilities.py",
}

_COMPONENTS = (
    ("instruments/", "vendor_exempt", "vendored_provider",
     "Imported provider tree. Not the leakage or BIM pin. Bytes stay so the manifest identity is unchanged."),
    ("godot/", "blocked", "unbound_engine",
     "Godot source retained. Not a grant. The host must select a real engine. This conversion does not invent a run."),
    ("runtimes/", "blocked", "unbound_engine",
     "Optional worker retained. Not a grant until the host selects the real executable."),
    ("experiments/", "blocked", "research",
     "Research script retained. Not a profile grant and not advertised as a tool."),
    ("tests/", "retained_and_verified", "verification",
     "Verification suite. Not weakened to widen a grant. A passing test is not physical qualification."),
    ("examples/", "retained_and_verified", "fixture",
     "Labelled fixture. Not a measurement and not a live credential."),
    ("validation/", "retained_and_verified", "evidence",
     "Retained audit. Not a new execution and not a waiver."),
    ("qualification/", "retained_and_verified", "evidence",
     "Local check record. Not a release."),
    ("delivery/", "retained_and_verified", "evidence",
     "Retained delivery artifact. Not a new scientific result."),
    ("docs/", "retained_and_verified", "record",
     "Page that owns a claim. Reading it is not a run."),
    ("src/", "retained_and_verified", "domain",
     "Domain implementation. MCP and ciw.services call it. The path stays because tests and pins import it."),
    (".github/", "retained_and_verified", "ci",
     "Continuous integration. Not a scientific grant."),
    ("tools/", "retained_and_verified", "maintenance",
     "Maintenance console or view. No required workflow depends on a human screen."),
    ("scripts/", "retained_and_verified", "maintenance",
     "Noninteractive maintenance script. Running it is not an MCP grant."),
    ("deploy/", "retained_and_verified", "packaging",
     "Deployment definition. Packaging is not execution."),
    ("containers/", "retained_and_verified", "packaging",
     "Container definition. A built image is not a qualified installation."),
    ("tasks/", "retained_and_verified", "work_order",
     "Work order. Not execution."),
    ("requirements/", "retained_and_verified", "lock",
     "Dependency lock. A lock file does not bind a provider."),
    ("third_party/", "vendor_exempt", "notice",
     "Third-party notice. Not scientific authority."),
    ("release/", "retained_and_verified", "ledger",
     "Capability ledger. Read with ncp_ledger_read. qualified stays not_assessed."),
    ("mcp/", "rewritten", "model_contract",
     "Machine contract for models and other software. The file does not authorize execution."),
)


def _component(path: str):
    if path in _REWRITTEN:
        return "rewritten", "service_boundary", (
            "Model-facing service, contract, or test of that boundary. "
            "Domain algorithms under this path were not copied."
        )
    name = path.rsplit("/", 1)[-1]
    if "node_modules/" in path:
        return "vendor_exempt", "dependency", "Tracked dependency pages. Not an NCP tool and not rewritten."
    if name == "MCP.md":
        return "generated", "folder_contract", (
            "Generated folder contract. authorizes_execution is false. Not a tool call and not a human console."
        )
    for prefix, disposition, component, reason in _COMPONENTS:
        if path.startswith(prefix):
            return disposition, component, reason
    return "retained_and_verified", "repository", (
        "Supporting tracked file. Accounted for. Not a tool call and not an unreviewed island."
    )


def dispositions(paths):
    """One row per tracked path. paths must be the final git file list."""
    rows = []
    for path in sorted(paths):
        disposition, component, reason = _component(path)
        rows.append({
            "path": path,
            "disposition": disposition,
            "component": component,
            "reason": reason,
            "authorizes_execution": False,
        })
    return rows


def document(paths):
    rows = dispositions(paths)
    counts = {}
    for row in rows:
        counts[row["disposition"]] = counts.get(row["disposition"], 0) + 1
    return {
        "schema": SCHEMA,
        "authorizes_execution": False,
        "qualification": "not_performed_by_this_file",
        "architecture": {
            "premise": "Models and other software call the same operations without a human screen.",
            "requirement": "One domain service layer, MCP as the model transport, a Python SDK on those same functions, holds preserved.",
            "decision": "Do not relocate src or instruments. Tests and pins import those paths. ciw.services is the application service. MCP tools delegate to it.",
            "assumption_resolved": "Vendored instruments are not the leakage or BIM checkout. Unbound engines stay blocked rather than simulated.",
        },
        "counts": counts,
        "files": rows,
    }
