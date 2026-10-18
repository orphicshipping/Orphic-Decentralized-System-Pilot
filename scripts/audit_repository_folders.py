"""Inventory every tracked folder at an immutable Git revision without running it.

Import edges and file mentions are review signals, not proof of executable
reachability, scientific equivalence or qualification. Imported trees keep their
own ownership and pins. No packages, workflow code or provider code are imported.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import unquote, urlsplit


def git(root: Path, *arguments: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(root), *arguments], input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def snapshot(root: Path, revision: str) -> tuple[str, str, list[dict], dict[str, bytes]]:
    commit = git(root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}").decode().strip()
    tree = git(root, "rev-parse", commit + "^{tree}").decode().strip()
    entries = []
    for raw in git(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
        if not raw:
            continue
        header, raw_path = raw.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        entries.append({"path": raw_path.decode(), "mode": mode, "kind": kind, "git_oid": oid})
    # Read committed objects, never an ignored worktree cache or symlink target.
    objects = {}
    ids = list(dict.fromkeys(e["git_oid"] for e in entries if e["kind"] == "blob"))
    for start in range(0, len(ids), 128):
        batch = ids[start:start + 128]
        raw = git(root, "cat-file", "--batch", data=("\n".join(batch) + "\n").encode())
        offset = 0
        for expected in batch:
            end = raw.index(b"\n", offset)
            oid, kind, size = raw[offset:end].decode().split()
            if oid != expected or kind != "blob":
                raise ValueError("Unexpected committed blob identity")
            size = int(size)
            objects[oid] = raw[end + 1:end + 1 + size]
            offset = end + size + 2
        if offset != len(raw):
            raise ValueError("Unexpected Git object framing")
    return commit, tree, entries, objects


def owner(path: str, modules: list[dict]) -> str:
    return next((m["id"] for m in modules if path == m["path"] or path.startswith(m["path"] + "/")), "ncp")


def purpose(path: str, modules: list[dict]) -> str:
    parts = PurePosixPath(path).parts
    if not parts:
        return "repository_root"
    if "node_modules" in parts:
        return "preserved_upstream_dependency"
    if "__pycache__" in parts or path.endswith((".pyc", ".pyo")):
        return "preserved_upstream_generated_cache"
    if owner(path, modules) != "ncp":
        if ".github" in parts:
            return "upstream_ci_reference_not_root_actions"
        if any(x in parts for x in ("tests", "test")):
            return "independent_provider_tests"
        if any(x in parts for x in ("data", "results", "fixtures", "examples")):
            return "independent_provider_data_or_examples"
        return "independent_provider_source_or_contract"
    return {
        ".github": "root_automation", "src": "installed_package",
        "scripts": "operator_or_qualification_tool", "tests": "core_test_or_fixture",
        "examples": "operator_example_or_teaching_reference", "experiments": "bounded_research_experiment",
        "runtimes": "optional_native_worker_source", "tools": "optional_tool_or_client",
        "godot": "optional_inspection_client", "docs": "documentation_or_retained_reference",
        "validation": "retained_evidence_or_opt_in_qualification", "qualification": "historical_or_opt_in_qualification",
        "delivery": "preserved_delivery_or_transport", "third_party": "upstream_notice_or_reference",
        "release": "release_policy_or_evidence", "requirements": "declared_environment",
        "tasks": "work_order", "deploy": "deployment_guide", "containers": "optional_container_profile",
        "instruments": "import_registry_or_common_boundary",
    }.get(parts[0], "root_project_metadata")


def normalized_target(source: str, relative: str) -> str:
    path = PurePosixPath(relative[1:]) if relative.startswith("/") else PurePosixPath(source).parent / relative
    parts = []
    for part in path.parts:
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def markdown_links(text: dict[str, str], tracked: set[str], folders: set[str], modules: list[dict]) -> list[dict]:
    links = []
    for path, body in text.items():
        if not path.endswith((".md", ".markdown")):
            continue
        fenced = False
        math_block = False
        for number, line in enumerate(body.splitlines(), 1):
            if re.match(r"^\s*(```|~~~)", line):
                fenced = not fenced
                continue
            if line.strip() in {r"\[", "$$"}:
                math_block = not math_block
                continue
            if line.strip() == r"\]":
                math_block = False
                continue
            if fenced or math_block:
                continue
            targets = [m.group(1) for m in re.finditer(r"(?<!!)\[[^\]\n]+\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)", line)
                       if line[:m.start()].count("`") % 2 == 0]
            targets += re.findall(r"^\s*\[[^\]]+\]:\s*(\S+)", line)
            for raw in targets:
                raw = raw.strip("<>")
                parsed = urlsplit(raw)
                if parsed.scheme or parsed.netloc or not parsed.path or raw in {"...", "…"}:
                    continue
                target = normalized_target(path, unquote(parsed.path))
                links.append({"source": path, "line": number, "target": target, "raw": raw,
                              "owner": owner(path, modules), "tracked_target_exists": target in tracked or target in folders})
    return links


def analyze(root: Path, revision: str = "HEAD") -> dict:
    commit, tree, entries, objects = snapshot(root, revision)
    tracked = {e["path"] for e in entries}
    modules = json.loads(objects[next(e["git_oid"] for e in entries if e["path"] == "instruments/manifest.json")])["modules"] if "instruments/manifest.json" in tracked else []
    for m in modules:
        p = PurePosixPath(m["path"])
        if p.is_absolute() or ".." in p.parts or not m["path"].startswith("instruments/"):
            raise ValueError("Import ownership requires a contained instruments path")
    text, inventory, directories = {}, [], defaultdict(list)
    for e in entries:
        path = e["path"]
        raw = objects.get(e["git_oid"])
        if raw is not None and e["mode"] != "120000":
            try:
                decoded = raw.decode("utf-8")
                if "\0" not in decoded:
                    text[path] = decoded
            except UnicodeDecodeError:
                pass
        row = {**e, "bytes": len(raw) if raw is not None else None,
               "sha256": sha256(raw).hexdigest() if raw is not None else None,
               "text": path in text, "owner": owner(path, modules), "purpose": purpose(path, modules)}
        inventory.append(row)
        for parent in PurePosixPath(path).parents:
            directories[str(parent)].append(row)
    folder_rows = []
    for folder, members in sorted(directories.items()):
        folder_rows.append({"path": folder, "owner": owner(folder, modules), "purpose": purpose(folder, modules),
            "direct_files": sum(str(PurePosixPath(x["path"]).parent) == folder for x in members),
            "subtree_files": len(members), "subtree_bytes": sum(x["bytes"] or 0 for x in members),
            "file_purposes": dict(sorted(Counter(x["purpose"] for x in members).items())),
            "manifest_id": next((m["id"] for m in modules if owner(folder, modules) == m["id"]), None)})
    package_modules = {}
    for path in tracked:
        if path.startswith("src/") and path.endswith(".py"):
            name = path[4:-3].replace("/", ".").removesuffix(".__init__")
            package_modules[name] = path
    reverse = {p: m for m, p in package_modules.items()}
    edges, declared, parse_errors, markers = [], [], [], []
    for path, body in sorted(text.items()):
        if not path.endswith(".py"):
            continue
        try:
            parsed = ast.parse(body, filename=path)
        except (SyntaxError, ValueError) as exc:
            parse_errors.append({"path": path, "error": str(exc)})
            continue
        own = reverse.get(path)
        package = own if path.endswith("/__init__.py") else own.rpartition(".")[0] if own else None
        for n in ast.walk(parsed):
            imports = []
            if isinstance(n, ast.Import):
                imports = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                base = n.module or ""
                if n.level:
                    if not package or n.level > len(package.split(".")):
                        continue
                    parts = package.split(".")
                    base = ".".join(parts[:len(parts) - n.level + 1] + ([base] if base else []))
                imports = [base] + [base + "." + a.name for a in n.names]
            for name in imports:
                if name in package_modules and package_modules[name] != path:
                    edges.append({"source": path, "line": n.lineno, "module": name, "target": package_modules[name]})
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                for name in re.findall(r"\b(?:ciw|net_geomatics)\.[A-Za-z_][A-Za-z0-9_.]*", n.value):
                    while name not in package_modules and "." in name:
                        name = name.rpartition(".")[0]
                    if name in package_modules and package_modules[name] != path:
                        declared.append({"source": path, "line": n.lineno, "module": name,
                                         "kind": "literal_string_mention_not_execution"})
            if isinstance(n, ast.Raise) and n.exc and "NotImplementedError" in ast.unparse(n.exc):
                markers.append({"path": path, "line": n.lineno, "text": ast.unparse(n.exc)})
    navigation = []
    if "src/ciw/operator_commands.py" in text:
        for n in ast.parse(text["src/ciw/operator_commands.py"]).body:
            if isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id == "COMMANDS" for x in n.targets):
                for name, module, description in ast.literal_eval(n.value):
                    navigation.append({"command": name, "module": "ciw." + module, "present": "ciw." + module in package_modules,
                                       "description": description})
    module_rows = []
    for name, path in sorted(package_modules.items()):
        imports = [e for e in edges if e["module"] == name]
        module_rows.append({"module": name, "path": path,
            "incoming_product_imports": [e for e in imports if e["source"].startswith("src/")],
            "incoming_other_imports": [e for e in imports if not e["source"].startswith("src/")],
            "declared_mentions": [e for e in declared if e["module"] == name],
            "navigation_commands": [x["command"] for x in navigation if x["module"] == name],
            "main_guard_mention": "__name__" in text.get(path, "") and "__main__" in text.get(path, "")})
    links = markdown_links(text, tracked, set(directories), modules)
    duplicates = defaultdict(list)
    for x in inventory:
        if x["sha256"]:
            duplicates[x["sha256"]].append(x["path"])
    return {"schema": "ncp.repository-folder-audit.v1", "source_commit": commit, "source_tree": tree,
        "scope": "All committed paths and every ancestor folder, plus a synthetic root row; ignored/untracked content is excluded.",
        "limitations": ["Static references are signals for review, not executable reachability or proof of an orphan.",
            "Dynamic dispatch, generated paths and installed-copy identity require separate review.",
            "Markdown inspection checks relative file targets, not remote URLs or anchors; it is not a full Markdown parser.",
            "No provider, native build, browser, device or workflow is executed by this audit.",
            "Committed imported bytecode and dependencies are inventoried without loading them."],
        "summary": {"files": len(inventory), "tracked_folders_excluding_root": len(folder_rows) - 1,
            "text_files": len(text), "python_files_parsed": sum(p.endswith(".py") for p in text) - len(parse_errors),
            "package_modules": len(package_modules), "commands": len(navigation), "imported_modules": len(modules),
            "python_parse_errors": len(parse_errors), "missing_command_modules": sum(not x["present"] for x in navigation),
            "ncp_missing_relative_link_signals": sum(not x["tracked_target_exists"] and x["owner"] == "ncp" for x in links)},
        "files": inventory, "folders": folder_rows, "modules": module_rows, "navigation": navigation,
        "python_parse_errors": parse_errors, "explicit_not_implemented_refusals": markers,
        "markdown_file_links": links,
        "duplicate_byte_groups": [{"sha256": h, "paths": ps} for h, ps in sorted(duplicates.items()) if len(ps) > 1]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--revision", default="HEAD", help="Immutable committed snapshot to read; worktree edits are not included")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = analyze(args.repository.resolve(), args.revision)
    # An audit must never replace tracked source/evidence or a previous report.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"source_commit": report["source_commit"], **report["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
