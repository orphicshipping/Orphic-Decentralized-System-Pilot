"""Prepare and provision a bounded, hash-locked offline Link Python environment.

Release-engineering tool, not a new NET runtime or updater. Inputs are reviewed
local wheels and an independently selected pyproject. This tool never downloads,
builds source packages, imports application code, replaces an environment, or
claims scientific/hardware qualification. Requires the PyPA packaging library
(already a dependency of NET's pytest development tooling) on the release host.
"""
from __future__ import annotations

import argparse
import base64
import configparser
import csv
from email.parser import BytesParser
from email.policy import compat32
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import select
import stat
import subprocess
import sys
import sysconfig
import time
import tomllib
import zipfile

from packaging.markers import default_environment
from packaging.requirements import Requirement, InvalidRequirement
from packaging.specifiers import SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

SCHEMA = 'ciw.link-offline-bundle.v1'
PROFILES = {'runtime': (), 'link': ('legibility',), 'qualification': ('dev', 'legibility')}
MAX_WHEELS = 64
MAX_WHEEL = 128 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_MEMBERS = 20000
MAX_METADATA = 4 * 1024 * 1024
MAX_COMMAND_OUTPUT = 8 * 1024 * 1024
REF = re.compile(r'sha256:[0-9a-f]{64}')
BOOTSTRAP_NAMES = {'pip', 'setuptools'}


def digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def _json(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def _loads(raw):
    def pairs(values):
        out = {}
        for key, value in values:
            if key in out:
                raise ValueError('Duplicate JSON field')
            out[key] = value
        return out
    def reject(value):
        raise ValueError('Nonfinite JSON value')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)


def _path(value):
    path = Path(value).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Deployment paths may not traverse symlinks')
    return path


def _read(path, limit, *, allow_empty=False):
    path = _path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not (0 if allow_empty else 1) <= info.st_size <= limit:
        raise ValueError('Require a nonempty bounded regular file: ' + path.name)
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) != info.st_size or len(raw) > limit:
        raise ValueError('File size changed or exceeded the input bound')
    return raw


def _anchor(raw, expected):
    if type(expected) is not str or REF.fullmatch(expected) is None or digest(raw) != expected:
        raise ValueError('Input differs from its independently selected SHA-256')


def _write(path, raw):
    path = _path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def target():
    """One target interpreter/platform per bundle, not a cross-compilation claim."""
    return {'markers': default_environment(), 'platform': sysconfig.get_platform(),
            'soabi': sysconfig.get_config_var('SOABI'), 'cache_tag': sys.implementation.cache_tag}


def _member(name):
    if type(name) is not str or not name or len(name) > 512 or '\\' in name or ':' in name:
        raise ValueError('Unsupported wheel member path')
    p = PurePosixPath(name)
    if p.is_absolute() or p.as_posix() != name or any(x in {'', '.', '..'} for x in p.parts):
        raise ValueError('Unsafe wheel member path')
    lower = name.casefold()
    # Python imports sitecustomize by module name: a package or native module
    # is also a startup hook. Filename-only .py checks do not cover that boundary.
    # Reserve these top-level import roots, including native ABI-tagged files.
    import_root = p.parts[0].casefold().partition('.')[0]
    if (lower.endswith(('.pth', '.pyc', '.pyo')) or p.name.casefold() in {'sitecustomize.py', 'usercustomize.py'}
            or any(x.casefold().endswith('.data') for x in p.parts)
            or import_root in {'pip', 'sitecustomize', 'usercustomize'}):
        raise ValueError('Startup hooks, pip replacement and .data spreading are outside this library-wheel profile')
    return p


def inspect_wheel(filename, raw):
    """Check bounded wheel data without extraction, imports or code execution."""
    if type(filename) is not str or Path(filename).name != filename or not filename.endswith('.whl'):
        raise ValueError('Require a single wheel basename')
    name, version, _, tags = parse_wheel_filename(filename)
    if name in BOOTSTRAP_NAMES:
        raise ValueError('Replacing the trusted Python bootstrap is a separate operation')
    if not set(tags).intersection(sys_tags()):
        raise ValueError('Wheel is incompatible with the current target')
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_WHEEL:
        raise ValueError('Wheel exceeds archive byte bounds')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos = z.infolist()
        if not 1 <= len(infos) <= MAX_MEMBERS or sum(x.file_size for x in infos) > MAX_EXPANDED:
            raise ValueError('Wheel exceeds expanded bounds')
        members = {}; folded = set()
        for entry in infos:
            if entry.is_dir():
                _member(entry.filename.rstrip('/'))
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if mode not in (0, stat.S_IFDIR) or entry.file_size or entry.flag_bits & 1:
                    raise ValueError('Directory entry is a link, special or nonempty member')
                continue
            path = _member(entry.filename)
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if mode not in (0, stat.S_IFREG) or entry.flag_bits & 1:
                raise ValueError('Wheel contains a link, special or encrypted member')
            if entry.filename.casefold() in folded:
                raise ValueError('Duplicate or case-colliding wheel member')
            folded.add(entry.filename.casefold()); members[entry.filename] = entry
        meta_dirs = {PurePosixPath(x).parts[0] for x in members if PurePosixPath(x).parts[0].endswith('.dist-info')}
        if len(meta_dirs) != 1:
            raise ValueError('Require one distribution metadata directory')
        meta_dir = next(iter(meta_dirs))
        expected_stem = str(name).replace('-', '_') + '-' + str(version)
        if meta_dir != expected_stem + '.dist-info':
            raise ValueError('Wheel filename and metadata directory differ')
        required = {meta_dir + '/' + x for x in ('METADATA', 'WHEEL', 'RECORD')}
        if not required <= members.keys() or any(members[x].file_size > MAX_METADATA for x in required):
            raise ValueError('Missing or oversized wheel metadata')
        metadata = BytesParser(policy=compat32).parsebytes(z.read(meta_dir + '/METADATA'))
        if len(metadata.get_all('Requires-Dist', [])) > 256 or len(metadata.get_all('Provides-Extra', [])) > 64:
            raise ValueError('Wheel dependency declaration exceeds the review budget')
        wheel = BytesParser(policy=compat32).parsebytes(z.read(meta_dir + '/WHEEL'))
        for key in ('Name', 'Version', 'Requires-Python'):
            if len(metadata.get_all(key, [])) != 1:
                raise ValueError('Missing or duplicate wheel metadata field: ' + key)
        if canonicalize_name(metadata['Name']) != name or Version(metadata['Version']) != version:
            raise ValueError('Wheel filename and METADATA identity differ')
        if not SpecifierSet(metadata['Requires-Python']).contains(default_environment()['python_full_version'], prereleases=True):
            raise ValueError('Wheel Python version constraint is unsatisfied')
        if (wheel.get_all('Wheel-Version') != ['1.0'] or
                wheel.get_all('Root-Is-Purelib') not in (['true'], ['false']) or
                set(wheel.get_all('Tag', [])) != {str(x) for x in tags}):
            raise ValueError('Unsupported or inconsistent wheel format/tag metadata')
        rows = list(csv.reader(io.StringIO(z.read(meta_dir + '/RECORD').decode('utf-8'))))
        checks = {}; record_path = meta_dir + '/RECORD'
        for row in rows:
            if len(row) != 3 or row[0] in checks:
                raise ValueError('Malformed or duplicate RECORD row')
            checks[row[0]] = row[1:]
        if set(checks) != set(members) or checks.get(record_path) != ['', '']:
            raise ValueError('RECORD does not cover the exact wheel inventory')
        hashes = {}
        for member, entry in members.items():
            payload = z.read(entry)
            hashes[member] = digest(payload)
            if member == record_path:
                continue
            encoded, size = checks[member]
            if not size.isdigit() or str(len(payload)) != size:
                raise ValueError('RECORD size mismatch')
            algorithm, sep, expected = encoded.partition('=')
            if sep != '=' or algorithm not in {'sha256', 'sha384', 'sha512'}:
                raise ValueError('Unsupported RECORD hash')
            actual = base64.urlsafe_b64encode(hashlib.new(algorithm, payload).digest()).rstrip(b'=').decode()
            if actual != expected:
                raise ValueError('RECORD hash mismatch')
        ep = meta_dir + '/entry_points.txt'
        commands = []
        if ep in members:
            if members[ep].file_size > 65536:
                raise ValueError('Oversized entry-point metadata')
            parser = configparser.ConfigParser(interpolation=None, strict=True)
            parser.optionxform = str
            parser.read_string(z.read(ep).decode('utf-8'))
            for group in ('console_scripts', 'gui_scripts'):
                for command in parser[group] if group in parser else ():
                    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', command)
                            or re.fullmatch(r'(pip|python)(\d+(\.\d+)*)?', command.casefold())):
                        raise ValueError('Unsafe or reserved bootstrap command')
                    commands.append(command)
        return {'filename': filename, 'name': str(name), 'version': str(version),
                'bytes': len(raw), 'sha256': digest(raw), 'expanded_bytes': sum(x.file_size for x in infos),
                'requires_python': metadata['Requires-Python'],
                'requires_dist': metadata.get_all('Requires-Dist', []),
                'extras': sorted(canonicalize_name(x) for x in metadata.get_all('Provides-Extra', [])),
                'file_hashes': hashes, 'record_path': record_path, 'commands': commands}


def _active(requirements, extras, environment):
    contexts = {''} | set(extras)
    for raw in requirements:
        try:
            req = Requirement(raw)
        except InvalidRequirement as exc:
            raise ValueError('Malformed dependency metadata') from exc
        if req.marker is None or any(req.marker.evaluate({**environment, 'extra': x}) for x in contexts):
            if req.url is not None:
                raise ValueError('Active direct-URL dependencies are forbidden in an offline library bundle')
            yield req


def _key(req):
    return (canonicalize_name(req.name), str(req.specifier), tuple(sorted(req.extras)))


def _plan(project_raw, wheels, profile):
    if profile not in PROFILES:
        raise ValueError('Unsupported offline deployment profile')
    if sys.platform != 'linux':
        raise ValueError('Initial offline provisioning profile supports Linux only')
    project = tomllib.loads(project_raw.decode('utf-8'))['project']
    if project.get('dynamic'):
        raise ValueError('Dynamic project metadata is outside this bounded release profile')
    project_name, project_version = canonicalize_name(project['name']), str(Version(project['version']))
    selected_extras = PROFILES[profile]
    root = wheels.get(project_name)
    if root is None or root['version'] != project_version:
        raise ValueError('Missing exact root project wheel')
    if project.get('requires-python') != root['requires_python']:
        raise ValueError('Project and wheel Python constraints differ')
    expected = list(project.get('dependencies', []))
    for extra in selected_extras:
        if extra not in project.get('optional-dependencies', {}):
            raise ValueError('Selected project extra is not declared')
        expected.extend(project['optional-dependencies'][extra])
    env = default_environment()
    active_project = list(_active(expected, selected_extras, env))
    for req in active_project:
        pins = list(req.specifier)
        if len(pins) != 1 or pins[0].operator != '==' or '*' in pins[0].version:
            raise ValueError('Direct project requirements must remain exact pins')
    if sorted(map(_key, active_project)) != sorted(map(_key, _active(root['requires_dist'], selected_extras, env))):
        raise ValueError('Project and selected root-wheel dependency metadata disagree')
    needed = {project_name: set(selected_extras)}
    processed = {}
    while True:
        pending = [n for n, extra in needed.items() if processed.get(n) != frozenset(extra)]
        if not pending:
            break
        for name in pending:
            if name not in wheels:
                raise ValueError('Missing transitive wheel: ' + name)
            extra = needed[name]
            if not extra <= set(wheels[name]['extras']):
                raise ValueError('Requested dependency extra is undeclared: ' + name)
            processed[name] = frozenset(extra)
            for req in _active(wheels[name]['requires_dist'], extra, env):
                dep = canonicalize_name(req.name)
                if dep not in wheels or not req.specifier.contains(wheels[dep]['version'], prereleases=True):
                    raise ValueError('Missing or conflicting dependency: ' + dep)
                needed.setdefault(dep, set()).update(canonicalize_name(x) for x in req.extras)
    if set(needed) != set(wheels):
        raise ValueError('Wheelhouse contains packages outside the selected dependency closure')
    owners = {}; top_owners = {}; commands = set()
    for name, meta in sorted(wheels.items()):
        for member in meta['file_hashes']:
            folded = member.casefold()
            if folded in owners:
                raise ValueError('Installed file overlap between distributions')
            owners[folded] = name
            top = PurePosixPath(member).parts[0].casefold()
            if top in top_owners and top_owners[top] != name:
                raise ValueError('Shared top-level package ownership is unsupported')
            top_owners[top] = name
        for command in meta['commands']:
            if command.casefold() in commands:
                raise ValueError('Console command overlap')
            commands.add(command.casefold())
    lines = []
    for name, meta in sorted(wheels.items()):
        extras = '[' + ','.join(sorted(needed[name])) + ']' if needed[name] else ''
        lines.append(f"{name}{extras}=={meta['version']} --hash={meta['sha256']}")
    lock = ('\n'.join(lines) + '\n').encode()
    manifest = {'schema': SCHEMA, 'profile': profile, 'project_ref': digest(project_raw),
                'project': {'name': project_name, 'version': project_version}, 'target': target(),
                'root_wheel_ref': root['sha256'], 'requirements_ref': digest(lock),
                'wheels': [{k: m[k] for k in ('filename', 'name', 'version', 'bytes', 'sha256')}
                           for _, m in sorted(wheels.items())]}
    return manifest, lock


def _inspect_wheels(raws):
    if not 1 <= len(raws) <= MAX_WHEELS or sum(map(len, raws.values())) > MAX_TOTAL:
        raise ValueError('Wheelhouse exceeds aggregate limits')
    out = {}; expanded = 0
    for filename, raw in sorted(raws.items()):
        meta = inspect_wheel(filename, raw)
        expanded += meta['expanded_bytes']
        if expanded > MAX_EXPANDED:
            raise ValueError('Wheelhouse exceeds aggregate expanded-byte bounds')
        if meta['name'] in out:
            raise ValueError('One wheel per distribution per target is required')
        out[meta['name']] = meta
    return out


def _summary(manifest, ref, status):
    return {'status': status, 'manifest_ref': ref, 'project': manifest['project'],
            'profile': manifest['profile'], 'package_count': len(manifest['wheels']),
            'target': manifest['target'], 'executes_application': False,
            'physical_qualification': 'NOT_ESTABLISHED', 'scientific_tests_run': False,
            'publisher_authenticated': False, 'network_access': False}


def prepare(project_path, wheelhouse, output, *, expected_project_ref, expected_root_ref, profile='link'):
    project_raw = _read(project_path, 262144); _anchor(project_raw, expected_project_ref)
    folder = _path(wheelhouse)
    entries = list(folder.iterdir())
    if not 1 <= len(entries) <= MAX_WHEELS or any(p.suffix != '.whl' for p in entries):
        raise ValueError('Supply a bounded wheel-only directory')
    raws = {}; total = 0
    for path in entries:
        data = _read(path, MAX_WHEEL); total += len(data)
        if total > MAX_TOTAL:
            raise ValueError('Wheelhouse aggregate byte limit exceeded')
        raws[path.name] = data
    inspected = _inspect_wheels(raws)
    manifest, lock = _plan(project_raw, inspected, profile)
    _anchor(raws[next(m['filename'] for m in inspected.values() if m['name'] == manifest['project']['name'])], expected_root_ref)
    root = _path(output)
    if folder == root or folder in root.parents or root in folder.parents:
        raise ValueError('Bundle destination must be separate from the input wheelhouse')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root/'wheels').mkdir(mode=0o700)
    _write(root/'pyproject.toml', project_raw); _write(root/'requirements.txt', lock)
    for name, raw in raws.items(): _write(root/'wheels'/name, raw)
    manifest_raw = _json(manifest); _write(root/'manifest.json', manifest_raw)
    return _summary(manifest, digest(manifest_raw), 'PREPARED_NOT_AUTHORIZED')


def _freeze(directory, expected_manifest_ref):
    root = _path(directory)
    raw = _read(root/'manifest.json', 262144); _anchor(raw, expected_manifest_ref)
    manifest = _loads(raw)
    if type(manifest) is not dict or set(manifest) != {'schema', 'profile', 'project_ref', 'project', 'target',
                                                    'root_wheel_ref', 'requirements_ref', 'wheels'}:
        raise ValueError('Malformed bundle manifest')
    if manifest['schema'] != SCHEMA or manifest['target'] != target():
        raise ValueError('Bundle targets a different interpreter or platform')
    rows = manifest['wheels']
    if type(rows) is not list or not 1 <= len(rows) <= MAX_WHEELS:
        raise ValueError('Unbounded wheel manifest')
    if {x.name for x in root.iterdir()} != {'manifest.json', 'pyproject.toml', 'requirements.txt', 'wheels'}:
        raise ValueError('Bundle has omitted or additional artifacts')
    _path(root/'wheels')
    frozen = {'manifest.json': raw, 'pyproject.toml': _read(root/'pyproject.toml', 262144),
              'requirements.txt': _read(root/'requirements.txt', 65536)}
    _anchor(frozen['pyproject.toml'], manifest['project_ref'])
    names = set(); total = 0; raws = {}
    for row in rows:
        if type(row) is not dict or set(row) != {'filename', 'name', 'version', 'bytes', 'sha256'}:
            raise ValueError('Malformed wheel selection')
        name = row['filename']
        if type(name) is not str or PurePosixPath(name).name != name or '\\' in name or name in names:
            raise ValueError('Invalid or duplicate selected wheel filename')
        if type(row['bytes']) is not int or not 0 < row['bytes'] <= MAX_WHEEL:
            raise ValueError('Invalid declared wheel size')
        total += row['bytes']
        if total > MAX_TOTAL: raise ValueError('Aggregate wheel limit exceeded')
        data = _read(root/'wheels'/name, row['bytes'])
        if len(data) != row['bytes']: raise ValueError('Wheel size differs')
        _anchor(data, row['sha256']); names.add(name); raws[name] = data
        frozen['wheels/'+name] = data
    if {x.name for x in (root/'wheels').iterdir()} != names:
        raise ValueError('Wheel directory has omitted or additional files')
    inspected = _inspect_wheels(raws)
    expected, lock = _plan(frozen['pyproject.toml'], inspected, manifest['profile'])
    if _json(expected) != _json(manifest) or lock != frozen['requirements.txt']:
        raise ValueError('Manifest/requirements differ from reviewed project and dependency closure')
    return frozen, manifest, inspected


def inspect_bundle(directory, *, expected_manifest_ref):
    _, manifest, _ = _freeze(directory, expected_manifest_ref)
    return _summary(manifest, expected_manifest_ref, 'VERIFIED_INPUTS_NOT_QUALIFIED')


def _environment(root):
    # Never inherit Python paths, package indexes, credentials, proxy settings,
    # pip targets/user flags, or arbitrary host configuration from the caller.
    home = root/'bootstrap-home'; home.mkdir(mode=0o700, exist_ok=True)
    env = {'PATH': os.defpath, 'HOME': str(home), 'XDG_CACHE_HOME': str(home/'cache'),
           'PIP_CONFIG_FILE': os.devnull, 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
           'LANG': 'C.UTF-8'}
    if os.name == 'nt':
        # Windows side-by-side/runtime loading needs SystemRoot even for offline
        # pip. Obtain it from the OS, never from caller-controlled environment.
        # https://docs.python.org/3/library/subprocess.html#subprocess.Popen
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x00000800)
        get_directory = kernel.GetWindowsDirectoryW
        get_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
        get_directory.restype = wintypes.UINT
        directory = ctypes.create_unicode_buffer(32768)
        count = get_directory(directory, len(directory))
        if count == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        if count >= len(directory):
            raise OSError('Native Windows directory exceeds the path budget')
        env['SystemRoot'] = directory.value
    return env


def _run(command, root, name, timeout=120):
    """Drain a bounded pipe while supervising the trusted install process group.

    Retain at most MAX_COMMAND_OUTPUT bytes, then fail and stop the owned group.
    The deadline covers process completion AND EOF, including an inherited pipe
    held by a child after the leader exits. This is not a hostile-code sandbox:
    it does not contain children that deliberately escape the process group or
    prevent writes through other descriptors opened by a trusted command.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Require a finite positive command deadline')
    log = _path(root/(name+'.log'))
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        process = subprocess.Popen(list(map(str, command)), cwd=root, env=_environment(root),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True, bufsize=0)
        try:
            output_fd = process.stdout.fileno()
            os.set_blocking(output_fd, False)
            deadline = time.monotonic() + timeout
            retained = 0
            eof = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('Provisioning command exceeded its deadline: ' + name)
                if not eof:
                    readable, _, _ = select.select([output_fd], [], [], min(remaining, 0.1))
                    if readable:
                        # One byte beyond the budget detects overflow without
                        # ever writing beyond the retained-log bound.
                        try:
                            chunk = os.read(output_fd, min(65536, MAX_COMMAND_OUTPUT-retained+1))
                        except BlockingIOError:
                            continue
                        if not chunk:
                            eof = True
                        else:
                            room = MAX_COMMAND_OUTPUT-retained
                            stream.write(chunk[:room])
                            retained += min(len(chunk), room)
                            if len(chunk) > room:
                                raise RuntimeError('Provisioning command output exceeded its limit: ' + name)
                else:
                    try:
                        process.wait(timeout=remaining)
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeError('Provisioning command exceeded its deadline: ' + name) from exc
                if eof and process.poll() is not None:
                    break
            code = process.returncode
            if code != 0:
                raise RuntimeError('Provisioning command failed: ' + name + '; retained exit=' + str(code))
        except BaseException:
            # Also clean up on read/write failure or caller cancellation. The
            # PID is from our own start_new_session child, never from input data.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        finally:
            process.stdout.close()
    return _read(log, MAX_COMMAND_OUTPUT, allow_empty=True)


def provision(directory, output, *, expected_manifest_ref, allow_install=False):
    """Create a new offline environment. Installation is never qualification."""
    if allow_install is not True:
        raise PermissionError('Explicit --allow-install is required before reading or provisioning')
    frozen, manifest, inspected = _freeze(directory, expected_manifest_ref)
    root = _path(output); source = _path(directory)
    if root == source or source in root.parents or root in source.parents:
        raise ValueError('Environment destination must be separate from the reviewed bundle')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    snapshot = root/'inputs'; snapshot.mkdir(mode=0o700)
    (snapshot/'wheels').mkdir(mode=0o700)
    for name, raw in frozen.items(): _write(snapshot/name, raw)
    python = root/'env/bin/python'
    _run([sys.executable, '-I', '-m', 'venv', '--copies', str(root/'env')], root, 'bootstrap')
    versions_code = ('import importlib.metadata as m,json;print(json.dumps('
                     '{d.metadata["Name"].lower().replace("_","-"):d.version for d in m.distributions()},sort_keys=True))')
    bootstrap = _loads(_run([python, '-I', '-c', versions_code], root, 'bootstrap-versions'))
    if set(bootstrap) & set(inspected):
        raise ValueError('Selected wheel would replace a bootstrap distribution')
    site = _run([python, '-I', '-c', 'import sysconfig;print(sysconfig.get_path("purelib"))'], root, 'site-before').decode().strip()
    site = _path(site)
    if not site.is_relative_to(root/'env'):
        raise ValueError('Installed package directory escapes the new environment')
    existing = {p.name.casefold() for p in site.iterdir()}
    existing_commands = {p.name.casefold() for p in (root/'env/bin').iterdir()}
    for metadata in inspected.values():
        if any(PurePosixPath(name).parts[0].casefold() in existing for name in metadata['file_hashes']):
            raise ValueError('Selected wheel overlaps the existing trusted bootstrap')
        if any(command.casefold() in existing_commands for command in metadata['commands']):
            raise ValueError('Selected entry point overlaps an existing bootstrap command')
    _run([python, '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check', '--no-input',
          '--no-cache-dir', 'install', '--no-index', '--only-binary=:all:', '--no-deps',
          '--require-hashes', '--no-compile', '--no-user', '--prefix', str(root/'env'),
          '--find-links', str(snapshot/'wheels'), '-r', str(snapshot/'requirements.txt')], root, 'offline-install')
    _run([python, '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'check'], root, 'dependency-check')
    versions = _loads(_run([python, '-I', '-c', versions_code], root, 'installed-versions'))
    expected_versions = {**bootstrap, **{n: m['version'] for n, m in inspected.items()}}
    if versions != expected_versions:
        raise ValueError('Installed distribution inventory differs from locked closure')
    site = _run([python, '-I', '-c', 'import sysconfig;print(sysconfig.get_path("purelib"))'], root, 'site').decode().strip()
    site = _path(site)
    if not site.is_relative_to(root/'env'):
        raise ValueError('Installed package directory escapes the new environment')
    checked = 0
    for metadata in inspected.values():
        for name, expected in metadata['file_hashes'].items():
            if name == metadata['record_path']:
                continue  # pip rewrites RECORD to include installer-generated files.
            _anchor(_read(site/name, MAX_EXPANDED, allow_empty=True), expected); checked += 1
    # Source files cannot be substituted after they were checked: pip consumed
    # our exact private copy. Check those bytes again before the success receipt.
    for name, raw in frozen.items():
        if _read(snapshot/name, max(1, len(raw))) != raw:
            raise ValueError('Frozen installation inputs changed')
    receipt = {**_summary(manifest, expected_manifest_ref, 'PROVISIONED_NOT_QUALIFIED'),
               'installed_versions': versions, 'bootstrap_versions': bootstrap,
               'installed_file_count': checked, 'environment': str(root/'env'),
               'interpreter_ref': digest(python.read_bytes()),
               'provisioner_source_ref': digest(Path(__file__).read_bytes()),
               'installer': 'trusted_host_python_venv_and_pip; not a hostile-code sandbox',
               'logs': {p.name:digest(p.read_bytes()) for p in sorted(root.glob('*.log'))}}
    _write(root/'provisioning.json', _json(receipt))
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--project', type=Path, required=True); p.add_argument('--project-ref', required=True)
    p.add_argument('--wheelhouse', type=Path, required=True); p.add_argument('--root-ref', required=True)
    p.add_argument('--profile', choices=PROFILES, default='link'); p.add_argument('--output', type=Path, required=True)
    for command in ('inspect', 'provision'):
        p = commands.add_parser(command); p.add_argument('directory', type=Path)
        p.add_argument('--manifest-ref', required=True)
        if command == 'provision':
            p.add_argument('--output', type=Path, required=True); p.add_argument('--allow-install', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare(args.project, args.wheelhouse, args.output, expected_project_ref=args.project_ref,
                             expected_root_ref=args.root_ref, profile=args.profile)
        elif args.command == 'inspect':
            result = inspect_bundle(args.directory, expected_manifest_ref=args.manifest_ref)
        else:
            result = provision(args.directory, args.output, expected_manifest_ref=args.manifest_ref, allow_install=args.allow_install)
        print(json.dumps(result, indent=2, allow_nan=False)); return 0
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, zipfile.BadZipFile,
            csv.Error, configparser.Error, OverflowError, RecursionError) as exc:
        print(json.dumps({'status':'REFUSED', 'reason':str(exc), 'physical_qualification':'NOT_ESTABLISHED'}), file=sys.stderr)
        return 2


if __name__ == '__main__': raise SystemExit(main())
