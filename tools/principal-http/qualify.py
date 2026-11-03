"""Run the pinned local native HTTP qualification and retain exact source hashes.

This script does not configure a real issuer, publish code or deploy a service.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    expected = {"mcp": "2.3.0", "PyJWT": "2.15.1", "cryptography": "46.0.0",
                "uvicorn": "0.54.0", "pytest": "9.0.2"}
    actual = {name: version(name) for name in expected}
    if actual != expected:
        raise SystemExit("Pinned HTTP qualification dependencies required")
    target = args.evidence.resolve()
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    command = [sys.executable, "-m", "pytest", "tests/test_principal_http.py",
               "tests/test_principal_http_audited.py", "-q", "--tb=short",
               "--junitxml=" + str(target / "tests.xml")]
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root / "src")}
    with (target / "tests.log").open("xb") as stream:
        result = subprocess.run(command, cwd=root, env=environment, stdout=stream, stderr=subprocess.STDOUT,
                                timeout=180, check=False)
    files = ["src/ciw/principal_http.py", "tests/test_principal_http.py",
             "tests/test_principal_http_audited.py", "tests/test_campaign_audited_host.py",
             "docs/PRINCIPAL_HTTP.md", "src/ciw/principal_policy.py", "src/ciw/audited_host.py",
             "src/ciw/audit_chain.py", "src/ciw/mcp_server.py", "tools/principal-http/qualify.py"]
    record = {"schema": "ciw.principal-http-qualification.v1", "scope": "local_protocol_only",
              "live_oauth_login": False, "hosted": False, "python": sys.version.split()[0],
              "dependencies": actual, "command": command, "exit_code": result.returncode,
              "source_sha256": {name: sha256((root / name).read_bytes()).hexdigest() for name in files},
              "evidence_sha256": {name: sha256((target / name).read_bytes()).hexdigest()
                                  for name in ("tests.log", "tests.xml") if (target / name).is_file()}}
    (target / "receipt.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"exit_code": result.returncode, "receipt": str(target / "receipt.json")}))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
