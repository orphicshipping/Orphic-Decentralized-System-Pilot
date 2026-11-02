"""Write the transformation ledger and capability map. Running this is not a tool call."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from ciw.services import capability_map
    from ciw.transformation import document

    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    paths = [item.decode() for item in tracked.split(b"\0") if item]
    ledger = document(paths)
    (ROOT / "mcp" / "transformation-ledger.json").write_text(
        json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    (ROOT / "mcp" / "capability-map.json").write_text(
        json.dumps(capability_map(), indent=2) + "\n", encoding="utf-8")
    packaged = ROOT / "src" / "ciw"
    copies = {
        packaged / "plug-in.json": ROOT / "mcp" / "plug-in.json",
        packaged / "capability-map.json": ROOT / "mcp" / "capability-map.json",
        packaged / "verification-report.json": ROOT / "mcp" / "verification-report.json",
        packaged / "capability-ledger.json": ROOT / "release" / "capability-ledger.json",
    }
    for dest, src in copies.items():
        dest.write_bytes(src.read_bytes())
    docs = []
    native = {
        "docs/JULIA_OSCILLATOR.md": "runtimes/julia-oscillator; Julia 1.10.12 executable is not installed",
        "docs/JULIA_SP1.md": "docs/JULIA_SP1.md; SP1 proof was not executed",
        "docs/PROVED_HEAT.md": "SP1 heat guest; proof was not executed",
        "docs/BIM_INTEROP_RUNTIME.md": "documented BIM commit 4b74abda40bba3277de69bf61e9e09283ae2d5b3; checkout is not selected",
        "docs/NET_ASSET_WORKER.md": "Godot and Blender paths are operator-supplied; neither binary is installed",
    }
    for path in paths:
        if not path.endswith(".md"):
            continue
        stem = Path(path).stem.lower().replace("-", "_")
        module = ROOT / "src" / "ciw" / f"{stem}.py"
        if path in {"README.md", "docs/AGENT_OPERATIONS.md"}:
            status, owner = "current_surface", path
        elif path in native:
            status, owner = "binding_missing", native[path]
        elif "AUDIT" in Path(path).stem.upper():
            status, owner = "historical_record", path
        elif module.is_file():
            status, owner = "documents_existing_module", "src/ciw/" + module.name
        elif path.startswith("instruments/") or "/vendor/" in path:
            status, owner = "vendor_document", path
        else:
            status, owner = "unbound_prose", path
        docs.append({"path": path, "status": status, "owner": owner, "authorizes_execution": False})
    (ROOT / "mcp" / "document-map.json").write_text(json.dumps({
        "schema": "ncp.document-map.v1",
        "authorizes_execution": False,
        "note": "current_surface is the operating contract. documents_existing_module means a same-stem module exists. unbound_prose was not relabelled historical or current. binding_missing is not a run.",
        "documents": docs,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"ledger {len(paths)} {ledger['counts']}")


if __name__ == "__main__":
    main()
