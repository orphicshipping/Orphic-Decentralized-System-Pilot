"""Execute the bounded manufacturing-data bridge from exact noneditable wheel bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

TESTS = ("test_data_manufacturing_bridge.py", "test_toolpoint_native_integration.py",
         "test_polymer_namespace_coexistence.py")
MINIMUM_TESTS = 46


def suite(cwd, report, env):
    subprocess.run([sys.executable, "-m", "pytest", "-q", *("tests/" + n for n in TESTS),
                    "--junitxml=" + str(report)], cwd=cwd, env=env, check=True)
    nodes = list(ET.parse(report).getroot().iter("testsuite"))
    totals = {k: sum(int(n.attrib.get(k, 0)) for n in nodes)
              for k in ("tests", "failures", "errors", "skipped")}
    if totals["tests"] < MINIMUM_TESTS or any(totals[k] for k in ("failures", "errors", "skipped")):
        raise ValueError(f"Incomplete manufacturing-data bridge gate: {totals}")
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    wheels = list(args.wheel_dir.resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Require exactly one candidate wheel")
    output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy(); env.pop("PYTHONPATH", None)
    source = suite(root, output / "source.xml", dict(env, PYTHONPATH=str(root / "src")))
    commands = []
    with tempfile.TemporaryDirectory(prefix="net-manufacturing-data-installed-") as temporary:
        outside = Path(temporary).resolve()
        if outside.is_relative_to(root):
            raise ValueError("Installed qualification requires a directory outside the checkout")
        (outside / "tests").mkdir()
        for name in TESTS: shutil.copy2(root / "tests" / name, outside / "tests" / name)
        code = "from pathlib import Path; import ciw; print(Path(ciw.__file__).resolve().parent)"
        package = Path(subprocess.check_output([sys.executable, "-I", "-c", code], cwd=outside, env=env, text=True).strip())
        if package.is_relative_to(root) or "site-packages" not in package.parts:
            raise ValueError("Imported package is not the isolated noneditable candidate")
        count = 0
        with zipfile.ZipFile(wheels[0]) as archive:
            for name in archive.namelist():
                if name.startswith("ciw/") and not name.endswith("/"):
                    value = archive.read(name)
                    if (root / "src" / name).read_bytes() != value or (package.parent / name).read_bytes() != value:
                        raise ValueError("Candidate source/wheel/installed mismatch: " + name)
                    count += 1
        installed = suite(outside, output / "installed.xml", env)
        executable = Path(sys.executable).parent / ("net.exe" if os.name == "nt" else "net")
        if not executable.is_file(): raise ValueError("Missing installed native net command")
        setup = '''
import json
from pathlib import Path
from ciw.session import Session
from ciw.instruments import make_demo_run
from ciw.manufacturing_runtime import evaluate
from ciw.polymer_quality_example import manufacturing_request
from ciw.toolpoint_stream_example import demo
from ciw.toolpoint_stream_store import initialize, StreamStore
Session(make_demo_run(), Path('initial')).save_workspace(Path('empty.json'))
Path('manufacturing.json').write_text(json.dumps(evaluate(manufacturing_request()),indent=2)+'\\n')
initialize('quality.sqlite3')
for i in range(2):
    r=demo(case='health-recovery' if i==0 else 'nominal',cycle_index=i)
    spec,head=r['spec_ref'],r['receipts'][-1]['receipt_ref']
    StreamStore('quality.sqlite3').import_report(str(i),r,expected_spec_ref=spec,expected_head_ref=head)
    exported=StreamStore('quality.sqlite3').export(str(i),expected_spec_ref=spec,expected_head_ref=head)
    Path('stream-'+str(i)+'.json').write_text(json.dumps(exported,indent=2)+'\\n')
'''
        subprocess.run([sys.executable, "-I", "-c", setup], cwd=outside, env=env, check=True)
        db_before = (outside / "quality.sqlite3").read_bytes()
        def run(expected, *arguments):
            result = subprocess.run([str(executable), "data", *map(str, arguments)], cwd=outside,
                                    env=env, capture_output=True, text=True, timeout=60)
            record = {"arguments": list(arguments), "expected_exit": expected, "actual_exit": result.returncode}
            commands.append(record)
            if result.returncode != expected:
                raise ValueError(f"Native command failed: {record}; {result.stderr}; {result.stdout}")
            return json.loads(result.stdout)
        workspace = "empty.json"; source_ids = []
        for i, (path, family) in enumerate((("manufacturing.json", "manufacturing-report"),
                ("stream-0.json", "toolpoint-stream-report"), ("stream-1.json", "toolpoint-stream-report"))):
            envelope = f"source-{i}.json"
            run(0, "retain", path, "--family", family, "--dataset-id", f"native-{i}", "--revision", "1",
                "--recorded-at", "2026-10-05T21:00:00Z", "--output", envelope)
            added = run(0, "add", envelope, "--label", f"native-{i}", "--workspace", workspace,
                        "--output", f"workspace-{i}.json")
            workspace = f"workspace-{i}.json"; source_ids.append(added["source_id"])
        request = ["review-toolpoint", "--manufacturing-source-id", source_ids[0],
                   "--stream-source-id", source_ids[1], "--stream-source-id", source_ids[2],
                   "--assessed-at-s", "12", "--workspace", workspace]
        first = run(0, *request, "--output", "reviewed.json")
        second = run(0, *request, "--output", "replayed.json")
        if first["status"] != "completed" or first["result"]["data"] != second["result"]["data"]:
            raise ValueError("Installed replay changed native containment data")
        if first["result"]["execution_id"] == second["result"]["execution_id"]:
            raise ValueError("Replayed review must have a fresh framework occurrence")
        data = first["result"]["data"]
        if (data["containment"]["quality"]["a001"]["state"] != "HELD"
                or data["authority"]["shipment_release"] != "not_authorized"
                or data["authority"]["action_authorized"] is not False):
            raise ValueError("Installed review changed native holds or authority")
        before = (outside / "reviewed.json").read_bytes()
        run(2, *request, "--output", "reviewed.json")
        if (outside / "reviewed.json").read_bytes() != before:
            raise ValueError("Create-only review workspace was overwritten")
        wrong = ["review-toolpoint", "--manufacturing-source-id", source_ids[1],
                 "--stream-source-id", source_ids[2], "--assessed-at-s", "12", "--workspace", workspace]
        refused = run(2, *wrong, "--output", "refused.json")
        if refused["status"] != "refused" or refused["result"] is not None:
            raise ValueError("A stream was accepted as a manufacturing source")
        if (outside / "quality.sqlite3").read_bytes() != db_before:
            raise ValueError("Data review wrote to the authoritative SQLite cache")
        for path in outside.glob("*.json"): shutil.copy2(path, output / path.name)
    receipt = {"python": sys.version, "platform": sys.platform,
        "source": source, "installed": installed, "verified_package_files": count,
        "wheel_sha256": hashlib.sha256(wheels[0].read_bytes()).hexdigest(),
        "native_command_journeys": len(commands), "native_commands": commands,
        "sqlite_bytes_unchanged": True, "physical_qualification": "not_established",
        "state_admission": "not_performed", "shipment_release": "not_authorized"}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__": main()
