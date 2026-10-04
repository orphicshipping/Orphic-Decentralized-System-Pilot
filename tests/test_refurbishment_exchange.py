from copy import deepcopy
import csv
import io
import json
from pathlib import Path

import pytest

from ciw.operations.runner import digest
from ciw.refurbishment_contract import example_commands
from ciw.refurbishment_exchange import export_archive, import_archive, export_csv, _cell
from ciw.refurbishment_report import asset_report, dashboard, export_report, money
from ciw.refurbishment_store import RefurbishmentStore


@pytest.fixture
def populated(tmp_path):
    with RefurbishmentStore.create(tmp_path / "source.db") as store:
        for command in example_commands():
            store.execute(command)
        yield store


def test_archive_round_trip_preserves_state_with_fresh_occurrences(populated, tmp_path):
    archive = tmp_path / "archive.json"
    exported = export_archive(populated, archive)
    target = tmp_path / "target.db"
    restored = import_archive(archive, target)
    assert restored["archive_sha256"] == exported["archive_sha256"]
    assert restored["occurrences"] == "fresh_command_replay"
    with RefurbishmentStore(target) as store:
        assert store.list_assets() == populated.list_assets()
        assert store.registry_id != populated.registry_id
        old = populated.export_history(); new = store.export_history()
        assert len(new) == len(old)
        assert all(a["event_id"] != b["event_id"] for a,b in zip(old,new))
        assert [r["command"] for r in old] == [r["command"] for r in new]
        assert restored["source_bundle_ref"] == digest(populated.export_bundle())
        assert restored["restored_bundle_ref"] == digest(store.export_bundle())
        assert restored["source_bundle_ref"] != restored["restored_bundle_ref"]
    before = target.read_bytes()
    with pytest.raises(ValueError):
        import_archive(archive, target)
    assert target.read_bytes() == before


@pytest.mark.parametrize("edit", ["digest", "snapshot", "command", "chain"])
def test_corrupt_archive_never_publishes_registry(populated, tmp_path, edit):
    archive = tmp_path / "archive.json"
    export_archive(populated, archive)
    value = json.loads(archive.read_text(encoding="utf-8"))
    if edit == "digest":
        value["bundle_ref"] = "sha256:" + "0" * 64
    elif edit == "snapshot":
        value["bundle"]["assets"][0]["location"] = "altered"
    elif edit == "command":
        value["bundle"]["events"][-1]["command"]["expected_revision"] = 0
    else:
        value["bundle"]["events"][-1]["event_ref"] = "sha256:" + "0" * 64
    if edit != "digest":
        value["bundle_ref"] = digest(value["bundle"])
    archive.write_text(json.dumps(value), encoding="utf-8")
    target = tmp_path / "target.db"
    with pytest.raises(ValueError):
        import_archive(archive, target)
    assert not target.exists()


def test_csv_is_exact_and_neutralizes_formula_cells(populated, tmp_path):
    asset = populated.list_assets()[0]
    populated.execute({"schema":"ciw.refurbishment-command.v1", "asset_id":asset["asset_id"],
        "command_id":"move-formula", "expected_revision":asset["revision"], "actor":"Test operator",
        "action":"relocate", "payload":{"location":"  =HYPERLINK(\"https://example.invalid\")", "notes":""}})
    inventory = tmp_path / "inventory.csv"; costs = tmp_path / "costs.csv"
    export_csv(populated, inventory)
    export_csv(populated, costs, kind="costs")
    row = next(csv.DictReader(io.StringIO(inventory.read_text(encoding="utf-8-sig"))))
    assert row["location"].startswith("'  =")
    assert row["total_cost_minor"] == "15500" and row["minor_unit_exponent"] == "2" and row["currency"] == "CAD"
    cost = next(csv.DictReader(io.StringIO(costs.read_text(encoding="utf-8-sig"))))
    assert cost["parts_cost_minor"] == "2500" and cost["labor_cost_minor"] == "3000"
    assert cost["total_cost_minor"] == "5500"
    before = inventory.read_bytes()
    with pytest.raises(FileExistsError, match="Choose a fresh output filename"):
        export_csv(populated, inventory)
    assert inventory.read_bytes() == before


def test_customer_report_redacts_contact_cost_and_escapes_records(populated, tmp_path):
    asset = deepcopy(populated.list_assets()[0])
    asset["configuration"]["model"] = "<script>alert('model')</script>"
    asset["work_orders"]["WO-DEMO-1"]["repairs"][0]["description"] = "<img src=x onerror=alert(1)>"
    raw = asset_report(asset).decode()
    assert "<script>" not in raw and "<img" not in raw
    assert "&lt;script&gt;" in raw and "&lt;img" in raw
    assert "Synthetic customer" not in raw and "acquisition_cost_minor" not in raw
    assert "labor_cost_minor" not in raw and "CAD 155.00" not in raw
    assert "DEMONSTRATION" in raw and "PASS" in raw
    internal = asset_report(asset, customer_copy=False).decode()
    assert "CAD 155.00" in internal and "labor_cost_minor" in internal
    desk = dashboard([asset], populated.registry_id).decode()
    assert "&lt;script&gt;" in desk and desk.count("<script>") == 1
    assert "data-search=" in desk and "DEMONSTRATION" in desk


def test_money_uses_integer_arithmetic():
    assert money(10000000000000001, "CAD", 2) == "CAD 100000000000000.01"
    assert money(9, "JPY", 0) == "JPY 9"
    assert money(-1, "KWD", 3) == "KWD -0.001"
    with pytest.raises(ValueError):
        money(True, "CAD", 2)


@pytest.mark.parametrize("transition", ["return", "new_order"])
def test_historical_pass_is_not_presented_as_current_after_work_resumes(populated, transition):
    asset = populated.list_assets()[0]
    initial = asset_report(asset).decode("utf-8")
    assert "<th>Current assessment</th><td>assessment-1: PASS</td>" in initial
    payload = ({"return_id": "return-1", "release_id": "release-1", "received_on": "2026-02-01",
                "reason": "Needs investigation", "condition": "Returned", "notes": ""}
               if transition == "return" else {"work_order_id": "WO-2", "summary": "New inspection"})
    populated.execute({"schema": "ciw.refurbishment-command.v1", "asset_id": asset["asset_id"],
                        "command_id": "resume-work", "expected_revision": asset["revision"],
                        "actor": "Test operator", "action": "record_return" if transition == "return" else "open_order",
                        "payload": payload})
    raw = asset_report(populated.get_asset(asset["asset_id"])).decode("utf-8")
    assert "<th>Current assessment</th><td>No current assessment</td>" in raw
    assert "Historical; not current" in raw
    assert "assessment-1: PASS</td>" not in raw
    assert "PASS" in raw  # Retained history remains visible, but is explicitly historical.


def test_imported_asset_with_synthetic_assessment_is_visibly_a_demo(tmp_path):
    commands = example_commands()
    commands[0]["payload"]["source_kind"] = "operator_import"
    with RefurbishmentStore.create(tmp_path / "mixed-origin.db") as store:
        for command in commands:
            store.execute(command)
        asset = store.list_assets()[0]
        raw = asset_report(asset).decode("utf-8")
        assert "DEMONSTRATION DATA" in raw
        assert "synthetic_or_public_demo_only" in raw
        assert "source_kinds" in raw
        assert "DEMONSTRATION DATA" in dashboard([asset], store.registry_id).decode("utf-8")


def test_customer_report_excludes_internal_fields_in_nested_service_records(populated):
    asset = deepcopy(populated.list_assets()[0])
    asset["location"] = "PRIVATE-LOCATION"
    order = asset["work_orders"]["WO-DEMO-1"]
    order["diagnoses"][0]["findings"] = "PRIVATE-DIAGNOSIS"
    order["repairs"][0]["actor"] = "PRIVATE-REPAIR-ACTOR"
    asset["releases"][0]["customer"] = {"name": "PRIVATE-CUSTOMER", "contact": "PRIVATE-CONTACT"}
    asset["releases"][0]["notes"] = "PRIVATE-RELEASE-NOTES"
    asset["warranties"][0]["notes"] = "PRIVATE-WARRANTY-NOTES"
    customer = asset_report(asset).decode("utf-8")
    internal = asset_report(asset, customer_copy=False).decode("utf-8")
    for private in ("PRIVATE-LOCATION", "PRIVATE-DIAGNOSIS", "PRIVATE-REPAIR-ACTOR", "PRIVATE-CUSTOMER",
                    "PRIVATE-CONTACT", "PRIVATE-RELEASE-NOTES", "PRIVATE-WARRANTY-NOTES"):
        assert private not in customer
        assert private in internal
    assert "Notation Systems \u00b7 Engineering Terminal" in customer
    assert "\u00c2\u00b7" not in customer and "\u00e2\u20ac" not in customer


@pytest.mark.parametrize("prefix", ["", " ", "\t", "\r", "\n", "\v", "\f", "\x1f", "\ufeff", "\u200b", "\u00a0", "\u2028", " \t\ufeff"])
@pytest.mark.parametrize("formula", ["=1+1", "+1+1", "-1+1", "@SUM(1,1)"])
def test_csv_formula_prefixes_remain_literal(prefix, formula):
    original = prefix + formula
    assert _cell(original) == "'" + original
    assert _cell(15500) == "15500"
    assert _cell("ordinary value") == "ordinary value"


def test_import_failure_after_partial_replay_never_publishes(populated, tmp_path, monkeypatch):
    archive = tmp_path / "archive.json"
    export_archive(populated, archive)
    source_before = populated.export_bundle()
    target = tmp_path / "target.db"
    original_execute = RefurbishmentStore.execute

    def fail_after_append(store, command):
        result = original_execute(store, command)
        if command["action"] == "record_warranty":
            raise ValueError("injected after staged command commit")
        return result

    monkeypatch.setattr(RefurbishmentStore, "execute", fail_after_append)
    with pytest.raises(ValueError, match="injected"):
        import_archive(archive, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".net-registry-import-*"))
    assert populated.export_bundle() == source_before


def test_import_publication_race_never_overwrites_new_destination(populated, tmp_path, monkeypatch):
    from ciw import refurbishment_exchange as exchange
    archive = tmp_path / "archive.json"
    export_archive(populated, archive)
    target = tmp_path / "target.db"
    original_link = exchange.os.link

    def racing_link(source, destination, *args, **kwargs):
        if Path(destination) == target:
            target.write_bytes(b"concurrently created destination")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(exchange.os, "link", racing_link)
    with pytest.raises(FileExistsError):
        import_archive(archive, target)
    assert target.read_bytes() == b"concurrently created destination"
    assert not list(tmp_path.glob(".net-registry-import-*"))


def test_restore_refuses_reused_occurrences_before_publication(populated, tmp_path, monkeypatch):
    archive = tmp_path / "archive.json"
    export_archive(populated, archive)
    source_bundle = populated.export_bundle()
    original_export = RefurbishmentStore.export_bundle

    def reused_occurrence(store):
        bundle = original_export(store)
        bundle["events"][0]["event_id"] = source_bundle["events"][0]["event_id"]
        return bundle

    monkeypatch.setattr(RefurbishmentStore, "export_bundle", reused_occurrence)
    target = tmp_path / "target.db"
    with pytest.raises(ValueError, match="fresh command replay"):
        import_archive(archive, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".net-registry-import-*"))
