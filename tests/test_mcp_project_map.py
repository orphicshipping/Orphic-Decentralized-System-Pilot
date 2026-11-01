"""The project map names real folders and does not authorize execution."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "mcp" / "project-map.json"


def test_project_map_matches_the_tree_and_does_not_grant():
    from ciw.capabilities import catalog
    from ciw.domain_surface import DOMAINS, OPERATIONS

    document = json.loads(MAP.read_text(encoding="utf-8"))
    assert document["schema"] == "ncp.mcp-project-map.v1"
    assert document["authorizes_execution"] is False
    assert document["qualification"] == "not_performed_by_this_file"
    folders = {row["path"] for row in document["folders"]}
    on_disk = {path.name for path in ROOT.iterdir() if path.is_dir() and not path.name.startswith(".")}
    assert folders == on_disk
    for row in document["folders"]:
        assert row["mcp"] in {"callable", "navigation", "unbound", "evidence"}
        assert row["mandate"]
    mandates = {row["id"] for row in document["mandates"]}
    assert {"pins", "proved-heat", "synthetic", "tests", "not-a-release"} <= mandates
    assert document["callable"]["synthetic_domains"]["domains"] == list(DOMAINS)
    indexed = {item["capability_id"] for item in catalog()["capabilities"]}
    assert {row[0] for row in OPERATIONS} <= indexed
    assert "ncp_domain_run" in document["callable"]["direct_tools"]
    assert "SP1 proved heat as a proof" in document["not_grants"]
    narrative = (ROOT / document["narrative"]).read_text(encoding="utf-8")
    assert "not a grant" in narrative
    assert "ncp_domain_run" in narrative
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/MCP_PROJECT.md" in readme
    assert "not an SP1 proof" in readme
    assert "MCP.md" in readme
    assert "folder-index.json" in readme
    assert "Operator reference" in readme
    index = json.loads((ROOT / "mcp" / "folder-index.json").read_text(encoding="utf-8"))
    assert index["schema"] == "ncp.mcp-folder-index.v1"
    assert index["authorizes_execution"] is False
    assert document["folder_index"] == "mcp/folder-index.json"


def test_every_tracked_folder_has_a_contract_that_does_not_grant():
    import subprocess
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    directories = {Path(".")}
    for raw in tracked.split(b"\0"):
        if not raw:
            continue
        parent = Path(raw.decode()).parent
        while parent != Path("."):
            directories.add(parent)
            parent = parent.parent
    checked = 0
    for directory in directories:
        contract = ROOT / directory / "MCP.md"
        probe = "MCP.md" if directory == Path(".") else (directory / "MCP.md").as_posix()
        ignored = subprocess.run(["git", "check-ignore", "-q", "--", probe], cwd=ROOT).returncode == 0
        if ignored:
            continue
        text = contract.read_text(encoding="utf-8")
        rel = "." if directory == Path(".") else directory.as_posix()
        assert f"- path: `{rel}`" in text
        assert "authorizes_execution: false" in text
        assert "not a tool call" in text
        assert f"- class: `" in text
        checked += 1
    assert checked > 700
    index = json.loads((ROOT / "mcp" / "folder-index.json").read_text(encoding="utf-8"))
    assert {row["path"] for row in index["folders"]} == {
        "." if directory == Path(".") else directory.as_posix()
        for directory in directories
        if subprocess.run(
            ["git", "check-ignore", "-q", "--", "MCP.md" if directory == Path(".") else (directory / "MCP.md").as_posix()],
            cwd=ROOT,
        ).returncode != 0
    }
    by_path = {row["path"]: row for row in index["folders"]}
    assert by_path["src/ciw/adapters"]["class"] == "navigation"
    assert "not a grant" in (ROOT / "src" / "ciw" / "adapters" / "MCP.md").read_text(encoding="utf-8")
    assert "control_gateway.py" in (ROOT / "src" / "ciw" / "MCP.md").read_text(encoding="utf-8")
    flow = (ROOT / "instruments" / "domain" / "flowstate" / "MCP.md").read_text(encoding="utf-8")
    bim = (ROOT / "instruments" / "domain" / "bim-estimator" / "MCP.md").read_text(encoding="utf-8")
    assert "Not the leakage pin" in flow
    assert "Not the clearance pin" in bim
    assert "not an SP1 proof" in bim
    julia = (ROOT / "examples" / "julia" / "MCP.md").read_text(encoding="utf-8")
    assert "Unbound" in julia
    assert by_path["examples/julia"]["class"] == "unbound"
    assert by_path["examples/julia"]["call"] == "none"
    heat = (ROOT / "examples" / "proved-heat" / "MCP.md").read_text(encoding="utf-8")
    assert "not an SP1 proof" in heat
    assert by_path["examples/proved-heat"]["call"] == "ncp_ledger_read"
