"""Independent containment/admission regressions with local synthetic canaries only.

No permissive policy subclass, fake namespace success or companion bypass is used.
The strict positive is explicitly skipped when the real namespace probe refuses.
"""
from copy import deepcopy
from hashlib import sha256
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys

import pytest

from ciw import services
from ciw.adapters import subprocess as runtime
from ciw.adapters.nvalchemi import NvalchemiLJBinding
from ciw.clocksync_profile import _ClockAdapter
from ciw.nise_profile import _NiseAdapter
from ciw.schematic_profile import _BoundedAdapter
from ciw.process_policy import ProcessPolicy, ProcessPolicyHeld, executable_sha256


CANARY = 'ODSP_REVIEW_SYNTHETIC_CANARY'
VALUE = 'public-review-canary-no-secret'
MARKER = b'ODSP-CONTAINMENT-REVIEW\n'


def limits(**kwargs):
    return ProcessPolicy(expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'), **kwargs)


def strict():
    if not Path('/usr/bin/bwrap').is_file():
        pytest.skip('Actual Bubblewrap executable missing; strict qualification is blocked')
    roots = {'/usr', str(Path(sys.executable).resolve().parents[1]), str(Path(sys.prefix).resolve())}
    return limits(mode='bubblewrap', read_only_roots=tuple(sorted(roots)),
                  expected_bwrap_sha256=executable_sha256('/usr/bin/bwrap'))


def child(tmp_path, code, **kwargs):
    return runtime._bounded_process([sys.executable, '-I', '-c', code],
        cwd=tmp_path, timeout=5, limit=16384, **kwargs)


def test_default_environment_is_fixed_and_each_call_detached(tmp_path, monkeypatch):
    monkeypatch.setenv(CANARY, VALUE)
    monkeypatch.setenv('HTTP_PROXY', 'http://synthetic.invalid:1')
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'credential.helper')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'synthetic-not-executed')
    code, raw = child(tmp_path, 'import os,json;print(json.dumps(dict(os.environ)))')
    assert code == 0
    assert json.loads(raw) == {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'LC_ALL':'C.UTF-8',
                              'OPENBLAS_NUM_THREADS':'1', 'OMP_NUM_THREADS':'1'}
    from ciw.process_policy import minimal_environment
    copied = minimal_environment()
    copied[CANARY] = VALUE
    assert CANARY not in minimal_environment()


def test_explicit_environment_filters_injection_and_preserves_only_host_values(tmp_path, monkeypatch):
    monkeypatch.setenv(CANARY, VALUE)
    selected = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'LC_ALL':'C.UTF-8',
                'ODSP_REVIEW_EXPLICIT':'intentional-host-value',
                **{k:'synthetic-never-loaded' for k in (
                    'LD_PRELOAD','LD_AUDIT','PYTHONPATH','PYTHONHOME','NODE_OPTIONS','NODE_PATH')}}
    before = dict(selected)
    code, raw = child(tmp_path, 'import os,json;print(json.dumps(dict(os.environ)))', environment=selected)
    assert code == 0 and selected == before
    assert json.loads(raw) == {k:v for k,v in selected.items() if k in (
        'PATH','LANG','LC_ALL','ODSP_REVIEW_EXPLICIT')}


def test_limits_host_helper_is_sanitized_but_remains_network_capable(tmp_path, monkeypatch):
    monkeypatch.setenv(CANARY, VALUE)
    selected = limits(cpu_seconds=2, memory_bytes=256*1024*1024,
                      file_bytes=8192, max_open_files=40, max_processes=512)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(2)
        port = listener.getsockname()[1]
        code, raw = child(tmp_path, f'''import json,os,resource,socket
with socket.create_connection(('127.0.0.1',{port}),timeout=1) as s: s.sendall({MARKER!r})
print(json.dumps({{'canary':{CANARY!r} in os.environ,'cpu':resource.getrlimit(resource.RLIMIT_CPU)}}))
''', process_policy=selected)
        peer, _ = listener.accept()
        with peer:
            peer.settimeout(2)
            assert peer.recv(100) == MARKER
    assert code == 0 and json.loads(raw) == {'canary':False, 'cpu':[2,2]}
    assert selected.qualify(tmp_path)['network_isolated'] is False


@pytest.mark.parametrize('kind', ['base','schematic','clock','nise','nvalchemi'])
def test_companion_constructor_requires_isolation_before_runtime_or_file_load(tmp_path, monkeypatch, kind):
    def forbidden(*a, **k):
        raise AssertionError('A missing/limits-only companion policy reached a process launch')
    monkeypatch.setattr(runtime.subprocess, 'Popen', forbidden)
    # No source fixture is needed: an isolating policy must be checked first.
    for selected in (None, limits()):
        with pytest.raises(ProcessPolicyHeld, match='companion_isolation_required'):
            if kind == 'nvalchemi':
                NvalchemiLJBinding(tmp_path/'missing-lock', '0'*40,
                    python_executable=sys.executable, process_policy=selected)
            elif kind == 'schematic':
                _BoundedAdapter(tmp_path/'missing-src', sys.executable, process_policy=selected)
            else:
                cls = {'base':runtime.PinnedSubprocessAdapter, 'clock':_ClockAdapter, 'nise':_NiseAdapter}[kind]
                cls(tmp_path/'missing-src', '0'*40, 'example.endpoint', process_policy=selected)


@pytest.mark.parametrize('cls', [runtime.PinnedSubprocessAdapter, _BoundedAdapter, _ClockAdapter, _NiseAdapter,
                               NvalchemiLJBinding])
@pytest.mark.parametrize('method', ['_run','_git'])
def test_every_launch_override_rechecks_policy_after_host_rebind(tmp_path, monkeypatch, cls, method):
    def forbidden(*a, **k):
        raise AssertionError('Invalid rebound policy reached a process launch')
    monkeypatch.setattr(runtime.subprocess, 'Popen', forbidden)
    # Construct only host state for a negative prelaunch boundary check. Nothing
    # fabricates successful source qualification or enables target execution.
    binding = cls.__new__(cls)
    binding._cleanup_uncertain = None
    binding.repository_root = tmp_path
    binding.source_root = tmp_path
    binding.python_executable = Path(sys.executable)
    binding.timeout_seconds = 1
    binding.max_output_bytes = 4096
    binding.deadline = None
    for selected in (None, limits()):
        binding.process_policy = selected
        with pytest.raises(ProcessPolicyHeld, match='companion_isolation_required'):
            if method == '_git':
                binding._git('status', '--porcelain')
            else:
                binding._run("raise AssertionError('must never execute')", [])


@pytest.mark.parametrize('cls', [runtime.PinnedSubprocessAdapter, _BoundedAdapter, _ClockAdapter, _NiseAdapter,
                               NvalchemiLJBinding])
@pytest.mark.parametrize('method', ['_run','_git'])
def test_strict_policy_is_forwarded_to_real_pin_check_on_each_override(tmp_path, monkeypatch, cls, method):
    from dataclasses import replace
    selected = replace(strict(), expected_prlimit_sha256='0'*64)
    def forbidden(*a, **k):
        raise AssertionError('A changed launcher pin reached Popen; policy was omitted or bypassed')
    monkeypatch.setattr(runtime.subprocess, 'Popen', forbidden)
    binding = cls.__new__(cls)
    binding._cleanup_uncertain = None
    binding.repository_root = binding.source_root = tmp_path
    binding.python_executable = Path(sys.executable)
    binding.timeout_seconds = 1
    binding.max_output_bytes = 4096
    binding.deadline = None
    binding.process_policy = selected
    if cls is NvalchemiLJBinding:
        # Real filesystem fingerprinting is preserved. The intentionally wrong
        # launcher pin must refuse before this synthetic module could execute.
        binding._site_packages = tmp_path/'synthetic-site-packages'
        binding._site_packages.mkdir()
        (binding._site_packages/'review.py').write_text('# Synthetic local test data only\n')
        binding._provider_files = binding._installed_files()
    with pytest.raises(ProcessPolicyHeld, match='pinned_launcher_changed'):
        if method == '_git':
            binding._git('status', '--porcelain')
        else:
            binding._run("raise AssertionError('must never execute')", [])


def test_real_namespace_failure_does_not_launch_requested_target(tmp_path, record_property):
    selected = strict()
    marker = tmp_path/'target-ran'
    try:
        selected.qualify(tmp_path)
    except ProcessPolicyHeld as exc:
        record_property('actual_namespace_result', exc.reason)
        assert exc.reason == 'namespace_probe_refused'
        with pytest.raises(ProcessPolicyHeld, match='namespace_probe_refused'):
            child(tmp_path, f"open({str(marker)!r},'w').write('synthetic')", process_policy=selected)
        assert not marker.exists()
    else:
        record_property('actual_namespace_result', 'available')
        code, _ = child(tmp_path, f"open({str(marker)!r},'w').write('synthetic')", process_policy=selected)
        assert code == 0 and marker.read_text() == 'synthetic'


def test_real_namespace_positive_network_filesystem_environment(tmp_path, monkeypatch):
    selected = strict()
    try:
        selected.qualify(tmp_path)
    except ProcessPolicyHeld as exc:
        pytest.skip(f'Positive namespace isolation remains unqualified: {exc.reason}')
    monkeypatch.setenv(CANARY, VALUE)
    outside = tmp_path.parent/'synthetic-outside-isolated-workdir'
    outside.write_text('no-secret synthetic fixture')
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(.2)
            port = listener.getsockname()[1]
            code, raw = child(tmp_path, f'''import json,os,socket
connected=False
try:
 s=socket.create_connection(('127.0.0.1',{port}),timeout=.2);s.close();connected=True
except OSError: pass
print(json.dumps({{'connected':connected,'outside':os.path.exists({str(outside)!r}),
 'canary':{CANARY!r} in os.environ,'net':os.readlink('/proc/self/ns/net')}}))
''', process_policy=selected)
            assert code == 0
            result = json.loads(raw)
            assert result['connected'] is result['outside'] is result['canary'] is False
            assert result['net'] != os.readlink('/proc/self/ns/net')
            with pytest.raises(socket.timeout):
                listener.accept()
    finally:
        outside.unlink()


ROUTES = [
    ('domain_read_profile','invoke_domain_read','ncp_domain_read_describe',{'domain':'gis'}),
    ('offline_intake','invoke_intake','ncp_intake_describe',{}),
    ('proof_profile','invoke_proof','ncp_proof_describe',{}),
    ('nise_profile','invoke_nise','ncp_nise_describe',{}),
    ('clocksync_profile','invoke_clocksync','ncp_clocksync_describe',{}),
    ('schematic_profile','invoke_schematic','ncp_schematic_describe',{}),
    ('recompiler_profile','invoke_recompiler','ncp_recompiler_describe',{}),
    ('state_read_profile','invoke_state','ncp_state_releases',{}),
    ('logistics_profile','invoke_logistics','ncp_logistics_catalog',{}),
]


@pytest.mark.parametrize('module,helper,name,valid', ROUTES)
def test_all_profile_routes_share_admission_before_helper(module, helper, name, valid, monkeypatch):
    profile = importlib.import_module('ciw.'+module)
    seen = []
    real = services.admit
    def admit(n, a):
        real(n, a)
        seen.append(('admitted', n, deepcopy(a)))
    def called(h, n, a):
        seen.append(('profile', n, deepcopy(a)))
        return 'local-routing-sentinel'
    monkeypatch.setattr(services, 'admit', admit)
    monkeypatch.setattr(profile, helper, called)
    assert services.invoke(name, deepcopy(valid), host=object()) == 'local-routing-sentinel'
    assert seen == [('admitted', name, valid), ('profile', name, valid)]
    for invalid in ([], {**valid,'principal':'someone-else'}, {**valid,'checkout':'/tmp/other'},
                    {**valid,'unexpected':True}, {**valid,'unexpected':'x'*65537}):
        seen.clear()
        with pytest.raises((ValueError, TypeError)):
            services.invoke(name, invalid, host=object())
        assert seen == []


def test_real_local_logistics_read_admits_and_revoked_facade_still_refuses(tmp_path, monkeypatch):
    if not shutil.which('node'):
        pytest.skip('Existing local OLM digest fixture requires Node')
    from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
    from logistics_fixture_helpers import Fixture, host
    monkeypatch.setenv('NCP_RETAIN_DIR', str(tmp_path/'retained'))
    transport = Fixture()
    raw = host(transport, principal='review-principal')
    seen = []
    real = services.admit
    def admit(n, a):
        real(n, a); seen.append((n, deepcopy(a)))
    monkeypatch.setattr(services, 'admit', admit)
    result = services.invoke('ncp_logistics_catalog', {}, host=raw)
    assert seen == [('ncp_logistics_catalog', {})] and len(transport.calls) == 1
    assert result['synthetic'] is True and result['authorizes_execution'] is False
    assert raw.store.native(result['artifact_id'])
    before_files = {str(p):sha256(p.read_bytes()).hexdigest() for p in (tmp_path/'retained').rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        services.invoke('ncp_logistics_catalog', {'principal':'other'}, host=raw)
    assert len(transport.calls) == 1
    assert {str(p):sha256(p.read_bytes()).hexdigest() for p in (tmp_path/'retained').rglob('*') if p.is_file()} == before_files
    grant = {'principal':'review-principal','enabled':True,
             'operations':[{'name':'ncp_logistics_catalog','constraints':{}}],
             'scientific_operations':[],'resources':[],'reference_scopes':[],'handoff_recipients':[]}
    document = {'schema':'ciw.principal-policy.v1','revision':'review-1','principals':[grant]}
    facade = PrincipalPolicy(lambda: document).facade('review-principal', logistics=raw)
    grant['operations'] = []
    with pytest.raises(PrincipalRefusal):
        services.invoke('ncp_logistics_catalog', {}, host=facade)
    assert len(transport.calls) == 1
    from ciw.principal_http import PrincipalTokenVerifier, AuthenticationRefused
    with pytest.raises(AuthenticationRefused):
        PrincipalTokenVerifier(None, None, {'review-principal':raw}, None)
