import csv
from decimal import Decimal
import io
import json
from pathlib import Path

import pytest

from ciw.electronics_repair import RepairCase, adapt_case, dec, interval_status, main, render_html, COLUMNS
from ciw.electronics_repair_demo import create_demo
from ciw.measurement_evidence import digest, dump


@pytest.fixture
def case(tmp_path):
    return create_demo(tmp_path / "case")


def modify(case, fn):
    m = json.loads(case.read_bytes())
    fn(m)
    case.write_bytes(dump(m))


def rows_change(case, fn):
    m = json.loads(case.read_bytes())
    path = case.parent / m["measurements"]["file"]
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    fn(rows)
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(out.getvalue(), encoding="utf-8")
    m["measurements"]["sha256"] = digest(path.read_bytes())
    case.write_bytes(dump(m))


def test_synthetic_demo_retains_failures_and_partial_result(case):
    report = RepairCase(case).evaluate()
    assert report["pair_count"] == 5 and report["measurement_count"] == 10
    loaded = next(p for p in report["pairs"] if p["check"]["id"] == "output" and p["condition"]["id"] == "loaded")
    assert loaded["before"]["value"] == "4.200"
    assert loaded["after"]["value"] == "5.240"
    assert loaded["after"]["expanded_uncertainty"] == "0.040"
    assert loaded["after"]["raw"]["value"] == "5240"
    assert loaded["before"]["numerical_status"] == "interval_outside_limits"
    assert loaded["after"]["numerical_status"] == "interval_overlaps_limits"
    for pair in report["pairs"]:
        assert "covariance" in pair["delta_uncertainty"]
        for phase in ("before", "after"):
            assert not pair[phase]["declaration_checks_satisfied"]
            assert set(pair[phase]["evidence_blockers"]) == {"criteria_approval_missing", "synthetic_data_not_physical_evidence"}
    assert report["return_to_service"] == "not_authorized_by_this_tool"
    assert report["local_instrument_qualification"] == "not_established"
    assert report["independent_held_out_validation"] == "not_established"
    assert report["hardware_control"] is False


def test_declared_operator_evidence_does_not_authorize_return(case):
    # This changes fixture declarations to exercise the branch, not real evidence.
    def change(m):
        m["data_origin"] = "operator_measurements"
        m["criteria"].update(approved_by="TEST DECLARATION", approved_at="2026-01-01T12:00:00Z")
    modify(case, change)
    report = RepairCase(case).evaluate()
    assert all(p[phase]["declaration_checks_satisfied"] for p in report["pairs"] for phase in ("before", "after"))
    assert report["return_to_service"] == "not_authorized_by_this_tool"
    assert report["safety_certification"] == "not_established"


@pytest.mark.parametrize("value,u,lo,hi,expected", [
    ("5", ".1", "4.9", "5.1", "interval_inside_limits"),
    ("4.8", ".1", "4.9", "5.1", "interval_overlaps_limits"),
    ("4.79", ".1", "4.9", "5.1", "interval_outside_limits"),
    ("5.2", ".1", "4.9", "5.1", "interval_overlaps_limits"),
    ("5.21", ".1", "4.9", "5.1", "interval_outside_limits")])
def test_inclusive_interval_rules(value, u, lo, hi, expected):
    assert interval_status(*map(Decimal, (value, u, lo, hi))) == expected


@pytest.mark.parametrize("value", ["NaN", "Infinity", "", "1e999", "1e-25", "1e13", True, 1.2, "1 V", " 1"])
def test_invalid_decimal_refused(value):
    with pytest.raises(ValueError):
        dec(value)


def test_current_and_correction_conversion(case):
    rows_change(case, lambda rows: rows[-1].update(value="480000", correction="1000", unit="uA", standard_uncertainty="10000"))
    pair = RepairCase(case).evaluate()["pairs"][-1]
    assert Decimal(pair["after"]["value"]) == Decimal(".481")
    assert Decimal(pair["after"]["expanded_uncertainty"]) == Decimal(".02")
    assert Decimal(pair["delta"]) == Decimal("-.199")


@pytest.mark.parametrize("field,value", [("phase","reference"),("unit","ohm"),("unit","mA"),
    ("device_serial","another-device"),("instrument_id","unknown"),("budget_id","budget-A"),
    ("calibration_id","cal-A"),("source_id","missing"),("standard_uncertainty","0"),
    ("standard_uncertainty","-1"),("timestamp","2026-01-02T10:00:00"),("value","NaN")])
def test_incompatible_readings_refused(case, field, value):
    rows_change(case, lambda rows: rows[0].update({field:value}))
    with pytest.raises((ValueError, KeyError)):
        RepairCase(case).evaluate()


@pytest.mark.parametrize("change", [lambda r:r.pop(), lambda r:r.append(dict(r[0])),
    lambda r:r[-1].update(id=r[0]["id"]), lambda r:r[-1].update(condition_id="idle"),
    lambda r:r[0].update(timestamp="2026-01-02T13:00:00Z")])
def test_incomplete_duplicate_or_disordered_matrix_refused(case, change):
    rows_change(case, change)
    with pytest.raises(ValueError):
        RepairCase(case).evaluate()


def test_expired_calibration_is_visible_blocker(case):
    modify(case, lambda m:m["calibrations"][0].update(valid_until="2026-01-01T23:59:59Z"))
    report = RepairCase(case).evaluate()
    assert "calibration_not_applicable_at_measurement_time" in report["pairs"][0]["after"]["evidence_blockers"]


def test_range_and_late_approval_are_blockers(case):
    def change(m):
        m["calibrations"][0]["range_max"] = "4"
        m["criteria"].update(approved_by="TEST", approved_at="2026-01-02T11:30:00Z")
    modify(case, change)
    pair = RepairCase(case).evaluate()["pairs"][0]
    assert "measurement_outside_declared_calibration_range" in pair["after"]["evidence_blockers"]
    assert "criteria_approval_after_measurement" in pair["before"]["evidence_blockers"]
    assert "criteria_approval_after_measurement" not in pair["after"]["evidence_blockers"]


@pytest.mark.parametrize("change", [lambda m:m["criteria"].update(approved_by="TEST"),
    lambda m:m["criteria"]["checks"][0].update(lower="6", upper="5"),
    lambda m:m["criteria"]["checks"][0].update(coverage_factor="0"),
    lambda m:m["criteria"]["checks"][0].update(unit="mV"),
    lambda m:m["criteria"]["checks"][0].update(condition_ids=["idle","idle"]),
    lambda m:m["interventions"][0].update(timestamp="2026-01-02T13:00:00Z"),
    lambda m:m.update(interventions=[]),
    lambda m:m["instruments"].append(dict(m["instruments"][0])),
    lambda m:m["budgets"][0].update(scope="calibration_only")])
def test_inconsistent_case_refused(case, change):
    modify(case, change)
    with pytest.raises(ValueError):
        RepairCase(case).evaluate()


@pytest.mark.parametrize("activity", ["repair", "refurbishment", "retrofit"])
def test_activity_history_and_revisions_retained(case, activity):
    modify(case, lambda m:m.update(activity=activity))
    report = RepairCase(case).evaluate()
    assert report["activity"] == activity and len(report["interventions"]) == 1


def test_refurbishment_can_record_no_intervention(case):
    modify(case, lambda m:m.update(activity="refurbishment", interventions=[]))
    assert RepairCase(case).evaluate()["interventions"] == []


def test_hash_and_path_tampering_refused(case):
    file = case.parent / "measurements.csv"
    file.write_bytes(file.read_bytes()+b"\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        RepairCase(case).evaluate()
    modify(case, lambda m:m["measurements"].update(file="../escape.csv"))
    with pytest.raises(ValueError, match="inside"):
        RepairCase(case).evaluate()


def test_complete_bundle_is_create_only_and_retains_exact_bytes(case, tmp_path):
    before = {p.name:p.read_bytes() for p in case.parent.iterdir()}
    out = tmp_path / "result"
    report = adapt_case(case, out)
    assert (out/"case.json").read_bytes() == case.read_bytes()
    assert json.loads((out/"report.json").read_bytes()) == report
    for item in report["evidence_index"]:
        raw = (out/"raw"/(item["sha256"].split(":")[1]+".bin")).read_bytes()
        assert digest(raw) == item["sha256"]
    assert before == {p.name:p.read_bytes() for p in case.parent.iterdir()}
    with pytest.raises(FileExistsError):
        adapt_case(case, out)


def test_inert_html_and_offline_csp(case):
    report = RepairCase(case).evaluate()
    hostile = '</script><script>window.injected=1</script>'
    report["device"]["model"] = hostile
    html = render_html(report).decode()
    assert hostile not in html and "connect-src 'none'" in html
    assert "script-src 'sha256-" in html
    assert "__DATA__" not in html


def test_cli_refuses_before_creating_output(case, tmp_path):
    rows_change(case, lambda rows:rows.pop())
    out = tmp_path/"bad"
    assert main(["--case",str(case),"--output-dir",str(out)]) == 1
    assert not out.exists()
