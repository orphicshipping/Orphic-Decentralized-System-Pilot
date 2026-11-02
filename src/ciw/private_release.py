"""Verify an explicitly selected private build; this grants no qualification.

The launcher supplies the manifest and independently pins the compact identity.
The retained wheel binds the source, dependency inventory and service files. The
manifest is a consistency/provenance receipt, not a signature or a trust root.
Install without bytecode and launch with -B so executable caches cannot bypass
the installed-file check. No source checkout or network is used by verification.
"""
from __future__ import annotations

from hashlib import sha256
import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

IDENTITY_SCHEMA = "odsp.private-release-identity.v1"
MANIFEST_SCHEMA = "odsp.private-release-manifest.v1"
SOURCE_SCHEMA = "odsp.private-release-source.v1"
DISTRIBUTION = "computational-instrumentation-workbench"
SOURCE_MARKER = "_private_release_source.json"
IDENTITY_FIELDS = {"schema", "repository", "source_commit", "source_tree", "wheel_sha256", "package_digest"}
MANIFEST_FIELDS = {"schema", "identity", "wheel", "distribution", "package_files", "dependencies", "service_files", "pins_digest"}
MAX_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 20000
WHEEL_NAME = re.compile(r"computational_instrumentation_workbench-[A-Za-z0-9_.+]+-py3-none-any\.whl\Z")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value) -> str:
    return sha256(canonical(value)).hexdigest()


def file_hash(path: Path) -> str:
    import hashlib
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate JSON field: " + key)
        result[key] = value
    return result


def strict_json(raw):
    def invalid(value):
        raise ValueError("nonfinite JSON value: " + value)
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid)


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is not None


def validate_identity(value):
    require(isinstance(value, dict) and set(value) == IDENTITY_FIELDS, "invalid release identity fields")
    require(value["schema"] == IDENTITY_SCHEMA, "unsupported release identity schema")
    require(isinstance(value["repository"], str) and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value["repository"]) is not None,
        "invalid canonical release repository")
    for key in ("source_commit", "source_tree"):
        require(_hex(value[key], 40), "invalid release " + key)
    for key in ("wheel_sha256", "package_digest"):
        require(_hex(value[key], 64), "invalid release " + key)
    return dict(value)


def _path(name):
    require(isinstance(name, str) and name and "\\" not in name
            and not any(ord(c) < 32 or c in '<>:"|?*' for c in name), "unsafe release file path")
    path = PurePosixPath(name)
    require(not path.is_absolute() and all(part not in ("", ".", "..")
            and part == part.rstrip(" .") for part in name.split("/")), "unsafe release file path")
    require(not any(re.fullmatch(r"(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])",
                                 part.split(".", 1)[0].casefold()) for part in path.parts),
            "reserved release file path")
    require("__pycache__" not in [part.casefold() for part in path.parts]
            and not name.casefold().endswith(".pyc"), "executable caches are unsupported; use --no-compile and -B")
    return path


def _files(value):
    require(isinstance(value, dict) and 0 < len(value) <= MAX_ENTRIES, "invalid release file inventory")
    seen = set()
    for name, checksum in value.items():
        _path(name)
        require(name.casefold() not in seen, "ambiguous release file path")
        seen.add(name.casefold())
        require(_hex(checksum, 64), "invalid release file hash")
    return value


def wheel_files(wheel: Path):
    """Read bounded, unambiguous package bytes without extracting the archive."""
    require(wheel.is_file() and not wheel.is_symlink() and 0 < wheel.stat().st_size <= MAX_BYTES,
            "release wheel must be a bounded regular file")
    files, seen, prefixes, total = {}, set(), {}, 0
    with zipfile.ZipFile(wheel) as archive:
        require(len(archive.infolist()) <= MAX_ENTRIES, "release wheel entry budget exceeded")
        for info in archive.infolist():
            name = info.orig_filename.rstrip("/")
            path = _path(name)
            require(name.casefold() not in seen, "duplicate release wheel path")
            seen.add(name.casefold())
            for index in range(1, len(path.parts) + 1):
                prefix = "/".join(path.parts[:index])
                node = (prefix, index < len(path.parts) or info.is_dir())
                require(prefixes.setdefault(prefix.casefold(), node) == node, "ambiguous release wheel prefix")
            require(not stat.S_ISLNK(info.external_attr >> 16), "wheel symlinks are unsupported")
            require(not any(part.casefold().endswith(".data") for part in path.parts), "wheel relocation is unsupported")
            require(not name.casefold().startswith("ciw/") or name.startswith("ciw/"), "ambiguous package root")
            total += info.file_size
            require(0 <= info.file_size <= MAX_BYTES and total <= MAX_BYTES, "release wheel byte budget exceeded")
            if not info.is_dir() and name.startswith("ciw/"):
                files[name[4:]] = sha256(archive.read(info)).hexdigest()
    require("__init__.py" in files and SOURCE_MARKER in files, "release wheel source binding is missing")
    return files


def installed_files(files, root):
    require(root.is_dir() and not root.is_symlink(), "installed package must be a real directory")
    actual, total, entries = {}, 0, 0
    for path in root.rglob("*"):
        entries += 1
        require(entries <= MAX_ENTRIES and not path.is_symlink(), "invalid installed package entry")
        relative = path.relative_to(root).as_posix()
        _path(relative)
        if path.is_dir():
            continue
        require(path.is_file() and relative in files, "installed package file inventory differs")
        total += path.stat().st_size
        require(total <= MAX_BYTES, "installed package byte budget exceeded")
        actual[relative] = file_hash(path)
    require(actual == files, "installed package bytes differ from release wheel")


def closure(manifest):
    return {key: manifest[key] for key in ("dependencies", "service_files", "pins_digest")}


def package_digest(manifest):
    return digest({"package_files": manifest["package_files"], **closure(manifest)})


def verify_manifest(manifest_path):
    """Check the retained build without claiming an installed runtime check."""
    path = Path(manifest_path)
    require(path.is_absolute() and path.is_file() and not path.is_symlink(), "release manifest must be an explicit absolute regular file")
    require(path.stat().st_size <= 4 * 1024 * 1024, "release manifest byte budget exceeded")
    manifest = strict_json(path.read_bytes())
    require(isinstance(manifest, dict) and set(manifest) == MANIFEST_FIELDS, "invalid release manifest fields")
    require(manifest["schema"] == MANIFEST_SCHEMA, "unsupported release manifest schema")
    identity = validate_identity(manifest["identity"])
    wheel = manifest["wheel"]
    require(isinstance(wheel, dict) and set(wheel) == {"filename", "size_bytes"}, "invalid wheel binding")
    require(isinstance(wheel["filename"], str) and WHEEL_NAME.fullmatch(wheel["filename"]), "invalid release wheel name")
    require(type(wheel["size_bytes"]) is int and 0 < wheel["size_bytes"] <= MAX_BYTES, "invalid wheel byte count")
    distribution = manifest["distribution"]
    require(isinstance(distribution, dict) and set(distribution) == {"name", "version"}
            and distribution["name"] == DISTRIBUTION and isinstance(distribution["version"], str), "invalid distribution binding")
    _files(manifest["package_files"])
    _files(manifest["service_files"])
    dependencies = manifest["dependencies"]
    require(isinstance(dependencies, dict) and 0 < len(dependencies) <= 256, "invalid dependency inventory")
    for name, version in dependencies.items():
        require(isinstance(name, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                and isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version), "invalid dependency binding")
    require({"numpy", "websockets", "mcp", "starlette", "uvicorn", "jsonschema"} <= set(dependencies), "required private runtime dependencies are missing")
    require(_hex(manifest["pins_digest"], 64), "invalid preserved-source digest")
    require(identity["package_digest"] == package_digest(manifest), "release package digest mismatch")
    target = path.parent / wheel["filename"]
    files = wheel_files(target)
    require(target.stat().st_size == wheel["size_bytes"] and file_hash(target) == identity["wheel_sha256"], "release wheel bytes mismatch")
    require(files == manifest["package_files"], "release package file manifest mismatch")
    with zipfile.ZipFile(target) as archive:
        source = strict_json(archive.read("ciw/" + SOURCE_MARKER))
    require(source == {"schema": SOURCE_SCHEMA, "repository": identity["repository"],
                       "source_commit": identity["source_commit"], "source_tree": identity["source_tree"],
                       "closure_digest": digest(closure(manifest))}, "release source/closure binding mismatch")
    return manifest


def verify_release(manifest_path):
    """Verify the installed CIW, service files and exact dependency versions."""
    try:
        manifest = verify_manifest(manifest_path)
        distribution = metadata.distribution(DISTRIBUTION)
        require(distribution.version == manifest["distribution"]["version"], "installed distribution version mismatch")
        root = Path(distribution.locate_file("ciw"))
        spec = importlib.util.find_spec("ciw")
        require(spec is not None and spec.origin is not None
                and Path(spec.origin).resolve() == (root / "__init__.py").resolve(), "imported package differs from installed distribution")
        direct = distribution.read_text("direct_url.json")
        if direct:
            require(not strict_json(direct).get("dir_info", {}).get("editable", False), "editable release installations are unsupported")
        installed_files(manifest["package_files"], root)
        for name, version in manifest["dependencies"].items():
            require(metadata.version(name) == version, "installed release dependency mismatch: " + name)
        service_root = Path(manifest_path).parent.parent
        for name, checksum in manifest["service_files"].items():
            target = service_root / name
            require(target.is_file() and not target.is_symlink()
                    and target.resolve().is_relative_to(service_root.resolve())
                    and file_hash(target) == checksum, "installed release service file mismatch: " + name)
        for directory in {str(PurePosixPath(name).parent) for name in manifest["service_files"]}:
            expected = {name[len(directory) + 1:]: checksum for name, checksum in manifest["service_files"].items()
                        if str(PurePosixPath(name).parent) == directory}
            installed_files(expected, service_root / directory)
        return dict(manifest["identity"])
    except (OSError, TypeError, KeyError, AttributeError, zipfile.BadZipFile, metadata.PackageNotFoundError) as exc:
        raise ValueError("private release verification refused: " + str(exc)) from exc
