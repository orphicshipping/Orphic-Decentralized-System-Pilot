from copy import deepcopy
import json
from pathlib import Path

import pytest

from ciw import refurbishment_cli as cli
from ciw.refurbishment_contract import example_commands
from ciw.refurbishment_store import RefurbishmentStore


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_cli_new_registry_retry_and_stale_command_are_explicit(tmp_path, capsys):
    db = tmp_path / "desk.db"
    assert cli.main(["init", "--db", str(db)]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["registry_id"]
    command = example_commands()[0]
    path = write(tmp_path / "intake.json", command)
    assert cli.main(["apply", str(path), "--db", str(db)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["revision"] == 1 and not first["replayed"]
    assert cli.main(["apply", str(path), "--db", str(db)]) == 0
    retry = json.loads(capsys.readouterr().out)
    assert retry["event_id"] == first["event_id"] and retry["replayed"]
    command["command_id"] += ":new"
    write(path, command)
    assert cli.main(["apply", str(path), "--db", str(db)]) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "REFUSED"
    assert cli.main(["show", command["asset_id"], "--db", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 1
    before = db.read_bytes()
    assert cli.main(["init", "--db", str(db)]) == 1
    assert db.read_bytes() == before


def test_read_missing_registry_does_not_create_it(tmp_path, capsys):
    db = tmp_path / "missing.db"
    assert cli.main(["list", "--db", str(db)]) == 1
    assert not db.exists()
    assert json.loads(capsys.readouterr().err)["status"] == "REFUSED"


def test_action_requires_identity_and_uses_exact_payload(tmp_path, capsys):
    db = tmp_path / "registry.db"
    with RefurbishmentStore.create(db):
        pass
    command = example_commands()[0]
    payload = write(tmp_path / "payload.json", command["payload"])
    assert cli.main(["action", "intake", "--db", str(db), "--asset", command["asset_id"],
        "--revision", "0", "--actor", "Demonstration operator", "--command-id", "intake-request-1",
        "--payload", str(payload)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["asset"]["source_kind"] == "synthetic"
    with RefurbishmentStore(db) as store:
        assert store.history(command["asset_id"])[0]["command"]["actor"] == "Demonstration operator"


def test_complete_retained_demo_restores_and_invalidates_return(tmp_path):
    root = tmp_path / "journey"
    result = cli.demonstration(root)
    assert result["status"] == "PASS"
    assert result["physical_validation"] == "not_established"
    assert result["independent_operator_acceptance"] == "not_performed"
    assert result["assessment"]["execution_id"] != result["replay"]["execution_id"]
    assert result["assessment"]["result_id"] != result["replay"]["result_id"]
    with RefurbishmentStore(root / "registry.sqlite3") as store:
        asset = store.get_asset(result["asset_id"])
        assert asset["stage"] == "returned" and asset["current_release_id"] is None
        assert len(asset["returns"]) == 1 and len(asset["releases"]) == 1
        assert asset["total_cost_minor"] == 15500
    with RefurbishmentStore(root / "restored.sqlite3") as store:
        asset = store.get_asset(result["asset_id"])
        assert asset["stage"] == "released" and asset["current_release_id"] == "release-1"
        assert store.registry_id != result["registry_id"]
    assert "DEMONSTRATION" in (root / "customer-report.html").read_text(encoding="utf-8")
    assert "PASS" in (root / "customer-report.html").read_text(encoding="utf-8")
    assert (root / "inventory.csv").is_file() and (root / "repair-costs.csv").is_file()
    before = (root / "demonstration.json").read_bytes()
    with pytest.raises(ValueError):
        cli.demonstration(root)
    assert (root / "demonstration.json").read_bytes() == before


def test_duplicate_keys_are_refused_before_mutation(tmp_path, capsys):
    db = tmp_path / "registry.db"
    with RefurbishmentStore.create(db):
        pass
    path = tmp_path / "bad.json"
    path.write_text('{"action":"intake","action":"release"}', encoding="utf-8")
    assert cli.main(["apply", str(path), "--db", str(db)]) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "REFUSED"
    with RefurbishmentStore(db) as store:
        assert store.list_assets() == []
