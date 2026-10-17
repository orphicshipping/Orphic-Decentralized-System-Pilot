"""Native plan, manufacturing and dossier surfaces must coexist without shadowing."""
import json
import os
from pathlib import Path
import subprocess
import sys

from ciw import microfab
from ciw import polymer_microfab
from ciw import polymer_production_dossier
from ciw.manufacturing_example import example_request as manufacturing_request


def test_three_module_owners_remain_distinct():
    modules = (microfab, polymer_microfab, polymer_production_dossier)
    assert {Path(m.__file__).name for m in modules} == {
        "microfab.py", "polymer_microfab.py", "polymer_production_dossier.py"}
    assert callable(microfab.compile_envelope)
    assert callable(polymer_microfab.compile_configuration)
    assert callable(polymer_production_dossier.run)
    assert not hasattr(polymer_microfab, "DOSSIER_SCHEMA")
    assert not hasattr(polymer_production_dossier, "compile_configuration")
    plan = microfab.compile_envelope(microfab.example_plan())
    request = manufacturing_request()
    config = polymer_microfab.compile_configuration(request["configuration"], request["preflight"], at_s=0)
    assert plan["schema"] == "ciw.microfab-envelope.v1"
    assert config["schema"] != plan["schema"]


def test_all_three_real_native_command_journeys(tmp_path):
    env = os.environ.copy()
    package_parent = Path(microfab.__file__).resolve().parent.parent
    if package_parent.name == "src":
        # pytest's source path must remain absolute when the native child changes cwd.
        env["PYTHONPATH"] = str(package_parent)
    else:
        env.pop("PYTHONPATH", None)
    imported = subprocess.check_output(
        [sys.executable, "-c", "from pathlib import Path; import ciw.microfab; print(Path(ciw.microfab.__file__).resolve())"],
        cwd=tmp_path, env=env, text=True,
    ).strip()
    assert Path(imported) == Path(microfab.__file__).resolve()
    def net(*args):
        return subprocess.run([sys.executable, "-m", "ciw.net", "polymer", *args],
                              cwd=tmp_path, env=env, capture_output=True, text=True, check=True)

    net("microfab", "example", "--output", "plan.json")
    net("microfab", "compile", "plan.json", "--output", "envelope.json")
    net("microfab", "verify", "plan.json", "envelope.json")
    net("manufacture", "example", "--output", "manufacturing.json")
    net("manufacture", "run", "manufacturing.json", "--output", "manufacturing-report.json")
    net("manufacture", "verify", "manufacturing-report.json", "--output", "manufacturing-audit.json")
    net("dossier", "example", "--output", "dossier-request.json")
    net("dossier", "run", "dossier-request.json", "--output-dir", "dossier")
    net("dossier", "inspect", "dossier")
    net("dossier", "verify", "dossier", "--output", "dossier-audit.json")
    net("dossier", "replay", "dossier", "--output-dir", "dossier-replay")
    def read(name):
        return json.loads((tmp_path / name).read_text(encoding="utf-8"))
    assert read("plan.json")["schema"] == "ciw.microfab-plan.v1"
    assert read("manufacturing.json")["schema"] == "ciw.polymer-manufacturing-request.v1"
    assert read("dossier/dossier.json")["schema"] == "ciw.polymer-microfab-dossier.v1"
    assert read("manufacturing-audit.json")["status"] == "PASS"
    assert read("dossier-audit.json")["status"] == "PASS"
    help_text = net("--help").stdout
    assert all(name in help_text for name in ("microfab", "manufacture", "dossier"))
