"""Equipment intake, repair orders, release review and customer handoff."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .control_contracts import save_new
from .hardware_workflow import read_bytes
from .session import loads_json
from .refurbishment_contract import COMMAND_SCHEMA, example_commands
from .refurbishment_store import RefurbishmentStore
from .refurbishment_exchange import export_archive, import_archive, export_csv
from .refurbishment_report import export_report, export_dashboard


def _load(path):
    return loads_json(read_bytes(path).decode("utf-8"))


def demonstration(destination: Path) -> dict:
    """Retain an offline synthetic business journey plus real NET run/replay."""
    from . import hardware_workflow
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Demonstration requires a new directory")
    destination.mkdir(parents=True, exist_ok=False)
    commands = example_commands()
    save_new(destination / "commands.json", commands)
    receipts = []
    with RefurbishmentStore.create(destination / "registry.sqlite3") as store:
        for command in commands:
            receipts.append(store.execute(command))
        asset_id = commands[0]["asset_id"]
        asset = store.get_asset(asset_id)
        request = next(command["payload"]["request"] for command in commands if command["action"] == "reassess")
        original = hardware_workflow.run(request, destination / "assessment")
        replay = hardware_workflow.replay(destination / "assessment", destination / "replay")
        if (original["status"] != "PASS" or replay["assessment"] != original["assessment"]
                or original["execution_id"] == replay["execution_id"] or original["result_id"] == replay["result_id"]):
            raise ValueError("Demonstration hardware replay did not preserve evidence with fresh occurrences")
        export_report(store, asset_id, destination / "customer-report.html")
        export_report(store, asset_id, destination / "internal-report.html", internal=True)
        export_archive(store, destination / "registry-archive.json")
        export_csv(store, destination / "inventory.csv")
        export_csv(store, destination / "repair-costs.csv", kind="costs")
        restored = import_archive(destination / "registry-archive.json", destination / "restored.sqlite3")
        return_command = {"schema": COMMAND_SCHEMA, "command_id":asset_id+":return", "asset_id":asset_id,
            "expected_revision":asset["revision"], "actor":"Synthetic fixture", "action":"record_return",
            "payload":{"return_id":"return-demo", "release_id":asset["current_release_id"],
                       "received_on":"2026-02-01", "reason":"Synthetic return journey", "condition":"Awaiting repeat diagnosis", "notes":""}}
        receipts.append(store.execute(return_command))
        returned = store.get_asset(asset_id)
        if returned["current_release_id"] is not None or returned["stage"] != "returned":
            raise ValueError("Return failed to invalidate the active release")
        export_dashboard(store, destination / "desk.html")
        verification = store.verify(asset_id)
        save_new(destination / "return-command.json", return_command)
        save_new(destination / "receipts.json", receipts)
        result = {"schema":"ciw.refurbishment-demo.v1", "status":"PASS", "source_kind":"synthetic",
            "asset_id":asset_id, "registry_id":store.registry_id, "revision":returned["revision"],
            "verification":verification, "restored_registry_id":restored["registry_id"],
            "assessment":{"execution_id":original["execution_id"], "result_id":original["result_id"]},
            "replay":{"execution_id":replay["execution_id"], "result_id":replay["result_id"]},
            "physical_validation":"not_established", "independent_operator_acceptance":"not_performed",
            "output_dir":str(destination)}
        save_new(destination / "demonstration.json", result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="net refurb", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("init", "Create a new local registry"), ("list", "List verified asset snapshots")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--db", type=Path, required=True)
    apply = commands.add_parser("apply", help="Apply one reviewed command with revision and retry identity")
    apply.add_argument("command_file", type=Path); apply.add_argument("--db", type=Path, required=True)
    action = commands.add_parser("action", help="Record one action from a payload file with explicit operator identity")
    action.add_argument("action", choices=["intake", "open_order", "diagnose", "record_repair", "replace_configuration", "relocate",
        "record_evidence", "reassess", "review", "release", "record_customer", "record_warranty", "record_return"])
    action.add_argument("--db", type=Path, required=True); action.add_argument("--asset", required=True)
    action.add_argument("--revision", type=int, required=True); action.add_argument("--actor", required=True)
    action.add_argument("--command-id", required=True); action.add_argument("--payload", type=Path, required=True)
    for name in ("show", "history", "verify", "report"):
        command = commands.add_parser(name)
        command.add_argument("asset_id"); command.add_argument("--db", type=Path, required=True)
        if name == "report":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--internal", action="store_true", help="Include internal notes, repair costs and raw evidence")
    dashboard = commands.add_parser("dashboard", help="Write a searchable, read-only offline operator desk")
    dashboard.add_argument("--db", type=Path, required=True); dashboard.add_argument("--output", type=Path, required=True)
    export = commands.add_parser("export", help="Write a portable replay archive or inventory/accounting CSV")
    export.add_argument("--db", type=Path, required=True); export.add_argument("--output", type=Path, required=True)
    export.add_argument("--format", choices=["archive", "inventory-csv", "costs-csv"], default="archive")
    restore = commands.add_parser("import", help="Verify and replay an archive into a new registry")
    restore.add_argument("archive", type=Path); restore.add_argument("--db", type=Path, required=True)
    example = commands.add_parser("example", help="Write editable synthetic commands for the complete lifecycle")
    example.add_argument("--output", type=Path, required=True)
    demo = commands.add_parser("demo", help="Run the offline synthetic lifecycle, export and restoration journey")
    demo.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "example":
            save_new(args.output, example_commands())
            result = {"status":"created", "source_kind":"synthetic", "output":str(args.output)}
        elif args.command == "demo":
            result = demonstration(args.output_dir)
        elif args.command == "import":
            result = import_archive(args.archive, args.db)
        elif args.command == "init":
            with RefurbishmentStore.create(args.db) as store:
                result = {"status":"created", "registry_id":store.registry_id, "db":str(args.db)}
        else:
            with RefurbishmentStore(args.db) as store:
                if args.command == "apply":
                    result = store.execute(_load(args.command_file))
                elif args.command == "action":
                    result = store.execute({"schema":COMMAND_SCHEMA, "command_id":args.command_id,
                        "asset_id":args.asset, "expected_revision":args.revision, "actor":args.actor,
                        "action":args.action, "payload":_load(args.payload)})
                elif args.command == "list":
                    result = {"status":"verified", "registry_id":store.registry_id, "assets":store.list_assets()}
                elif args.command == "show":
                    result = store.get_asset(args.asset_id)
                elif args.command == "history":
                    result = {"status":"verified", "asset_id":args.asset_id, "events":store.history(args.asset_id)}
                elif args.command == "verify":
                    result = store.verify(args.asset_id)
                elif args.command == "report":
                    result = export_report(store, args.asset_id, args.output, internal=args.internal)
                elif args.command == "dashboard":
                    result = export_dashboard(store, args.output)
                elif args.format == "archive":
                    result = export_archive(store, args.output)
                else:
                    result = export_csv(store, args.output, kind="inventory" if args.format == "inventory-csv" else "costs")
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, sqlite3.Error) as exc:
        print(json.dumps({"status":"REFUSED", "reason":str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
