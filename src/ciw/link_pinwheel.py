"""Retained five-blade investigation view and local improvement review lifecycle.

The hub is an inspection of existing NET evidence, never a new canonical state.
Original scalar occurrences remain unchanged. A review adds two computational
occurrences only; a physical experiment requires independent authorization.
"""
from __future__ import annotations
from copy import deepcopy
import html
import json
from pathlib import Path
import tempfile

from . import link_contract as c, link_study as study, link_study_io, link_measurements as measurements
from . import link_measurement_io as exchange, link_improvement as improvement
from .control_checks import inspect_record
from .control_contracts import save_new
from .control_plane import run_graph
from .hardware_workflow import read_bytes
from .link_store import safe_path
from .session import Session

MAX_BYTES = 32 * 1024 * 1024


def _original(bundle):
    with tempfile.TemporaryDirectory(prefix="net-pinwheel-source-") as tmp:
        for name, packed in bundle["source_files"].items():
            (Path(tmp) / name).write_bytes(measurements._unpack(packed))
        request, raw, session, graph = link_study_io.read(tmp)
    return request, raw, session, graph


def plan_for(bundle, *, instance_id, max_standard_uncertainty, candidates=None):
    bundle = measurements.validate(bundle)
    request, _, session, _ = _original(bundle)
    results = {r["operation_id"]: r for r in session.results.values()}
    n = len(results[study.OPS[0]]["data"]["rows"])
    if candidates is None:
        candidates = [{"id": identity, "label": label, "kind": "covariance_scale_counterfactual", "scales": scales,
            "rationale": "Hypothetical 50 percent input-error amplitude; must be experimentally established"}
            for identity, label, scales in (
                ("readings-half", "Reduce reading-error amplitudes", [0.5] * n + [1, 1]),
                ("gain-half", "Reduce gain-error amplitude", [1] * n + [0.5, 1]),
                ("offset-half", "Reduce shared offset-error amplitude", [1] * n + [1, 0.5]))]
    plan = {"schema": improvement.PLAN_SCHEMA, "instance_id": instance_id, "bundle_id": bundle["bundle_id"],
        "source_evidence_id": session.run["evidence_id"], "model_result_id": results[study.OPS[2]]["result_id"],
        "audit_result_id": results[study.OPS[3]]["result_id"],
        "objective": {"quantity": "corrected_mean", "unit": results[study.OPS[2]]["data"]["unit"],
                      "max_standard_uncertainty": max_standard_uncertainty}, "candidates": deepcopy(candidates)}
    return improvement.validate_plan(plan, source=session.run, model=results[study.OPS[2]], audit=results[study.OPS[3]])


def run(bundle, plan, output, *, raw_plan=None):
    bundle = measurements.validate(bundle)
    _, _, original, _ = _original(bundle)
    results = {r["operation_id"]: r for r in original.results.values()}
    plan = improvement.validate_plan(plan, source=original.run, model=results[study.OPS[2]], audit=results[study.OPS[3]])
    if plan["bundle_id"] != bundle["bundle_id"]:
        raise ValueError("Plan belongs to another measurement interpretation bundle")
    raw = c.bounded(plan, improvement.MAX_PLAN) if raw_plan is None else raw_plan
    if not study._equal(c.loads(raw, improvement.MAX_PLAN), plan):
        raise ValueError("Plan differs from its exact retained source bytes")
    registry = improvement.registry()
    root = safe_path(output); root.mkdir(parents=True, exist_ok=False)
    exchange.save(bundle, root / "bundle.json")
    exchange._write(root / "plan.json", raw)
    with tempfile.TemporaryDirectory(prefix="net-pinwheel-restore-") as tmp:
        old = Path(tmp) / "workspace.json"
        old.write_bytes(measurements._unpack(bundle["source_files"]["workspace.json"]))
        session = Session.from_workspace(old, root / "occurrences")
    session.operations = registry.operations
    graph = run_graph(session, improvement.graph(plan, results[study.OPS[2]], results[study.OPS[3]]), registry)
    session.save_workspace(root / "workspace.json")
    save_new(root / "graph.json", graph)
    return inspect(root)


def read(directory):
    improvement.register()
    root = safe_path(directory)
    bundle = exchange.load(root / "bundle.json")
    plan_raw = read_bytes(root / "plan.json", limit=improvement.MAX_PLAN)
    plan = c.loads(plan_raw, improvement.MAX_PLAN)
    _, _, original, _ = _original(bundle)
    old = {r["operation_id"]: r for r in original.results.values()}
    improvement.validate_plan(plan, source=original.run, model=old[study.OPS[2]], audit=old[study.OPS[3]])
    if plan["bundle_id"] != bundle["bundle_id"]:
        raise ValueError("Review plan and original measurement bundle disagree")
    workspace_raw = read_bytes(root / "workspace.json", limit=MAX_BYTES)
    workspace = c.loads(workspace_raw, MAX_BYTES)
    # The local review format only admits fields emitted by the existing Session.
    # Do not change the general Session compatibility policy.
    c.keys(workspace, set(original._workspace()))
    with tempfile.TemporaryDirectory(prefix="net-pinwheel-inspect-") as tmp:
        path = Path(tmp) / "workspace.json"; path.write_bytes(workspace_raw)
        session = Session.from_workspace(path, Path(tmp) / "restored")
    if not study._equal(original.run, session.run):
        raise ValueError("Review workspace changed its original source")
    for namespace in ("results", "executions"):
        for identity, item in getattr(original, namespace).items():
            if not study._equal(getattr(session, namespace).get(identity), item):
                raise ValueError("Review must preserve exact original result and execution occurrences")
    new_exec = {k: v for k, v in session.executions.items() if k not in original.executions}
    new_results = {k: v for k, v in session.results.items() if k not in original.results}
    operations = [v["operation_id"] for v in new_exec.values()]
    if len(operations) not in (1, 2) or set(operations) != set(improvement.OPS[:len(operations)]):
        raise ValueError("Only one review and its dependent arithmetic audit may extend this investigation")
    graph = c.loads(read_bytes(root / "graph.json", limit=MAX_BYTES), MAX_BYTES)
    inspect_record(graph)
    if (not study._equal(graph["experiment"], improvement.graph(plan, old[study.OPS[2]], old[study.OPS[3]]))
            or graph["source_evidence_id"] != original.run["evidence_id"]):
        raise ValueError("Review graph source or declaration mismatch")
    seen_exec, seen_results = set(), set()
    for node in graph["nodes"].values():
        if node["status"] in {"completed", "refused"}:
            occurrence = node["execution"]
            if not study._equal(new_exec.get(occurrence["execution_id"]), occurrence):
                raise ValueError("Graph does not match the retained execution")
            seen_exec.add(occurrence["execution_id"])
        if node["status"] == "completed":
            result = node["result"]
            if not study._equal(new_results.get(result["result_id"]), result):
                raise ValueError("Graph does not match the retained result")
            seen_results.add(result["result_id"])
    if seen_exec != set(new_exec) or seen_results != set(new_results):
        raise ValueError("Unaccounted review occurrences")
    return bundle, plan, session, graph


def inspect(directory):
    bundle, plan, session, graph = read(directory)
    selected = {r["operation_id"]: r for r in session.results.values() if r["operation_id"] in improvement.OPS}
    return {"schema": "ciw.link-pinwheel-inspection.v1", "status": graph["status"], "bundle_id": bundle["bundle_id"],
        "instance_id": plan["instance_id"], "source_evidence_id": session.run["evidence_id"],
        "plan_ref": c.content_identity(plan), "results": deepcopy(selected),
        "original_execution_ids": sorted(e["execution_id"] for e in session.executions.values() if e["operation_id"] in study.OPS),
        "review_execution_ids": sorted(e["execution_id"] for e in session.executions.values() if e["operation_id"] in improvement.OPS),
        "limits": dict(improvement.LIMITS)}


def replay(directory, output):
    bundle, plan, _, _ = read(directory)
    return run(bundle, plan, output, raw_plan=read_bytes(safe_path(directory) / "plan.json", limit=improvement.MAX_PLAN))


def restore_source(directory, output):
    bundle, _, _, _ = read(directory)
    return exchange.restore(bundle, output)


def agent(directory):
    read(directory)
    from .agent_api import AgentHost
    return AgentHost(registry=improvement.registry(bind=False), inputs={
        "investigation": read_bytes(safe_path(directory) / "workspace.json", limit=8 * 1024 * 1024)}, allow_operations=())


def report(directory, output):
    summary = inspect(directory)
    review_result = summary["results"].get(improvement.OPS[0])
    if review_result is None:
        raise ValueError("No completed improvement review to display; inspect the retained refusal")
    review = review_result["data"]
    audit = summary["results"].get(improvement.OPS[1], {}).get("data", {})
    feedback = review["feedback"]
    esc = lambda value: html.escape(str(value), quote=True)
    number = lambda value: "UNKNOWN" if value is None else format(value, ".9g")
    cards = "".join(f'<a class="blade" href="#blade-{i}"><span>0{i+1}</span><h2>{esc(item["name"].upper())}</h2><p>{esc(item["status"])}</p></a>'
                    for i, item in enumerate(review["blades"]))
    details = "".join(f'<section id="blade-{i}"><h2>{esc(item["name"])}</h2><p>{esc(item["meaning"])}</p><code>{esc(item["evidence"])}</code></section>'
                      for i, item in enumerate(review["blades"]))
    rows = "".join('<tr>' + ''.join('<td>'+esc(v)+'</td>' for v in (
        item['candidate_id'], number(item['standard_uncertainty']), number(item['variance_reduction']),
        item['status'], str(item['objective_met']))) + '</tr>' for item in feedback['candidates'])
    actions = ''.join('<p>'+esc(item)+'</p>' for item in review['next_actions'])
    payload = esc(json.dumps(summary, indent=2, allow_nan=False))
    body = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Orphic Systems / Industrial Pinwheel</title><style>
:root{{color-scheme:dark;--bg:#111813;--panel:#19231b;--ink:#d5e5bb;--line:#657a58}}*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 ui-monospace,monospace}}main{{max-width:1180px;margin:auto;padding:20px}}
h1{{font-size:clamp(25px,5vw,42px);letter-spacing:.045em}}h2{{font-size:18px}}a{{color:inherit}}a:focus-visible,summary:focus-visible{{outline:3px solid var(--ink);outline-offset:4px}}
header{{border-bottom:3px solid var(--ink)}}.tag{{border:1px solid var(--line);padding:4px 9px;display:inline-block;margin:4px 4px 4px 0}}
.blades{{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px;margin:20px 0}}.blade,section{{border:2px solid var(--line);padding:16px;background:var(--panel);box-shadow:4px 4px #050805}}
.blade{{text-decoration:none;min-height:140px;overflow-wrap:anywhere}}.blade span{{font-size:24px}}section{{margin:18px 0;scroll-margin-top:12px}}.hub{{text-align:center;border:3px double var(--ink)}}
.readout{{font-size:clamp(24px,6vw,38px);font-variant-numeric:tabular-nums}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%}}td,th{{padding:9px;border-bottom:1px solid var(--line);text-align:left}}code,pre{{overflow-wrap:anywhere;white-space:pre-wrap}}summary{{min-height:48px;padding:10px;cursor:pointer}}.muted{{font-size:14px}}@media(max-width:430px){{main{{padding:12px}}.blades{{grid-template-columns:1fr}}}}
</style><main><header><p>NOTATION SYSTEMS / GAUGELINK → NET</p><h1>INDUSTRIAL PINWHEEL</h1>
<p><span class="tag">{esc(review['hub']['origin']).upper()}</span><span class="tag">RETAINED / LOCAL ONLY</span><span class="tag">PROPOSAL ONLY</span></p></header>
<nav class="blades" aria-label="Industrial pinwheel areas">{cards}</nav>
<section class="hub"><h2>VERIFIED PHYSICAL STATE · TARGET</h2><div class="readout">NOT ESTABLISHED</div><p>One existing evidence substrate. No new state owner.</p><code>{esc(summary['source_evidence_id'])}</code></section>
<section><h2>UNDERSTAND → OPERATIONALIZE → AUGMENT</h2><p>Proposed experiment: <strong>{esc(feedback['proposed_candidate_id'] or 'NO RANKED PROPOSAL')}</strong></p>
<p>Conditional projected variance: {esc(number(feedback['linearized_variance']))} {esc(review['objective']['unit'])}². Objective: standard uncertainty ≤ {esc(number(review['objective']['max_standard_uncertainty']))} {esc(review['objective']['unit'])}.</p>
<div class="scroll"><table><thead><tr><th>Counterfactual</th><th>Predicted standard uncertainty</th><th>Variance reduction</th><th>Status</th><th>Objective met</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="muted">Full covariance, including signed cross-terms. These are hypothetical input-error reductions, not measured improvements. No dollar ROI or optimal sensor-placement claim.</p></section>
<section><h2>INDEPENDENT ARITHMETIC AUDIT</h2><div class="readout">{esc(audit.get('status','NOT RUN'))}</div><p>Does not qualify calibration, a model deployment or a physical instrument.</p></section>
{details}<section><h2>NEXT EXPERIMENT REQUIREMENTS</h2>{actions}</section>
<section><details><summary>Inspect exact operation identities, covariance terms and audit checks</summary><pre>{payload}</pre></details></section>
<footer><p>NO DEVICE CONTACT · NO ACTUATION · NO SHARING · NO AUTOMATIC DEPLOYMENT</p></footer></main></html>'''
    path = safe_path(output); exchange._write(path, body.encode('utf-8'))
    return {"status": "RETAINED_REPORT", "path": str(path), "sha256": c.bytes_ref(body.encode('utf-8'))}
