"""The no-IFC demo executes, retains evidence, and keeps simulated input distinct."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gat.demo import tank_level

ROOT = Path(__file__).resolve().parents[1]


class TankLevelDemoTests(unittest.TestCase):
    def test_cli_writes_to_requested_directory_and_preserves_simulated_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "tank output"
            result = subprocess.run([sys.executable, "-m", "gat.demo.tank_level", "--demo", "-o", str(output)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "tank-level-report.json").read_text())
            self.assertTrue((output / "ledger.json").is_file())
            self.assertFalse((output / "out").exists())
        self.assertTrue(report["synthetic"])
        self.assertFalse(report["released"])
        self.assertEqual(report["observation"]["kind"], "SIMULATED")
        self.assertEqual(report["receipt"]["subject"], "IndustrialSlot:TANK-T1.Level")
        self.assertTrue(report["verification"]["passed"])
        self.assertEqual(report["verification"]["disposition"], "REQUEST_EVIDENCE")
        self.assertFalse(report["receipt"]["usable_as_field_evidence"])
        self.assertNotEqual(report["receipt"]["prior_world_digest"], report["world_digest"])
        self.assertAlmostEqual(report["posterior"]["mean"], float(Fraction(6033, 2504)), places=12)

    def test_repeated_demo_reproduces_state_and_evidence_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            first = tank_level.run_tank_level(Path(directory) / "first")
            second = tank_level.run_tank_level(Path(directory) / "second")
        self.assertEqual(first, second)

    def test_invalid_acquisition_or_calibration_binding_refuses_before_output(self):
        record = json.loads(tank_level._MEAS.read_text())
        for name, change, expected in (
            ("calibration", {"calibration_id": "unrelated"}, "calibration_id"),
            ("unit", {"indicated_unit": "mm"}, "indicated unit"),
            ("quality", {"quality": {"acquisition": "missing", "calibration": "applicable"}}, "received observation"),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "observation.jsonl"
                source.write_text(json.dumps({**record, **change}) + "\n")
                output = Path(directory) / "result"
                with patch.object(tank_level, "_MEAS", source):
                    with self.assertRaisesRegex(ValueError, expected):
                        tank_level.run_tank_level(output)
                self.assertFalse(output.exists())
