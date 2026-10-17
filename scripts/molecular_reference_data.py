#!/usr/bin/env python3
"""Curated molecular data -> existing GROMACS/LAMMPS boundaries.

No downloads, fitting, new provider registry or implicit periodicity conversion.
The source pack is a pinned input artifact, not a new evidence/state store.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from hashlib import sha1, sha256
import json
import math
from pathlib import Path
import re
import sys
from uuid import uuid4

MAX_BYTES = 4 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")
EV_TO_KJMOL = Decimal('1.602176634e-19') * Decimal('6.02214076e23') / 1000
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / 'examples/molecular-data'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False, ensure_ascii=True).encode('ascii')


def read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Require a regular, non-symlink source file')
    with path.open('rb') as f:
        raw = f.read(MAX_BYTES + 1)
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('Source exceeds the bounded byte budget')
    return raw


def loads(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('Duplicate JSON key')
            result[k] = v
        return result
    def reject(_):
        raise ValueError('Nonfinite JSON constant')
    def floating(text):
        value = float(text)
        if not math.isfinite(value) or (value == 0 and Decimal(text) != 0):
            raise ValueError('JSON floating value overflows or underflows')
        return value
    result = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                        parse_constant=reject, parse_float=floating)
    canonical(result)
    return result


def digest(raw):
    return sha256(raw).hexdigest()


def safe_path(root, relative):
    if type(relative) is not str or not relative or '\\' in relative:
        raise ValueError('Invalid relative artifact path')
    p = Path(relative)
    if p.is_absolute() or '..' in p.parts:
        raise ValueError('Source path must remain within the pack')
    current = root
    for part in p.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('No source symlinks')
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError('Source path escaped the pack')
    return current


def read_catalog(data, expected):
    if type(expected) is not str or not HEX.fullmatch(expected):
        raise ValueError('Supply an externally retained catalog SHA-256')
    raw = read(data / 'catalog.json')
    if digest(raw) != expected:
        raise ValueError('Catalog anchor mismatch')
    c = loads(raw)
    if type(c) is not dict:
        raise ValueError('Catalog must be an object')
    if c.get('schema') != 'net.molecular-reference-catalog.v1':
        raise ValueError('Unsupported data catalog')
    if {s['source_id'] for s in c['sources']} != {'nist-lj4.xyz','ethane.txt','n-butane.txt','FEASST-LICENSE.rst'} or len(c['sources']) != 4:
        raise ValueError('Curated pack requires its four exact source roles')
    seen = set(); paths = set()
    for source in c['sources']:
        if source['source_id'] in seen or source['path'] in paths:
            raise ValueError('Duplicate source identity')
        seen.add(source['source_id']);paths.add(source['path'])
        b = read(safe_path(data, source['path']))
        blob = sha1(b'blob ' + str(len(b)).encode() + b'\0' + b).hexdigest()
        if (len(b) != source['bytes'] or digest(b) != source['sha256']
                or blob != source['git_blob_sha1']):
            raise ValueError('Source bytes disagree with SHA-256/Git identity')
    return c


def nist_coordinates(raw):
    lines = raw.decode('ascii').splitlines()
    if len(lines) != 32 or lines[0] != '30' or lines[1] != '1 8.0 8.0 8.0':
        raise ValueError('Only the complete NIST cuboid configuration 4 is supported')
    positions = []
    for i, line in enumerate(lines[2:], 1):
        fields = line.split()
        if len(fields) != 4 or fields[0] != str(i):
            raise ValueError('NIST particle order or shape mismatch')
        row = [float(x) for x in fields[1:]]
        if not all(math.isfinite(x) and -4 <= x <= 4 for x in row):
            raise ValueError('Invalid reduced coordinate')
        positions.append(row)
    return positions


def lj(positions, *, sigma=1., epsilon=1., cutoff=3., box=None, force_shift=False):
    """Independent all-pairs reference; virial is sum r_ij dot F_ij, not /3."""
    if not 2 <= len(positions) <= 32:
        raise ValueError('Reference requires 2..32 particles')
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
           for v in [sigma, epsilon, cutoff]):
        raise ValueError('Invalid LJ parameters')
    if box is not None and (type(box) is not list or len(box) != 3
            or any(type(x) not in (float, int) or not math.isfinite(x) or x < 2*cutoff for x in box)):
        raise ValueError('Invalid minimum-image box')
    for row in positions:
        if len(row) != 3 or any(type(x) not in (float, int) or not math.isfinite(x) or abs(x)>1000 for x in row):
            raise ValueError('Invalid reference coordinates')
    if type(force_shift) is not bool:
        raise ValueError('Force shift must be an explicit boolean')
    F = [[0., 0., 0.] for _ in positions]
    energy, virial = [], []
    qc = (sigma/cutoff)**6
    vc = 4*epsilon*(qc*qc-qc)
    dvc = 24*epsilon/cutoff*(qc-2*qc*qc)
    for i, a in enumerate(positions):
        for j in range(i):
            d = [x-y for x,y in zip(a, positions[j])]
            if box is not None:
                d = [x-L*round(x/L) for x,L in zip(d,box)]
            r2 = math.fsum(x*x for x in d)
            if r2 <= 0:
                raise ValueError('Coincident sites or periodic images')
            r = math.sqrt(r2)
            if r >= cutoff:
                continue
            q = (sigma*sigma/r2)**3
            e = 4*epsilon*(q*q-q)
            factor = 24*epsilon*(2*q*q-q)/r2
            if force_shift:
                e -= vc+(r-cutoff)*dvc
                factor += dvc/r
            energy.append(e);virial.append(factor*r2)
            for k in range(3):
                f = factor*d[k];F[i][k] += f;F[j][k] -= f
    return dict(energy=math.fsum(energy), virial=math.fsum(virial), forces=F)


def printed_tolerance(text):
    # Printed precision is NOT an experimental standard uncertainty.
    return float(Decimal(1).scaleb(Decimal(text).as_tuple().exponent)/2) + 1e-10


def check_nist(c, positions):
    ref = c['nist_reference']
    if (ref['configuration_id'] != '4' or ref['particle_count'] != 30
            or ref['box_reduced'] != [8.,8.,8.] or ref['boundary'] != 'minimum_image_xyz'):
        raise ValueError('NIST reference semantics differ')
    if len(ref['comparisons']) != 3 or {(r['mode'],r['cutoff_sigma']) for r in ref['comparisons']} != {('truncated',3.),('truncated',4.),('linear_force_shift',3.)}:
        raise ValueError('Require all three distinct NIST reference policies')
    rows = []
    for row in ref['comparisons']:
        cut = row['cutoff_sigma'];shift = row['mode'] == 'linear_force_shift'
        if (row['mode'],cut) not in [('truncated',3.),('truncated',4.),('linear_force_shift',3.)]:
            raise ValueError('Unsupported published truncation scheme')
        computed = lj(positions, cutoff=cut, box=ref['box_reduced'], force_shift=shift)
        # Table is extensive total LRC; website equation is per particle.
        tail = 0. if shift else 30*8*math.pi/3*(30/512)*(1/(3*cut**9)-1/cut**3)
        for key,value in [('pair_energy',computed['energy']),('pair_virial',computed['virial']),('tail_energy',tail)]:
            tolerance = printed_tolerance(row[key])
            rows.append(dict(mode=row['mode'],cutoff_sigma=cut,quantity=key,
                computed=value,published_text=row[key],residual=value-float(row[key]),
                tolerance=tolerance,status='PASS' if abs(value-float(row[key]))<=tolerance else 'FAIL'))
    return dict(status='PASS' if all(r['status']=='PASS' for r in rows) else 'FAIL',
        kind='independent_scalar_vs_published_reference_not_native_engine_execution',checks=rows)


def normalize_targets(c):
    conversions = {'g/cm3': ('kg/m3',Decimal(1000),Decimal(0)),
                   'g/10min':('kg/s',Decimal(1)/Decimal(600000),Decimal(0)),
                   'MPa':('Pa',Decimal(1000000),Decimal(0)),
                   'degC':('K',Decimal(1),Decimal('273.15'))}
    sources = {r['source_id']:r for r in c['industrial_sources']}
    output=[];ids=set()
    for r in c['industrial_targets']:
        if r['target_id'] in ids or r['source_id'] not in sources:
            raise ValueError('Duplicate target or absent provenance')
        ids.add(r['target_id'])
        if (r['origin']!='manufacturer_nominal_property' or r['material_lot'] is not None
                or r['measurement_time'] is not None or r['qualification'] != 'not_performed'
                or r['uncertainty']!={'status':'unspecified'} or r['training_use_authorized'] is not False):
            raise ValueError('A nominal supplier claim cannot be promoted to measured/training data')
        expected_unit={'mass_density':'g/cm3','melt_mass_flow_rate':'g/10min',
            'tensile_yield_stress':'MPa','tensile_break_stress':'MPa','flexural_secant_modulus':'MPa',
            'melting_peak_temperature':'degC','crystallization_peak_temperature':'degC'}.get(r['property'])
        if r['unit']!=expected_unit:
            raise ValueError('Incompatible property and unit; MFR is not viscosity')
        number=Decimal(r['value_text'])
        if not number.is_finite() or number<=0:
            raise ValueError('Invalid supplier number')
        unit,scale,offset=conversions[r['unit']]
        output.append({**r,'normalized_value':float(number*scale+offset),'normalized_unit':unit,
            'conversion':{'scale_decimal':str(scale),'offset_decimal':str(offset)},
            'source_url':sources[r['source_id']]['url'],
            'comparison_status':'NOT_COMPARABLE_TO_CURRENT_LJ_PROFILES',
            'reason':'Unknown lot/conditions; polymer properties are not monatomic LJ validation'})
    return output


def forcefield_inventory(data,c):
    output=[]
    for entry in c['academic_forcefields']:
        src=next(s for s in c['sources'] if s['source_id']==entry['source_id'])
        raw=read(safe_path(data,src['path']));sections={};active=None
        for line in raw.decode('ascii').splitlines():
            if not line or line.startswith('#'):continue
            if line in {'Site Types','Sites','Bond Types','Bonds','Angle Types','Angles','Dihedral Types','Dihedrals'}:
                if line in sections:raise ValueError('Duplicate force-field section')
                active=line;sections[active]=[]
            elif active is None:raise ValueError('Unknown force-field record')
            else:sections[active].append(line)
        for key in ('Site Types','Sites','Bond Types','Bonds'):
            if not sections.get(key):raise ValueError('Incomplete bonded definition')
        output.append({**entry,'source_sha256':src['sha256'],'native_sections':sections,
            'units':{'length':'angstrom','energy':'kJ/mol'},
            'executable':False,'conversion_status':'BLOCKED_PENDING_BONDED_PROFILE',
            'warning':'Preserve functional forms, exclusions and mixing rules; no direct relabeling as Ar'})
    return output


def gromacs_files(positions_A, *, box_A, cutoff_A, epsilon_eV, sigma_A):
    # 9 decimals in nm -> 1e-8 A quantization; recompute on represented values.
    lines=['NET derived simulation input; not a physical measurement',f'{len(positions_A):5d}']
    represented=[]
    for i,row in enumerate(positions_A,1):
        shifted=[(Decimal(str(x))+Decimal(str(box_A))/2)/10 for x in row]
        text=[f'{x:14.9f}' for x in shifted]
        represented.append([float(Decimal(t)*10-Decimal(str(box_A))/2) for t in text])
        lines.append(f'{i:5d}{"AR":<5}{"AR":>5}{i:5d}'+''.join(text))
    lines.append(' '.join([f'{box_A/10:.9f}']*3))
    e=Decimal(str(epsilon_eV))*EV_TO_KJMOL
    top=f'''[ defaults ]
1 2 no 1.0 1.0
[ atomtypes ]
Ar 18 39.948 0.0 A {Decimal(str(sigma_A))/10} {e}
[ moleculetype ]
AR 1
[ atoms ]
1 Ar 1 AR AR 1 0.0 39.948
[ system ]
NET retained molecular-data configuration
[ molecules ]
AR {len(positions_A)}
'''
    cut=Decimal(str(cutoff_A))/10
    mdp=f'''integrator = md
nsteps = 0
dt = 0.001
cutoff-scheme = Verlet
nstlist = 1
verlet-buffer-tolerance = -1
rlist = {cut}
coulombtype = Cut-off
rcoulomb = {cut}
vdwtype = Cut-off
vdw-modifier = None
rvdw = {cut}
DispCorr = no
pbc = xyz
constraints = none
tcoupl = no
pcoupl = no
comm-mode = None
nstcomm = 0
gen-vel = no
nstcalcenergy = 1
nstenergy = 1
nstlog = 1
'''
    return {'system.top':top.encode(),'run.mdp':mdp.encode(),'conf.gro':('\n'.join(lines)+'\n').encode()},represented


def write_new(path,raw):
    with path.open('xb') as f:f.write(raw)


def render(data,expected,profile,allow_projection=False):
    c=read_catalog(data,expected)
    positions=nist_coordinates(read(data/'sources/nist-lj4.xyz'))
    nist=check_nist(c,positions)
    if nist['status']!='PASS':raise ValueError('Procured NIST coordinates fail published reference checks')
    if profile not in {'nist-periodic','cluster-projection'}:raise ValueError('Unknown profile')
    projected=profile=='cluster-projection'
    if projected and allow_projection is not True:
        raise ValueError('Explicit --allow-projection required: boundary/cutoff/precision change')
    scaled=[[float((Decimal(str(x))*Decimal('3.4')).quantize(Decimal('0.00000001'),rounding=ROUND_HALF_EVEN)) for x in row] for row in positions]
    box=256. if projected else 27.2;cutoff=8.5 if projected else 10.2
    files,represented=gromacs_files(scaled,box_A=box,cutoff_A=cutoff,epsilon_eV=0.0104,sigma_A=3.4)
    if max(math.dist(a,b) for a,b in zip(scaled,represented))>1e-12:raise ValueError('GRO mapping changed the common coordinates')
    if projected:
        # Instantaneous equivalence of large periodic box and isolated cluster.
        for k in range(3):
            span=max(p[k] for p in scaled)-min(p[k] for p in scaled)
            if span>=box/2 or box-span<=cutoff:raise ValueError('Periodic image exclusion failed')
        for i,a in enumerate(scaled):
            for b in scaled[:i]:
                r=math.dist(a,b)
                if r<2.55 or abs(r-8.5)<0.001:raise ValueError('Coordinates outside existing LAMMPS profile')
    native={'schema':'ste.lammps-lj-input.v1','origin':'synthetic',
        'configuration_id':'nist4-derived-isolated-quantized','frame_id':'nist4-cuboid-cartesian',
        'atom_ids':[f'NIST4-{i}' for i in range(1,31)],'positions_A':scaled}
    if projected:files['lammps-input.json']=canonical(native)+b'\n'
    reference=lj(represented,sigma=3.4,epsilon=0.0104,cutoff=cutoff,
                 box=None if projected else [box]*3)
    files['scalar-reference.json']=canonical({**reference,'energy_unit':'eV','force_unit':'eV/angstrom',
        'gromacs_energy_kj_mol':reference['energy']*float(EV_TO_KJMOL),
        'status':'computed_independent_scalar_not_native_result'})+b'\n'
    files['industrial-targets.json']=canonical(normalize_targets(c))+b'\n'
    files['academic-forcefields.json']=canonical(forcefield_inventory(data,c))+b'\n'
    files['nist-reference-check.json']=canonical(nist)+b'\n'
    files['catalog.json']=read(data/'catalog.json')
    for src in c['sources']:
        files[src['path']]=read(safe_path(data,src['path']))
    m=dict(schema='net.molecular-data-export.v1',capture_id='capture-'+uuid4().hex,
        created_at=datetime.now(timezone.utc).isoformat(),catalog_sha256=expected,
        profile=profile,parent_coordinate_sha256=digest(read(data/'sources/nist-lj4.xyz')),
        split_group='nist-configuration-4:'+digest(read(data/'sources/nist-lj4.xyz')),
        split_policy='all_derivatives_share_source_group_no_training_split_assigned',
        transformations=dict(scale_angstrom_per_sigma=3.4,coordinate_quantum_A=1e-8,
            original_boundary='minimum_image_xyz',lammps_boundary='f f f' if projected else 'unsupported',
            gromacs_boundary='minimum_image_xyz',gromacs_box_A=box,cutoff_A=cutoff,
            original_published_cutoffs_sigma=[3.,4.],epsilon_eV=0.0104,
            native_observable='pair_energy_without_tail',tail_correction_applied=False,
            energy_conversion_eV_to_kJ_per_mol=str(EV_TO_KJMOL),
            periodic_image_exclusion_verified=projected,
            equivalence_scope='single_point_only_no_dynamics' if projected else 'NIST_periodic_pair_energy'),
        reuse_of_published_NIST_energy_as_projection_label=False,
        files={name:{'sha256':digest(raw),'bytes':len(raw)} for name,raw in files.items()},
        native_execution='NOT_RUN',physical_validation='not_performed',equipment_authority=False,
        training_use_authorized=False)
    return files,m


def prepare(data,expected,output,profile,allow_projection=False):
    files,m=render(data,expected,profile,allow_projection)
    out=Path(output).absolute()
    if out.resolve().is_relative_to(ROOT):raise ValueError('Output must be new and outside checkout')
    out.mkdir(parents=True,exist_ok=False)
    for name,raw in files.items():
        dest=out/name;dest.parent.mkdir(parents=True,exist_ok=True);write_new(dest,raw)
    raw=canonical(m)+b'\n';write_new(out/'manifest.json',raw)
    return dict(status='PREPARED_NOT_NATIVE_EXECUTED',manifest_sha256=digest(raw),directory=str(out))


def verify_export(path,anchor):
    if not isinstance(anchor,str) or not HEX.fullmatch(anchor):raise ValueError('External manifest anchor required')
    raw=read(path/'manifest.json')
    if digest(raw)!=anchor:raise ValueError('Export manifest anchor mismatch')
    m=loads(raw)
    if m.get('schema')!='net.molecular-data-export.v1' or m.get('native_execution')!='NOT_RUN':
        raise ValueError('Invalid export state')
    for name,identity in m['files'].items():
        b=read(safe_path(path,name))
        if len(b)!=identity['bytes'] or digest(b)!=identity['sha256']:raise ValueError('Export artifact mismatch')
    expected_files,rebuilt=render(path,m['catalog_sha256'],m['profile'],allow_projection=True)
    for key in set(rebuilt)-{'capture_id','created_at'}:
        if rebuilt[key]!=m.get(key):raise ValueError('Export semantic binding mismatch: '+key)
    if set(m)!=set(rebuilt):raise ValueError('Unexpected export manifest fields')
    if not re.fullmatch('capture-[0-9a-f]{32}',m['capture_id']):raise ValueError('Invalid capture identity')
    if datetime.fromisoformat(m['created_at']).utcoffset() is None:raise ValueError('Timezone required')
    if set(m['files'])!=set(expected_files):raise ValueError('Artifact set mismatch')
    for name,raw in expected_files.items():
        if raw!=read(safe_path(path,name)):raise ValueError('Export semantic replay differs: '+name)
    return m


def run_native(path,anchor,provider,executable,pin,output,*,runtime_binding=None,binding_sha256=None):
    """Dispatch only via the existing provider, never a surrogate replacement."""
    m=verify_export(path,anchor)
    frozen={name:read(safe_path(path,name)) for name in m['files']}
    if any(digest(raw)!=m['files'][name]['sha256'] for name,raw in frozen.items()):
        raise ValueError('Export changed before dispatch')
    out=Path(output).absolute()
    if out.resolve().is_relative_to(ROOT):raise ValueError('Native output must be outside checkout')
    out.mkdir(parents=True,exist_ok=False)
    report=dict(schema='net.molecular-data-native-gate.v1',execution_id='execution-'+uuid4().hex,
        export_sha256=anchor,provider=provider,created_at=datetime.now(timezone.utc).isoformat(),
        status='NOT_RUN',physical_validation='not_performed',equipment_authority=False)
    code=2
    try:
        if provider not in {'gromacs','lammps'}:raise ValueError('Unsupported provider')
        if provider=='lammps' and m['profile']!='cluster-projection':
            report['blocker']='Current LAMMPS profile refuses the original periodic NIST case'
        elif executable is None or pin is None:
            report['blocker']='Explicit executable and independently reviewed SHA-256 required'
        elif not executable.is_absolute() or not executable.is_file():
            report['blocker']='Explicit native executable is unavailable'
        elif not HEX.fullmatch(pin):raise ValueError('Invalid executable SHA-256')
        elif provider=='gromacs' and (runtime_binding is None or binding_sha256 is None):
            report['blocker']='GROMACS requires an explicit runtime-binding file and its externally reviewed SHA-256'
        else:
            sys.path.insert(0,str(ROOT/'instruments/execution/compute-runtime'))
            if provider=='lammps':
                from execution import lammps as lm
                value=lm.validate_input(lm.load(frozen['lammps-input.json']))
                version=lm.probe(executable,pin)
                result=lm.run_lammps_specification(lm.specification(value,version,pin),executable,
                                                   artifact_directory=out/'native')
                native_anchor=lm.digest(lm.read_regular(out/'native/manifest.json'))
                check=lm.verify_capture(out/'native',native_anchor)
                comparison=check['comparison'];passed=comparison['status']=='PASS'
            else:
                from execution import gromacs as gm
                from execution.specification import ExecutionSpecification
                from execution.gromacs_artifacts import validate_manifest, validate_receipt
                binding_raw=read(runtime_binding)
                if not HEX.fullmatch(binding_sha256) or digest(binding_raw)!=binding_sha256:
                    raise ValueError('GROMACS runtime binding anchor mismatch')
                binding=validate_manifest(loads(binding_raw))
                if binding['executable']!={'path':str(executable),'sha256':pin}:
                    raise ValueError('Runtime binding differs from explicit executable')
                report['runtime_binding_sha256']=binding_sha256
                version=binding['version_line']  # Runner performs the actual version probe.
                spec=ExecutionSpecification(gm.gromacs_program_descriptor(version,frozen['system.top'],
                    runtime_manifest=binding),frozen['run.mdp'],frozen['conf.gro'])
                result=gm.run_gromacs_specification(spec,executable,expected_binary_sha256=pin,
                    max_warnings=0,cpu_only=True,runtime_manifest=binding,artifact_directory=out/'native')
                receipt_raw=read(out/'native/receipt.json')
                native_anchor=digest(receipt_raw)
                validate_receipt(out/'native')  # Only the existing offline validator.
                if digest(read(out/'native/receipt.json'))!=native_anchor:
                    raise ValueError('GROMACS receipt changed during validation')
                if result.status!='completed':raise ValueError(result.detail)
                actual=float(result.output.split()[1])
                expected=loads(frozen['scalar-reference.json'])['gromacs_energy_kj_mol']
                passed=math.isfinite(actual) and math.isclose(actual,expected,rel_tol=5e-6,abs_tol=2e-5)
                comparison=dict(comparison_id='comparison-'+uuid4().hex,status='PASS' if passed else 'FAIL',
                    actual_kj_mol=actual,reference_kj_mol=expected,residual_kj_mol=actual-expected,
                    atol_kj_mol=2e-5,rtol=5e-6,policy='provisional_numerical_not_measurement_uncertainty')
            report.update(status='PASS' if passed and result.status=='completed' else 'FAIL',
                comparison=comparison,native_receipt_sha256=native_anchor,
                native_receipt_file='receipt.json' if provider=='gromacs' else 'manifest.json',
                reference_evidence_sha256=m['files']['scalar-reference.json']['sha256'],
                computation_identity=result.computation_identity)
            code=0 if report['status']=='PASS' else 1
    except Exception as exc:
        report.update(status='FAIL',error={'type':type(exc).__name__,'message':str(exc)});code=1
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        write_new(out/'gate.json',canonical(report)+b'\n')
    return code,report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='cmd',required=True)
    for cmd in ['check','prepare']:
        a=s.add_parser(cmd);a.add_argument('--data',type=Path,default=DEFAULT_DATA)
        a.add_argument('--catalog-sha256',required=True)
        if cmd=='prepare':
            a.add_argument('--output',type=Path,required=True)
            a.add_argument('--profile',choices=['nist-periodic','cluster-projection'],required=True)
            a.add_argument('--allow-projection',action='store_true')
    a=s.add_parser('verify');a.add_argument('--export',type=Path,required=True);a.add_argument('--manifest-sha256',required=True)
    a=s.add_parser('run');a.add_argument('--export',type=Path,required=True);a.add_argument('--manifest-sha256',required=True)
    a.add_argument('--provider',choices=['gromacs','lammps'],required=True);a.add_argument('--executable',type=Path)
    a.add_argument('--expected-sha256');a.add_argument('--output',type=Path,required=True)
    a.add_argument('--runtime-binding',type=Path,help='Required for GROMACS retained execution')
    a.add_argument('--binding-sha256',help='External SHA-256 of the runtime-binding JSON bytes')
    a=p.parse_args(argv)
    try:
        if a.cmd=='check':
            c=read_catalog(a.data,a.catalog_sha256)
            result=check_nist(c,nist_coordinates(read(a.data/'sources/nist-lj4.xyz')))
            result['industrial_targets']=normalize_targets(c)
            result['forcefields']=forcefield_inventory(a.data,c)
            code=0 if result['status']=='PASS' else 1
        elif a.cmd=='prepare':
            result=prepare(a.data,a.catalog_sha256,a.output,a.profile,a.allow_projection);code=0
        elif a.cmd=='verify':
            result=verify_export(a.export,a.manifest_sha256);code=0
        else:
            code,result=run_native(a.export,a.manifest_sha256,a.provider,a.executable,a.expected_sha256,a.output,
                runtime_binding=a.runtime_binding,binding_sha256=a.binding_sha256)
        print(json.dumps(result,allow_nan=False,indent=2));return code
    except Exception as exc:
        print(json.dumps({'status':'FAIL','error':str(exc)}));return 1


if __name__=='__main__':raise SystemExit(main())
