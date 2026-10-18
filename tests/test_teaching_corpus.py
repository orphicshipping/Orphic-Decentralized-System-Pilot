"""Teaching export acceptance, containment and claim-boundary regressions."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "examples/workflows"
sys.path.insert(0, str(WORKFLOWS))
from usecase_catalog import INDEX, generate, read_index, validate_entries
from usecase_templates import EMIT_FUNCTIONS, _owned_payload
import emit_all_usecases as exporter


def test_catalog_reproducible_and_covers_every_owned_family():
    expected = generate()
    assert json.loads(INDEX.read_text()) == expected
    assert set(expected["counts"]) == set(EMIT_FUNCTIONS)
    assert len(expected["entries"]) == 10
    assert expected["historical_9074_corpus_restored"] is False


@pytest.mark.parametrize("mutation", ["traversal", "duplicate", "family", "executable", "missing"])
def test_catalog_rejects_invalid_inputs(mutation):
    entries = deepcopy(generate()["entries"])
    if mutation == "traversal": entries[0]["render_path"] = "../outside.json"
    elif mutation == "duplicate": entries.append(deepcopy(entries[0]))
    elif mutation == "family": entries[0]["family"] = "arbitrary.module"
    elif mutation == "executable": entries[0]["generated"] = False
    else: del entries[0]["non_claim"]
    with pytest.raises(ValueError): validate_entries(entries, set(EMIT_FUNCTIONS))


def test_all_families_emit_and_receipts_match_exact_bytes(tmp_path):
    receipt = tmp_path / "receipt.json"
    subprocess.run([sys.executable, str(WORKFLOWS / "emit_all_usecases.py"),
                    "--output-root", str(tmp_path), "--receipt", str(receipt)], check=True)
    data = json.loads(receipt.read_text())
    assert data["emitted"] == 10
    assert data["native_provider_qualification"] == "NOT_RUN"
    for row in data["entries"]:
        path = tmp_path / row["render_path"]
        assert sha256(path.read_bytes()).hexdigest() == row["sha256"]
        payload = json.loads(path.read_text())
        assert payload["may_authorize"] is False
        assert payload["fresh_verifier_occurrence"] is False
        assert payload["canonicalAdmission"] is False
        assert payload["evidence_seam"]["local_receipt"] == row["render_path"]
    heat = next(row for row in data["entries"] if row["family"] == "PROVED_HEAT")
    payload = json.loads((tmp_path / heat["render_path"]).read_text())
    assert payload["status"] == payload["upstream_status"] == "UNAVAILABLE"
    assert payload["proof_status"] == "NOT_CHECKED"


def test_owned_payloads_are_independent_and_sources_exist():
    for family in EMIT_FUNCTIONS:
        first = _owned_payload(family)
        first["may_authorize"] = True
        second = _owned_payload(family)
        assert second["may_authorize"] is False
        for citation in second.get("source_records", []):
            assert sha256((ROOT / citation["path"]).read_bytes()).hexdigest() == citation["sha256"]


def test_invalid_batch_writes_nothing(tmp_path):
    catalog = generate()
    catalog["entries"][-1]["render_path"] = "../../source.py"
    index = tmp_path / "bad.json"
    index.write_text(json.dumps(catalog))
    output = tmp_path / "output"
    result = subprocess.run([sys.executable, str(WORKFLOWS / "emit_all_usecases.py"),
                             "--index", str(index), "--output-root", str(output)], capture_output=True)
    assert result.returncode == 1
    assert not output.exists()


def test_energy_analyzer_preserves_ineligible_cases():
    analyses = _owned_payload("ENERGY_ACCURACY")["analyses"]
    assert analyses[0]["comparison"]["classification"] == "synthetic_only"
    assert not analyses[1]["comparison"]["eligible"]
    assert analyses[2]["measurement"]["gross_energy_j"] is None


def test_duplicate_json_and_empty_catalog_refuse(tmp_path):
    path = tmp_path / "index.json"
    path.write_text('{"schema":"ncp.teaching-catalog.v1","entries":[],"entries":[]}')
    with pytest.raises(ValueError): read_index(path, set(EMIT_FUNCTIONS))
    with pytest.raises(ValueError): validate_entries([], set(EMIT_FUNCTIONS))


@pytest.mark.parametrize('client', ['bevy', 'godot'])
def test_one_host_command_emits_and_launches_exact_record(tmp_path, monkeypatch, client):
    binary = tmp_path / 'operator selected executable'
    binary.write_text('operator-owned binary fixture')
    binary.chmod(0o700)
    receipt = tmp_path / 'receipt.json'
    entry = generate()['entries'][0]
    render = tmp_path / entry['render_path']
    def run(command, **kwargs):
        assert command[0] == str(binary)
        assert kwargs == {'capture_output': True, 'text': True, 'timeout': 15.0}
        assert render.is_file() and json.loads(render.read_bytes())['may_authorize'] is False
        if client == 'bevy':
            assert command[1:] == ['--frames', '8', str(render)]
        else:
            assert command[1:] == ['--audio-driver', 'Dummy', '--path', str(ROOT / 'godot'), '--', '--retained-render', str(render), '--frames', '8']
        return subprocess.CompletedProcess(command, 0, 'projected', '')
    monkeypatch.setattr(exporter.subprocess, 'run', run)
    monkeypatch.setattr(sys, 'argv', ['emit', '--scenario', entry['slug'], '--output-root', str(tmp_path),
        '--receipt', str(receipt), '--client', client, '--binary', str(binary), '--frames', '8', '--timeout', '15'])
    assert exporter.main() == 0
    data = json.loads(receipt.read_bytes())
    assert data['emitted'] == 1 and data['projector']['status'] == 'COMPLETED'
    assert data['projector']['input_sha256'] == data['entries'][0]['sha256']
    assert data['projector']['input_unchanged'] is True
    assert data['projector']['client_binary_sha256'] == sha256(binary.read_bytes()).hexdigest()
    assert data['projector']['display_observation'] == 'NOT_CHECKED_BY_LAUNCHER'
    assert data['native_provider_qualification'] == 'NOT_RUN'


@pytest.mark.parametrize('extra', [
    ['--scenario', 'unknown'], ['--client', 'bevy'], ['--binary', '/unbound'],
    ['--scenario', 'reservoir-disagreement', '--client', 'godot', '--binary', 'relative'],
])
def test_invalid_launcher_selection_writes_nothing(tmp_path, monkeypatch, extra):
    output = tmp_path / 'new-output'
    monkeypatch.setattr(sys, 'argv', ['emit', '--output-root', str(output), *extra])
    assert exporter.main() == 1
    assert not output.exists()


@pytest.mark.parametrize('outcome', ['failed', 'timeout', 'mutated', 'unavailable'])
def test_launch_failure_retains_an_explicit_receipt(tmp_path, monkeypatch, outcome):
    binary = tmp_path / 'binary'
    binary.write_text('operator fixture')
    binary.chmod(0o700)
    receipt = tmp_path / 'receipt.json'
    entry = generate()['entries'][0]
    def run(command, **kwargs):
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(command, 1, output=b'partial')
        if outcome == 'unavailable':
            raise OSError('unavailable display')
        if outcome == 'mutated':
            (tmp_path / entry['render_path']).write_text('{}')
        return subprocess.CompletedProcess(command, 1 if outcome == 'failed' else 0, '', '')
    monkeypatch.setattr(exporter.subprocess, 'run', run)
    monkeypatch.setattr(sys, 'argv', ['emit', '--scenario', entry['slug'], '--output-root', str(tmp_path),
        '--receipt', str(receipt), '--client', 'bevy', '--binary', str(binary)])
    assert exporter.main() == 1
    data = json.loads(receipt.read_bytes())
    assert data['layer_export'] == 'PASS'
    assert data['projector']['status'] == ('TIMED_OUT' if outcome == 'timeout' else 'FAILED')
    assert data['projector']['input_unchanged'] is (outcome != 'mutated')


def test_owned_exports_conform_to_frozen_writer_contract(tmp_path):
    jsonschema = pytest.importorskip('jsonschema', reason='Install the declared bench-models extra for schema validation')
    validator = jsonschema.Draft202012Validator(json.loads((ROOT / 'examples/retained-render.schema.json').read_bytes()))
    for entry in generate()['entries']:
        output = tmp_path / entry['render_path']
        EMIT_FUNCTIONS[entry['family']](output, entry)
        validator.validate(json.loads(output.read_bytes()))


def test_render_writer_rejects_nonfinite_before_writing_and_forces_display_boundary(tmp_path):
    sys.path.insert(0, str(ROOT / 'examples'))
    from _render_json import normalize_render, write_render
    for flag in ['true', 1, None, False]:
        value = normalize_render({'fresh_verifier_occurrence': flag, 'may_authorize': True, 'presentation_only': False})
        assert value['fresh_verifier_occurrence'] is False
        assert value['may_authorize'] is False and value['presentation_only'] is True
    target = tmp_path / 'absent' / 'render.json'
    with pytest.raises(ValueError):
        write_render(target, {'points': [[float('nan'), 0]]})
    assert not target.parent.exists()
