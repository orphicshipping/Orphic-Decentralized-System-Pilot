"""Curated data checks. Synthetic handoff tests are not native MD execution."""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha1
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('molecular_reference_data', ROOT/'scripts/molecular_reference_data.py')
md = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(md)
DATA = ROOT/'examples/molecular-data'
ANCHOR = (DATA/'catalog.sha256').read_text().strip()


@pytest.fixture
def catalog():
    return md.read_catalog(DATA, ANCHOR)


@pytest.fixture
def coordinates():
    return md.nist_coordinates(md.read(DATA/'sources/nist-lj4.xyz'))


@pytest.fixture
def cluster(tmp_path):
    dest=tmp_path/'cluster'
    report=md.prepare(DATA,ANCHOR,dest,'cluster-projection',True)
    return dest,report['manifest_sha256']


def test_exact_source_bytes_license_and_pins(catalog):
    assert len(catalog['sources'])==4
    for source in catalog['sources']:
        raw=md.read(DATA/source['path'])
        assert sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==source['git_blob_sha1']
        assert source['physical_measurement'] is False
        assert source['revision']=='af95d31a6401f08f99d38439c71427b30ce22e0a'
    assert b'acknowledge' in md.read(DATA/'sources/FEASST-LICENSE.rst')


def test_published_nist_values_are_all_reproduced(catalog,coordinates):
    result=md.check_nist(catalog,coordinates)
    assert result['status']=='PASS' and len(result['checks'])==9
    assert all(abs(r['residual'])<=r['tolerance'] for r in result['checks'])
    assert 'not_native_engine_execution' in result['kind']


@pytest.mark.parametrize('bad', ['empty','duplicate','box','boundary','quantity'])
def test_reference_policy_mismatch_cannot_pass(catalog,coordinates,bad):
    if bad=='empty':catalog['nist_reference']['comparisons']=[]
    elif bad=='duplicate':catalog['nist_reference']['comparisons'][1]=deepcopy(catalog['nist_reference']['comparisons'][0])
    elif bad=='box':catalog['nist_reference']['box_reduced']=[9,9,9]
    elif bad=='boundary':catalog['nist_reference']['boundary']='nonperiodic'
    else:catalog['nist_reference']['comparisons'][0]['pair_energy']='0.0000E0'
    if bad=='quantity':assert md.check_nist(catalog,coordinates)['status']=='FAIL'
    else:
        with pytest.raises(ValueError):md.check_nist(catalog,coordinates)


@pytest.mark.parametrize('anchor', ['0'*64,'a'*63,'sha256:'+'a'*64,None])
def test_catalog_requires_external_exact_anchor(anchor):
    with pytest.raises(ValueError):md.read_catalog(DATA,anchor)


@pytest.mark.parametrize('name',['nist-lj4.xyz','ethane.txt','n-butane.txt','FEASST-LICENSE.rst'])
def test_source_drift_detected(tmp_path,name):
    dest=tmp_path/'pack';shutil.copytree(DATA,dest)
    with (dest/'sources'/name).open('ab') as f:f.write(b'\n')
    with pytest.raises(ValueError,match='Source bytes'):md.read_catalog(dest,ANCHOR)


@pytest.mark.parametrize('raw',[b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":Infinity}',
                             b'{"x":1e999}',b'{"x":1e-999}',b'\xff'])
def test_strict_json(raw):
    with pytest.raises((ValueError,UnicodeError)):md.loads(raw)


@pytest.mark.parametrize('relative',['../outside','/etc/passwd','a/../../outside','a\\b',''])
def test_paths_do_not_escape(tmp_path,relative):
    with pytest.raises(ValueError):md.safe_path(tmp_path,relative)


def test_symlink_is_not_a_source(tmp_path):
    target=tmp_path/'real';target.write_text('data')
    try:(tmp_path/'link').symlink_to(target)
    except OSError:pytest.skip('OS does not permit this symlink test')
    with pytest.raises(ValueError):md.read(tmp_path/'link')
    with pytest.raises(ValueError):md.safe_path(tmp_path,'link')


@pytest.mark.parametrize('mutation',['count','cell','duplicate','nan','bound','extra'])
def test_nist_configuration_is_not_silently_repaired(mutation):
    raw=md.read(DATA/'sources/nist-lj4.xyz');lines=raw.decode().splitlines()
    if mutation=='count':lines[0]='29'
    elif mutation=='cell':lines[1]='1 9.0 8.0 8.0'
    elif mutation=='duplicate':lines[3]=lines[2]
    elif mutation=='nan':lines[2]='1 nan 0 0'
    elif mutation=='bound':lines[2]='1 4.1 0 0'
    else:lines.append('extra')
    with pytest.raises(ValueError):md.nist_coordinates('\n'.join(lines).encode())


@pytest.mark.parametrize('shift',[False,True])
def test_energy_gradient_force_and_force_balance(coordinates,shift):
    original=md.lj(coordinates,box=[8.,8.,8.],force_shift=shift)
    h=1e-6
    for atom in [0,7,19]:
        for axis in range(3):
            plus=deepcopy(coordinates);minus=deepcopy(coordinates)
            plus[atom][axis]+=h;minus[atom][axis]-=h
            force=-(md.lj(plus,box=[8.,8.,8.],force_shift=shift)['energy']-
                    md.lj(minus,box=[8.,8.,8.],force_shift=shift)['energy'])/(2*h)
            assert force==pytest.approx(original['forces'][atom][axis],rel=2e-6,abs=2e-8)
    for axis in range(3):assert math.fsum(f[axis] for f in original['forces'])==pytest.approx(0,abs=1e-12)


def test_symmetry_does_not_change_the_declared_model(coordinates):
    original=md.lj(coordinates,box=[8.,8.,8.])
    transformed=[[-p[1]+1.2,p[0]+.2,p[2]-.8] for p in coordinates]
    rotated=md.lj(transformed,box=[8.,8.,8.])
    assert rotated['energy']==pytest.approx(original['energy'],abs=1e-11)
    reversed_result=md.lj(list(reversed(coordinates)),box=[8.,8.,8.])
    for a,b in zip(reversed_result['forces'],reversed(original['forces'])):
        assert a==pytest.approx(b,abs=1e-11)
    for a,b in zip(rotated['forces'],original['forces']):assert a==pytest.approx([-b[1],b[0],b[2]],abs=1e-11)


@pytest.mark.parametrize('kwargs',[{'sigma':0},{'epsilon':True},{'cutoff':float('nan')},{'box':[1,1,1]}])
def test_invalid_reference_parameters(kwargs):
    with pytest.raises(ValueError):md.lj([[0,0,0],[1,0,0]],**kwargs)


def test_original_periodicity_is_not_discarded_implicitly(tmp_path):
    with pytest.raises(ValueError,match='allow-projection'):
        md.prepare(DATA,ANCHOR,tmp_path/'refuse','cluster-projection')
    assert not (tmp_path/'refuse').exists()


def test_same_source_derivatives_share_split_group():
    _,a=md.render(DATA,ANCHOR,'nist-periodic')
    _,b=md.render(DATA,ANCHOR,'cluster-projection',True)
    assert a['split_group']==b['split_group']
    assert a['training_use_authorized'] is b['training_use_authorized'] is False
    assert a['profile']!=b['profile']


def test_cluster_two_engine_inputs_represent_same_static_coordinates(cluster):
    path,anchor=cluster;m=md.verify_export(path,anchor)
    atomic=md.loads(md.read(path/'lammps-input.json'))
    rows=md.read(path/'conf.gro').decode().splitlines()
    for source,gro in zip(atomic['positions_A'],rows[2:32]):
        actual=[float(Decimal(gro[k:k+14])*10-128) for k in (20,34,48)]
        assert actual==source
    assert rows[32]=='25.600000000 25.600000000 25.600000000'
    assert b'AR 30\n' in md.read(path/'system.top')
    assert b'rvdw = 0.85' in md.read(path/'run.mdp')
    assert b'DispCorr = no' in md.read(path/'run.mdp')
    assert m['reuse_of_published_NIST_energy_as_projection_label'] is False
    assert m['transformations']['equivalence_scope']=='single_point_only_no_dynamics'
    plain=md.lj(atomic['positions_A'],sigma=3.4,epsilon=.0104,cutoff=8.5)
    periodic=md.lj(atomic['positions_A'],sigma=3.4,epsilon=.0104,cutoff=8.5,box=[256]*3)
    assert periodic==plain


def test_unit_conversion_preserves_originals(catalog):
    assert md.EV_TO_KJMOL==Decimal('96.4853321233100184')
    targets=md.normalize_targets(catalog)
    assert len(targets)==14 and len({r['material_id'] for r in targets})==3
    density=next(r for r in targets if r['material_id']=='Shell 65N8U' and r['property']=='mass_density')
    assert density['normalized_value']==965 and density['value_text']=='0.965'
    mfr=next(r for r in targets if r['material_id']=='Shell 65N8U' and r['property']=='melt_mass_flow_rate')
    assert mfr['normalized_value']==pytest.approx(8.2/600000)
    assert mfr['conditions']=={'temperature_K':463.15,'load_kg':2.16}
    melt=next(r for r in targets if r['property']=='melting_peak_temperature')
    assert melt['normalized_value']==404.15
    assert all(r['measurement_time'] is None and r['material_lot'] is None for r in targets)
    assert all(r['uncertainty']=={'status':'unspecified'} for r in targets)
    assert all(r['comparison_status']=='NOT_COMPARABLE_TO_CURRENT_LJ_PROFILES' for r in targets)
    assert all(r['raw_document_retained'] is False for r in catalog['industrial_sources'])


@pytest.mark.parametrize('promotion',['origin','uncertainty','training','measurement','lot','qualification','unit'])
def test_industrial_claims_cannot_be_promoted(catalog,promotion):
    row=catalog['industrial_targets'][0]
    k,v={'origin':('origin','measured'),'uncertainty':('uncertainty',{'status':'declared','value':0}),
        'training':('training_use_authorized',True),'measurement':('measurement_time','2026-10-05'),
        'lot':('material_lot','invented-lot'),'qualification':('qualification','validated'),
        'unit':('unit','Pa*s')}[promotion]
    row[k]=v
    with pytest.raises(ValueError):md.normalize_targets(catalog)


def test_mfr_is_not_viscosity(catalog):
    row=next(r for r in catalog['industrial_targets'] if r['property']=='melt_mass_flow_rate')
    row['property']='dynamic_viscosity'
    with pytest.raises(ValueError,match='MFR'):md.normalize_targets(catalog)


def test_alkane_forcefields_are_retained_not_falsely_executable(catalog):
    rows=md.forcefield_inventory(DATA,catalog)
    assert len(rows)==2
    ethane,butane=rows
    assert len(ethane['native_sections']['Sites'])==2
    assert len(butane['native_sections']['Sites'])==4
    assert len(butane['native_sections']['Dihedrals'])==1
    assert all(r['executable'] is False and r['conversion_status']=='BLOCKED_PENDING_BONDED_PROFILE' for r in rows)
    assert 'RigidBond length=1.54' in butane['native_sections']['Bond Types'][0]


def test_offline_verify_does_not_execute_a_provider(cluster,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('provider must not run')
    monkeypatch.setattr(subprocess,'run',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
    result=md.verify_export(*cluster)
    assert result['native_execution']=='NOT_RUN'


@pytest.mark.parametrize('name',['conf.gro','system.top','run.mdp','lammps-input.json',
                                  'scalar-reference.json','industrial-targets.json','sources/nist-lj4.xyz'])
def test_any_export_byte_change_is_detected(cluster,name):
    path,anchor=cluster
    with (path/name).open('ab') as f:f.write(b'\n')
    with pytest.raises(ValueError):md.verify_export(path,anchor)


def test_resealed_wrong_reference_is_detected_by_semantic_replay(cluster):
    path,_=cluster
    raw=md.loads(md.read(path/'scalar-reference.json'));raw['energy']=0
    b=md.canonical(raw)+b'\n';(path/'scalar-reference.json').write_bytes(b)
    m=md.loads(md.read(path/'manifest.json'));m['files']['scalar-reference.json']={'sha256':md.digest(b),'bytes':len(b)}
    mb=md.canonical(m)+b'\n';(path/'manifest.json').write_bytes(mb)
    with pytest.raises(ValueError,match='semantic'):md.verify_export(path,md.digest(mb))


def test_repeated_prepare_has_same_payload_and_new_capture(tmp_path):
    a=md.prepare(DATA,ANCHOR,tmp_path/'a','cluster-projection',True)
    b=md.prepare(DATA,ANCHOR,tmp_path/'b','cluster-projection',True)
    ma=md.verify_export(tmp_path/'a',a['manifest_sha256']);mb=md.verify_export(tmp_path/'b',b['manifest_sha256'])
    assert ma['files']==mb['files'] and ma['capture_id']!=mb['capture_id']
    with pytest.raises(FileExistsError):md.prepare(DATA,ANCHOR,tmp_path/'a','cluster-projection',True)


@pytest.mark.parametrize('provider',['gromacs','lammps'])
def test_absent_native_binding_is_not_run(cluster,tmp_path,provider):
    path,anchor=cluster
    code,r=md.run_native(path,anchor,provider,None,None,tmp_path/'native')
    assert code==2 and r['status']=='NOT_RUN'
    assert r['equipment_authority'] is False
    assert json.loads((tmp_path/'native/gate.json').read_text())==r


def test_periodic_nist_refuses_current_lammps_even_with_a_binary(tmp_path):
    out=tmp_path/'periodic';prepared=md.prepare(DATA,ANCHOR,out,'nist-periodic')
    exe=tmp_path/'fake';exe.write_text('not native')
    code,r=md.run_native(out,prepared['manifest_sha256'],'lammps',exe,md.digest(exe.read_bytes()),tmp_path/'native')
    assert code==2 and r['status']=='NOT_RUN' and 'periodic' in r['blocker']


def test_gromacs_refuses_without_build_dependency_manifest(cluster,tmp_path):
    exe=tmp_path/'fake';exe.write_text('not native')
    code,r=md.run_native(*cluster,'gromacs',exe,md.digest(exe.read_bytes()),tmp_path/'native')
    assert code==2 and 'runtime-binding' in r['blocker']


def test_cli_check_prepare_verify_and_not_run(tmp_path,capsys):
    assert md.main(['check','--data',str(DATA),'--catalog-sha256',ANCHOR])==0
    json.loads(capsys.readouterr().out)
    out=tmp_path/'export'
    assert md.main(['prepare','--data',str(DATA),'--catalog-sha256',ANCHOR,'--output',str(out),
                    '--profile','cluster-projection','--allow-projection'])==0
    report=json.loads(capsys.readouterr().out)
    assert md.main(['verify','--export',str(out),'--manifest-sha256',report['manifest_sha256']])==0
    capsys.readouterr()
    assert md.main(['run','--export',str(out),'--manifest-sha256',report['manifest_sha256'],
                    '--provider','lammps','--output',str(tmp_path/'native')])==2
    assert json.loads(capsys.readouterr().out)['status']=='NOT_RUN'


def test_existing_lammps_contract_accepts_export_and_agrees_with_independent_reference(cluster):
    """Requires PR197 source, not a binary. Never a native execution claim."""
    sys.path.insert(0,str(ROOT/'instruments/execution/compute-runtime'))
    from execution import lammps as lm
    value=lm.validate_input(lm.load(md.read(cluster[0]/'lammps-input.json')))
    expected=md.loads(md.read(cluster[0]/'scalar-reference.json'))
    reference=lm.scalar_reference(value)
    assert reference[0]==pytest.approx(expected['energy'],abs=1e-12)
    for a,b in zip(reference[1],expected['forces']):assert a==pytest.approx(b,abs=1e-12)
    # Real program/configuration identity constructors, still no executable.
    spec=lm.specification(value,lm.VERSION_PREFIX+'test-double', '1'*64)
    assert spec.input_payload==lm.canonical(value)


def test_gromacs_current_manifest_interface_handoff_is_explicit(cluster,tmp_path,monkeypatch):
    """Protocol double matches source-inspected PR192, not executed GROMACS."""
    import types
    exe=tmp_path/'fake';exe.write_bytes(b'explicit test double')
    pin=md.digest(exe.read_bytes())
    binding={'schema':'ste.gromacs-runtime-binding.v1','version_line':'GROMACS version: test-double',
        'executable':{'path':str(exe),'sha256':pin},
        'build_record':{'path':str(tmp_path/'build'),'sha256':'2'*64},'dependencies':[]}
    bindingfile=tmp_path/'binding.json';bindingfile.write_bytes(md.canonical(binding))
    expected=md.loads(md.read(cluster[0]/'scalar-reference.json'))['gromacs_energy_kj_mol']
    gm=types.ModuleType('execution.gromacs');ga=types.ModuleType('execution.gromacs_artifacts')
    def descriptor(version,top,*,runtime_manifest):
        assert runtime_manifest==binding and version==binding['version_line']
        return b'protocol-test-only\n'+top
    def run(spec,executable,*,expected_binary_sha256,max_warnings,cpu_only,runtime_manifest,artifact_directory):
        assert expected_binary_sha256==pin and max_warnings==0 and cpu_only is True
        assert runtime_manifest==binding and executable==exe
        assert spec.configuration==md.read(cluster[0]/'run.mdp')
        artifact_directory.mkdir();(artifact_directory/'receipt.json').write_bytes(b'{"synthetic_protocol_double":true}')
        return SimpleNamespace(status='completed',output=f'potential_kj_per_mol {expected}'.encode(),computation_identity='test-double',detail=None)
    gm.gromacs_program_descriptor=descriptor;gm.run_gromacs_specification=run
    ga.validate_manifest=lambda x:x
    ga.validate_receipt=lambda path:{'synthetic_protocol_double':True}
    monkeypatch.setitem(sys.modules,'execution.gromacs',gm);monkeypatch.setitem(sys.modules,'execution.gromacs_artifacts',ga)
    import execution
    monkeypatch.setattr(execution,'gromacs',gm,raising=False)
    code,r=md.run_native(*cluster,'gromacs',exe,pin,tmp_path/'native',
        runtime_binding=bindingfile,binding_sha256=md.digest(bindingfile.read_bytes()))
    assert code==0 and r['status']=='PASS' and r['computation_identity']=='test-double'
    assert r['native_receipt_file']=='receipt.json'
