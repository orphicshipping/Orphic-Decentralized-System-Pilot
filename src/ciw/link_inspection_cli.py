"""Digital Inspection: one retained-workflow front door, not a new runtime.

Default output is for an operator; --advanced retains scientific detail. No
ports, discovery, network, enrollment, configuration grants or release actions.
The existing four-operation scalar graph owns calculation and validation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import html
import json
from pathlib import Path
import sys
import time

from . import link_contract as c, link_study as study, link_study_io as io
from .control_contracts import save_new
from .hardware_workflow import read_bytes
from .link_store import safe_path


_STATE = {
    "MISSING_DECLARATION": "Calibration declaration needed",
    "ACQUISITION_TIME_UNKNOWN": "Acquisition time needed to check calibration",
    "OUTSIDE_DECLARED_VALIDITY": "Reading outside the declared calibration period",
    "DECLARED_APPLICABLE": "Applicability declared; calibration not authenticated",
}


def view(directory, *, request_ref=None, advanced=False):
    """Reopen and validate existing Session results, never trust a UI status file."""
    root = safe_path(directory)
    # Anchor and source validation must consume the same read, not a second
    # path open after a separate hash check.
    retained = io.inspect(root, expected_request_ref=request_ref)
    results = retained["results"]
    decoded = results.get(study.OPS[0], {}).get("data", {})
    corrected = results.get(study.OPS[1], {}).get("data", {})
    model = results.get(study.OPS[2], {}).get("data", {})
    audit = results.get(study.OPS[3], {}).get("data", {})
    complete = all(op in results for op in study.OPS) and audit.get("status") == "PASS"
    statuses = set(corrected.get("applicability", []))
    outside = model.get("nominal_comparison") == "OUTSIDE"
    readings = decoded.get("rows", [])
    calibration = next((s for s in _STATE if s in statuses), "MISSING_DECLARATION")
    timing_unknown = not readings or any(r["acquired_at"] is None for r in readings)
    flagged = bool(retained["source_quality_flags"])
    if flagged:
        next_action = "Review the recorded source quality flags in Advanced. Original bytes are retained; flagged readings are not interpreted by this profile."
    elif audit.get("status") == "FAIL":
        next_action = "The arithmetic audit failed. The displayed value is withheld; review the retained candidate and audit in Advanced. Original bytes are retained."
    elif not complete:
        next_action = "Review the retained input or refused calculation in Advanced; do not discard the original recording."
    elif outside:
        next_action = "Review the result outside the declared nominal limit. This is not a qualified reject/release decision."
    elif timing_unknown:
        next_action = "Provide acquisition-time and reference-calibration evidence before using this result for acceptance."
    else:
        next_action = "Complete reference calibration, uncertainty and physical qualification before acceptance use."
    value = {
        "schema": "ciw.link-inspection-view.v1", "capability": "Digital Inspection",
        "mode": "retained_offline_preview", "origin": retained["origin"],
        "status": "INCOMPLETE" if not complete else "REVIEW_REQUIRED" if outside else "NEEDS_QUALIFICATION",
        "qualification": "HELD", "calculation": "COMPLETE" if complete else "INCOMPLETE",
        "reading": {"kind": "declared_corrected_mean", "value": model.get("mean") if complete else None,
                    "unit": model.get("unit"), "count": model.get("count", 0)},
        "checks": {
            "recording": "RETAINED_AND_DECODED" if decoded else "RETAINED_NOT_DECODED",
            "arithmetic": audit.get("status", "NOT_RUN"),
            "source_quality": "FLAGGED_REVIEW_REQUIRED" if flagged else "NO_FLAGS_DECLARED",
            "nominal_comparison": "WITHHELD_FAILED_AUDIT" if audit.get("status") == "FAIL" else model.get("nominal_comparison", "NOT_RUN"),
            "calibration": calibration,
            "acquisition_time": "UNKNOWN" if timing_unknown else "DECLARED_ONLY",
            "uncertainty": "WITHHELD_FAILED_AUDIT" if audit.get("status") == "FAIL" else corrected.get("uncertainty_status", "NOT_SUPPLIED"),
        },
        "next_action": next_action,
        "time_to_value": {"first_qualified_measurement_ns": None,
                          "scope": "not_measured; processing a recording is not physical qualification"},
        "authority": {"part_release": False, "machine_control": False, "cloud_upload": False,
                      "device_configuration": False, "active_authority_restored": False},
        "reference": retained["request_ref"],
    }
    if advanced:
        value["advanced"] = retained
    return value


def _number(value):
    return "Not available" if value is None else format(value, ".9g")


def panel(directory, output, *, request_ref=None):
    """Private, offline HTML with explicit limits and native details disclosure."""
    value = view(directory, request_ref=request_ref, advanced=True)
    esc = lambda x: html.escape(str(x), quote=True)
    check = value["checks"]
    body = f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Link / Digital Inspection</title><style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;background:#0f1611;color:#d8e3bb;font:17px/1.5 system-ui,sans-serif}}
main{{max-width:820px;margin:auto;padding:24px}}section,details{{border:1px solid #57705c;border-radius:8px;padding:20px;margin:20px 0}}
h1{{font-size:clamp(28px,6vw,42px);margin:12px 0}}h2{{font-size:20px}}.reading{{font:clamp(32px,8vw,54px)/1.2 ui-monospace,monospace;overflow-wrap:anywhere}}
.badge{{font-size:14px;letter-spacing:.06em}}.checks{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
p,code,pre{{overflow-wrap:anywhere}}pre{{white-space:pre-wrap;font-size:13px}}summary{{cursor:pointer;min-height:44px;padding:8px}}@media(max-width:460px){{main{{padding:14px}}.checks{{grid-template-columns:1fr}}}}
</style><main><header><p>LINK / NET</p><h1>Digital Inspection</h1>
<p class="badge">RETAINED PREVIEW · {esc(str(value['origin']).upper())} · NOT LIVE</p></header>
<section aria-label="Result"><p class="badge">{esc(value['status'].replace('_',' '))}</p>
<h2>Recorded result</h2><p class="reading">{esc(_number(value['reading']['value']))} {esc(value['reading']['unit'] or '')}</p>
<p>Mean after declared correction, {value['reading']['count']} reading(s).</p>
<p><strong>Qualified measurement: not yet available.</strong></p>
<p>{esc(value['next_action'])}</p></section>
<section aria-label="Trust checks"><h2>What is ready?</h2><div class="checks">
<div><strong>Recording</strong><p>{esc(check['recording'].replace('_',' ').capitalize())}</p></div>
<div><strong>Source quality</strong><p>{esc(check['source_quality'].replace('_',' ').capitalize())}</p></div>
<div><strong>Calculation check</strong><p>{esc(check['arithmetic'])} · not physical validation</p></div>
<div><strong>Calibration</strong><p>{esc(_STATE[check['calibration']])}</p></div>
<div><strong>Uncertainty</strong><p>{esc(check['uncertainty'].replace('_',' ').capitalize())}</p></div>
</div><p>No part release, machine command or cloud upload is authorized.</p></section>
<details><summary>Advanced: evidence, units, clocks and execution records</summary>
<pre>{esc(json.dumps(value, indent=2, allow_nan=False))}</pre></details>
<footer><p>Original data stays local. Reopening this page does not reconnect a device or restore permission.</p></footer></main></html>'''
    path = safe_path(output)
    with path.open("xb") as stream:
        stream.write(body.encode("utf-8"))
    return {"path": str(path), "sha256": sha256(body.encode("utf-8")).hexdigest()}


def run(request, output, *, raw=None):
    """One action wraps the unchanged scalar graph and creates its operator page."""
    start = time.monotonic_ns()
    started = datetime.now(timezone.utc).isoformat()
    completed = io.run(request, output, raw=raw)
    value = view(output, request_ref=completed["request_ref"])
    elapsed = time.monotonic_ns() - start
    if elapsed < 0:
        raise RuntimeError("Host monotonic clock regressed; no elapsed-time claim")
    metrics = {"schema": "ciw.link-operator-processing.v1", "request_ref": value["reference"],
               "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
               "recorded_processing_duration_ns": elapsed, "clock": "host_monotonic",
               "first_qualified_measurement_ns": None,
               "scope": "local processing of an existing recording, not install time or qualification"}
    save_new(safe_path(output) / "operator-processing.json", metrics)
    value["report"] = panel(output, safe_path(output) / "inspection.html", request_ref=value["reference"])
    value["processing"] = metrics
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(prog="net link inspection", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("demo", help="One-command synthetic recorded preview; no hardware")
    p.add_argument("--output-dir", type=Path, required=True)
    p = sub.add_parser("run", help="Process an engineer-prepared, retained scalar request")
    p.add_argument("request", type=Path); p.add_argument("--request-ref", required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p = sub.add_parser("status", help="Read existing evidence without a device or network")
    p.add_argument("directory", type=Path); p.add_argument("--request-ref", required=True)
    for p in sub.choices.values():
        p.add_argument("--advanced", action="store_true", help="Print detailed scientific records as JSON")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            from .link_study_cli import example
            result = run(example(), args.output_dir)
        elif args.command == "run":
            raw = read_bytes(safe_path(args.request), limit=study.MAX_BYTES)
            c.content_ref(args.request_ref)
            if c.bytes_ref(raw) != args.request_ref:
                raise ValueError("Request differs from the externally selected input")
            result = run(c.loads(raw, study.MAX_BYTES), args.output_dir, raw=raw)
        else:
            result = view(args.directory, request_ref=args.request_ref, advanced=args.advanced)
        if args.advanced:
            if "advanced" not in result:
                result["advanced"] = view(args.output_dir, request_ref=result["reference"], advanced=True)["advanced"]
            print(json.dumps(result, indent=2, allow_nan=False))
        else:
            print("Digital Inspection / " + str(result["origin"]).upper() + " / RECORDED PREVIEW")
            print("Result: " + _number(result["reading"]["value"]) + " " + (result["reading"]["unit"] or ""))
            print("Status: " + result["status"].replace("_", " ") + "; qualification HELD")
            print("Next: " + result["next_action"])
            if "report" in result: print("Open locally: " + result["report"]["path"])
            print("Request reference: " + result["reference"])
            print("Advanced details: add --advanced. No cloud connection is needed.")
        # Success is command completion only. No qualified-success exit code exists.
        return 2 if result["status"] == "INCOMPLETE" else 1 if result["status"] == "REVIEW_REQUIRED" else 0
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, OverflowError, RecursionError) as exc:
        if args.advanced:
            print(json.dumps({"status": "REFUSED", "reason": str(exc), "qualification": "HELD"}), file=sys.stderr)
        else:
            print("Digital Inspection needs attention: " + str(exc) + ". No qualification or authority was granted.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
