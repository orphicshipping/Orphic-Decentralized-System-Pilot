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
