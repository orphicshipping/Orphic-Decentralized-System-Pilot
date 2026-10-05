"""Run, inspect and reproduce bounded material benchmarks, without state admission.

This module extends PR173's benchmark artifact path. It is not an alternative
operation registry, evidence ledger, workspace format or equipment controller.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import sys
import uuid

from . import thermochemical_benchmark as tc
from . import polymer_viscoelastic as pv
from . import polymer_population as pp

Refusal = tc.Refusal
RESULT_SCHEMA = "ciw.materials-benchmark-result.v1"
MAX_BYTES = 8*1024*1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
MODULES = {tc.REQUEST_SCHEMA: tc, pv.SCHEMA: pv, pp.SCHEMA: pp}


def _guard_tree(data):
    """Bounded JSON tree independent of interpreter recursion settings."""
    stack=[(data,0)]; count=0
    while stack:
        value,depth=stack.pop();count+=1
        if depth>16 or count>250000:
            raise Refusal("JSON tree exceeds depth/node budget")
        if type(value) is dict:
            if any(type(k) is not str or len(k)>128 for k in value):
                raise Refusal("Invalid JSON object key")
            stack.extend((v,depth+1) for v in value.values())
        elif type(value) is list:
            stack.extend((v,depth+1) for v in value)
        elif type(value) in (int,float):
            tc._number(value,-1e308,1e308,"JSON number")
        elif type(value) is str:
            if len(value)>1024:raise Refusal("JSON string exceeds budget")
        elif value is not None and type(value) is not bool:
            raise Refusal("Unsupported JSON value type")


def digest(data):
    return hashlib.sha256(tc._canonical(data)).hexdigest()


def _module(request):
    if type(request) is not dict or type(request.get("schema")) is not str:
        raise Refusal("Request requires a declared schema")
    module = MODULES.get(request["schema"])
    if module is None:
        raise Refusal("Unsupported benchmark schema; no arbitrary provider dispatch")
    return module


def _source(module):
    return hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()


def _closure():
    return {"materials_bench.py":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "thermochemical_benchmark.py":_source(tc),
            "polymer_viscoelastic.py":_source(pv), "polymer_population.py":_source(pp)}


def run(request):
    _guard_tree(request)
    module = _module(request)
    if module is tc:
        return tc.make_report(request)  # Preserve the PR173 result schema and identity.
    p = module.validate_request(request)
    result = module.simulate(p) if module is pv else module.analyze(p)
    body = {"schema": RESULT_SCHEMA, "request": p, "request_sha256": digest(p),
            "kernel_source_sha256": _source(module), "source_files_sha256": _closure(),
            "runtime": {"implementation": platform.python_implementation(),
                        "python_version": platform.python_version(), "platform": sys.platform},
            "result": result}
    return {**body, "content_sha256": digest(body), "execution_id": str(uuid.uuid4())}


def _equal(expected, actual, path="value", *, rtol=1e-12, atol=1e-12):
    """Typed, complete comparison; booleans are never accepted as numbers."""
    if type(expected) in (float,int):
        if type(actual) not in (float,int) or not math.isfinite(actual):
            raise Refusal(f"{path}: expected finite numeric value")
        if not math.isclose(expected,actual,rel_tol=rtol,abs_tol=atol):
            raise Refusal(f"{path}: numerical value differs")
    elif type(expected) is dict:
        tc._keys(actual,set(expected),path)
        for key,value in expected.items(): _equal(value,actual[key],f"{path}.{key}",rtol=rtol,atol=atol)
    elif type(expected) is list:
        if type(actual) is not list or len(expected)!=len(actual):
            raise Refusal(f"{path}: dimensions differ")
        for i,(x,y) in enumerate(zip(expected,actual)): _equal(x,y,f"{path}[{i}]",rtol=rtol,atol=atol)
    elif type(actual) is not type(expected) or actual != expected:
        raise Refusal(f"{path}: exact value or type differs")


def _tc_data(report,p):
    metrics = tc.check_balances(p, report["trajectory"])
    _equal(metrics,report["balance_metrics"],"balance_metrics")
    solver = report["solver"]
    tc._keys(solver,{"algorithm","attempted_steps","accepted_steps","rejected_steps","settings"},"solver")
    if solver["algorithm"] != "python-rk4-step-doubling.v1" or tc._canonical(solver["settings"]) != tc._canonical(p["solver"]):
        raise Refusal("Solver declaration differs")
    for key in ("attempted_steps","accepted_steps","rejected_steps"):
        if type(solver[key]) is not int or not 0 <= solver[key] <= p["solver"]["max_steps"]:
            raise Refusal("Solver accounting must use bounded exact integers")
    if (solver["attempted_steps"] != solver["accepted_steps"]+solver["rejected_steps"]
            or solver["accepted_steps"] < len(p["time_s"])-1):
        raise Refusal("Solver work accounting differs")
    _equal(tc.AUTHORITY,report["authority"],"authority")
    analytic = report["analytic_reference"]
    if p["activation_energy_j_mol"] != 0 and p["reference_rate_s_inv"] != 0:
        _equal({"status":"not_applicable","reason":"temperature-dependent-rate"},analytic,"analytic_reference")
    else:
        tc._keys(analytic,{"status","within_policy","concentration_max_abs_mol_m3",
                          "temperature_change_max_abs_k","heat_max_abs_j","policy"},"analytic_reference")
        _equal("passed",analytic["status"]); _equal(True,analytic["within_policy"])
        _equal({"rtol":2e-8,"atol_concentration_mol_m3":2e-8,
                "atol_temperature_k":2e-6,"atol_heat_j":2e-5},analytic["policy"],"policy",rtol=0,atol=0)
        for key in ("concentration_max_abs_mol_m3","temperature_change_max_abs_k","heat_max_abs_j"):
            tc._number(analytic[key],0,1e15,key)
    # Inspection does not rerun a solver or the analytic trajectory oracle.


def inspect(report):
    _guard_tree(report)
    if type(report) is not dict:
        raise Refusal("Report must be an object")
    schema=report.get("schema")
    common={"schema","request","request_sha256","kernel_source_sha256","runtime","content_sha256","execution_id"}
    if schema==tc.RESULT_SCHEMA:
        keys=common|{"trajectory","balance_metrics","solver","analytic_reference","authority"}
    elif schema==RESULT_SCHEMA:
        keys=common|{"result","source_files_sha256"}
    else:
        raise Refusal("Unsupported result schema")
    if "request_bytes_sha256" in report: keys.add("request_bytes_sha256")
    tc._keys(report,keys,"report")
    for key in ("content_sha256","request_sha256","kernel_source_sha256","request_bytes_sha256"):
        if key in report and (type(report[key]) is not str or not _HEX.fullmatch(report[key])):
            raise Refusal(f"{key}: invalid content identity")
    execution=report["execution_id"]
    try:
        parsed=uuid.UUID(execution)
        if type(execution) is not str or str(parsed)!=execution or parsed.version!=4: raise ValueError
    except (ValueError,AttributeError,TypeError) as exc:
        raise Refusal("Invalid execution identity") from exc
    body={k:v for k,v in report.items() if k not in ("content_sha256","execution_id","request_bytes_sha256")}
    if digest(body)!=report["content_sha256"]:
        raise Refusal("Report content integrity differs")
    module=_module(report["request"])
    p=module.validate_request(report["request"])
    if digest(p)!=report["request_sha256"]:
        raise Refusal("Normalized request identity differs")
    runtime=report["runtime"]
    tc._keys(runtime,{"implementation","python_version","platform"},"runtime")
    if any(type(v) is not str or not 1<=len(v)<=128 for v in runtime.values()):
        raise Refusal("Invalid observed runtime labels")
    if schema==tc.RESULT_SCHEMA:
        if module is not tc: raise Refusal("Thermochemical report binds a different model")
        _tc_data(report,p)
    else:
        if module is tc: raise Refusal("Thermochemical model requires its original report schema")
        tc._keys(report["source_files_sha256"],set(_closure()),"source_files_sha256")
        for value in report["source_files_sha256"].values():
            if type(value) is not str or not _HEX.fullmatch(value):
                raise Refusal("Invalid source closure identity")
        if report["source_files_sha256"][Path(module.__file__).name]!=report["kernel_source_sha256"]:
            raise Refusal("Kernel/closure source identities differ")
        result=report["result"]
        if module is pv:
            tc._keys(result,{"trajectory","dynamic_moduli","balance_metrics","work_accounting","authority"},"result")
            _equal(pv.check_balances(p,result["trajectory"]),result["balance_metrics"],"balance_metrics")
            _equal(pv.dynamic_moduli(p),result["dynamic_moduli"],"dynamic_moduli")
            accounting={"segments":len(p["time_s"])-1,
                        "mode_updates":(len(p["time_s"])-1)*len(p["modes"]),
                        "frequency_mode_evaluations":len(p["frequency_hz"])*len(p["modes"])}
            tc._keys(result["work_accounting"],set(accounting),"work_accounting")
            if any(type(result["work_accounting"][k]) is not int or result["work_accounting"][k]!=v for k,v in accounting.items()):
                raise Refusal("Mode work accounting differs")
            _equal(pv.AUTHORITY,result["authority"],"authority")
        else:
            _equal(pp.analyze(p),result,"population_result")
    return {"schema":"ciw.materials-benchmark-inspection.v1", "verification_id":str(uuid.uuid4()),
            "execution_id":execution,"content_sha256":report["content_sha256"],
            "complete_report_sha256":digest(report),
            "status":"passed", "scope":"integrity-and-algebraic-consistency-only",
            "solver_rerun":False,"physical_validation":False,"state_admission":False,
            "source_matches_current_kernel":report["kernel_source_sha256"]==_source(module),
            "source_closure_matches_current":report.get("source_files_sha256")==_closure() if schema==RESULT_SCHEMA else None,
            "runtime_authenticated":False,"execution_authenticated":False,
            "raw_request_bytes_verified":False}


def _compare_material_results(module, original, fresh):
    """Quantity-specific reproduction policies; no mixed-unit error score."""
    policies = {}
    def compare_quantity(name, a, b, unit, atol, rtol=2e-10):
        _equal(a, b, name, rtol=rtol, atol=atol)
        policies[name] = {"unit": unit, "rtol": rtol, "atol": atol}
    if module is pv:
        a, b = original["trajectory"], fresh["trajectory"]
        for name in ("time_s", "strain", "mode_order"):
            _equal(a[name], b[name], name, rtol=0, atol=0)
        for name in ("mode_stress_pa", "stress_pa"):
            compare_quantity(name, a[name], b[name], "Pa", 1e-7)
        for name in ("stored_energy_j_m3", "work_j_m3", "dissipated_energy_j_m3"):
            compare_quantity(name, a[name], b[name], "J/m^3", 1e-9)
        for name in ("storage_modulus_pa", "loss_modulus_pa"):
            compare_quantity(name, original["dynamic_moduli"][name], fresh["dynamic_moduli"][name], "Pa", 1e-7)
        # Sampling, semantics, counters and authority were also checked by inspect.
        for name in ("frequency_hz", "convention"):
            _equal(original["dynamic_moduli"][name], fresh["dynamic_moduli"][name], name, rtol=0, atol=0)
        for name in ("work_accounting", "authority"):
            _equal(original[name], fresh[name], name, rtol=0, atol=0)
        # Algebraic residuals are recomputed on inspection; they are not global
        # error estimates or part of the cross-runtime trajectory comparison.
    else:
        for name in ("bin_order", "normalization", "authority"):
            _equal(original[name], fresh[name], name, rtol=0, atol=0)
        for name in ("number_fraction", "mass_fraction", "dispersity"):
            compare_quantity(name, original[name], fresh[name], "1", 1e-14, rtol=2e-12)
        for name in ("number_average_kg_mol", "mass_average_kg_mol", "z_average_kg_mol"):
            compare_quantity(name, original[name], fresh[name], "kg/mol", 1e-12, rtol=2e-12)
    return {"within_policy": True, "quantity_policies": policies}


def reproduce(report):
    """Fresh current-implementation reproduction, not independent solver verification."""
    inspected = inspect(report)
    fresh = run(report["request"])
    module = _module(report["request"])
    if module is tc:
        comparison = tc.compare_trajectories(report["request"], report["trajectory"], fresh["trajectory"])
        if not comparison["within_policy"]:
            raise Refusal("Thermochemical reproduction differs")
    else:
        comparison = _compare_material_results(module, report["result"], fresh["result"])
    return {"schema": "ciw.materials-benchmark-reproduction.v1",
            "verification_id": str(uuid.uuid4()), "original_execution_id": report["execution_id"],
            "original_complete_report_sha256": inspected["complete_report_sha256"],
            "fresh_report": fresh, "comparison": comparison, "status": "passed",
            "scope": "current-kernel-reexecution-not-independent-verification",
            "physical_validation": False, "state_admission": False}


def _load(raw):
    if type(raw) is not bytes or len(raw)>MAX_BYTES: raise Refusal("JSON exceeds 8 MiB byte budget")
    def invalid(value): raise Refusal(f"Non-finite JSON constant: {value}")
    try:
        value=json.loads(raw.decode("utf-8"),object_pairs_hook=tc._unique_object,parse_constant=invalid)
        _guard_tree(value)
        if type(value) is not dict:raise Refusal("JSON root must be an object")
        return value
    except (ValueError,RecursionError) as exc:
        raise Refusal(f"Invalid bounded JSON: {exc}") from exc


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("run","inspect","reproduce"))
    parser.add_argument("input",type=Path)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args(argv)
    try:
        with args.input.open("rb") as stream: raw=stream.read(MAX_BYTES+1)
        data=_load(raw)
        result={"run":run,"inspect":inspect,"reproduce":reproduce}[args.action](data)
        if args.action=="run": result["request_bytes_sha256"]=hashlib.sha256(raw).hexdigest()
        text=json.dumps(result,indent=2,allow_nan=False)+"\n"
        if args.output: tc._write_new_report(args.output,text)
        else:sys.stdout.write(text)
        return 0
    except (ValueError,OSError,TypeError,OverflowError,RecursionError) as exc:
        print(f"Materials benchmark refused: {exc}",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
