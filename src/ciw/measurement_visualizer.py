"""Read-only, offline projection of the retained public measurement evidence v2.

Inventory consistency is not publisher authentication or scientific verification.
This viewer neither executes providers nor recomputes qualification decisions.
"""
from __future__ import annotations

import argparse
import base64
from hashlib import sha256
from importlib.resources import files
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from urllib.parse import urlsplit

from .measurement_evidence import decimal_float, digest, hash_id, load, text

SCHEMA = "ciw.measurement-evidence-view.v1"
FILE_LIMIT = 8 * 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024
COUNTS = {"thermal": 6, "density": 6, "chemistry": 24, "dsc": 180}
TITLES = {"thermal": "Thermometry", "density": "Liquid density",
          "chemistry": "Ethanol in aqueous matrix", "dsc": "Indium DSC measurements"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _relative(name):
    require(type(name) is str and bool(name) and "\\" not in name and ":" not in name,
            "Require a confined relative inventory path")
    p = PurePosixPath(name)
    require(not p.is_absolute() and all(s not in (".", "..") for s in name.split("/"))
            and all(name.split("/")), "Require a confined relative inventory path")
    return name


class Inventory:
    """Read each selected file once, within a bounded, operator-selected package."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.raw = {}
        raw = self._read("SHA256SUMS")
        self.entries = {}
        for line in raw.decode("utf-8").splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
            require(match is not None, "Malformed SHA256SUMS line")
            identity, name = match.groups()
            _relative(name)
            require(name not in self.entries, "Duplicate inventory path")
            self.entries[name] = "sha256:" + identity
        require(self.entries, "Empty inventory")

    def _read(self, name):
        name = _relative(name)
        path = (self.root / name).resolve()
        require(path.is_relative_to(self.root) and path.is_file(), "Evidence path missing or escapes package")
        require(0 < path.stat().st_size <= FILE_LIMIT, "Evidence file exceeds byte budget")
        raw = path.read_bytes()
        require(0 < len(raw) <= FILE_LIMIT, "Evidence file exceeds byte budget")
        require(sum(map(len, self.raw.values())) + len(raw) <= TOTAL_LIMIT, "Evidence exceeds aggregate byte budget")
        self.raw[name] = raw
        return raw

    def read(self, name):
        require(name in self.entries, "Selected file absent from SHA256SUMS")
        raw = self.raw.get(name)
        if raw is None:
            raw = self._read(name)
        require(digest(raw) == self.entries[name], "Inventory hash mismatch: " + name)
        return load(raw)

    def identity(self, name):
        return digest(self.raw[name])


def indexed(rows, count):
    require(type(rows) is list and len(rows) == count, "Incomplete selected evidence table")
    result = {}
    for row in rows:
        require(type(row) is dict, "Malformed evidence row")
        identity = text(row["id"])
        require(identity not in result, "Repeated evidence row identity")
        result[identity] = row
    return result


def _number(value, *, positive=False):
    require(type(value) is str and len(value) <= 1024, "Plot value must retain its decimal string")
    number = decimal_float(value)
    require(not positive or number > 0, "Uncertainty or rate must be positive")
    return number


def _url(value):
    value = text(value)
    parts = urlsplit(value)
    require(parts.scheme == "https" and bool(parts.netloc) and not parts.username
            and not parts.password, "Source link must be HTTPS")
    return value


def _base(store, family):
    prefix = "results/" + family + "/"
    manifest = store.read(prefix + "campaign.json")
    original_manifest = store.read("campaigns/" + family + "/campaign.json")
    require(manifest == original_manifest and store.identity(prefix + "campaign.json") ==
            store.identity("campaigns/" + family + "/campaign.json"), "Campaign copies differ")
    report = store.read(prefix + "report.json")
    require(report["campaign_id"] == manifest["campaign_id"], "Report campaign identity mismatch")
    require(report["manifest_sha256"] == store.identity(prefix + "campaign.json"), "Report manifest hash mismatch")
    require(hash_id(report["source_sha256"]) == hash_id(manifest["source"]["sha256"]), "Source identity mismatch")
    require(manifest["source"]["file"] == "source.pdf" and manifest["records"]["file"] == "records.json",
            "Unexpected campaign evidence path")
    require(report["release_status"] == "unchanged" and report["local_instrument_qualification"] == "not_established",
            "Unsupported qualification or release claim")
    source_url = _url(report["source_url"])
    if "source_url" in manifest:
        require(manifest["source_url"] == source_url, "Source URL mismatch")
    name = "source-records.json" if family in ("thermal", "dsc") else "records.json"
    records = store.read(prefix + name)
    original = store.read("campaigns/" + family + "/records.json")
    require(records == original and store.identity(prefix + name) == store.identity("campaigns/" + family + "/records.json"),
            "Record copies differ")
    require(report["records_sha256"] == manifest["records"]["sha256"] == store.identity(prefix + name),
            "Record identity mismatch")
    return manifest, report, records


def _comparisons(store, family):
    manifest, report, records = _base(store, family)
    thermal = family == "thermal"
    require(report["schema"] == ("ciw.published-thermal-difference-results.v1" if thermal else
            "ciw.published-measurement-results.v1"), "Unsupported comparison report")
    require(manifest["schema"] == ("ciw.published-thermal-differences.v1" if thermal else
            "ciw.published-measurement-comparison.v1"), "Unsupported comparison campaign")
    require(report["analysis_mode"] == "retrospective_published_report", "Unsupported comparison mode")
    source = records if thermal else store.read("campaigns/" + family + "/source-records.json")
    source_hash = source["raw_source_sha256"] if thermal else source[
        "raw_report_sha256" if family == "chemistry" else "source_sha256"]
    if family in ("thermal", "chemistry"):
        source_hash = "sha256:" + source_hash
    require(source_hash == report["source_sha256"] and source["source_url"] == report["source_url"],
            "Source record provenance mismatch")
    originals = indexed(source["records"], COUNTS[family])
    normalized = indexed(records["records"] if thermal else records, COUNTS[family])
    evaluated = indexed(report["results"], COUNTS[family])
    require(originals.keys() == normalized.keys() == evaluated.keys(), "Row identity alignment mismatch")
    criterion = report["criterion"]
    require(criterion["local_instrument_approved"] is False, "Local criterion approval must remain absent")
    if not thermal:
        require(criterion == manifest["criterion"], "Report criterion differs from campaign")
        require(criterion["basis"] == ("published_report" if family == "density" else "descriptive_analysis")
                and criterion["rule"] == ("En_lt_1" if family == "density" else "interval_includes_zero_k2"),
                "Unsupported comparison criterion")
    else:
        require(report["formal_qualification"] == report["physical_validation"] == "not_established",
                "Thermal qualification must remain absent")
    rows = []
    units = set()
    for identity, row in evaluated.items():
        original, record = originals[identity], normalized[identity]
        require(row["candidate_lab"] == record["candidate_lab"] == original["candidate_lab"], "Laboratory identity mismatch")
        for key in ("candidate_value", "reference_value", "candidate_standard_uncertainty", "reference_standard_uncertainty"):
            if not thermal:
                require(record[key] == original[key], "Original and normalized measured values differ")
        issue = row.get("source_issue")
        require(issue is None or type(issue) is str, "Malformed source issue")
        require(bool(issue) == bool(original.get("source_issue")), "Source issue was lost or added")
        if not thermal:
            require(issue == original.get("source_issue"), "Source issue changed")
        if thermal and row.get("source_En_rounding_issue"):
            issue = " | ".join(filter(None, [issue, text(row["source_En_rounding_issue"])]))
        state = row["status"]
        statuses = {"descriptive_only": "descriptive", "source_review_required": "source_review",
                    "source_discrepancy_requires_review": "source_review"}
        if family == "density":
            statuses.update(satisfies_published_rule="published_rule_satisfied",
                            does_not_satisfy_published_rule="published_rule_not_satisfied")
        require(state in statuses, "Unsupported source comparison status")
        require(not issue or statuses[state] == "source_review", "Source issue cannot be presented as unflagged")
        unit = row["output_unit" if thermal else "unit"]
        require(unit == {"thermal": "uK", "density": "kg/m3", "chemistry": "mg/g"}[family], "Unexpected comparison unit")
        units.add(unit)
        page = original["source_pdf_page"]
        require(str(page).isdigit() and int(page) > 0, "Missing source page")
        rows.append(dict(id=identity, label=row["candidate_lab"],
            group=("Level " + original["level"] if family == "chemistry" else original["material"]),
            value=_number(row["difference"]), uncertainty=_number(row[
                "reconstructed_expanded_difference" if thermal else "expanded_uncertainty_k2"], positive=True),
            status=statuses[state], issue=issue,
            raw=dict(report=row, record=record, source=original),
            source_locator=report["source_url"].split("#")[0] + "#page=" + str(page)))
    controls = report["pilot_controls"] if thermal else []
    if thermal:
        require(len(controls) == 2 and [c["source_literals"] for c in controls] == source["pilot_control_rows"],
                "Pilot controls differ from source")
        require(all(c["independent_candidate"] is False and c["numerical_interval_evaluated"] is False
                    for c in controls), "Pilot controls cannot be independent scored candidates")
    notes = dict(method=(source["quantity_semantics"] if thermal else report["method_summary"]),
        uncertainty=(report["uncertainty_convention"] if thermal else
            "Expanded difference uncertainty at k=2 assumes zero candidate-reference covariance. Independent value assignment alone does not prove that assumption."),
        reference=(report["reference_assignment"] if thermal else report["reference_summary"]),
        criteria=("Conditional source-expanded interval only; coverage ambiguities remain unresolved. No numerical qualification criterion or local approval."
                  if thermal else criterion["source_locator"]))
    return dict(id=family, title=TITLES[family], kind="comparison", unit=units.pop(),
        source_url=report["source_url"], source_sha256=report["source_sha256"], notes=notes, rows=rows,
        counts=dict(available=len(rows), flagged=sum(r["status"] == "source_review" for r in rows)), pilot_controls=controls)


def _dsc(store):
    manifest, report, originals = _base(store, "dsc")
    require(manifest["schema"] == "ciw.nist-srm2232a-published-dsc.v1" and
            report["schema"] == "ciw.published-dsc-import-report.v1", "Unsupported DSC schema")
    require(report["independent_held_out_reference"].startswith("not_established"), "Unsupported DSC holdout claim")
    metadata = store.read("results/dsc/study-metadata.json")
    original_metadata = store.read("campaigns/dsc/metadata.json")
    require(metadata == original_metadata and manifest["metadata"]["file"] == "metadata.json", "DSC metadata mismatch")
    require(report["metadata_sha256"] == manifest["metadata"]["sha256"]
            == store.identity("campaigns/dsc/metadata.json"), "DSC metadata identity mismatch")
    require("sha256:" + metadata["source_sha256"] == report["source_sha256"] and "sha256:" + metadata["records_sha256"] == report["records_sha256"],
            "DSC metadata provenance mismatch")
    normalized = store.read("results/dsc/normalized-records.json")
    require(normalized["schema"] == "ciw.normalized-published-dsc-records.v1", "Unsupported DSC normalization")
    raw = indexed(originals, 180)
    normalized = indexed(normalized["records"], 180)
    require(raw.keys() == normalized.keys(), "DSC row identity alignment mismatch")
    require(report["counts"]["measurement_rows"] == 180, "DSC report count mismatch")
    rows = []
    for identity, row in normalized.items():
        require(all(row.get(k) == v for k, v in raw[identity].items()), "DSC normalization changed original tokens")
        require(row["uncertainty_scope"] == "systematic_effects_only_not_total_per_measurement"
                and row["replicate_semantics"] == "ordinal_in_published_table_not_acquisition_order", "DSC semantics changed")
        require(type(row["source_pdf_page"]) is int and row["source_pdf_page"] > 0, "Invalid DSC source page")
        rows.append(dict(id=identity, label=row["sample_id"] + " / replicate " + str(row["replicate_index_in_table"]),
            group=row["sample_id"], value=_number(row["enthalpy_J_per_g"]), uncertainty=None,
            systematic_uncertainty=_number(row["systematic_standard_uncertainty_J_per_g"], positive=True),
            x=_number(row["measured_heating_rate_C_per_min"], positive=True), nominal_rate=row["nominal_heating_rate_C_per_min"],
            sample=row["sample_id"], ingot=row["ingot_id"], replicate=row["replicate_index_in_table"],
            status="descriptive", issue=None, raw=dict(record=row, source=raw[identity]),
            source_locator=report["source_url"] + "#page=" + str(row["source_pdf_page"])))
    return dict(id="dsc", title=TITLES["dsc"], kind="dsc", unit="J/g", source_url=report["source_url"],
        source_sha256=report["source_sha256"], notes=dict(method=metadata["endpoint"],
            uncertainty="Systematic standard uncertainty only, not total per-measurement uncertainty. No uncertainty of the mean or independent error bars are inferred.",
            reference=report["independent_held_out_reference"],
            criteria="Descriptive observations only. Indium calibration-material data do not establish polymer qualification. Replicate order is table order, not acquisition time."),
        counts=dict(available=180, flagged=0), rows=rows, pilot_controls=[])


def build_payload(evidence_dir):
    store = Inventory(evidence_dir)
    summary = store.read("summary.json")
    require(summary["schema"] == "ciw.public-measurement-evidence-summary.v2"
            and summary["release_status"] == "unchanged" and summary["local_instrument_qualification"] == "not_established",
            "Unsupported package or qualification claim")
    require(summary["actual_published_candidate_rows"] == 36 and summary["additional_DSC_measurement_rows"] == 180,
            "Unexpected v2 package scope")
    datasets = [_comparisons(store, family) for family in ("thermal", "density", "chemistry")]
    datasets.append(_dsc(store))
    return dict(schema=SCHEMA, title="Physical measurement evidence", release_status="unchanged",
        local_instrument_qualification="not_established", integrity=dict(status="inventory_consistent",
            scope="File inventory and internal identity consistency only; no new scientific verification or publisher authentication. Source PDF bytes are not checked by this view.",
            files_checked=len(store.raw) - 1), datasets=datasets)


def render_html(payload):
    require(payload.get("schema") == SCHEMA, "Unsupported visualizer payload")
    assets = files("ciw").joinpath("web")
    template = assets.joinpath("measurement_visualizer.html").read_text(encoding="utf-8")
    css = assets.joinpath("measurement_visualizer.css").read_text(encoding="utf-8")
    js = assets.joinpath("measurement_visualizer.js").read_text(encoding="utf-8")
    hashed = lambda s: base64.b64encode(sha256(s.encode()).digest()).decode("ascii")
    csp = ("default-src 'none'; base-uri 'none'; form-action 'none'; connect-src 'none'; "
           f"script-src 'sha256-{hashed(js)}'; style-src 'sha256-{hashed(css)}'; img-src data:")
    replacements = {"__CSP__": csp, "__STYLE__": css, "__SCRIPT__": js,
        "__DATA__": base64.b64encode(json.dumps(payload, allow_nan=False, ensure_ascii=True).encode()).decode()}
    for marker, value in replacements.items():
        require(template.count(marker) == 1, "Packaged measurement viewer template is inconsistent")
        template = template.replace(marker, value)
    raw = template.encode()
    require(len(raw) <= 12 * 1024 * 1024, "Measurement HTML exceeds byte budget")
    return raw


def write_html(evidence_dir, output):
    raw = render_html(build_payload(evidence_dir))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".net-measurement-", dir=output.parent) as tmp:
        staged = Path(tmp) / "index.html"
        with staged.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged, output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        path = write_html(args.evidence_dir, args.output)
        print(json.dumps(dict(status="created", output=str(path), local_instrument_qualification="not_established",
                              release_status="unchanged", provider_execution=False)))
        return 0
    except (ValueError, OSError, KeyError, TypeError, UnicodeError, OverflowError) as error:
        print(json.dumps(dict(status="refused", error=str(error))), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
