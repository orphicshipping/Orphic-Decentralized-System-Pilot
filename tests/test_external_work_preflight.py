"""Dependency-free synthetic startup checks; no Access login or tunnel launch."""
from contextlib import closing, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from ciw.external_work_preflight import REQUIRED_VERSIONS, inspect_prerequisites


ROOT = Path(__file__).resolve().parents[1]


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "work.sqlite3"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript("""
                CREATE TABLE clients (id TEXT PRIMARY KEY, role TEXT, parent TEXT, enabled INTEGER);
                CREATE TABLE contexts (id TEXT PRIMARY KEY, parent TEXT, document TEXT, enabled INTEGER);
                CREATE TABLE tasks (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE,
                    requester TEXT, request_key TEXT, document TEXT, UNIQUE(requester, request_key));
                INSERT INTO clients VALUES ('fixture-requester', 'requester', 'intent:fixture', 1);
                INSERT INTO clients VALUES ('fixture-worker', 'worker', 'intent:fixture', 1);
                PRAGMA user_version=1;
            """)
        self.config = {
            "schema": "ciw.external-work-cloudflare.v1", "database": "work.sqlite3",
            "issuer": "https://fixture.cloudflareaccess.com",
            "endpoints": [
                {"public_url": "https://requester.example/requester/mcp", "audience": "requester-aud",
                 "role": "requester", "subjects": {"fixture-subject": "fixture-requester"}},
                {"public_url": "https://worker.example/worker/mcp", "audience": "worker-aud",
                 "role": "worker", "subjects": {"fixture-subject": "fixture-worker"}},
            ],
        }
        self.config_path = self.root / "config.json"
        self.write_config()
        self.binary = self.root / "cloudflared-fixture"
        self.binary.write_bytes(b"Synthetic inert bytes; never execute this fixture.\n")
        self.sha256 = hashlib.sha256(self.binary.read_bytes()).hexdigest()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def inspect(self, versions=None):
        versions = REQUIRED_VERSIONS if versions is None else versions
        with patch("ciw.external_work_preflight.metadata.version", side_effect=lambda name: versions[name]):
            return inspect_prerequisites(self.config_path, self.binary, self.sha256)

    def assert_blocked(self, report, check):
        self.assertEqual(report["status"], "blocked")
        self.assertIn(check, {item["check"] for item in report["blockers"]})

    def test_valid_synthetic_prerequisites_are_read_only_and_not_live_success(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        report = self.inspect()
        self.assertEqual(report["status"], "prerequisites_ready")
        self.assertEqual(report["gateway_configuration_validation"], "not_run")
        self.assertEqual(report["live_authentication_and_health"], "not_tested")
        self.assertFalse(report["activation_authorized"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_required_pins_match_project_manifest(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        declared = project["dependencies"] + project["optional-dependencies"]["external-work"]
        expected = {name.removesuffix("[crypto]"): version for name, version in
                    (item.split("==") for item in declared)}
        self.assertEqual(REQUIRED_VERSIONS, expected)

    def test_fixture_database_connections_are_explicitly_closed(self):
        original_connect = sqlite3.connect
        connections = []
        def tracked_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connections.append(connection)
            return connection
        case = PreflightTests("test_valid_synthetic_prerequisites_are_read_only_and_not_live_success")
        try:
            with patch("sqlite3.connect", side_effect=tracked_connect):
                case.setUp()
            self.assertTrue(connections)
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
        finally:
            for connection in connections:
                connection.close()
            case.doCleanups()

    def test_mismatched_pin_is_dependency_blocker_not_test_failure(self):
        versions = dict(REQUIRED_VERSIONS, mcp="1.29.0")
        report = self.inspect(versions)
        self.assert_blocked(report, "dependency")
        self.assertEqual(report["blockers"], [{"check": "dependency", "package": "mcp",
                         "reason": "exact_pin_missing_or_mismatched"}])

    def test_missing_distribution_is_sanitized(self):
        from importlib.metadata import PackageNotFoundError
        with patch("ciw.external_work_preflight.metadata.version", side_effect=PackageNotFoundError("private-path")):
            report = inspect_prerequisites(self.config_path, self.binary, self.sha256)
        self.assert_blocked(report, "dependency")
        self.assertNotIn("private-path", json.dumps(report))

    def test_unreadable_distribution_metadata_is_sanitized(self):
        with patch("ciw.external_work_preflight.metadata.version", side_effect=PermissionError("private-path")):
            report = inspect_prerequisites(self.config_path, self.binary, self.sha256)
        self.assert_blocked(report, "dependency")
        self.assertNotIn("private-path", json.dumps(report))

    def test_malformed_distribution_versions_are_not_echoed(self):
        for value in ("/private/metadata", "secret-like\nvalue", "privatevalue",
                      "1.0+privatevalue", "x" * 129, None, 123):
            with self.subTest(value=value):
                versions = dict(REQUIRED_VERSIONS, mcp=value)
                report = self.inspect(versions)
                self.assert_blocked(report, "dependency")
                entry = next(item for item in report["checks"] if item.get("package") == "mcp")
                self.assertIsNone(entry["actual"])

    def test_empty_subject_map_is_blocked(self):
        self.config["endpoints"][0]["subjects"] = {}
        self.write_config()
        self.assert_blocked(self.inspect(), "configuration_prerequisites")

    def test_missing_principal_is_blocked(self):
        self.config["endpoints"][0]["subjects"] = {"fixture-subject": "unprovisioned"}
        self.write_config()
        self.assert_blocked(self.inspect(), "database")

    def test_wrong_role_is_blocked(self):
        self.config["endpoints"][0]["subjects"] = {"fixture-subject": "fixture-worker"}
        self.write_config()
        self.assert_blocked(self.inspect(), "database")

    def test_revoked_principal_is_blocked(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE clients SET enabled=0 WHERE id='fixture-requester'")
        self.assert_blocked(self.inspect(), "database")

    def test_absolute_database_path_is_supported(self):
        self.config["database"] = str(self.database)
        self.write_config()
        self.assertEqual(self.inspect()["status"], "prerequisites_ready")

    def test_nonexistent_database_is_not_created(self):
        self.database.unlink()
        self.assert_blocked(self.inspect(), "database")
        self.assertFalse(self.database.exists())

    def test_empty_database_is_not_initialized(self):
        self.database.write_bytes(b"")
        self.assert_blocked(self.inspect(), "database")
        self.assertEqual(self.database.read_bytes(), b"")

    def test_corrupt_database_is_sanitized_and_unchanged(self):
        self.database.write_bytes(b"not sqlite; sensitive fixture contents")
        before = self.database.read_bytes()
        report = self.inspect()
        self.assert_blocked(report, "database")
        self.assertEqual(self.database.read_bytes(), before)
        self.assertNotIn("sensitive fixture", json.dumps(report))

    def test_newer_database_version_is_not_migrated(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("PRAGMA user_version=2")
        before = self.database.read_bytes()
        self.assert_blocked(self.inspect(), "database")
        self.assertEqual(self.database.read_bytes(), before)

    def test_missing_table_is_not_created(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("DROP TABLE contexts")
        before = self.database.read_bytes()
        self.assert_blocked(self.inspect(), "database")
        self.assertEqual(self.database.read_bytes(), before)

    def test_sqlite_sidecars_refuse_immutable_snapshot(self):
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(self.database) + suffix)
                sidecar.write_bytes(b"fixture")
                report = self.inspect()
                self.assertIn({"check": "database", "reason": "database_not_quiescent"}, report["blockers"])
                sidecar.unlink()

    def test_symlink_database_is_refused(self):
        target = self.root / "target.sqlite3"
        self.database.rename(target)
        try:
            self.database.symlink_to(target)
        except OSError:
            self.skipTest("Host does not permit creation of the synthetic symlink")
        self.assert_blocked(self.inspect(), "database")

    def test_duplicate_json_keys_are_refused(self):
        self.config_path.write_text('{"schema":"ciw.external-work-cloudflare.v1","schema":"other"}')
        self.assert_blocked(self.inspect(), "configuration_prerequisites")

    def test_oversized_configuration_is_refused(self):
        self.config_path.write_bytes(b" " * 65537)
        self.assert_blocked(self.inspect(), "configuration_prerequisites")

    def test_malformed_configuration_is_sanitized(self):
        self.config_path.write_bytes(b"invalid secret-like fixture")
        report = self.inspect()
        self.assert_blocked(report, "configuration_prerequisites")
        self.assertNotIn("secret-like", json.dumps(report))

    def test_missing_configuration_is_blocked(self):
        self.config_path.unlink()
        self.assert_blocked(self.inspect(), "configuration_prerequisites")

    def test_hash_mismatch_is_blocked(self):
        self.sha256 = "0" * 64
        self.assert_blocked(self.inspect(), "cloudflared")

    def test_missing_binary_is_blocked(self):
        self.binary.unlink()
        self.assert_blocked(self.inspect(), "cloudflared")

    def test_invalid_hash_is_blocked(self):
        self.sha256 = "not-a-pin"
        self.assert_blocked(self.inspect(), "cloudflared")

    def test_output_omits_paths_subjects_audiences_and_principals(self):
        raw = json.dumps(self.inspect())
        for private in (str(self.root), "fixture-subject", "fixture-requester", "requester-aud",
                        "fixture.cloudflareaccess.com", "requester.example"):
            self.assertNotIn(private, raw)

    def launcher(self):
        spec = importlib.util.spec_from_file_location("ncp_launcher_fixture",
            ROOT / "examples/external-work/start_cloudflare.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_launcher(self, *, check, versions):
        module = self.launcher()
        argv = ["start_cloudflare.py", "--config", str(self.config_path), "--cloudflared", str(self.binary),
                "--cloudflared-sha256", self.sha256]
        if check:
            argv.append("--check")
        else:
            argv += ["--log-dir", str(self.root / "must-not-exist")]
        output = io.StringIO()
        with patch("sys.argv", argv), redirect_stdout(output), \
             patch("ciw.external_work_preflight.metadata.version", side_effect=lambda name: versions[name]), \
             patch.object(module.getpass, "getpass", side_effect=AssertionError("token prompt")), \
             patch.object(module.subprocess, "Popen", side_effect=AssertionError("process startup")), \
             patch.object(module.socket, "socket", side_effect=AssertionError("network access")):
            code = module.main()
        self.assertFalse((self.root / "must-not-exist").exists())
        return code, json.loads(output.getvalue())

    def test_check_does_not_import_gateway_prompt_or_start(self):
        code, report = self.run_launcher(check=True, versions=REQUIRED_VERSIONS)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "prerequisites_ready")

    def test_blocked_start_returns_before_import_prompt_or_process(self):
        code, report = self.run_launcher(check=False, versions=dict(REQUIRED_VERSIONS, mcp="1.29.0"))
        self.assertEqual(code, 2)
        self.assert_blocked(report, "dependency")

    def test_check_blocked_exit_code(self):
        self.config["endpoints"][0]["subjects"] = {}
        self.write_config()
        code, report = self.run_launcher(check=True, versions=REQUIRED_VERSIONS)
        self.assertEqual(code, 2)
        self.assert_blocked(report, "configuration_prerequisites")

    def test_fresh_plain_python_check_creates_no_bytecode_or_other_files(self):
        checkout = self.root / "fresh-checkout"
        source = checkout / "src/ciw"
        source.mkdir(parents=True)
        for name in ("__init__.py", "external_work_preflight.py"):
            shutil.copyfile(ROOT / "src/ciw" / name, source / name)
        launcher = checkout / "start_cloudflare.py"
        shutil.copyfile(ROOT / "examples/external-work/start_cloudflare.py", launcher)
        self.config["endpoints"][0]["subjects"] = {}
        self.write_config()
        before = {str(p.relative_to(self.root)): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        env = dict(os.environ, PYTHONPATH=str(checkout / "src"))
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        result = subprocess.run([sys.executable, str(launcher), "--check",
            "--config", str(self.config_path), "--cloudflared", str(self.binary),
            "--cloudflared-sha256", self.sha256], cwd=checkout, env=env,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assert_blocked(json.loads(result.stdout), "configuration_prerequisites")
        after = {str(p.relative_to(self.root)): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(any(checkout.rglob("__pycache__")))


if __name__ == "__main__":
    unittest.main()
