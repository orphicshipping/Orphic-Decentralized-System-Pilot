"""Import paired DC bench endpoints for a documented electronics repair case.

This is a vendor-neutral, manually mapped CSV adapter. It does not control a
bench, diagnose component faults, certify safety, or authorize return to service.
"""
from __future__ import annotations

import argparse
import base64
import csv
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from importlib.resources import files
import io
import json
from pathlib import Path
import re
import sys

from .measurement_evidence import EvidenceStore, keys, text, utc, dump, digest

SCHEMA = "ciw.electronics-repair-case.v1"
COLUMNS = "id phase check_id condition_id device_serial instrument_id calibration_id budget_id timestamp value correction unit standard_uncertainty source_id source_locator".split()
UNITS = {"V": ("dc_voltage", Decimal(1)), "mV": ("dc_voltage", Decimal(".001")),
         "A": ("dc_current", Decimal(1)), "mA": ("dc_current", Decimal(".001")),
         "uA": ("dc_current", Decimal(".000001"))}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def dec(token, *, nonnegative=False, positive=False):
    require(type(token) is str and len(token) <= 64 and
            re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,2})?", token),
            "Require a bounded exact decimal string")
    value = Decimal(token)
    # Decimal.__abs__ rounds under the caller's context. Domain validation must
    # compare the original token, including digits immediately beyond a bound.
    require(value.is_finite() and (value == 0 or Decimal("1e-24") <= value.copy_abs() <= Decimal("1e12")),
            "Decimal exceeds supported endpoint range")
    require(not nonnegative or value >= 0, "Require a nonnegative value")
    require(not positive or value > 0, "Require a positive value")
    return value


def index(items, name):
    require(type(items) is list and 0 < len(items) <= 256, "Require bounded nonempty " + name)
    result = {}
    for item in items:
        require(type(item) is dict, "Malformed " + name)
        identity = text(item["id"])
        require(identity not in result, "Duplicate " + name + " identity")
        result[identity] = item
    return result


def interval_status(value, expanded, lower, upper):
    if value - expanded >= lower and value + expanded <= upper:
        return "interval_inside_limits"
    if value + expanded < lower or value - expanded > upper:
        return "interval_outside_limits"
    return "interval_overlaps_limits"


class RepairCase(EvidenceStore):
    def evaluate(self):
        m = self.manifest
        keys(m, "schema case_id activity data_origin device fixture conditions instruments calibrations budgets sources measurements criteria interventions")
        require(m["schema"] == SCHEMA, "Unsupported electronics case schema")
        text(m["case_id"])
        require(m["activity"] in ("repair", "refurbishment", "retrofit"), "Unsupported case activity")
        require(m["data_origin"] in ("synthetic_demo", "operator_measurements"), "Declare measurement origin")
        keys(m["device"], "manufacturer model serial hardware_revision_before hardware_revision_after firmware_revision_before firmware_revision_after")
        for value in m["device"].values():
            text(value)
        keys(m["fixture"], "id description")
        for value in m["fixture"].values():
            text(value)
        conditions = index(m["conditions"], "conditions")
        for c in conditions.values():
            keys(c, "id input_voltage_V load_current_A ambient_temperature_C settling_time_s notes")
            dec(c["input_voltage_V"], positive=True)
            dec(c["load_current_A"], nonnegative=True)
            dec(c["ambient_temperature_C"])
            dec(c["settling_time_s"], nonnegative=True)
            text(c["notes"])
        instruments = index(m["instruments"], "instruments")
        for instrument in instruments.values():
            keys(instrument, "id manufacturer model serial")
            for value in instrument.values():
                text(value)
        calibrations = index(m["calibrations"], "calibrations")
        for cal in calibrations.values():
            keys(cal, "id instrument_id quantity unit range_min range_max valid_from valid_until evidence")
            require(cal["instrument_id"] in instruments, "Unknown calibrated instrument")
            self._quantity(cal)
            require(dec(cal["range_min"]) < dec(cal["range_max"]), "Invalid calibration range")
            require(utc(cal["valid_from"]) <= utc(cal["valid_until"]), "Invalid calibration validity period")
            self.read(cal["evidence"])
        budgets = index(m["budgets"], "uncertainty budgets")
        for budget in budgets.values():
            keys(budget, "id quantity unit scope evidence")
            self._quantity(budget)
            require(budget["scope"] == "combined_standard_declared", "Require declared combined standard uncertainty")
            self.read(budget["evidence"])
        sources = index(m["sources"], "original exports")
        for source in sources.values():
            keys(source, "id evidence mapping_notes")
            text(source["mapping_notes"])
            self.read(source["evidence"])
        criteria = m["criteria"]
        keys(criteria, "document approved_by approved_at checks")
        self.read(criteria["document"])
        approved = criteria["approved_by"] is not None
        require(approved == (criteria["approved_at"] is not None), "Approval identity and time must be supplied together")
        if approved:
            text(criteria["approved_by"])
            utc(criteria["approved_at"])
        checks = index(criteria["checks"], "checks")
        expected = set()
        for check in checks.values():
            keys(check, "id label test_point quantity unit lower upper coverage_factor condition_ids")
            text(check["label"])
            text(check["test_point"])
            self._quantity(check)
            require(dec(check["lower"]) < dec(check["upper"]), "Invalid engineering limits")
            require(0 < dec(check["coverage_factor"]) <= 10, "Unsupported coverage factor")
            ids = check["condition_ids"]
            require(type(ids) is list and len(ids) > 0 and all(type(v) is str for v in ids)
                    and len(set(ids)) == len(ids) and set(ids) <= conditions.keys(), "Invalid required conditions")
            expected.update((phase, check["id"], cid) for cid in ids for phase in ("before", "after"))
        require(len(expected) <= 2048, "Too many required endpoints")
        interventions = m["interventions"]
        require(type(interventions) is list and len(interventions) <= 128, "Invalid intervention list")
        require(interventions or m["activity"] == "refurbishment", "Repair and retrofit cases require an intervention record")
        times, intervention_ids = [], set()
        for intervention in interventions:
            keys(intervention, "id timestamp technician action removed_part installed_part notes")
            for name in ("id", "technician", "action", "notes"):
                text(intervention[name])
            require(intervention["id"] not in intervention_ids, "Duplicate intervention")
            intervention_ids.add(intervention["id"])
            for name in ("removed_part", "installed_part"):
                if intervention[name] is not None:
                    text(intervention[name])
            times.append(utc(intervention["timestamp"]))
        raw = self.read(m["measurements"])
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        require(reader.fieldnames == COLUMNS, "CSV header must match the canonical DC endpoint schema")
        rows, seen, ids, phase_times = {}, set(), set(), {"before": [], "after": []}
        with localcontext() as ctx:
            ctx.prec = 100
            for line, row in enumerate(reader, 2):
                require(line <= 2050 and set(row) == set(COLUMNS) and all(type(v) is str for v in row.values()), "Malformed or oversized CSV")
                for value in row.values():
                    text(value)
                require(row["id"] not in ids, "Duplicate measurement identity")
                ids.add(row["id"])
                key = (row["phase"], row["check_id"], row["condition_id"])
                require(key in expected and key not in seen, "Unexpected or duplicate phase/check/condition endpoint")
                seen.add(key)
                require(row["device_serial"] == m["device"]["serial"], "Measurement belongs to a different device")
                require(row["instrument_id"] in instruments and row["calibration_id"] in calibrations and
                        row["budget_id"] in budgets and row["source_id"] in sources, "Unresolved measurement evidence identity")
                cal, budget, check = calibrations[row["calibration_id"]], budgets[row["budget_id"]], checks[row["check_id"]]
                require(cal["instrument_id"] == row["instrument_id"], "Calibration belongs to a different instrument")
                require(row["unit"] in UNITS and UNITS[row["unit"]][0] == check["quantity"], "Measurement unit/quantity mismatch")
                require(cal["quantity"] == budget["quantity"] == check["quantity"], "Calibration or uncertainty quantity mismatch")
                instant = utc(row["timestamp"])
                phase_times[row["phase"]].append(instant)
                scale = UNITS[row["unit"]][1]
                reading = dec(row["value"]) * scale
                correction = dec(row["correction"]) * scale
                value = reading + correction
                u = dec(row["standard_uncertainty"], positive=True) * scale
                expanded = u * dec(check["coverage_factor"])
                blockers = []
                if not utc(cal["valid_from"]) <= instant <= utc(cal["valid_until"]):
                    blockers.append("calibration_not_applicable_at_measurement_time")
                if not (dec(cal["range_min"]) <= min(reading, value) and max(reading, value) <= dec(cal["range_max"])):
                    blockers.append("measurement_outside_declared_calibration_range")
                if not approved:
                    blockers.append("criteria_approval_missing")
                elif utc(criteria["approved_at"]) > instant:
                    blockers.append("criteria_approval_after_measurement")
                if m["data_origin"] == "synthetic_demo":
                    blockers.append("synthetic_data_not_physical_evidence")
                rows[key] = dict(id=row["id"], phase=row["phase"], source_csv_line=line,
                    value=str(value), unit=check["unit"], standard_uncertainty=str(u), expanded_uncertainty=str(expanded),
                    coverage_factor=check["coverage_factor"], applied_correction=str(correction),
                    numerical_status=interval_status(value, expanded, dec(check["lower"]), dec(check["upper"])),
                    evidence_blockers=blockers, declaration_checks_satisfied=not blockers, raw=row)
            require(seen == expected, "Incomplete before/after matrix; every declared check and condition is required")
            require(max(phase_times["before"]) < min(phase_times["after"]), "Before/after acquisition order is inconsistent")
            if times:
                require(max(phase_times["before"]) < min(times) and max(times) < min(phase_times["after"]),
                        "Interventions must occur after all before measurements and before all after measurements")
            pairs = []
            for check in checks.values():
                for cid in check["condition_ids"]:
                    before, after = rows[("before", check["id"], cid)], rows[("after", check["id"], cid)]
                    pairs.append(dict(id=digest(dump([check["id"], cid])), check=check, condition=conditions[cid],
                        before=before, after=after, delta=str(Decimal(after["value"]) - Decimal(before["value"])),
                        delta_uncertainty="not_computed; shared calibration and before/after covariance require a separate model"))
        return dict(schema="ciw.electronics-repair-report.v1", case_id=m["case_id"], activity=m["activity"],
            data_origin=m["data_origin"], device=m["device"], fixture=m["fixture"], instruments=m["instruments"],
            interventions=interventions, criteria_approval_declared=approved, criteria=criteria,
            manifest_sha256=digest(self.raw_manifest), measurements_sha256=m["measurements"]["sha256"],
            pairs=pairs, measurement_count=len(rows), pair_count=len(pairs),
            evidence_index=[dict(sha256=identity, bytes=len(content)) for identity, content in sorted(self.retained.items())],
            interpretation="Numerical intervals compared with declared limits; file and declaration checks do not authenticate calibration or approval.",
            independence="Before/after measurements of the same device are not an independent held-out validation set.",
            independent_held_out_validation="not_established", local_instrument_qualification="not_established",
            return_to_service="not_authorized_by_this_tool", safety_certification="not_established",
            hardware_control=False, release_status="unchanged")

    @staticmethod
    def _quantity(record):
        require(record["quantity"] in ("dc_voltage", "dc_current") and
                record["unit"] == {"dc_voltage": "V", "dc_current": "A"}[record["quantity"]],
                "Checks, calibration ranges and budgets require canonical DC V or A")


def render_html(report):
    assets = files("ciw").joinpath("web")
    template = assets.joinpath("electronics_repair.html").read_text(encoding="utf-8")
    css = assets.joinpath("electronics_repair.css").read_text(encoding="utf-8")
    js = (assets.joinpath("electronics_repair_scene.js").read_text(encoding="utf-8") + "\n" +
          assets.joinpath("electronics_repair.js").read_text(encoding="utf-8"))
    hashed = lambda value: base64.b64encode(sha256(value.encode()).digest()).decode()
    replacements = {"__STYLE__": css, "__SCRIPT__": js,
        "__DATA__": base64.b64encode(dump(report)).decode(),
        "__CSP__": "default-src 'none'; base-uri 'none'; form-action 'none'; connect-src 'none'; " +
                   f"style-src 'sha256-{hashed(css)}'; script-src 'sha256-{hashed(js)}'; img-src data:"}
    for key, value in replacements.items():
        require(template.count(key) == 1, "Inconsistent repair viewer assets")
        template = template.replace(key, value)
    raw = template.encode()
    require(len(raw) <= 12 * 1024 * 1024, "Repair viewer exceeds byte budget")
    return raw


def adapt_case(manifest_path, output_dir):
    case = RepairCase(manifest_path)
    report = case.evaluate()
    html = render_html(report)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    (target / "raw").mkdir()
    (target / "case.json").write_bytes(case.raw_manifest)
    for identity, content in case.retained.items():
        (target / "raw" / (identity.split(":")[1] + ".bin")).write_bytes(content)
    (target / "index.html").write_bytes(html)
    (target / "report.json").write_bytes(dump(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        report = adapt_case(args.case, args.output_dir)
        print(json.dumps({"status": "imported", "pair_count": report["pair_count"],
                          "data_origin": report["data_origin"], "return_to_service": report["return_to_service"]}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, UnicodeError, InvalidOperation, csv.Error) as error:
        print(json.dumps({"status": "refused", "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
