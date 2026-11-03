"""Reproduce the optional local coordinator qualification inputs under /tmp.

No sudo, apt install, global packages, Docker, credentials, services or grants.
Application dependencies are inherited from the selected qualified ODSP Python;
the optional PostgreSQL/NATS drivers and test runner are separately hash-locked.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import urllib.request


def fetch(directory, item, filename):
    url = item["url"]
    if not url.startswith(("https://deb.debian.org/debian/", "https://files.pythonhosted.org/")):
        raise ValueError("unapproved qualification source")
    expected = item.get("sha256", item.get("SHA256"))
    path = directory / filename
    if path.exists():
        raw = path.read_bytes()
    else:
        with urllib.request.urlopen(url, timeout=30) as response:
            raw = response.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError("download exceeds fixed qualification archive budget")
        if sha256(raw).hexdigest() != expected:
            raise ValueError("download hash mismatch for " + filename)
        with path.open("xb") as stream:
            stream.write(raw)
    if sha256(raw).hexdigest() != expected:
        raise ValueError("retained archive hash mismatch for " + filename)
    if "Size" in item and len(raw) != int(item["Size"]):
        raise ValueError("archive size mismatch for " + filename)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--with-pooler", action="store_true",
                        help="include separately pinned optional PgBouncer test runtime")
    arguments = parser.parse_args()
    destination = arguments.destination.absolute()
    if (not destination.is_relative_to(Path("/tmp")) or destination == Path("/tmp")
            or destination.resolve() != destination):
        raise ValueError("select a new, non-symlinked private directory beneath /tmp")
    if platform.system() != "Linux" or platform.machine() != "x86_64" or sys.version_info[:2] != (3, 12):
        raise ValueError("qualification lock supports Python 3.12 on Linux x86_64")
    if shutil.disk_usage("/tmp").free < 1536 * 1024 * 1024:
        raise RuntimeError("require 512 MiB bootstrap capacity plus 1 GiB free-space reserve")
    destination.mkdir(mode=0o700, exist_ok=False)
    downloads, root = destination / "downloads", destination / "root"
    downloads.mkdir(); root.mkdir()
    lock = json.loads((Path(__file__).parent / "runtime-lock.json").read_text())
    for item in lock["debian_packages"]:
        archive = fetch(downloads, item, Path(item["Filename"]).name)
        subprocess.run(["dpkg-deb", "--extract", str(archive), str(root)], check=True)
    pooler_lock = Path(__file__).parent / "pooler-lock.json"
    if arguments.with_pooler:
        for item in json.loads(pooler_lock.read_text())["debian_packages"]:
            archive = fetch(downloads, item, Path(item["Filename"]).name)
            subprocess.run(["dpkg-deb", "--extract", str(archive), str(root)], check=True)
    for item in lock["python_wheels"]:
        fetch(downloads, item, item["filename"])
    venv = destination / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)], check=True)
    subprocess.run([str(venv / "bin/python"), "-m", "pip", "install", "--no-index", "--require-hashes",
                    "--find-links", str(downloads), "-r", str(Path(__file__).parent / "requirements.lock")], check=True)
    manifest = {"schema": "ciw.distributed-bootstrap.v1", "python": sys.version,
                "runtime_lock_sha256": sha256((Path(__file__).parent / "runtime-lock.json").read_bytes()).hexdigest(),
                "postgres_bin": str(root / "usr/lib/postgresql/17/bin"),
                "nats_server": str(root / "usr/sbin/nats-server"), "python_executable": str(venv / "bin/python"),
                "scope": "local_inputs_only_no_services_started"}
    if arguments.with_pooler:
        manifest["pgbouncer"] = str(root / "usr/sbin/pgbouncer")
        manifest["pooler_lock_sha256"] = sha256(pooler_lock.read_bytes()).hexdigest()
    (destination / "bootstrap.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
