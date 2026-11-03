#!/usr/bin/env python3
"""Write a deterministic CycloneDX inventory from a host-pinned release and lock.

No downloads, imports from dependency archives, package installs or source changes.
Archive integrity is not installed dependency byte attestation or authentication.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from email.parser import BytesParser
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from urllib.parse import quote
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ciw import private_release as release
from ciw.campaign_contracts import Limits, canonical, closed_object, parse

MAX_ARCHIVE = 128 * 1024 * 1024
MAX_ARCHIVE_TOTAL = 512 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_ARCHIVES = 256
# The retained pydantic 2.14.2 METADATA is 137,593 bytes (includes its long
# description); this cap admits the existing lock while bounding metadata reads.
MAX_METADATA = 262144
DOCUMENT_LIMITS = Limits(max_bytes=4 * 1024 * 1024, max_nodes=1048576,
                        max_object=16384, max_array=65536)
SBOM_LIMITS = Limits(max_bytes=8 * 1024 * 1024, max_nodes=1048576,
                    max_object=16384, max_array=65536)
_LOCK = re.compile(r'([a-z0-9]+(?:-[a-z0-9]+)*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*) --hash=sha256:([0-9a-f]{64})\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_GIT = re.compile(r'[0-9a-f]{40}\Z')


def require(condition, code):
    if not condition:
        raise ValueError(code)


def safe_path(path):
    path = Path(path)
    require(path.is_absolute(), 'sbom_path_must_be_absolute')
    require(not any(part.is_symlink() for part in (path, *path.parents)), 'sbom_symlink_refused')
    return path


@contextmanager
def regular(path, limit):
    path = safe_path(path)
    nofollow = getattr(os, 'O_NOFOLLOW', None)
    require(nofollow is not None, 'sbom_nofollow_unsupported')
    fd = os.open(path, os.O_RDONLY | nofollow | getattr(os, 'O_NONBLOCK', 0))
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= limit,
                'sbom_file_budget_or_type_refused')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            yield stream, info.st_size
    finally:
        os.close(fd)


def read(path, limit):
    with regular(path, limit) as (stream, _):
        raw = stream.read(limit + 1)
    require(0 < len(raw) <= limit, 'sbom_file_budget_or_type_refused')
    return raw


def locked(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 65536, 'sbom_lock_budget')
    lines = raw.decode('ascii').splitlines()
    require(0 < len(lines) <= MAX_ARCHIVES, 'sbom_lock_count')
    records = {}
    for line in lines:
        match = _LOCK.fullmatch(line)
        require(match is not None, 'sbom_lock_requires_exact_version_and_single_hash')
        name, version, checksum = match.groups()
        require(name not in records, 'sbom_duplicate_lock_package')
        records[name] = (version, checksum)
    return records


def normalized(value):
    return re.sub(r'[-_.]+', '-', value).lower()


def archive_record(path, records):
    require(path.name.endswith('.whl'), 'sbom_only_wheel_archives_supported')
    parts = path.name[:-4].split('-')
    require(len(parts) in (5, 6), 'sbom_invalid_wheel_filename')
    name, version = normalized(parts[0]), parts[1]
    require(name in records and records[name][0] == version, 'sbom_unlocked_archive')
    with regular(path, MAX_ARCHIVE) as (stream, size):
        checksum, total = sha256(), 0
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            require(total <= MAX_ARCHIVE, 'sbom_archive_budget')
            checksum.update(chunk)
        require(total == size and checksum.hexdigest() == records[name][1], 'sbom_archive_hash_mismatch')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            require(0 < len(entries) <= 20000, 'sbom_archive_entry_budget')
            seen, expanded, metadata = set(), 0, []
            for info in entries:
                path_name = info.orig_filename.rstrip('/')
                relative = PurePosixPath(path_name)
                require(path_name and not relative.is_absolute() and '\\' not in path_name
                        and all(part not in ('', '.', '..') for part in path_name.split('/'))
                        and not any(ord(c) < 32 for c in path_name), 'sbom_archive_path_refused')
                require(path_name.casefold() not in seen and not stat.S_ISLNK(info.external_attr >> 16),
                        'sbom_archive_duplicate_or_symlink')
                seen.add(path_name.casefold())
                expanded += info.file_size
                require(0 <= info.file_size <= MAX_EXPANDED and expanded <= MAX_EXPANDED,
                        'sbom_archive_expanded_budget')
                if len(relative.parts) == 2 and relative.parts[0].endswith('.dist-info') and relative.name == 'METADATA':
                    require(info.file_size <= MAX_METADATA, 'sbom_metadata_budget')
                    metadata.append(archive.read(info))
            require(len(metadata) == 1, 'sbom_metadata_missing_or_ambiguous')
            message = BytesParser().parsebytes(metadata[0], headersonly=True)
            require(message.get_all('Name') is not None and len(message.get_all('Name')) == 1
                    and message.get_all('Version') is not None and len(message.get_all('Version')) == 1,
                    'sbom_metadata_identity_missing_or_ambiguous')
            require(normalized(message['Name']) == name and message['Version'] == version,
                    'sbom_metadata_identity_mismatch')
    return {'name':name, 'version':version, 'sha256':checksum.hexdigest(),
            'filename':path.name, 'size_bytes':size}


def check_pins(value, expected_digest):
    closed_object(value, {'schema','imports','manifest_sha256','runtime_pin_files','godot_prop'})
    require(value['schema'] == 'odsp.private-preserved-sources.v1'
            and release.digest(value) == expected_digest, 'sbom_source_pins_binding_mismatch')
    imports = value['imports']
    require(type(imports) is list and len(imports) == 21, 'sbom_requires_all_21_source_trees')
    roles, paths = set(), set()
    for row in imports:
        closed_object(row, {'role','path','import_revision','import_tree'}, {'runtime_revision'})
        require(type(row['role']) is str and row['role'] and row['role'] not in roles
                and type(row['path']) is str and row['path'] not in paths, 'sbom_source_identity_ambiguous')
        release._path(row['path'])
        roles.add(row['role']); paths.add(row['path'])
        for field in ('import_revision','import_tree','runtime_revision'):
            if field in row:
                if field == 'runtime_revision' and row[field] is None:
                    continue  # Existing provenance: this import selects no runtime.
                require(type(row[field]) is str and _GIT.fullmatch(row[field]), 'sbom_invalid_source_git_id')
    require(type(value['manifest_sha256']) is str and _SHA.fullmatch(value['manifest_sha256']),
            'sbom_invalid_manifest_hash')
    require(type(value['runtime_pin_files']) is dict and value['runtime_pin_files'], 'sbom_runtime_pins_missing')
    for name, checksum in value['runtime_pin_files'].items():
        release._path(name)
        require(type(checksum) is str and _SHA.fullmatch(checksum), 'sbom_invalid_runtime_pin_hash')
    closed_object(value['godot_prop'], {'path','tree'})
    require(value['godot_prop']['path'] == 'examples/workcells/godot-prop'
            and type(value['godot_prop']['tree']) is str and _GIT.fullmatch(value['godot_prop']['tree']),
            'sbom_exact_capsule_binding_missing')


def properties(values):
    return [{'name':'odsp:' + name, 'value':str(value)} for name, value in sorted(values.items())]


def hashed_component(kind, reference, name, checksum, **values):
    return {'type':kind, 'bom-ref':reference, 'name':name,
            'hashes':[{'alg':'SHA-256','content':checksum}], **values}


def build_sbom(*, manifest_path, identity_path, lock_path, expected_lock_sha256,
               wheelhouse, pins_path):
    """Verify selected local artifact bytes; no installed-dependency or auth claim."""
    manifest_path, wheelhouse = safe_path(manifest_path), safe_path(wheelhouse)
    manifest_raw = read(manifest_path, DOCUMENT_LIMITS.max_bytes)
    parse(manifest_raw, limits=DOCUMENT_LIMITS)
    manifest = release.verify_manifest(manifest_path)
    identity = release.validate_identity(parse(read(identity_path, 65536)))
    require(release.canonical(identity) == release.canonical(manifest['identity']), 'sbom_expected_release_mismatch')
    lock_raw = read(lock_path, 65536)
    require(type(expected_lock_sha256) is str and _SHA.fullmatch(expected_lock_sha256)
            and sha256(lock_raw).hexdigest() == expected_lock_sha256, 'sbom_expected_lock_mismatch')
    records = locked(lock_raw)
    require({name:row[0] for name,row in records.items()} == manifest['dependencies'], 'sbom_lock_dependency_closure_mismatch')
    require(wheelhouse.is_dir(), 'sbom_wheelhouse_missing')
    archives, total = {}, 0
    for index, path in enumerate(sorted(wheelhouse.iterdir())):
        require(index < MAX_ARCHIVES, 'sbom_archive_count')
        record = archive_record(path, records)
        total += record['size_bytes']
        require(total <= MAX_ARCHIVE_TOTAL and record['name'] not in archives, 'sbom_archive_total_or_duplicate')
        archives[record['name']] = record
    require(set(archives) == set(records), 'sbom_locked_archives_missing')
    pins_raw = read(pins_path, 262144)
    pins = parse(pins_raw, limits=Limits(max_bytes=262144))
    check_pins(pins, manifest['pins_digest'])
    # Verify service bytes too; version labels or an unexamined manifest are not
    # substituted for archive, wheel and service-file closure.
    service_root = manifest_path.parent.parent
    for directory in {str(PurePosixPath(name).parent) for name in manifest['service_files']}:
        safe_path(service_root / directory)
        expected = {name[len(directory)+1:]:checksum for name,checksum in manifest['service_files'].items()
                    if str(PurePosixPath(name).parent) == directory}
        release.installed_files(expected, service_root / directory)
    require(read(manifest_path, DOCUMENT_LIMITS.max_bytes) == manifest_raw, 'sbom_manifest_changed_during_verification')
    main_ref = 'urn:sha256:' + identity['wheel_sha256']
    metadata = {'component':hashed_component('application', main_ref, manifest['distribution']['name'],
        identity['wheel_sha256'], version=manifest['distribution']['version']),
        'properties':properties({
            'repository':identity['repository'], 'source-commit':identity['source_commit'],
            'source-tree':identity['source_tree'], 'package-digest':identity['package_digest'],
            'manifest-sha256':sha256(manifest_raw).hexdigest(), 'lock-sha256':expected_lock_sha256,
            'pins-file-sha256':sha256(pins_raw).hexdigest(), 'pins-canonical-sha256':manifest['pins_digest'],
            'preserved-source-inventory':release.canonical(pins).decode('ascii'),
            'verification-scope':'selected dependency archive bytes, owned wheel packages and service files',
            'installed-dependency-bytes':'not_verified', 'source-authentication':'host_selected_unverified',
            'dependency-graph':'selected locked runtime closure; transitive edges not independently resolved',
            'authorizes-execution':'false'})}
    components, graph, direct = [], [], []
    for name, record in sorted(archives.items()):
        reference = 'pkg:pypi/' + name + '@' + quote(record['version'], safe='')
        components.append(hashed_component('library', reference, name, record['sha256'],
            version=record['version'], purl=reference,
            properties=properties({'archive':record['filename'],'archive-size-bytes':record['size_bytes'],
                                   'hash-scope':'complete retained wheel archive'})))
        direct.append(reference)
    for root in manifest['package_roots']:
        reference = 'odsp:owned-package:' + root
        members = [hashed_component('file', 'odsp:owned-file:' + name, name, checksum)
                   for name,checksum in sorted(manifest['package_files'].items()) if name.startswith(root + '/')]
        components.append({'type':'library','bom-ref':reference,'name':root,
                           'properties':properties({'ownership':'release wheel package root'}),
                           'components':members})
        graph.append({'ref':reference,'dependsOn':[entry['bom-ref'] for entry in members]})
        direct.append(reference)
    for name, checksum in sorted(manifest['service_files'].items()):
        reference = 'odsp:service-file:' + name
        components.append(hashed_component('file', reference, name, checksum))
        direct.append(reference)
    graph.insert(0, {'ref':main_ref,'dependsOn':direct})
    document = {'bomFormat':'CycloneDX','specVersion':'1.6','version':1,
                'metadata':metadata,'components':components,'dependencies':graph}
    canonical(document, limits=SBOM_LIMITS)
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('manifest','identity','lock','expected-lock-sha256','wheelhouse','pins','output'):
        parser.add_argument('--' + flag, required=True)
    args = parser.parse_args(argv)
    output = safe_path(args.output)
    require(not output.exists(), 'sbom_output_already_exists')
    document = build_sbom(manifest_path=args.manifest, identity_path=args.identity,
        lock_path=args.lock, expected_lock_sha256=args.expected_lock_sha256,
        wheelhouse=args.wheelhouse, pins_path=args.pins)
    raw = canonical(document, limits=SBOM_LIMITS) + b'\n'
    # Exclusive creation preserves existing artifacts and does not follow links.
    with output.open('xb') as stream:
        stream.write(raw)
    source_commit = next(item['value'] for item in document['metadata']['properties']
                         if item['name'] == 'odsp:source-commit')
    print(json.dumps({'schema':'odsp.locked-sbom-build.v1','sha256':sha256(raw).hexdigest(),
        'size_bytes':len(raw),'source_commit':source_commit,
        'qualification':'local_artifact_integrity_only','authorizes_execution':False}, sort_keys=True))


if __name__ == '__main__':
    main()
