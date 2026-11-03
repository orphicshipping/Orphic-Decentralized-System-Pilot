"""Reproduce hash-locked local protocol test inputs; starts no service.

Use an existing CPython 3.12 environment with pip>=22.3. No system package,
credentials, global toolchain, network permission or hosted resource is created.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import argparse
from hashlib import file_digest,sha256
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import urllib.request


def digest(path):
    with path.open('rb') as stream:return file_digest(stream,'sha256').hexdigest()


def fetch(directory,item,cache):
    name=item.get('filename',item['url'].rsplit('/',1)[-1])
    if Path(name).name!=name or not item['url'].startswith(
            ('https://files.pythonhosted.org/','https://deb.debian.org/debian/')):
        raise ValueError('unapproved runtime input')
    target=directory/name
    local=cache/name if cache else None
    if local and local.is_file() and not local.is_symlink():
        if local.stat().st_size==item['bytes'] and digest(local)==item['sha256']:
            shutil.copyfile(local,target)
    if not target.exists():
        with urllib.request.urlopen(item['url'],timeout=30) as response:
            raw=response.read(64*1024**2+1)
        if len(raw)!=item['bytes'] or len(raw)>64*1024**2 or sha256(raw).hexdigest()!=item['sha256']:
            raise ValueError('download identity mismatch')
        with target.open('xb') as stream:stream.write(raw)
    if target.stat().st_size!=item['bytes'] or digest(target)!=item['sha256']:
        raise ValueError('retained input identity mismatch')
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--wheelhouse',type=Path)
    args=parser.parse_args()
    destination=args.destination.absolute()
    if (destination.resolve()!=destination or not destination.is_relative_to(Path('/tmp'))
            or destination==Path('/tmp')):
        raise ValueError('select a new private /tmp directory')
    if platform.system()!='Linux' or platform.machine()!='x86_64' or sys.version_info[:2]!=(3,12):
        raise ValueError('lock requires Linux x86_64 CPython 3.12')
    if shutil.disk_usage('/tmp').free<1536*1024**2:
        raise RuntimeError('at least 1.5 GiB scratch required, preserving 1 GiB reserve')
    subprocess.run([sys.executable,'-m','pip','--version'],check=True)
    destination.mkdir(mode=0o700,exist_ok=False)
    downloads=destination/'downloads';downloads.mkdir()
    native=destination/'root';native.mkdir()
    here=Path(__file__).resolve().parent
    lock=json.loads((here/'runtime-lock.json').read_text())
    for item in lock['debian_packages']:
        archive=fetch(downloads,item,None)
        subprocess.run(['dpkg-deb','--extract',str(archive),str(native)],check=True)
    for item in lock['python_wheels']:fetch(downloads,item,args.wheelhouse)
    venv=destination/'venv'
    subprocess.run([sys.executable,'-m','venv','--without-pip',str(venv)],check=True)
    python=venv/'bin/python'
    command=[sys.executable,'-m','pip','--python',str(python)]
    subprocess.run([*command,'install','--isolated','--no-index','--no-cache-dir','--require-hashes',
                    '--find-links',str(downloads),'-r',str(here/'requirements.lock')],check=True)
    subprocess.run([*command,'check'],check=True)
    receipt={'schema':'odsp.live-runtime-bootstrap.v1','python':sys.version,
             'python_executable_sha256':digest(python.resolve()),
             'runtime_lock_sha256':digest(here/'runtime-lock.json'),
             'requirements_sha256':digest(here/'requirements.lock'),
             'python_executable':str(python),'mosquitto':str(native/'usr/sbin/mosquitto'),
             'library_directory':str(native/'usr/lib/x86_64-linux-gnu'),
             'scope':'local qualification inputs only; no connection or service started'}
    (destination/'bootstrap.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':main()
