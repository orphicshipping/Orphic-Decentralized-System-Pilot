"""Seal an already provisioned dependency wheelhouse; never download packages."""
from __future__ import annotations
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import zipfile


def seal(context):
    dependencies = json.loads((context / 'dependencies.json').read_text())
    found = {}
    for wheel in sorted((context / 'wheelhouse').glob('*.whl')):
        if wheel.is_symlink():
            raise ValueError('wheelhouse symlinks are unsupported')
        with zipfile.ZipFile(wheel) as archive:
            entries = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
            if len(entries) != 1:
                raise ValueError('dependency wheel metadata is ambiguous')
            meta = BytesParser().parsebytes(archive.read(entries[0]))
        name = re.sub(r'[-_.]+', '-', meta['Name']).lower()
        if name not in dependencies or dependencies[name] != meta['Version'] or name in found:
            raise ValueError('dependency wheel does not match the exact selected inventory: ' + name)
        found[name] = {'filename': wheel.name, 'version': meta['Version'], 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(), 'size_bytes': wheel.stat().st_size}
    if set(found) != set(dependencies):
        raise ValueError('dependency wheelhouse is incomplete')
    with (context / 'requirements.hashed.lock').open('x') as stream:
        stream.writelines(f"{name}=={row['version']} --hash=sha256:{row['sha256']}\n" for name, row in sorted(found.items()))
    report = {'schema': 'odsp.private-wheelhouse.v1', 'identity': json.loads((context / 'identity.json').read_text()), 'dependencies': found}
    with (context / 'wheelhouse-receipt.json').open('x') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True, type=Path)
    args = parser.parse_args()
    try:
        report = seal(args.context)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(json.dumps({'status': 'refused', 'reason': str(error)}))
        return 1
    print(json.dumps({'status': 'passed', 'dependency_wheels': len(report['dependencies'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
