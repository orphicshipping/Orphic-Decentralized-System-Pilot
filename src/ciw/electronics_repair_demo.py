"""Create an explicitly synthetic software fixture, never bench evidence."""
from pathlib import Path
import csv
import io

from .electronics_repair import SCHEMA, COLUMNS
from .measurement_evidence import dump, digest


def create_demo(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    def evidence(name, content):
        raw = content.encode()
        (root / name).write_bytes(raw)
        return {"file": name, "sha256": digest(raw)}
    calibration = evidence("SYNTHETIC-calibration.txt", "SYNTHETIC SOFTWARE FIXTURE. No real instrument or calibration certificate.\n")
    budget = evidence("SYNTHETIC-budget.txt", "SYNTHETIC SOFTWARE FIXTURE. Example combined standard uncertainties only. Operator budgets must account for calibration, leads, loading, fixture and repeatability.\n")
    limits = evidence("SYNTHETIC-limits.txt", "SYNTHETIC SOFTWARE FIXTURE. Example limits are unapproved and have no equipment applicability. Inclusive interval containment is a software demonstration only.\n")
    checks = [dict(id="output", label="Output rail", test_point="OUT+ to OUT-", quantity="dc_voltage", unit="V", lower="4.75", upper="5.25", coverage_factor="2", condition_ids=["idle", "loaded"]),
              dict(id="input", label="Input rail", test_point="IN+ to IN-", quantity="dc_voltage", unit="V", lower="11.8", upper="12.2", coverage_factor="2", condition_ids=["idle", "loaded"]),
              dict(id="current", label="Input current", test_point="Input series measurement", quantity="dc_current", unit="A", lower="0", upper="0.6", coverage_factor="2", condition_ids=["loaded"])]
    values = {("output", "idle"): ("4.65", "5.01", "V", "0.02"),
              ("output", "loaded"): ("4200", "5240", "mV", "20"),
              ("input", "idle"): ("12.02", "12.01", "V", "0.01"),
              ("input", "loaded"): ("11.99", "12.00", "V", "0.01"),
              ("current", "loaded"): ("680", "480", "mA", "10")}
    rows = []
    for phase, idx, time in (("before", 0, "2026-01-02T10:00:00Z"), ("after", 1, "2026-01-02T12:00:00Z")):
        for (check, condition), valueset in values.items():
            rows.append(dict(zip(COLUMNS, [f"SYNTH-{phase}-{check}-{condition}", phase, check, condition, "SYNTHETIC-DUT-001", "dmm", "cal-A" if check == "current" else "cal-V", "budget-A" if check == "current" else "budget-V", time, valueset[idx], "0", valueset[2], valueset[3], "export", "synthetic example row " + str(len(rows) + 1)])))
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    readings = stream.getvalue()
    source = evidence("SYNTHETIC-original-export.csv", readings)
    measurements = evidence("measurements.csv", readings)
    manifest = dict(schema=SCHEMA, case_id="SYNTHETIC-REPAIR-001", activity="repair", data_origin="synthetic_demo",
        device=dict(manufacturer="SYNTHETIC", model="Illustrative 12 V to 5 V module", serial="SYNTHETIC-DUT-001",
            hardware_revision_before="DEMO-A", hardware_revision_after="DEMO-A", firmware_revision_before="not applicable", firmware_revision_after="not applicable"),
        fixture=dict(id="SYNTHETIC-FIXTURE", description="Illustrative declared setup. No hardware was connected."),
        conditions=[dict(id=name, input_voltage_V="12", load_current_A=load, ambient_temperature_C="23", settling_time_s="30", notes="SYNTHETIC declared condition; not measured bench conditions.") for name, load in (("idle", "0"), ("loaded", "1"))],
        instruments=[dict(id="dmm", manufacturer="SYNTHETIC", model="Example DMM", serial="SYNTHETIC-DMM-001")],
        calibrations=[dict(id="cal-"+unit, instrument_id="dmm", quantity=quantity, unit=unit, range_min="-"+maximum, range_max=maximum, valid_from="2026-01-01T00:00:00Z", valid_until="2026-12-31T23:59:59Z", evidence=calibration) for quantity, unit, maximum in (("dc_voltage", "V", "30"), ("dc_current", "A", "2"))],
        budgets=[dict(id="budget-"+unit, quantity=quantity, unit=unit, scope="combined_standard_declared", evidence=budget) for quantity, unit in (("dc_voltage", "V"), ("dc_current", "A"))],
        sources=[dict(id="export", evidence=source, mapping_notes="Synthetic canonical export copied without remapping. Not a vendor-specific driver or a real export.")],
        measurements=measurements, criteria=dict(document=limits, approved_by=None, approved_at=None, checks=checks),
        interventions=[dict(id="SYNTHETIC-ACTION", timestamp="2026-01-02T11:00:00Z", technician="SYNTHETIC TECHNICIAN", action="Illustrative component replacement", removed_part="SYNTHETIC-C1-old", installed_part="SYNTHETIC-C1-new", notes="Software example only. No physical repair performed.")])
    (root / "case.json").write_bytes(dump(manifest))
    return root / "case.json"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    print(create_demo(parser.parse_args().output_dir))
