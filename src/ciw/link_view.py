"""Offline pixel-instrument view over an existing retained NET investigation."""
from __future__ import annotations

import base64
from hashlib import sha256
import html
import json
from pathlib import Path

from .link_workflow import inspect
from .link_contract import bytes_ref, channel_for
from .link_store import safe_path

_SCRIPT = """document.getElementById('filter').addEventListener('input',function(){const term=this.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(row=>{row.hidden=!row.textContent.toLowerCase().includes(term);});});"""


def report(directory, output):
    summary = inspect(directory)
    esc = lambda value: html.escape(str(value), quote=True)
    data, device = summary["data"], summary["manifest"]
    observations = [] if data is None else data["observations"]
    projection = {} if data is None else data["projection"]
    rows = []
    for item in observations:
        channel = channel_for(device, item["channel_id"])
        rows.append("<tr>" + "".join("<td>" + esc(v) + "</td>" for v in (
            item["sequence"], item["channel_id"], item["stage"], item["value"] if item["value"] is not None else "MISSING",
            channel["unit"], item["device_tick"] if item["device_tick"] is not None else "UNKNOWN",
            "DECLARED" if item["uncertainty"] is not None else "JOINT DECLARED" if projection.get("joint_covariance") is not None else "NOT SUPPLIED")) + "</tr>")
    rows = "".join(rows)
    origins = ", ".join(sorted({v["origin"].upper() for v in observations})) or "REFUSED"
    script_hash = base64.b64encode(sha256(_SCRIPT.encode()).digest()).decode()
    body = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{script_hash}'">
<title>Notation Link / retained instrument record</title><style>
:root{{color-scheme:dark;--paper:#d8e3bb;--ink:#0f1611;--muted:#9caf88;--edge:#57705c}}*{{box-sizing:border-box}}
body{{margin:0;background:var(--ink);color:var(--paper);font:16px/1.55 ui-monospace,monospace}}main{{max-width:1120px;margin:auto;padding:24px}}
header{{border-bottom:4px solid var(--paper);padding-bottom:16px;display:flex;gap:24px;align-items:center;justify-content:space-between}}
h1{{font-size:clamp(22px,4vw,38px);letter-spacing:.06em;margin:0}}h2{{font-size:18px;letter-spacing:.1em}}.sub{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:18px;margin:24px 0}}section{{border:2px solid var(--edge);padding:18px;box-shadow:5px 5px 0 #050805;margin:20px 0}}
.grid section{{margin:0}}.badge{{border:2px solid var(--paper);padding:7px;white-space:nowrap}}.readout{{font-size:40px;font-variant-numeric:tabular-nums}}
input{{font:inherit;border:2px solid var(--edge);padding:10px;background:var(--ink);color:var(--paper);width:min(100%,400px)}}input:focus,summary:focus{{outline:3px solid var(--paper);outline-offset:3px}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;text-align:left}}td,th{{padding:10px;border-bottom:1px solid var(--edge)}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}code{{overflow-wrap:anywhere}}summary{{cursor:pointer;min-height:44px;padding:8px 0}}footer{{border-top:2px solid var(--edge);margin-top:24px;padding:16px 0;color:var(--muted)}}
@media(max-width:540px){{main{{padding:14px}}header{{display:block}}.badge{{display:inline-block;margin-top:12px}}td,th{{padding:8px}}}}
</style></head><body><main><header><div><h1>NOTATION / LINK</h1><div class="sub">INDUSTRIAL TOOLING + INSTRUMENTATION</div></div><span class="badge">{esc(origins)} / RETAINED</span></header>
<div class="grid"><section><h2>INSTRUMENT</h2><div>{esc(device.get('persona','UNKNOWN') if isinstance(device,dict) else 'UNKNOWN')}</div><p>{esc(device.get('device_id','UNKNOWN') if isinstance(device,dict) else 'UNKNOWN')}</p><div class="sub">Physical control: NONE</div></section>
<section><h2>OBSERVATIONS</h2><div class="readout">{len(observations):04d}</div><div>{esc(summary['status'])}</div><div class="sub">Not a live reading</div></section>
<section><h2>EVIDENCE STATE</h2><div>RAW / RETAINED IN CAPTURE</div><div>NET / SESSION RETAINED</div><div>VERIFY / NOT VERIFIED</div><div>CLOCK ALIGNMENT / UNKNOWN</div></section></div>
<section><h2>MEASUREMENT REGISTER</h2><label for="filter">Filter retained rows </label><input id="filter" type="search" placeholder="Channel, stage, or reading">
<div class="scroll"><table><thead><tr><th>SEQ</th><th>CHANNEL</th><th>STAGE</th><th>VALUE</th><th>UNIT</th><th>DEVICE TICK</th><th>UNCERTAINTY</th></tr></thead><tbody>{rows}</tbody></table></div>
<p>Sequence gaps: {esc(json.dumps(projection.get('gaps',[])))}. Withheld native projections: {len(projection.get('withheld',[]))}.</p>
<p class="sub">Device-reported values are not automatically calibrated. Host receipt time is not acquisition time.</p></section>
<section><h2>CALIBRATION / CONFIGURATION</h2><pre>{esc(json.dumps(None if data is None else data['calibrations'],indent=2))}</pre><details><summary>Device, firmware, gateware and configuration declarations</summary><pre>{esc(json.dumps(device,indent=2))}</pre></details></section>
<section><h2>INVESTIGATION IDENTITIES</h2><p>Evidence <code>{esc(summary['evidence_id'])}</code></p><p>Execution <code>{esc(summary['execution_id'])}</code></p><p>Result <code>{esc(summary['result_id'])}</code></p>
<details><summary>Original observations, raw byte references, native mappings and scope</summary><pre>{esc(json.dumps(data,indent=2))}</pre></details></section>
<footer>READ-ONLY OFFLINE DASHPLATE. No device connection, firmware update, calibration approval, manufacturing release or state admission. Rendering does not grant authority.</footer>
</main><script>{_SCRIPT}</script></body></html>'''
    output = safe_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(body)
    return {"status": "CREATED", "report": str(output), "report_ref": bytes_ref(body.encode()),
            "source_evidence_id": summary["evidence_id"], "network_access": False}
