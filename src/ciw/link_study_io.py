"""Create-only scalar-study jobs, existing MCP service, and retained Dashplate."""
from __future__ import annotations
from copy import deepcopy
import html
import json
import os
from pathlib import Path
import tempfile

from . import link_contract as c, link_study as study
from .control_contracts import save_new
from .control_plane import run_graph
from .hardware_workflow import read_bytes
from .link_store import safe_path
from .session import Session, loads_json


def run(request, output, *, raw=None):
    raw=c.bounded(request,study.MAX_BYTES) if raw is None else raw
    if not study._equal(c.loads(raw,study.MAX_BYTES),request):
        raise ValueError("Original request bytes disagree with request")
    source=study.make_source(request,c.bytes_ref(raw))
    root=safe_path(output);root.mkdir(parents=True,exist_ok=False)
    with (root/"request.json").open("xb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    registry=study.registry()
    session=Session(source,root,operations=registry.operations)
    outcome=run_graph(session,study.graph(),registry)
    session.save_workspace(root/"workspace.json")
    save_new(root/"graph.json",outcome)
    return inspect(root,expected_request_ref=c.bytes_ref(raw))


def _read_snapshot(directory, *, expected_request_ref=None):
    """Read each source artifact once, validate that set, and retain its bytes.

    This is a coherent in-memory read, not a filesystem transaction or origin
    authentication. Never validate one path read and later serve a second read.
    """
    if expected_request_ref is not None:
        c.content_ref(expected_request_ref)
    study.register()
    root=safe_path(directory)
    raw=read_bytes(root/"request.json",limit=study.MAX_BYTES)
    if expected_request_ref is not None and c.bytes_ref(raw) != expected_request_ref:
        raise ValueError("Investigation differs from the externally selected request")
    request=c.loads(raw,study.MAX_BYTES)
    workspace_raw=read_bytes(root/"workspace.json",limit=32*1024*1024)
    workspace=loads_json(workspace_raw.decode())
    with tempfile.TemporaryDirectory(prefix="net-link-study-") as temp:
        path=Path(temp);save_new(path/"workspace.json",workspace)
        session=Session.from_workspace(path/"workspace.json",path/"reopened")
    if not study._equal(session.run,study.make_source(request,c.bytes_ref(raw))):
        raise ValueError("Workspace source disagrees with exact retained request bytes")
    result={}
    for execution in session.executions.values():
        op=execution["operation_id"]
        if op not in study.OPS or op in result: raise ValueError("Unexpected or repeated study occurrence")
        result[op]=execution
    expected=study.OPS[:len(result)]
    if set(result)!=set(expected) or not result: raise ValueError("Study occurrences are not a prefix of its fixed graph")
    if any(result[op]["status"]!="completed" for op in expected[:-1]):
        raise ValueError("A refused prerequisite cannot have later study occurrences")
    graph=loads_json(read_bytes(root/"graph.json",limit=32*1024*1024).decode())
    from .control_checks import inspect_record
    inspect_record(graph)
    if (not study._equal(graph["experiment"],study.graph()) or graph["source_evidence_id"]!=session.run["evidence_id"]):
        raise ValueError("Graph source/contract mismatch")
    seen = set()
    for node in graph["nodes"].values():
        if node["status"] in {"completed", "refused"}:
            eid = node["execution"]["execution_id"]
            if not study._equal(session.executions.get(eid), node["execution"]):
                raise ValueError("Graph execution differs from Session")
            seen.add(eid)
        if node["status"]=="completed":
            retained=session.results.get(node["result"]["result_id"])
            if not study._equal(retained,node["result"]): raise ValueError("Graph result differs from Session")
    if seen != set(session.executions):
        raise ValueError("Graph does not account for every retained execution")
    return request,raw,session,graph,workspace_raw


def read(directory, *, expected_request_ref=None):
    """Preserve the four-value reader contract for existing native consumers."""
    return _read_snapshot(directory,expected_request_ref=expected_request_ref)[:4]


def inspect(directory, *, expected_request_ref=None):
    request,raw,session,graph=read(directory,expected_request_ref=expected_request_ref)
    results={r["operation_id"]:r for r in session.results.values()}
    # Origin belongs to the retained observation, not successful decoding. A
    # malformed synthetic reading must not lose its synthetic label on failure.
    captured = study.gateway.validate_capture(request["capture"])
    origins = {item["origin"] for item in captured["observations"]}
    origin = next(iter(origins)) if len(origins) == 1 else None
    return {"schema":"ciw.link-scalar-inspection.v1","status":graph["status"],
        "evidence_id":session.run["evidence_id"],"request_ref":c.bytes_ref(raw),
        "results":deepcopy(results),"executions":deepcopy(session.executions),
        "origin":origin,
        "source_quality_flags":[{"measurement_id":item["measurement_id"],
                                  "flags":deepcopy(item["quality_flags"])}
                                 for item in captured["observations"] if item["quality_flags"]],
        "limits":dict(c.LIMITS)}


def replay(directory, output, *, expected_request_ref=None):
    request,raw,_,_=read(directory,expected_request_ref=expected_request_ref)
    return run(request,output,raw=raw)


def export(directory, output, *, expected_request_ref=None):
    request,_,session,_=read(directory,expected_request_ref=expected_request_ref)
    results={r["operation_id"]:r for r in session.results.values()}
    if not all(op in results for op in study.OPS): raise ValueError("Incomplete study cannot export a completed model")
    # A completed verification operation may legitimately report FAIL.
    # Completion alone never authorizes projection of a known failed result.
    if results[study.OPS[-1]]["data"]["status"] != "PASS":
        raise ValueError("Failed arithmetic audit blocks native scalar export; original evidence retained")
    native=study.native(request,results[study.OPS[0]]["data"],results[study.OPS[1]]["data"],
        execution_id=results[study.OPS[1]]["execution_id"],source_evidence=session.run["evidence_id"])
    output=safe_path(output);output.mkdir(parents=True,exist_ok=False)
    save_new(output/"observations.json",native.pop("stream"))
    if native["covariance"] is not None: save_new(output/"covariance.json",native["covariance"])
    save_new(output/"mapping.json",native)
    return {"status":"EXPORTED","path":str(output),"limits":dict(c.LIMITS)}


def agent(directory, *, expected_request_ref=None):
    """Expose only the exact validated workspace snapshot, with zero grants."""
    *_,workspace_raw=_read_snapshot(directory,expected_request_ref=expected_request_ref)
    from .agent_api import AgentHost, MAX_DOCUMENT
    if len(workspace_raw) > MAX_DOCUMENT:
        raise ValueError("Validated workspace exceeds the existing MCP document bound")
    return AgentHost(registry=study.registry(bind=False),inputs={
        "investigation":workspace_raw},allow_operations=())


def report(directory, output, *, expected_request_ref=None):
    summary=inspect(directory,expected_request_ref=expected_request_ref); results=summary["results"]
    decoded=results.get(study.OPS[0],{}).get("data",{})
    corrected=results.get(study.OPS[1],{}).get("data",{})
    model=results.get(study.OPS[2],{}).get("data",{})
    audit=results.get(study.OPS[3],{}).get("data",{})
    audit_passed=all(op in results for op in study.OPS) and audit.get("status")=="PASS"
    # Preserve failed numerical candidates in Advanced, never headline them as
    # the result of an accepted arithmetic path. Raw readings remain visible.
    readout=model if audit_passed else {}
    table_values=corrected.get("values",[]) if audit_passed else [None]*len(decoded.get("rows",[]))
    readout_note="Passing arithmetic only; physical qualification HELD." if audit_passed else "READOUT WITHHELD: no passing arithmetic audit. Original candidates remain in Advanced."
    esc=lambda x:html.escape(str(x),quote=True)
    display=lambda x: "UNKNOWN" if x is None else format(x,".8g") if type(x) in (float,int) else str(x)
    rows="".join("<tr>"+"".join("<td>"+esc(v)+"</td>" for v in
        (i,display(r["value"]),display(table_values[i]),r["unit"],r["receipt_tick"],
         corrected.get("applicability",["UNKNOWN"]*len(decoded.get("rows",[])))[i]))+"</tr>"
        for i,r in enumerate(decoded.get("rows",[])))
    # Match Link's existing visual grammar, keep source text escaped and offline.
    body=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Notation Link / scalar investigation</title><style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;background:#0f1611;color:#d8e3bb;font:16px/1.5 ui-monospace,monospace}}
main{{max-width:1120px;margin:auto;padding:20px}}h1{{font-size:clamp(24px,5vw,40px);letter-spacing:.05em}}
section{{border:2px solid #57705c;padding:18px;margin:20px 0;box-shadow:5px 5px #050805}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}}
.readout{{font-size:36px;font-variant-numeric:tabular-nums}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #57705c}}
.scroll{{overflow:auto}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}summary{{min-height:44px;padding:10px;cursor:pointer}}code{{overflow-wrap:anywhere}}</style>
<main><header><h1>NOTATION / LINK</h1><p>SCALAR INVESTIGATION · {esc(summary['status'].upper())} · {esc(summary['origin'])}</p>
<p>RETAINED RECORD / NOT A LIVE CONTROLLER / PHYSICAL QUALIFICATION HELD</p><p>{esc(readout_note)}</p></header>
<div class="grid"><section><h2>CORRECTED MEAN</h2><div class="readout">{esc(display(readout.get('mean')))} {esc(model.get('unit',''))}</div></section>
<section><h2>STANDARD UNCERTAINTY</h2><div class="readout">{esc(display(readout.get('standard_uncertainty')))} {esc(model.get('unit',''))}</div><p>Conditional model only</p></section>
<section><h2>ARITHMETIC AUDIT</h2><div class="readout">{esc(audit.get('status','NOT RUN'))}</div><p>Not calibration certification</p></section></div>
<section><h2>RAW → DECLARED CORRECTION → MODEL → AUDIT</h2><div class="scroll"><table><thead><tr><th>Row</th><th>Reported</th><th>Corrected</th><th>Unit</th><th>Receipt tick</th><th>Calibration applicability</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section><details><summary>Evidence, operations and checks</summary><pre>{esc(json.dumps(summary,indent=2,allow_nan=False))}</pre></details></section>
<footer><p>No instrument commands. No hidden interpolation. Original bytes remain retained.</p><code>{esc(summary['evidence_id'])}</code></footer></main></html>'''
    output=safe_path(output)
    with output.open("xb") as stream:stream.write(body.encode())
    return {"status":"RETAINED_REPORT","path":str(output),"sha256":c.bytes_ref(body.encode())}
