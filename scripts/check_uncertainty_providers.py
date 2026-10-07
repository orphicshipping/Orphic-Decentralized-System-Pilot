"""Qualify the provisioned optional provider slice and retain exact CLI evidence.

Run using the separately pinned provider interpreter. No installation, network,
hardware I/O or capability registration is performed by this qualifier.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--test-file", type=Path, default=Path(__file__).resolve().parents[1]/"tests/test_uncertainty_providers.py")
    parser.add_argument("--installed", action="store_true", help="Require NCP imported outside the source checkout")
    args = parser.parse_args()
    from ciw import uncertainty_providers as providers
    if args.installed and Path(providers.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1]/"src"):
        raise ValueError("Installed qualification must not import the source checkout")
    # Missing providers or version drift is a failure, never an optional skip.
    runtimes = [providers._identity(name) for name in ("PyDynamic", "metrolopy")]
    args.output_dir.mkdir(parents=True, exist_ok=False)
    python_command = [sys.executable] + (["-I"] if args.installed else [])
    child_env = os.environ.copy()
    if args.installed:
        # -I protects these direct children. Removing Python path overrides also
        # protects grandchildren started by tests and provider integration code.
        for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE"):
            child_env.pop(name, None)
    test_command = python_command + ["-m","pytest",str(args.test_file),"-q","-ra","-o","pythonpath=",
        f"--junitxml={args.output_dir/'tests.xml'}"]
    tested = subprocess.run(test_command,capture_output=True,timeout=240,env=child_env)
    (args.output_dir/"pytest.stdout.txt").write_bytes(tested.stdout)
    (args.output_dir/"pytest.stderr.txt").write_bytes(tested.stderr)
    suites = ET.parse(args.output_dir/"tests.xml").getroot().findall("testsuite")
    counts = {key:sum(int(suite.attrib.get(key,0)) for suite in suites) for key in ("tests","failures","errors","skipped")}
    fixture = runpy.run_path(str(args.test_file))
    cases = [("fir-valid", "pydynamic-fir", fixture["fir"]()),
        ("fir-full", "pydynamic-fir", fixture["fir"](mode="full")),
        ("fir-uncertain-coefficients", "pydynamic-fir", fixture["fir"](uncertain_coefficients=True)),
        ("fir-exact", "pydynamic-fir", fixture["fir"](exact=True)),
        ("fir-tiny", "pydynamic-fir", fixture["fir"](scale=1e-280)),
        ("metrolopy-shared", "metrolopy-link", fixture["comparison"]())]
    calls = []
    for name, operation, request in cases:
        source, output = args.output_dir/(name+"-request.json"), args.output_dir/(name+"-result.json")
        raw = (json.dumps(request,sort_keys=True,indent=2)+"\n").encode();source.write_bytes(raw)
        command = python_command + ["-m","ciw.uncertainty_providers",operation,str(source),"--output",str(output)]
        run = subprocess.run(command,capture_output=True,timeout=60,env=child_env)
        (args.output_dir/(name+"-stderr.txt")).write_bytes(run.stderr)
        if run.returncode:
            raise RuntimeError(f"Provider CLI case failed: {name}; see retained stderr")
        result = json.loads(output.read_bytes())
        if (result["input_bytes_ref"] != "sha256:"+sha256(raw).hexdigest()
                or source.read_bytes() != raw or result["numerical_status"] != "PASS"
                or result["limits"]["physical_qualification"] != "HELD"):
            raise ValueError("CLI evidence integrity or qualification-state mismatch")
        calls.append({"case":name,"command":command,"request_sha256":sha256(raw).hexdigest(),
            "result_sha256":sha256(output.read_bytes()).hexdigest(),"checks":len(result["checks"]),"status":"PASS"})
    report = {"schema":"ciw.uncertainty-provider-qualification.v1", "status":"PASS" if tested.returncode == 0
        and counts["tests"] >= 44 and not any(counts[key] for key in ("failures","errors","skipped")) else "FAIL",
        "scope":"Bounded synthetic FIR and first-order Link affine/mean comparisons plus refusals; no physical qualification",
        "test_counts":counts,"minimum_tests":44,"installed_child_isolation":args.installed,
        "test_command":test_command,"test_file_sha256":sha256(args.test_file.read_bytes()).hexdigest(),
        "runtime_file":str(Path(providers.__file__).resolve()),"provider_identities":runtimes,"cli_calls":calls,
        "known_limits":["PyDynamic 2.5.1 is archived upstream since May 2024",
            "MetroloPy may lose tiny mixed-scale cross-covariance or fail on degenerate correlation; independent arithmetic checks refuse these results",
            "Timing and model discrepancy uncertainty are not included in FIR; exact timing must be explicitly declared",
            "No Monte Carlo, arbitrary unit conversion, constrained-coordinate model or MCP host binding is qualified"],
        "physical_qualification":"HELD"}
    (args.output_dir/"qualification.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"test_counts":counts,"cli_calls":len(calls),"report":str(args.output_dir/"qualification.json")}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
