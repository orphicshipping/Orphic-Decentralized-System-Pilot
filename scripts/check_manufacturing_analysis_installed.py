"""Check a noneditable NET wheel and full retained manufacturing test closure."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

TESTS = ('test_sensor_project.py', 'test_sensor_manufacturing.py', 'test_sensor_manufacturing_analysis.py', 'test_sensor_manufacturing_timing.py', 'test_sensor_manufacturing_uncertainty.py')
PINS = {'numpy': '2.4.3', 'websockets': '16.0', 'pytest': '9.0.2'}


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--wheel',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--allow-host-dependencies',action='store_true')
    args=p.parse_args();source=Path(__file__).resolve().parents[1]
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    report={'started_at':datetime.now(timezone.utc).isoformat(),'status':'RUNNING',
            'python':sys.version,'physical_hardware_tested':False}
    code=2
    try:
        if sys.prefix==sys.base_prefix:raise ValueError('Require a separately provisioned virtual environment')
        versions={name:metadata.version(name) for name in PINS}
        mismatch={k:{'required':v,'actual':versions[k]} for k,v in PINS.items() if versions[k]!=v}
        report.update(versions=versions,dependency_mismatches=mismatch,exact_pins=not bool(mismatch))
        if mismatch and not args.allow_host_dependencies:raise ValueError('Unchanged dependency pins not satisfied')
        import ciw
        package=Path(ciw.__file__).resolve().parent
        if not package.is_relative_to(Path(sys.prefix).resolve()) or package.is_relative_to(source):
            raise ValueError('NET must import from its noneditable installation, not source')
        with zipfile.ZipFile(args.wheel) as archive:
            expected={name:hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
                      if name.startswith('ciw/') and not name.endswith('/')}
        actual={f'ciw/{f.relative_to(package).as_posix()}' for f in package.rglob('*')
                if f.is_file() and '__pycache__' not in f.parts}
        source_package=source/'src/ciw'
        original={f'ciw/{f.relative_to(source_package).as_posix()}' for f in source_package.rglob('*')
                  if f.is_file() and '__pycache__' not in f.parts}
        if actual!=set(expected) or original!=set(expected):raise ValueError('Package inventories differ')
        for name,digest in expected.items():
            for path in (package.parent/name,source/'src'/name):
                if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Package byte mismatch: '+name)
        report.update(package_files=len(expected),package_path=str(package),
                      wheel_sha256=hashlib.sha256(args.wheel.read_bytes()).hexdigest())
        for name in TESTS:shutil.copyfile(source/'tests'/name,out/name)
        (out/'pytest.ini').write_text('[pytest]\n')
        env={k:v for k,v in os.environ.items() if k not in {'PYTHONPATH','PYTHONHOME','PYTEST_ADDOPTS','PYTEST_PLUGINS'}}
        env.update(PYTHONNOUSERSITE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
        command=[sys.executable,'-I','-m','pytest','-q','-c','pytest.ini','-o','pythonpath=',*TESTS,'--junitxml=installed.xml']
        child=subprocess.run(command,cwd=out,env=env,capture_output=True,text=True,timeout=300)
        (out/'tests.log').write_text(child.stdout+child.stderr)
        if child.returncode:raise ValueError('Installed suite failed; see tests.log')
        cases=ET.parse(out/'installed.xml').findall('.//testcase')
        if not cases or any(any(case.find(t) is not None for t in ('failure','error','skipped')) for case in cases):
            raise ValueError('Require actual passing cases without failures, errors or skips')
        report.update(passed=len(cases),status='PASS',scope='selected_manufacturing_and_sensor_tests_not_full_monorepo')
        code=0
    except (ValueError,OSError,RuntimeError,metadata.PackageNotFoundError) as exc:
        report.update(status='BLOCKED',reason=str(exc))
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));return code


if __name__=='__main__':raise SystemExit(main())
