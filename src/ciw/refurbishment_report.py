"""Offline asset reports and a searchable operator dashboard."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
import tempfile

from .operations.runner import digest


def publish_bytes(path: Path, raw: bytes) -> None:
    """Publish a complete, new file without replacing existing destinations."""
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Output already exists: {path}. Choose a fresh output filename.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".net-report-", dir=path.parent) as directory:
        staged = Path(directory) / "report"
        with staged.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(staged, path)
        except FileExistsError:
            raise FileExistsError(f"Output already exists: {path}. Choose a fresh output filename.") from None


def money(amount: int, currency: str, exponent: int) -> str:
    """Display exact integer amounts without floating point conversion."""
    if type(amount) is not int or type(exponent) is not int or not 0 <= exponent <= 6:
        raise ValueError("Invalid money amount or exponent")
    sign = "-" if amount < 0 else ""
    digits = str(abs(amount)).zfill(exponent + 1)
    value = digits if exponent == 0 else digits[:-exponent] + "." + digits[-exponent:]
    return f"{currency} {sign}{value}"


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


_STYLE = """
:root{color-scheme:light;font:16px/1.55 system-ui,sans-serif;color:#172c34;background:#f3f6f5}
body{max-width:1120px;margin:auto;padding:36px 24px}h1{font-size:2.2rem;line-height:1.15}
h2{margin-top:2rem}header small{letter-spacing:.16em;text-transform:uppercase;color:#39636c}
.card,section{background:white;border:1px solid #cddbd9;border-radius:12px;padding:22px;margin:18px 0}
.banner{border-left:6px solid #b06000;background:#fff3df;padding:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:16px}
.metric strong{font-size:1.5rem;display:block}table{border-collapse:collapse;width:100%;font-size:.94rem}
th,td{padding:10px;border-bottom:1px solid #dce5e3;text-align:left;vertical-align:top;overflow-wrap:anywhere}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 ui-monospace,monospace;background:#f2f5f4;padding:12px}
input{padding:12px;border:1px solid #8ba5a5;border-radius:6px;width:min(600px,90%);font:inherit}
.muted{color:#4b646b}a{color:#006a77}details{margin:12px 0}code{overflow-wrap:anywhere}
@media print{body{background:white;padding:0}input,.no-print{display:none}section{break-inside:avoid}}
"""


def _page(title: str, body: str, script: str = "") -> bytes:
    # All record data is escaped before insertion; only fixed application code is executable.
    policy = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    return ("<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<meta http-equiv='Content-Security-Policy' content=\"{policy}\">"
            f"<title>{_e(title)}</title><style>{_STYLE}</style><body>"
            "<header><small>Notation Systems · Engineering Terminal</small>"
            f"<h1>{_e(title)}</h1></header>{body}{script}</body></html>").encode("utf-8")


def _details(title: str, value: object) -> str:
    return f"<details><summary>{_e(title)}</summary><pre>{_e(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))}</pre></details>"



def _current_order_id(asset: dict) -> str | None:
    if asset["active_work_order_id"] is not None:
        return asset["active_work_order_id"]
    return next((row["work_order_id"] for row in asset["releases"]
                 if row["release_id"] == asset["current_release_id"]), None)


def _assessment_rows(asset: dict, order: dict) -> list[dict]:
    current_order = _current_order_id(asset)
    rows = []
    for assessment in order["assessments"]:
        current = (order["work_order_id"] == current_order
                   and assessment["assessment_id"] == order["current_assessment_id"]
                   and assessment["configuration_ref"] == asset["configuration_ref"]
                   and assessment["generation"] == order["generation"])
        rows.append({"assessment_id": assessment["assessment_id"],
                     "status": assessment["assessment"]["status"],
                     "configuration_ref": assessment["configuration_ref"],
                     "source_kind": assessment["request"]["observations"]["source_kind"],
                     "relevance": "Current assessment" if current else "Historical; not current"})
    return rows


def _demonstration_data(asset: dict) -> bool:
    # Intake origin alone cannot establish the provenance of later observations.
    return (asset["source_kind"] != "operator_import"
            or any(row["source_kind"] != "operator_import" for row in asset["evidence"].values())
            or any(row["request"]["observations"]["source_kind"] != "operator_import"
                   for order in asset["work_orders"].values() for row in order["assessments"])
            or any(row["disposition"] == "demo_release" for row in asset["releases"]))


def asset_report(asset: dict, *, customer_copy: bool = True) -> bytes:
    """A retained declaration report, with costs/contact details excluded by default."""
    configuration = asset["configuration"]
    synthetic = _demonstration_data(asset)
    body = ("<p class='banner'><strong>DEMONSTRATION DATA — no physical equipment qualification.</strong> "
            "This report contains synthetic or public example records.</p>" if synthetic else
            "<p class='banner'>Operator-declared equipment records. NET has not independently authenticated physical observations.</p>")
    body += "<p>Release decisions below are recorded operator declarations. This report does not certify safety, sanitization, calibration or compliance.</p>"
    current = [row for order in asset["work_orders"].values()
               for row in _assessment_rows(asset, order) if row["relevance"] == "Current assessment"]
    current_summary = (current[0]["assessment_id"] + ": " + current[0]["status"]
                       if current else "No current assessment")
    rows = [("Asset", asset["asset_id"]), ("Manufacturer / model", f"{configuration['manufacturer']} / {configuration['model']}"),
            ("Serial number", configuration["serial_number"] or "Not recorded"), ("Condition", asset["condition"]),
            ("Workflow stage", asset["stage"]), ("Revision", asset["revision"]),
            ("Configuration reference", asset["configuration_ref"]),
            ("Current assessment", current_summary),
            ("Current release", asset["current_release_id"] or "Not released")]
    if not customer_copy:
        rows += [("Location", asset["location"]), ("Total recorded cost", money(asset["total_cost_minor"], asset["currency"], asset["minor_unit_exponent"]))]
    body += "<section><h2>Equipment</h2><table>" + "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k,v in rows) + "</table></section>"
    body += "<section><h2>Configuration</h2>" + _details("Installed components and software", configuration) + "</section>"
    body += "<section><h2>Work performed and assessment</h2>"
    for order in asset["work_orders"].values():
        body += f"<h3>{_e(order['work_order_id'])} · {_e(order['summary'])}</h3>"
        body += f"<p>Status: {_e(order['status'])}</p>"
        if customer_copy:
            # Customer packs omit supplier costs, internal diagnoses, raw evidence and contact data.
            repairs = [{k: row[k] for k in ("repair_id", "description") if k in row} for row in order["repairs"]]
            body += _details("Repair descriptions", repairs)
            body += _details("Assessment history", _assessment_rows(asset, order))
        else:
            body += _details("Work order history", order)
    body += "</section><section><h2>Release and service history</h2>"
    for label, field in (("Releases", "releases"), ("Warranty declarations", "warranties"), ("Returns", "returns")):
        values = asset[field]
        if customer_copy:
            allowed = {"release_id", "review_id", "disposition", "configuration_ref", "warranty_id", "starts_on", "ends_on", "terms", "return_id", "received_on", "condition", "scope", "source_kinds", "certification_authority", "release_scope"}
            values = [{k:v for k,v in row.items() if k in allowed} for row in values]
        if field == "releases":
            values = [{**row, "current": row["release_id"] == asset["current_release_id"]} for row in values]
        body += _details(label, values)
    body += "</section><p class='muted'>This is a snapshot of the stated revision. Later repairs or returns can invalidate a release. Warranty terms are declarations supplied by the operator.</p>"
    return _page("Equipment report · " + asset["asset_id"], body)


def dashboard(assets: list[dict], registry_id: str) -> bytes:
    unreleased = sum(row["current_release_id"] is None for row in assets)
    demo = sum(_demonstration_data(row) for row in assets)
    body = (f"<div class='grid card'><div class='metric'><strong>{len(assets)}</strong>Assets</div>"
            f"<div class='metric'><strong>{unreleased}</strong>Without current release</div>"
            f"<div class='metric'><strong>{demo}</strong>Assets with demonstration data</div></div>"
            "<p>Read-only registry snapshot. Search by asset, serial, model, location or stage. Open a row for the retained details.</p>"
            "<label for='search'>Find equipment</label><br><input id='search' type='search' placeholder='Asset, model, serial, location…'>")
    for row in assets:
        config = row["configuration"]
        search = " ".join(str(row.get(k, "")) for k in ("asset_id", "location", "condition", "stage")) + " " + str(config["model"]) + " " + str(config["serial_number"])
        body += (f"<section class='asset' data-search='{_e(search.lower())}'><h2>{_e(row['asset_id'])} · {_e(config['model'])}</h2>"
                 f"<p>{_e(row['stage'])} · {_e(row['location'])} · Revision {_e(row['revision'])}</p>"
                 f"<p>{'DEMONSTRATION DATA' if _demonstration_data(row) else 'Operator-declared records'} · "
                 f"{_e(money(row['total_cost_minor'], row['currency'], row['minor_unit_exponent']))}</p>"
                 + _details("Equipment and work orders", row) + "</section>")
    body += f"<p class='muted'>Registry {_e(registry_id)}. Snapshots do not grant release or physical qualification.</p>"
    script = "<script>document.getElementById('search').addEventListener('input',function(){const q=this.value.toLowerCase();document.querySelectorAll('.asset').forEach(function(e){e.hidden=!e.dataset.search.includes(q)})});</script>"
    return _page("Refurbishment desk", body, script)


def export_report(store, asset_id: str, destination: Path, *, internal: bool = False) -> dict:
    asset = store.get_asset(asset_id)
    publish_bytes(destination, asset_report(asset, customer_copy=not internal))
    return {"schema": "ciw.refurbishment-report.v1", "status": "created", "asset_id": asset_id,
            "revision": asset["revision"], "asset_ref": digest(asset), "audience": "internal" if internal else "customer",
            "output": str(destination), "physical_validation": "not_established"}


def export_dashboard(store, destination: Path) -> dict:
    assets = store.list_assets()
    publish_bytes(destination, dashboard(assets, store.registry_id))
    return {"status": "created", "assets": len(assets), "output": str(destination), "read_only": True}
