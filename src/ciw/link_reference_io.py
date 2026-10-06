"""Create-only published-reference investigations; no network or instrument access."""
from __future__ import annotations
import argparse
from copy import deepcopy
import html
import json
import os
from pathlib import Path
import sys
import tempfile

from . import link_contract as c, link_references as refs
from .control_contracts import save_new, validate_artifact
from .control_plane import run_graph
from .control_checks import inspect_record
from .hardware_workflow import read_bytes
from .link_store import safe_path
from .session import Session, loads_json

FILES=("request.json","workspace.json","graph.json")
MAX_WORKSPACE=8*1024*1024


def run(value,output,*,raw=None):
    raw=c.bounded(value,refs.MAX_BYTES) if raw is None else raw
    if c.canonical_json(c.loads(raw,refs.MAX_BYTES))!=c.canonical_json(value):
        raise ValueError("Original request bytes differ from supplied request")
    refs.validate_request(value)
    registry=refs.registry()
    source=refs.make_source(value,c.bytes_ref(raw))
    root=safe_path(output);root.mkdir(parents=True,exist_ok=False)
    with (root/FILES[0]).open("xb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    session=Session(source,root,operations=registry.operations)
    outcome=run_graph(session,refs.graph(),registry)
    session.save_workspace(root/FILES[1]);save_new(root/FILES[2],outcome)
    return inspect(root)


def read(directory):
    refs.register();root=safe_path(directory)
    raw=read_bytes(root/FILES[0],limit=refs.MAX_BYTES)
    value=c.loads(raw,refs.MAX_BYTES);refs.validate_request(value)
    saved=loads_json(read_bytes(root/FILES[1],limit=MAX_WORKSPACE).decode())
    c.keys(saved,{"workspace_version","saved_at","run","selection","results","view_settings","executions"})
    with tempfile.TemporaryDirectory(prefix="net-reference-read-") as temp:
        p=Path(temp);save_new(p/FILES[1],saved)
        session=Session.from_workspace(p/FILES[1],p/"restored")
    if c.canonical_json(session.run)!=c.canonical_json(refs.make_source(value,c.bytes_ref(raw))):
        raise ValueError("Reference workspace does not bind original request bytes")
    graph=loads_json(read_bytes(root/FILES[2],limit=MAX_WORKSPACE).decode())
    inspect_record(graph)
    if (c.canonical_json(graph["experiment"])!=c.canonical_json(refs.graph()) or
        graph["source_evidence_id"]!=session.run["evidence_id"]):
        raise ValueError("Reference graph does not match the original source/operations")
    seen,results=set(),set()
    for node in graph["nodes"].values():
        if node["status"] in {"completed","refused"}:
            e=node["execution"];key=e["execution_id"]
            if key in seen or c.canonical_json(session.executions.get(key))!=c.canonical_json(e):
                raise ValueError("Reference graph execution mismatch")
            seen.add(key)
        if node["status"]=="completed":
            r=node["result"];key=r["result_id"]
            if key in results or c.canonical_json(session.results.get(key))!=c.canonical_json(r):
                raise ValueError("Reference graph result mismatch")
            results.add(key)
    if seen!=set(session.executions) or results!=set(session.results):
        raise ValueError("Reference graph must account for all original occurrences")
    executions=list(session.executions.values())
    ops=[e["operation_id"] for e in executions]
    if len(ops) not in (1,2) or set(ops)!=set(refs.OPS[:len(ops)]):
        raise ValueError("Reference investigation contains unsupported occurrences")
    return value,raw,session,graph


def inspect(directory):
    value,raw,session,graph=read(directory)
    return {"schema":"ciw.link-reference-inspection.v1","profile_id":value["profile_id"],"status":graph["status"],
        "evidence_id":session.run["evidence_id"],"request_bytes_ref":c.bytes_ref(raw),
        "results":{r["operation_id"]:deepcopy(r) for r in session.results.values()},
        "executions":deepcopy(session.executions),"limits":dict(refs.LIMITS)}


def replay(directory,output):
    value,raw,_,_=read(directory)
    return run(value,output,raw=raw)


def export(directory,output):
    value,raw,session,_=read(directory)
    results={r["operation_id"]:r for r in session.results.values()}
    if not all(op in results for op in refs.OPS):
        raise ValueError("Only a complete reference review can export derived records")
    review=results[refs.OPS[0]]["data"]
    payload=c.unpack(value["payload"],8192)
    validate_artifact(review["source_artifact"],payload)
    root=safe_path(output);root.mkdir(parents=True,exist_ok=False)
    # Original source and investigation bytes, not a reconstructed fixture.
    for name in FILES:
        with (root/name).open("xb") as f:f.write(read_bytes(safe_path(directory)/name,limit=MAX_WORKSPACE))
    with (root/"source.bin").open("xb") as f:f.write(payload)
    save_new(root/"source-artifact.json",review["source_artifact"])
    save_new(root/"reference-review.json",review)
    save_new(root/"reference-audit.json",results[refs.OPS[1]]["data"])
    for index,row in enumerate(review["data"]["rows"]):
        if "covariance" in row:
            save_new(root/f"covariance-{index:02d}.json",row["covariance"])
            save_new(root/f"magnitude-covariance-{index:02d}.json",row["magnitude_covariance"])
    copied=inspect(root)
    if copied["evidence_id"]!=session.run["evidence_id"] or set(copied["executions"])!=set(session.executions):
        raise ValueError("Original investigation changed during export")
    # Manifest last. A process interruption leaves no completion manifest.
    manifest={"schema":"ciw.link-reference-export.v1","profile_id":value["profile_id"],
        "source_evidence_id":session.run["evidence_id"],"result_ids":[results[op]["result_id"] for op in refs.OPS],
        "files":{p.name:c.bytes_ref(p.read_bytes()) for p in sorted(root.iterdir())},"limits":dict(refs.LIMITS)}
    save_new(root/"export-manifest.json",manifest)
    return manifest


def review_export(directory):
    root=safe_path(directory)
    manifest=c.loads(read_bytes(root/"export-manifest.json",limit=refs.MAX_BYTES),refs.MAX_BYTES)
    c.keys(manifest,{"schema","profile_id","source_evidence_id","result_ids","files","limits"})
    if manifest["schema"]!="ciw.link-reference-export.v1" or c.canonical_json(manifest["limits"])!=c.canonical_json(refs.LIMITS):
        raise ValueError("Unsupported reference export manifest")
    if type(manifest["files"]) is not dict or not 7<=len(manifest["files"])<=41:
        raise ValueError("Unbounded or malformed reference export")
    expected=set(manifest["files"])|{"export-manifest.json"}
    if any(type(name) is not str or Path(name).name!=name for name in expected):
        raise ValueError("Only direct regular export files are accepted")
    if {p.name for p in root.iterdir()}!=expected:
        raise ValueError("Missing or extra reference export files")
    for name,digest in manifest["files"].items():
        c.content_ref(digest)
        if c.bytes_ref(read_bytes(safe_path(root/name),limit=MAX_WORKSPACE))!=digest:
            raise ValueError("Reference export byte mismatch")
    value,_,session,_=read(root)
    results={r["operation_id"]:r for r in session.results.values()}
    if set(results)!=set(refs.OPS):raise ValueError("Export must preserve both original operations")
    review=results[refs.OPS[0]]["data"]
    derivatives={"source-artifact.json":review["source_artifact"],"reference-review.json":review,
                 "reference-audit.json":results[refs.OPS[1]]["data"]}
    for index,row in enumerate(review["data"]["rows"]):
        if "covariance" in row:
            derivatives[f"covariance-{index:02d}.json"]=row["covariance"]
            derivatives[f"magnitude-covariance-{index:02d}.json"]=row["magnitude_covariance"]
    if set(manifest["files"])!=set(FILES)|{"source.bin"}|set(derivatives):
        raise ValueError("Reference export does not account for the exact derivative family")
    for name,native in derivatives.items():
        if c.canonical_json(c.loads(read_bytes(root/name,limit=MAX_WORKSPACE),MAX_WORKSPACE))!=c.canonical_json(native):
            raise ValueError("Rehashed reference derivative contradicts original occurrence")
    if (read_bytes(root/"source.bin",limit=8192)!=c.unpack(value["payload"],8192)
            or manifest["profile_id"]!=value["profile_id"] or manifest["source_evidence_id"]!=session.run["evidence_id"]
            or manifest["result_ids"]!=[results[op]["result_id"] for op in refs.OPS]):
        raise ValueError("Reference export occurrence/source mismatch")
    return {"status":"EXPORT_CHECKED","profile_id":value["profile_id"],"source_evidence_id":session.run["evidence_id"],
            "files_checked":len(manifest["files"]),"new_execution":False,"limits":dict(refs.LIMITS)}


def agent(directory):
    read(directory)
    from .agent_api import AgentHost
    return AgentHost(registry=refs.registry(bind=False),inputs={
        "investigation":read_bytes(safe_path(directory)/FILES[1],limit=MAX_WORKSPACE)},allow_operations=())


def report(directory,output):
    info=inspect(directory);results=info["results"]
    result=results.get(refs.OPS[0],{}).get("data",{})
    audit=results.get(refs.OPS[1],{}).get("data",{})
    source=refs.PROFILES[info["profile_id"]]
    esc=lambda v:html.escape(str(v),quote=True)
    details=esc(json.dumps(info,indent=2,allow_nan=False))
    if info["profile_id"]=="nist-pontius":
        headers=("Set","Source line","Load / unit unspecified","Deflection / unit unspecified","Residual")
        rows=[(r["set_index"],r["line"],r["load"],r["deflection"],r["residual"]) for r in result.get("data",{}).get("rows",[])]
        note="40 publisher observations; units and acquisition times unspecified. Certified coefficients evaluated, not fitted."
    else:
        headers=("Source line","Frequency / Hz","Magnitude / V/Pa","Variance / (V/Pa)²","Published u / V/Pa")
        rows=[(r["line"],r["frequency_hz"],format(r["magnitude"],".8g"),format(r["magnitude_variance"],".8g"),r["published_standard_uncertainty"]) for r in result.get("data",{}).get("rows",[])]
        note="17 frequency rows only; full original CSV not retained. Pointwise 2×2 covariance preserved; cross-frequency covariance UNKNOWN."
    table="<tr>"+"".join("<th>"+esc(v)+"</th>" for v in headers)+"</tr>"
    table+="".join("<tr>"+"".join("<td>"+esc(v)+"</td>" for v in row)+"</tr>" for row in rows)
    body=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Notation / institutional references</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#101712;color:#d9e8b9;font:16px/1.5 ui-monospace,monospace}}
main{{max-width:1140px;margin:auto;padding:20px}}h1{{font-size:clamp(25px,5vw,42px)}}section{{border:2px solid #68836a;padding:18px;margin:18px 0;box-shadow:5px 5px #040705}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}}.readout{{font-size:28px}}.scroll{{overflow:auto}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:9px;border-bottom:1px solid #68836a}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}summary{{min-height:44px;cursor:pointer}}code,p{{overflow-wrap:anywhere}}</style>
<main><h1>NOTATION / REFERENCE LIBRARY</h1><p>{esc(source['title'])}</p><div class="grid"><section>SOURCE RECORDS<div class="readout">{len(rows)}</div>{esc(source['source_scope'])}</section>
<section>ARITHMETIC AUDIT<div class="readout">{esc(audit.get('status','NOT RUN'))}</div>Mapping and conditional arithmetic only</section>
<section>OUR HARDWARE<div class="readout">NOT QUALIFIED</div>No calibration transfer or device command</section></div>
<section><h2>PUBLISHED EVIDENCE</h2><p>{esc(note)}</p><div class="scroll"><table>{table}</table></div></section>
<section><h2>SOURCE AND RIGHTS</h2><p>{esc(source['source_url'])}</p><p>{esc(source['reference_url'])}</p><p>{esc(source['rights'])}</p><p>Source bytes / {esc(source['sha256'])}</p></section>
<section><details><summary>Original provenance, covariance, execution and audit records</summary><pre>{details}</pre></details></section>
<p>RETAINED / OFFLINE / NO TRAINING OR PHYSICAL ACCEPTANCE</p><code>{esc(info['evidence_id'])}</code></main></html>'''
    with safe_path(output).open("xb") as f:f.write(body.encode())
    return {"status":"RETAINED_REPORT","sha256":c.bytes_ref(body.encode())}


def main(argv=None):
    p=argparse.ArgumentParser(prog="net link reference",description=__doc__)
    subs=p.add_subparsers(dest="command",required=True)
    subs.add_parser("catalog")
    q=subs.add_parser("prepare");q.add_argument("profile",choices=refs.PROFILES);q.add_argument("input");q.add_argument("--output",required=True)
    for name in ("run","inspect","replay","export","review-export","report","mcp"):
        q=subs.add_parser(name);q.add_argument("input")
        if name in ("run","replay","export"):q.add_argument("--output-dir",required=True)
        if name=="report":q.add_argument("--output",required=True)
    a=p.parse_args(argv)
    try:
        if a.command=="catalog":out=refs.catalog()
        elif a.command=="prepare":
            value=refs.request(a.profile,read_bytes(safe_path(a.input),limit=8192));save_new(safe_path(a.output),value);out={"status":"PREPARED","profile_id":a.profile}
        elif a.command=="run":
            raw=read_bytes(safe_path(a.input),limit=refs.MAX_BYTES);out=run(c.loads(raw,refs.MAX_BYTES),a.output_dir,raw=raw)
        elif a.command=="inspect":out=inspect(a.input)
        elif a.command=="replay":out=replay(a.input,a.output_dir)
        elif a.command=="export":out=export(a.input,a.output_dir)
        elif a.command=="review-export":out=review_export(a.input)
        elif a.command=="report":out=report(a.input,a.output)
        else:
            from .agent_mcp import serve
            with agent(a.input) as host:return serve(host,sys.stdin.buffer,sys.stdout.buffer)
        print(json.dumps(out,indent=2,allow_nan=False))
        return 2 if out.get("status")=="incomplete" else 0
    except (ValueError,TypeError,KeyError,OSError,RuntimeError,OverflowError) as exc:
        print(f"net link reference: {exc}",file=sys.stderr);return 2


if __name__=="__main__":raise SystemExit(main())
