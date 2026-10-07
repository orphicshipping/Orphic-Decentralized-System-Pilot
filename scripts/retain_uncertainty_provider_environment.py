"""Retain pinned provider wheels/source archives and verify installed wheel code.

Release-host tool, explicitly invoked by an operator. Reads PyPI metadata and
downloads the selected installed versions; does not install or execute archives.
The resulting wheelhouse/lock is specific to this interpreter and platform.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import platform
import sys
import tarfile
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile

from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename


def read_url(url, limit):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"pypi.org", "files.pythonhosted.org"}:
        raise ValueError("Only the selected PyPI release and files.pythonhosted.org are download sources")
    with urlopen(url, timeout=60) as response:
        final = urlparse(response.url)
        if final.scheme != "https" or final.hostname not in {"pypi.org", "files.pythonhosted.org"}:
            raise ValueError("Artifact redirected outside the allowed release hosts")
        raw = response.read(limit+1)
    if len(raw) > limit:
        raise ValueError("Artifact exceeded size bound")
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pins", type=Path, required=True, help="Exact name==version lines")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    pins = []
    for line in args.pins.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, version = line.strip().split("==")
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in name+version):
            raise ValueError("Pins must be literal package names and versions")
        if metadata.version(name) != version:
            raise ValueError("Run in the exact provisioned environment")
        pins.append((name, version))
    if not 4 <= len(pins) <= 64 or len({canonicalize_name(n) for n, _ in pins}) != len(pins):
        raise ValueError("Require 4..64 unique pinned distributions")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir/"wheelhouse").mkdir()
    (args.output_dir/"sources").mkdir()
    (args.output_dir/"metadata").mkdir()
    tags = list(sys_tags())
    rank = {tag:i for i, tag in enumerate(tags)}

    def capture(pin):
        name, version = pin
        raw = read_url(f"https://pypi.org/pypi/{name}/{version}/json", 8*1024*1024)
        data = json.loads(raw)
        (args.output_dir/"metadata"/f"{name}-{version}.json").write_bytes(raw)
        candidates = []
        for entry in data["urls"]:
            if entry["packagetype"] == "bdist_wheel":
                wheel_name, wheel_version, _, wheel_tags = parse_wheel_filename(entry["filename"])
                compatible = [rank[t] for t in wheel_tags if t in rank]
                if compatible and wheel_name == canonicalize_name(name) and str(wheel_version) == version:
                    candidates.append((min(compatible), entry))
        if not candidates:
            raise ValueError(f"No compatible released wheel for {name}=={version}")
        chosen = min(candidates, key=lambda x:x[0])[1]
        selected = [("wheelhouse",chosen)]
        if canonicalize_name(name) in {"pydynamic","metrolopy"}:
            source = [x for x in data["urls"] if x["packagetype"] == "sdist"]
            if len(source) != 1:
                raise ValueError("Require one identified source release")
            selected.append(("sources",source[0]))
        artifacts = []
        for directory, item in selected:
            filename = item["filename"]
            if Path(filename).name != filename:
                raise ValueError("Invalid upstream filename")
            content = read_url(item["url"], 128*1024*1024)
            digest = sha256(content).hexdigest()
            if digest != item["digests"]["sha256"] or len(content) != item["size"]:
                raise ValueError("Artifact differs from release metadata")
            path = args.output_dir/directory/filename; path.write_bytes(content)
            artifacts.append({"kind":directory,"filename":filename,"sha256":digest,"bytes":len(content),"url":item["url"]})
        installed = metadata.distribution(name)
        code = []
        with zipfile.ZipFile(args.output_dir/"wheelhouse"/chosen["filename"]) as wheel:
            for member in wheel.infolist():
                # Compiled numerical libraries and Python source must match the
                # identified wheel. RECORD/launchers may be rewritten at install.
                if member.filename.lower().endswith((".py", ".pyd", ".dll", ".so")) and ".data/" not in member.filename:
                    member_path = PurePosixPath(member.filename)
                    if member_path.is_absolute() or ".." in member_path.parts or "\\" in member.filename:
                        raise ValueError("Unsafe wheel member path")
                    installed_path = Path(installed.locate_file(member.filename)).resolve()
                    if not installed_path.is_relative_to(Path(installed.locate_file("")).resolve()):
                        raise ValueError("Installed wheel file escapes its distribution root")
                    expected = wheel.read(member)
                    actual = installed_path.read_bytes()
                    if actual != expected:
                        raise ValueError(f"Installed code differs from selected wheel: {name}/{member.filename}")
                    code.append([member.filename,sha256(actual).hexdigest()])
        license_files = []
        with zipfile.ZipFile(args.output_dir/"wheelhouse"/chosen["filename"]) as wheel:
            for item in wheel.namelist():
                if "licen" in item.lower() and not item.endswith("/"):
                    license_files.append({"path":item,"sha256":sha256(wheel.read(item)).hexdigest()})
        source_licenses = []
        source_wheel_matches = []
        for artifact in artifacts:
            if artifact["kind"] == "sources":
                with tarfile.open(args.output_dir/"sources"/artifact["filename"], "r:gz") as source:
                    source_python = {item.name.split("/src/",1)[1]:item for item in source.getmembers()
                        if item.isfile() and "/src/" in item.name and item.name.endswith(".py")}
                    with zipfile.ZipFile(args.output_dir/"wheelhouse"/chosen["filename"]) as wheel:
                        for filename in wheel.namelist():
                            if filename.endswith(".py"):
                                item = source_python.get(filename)
                                if item is None or item.size > 4*1024*1024 or source.extractfile(item).read() != wheel.read(filename):
                                    raise ValueError("Provider wheel Python differs from the identified source archive")
                                source_wheel_matches.append(filename)
                    for item in source.getmembers():
                        if "licen" in item.name.lower() and item.isfile():
                            if item.size > 256*1024:
                                raise ValueError("License document exceeds bound")
                            source_licenses.append({"path":item.name,"sha256":sha256(source.extractfile(item).read()).hexdigest()})
        return {"name":name,"version":version,"artifacts":artifacts,"source_license_files":source_licenses,
            "source_wheel_python_files_verified":len(source_wheel_matches),
            "installed_code_files_verified":len(code), "installed_code_inventory_sha256":sha256(json.dumps(code,sort_keys=True).encode()).hexdigest(),
            "release_metadata_sha256":sha256(raw).hexdigest(), "license_expression":data["info"].get("license_expression"),
            "license":data["info"].get("license"),"license_files":license_files,
            "requires_dist":data["info"].get("requires_dist") or []}

    with ThreadPoolExecutor(max_workers=4) as pool:
        distributions = list(pool.map(capture, pins))
    report = {"schema":"ciw.optional-provider-environment.v1","python":platform.python_version(),
        "platform":platform.platform(),"interpreter":sys.executable,
        "distributions":distributions,"physical_qualification":"not_established",
        "scope":"Pinned releases, archive byte identity and installed wheel code equality; not publisher authentication or a scientific qualification"}
    (args.output_dir/"environment.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    lock = ["# Platform-specific captured wheels; install with --require-hashes --only-binary=:all:"]
    for item in distributions:
        wheel = next(x for x in item["artifacts"] if x["kind"] == "wheelhouse")
        lock.append(f"{item['name']}=={item['version']} --hash=sha256:{wheel['sha256']}")
    (args.output_dir/"requirements.lock").write_text("\n".join(lock)+"\n",encoding="utf-8")
    print(json.dumps({"distributions":len(distributions), "installed_code_files_verified":sum(x["installed_code_files_verified"] for x in distributions),
        "report":str(args.output_dir/"environment.json")}))


if __name__ == "__main__":
    main()
