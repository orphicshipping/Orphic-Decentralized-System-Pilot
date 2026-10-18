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
