#!/usr/bin/env python3
"""Exercise the existing boundary and new network lifecycle, source or installed.

Offline development may explicitly inherit the invoking interpreter's installed
dependencies. This is not pinned-environment or production security qualification.
CI omits that switch and requires the repository pins in an isolated environment.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import venv
import xml.etree.ElementTree as ET


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--wheel',type=Path)
    p.add_argument('--inherit-dependencies',action='store_true');p.add_argument('--require-pins',action='store_true')
    a=p.parse_args(argv);repo=Path(__file__).resolve().parents[1];out=a.output.absolute()
    if out.resolve().is_relative_to(repo):p.error('output must be outside the source checkout')
    if a.inherit_dependencies and a.wheel is None:p.error('inheritance is for installed offline mode only')
    out.mkdir(parents=True,exist_ok=False)
    result={'schema':'ciw.network-qualification.v1','status':'FAIL','started_at':datetime.now(timezone.utc).isoformat(),
            'mode':'installed' if a.wheel else 'source','dependency_inheritance':a.inherit_dependencies,'commands':[],
            'SP1':'NOT_RUN','actual_login_ceremony':False,'physical_acquisition':False,'transmitted':False,'installed_release':False}
    env=dict(os.environ);env.pop('PYTHONPATH',None);env['NET_BOUNDARY_REQUIRE_CXX']='1'
    env['NET_BOUNDARY_CPP_SOURCE']=str(repo/'runtimes/device-boundary/affine.cpp')
    def run(cmd,label,expected=0):
        start=datetime.now(timezone.utc).isoformat()
        r=subprocess.run(cmd,cwd=out,env=env,capture_output=True,timeout=300)
        log=r.stdout+b'\n--- STDERR ---\n'+r.stderr;(out/(label+'.log')).write_bytes(log)
        result['commands'].append({'name':label,'started_at':start,'finished_at':datetime.now(timezone.utc).isoformat(),
            'returncode':r.returncode,'expected_returncode':expected,'log_sha256':sha256(log).hexdigest()})
        if r.returncode!=expected:raise RuntimeError(label+' returned '+str(r.returncode))
        return r.stdout
    try:
        py=sys.executable;opts=[]
        if a.wheel:
            wheel=a.wheel.resolve(strict=True);result['wheel_sha256']=sha256(wheel.read_bytes()).hexdigest()
            venv.EnvBuilder(with_pip=True,system_site_packages=a.inherit_dependencies).create(out/'environment')
            py=str(out/'environment'/('Scripts/python.exe' if os.name=='nt' else 'bin/python'));opts=['-I']
            if a.inherit_dependencies:
                lib=subprocess.check_output([py,'-I','-c','import sysconfig;print(sysconfig.get_paths()["purelib"])'],text=True).strip()
                (Path(lib)/'inherited-dependencies.pth').write_text(sysconfig.get_paths()['purelib']+'\n')
            cmd=[py,'-I','-m','pip','install']
            cmd+=['--no-deps',str(wheel)] if a.inherit_dependencies else [str(wheel)+'[dev,legibility,network]']
            run(cmd,'install')
            run([py,'-I','-c','from pathlib import Path;import ciw,sys;assert Path(ciw.__file__).resolve().is_relative_to(Path(sys.prefix).resolve());print(ciw.__file__)'],'installed-import')
        else:env['PYTHONPATH']=str(repo/'src')
        versions=json.loads(run([py,*opts,'-c',"import importlib.metadata as m,json;print(json.dumps({n:m.version(n) for n in ('numpy','cryptography','PyJWT','websockets','pytest')}))"],'versions'))
        result['versions']=versions
        result['repository_pins_match']=versions=={'numpy':'2.4.3','cryptography':'46.0.0','PyJWT':'2.13.0','websockets':'16.0','pytest':'9.0.2'}
        if a.require_pins and not result['repository_pins_match']:raise ValueError('repository dependency pin mismatch')
        tests=['test_device_boundary.py','test_network_lifecycle.py']
        for name in tests:shutil.copy2(repo/'tests'/name,out/name)
        (out/'pytest.ini').write_text('[pytest]\npythonpath =\naddopts = -ra\n')
        run([py,*opts,'-m','pytest','-q','-c',str(out/'pytest.ini'),*[str(out/t) for t in tests],
             '--junitxml='+str(out/'tests.xml')],'tests')
        suite=ET.parse(out/'tests.xml').getroot().find('testsuite')
        result['tests']={name:int(suite.attrib[name]) for name in ('tests','failures','errors','skipped')}
        if result['tests']['skipped'] or result['tests']['failures'] or result['tests']['errors']:raise ValueError('qualification requires no skipped or failed tests')
        for command in ('boundary','network'):
            data=json.loads(run([py,*opts,'-m','ciw.net',command,'demo','--output',str(out/command)],command+'-demo'))
            anchor=data['workspace_sha256']
            run([py,*opts,'-m','ciw.net',command,'inspect','--workspace',str(out/command/'workspace.json'),
                 '--workspace-sha256',anchor],command+'-inspect')
        run([py,*opts,'-m','ciw.net','boundary','proof-gate','--output',str(out/'proof-gate')],'proof-not-run',2)
        run([py,*opts,'-m','ciw.net','catalog','--audit','--json'],'catalog-audit')
        result['status']='PASS'
    except Exception as exc:result['reason']=type(exc).__name__+': '+str(exc)
    result['finished_at']=datetime.now(timezone.utc).isoformat()
    (out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
