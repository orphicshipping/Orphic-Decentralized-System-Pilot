"""Fixed KiCad 10 CLI checks/exports, supervised by NET's existing process helper.

Operator-selected executable only. No jobsets, edits, fabrication submission, IPC
or SWIG. This is not a sandbox or an attestation of shared-library dependency closure.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
from copy import deepcopy

from . import sensor_project as project
from .control_contracts import bytes_ref, content_ref, keys, text
from .interactive_simulation import file_sha, run_process
from .operations.registry import Operation
from .operations.schemas import register_payload_validator

OPERATION = "electronics.kicad-package.v1"
MAX_EXPORT = 2 * 1024 * 1024
MAX_CAPTURE = 4 * 1024 * 1024


def command_plan(run):
    spec, inv, files = project.context(run)
    def one(kind):
        matches = [a['path'] for a in spec['artifacts'] if a['kind'] == kind]
        if len(matches) != 1: raise ValueError('This KiCad profile requires exactly one ' + kind)
        return matches[0]
    pro, sch, pcb = one('electronics.project'), one('electronics.schematic'), one('electronics.pcb')
    if not (Path(pro).with_suffix('') == Path(sch).with_suffix('') == Path(pcb).with_suffix('')):
        raise ValueError('Project/schematic/PCB must share the same retained stem')
    return [
        {'stage':'erc','args':['sch','erc','--format','json','--severity-all','--exit-code-violations','--output','erc.json','{input}/'+sch], 'outputs':['erc.json'],'allowed_codes':[0,5]},
        {'stage':'drc','args':['pcb','drc','--format','json','--severity-all','--schematic-parity','--exit-code-violations','--output','drc.json','{input}/'+pcb], 'outputs':['drc.json'],'allowed_codes':[0,5]},
        {'stage':'bom','args':['sch','export','bom','--output','bom.csv','{input}/'+sch], 'outputs':['bom.csv'],'allowed_codes':[0]},
        {'stage':'position','args':['pcb','export','pos','--format','csv','--units','mm','--output','positions.csv','{input}/'+pcb], 'outputs':['positions.csv'],'allowed_codes':[0]},
        {'stage':'gerber','args':['pcb','export','gerbers','--output','gerbers/','{input}/'+pcb], 'outputs':['gerbers/'],'allowed_codes':[0]},
        {'stage':'drill','args':['pcb','export','drill','--format','excellon','--output','drill/','{input}/'+pcb], 'outputs':['drill/'],'allowed_codes':[0]},
        {'stage':'step','args':['pcb','export','step','--output','board.step','{input}/'+pcb], 'outputs':['board.step'],'allowed_codes':[0]},
        {'stage':'glb','args':['pcb','export','glb','--output','board.glb','{input}/'+pcb], 'outputs':['board.glb'],'allowed_codes':[0]},
    ]


class Binding:
    def __init__(self, executable, *, expected_sha256, expected_version):
        content_ref(expected_sha256)
        if type(expected_version) is not str or not re.fullmatch(r'10\.0\.\d+', expected_version):
            raise ValueError('Require an explicit stable KiCad 10.0 patch version, not nightly')
        path = Path(executable).expanduser()
        if not path.is_absolute() or not path.is_file(): raise ValueError('An installed absolute kicad-cli path is required')
        self.path = path.resolve(strict=True)
        self.sha, self.version = expected_sha256, expected_version
        self.runtime_identity()

    def runtime_identity(self):
        if file_sha(self.path) != self.sha: raise ValueError('KiCad executable drift or incorrect hash')
        return {'provider':'ciw.kicad-cli', 'version':self.version, 'executable_sha256':self.sha,
                'adapter_sha256':bytes_ref(Path(__file__).read_bytes().replace(b'\r\n', b'\n')),
                'supervisor_sha256':bytes_ref(Path(__file__).with_name('interactive_simulation.py').read_bytes().replace(b'\r\n', b'\n')),
                'dependency_closure':'not_attested', 'scope':'trusted CLI checks/exports only'}

    def invoke(self, run, parameters):
        if not project.same(parameters, {}): raise ValueError('KiCad profile has no data-selected commands or parameters')
        plan = command_plan(run); spec, inventory, raws = project.context(run)
        self.runtime_identity()
        with tempfile.TemporaryDirectory(prefix='net-kicad-') as temporary:
            root = Path(temporary); inputs=root/'inputs'; inputs.mkdir()
            for name, raw in raws.items():
                dest=inputs/name; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(raw)
                dest.chmod(0o755 if inventory['files'][name]['executable'] else 0o644)
            stages=[]; captured={}; total=0
            def capture_file(key, path):
                nonlocal total
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_EXPORT: raise ValueError('Invalid KiCad output')
                raw=path.read_bytes(); total+=len(raw)
                if total > MAX_CAPTURE: raise ValueError('KiCad capture budget exceeded')
                captured[key]={'sha256':bytes_ref(raw), 'bytes':len(raw), 'base64':project._pack(raw)}
            probe=root/'version';probe.mkdir()
            run_process([str(self.path),'version'], probe, timeout=10)
            label=(probe/'stdout.log').read_text().strip()
            if label != self.version: raise ValueError('KiCad reported version differs from operator binding')
            capture_file('version/stdout.log', probe/'stdout.log');capture_file('version/stderr.log', probe/'stderr.log')
            for entry in plan:
                self.runtime_identity()
                stage=root/entry['stage'];stage.mkdir()
                args=[str(inputs)+a[len('{input}'):] if a.startswith('{input}') else a for a in entry['args']]
                for name in entry['outputs']:
                    if name.endswith('/'): (stage/name).mkdir()
                try:
                    result=run_process([str(self.path), *args], stage, [stage/n for n in entry['outputs']], timeout=120)
                    code=result['returncode']
                except RuntimeError as exc:
                    result=json.loads(str(exc));code=result.get('returncode')
                    if result.get('failure') != 'runtime exit 5' or 5 not in entry['allowed_codes']: raise
                if code not in entry['allowed_codes']: raise ValueError('Unexpected KiCad return code')
                for name in entry['outputs']:
                    path=stage/name
                    if name.endswith('/'):
                        candidates=sorted(p for p in path.rglob('*') if p.is_file())
                        if not 1 <= len(candidates) <= 128: raise ValueError('Missing/oversized manufacturing output set')
                        for p in candidates: capture_file(entry['stage']+'/'+p.relative_to(stage).as_posix(),p)
                    else:
                        if not path.exists() or path.stat().st_size == 0: raise ValueError('Missing native export/report')
                        capture_file(entry['stage']+'/'+name,path)
                        if entry['stage'] in ('erc','drc'):
                            report=project.loads_json(path.read_text())
                            if type(report) is not dict: raise ValueError('KiCad JSON report must be an object')
                capture_file(entry['stage']+'/stdout.log',stage/'stdout.log');capture_file(entry['stage']+'/stderr.log',stage/'stderr.log')
                stages.append({'stage':entry['stage'],'arguments':entry['args'], 'returncode':code,
                               'status':'VIOLATIONS_REPORTED' if code == 5 else 'COMPLETED', 'diagnostic':result})
                if project.packets.inventory(inputs) != inventory: raise ValueError('KiCad modified retained input copies or added untracked inputs')
                self.runtime_identity()
            return {'schema':'ciw.kicad-package-result.v1','inventory_id':inventory['inventory_id'],
                    'runtime':self.runtime_identity(),'stages':stages,'artifacts':captured,
                    'manufacturing_approval':False, 'physical_validation':'not_established',
                    'exclusions':'reported with severity-all; original project/rules retained',
                    'library_closure':'operator must review all external symbol/footprint/3D references',
                    'status':'CANDIDATE_OUTPUTS', 'source_files_unchanged':True}

    def operation(self):
        return Operation(OPERATION, 'backend', self.invoke, self.runtime_identity)


def validate_payload(operation, data, run, parameters, selection):
    if (operation != OPERATION or not project.same(parameters, {}) or not project.same({key: selection[key] for key in ('channel','interval_s')}, {'channel':project.CHANNEL,'interval_s':[0.0,1.0]})):
        raise ValueError('Invalid KiCad operation selection')
    keys(data, {'schema','inventory_id','runtime','stages','artifacts','manufacturing_approval','physical_validation','exclusions','library_closure','status','source_files_unchanged'})
    _,inv,_=project.context(run)
    if data['schema']!='ciw.kicad-package-result.v1' or data['inventory_id'] != inv['inventory_id'] or data['manufacturing_approval'] is not False or data['source_files_unchanged'] is not True or data['physical_validation']!='not_established' or data['status']!='CANDIDATE_OUTPUTS':
        raise ValueError('KiCad result source/authority mismatch')
    plan=command_plan(run)
    if type(data['stages']) is not list or len(data['stages'])!=len(plan): raise ValueError('Incomplete KiCad stages')
    if type(data['artifacts']) is not dict or not 18 <= len(data['artifacts']) <= 300: raise ValueError('Incomplete native artifact inventory')
    keys(data['runtime'], {'provider','version','executable_sha256','adapter_sha256','supervisor_sha256','dependency_closure','scope'})
    if data['runtime']['provider']!='ciw.kicad-cli' or not re.fullmatch(r'10\.0\.\d+',data['runtime']['version']): raise ValueError('Unsupported KiCad runtime')
    for k in ('executable_sha256','adapter_sha256','supervisor_sha256'): content_ref(data['runtime'][k])
    if data['runtime']['dependency_closure'] != 'not_attested' or data['runtime']['scope'] != 'trusted CLI checks/exports only':
        raise ValueError('Runtime authority/closure claim changed')
    if data['exclusions'] != 'reported with severity-all; original project/rules retained' or data['library_closure'] != 'operator must review all external symbol/footprint/3D references':
        raise ValueError('Undeclared native qualification claim')
    total=0
    for key,item in data['artifacts'].items():
        project.packets.relative(key); keys(item,{'sha256','bytes','base64'})
        raw=project._unpack(item['base64']); total+=len(raw)
        if type(item['bytes']) is not int or len(raw)!=item['bytes'] or len(raw)>MAX_EXPORT or bytes_ref(raw)!=item['sha256']: raise ValueError('Native artifact altered')
    if total>MAX_CAPTURE: raise ValueError('Native artifact budget')
    version=project._unpack(data['artifacts']['version/stdout.log']['base64']).decode().strip()
    if version!=data['runtime']['version']: raise ValueError('Version transcript mismatch')
    for expected,actual in zip(plan,data['stages']):
        keys(actual,{'stage','arguments','returncode','status','diagnostic'})
        code=actual['returncode']
        if actual['stage']!=expected['stage'] or actual['arguments']!=expected['args'] or type(code) is not int or code not in expected['allowed_codes'] or actual['status']!=('VIOLATIONS_REPORTED' if code==5 else 'COMPLETED'):
            raise ValueError('KiCad stage/command/outcome mismatch')
        for path in expected['outputs']:
            key=actual['stage']+'/'+path
            selected=[k for k in data['artifacts'] if k.startswith(key)] if path.endswith('/') else ([key] if key in data['artifacts'] else [])
            if not selected or any(not project._unpack(data['artifacts'][name]['base64']) for name in selected): raise ValueError('Required native output missing')
            if actual['stage'] in ('erc','drc'):
                report = project.loads_json(project._unpack(data['artifacts'][key]['base64']).decode())
                if type(report) is not dict: raise ValueError('Native report is not an object')
        for stream in ('stdout.log','stderr.log'):
            if actual['stage']+'/'+stream not in data['artifacts']: raise ValueError('Native logs missing')
    # This validates custody/shape, not a second ERC/DRC calculation.
