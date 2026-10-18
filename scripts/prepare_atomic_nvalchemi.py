"""Trusted-operator metadata probe; records an explicit CPU/CUDA binding for review.

Never called by saved JSON, MCP or Session. Installs nothing and does not run a
prediction. The separate native gate consumes the reviewed output.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from ciw.session import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): parser.error("binding output already exists")
    root = args.repository.resolve()
    actual = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True).stdout.strip()
    if actual != args.revision: parser.error("repository HEAD differs from the requested full revision")
    code = "import sys,json;sys.path.insert(0,sys.argv[1]);from ciw.nvalchemi_worker import probe_environment;print(json.dumps(probe_environment(),allow_nan=False))"
    executable = args.python.expanduser().absolute()
    probe = subprocess.run([str(executable), "-I", "-B", "-c", code, str(root / "src")],
                           cwd=root.parent, check=True, timeout=300, capture_output=True, text=True)
    environment = json.loads(probe.stdout)
    binding = {"net_root": str(root), "net_revision": actual, "python_executable": str(executable),
               "expected_python_sha256": sha256(executable.read_bytes()).hexdigest(),
               "expected_python_version": environment["python_version"],
               "expected_dependencies": environment["dependencies"], "device": args.device}
    write_json(args.output, binding)
    print(f"binding={args.output}; metadata_only=true; native_qualification=NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
