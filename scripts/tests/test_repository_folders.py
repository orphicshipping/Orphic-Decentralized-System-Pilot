"""Exercise immutable inventory and ownership boundaries on real Git snapshots."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("folder_audit", Path(__file__).resolve().parents[1] / "audit_repository_folders.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class RepositoryAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Audit fixture")
        self.git("config", "user.email", "audit@example.invalid")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args]).decode().strip()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "Snapshot")
        return self.git("rev-parse", "HEAD")

    def test_committed_snapshot_ignores_dirty_untracked_and_ignored_content(self):
        self.write("src/ciw/a.py", "VALUE = 1\n")
        self.write(".gitignore", "cache/\n")
        sha = self.commit()
        self.write("src/ciw/a.py", "raise RuntimeError('dirty code must not execute')\n")
        self.write("loose/untracked.py", "raise RuntimeError('untracked')\n")
        self.write("cache/generated.py", "not python\n")
        report = audit.analyze(self.root, sha)
        self.assertEqual(report["source_commit"], sha)
        self.assertEqual(report["summary"]["files"], 2)
        self.assertEqual({x["path"] for x in report["folders"]}, {".", "src", "src/ciw"})
        self.assertEqual(report["files"][1]["bytes"], len("VALUE = 1\n"))

    def test_imported_dependencies_and_nested_workflows_retain_provider_ownership(self):
        self.write("instruments/manifest.json", json.dumps({"modules": [{"id": "view", "path": "instruments/views/view"}]}))
        self.write("instruments/views/view/.github/workflows/check.yml", "name: upstream\n")
        self.write("instruments/views/view/node_modules/x/index.js", "throw 'never run';\n")
        self.write("instruments/views/view/engine/__pycache__/x.pyc", "opaque bytes")
        self.commit()
        rows = {x["path"]: x for x in audit.analyze(self.root)["files"]}
        self.assertEqual(rows["instruments/views/view/.github/workflows/check.yml"]["purpose"], "upstream_ci_reference_not_root_actions")
        self.assertEqual(rows["instruments/views/view/node_modules/x/index.js"]["purpose"], "preserved_upstream_dependency")
        self.assertEqual(rows["instruments/views/view/engine/__pycache__/x.pyc"]["owner"], "view")

    def test_symlink_target_is_not_read_or_parsed(self):
        outside = self.root.parent / (self.root.name + "-outside.py")
        outside.write_text("invalid syntax outside repository")
        self.addCleanup(outside.unlink)
        try:
            (self.root / "linked.py").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Host does not permit symlink creation")
        self.commit()
        report = audit.analyze(self.root)
        self.assertEqual(report["files"][0]["mode"], "120000")
        self.assertFalse(report["files"][0]["text"])
        self.assertEqual(report["summary"]["python_files_parsed"], 0)

    def test_relative_imports_navigation_and_mentions_are_distinct(self):
        self.write("src/ciw/__init__.py", "")
        self.write("src/ciw/a.py", "from . import b\nTEXT = 'ciw.worker'\nraise RuntimeError('not executed')\n")
        self.write("src/ciw/b.py", "VALUE = 2\n")
        self.write("src/ciw/worker.py", "VALUE = 3\n")
        self.write("src/ciw/operator_commands.py", "COMMANDS = (('show', 'a', 'Read'), ('missing', 'absent', 'No implementation'))\n")
        self.commit()
        report = audit.analyze(self.root)
        modules = {x["module"]: x for x in report["modules"]}
        self.assertEqual(modules["ciw.b"]["incoming_product_imports"][0]["source"], "src/ciw/a.py")
        self.assertFalse(modules["ciw.worker"]["incoming_product_imports"])
        self.assertEqual(modules["ciw.worker"]["declared_mentions"][0]["kind"], "literal_string_mention_not_execution")
        self.assertEqual(modules["ciw.a"]["navigation_commands"], ["show"])
        self.assertEqual(report["summary"]["missing_command_modules"], 1)

    def test_links_do_not_promote_generated_targets_or_math_to_present_files(self):
        self.write("docs/a.md", "[`Present`](../README.md#heading)\n[Absent](../generated/output.json)\n[Escape](../../outside.md)\n`[Inline example](missing.md)`\n```md\n[Example](missing.md)\n```\n\\[\nM[f](s)\n\\]\n[Placeholder](...)\n")
        self.write("README.md", "# Heading\n")
        self.commit()
        links = audit.analyze(self.root)["markdown_file_links"]
        self.assertEqual(len(links), 3)
        self.assertTrue(links[0]["tracked_target_exists"])
        self.assertFalse(links[1]["tracked_target_exists"])
        self.assertEqual(links[2]["target"], "../outside.md")

    def test_previous_revision_is_not_relabelled_as_current_content(self):
        self.write("src/ciw/a.py", "FIRST = 1\n")
        old = self.commit()
        self.write("src/ciw/a.py", "SECOND = 2\n")
        new = self.commit()
        before, after = audit.analyze(self.root, old), audit.analyze(self.root, new)
        self.assertNotEqual(before["source_tree"], after["source_tree"])
        self.assertNotEqual(before["files"][0]["sha256"], after["files"][0]["sha256"])

    def test_output_cannot_overwrite_source_or_prior_evidence(self):
        self.write("README.md", "retained\n")
        self.commit()
        with self.assertRaises(FileExistsError):
            audit.main(["--repository", str(self.root), "--output", str(self.root / "README.md")])
        self.assertEqual((self.root / "README.md").read_text(), "retained\n")

    def test_invalid_ownership_path_refuses_without_inspecting_outside(self):
        self.write("instruments/manifest.json", json.dumps({"modules": [{"id": "bad", "path": "instruments/../../escape"}]}))
        self.commit()
        with self.assertRaises(ValueError):
            audit.analyze(self.root)


if __name__ == "__main__":
    unittest.main()
