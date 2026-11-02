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
    for path in paths:
        if path.endswith(".md"):
            operating_guides = {"docs/ODRP.md", "docs/READ_ONLY_ADAPTERS.md",
                                "docs/PRIVATE_RELEASE_CONTRACT.md", "docs/PRIVATE_HOST_OPERATIONS.md",
                                "docs/BOUNDED_NATIVE_PROFILES.md", "tools/odrp-private-host-candidate/README.md"}
            current = path in {"README.md", "docs/AGENT_OPERATIONS.md"} | operating_guides
            docs.append({
                "path": path,
                "status": "current_surface" if current else "not_re_audited",
                "owner": (path if path in operating_guides else
                          "mcp/README and docs/AGENT_OPERATIONS.md" if current else
                          "owning guide, not re-audited in this pass"),
            })
    (ROOT / "mcp" / "document-map.json").write_text(json.dumps({
        "schema": "ncp.document-map.v1",
        "authorizes_execution": False,
        "note": "current_surface is the operating contract. not_re_audited is not a claim that the page is historical or current.",
        "documents": docs,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"ledger {len(paths)} {ledger['counts']}")


if __name__ == "__main__":
    main()
