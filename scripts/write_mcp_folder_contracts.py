"""Write tracked-folder contracts and mcp/folder-index.json.

Reading or running this script is not a tool call and not a grant.
Imported trees and exact-input capsules keep their bytes, modes, and paths.
Their navigation contracts live outside the protected source directories.
"""
from __future__ import annotations

import json
import subprocess
from html import escape
from pathlib import Path
from urllib.parse import quote

from monorepo import load_manifest

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "mcp" / "project-map.json"
INDEX_PATH = ROOT / "mcp" / "folder-index.json"
OVERLAY_PATH = ROOT / "mcp" / "source-contracts.md"

# This fixture is an exact input capsule owned by workcell_recipe.PROP.
# WorkcellHost intentionally rejects files outside the declared source_paths.
CAPSULE_ROOTS = ("examples/workcells/godot-prop",)

# Longest matching prefix wins. Call is what a model should use instead of
# the files. "none" means this folder is not a tool.
OVERRIDES: tuple[tuple[str, str, str], ...] = (
    (
        "tools/odrp-private-host-candidate",
        "none",
        "Isolated ODSP / ODRP build and qualification candidate. Not an independently callable tool. No production corpus, deployment, new grants, or historical-state migration is authorized by this folder.",
    ),
    (
        "instruments/domain/flowstate",
        "none",
        "Vendored FlowState tree. Not the leakage pin. Do not point an adapter here. Leakage runs only when the host selected the retained checkout.",
    ),
    (
        "instruments/domain/bim-estimator",
        "none",
        "Vendored BIM tree. Not the clearance pin. SP1 sources in this tree are not an SP1 proof. Clearance runs only when the host selected the retained checkout.",
    ),
    (
        "examples/proved-heat",
        "ncp_ledger_read",
        "Fixture for the registered heat profile. `proved_heat` is a ledger read, not an SP1 proof.",
    ),
    (
        "examples/leakage",
        "none",
        "Leakage fixture. This directory does not select the pin. The host launch does.",
    ),
    (
        "examples/metrology-clearance",
        "none",
        "Clearance fixture. This directory does not select the pin. A held inspection is not a physical clearance.",
    ),
    (
        "examples/cse-bim-quantity",
        "none",
        "BIM quantity fixture. Not the clearance pin and not a physical clearance.",
    ),
    (
        "examples/bim-quantity",
        "none",
        "BIM quantity fixture. Not the clearance pin and not a physical clearance.",
    ),
    (
        "examples/julia",
        "none",
        "Julia example. Unbound. Do not invent a Julia run.",
    ),
    (
        "runtimes/julia-oscillator",
        "none",
        "Julia worker. Unbound until a host selects the real executable. Do not invent one.",
    ),
    (
        "runtimes/julia-oscillator-kernel",
        "none",
        "Julia kernel. Unbound until a host selects the real executable. Do not invent one.",
    ),
    (
        "runtimes/godot-oscillator-kernel",
        "none",
        "Godot kernel. Unbound. Not an MCP grant and not a physical simulation result.",
    ),
    (
        "examples/sensor-project/godot",
        "none",
        "Godot prop. Unbound. Not an MCP grant.",
    ),
    (
        "examples/workcells/godot-prop",
        "none",
        "Godot prop. Unbound. Not an MCP grant.",
    ),
    (
        "tools/bevy-render-view",
        "none",
        "Bevy view. Unbound. Not an MCP grant.",
    ),
    (
        "godot",
        "none",
        "Godot scenes and scripts. Unbound. Not an MCP grant and not a physical simulation result.",
    ),
    (
        "examples/atmosphere-measurements",
        "none",
        "Declared measurement-ingress fixture. Not a measurement and not a domain grant.",
    ),
    (
        "examples/atmosphere-reference",
        "none",
        "Reference-comparison fixture. Agreement is not qualification and not a measurement.",
    ),
    (
        "examples/atmosphere-moist",
        "ncp_domain_run",
        "Synthetic moist-atmosphere fixture. Domain `atmosphere-moist`. Not a measurement.",
    ),
    (
        "examples/atmosphere",
        "ncp_domain_run",
        "Synthetic dry-atmosphere fixture. Domain `atmosphere`. Not a measurement.",
    ),
    (
        "examples/weather",
        "ncp_domain_run",
        "Synthetic weather fixtures. Call `ncp_domain_list` for the profile name, then `ncp_domain_run`. Not a forecast.",
    ),
    (
        "examples/impact-contact",
        "ncp_domain_run",
        "Synthetic impact fixture. Domain `impact`, name `contact`. Not a physical release.",
    ),
    (
        "examples/impact-plate",
        "ncp_domain_run",
        "Synthetic plate fixture. Domain `impact`, name `plate`. The field is not retained. Not a physical release.",
    ),
    (
        "examples/impact-crush",
        "ncp_domain_run",
        "Synthetic crush fixture. Domain `impact`, name `crush`. Not a physical release.",
    ),
    (
        "examples/impact-sweep",
        "ncp_domain_list",
        "Impact scenario envelope. Not a release. One case is `ncp_domain_run` on domain `impact`.",
    ),
    (
        "examples/fluid-dynamics",
        "ncp_fluid_field",
        "Wave and SPH fixtures. `solver` is `wave` or `sph`. The field is not retained. Not a measurement.",
    ),
    (
        "examples/fluid-experiments",
        "ncp_fluid_field",
        "Fluid research fixtures. Wave and SPH are `ncp_fluid_field`. Not a measurement.",
    ),
    (
        "examples/fluid_operations",
        "ncp_fluid_field",
        "Fluid operation fixtures. Wave and SPH are `ncp_fluid_field`. Not a measurement.",
    ),
    (
        "examples/polymer",
        "ncp_polymer_cycle",
        "Polymer fixtures. The direct synthetic cycle is `ncp_polymer_cycle`. A completed cycle is not a part release.",
    ),
    (
        "examples/sequence-kinetics",
        "ncp_domain_run",
        "Synthetic sequence fixture. Domain `sequence`. Not a measurement.",
    ),
    (
        "validation/repository-folder-audit-20261018",
        "none",
        "Frozen folder inventory from 2026-10-18. Not the current folder index and not a waiver.",
    ),
    (
        "src/ciw/adapters",
        "ncp_index_list",
        "Adapter implementations. A path in this folder does not select a checkout and is not a grant.",
    ),
    (
        "src/ciw",
        "ncp_index_list",
        "The `ncp_*` gateway lives in this package, including `control_gateway.py` and `domain_surface.py`. A module path is not a grant.",
    ),
    (
        "src",
        "ncp_index_list",
        "Implementation. The ciw package owns contracts, sessions, and the ncp_* gateway. A module path is not a grant.",
    ),
    (
        "release",
        "ncp_ledger_read",
        "Capability ledger. `qualified` stays `not_assessed`. `proved_heat` is a read, not an SP1 proof.",
    ),
)


def git_bytes(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def ignored(probe: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", "--", probe], cwd=ROOT).returncode == 0


SET_RULE = "This folder does not opt for the easiest task in a set. Name the task."
ENTRY_MARK = "<!-- ncp-folder-entry -->\n"


def protected_roots() -> tuple[str, ...]:
    # Validate the existing manifest before writing anything. Missing or invalid
    # provenance must never silently turn imported trees into writable folders.
    return tuple(module["path"] for module in load_manifest(ROOT)["modules"]) + CAPSULE_ROOTS


def protected_root(rel: str, roots: tuple[str, ...]) -> str | None:
    return next((root for root in roots if rel == root or rel.startswith(root + "/")), None)


def tracked_directories() -> tuple[set[Path], dict[Path, list[str]]]:
    directories = {Path(".")}
    files: dict[Path, list[str]] = {Path("."): []}
    tracked = git_bytes("ls-files", "-z")
    for raw in tracked.split(b"\0"):
        if not raw:
            continue
        path = Path(raw.decode())
        files.setdefault(path.parent, []).append(path.name)
        parent = path.parent
        while parent != Path("."):
            directories.add(parent)
            files.setdefault(parent, [])
            parent = parent.parent
    return directories, files


def load_tops() -> dict[str, dict[str, str]]:
    document = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    return {row["path"]: row for row in document["folders"]}


def override(rel: str) -> tuple[str, str] | None:
    best: tuple[str, str, str] | None = None
    for prefix, call, sentence in OVERRIDES:
        if rel == prefix or rel.startswith(prefix + "/"):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, call, sentence)
    if best is None:
        return None
    return best[1], best[2]


def top_row(rel: str, tops: dict[str, dict[str, str]]) -> dict[str, str] | None:
    if rel == ".":
        return None
    return tops.get(rel.split("/", 1)[0])


def classify(rel: str, tops: dict[str, dict[str, str]]) -> str:
    found = override(rel)
    if found is not None and "nbound" in found[1]:
        return "unbound"
    row = top_row(rel, tops)
    if row is None:
        return "navigation"
    if rel == row["path"]:
        return row["mcp"]
    if row["mcp"] == "unbound":
        return "unbound"
    if row["mcp"] == "evidence":
        return "evidence"
    return "navigation"


def call_for(rel: str, kind: str, tops: dict[str, dict[str, str]]) -> str:
    found = override(rel)
    if found is not None:
        return found[0]
    if kind in {"unbound", "evidence"}:
        return "none"
    return "ncp_index_list"


def sentence_for(rel: str, kind: str, tops: dict[str, dict[str, str]]) -> str:
    found = override(rel)
    if found is not None:
        return found[1]
    if rel == ".":
        return "Repository root. Start at the project map and the folder index. This file is not a grant."
    row = top_row(rel, tops)
    if row is None:
        return "Repository machinery. Not a tool and not a grant."
    if rel == row["path"]:
        return row["mandate"]
    leaf = rel.rsplit("/", 1)[-1]
    return f"`{leaf}` is inside `{row['path']}` ({kind}). {row['mandate']}"


def link(rel: str, target: str) -> str:
    if rel == ".":
        return target
    return "../" * rel.count("/") + "../" + target


def contract(rel: str, kind: str, call: str, sentence: str, names: list[str], *, location: str | None = None) -> str:
    shown = sorted(name for name in names if name != "MCP.md")
    extra = ""
    if shown:
        head = ", ".join(f"`{name}`" for name in shown[:8])
        more = f" ({len(shown) - 8} more)" if len(shown) > 8 else ""
        extra = f"\nTracked files here, none of them a tool call: {head}{more}.\n"
    target_map = link(rel if location is None else location, "mcp/project-map.json")
    target_index = link(rel if location is None else location, "mcp/folder-index.json")
    return (
        "# MCP contract\n"
        "\n"
        f"- path: `{rel}`\n"
        f"- class: `{kind}`\n"
        "- authorizes_execution: false\n"
        f"- call: `{call}`\n"
        "\n"
        f"{sentence}\n"
        f"{extra}\n"
        f"{SET_RULE}\n"
        "\n"
        "A file in this folder is not a tool call. This folder is not a human console. "
        f"Use `ncp_index_list` and the [project map]({target_map}). "
        f"The [folder index]({target_index}) lists every tracked folder. "
        "Reading this contract does not run a solver, bind a provider, or qualify a result. "
        "A PASS, a LOCAL check, or a completed graph is not a physical release.\n"
    )


def child_names(rel: str, directories: set[Path]) -> list[str]:
    parent = Path(".") if rel == "." else Path(rel)
    names = []
    for directory in directories:
        if directory != Path(".") and directory.parent == parent:
            names.append(directory.name)
    return sorted(names)


def readme_entry(rel: str, kind: str, call: str, children: list[str]) -> str:
    shown = ", ".join(f"`{name}`" for name in children[:12])
    more = f" ({len(children) - 12} more)" if len(children) > 12 else ""
    tasks = (
        f"Tasks in this folder, none of them the default: {shown}{more}.\n\n"
        if children
        else "This folder is not a menu. It still does not select a task for the caller.\n\n"
    )
    readme = "the [README](README.md)" if rel == "." else "[MCP.md](MCP.md)"
    return (
        f"{ENTRY_MARK}"
        "# MCP entry\n"
        "\n"
        f"- path: `{rel}`\n"
        f"- class: `{kind}`\n"
        "- authorizes_execution: false\n"
        f"- call: `{call}`\n"
        "\n"
        f"{SET_RULE}\n"
        "\n"
        f"{tasks}"
        f"The contract is {readme}. "
        "A file in this folder is not a tool call. This folder is not a human console. "
        "Reading this page does not run a solver, bind a provider, or qualify a result.\n"
    )


def write_readme(rel: str, directory: Path, kind: str, call: str, directories: set[Path]) -> None:
    if rel == "." or "node_modules" in rel.split("/"):
        return
    probe = f"{rel}/README.md"
    if ignored(probe):
        return
    path = ROOT / directory / "README.md"
    entry = readme_entry(rel, kind, call, child_names(rel, directories))
    if path.is_file():
        current = path.read_text(encoding="utf-8")
        if current.startswith(ENTRY_MARK):
            rest = current.split("\n---\n\n", 1)
            tail = rest[1] if len(rest) == 2 else ""
        else:
            tail = current
        if tail and not tail.startswith("\n"):
            body = entry + "\n---\n\n" + tail
        elif tail:
            body = entry + "\n---\n\n" + tail.lstrip("\n")
        else:
            body = entry
    else:
        body = entry
    path.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")


def main() -> None:
    roots = protected_roots()
    tops = load_tops()
    directories, files = tracked_directories()
    rows = []
    written = 0
    overlay_rel = OVERLAY_PATH.relative_to(ROOT).as_posix()
    overlay = [
        "# Protected source folder contracts\n\n"
        "Generated navigation for imported source trees and exact-input capsules. "
        "The [folder index](folder-index.json) points to every protected tracked folder. "
        "Source README files retain their original bytes; no navigation files are written into these trees. "
        "These contracts do not authorize execution or qualify a result.\n"
    ]
    for directory in sorted(directories, key=lambda path: (path != Path("."), path.as_posix())):
        rel = "." if directory == Path(".") else directory.as_posix()
        probe = "MCP.md" if directory == Path(".") else f"{rel}/MCP.md"
        protected = protected_root(rel, roots)
        # Imported ignore rules cannot hide tracked source from the external index.
        if protected is None and ignored(probe):
            continue
        kind = classify(rel, tops)
        call = call_for(rel, kind, tops)
        sentence = sentence_for(rel, kind, tops)
        row = {"path": rel, "class": kind, "call": call, "contract": probe}
        if protected is not None:
            text = contract(rel, kind, call, sentence, files.get(directory, []), location="mcp")
            overlay.append(f'\n<a id="{escape(rel, quote=True)}"></a>\n\n' + text.replace("# MCP contract\n", f"## `{rel}`\n", 1))
            row.update(contract=overlay_rel + "#" + quote(rel, safe="/"), protected_source=protected)
        else:
            text = contract(rel, kind, call, sentence, files.get(directory, []))
            path = ROOT / directory / "MCP.md"
            path.write_text(text, encoding="utf-8")
            write_readme(rel, directory, kind, call, directories)
        rows.append(row)
        written += 1
    OVERLAY_PATH.write_text("".join(overlay), encoding="utf-8")
    document = {
        "schema": "ncp.mcp-folder-index.v1",
        "authorizes_execution": False,
        "qualification": "not_performed_by_this_file",
        "contract": "MCP.md",
        "protected_source_contracts": overlay_rel,
        "project_map": "mcp/project-map.json",
        "note": "One row per tracked folder, except ignored local contract paths outside protected sources. Each row points to its contract. Imported sources and exact-input capsules use external contracts and retain their original bytes and modes. A row is not a tool call and does not authorize execution. A folder does not opt for the easiest task in a set.",
        "folders": rows,
    }
    INDEX_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {written} contracts and {INDEX_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
