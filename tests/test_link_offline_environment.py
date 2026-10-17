"""Offline deployment tests use tiny, explicitly synthetic wheels and real pip."""
import base64
from copy import deepcopy
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'provision_link_environment.py'
spec = importlib.util.spec_from_file_location('link_offline_environment', SCRIPT)
offline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offline)


def ref(raw): return 'sha256:' + hashlib.sha256(raw).hexdigest()


def wheel_bytes(name, version='1.0', requirements=(), extras=(), files=None, *, record_fault=False):
    stem = name.replace('-', '_') + '-' + version
    files = dict(files or {name.replace('-', '_') + '/__init__.py': b'VALUE = 42\n'})
    metadata = f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nRequires-Python: >=3.11\n'
    metadata += ''.join(f'Requires-Dist: {r}\n' for r in requirements)
    metadata += ''.join(f'Provides-Extra: {e}\n' for e in extras)
    files[stem + '.dist-info/METADATA'] = (metadata + '\n').encode()
    files[stem + '.dist-info/WHEEL'] = b'Wheel-Version: 1.0\nGenerator: NET-synthetic-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
    record = stem + '.dist-info/RECORD'
    out = io.StringIO(); writer = csv.writer(out, lineterminator='\n')
    for path, raw in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode()
        writer.writerow([path, 'sha256=' + digest, len(raw)])
    writer.writerow([record, '', '']); files[record] = out.getvalue().encode()
    if record_fault:
        files[next(iter(files))] += b'# changed after RECORD\n'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, raw in files.items(): archive.writestr(path, raw)
    return stem + '-py3-none-any.whl', buf.getvalue()


def put_wheel(folder, *args, **kwargs):
    name, raw = wheel_bytes(*args, **kwargs)
    (folder/name).write_bytes(raw)
    return folder/name


@pytest.fixture
def project(tmp_path):
    raw = b'''[project]
name = "net-offline-fixture"
version = "1.0"
requires-python = ">=3.11"
dependencies = ["net-offline-dependency==1.0"]
[project.optional-dependencies]
legibility = ["net-offline-crypto==1.0"]
dev = ["net-offline-test==1.0"]
unused = ["network-payload @ https://invalid.example/not-used.whl"]
'''
    path = tmp_path/'pyproject.toml'; path.write_bytes(raw)
    wheels = tmp_path/'wheels'; wheels.mkdir()
    root = put_wheel(wheels, 'net-offline-fixture', requirements=(
        'net-offline-dependency==1.0', 'net-offline-crypto==1.0; extra == "legibility"',
        'net-offline-test==1.0; extra == "dev"',
        'network-payload @ https://invalid.example/not-used.whl ; extra == "unused"'),
        extras=('legibility', 'dev', 'unused'))
    put_wheel(wheels, 'net-offline-dependency')
    return path, wheels, root


def prepare(project, target, profile='runtime'):
    path, wheels, root = project
    return offline.prepare(path, wheels, target, expected_project_ref=ref(path.read_bytes()),
                           expected_root_ref=ref(root.read_bytes()), profile=profile)


def snapshot(root): return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_prepare_verifies_full_graph_and_preserves_original_wheel_bytes(project, tmp_path):
    path,wheels,root = project; originals = snapshot(wheels)
    result = prepare(project, tmp_path/'bundle')
    assert result['status'] == 'PREPARED_NOT_AUTHORIZED'
    assert result['project'] == {'name':'net-offline-fixture', 'version':'1.0'}
    assert result['package_count'] == 2
    assert snapshot(tmp_path/'bundle'/'wheels') == originals
    checked = offline.inspect_bundle(tmp_path/'bundle', expected_manifest_ref=result['manifest_ref'])
    assert checked['status'] == 'VERIFIED_INPUTS_NOT_QUALIFIED'
    assert checked['physical_qualification'] == 'NOT_ESTABLISHED'
    assert checked['executes_application'] is False
    lock = (tmp_path/'bundle'/'requirements.txt').read_text()
    assert '--hash=sha256:' in lock and '==' in lock and 'https:' not in lock


@pytest.mark.parametrize('profile,extra,count', [('link', ('net-offline-crypto',),3),
        ('qualification', ('net-offline-crypto','net-offline-test'),4)])
def test_requested_extras_only_and_unused_direct_url_is_not_resolved(project,tmp_path,profile,extra,count):
    for name in extra: put_wheel(project[1], name)
    result = prepare(project, tmp_path/'bundle', profile)
    assert result['package_count']==count
    assert 'network-payload' not in (tmp_path/'bundle'/'requirements.txt').read_text()


def test_transitive_ranges_are_resolved_from_locked_local_wheels(project,tmp_path):
    path,wheels,root=project
    put_wheel(wheels,'net-offline-dependency',requirements=('net-offline-leaf>=1.0,<2',))
    put_wheel(wheels,'net-offline-leaf','1.5')
    result=prepare(project,tmp_path/'bundle')
    assert result['package_count']==3
    assert 'net-offline-leaf==1.5 --hash=' in (tmp_path/'bundle'/'requirements.txt').read_text()


@pytest.mark.parametrize('kind',['missing','wrong_version','extra_wheel','duplicate_version','direct_url',
                                  'wrong_project_pin','metadata_project_disagrees'])
def test_incomplete_or_ambiguous_bundle_refuses_before_destination(project,tmp_path,kind):
    path,wheels,root=project
    if kind=='missing': (wheels/'net_offline_dependency-1.0-py3-none-any.whl').unlink()
    elif kind=='wrong_version':
        (wheels/'net_offline_dependency-1.0-py3-none-any.whl').unlink(); put_wheel(wheels,'net-offline-dependency','2.0')
    elif kind=='extra_wheel': put_wheel(wheels,'unselected-package')
    elif kind=='duplicate_version': put_wheel(wheels,'net-offline-dependency','2.0')
    elif kind=='direct_url': put_wheel(wheels,'net-offline-dependency',requirements=('evil @ https://invalid.example/payload.whl',))
    elif kind=='wrong_project_pin': path.write_bytes(path.read_bytes().replace(b'dependency==1.0',b'dependency>=1.0'))
    else: put_wheel(wheels,'net-offline-fixture', requirements=())
    with pytest.raises(ValueError): prepare(project,tmp_path/'blocked')
    assert not (tmp_path/'blocked').exists()


@pytest.mark.parametrize('kind',['project','root'])
def test_independent_selection_hashes_required_before_bundle_creation(project,tmp_path,kind):
    path,wheels,root=project
    args={'expected_project_ref':ref(path.read_bytes()),'expected_root_ref':ref(root.read_bytes()),'profile':'runtime'}
    args['expected_'+kind+'_ref']='sha256:'+'0'*64
    with pytest.raises(ValueError): offline.prepare(path,wheels,tmp_path/'blocked',**args)
    assert not (tmp_path/'blocked').exists()


@pytest.mark.parametrize('unsafe',['../escape.py','/absolute.py','pkg\\escape.py','x:y.py',
   'inject.pth','sitecustomize.py','usercustomize.py','pkg.data/purelib/payload.py', 'pip/__init__.py'])
def test_unsafe_archive_member_refuses_without_import_or_unpack(project,tmp_path,unsafe):
    put_wheel(project[1],'net-offline-dependency',files={unsafe:b'raise RuntimeError("must not run")\n'})
    with pytest.raises(ValueError): prepare(project,tmp_path/'blocked')
    assert not (tmp_path/'blocked').exists()


def test_changed_wheel_record_refuses(project,tmp_path):
    put_wheel(project[1],'net-offline-dependency',record_fault=True)
    with pytest.raises(ValueError, match='RECORD'): prepare(project,tmp_path/'blocked')
    assert not (tmp_path/'blocked').exists()


def test_duplicate_zip_members_refuse(project,tmp_path):
    path=project[1]/'net_offline_dependency-1.0-py3-none-any.whl'
    with zipfile.ZipFile(path,'a') as z:
        with pytest.warns(UserWarning): z.writestr('net_offline_dependency/__init__.py',b'VALUE=99')
    with pytest.raises(ValueError): prepare(project,tmp_path/'blocked')


def test_two_distributions_cannot_write_the_same_installed_file(project,tmp_path):
    put_wheel(project[1],'net-offline-dependency',files={'net_offline_fixture/__init__.py':b'VALUE=99'})
    with pytest.raises(ValueError,match='overlap|owner'): prepare(project,tmp_path/'blocked')


def test_pip_script_replacement_is_rejected(project,tmp_path):
    put_wheel(project[1],'net-offline-dependency',files={
      'net_offline_dependency/__init__.py': b'VALUE=42',
      'net_offline_dependency-1.0.dist-info/entry_points.txt':b'[console_scripts]\npip = net_offline_dependency:main\n'})
    with pytest.raises(ValueError): prepare(project,tmp_path/'blocked')


@pytest.mark.parametrize('change',['wheel','project','lock','manifest','extra_file','symlink'])
def test_bundle_tampering_refuses_before_environment_creation(project,tmp_path,change):
    result=prepare(project,tmp_path/'bundle');b=tmp_path/'bundle'
    if change=='wheel': next((b/'wheels').iterdir()).write_bytes(b'not a wheel')
    elif change=='project': (b/'pyproject.toml').write_bytes(b'[project]\nname="other"')
    elif change=='lock': (b/'requirements.txt').write_bytes(b'--index-url https://invalid.example\n')
    elif change=='manifest': (b/'manifest.json').write_bytes(b'{}')
    elif change=='extra_file': (b/'unexpected').write_bytes(b'data')
    else:
        p=next((b/'wheels').iterdir());copy=tmp_path/'outside.whl';copy.write_bytes(p.read_bytes());p.unlink();p.symlink_to(copy)
    with pytest.raises((ValueError,OSError)):
        offline.provision(b,tmp_path/'env',expected_manifest_ref=result['manifest_ref'],allow_install=True)
    assert not (tmp_path/'env').exists()


def test_no_install_permission_means_no_reads_or_writes(tmp_path,monkeypatch):
    monkeypatch.setattr(offline,'inspect_bundle',lambda *a,**k:pytest.fail('read before permission'))
    with pytest.raises(PermissionError): offline.provision('absent',tmp_path/'blocked',expected_manifest_ref='sha256:'+'0'*64)
    assert not (tmp_path/'blocked').exists()


def test_real_pip_offline_install_uses_snapshot_not_host_packages(project,tmp_path,monkeypatch):
    prepared=prepare(project,tmp_path/'bundle'); before=snapshot(tmp_path/'bundle')
    monkeypatch.setenv('PIP_TARGET',str(tmp_path/'outside'))
    monkeypatch.setenv('PIP_INDEX_URL','https://invalid.example/credentials-must-not-be-logged')
    monkeypatch.setenv('PYTHONPATH',str(tmp_path/'host-injection'))
    result=offline.provision(tmp_path/'bundle',tmp_path/'deployment',expected_manifest_ref=prepared['manifest_ref'],allow_install=True)
    assert result['status']=='PROVISIONED_NOT_QUALIFIED'
    assert result['installed_versions']['net-offline-fixture']=='1.0'
    assert result['installed_versions']['net-offline-dependency']=='1.0'
    assert result['installed_file_count']>=6
    assert result['scientific_tests_run'] is False and result['physical_qualification']=='NOT_ESTABLISHED'
    assert (tmp_path/'deployment/provisioning.json').is_file()
    assert not (tmp_path/'outside').exists() and before==snapshot(tmp_path/'bundle')
    python=tmp_path/'deployment/env/bin/python'
    proc=subprocess.run([str(python),'-I','-c','import net_offline_fixture;print(net_offline_fixture.VALUE)'],text=True,capture_output=True,timeout=20)
    assert proc.returncode==0 and proc.stdout.strip()=='42',proc.stderr
    for log in (tmp_path/'deployment').glob('*.log'):
        assert 'credentials-must-not-be-logged' not in log.read_text()
    with pytest.raises(FileExistsError): offline.provision(tmp_path/'bundle',tmp_path/'deployment',expected_manifest_ref=prepared['manifest_ref'],allow_install=True)


def test_install_failure_leaves_no_success_receipt_and_preserves_input(project,tmp_path,monkeypatch):
    result=prepare(project,tmp_path/'bundle');before=snapshot(tmp_path/'bundle')
    monkeypatch.setattr(offline,'_run',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('injected execution failure')))
    with pytest.raises(RuntimeError): offline.provision(tmp_path/'bundle',tmp_path/'deployment',expected_manifest_ref=result['manifest_ref'],allow_install=True)
    assert not (tmp_path/'deployment/provisioning.json').exists()
    assert before==snapshot(tmp_path/'bundle')


def test_current_target_mismatch_refuses(project,tmp_path,monkeypatch):
    result=prepare(project,tmp_path/'bundle');old=offline.target()
    monkeypatch.setattr(offline,'target',lambda:{**old,'platform':'incompatible-platform'})
    with pytest.raises(ValueError): offline.provision(tmp_path/'bundle',tmp_path/'blocked',expected_manifest_ref=result['manifest_ref'],allow_install=True)
    assert not (tmp_path/'blocked').exists()


def test_prepare_and_inspect_cli_have_distinct_statuses(project,tmp_path,capsys):
    path,wheels,root=project
    assert offline.main(['prepare','--project',str(path),'--project-ref',ref(path.read_bytes()),
      '--wheelhouse',str(wheels),'--root-ref',ref(root.read_bytes()),'--profile','runtime','--output',str(tmp_path/'bundle')])==0
    value=json.loads(capsys.readouterr().out)
    assert offline.main(['inspect',str(tmp_path/'bundle'),'--manifest-ref',value['manifest_ref']])==0
    assert json.loads(capsys.readouterr().out)['status']=='VERIFIED_INPUTS_NOT_QUALIFIED'
    assert offline.main(['provision',str(tmp_path/'bundle'),'--manifest-ref',value['manifest_ref'],'--output',str(tmp_path/'blocked')])==2
    assert not (tmp_path/'blocked').exists()


def test_empty_typed_marker_is_preserved_during_real_install(project,tmp_path):
    put_wheel(project[1],'net-offline-dependency',files={
       'net_offline_dependency/__init__.py':b'VALUE=42\n', 'net_offline_dependency/py.typed':b''})
    result=prepare(project,tmp_path/'bundle')
    done=offline.provision(tmp_path/'bundle',tmp_path/'deployment',expected_manifest_ref=result['manifest_ref'],allow_install=True)
    assert done['status']=='PROVISIONED_NOT_QUALIFIED'
    markers = list((tmp_path/'deployment/env').rglob('net_offline_dependency/py.typed'))
    assert len(markers) == 1
    assert markers[0].read_bytes() == b''


def test_source_replacement_after_freeze_cannot_change_installed_bytes(project,tmp_path,monkeypatch):
    result=prepare(project,tmp_path/'bundle');real_run=offline._run
    def swap(command,root,name,**kw):
        if name=='bootstrap':
            source=next((tmp_path/'bundle/wheels').iterdir())
            source.write_bytes(b'changed after the actual validated read')
        return real_run(command,root,name,**kw)
    monkeypatch.setattr(offline,'_run',swap)
    done=offline.provision(tmp_path/'bundle',tmp_path/'deployment',expected_manifest_ref=result['manifest_ref'],allow_install=True)
    assert done['status']=='PROVISIONED_NOT_QUALIFIED'
    assert offline.inspect_bundle(tmp_path/'deployment/inputs',expected_manifest_ref=result['manifest_ref'])['package_count']==2
    with pytest.raises(ValueError): offline.inspect_bundle(tmp_path/'bundle',expected_manifest_ref=result['manifest_ref'])


def test_entry_point_cannot_replace_virtualenv_activation(project,tmp_path):
    put_wheel(project[1],'net-offline-dependency',files={
      'net_offline_dependency/__init__.py':b'VALUE=42',
      'net_offline_dependency-1.0.dist-info/entry_points.txt':b'[console_scripts]\nactivate = net_offline_dependency:main\n'})
    result=prepare(project,tmp_path/'bundle')
    with pytest.raises(ValueError,match='bootstrap|existing'):
        offline.provision(tmp_path/'bundle',tmp_path/'deployment',expected_manifest_ref=result['manifest_ref'],allow_install=True)
    assert not (tmp_path/'deployment/offline-install.log').exists()
    assert not (tmp_path/'deployment/provisioning.json').exists()


def test_signed_or_executable_zip_links_are_not_ordinary_directories(project,tmp_path):
    path=project[1]/'net_offline_dependency-1.0-py3-none-any.whl'
    with zipfile.ZipFile(path,'a') as z:
        entry=zipfile.ZipInfo('sneaky/')
        entry.create_system=3;entry.external_attr=(0o120777 << 16)
        z.writestr(entry,b'outside')
    with pytest.raises(ValueError): prepare(project,tmp_path/'blocked')


def test_project_and_snapshot_are_never_inside_install_destination(project,tmp_path):
    result=prepare(project,tmp_path/'bundle')
    with pytest.raises(ValueError): offline.provision(tmp_path/'bundle',tmp_path/'bundle/env',
        expected_manifest_ref=result['manifest_ref'],allow_install=True)
    assert not (tmp_path/'bundle/env').exists()


@pytest.mark.parametrize('budget',['MAX_WHEEL','MAX_TOTAL','MAX_EXPANDED','MAX_MEMBERS'])
def test_archive_budgets_are_not_bypassed_by_small_compressed_payload(project,tmp_path,monkeypatch,budget):
    monkeypatch.setattr(offline,budget,1)
    with pytest.raises(ValueError): prepare(project,tmp_path/'blocked')
    assert not (tmp_path/'blocked').exists()


def test_unselected_optional_extra_does_not_expand_trust(project,tmp_path):
    with pytest.raises(ValueError): prepare(project,tmp_path/'blocked','unused')
    assert not (tmp_path/'blocked').exists()


def test_no_provisioning_from_forged_dependency_rows(project,tmp_path):
    prepared=prepare(project,tmp_path/'bundle');p=tmp_path/'bundle/manifest.json'
    obj=json.loads(p.read_bytes());obj['wheels'][0]['version']='99.0'
    raw=json.dumps(obj).encode();p.write_bytes(raw)
    with pytest.raises(ValueError): offline.inspect_bundle(tmp_path/'bundle',expected_manifest_ref=ref(raw))


def test_manifest_duplicate_fields_never_define_install_selection(project,tmp_path):
    prepared=prepare(project,tmp_path/'bundle');p=tmp_path/'bundle/manifest.json'
    raw=p.read_bytes();bad=raw[:-2]+b',"profile":"runtime"}\n';p.write_bytes(bad)
    with pytest.raises(ValueError): offline.inspect_bundle(tmp_path/'bundle',expected_manifest_ref=ref(bad))


def test_actual_provisioning_process_deadline_reaps_its_child(tmp_path):
    with pytest.raises(RuntimeError,match='deadline'):
        offline._run([sys.executable,'-c','import time;time.sleep(60)'],tmp_path,'bounded-child',timeout=0.1)
    assert (tmp_path/'bounded-child.log').is_file()


def test_malformed_dependency_cannot_echo_embedded_credential(project,tmp_path,capsys):
    put_wheel(project[1],'net-offline-dependency',requirements=(
        'private @ https://name:synthetic-secret@invalid.example/bad.whl; extra',))
    path,wheels,root=project
    assert offline.main(['prepare','--project',str(path),'--project-ref',ref(path.read_bytes()),
      '--root-ref',ref(root.read_bytes()),'--wheelhouse',str(wheels),'--profile','runtime','--output',str(tmp_path/'blocked')])==2
    assert 'synthetic-secret' not in capsys.readouterr().err


# Deployment guards: synthetic packages and real bounded subprocesses only.
@pytest.mark.parametrize('member', [
    'sitecustomize/__init__.py', 'usercustomize/__init__.py',
    'sitecustomize/support.py', 'sitecustomize.so',
    'sitecustomize.abi3.so', 'usercustomize.abi3.so',
    'sitecustomize.cpython-313-x86_64-linux-gnu.so', 'pip.abi3.so',
])
def test_reserved_startup_import_roots_refuse_before_bundle_creation(project, tmp_path, member):
    put_wheel(project[1], 'net-offline-dependency', files={member: b'# synthetic inert member\n'})
    with pytest.raises(ValueError, match='Startup|bootstrap|reserved'):
        prepare(project, tmp_path/'blocked-startup')
    assert not (tmp_path/'blocked-startup').exists()


@pytest.mark.parametrize('member', [
    'net_offline_dependency/sitecustomize_helpers.py',
    'sitecustomize_notes/__init__.py', 'sitecustomize_notes.py',
])
def test_nonreserved_similar_names_remain_supported(project, tmp_path, member):
    put_wheel(project[1], 'net-offline-dependency', files={member: b'VALUE=42\n'})
    result = prepare(project, tmp_path/'accepted-library')
    assert result['status'] == 'PREPARED_NOT_AUTHORIZED'
    assert result['executes_application'] is False


def test_previously_prepared_startup_bundle_is_rejected_before_provision(project, tmp_path, monkeypatch):
    # Reproduce the prior member-check omission only while constructing old input.
    # The real new reader/provisioner then rechecks the original wheel and refuses.
    from pathlib import PurePosixPath
    put_wheel(project[1], 'net-offline-dependency', files={
        'sitecustomize/__init__.py': b'raise RuntimeError("must not import")\n'})
    with monkeypatch.context() as old_validator:
        old_validator.setattr(offline, '_member', lambda name: PurePosixPath(name))
        result = prepare(project, tmp_path/'old-bundle')
    before = snapshot(tmp_path/'old-bundle')
    with pytest.raises(ValueError, match='Startup|bootstrap|reserved'):
        offline.provision(tmp_path/'old-bundle', tmp_path/'not-installed',
            expected_manifest_ref=result['manifest_ref'], allow_install=True)
    assert not (tmp_path/'not-installed').exists()
    assert snapshot(tmp_path/'old-bundle') == before


@pytest.mark.parametrize('fd', [1, 2])
def test_command_output_limit_is_enforced_while_reading(tmp_path, monkeypatch, fd):
    cap = 65536
    monkeypatch.setattr(offline, 'MAX_COMMAND_OUTPUT', cap, raising=False)
    command = [sys.executable, '-I', '-c', f'import os;os.write({fd},b"X"*{cap+1})']
    with pytest.raises(RuntimeError, match='output.*limit'):
        offline._run(command, tmp_path, 'excess-output', timeout=5)
    assert (tmp_path/'excess-output.log').read_bytes() == b'X'*cap


@pytest.mark.parametrize('size', [0, 1, 65535, 65536])
def test_bounded_command_output_is_exact_including_empty(tmp_path, monkeypatch, size):
    monkeypatch.setattr(offline, 'MAX_COMMAND_OUTPUT', 65536, raising=False)
    command = [sys.executable, '-I', '-c', f'import os;os.write(1,b"Y"*{size})']
    result = offline._run(command, tmp_path, 'bounded-output', timeout=5)
    assert result == b'Y'*size
    assert (tmp_path/'bounded-output.log').read_bytes() == result


def test_runtime_default_output_ceiling_is_enforced(tmp_path):
    cap = 8 * 1024 * 1024
    with pytest.raises(RuntimeError, match='output.*limit'):
        offline._run([sys.executable, '-I', '-c',
                      f'import os;os.write(1,b"Z"*{cap+1})'], tmp_path, 'default-limit', timeout=5)
    assert (tmp_path/'default-limit.log').stat().st_size == cap


def test_installer_output_failure_keeps_original_bundle_and_no_success_receipt(project, tmp_path, monkeypatch):
    prepared = prepare(project, tmp_path/'bundle')
    before = snapshot(tmp_path/'bundle')
    actual = offline._run
    monkeypatch.setattr(offline, 'MAX_COMMAND_OUTPUT', 65536, raising=False)
    def noisy_install(command, root, name, **kw):
        if name == 'offline-install':
            # A real, failing child replaces pip only for this failure-path probe.
            command = [sys.executable, '-I', '-c', 'import os;os.write(1,b"X"*65537)']
        return actual(command, root, name, **kw)
    monkeypatch.setattr(offline, '_run', noisy_install)
    with pytest.raises(RuntimeError, match='output.*limit'):
        offline.provision(tmp_path/'bundle', tmp_path/'partial',
            expected_manifest_ref=prepared['manifest_ref'], allow_install=True)
    assert not (tmp_path/'partial/provisioning.json').exists()
    assert (tmp_path/'partial/offline-install.log').stat().st_size == 65536
    assert snapshot(tmp_path/'bundle') == before


def test_timeout_covers_inherited_pipe_after_parent_exits(tmp_path):
    import signal
    import time
    child = None
    code = ('import os,subprocess,sys\n'
            'p=subprocess.Popen([sys.executable, "-I", "-c", "import time;time.sleep(30)"])\n'
            'print("owned-child-pid="+str(p.pid),flush=True)\nos._exit(0)\n')
    try:
        with pytest.raises(RuntimeError, match='deadline'):
            offline._run([sys.executable, '-I', '-c', code], tmp_path, 'descendant', timeout=5.0)
        pid_lines = [line for line in (tmp_path/'descendant.log').read_text().splitlines()
                     if line.startswith('owned-child-pid=')]
        assert len(pid_lines) == 1
        child = int(pid_lines[0].split('=', 1)[1])
        # A killed orphan may be waiting for PID 1 to reap it; a zombie is not live.
        for _ in range(100):
            status = Path(f'/proc/{child}/stat')
            if not status.exists() or status.read_text().split()[2] == 'Z':
                break
            time.sleep(0.01)
        else:
            pytest.fail('Owned descendant still running after command deadline')
    finally:
        # Test-only cleanup also prevents the old-implementation reproduction
        # from leaving its harmless sleeping fixture process behind.
        log = tmp_path/'descendant.log'
        if child is None and log.exists():
            pid_lines = [line for line in log.read_text().splitlines()
                         if line.startswith('owned-child-pid=')]
            if len(pid_lines) == 1:
                child = int(pid_lines[0].split('=', 1)[1])
        if child is not None:
            try: os.kill(child, signal.SIGKILL)
            except ProcessLookupError: pass


def test_deadline_waits_for_child_even_when_child_closes_output(tmp_path):
    code = 'import os,time;os.close(1);os.close(2);time.sleep(30)'
    with pytest.raises(RuntimeError, match='deadline'):
        offline._run([sys.executable, '-I', '-c', code], tmp_path, 'closed-pipe', timeout=0.2)
    assert (tmp_path/'closed-pipe.log').read_bytes() == b''


def test_command_failure_keeps_original_output_and_exit(tmp_path):
    with pytest.raises(RuntimeError, match='exit=7'):
        offline._run([sys.executable, '-I', '-c',
            'import os;os.write(1,b"diagnostic\\x00\\xff");os._exit(7)'], tmp_path, 'failed-child', timeout=5)
    assert (tmp_path/'failed-child.log').read_bytes() == b'diagnostic\x00\xff'


@pytest.mark.parametrize('timeout', [True, 0, -1, float('nan'), float('inf'), '1'])
def test_invalid_command_deadline_refuses_before_start(tmp_path, monkeypatch, timeout):
    monkeypatch.setattr(offline.subprocess, 'Popen', lambda *a, **k: pytest.fail('spawned invalid deadline'))
    with pytest.raises(ValueError, match='deadline'):
        offline._run([sys.executable, '-c', 'pass'], tmp_path, 'invalid-deadline', timeout=timeout)
    assert not (tmp_path/'invalid-deadline.log').exists()


def test_monitor_cancellation_reaps_the_real_child(tmp_path, monkeypatch):
    actual = subprocess.Popen
    children = []
    def start(*a, **kw):
        proc = actual(*a, **kw); children.append(proc); return proc
    def cancelled(*a, **kw):
        raise KeyboardInterrupt('synthetic monitor cancellation')
    monkeypatch.setattr(offline.subprocess, 'Popen', start)
    monkeypatch.setattr(offline.select, 'select', cancelled)
    with pytest.raises(KeyboardInterrupt):
        offline._run([sys.executable, '-I', '-c', 'import time;time.sleep(30)'],
                     tmp_path, 'cancelled', timeout=5)
    assert len(children) == 1 and children[0].poll() is not None


def test_log_creation_is_private_and_never_overwrites(tmp_path):
    import stat
    command = [sys.executable, '-I', '-c', 'print("local diagnostic")']
    offline._run(command, tmp_path, 'private', timeout=5)
    path = tmp_path/'private.log'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.read_bytes()
    with pytest.raises(FileExistsError): offline._run(command, tmp_path, 'private', timeout=5)
    assert path.read_bytes() == before
