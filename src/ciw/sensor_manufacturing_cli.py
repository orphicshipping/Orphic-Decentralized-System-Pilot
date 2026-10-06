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
    original_makespan = schedule["makespan_s"]
    analysis = data.get("analysis")
    detail = ""
    if analysis:
        selection = analysis["selection"]
        if selection["schedule"] is not None:
            prior = {r["operation_id"]: r for r in schedule["instructions"]}
            schedule = {**schedule, "makespan_s": selection["audit"]["makespan_s"],
                        "instructions": [{**prior[r["operation_id"]], **r} for r in selection["schedule"]]}
        diag = analysis["diagnostics"]
        if diag:
            sensitivities = "".join("<tr>" + "".join("<td>"+escape(str(r[k]))+"</td>" for k in
                ("operation_id", "duration_s", "total_slack_s", "makespan_reduction_s")) + "</tr>"
                for r in diag["duration_sensitivity"])
            envelope = diag["timing_envelope"]
            band = (f"{envelope['lower_s']}–{envelope['upper_s']} seconds under declared duration ranges. "
                    "This is not a confidence interval." if envelope else "Duration uncertainty was not supplied; no timing range is invented.")
            detail = f"""<section><h2>Schedule improvement and uncertainty</h2>
<p>Original greedy schedule: {original_makespan} s. Selected schedule: {schedule['makespan_s']} s.</p>
<p><strong>{escape(selection['optimality'])}</strong> · Search nodes: {selection['search_nodes']} ·
Lower bound: {selection['lower_bound_s']} s · Remaining gap: {selection['absolute_gap_s']} s.</p>
<p>Independent schedule-constraint check: {escape(selection['audit']['status'])}. No physical qualification is implied.</p>
<p>{escape(band)}</p><h3>Where a 10% operation-duration reduction helps</h3>
<p>One operation at a time, preserving selected resource assignment and order. This does not claim that the reduction is physically achievable.</p>
<div class="scroll"><table><tr><th>Operation</th><th>Duration / s</th><th>Total slack / s</th><th>Route saving / s</th></tr>{sensitivities}</table></div></section>"""
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
{detail}<section><h2>Capability-bound route</h2><div class="scroll"><table><tr><th>Operation</th><th>Resource</th><th>Batches</th><th>Start / s</th><th>Finish / s</th><th>State</th></tr>{rows}</table></div></section>
<section><h2>Inspection coverage</h2><p>Nominal comparisons remain distinct from calibration, uncertainty, physical acceptance and unit genealogy.</p><div class="scroll"><table><tr><th>Planned unit</th><th>Requirement</th><th>Comparison</th><th>Qualification</th></tr>{inspect_rows}</table></div></section>
<details><summary>Original artifacts, identities and model limits</summary><pre>{escape(json.dumps(snapshot,indent=2,allow_nan=False))}</pre></details></main></html>'''
    output = p._destination(output)
    # Keep the original session separate from this derived, rebuildable view.
    if output.is_relative_to(Path(directory).absolute()): raise ValueError("Place report outside the retained session")
    with output.open("x", encoding="utf-8") as stream: stream.write(page)
    return {"status": "REPORT_WRITTEN", "path": str(output), "snapshot_ref": snapshot["snapshot_ref"]}


def comparison_report(view: dict, output: Path, sources: list[Path]) -> None:
    """A derived comparison view of original native Session results; no new run."""
    def val(value):
        return escape("Not established" if value is None else str(value))
    labels = {
        "NOT_EVALUATED": "Baseline / not evaluated",
        "GUARANTEED_FASTER_WITHIN_DECLARED_BOXES": "Faster throughout declared ranges",
        "GUARANTEED_SLOWER_WITHIN_DECLARED_BOXES": "Slower throughout declared ranges",
        "INCONCLUSIVE_OVERLAPPING_BOUNDS": "Inconclusive: ranges overlap",
        "SAME_PRODUCT_ORDER_INSPECTIONS_AND_NONPROCESS_BYTES": "Same product and inspection scope",
        "CHANGED_SCOPE_NO_SPEEDUP_CLAIM": "Scope changed: no speedup claim",
    }
    rows = ""
    for i, r in enumerate(view["rows"]):
        interval = r["savings_interval_s"]
        saving = None if interval is None else f"{interval[0]:g} to {interval[1]:g}"
        values = ["Baseline" if i == 0 else f"Candidate {i}", r["makespan_s"],
                  r["nominal_seconds_saved_against_first"], saving,
                  labels[r["robustness"]], labels[r["scope"]]]
        rows += "<tr>" + "".join("<td>"+val(v)+"</td>" for v in values) + "</tr>"
    output = p._destination(output)
    if any(output.is_relative_to(Path(source).absolute()) for source in sources):
        raise ValueError("Place the comparison report outside retained sessions")
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NET manufacturing design comparison</title><style>
body{{font:16px system-ui,sans-serif;max-width:1150px;margin:32px auto;padding:0 16px;line-height:1.5}}h1{{line-height:1.15}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:820px;font-size:14px;font-variant-numeric:tabular-nums}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #bbb}}th{{white-space:nowrap}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere}}details{{margin-top:24px;padding:12px;border:1px solid #aaa}}@media(max-width:600px){{h1{{font-size:28px}}}}
</style><main><p>NOTATION SYSTEMS / NET / PRODUCT–PROCESS CO-DESIGN</p><h1>Manufacturing process comparison</h1>
<p>The first retained session is the baseline. Native product bytes, selected requirements, order and inspection bindings must match before a speedup is reported.</p>
<p>Nominal durations are scenario calculations, not measured production savings. Duration ranges are declared boxes, not confidence intervals. Different scenarios are not assumed correlated.</p>
<div class="scroll"><table><tr><th>Variant</th><th>Duration / s</th><th>Nominal saved / s</th><th>Saving range / s</th><th>Robustness</th><th>Comparison scope</th></tr>{rows}</table></div>
<details><summary>Exact retained snapshots and comparison limits</summary><pre>{escape(json.dumps(view,indent=2,allow_nan=False))}</pre></details></main></html>'''
    with output.open("x", encoding="utf-8") as stream:
        stream.write(page)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("run"); c.add_argument("root", type=Path); c.add_argument("request", type=Path); c.add_argument("--output-dir", required=True, type=Path); c.add_argument("--analyze", action="store_true", help="Bounded schedule search, bottlenecks and declared timing ranges")
    c=sub.add_parser("compare"); c.add_argument("directories", nargs="+", type=Path); c.add_argument("--output", type=Path)
    for name in ("inspect", "replay", "export-inputs", "report"):
        c=sub.add_parser(name); c.add_argument("directory", type=Path); c.add_argument("--expected-snapshot-ref")
        if name in ("replay", "export-inputs"): c.add_argument("--output-dir", type=Path, required=True)
        if name == "report": c.add_argument("--output", type=Path, required=True)
    args=parser.parse_args(argv)
    try:
        if args.command == "run":
            request=p._destination(args.request)
            if not request.is_file() or request.stat().st_size > 128*1024: raise ValueError("Invalid project request")
            raw=request.read_bytes(); result=m.execute(p.source(args.root,p.loads_json(raw.decode("utf-8")),raw),args.output_dir,analyze=args.analyze)
        elif args.command == "compare":
            from .sensor_manufacturing_analysis import compare
            result = compare(args.directories)
            if args.output:
                comparison_report(result, args.output, args.directories)
        elif args.command == "inspect": result=m.inspect(args.directory,args.expected_snapshot_ref)
        elif args.command == "replay": result=m.replay(args.directory,args.output_dir,args.expected_snapshot_ref)
        elif args.command == "report": result=report(args.directory,args.output,args.expected_snapshot_ref)
        else: result=m.export_inputs(args.directory,args.output_dir,args.expected_snapshot_ref)
        print(json.dumps(result,indent=2,allow_nan=False))
        return 2 if result.get("status")=="REFUSED" or (result.get("data") or {}).get("schedule",{}).get("status")=="INFEASIBLE" else 0
    except (ValueError,TypeError,KeyError,OSError,RuntimeError,OverflowError,RecursionError) as exc:
        print(json.dumps({"status":"REFUSED","reason":str(exc),"claims":m.CLAIMS}),file=sys.stderr); return 2


if __name__ == "__main__": raise SystemExit(main())
