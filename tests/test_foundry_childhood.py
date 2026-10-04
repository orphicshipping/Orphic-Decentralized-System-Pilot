from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import pytest
from ciw.control_contracts import bytes_ref
from ciw.foundry_childhood import ChildhoodBinding,evaluate_data,policy,bounded_state_equal,source


def remove_test_project(path):
    # A failing regression must not strand its deliberately long cache tree.
    assert path.resolve().parent == Path(tempfile.gettempdir()).resolve()
    assert path.name.startswith('1792-childhood-foundry-')
    target = '\\\\?\\' + str(path) if os.name == 'nt' else str(path)
    if Path(target).exists(): shutil.rmtree(target)

@pytest.fixture
def captured():
    # Retained native observation is data for verifier tests, not a new execution.
    return json.loads((Path(__file__).parent/'fixtures/childhood_native_capture.json').read_text())

def test_retained_native_capture(captured):
    assert evaluate_data(captured)['status']=='PASS'
    assert captured['capture_class']=='actual_input_driven_journey'

@pytest.mark.parametrize('fault',['future','cause','covariance','identity','reload','hidden','delivery','order','travel','clock','historical','probability'])
def test_verifier_rejects_retained_counterexamples(captured,fault):
    d=deepcopy(captured)
    if fault=='future': d['samples'][2]['memories'][1]['received_tick']=100000
    if fault=='cause': d['samples'][3]['perspective']['cause_identity']='invented-enemy'
    if fault=='covariance': d['samples'][3]['perspective']['weights']=[0,1]
    if fault=='identity': d['samples'][4]['state']['player']['character_id']='another'
    if fault=='reload': d['samples'][12]['state']['childhood']['tracks']=2
    if fault=='hidden': d['samples'][0]['memories']=[{'id':'secret'}]
    if fault=='delivery': d['samples'][2]['state']['childhood']['heard']=[]
    if fault=='order': d['samples'].reverse()
    if fault=='travel': d['samples'][4]['state']['childhood']['ride_gate']=0
    if fault=='clock': d['samples'][4]['state']['childhood']['tick']=True
    if fault=='historical': d['historical_authentication']=True
    if fault=='probability': d['samples'][3]['perspective']['calibrated_probability']=True
    d['failures']=0;d['assertions']=999999
    assert evaluate_data(d)['status']=='FAIL'

def test_policy_cannot_weaken():
    with pytest.raises(ValueError):policy({'ignore_knowledge':True})

def test_numeric_json_bound_does_not_erase_identity():
    assert bounded_state_equal({'x':0.1},{'x':0.1+1e-15})
    assert not bounded_state_equal({'tick':1},{'tick':True})
    assert not bounded_state_equal({'id':'a'},{'id':'b'})
    assert not bounded_state_equal({'x':0.1},{'x':0.1+1e-6})


@pytest.mark.parametrize('newline', [b'\n', b'\r\n'], ids=['LF', 'CRLF'])
@pytest.mark.parametrize('mutation', [None, 'project.godot', 'foundry/childhood_slice.gd'],
                         ids=['unchanged', 'project-mutated', 'script-mutated'])
@pytest.mark.parametrize('long_cache', [False, True], ids=['ordinary-cache', 'long-cache'])
def test_native_binding_preserves_project_bytes_and_detects_mutation(tmp_path, monkeypatch, newline, mutation, long_cache):
    game_root = tmp_path / 'source'
    game = game_root / 'game'
    (game / 'foundry').mkdir(parents=True)
    original_project = newline.join([
        '; UTF-8 comment: \u00e9'.encode('utf-8'), b'[application]', b'config/name="1792"', b'',
    ])
    (game / 'project.godot').write_bytes(original_project)
    (game / 'foundry/childhood_slice.gd').write_bytes(b'extends SceneTree\n')
    executable = tmp_path / 'godot'
    executable.write_bytes(b'fake engine for adapter test')
    binding = ChildhoodBinding(executable, game_root, bytes_ref(executable.read_bytes()))
    calls = []
    isolated_roots = []
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'keep.txt'
    sentinel.write_bytes(b'outside the temporary project')
    native_run = subprocess.run

    def fake_engine(command, **kwargs):
        calls.append(command)
        isolated_game = Path(command[command.index('--path') + 1])
        assert not str(isolated_game).startswith('\\\\?\\')
        isolated_roots.append(isolated_game.parent)
        if long_cache and '--editor' in command:
            # Godot can create these long editor-cache names even when ordinary
            # Windows Python paths cannot stat or remove the resulting files.
            cache = isolated_game / '.godot/editor' / ('native-cache-' + 'x' * 220 + '.cfg')
            assert len(str(cache)) > 260
            assert len(cache.name.encode('utf-8')) < 255
            extended = Path('\\\\?\\' + str(cache)) if os.name == 'nt' else cache
            extended.parent.mkdir(parents=True)
            extended.write_bytes(b'generated cache')
            # Directory links inside the private tree must be removed as links;
            # cleanup must never traverse into another directory.
            junction = isolated_game.parent / 'outside-junction'
            if os.name == 'nt':
                native_run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
                           check=True, capture_output=True)
            else:
                junction.symlink_to(outside, target_is_directory=True)
        isolated_project = (isolated_game / 'project.godot').read_bytes()
        assert re.sub(rb'config/name="1792-foundry-[0-9a-f]{32}"',
                      b'config/name="1792"', isolated_project) == original_project
        assert b'config/name="1792"' not in isolated_project
        if '--script' in command:
            request = json.loads(Path(command[-2]).read_bytes())
            Path(command[-1]).write_bytes(json.dumps(request).encode('utf-8'))
            if mutation:
                target = isolated_game / mutation
                target.write_bytes(target.read_bytes() + b'; engine changed source\n')
        return subprocess.CompletedProcess(command, 0, 'native test log\n', '')

    monkeypatch.setattr('ciw.foundry_childhood.subprocess.run', fake_engine)
    try:
        if mutation:
            with pytest.raises(ValueError, match='^ENGINE_MUTATED_SOURCE$'):
                binding.invoke(source(binding.lock), {'nonce': 'test-native-byte-preservation'})
        else:
            capture = binding.invoke(source(binding.lock), {'nonce': 'test-native-byte-preservation'})
            assert capture['observations'] == capture['request']
        assert len(calls) == 2
        assert (game / 'project.godot').read_bytes() == original_project
        assert sentinel.read_bytes() == b'outside the temporary project'
        assert all(not path.exists() for path in isolated_roots)
    finally:
        for path in set(isolated_roots): remove_test_project(path)


@pytest.mark.parametrize('body_failure', [False, True], ids=['cleanup-only', 'body-and-cleanup'])
def test_isolated_project_does_not_suppress_cleanup_errors(monkeypatch, body_failure):
    from ciw.foundry_childhood import _isolated_project
    root = None
    def blocked_cleanup(*args, **kwargs):
        raise PermissionError('cleanup deliberately blocked')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(shutil, 'rmtree', blocked_cleanup)
            with pytest.raises(PermissionError, match='cleanup deliberately blocked') as error:
                with _isolated_project() as root:
                    (root / 'temporary.txt').write_bytes(b'private generated data')
                    if body_failure: raise ValueError('ENGINE_MUTATED_SOURCE')
            if body_failure:
                assert isinstance(error.value.__context__, ValueError)
                assert str(error.value.__context__) == 'ENGINE_MUTATED_SOURCE'
    finally:
        if root is not None: remove_test_project(root)
