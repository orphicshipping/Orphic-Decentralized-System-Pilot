"""Bounded schedule search and diagnostics for the existing manufacturing planner.

Pure planning functions; no providers, device calls, new stores or source edits.
All exact times are rational seconds. Search enumerates precedence-ready operations
and their eligible resources, appending each operation to a resource's order.
"""
from __future__ import annotations

from fractions import Fraction as F
from typing import Any

from .control_contracts import bytes_ref, keys
from .core.identities import content_identity

OPTIONS_PATH = "manufacturing-analysis.json"
MAX_NODES = 20_000
ZERO = F(0)


def rational(value: int | float) -> F:
    # Validation of input numbers belongs to the unchanged definition validator.
    return F(str(value))


def encoded(value: F) -> list[str]:
    return [str(value.numerator), str(value.denominator)]


def decoded(value: Any) -> F:
    if (type(value) is not list or len(value) != 2
            or any(type(x) is not str or len(x) > 400 for x in value)):
        raise ValueError("Invalid exact rational time")
    try:
        result = F(int(value[0]), int(value[1]))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("Invalid exact rational time") from exc
    if encoded(result) != value:
        raise ValueError("Noncanonical rational time")
    return result


def options(files: dict, roles: dict, plan: dict) -> dict:
    from . import sensor_manufacturing as m
    if OPTIONS_PATH not in files:
        return {"node_budget": 2048, "duration_bounds": None, "source_ref": None}
    data = m._json(OPTIONS_PATH, files, roles, {"model.input"})
    keys(data, {"schema", "definition_ref", "node_budget", "duration_bounds", "basis"})
    if (data["schema"] != "ciw.manufacturing-analysis-options.v1"
            or data["definition_ref"] != bytes_ref(files[m.PLAN_PATH])
            or data["basis"] != "declared_duration_intervals_not_probability"):
        raise ValueError("Analysis options must bind the exact original definition and declared basis")
    budget = data["node_budget"]
    if type(budget) is not int or not 1 <= budget <= MAX_NODES:
        raise ValueError("Search node budget must be 1..20000")
    bounds = data["duration_bounds"]
    if bounds is not None:
        steps = {s["operation_id"]: s for s in plan["operations"]}
        keys(bounds, set(steps))
        for name, row in bounds.items():
            keys(row, {"setup_s", "per_batch_s"})
            for field in row:
                pair = m._list(row[field], 2, 2)
                m._number(pair[0]); m._number(pair[1])
                if not rational(pair[0]) <= rational(steps[name][field]) <= rational(pair[1]):
                    raise ValueError("Every duration interval must contain its nominal declaration")
    return {"node_budget": budget, "duration_bounds": bounds, "source_ref": bytes_ref(files[OPTIONS_PATH])}


def _durations(plan: dict) -> dict[str, F]:
    n = plan["order"]["quantity"]
    return {s["operation_id"]: rational(s["setup_s"]) +
            ((n + s["batch_size"] - 1) // s["batch_size"]) * rational(s["per_batch_s"])
            for s in plan["operations"]}


def _row(name: str, resource: str, start: F, end: F) -> dict:
    return {"operation_id": name, "resource_id": resource,
            "start_s": float(start), "finish_s": float(end),
            "start_q": encoded(start), "finish_q": encoded(end)}


def audit_schedule(plan: dict, rows: list[dict]) -> dict:
    """Check a complete schedule independently of the search/eligibility results.

    This is a deterministic schedule-constraint check, not an optimality proof,
    physical qualification, independent third-party audit or verification identity.
    """
    steps = {s["operation_id"]: s for s in plan["operations"]}
    resources = {r["resource_id"]: r for r in plan["resources"]}
    if type(rows) is not list or len(rows) != len(steps):
        raise ValueError("Schedule must cover every operation exactly once")
    finishes: dict[str, F] = {}
    available = dict.fromkeys(resources, ZERO)
    for row in rows:
        keys(row, {"operation_id", "resource_id", "start_s", "finish_s", "start_q", "finish_q"})
        name, rid = row["operation_id"], row["resource_id"]
        if name not in steps or name in finishes or rid not in resources:
            raise ValueError("Duplicate or unknown scheduled operation/resource")
        step, resource = steps[name], resources[rid]
        if (rid not in step["allowed_resources"] or step["batch_size"] > resource["batch_capacity"]
                or not set(step["requires"]) <= set(resource["capabilities"])):
            raise ValueError("Schedule violates an explicit resource capability")
        limits = {x["parameter"]: x for x in resource["limits"]}
        for d in step["demands"]:
            limit = limits.get(d["parameter"])
            if (limit is None or limit["unit"] != d["unit"]
                    or not rational(limit["lower"]) <= rational(d["value"]) <= rational(limit["upper"])):
                raise ValueError("Schedule violates a declared resource envelope")
        if not set(step["depends_on"]) <= set(finishes):
            raise ValueError("Dispatch order is not precedence-compatible")
        start, end = decoded(row["start_q"]), decoded(row["finish_q"])
        batches = (plan["order"]["quantity"] + step["batch_size"] - 1) // step["batch_size"]
        duration = F(str(step["setup_s"])) + batches * F(str(step["per_batch_s"]))
        if (start < 0 or end - start != duration or start < available[rid]
                or any(start < finishes[p] for p in step["depends_on"])):
            raise ValueError("Schedule violates duration, precedence or exclusive-resource occupancy")
        if (type(row["start_s"]) not in (int, float) or type(row["finish_s"]) not in (int, float)
                or row["start_s"] != float(start) or row["finish_s"] != float(end)):
            raise ValueError("Displayed seconds disagree with exact schedule times")
        finishes[name] = end; available[rid] = end
    finish = max(finishes.values(), default=ZERO)
    return {"status": "PASS", "checked_operations": len(rows), "makespan_s": float(finish),
            "makespan_q": encoded(finish), "scope": "schedule_constraints_not_physical_verification"}


def optimize(plan: dict, baseline: dict, budget: int) -> dict:
    """Bounded depth-first branch-and-bound over the declared whole-lot model."""
    from . import sensor_manufacturing as m
    if type(budget) is not int or not 1 <= budget <= MAX_NODES:
        raise ValueError("Invalid deterministic search budget")
    steps = {s["operation_id"]: s for s in plan["operations"]}
    order = m._order(steps)
    durations = _durations(plan)
    resources = sorted(r["resource_id"] for r in plan["resources"])
    old = {r["operation_id"]: r for r in baseline["instructions"]}
    eligible = {name: sorted(set(steps[name]["allowed_resources"]) - set(old[name]["rejected_resources"])) for name in order}
    missing = sorted(n for n in order if not eligible[n])
    if missing:
        return {"status": "INFEASIBLE", "incompatible_operations": missing,
                "schedule": None, "audit": None, "search_nodes": 0,
                "lower_bound_s": None, "absolute_gap_s": None, "optimality": "NOT_APPLICABLE"}

    # Reproduce the original dispatch choices using exact arithmetic; the v1
    # result itself is not modified or reserialized into a different identity.
    finish, available, best_rows = {}, dict.fromkeys(resources, ZERO), []
    for name in order:
        rid = old[name]["resource_id"]
        start = max(available[rid], max((finish[p] for p in steps[name]["depends_on"]), default=ZERO))
        end = start + durations[name]
        best_rows.append(_row(name, rid, start, end)); finish[name] = end; available[rid] = end
    upper = max(finish.values(), default=ZERO)
    audit_schedule(plan, best_rows)
    initial_upper = upper

    def lower(done: dict, avail: dict) -> F:
        earliest = dict(done)
        for name in order:
            if name not in done:
                earliest[name] = max(min(avail[r] for r in eligible[name]),
                    max((earliest[p] for p in steps[name]["depends_on"]), default=ZERO)) + durations[name]
        bound = max(earliest.values(), default=ZERO)
        for rid in resources:
            compulsory = sum((durations[n] for n in order if n not in done and eligible[n] == [rid]), ZERO)
            bound = max(bound, avail[rid] + compulsory)
        return bound

    root_bound = lower({}, dict.fromkeys(resources, ZERO))
    visited, truncated = 0, False
    def visit(done: dict, avail: dict, rows: list) -> None:
        nonlocal upper, best_rows, visited, truncated
        if visited >= budget:
            truncated = True
            return
        visited += 1
        if lower(done, avail) >= upper:
            return
        if len(done) == len(steps):
            upper = max(done.values(), default=ZERO); best_rows = rows
            return
        for name in order:
            if name in done or not set(steps[name]["depends_on"]) <= set(done):
                continue
            pred = max((done[p] for p in steps[name]["depends_on"]), default=ZERO)
            for rid in eligible[name]:
                start = max(pred, avail[rid]); end = start + durations[name]
                visit({**done, name: end}, {**avail, rid: end}, rows + [_row(name, rid, start, end)])
                if visited >= budget:
                    truncated = True
                    return
    visit({}, dict.fromkeys(resources, ZERO), [])
    # Equality of a valid initial lower bound and incumbent establishes optimality
    # even when a budget was reached. Otherwise only completed enumeration does.
    optimum = not truncated or root_bound == upper
    bound = upper if optimum else root_bound
    checked = audit_schedule(plan, best_rows)
    if decoded(checked["makespan_q"]) != upper:
        raise ValueError("Search incumbent disagrees with independent schedule check")
    return {"status": "FEASIBLE", "incompatible_operations": [], "schedule": best_rows, "audit": checked,
            "search_nodes": visited, "node_budget": budget,
            "termination": "BOUND_CLOSED" if root_bound == upper else ("NODE_BUDGET" if truncated else "SEARCH_EXHAUSTED"),
            "optimality": "OPTIMAL_FOR_DECLARED_MODEL" if optimum else "BEST_FOUND_NOT_PROVEN_OPTIMAL",
            "lower_bound_s": float(bound), "lower_bound_q": encoded(bound),
            "absolute_gap_s": float(upper-bound), "absolute_gap_q": encoded(upper-bound),
            "initial_makespan_s": float(initial_upper), "improvement_s": float(initial_upper-upper),
            "model": "nonpreemptive_whole_lot_fixed_durations_exclusive_resources_no_calendars",
            "search": "deterministic_ready_operation_and_resource_branch_and_bound"}


def _dispatch(plan: dict, rows: list, durations: dict) -> tuple[dict, dict]:
    steps = {s["operation_id"]: s for s in plan["operations"]}
    ends, last, parents = {}, {}, {}
    for row in rows:
        name, rid = row["operation_id"], row["resource_id"]
        parents[name] = set(steps[name]["depends_on"]) | ({last[rid]} if rid in last else set())
        ends[name] = max((ends[p] for p in parents[name]), default=ZERO) + durations[name]
        last[rid] = name
    return ends, parents


def diagnostics(plan: dict, rows: list, bounds: dict | None) -> dict:
    durations = _durations(plan)
    ends, parents = _dispatch(plan, rows, durations)
    makespan = max(ends.values(), default=ZERO)
    tails = {}
    for name in reversed(list(ends)):
        tails[name] = max((durations[c] + tails[c] for c in tails if name in parents[c]), default=ZERO)
    details = []
    for name in sorted(ends):
        reduced = {**durations, name: durations[name] * F(9, 10)}
        changed, _ = _dispatch(plan, rows, reduced)
        slack = makespan - ends[name] - tails[name]
        details.append({"operation_id": name, "duration_s": float(durations[name]),
            "total_slack_s": float(slack), "critical": slack == 0,
            "ten_percent_duration_reduction_s": float(durations[name]/10),
            "makespan_reduction_s": float(makespan-max(changed.values(), default=ZERO))})
    details.sort(key=lambda r: (-r["makespan_reduction_s"], r["operation_id"]))
    timing = None
    if bounds is not None:
        n = plan["order"]["quantity"]
        low, high = {}, {}
        for step in plan["operations"]:
            name = step["operation_id"]; b = bounds[name]
            count = (n + step["batch_size"] - 1) // step["batch_size"]
            low[name] = rational(b["setup_s"][0]) + count*rational(b["per_batch_s"][0])
            high[name] = rational(b["setup_s"][1]) + count*rational(b["per_batch_s"][1])
        a, _ = _dispatch(plan, rows, low); b, _ = _dispatch(plan, rows, high)
        lo, hi = max(a.values(), default=ZERO), max(b.values(), default=ZERO)
        timing = {"lower_s": float(lo), "upper_s": float(hi), "lower_q": encoded(lo), "upper_q": encoded(hi),
                  "scope": "fixed_assignment_and_resource_order_all_low_all_high_declared_box",
                  "probability_or_coverage": None, "empirical_validation": "NOT_ESTABLISHED"}
    res = {r["resource_id"]: r for r in plan["resources"]}
    cost, currencies, known = ZERO, set(), True
    for row in rows:
        r = res[row["resource_id"]]
        if r["rate_per_hour"] is None:
            known = False
        else:
            cost += durations[row["operation_id"]] * rational(r["rate_per_hour"])/3600
            currencies.add(r["currency"])
    return {"critical_operations": sorted(r["operation_id"] for r in details if r["critical"]),
            "duration_sensitivity": details, "sensitivity_scope": "one_at_a_time_ten_percent_fixed_dispatch_not_a_derivative",
            "timing_envelope": timing, "modeled_processing_cost": float(cost) if known and len(currencies) == 1 else None,
            "currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "cost_scope": "processing_only_no_capex_components_freight_or_yield"}


def analyze(run: dict, parameters: dict) -> dict:
    from . import sensor_manufacturing as m
    baseline = m.compile_definition(run, parameters)
    spec, inventory, files, roles, plan, _, _, _ = m._definition(run)
    opts = options(files, roles, plan)
    selection = optimize(plan, baseline["schedule"], opts["node_budget"])
    diag = diagnostics(plan, selection["schedule"], opts["duration_bounds"]) if selection["schedule"] is not None else None
    scope = {"product": plan["product"], "order": plan["order"], "inspections": plan["inspections"],
             "requirements_path": plan["requirements_path"], "bom_selection": plan["bom"], "project_mode": spec["mode"],
             "artifacts": {p: v["sha256"] for p, v in inventory["files"].items() if p not in {m.PLAN_PATH, OPTIONS_PATH}}}
    return {**baseline, "schema": "ciw.sensor-manufacturing-plan.v2",
            "analysis": {"options": opts, "selection": selection, "diagnostics": diag,
                         "comparison_scope_ref": content_identity(scope),
                         "baseline_schedule_preserved": True, "physical_improvement_claim": False}}


def compare(directories: list) -> dict:
    """Read-only derived comparison; native computation stays in each NET Session."""
    from . import sensor_manufacturing as m
    if type(directories) is not list or not 2 <= len(directories) <= 8:
        raise ValueError("Compare 2..8 retained analysis sessions")
    snapshots = [m.inspect(p) for p in directories]
    if len({s["snapshot_ref"] for s in snapshots}) != len(snapshots):
        raise ValueError("Comparison requires distinct retained snapshots")
    if any(s["data"] is None or s["data"]["schema"] != "ciw.sensor-manufacturing-plan.v2" for s in snapshots):
        raise ValueError("Comparison requires completed v2 analyses, not v1 or refused sessions")
    base = snapshots[0]["data"]["analysis"]
    rows = []
    for snap in snapshots:
        data = snap["data"]; a = data["analysis"]; selected = a["selection"]
        comparable = a["comparison_scope_ref"] == base["comparison_scope_ref"]
        feasible = selected["status"] == base["selection"]["status"] == "FEASIBLE"
        delta, interval, robust = None, None, "NOT_EVALUATED"
        if comparable and feasible:
            delta = float(decoded(base["selection"]["audit"]["makespan_q"]) - decoded(selected["audit"]["makespan_q"]))
            x = base["diagnostics"]["timing_envelope"]; y = a["diagnostics"]["timing_envelope"]
            if x is not None and y is not None and snap is not snapshots[0]:
                lo = decoded(x["lower_q"]) - decoded(y["upper_q"])
                hi = decoded(x["upper_q"]) - decoded(y["lower_q"])
                interval = [float(lo), float(hi)]
                robust = "GUARANTEED_FASTER_WITHIN_DECLARED_BOXES" if lo > 0 else ("GUARANTEED_SLOWER_WITHIN_DECLARED_BOXES" if hi < 0 else "INCONCLUSIVE_OVERLAPPING_BOUNDS")
        rows.append({"snapshot_ref": snap["snapshot_ref"], "evidence_id": snap["evidence_id"],
            "execution_id": snap["execution_id"], "result_id": snap["result_id"],
            "definition_ref": data["definition_ref"], "comparison_scope_ref": a["comparison_scope_ref"],
            "scope": "SAME_PRODUCT_ORDER_INSPECTIONS_AND_NONPROCESS_BYTES" if comparable else "CHANGED_SCOPE_NO_SPEEDUP_CLAIM",
            "status": selected["status"], "optimality": selected["optimality"],
            "makespan_s": selected["audit"]["makespan_s"] if selected["audit"] else None,
            "nominal_seconds_saved_against_first": delta, "savings_interval_s": interval,
            "robustness": robust, "timing_envelope": a["diagnostics"]["timing_envelope"] if a["diagnostics"] else None})
    result = {"schema": "ciw.manufacturing-comparison-view.v1", "rows": rows,
              "kind": "derived_read_only_view_of_retained_sessions", "new_execution": False,
              "scope": "process_fixture_alternatives_not_native_product_redesign",
              "uncertainty_scope": "cartesian_bounds_no_probabilities_or_cross_scenario_correlation",
              "physical_savings_established": False, "claims": m.CLAIMS}
    result["view_ref"] = content_identity(result)
    return result
