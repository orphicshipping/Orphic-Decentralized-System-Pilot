"""Execute, inspect, replay and export retained manufacturing planning in NET."""
from __future__ import annotations
import argparse
from html import escape
import json
from pathlib import Path
import sys

from . import sensor_manufacturing as m, sensor_project as p


def report(directory: Path, output: Path, expected: str | None = None) -> dict:
    snapshot = m.inspect(directory, expected)
    if snapshot["data"] is None: raise ValueError("A completed compilation is required for a report")
    data = snapshot["data"]; schedule = data["schedule"]
    def val(value): return escape("Unknown" if value is None else str(value))
    rows = "".join("<tr>"+"".join("<td>"+val(row[k])+"</td>" for k in
        ("operation_id", "resource_id", "batches", "start_s", "finish_s", "status"))+"</tr>" for row in schedule["instructions"])
    inspect_rows = "".join("<tr>"+"".join("<td>"+val(row[k])+"</td>" for k in
        ("unit_id", "requirement_id", "nominal_comparison", "qualification"))+"</tr>" for row in data["inspections"])
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Notation — manufacturing work package</title><style>
body{{font:16px system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;line-height:1.5}}h1{{line-height:1.15}}
section{{margin:28px 0}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #ccc}}
code,pre{{overflow-wrap:anywhere;white-space:pre-wrap}}details{{border:1px solid #aaa;padding:12px}}.tag{{border:1px solid;padding:6px 10px;display:inline-block}}@media(max-width:600px){{body{{margin:16px auto;padding:0 12px}}h1{{font-size:28px}}}}
</style><main><p>NOTATION SYSTEMS / NET / MANUFACTURING</p><h1>{val(data['product']['product_id'])} · {val(data['product']['revision'])}</h1>
<p class="tag">Planning computation — not manufacturing release</p>
<p>{val(data['order']['quantity'])} planned units · {val(data['bom']['total_components'])} BOM components · modeled duration {val(schedule['makespan_s'])} s</p>
<p>{val(schedule['status'])}. Resource capabilities, batching and times are explicit declarations. No physical unit or machine program is created.</p>
<section><h2>Capability-bound route</h2><div class="scroll"><table><tr><th>Operation</th><th>Resource</th><th>Batches</th><th>Start / s</th><th>Finish / s</th><th>State</th></tr>{rows}</table></div></section>
<section><h2>Inspection coverage</h2><p>Nominal comparisons remain distinct from calibration, uncertainty, physical acceptance and unit genealogy.</p><div class="scroll"><table><tr><th>Planned unit</th><th>Requirement</th><th>Comparison</th><th>Qualification</th></tr>{inspect_rows}</table></div></section>
<details><summary>Original artifacts, identities and model limits</summary><pre>{escape(json.dumps(snapshot,indent=2,allow_nan=False))}</pre></details></main></html>'''
    output = p._destination(output)
    # Keep the original session separate from this derived, rebuildable view.
    if output.is_relative_to(Path(directory).absolute()): raise ValueError("Place report outside the retained session")
    with output.open("x", encoding="utf-8") as stream: stream.write(page)
    return {"status": "REPORT_WRITTEN", "path": str(output), "snapshot_ref": snapshot["snapshot_ref"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("run"); c.add_argument("root", type=Path); c.add_argument("request", type=Path); c.add_argument("--output-dir", required=True, type=Path)
    for name in ("inspect", "replay", "export-inputs", "report"):
        c=sub.add_parser(name); c.add_argument("directory", type=Path); c.add_argument("--expected-snapshot-ref")
        if name in ("replay", "export-inputs"): c.add_argument("--output-dir", type=Path, required=True)
        if name == "report": c.add_argument("--output", type=Path, required=True)
    args=parser.parse_args(argv)
    try:
        if args.command == "run":
            request=p._destination(args.request)
            if not request.is_file() or request.stat().st_size > 128*1024: raise ValueError("Invalid project request")
            raw=request.read_bytes(); result=m.execute(p.source(args.root,p.loads_json(raw.decode("utf-8")),raw),args.output_dir)
        elif args.command == "inspect": result=m.inspect(args.directory,args.expected_snapshot_ref)
        elif args.command == "replay": result=m.replay(args.directory,args.output_dir,args.expected_snapshot_ref)
        elif args.command == "report": result=report(args.directory,args.output,args.expected_snapshot_ref)
        else: result=m.export_inputs(args.directory,args.output_dir,args.expected_snapshot_ref)
        print(json.dumps(result,indent=2,allow_nan=False))
        return 2 if result.get("status")=="REFUSED" or (result.get("data") or {}).get("schedule",{}).get("status")=="INFEASIBLE" else 0
    except (ValueError,TypeError,KeyError,OSError,RuntimeError,OverflowError,RecursionError) as exc:
        print(json.dumps({"status":"REFUSED","reason":str(exc),"claims":m.CLAIMS}),file=sys.stderr); return 2


if __name__ == "__main__": raise SystemExit(main())
