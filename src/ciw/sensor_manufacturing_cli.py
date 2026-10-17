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
    timing = data.get("timing_characterization")
    if timing:
        def fmt(value):
            return escape("Not identified" if value is None else (f"{value:g}" if type(value) in (int, float) else str(value)))
        timing_cards = ""
        for item in timing["series"]:
            fit = item["fit"]; old = item["baseline_holdout_errors"]; new = item["fitted_holdout_errors"]
            metrics = (("Setup time / s", fit["setup_s"]["value"] if fit["setup_s"] else None),
                       ("Time per batch / s", fit["per_batch_s"]["value"] if fit["per_batch_s"] else None),
                       ("Original holdout RMSE / s", old["rmse_s"]),
                       ("Fitted holdout RMSE / s", new["rmse_s"] if new else None))
            values = "".join("<div><dt>"+fmt(label)+"</dt><dd>"+fmt(value)+"</dd></div>" for label,value in metrics)
            state = "Candidate for review" if item["status"] == "CANDIDATE_FOR_REVIEW" else "Needs investigation"
            timing_cards += ("<article class=timing-card><h3>"+fmt(item["operation_id"])+"</h3><p><strong>"+
                             fmt(item["semantics"].capitalize())+" duration observations</strong> · "+state+
                             "</p><dl>"+values+"</dl></article>")
        projection = timing["fixed_dispatch_projection"]
        projected = (f"Original route prediction: {projection['baseline_duration_s']['value']:g} s. "
                     f"Candidate prediction: {projection['candidate_duration_s']['value']:g} s. "
                     "This is a model correction, not a process speedup. Unchanged operation times remain declarations."
                     if projection else "No timing update is eligible for the selected dispatch.")
        actions = "".join("<p><strong>"+fmt(r["operation_id"])+"</strong>: "+fmt("; ".join(reason.replace("_", " ").lower() for reason in r["reasons"]))+"</p>" for r in timing["next_experiments"])
        timing_detail = f"""<section class="timing-review"><h2>Evidence-bound timing characterization</h2>
<p>Separate setup and per-batch durations using original native duration observations. Train and holdout assignments bind to the retained protocol.</p>
<div class="timing-cards">{timing_cards}</div>
<p>{escape(projected)}</p><p>Residual statistics are descriptive, not confidence intervals. Parameter uncertainty is not established. No automatic model promotion.</p>{actions}</section>"""
        detail = timing_detail + detail
    uncertainty = data.get("timing_uncertainty")
    if uncertainty:
        cards = ""
        for row in uncertainty["series"]:
            target = row.get("target_mean")
            metrics = [(r["label"] + " standard uncertainty / s", r["standard_uncertainty_s"]) for r in row.get("parameters", [])]
            metrics.append(("Target mean standard uncertainty / s", target["standard_uncertainty_s"] if target else None))
            values = "".join("<div><dt>"+val(label)+"</dt><dd>"+val(None if value is None else f"{value:.6g}")+"</dd></div>" for label,value in metrics)
            screens = "; ".join(r["screen"].replace("_", " ").lower() for r in row.get("heldout_residuals", []))
            cards += ("<article class=timing-card><h3>"+val(row["operation_id"])+"</h3><p>"+
                      val(row.get("review_status", row["status"]).replace("_", " ").lower())+
                      "</p><dl>"+values+"</dl><p>Held-out diagnostics: "+val(screens)+"</p></article>")
        route = uncertainty.get("route_component")
        route_text = ("Candidate route estimate: " + val(route.get("candidate_duration_s")) + " s. Local route measurement contribution: " + val(route.get("standard_uncertainty_s")) +
                      " s standard uncertainty. " + val(route["status"].replace("_", " ").lower())
                      if route else "No route uncertainty is available.")
        detail = ("<section class=timing-review><h2>Correlated timing uncertainty</h2><p>"+
                  val(uncertainty["status"].replace("_", " ").lower())+"</p>"+cards+
                  "<p>"+route_text+"</p><p>Shared errors may cancel in held-out residuals without cancelling in a prediction. "
                  "The full supplied covariance is retained; independence is not assumed. Bands use the explicit multiplier, "
                  "not an asserted confidence probability. Model discrepancy, future process variation and total route uncertainty remain unknown.</p></section>"+detail)
        if uncertainty["propagated_covariance"] is not None:
            detail = detail.replace("Parameter uncertainty is not established.", "The propagated measurement component is shown above; total prediction uncertainty is not established.")
    rows = "".join("<tr>"+"".join("<td>"+val(row[k])+"</td>" for k in
        ("operation_id", "resource_id", "batches", "start_s", "finish_s", "status"))+"</tr>" for row in schedule["instructions"])
    inspect_rows = "".join("<tr>"+"".join("<td>"+val(row[k])+"</td>" for k in
        ("unit_id", "requirement_id", "nominal_comparison", "qualification"))+"</tr>" for row in data["inspections"])
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Notation — manufacturing work package</title><style>
body{{font:16px system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;line-height:1.5}}h1{{line-height:1.15}}
section{{margin:28px 0}}.timing-review{{overflow-wrap:anywhere}}.timing-card{{border:1px solid #bbb;padding:16px;margin:16px 0}}.timing-card h3{{margin:0}}.timing-card dl{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin-bottom:0}}.timing-card dt{{font-size:14px}}.timing-card dd{{font-size:24px;font-weight:600;margin:4px 0}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #ccc}}
code,pre{{overflow-wrap:anywhere;white-space:pre-wrap}}details{{border:1px solid #aaa;padding:12px}}.tag{{border:1px solid;padding:6px 10px;display:inline-block}}@media(max-width:600px){{body{{margin:16px auto;padding:0 12px}}h1{{font-size:28px}}}}
</style><main><p>NOTATION SYSTEMS / NET / MANUFACTURING</p><h1>{val(data['product']['product_id'])} · {val(data['product']['revision'])}</h1>
<p class="tag">Planning computation — not manufacturing release</p>
<p>{val(data['order']['quantity'])} planned units · {val(data['bom']['total_components'])} BOM components · {"baseline modeled duration" if uncertainty else "modeled duration"} {val(schedule['makespan_s'])} s</p>
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
    c = sub.add_parser("run"); c.add_argument("root", type=Path); c.add_argument("request", type=Path); c.add_argument("--output-dir", required=True, type=Path); modes = c.add_mutually_exclusive_group(); modes.add_argument("--analyze", action="store_true", help="Bounded schedule search, bottlenecks and declared timing ranges"); modes.add_argument("--characterize-timing", action="store_true", help="Original native duration observations, setup/batch identification and held-out prediction checks")
    modes.add_argument("--timing-uncertainty", action="store_true", help="Preserve the fit and propagate full joint trial covariance")
    c=sub.add_parser("timing-protocol", help="Prepare hash-bound trial instructions; no device access")
    c.add_argument("root", type=Path); c.add_argument("request", type=Path); c.add_argument("--output", type=Path, required=True)
    for flag in ("operation-id", "resource-id", "stream-path", "model-id", "clock-id", "frame"):
        c.add_argument("--"+flag, required=True)
    c.add_argument("--semantics", choices=("observed", "simulated"), required=True)
    c.add_argument("--train-batches", nargs="+", type=int, required=True)
    c.add_argument("--holdout-batches", nargs="+", type=int, required=True)
    c.add_argument("--max-holdout-error-s", type=float, required=True)
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
            raw=request.read_bytes(); result=m.execute(p.source(args.root,p.loads_json(raw.decode("utf-8")),raw),args.output_dir,analyze=args.analyze,characterize_timing=args.characterize_timing,timing_uncertainty=args.timing_uncertainty)
        elif args.command == "timing-protocol":
            from .sensor_manufacturing_timing import prepare_protocol
            from .control_contracts import bytes_ref
            request=p._destination(args.request)
            if not request.is_file() or request.stat().st_size > 128*1024:
                raise ValueError("Invalid project request")
            raw=request.read_bytes()
            protocol=prepare_protocol(p.source(args.root,p.loads_json(raw.decode("utf-8")),raw),
                operation_id=args.operation_id, resource_id=args.resource_id, stream_path=args.stream_path,
                model_id=args.model_id, clock_id=args.clock_id, frame=args.frame, semantics=args.semantics,
                train_batches=args.train_batches, holdout_batches=args.holdout_batches,
                max_holdout_error_s=args.max_holdout_error_s)
            destination=p._destination(args.output)
            if destination.is_relative_to(p.packets.root_dir(args.root)):
                raise ValueError("Place the proposed protocol outside the original input inventory")
            encoded=(json.dumps(protocol,indent=2,allow_nan=False)+"\n").encode("utf-8")
            with destination.open("xb") as stream: stream.write(encoded)
            result={"status":"TIMING_PROTOCOL_PREPARED", "protocol_ref":bytes_ref(encoded),
                    "output":str(destination), "device_contact":False, "new_measurements":0}
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
