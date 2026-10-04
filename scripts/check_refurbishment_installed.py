"""Qualify installed asset workflows and public-data bench imports outside checkout.

This executes software acceptance with labeled examples. It does not represent
physical equipment qualification or independent human operator acceptance.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import zipfile

# Load only the trusted sibling checker; never import source ciw from this script.
_SPEC = importlib.util.spec_from_file_location("net_hardware_installed_helper", Path(__file__).with_name("check_hardware_installed.py"))
_HELPER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_HELPER)


class Check(_HELPER.Check):
    def surface(self, name, surface, *arguments, expected=0):
        return self.execute(name, [str(self.console), surface, *map(str, arguments)], expected)


def lifecycle(check):
    root = check.root
    demo = check.surface("business-lifecycle", "refurb", "demo", "--output-dir", "business")
    check.require("complete-synthetic-business-journey", demo["status"] == "PASS" and demo["source_kind"] == "synthetic")
    check.require("fresh-retained-hardware-replay", demo["assessment"]["execution_id"] != demo["replay"]["execution_id"]
                  and demo["assessment"]["result_id"] != demo["replay"]["result_id"])
    check.require("no-physical-or-independent-human-acceptance-claim", demo["physical_validation"] == "not_established"
                  and demo["independent_operator_acceptance"] == "not_performed")
    database = "business/registry.sqlite3"
    asset = check.surface("returned-asset", "refurb", "show", demo["asset_id"], "--db", database)
    check.require("return-invalidates-active-disposition", asset["stage"] == "returned" and asset["current_release_id"] is None)
    check.require("parts-and-labor-use-exact-minor-units", asset["total_cost_minor"] == 15500
                  and asset["currency"] == "CAD" and asset["minor_unit_exponent"] == 2)
    check.surface("verify-history", "refurb", "verify", demo["asset_id"], "--db", database)
    restored = check.surface("restored-asset", "refurb", "show", demo["asset_id"], "--db", "business/restored.sqlite3")
    check.require("archive-restores-pre-return-release", restored["stage"] == "released" and restored["current_release_id"] == "release-1")
    commands = _HELPER._read(root / "business/commands.json")
    _HELPER._write(root / "retry.json", commands[0])
    retry = check.surface("idempotent-retry", "refurb", "apply", "retry.json", "--db", database)
    receipts = _HELPER._read(root / "business/receipts.json")
    check.require("retry-preserves-original-event", retry["replayed"] is True and retry["event_id"] == receipts[0]["event_id"])
    after = check.surface("after-retry", "refurb", "show", demo["asset_id"], "--db", database)
    check.require("retry-does-not-mutate-current-asset", after == asset)
    stale = deepcopy(commands[1]); stale["command_id"] = "stale-retry-new-id"
    _HELPER._write(root / "stale-command.json", stale)
    refused = check.surface("stale-revision", "refurb", "apply", "stale-command.json", "--db", database, expected=1)
    check.require("stale-revision-refused", refused["status"] == "REFUSED")
    missing = check.surface("missing-registry", "refurb", "list", "--db", "missing.sqlite3", expected=1)
    check.require("reads-do-not-create-empty-registry", missing["status"] == "REFUSED" and not (root / "missing.sqlite3").exists())
    customer = (root / "business/customer-report.html").read_text(encoding="utf-8")
    check.require("customer-pack-correctly-scoped", "DEMONSTRATION" in customer and "PASS" in customer
                  and "does not certify" in customer and "labor_cost_minor" not in customer and "Synthetic customer" not in customer)
    check.require("offline-desk-and-portable-exports", all((root / "business" / name).is_file()
                  for name in ("desk.html", "inventory.csv", "repair-costs.csv", "registry-archive.json")))
    before = _HELPER._hash(root / "business/customer-report.html")
    check.surface("report-overwrite-refused", "refurb", "report", demo["asset_id"], "--db", database,
                  "--output", "business/customer-report.html", expected=1)
    check.require("existing-customer-pack-preserved", _HELPER._hash(root / "business/customer-report.html") == before)
    return {"asset_id":demo["asset_id"], "registry_id":demo["registry_id"], "final_revision":asset["revision"]}


def bench_journey(check):
    results = {}
    for case, outcome, code in (("public", "PASS", 0), ("pass", "PASS", 0), ("fail", "FAIL", 2),
                                ("missing", "INDETERMINATE", 3), ("stale", "REFUSED", 1)):
        base = "bench-" + case
        example = check.surface(base, "bench", "example", "--case", case, "--output-dir", base)
        check.require(base + "-origin-labeled", example["assessment_source_kind"] == "synthetic"
                      and example["source_kind"] == ("external_public" if case == "public" else "synthetic"))
        verified = check.surface(base + "-verify", "bench", "verify", base + "/evidence.json",
                                 "--configuration", base + "/configuration.json")
        check.require(base + "-raw-bindings-consistent", verified["status"] == "bindings_consistent"
                      and verified["source_authenticity"] == "not_established")
        run = check.surface(base + "-assess", "hardware", "run", base + "/request.json", "--output-dir", base + "-run", expected=code)
        check.require(base + "-expected-assessment", run["status"] == outcome)
        if case == "stale":
            check.require("stale-configuration-not-published", not (check.root / (base + "-run")).exists())
        else:
            check.require(base + "-no-physical-validation", run["source_kind"] == "synthetic"
                          and run["assessment"]["authority"]["physical_validation"] == "not_established")
        results[case] = outcome
    check.surface("data-only-maintenance-plan", "bench", "plan", "--configuration", "bench-pass/configuration.json",
                  "--expected-configuration", "bench-pass/configuration.json", "--slot", "storage-0", "--action", "sanitize",
                  "--procedure-id", "example-only-no-execution", "--operator", "Synthetic acceptance",
                  "--source-kind", "synthetic", "--output", "maintenance-plan.json")
    check.surface("verify-maintenance-plan", "bench", "verify", "maintenance-plan.json")
    plan = _HELPER._read(check.root / "maintenance-plan.json")
    check.require("maintenance-plan-does-not-execute", plan["authority"]["maintenance_execution"] == "not_performed"
                  and plan["source_kind"] == "synthetic" and plan["destructive_operation"] is True
                  and plan["execution"] == "not_supported"
                  and all(item["status"] == "pending" for item in plan["preconditions"]))
    public = _HELPER._read(check.root / "bench-public/evidence.json")
    source = public["provenance"]["source"]
    check.require("public-fixture-pinned-and-attributed", source["revision"] == "6274decc61212cc61e58796cc3b8cf5cfa035809"
                  and source["license"] == "Apache-2.0"
                  and source["raw_sha256"] == "sha256:2fd5253bb0792ac6472db1add36feb757218b8d42dee40d60752cfa73d6b68cf"
                  and public["provenance"]["derivation"] is None)
    forged = deepcopy(_HELPER._read(check.root / "bench-pass/evidence.json"))
    forged["metrics"][0]["value"] = 999
    forged["record_digest"] = "sha256:" + sha256(json.dumps(
        {key:value for key,value in forged.items() if key != "record_digest"},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    _HELPER._write(check.root / "forged-evidence.json", forged)
    refused = check.surface("resealed-metric-mutation", "bench", "verify", "forged-evidence.json",
                            "--configuration", "bench-pass/configuration.json", expected=1)
    check.require("raw-evidence-rederived-after-resealing", refused["status"] == "REFUSED")
    return results


def qualify(python, console, wheel, destination, timeout=120):
    python, console, wheel = (Path(p).absolute() for p in (python, console, wheel))
    if not all(p.is_file() for p in (python, console, wheel)):
        raise ValueError("Require installed Python, net launcher and wheel")
    checkout = Path(__file__).resolve().parents[1]
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(checkout):
        raise ValueError("Choose a new output directory outside the source checkout")
    destination.mkdir(parents=True, exist_ok=False)
    check = Check(python, console, destination.resolve(), timeout)
    report = {"schema":"ciw.refurbishment-installed-qualification.v1", "status":"FAIL",
        "started_at":datetime.now(timezone.utc).isoformat(), "commands":check.commands, "checks":check.checks,
        "physical_validation":"not_established", "independent_operator_acceptance":"not_performed"}
    try:
        provenance = check.execute("installed-provenance", [str(python), "-I", "-c", _HELPER.PROVENANCE])
        report["installed_package"] = provenance
        package_root = Path(provenance["package_root"]).resolve(strict=True)
        editable = (provenance.get("direct_url") or {}).get("dir_info", {}).get("editable")
        check.require("isolated-installed-wheel-no-source-fallback", provenance["isolated"] is True and editable is not True
                      and Path(provenance["ciw_origin"]).resolve() == package_root / "__init__.py"
                      and not package_root.is_relative_to(checkout))
        report["wheel_byte_proof"] = _HELPER._wheel_proof(check, wheel, package_root)
        files = report["wheel_byte_proof"]["matched_files"]
        check.require("business-bench-and-source-attribution-packaged", all(name in files for name in (
            "ciw/refurbishment_store.py", "ciw/refurbishment_cli.py", "ciw/hardware_bench.py",
            "ciw/hardware_bench_cli.py", "ciw/bench_examples/smartctl-public.json",
            "ciw/bench_examples/LICENSE.txt", "ciw/bench_examples/attribution.json")))
        report["business"] = lifecycle(check)
        report["bench"] = bench_journey(check)
        report["status"] = "PASS"
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
        report["failure"] = {"type":type(exc).__name__, "reason":str(exc)}
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    _HELPER._write(check.root / "qualification.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("python", "net", "wheel", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 300:
        parser.error("timeout must be between 1 and 300 seconds")
    try:
        report = qualify(args.python, args.net, args.wheel, args.output_dir, args.timeout)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status":"REFUSED", "reason":str(exc)})); return 2
    print(json.dumps({"status":report["status"], "qualification":str(args.output_dir.absolute() / "qualification.json"),
        "commands":len(report["commands"]), "checks":len(report["checks"]), "failure":report.get("failure")}, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
