"""Reject mismatched runner environments and unsafe workflow routing changes."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


runner = module("check_native_runner")
policy = module("check_ci_policy")


class NativeRunnerTests(unittest.TestCase):
    def valid(self):
        return {"runner_environment": "self-hosted", "system": "Linux", "architecture": "x86_64",
                "os_id": "ubuntu", "os_version": "24.04",
                "available_memory_bytes": 7 * runner.GIB, "free_disk_bytes": 20 * runner.GIB,
                "workspace_free_disk_bytes": 20 * runner.GIB,
                "tools": {name: "/usr/bin/" + name for name in runner.TOOLS}}

    def test_exact_resource_boundary_is_accepted_without_mutation(self):
        info = self.valid()
        before = deepcopy(info)
        self.assertEqual(runner.validate(info), [])
        self.assertEqual(info, before)

    def test_each_insufficient_resource_fails(self):
        for field in ("available_memory_bytes", "free_disk_bytes", "workspace_free_disk_bytes"):
            with self.subTest(field=field):
                info = self.valid()
                info[field] -= 1
                self.assertTrue(runner.validate(info))

    def test_incompatible_environment_fails(self):
        for key, value in (("runner_environment", "github-hosted"), ("system", "Windows"),
                           ("architecture", "aarch64"), ("os_id", "debian"), ("os_version", "22.04")):
            with self.subTest(key=key):
                info = self.valid()
                info[key] = value
                self.assertTrue(runner.validate(info))

    def test_every_missing_executable_is_reported(self):
        info = self.valid()
        info["tools"] = {}
        errors = runner.validate(info)
        self.assertEqual(len(errors), len(runner.TOOLS))
        for name in runner.TOOLS:
            self.assertIn(f"Missing prerequisite executable: {name}", errors)

    def workflow(self, name):
        return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)

    def test_checked_in_routes_and_preflights_pass(self):
        for name in policy.NATIVE_RUNNERS:
            with self.subTest(name=name):
                self.assertEqual(policy.check_native_routing(name, self.workflow(name)), [])

    def test_pr_routing_and_missing_opt_in_are_rejected(self):
        name = "proved-heat.yml"
        for before, after in (("workflow_dispatch", "pull_request"),
                              ("vars.NCP_NATIVE_LINUX_RUNNER == 'enabled'", "true"),
                              ("ncp-native-linux", "unrelated-machine"),
                              ("ubuntu-24.04", "windows-latest")):
            with self.subTest(after=after):
                value = self.workflow(name)
                job = value["jobs"]["native-proof"]
                job["runs-on"] = job["runs-on"].replace(before, after)
                self.assertTrue(policy.check_native_routing(name, value))

    def test_removed_or_disabled_preflight_is_rejected(self):
        for disable in (False, True):
            value = self.workflow("proved-heat.yml")
            steps = value["jobs"]["native-proof"]["steps"]
            check = next(s for s in steps if s.get("name") == "Validate dedicated native runner")
            if disable:
                check["if"] = "false"
            else:
                steps.remove(check)
            self.assertTrue(policy.check_native_routing("proved-heat.yml", value))


if __name__ == "__main__":
    unittest.main()
